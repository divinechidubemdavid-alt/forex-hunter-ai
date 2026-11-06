
import logging
from datetime import datetime
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
from market_data import fetch_candles, MarketDataError
from strategy import analyze, format_signal
from chart import create_market_chart
from report_charts import (
    create_performance_report,
    create_tracked_report,
    create_history_report,
)

logger = logging.getLogger(__name__)

WAT = ZoneInfo("Africa/Lagos")

MENU = ReplyKeyboardMarkup(
    [
        ["🔎 Market Scanner", "🔥 Strong Signals"],
        ["📌 My Tracked Signals", "📊 Performance"],
        ["📚 Signal History", "⚙️ Settings"],
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


def _user_id(update):
    return str(update.effective_user.id)


def _market_keyboard():
    rows = []

    for start in range(0, len(MARKET_NAMES), 2):
        row = []

        for market in MARKET_NAMES[start:start + 2]:
            row.append(
                InlineKeyboardButton(
                    market,
                    callback_data=f"market:{market}",
                )
            )

        rows.append(row)

    return InlineKeyboardMarkup(rows)


def _timeframe_keyboard(market):
    rows = []

    for start in range(0, len(TIMEFRAMES), 2):
        row = []

        for label, timeframe in TIMEFRAMES[start:start + 2]:
            row.append(
                InlineKeyboardButton(
                    label,
                    callback_data=f"tf:{market}:{timeframe}",
                )
            )

        rows.append(row)

    return InlineKeyboardMarkup(rows)


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
    context = result.get("higher_timeframe", "Not available")

    return (
        f"{text}\n\n"
        f"Strategy: {strategy_name}\n"
        f"Quality score: {score}/100\n"
        f"1H context: {context}\n\n"
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
        "🔎 MARKET SCANNER\n\nChoose a market to analyse:",
        reply_markup=_market_keyboard(),
    )


async def show_strong_signals(update, context):
    opportunities = state.get_opportunities(limit=20)
    now = datetime.now(WAT)
    strong = []
    for item in opportunities:
        created = item.get("created_at")
        try:
            created_at = datetime.fromisoformat(
                str(created).replace("Z", "+00:00")
            ).astimezone(WAT)
        except (TypeError, ValueError):
            continue
        if (
            item.get("direction") in ("BUY", "SELL")
            and float(item.get("score", 0)) >= 70
            and (now - created_at).total_seconds() <= 24 * 60 * 60
        ):
            strong.append(item)
    await update.message.reply_text(
        "🔥 STRONG SIGNALS\n\nFresh automatic opportunities. "
        "These are not tracked until you choose to track them.",
        reply_markup=MENU,
    )
    if not strong:
        await update.message.reply_text(
            "No fresh qualifying opportunities are currently available.",
            reply_markup=MENU,
        )
        return
    for item in strong[:10]:
        oid = item.get("id")
        text = (
            f"🔥 OPPORTUNITY #{oid}\n\n"
            f"Market: {item.get('symbol', 'N/A')}\n"
            f"Direction: {item.get('direction', 'N/A')}\n"
            f"Timeframe: {item.get('timeframe', '15m')}\n"
            f"Entry: {item.get('entry', 'N/A')}\n"
            f"Stop loss: {item.get('stop_loss', 'N/A')}\n"
            f"TP1: {item.get('tp1', 'N/A')}\n"
            f"TP2: {item.get('tp2', 'N/A')}\n"
            f"TP3: {item.get('tp3', 'N/A')}\n"
            f"Quality: {item.get('score', 0)}/100\n"
            f"Strategy: {item.get('strategy', 'N/A')}"
        )
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("📌 TRACK THIS SIGNAL", callback_data=f"optrack:{oid}")
        ]])
        await update.message.reply_text(text, reply_markup=keyboard)


async def show_tracked_signals(update, context):
    user_id = _user_id(update)
    signals = state.get_tracked_signals(user_id)
    report = create_tracked_report(signals)
    await update.message.reply_photo(
        photo=report,
        caption="📌 MY TRACKED SIGNALS — visual overview",
    )
    if not signals:
        await update.message.reply_text(
            "You are not tracking any signals yet.",
            reply_markup=MENU,
        )
        return
    for signal in signals[:15]:
        signal_id = signal["id"]
        text = (
            f"📌 TRACKED SIGNAL #{signal_id}\n\n"
            f"Market: {signal.get('symbol', 'N/A')}\n"
            f"Direction: {signal.get('direction', 'N/A')}\n"
            f"Timeframe: {signal.get('timeframe', 'N/A')}\n"
            f"Entry: {signal.get('entry', 'N/A')}\n"
            f"Stop loss: {signal.get('stop_loss', 'N/A')}\n"
            f"TP1: {signal.get('tp1', 'N/A')}\n"
            f"TP2: {signal.get('tp2', 'N/A')}\n"
            f"TP3: {signal.get('tp3', 'N/A')}\n"
            f"Realised R: {signal.get('realised_r', 0)}\n"
            f"Status: {signal.get('status', 'N/A')}"
        )
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("Stop tracking", callback_data=f"stop:{signal_id}")
        ]])
        await update.message.reply_text(text, reply_markup=keyboard)


