
import asyncio
import logging
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import state
from config import MARKETS
from market_scope import get_strong_signal_markets, get_session_name, is_market_allowed
from market_data import fetch_candles, MarketDataError
from strategy import analyze, format_signal
from chart import create_market_chart
from trading_styles import (
    normalise_trading_style,
    required_higher_timeframes,
    style_has_trade_levels,
    style_label,
    style_timeframes,
)
from economic_events_bot import show_event_center, handle_event_callback, handle_event_text, register_event_handlers
from account_manager_bot import MENU_LABEL as ACCOUNT_MANAGER_LABEL, show_account_manager, handle_account_callback, handle_account_text
from report_charts import (
    create_performance_report,
    create_tracked_report,
    create_history_report,
)

logger = logging.getLogger(__name__)

WAT = ZoneInfo("Africa/Lagos")

# Keep freshly scanned indicator charts in memory alongside the 15-minute snapshot.
STRONG_SIGNAL_CHARTS = {}

MENU = ReplyKeyboardMarkup(
    [
        ["🔎 Market Scanner", "🔥 Strong Signals"],
        ["📌 My Tracked Signals", "📊 Performance"],
        ["📚 Signal History", "⚙️ Settings"],
        ["🗓 Economic Events"],
        ["🛡 Account Manager"],
        ["ℹ️ Help"],
    ],
    resize_keyboard=True,
    is_persistent=True,
)

MARKET_NAMES = list(MARKETS.keys())

TIMEFRAMES = [
    ("1 Minute", "1m"),
    ("5 Minutes", "5m"),
    ("15 Minutes", "15m"),
    ("30 Minutes", "30m"),
    ("1 Hour", "1h"),
    ("4 Hours", "4h"),
    ("1 Day", "1day"),
]

MENU_NAVIGATION_LABELS = {
    "🔎 Market Scanner",
    "🔥 Strong Signals",
    "📌 My Tracked Signals",
    "📊 Performance",
    "📚 Signal History",
    "⚙️ Settings",
    "🗓 Economic Events",
    "🛡 Account Manager",
    "ℹ️ Help",
}

PENDING_INPUT_KEYS = (
    "account_wizard",
    "account_snapshot_id",
    "account_plan_pending",
    "account_risk_pending",
    "awaiting_custom_risk",
    "econ_awaiting",
    "report_date_range",
)


def _reset_pending_input_for_menu_navigation(context, message_text):
    """Let a menu button cancel any unfinished text-input flow.

    Telegram reply-keyboard buttons arrive as ordinary text messages. Without
    this guard, a menu label can be mistaken for a numeric value by an active
    account/risk wizard and raise a float-conversion error.
    """
    if str(message_text or "").strip() not in MENU_NAVIGATION_LABELS:
        return []

    cleared = []
    for key in PENDING_INPUT_KEYS:
        if key in context.user_data:
            context.user_data.pop(key, None)
            cleared.append(key)
    return cleared



def _user_id(update):
    return str(update.effective_user.id)


def _style_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚡ Scalping Trade", callback_data="style:SCALPING")],
        [InlineKeyboardButton("📅 Day Trade", callback_data="style:DAY")],
        [InlineKeyboardButton("🌊 Swing Trade", callback_data="style:SWING")],
    ])


def _timeframe_keyboard(trading_style):
    labels = {
        "1m": "1 Minute", "5m": "5 Minutes",
        "15m": "15 Minutes", "30m": "30 Minutes",
        "1h": "1 Hour", "4h": "4 Hours", "1day": "1 Day",
    }
    timeframes = style_timeframes(trading_style)
    rows = []
    for start in range(0, len(timeframes), 2):
        row = [
            InlineKeyboardButton(
                labels[timeframe],
                callback_data=f"tf:{trading_style}:{timeframe}",
            )
            for timeframe in timeframes[start:start + 2]
        ]
        rows.append(row)
    return InlineKeyboardMarkup(rows)


def _market_keyboard(markets, trading_style, timeframe):
    rows = []
    for start in range(0, len(markets), 2):
        row = [
            InlineKeyboardButton(
                market,
                callback_data=f"market:{trading_style}:{timeframe}:{market}",
            )
            for market in markets[start:start + 2]
        ]
        rows.append(row)
    return InlineKeyboardMarkup(rows)


def _signal_style_label(signal):
    value = signal.get("trading_style")
    if not value:
        return "LEGACY / UNKNOWN"
    try:
        return style_label(value)
    except ValueError:
        return "LEGACY / UNKNOWN"


def _signal_keyboard(signal_id):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "📌 TRACK THIS SIGNAL",
                callback_data=f"track:{signal_id}",
            )
        ]
    ])


