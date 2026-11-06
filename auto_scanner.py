
import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import state
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from signal_monitor import monitor_signals_loop
from config import MARKETS, TELEGRAM_CHANNEL_ID
from market_data import fetch_candles, MarketDataError
from strategy import analyze, format_signal
from chart import create_market_chart

logger = logging.getLogger(__name__)

WAT = ZoneInfo("Africa/Lagos")

SCAN_TIMEFRAME = "15m"
SCAN_INTERVAL_SECONDS = 60
MINIMUM_SCAN_INTERVAL_MINUTES = 15

GOLD = "XAU/USD"
BITCOIN = "BTC/USD"

FOREX_MARKETS = [
    "EUR/USD",
    "GBP/USD",
    "USD/JPY",
    "AUD/USD",
    "USD/CAD",
    "USD/CHF",
]

ALL_MARKETS = [
    GOLD,
    "EUR/USD",
    "GBP/USD",
    "USD/JPY",
    "AUD/USD",
    "USD/CAD",
    "USD/CHF",
    BITCOIN,
]


def _is_weekend(now):
    return now.weekday() >= 5


def _forex_market_open(now):
    """
    Conservative forex availability window in Nigeria time.
    Avoids scanning during the normal weekend closure.
    """

    weekday = now.weekday()
    current_minutes = now.hour * 60 + now.minute

    # Sunday: wait until the evening opening period.
    if weekday == 6:
        return current_minutes >= 22 * 60

    # Friday: stop before the normal evening closure.
    if weekday == 4:
        return current_minutes < 22 * 60

    # Saturday: closed.
    if weekday == 5:
        return False

    return True


def _eligible_markets(now):
    if _is_weekend(now):
        return [BITCOIN] if BITCOIN in MARKETS else []

    markets = []

    if GOLD in MARKETS:
        markets.append(GOLD)

    if _forex_market_open(now):
        markets.extend(
            market for market in FOREX_MARKETS
            if market in MARKETS
        )

    return markets


def _next_market(now):
    """
    Select the next market using persistent rotation.

    Gold receives approximately half of available scan slots.
    Other weekday slots rotate across the six forex pairs.
    Bitcoin is scanned on weekends only.
    """

    eligible = _eligible_markets(now)

    if not eligible:
        return None

    if eligible == [BITCOIN]:
        return BITCOIN

    sequence = []

    # Two Gold slots for every two non-Gold slots.
    # This gives Gold approximately 50% of weekday scan slots.
    sequence.extend([
        GOLD,
        "EUR/USD",
        GOLD,
        "GBP/USD",
        GOLD,
        "USD/JPY",
        GOLD,
        "AUD/USD",
        GOLD,
        "USD/CAD",
        GOLD,
        "USD/CHF",
    ])

    sequence = [
        market for market in sequence
        if market in eligible
    ]

    if not sequence:
        return eligible[0]

    position = int(
        state.get_scanner_state("market_rotation_position", 0)
    )

    selected = sequence[position % len(sequence)]

    state.set_scanner_state(
        "market_rotation_position",
        position + 1,
    )

    return selected


def _destinations():
    destinations = set()

    try:
        destinations.update(
            str(user_id) for user_id in state.get_users()
        )
    except Exception:
        logger.exception("Could not load registered users.")

    if TELEGRAM_CHANNEL_ID:
        destinations.add(str(TELEGRAM_CHANNEL_ID))

    return sorted(destinations)


def _signal_key(symbol, result):
    candle_time = str(
        result.get("candle_time", "unknown")
    )

    direction = str(
        result.get("status", "unknown")
    )

    return (
        f"adaptive-signal:{symbol}:{SCAN_TIMEFRAME}:"
        f"{direction}:{candle_time}"
    )


