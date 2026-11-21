"""Special market-condition monitoring using completed 5-minute candles only.

One Twelve Data 5-minute feed is shared for Gold on weekdays and BTC/USD on
weekends. Updates describe market conditions; they are not trade signals.
"""
import asyncio
import logging
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import re

import pandas as pd

import state
from config import MARKETS, TELEGRAM_CHANNEL_ID, GOLD_BUDGET_SHARE
from market_data import fetch_candles, MarketDataError
from strategy import calculate_indicators, analyze
from chart import create_market_chart

logger = logging.getLogger(__name__)

GOLD = "XAU/USD"
BITCOIN = "BTC/USD"
SPECIAL_BASE_INTERVAL_SECONDS = 5 * 60
SPECIAL_ACTIVE_INTERVAL_SECONDS = 3 * 60
GOLD_MONITOR_LOOP_SECONDS = 60
GOLD_SOURCE_INTERVAL_SECONDS = 5 * 60
GOLD_SOURCE_OUTPUTSIZE = 500
# Special Monitoring analyses one timeframe only for both supported markets.
GOLD_TIMEFRAMES = ("5m",)
GOLD_FETCH_SCHEDULE = {
    "m5_source": GOLD_SOURCE_INTERVAL_SECONDS,
}
GOLD_AUTOMATIC_SCAN_INTERVAL_SECONDS = 15 * 60


def estimated_daily_gold_monitor_requests():
    """Upper-bound REST requests used by the dedicated Gold monitor alone."""
    return sum(
        86400 // interval
        for interval in GOLD_FETCH_SCHEDULE.values()
    )


def estimated_daily_gold_requests():
    """Total Gold requests, including the scanner charged to the other pool."""
    return estimated_daily_gold_monitor_requests() + (
        86400 // GOLD_AUTOMATIC_SCAN_INTERVAL_SECONDS
    )


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

    candidates = frame
    breakout_time = pd.to_datetime(
        watch.get("candle_time"), utc=True, errors="coerce"
    )
    if isinstance(frame.index, pd.DatetimeIndex) and not pd.isna(breakout_time):
        candidates = frame.loc[frame.index > breakout_time]
    else:
        candidates = frame.tail(1)

    tolerance = atr * 0.30
    for _, candle in candidates.iterrows():
        if direction == "UP":
            touched = float(candle["low"]) <= level + tolerance
            held = (
                float(candle["close"]) > level
                and float(candle["close"]) > float(candle["open"])
            )
        else:
            touched = float(candle["high"]) >= level - tolerance
            held = (
                float(candle["close"]) < level
                and float(candle["close"]) < float(candle["open"])
            )
        if touched and held:
            return direction
    return None


def _recent_trendline_breakout(frame, atr, previous_candle_time=None):
    """Check recent newly closed candles so an 8-minute poll cannot miss a break."""
    start = max(1, len(frame) - 12)
    previous_time = pd.to_datetime(
        previous_candle_time, utc=True, errors="coerce"
    )
    if isinstance(frame.index, pd.DatetimeIndex) and not pd.isna(previous_time):
        new_positions = [
            index for index, candle_time in enumerate(frame.index)
            if candle_time > previous_time
        ]
        if new_positions:
            start = max(start, new_positions[0])
        else:
            return None
    else:
        start = len(frame) - 1

    for position in range(start, len(frame)):
        segment = frame.iloc[:position + 1]
        current_atr = _safe_float(segment.iloc[-1].get("atr"), atr)
        breakout = _trendline_breakout(segment, current_atr)
        if breakout:
            breakout["candle_time"] = str(segment.index[-1])
            return breakout
    return None


def _safe_float(value, default=None):
    try:
        result = float(value)
        return result if pd.notna(result) else default
    except (TypeError, ValueError):
        return default