def _signal_text(symbol, result):
    text = format_signal(symbol, result)

    score = result.get("score", 0)
    strategy_name = result.get("strategy", "Market analysis")
    confirmations = result.get("higher_timeframe_confirmations") or {}
    context = ", ".join(
        f"{timeframe.upper()} {direction}"
        for timeframe, direction in confirmations.items()
    ) or "Not available"

    return (
        f"{text}\n\n"
        f"Strategy: {strategy_name}\n"
        f"Quality score: {score}/100\n"
        f"Higher-timeframe confirmation: {context}\n\n"
        "The score measures setup quality. "
        "It is not a guaranteed win probability."
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    state.ensure_user(user.id)

    await update.message.reply_text(
        "⚡ FOREX HUNTER AI\n\n"
        "ANALYSE. IDENTIFY. TRACK. IMPROVE.\n\n"
        "Welcome. Choose an option below to begin.",
        reply_markup=MENU,
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = (
        "ℹ️ FOREX HUNTER AI — HELP\n\n"
        "🔎 Market Scanner\n"
        "Analyse a market using your chosen timeframe.\n\n"
        "🔥 Strong Signals\n"
        "View currently tracked qualifying opportunities.\n\n"
        "📌 My Tracked Signals\n"
        "View signals you have chosen to track.\n\n"
        "📊 Performance\n"
        "View results from saved signal records.\n\n"
        "📚 Signal History\n"
        "View past and completed signals.\n\n"
        "⚙️ Settings\n"
        "Change personal market, timeframe, risk and notification preferences.\n\n"
        "🗓 Economic Events\n"
        "Review official high-impact calendars, four pre-release checkpoints, event history and post-release reports.\n\n"
        "Important:\n"
        "This bot provides market analysis only. It does not connect "
        "to your broker or place trades. Always verify prices and "
        "risk before making a trading decision."
    )

    await update.message.reply_text(
        message,
        reply_markup=MENU,
    )


async def show_market_scanner(update, context):
    await update.message.reply_text(
        "🔎 ANALYZE MARKET\n\n"
        "First choose your trading style. Each style has its own entry "
        "timeframes and higher-timeframe confirmation requirements.",
        reply_markup=_style_keyboard(),
    )


async def _fetch_strong_market_frame(symbol):
    """Wait for the shared provider cooldown rather than skipping later markets."""
    for attempt in range(12):
        try:
            return await fetch_candles(
                MARKETS[symbol],
                outputsize=500,
                interval="15m",
                purpose="gold_monitor" if symbol == "XAU/USD" else "auto",
            )
        except MarketDataError as exc:
            match = re.search(
                r"API cooldown active\. Try again in ([0-9.]+) seconds",
                str(exc),
            )
            if not match or attempt == 11:
                raise
            await asyncio.sleep(float(match.group(1)) + 0.2)
    raise MarketDataError("Could not obtain market data after cooldown retries.")


async def _refresh_strong_signal_snapshot():
    """Refresh eligible markets and return current strong setups.

    Weekends are BTC/USD-only. Weekdays check configured forex pairs and Gold only.
    Snapshots are scoped separately so weekday forex opportunities cannot
    appear in a weekend Strong Signals view.
    """
    now = datetime.now(WAT)
    weekend_only = now.weekday() >= 5
    scan_markets = get_strong_signal_markets(MARKETS, now.weekday())
    market_scope = "weekend_btc_only" if weekend_only else "weekday_all_markets"

    saved = state.get_scanner_state("strong_signals_snapshot", {}) or {}
    saved_at = saved.get("updated_at")
    try:
        saved_time = datetime.fromisoformat(
            str(saved_at).replace("Z", "+00:00")
        ).astimezone(WAT)
    except (TypeError, ValueError):
        saved_time = None

    if (
        saved_time
        and saved.get("market_scope") == market_scope
        and (now - saved_time).total_seconds() < 15 * 60
        and all(str(oid) in STRONG_SIGNAL_CHARTS for oid in saved.get("opportunity_ids", []))
    ):
        cached_items = [
            item for item in (
                state.get_opportunity(oid) for oid in saved.get("opportunity_ids", [])
            )
            if item and item.get("symbol") in scan_markets
            and item.get("trading_style") == "DAY"
        ]
        return (
            cached_items,
            saved_time,
            True,
            int(saved.get("markets_checked", 0)),
            list(saved.get("errors", [])),
        )

    strong = []
    errors = []
    markets_checked = 0
    for symbol in scan_markets:
        try:
            frame = await _fetch_strong_market_frame(symbol)
            markets_checked += 1
            result = analyze(frame, "15m", trading_style="DAY")
            if result.get("status") not in ("BUY", "SELL"):
                continue
            if float(result.get("score", 0)) < 70:
                continue

            result["symbol"] = symbol
            result["timeframe"] = "15m"
            result["trading_style"] = "DAY"
            result["style_label"] = "DAY TRADE"
            result["candle_time"] = str(
                result.get("candle_time") or frame.index[-1]
            )
            raw_targets = result.get("take_profits") or []
            targets = list(raw_targets) if isinstance(raw_targets, (list, tuple)) else [raw_targets]
            key = (
                f"adaptive-signal:{symbol}:15m:"
                f"{result['status']}:{result['candle_time']}:DAY"
            )
            opportunity = state.add_opportunity(key, {
                "symbol": symbol,
                "trading_style": "DAY",
                "style_label": "DAY TRADE",
                "direction": result["status"],
                "timeframe": "15m",
                "entry": result.get("entry"),
                "stop_loss": result.get("stop_loss"),
                "tp1": targets[0] if len(targets) > 0 else None,
                "tp2": targets[1] if len(targets) > 1 else None,
                "tp3": targets[2] if len(targets) > 2 else None,
                "take_profits": targets,
                "score": result.get("score", 0),
                "strategy": result.get("strategy", ""),
                "candle_time": result["candle_time"],
                "higher_timeframe": result.get("higher_timeframe"),
                "higher_timeframe_confirmations": result.get("higher_timeframe_confirmations", {}),
            })
            strong.append(opportunity)
            try:
                STRONG_SIGNAL_CHARTS[str(opportunity["id"])] = create_market_chart(
                    result.get("candles", frame), symbol, "15m",
                    entry=result.get("entry"),
                    stop_loss=result.get("stop_loss"),
                    take_profits=targets,
                    strategy=result.get("strategy"),
                    signal_stage=result.get("signal_stage"),
                    trading_style="DAY",
                )
            except Exception:
                logger.exception("Could not create Strong Signals indicator chart for %s.", symbol)
        except MarketDataError as exc:
            errors.append(f"{symbol}: {exc}")
            logger.warning("Strong Signals refresh unavailable for %s: %s", symbol, exc)
        except Exception:
            errors.append(f"{symbol}: analysis error")
            logger.exception("Strong Signals analysis failed for %s.", symbol)

    active_chart_ids = {str(item["id"]) for item in strong}
    for cached_id in list(STRONG_SIGNAL_CHARTS):
        if cached_id not in active_chart_ids:
            STRONG_SIGNAL_CHARTS.pop(cached_id, None)

    snapshot_time = datetime.now(WAT)
    state.set_scanner_state("strong_signals_snapshot", {
        "updated_at": snapshot_time.isoformat(),
        "market_scope": market_scope,
        "opportunity_ids": [str(item["id"]) for item in strong],
        "markets_checked": markets_checked,
        "errors": errors,
    })
    return strong, snapshot_time, False, markets_checked, errors


async def show_strong_signals(update, context):
    user_id = _user_id(update)
    scan_markets = get_strong_signal_markets(MARKETS, datetime.now(WAT).weekday())
    market_scope_text = (
        "BTC/USD only (weekend mode)"
        if len(scan_markets) == 1 and scan_markets[0] == "BTC/USD"
        else "configured forex pairs, Gold and BTC/USD (weekday mode)"
    )
    await update.message.reply_text(
        "🔄 Refreshing DAY TRADE Strong Signals across "
        f"{market_scope_text} using the latest completed 15-minute candles. "
        "This may take a little while."
    )

    (
        opportunities,
        refreshed_at,
        from_cache,
        markets_checked,
        market_errors,
    ) = await _refresh_strong_signal_snapshot()

    # Also hide opportunities tracked before per-user claims were introduced.
    tracked_fingerprints = set()
    for tracked in state.get_signal_history(user_id, limit=500):
        tracked_fingerprints.add((
            str(tracked.get("symbol")),
            str(tracked.get("direction")),
            str(tracked.get("timeframe", "15m")),
            str(tracked.get("entry")),
            str(tracked.get("candle_time")),
        ))

    strong = []
    for item in opportunities:
        claimed_by = item.get("claimed_by", [])
        if not isinstance(claimed_by, list):
            claimed_by = []
        if str(user_id) in {str(value) for value in claimed_by}:
            continue
        fingerprint = (
            str(item.get("symbol")),
            str(item.get("direction")),
            str(item.get("timeframe", "15m")),
            str(item.get("entry")),
            str(item.get("candle_time")),
        )
        if fingerprint in tracked_fingerprints:
            continue
        if (
            item.get("direction") in ("BUY", "SELL")
            and float(item.get("score", 0)) >= 70
            and item.get("trading_style") == "DAY"
        ):
            strong.append(item)

    label = "cached scan" if from_cache else "fresh scan"
    await update.message.reply_text(
        f"🔥 STRONG SIGNALS — DAY TRADE ONLY — {label}\n"
        f"Last scan: {refreshed_at.strftime('%d %b %Y, %H:%M:%S')} WAT\n"
        f"Markets checked successfully: {markets_checked}/{len(scan_markets)}\n"
        f"Qualifying fresh setups: {len(strong)}\n\n"
        "Only current qualifying opportunities are shown. "
        "Track a signal to monitor its TP/SL progress.",
        reply_markup=MENU,
    )
    if market_errors:
        await update.message.reply_text(
            "⚠️ Some markets could not be checked:\n"
            + "\n".join(f"• {error}" for error in market_errors),
            reply_markup=MENU,
        )
    if not strong:
        await update.message.reply_text(
            "No fresh strong opportunities are available right now. "
            "The bot will not recycle old entries.",
            reply_markup=MENU,
        )
        return

    for item in strong:
        oid = item.get("id")
        message = (
            f"🔥 DAY TRADE — STRONG OPPORTUNITY #{oid}\n\n"
            f"Market: {item.get('symbol', 'N/A')}\n"
            f"Direction: {item.get('direction', 'N/A')}\n"
            f"Timeframe: {item.get('timeframe', '15m')}\n"
            f"Entry: {item.get('entry', 'N/A')}\n"
            f"Stop loss: {item.get('stop_loss', 'N/A')}\n"
            f"TP1: {item.get('tp1', 'N/A')}\n"
            f"TP2: {item.get('tp2', 'N/A')}\n"
            f"TP3: {item.get('tp3', 'N/A')}\n"
            f"Quality: {item.get('score', 0)}/100\n"
            f"Strategy: {item.get('strategy', 'N/A')}\\n"
            f"Higher-timeframe confirmation: {', '.join(f'{tf.upper()} {direction}' for tf, direction in (item.get('higher_timeframe_confirmations') or {}).items()) or 'Unavailable'}"
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📌 TRACK THIS SIGNAL", callback_data=f"optrack:{oid}")],
            [InlineKeyboardButton("🧮 CALCULATE POSITION SIZE", callback_data=f"account:plan-opportunity:{oid}")],
        ])
        chart = STRONG_SIGNAL_CHARTS.get(str(oid))
        if chart is not None:
            chart.seek(0)
            await update.message.reply_photo(
                photo=chart, caption=message, reply_markup=keyboard,
            )
        else:
            await update.message.reply_text(message, reply_markup=keyboard)


def _signal_progress(signal):
    if str(signal.get("trading_style", "")).upper() == "SCALPING":
        return "Completed-candle reversal / invalidation monitoring"
    parts = []
    for number in (1, 2, 3):
        hit = bool(signal.get(f"tp{number}_hit"))
        parts.append(f"TP{number} {'✓' if hit else '○'}")
    return "  →  ".join(parts)


def _tracked_signal_keyboard(signal_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "📈 View chart & details",
            callback_data=f"signal:{signal_id}",
        )],
        [InlineKeyboardButton(
            "⏹ Close / Stop tracking",
            callback_data=f"stop:{signal_id}",
        )],
    ])


