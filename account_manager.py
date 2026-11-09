"""Universal, manual-first trading account manager.

This module stores configurable account rules and calculates risk plans.
It never connects to a broker or places orders.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4


ACCOUNT_TYPES = {"PROP_CHALLENGE", "FUNDED_PROP", "PERSONAL"}
DRAWDOWN_MODELS = {"STATIC", "TRAILING"}
CURRENCIES = {"USD", "EUR", "GBP", "NGN"}


def _number(value, label, minimum=0.0, maximum=1_000_000_000.0):
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{label} must be a number.")
    if result < minimum or result > maximum:
        raise ValueError(f"{label} must be between {minimum:g} and {maximum:g}.")
    return result


def validate_profile(profile):
    name = str(profile.get("name", "")).strip()
    if not name or len(name) > 60:
        raise ValueError("Account name must contain 1–60 characters.")
    account_type = str(profile.get("account_type", "")).upper()
    if account_type not in ACCOUNT_TYPES:
        raise ValueError("Unsupported account type.")
    balance = _number(profile.get("starting_balance"), "Starting balance", 1, 1_000_000_000)
    currency = str(profile.get("currency", "USD")).upper()
    if currency not in CURRENCIES:
        raise ValueError("Supported currencies: USD, EUR, GBP and NGN.")
    rules = dict(profile.get("rules") or {})
    for key, label in (
        ("daily_loss_percent", "Daily loss percentage"),
        ("overall_loss_percent", "Overall loss percentage"),
        ("profit_target_percent", "Profit target percentage"),
    ):
        value = rules.get(key)
        if value not in (None, ""):
            rules[key] = _number(value, label, 0, 100)
        else:
            rules[key] = None
    model = str(rules.get("drawdown_model", "STATIC")).upper()
    if model not in DRAWDOWN_MODELS:
        raise ValueError("Drawdown model must be STATIC or TRAILING.")
    rules["drawdown_model"] = model
    rules["verified"] = bool(rules.get("verified", False))
    return {
        "name": name,
        "account_type": account_type,
        "firm_name": str(profile.get("firm_name", "")).strip()[:80],
        "starting_balance": balance,
        "currency": currency,
        "rules": rules,
    }


def create_profile(user_id, profile):
    clean = validate_profile(profile)
    record = {
        "id": uuid4().hex[:10],
        "user_id": str(user_id),
        **clean,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "snapshot": None,
        "snapshots": [],
        "active": True,
    }
    return record


def calculate_account_metrics(profile, balance, equity):
    balance = _number(balance, "Balance")
    equity = _number(equity, "Equity")
    start = float(profile["starting_balance"])
    rules = profile.get("rules") or {}
    current_loss = max(0.0, start - equity)
    overall_pct = rules.get("overall_loss_percent")
    daily_pct = rules.get("daily_loss_percent")
    overall_limit = start * float(overall_pct) / 100 if overall_pct is not None else None
    daily_limit = start * float(daily_pct) / 100 if daily_pct is not None else None
    return {
        "balance": balance,
        "equity": equity,
        "floating_pnl": equity - balance,
        "overall_loss_used": current_loss,
        "overall_loss_limit": overall_limit,
        "overall_loss_remaining": max(0.0, overall_limit - current_loss) if overall_limit is not None else None,
        "overall_loss_floor": start - overall_limit if overall_limit is not None and rules.get("drawdown_model") == "STATIC" else None,
        "daily_loss_limit": daily_limit,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def calculate_position_size(account_equity, risk_percent, stop_distance, value_per_price_unit_per_lot, max_lot=None):
    """Generic sizing requires broker/instrument value metadata; never guesses pip value."""
    equity = _number(account_equity, "Equity", 0.01)
    risk_pct = _number(risk_percent, "Risk percentage", 0.01, 5.0)
    distance = _number(stop_distance, "Stop distance", 0.00000001)
    unit_value = _number(value_per_price_unit_per_lot, "Value per price unit per lot", 0.00000001)
    risk_amount = equity * risk_pct / 100
    lots = risk_amount / (distance * unit_value)
    if max_lot is not None:
        lots = min(lots, _number(max_lot, "Maximum lot", 0.00000001))
    return {"risk_amount": risk_amount, "risk_percent": risk_pct, "stop_distance": distance, "lots": lots}


def save_profile(user_id, profile):
    return state_add_account_profile(user_id, profile)


def state_add_account_profile(user_id, profile):
    import state
    return state.add_account_profile(user_id, profile)


def render_account_report(profile):
    """Create a compact PNG dashboard from the latest recorded snapshot."""
    from io import BytesIO
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    snapshot = profile.get("snapshot") or {}
    balance = float(snapshot.get("balance", profile["starting_balance"]))
    equity = float(snapshot.get("equity", balance))
    currency = profile.get("currency", "USD")
    fig, ax = plt.subplots(figsize=(9, 4.8))
    fig.patch.set_facecolor("#080B0A")
    ax.set_facecolor("#101613")
    ax.bar(["Balance", "Equity"], [balance, equity], color=["#D6AD45", "#39FF88"])
    ax.set_title(f"{profile['name']}  |  ACCOUNT SNAPSHOT", color="#E8F0EA", weight="bold")
    ax.set_ylabel(currency, color="#E8F0EA")
    ax.tick_params(colors="#E8F0EA")
    for spine in ax.spines.values():
        spine.set_color("#344238")
    ax.grid(axis="y", color="#26332B", linestyle=":", alpha=.7)
    fig.tight_layout()
    output = BytesIO()
    fig.savefig(output, format="png", dpi=140, facecolor=fig.get_facecolor())
    plt.close(fig)
    output.seek(0)
    output.name = "account_dashboard.png"
    return output
