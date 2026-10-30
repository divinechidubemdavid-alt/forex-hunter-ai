
"""Forex Hunter AI Telegram bot. Manual analysis only; never places orders."""

import asyncio
import logging
from typing import Any

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    BotCommand,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

import state
from config import (
    BOT_NAME,
    TELEGRAM_BOT_TOKEN,
    TWELVE_DATA_API_KEY,
    MARKETS,
    TIMEFRAMES,
)
from market_data import fetch_candles, MarketDataError
from strategy import analyze, format_signal
from chart import create_market_chart


logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

ACTIVE_CHECK_SECONDS = 300

MAIN_MENU = [
    ["🔎 Scan Market"],
    ["➕ Register Trade", "📋 My Active Trades"],
    ["📈 API Usage", "🔔 Alert Settings"],
    ["✅ Close Trade", "❓ Help"],
]


# ---------------------------------------------------------------------------
# Compatibility helpers: use the function names in the current state.py.
# ---------------------------------------------------------------------------

def _ensure_user(user_id: int) -> None:
    if hasattr(state, "ensure_user"):
        state.ensure_user(user_id)
    elif hasattr(state, "register_user"):
        state.register_user(user_id)


def _get_timeframe(user_id: int) -> str:
    if hasattr(state, "get_timeframe"):
        return state.get_timeframe(user_id)
    if hasattr(state, "get_user_timeframe"):
        return state.get_user_timeframe(user_id)
    return getattr(__import__("config"), "DEFAULT_TIMEFRAME", "5m")


def _set_timeframe(user_id: int, timeframe: str) -> None:
    if hasattr(state, "set_timeframe"):
        state.set_timeframe(user_id, timeframe)
    elif hasattr(state, "set_user_timeframe"):
        state.set_user_timeframe(user_id, timeframe)


def _api_usage() -> dict:
    if hasattr(state, "get_api_usage"):
        return state.get_api_usage()
    if hasattr(state, "api_usage"):
        return state.api_usage()
    return {"used": 0, "limit": 800, "remaining": 800, "date": "unknown"}


def _alerts_enabled(user_id: int) -> bool:
    if hasattr(state, "user_monitoring_enabled"):
        return bool(state.user_monitoring_enabled(user_id))
    return True


def _set_alerts_enabled(user_id: int, enabled: bool) -> None:
    if hasattr(state, "set_user_monitoring"):
        state.set_user_monitoring(user_id, enabled)


def _all_active_trades() -> list[tuple[int, dict]]:
    """Return (user_id, trade) pairs for either list- or dict-based state APIs."""
    raw = state.get_active_trades()
    pairs: list[tuple[int, dict]] = []

    if isinstance(raw, dict):
        for key, value in raw.items():
            if not isinstance(value, dict):
                continue
            # Some versions return {user_id: trade}; others return {trade_id: trade}.
            uid = value.get("user_id")
            if uid is None:
                try:
                    uid = int(key)
                except (TypeError, ValueError):
                    continue
            pairs.append((int(uid), value))
    elif isinstance(raw, (list, tuple)):
        for trade in raw:
            if not isinstance(trade, dict):
                continue
            uid = trade.get("user_id")
            if uid is None:
                continue
            pairs.append((int(uid), trade))

    return pairs


def _active_trades_for_user(user_id: int) -> list[dict]:
    """Return every active trade belonging to this user."""
    if hasattr(state, "get_active_trades"):
        try:
            return state.get_active_trades(user_id)
        except TypeError:
            return [
                trade for uid, trade in _all_active_trades()
                if uid == int(user_id)
            ]
    trade = state.get_active_trade(user_id) if hasattr(state, "get_active_trade") else None
    return [trade] if trade else []


def _active_trade_for_user(user_id: int) -> dict | None:
    """Compatibility helper: return the most recently registered active trade."""
    trades = _active_trades_for_user(user_id)
    return trades[-1] if trades else None


def _add_trade(user_id: int, trade: dict) -> dict | None:
    if hasattr(state, "add_trade"):
        return state.add_trade(user_id, trade)
    if hasattr(state, "set_active_trade"):
        return state.set_active_trade(user_id, trade)
    raise RuntimeError("state.py has no add_trade/set_active_trade function.")


