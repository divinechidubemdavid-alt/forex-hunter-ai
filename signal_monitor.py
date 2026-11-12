
import asyncio
import json
import logging
import re
from datetime import datetime, timezone

import pandas as pd
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto

import state
from config import MARKETS
from market_data import fetch_candles, MarketDataError
from config import TWELVE_DATA_API_KEY
import websockets
from strategy import assess_trend_health
from chart import create_market_chart
from trading_styles import style_label

logger = logging.getLogger(__name__)

MONITOR_INTERVAL_SECONDS = 15 * 60
PRICE_STREAM_URL = "wss://ws.twelvedata.com/v1/quotes/price"
PRICE_STREAM_HEARTBEAT_SECONDS = 10
PRICE_STREAM_RECONNECT_MAX_SECONDS = 60
PARTIAL_FRACTION = 1.0 / 3.0


def _style_label_for_signal(signal):
    value = signal.get("trading_style")
    if not value:
        return "LEGACY / UNKNOWN"
    try:
        return style_label(value)
    except ValueError:
        return "LEGACY / UNKNOWN"


def _number(value):
    try:
        result = float(value)
        if pd.notna(result):
            return result
    except (TypeError, ValueError):
        pass
    return None


def _timestamp(value):
    if value is None or str(value).strip() in ("", "unknown", "None"):
        return None
    try:
        return pd.to_datetime(value, utc=True)
    except Exception:
        return None


def _signal_id(signal):
    return signal.get("id") or signal.get("signal_id")


def _targets(signal):
    values = [
        signal.get("tp1"),
        signal.get("tp2"),
        signal.get("tp3"),
    ]
    if not any(value is not None for value in values):
        raw = signal.get("take_profits") or []
        if isinstance(raw, (list, tuple)):
            values = list(raw[:3])
    return [_number(value) for value in values[:3]]


def _realised_r(flags, stop_hit=False):
    """
    Realised R for one-third exits at +1R, +2R and +3R.

    An open position only records exits already achieved. The unclosed
    fraction is charged -1R per fraction only when the stop is reached.
    """
    earned = sum(
        (index + 1) * PARTIAL_FRACTION
        for index, hit in enumerate(flags)
        if hit
    )
    if not stop_hit:
        return earned

    remaining_fraction = max(
        0.0,
        1.0 - sum(PARTIAL_FRACTION for hit in flags if hit),
    )
    return earned - remaining_fraction


async def _notify(app, user_id, settings, notification_key, message, reply_markup=None):
    """
    Apply the user's tracking-notification mode.

    Silent mode stores all milestones without Telegram alerts.
    Important mode alerts only for TP1/TP2/TP3 and stop loss.
    All mode alerts for every recorded monitoring outcome/event.
    """
    mode = str(
        settings.get("tracking_notification_mode", "silent")
    ).lower()

    if mode == "silent":
        return

    important_events = {"tp1", "tp2", "tp3", "stop_loss", "trend_warning"}
    if mode == "important" and notification_key not in important_events:
        return

    if mode not in {"important", "all"}:
        logger.warning(
            "Unknown tracking notification mode %r for user %s; using silent.",
            mode, user_id,
        )
        return

    try:
        await app.bot.send_message(chat_id=int(user_id), text=message, reply_markup=reply_markup)
    except Exception:
        logger.exception(
            "Could not send signal-monitor notification to user %s.",
            user_id,
        )


