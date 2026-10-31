"""Scheduled daily opportunity scanner for Forex Hunter AI."""

import asyncio
import logging
from datetime import datetime, timedelta
from io import BytesIO
from zoneinfo import ZoneInfo

import state
from config import MARKETS, TELEGRAM_CHANNEL_ID
from market_data import fetch_candles, MarketDataError
from strategy import analyze, format_signal
from chart import create_market_chart

logger = logging.getLogger(__name__)

NIGERIA_TZ = ZoneInfo("Africa/Lagos")
SCAN_TIMEFRAME = "5m"
CHECK_INTERVAL_SECONDS = 10
STARTUP_GRACE_SECONDS = 90

# One market per scheduled scan.
# Times are Nigeria local time (WAT).
DAILY_SCHEDULE = {
    6: "GBP/USD",
    12: "USD/JPY",
    18: "XAU/USD",
    0: "BTC/USD",
}


def _provider_symbol(symbol):
    return MARKETS[symbol]


def _slot_key(slot_time):
    return "auto-scan-slot:" + slot_time.strftime("%Y-%m-%d-%H%M")


def _signal_key(symbol, result):
    candle_time = str(result.get("candle_time", "unknown"))
    direction = str(result.get("status", "unknown"))
    return (
        f"auto-signal:{symbol}:{SCAN_TIMEFRAME}:"
        f"{direction}:{candle_time}"
    )


def _destinations():
    """Send to people who have started the bot, plus an optional channel."""
    destinations = set()

    try:
        for user_id in state.get_users():
            destinations.add(str(user_id))
    except Exception:
        logger.exception("Could not load Telegram bot users.")

    if TELEGRAM_CHANNEL_ID:
        destinations.add(str(TELEGRAM_CHANNEL_ID))

    return sorted(destinations)


async def _send_signal(app, symbol, result):
    message = format_signal(symbol, result)

    chart_bytes = None
    try:
        chart = create_market_chart(
            result.get("candles"),
            symbol,
            SCAN_TIMEFRAME,
            entry=result.get("entry"),
            stop_loss=result.get("stop_loss"),
            take_profits=result.get("take_profits"),
        )
        chart_bytes = chart.getvalue()
    except Exception:
        logger.exception("Could not create automatic scan chart for %s", symbol)

    destinations = _destinations()

    if not destinations:
        logger.info(
            "Confirmed %s %s setup, but no Telegram recipients are registered.",
            result.get("status"), symbol
        )
        return

    for destination in destinations:
        try:
            chat_id = int(destination)
            if chart_bytes:
                photo = BytesIO(chart_bytes)
                photo.name = f"{symbol.replace('/', '_')}_{SCAN_TIMEFRAME}.png"
                await app.bot.send_photo(
                    chat_id=chat_id,
                    photo=photo,
                    caption=message,
                )
            else:
                await app.bot.send_message(
                    chat_id=chat_id,
                    text=message,
                )
        except Exception:
            logger.exception(
                "Could not deliver automatic opportunity alert to %s",
                destination
            )


async def _run_one_market(app, symbol):
    if symbol not in MARKETS:
        logger.error("Scheduled market %s is not configured.", symbol)
        return

    try:
        frame = await fetch_candles(_provider_symbol(symbol))
        result = analyze(frame, SCAN_TIMEFRAME)
        result["symbol"] = symbol

        # No Telegram message for WAIT, stale data, or invalid setups.
        if result.get("status") not in ("BUY", "SELL"):
            logger.info(
                "Scheduled scan: no confirmed setup for %s. Staying silent.",
                symbol
            )
            return

        key = _signal_key(symbol, result)

        # Prevent the same closed-candle signal from being broadcast twice.
        if state.get_last_alert(key):
            logger.info("Duplicate automatic signal suppressed: %s", key)
            return

        # Save before sending to avoid duplicate broadcasts after a restart.
        state.set_last_alert(key, "sent")
        await _send_signal(app, symbol, result)

    except MarketDataError as exc:
        logger.warning("Scheduled scan unavailable for %s: %s", symbol, exc)
    except Exception:
        logger.exception("Scheduled scan failed for %s.", symbol)


async def _check_due_slot(app):
    now = datetime.now(NIGERIA_TZ)

    for hour, symbol in DAILY_SCHEDULE.items():
        slot_time = now.replace(
            hour=hour, minute=0, second=0, microsecond=0
        )
        elapsed = (now - slot_time).total_seconds()

        # Run only at the scheduled time or within the short startup grace.
        # Missed scans are skipped; they are never replayed hours later.
        if elapsed < 0 or elapsed >= STARTUP_GRACE_SECONDS:
            continue

        key = _slot_key(slot_time)

        if state.get_last_alert(key):
            continue

        # Persist the slot before making a market-data request.
        state.set_last_alert(key, "started")

        logger.info(
            "Starting scheduled Nigeria-time scan: %s at %s",
            symbol, slot_time.isoformat()
        )
        await _run_one_market(app, symbol)


async def auto_scan_loop(app):
    """Check the clock only; market data is fetched only at scheduled slots."""
    logger.info(
        "Automatic opportunity scanner started. "
        "Nigeria-time schedule: 00:00 BTC/USD, 06:00 GBP/USD, "
        "12:00 USD/JPY, 18:00 XAU/USD."
    )

    while True:
        try:
            await _check_due_slot(app)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Automatic scanner loop error.")

        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