async def _active_market_tracked_signals(user_id):
    """Keep retired-market records in history, not in the active tracking view."""
    return [
        signal for signal in state.get_tracked_signals(user_id)
        if signal.get("symbol") in MARKETS
    ]


async def _send_tracked_signals(chat, user_id):
    signals = _active_market_tracked_signals(user_id)

    if not signals:
        await chat.send_message(
            "📌 MY TRACKED SIGNALS\n\n"
            "You are not tracking any active signals yet.",
            reply_markup=MENU,
        )
        return

    report = create_tracked_report(signals)
    await chat.send_photo(
        photo=report,
        caption="📌 MY TRACKED SIGNALS — visual overview",
    )

    for signal in signals[:15]:
        signal_id = signal["id"]
        status = signal.get("status", "TRACKING")
        progress = _signal_progress(signal)
        text = (
            f"📌 {_signal_style_label(signal)} — TRACKED SIGNAL #{signal_id}\n\n"
            f"Market: {signal.get('symbol', 'N/A')}\n"
            f"Direction: {signal.get('direction', 'N/A')}\n"
            f"Timeframe: {signal.get('timeframe', 'N/A')}\n"
            f"Higher-timeframe confirmation: {', '.join(f'{tf.upper()} {direction}' for tf, direction in (signal.get('higher_timeframe_confirmations') or {}).items()) or 'Unavailable'}\n"
            f"Entry: {signal.get('entry', 'N/A')}\n"
            f"Current price: {signal.get('current_price', 'Not updated')}\n"
            + (
                f"Stop loss: {signal.get('stop_loss', 'N/A')}\n"
                f"TP1: {signal.get('tp1', 'N/A')}\n"
                f"TP2: {signal.get('tp2', 'N/A')}\n"
                f"TP3: {signal.get('tp3', 'N/A')}\n"
                if style_has_trade_levels(signal.get("trading_style", "DAY"))
                else f"Management: {signal.get('invalidation_rule', 'Completed-candle reversal monitoring')}\n"
            )
            + f"\nProgress: {progress}\n"
            f"Status: {status}\n"
            f"Realised R: {float(signal.get('realised_r', 0) or 0):+.2f}R\n\n"
            "Open this signal to view its individual market chart."
        )
        await chat.send_message(
            text,
            reply_markup=_tracked_signal_keyboard(signal_id),
        )


async def show_tracked_signals(update, context):
    await _send_tracked_signals(
        update.effective_chat,
        _user_id(update),
    )