def _close_specific_trade(user_id: int, trade_id: str) -> bool:
    """Close only the selected trade, never another active trade."""
    if hasattr(state, "close_trade"):
        return bool(state.close_trade(str(trade_id), user_id))
    if hasattr(state, "close_active_trade"):
        return bool(state.close_active_trade(user_id))
    return False


def _close_user_trade(user_id: int) -> dict | None:
    """Close the most recently registered active trade for /close."""
    trade = _active_trade_for_user(user_id)
    if not trade:
        return None
    trade_id = trade.get("id", trade.get("trade_id"))
    if trade_id is None or not _close_specific_trade(user_id, str(trade_id)):
        raise RuntimeError("The selected trade could not be closed.")
    return trade


def _set_trade_status(user_id: int, trade: dict, status: str,
                      candle_time: Any, alert_state: str) -> None:
    """Persist monitoring status when supported; don't break monitoring otherwise."""
    if hasattr(state, "update_trade_monitor_status"):
        try:
            state.update_trade_monitor_status(
                user_id, status, candle_time, alert_state
            )
            return
        except TypeError:
            # Accommodate versions whose function takes a trade ID first.
            trade_id = trade.get("id", trade.get("trade_id"))
            state.update_trade_monitor_status(
                trade_id, status, candle_time, alert_state
            )
            return
    if hasattr(state, "update_trade"):
        try:
            state.update_trade(
                trade.get("id", trade.get("trade_id")),
                {"last_status": status, "last_candle_time": str(candle_time),
                 "last_alert_state": alert_state},
            )
        except Exception:
            logger.debug("Trade status could not be persisted.", exc_info=True)


def _should_send_alert(trade: dict, alert_state: str) -> bool:
    """Suppress repeated alerts where the state API supports alert persistence."""
    old = trade.get("last_alert_state")
    trade_id = trade.get("id", trade.get("trade_id", "unknown"))
    key = f"trade-alert:{trade.get('user_id', '')}:{trade_id}"
    if hasattr(state, "get_last_alert"):
        try:
            old = state.get_last_alert(key)
        except Exception:
            pass
    changed = alert_state != old
    if changed and hasattr(state, "set_last_alert"):
        try:
            state.set_last_alert(key, alert_state)
        except Exception:
            pass
    return changed


# ---------------------------------------------------------------------------
# Keyboards and formatting
# ---------------------------------------------------------------------------

def main_keyboard():
    return ReplyKeyboardMarkup(MAIN_MENU, resize_keyboard=True, is_persistent=True)


def _market_names():
    return list(MARKETS.keys()) if isinstance(MARKETS, dict) else list(MARKETS)


def _provider_symbol(symbol: str):
    return MARKETS[symbol] if isinstance(MARKETS, dict) else symbol


def market_keyboard(action: str):
    rows = [
        [InlineKeyboardButton(symbol, callback_data=f"{action}|{symbol}")]
        for symbol in _market_names()
    ]
    rows.append([InlineKeyboardButton("⬅️ Main menu", callback_data="HOME")])
    return InlineKeyboardMarkup(rows)


def timeframe_keyboard(action: str, symbol: str):
    rows = [[
        InlineKeyboardButton(
            timeframe, callback_data=f"{action}|{symbol}|{timeframe}"
        )
        for timeframe in TIMEFRAMES
    ]]
    rows.append([InlineKeyboardButton("⬅️ Main menu", callback_data="HOME")])
    return InlineKeyboardMarkup(rows)


def _price(value, symbol):
    if "JPY" in symbol:
        digits = 3
    elif symbol == "XAU/USD":
        digits = 2
    else:
        digits = 5
    return f"{float(value):.{digits}f}"


def normalise_symbol(value):
    symbol = value.strip().upper().replace(" ", "")
    aliases = {
        "EURUSD": "EUR/USD",
        "GBPUSD": "GBP/USD",
        "USDJPY": "USD/JPY",
        "XAUUSD": "XAU/USD",
    }
    return aliases.get(symbol, symbol)


def _format_trade(trade: dict) -> str:
    symbol = trade.get("symbol", "Unknown")
    targets = trade.get("take_profits", [])
    target_text = "\n".join(
        f"TP{index}: {_price(value, symbol)}"
        for index, value in enumerate(targets, 1)
    )
    return (
        f"Market: {symbol}\n"
        f"Direction: {trade.get('direction', 'Unknown')}\n"
        f"Entry: {_price(trade.get('entry', 0), symbol)}\n"
        f"Stop loss: {_price(trade.get('stop_loss', 0), symbol)}\n"
        f"{target_text}\n"
        f"Status: {trade.get('last_status', 'Registered')}"
    )