async def _send_signal(app, symbol, result):
    """Publish an opportunity without silently adding it to user tracking."""
    message = format_signal(symbol, result)
    raw_targets = result.get("take_profits") or []
    targets = list(raw_targets) if isinstance(raw_targets, (list, tuple)) else [raw_targets]
    signal_record = {
        "symbol": symbol,
        "direction": result.get("status") or result.get("direction"),
        "timeframe": result.get("timeframe") or SCAN_TIMEFRAME,
        "entry": result.get("entry"),
        "stop_loss": result.get("stop_loss"),
        "tp1": targets[0] if len(targets) > 0 else None,
        "tp2": targets[1] if len(targets) > 1 else None,
        "tp3": targets[2] if len(targets) > 2 else None,
        "take_profits": targets,
        "score": result.get("score", 0),
        "strategy": result.get("strategy", ""),
        "candle_time": str(result.get("candle_time") or "unknown"),
        "higher_timeframe": result.get("higher_timeframe"),
    }
    opportunity_key = _signal_key(symbol, result)
    opportunity = state.add_opportunity(opportunity_key, signal_record)

    chart = None
    try:
        chart = create_market_chart(
            result.get("candles"), symbol, SCAN_TIMEFRAME,
            entry=result.get("entry"),
            stop_loss=result.get("stop_loss"),
            take_profits=targets,
        )
    except Exception:
        logger.exception("Could not create chart for %s.", symbol)

    try:
        user_ids = state.get_users()
    except Exception:
        logger.exception("Could not load registered users.")
        user_ids = []

    delivered = 0
    for user_id in user_ids:
        try:
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "📌 TRACK THIS SIGNAL",
                    callback_data=f"optrack:{opportunity['id']}",
                )
            ]])
            if chart is not None:
                chart.seek(0)
                await app.bot.send_photo(
                    chat_id=int(user_id), photo=chart,
                    caption=message, reply_markup=keyboard,
                )
            else:
                await app.bot.send_message(
                    chat_id=int(user_id), text=message,
                    reply_markup=keyboard,
                )
            delivered += 1
        except Exception:
            logger.exception("Could not send opportunity to user %s.", user_id)

    if TELEGRAM_CHANNEL_ID:
        try:
            if chart is not None:
                chart.seek(0)
                await app.bot.send_photo(
                    chat_id=int(TELEGRAM_CHANNEL_ID),
                    photo=chart, caption=message,
                )
            else:
                await app.bot.send_message(
                    chat_id=int(TELEGRAM_CHANNEL_ID), text=message,
                )
            delivered += 1
        except Exception:
            logger.exception("Could not publish opportunity to channel.")

    if delivered == 0:
        logger.warning("Opportunity for %s saved, but no destination received it.", symbol)
    return delivered


async def _scan_market(app, symbol):
    if symbol not in MARKETS:
        logger.warning(
            "Market %s is not configured.",
            symbol,
        )
        return

    try:
        frame = await fetch_candles(
            MARKETS[symbol],
            outputsize=500,
            interval="15m",
        )

        result = analyze(
            frame,
            SCAN_TIMEFRAME,
        )

        result["symbol"] = symbol

        if result.get("status") not in ("BUY", "SELL"):
            logger.info(
                "No qualifying setup for %s. No notification sent.",
                symbol,
            )
            return

        score = float(result.get("score", 0))

        if score < 70:
            logger.info(
                "Signal for %s did not reach the alert threshold.",
                symbol,
            )
            return

        key = _signal_key(symbol, result)

        if state.get_last_alert(key):
            logger.info(
                "Duplicate signal suppressed: %s",
                key,
            )
            return

        delivered = await _send_signal(app, symbol, result)
        if delivered:
            state.set_last_alert(key, "sent")
        else:
            logger.warning(
                "No delivery confirmed for %s; leaving alert eligible for retry.",
                key,
            )

    except MarketDataError as exc:
        logger.warning(
            "Market data unavailable for %s: %s",
            symbol,
            exc,
        )

    except Exception:
        logger.exception(
            "Automatic scan failed for %s.",
            symbol,
        )


async def auto_scan_loop(app):
    monitor_task = app.bot_data.get("_signal_monitor_task")
    if monitor_task is None or monitor_task.done():
        app.bot_data["_signal_monitor_task"] = asyncio.create_task(
            monitor_signals_loop(app)
        )

    """
    One central adaptive scanning loop.

    It checks the clock frequently but requests market data
    only when a new scan interval is due.
    """

    logger.info(
        "Adaptive Forex Hunter AI scanner started."
    )

    while True:
        try:
            now = datetime.now(WAT)

            last_scan = state.get_scanner_state(
                "last_scan_epoch",
                0,
            )

            current_epoch = now.timestamp()

            elapsed_minutes = (
                current_epoch - float(last_scan)
            ) / 60

            if elapsed_minutes >= MINIMUM_SCAN_INTERVAL_MINUTES:
                market = _next_market(now)

                if market:
                    # Record the slot before requesting data.
                    state.set_scanner_state(
                        "last_scan_epoch",
                        current_epoch,
                    )

                    logger.info(
                        "Adaptive scan selected %s at %s WAT.",
                        market,
                        now.isoformat(),
                    )

                    await _scan_market(
                        app,
                        market,
                    )

                else:
                    logger.info(
                        "No eligible market is currently open."
                    )

        except asyncio.CancelledError:
            raise

        except Exception:
            logger.exception(
                "Adaptive scanner loop error."
            )

        await asyncio.sleep(SCAN_INTERVAL_SECONDS)
