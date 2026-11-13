

import json
import os
import threading
import time
from datetime import datetime, timezone

from config import (
    STATE_FILE,
    TWELVE_DATA_DAILY_CREDIT_LIMIT,
    MIN_REQUEST_INTERVAL_SECONDS,
    API_RESERVE_REQUESTS,
    GOLD_BUDGET_SHARE,
    MARKETS,
)

_lock = threading.RLock()


def _now():
    return datetime.now(timezone.utc).isoformat()


def _today():
    # Twelve Data Basic's 800-credit daily quota resets at 00:00 UTC.
    return datetime.now(timezone.utc).date().isoformat()


def _empty_state():
    return {
        "date": _today(),
        "api_requests": 0,
        "api_gold_monitor_requests": 0,
        "api_other_background_requests": 0,
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
        "opportunities": {},
        "next_opportunity_id": 1,
        "account_profiles": {},
    }


def _api_counter_needs_reset(data):
    today = _today()
    try:
        last_epoch = float(data.get("last_request_epoch", 0.0))
    except (TypeError, ValueError):
        last_epoch = 0.0

    if last_epoch > 0:
        last_request_day = datetime.fromtimestamp(
            last_epoch, timezone.utc
        ).date().isoformat()
        return last_request_day != today

    return data.get("date") != today


def _reset_api_counters(data):
    data["date"] = _today()
    data["api_requests"] = 0
    data["api_gold_monitor_requests"] = 0
    data["api_other_background_requests"] = 0
    data["last_request_epoch"] = 0.0


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

    if _api_counter_needs_reset(data):
        _reset_api_counters(data)
    else:
        # Migrate the old Nigeria-local date field to the provider's UTC day
        # without discarding requests already made during this UTC day.
        data["date"] = _today()

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
    if _api_counter_needs_reset(_state):
        _reset_api_counters(_state)
    else:
        _state["date"] = _today()


# ---------------------------------------------------------
# API BUDGET
# ---------------------------------------------------------