# ---------------------------------------------------------------------------
# Commands and menu actions
# ---------------------------------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    _ensure_user(user_id)
    message = (
        f"Welcome to {BOT_NAME}!\n\n"
        "Forex trend analysis using EMA 9/21 and ADX.\n\n"
        "Supported markets: EUR/USD, GBP/USD, USD/JPY and XAU/USD (Gold).\n\n"
        "Choose a button below to begin.\n\n"
        "⚠️ ANALYSIS ONLY: this bot never places orders. Signals are not "
        "guaranteed profits. Always verify prices with your broker."
    )
    await update.effective_message.reply_text(message, reply_markup=main_keyboard())


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = (
        "Forex Hunter AI — Help\n\n"
        "🔎 Scan Market — choose one market and timeframe to scan\n"
        "➕ Register Trade — register a placed trade using market, timeframe, direction and entry\n"
        "📋 My Active Trades — view your registered trade\n"
        "📈 API Usage — view Twelve Data usage\n"
        "🔔 Alert Settings — enable or disable trade alerts\n"
        "✅ Close Trade — stop tracking your registered trade\n\n"
        "Commands: /start /help /scan (or /analyze) /timeframe /register "
        "/active /close /api /status /paper /alerts_on /alerts_off\n\n"
        "Registered trades are checked every five minutes while the bot "
        "process is running. No orders are ever placed."
    )
    await update.effective_message.reply_text(message, reply_markup=main_keyboard())


async def show_scan_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "Choose ONE market to scan:", reply_markup=market_keyboard("SCAN")
    )


async def choose_timeframe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(tf, callback_data=f"SETTF|{tf}") for tf in TIMEFRAMES],
        [InlineKeyboardButton("⬅️ Main menu", callback_data="HOME")],
    ])
    await update.effective_message.reply_text(
        "Choose your default entry timeframe:", reply_markup=keyboard
    )


async def do_analysis(chat_id, user_id, symbol, timeframe, context):
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"Checking {symbol} on {timeframe} using completed candles…",
    )
    try:
        frame = await fetch_candles(_provider_symbol(symbol))
        result = analyze(frame, timeframe)
        result["symbol"] = symbol
        await context.bot.send_message(
            chat_id=chat_id, text=format_signal(symbol, result)
        )

        if result.get("status") in ("BUY", "SELL"):
            try:
                chart = create_market_chart(
                    result.get("candles", frame),
                    symbol,
                    timeframe,
                    entry=result.get("entry"),
                    stop_loss=result.get("stop_loss"),
                    take_profits=result.get("take_profits"),
                )
                await context.bot.send_photo(
                    chat_id=chat_id,
                    photo=chart,
                    caption=f"{symbol} {timeframe} chart\nAnalysis only — no order was placed.",
                )
            except Exception:
                logger.exception("Chart creation failed for %s", symbol)
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="The analysis completed, but the chart could not be created.",
                )
    except MarketDataError as exc:
        await context.bot.send_message(chat_id=chat_id, text=f"Market data unavailable: {exc}")
    except Exception:
        logger.exception("Analysis failed for %s", symbol)
        await context.bot.send_message(
            chat_id=chat_id,
            text="Analysis failed unexpectedly. Check the Codespaces terminal for details.",
        )


async def scan_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    _ensure_user(user_id)
    if not context.args:
        await show_scan_menu(update, context)
        return
    symbol = normalise_symbol(context.args[0])
    if symbol not in _market_names():
        await update.effective_message.reply_text(
            "Supported markets: EUR/USD, GBP/USD, USD/JPY and XAU/USD.\nExample: /scan EUR/USD"
        )
        return
    await do_analysis(update.effective_chat.id, user_id, symbol, _get_timeframe(user_id), context)


async def analyze_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    _ensure_user(user_id)
    if not context.args:
        await show_scan_menu(update, context)
        return
    symbol = normalise_symbol("".join(context.args))
    if symbol not in _market_names():
        await update.effective_message.reply_text(
            "Supported markets: EUR/USD, GBP/USD, USD/JPY and XAU/USD.\nExample: /analyze EUR/USD"
        )
        return
    await do_analysis(update.effective_chat.id, user_id, symbol, _get_timeframe(user_id), context)