async def _show_signal_detail(chat, user_id, signal_id):
    signal = state.get_signal(signal_id, user_id)
    if not signal:
        await chat.send_message(
            "This signal could not be found or does not belong to your account."
        )
        return

    progress = _signal_progress(signal)
    status = signal.get("status", "TRACKING")
    current_price = signal.get("current_price", "Not available yet")
    last_checked = signal.get("last_checked", "Not checked yet")
    events = state.get_signal_events(signal_id, user_id)
    recent_events = events[-4:]
    event_lines = [
        f"• {event.get('type')}: {event.get('message')}"
        for event in recent_events
    ]
    event_text = "\n".join(event_lines) if event_lines else "No milestones recorded yet."

    caption = (
        f"📈 {_signal_style_label(signal)} — {signal.get('symbol', 'Unknown')} — "
        f"{signal.get('direction', 'N/A')}\n"
        f"Signal #{signal_id}  |  {str(signal.get('timeframe', '15m')).upper()}\n"
        f"Higher-timeframe confirmation: {', '.join(f'{tf.upper()} {direction}' for tf, direction in (signal.get('higher_timeframe_confirmations') or {}).items()) or 'Unavailable'}\n\n"
        f"Entry: {signal.get('entry', 'N/A')}\n"
        f"Current price: {current_price}\n"
        + (
            f"Stop loss: {signal.get('stop_loss', 'N/A')}\n"
            f"TP1: {signal.get('tp1', 'N/A')}\n"
            f"TP2: {signal.get('tp2', 'N/A')}\n"
            f"TP3: {signal.get('tp3', 'N/A')}\n"
            if style_has_trade_levels(signal.get("trading_style", "DAY"))
            else f"Management: {signal.get('invalidation_rule', 'Completed-candle reversal monitoring')}\n"
        )
        + f"\nProgress: {progress}\n"
        f"Status: {status}\n"
        f"Realised result: {float(signal.get('realised_r', 0) or 0):+.2f}R\n"
        f"Last market check: {last_checked}\n\n"
        f"RECENT EVENTS\n{event_text}\n\n"
        "Signal tracking is analytical and does not confirm broker execution."
    )

    keyboard_rows = []
    if status == "TRACKING":
        keyboard_rows.append([
            InlineKeyboardButton(
                "🔄 Refresh Chart",
                callback_data=f"signalrefresh:{signal_id}",
            )
        ])
    keyboard_rows.append([
        InlineKeyboardButton(
            "⬅️ Back to Tracked Signals",
            callback_data="signalback",
        )
    ])
    if status == "TRACKING":
        keyboard_rows.append([
            InlineKeyboardButton(
                "⏹ Close / Stop tracking",
                callback_data=f"stop:{signal_id}",
            )
        ])
    keyboard = InlineKeyboardMarkup(keyboard_rows)

    try:
        timeframe = str(signal.get("timeframe") or "15m")
        frame = await fetch_candles(
            MARKETS[signal["symbol"]],
            outputsize=100,
            interval=timeframe,
            purpose="gold_monitor" if signal["symbol"] == "XAU/USD" else "monitor",
        )
        latest_chart_price = float(frame["close"].iloc[-1])
        caption = caption.replace(
            f"Current price: {current_price}",
            f"Latest closed-candle price: {latest_chart_price:g}",
        )
        caption = caption[:950]
        targets = [
            signal.get("tp1"),
            signal.get("tp2"),
            signal.get("tp3"),
        ]
        chart = create_market_chart(
            frame,
            signal.get("symbol", "Unknown"),
            timeframe,
            entry=signal.get("entry"),
            stop_loss=signal.get("stop_loss"),
            take_profits=targets,
            strategy=signal.get("strategy"),
            trading_style=signal.get("trading_style") or "LEGACY / UNKNOWN",
        )
        sent_message = await chat.send_photo(
            photo=chart,
            caption=caption,
            reply_markup=keyboard,
        )
        state.update_signal(signal_id, {
            "chart_chat_id": chat.id,
            "chart_message_id": sent_message.message_id,
            "chart_last_update": datetime.now(WAT).isoformat(),
        })
    except (MarketDataError, ValueError, KeyError) as exc:
        await chat.send_message(
            caption
            + "\n\n⚠️ The chart could not be refreshed right now: "
            + str(exc)
            + "\nYour saved signal and tracking record are unchanged.",
            reply_markup=keyboard,
        )
    except Exception:
        logger.exception("Could not render tracked signal chart %s.", signal_id)
        await chat.send_message(
            caption + "\n\n⚠️ Chart rendering is temporarily unavailable.",
            reply_markup=keyboard,
        )

def _performance_summary(signals):
    signals = list(signals or [])
    completed = [s for s in signals if s.get("status") in {
        "TP3", "MANUAL_WIN", "STOP_LOSS", "INVALIDATED", "AMBIGUOUS", "MANUALLY_STOPPED"
    }]
    eligible = [s for s in signals if s.get("status") in {"TP3", "MANUAL_WIN", "STOP_LOSS"}]
    wins = sum(s.get("status") in {"TP3", "MANUAL_WIN"} for s in eligible)
    losses = sum(s.get("status") == "STOP_LOSS" for s in eligible)
    win_rate = wins / len(eligible) * 100 if eligible else 0
    total_r = sum(float(s.get("realised_r", 0) or 0) for s in signals
                  if s.get("status") not in {"MANUALLY_STOPPED", "INVALIDATED", "AMBIGUOUS"})
    tp = [sum(bool(s.get(f"tp{i}_hit")) for s in signals) for i in (1, 2, 3)]
    return (
        "📊 PERFORMANCE CENTRE\n\n"
        f"Generated/tracked: {len(signals)}\n"
        f"Completed: {len(completed)}\n"
        f"Wins: {wins}  |  Losses: {losses}\n"
        f"Win rate (TP3 and manual TP1/TP2 closes vs SL): {win_rate:.1f}%\n"
        f"TP achievements: TP1 {tp[0]} | TP2 {tp[1]} | TP3 {tp[2]}\n"
        f"Cumulative realised R: {total_r:+.2f}R\n\n"
        "Manual closes after TP1/TP2 count as milestone wins. Stops before TP1, invalidated "
        "and ambiguous signals are excluded. This is signal analytics, not broker P/L."
    )


def _signal_report_time(signal):
    """Use completion time for closed signals and creation time for active ones."""
    raw = signal.get("completed_at") or signal.get("created_at")
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=WAT)
        return parsed.astimezone(WAT)
    except (TypeError, ValueError):
        return None


def _report_period(signals, period, custom_start=None, custom_end=None):
    now = datetime.now(WAT)
    today = now.date()
    if period == "yesterday":
        start_date = today - timedelta(days=1)
        start = datetime.combine(start_date, datetime.min.time(), tzinfo=WAT)
        end = datetime.combine(today, datetime.min.time(), tzinfo=WAT)
        label = "Yesterday"
    elif period == "week":
        start_date = today - timedelta(days=6)
        start = datetime.combine(start_date, datetime.min.time(), tzinfo=WAT)
        end = now
        label = "Last 7 days"
    elif period == "30days":
        start_date = today - timedelta(days=29)
        start = datetime.combine(start_date, datetime.min.time(), tzinfo=WAT)
        end = now
        label = "Last 30 days"
    elif period == "lastmonth":
        first_this_month = today.replace(day=1)
        last_month_end = datetime.combine(first_this_month, datetime.min.time(), tzinfo=WAT)
        previous_month_last_day = first_this_month - timedelta(days=1)
        start = datetime.combine(previous_month_last_day.replace(day=1), datetime.min.time(), tzinfo=WAT)
        end = last_month_end
        label = previous_month_last_day.strftime("%B %Y")
    elif period == "twomonthsago":
        first_this_month = today.replace(day=1)
        last_month_first = (first_this_month - timedelta(days=1)).replace(day=1)
        two_months_first = (last_month_first - timedelta(days=1)).replace(day=1)
        start = datetime.combine(two_months_first, datetime.min.time(), tzinfo=WAT)
        end = datetime.combine(last_month_first, datetime.min.time(), tzinfo=WAT)
        label = two_months_first.strftime("%B %Y")
    elif period == "custom":
        if not custom_start or not custom_end:
            return [], "Custom date range"
        start = datetime.combine(custom_start, datetime.min.time(), tzinfo=WAT)
        end = datetime.combine(custom_end + timedelta(days=1), datetime.min.time(), tzinfo=WAT)
        label = f"{custom_start:%d %b %Y} – {custom_end:%d %b %Y}"
    else:
        return list(signals), "All history"

    filtered = []
    for signal in signals:
        activity_time = _signal_report_time(signal)
        if activity_time and start <= activity_time < end:
            filtered.append(signal)
    return filtered, label


