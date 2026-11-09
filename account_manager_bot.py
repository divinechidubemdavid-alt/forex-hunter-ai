"""Telegram UI for universal manual-first account management."""
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import state
from account_manager import (
    create_profile,
    calculate_account_metrics,
    calculate_position_size,
    render_account_report,
)

MENU_LABEL = "🛡 Account Manager"


def _home_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Register account", callback_data="account:register")],
        [InlineKeyboardButton("📂 My accounts", callback_data="account:list")],
        [InlineKeyboardButton("📊 Account dashboard", callback_data="account:dashboard")],
        [InlineKeyboardButton("🔄 Update balance & equity", callback_data="account:update")],
        [InlineKeyboardButton("🧮 Position risk calculator", callback_data="account:risk")],
    ])


async def show_account_manager(update, context):
    profiles = state.get_account_profiles(update.effective_user.id)
    active = next((p for p in profiles if p.get("active")), None)
    summary = "No account registered yet."
    if active:
        snapshot = active.get("snapshot") or {}
        summary = (
            f"Active account: {active['name']}\n"
            f"Type: {active['account_type']}\n"
            f"Balance: {snapshot.get('balance', 'Not entered')}\n"
            f"Equity: {snapshot.get('equity', 'Not entered')}\n"
            f"Rules verified: {'Yes' if active.get('rules', {}).get('verified') else 'No — review required'}"
        )
    await update.effective_message.reply_text(
        "🛡 UNIVERSAL ACCOUNT MANAGER\n\n"
        "Manage prop-firm challenges, funded accounts and personal trading accounts.\n\n"
        + summary
        + "\n\nThis version uses manual snapshots. Automatic MT5 connection is a later, separately tested stage.",
        reply_markup=_home_keyboard(),
    )


async def handle_account_callback(update, context):
    query = update.callback_query
    data = str(query.data or "")
    if not data.startswith("account:"):
        return False
    await query.answer()
    user_id = update.effective_user.id
    action = data.split(":", 1)[1]

    if action == "register":
        context.user_data["account_wizard"] = {"step": "name"}
        await query.message.reply_text("Let's register an account.\n\n1/9 — Send an account nickname, for example: My $5K Challenge")
    elif action == "list":
        profiles = state.get_account_profiles(user_id)
        if not profiles:
            await query.message.reply_text("No accounts registered yet. Choose Register account.", reply_markup=_home_keyboard())
        else:
            rows = [[InlineKeyboardButton(
                ("✓ " if item.get("active") else "") + item["name"],
                callback_data=f"account:select:{item['id']}",
            )] for item in profiles]
            await query.message.reply_text("Your saved accounts. Select one to make it active:", reply_markup=InlineKeyboardMarkup(rows))
    elif action.startswith("select:"):
        account_id = action.split(":", 1)[1]
        if state.set_active_account(user_id, account_id):
            await query.message.reply_text("Active account changed.", reply_markup=_home_keyboard())
        else:
            await query.message.reply_text("That account could not be found.")
    elif action == "update":
        profiles = state.get_account_profiles(user_id)
        active = next((p for p in profiles if p.get("active")), None)
        if not active:
            await query.message.reply_text("Register an account first.")
        else:
            context.user_data["account_snapshot_id"] = active["id"]
            await query.message.reply_text("Send your latest figures as: balance | equity\nExample: 4980 | 4965\nUse the same account currency. These are manual figures, not a live MT5 feed.")
    elif action == "dashboard":
        profiles = state.get_account_profiles(user_id)
        active = next((p for p in profiles if p.get("active")), None)
        if not active:
            await query.message.reply_text("Register an account first.")
        else:
            snapshot = active.get("snapshot") or {}
            report = render_account_report(active)
            await query.message.reply_photo(
                photo=report,
                caption=(
                    f"📊 {active['name']} — ACCOUNT SNAPSHOT\n"
                    f"Balance: {snapshot.get('balance', active['starting_balance'])} {active['currency']}\n"
                    f"Equity: {snapshot.get('equity', 'Not updated')}\n"
                    f"Floating P/L: {snapshot.get('floating_pnl', 'Not available')}\n"
                    f"Last update: {snapshot.get('updated_at', 'No manual update yet')}\n"
                    "Visual report uses the latest saved figures; it is not live broker data."
                ),
            )
    elif action == "risk":
        context.user_data["account_risk_pending"] = True
        await query.message.reply_text(
            "Position risk calculator\n\n"
            "Send: equity | risk % | stop distance | value per price unit per 1.0 lot\n"
            "Example: 5000 | 0.25 | 2.5 | 10\n\n"
            "The last value must come from your broker's contract/tick specifications. Do not guess it. This calculator does not place trades."
        )
    return True