async def api_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    usage = _api_usage()
    await update.effective_message.reply_text(
        "Twelve Data daily safety budget (UTC)\n\n"
        f"Date: {usage.get('date', 'unknown')}\n"
        f"Requests reserved: {usage.get('used', 0)}\n"
        f"Safety budget: {usage.get('limit', 800)}\n"
        f"Remaining: {usage.get('remaining', 0)}\n\n"
        "Requests reserved before failed calls may still count. Cached data can avoid extra requests.",
        reply_markup=main_keyboard(),
    )


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    _ensure_user(user_id)
    usage = _api_usage()
    trade = _active_trade_for_user(user_id)
    await update.effective_message.reply_text(
        f"{BOT_NAME} status\n\n"
        f"Twelve Data key: {'configured' if TWELVE_DATA_API_KEY else 'MISSING'}\n"
        f"API requests reserved today (UTC): {usage.get('used', 0)}/{usage.get('limit', 800)}\n"
        f"Safety budget remaining: {usage.get('remaining', 0)}\n"
        f"Your timeframe: {_get_timeframe(user_id)}\n"
        f"Your active trade: {'yes' if trade else 'no'}\n"
        "Automatic trade execution: OFF",
        reply_markup=main_keyboard(),
    )


async def timeframe_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    _ensure_user(user_id)
    if context.args:
        timeframe = context.args[0].lower()
        if timeframe not in TIMEFRAMES:
            await update.effective_message.reply_text("Choose 1m, 5m or 15m.")
            return
        _set_timeframe(user_id, timeframe)
        await update.effective_message.reply_text(
            f"Your default timeframe is now {timeframe}.", reply_markup=main_keyboard()
        )
        return
    await choose_timeframe(update, context)


async def paper_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "Manual trade tracking\n\nEnter your own entry, stop loss and take-profit levels. "
        "Forex Hunter AI monitors the registered levels while running. It does not connect "
        "to a broker or place orders. Consider practising on a demo account."
    )


async def alerts_on(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    _ensure_user(user_id)
    _set_alerts_enabled(user_id, True)
    await update.effective_message.reply_text("Your trade-monitoring alerts are ON.", reply_markup=main_keyboard())


async def alerts_off(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    _ensure_user(user_id)
    _set_alerts_enabled(user_id, False)
    await update.effective_message.reply_text(
        "Your trade-monitoring alerts are OFF. Your registered trade remains saved.",
        reply_markup=main_keyboard(),
    )


async def register_trade_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    _ensure_user(user_id)
    context.user_data["trade_draft"] = {}
    context.user_data.pop("trade_step", None)
    await update.effective_message.reply_text(
        "Choose the market for the trade you want to track:",
        reply_markup=market_keyboard("REGISTER"),
    )


async def show_active(update: Update, context: ContextTypes.DEFAULT_TYPE):
    trades = _active_trades_for_user(update.effective_user.id)
    if not trades:
        await update.effective_message.reply_text(
            "You have no registered active trades. Tap ➕ Register Trade to add one.",
            reply_markup=main_keyboard(),
        )
        return

    for trade in trades:
        trade_id = str(trade.get("id", trade.get("trade_id", "")))
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(
                f"✅ Close {trade.get('symbol', 'trade')} #{trade_id}",
                callback_data=f"CLOSETRADE|{trade_id}",
            )]
        ]) if trade_id else None
        await update.effective_message.reply_text(
            "Your registered active trade\\n" + _format_trade(trade),
            reply_markup=keyboard,
        )
    await update.effective_message.reply_text(
        "Your trades are saved separately. Closing one trade will not close the others.",
        reply_markup=main_keyboard(),
    )


async def close_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        trade = _close_user_trade(update.effective_user.id)
    except Exception as exc:
        logger.exception("Could not close trade")
        await update.effective_message.reply_text(
            f"I couldn't close the trade because of a state-file mismatch: {exc}",
            reply_markup=main_keyboard(),
        )
        return
    await update.effective_message.reply_text(
        f"Stopped tracking your {trade.get('symbol', 'market')} trade. No further monitoring alerts will be sent."
        if trade else "You have no active registered trade to close.",
        reply_markup=main_keyboard(),
    )