async def _check_one_signal(app, user_id, signal, candle, candle_time):
    signal_id = _signal_id(signal)
    if signal_id is None:
        logger.warning("Tracked signal has no ID; skipped.")
        return

    direction = str(
        signal.get("direction") or signal.get("status") or ""
    ).upper()

    if direction not in ("BUY", "SELL"):
        return

    if str(signal.get("trading_style", "")).upper() == "SCALPING":
        # Scalping has no Stop Loss or TP levels. Record candle progress and
        # let the reversal monitor evaluate completed-candle invalidation.
        state.update_signal(signal_id, {
            "current_price": _number(candle.get("close")),
            "last_checked": str(candle_time),
        })
        return

    entry = _number(signal.get("entry"))
    stop = _number(signal.get("stop_loss"))
    targets = _targets(signal)

    if entry is None or stop is None or any(x is None for x in targets):
        logger.warning("Signal %s has incomplete price levels.", signal_id)
        return

    risk = abs(entry - stop)
    if risk <= 0:
        logger.warning("Signal %s has zero or invalid risk.", signal_id)
        return

    candle_high = _number(candle.get("high"))
    candle_low = _number(candle.get("low"))
    candle_close = _number(candle.get("close"))

    if candle_high is None or candle_low is None or candle_close is None:
        return

    signal_time = _timestamp(signal.get("candle_time"))
    checked_time = _timestamp(signal.get("last_checked"))
    current_time = _timestamp(candle_time)

    if current_time is None:
        return
    if signal_time is not None and current_time <= signal_time:
        return
    if checked_time is not None and current_time <= checked_time:
        return

    settings = state.get_user_settings(user_id) or {}
    flags = [
        bool(signal.get("tp1_hit")),
        bool(signal.get("tp2_hit")),
        bool(signal.get("tp3_hit")),
    ]

    if direction == "BUY":
        stop_hit = candle_low <= stop
        target_hits = [
            candle_high >= target for target in targets
        ]
    else:
        stop_hit = candle_high >= stop
        target_hits = [
            candle_low <= target for target in targets
        ]

    newly_hit = [
        target_hits[index] and not flags[index]
        for index in range(3)
    ]

    updates = {
        "current_price": candle_close,
        "last_checked": str(candle_time),
    }

    # OHLC candles cannot tell us whether the stop or a new target
    # was reached first when both occur inside the same candle.
    if stop_hit and any(newly_hit):
        updates["last_checked"] = str(candle_time)
        state.update_signal(signal_id, updates)
        state.add_signal_event(
            signal_id,
            "AMBIGUOUS",
            "The same completed candle touched the stop loss and a new "
            "take-profit level. The order cannot be established from OHLC.",
            {
                "candle_time": str(candle_time),
                "high": candle_high,
                "low": candle_low,
                "close": candle_close,
            },
        )
        state.complete_signal(
            signal_id,
            "AMBIGUOUS",
            "Outcome requires review because stop loss and a new target "
            "were both touched in the same candle.",
        )
        await _notify(
            app, user_id, settings, "system",
            f"⚠️ {_style_label_for_signal(signal)} — {signal.get('symbol', 'Signal')}: outcome is ambiguous. "
            "The stop loss and a new target were both touched in one "
            "candle. Please review the chart.",
        )
        return

    if stop_hit:
        realised = _realised_r(flags, stop_hit=True)
        updates.update({
            "stop_loss_hit": True,
            "realised_r": realised,
            "result_r": realised,
        })
        state.update_signal(signal_id, updates)
        state.add_signal_event(
            signal_id,
            "STOP_LOSS",
            f"Stop loss reached. Realised result: {realised:+.2f}R.",
            {
                "realised_r": realised,
                "candle_time": str(candle_time),
            },
        )
        state.complete_signal(
            signal_id,
            "STOP_LOSS",
            f"Stop loss reached. Realised result: {realised:+.2f}R.",
        )
        await _notify(
            app, user_id, settings, "stop_loss",
            f"🛑 {_style_label_for_signal(signal)} — {signal.get('symbol', 'Signal')}: stop loss reached. "
            f"Realised result: {realised:+.2f}R.",
        )
        return

    changed = False
    milestone_notifications = []
    for index in range(3):
        if newly_hit[index]:
            flags[index] = True
            changed = True
            target_number = index + 1
            target_r = target_number
            realised = _realised_r(flags)

            updates[f"tp{target_number}_hit"] = True
            updates["realised_r"] = realised
            updates["result_r"] = realised

            state.add_signal_event(
                signal_id,
                f"TP{target_number}",
                f"TP{target_number} reached. One-third partial exit recorded. "
                f"Realised result: {realised:+.2f}R.",
                {
                    "target_price": targets[index],
                    "target_r": target_r,
                    "realised_r": realised,
                    "candle_time": str(candle_time),
                },
            )
            milestone_notifications.append((target_number, realised))

    if changed:
        # Persist target flags before sending action buttons so a fast tap
        # cannot arrive before the milestone is visible to the callback.
        state.update_signal(signal_id, updates)
        if flags[2]:
            state.complete_signal(
                signal_id,
                "TP3",
                "TP3 reached. All three one-third partial exits recorded. "
                "Total realised result: +2.00R.",
            )
        latest_milestone = max((number for number, _ in milestone_notifications), default=0)
        for target_number, realised in milestone_notifications:
            keyboard = None
            if target_number == latest_milestone and target_number in (1, 2):
                keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton(
                        f"🔒 Secure profit — close after TP{target_number}",
                        callback_data=f"manualclose:{signal_id}",
                    )],
                    [InlineKeyboardButton(
                        "▶️ Continue tracking",
                        callback_data=f"manualcontinue:{signal_id}",
                    )],
                ])
            message = (
                f"🎯 {_style_label_for_signal(signal)} — {signal.get('symbol', 'Signal')}: TP{target_number} reached. "
                f"One-third partial exit recorded. Realised result: {realised:+.2f}R."
            )
            if keyboard:
                message += (
                    f"\n\nWould you like to close and record a TP{target_number} "
                    "milestone win, or continue towards the next target?"
                )
            await _notify(
                app, user_id, settings, f"tp{target_number}",
                message, reply_markup=keyboard,
            )
    else:
        state.update_signal(signal_id, updates)



