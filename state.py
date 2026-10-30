

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
            pass

    if data.get("date") != _today():
        data["date"] = _today()
        data["api_requests"] = 0
        data["last_request_epoch"] = 0.0

    for key, default in _empty_state().items():
        if key not in data or not isinstance(data[key], type(default)):
            data[key] = default

    return data


_state = _load_state()


def _save():
    temporary = STATE_FILE + ".tmp"

    with open(temporary, "w", encoding="utf-8") as file:
        json.dump(_state, file, indent=2)

    os.replace(temporary, STATE_FILE)


def _refresh_daily_counter():
    if _state.get("date") != _today():
        _state["date"] = _today()
        _state["api_requests"] = 0
        _state["last_request_epoch"] = 0.0


def reserve_api_request():
    """Reserve one API request without exceeding the configured daily budget."""
    with _lock:
        _refresh_daily_counter()

        if _state["api_requests"] >= TWELVE_DATA_DAILY_CREDIT_LIMIT:
            return False, (
                "Daily API safety budget reached. "
                "Try again after the daily counter resets."
            )

        now = time.time()
        elapsed = now - float(_state.get("last_request_epoch", 0.0))
        wait = MIN_REQUEST_INTERVAL_SECONDS - elapsed

        if wait > 0:
            return False, (
                f"API request cooldown active. Try again in "
                f"{wait:.1f} seconds."
            )

        _state["api_requests"] += 1
        _state["last_request_epoch"] = now
        _save()

        return True, "Request reserved."


def get_api_usage():
    with _lock:
        _refresh_daily_counter()
        _save()

        used = int(_state["api_requests"])
        limit = TWELVE_DATA_DAILY_CREDIT_LIMIT

        return {
            "used": used,
            "limit": limit,
            "remaining": max(0, limit - used),
            "date": _state["date"],
        }


def ensure_user(user_id):
    key = str(user_id)

    with _lock:
        user = _state["users"].setdefault(key, {})
        user.setdefault("monitoring_enabled", True)
        _state["monitoring_enabled"].setdefault(key, True)
        _state["timeframes"].setdefault(key, "5m")
        _save()
        return dict(user)


def user_monitoring_enabled(user_id):
    with _lock:
        key = str(user_id)
        return bool(_state["monitoring_enabled"].get(key, True))


def set_user_monitoring(user_id, enabled):
    with _lock:
        key = str(user_id)
        _state["monitoring_enabled"][key] = bool(enabled)
        _state["users"].setdefault(key, {})[
            "monitoring_enabled"
        ] = bool(enabled)
        _save()


def get_timeframe(user_id):
    with _lock:
        return _state["timeframes"].get(str(user_id), "5m")


def set_timeframe(user_id, timeframe):
    with _lock:
        _state["timeframes"][str(user_id)] = str(timeframe)
        _save()


def get_users():
    with _lock:
        return list(_state["users"].keys())


def get_last_alert(key):
    with _lock:
        return _state["last_alerts"].get(str(key))


def set_last_alert(key, value):
    with _lock:
        _state["last_alerts"][str(key)] = value
        _save()


def add_trade(user_id, trade):
    with _lock:
        trade_id = str(_state["next_trade_id"])
        _state["next_trade_id"] += 1

        record = dict(trade)
        record["id"] = trade_id
        record["user_id"] = str(user_id)
        record["active"] = True
        record["created_at"] = datetime.now(
            timezone.utc
        ).isoformat()

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

        if user_id is not None and trade.get("user_id") != str(user_id):
            return None

        return dict(trade)


def close_trade(trade_id, user_id=None):
    with _lock:
        trade = _state["active_trades"].get(str(trade_id))

        if not trade or not trade.get("active", True):
            return False

        if user_id is not None and trade.get("user_id") != str(user_id):
            return False

        trade["active"] = False
        trade["closed_at"] = datetime.now(
            timezone.utc
        ).isoformat()

        _save()
        return True