def classify_gold_market(frame, timeframe, previous_state=None, symbol=GOLD):
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
        breakout = _recent_trendline_breakout(
            data, atr, previous_state.get("candle_time")
        )
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
                "candle_time": breakout["candle_time"],
                "age": 0,
            }
        else:
            near_ema = abs(close - ema21) <= atr * 0.35
            position = len(data) - 1
            support_points = _swing_points(data.iloc[:-1], "low", "low")
            resistance_points = _swing_points(data.iloc[:-1], "high", "high")
            support_line, support_slope = _line_value(support_points, position)
            resistance_line, resistance_slope = _line_value(resistance_points, position)
            near_rising_support = (
                support_line is not None
                and support_slope > 0
                and abs(float(last["low"]) - support_line) <= atr * 0.35
            )
            near_falling_resistance = (
                resistance_line is not None
                and resistance_slope < 0
                and abs(float(last["high"]) - resistance_line) <= atr * 0.35
            )
            bullish_pullback = (
                adx >= 18
                and ema9 > ema21
                and (
                    float(last["low"]) <= ema21 + atr * 0.25
                    or near_rising_support
                )
                and close > ema9
                and close > float(last["open"])
                and _safe_float(previous.get("ema9"), 0.0)
                    > _safe_float(previous.get("ema21"), 0.0)
            )
            bearish_pullback = (
                adx >= 18
                and ema9 < ema21
                and (
                    float(last["high"]) >= ema21 - atr * 0.25
                    or near_falling_resistance
                )
                and close < ema9
                and close < float(last["open"])
                and _safe_float(previous.get("ema9"), 0.0)
                    < _safe_float(previous.get("ema21"), 0.0)
            )
            if bullish_pullback:
                condition, direction = "PULLBACK CONTINUATION — UP", "UP"
                detail = "Price pulled back towards EMA21 or rising trendline support and closed bullish above EMA9."
            elif bearish_pullback:
                condition, direction = "PULLBACK CONTINUATION — DOWN", "DOWN"
                detail = "Price pulled back towards EMA21 or falling trendline resistance and closed bearish below EMA9."
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
        "symbol": symbol,
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
    heading = f"{result['symbol']} SPECIAL MONITORING UPDATE"
    if initial:
        heading = f"{result['symbol']} SPECIAL MONITORING — INITIAL STATUS"
    return (
        f"🟡 {heading}\n\n"
        f"Market: {result['symbol']}\n"
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
            result["symbol"],
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



async def _send_early_alert(app, symbol, result):
    """Send a provisional WATCH/candidate alert without creating a trade."""
    stage = result.get("signal_stage")
    direction = result.get("direction") or result.get("status") or "UNCONFIRMED"
    title = "EARLY ENTRY CANDIDATE — PROVISIONAL" if stage == "EARLY_ENTRY_CANDIDATE" else "DEVELOPING TREND — WATCH"
    candles = result.get("candles")
    latest_price = result.get("entry")
    if latest_price is None and candles is not None and len(candles):
        latest_price = candles.iloc[-1]["close"]
    lines = [f"🟡 {title}", "", f"Market: {symbol}", f"Timeframe: {result.get('timeframe', '5m')}", f"Direction being watched: {direction}", f"Setup score: {float(result.get('score', 0) or 0):.0f}/100"]
    if latest_price is not None:
        lines.append(f"Price: {float(latest_price):g}")
    if result.get("stop_loss") is not None:
        lines.append(f"Provisional structural invalidation: {float(result['stop_loss']):g}")
    lines.extend(["", str(result.get("reason") or "Early momentum is developing; wait for confirmation."), "", "⚠️ PROVISIONAL ONLY — not a confirmed BUY/SELL signal. Do not treat this as an entry instruction. The confirmed scanner still requires at least 80/100 and completed-candle confirmation."])
    caption = "\n".join(lines)
    chart = None
    try:
        chart = create_market_chart(candles, symbol, result.get("timeframe", "5m"), strategy="EARLY TREND DISCOVERY", signal_stage=stage)
    except Exception:
        logger.exception("Could not create %s early-discovery chart.", symbol)
    candle_time = str(result.get("candle_time") or "unknown")
    delivery_key = f"early-discovery:{symbol}:{stage}:{direction}:{candle_time}"
    for user_id in state.get_users():
        target_key = f"{delivery_key}:user:{user_id}"
        if state.get_last_alert(target_key):
            continue
        try:
            if not state.user_monitoring_enabled(user_id):
                continue
            settings = state.get_user_settings(user_id) or {}
            notifications = settings.get("notifications") or {}
            if notifications.get("gold_updates", True) is False:
                continue
            if chart is not None:
                chart.seek(0)
                await app.bot.send_photo(chat_id=int(user_id), photo=chart, caption=caption)
            else:
                await app.bot.send_message(chat_id=int(user_id), text=caption)
            state.set_last_alert(target_key, "sent")
        except Exception:
            logger.exception("Could not send provisional alert to user %s.", user_id)


async def _analyse_and_notify(app, timeframe, frame, symbol=GOLD):
    key = f"special_monitor:{symbol}:condition:{timeframe}"
    previous = state.get_scanner_state(key, {})
    if not isinstance(previous, dict):
        previous = {}
    result = classify_gold_market(frame, timeframe, previous, symbol=symbol)
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

    # Publish provisional discovery stages only; confirmed BUY/SELL alerts
    # remain the responsibility of the main scanner.
    if timeframe == "5m" and new_candle:
        try:
            early = analyze(frame, timeframe, trading_style="SCALPING")
            if early.get("signal_stage") in {"EARLY_WATCH", "EARLY_ENTRY_CANDIDATE"}:
                await _send_early_alert(app, symbol, early)
        except Exception:
            logger.exception("Could not evaluate provisional %s early alert.", symbol)


async def _fetch_gold_frame(symbol, timeframe, interval, outputsize=1500):
    # The shared eight-second cooldown applies across every Twelve Data
    # consumer. Wait for it rather than turning a temporary cooldown into
    # a missed Gold timeframe.
    for attempt in range(6):
        try:
            return await fetch_candles(
                MARKETS[symbol],
                outputsize=outputsize,
                interval=interval,
                purpose="gold_monitor",
            )
        except MarketDataError as exc:
            match = re.search(
                r"Try again in ([0-9.]+) seconds",
                str(exc),
            )
            if not match or attempt == 5:
                raise
            await asyncio.sleep(float(match.group(1)) + 0.25)
    return None


async def _gold_monitor_cycle(app, now_epoch=None):
    """Run 5m discovery: Gold on weekdays and Bitcoin on weekends."""
    now_epoch = time.time() if now_epoch is None else float(now_epoch)
    local_now = datetime.fromtimestamp(now_epoch, tz=ZoneInfo("Africa/Lagos"))
    symbol = BITCOIN if local_now.weekday() >= 5 else GOLD
    if symbol not in MARKETS:
        return

    previous_condition = state.get_scanner_state(f"special_monitor:{symbol}:condition:5m", {})
    promising = isinstance(previous_condition, dict) and any(
        token in str(previous_condition.get("condition", ""))
        for token in ("TRENDLINE BREAKOUT", "PULLBACK CONTINUATION", "UPTREND", "DOWNTREND")
    )
    interval_seconds = SPECIAL_ACTIVE_INTERVAL_SECONDS if promising else SPECIAL_BASE_INTERVAL_SECONDS
    source_retry_key = f"special_monitor:{symbol}:retry_after:m5_source"
    source_retry_after = float(state.get_scanner_state(source_retry_key, 0) or 0)
    slot_key = f"{symbol}:m5_source:{interval_seconds}"
    source_due, source_slot = _slot_due(slot_key, interval_seconds, now_epoch)
    if not source_due or now_epoch < source_retry_after:
        return

    try:
        source = await _fetch_gold_frame(symbol, "5m", "5m", GOLD_SOURCE_OUTPUTSIZE)
        _mark_slot(slot_key, source_slot)
        state.set_scanner_state(source_retry_key, 0)
        timeframe = "5m"
        if len(source) >= 80:
            await _analyse_and_notify(app, timeframe, source, symbol=symbol)
        else:
            logger.warning(
                "%s 5m history is too short (%d candles).",
                symbol, len(source),
            )
    except (MarketDataError, ValueError) as exc:
        logger.warning("%s 5m special monitoring delayed: %s", symbol, exc)
        state.set_scanner_state(
            source_retry_key,
            now_epoch + interval_seconds,
        )


async def gold_market_monitor_loop(app):
    """Run the dedicated Gold-first condition monitor without extra strategies or orders."""
    logger.info(
        "Special 5m-only discovery monitor started. Timeframe: %s. "
        "Dedicated monitoring uses %.0f%% of the background API pool.",
        ", ".join(GOLD_TIMEFRAMES),
        GOLD_BUDGET_SHARE * 100,
    )
    while True:
        try:
            await _gold_monitor_cycle(app)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Special market monitor cycle failed.")
        await asyncio.sleep(GOLD_MONITOR_LOOP_SECONDS)
