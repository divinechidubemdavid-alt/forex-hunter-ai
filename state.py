

import json
import os
import threading
import time
from datetime import datetime, timezone

from config import (
    STATE_FILE,
    TWELVE_DATA_DAILY_CREDIT_LIMIT,
    MIN_REQUEST_INTERVAL_SECONDS,
)

_lock = threading.RLock()


def _now():
    return datetime.now(timezone.utc).isoformat()


def _today():
    return datetime.now(timezone.utc).date().isoformat()


def _empty_state():
    return {
        "date": _today(),
        "api_requests": 0,
        "last_request_epoch": 0.0,
        "last_alerts": {},
        "users": {},
        "timeframes": {},
        "monitoring_enabled": {},
        "active_trades": {},
        "next_trade_id": 1,
        "signals": {},
        "signal_events": [],
        "next_signal_id": 1,
        "scanner_state": {},
        "api_budget": {},
    }


def _load_state():
    data = _empty_state()

    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as file:
                saved = json.load(file)

            if isinstance(saved, dict):
                data.update(saved)

        except (OSError, ValueError, TypeError):
            # Do not overwrite a damaged file automatically.
            damaged = STATE_FILE + ".damaged"
            try:
                if not os.path.exists(damaged):
                    os.replace(STATE_FILE, damaged)
            except OSError:
                pass

    defaults = _empty_state()

    for key, default in defaults.items():
        if key not in data or not isinstance(data[key], type(default)):
            data[key] = default

    if data.get("date") != _today():
        data["date"] = _today()
        data["api_requests"] = 0
        data["last_request_epoch"] = 0.0

    return data


_state = _load_state()


def _save():
    directory = os.path.dirname(STATE_FILE)

    if directory:
        os.makedirs(directory, exist_ok=True)

    temporary = STATE_FILE + ".tmp"

    with open(temporary, "w", encoding="utf-8") as file:
        json.dump(_state, file, indent=2, ensure_ascii=False)

    os.replace(temporary, STATE_FILE)


def _refresh_daily_counter():
    if _state.get("date") != _today():
        _state["date"] = _today()
        _state["api_requests"] = 0
        _state["last_request_epoch"] = 0.0


# ---------------------------------------------------------
# API BUDGET
# ---------------------------------------------------------

def reserve_api_request():
    """
    Reserve one request against the local daily safety limit.

    Important: this counts requests, not guaranteed provider credits.
    The final API controller must account for endpoint-specific costs.
    """

    with _lock:
        _refresh_daily_counter()

        limit = int(TWELVE_DATA_DAILY_CREDIT_LIMIT)
        used = int(_state.get("api_requests", 0))

        if used >= limit:
            return False, (
                "Daily API safety limit reached. "
                "The counter resets on the next UTC day."
            )

        now = time.time()
        previous = float(_state.get("last_request_epoch", 0.0))
        elapsed = now - previous
        wait = float(MIN_REQUEST_INTERVAL_SECONDS) - elapsed

        if wait > 0:
            return False, (
                f"API cooldown active. Try again in {wait:.1f} seconds."
            )

        _state["api_requests"] = used + 1
        _state["last_request_epoch"] = now
        _save()

        return True, "Request reserved."


def get_api_usage():
    with _lock:
        _refresh_daily_counter()

        used = int(_state.get("api_requests", 0))
        limit = int(TWELVE_DATA_DAILY_CREDIT_LIMIT)

        return {
            "used": used,
            "limit": limit,
            "remaining": max(0, limit - used),
            "date": _state["date"],
        }


# ---------------------------------------------------------
# USER RECORDS AND PERSONAL SETTINGS
# ---------------------------------------------------------

def ensure_user(user_id):
    key = str(user_id)

    with _lock:
        user = _state["users"].setdefault(key, {})

        user.setdefault("monitoring_enabled", True)
        user.setdefault("markets", [
            "XAU/USD",
            "EUR/USD",
            "GBP/USD",
            "USD/JPY",
            "AUD/USD",
            "USD/CAD",
            "USD/CHF",
            "BTC/USD",
        ])
        user.setdefault("notifications", {
            "new_signals": True,
            "tp1": True,
            "tp2": True,
            "tp3": True,
            "stop_loss": True,
            "weakening": True,
            "system": True,
        })
        user.setdefault("risk_percent", 1.0)
        user.setdefault("risk_amount", None)
        user.setdefault("timezone", "Africa/Lagos")

        _state["monitoring_enabled"].setdefault(key, True)
        _state["timeframes"].setdefault(key, "15m")

        _save()
        return dict(user)