def _report_period_keyboard(report_type):
    prefix = "perf" if report_type == "performance" else "history"
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Yesterday", callback_data=f"{prefix}:yesterday"),
            InlineKeyboardButton("Last 7 days", callback_data=f"{prefix}:week"),
        ],
        [
            InlineKeyboardButton("Last 30 days", callback_data=f"{prefix}:30days"),
            InlineKeyboardButton("Last month", callback_data=f"{prefix}:lastmonth"),
        ],
        [
            InlineKeyboardButton("2 months ago", callback_data=f"{prefix}:twomonthsago"),
            InlineKeyboardButton("All history", callback_data=f"{prefix}:all"),
        ],
        [
            InlineKeyboardButton("📅 Custom dates", callback_data=f"{prefix}:custom"),
        ],
    ])


async def _send_performance(chat, user_id, period, custom_start=None, custom_end=None):
    all_signals = state.get_signal_history(user_id, limit=10000)
    signals, label = _report_period(all_signals, period, custom_start, custom_end)
    report = create_performance_report(signals, period_label=label)
    await chat.send_photo(
        photo=report,
        caption=f"📊 PERFORMANCE CENTRE — {label}\nImage report • WAT",
        reply_markup=_report_period_keyboard("performance"),
    )


async def show_performance(update, context):
    await _send_performance(update.effective_chat, _user_id(update), "all")


async def _send_history(chat, user_id, period, custom_start=None, custom_end=None):
    all_signals = state.get_signal_history(user_id, limit=10000)
    signals, label = _report_period(all_signals, period, custom_start, custom_end)
    report = create_history_report(signals, period_label=label)
    await chat.send_photo(
        photo=report,
        caption=f"📚 SIGNAL HISTORY — {label}\nVisual trade record • WAT",
        reply_markup=_report_period_keyboard("history"),
    )


async def show_history(update, context):
    await _send_history(update.effective_chat, _user_id(update), "all")


async def show_settings(update, context):
    user_id = _user_id(update)
    settings = state.get_user_settings(user_id)
    timeframe = state.get_timeframe(user_id)
    mode = settings.get("tracking_notification_mode", "silent")
    mode_labels = {
        "silent": "Silent Monitoring",
        "important": "Important Alerts Only",
        "all": "All Updates",
    }
    mode_label = mode_labels.get(mode, "Silent Monitoring")

    text = (
        "⚙️ PERSONAL SETTINGS\n\n"
        f"Default timeframe: {timeframe}\n"
        f"Preferred markets: {', '.join(settings.get('markets', []))}\n"
        f"Risk preference: {settings.get('risk_percent', 1.0)}%\n"
        f"Timezone: {settings.get('timezone', 'Africa/Lagos')}\n"
        f"Tracking notifications: {mode_label}\n\n"
        "Choose a setting below."
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "📡 API Usage",
                callback_data="settings:api_usage",
            )
        ],
        [
            InlineKeyboardButton(
                "Change default timeframe",
                callback_data="settings:timeframe",
            )
        ],
        [
            InlineKeyboardButton(
                "Change risk preference",
                callback_data="settings:risk",
            )
        ],
        [
            InlineKeyboardButton(
                "Signal notification mode",
                callback_data="settings:notifications",
            )
        ],
    ])

    await update.message.reply_text(
        text,
        reply_markup=keyboard,
    )


async def show_api_usage(chat):
    usage = state.get_api_usage()
    used = usage["used"]
    limit = usage["limit"]
    remaining = usage["remaining"]
    percent = (used / limit * 100) if limit else 0

    gold_used = usage.get("gold_monitor_used", 0)
    gold_limit = usage.get("gold_monitor_limit", 0)
    other_used = usage.get("other_background_used", 0)
    other_limit = usage.get("other_background_limit", 0)

    text = (
        "📡 API USAGE\n\n"
        f"Date: {usage['date']} (UTC reset day)\n"
        f"Requests recorded today: {used:,} / {limit:,}\n"
        f"Remaining local allowance: {remaining:,}\n"
        f"Usage: {percent:.1f}%\n\n"
        "PROTECTED DAILY ALLOCATIONS\n"
        f"• Gold market requests (scanner + monitoring): {gold_used:,} / {gold_limit:,}\n"
        f"• Other background work: {other_used:,} / {other_limit:,}\n"
        "• Manual request reserve: 100 requests\n"
        "• Shared minimum request interval: 8 seconds\n"
        "• Daily counter resets at 00:00 UTC.\n\n"
        "Gold receives 50% of the background API allocation. "
        "This local counter is a safety estimate, not a live "
        "Twelve Data billing statement."
    )

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "⬅️ Back to Settings",
            callback_data="settings:back",
        )
    ]])
    await chat.send_message(text, reply_markup=keyboard)


async def analyse_market(update, context, symbol, timeframe, trading_style="DAY"):
    user_id = _user_id(update)
    trading_style = normalise_trading_style(trading_style)
    try:
        required_higher_timeframes(trading_style, timeframe)
    except ValueError as exc:
        await update.callback_query.edit_message_text(str(exc))
        return

    if symbol not in MARKETS:
        await update.callback_query.edit_message_text(
            "That market is not configured."
        )
        return

    if not is_market_allowed(symbol, datetime.now(WAT).weekday()):
        await update.callback_query.edit_message_text(
            "That market is not available in the current trading session. "
            "Please reopen Market Scanner to see the currently enabled markets."
        )
        return

    await update.callback_query.edit_message_text(
        f"⏳ Analysing {symbol} on {timeframe.upper()}...\n"
        "Please wait for completed candle data."
    )

    try:
        frame = await fetch_candles(
            MARKETS[symbol],
            outputsize=1500,
            interval=timeframe,
        )

        result = analyze(
            frame,
            timeframe,
            trading_style=trading_style,
        )

        result["symbol"] = symbol

        if result.get("status") not in ("BUY", "SELL"):
            await update.effective_chat.send_message(
                f"🔎 {style_label(trading_style)} — {symbol} — NO QUALIFYING SETUP\n\n"
                f"{result.get('reason', 'The conditions are not aligned.')}\n\n"
                "No trade recommendation has been generated.",
                reply_markup=MENU,
            )
            return

        text = _signal_text(symbol, result)

        chart = create_market_chart(
            result.get("candles", frame),
            symbol,
            timeframe,
            entry=result.get("entry"),
            stop_loss=result.get("stop_loss"),
            take_profits=result.get("take_profits"),
            strategy=result.get("strategy"),
            signal_stage=result.get("signal_stage"),
            trading_style=trading_style,
        )

        signal_id = (
            f"manual-{user_id}-"
            f"{int(datetime.now().timestamp())}"
        )
        keyboard_rows = [[
            InlineKeyboardButton("📌 TRACK THIS SIGNAL", callback_data=f"track:{signal_id}")
        ]]
        if (
            style_has_trade_levels(trading_style)
            and result.get("entry") is not None
            and result.get("stop_loss") is not None
            and len(result.get("take_profits") or []) == 3
        ):
            keyboard_rows.append([
                InlineKeyboardButton(
                    "🧮 CALCULATE POSITION SIZE",
                    callback_data=f"account:plan-manual:{signal_id}",
                )
            ])
        keyboard = InlineKeyboardMarkup(keyboard_rows)
        context.user_data[signal_id] = {
            "symbol": symbol,
            "trading_style": trading_style,
            "style_label": style_label(trading_style),
            "invalidation_rule": result.get("invalidation_rule"),
            "higher_timeframe_confirmations": result.get("higher_timeframe_confirmations", {}),
            "direction": result["status"],
            "timeframe": timeframe,
            "entry": result.get("entry"),
            "stop_loss": result.get("stop_loss"),
            "take_profits": result.get("take_profits", []),
            "score": result.get("score", 0),
            "strategy": result.get("strategy", ""),
            "candle_time": str(result.get("candle_time") or frame.index[-1]),
        }
        if not style_has_trade_levels(trading_style):
            context.user_data[signal_id].pop("stop_loss", None)
            context.user_data[signal_id].pop("take_profits", None)

        chart.seek(0)

        await update.effective_chat.send_photo(
            photo=chart,
            caption=text,
            reply_markup=keyboard,
        )

    except (MarketDataError, ValueError) as exc:
        await update.effective_chat.send_message(
            f"⚠️ Could not analyse {symbol}:\n{exc}",
            reply_markup=MENU,
        )

    except Exception:
        logger.exception(
            "Manual analysis failed for %s.",
            symbol,
        )

        await update.effective_chat.send_message(
            "⚠️ An unexpected analysis error occurred. "
            "Please try again later.",
            reply_markup=MENU,
        )