def _performance_summary(signals):
    signals = list(signals or [])
    completed = [s for s in signals if s.get("status") in {
        "TP3", "STOP_LOSS", "INVALIDATED", "AMBIGUOUS", "MANUALLY_STOPPED"
    }]
    eligible = [s for s in signals if s.get("status") in {"TP3", "STOP_LOSS"}]
    wins = sum(s.get("status") == "TP3" for s in eligible)
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
        f"Win rate (TP3 vs SL): {win_rate:.1f}%\n"
        f"TP achievements: TP1 {tp[0]} | TP2 {tp[1]} | TP3 {tp[2]}\n"
        f"Cumulative realised R: {total_r:+.2f}R\n\n"
        "Manually stopped, invalidated and ambiguous signals are excluded "
        "from win/loss statistics. This is signal analytics, not broker P/L."
    )


def _performance_period(signals, period):
    now = datetime.now(WAT)
    if period == "day":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        label = "Today"
    elif period == "week":
        start = (now - __import__("datetime").timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        label = "This week"
    elif period == "month":
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        label = "This month"
    else:
        return list(signals), "All time"

    filtered = []
    for signal in signals:
        try:
            created = datetime.fromisoformat(
                str(signal.get("created_at")).replace("Z", "+00:00")
            ).astimezone(WAT)
        except (TypeError, ValueError):
            continue
        if created >= start:
            filtered.append(signal)
    return filtered, label


def _performance_period_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Today", callback_data="perf:day"),
            InlineKeyboardButton("This week", callback_data="perf:week"),
        ],
        [
            InlineKeyboardButton("This month", callback_data="perf:month"),
            InlineKeyboardButton("All time", callback_data="perf:all"),
        ],
    ])


async def _send_performance(chat, user_id, period):
    all_signals = state.get_signal_history(user_id, limit=10000)
    signals, label = _performance_period(all_signals, period)
    report = create_performance_report(signals, period_label=label)
    await chat.send_photo(
        photo=report,
        caption=f"📊 PERFORMANCE CENTRE — {label}",
        reply_markup=_performance_period_keyboard(),
    )
    await chat.send_message(
        _performance_summary(signals),
        reply_markup=MENU,
    )


async def show_performance(update, context):
    await _send_performance(update.effective_chat, _user_id(update), "all")


async def show_history(update, context):
    signals = state.get_signal_history(_user_id(update), limit=30)
    report = create_history_report(signals)
    await update.message.reply_photo(
        photo=report,
        caption="📚 SIGNAL HISTORY — visual history report",
    )
    if not signals:
        await update.message.reply_text("No saved signals yet.", reply_markup=MENU)
        return
    lines = ["📚 RECENT SIGNAL HISTORY\n"]
    for signal in signals[:20]:
        lines.append(
            f"#{signal.get('id')} {signal.get('symbol', 'N/A')} "
            f"{signal.get('direction', 'N/A')}\n"
            f"Status: {signal.get('status', 'N/A')} | "
            f"Score: {signal.get('score', 0)}/100 | "
            f"Realised R: {signal.get('realised_r', 0)}"
        )
    await update.message.reply_text("\n".join(lines), reply_markup=MENU)


async def show_settings(update, context):
    user_id = _user_id(update)
    settings = state.get_user_settings(user_id)
    timeframe = state.get_timeframe(user_id)
    usage = state.get_api_usage()

    text = (
        "⚙️ PERSONAL SETTINGS\n\n"
        f"Default timeframe: {timeframe}\n"
        f"Preferred markets: {', '.join(settings.get('markets', []))}\n"
        f"Risk preference: {settings.get('risk_percent', 1.0)}%\n"
        f"Timezone: {settings.get('timezone', 'Africa/Lagos')}\n\n"
        "API safety counter:\n"
        f"{usage['used']} / {usage['limit']} requests recorded today\n"
        f"Remaining local request allowance: {usage['remaining']}\n\n"
        "The API counter counts local requests; it is not a live "
        "Twelve Data credit statement."
    )

    keyboard = InlineKeyboardMarkup([
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
    ])

    await update.message.reply_text(
        text,
        reply_markup=keyboard,
    )


