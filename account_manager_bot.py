"""Telegram UI for universal manual-first account management."""
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from datetime import datetime
from zoneinfo import ZoneInfo

from config import MARKETS, TIMEFRAMES
from market_data import fetch_candles, MarketDataError
from strategy import analyze, format_signal
from chart import create_market_chart
from market_scope import is_market_allowed

import state
from account_manager import (
    create_profile,
    calculate_account_metrics,
    calculate_position_size,
    calculate_account_trade_plan,
    render_account_report,
    render_account_action_report,
    normalise_account_type,
    account_registration_steps,
)

MENU_LABEL = "🛡 Account Manager"


def _home_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Register account", callback_data="account:register")],
        [InlineKeyboardButton("📂 My accounts", callback_data="account:list")],
        [InlineKeyboardButton("📊 Account dashboard", callback_data="account:dashboard")],
        [InlineKeyboardButton("🔄 Update balance & equity", callback_data="account:update")],
        [InlineKeyboardButton("🧮 Position risk calculator", callback_data="account:risk")],
        [InlineKeyboardButton("📈 Live market chart", callback_data="account:chart")],
        [InlineKeyboardButton("🎯 Trade planner", callback_data="account:planner")],
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
    await update.effective_message.reply_photo(
        photo=render_account_action_report("Universal Account Manager", "Analyse • Track • Protect"),
        caption=(
            "🛡 UNIVERSAL ACCOUNT MANAGER\n\n"
            "Manage prop-firm challenges, funded accounts and personal trading accounts.\n\n"
            + summary
            + "\n\nManual snapshots, live market charts and a completed-candle trade planner are available. Automatic MT5 monitoring is a later stage."
        ),
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

    if action.startswith("drawdown:"):
        wizard = context.user_data.get("account_wizard")
        model = action.split(":", 1)[1].upper()
        if not wizard or wizard.get("step") != "drawdown_model" or model not in {"STATIC", "TRAILING"}:
            await query.message.reply_text("This registration choice has expired. Please start registration again.")
            return True
        wizard["drawdown_model"] = model
        await _save_registered_account(query.message, context, user_id, wizard)
    elif action == "register":
        context.user_data["account_wizard"] = {"step": "name"}
        await query.message.reply_photo(
            photo=render_account_action_report("Register Trading Account", "Set up a prop challenge, funded or personal account"),
            caption="Let's register an account.\n\nStep 1 — Send an account nickname, for example: My Personal Account."
        )
    elif action == "list":
        profiles = state.get_account_profiles(user_id)
        if not profiles:
            await query.message.reply_text("No accounts registered yet. Choose Register account.", reply_markup=_home_keyboard())
        else:
            rows = [[InlineKeyboardButton(
                ("✓ " if item.get("active") else "") + item["name"],
                callback_data=f"account:select:{item['id']}",
            )] for item in profiles]
            await query.message.reply_photo(
                photo=render_account_action_report("Your Accounts", f"{len(profiles)} account profile(s) saved"),
                caption="Your saved accounts. Select one to make it active:",
                reply_markup=InlineKeyboardMarkup(rows)
            )
    elif action.startswith("select:"):
        account_id = action.split(":", 1)[1]
        if state.set_active_account(user_id, account_id):
            await query.message.reply_photo(
                photo=render_account_action_report("Active Account Selected", "All account tools now use this profile"),
                caption="Active account changed.",
                reply_markup=_home_keyboard()
            )
        else:
            await query.message.reply_text("That account could not be found.")
    elif action == "update":
        profiles = state.get_account_profiles(user_id)
        active = next((p for p in profiles if p.get("active")), None)
        if not active:
            await query.message.reply_text("Register an account first.")
        else:
            context.user_data["account_snapshot_id"] = active["id"]
            await query.message.reply_photo(
                photo=render_account_action_report("Balance & Equity Update", f"{active['name']} • manual snapshot"),
                caption="Send your latest figures as: balance | equity\nExample: 4980 | 4965\nUse the same account currency. These are manual figures, not a live MT5 feed."
            )
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

    elif action == "chart":
        symbols = [s for s in MARKETS if is_market_allowed(s, datetime.now(ZoneInfo("Africa/Lagos")).weekday())]
        rows = [[InlineKeyboardButton(s, callback_data=f"account:chart-symbol:{s}") for s in symbols[i:i + 2]] for i in range(0, len(symbols), 2)]
        await query.message.reply_text("Choose a market for a fresh chart:", reply_markup=InlineKeyboardMarkup(rows))
    elif action.startswith("chart-symbol:"):
        symbol = action.split(":", 1)[1]
        if symbol not in MARKETS or not is_market_allowed(symbol, datetime.now(ZoneInfo("Africa/Lagos")).weekday()):
            await query.message.reply_text("That market is not available in the current session.")
        else:
            context.user_data["account_chart_symbol"] = symbol
            rows = [[InlineKeyboardButton(tf, callback_data=f"account:chart-tf:{tf}")] for tf in TIMEFRAMES]
            await query.message.reply_text(f"Choose timeframe for {symbol}:", reply_markup=InlineKeyboardMarkup(rows))
    elif action.startswith("chart-tf:"):
        timeframe = action.split(":", 1)[1]
        symbol = context.user_data.get("account_chart_symbol")
        if timeframe not in TIMEFRAMES or symbol not in MARKETS:
            await query.message.reply_text("Please select the market and timeframe again.")
        else:
            await query.message.reply_text("Fetching completed candles and preparing your chart…")
            try:
                candles = await fetch_candles(symbol, outputsize=500, interval=timeframe, purpose="manual")
                image = create_market_chart(candles, symbol, timeframe)
                await query.message.reply_photo(photo=image, caption=f"📈 {symbol} | {timeframe.upper()}\nLast completed candle: {candles.index[-1]}\nMarket structure annotations included. Verify broker prices before trading.")
            except (MarketDataError, ValueError) as exc:
                await query.message.reply_text(f"Chart unavailable: {exc}")
    elif action == "planner":
        active = state.get_active_account(user_id)
        if not active:
            await query.message.reply_photo(
                photo=render_account_action_report("Account Required", "Register an account before building an account-aware plan"),
                caption="Please register an account first, then select it as your active account.",
                reply_markup=_home_keyboard(),
            )
            return True
        symbols = [s for s in MARKETS if is_market_allowed(s, datetime.now(ZoneInfo("Africa/Lagos")).weekday())]
        rows = [[InlineKeyboardButton(s, callback_data=f"account:plan-symbol:{s}") for s in symbols[i:i + 2]] for i in range(0, len(symbols), 2)]
        await query.message.reply_text("TRADE PLANNER\n\nChoose a market. It will analyse completed candles and show a chart. WAIT is a valid result; the planner will not invent a trade.", reply_markup=InlineKeyboardMarkup(rows))
    elif action.startswith("plan-symbol:"):
        symbol = action.split(":", 1)[1]
        if symbol not in MARKETS or not is_market_allowed(symbol, datetime.now(ZoneInfo("Africa/Lagos")).weekday()):
            await query.message.reply_text("That market is not available in the current session.")
        else:
            context.user_data["account_plan_symbol"] = symbol
            rows = [[InlineKeyboardButton(tf, callback_data=f"account:plan-tf:{tf}")] for tf in TIMEFRAMES]
            await query.message.reply_text(f"Choose analysis timeframe for {symbol}:", reply_markup=InlineKeyboardMarkup(rows))
    elif action.startswith("plan-tf:"):
        timeframe = action.split(":", 1)[1]
        symbol = context.user_data.get("account_plan_symbol")
        if timeframe not in TIMEFRAMES or symbol not in MARKETS:
            await query.message.reply_text("Please select the market and timeframe again.")
        else:
            await query.message.reply_text("Analysing completed candles and building your trade-plan chart…")
            try:
                candles = await fetch_candles(symbol, outputsize=500, interval=timeframe, purpose="manual")
                result = analyze(candles, timeframe)
                result["symbol"] = symbol
                result["timeframe"] = timeframe
                direction = result.get("status", "WAIT")
                entry = result.get("entry") if direction in ("BUY", "SELL") else None
                stop = result.get("stop_loss") if entry is not None else None
                targets = result.get("take_profits") if entry is not None else []
                image = create_market_chart(candles, symbol, timeframe, entry=entry, stop_loss=stop, take_profits=targets)
                summary = format_signal(symbol, result)
                if entry is None or stop is None:
                    await query.message.reply_photo(
                        photo=image,
                        caption=f"ACCOUNT-AWARE TRADE PLAN\n\n{summary}\n\nNo qualified setup. No lot size is calculated and no order is placed.",
                        reply_markup=_home_keyboard(),
                    )
                    return True

                active = state.get_active_account(user_id)
                if not active:
                    await query.message.reply_photo(
                        photo=image,
                        caption="The market setup qualified, but no active account is selected. Select an account and run the planner again.",
                        reply_markup=_home_keyboard(),
                    )
                    return True
                snapshot = active.get("snapshot") or {}
                equity = snapshot.get("equity")
                equity_source = "manual equity snapshot" if equity is not None else "starting balance (no snapshot)"
                if snapshot.get("updated_at"):
                    try:
                        updated = datetime.fromisoformat(str(snapshot["updated_at"]).replace("Z", "+00:00"))
                        age_hours = (datetime.now(ZoneInfo("UTC")) - updated).total_seconds() / 3600
                        if age_hours > 24:
                            await query.message.reply_photo(
                                photo=image,
                                caption=f"Setup: {symbol} {direction}\nYour saved equity snapshot is {age_hours:.1f} hours old. Update balance and equity, then run the planner again. No lot size was guessed.",
                                reply_markup=_home_keyboard(),
                            )
                            return True
                    except (TypeError, ValueError):
                        await query.message.reply_text("Your saved equity timestamp is invalid. Update balance and equity before sizing.")
                        return True
                settings = state.get_user_settings(user_id)
                risk_pct = float(settings.get("risk_percent", 1.0) or 1.0)
                if risk_pct > 5.0:
                    risk_pct = 5.0
                    risk_note = "Your saved risk preference is above the planner's 5% safety ceiling; this plan is capped at 5%."
                else:
                    risk_note = f"Risk preference: {risk_pct:g}%."
                context.user_data["account_plan_pending"] = {
                    "trade": {"symbol": symbol, "timeframe": timeframe, **result},
                    "account_id": active["id"],
                    "risk_percent": risk_pct,
                    "equity": float(equity if equity is not None else active["starting_balance"]),
                    "equity_source": equity_source,
                    "risk_note": risk_note,
                    "awaiting_positions_count": True,
                }
                await query.message.reply_photo(
                    photo=image,
                    caption=(
                        f"ACCOUNT-AWARE TRADE PLAN\n\n{summary}\n\n"
                        f"Active account: {active['name']}\n"
                        f"Sizing equity: {float(equity if equity is not None else active['starting_balance']):,.2f} {active['currency']} ({equity_source})\n"
                        f"{risk_note}\n\n"
                        "How many separate positions do you want to open for this one signal? Send a whole number from 1 to 20. "
                        "The total risk budget will be shared across all positions; choosing more positions will not increase total risk.\n"
                        "After that, the planner will ask for the broker-verified monetary value needed for sizing. This planner does not place orders."
                    ),
                )
            except (MarketDataError, ValueError, KeyError) as exc:
                await query.message.reply_text(f"Trade plan unavailable: {exc}")
    elif action == "risk":
        context.user_data["account_risk_pending"] = True
        await query.message.reply_photo(
            photo=render_account_action_report("Position Risk Calculator", "Risk amount and lot estimate require verified broker contract data"),
            caption=(
                "Position risk calculator\n\n"
                "Send: equity | risk % | stop distance | value per price unit per 1.0 lot\n"
                "Example: 5000 | 0.25 | 2.5 | 10\n\n"
                "The last value must come from your broker's contract/tick specifications. Do not guess it. This calculator does not place trades."
            )
        )
    return True


async def _save_registered_account(message, context, user_id, wizard):
    profile = create_profile(user_id, {
        "name": wizard["name"],
        "account_type": wizard["account_type"],
        "firm_name": wizard.get("firm_name", ""),
        "starting_balance": wizard["starting_balance"],
        "currency": wizard["currency"],
        "rules": {
            "daily_loss_percent": wizard["daily_loss_percent"] or None,
            "overall_loss_percent": wizard.get("overall_loss_percent") or None,
            "profit_target_percent": wizard.get("profit_target_percent") or None,
            "drawdown_model": wizard.get("drawdown_model"),
            "verified": False,
        },
    })
    state.add_account_profile(user_id, profile)
    context.user_data.pop("account_wizard", None)
    await message.reply_photo(
        photo=render_account_action_report(
            "Account Registered",
            "Rules remain unverified until checked against the firm's current rulebook",
            {"Starting balance": profile["starting_balance"]}
        ),
        caption=(
            f"✅ Account registered: {profile['name']}\\n"
            f"Type: {profile['account_type']}\\n"
            f"Starting balance: {profile['starting_balance']} {profile['currency']}\\n"
            "Rules are marked UNVERIFIED. Check your firm's official dashboard/rulebook before relying on them."
        ),
        reply_markup=_home_keyboard(),
    )


async def handle_account_text(update, context):
    text = (update.message.text or "").strip()
    user_id = update.effective_user.id

    wizard = context.user_data.get("account_wizard")
    if wizard:
        if text.lower() == "cancel":
            context.user_data.pop("account_wizard", None)
            await update.message.reply_text(
                "Account registration cancelled. You can start again from Register account."
            )
            return True
        step = wizard["step"]
        prompts = {
            "name": "What name do you want to give this account? Example: My Personal Account.",
            "account_type": "What type of account is this? Send PERSONAL, PROP_CHALLENGE, or FUNDED_PROP (instant funding).",
            "firm_name": "What is the name of the prop firm? Example: FTMO.",
            "starting_balance": "What is the account's starting balance? Send the amount as a number. Example: 5000.",
            "currency": "What currency does the account use? Send USD, EUR, GBP, or NGN.",
            "daily_loss_percent": "What is the daily loss limit? Enter the percent you are allowed to lose in one day. Example: 5. Enter 0 if there is no daily limit.",
            "overall_loss_percent": "What is the maximum total loss allowed? Enter the percent. Example: 10.",
            "profit_target_percent": "What profit target must you reach? Enter the percent. Example: 8.",
            "drawdown_model": "Choose the drawdown rule shown in your prop firm's official rules. Static: the loss limit stays fixed. Trailing: the loss limit can move up as your account reaches new highs. Firms calculate this differently, so check your rulebook if you are unsure.",
        }
        try:
            if step not in prompts:
                raise ValueError("Registration step is invalid. Please cancel and start again.")
            if step == "name":
                value = text.strip()
            elif step == "account_type":
                value = normalise_account_type(text)
            elif step == "starting_balance":
                value = float(text.replace(",", "").strip())
                if value <= 0:
                    raise ValueError("Starting balance must be greater than zero.")
            elif step in {"daily_loss_percent", "overall_loss_percent", "profit_target_percent"}:
                value = float(text.replace("%", "").strip())
                if not 0 <= value <= 100:
                    raise ValueError("Enter a percentage from 0 to 100, or 0 if unknown.")
            elif step == "currency":
                value = text.upper().strip()
                if value not in {"USD", "EUR", "GBP", "NGN"}:
                    raise ValueError("Use USD, EUR, GBP or NGN.")
            elif step == "firm_name" and text.strip() == "-":
                value = ""
            elif step == "drawdown_model":
                value = text.upper().strip()
                if value not in {"STATIC", "TRAILING"}:
                    raise ValueError("Send STATIC or TRAILING.")
            else:
                value = text.strip()
            wizard[step] = value
            sequence = account_registration_steps(wizard.get("account_type"))
            next_index = sequence.index(step) + 1
            if next_index < len(sequence):
                next_step = sequence[next_index]
                wizard["step"] = next_step
                if next_step == "drawdown_model":
                    keyboard = InlineKeyboardMarkup([
                        [InlineKeyboardButton("Static — fixed loss limit", callback_data="account:drawdown:STATIC")],
                        [InlineKeyboardButton("Trailing — limit can move up", callback_data="account:drawdown:TRAILING")],
                    ])
                    await update.message.reply_text(prompts[next_step], reply_markup=keyboard)
                else:
                    await update.message.reply_text(prompts[next_step])
                return True
            await _save_registered_account(update.message, context, user_id, wizard)
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
            await update.message.reply_photo(
                photo=render_account_action_report(
                    "Balance & Equity Snapshot",
                    "Manually entered figures • not a live MT5 feed",
                    {"Balance": balance, "Equity": equity, "Floating P/L": metrics["floating_pnl"]}
                ),
                caption=(
                    f"✅ Manual account snapshot saved for {saved['name']}\n"
                    f"Balance: {balance:.2f} {profile['currency']}\n"
                    f"Equity: {equity:.2f} {profile['currency']}\n"
                    f"Floating P/L: {metrics['floating_pnl']:+.2f} {profile['currency']}\n"
                    "This is a manually entered snapshot, not a live MT5 connection."
                ),
                reply_markup=_home_keyboard(),
            )
        except (ValueError, TypeError) as exc:
            await update.message.reply_text(f"{exc}\nPlease try again.")
        return True

    if context.user_data.get("account_plan_pending"):
        if text.lower() == "cancel":
            context.user_data.pop("account_plan_pending", None)
            await update.message.reply_text("Account-aware trade plan cancelled.", reply_markup=_home_keyboard())
            return True
        try:
            pending = context.user_data["account_plan_pending"]
            if pending.get("awaiting_positions_count"):
                if not text.isdigit():
                    raise ValueError("Enter a whole number from 1 to 20.")
                positions_count = int(text)
                if not 1 <= positions_count <= 20:
                    raise ValueError("Choose between 1 and 20 positions.")
                pending["positions_count"] = positions_count
                pending["awaiting_positions_count"] = False
                await update.message.reply_text(
                    f"Number of positions: {positions_count}. Total risk remains {pending['risk_percent']:g}% across all positions.\n\n"
                    "Now send the broker-verified monetary value of a 1.0-lot position for a 1.0 price-unit move, in this account currency. "
                    "Use the value for this exact symbol and account currency. Do not guess it, or send cancel."
                )
                return True
            unit_value = float(text.replace(",", "").strip())
            profile = state.get_account_profile(user_id, pending["account_id"])
            if not profile or not profile.get("active"):
                raise ValueError("Your active account changed or is unavailable. Start the planner again.")
            trade = pending["trade"]
            # Use the active snapshot captured when this plan was generated.
            profile_for_plan = dict(profile)
            profile_for_plan["snapshot"] = dict(profile.get("snapshot") or {})
            if pending["equity_source"].startswith("starting balance"):
                profile_for_plan["snapshot"].pop("equity", None)
            else:
                profile_for_plan["snapshot"]["equity"] = pending["equity"]
            plan = calculate_account_trade_plan(
                profile_for_plan, trade, pending["risk_percent"], unit_value,
                positions_count=pending.get("positions_count", 1),
            )
            context.user_data.pop("account_plan_pending", None)
            report = render_account_action_report(
                "Account-Aware Position Plan",
                f"{plan['account_name']} • theoretical sizing only",
                {"Risk amount": plan["risk_amount"], "Lot estimate": plan["lots"]},
            )
            await update.message.reply_photo(
                photo=report,
                caption=(
                    "🛡 ACCOUNT-AWARE TRADE PLAN\n\n"
                    f"Account: {plan['account_name']}\n"
                    f"Market: {plan['symbol']} | {plan['timeframe']}\n"
                    f"Direction: {plan['direction']}\n"
                    f"Equity basis: {plan['equity']:,.2f} {plan['currency']} ({plan['equity_source']})\n"
                    f"Risk: {plan['risk_percent']:g}% = {plan['risk_amount']:,.2f} {plan['currency']}\n"
                    f"Entry: {plan['entry']}\nStop Loss: {plan['stop_loss']}\n"
                    f"TP1: {plan['take_profits'][0]}\nTP2: {plan['take_profits'][1]}\nTP3: {plan['take_profits'][2]}\n"
                    f"Stop distance: {plan['risk_distance']}\n"
                    f"Verified unit value entered: {unit_value:g} {plan['currency']} per price unit per 1.0 lot\n"
                    f"Number of positions: {plan['positions_count']}\n"
                    f"Total theoretical volume: {plan['lots']:.4f} lots\n"
                    f"Theoretical volume per position: {plan['per_position_lots']:.4f} lots\n"
                    f"Risk per position (before rounding/costs): {plan['per_position_risk_amount']:,.2f} {plan['currency']}\n\n"
                    "All positions use the same signal entry, technical stop loss and original TP1–TP3 plan. "
                    "Total risk is shared, not multiplied. Before trading, verify tick size/value, contract size, account-currency conversion, lot step, minimum/maximum volume and commission. "
                    "Round each volume DOWN to your broker's permitted lot step and recheck aggregate risk. Calculation only: no MT5 connection or order is placed."
                ),
                reply_markup=_home_keyboard(),
            )
        except (ValueError, TypeError, KeyError) as exc:
            await update.message.reply_text(f"{exc}\nPlease enter the verified unit value as a positive number, or send cancel.")
        return True

    if context.user_data.get("account_risk_pending"):
        try:
            parts = [part.strip().replace(",", "") for part in text.split("|")]
            if len(parts) != 4:
                raise ValueError("Send four values separated by |.")
            equity, risk_pct, stop_distance, unit_value = map(float, parts)
            result = calculate_position_size(equity, risk_pct, stop_distance, unit_value)
            context.user_data.pop("account_risk_pending", None)
            await update.message.reply_photo(
                photo=render_account_action_report(
                    "Position Risk Estimate",
                    "Check broker tick value, lot step, commissions and account currency",
                    {"Risk amount": result["risk_amount"], "Calculated lots": result["lots"]}
                ),
                caption=(
                    "🧮 POSITION RISK ESTIMATE\n\n"
                    f"Equity: {equity:.2f}\n"
                    f"Risk: {risk_pct:g}% ({result['risk_amount']:.2f})\n"
                    f"Stop distance: {stop_distance:g}\n"
                    f"Value per price unit per lot: {unit_value:g}\n"
                    f"Calculated volume: {result['lots']:.4f} lots\n\n"
                    "Before using this estimate, verify tick value, contract size, lot step, minimum/maximum volume, commission and account currency with your broker. This is not an order instruction."
                ),
                reply_markup=_home_keyboard(),
            )
        except (ValueError, TypeError) as exc:
            await update.message.reply_text(f"{exc}\nPlease try again.")
        return True

    return False