async def alert_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    _ensure_user(user_id)
    enabled = _alerts_enabled(user_id)
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"Turn alerts {'OFF' if enabled else 'ON'}", callback_data="ALERTS")],
        [InlineKeyboardButton("⬅️ Main menu", callback_data="HOME")],
    ])
    await update.effective_message.reply_text(
        f"Your trade-monitoring alerts are currently {'ON' if enabled else 'OFF'}.",
        reply_markup=keyboard,
    )


# ---------------------------------------------------------------------------
# Button callbacks and trade registration conversation
# ---------------------------------------------------------------------------

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    user_id = query.from_user.id
    _ensure_user(user_id)

    if data == "HOME":
        await query.message.reply_text("Main menu:", reply_markup=main_keyboard())
        return

    if data.startswith("SETTF|"):
        timeframe = data.split("|", 1)[1]
        if timeframe in TIMEFRAMES:
            _set_timeframe(user_id, timeframe)
            await query.message.reply_text(f"Entry timeframe set to {timeframe}.", reply_markup=main_keyboard())
        return

    if data.startswith("SCAN|"):
        parts = data.split("|")
        action = "SCAN"
        if len(parts) == 2:
            symbol = parts[1]
            if symbol in _market_names():
                await query.message.reply_text(
                    f"Choose timeframe for {symbol}:",
                    reply_markup=timeframe_keyboard(action, symbol),
                )
            return
        if len(parts) == 3:
            _, symbol, timeframe = parts
            if symbol in _market_names() and timeframe in TIMEFRAMES:
                _set_timeframe(user_id, timeframe)
                await do_analysis(query.message.chat_id, user_id, symbol, timeframe, context)
            return

    if data.startswith("REGISTER|"):
        symbol = data.split("|", 1)[1]
        if symbol in _market_names():
            context.user_data["trade_draft"] = {"symbol": symbol}
            context.user_data["trade_step"] = "direction"
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("BUY", callback_data="DIRECTION|BUY"),
                InlineKeyboardButton("SELL", callback_data="DIRECTION|SELL"),
            ]])
            await query.message.reply_text(
                f"Registering {symbol}. Choose the direction:",
                reply_markup=keyboard,
            )
        return

    if data.startswith("DIRECTION|"):
        direction = data.split("|", 1)[1]
        draft = context.user_data.get("trade_draft")
        if draft and direction in ("BUY", "SELL"):
            draft["direction"] = direction
            context.user_data["trade_step"] = "entry"
            await query.message.reply_text("Send the entry price using numbers only.")
        return

    if data == "ACTIVE":
        trade = _active_trade_for_user(user_id)
        await query.message.reply_text(
            "No active registered trade." if not trade else "Your registered trade\n" + _format_trade(trade),
            reply_markup=main_keyboard(),
        )
        return

    if data.startswith("CLOSETRADE|"):
        trade_id = data.split("|", 1)[1]
        try:
            trade = state.get_trade(trade_id, user_id) if hasattr(state, "get_trade") else None
            if trade and _close_specific_trade(user_id, trade_id):
                await query.message.reply_text(
                    f"Stopped tracking {trade.get('symbol', 'your trade')} #{trade_id}. Other trades remain active.",
                    reply_markup=main_keyboard(),
                )
            else:
                await query.message.reply_text(
                    "That trade is already closed or could not be found.",
                    reply_markup=main_keyboard(),
                )
        except Exception:
            logger.exception("Could not close selected trade")
            await query.message.reply_text(
                "I couldn't close that trade. Check the Codespaces terminal.",
                reply_markup=main_keyboard(),
            )
        return

    if data == "CLOSE":
        await close_command_from_callback(query, user_id)
        return

    if data == "API":
        usage = _api_usage()
        await query.message.reply_text(
            f"Twelve Data budget (UTC): {usage.get('used', 0)}/{usage.get('limit', 800)} requests reserved; "
            f"{usage.get('remaining', 0)} remaining.",
            reply_markup=main_keyboard(),
        )
        return

    if data == "ALERTS":
        enabled = not _alerts_enabled(user_id)
        _set_alerts_enabled(user_id, enabled)
        await query.message.reply_text(
            f"Trade-monitoring alerts are {'ON' if enabled else 'OFF'}.",
            reply_markup=main_keyboard(),
        )
        return

    if data == "HELP":
        await query.message.reply_text(
            "Choose Scan Market to analyse one market, Register Trade to track your own levels, "
            "and Close Trade to stop tracking. No orders are placed.",
            reply_markup=main_keyboard(),
        )


