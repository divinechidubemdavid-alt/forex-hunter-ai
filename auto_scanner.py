
import asyncio
import logging
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import state
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from config import MARKETS, TELEGRAM_CHANNEL_ID, FOREX_MARKETS
from market_scope import get_session_markets
from market_data import fetch_candles, MarketDataError
from strategy import analyze, format_signal
from chart import create_market_chart
from trading_styles import AUTOMATIC_TRADING_STYLE, style_label

logger = logging.getLogger(__name__)

WAT = ZoneInfo("Africa/Lagos")

SCAN_TIMEFRAME = "15m"
SCAN_INTERVAL_SECONDS = 60
MINIMUM_SCAN_INTERVAL_MINUTES = 30

GOLD = "XAU/USD"
BITCOIN = "BTC/USD"



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
    """Apply the shared session allow-list, then respect forex market hours."""
    allowed = get_session_markets(MARKETS, now.weekday())

    if _is_weekend(now):
        return [BITCOIN] if BITCOIN in allowed else []

    markets = []

    if GOLD in allowed:
        markets.append(GOLD)

    if _forex_market_open(now):
        markets.extend(
            market for market in FOREX_MARKETS
            if market in allowed
        )

    # Bitcoin trades continuously and remains eligible on weekdays too.
    if BITCOIN in allowed:
        markets.append(BITCOIN)

    return markets


def _markets_for_cycle(now):
    """
    Return every currently eligible market for this scan cycle.

    Gold is checked first, then the three retained forex pairs in a
    rotating order. Weekends remain BTC/USD-only.
    No eligible market is skipped merely because another market
    has priority.
    """
    eligible = _eligible_markets(now)

    if eligible == [BITCOIN]:
        return [BITCOIN]

    if not eligible:
        return []

    forex_order = list(FOREX_MARKETS)
    position = int(
        state.get_scanner_state("market_rotation_position", 0)
    )
    if forex_order:
        offset = position % len(forex_order)
        forex_order = forex_order[offset:] + forex_order[:offset]
        state.set_scanner_state(
            "market_rotation_position",
            position + 1,
        )

    ordered = []
    if GOLD in eligible:
        ordered.append(GOLD)
    ordered.extend(market for market in forex_order if market in eligible)
    if BITCOIN in eligible:
        ordered.append(BITCOIN)
    return ordered


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
        f"{direction}:{candle_time}:{AUTOMATIC_TRADING_STYLE}"
    )


async def _send_signal(app, symbol, result):
    """Publish an opportunity without silently adding it to user tracking."""
    result["trading_style"] = AUTOMATIC_TRADING_STYLE
    result["style_label"] = style_label(AUTOMATIC_TRADING_STYLE)
    message = format_signal(symbol, result)
    raw_targets = result.get("take_profits") or []
    targets = list(raw_targets) if isinstance(raw_targets, (list, tuple)) else [raw_targets]
    signal_record = {
        "symbol": symbol,
        "trading_style": AUTOMATIC_TRADING_STYLE,
        "style_label": style_label(AUTOMATIC_TRADING_STYLE),
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
        "higher_timeframe_confirmations": result.get("higher_timeframe_confirmations", {}),
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
            strategy=result.get("strategy"),
            signal_stage=result.get("signal_stage"),
            trading_style=AUTOMATIC_TRADING_STYLE,
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
        delivery_key = f"{opportunity_key}:user:{user_id}"
        if state.get_last_alert(delivery_key):
            continue
        try:
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton(
                    "📌 TRACK THIS SIGNAL",
                    callback_data=f"optrack:{opportunity['id']}",
                )],
                [InlineKeyboardButton(
                    "🧮 CALCULATE POSITION SIZE",
                    callback_data=f"account:plan-opportunity:{opportunity['id']}",
                )],
            ])
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
            state.set_last_alert(delivery_key, "sent")
            delivered += 1
        except Exception:
            logger.exception("Could not send opportunity to user %s.", user_id)

    if TELEGRAM_CHANNEL_ID:
        channel_delivery_key = (
            f"{opportunity_key}:channel:{TELEGRAM_CHANNEL_ID}"
        )
        if not state.get_last_alert(channel_delivery_key):
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
                state.set_last_alert(channel_delivery_key, "sent")
                delivered += 1
            except Exception:
                logger.exception("Could not publish opportunity to channel.")

    if delivered == 0:
        logger.info(
            "No new delivery for %s; previously delivered recipients are skipped "
            "and failed destinations remain eligible for retry.",
            symbol,
        )
    return delivered


async def _fetch_scanner_frame(symbol):
    purpose = "gold_monitor" if symbol == GOLD else "auto"
    for attempt in range(5):
        try:
            return await fetch_candles(
                MARKETS[symbol],
                outputsize=500,
                interval="15m",
                purpose=purpose,
            )
        except MarketDataError as exc:
            match = re.search(
                r"Try again in ([0-9.]+) seconds",
                str(exc),
            )
            if not match or attempt == 4:
                raise
            await asyncio.sleep(float(match.group(1)) + 0.25)
    return None


async def _scan_market(app, symbol):
    currently_eligible = _eligible_markets(datetime.now(WAT))
    if symbol not in currently_eligible:
        logger.warning(
            "Market %s is outside the currently eligible session or market hours; scan skipped.",
            symbol,
        )
        return

    if symbol not in MARKETS:
        logger.warning(
            "Market %s is not configured.",
            symbol,
        )
        return

    try:
        frame = await _fetch_scanner_frame(symbol)

        result = analyze(
            frame,
            SCAN_TIMEFRAME,
            trading_style=AUTOMATIC_TRADING_STYLE,
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

        delivered = await _send_signal(app, symbol, result)
        if not delivered:
            logger.info(
                "No new delivery confirmed for %s; failed destinations remain eligible for retry.",
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
    """
    One central adaptive scanning loop. The signal monitor is started
    separately by main.py so it runs exactly once.

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
                markets = _markets_for_cycle(now)

                if markets:
                    logger.info(
                        "Starting 30-minute scan cycle for %d markets: %s",
                        len(markets),
                        ", ".join(markets),
                    )

                    # Scan every eligible market in this cycle. Each
                    # confirmed signal is sent immediately after detection.
                    for index, market in enumerate(markets):
                        if index:
                            # Respect Twelve Data's shared request cooldown.
                            await asyncio.sleep(8)

                        logger.info(
                            "Scanning %s at %s WAT.",
                            market,
                            datetime.now(WAT).isoformat(),
                        )
                        await _scan_market(app, market)

                    state.set_scanner_state(
                        "last_scan_epoch",
                        datetime.now(WAT).timestamp(),
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