async def handle_event_callback_if_needed(update, context):
    query = update.callback_query
    if query and str(query.data or "").startswith("econ:"):
        await handle_event_callback(update, context)
        return True
    return False


async def handle_callback(update, context):
    if await handle_account_callback(update, context):
        return
    if await handle_event_callback_if_needed(update, context):
        return
    query = update.callback_query
    await query.answer()

    data = query.data or ""
    user_id = _user_id(update)

    if data.startswith("style:"):
        trading_style = normalise_trading_style(data.split(":", 1)[1])
        await query.edit_message_text(
            f"{style_label(trading_style)} selected. Choose an entry timeframe:",
            reply_markup=_timeframe_keyboard(trading_style),
        )
        return

    if data.startswith("tf:"):
        parts = data.split(":")
        if len(parts) != 3:
            return
        trading_style = normalise_trading_style(parts[1])
        timeframe = parts[2]
        try:
            required_higher_timeframes(trading_style, timeframe)
        except ValueError as exc:
            await query.edit_message_text(str(exc))
            return
        markets = get_strong_signal_markets(MARKETS, datetime.now(WAT).weekday())
        await query.edit_message_text(
            f"{style_label(trading_style)} — {timeframe.upper()} selected. "
            "Now choose a market:",
            reply_markup=_market_keyboard(markets, trading_style, timeframe),
        )
        return

    if data.startswith("market:"):
        parts = data.split(":")
        if len(parts) != 4:
            return
        trading_style = normalise_trading_style(parts[1])
        timeframe = parts[2]
        symbol = parts[3]
        try:
            required_higher_timeframes(trading_style, timeframe)
        except ValueError as exc:
            await query.edit_message_text(str(exc))
            return
        if symbol not in MARKETS or not is_market_allowed(symbol, datetime.now(WAT).weekday()):
            await query.edit_message_text("That market is not available in the current session.")
            return
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton(
                "⚡ GENERATE ANALYSIS",
                callback_data=f"generate:{trading_style}:{timeframe}:{symbol}",
            )
        ]])
        await query.edit_message_text(
            f"{style_label(trading_style)}\\n"
            f"Market: {symbol}\\n"
            f"Entry timeframe: {timeframe.upper()}\\n\\n"
            "Ready to generate the analysis?",
            reply_markup=keyboard,
        )
        return

    if data.startswith("generate:"):
        parts = data.split(":")
        if len(parts) != 4:
            return
        await analyse_market(
            update, context, parts[3], parts[2], parts[1],
        )
        return

    if data.startswith("perf:") or data.startswith("history:"):
        report_type, period = data.split(":", 1)
        if period == "custom":
            context.user_data["report_date_range"] = {"type": report_type}
            await query.message.reply_text(
                "📅 CUSTOM REPORT RANGE\n\n"
                "Send the START date in YYYY-MM-DD format.\n"
                "Example: 2026-10-01"
            )
            return
        allowed_periods = {
            "yesterday", "week", "30days", "lastmonth",
            "twomonthsago", "all",
        }
        if period not in allowed_periods:
            return
        if report_type == "perf":
            await _send_performance(query.message.chat, user_id, period)
        else:
            await _send_history(query.message.chat, user_id, period)
        return

    if data.startswith("optrack:"):
        opportunity_id = data.split(":", 1)[1]
        opportunity = state.get_opportunity(opportunity_id)
        if not opportunity:
            await query.edit_message_text("This opportunity is no longer available.")
            return
        claimed_by = opportunity.get("claimed_by", [])
        if not isinstance(claimed_by, list):
            claimed_by = []
        if str(user_id) in {str(value) for value in claimed_by}:
            await query.edit_message_text(
                "You have already tracked this opportunity. "
                "It has been removed from your Strong Signals list."
            )
            return
        if opportunity.get("direction") not in ("BUY", "SELL"):
            await query.edit_message_text("This opportunity is not trackable.")
            return
        targets = opportunity.get("take_profits") or []
        record = state.add_signal(user_id, {
            "opportunity_id": str(opportunity_id),
            "symbol": opportunity.get("symbol"),
            "trading_style": opportunity.get("trading_style", "DAY"),
            "direction": opportunity.get("direction"),
            "timeframe": opportunity.get("timeframe", "15m"),
            "entry": opportunity.get("entry"),
            "stop_loss": opportunity.get("stop_loss"),
            "tp1": opportunity.get("tp1") or (targets[0] if len(targets) > 0 else None),
            "tp2": opportunity.get("tp2") or (targets[1] if len(targets) > 1 else None),
            "tp3": opportunity.get("tp3") or (targets[2] if len(targets) > 2 else None),
            "take_profits": targets,
            "score": opportunity.get("score", 0),
            "strategy": opportunity.get("strategy", ""),
            "candle_time": opportunity.get("candle_time"),
            "higher_timeframe": opportunity.get("higher_timeframe"),
            "higher_timeframe_confirmations": opportunity.get("higher_timeframe_confirmations", {}),
        })
        state.mark_opportunity_claimed(opportunity_id, user_id)
        message = (
            f"✅ Opportunity saved as tracked signal #{record['id']}. "
            "It has been removed from your Strong Signals list. "
            "Automatic monitoring will now follow this signal."
        )
        if getattr(query.message, "photo", None):
            await query.edit_message_caption(caption=message)
        else:
            await query.edit_message_text(text=message)
        return

    if data.startswith("track:"):
        signal_id = data.split(":", 1)[1]

        pending = context.user_data.get(signal_id)

        if not pending:
            await query.edit_message_text(
                "This signal is no longer available to track. "
                "Run the analysis again."
            )
            return

        take_profits = pending.get("take_profits", [])
        trading_style = pending.get("trading_style", "DAY")
        signal_record = {
            "symbol": pending["symbol"],
            "trading_style": trading_style,
            "invalidation_rule": pending.get("invalidation_rule"),
            "direction": pending["direction"],
            "timeframe": pending["timeframe"],
            "entry": pending.get("entry"),
            "score": pending.get("score", 0),
            "strategy": pending.get("strategy", ""),
            "candle_time": pending.get("candle_time"),
            "higher_timeframe_confirmations": pending.get("higher_timeframe_confirmations", {}),
        }
        if style_has_trade_levels(trading_style):
            signal_record.update({
                "stop_loss": pending.get("stop_loss"),
                "tp1": take_profits[0] if len(take_profits) > 0 else None,
                "tp2": take_profits[1] if len(take_profits) > 1 else None,
                "tp3": take_profits[2] if len(take_profits) > 2 else None,
                "take_profits": take_profits,
            })
        record = state.add_signal(user_id, signal_record)

        context.user_data.pop(signal_id, None)

        message = (
            f"✅ {_signal_style_label(record)} — SIGNAL #{record['id']} SAVED\n\n"
            f"{record['symbol']} {record['direction']}\n"
            "It has been added to your tracked signals.\n\n"
            "Automatic milestone monitoring is active for this signal."
        )
        if getattr(query.message, "photo", None):
            await query.edit_message_caption(caption=message)
        else:
            await query.edit_message_text(text=message)
        return

    if data == "signalback":
        await _send_tracked_signals(query.message.chat, user_id)
        return

    if data.startswith("signalrefresh:"):
        signal_id = data.split(":", 1)[1]
        await _show_signal_detail(query.message.chat, user_id, signal_id)
        return

    if data.startswith("signal:"):
        signal_id = data.split(":", 1)[1]
        await _show_signal_detail(query.message.chat, user_id, signal_id)
        return

    if data == "settings:api_usage":
        await show_api_usage(query.message.chat)
        return

    if data == "settings:back":
        settings = state.get_user_settings(user_id)
        timeframe = state.get_timeframe(user_id)
        mode = settings.get("tracking_notification_mode", "silent")
        mode_labels = {
            "silent": "Silent Monitoring",
            "important": "Important Alerts Only",
            "all": "All Updates",
        }
        mode_label = mode_labels.get(mode, "Silent Monitoring")
        text = (
            "⚙️ PERSONAL SETTINGS\n\n"
            f"Default timeframe: {timeframe}\n"
            f"Preferred markets: {', '.join(settings.get('markets', []))}\n"
            f"Risk preference: {settings.get('risk_percent', 1.0)}%\n"
            f"Timezone: {settings.get('timezone', 'Africa/Lagos')}\n"
            f"Tracking notifications: {mode_label}\n\n"
            "Choose a setting below."
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📡 API Usage", callback_data="settings:api_usage")],
            [InlineKeyboardButton("Change default timeframe", callback_data="settings:timeframe")],
            [InlineKeyboardButton("Change risk preference", callback_data="settings:risk")],
            [InlineKeyboardButton("Signal notification mode", callback_data="settings:notifications")],
        ])
        await query.edit_message_text(text, reply_markup=keyboard)
        return

    if data == "settings:notifications":
        current = state.get_user_settings(user_id).get(
            "tracking_notification_mode", "silent"
        )
        labels = {
            "silent": "Silent Monitoring",
            "important": "Important Alerts Only",
            "all": "All Updates",
        }
        rows = [
            [InlineKeyboardButton(
                ("✓ " if current == "silent" else "") + "Silent Monitoring",
                callback_data="notify:silent",
            )],
            [InlineKeyboardButton(
                ("✓ " if current == "important" else "") + "Important Alerts Only",
                callback_data="notify:important",
            )],
            [InlineKeyboardButton(
                ("✓ " if current == "all" else "") + "All Updates",
                callback_data="notify:all",
            )],
        ]
        await query.edit_message_text(
            "Choose how Forex Hunter AI should notify you about tracked-signal events.\n\n"
            "Silent Monitoring: no TP/SL messages; progress stays in My Tracked Signals.\n"
            "Important Alerts Only: notify on TP1, TP2, TP3, stop loss and significant trend warnings.\n"
            "All Updates: notify on every recorded monitoring outcome and trend warning.\n\n"
            f"Current setting: {labels.get(current, 'Silent Monitoring')}",
            reply_markup=InlineKeyboardMarkup(rows),
        )
        return

    if data.startswith("notify:"):
        mode = data.split(":", 1)[1]
        if mode not in {"silent", "important", "all"}:
            await query.edit_message_text("That notification mode is not supported.")
            return
        state.update_user_setting(
            user_id, "tracking_notification_mode", mode
        )
        labels = {
            "silent": "Silent Monitoring",
            "important": "Important Alerts Only",
            "all": "All Updates",
        }
        await query.edit_message_text(
            f"✅ Tracking notifications set to: {labels[mode]}."
        )
        return

    if data.startswith("stop:"):
        signal_id = data.split(":", 1)[1]
        signal = state.get_signal(signal_id, user_id)
        if not signal or signal.get("status") != "TRACKING":
            await query.edit_message_text("This signal is no longer active or does not belong to your account.")
            return

        if signal.get("tp1_hit") or signal.get("tp2_hit"):
            reached = 2 if signal.get("tp2_hit") else 1
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton(
                    f"✅ Confirm close — record TP{reached} win",
                    callback_data=f"manualclose:{signal_id}",
                )],
                [InlineKeyboardButton(
                    "↩️ Continue tracking",
                    callback_data=f"manualcontinue:{signal_id}",
                )],
            ])
            message = (
                f"🎯 TP{reached} has already been reached.\n\n"
                "You can secure the achieved milestone now or continue tracking "
                "towards the next target. If you close, the history will record "
                f"WIN — manually closed after TP{reached}. The realised R will "
                "use the latest monitored closed-candle price, not an assumed broker fill."
            )
            if getattr(query.message, "photo", None):
                await query.edit_message_caption(caption=message, reply_markup=keyboard)
            else:
                await query.edit_message_text(text=message, reply_markup=keyboard)
        else:
            stopped = state.stop_tracking_signal(signal_id, user_id)
            message = (
                "Tracking stopped before TP1. The record is preserved but is not counted as a win."
                if stopped else "This signal could not be stopped. It may already be completed."
            )
            if getattr(query.message, "photo", None):
                await query.edit_message_caption(caption=message)
            else:
                await query.edit_message_text(text=message)
        return

    if data.startswith("manualclose:"):
        signal_id = data.split(":", 1)[1]
        closed = state.close_signal_after_target(signal_id, user_id)
        if not closed:
            await query.edit_message_text("Could not close this signal. Confirm that TP1 or TP2 was reached and the signal is still tracking.")
            return
        reached = int(closed.get("manual_close_after_tp", 1))
        exit_price = closed.get("manual_exit_price")
        exit_text = f"Last monitored price: {exit_price}\n" if exit_price is not None else "Exit price: unavailable; no price has been invented.\n"
        message = (
            f"🏆 WIN — MANUALLY CLOSED AFTER TP{reached}\n\n"
            f"{closed.get('symbol', 'Signal')} {closed.get('direction', '')}\n"
            f"{exit_text}Realised result estimate: {float(closed.get('realised_r', 0) or 0):+.2f}R\n\n"
            "Saved in Signal History and Performance. This is a target-milestone record, not a broker-confirmed execution."
        )
        if getattr(query.message, "photo", None):
            await query.edit_message_caption(caption=message)
        else:
            await query.edit_message_text(text=message)
        return

    if data.startswith("manualcontinue:"):
        signal_id = data.split(":", 1)[1]
        signal = state.get_signal(signal_id, user_id)
        if not signal or signal.get("status") != "TRACKING":
            await query.edit_message_text("This signal is no longer active.")
            return
        message = (
            f"✅ Continuing to track {signal.get('symbol', 'this signal')}. "
            "Monitoring remains active for the next target or stop-loss."
        )
        if getattr(query.message, "photo", None):
            await query.edit_message_caption(caption=message)
        else:
            await query.edit_message_text(text=message)
        return

    if data == "settings:timeframe":
        rows = []

        for label, timeframe in TIMEFRAMES:
            rows.append([
                InlineKeyboardButton(
                    label,
                    callback_data=f"settf:{timeframe}",
                )
            ])

        await query.edit_message_text(
            "Choose your default timeframe:",
            reply_markup=InlineKeyboardMarkup(rows),
        )
        return

    if data.startswith("settf:"):
        timeframe = data.split(":", 1)[1]

        state.set_timeframe(user_id, timeframe)

        await query.edit_message_text(
            f"Default timeframe updated to {timeframe.upper()}."
        )
        return

    if data == "settings:risk":
        rows = [
            [
                InlineKeyboardButton("0.5%", callback_data="risk:0.5"),
                InlineKeyboardButton("1%", callback_data="risk:1"),
                InlineKeyboardButton("2%", callback_data="risk:2"),
                InlineKeyboardButton("5%", callback_data="risk:5"),
            ],
            [
                InlineKeyboardButton("10%", callback_data="risk:10"),
                InlineKeyboardButton("15%", callback_data="risk:15"),
                InlineKeyboardButton("20%", callback_data="risk:20"),
            ],
            [InlineKeyboardButton("✍️ Custom percentage", callback_data="risk:custom")],
        ]
        await query.edit_message_text(
            "Choose your preferred risk percentage.\n\n"
            "You can select a preset or enter a custom value from 0.1% to 20%. "
            "This setting is a planning preference only; the bot does not "
            "calculate broker lot sizes or place trades. Risking 20% on one "
            "trade is extremely aggressive and can cause rapid account losses.",
            reply_markup=InlineKeyboardMarkup(rows),
        )
        return

    if data == "risk:custom":
        context.user_data["awaiting_custom_risk"] = True
        await query.edit_message_text(
            "✍️ Send your custom risk percentage as a number.\n\n"
            "Examples: 0.75, 1.25, or 1.5\n"
            "Allowed range: 0.1% to 20%.\n\n"
            "Type cancel to leave this setting unchanged."
        )
        return

    if data.startswith("risk:"):
        risk = float(data.split(":", 1)[1])

        settings = state.get_user_settings(user_id)
        settings["risk_percent"] = risk

        state.update_user_setting(
            user_id,
            "risk_percent",
            risk,
        )

        await query.edit_message_text(
            f"Risk preference updated to {risk:g}%.\n\n"
            "This is a sizing preference, not a fixed lot size. "
            "Always calculate lot size after choosing a valid technical stop loss. "
            "Risking 20% on one trade is extremely aggressive and can cause rapid losses."
        )
        return