def get_user_settings(user_id):
    with _lock:
        user = ensure_user(user_id)
        return user


def update_user_setting(user_id, setting, value):
    allowed = {
        "markets",
        "notifications",
        "risk_percent",
        "risk_amount",
        "timezone",
    }

    if setting not in allowed:
        raise ValueError("That setting cannot be changed.")

    with _lock:
        key = str(user_id)
        user = _state["users"].setdefault(key, {})
        user[setting] = value
        _save()
        return dict(user)


def get_users():
    with _lock:
        return list(_state["users"].keys())


def user_monitoring_enabled(user_id):
    with _lock:
        return bool(
            _state["monitoring_enabled"].get(str(user_id), True)
        )


def set_user_monitoring(user_id, enabled):
    with _lock:
        key = str(user_id)
        value = bool(enabled)

        _state["monitoring_enabled"][key] = value
        _state["users"].setdefault(key, {})[
            "monitoring_enabled"
        ] = value

        _save()


def get_timeframe(user_id):
    with _lock:
        return _state["timeframes"].get(str(user_id), "15m")


def set_timeframe(user_id, timeframe):
    allowed = {
        "1m", "5m", "15m", "30m",
        "1h", "4h", "1d", "1day",
    }

    value = str(timeframe).lower()

    if value not in allowed:
        raise ValueError("Unsupported timeframe.")

    with _lock:
        _state["timeframes"][str(user_id)] = value
        _save()


# ---------------------------------------------------------
# ALERT DEDUPLICATION
# ---------------------------------------------------------

def get_last_alert(key):
    with _lock:
        return _state["last_alerts"].get(str(key))


def set_last_alert(key, value):
    with _lock:
        _state["last_alerts"][str(key)] = value
        _save()


# ---------------------------------------------------------
# SIGNAL TRACKING
# ---------------------------------------------------------

def add_signal(user_id, signal):
    """
    Save an individual signal.

    Multiple signals for the same market are allowed.
    """

    with _lock:
        signal_id = str(_state["next_signal_id"])
        _state["next_signal_id"] += 1

        record = dict(signal)
        record.update({
            "id": signal_id,
            "user_id": str(user_id),
            "status": "TRACKING",
            "created_at": _now(),
            "updated_at": _now(),
            "completed_at": None,
            "events": [],
        })

        _state["signals"][signal_id] = record

        _add_signal_event_locked(
            signal_id,
            "SIGNAL_TRACKED",
            "Signal added to tracking.",
        )

        _save()
        return dict(record)


def get_signal(signal_id, user_id=None):
    with _lock:
        signal = _state["signals"].get(str(signal_id))

        if not signal:
            return None

        if user_id is not None:
            if signal.get("user_id") != str(user_id):
                return None

        return dict(signal)


def get_tracked_signals(user_id=None):
    with _lock:
        records = []

        for signal in _state["signals"].values():
            if signal.get("status") != "TRACKING":
                continue

            if user_id is not None:
                if signal.get("user_id") != str(user_id):
                    continue

            records.append(dict(signal))

        return sorted(
            records,
            key=lambda item: item.get("created_at", ""),
            reverse=True,
        )


def get_signal_history(user_id=None, limit=100):
    with _lock:
        records = []

        for signal in _state["signals"].values():
            if user_id is not None:
                if signal.get("user_id") != str(user_id):
                    continue

            records.append(dict(signal))

        records.sort(
            key=lambda item: item.get("created_at", ""),
            reverse=True,
        )

        return records[:max(1, int(limit))]


def _add_signal_event_locked(signal_id, event_type, message, details=None):
    event = {
        "signal_id": str(signal_id),
        "type": str(event_type),
        "message": str(message),
        "details": details or {},
        "timestamp": _now(),
    }

    _state["signal_events"].append(event)

    signal = _state["signals"].get(str(signal_id))

    if signal is not None:
        signal.setdefault("events", []).append(event)
        signal["updated_at"] = event["timestamp"]

    return event