async def close_command_from_callback(query, user_id):
    try:
        trade = _close_user_trade(user_id)
        message = (
            f"Stopped tracking {trade.get('symbol', 'your market')}."
            if trade else "No active registered trade."
        )
    except Exception:
        logger.exception("Could not close trade from button")
        message = "I couldn't close the trade. Check the Codespaces terminal."
    await query.message.reply_text(message, reply_markup=main_keyboard())


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message_text = (update.effective_message.text or "").strip()
    user_id = update.effective_user.id
    _ensure_user(user_id)

    buttons = {
        "🔎 Scan Market": show_scan_menu,
        "➕ Register Trade": register_trade_command,
        "📋 My Active Trades": show_active,
        "📈 API Usage": api_command,
        "🔔 Alert Settings": alert_settings,
        "✅ Close Trade": close_command,
        "❓ Help": help_command,
    }
    if message_text in buttons:
        await buttons[message_text](update, context)
        return

    draft = context.user_data.get("trade_draft")
    step = context.user_data.get("trade_step")
    if not draft or not step:
        return

    if step == "direction":
        direction = message_text.upper()
        if direction not in ("BUY", "SELL"):
            await update.effective_message.reply_text("Please choose BUY or SELL using the buttons.")
            return
        draft["direction"] = direction
        context.user_data["trade_step"] = "entry"
        await update.effective_message.reply_text("Send the entry price using numbers only.")
        return

    try:
        value = float(message_text.replace(",", ""))
        if value <= 0:
            raise ValueError
    except ValueError:
        await update.effective_message.reply_text("Please send a valid positive price number.")
        return

    prompts = {
        "entry": ("entry", "stop_loss", "Send the stop-loss price."),
        "stop_loss": ("stop_loss", "tp1", "Send the TP1 price."),
        "tp1": ("tp1", "tp2", "Send the TP2 price."),
        "tp2": ("tp2", "tp3", "Send the TP3 price."),
    }
    if step in prompts:
        field, next_step, prompt = prompts[step]
        if field == "tp1":
            draft["take_profits"] = [value]
        elif field == "tp2":
            draft["take_profits"].append(value)
        else:
            draft[field] = value
        context.user_data["trade_step"] = next_step
        await update.effective_message.reply_text(prompt)
        return

    if step == "tp3":
        draft.setdefault("take_profits", []).append(value)
        entry = float(draft["entry"])
        stop_loss = float(draft["stop_loss"])
        targets = [float(v) for v in draft["take_profits"]]
        direction = draft["direction"]
        if direction == "BUY":
            valid = stop_loss < entry and all(t > entry for t in targets) and targets == sorted(targets)
        else:
            valid = stop_loss > entry and all(t < entry for t in targets) and targets == sorted(targets, reverse=True)

        if not valid:
            await update.effective_message.reply_text(
                "These levels don't match the chosen direction or TP order. Please start again with ➕ Register Trade.",
                reply_markup=main_keyboard(),
            )
            context.user_data.pop("trade_draft", None)
            context.user_data.pop("trade_step", None)
            return

        draft["timeframe"] = _get_timeframe(user_id)
        draft["last_status"] = "Registered"
        draft["last_alert_state"] = "normal"
        try:
            _add_trade(user_id, dict(draft))
        except Exception as exc:
            logger.exception("Could not save trade")
            await update.effective_message.reply_text(
                f"I couldn't save this trade because of a state-file mismatch: {exc}",
                reply_markup=main_keyboard(),
            )
            return

        context.user_data.pop("trade_draft", None)
        context.user_data.pop("trade_step", None)
        await update.effective_message.reply_text(
            "Trade registered successfully.\n\nI will check this registered trade every five minutes "
            "while the bot is running and your alerts are enabled. No orders will be placed.",
            reply_markup=main_keyboard(),
        )


# ---------------------------------------------------------------------------
# Active-trade monitor: one market-data fetch per distinct active symbol/cycle.
# ---------------------------------------------------------------------------