def text_for_event_center(update):
    return (update.message.text or "").strip() == "🗓 Economic Events"


async def handle_text(update, context):
    incoming_text = (update.message.text or "").strip()
    cancelled_inputs = _reset_pending_input_for_menu_navigation(
        context, incoming_text
    )
    if cancelled_inputs:
        await update.message.reply_text(
            "Your previous input step was cancelled so you can navigate the menu. "
            "No account figures or settings were changed."
        )

    if await handle_account_text(update, context):
        return
    if text_for_event_center(update):
        await show_event_center(update, context)
        return
    if await handle_event_text(update, context):
        return

    pending_range = context.user_data.get("report_date_range")
    if pending_range:
        raw_date = (update.message.text or "").strip()
        try:
            parsed_date = datetime.strptime(raw_date, "%Y-%m-%d").date()
        except ValueError:
            await update.message.reply_text(
                "Please use YYYY-MM-DD, for example 2026-10-01. "
                "Your custom report range is still waiting."
            )
            return

        if "start" not in pending_range:
            pending_range["start"] = parsed_date
            await update.message.reply_text(
                "Now send the END date in YYYY-MM-DD format. "
                "The end date is included in the report."
            )
            return

        start_date = pending_range["start"]
        if parsed_date < start_date:
            await update.message.reply_text(
                "The end date cannot be earlier than the start date. "
                "Please send the end date again."
            )
            return

        report_type = pending_range["type"]
        context.user_data.pop("report_date_range", None)
        if report_type == "performance":
            await _send_performance(
                update.effective_chat, _user_id(update), "custom",
                custom_start=start_date, custom_end=parsed_date,
            )
        else:
            await _send_history(
                update.effective_chat, _user_id(update), "custom",
                custom_start=start_date, custom_end=parsed_date,
            )
        return

    text = (update.message.text or "").strip()

    if text.lower() == "cancel" and context.user_data.get("awaiting_custom_risk"):
        context.user_data.pop("awaiting_custom_risk", None)
        await update.message.reply_text(
            "Custom risk entry cancelled. Your previous setting is unchanged.",
            reply_markup=MENU,
        )
        return

    if context.user_data.get("awaiting_custom_risk"):
        try:
            risk = float(text.replace("%", "").strip())
        except ValueError:
            await update.message.reply_text(
                "Please enter a valid number, for example 0.75 or 1.25. "
                "Allowed range: 0.1% to 20%."
            )
            return
        if not 0.1 <= risk <= 20.0:
            await update.message.reply_text(
                "That value is outside the allowed range. Enter 0.1% to 20%."
            )
            return
        state.update_user_setting(_user_id(update), "risk_percent", risk)
        context.user_data.pop("awaiting_custom_risk", None)
        await update.message.reply_text(
            f"✅ Custom risk preference saved: {risk:g}%.\n\n"
            "This is a planning preference, not a broker order or automatic "
            "lot-size calculation. Always calculate position size from your "
            "account balance and technical stop-loss distance. Risking 20% "
            "on one trade is extremely aggressive and can cause rapid losses.",
            reply_markup=MENU,
        )
        return

    if text == ACCOUNT_MANAGER_LABEL:
        await show_account_manager(update, context)

    elif text == "🔎 Market Scanner":
        await show_market_scanner(update, context)

    elif text == "🔥 Strong Signals":
        await show_strong_signals(update, context)

    elif text == "📌 My Tracked Signals":
        await show_tracked_signals(update, context)

    elif text == "📊 Performance":
        await show_performance(update, context)

    elif text == "📚 Signal History":
        await show_history(update, context)

    elif text == "⚙️ Settings":
        await show_settings(update, context)

    elif text == "ℹ️ Help":
        await help_command(update, context)

    else:
        await update.message.reply_text(
            "Please use the menu buttons to navigate Forex Hunter AI.",
            reply_markup=MENU,
        )


def register_handlers(application: Application):
    register_event_handlers(application)
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CallbackQueryHandler(handle_callback))
    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_text,
        )
    )