async def analyse_market(update, context, symbol, timeframe):
    user_id = _user_id(update)

    if symbol not in MARKETS:
        await update.callback_query.edit_message_text(
            "That market is not configured."
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
        )

        result["symbol"] = symbol

        if result.get("status") not in ("BUY", "SELL"):
            await update.effective_chat.send_message(
                f"🔎 {symbol} — NO QUALIFYING SETUP\n\n"
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
        )

        signal_id = (
            f"manual-{user_id}-"
            f"{int(datetime.now().timestamp())}"
        )
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton(
                "📌 TRACK THIS SIGNAL",
                callback_data=f"track:{signal_id}",
            )
        ]])
        context.user_data[signal_id] = {
            "symbol": symbol,
            "direction": result["status"],
            "timeframe": timeframe,
            "entry": result.get("entry"),
            "stop_loss": result.get("stop_loss"),
            "take_profits": result.get("take_profits", []),
            "score": result.get("score", 0),
            "strategy": result.get("strategy", ""),
            "candle_time": str(frame.index[-1]),
        }

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


async def handle_callback(update, context):
    query = update.callback_query
    await query.answer()

    data = query.data or ""
    user_id = _user_id(update)

    if data.startswith("market:"):
        market = data.split(":", 1)[1]

        await query.edit_message_text(
            f"Choose a timeframe for {market}:",
            reply_markup=_timeframe_keyboard(market),
        )
        return

    if data.startswith("tf:"):
        parts = data.split(":")

        if len(parts) != 3:
            return

        await analyse_market(
            update,
            context,
            parts[1],
            parts[2],
        )
        return

    if data.startswith("perf:"):
        period = data.split(":", 1)[1]
        if period not in {"day", "week", "month", "all"}:
            return
        await _send_performance(query.message.chat, user_id, period)
        return

    if data.startswith("optrack:"):
        opportunity_id = data.split(":", 1)[1]
        opportunity = state.get_opportunity(opportunity_id)
        if not opportunity:
            await query.edit_message_text("This opportunity is no longer available.")
            return
        if opportunity.get("direction") not in ("BUY", "SELL"):
            await query.edit_message_text("This opportunity is not trackable.")
            return
        targets = opportunity.get("take_profits") or []
        record = state.add_signal(user_id, {
            "symbol": opportunity.get("symbol"),
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
        })
        await query.edit_message_text(
            f"✅ Opportunity saved as tracked signal #{record['id']}. "
            "Automatic monitoring will now follow this signal."
        )
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

        record = state.add_signal(
            user_id,
            {
                "symbol": pending["symbol"],
                "direction": pending["direction"],
                "timeframe": pending["timeframe"],
                "entry": pending.get("entry"),
                "stop_loss": pending.get("stop_loss"),
                "tp1": take_profits[0] if len(take_profits) > 0 else None,
                "tp2": take_profits[1] if len(take_profits) > 1 else None,
                "tp3": take_profits[2] if len(take_profits) > 2 else None,
                "take_profits": take_profits,
                "score": pending.get("score", 0),
                "strategy": pending.get("strategy", ""),
                "candle_time": pending.get("candle_time"),
            },
        )

        context.user_data.pop(signal_id, None)

        await query.edit_message_text(
            f"✅ SIGNAL #{record['id']} SAVED\n\n"
            f"{record['symbol']} {record['direction']}\n"
            "It has been added to your tracked signals.\n\n"
            "Automatic milestone monitoring is active for this signal."
        )
        return

    if data.startswith("stop:"):
        signal_id = data.split(":", 1)[1]

        stopped = state.stop_tracking_signal(
            signal_id,
            user_id,
        )

        await query.edit_message_text(
            "Tracking stopped. The signal history is preserved. "
            "This is not recorded as a win or loss."
            if stopped
            else "This signal could not be stopped. "
                 "It may already be completed or belong to another user."
        )
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
                InlineKeyboardButton(
                    "0.5%",
                    callback_data="risk:0.5",
                ),
                InlineKeyboardButton(
                    "1%",
                    callback_data="risk:1",
                ),
                InlineKeyboardButton(
                    "2%",
                    callback_data="risk:2",
                ),
            ]
        ]

        await query.edit_message_text(
            "Choose your preferred risk percentage:",
            reply_markup=InlineKeyboardMarkup(rows),
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
            "Always calculate lot size after choosing a valid technical stop loss."
        )
        return


async def handle_text(update, context):
    text = (update.message.text or "").strip()

    if text == "🔎 Market Scanner":
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
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CallbackQueryHandler(handle_callback))
    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_text,
        )
    )