def _stream_symbols():
    """Return the active tracked symbols, bounded by the provider's 8 trial slots."""
    symbols = set()
    for user_id in state.get_users():
        try:
            if not state.user_monitoring_enabled(user_id):
                continue
            for signal in state.get_tracked_signals(user_id):
                symbol = signal.get("symbol")
                if symbol in MARKETS:
                    symbols.add(symbol)
        except Exception:
            logger.exception("Could not collect symbols for the live price stream.")
    return sorted(symbols)[:8]


async def _check_one_price(app, user_id, signal, quote):
    """Record target/stop milestones from each live quote, independent of chart timeframe."""
    signal_id = _signal_id(signal)
    if signal_id is None or signal.get("status") != "TRACKING":
        return

    direction = str(signal.get("direction") or "").upper()
    if direction not in {"BUY", "SELL"}:
        return

    if str(signal.get("trading_style", "")).upper() == "SCALPING":
        # Quotes update the display only; scalp exits are not represented as
        # hidden price-based Stop Loss or take-profit levels.
        state.update_signal(signal_id, {
            "current_price": _number(quote.get("bid") or quote.get("price")),
            "last_price_timestamp": datetime.now(timezone.utc).isoformat(),
        })
        return

    # A long position exits on bid; a short position exits on ask.
    raw_price = quote.get("bid") if direction == "BUY" else quote.get("ask")
    if raw_price is None:
        raw_price = quote.get("price")
    price = _number(raw_price)
    if price is None:
        return

    timestamp = quote.get("timestamp")
    try:
        tick_time = datetime.fromtimestamp(float(timestamp), tz=timezone.utc)
    except (TypeError, ValueError, OverflowError):
        tick_time = datetime.now(timezone.utc)

    tick_iso = tick_time.isoformat()
    previous_tick = _timestamp(signal.get("last_price_timestamp"))
    if previous_tick is not None and pd.Timestamp(tick_time) <= previous_tick:
        return

    entry = _number(signal.get("entry"))
    stop = _number(signal.get("stop_loss"))
    targets = _targets(signal)
    if entry is None or stop is None or any(value is None for value in targets):
        logger.warning("Signal %s has incomplete price levels.", signal_id)
        return
    if abs(entry - stop) <= 0:
        return

    flags = [bool(signal.get(f"tp{i}_hit")) for i in (1, 2, 3)]
    # Only compare two actual stream observations; a candle close from the
    # slower fallback monitor is not a reliable previous tick.
    previous_price = (
        _number(signal.get("current_price")) if previous_tick is not None else None
    )

    if direction == "BUY":
        stop_hit = price <= stop
        target_hits = [price >= target for target in targets]
        crossed_stop_and_target = (
            previous_price is not None
            and previous_price >= min(targets[i] for i in range(3) if not flags[i])
            and price <= stop
            if not all(flags) else False
        )
    else:
        stop_hit = price >= stop
        target_hits = [price <= target for target in targets]
        crossed_stop_and_target = (
            previous_price is not None
            and previous_price <= max(targets[i] for i in range(3) if not flags[i])
            and price >= stop
            if not all(flags) else False
        )

    newly_hit = [target_hits[i] and not flags[i] for i in range(3)]
    settings = state.get_user_settings(user_id) or {}

    if crossed_stop_and_target:
        state.update_signal(signal_id, {
            "current_price": price,
            "last_price_timestamp": tick_iso,
        })
        state.add_signal_event(
            signal_id, "AMBIGUOUS",
            "A price gap crossed both an unrecorded target and stop loss "
            "between received quotes; the execution order cannot be established.",
            {"previous_price": previous_price, "price": price, "timestamp": tick_iso},
        )
        state.complete_signal(
            signal_id, "AMBIGUOUS",
            "Outcome requires review because a price gap crossed both a target and stop.",
        )
        await _notify(
            app, user_id, settings, "system",
            f"⚠️ {_style_label_for_signal(signal)} — {signal.get('symbol', 'Signal')}: price gap crossed both a target "
            "and stop loss between quotes. Outcome marked ambiguous for review.",
        )
        return

    updates = {"current_price": price, "last_price_timestamp": tick_iso}
    if stop_hit:
        realised = _realised_r(flags, stop_hit=True)
        updates.update({
            "stop_loss_hit": True,
            "realised_r": realised,
            "result_r": realised,
        })
        state.update_signal(signal_id, updates)
        state.add_signal_event(
            signal_id, "STOP_LOSS",
            f"Stop loss reached from live price stream. Realised result: {realised:+.2f}R.",
            {"price": price, "realised_r": realised, "timestamp": tick_iso},
        )
        state.complete_signal(
            signal_id, "STOP_LOSS",
            f"Stop loss reached. Realised result: {realised:+.2f}R.",
        )
        await _notify(
            app, user_id, settings, "stop_loss",
            f"🛑 {_style_label_for_signal(signal)} — {signal.get('symbol', 'Signal')}: stop loss reached. "
            f"Live quote: {price:g}. Realised result: {realised:+.2f}R.",
        )
        return

    milestones = []
    for index in range(3):
        if newly_hit[index]:
            flags[index] = True
            number = index + 1
            realised = _realised_r(flags)
            updates[f"tp{number}_hit"] = True
            updates["realised_r"] = realised
            updates["result_r"] = realised
            milestones.append((number, realised, targets[index]))

    state.update_signal(signal_id, updates)
    for number, realised, target_price in milestones:
        state.add_signal_event(
            signal_id, f"TP{number}",
            f"TP{number} reached from live price stream. "
            f"One-third partial exit recorded. Realised result: {realised:+.2f}R.",
            {
                "target_price": target_price,
                "price": price,
                "target_r": number,
                "realised_r": realised,
                "timestamp": tick_iso,
                "price_source": "Twelve Data WebSocket",
            },
        )

    if flags[2]:
        state.complete_signal(
            signal_id, "TP3",
            "TP3 reached from live price stream. All three one-third partial exits recorded. "
            "Total realised result: +2.00R.",
        )

    for number, realised, _ in milestones:
        keyboard = None
        if number in (1, 2) and number == max(item[0] for item in milestones):
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton(
                    f"🔒 Secure profit — close after TP{number}",
                    callback_data=f"manualclose:{signal_id}",
                )],
                [InlineKeyboardButton(
                    "▶️ Continue tracking",
                    callback_data=f"manualcontinue:{signal_id}",
                )],
            ])
        message = (
            f"🎯 {signal.get('symbol', 'Signal')}: TP{number} reached. "
            f"Live quote: {price:g}. One-third partial exit recorded. "
            f"Realised result: {realised:+.2f}R."
        )
        if keyboard:
            message += (
                f"\n\nWould you like to close and record a TP{number} "
                "milestone win, or continue towards the next target?"
            )
        await _notify(app, user_id, settings, f"tp{number}", message, reply_markup=keyboard)