def reserve_api_request(purpose='manual'):
    """
    Reserve one request against the local daily safety limit.

    Important: this counts requests, not guaranteed provider credits.
    The final API controller must account for endpoint-specific costs.
    """

    with _lock:
        _refresh_daily_counter()

        limit = int(TWELVE_DATA_DAILY_CREDIT_LIMIT)
        used = int(_state.get("api_requests", 0))

        purpose = str(purpose).strip().lower()

        # Split the 700-request background pool into a protected 50/50
        # allocation: only the dedicated Gold multi-timeframe monitor uses
        # the Gold pool; scanners, event checks and tracked-signal monitoring
        # use the other pool. The final 100 slots remain for manual requests.
        background_purposes = {"auto", "monitor", "gold_monitor"}
        background_limit = max(0, limit - int(API_RESERVE_REQUESTS))
        if purpose in background_purposes:
            if used >= background_limit:
                return False, (
                    "Background market monitoring paused to protect the "
                    "reserved daily API allowance for manual analysis."
                )

            gold_limit = int(background_limit * float(GOLD_BUDGET_SHARE))
            other_limit = background_limit - gold_limit
            if purpose == "gold_monitor":
                gold_used = int(_state.get("api_gold_monitor_requests", 0))
                if gold_used >= gold_limit:
                    return False, (
                        "Gold monitoring reached its protected daily API "
                        "allocation. It will resume after the UTC reset."
                    )
            else:
                other_used = int(_state.get("api_other_background_requests", 0))
                if other_used >= other_limit:
                    return False, (
                        "Other background monitoring reached its protected "
                        "daily API allocation. Manual requests remain reserved."
                    )

        if used >= limit:
            return False, (
                "Daily API safety limit reached. "
                "The counter resets at midnight UTC."
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
        if purpose == "gold_monitor":
            _state["api_gold_monitor_requests"] = int(
                _state.get("api_gold_monitor_requests", 0)
            ) + 1
        elif purpose in background_purposes:
            _state["api_other_background_requests"] = int(
                _state.get("api_other_background_requests", 0)
            ) + 1
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
            "gold_monitor_used": int(_state.get("api_gold_monitor_requests", 0)),
            "other_background_used": int(_state.get("api_other_background_requests", 0)),
            "background_limit": max(0, limit - int(API_RESERVE_REQUESTS)),
            "gold_monitor_limit": int(
                max(0, limit - int(API_RESERVE_REQUESTS)) * float(GOLD_BUDGET_SHARE)
            ),
            "other_background_limit": (
                max(0, limit - int(API_RESERVE_REQUESTS))
                - int(max(0, limit - int(API_RESERVE_REQUESTS)) * float(GOLD_BUDGET_SHARE))
            ),
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
        user.setdefault("markets", list(MARKETS))
        # Remove retired markets from old saved preferences without touching
        # the user's other profile, risk, or performance data.
        if isinstance(user.get("markets"), list):
            user["markets"] = [
                symbol for symbol in user["markets"] if symbol in MARKETS
            ]
        user.setdefault("notifications", {
            "new_signals": True,
            "tp1": True,
            "tp2": True,
            "tp3": True,
            "stop_loss": True,
            "weakening": True,
            "gold_updates": True,
            "system": True,
        })
        user.setdefault("tracking_notification_mode", "silent")
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
        "tracking_notification_mode",
        "risk_percent",
        "risk_amount",
        "timezone",
    }

    if setting not in allowed:
        raise ValueError("That setting cannot be changed.")

    with _lock:
        key = str(user_id)
        user = _state["users"].setdefault(key, {})
        if setting == "markets":
            value = [
                symbol for symbol in (value or [])
                if symbol in MARKETS
            ]
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
        "last_price_timestamp",
        "result_r",
        "realised_r",
        "trend_warning_key",
        "chart_chat_id",
        "chart_message_id",
        "chart_last_update",
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
    """Stop monitoring before any take-profit milestone; never label it a win."""
    with _lock:
        signal = _state["signals"].get(str(signal_id))
        if not signal or signal.get("user_id") != str(user_id):
            return False
        if signal.get("status") != "TRACKING":
            return False
        if signal.get("tp1_hit") or signal.get("tp2_hit"):
            return False
        signal["status"] = "MANUALLY_STOPPED"
        signal["completed_at"] = _now()
        signal["updated_at"] = signal["completed_at"]
        _add_signal_event_locked(
            signal_id,
            "MANUALLY_STOPPED",
            "User stopped tracking before TP1. Excluded from win/loss statistics.",
        )
        _save()
        return True


def close_signal_after_target(signal_id, user_id):
    """Close a tracked signal as a milestone-qualified manual win after TP1/TP2."""
    with _lock:
        signal = _state["signals"].get(str(signal_id))
        if not signal or signal.get("user_id") != str(user_id):
            return None
        if signal.get("status") != "TRACKING":
            return None

        reached = 2 if signal.get("tp2_hit") else (1 if signal.get("tp1_hit") else 0)
        if reached == 0:
            return None

        entry = float(signal.get("entry") or 0)
        stop = float(signal.get("stop_loss") or 0)
        exit_price = signal.get("current_price")
        try:
            exit_price = float(exit_price)
        except (TypeError, ValueError):
            exit_price = None

        risk = abs(entry - stop)
        direction = str(signal.get("direction", "")).upper()
        if exit_price is not None and risk > 0 and direction in {"BUY", "SELL"}:
            signed_move = exit_price - entry if direction == "BUY" else entry - exit_price
            exit_r = signed_move / risk
            flags = [bool(signal.get(f"tp{i}_hit")) for i in (1, 2, 3)]
            booked_r = sum((i + 1) / 3 for i, hit in enumerate(flags) if hit)
            remaining = max(0.0, 1.0 - sum(1 / 3 for hit in flags if hit))
            realised_r = booked_r + remaining * exit_r
        else:
            # Never invent an exit price. The achieved milestone is still retained.
            exit_price = None
            realised_r = float(signal.get("realised_r", 0) or 0)

        now = _now()
        signal.update({
            "status": "MANUAL_WIN",
            "completed_at": now,
            "updated_at": now,
            "manual_close": True,
            "manual_close_after_tp": reached,
            "manual_exit_price": exit_price,
            "realised_r": realised_r,
            "result_r": realised_r,
        })
        _add_signal_event_locked(
            signal_id,
            "MANUAL_WIN",
            f"User manually closed after TP{reached}. Recorded as a target-achievement win; realised result {realised_r:+.2f}R.",
            {
                "closed_after_tp": reached,
                "exit_price": exit_price,
                "realised_r": realised_r,
                "exit_price_source": "last monitored closed-candle price" if exit_price is not None else "unavailable",
            },
        )
        _save()
        return dict(signal)


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
# AUTOMATIC OPPORTUNITY FEED (NOT USER TRACKING)
# ---------------------------------------------------------

