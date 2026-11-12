"""Gold-first multi-timeframe market-condition monitoring.

The monitor shares one M1 request across M1/M5/M15 by resampling completed
candles. Higher timeframes are fetched only when their own candle interval
is due. Updates describe market conditions; they are not trade signals.
"""
import asyncio
import logging
import time
from datetime import datetime, timezone

import pandas as pd

import state
from config import MARKETS, TELEGRAM_CHANNEL_ID, GOLD_BUDGET_SHARE
from market_data import fetch_candles, resample_candles, MarketDataError
from strategy import calculate_indicators
from chart import create_market_chart

logger = logging.getLogger(__name__)

GOLD = "XAU/USD"
GOLD_MONITOR_LOOP_SECONDS = 60
GOLD_SOURCE_INTERVAL_SECONDS = 6 * 60
GOLD_SOURCE_OUTPUTSIZE = 2000
GOLD_TIMEFRAMES = ("1m", "5m", "15m", "30m", "1h", "4h", "1day")
GOLD_FETCH_SCHEDULE = {
    "m1_source": GOLD_SOURCE_INTERVAL_SECONDS,
    "30m": 30 * 60,
    "1h": 60 * 60,
    "4h": 4 * 60 * 60,
    "1day": 24 * 60 * 60,
}


def _slot_due(key, interval_seconds, now_epoch=None):
    """Return whether a time bucket is new; do not consume a slot until fetched."""
    now_epoch = time.time() if now_epoch is None else float(now_epoch)
    slot = int(now_epoch // int(interval_seconds))
    previous = state.get_scanner_state(f"gold_monitor:slot:{key}")
    return previous != slot, slot


def _mark_slot(key, slot):
    state.set_scanner_state(f"gold_monitor:slot:{key}", int(slot))


def _swing_points(frame, column, mode, radius=2):
    values = pd.to_numeric(frame[column], errors="coerce").to_numpy(dtype=float)
    points = []
    for index in range(radius, len(values) - radius):
        window = values[index - radius:index + radius + 1]
        if mode == "high":
            if values[index] == window.max() and (window == values[index]).sum() == 1:
                points.append((index, float(values[index])))
        elif values[index] == window.min() and (window == values[index]).sum() == 1:
            points.append((index, float(values[index])))
    return points


def _line_value(points, position):
    if len(points) < 2:
        return None, None
    (x1, y1), (x2, y2) = points[-2:]
    if x2 == x1:
        return None, None
    slope = (y2 - y1) / (x2 - x1)
    return y1 + slope * (position - x1), slope


def _trendline_breakout(frame, atr):
    """Find a closed-candle break of a confirmed descending/ascending swing line."""
    if len(frame) < 25 or atr <= 0:
        return None
    tolerance = atr * 0.20
    previous = frame.iloc[-2]
    last = frame.iloc[-1]
    position = len(frame) - 1

    highs = _swing_points(frame.iloc[:-1], "high", "high")
    resistance, resistance_slope = _line_value(highs, position)
    if resistance is not None and resistance_slope < 0:
        previous_line, _ = _line_value(highs, position - 1)
        if (
            previous_line is not None
            and float(previous["close"]) <= previous_line + tolerance
            and float(last["close"]) > resistance + tolerance
        ):
            return {"direction": "UP", "level": float(resistance)}

    lows = _swing_points(frame.iloc[:-1], "low", "low")
    support, support_slope = _line_value(lows, position)
    if support is not None and support_slope > 0:
        previous_line, _ = _line_value(lows, position - 1)
        if (
            previous_line is not None
            and float(previous["close"]) >= previous_line - tolerance
            and float(last["close"]) < support - tolerance
        ):
            return {"direction": "DOWN", "level": float(support)}
    return None


def _breakout_retest(frame, watch, atr):
    if not isinstance(watch, dict) or atr <= 0:
        return None
    direction = str(watch.get("direction", "")).upper()
    level = _safe_float(watch.get("level"))
    if direction not in {"UP", "DOWN"} or level is None:
        return None
    last = frame.iloc[-1]
    tolerance = atr * 0.30
    if direction == "UP":
        touched = float(last["low"]) <= level + tolerance
        held = float(last["close"]) > level and float(last["close"]) > float(last["open"])
    else:
        touched = float(last["high"]) >= level - tolerance
        held = float(last["close"]) < level and float(last["close"]) < float(last["open"])
    return direction if touched and held else None


def _safe_float(value, default=None):
    try:
        result = float(value)
        return result if pd.notna(result) else default
    except (TypeError, ValueError):
        return default


def classify_gold_market(frame, timeframe, previous_state=None):
    """Classify a completed-candle Gold frame and describe actionable structure."""
    if frame is None or frame.empty:
        raise ValueError("Gold monitor received an empty candle frame.")
    required = {"open", "high", "low", "close"}
    if not required.issubset(frame.columns):
        raise ValueError("Gold monitor requires OHLC candle data.")
    if len(frame) < 80:
        raise ValueError(
            f"Gold {timeframe} requires at least 80 completed candles; "
            f"received {len(frame)}."
        )

    data = calculate_indicators(frame)
    data = data.dropna(subset=["ema9", "ema21", "atr", "adx", "plus_di", "minus_di"])
    if len(data) < 2:
        raise ValueError(f"Gold {timeframe} indicators are not ready.")

    previous_state = previous_state if isinstance(previous_state, dict) else {}
    previous = data.iloc[-2]
    last = data.iloc[-1]
    atr = _safe_float(last.get("atr"), 0.0)
    adx = _safe_float(last.get("adx"), 0.0)
    ema9 = _safe_float(last.get("ema9"), 0.0)
    ema21 = _safe_float(last.get("ema21"), 0.0)
    plus_di = _safe_float(last.get("plus_di"), 0.0)
    minus_di = _safe_float(last.get("minus_di"), 0.0)
    close = _safe_float(last.get("close"), 0.0)
    if atr <= 0 or close <= 0:
        raise ValueError(f"Gold {timeframe} volatility data is invalid.")

    condition = "MIXED / TRANSITION"
    direction = "NEUTRAL"
    detail = "Price and directional indicators are not sufficiently aligned."
    breakout_watch = previous_state.get("breakout_watch")
    if isinstance(breakout_watch, dict):
        breakout_watch = dict(breakout_watch)
        breakout_watch["age"] = int(breakout_watch.get("age", 0)) + 1
        if breakout_watch["age"] > 10:
            breakout_watch = None
    else:
        breakout_watch = None

    retest = _breakout_retest(data, breakout_watch, atr)
    if retest:
        condition = f"TRENDLINE RETEST — {retest}"
        direction = retest
        detail = (
            f"Gold returned to the previously broken trendline area and "
            f"closed back in the {retest.lower()} direction near "
            f"{_safe_float(breakout_watch.get('level')):g}."
        )
        breakout_watch = None
    else:
        breakout = _trendline_breakout(data, atr)
        if breakout:
            direction = breakout["direction"]
            condition = f"TRENDLINE BREAKOUT — {direction}"
            detail = (
                f"Gold closed beyond a confirmed "
                f"{'descending resistance' if direction == 'UP' else 'ascending support'} "
                f"trendline near {breakout['level']:g}. Watch for a retest; "
                f"this is not a trade instruction."
            )
            breakout_watch = {
                "direction": direction,
                "level": breakout["level"],
                "age": 0,
            }
        else:
            near_ema = abs(close - ema21) <= atr * 0.35
            bullish_pullback = (
                ema9 > ema21
                and float(last["low"]) <= ema21 + atr * 0.25
                and close > ema9
                and close > float(last["open"])
                and _safe_float(previous.get("ema9"), 0.0)
                    > _safe_float(previous.get("ema21"), 0.0)
            )
            bearish_pullback = (
                ema9 < ema21
                and float(last["high"]) >= ema21 - atr * 0.25
                and close < ema9
                and close < float(last["open"])
                and _safe_float(previous.get("ema9"), 0.0)
                    < _safe_float(previous.get("ema21"), 0.0)
            )
            if bullish_pullback:
                condition, direction = "PULLBACK CONTINUATION — UP", "UP"
                detail = "Price pulled back towards the EMA21 area and closed bullish above EMA9."
            elif bearish_pullback:
                condition, direction = "PULLBACK CONTINUATION — DOWN", "DOWN"
                detail = "Price pulled back towards the EMA21 area and closed bearish below EMA9."
            elif (
                adx < 18
                and abs(ema9 - ema21) <= atr * 0.60
                and (data.tail(20)["high"].max() - data.tail(20)["low"].min()) <= atr * 12
            ):
                condition, direction = "CONSOLIDATION / RANGE", "NEUTRAL"
                detail = "ADX and EMA separation are weak; Gold is trading in a relatively compressed range."
            elif ema9 > ema21 and plus_di > minus_di and close > ema21 and adx >= 18:
                condition, direction = "UPTREND", "UP"
                detail = "EMA9 is above EMA21, positive directional movement leads, and ADX confirms trend strength."
            elif ema9 < ema21 and minus_di > plus_di and close < ema21 and adx >= 18:
                condition, direction = "DOWNTREND", "DOWN"
                detail = "EMA9 is below EMA21, negative directional movement leads, and ADX confirms trend strength."
            elif near_ema and adx < 22:
                condition, direction = "WEAK TREND / CONSOLIDATION", "NEUTRAL"
                detail = "Price is near EMA21 and trend strength is limited; wait for a clearer direction."

    return {
        "symbol": GOLD,
        "timeframe": timeframe,
        "condition": condition,
        "direction": direction,
        "detail": detail,
        "price": close,
        "adx": adx,
        "rsi": _safe_float(last.get("rsi"), 50.0),
        "atr": atr,
        "candle_time": str(data.index[-1]),
        "breakout_watch": breakout_watch,
        "candles": frame,
    }


def _notification_text(result, initial=False):
    heading = "GOLD MULTI-TIMEFRAME UPDATE"
    if initial:
        heading = "GOLD MONITORING — INITIAL TIMEFRAME STATUS"
    return (
        f"🟡 {heading}\n\n"
        f"Market: XAU/USD (Gold)\n"
        f"Timeframe: {result['timeframe'].upper()}\n"
        f"Condition: {result['condition']}\n"
        f"Direction: {result['direction']}\n"
        f"Price: {result['price']:g}\n"
        f"ADX: {result['adx']:.1f} | RSI: {result['rsi']:.1f}\n\n"
        f"{result['detail']}\n\n"
        "This is a market-condition update, not a BUY/SELL signal."
    )


async def _send_gold_update(app, result, initial=False):
    """Send one chart-backed update to users who have monitoring enabled."""
    caption = _notification_text(result, initial=initial)
    chart = None
    try:
        chart = create_market_chart(
            result["candles"],
            GOLD,
            result["timeframe"],
            strategy="GOLD MARKET MONITOR",
            signal_stage=result["condition"],
        )
    except Exception:
        logger.exception("Could not create Gold %s monitoring chart.", result["timeframe"])

    for user_id in state.get_users():
        try:
            if not state.user_monitoring_enabled(user_id):
                continue
            settings = state.get_user_settings(user_id) or {}
            notifications = settings.get("notifications") or {}
            if notifications.get("gold_updates", True) is False:
                continue
            if chart is not None:
                chart.seek(0)
                await app.bot.send_photo(
                    chat_id=int(user_id), photo=chart, caption=caption
                )
            else:
                await app.bot.send_message(chat_id=int(user_id), text=caption)
        except Exception:
            logger.exception("Could not send Gold update to user %s.", user_id)

    if TELEGRAM_CHANNEL_ID:
        try:
            if chart is not None:
                chart.seek(0)
                await app.bot.send_photo(
                    chat_id=int(TELEGRAM_CHANNEL_ID), photo=chart, caption=caption
                )
            else:
                await app.bot.send_message(
                    chat_id=int(TELEGRAM_CHANNEL_ID), text=caption
                )
        except Exception:
            logger.exception("Could not publish Gold update to channel.")


async def _analyse_and_notify(app, timeframe, frame):
    key = f"gold_monitor:condition:{timeframe}"
    previous = state.get_scanner_state(key, {})
    if not isinstance(previous, dict):
        previous = {}
    result = classify_gold_market(frame, timeframe, previous)
    condition_changed = previous.get("condition") != result["condition"]
    first_snapshot = not previous
    new_candle = previous.get("candle_time") != result["candle_time"]

    state.set_scanner_state(
        key,
        {
            "condition": result["condition"],
            "direction": result["direction"],
            "candle_time": result["candle_time"],
            "breakout_watch": result["breakout_watch"],
            "price": result["price"],
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    if first_snapshot or (condition_changed and new_candle):
        await _send_gold_update(app, result, initial=first_snapshot)


async def _fetch_gold_frame(timeframe, interval, outputsize=1500):
    return await fetch_candles(
        MARKETS[GOLD],
        outputsize=outputsize,
        interval=interval,
        purpose="gold_monitor",
    )


async def _gold_monitor_cycle(app, now_epoch=None):
    """Fetch only due Gold timeframes and share the M1 feed across M1/M5/M15."""
    if GOLD not in MARKETS:
        return
    now_epoch = time.time() if now_epoch is None else float(now_epoch)
    # Gold is a weekday market only. No Gold API calls are made on weekends.
    if datetime.fromtimestamp(now_epoch, tz=timezone.utc).weekday() >= 5:
        return

    retry_after = float(state.get_scanner_state("gold_monitor:retry_after", 0) or 0)
    if now_epoch < retry_after:
        return

    source_due, source_slot = _slot_due("m1_source", GOLD_FETCH_SCHEDULE["m1_source"], now_epoch)
    if source_due:
        try:
            source = await _fetch_gold_frame("1m", "1m", GOLD_SOURCE_OUTPUTSIZE)
            _mark_slot("m1_source", source_slot)
            for timeframe in ("1m", "5m", "15m"):
                frame = resample_candles(source, timeframe)
                if len(frame) >= 80:
                    await _analyse_and_notify(app, timeframe, frame)
                else:
                    logger.warning(
                        "Gold %s history is too short after resampling (%d candles).",
                        timeframe, len(frame),
                    )
        except (MarketDataError, ValueError) as exc:
            logger.warning("Gold M1/M5/M15 monitoring delayed: %s", exc)
            state.set_scanner_state("gold_monitor:retry_after", now_epoch + 60)

    for timeframe, interval in (("30m", "30m"), ("1h", "1h"), ("4h", "4h"), ("1day", "1day")):
        due, slot = _slot_due(timeframe, GOLD_FETCH_SCHEDULE[timeframe], now_epoch)
        if not due:
            continue
        try:
            frame = await _fetch_gold_frame(timeframe, interval, 1500)
            _mark_slot(timeframe, slot)
            await _analyse_and_notify(app, timeframe, frame)
        except (MarketDataError, ValueError) as exc:
            logger.warning("Gold %s monitoring delayed: %s", timeframe, exc)
            state.set_scanner_state("gold_monitor:retry_after", now_epoch + 60)


async def gold_market_monitor_loop(app):
    """Run the dedicated Gold-first condition monitor without extra strategies or orders."""
    logger.info(
        "Gold multi-timeframe monitor started. Timeframes: %s. "
        "Gold receives %.0f%% of the background API budget.",
        ", ".join(GOLD_TIMEFRAMES),
        GOLD_BUDGET_SHARE * 100,
    )
    while True:
        try:
            await _gold_monitor_cycle(app)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Gold multi-timeframe monitor cycle failed.")
        await asyncio.sleep(GOLD_MONITOR_LOOP_SECONDS)