async def _process_stream_quote(app, quote):
    symbol = str(quote.get("symbol") or "").strip().upper()
    if symbol not in MARKETS:
        return
    for user_id in state.get_users():
        try:
            if not state.user_monitoring_enabled(user_id):
                continue
            for signal in state.get_tracked_signals(user_id):
                if str(signal.get("symbol", "")).upper() == symbol:
                    current = state.get_signal(_signal_id(signal), user_id)
                    if current and current.get("status") == "TRACKING":
                        await _check_one_price(app, user_id, current, quote)
        except Exception:
            logger.exception("Live quote processing failed for %s / user %s.", symbol, user_id)


async def live_price_stream_loop(app):
    """One shared Twelve Data WebSocket connection; REST credits are not consumed by ticks."""
    if not TWELVE_DATA_API_KEY:
        logger.warning("Live price stream disabled: Twelve Data API key is missing.")
        return

    backoff = 5
    while True:
        try:
            symbols = _stream_symbols()
            if not symbols:
                await asyncio.sleep(15)
                continue

            uri = f"{PRICE_STREAM_URL}?apikey={TWELVE_DATA_API_KEY}"
            async with websockets.connect(
                uri, ping_interval=None, open_timeout=15, close_timeout=5
            ) as websocket:
                await websocket.send(json.dumps({
                    "action": "subscribe",
                    "params": {"symbols": ",".join(symbols)},
                }))
                logger.info("Requested live price subscriptions for %s.", ", ".join(symbols))
                backoff = 5
                last_heartbeat = asyncio.get_running_loop().time()
                last_refresh = last_heartbeat

                while True:
                    now = asyncio.get_running_loop().time()
                    if now - last_heartbeat >= PRICE_STREAM_HEARTBEAT_SECONDS:
                        await websocket.send(json.dumps({"action": "heartbeat"}))
                        last_heartbeat = now
                    if now - last_refresh >= 60:
                        latest_symbols = _stream_symbols()
                        new_symbols = [item for item in latest_symbols if item not in symbols]
                        if new_symbols:
                            await websocket.send(json.dumps({
                                "action": "subscribe",
                                "params": {"symbols": ",".join(new_symbols)},
                            }))
                            symbols = sorted(set(symbols).union(new_symbols))
                            logger.info("Added live price subscriptions: %s.", ", ".join(new_symbols))
                        last_refresh = now

                    try:
                        raw = await asyncio.wait_for(websocket.recv(), timeout=1)
                    except asyncio.TimeoutError:
                        continue

                    try:
                        payload = json.loads(raw)
                    except (TypeError, ValueError):
                        logger.warning("Ignored malformed Twelve Data WebSocket message.")
                        continue

                    if payload.get("event") == "price":
                        await _process_stream_quote(app, payload)
                    elif payload.get("event") == "subscribe-status":
                        failed = payload.get("fails") or []
                        if failed:
                            logger.warning("Twelve Data rejected live subscriptions: %s", failed)
                        logger.info(
                            "Twelve Data subscription status: %s successful, %s failed.",
                            len(payload.get("success") or []), len(failed),
                        )
                    elif payload.get("status") == "error":
                        logger.error("Twelve Data WebSocket error: %s", payload)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "Live price stream disconnected; reconnecting in %s seconds.", backoff
            )
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, PRICE_STREAM_RECONNECT_MAX_SECONDS)



