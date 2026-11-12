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


def normalise_account_type(value):
    """Accept clear user-facing account-type labels and return the stored enum."""
    text = str(value or "").strip().upper().replace("-", "_")
    aliases = {
        "PERSONAL": "PERSONAL",
        "PERSONAL ACCOUNT": "PERSONAL",
        "PROP_CHALLENGE": "PROP_CHALLENGE",
        "PROP CHALLENGE": "PROP_CHALLENGE",
        "PROP FIRM CHALLENGE": "PROP_CHALLENGE",
        "FUNDED_PROP": "FUNDED_PROP",
        "FUNDED PROP": "FUNDED_PROP",
        "FUNDED ACCOUNT": "FUNDED_PROP",
    }
    try:
        return aliases[text]
    except KeyError as exc:
        raise ValueError("Choose PERSONAL, PROP_CHALLENGE or FUNDED_PROP.") from exc


def account_registration_steps(account_type=None):
    """Return the correct wizard order; personal accounts skip firm name."""
    steps = ["name", "account_type"]
    if account_type != "PERSONAL":
        steps.append("firm_name")
    steps.extend([
        "starting_balance",
        "currency",
        "daily_loss_percent",
        "overall_loss_percent",
        "profit_target_percent",
        "drawdown_model",
    ])
    return steps


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
    overall_pct = rules.get("overall_loss_percent")
    daily_pct = rules.get("daily_loss_percent")
    overall_limit = start * float(overall_pct) / 100 if overall_pct is not None else None
    daily_limit = start * float(daily_pct) / 100 if daily_pct is not None else None
    model = rules.get("drawdown_model", "STATIC")

    # A static limit can be measured from the initial balance. A trailing
    # limit needs the firm's exact high-water-mark/reset rules and a recorded
    # equity history; never approximate it from starting balance alone.
    if model == "STATIC":
        overall_loss_used = max(0.0, start - equity)
        overall_loss_remaining = (
            max(0.0, overall_limit - overall_loss_used)
            if overall_limit is not None else None
        )
        overall_loss_floor = (
            start - overall_limit if overall_limit is not None else None
        )
    else:
        overall_loss_used = None
        overall_loss_remaining = None
        overall_loss_floor = None

    return {
        "balance": balance,
        "equity": equity,
        "floating_pnl": equity - balance,
        "drawdown_model": model,
        "overall_loss_used": overall_loss_used,
        "overall_loss_limit": overall_limit,
        "overall_loss_remaining": overall_loss_remaining,
        "overall_loss_floor": overall_loss_floor,
        "overall_loss_tracking_available": model == "STATIC",
        "daily_loss_limit": daily_limit,
        "daily_loss_tracking_available": False,
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


def calculate_account_trade_plan(profile, trade, risk_percent, value_per_price_unit_per_lot):
    """Combine a qualified strategy setup with account-based position sizing.

    The supplied unit value must be verified from the user's broker contract
    specifications. This function never infers pip/tick value.
    """
    direction = str(trade.get("status", "")).upper()
    if direction not in {"BUY", "SELL"}:
        raise ValueError("A qualified BUY or SELL setup is required.")
    entry = _number(trade.get("entry"), "Entry", 0.00000001)
    stop = _number(trade.get("stop_loss"), "Stop loss", 0.00000001)
    targets = trade.get("take_profits") or []
    if len(targets) != 3:
        raise ValueError("The strategy must provide all three take-profit levels.")
    targets = [_number(value, f"TP{index}", 0.00000001)
               for index, value in enumerate(targets, 1)]
    if (direction == "BUY" and not stop < entry) or (direction == "SELL" and not stop > entry):
        raise ValueError("Stop loss is on the wrong side of the entry.")
    if direction == "BUY" and not (entry < targets[0] < targets[1] < targets[2]):
        raise ValueError("BUY take-profit levels must rise above entry in TP1–TP3 order.")
    if direction == "SELL" and not (entry > targets[0] > targets[1] > targets[2]):
        raise ValueError("SELL take-profit levels must fall below entry in TP1–TP3 order.")
    risk_distance = abs(entry - stop)
    if risk_distance <= 0:
        raise ValueError("Entry-to-stop distance must be greater than zero.")

    snapshot = profile.get("snapshot") or {}
    equity = snapshot.get("equity")
    if equity is None:
        equity = profile.get("starting_balance")
        equity_source = "starting balance (no manual equity snapshot)"
    else:
        equity_source = "latest manually saved equity snapshot"
    sizing = calculate_position_size(
        equity, risk_percent, risk_distance, value_per_price_unit_per_lot
    )
    return {
        "symbol": trade.get("symbol"),
        "timeframe": trade.get("timeframe"),
        "direction": direction,
        "entry": entry,
        "stop_loss": stop,
        "take_profits": targets,
        "risk_distance": risk_distance,
        "equity": float(equity),
        "equity_source": equity_source,
        "currency": profile.get("currency", "USD"),
        "account_name": profile.get("name", "Active account"),
        **sizing,
    }


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



def render_account_action_report(title, subtitle="", metrics=None):
    """Render a compact branded visual for Account Manager actions."""
    from io import BytesIO
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    metrics = metrics or {}
    labels = list(metrics.keys())[:4]
    values = [float(metrics[key]) for key in labels]
    fig, ax = plt.subplots(figsize=(8, 4.5))
    fig.patch.set_facecolor("#080B0A")
    ax.set_facecolor("#101613")
    if labels:
        bars = ax.bar(labels, values, color=["#39FF88", "#D6AD45", "#FF5263", "#829188"][:len(labels)])
        ax.tick_params(axis="x", colors="#E8F0EA", labelrotation=12)
        ax.tick_params(axis="y", colors="#E8F0EA")
        for bar, value in zip(bars, values):
            ax.text(bar.get_x() + bar.get_width()/2, bar.get_height(), f"{value:,.2f}",
                    ha="center", va="bottom", color="#E8F0EA", fontsize=9)
        ax.grid(axis="y", color="#26332B", linestyle=":", alpha=.7)
    else:
        ax.set_axis_off()
        ax.text(.5, .55, "FOREX HUNTER AI", transform=ax.transAxes,
                ha="center", va="center", color="#39FF88", fontsize=20, weight="bold")
        ax.text(.5, .38, "ACCOUNT MANAGEMENT", transform=ax.transAxes,
                ha="center", va="center", color="#E8F0EA", fontsize=11)
    ax.set_title(str(title).upper(), color="#E8F0EA", weight="bold", pad=18)
    if subtitle:
        fig.text(.5, .04, str(subtitle)[:110], ha="center", color="#D6AD45", fontsize=9)
    for spine in ax.spines.values():
        spine.set_color("#344238")
    fig.tight_layout(rect=(0, .08, 1, 1))
    output = BytesIO()
    fig.savefig(output, format="png", dpi=140, facecolor=fig.get_facecolor())
    plt.close(fig)
    output.seek(0)
    output.name = "account_action_report.png"
    return output