async def handle_account_text(update, context):
    text = (update.message.text or "").strip()
    user_id = update.effective_user.id

    wizard = context.user_data.get("account_wizard")
    if wizard:
        step = wizard["step"]
        prompts = {
            "name": ("name", "2/9 — Which account type? Send PROP_CHALLENGE, FUNDED_PROP, or PERSONAL."),
            "account_type": ("account_type", "3/9 — Enter the prop-firm name, or send - for a personal account."),
            "firm_name": ("firm_name", "4/9 — Enter starting account balance, for example 5000."),
            "starting_balance": ("starting_balance", "5/9 — Enter account currency: USD, EUR, GBP or NGN."),
            "currency": ("currency", "6/9 — Daily loss limit as a percentage, or 0 if not applicable. Do not guess; use 0 if unknown."),
            "daily_loss_percent": ("daily_loss_percent", "7/9 — Overall maximum loss percentage, or 0 if unknown."),
            "overall_loss_percent": ("overall_loss_percent", "8/9 — Profit target percentage, or 0 if not applicable/unknown."),
            "profit_target_percent": ("profit_target_percent", "9/9 — Drawdown model: STATIC or TRAILING. If unknown, choose STATIC only as a placeholder and verify before trading."),
        }
        try:
            key, prompt = prompts[step]
            if step == "account_type":
                value = text.upper()
                if value not in {"PROP_CHALLENGE", "FUNDED_PROP", "PERSONAL"}:
                    raise ValueError("Send PROP_CHALLENGE, FUNDED_PROP, or PERSONAL.")
            elif step == "starting_balance":
                value = float(text.replace(",", ""))
                if value <= 0:
                    raise ValueError("Starting balance must be greater than zero.")
            elif step in {"daily_loss_percent", "overall_loss_percent", "profit_target_percent"}:
                value = float(text.replace("%", ""))
                if not 0 <= value <= 100:
                    raise ValueError("Enter a percentage from 0 to 100, or 0 if unknown.")
            elif step == "currency":
                value = text.upper()
                if value not in {"USD", "EUR", "GBP", "NGN"}:
                    raise ValueError("Use USD, EUR, GBP or NGN.")
            elif step == "firm_name" and text == "-":
                value = ""
            elif step == "drawdown_model":
                value = text.upper()
                if value not in {"STATIC", "TRAILING"}:
                    raise ValueError("Send STATIC or TRAILING.")
            else:
                value = text
            wizard[key] = value
            sequence = ["name", "account_type", "firm_name", "starting_balance", "currency", "daily_loss_percent", "overall_loss_percent", "profit_target_percent", "drawdown_model"]
            next_index = sequence.index(step) + 1
            if next_index < len(sequence):
                wizard["step"] = sequence[next_index]
                if sequence[next_index] == "drawdown_model":
                    await update.message.reply_text(prompt)
                else:
                    await update.message.reply_text(prompts[sequence[next_index]][1])
                return True
            profile = create_profile(user_id, {
                "name": wizard["name"],
                "account_type": wizard["account_type"],
                "firm_name": wizard.get("firm_name", ""),
                "starting_balance": wizard["starting_balance"],
                "currency": wizard["currency"],
                "rules": {
                    "daily_loss_percent": wizard["daily_loss_percent"] or None,
                    "overall_loss_percent": wizard["overall_loss_percent"] or None,
                    "profit_target_percent": wizard["profit_target_percent"] or None,
                    "drawdown_model": wizard["drawdown_model"],
                    "verified": False,
                },
            })
            state.add_account_profile(user_id, profile)
            context.user_data.pop("account_wizard", None)
            await update.message.reply_text(
                f"✅ Account registered: {profile['name']}\n"
                f"Type: {profile['account_type']}\n"
                f"Starting balance: {profile['starting_balance']} {profile['currency']}\n"
                "Rules are marked UNVERIFIED. Check your firm's official dashboard/rulebook before relying on them.",
                reply_markup=_home_keyboard(),
            )
            return True
        except (ValueError, KeyError) as exc:
            await update.message.reply_text(f"{exc}\nPlease try again.")
            return True

    if "account_snapshot_id" in context.user_data:
        try:
            parts = [part.strip().replace(",", "") for part in text.split("|")]
            if len(parts) != 2:
                raise ValueError("Send exactly: balance | equity")
            balance, equity = map(float, parts)
            if balance < 0 or equity < 0:
                raise ValueError("Balance and equity cannot be negative.")
            account_id = context.user_data["account_snapshot_id"]
            profile = state.get_account_profile(user_id, account_id)
            if not profile:
                raise ValueError("The selected account no longer exists.")
            metrics = calculate_account_metrics(profile, balance, equity)
            saved = state.update_account_snapshot(user_id, account_id, metrics)
            context.user_data.pop("account_snapshot_id", None)
            await update.message.reply_text(
                f"✅ Manual account snapshot saved for {saved['name']}\n"
                f"Balance: {balance:.2f} {profile['currency']}\n"
                f"Equity: {equity:.2f} {profile['currency']}\n"
                f"Floating P/L: {metrics['floating_pnl']:+.2f} {profile['currency']}\n"
                "This is a manually entered snapshot, not a live MT5 connection.",
                reply_markup=_home_keyboard(),
            )
        except (ValueError, TypeError) as exc:
            await update.message.reply_text(f"{exc}\nPlease try again.")
        return True

    if context.user_data.get("account_risk_pending"):
        try:
            parts = [part.strip().replace(",", "") for part in text.split("|")]
            if len(parts) != 4:
                raise ValueError("Send four values separated by |.")
            equity, risk_pct, stop_distance, unit_value = map(float, parts)
            result = calculate_position_size(equity, risk_pct, stop_distance, unit_value)
            context.user_data.pop("account_risk_pending", None)
            await update.message.reply_text(
                "🧮 POSITION RISK ESTIMATE\n\n"
                f"Equity: {equity:.2f}\n"
                f"Risk: {risk_pct:g}% ({result['risk_amount']:.2f})\n"
                f"Stop distance: {stop_distance:g}\n"
                f"Value per price unit per lot: {unit_value:g}\n"
                f"Calculated volume: {result['lots']:.4f} lots\n\n"
                "Before using this estimate, verify tick value, contract size, lot step, minimum/maximum volume, commission and account currency with your broker. This is not an order instruction.",
                reply_markup=_home_keyboard(),
            )
        except (ValueError, TypeError) as exc:
            await update.message.reply_text(f"{exc}\nPlease try again.")
        return True

    return False