async def _check_trend_warning(app, user_id, signal, frame):
    """Record and notify once per completed candle for each warning type."""
    signal_id = _signal_id(signal)
    warning = assess_trend_health(frame, signal.get("direction"))
    previous_warning = signal.get("trend_warning_key")
    if not warning:
        if previous_warning:
            state.update_signal(signal_id, {"trend_warning_key": None})
        return

    # One alert per continuous warning episode, not one alert per candle.
    warning_key = warning["type"]
    if previous_warning == warning_key:
        return

    state.update_signal(signal_id, {"trend_warning_key": warning_key})
    state.add_signal_event(
        signal_id,
        warning["type"],
        warning["message"],
        {"candle_time": warning["candle_time"]},
    )
    is_scalping = str(signal.get("trading_style", "")).upper() == "SCALPING"
    if is_scalping and warning["type"] == "TREND_REVERSAL_WARNING":
        state.complete_signal(
            signal_id,
            "INVALIDATED",
            "Scalping setup invalidated by a confirmed completed-candle "
            "reversal warning. No Stop Loss or take-profit levels were used.",
        )
    settings = state.get_user_settings(user_id) or {}
    mode = str(settings.get("tracking_notification_mode", "silent")).lower()
    if mode not in {"important", "all"}:
        return

    # Send the weakening-trend warning with its own fresh chart snapshot.
    timeframe = str(signal.get("timeframe") or "15m")
    try:
        chart = create_market_chart(
            frame,
            signal.get("symbol", "Unknown"),
            timeframe,
            entry=signal.get("entry"),
            stop_loss=signal.get("stop_loss"),
            take_profits=_targets(signal),
            strategy=signal.get("strategy"),
            trading_style=signal.get("trading_style"),
        )
        chart.seek(0)
        latest = float(frame["close"].iloc[-1])
        caption = (
            f"⚠️ {_style_label_for_signal(signal)} — TREND WEAKENING / REVERSAL RISK\n"
            f"{signal.get('symbol', 'Tracked signal')} — {signal.get('direction', '')}\n"
            f"Signal #{signal_id} | {timeframe.upper()}\n"
            f"Latest closed-candle price: {latest:g}\n"
            f"{warning['message']}\n\n"
            "Chart shows the latest completed candles and the applicable style information. "
            "This is a warning, not a confirmed reversal."
        )[:950]
        await app.bot.send_photo(chat_id=int(user_id), photo=chart, caption=caption)
    except Exception:
        logger.exception("Could not send trend-warning chart for signal %s.", signal_id)
        await _notify(
            app, user_id, settings, "trend_warning",
            f"⚠️ {_style_label_for_signal(signal)} — {signal.get('symbol', 'Tracked signal')} — {warning['message']}\n"
            "⚠️ Warning chart could not be generated this time.",
        )


