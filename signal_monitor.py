
import asyncio
import logging
from datetime import datetime, timezone

import pandas as pd
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto

import state
from config import MARKETS
from market_data import fetch_candles, MarketDataError
from strategy import assess_trend_health
from chart import create_market_chart

logger = logging.getLogger(__name__)

MONITOR_INTERVAL_SECONDS = 15 * 60
PARTIAL_FRACTION = 1.0 / 3.0


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


async def _notify(app, user_id, settings, notification_key, message):
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
        await app.bot.send_message(chat_id=int(user_id), text=message)
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
            f"⚠️ {signal.get('symbol', 'Signal')}: outcome is ambiguous. "
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
            f"🛑 {signal.get('symbol', 'Signal')}: stop loss reached. "
            f"Realised result: {realised:+.2f}R.",
        )
        return

    changed = False
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

            await _notify(
                app, user_id, settings, f"tp{target_number}",
                f"🎯 {signal.get('symbol', 'Signal')}: TP{target_number} "
                f"reached. One-third partial exit recorded. "
                f"Realised result: {realised:+.2f}R.",
            )

    if changed:
        state.update_signal(signal_id, updates)
        if flags[2]:
            state.complete_signal(
                signal_id,
                "TP3",
                "TP3 reached. All three one-third partial exits recorded. "
                "Total realised result: +2.00R.",
            )
    else:
        state.update_signal(signal_id, updates)



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
    settings = state.get_user_settings(user_id) or {}
    await _notify(
        app, user_id, settings, "trend_warning",
        f"⚠️ {signal.get('symbol', 'Tracked signal')} — {warning['message']}",
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
        )
        chart.seek(0)
        latest = float(frame["close"].iloc[-1])
        caption = (
            f"📈 {signal.get('symbol', 'Unknown')} — {signal.get('direction', 'N/A')}\n"
            f"Signal #{signal_id} | {timeframe.upper()}\n"
            f"Latest closed-candle price: {latest:g}\n"
            f"Entry: {signal.get('entry', 'N/A')} | SL: {signal.get('stop_loss', 'N/A')}\n"
            f"TP1: {signal.get('tp1', 'N/A')} | TP2: {signal.get('tp2', 'N/A')} | "
            f"TP3: {signal.get('tp3', 'N/A')}\n"
            f"Progress: {'✓' if signal.get('tp1_hit') else '○'} TP1  "
            f"{'✓' if signal.get('tp2_hit') else '○'} TP2  "
            f"{'✓' if signal.get('tp3_hit') else '○'} TP3\n"
            f"Status: {signal.get('status', 'TRACKING')}\n"
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
            frame = await fetch_candles(
                MARKETS[symbol],
                outputsize=100,
                interval=timeframe,
                purpose="monitor",
            )
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