def add_signal_event(signal_id, event_type, message, details=None):
    with _lock:
        if str(signal_id) not in _state["signals"]:
            return False

        _add_signal_event_locked(
            signal_id,
            event_type,
            message,
            details,
        )

        _save()
        return True


def update_signal(signal_id, updates):
    allowed = {
        "status",
        "current_price",
        "current_stop_loss",
        "tp1_hit",
        "tp2_hit",
        "tp3_hit",
        "stop_loss_hit",
        "last_checked",
        "result_r",
        "realised_r",
    }

    with _lock:
        signal = _state["signals"].get(str(signal_id))

        if not signal:
            return None

        for key, value in updates.items():
            if key in allowed:
                signal[key] = value

        signal["updated_at"] = _now()
        _save()

        return dict(signal)


def complete_signal(signal_id, result, message="Signal completed."):
    allowed_results = {
        "TP3",
        "STOP_LOSS",
        "INVALIDATED",
        "MANUALLY_STOPPED",
        "AMBIGUOUS",
    }

    if result not in allowed_results:
        raise ValueError("Invalid signal completion result.")

    with _lock:
        signal = _state["signals"].get(str(signal_id))

        if not signal or signal.get("status") != "TRACKING":
            return False

        signal["status"] = result
        signal["completed_at"] = _now()
        signal["updated_at"] = _now()

        _add_signal_event_locked(
            signal_id,
            result,
            message,
        )

        _save()
        return True


def stop_tracking_signal(signal_id, user_id):
    """
    Stop monitoring without recording a win or loss.
    """

    with _lock:
        signal = _state["signals"].get(str(signal_id))

        if not signal:
            return False

        if signal.get("user_id") != str(user_id):
            return False

        if signal.get("status") != "TRACKING":
            return False

        signal["status"] = "MANUALLY_STOPPED"
        signal["completed_at"] = _now()

        _add_signal_event_locked(
            signal_id,
            "MANUALLY_STOPPED",
            "User stopped tracking this signal.",
        )

        _save()
        return True


def get_signal_events(signal_id=None, user_id=None):
    with _lock:
        events = []

        for event in _state["signal_events"]:
            if signal_id is not None:
                if event.get("signal_id") != str(signal_id):
                    continue

            if user_id is not None:
                signal = _state["signals"].get(
                    str(event.get("signal_id"))
                )

                if not signal or signal.get("user_id") != str(user_id):
                    continue

            events.append(dict(event))

        return events


# ---------------------------------------------------------
# SCANNER STATE
# ---------------------------------------------------------

def get_scanner_state(key, default=None):
    with _lock:
        return _state["scanner_state"].get(str(key), default)


def set_scanner_state(key, value):
    with _lock:
        _state["scanner_state"][str(key)] = value
        _save()


# ---------------------------------------------------------
# LEGACY TRADE FUNCTIONS
# Kept temporarily for compatibility during file replacement.
# ---------------------------------------------------------

def add_trade(user_id, trade):
    with _lock:
        trade_id = str(_state["next_trade_id"])
        _state["next_trade_id"] += 1

        record = dict(trade)
        record["id"] = trade_id
        record["user_id"] = str(user_id)
        record["active"] = True
        record["created_at"] = _now()

        _state["active_trades"][trade_id] = record
        _save()

        return dict(record)


def get_active_trades(user_id=None):
    with _lock:
        trades = [
            dict(trade)
            for trade in _state["active_trades"].values()
            if trade.get("active", True)
        ]

        if user_id is not None:
            trades = [
                trade for trade in trades
                if trade.get("user_id") == str(user_id)
            ]

        return trades


def get_trade(trade_id, user_id=None):
    with _lock:
        trade = _state["active_trades"].get(str(trade_id))

        if not trade or not trade.get("active", True):
            return None

        if user_id is not None:
            if trade.get("user_id") != str(user_id):
                return None

        return dict(trade)


def close_trade(trade_id, user_id=None):
    with _lock:
        trade = _state["active_trades"].get(str(trade_id))

        if not trade or not trade.get("active", True):
            return False

        if user_id is not None:
            if trade.get("user_id") != str(user_id):
                return False

        trade["active"] = False
        trade["closed_at"] = _now()

        _save()
        return True