async def active_trade_loop(app: Application):
    while True:
        try:
            trades = _all_active_trades()
            if trades:
                candles_by_symbol = {}
                for user_id, trade in trades:
                    symbol = trade.get("symbol")
                    if symbol not in _market_names() or symbol in candles_by_symbol:
                        continue
                    try:
                        candles_by_symbol[symbol] = await fetch_candles(_provider_symbol(symbol))
                    except Exception as exc:
                        logger.warning("Monitoring fetch failed for %s: %s", symbol, exc)

                for user_id, trade in trades:
                    symbol = trade.get("symbol")
                    if symbol not in candles_by_symbol:
                        continue
                    try:
                        frame = candles_by_symbol[symbol]
                        timeframe = trade.get("timeframe", _get_timeframe(user_id))
                        result = analyze(frame, timeframe)
                        candle_time = result.get("candle_time")
                        close_price = float(frame["close"].iloc[-1])
                        direction = str(trade.get("direction", "")).upper()
                        stop_loss = float(trade.get("stop_loss"))
                        targets = [float(v) for v in trade.get("take_profits", [])]

                        status = "Trade being monitored"
                        alert_state = "normal"
                        hit = None

                        if direction == "BUY":
                            if close_price <= stop_loss:
                                hit = "Stop-loss level reached"
                            else:
                                for index, target in enumerate(targets, 1):
                                    if close_price >= target:
                                        hit = f"TP{index} level reached"
                                        break
                        elif direction == "SELL":
                            if close_price >= stop_loss:
                                hit = "Stop-loss level reached"
                            else:
                                for index, target in enumerate(targets, 1):
                                    if close_price <= target:
                                        hit = f"TP{index} level reached"
                                        break

                        if hit:
                            status, alert_state = hit, hit
                        elif result.get("status") in ("BUY", "SELL") and result.get("status") != direction:
                            status = "Trend direction changed — review trade"
                            alert_state = "direction-change"
                        elif result.get("status") == "WAIT":
                            status = "Trend weakening or no fresh confirmation — review trade"
                            alert_state = "weakening"

                        _set_trade_status(user_id, trade, status, candle_time, alert_state)

                        if (
                            _alerts_enabled(user_id)
                            and alert_state != "normal"
                            and _should_send_alert(trade, alert_state)
                        ):
                            await app.bot.send_message(
                                chat_id=int(user_id),
                                text=(
                                    "Forex Hunter AI — Trade Monitor\n"
                                    f"Market: {symbol}\n"
                                    f"Direction: {direction}\n"
                                    f"Latest price: {_price(close_price, symbol)}\n"
                                    f"Status: {status}\n\n"
                                    "Review your trade manually. No action was taken."
                                ),
                            )
                    except Exception:
                        logger.exception("Could not monitor registered trade for user %s", user_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Unexpected monitoring loop error")
        await asyncio.sleep(ACTIVE_CHECK_SECONDS)


async def post_init(app: Application):
    commands = [
        BotCommand("start", "Open the main menu"),
        BotCommand("help", "Help and command guide"),
        BotCommand("scan", "Scan one market"),
        BotCommand("analyze", "Analyse one market"),
        BotCommand("timeframe", "Choose 1m, 5m or 15m"),
        BotCommand("register", "Register a trade to monitor"),
        BotCommand("active", "View active trade"),
        BotCommand("close", "Stop tracking your trade"),
        BotCommand("status", "Bot and API status"),
        BotCommand("api", "View Twelve Data API usage"),
        BotCommand("paper", "Manual trade tracking information"),
        BotCommand("alerts_on", "Enable trade alerts"),
        BotCommand("alerts_off", "Disable trade alerts"),
    ]
    await app.bot.set_my_commands(commands)
    app.bot_data["monitor_task"] = asyncio.create_task(active_trade_loop(app))


async def post_shutdown(app: Application):
    task = app.bot_data.get("monitor_task")
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


def main():
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is missing from Codespaces Secrets.")
    if not TWELVE_DATA_API_KEY:
        logger.warning("TWELVE_DATA_API_KEY is missing. Market requests will fail until it is configured.")

    app = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("scan", scan_command))
    app.add_handler(CommandHandler("analyze", analyze_command))
    app.add_handler(CommandHandler("timeframe", timeframe_command))
    app.add_handler(CommandHandler("register", register_trade_command))
    app.add_handler(CommandHandler("active", show_active))
    app.add_handler(CommandHandler("close", close_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("api", api_command))
    app.add_handler(CommandHandler("paper", paper_command))
    app.add_handler(CommandHandler("alerts_on", alerts_on))
    app.add_handler(CommandHandler("alerts_off", alerts_off))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    logger.info("Starting %s", BOT_NAME)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