def add_opportunity(key, opportunity):
    """Store a scanner opportunity separately from tracked signals."""
    with _lock:
        key = str(key)
        for existing in _state["opportunities"].values():
            if existing.get("key") == key:
                return dict(existing)

        opportunity_id = str(_state["next_opportunity_id"])
        _state["next_opportunity_id"] += 1
        record = dict(opportunity)
        record.update({
            "id": opportunity_id,
            "key": key,
            "created_at": _now(),
        })
        _state["opportunities"][opportunity_id] = record

        # Keep a bounded feed so the state file does not grow forever.
        if len(_state["opportunities"]) > 300:
            oldest = sorted(
                _state["opportunities"].items(),
                key=lambda item: item[1].get("created_at", ""),
            )
            for old_id, _ in oldest[:len(_state["opportunities"]) - 300]:
                _state["opportunities"].pop(old_id, None)

        _save()
        return dict(record)


def get_opportunities(limit=30):
    with _lock:
        records = [
            dict(item) for item in _state["opportunities"].values()
        ]
        records.sort(
            key=lambda item: item.get("created_at", ""),
            reverse=True,
        )
        return records[:max(1, int(limit))]


def get_opportunity(opportunity_id):
    with _lock:
        item = _state["opportunities"].get(str(opportunity_id))
        return dict(item) if item else None


def mark_opportunity_claimed(opportunity_id, user_id):
    """Hide a scanner opportunity from this user after they track it.

    Claims are per-user because the automatic opportunity feed is shared;
    one user's decision must not hide an opportunity from every other user.
    """
    with _lock:
        item = _state["opportunities"].get(str(opportunity_id))
        if not item:
            return False

        claimed_by = item.get("claimed_by", [])
        if not isinstance(claimed_by, list):
            claimed_by = []

        user_key = str(user_id)
        if user_key not in claimed_by:
            claimed_by.append(user_key)

        item["claimed_by"] = claimed_by
        item["updated_at"] = _now()
        _save()
        return True


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


# ---------------------------------------------------------
# UNIVERSAL ACCOUNT MANAGER
# ---------------------------------------------------------

def add_account_profile(user_id, profile):
    """Save an account profile owned by one Telegram user."""
    with _lock:
        record = dict(profile)
        record["user_id"] = str(user_id)
        record.setdefault("snapshots", [])
        _state["account_profiles"].setdefault(str(user_id), {})[str(record["id"])] = record
        _save()
        return dict(record)


def get_account_profiles(user_id):
    with _lock:
        records = _state["account_profiles"].get(str(user_id), {})
        return [dict(item) for item in records.values()]


def get_account_profile(user_id, account_id):
    with _lock:
        record = _state["account_profiles"].get(str(user_id), {}).get(str(account_id))
        return dict(record) if record else None


def update_account_snapshot(user_id, account_id, snapshot):
    with _lock:
        record = _state["account_profiles"].get(str(user_id), {}).get(str(account_id))
        if not record:
            return None
        saved = dict(snapshot)
        record["snapshot"] = saved
        record.setdefault("snapshots", []).append(saved)
        record["snapshots"] = record["snapshots"][-100:]
        _save()
        return dict(record)


def set_active_account(user_id, account_id):
    with _lock:
        records = _state["account_profiles"].get(str(user_id), {})
        if str(account_id) not in records:
            return False
        for key, record in records.items():
            record["active"] = key == str(account_id)
        _save()
        return True


def get_active_account(user_id):
    with _lock:
        records = _state["account_profiles"].get(str(user_id), {})
        for record in records.values():
            if record.get("active"):
                return dict(record)
        return None