async def _refresh_saved_chart(app, user_id, signal, frame):
    """Edit the user's existing tracked-signal chart; never send chart spam."""
    chat_id = signal.get("chart_chat_id")
    message_id = signal.get("chart_message_id")
    signal_id = _signal_id(signal)
    if not chat_id or not message_id or signal.get("status") != "TRACKING":
        return

    timeframe = str(signal.get("timeframe") or "15m")
    try:
        chart = create_market_chart(
            frame,
            signal.get("symbol", "Unknown"),
            timeframe,
            entry=signal.get("entry"),
            stop_loss=signal.get("stop_loss"),
            take_profits=_targets(signal),
            strategy=signal.get("strategy"),
            trading_style=signal.get("trading_style"),
        )
        chart.seek(0)
        latest = float(frame["close"].iloc[-1])
        caption = (
            f"📈 {_style_label_for_signal(signal)} — {signal.get('symbol', 'Unknown')} — {signal.get('direction', 'N/A')}\n"
            f"Signal #{signal_id} | {timeframe.upper()}\n"
            f"Latest closed-candle price: {latest:g}\n"
            + (
                f"Entry: {signal.get('entry', 'N/A')} | SL: {signal.get('stop_loss', 'N/A')}\n"
                f"TP1: {signal.get('tp1', 'N/A')} | TP2: {signal.get('tp2', 'N/A')} | "
                f"TP3: {signal.get('tp3', 'N/A')}\n"
                f"Progress: {'✓' if signal.get('tp1_hit') else '○'} TP1  "
                f"{'✓' if signal.get('tp2_hit') else '○'} TP2  "
                f"{'✓' if signal.get('tp3_hit') else '○'} TP3\n"
                if str(signal.get("trading_style", "")).upper() != "SCALPING"
                else "Management: completed-candle reversal / invalidation monitoring\n"
            )
            + f"Status: {signal.get('status', 'TRACKING')}\n"
            "Updated from completed candles. Not a tick-by-tick live feed."
        )[:950]
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Refresh Chart", callback_data=f"signalrefresh:{signal_id}")],
            [InlineKeyboardButton("⬅️ Back to Tracked Signals", callback_data="signalback")],
        ])
        await app.bot.edit_message_media(
            chat_id=int(chat_id),
            message_id=int(message_id),
            media=InputMediaPhoto(media=chart, caption=caption),
            reply_markup=keyboard,
        )
        state.update_signal(signal_id, {
            "chart_last_update": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as exc:
        # A user may have deleted the message or Telegram may reject an old
        # message edit. Keep monitoring and avoid repeatedly logging noise.
        logger.debug(
            "Saved chart refresh skipped for signal %s (%s).",
            signal_id, exc,
        )



async def _fetch_monitor_frame(symbol, timeframe):
    """Respect the shared provider cooldown instead of silently skipping a market."""
    for attempt in range(5):
        try:
            return await fetch_candles(
                MARKETS[symbol],
                outputsize=100,
                interval=timeframe,
                purpose="monitor",
            )
        except MarketDataError as exc:
            match = re.search(r"cooldown active\. Try again in ([0-9.]+) seconds", str(exc))
            if not match or attempt == 4:
                raise
            await asyncio.sleep(float(match.group(1)) + 0.25)
    return None


async def _monitor_cycle(app):
    # Collect each user's signals, then fetch candles once per
    # market/timeframe combination for all users.
    grouped = {}

    for user_id in state.get_users():
        try:
            if not state.user_monitoring_enabled(user_id):
                continue
            signals = state.get_tracked_signals(user_id)
        except Exception:
            logger.exception(
                "Could not load tracked signals for user %s.", user_id
            )
            continue

        for signal in signals:
            symbol = signal.get("symbol")
            timeframe = str(signal.get("timeframe") or "15m")
            signal_id = _signal_id(signal)

            if not symbol or symbol not in MARKETS or signal_id is None:
                continue

            key = (symbol, timeframe)
            grouped.setdefault(key, []).append((user_id, signal))

    for (symbol, timeframe), records in grouped.items():
        try:
            frame = await _fetch_monitor_frame(symbol, timeframe)
            if frame is None or frame.empty:
                continue

            for user_id, original_signal in records:
                signal = original_signal
                try:
                    last_checked = _timestamp(signal.get("last_checked"))
                    signal_time = _timestamp(signal.get("candle_time"))
                    pending_candles = frame
                    if last_checked is not None:
                        pending_candles = pending_candles[
                            pending_candles.index > last_checked
                        ]
                    if signal_time is not None:
                        pending_candles = pending_candles[
                            pending_candles.index > signal_time
                        ]

                    # Process every unseen closed candle, not only the latest.
                    for candle_time, candle in pending_candles.iterrows():
                        current = state.get_signal(_signal_id(signal), user_id)
                        if not current or current.get("status") != "TRACKING":
                            break
                        await _check_one_signal(
                            app, user_id, current, candle, candle_time
                        )

                    current = state.get_signal(_signal_id(signal), user_id)
                    if current and current.get("status") == "TRACKING":
                        await _check_trend_warning(app, user_id, current, frame)
                        current = state.get_signal(_signal_id(signal), user_id) or current
                        await _refresh_saved_chart(app, user_id, current, frame)
                except Exception:
                    logger.exception(
                        "Could not monitor signal %s for user %s.",
                        _signal_id(signal), user_id,
                    )

        except MarketDataError as exc:
            logger.warning(
                "Market data unavailable while monitoring %s: %s",
                symbol, exc,
            )
        except Exception:
            logger.exception(
                "Monitoring cycle failed for %s (%s).", symbol, timeframe
            )


async def monitor_signals_loop(app):
    logger.info(
        "Automatic saved-signal monitoring started. "
        "Check interval: %s seconds.",
        MONITOR_INTERVAL_SECONDS,
    )
    while True:
        try:
            await _monitor_cycle(app)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Saved-signal monitoring cycle failed.")
        await asyncio.sleep(MONITOR_INTERVAL_SECONDS)
