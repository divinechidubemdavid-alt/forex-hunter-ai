"""Telegram UI and scheduler for Economic Event Intelligence."""
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CommandHandler

from chart import create_market_chart
import economic_events as ee
import economic_research as research
import state

log = logging.getLogger(__name__)
WAT = ZoneInfo("Africa/Lagos")
POLL_MINUTES = (2, 5, 15, 30, 60)


def event_center_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add personal event", callback_data="econ:add")],
        [InlineKeyboardButton("📅 Upcoming events", callback_data="econ:upcoming"),
         InlineKeyboardButton("🗂 Event history", callback_data="econ:history")],
        [InlineKeyboardButton("🔄 Sync official calendar", callback_data="econ:import")],
    ])


def _event_text(event):
    when = ee._parse_dt(event["scheduled_at"]).astimezone(WAT)
    forecast = event.get("forecast")
    forecast_label = event.get("forecast_type", "market consensus" if forecast is not None else "not supplied")
    return (
        f'🗓 {event["title"]}\n'
        f'Currency: {event.get("currency", "USD")} | Impact: {event.get("impact", "high").upper()}\n'
        f'Time: {when:%a %d %b %Y, %H:%M} WAT\n'
        f'Forecast ({forecast_label}): {forecast if forecast is not None else "Not supplied"} | '
        f'Previous: {event.get("previous") or "Not supplied"}\n'
        f'Source: {event.get("source", "unknown")}\n'
        f'Status: {event.get("status", "scheduled").upper()}'
    )


async def show_event_center(update, context):
    await update.effective_message.reply_text(
        "🧠 ECONOMIC EVENT INTELLIGENCE\n\n"
        "Official BLS calendar events are synced automatically. U.S. employment and CPI actuals "
        "can be verified against the official BLS data API after release. Other releases are "
        "never marked verified unless a supported official source supplies the actual figure.\n\n"
        "Technical checks use completed candles and the existing Twelve Data daily safety limit. "
        "No market-consensus figure is invented. All event times are shown in Nigeria time.",
        reply_markup=event_center_keyboard(),
    )


async def _show_list(chat, include_past, user_id):
    rows = ee.list_events(include_past=include_past, limit=25, user_id=user_id)
    if not rows:
        await chat.send_message("No matching economic events are saved yet. Official BLS calendar sync runs automatically.")
        return
    for event in rows:
        buttons = [InlineKeyboardButton("🔎 Analysis & history", callback_data=f'econ:view:{event["id"]}')]
        if event.get("post_event_report"):
            buttons.append(InlineKeyboardButton("📊 Post-event report", callback_data=f'econ:post:{event["id"]}'))
        await chat.send_message(_event_text(event), reply_markup=InlineKeyboardMarkup([buttons]))


def _owns_event(event, user_id):
    return bool(event) and (
        event.get("owner_id") is None or str(event.get("owner_id")) == str(user_id)
    )


async def handle_event_callback(update, context):
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    user_id = str(update.effective_user.id)
    if data == "econ:add":
        context.user_data["econ_awaiting"] = "event"
        await query.message.reply_text(
            "Send a personal event in WAT:\n"
            "YYYY-MM-DD HH:MM | CURRENCY | EVENT TITLE | FORECAST | PREVIOUS | HIGHER_IS_BETTER\n"
            "Example: 2026-12-11 16:30 | USD | US CPI | 3.1 | 3.2 | no\n"
            "Use - for unknown figures. This event and its alerts are private to your Telegram account."
        )
    elif data == "econ:upcoming":
        await _show_list(query.message.chat, False, user_id)
    elif data == "econ:history":
        await _show_list(query.message.chat, True, user_id)
    elif data == "econ:import":
        try:
            imported = await ee.fetch_ics_events()
            fomc = await ee.fetch_fomc_events()
            bea = await ee.fetch_bea_events()
            total = len(imported) + len(fomc) + len(bea)
            await query.message.reply_text(
                f"Official BLS, Federal Reserve and BEA calendar sync complete: {total} new high-impact event(s)."
                if total else "Official calendars are up to date; no new high-impact events were added."
            )
        except Exception as exc:
            log.exception("Official calendar import failed")
            await query.message.reply_text(f"Calendar sync failed safely: {exc}")
    elif data.startswith(("econ:view:", "econ:actual:", "econ:post:")):
        action, event_id = data.split(":", 2)[1:]
        event = ee.get_event(event_id)
        if not _owns_event(event, user_id):
            await query.message.reply_text("That event is not available in your event history.")
            return
        if action == "actual":
            context.user_data["econ_awaiting"] = "actual:" + event_id
            await query.message.reply_text(
                "Send published actual data as: ACTUAL | REVISED_PREVIOUS (or -) | NOTES\n"
                "Only use figures confirmed by the official release."
            )
            return
        if action == "post":
            report = event.get("post_event_report")
            if not report:
                await query.message.reply_text("No verified post-release report is available yet.")
                return
            analysis = report["analysis"]
            photo = ee.render_event_chart(event, analysis)
            caption = (
                f'📊 POST-RELEASE REPORT — {event["title"]}\n'
                f'Actual: {report["actual"]} | Forecast: {report.get("forecast") or "N/A"} | '
                f'Previous: {report.get("previous") or "N/A"}\n'
                f'Source: {report.get("actual_details", {}).get("source", "user supplied")}\n'
                f'Direction: {analysis["direction"]} | Confidence: {analysis["confidence"]}/100\n'
                f'Forecast score: {report.get("forecast_score", "UNSCORED")}\n'
                + "\n".join(
                    f'Market reaction +{item["minutes_after"]}m: {item["market"]} {item["direction"]} '
                    f'(ADX {item.get("adx")}, RSI {item.get("rsi")})'
                    for item in report.get("market_reactions", [])
                )
                + "\n" + "\n".join("• " + x for x in analysis["reasons"])
            )
            await query.message.reply_photo(photo=photo, caption=caption[:1020])
            return
        analysis = ee.directional_analysis(event)
        report = event.get("post_event_report")
        checkpoints = event.get("checkpoints", {})
        checkpoint_text = "\n".join(
            f'{name}: {item.get("verification", item.get("analysis", {}).get("direction", "saved"))}'
            for name, item in checkpoints.items()
        ) or "No checkpoint reports saved yet."
        caption = (
            _event_text(event) + "\n\n"
            f'CURRENT FUNDAMENTAL BIAS: {analysis["direction"]} '
            f'({analysis["confidence"]}/100)\n'
            + "\n".join("• " + x for x in analysis["reasons"])
            + "\n\nAFFECTED PAIR BIASES:\n"
            + "\n".join(f'{pair}: {bias}' for pair, bias in analysis.get("market_biases", {}).items())
            + "\n\nCHECKPOINT HISTORY:\n" + checkpoint_text
            + ("\n\nPost-release report: available." if report else "\n\nPost-release report: awaiting official actual data.")
        )
        photo = ee.render_event_chart(event, analysis)
        await query.message.reply_photo(photo=photo, caption=caption[:1020])
        await query.message.reply_text(
            "Event actions:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✍️ Record actual release", callback_data=f'econ:actual:{event_id}')],
                [InlineKeyboardButton("📊 View post-event report", callback_data=f'econ:post:{event_id}')],
            ]),
        )


async def handle_event_text(update, context):
    mode = context.user_data.get("econ_awaiting")
    if not mode:
        return False
    raw = (update.message.text or "").strip()
    user_id = str(update.effective_user.id)
    try:
        if mode == "event":
            parts = [p.strip() for p in raw.split("|")]
            if len(parts) != 6:
                raise ValueError("Please send all six fields separated by |.")
            date_text, currency, title, forecast, previous, higher = parts
            when = datetime.strptime(date_text, "%Y-%m-%d %H:%M").replace(tzinfo=WAT)
            if higher.lower() in {"yes", "y", "true", "1"}:
                higher_is_better = True
            elif higher.lower() in {"no", "n", "false", "0"}:
                higher_is_better = False
            else:
                raise ValueError("Higher-is-better must be yes or no.")
            event = ee.create_event(
                title, when, currency=currency, forecast=None if forecast == "-" else forecast,
                previous=None if previous == "-" else previous, owner_id=user_id,
            )
            event["higher_is_better"] = higher_is_better
            ee.save_event(event)
            context.user_data.pop("econ_awaiting", None)
            await update.message.reply_text("✅ Personal event saved. Only your account will receive its alerts.")
        elif mode.startswith("actual:"):
            event_id = mode.split(":", 1)[1]
            event = ee.get_event(event_id)
            if not _owns_event(event, user_id):
                raise ValueError("This event does not belong to your account.")
            parts = [p.strip() for p in raw.split("|", 2)]
            if len(parts) != 3:
                raise ValueError("Please use ACTUAL | REVISED_PREVIOUS (or -) | NOTES.")
            report = ee.record_actual(
                event_id, parts[0], None if parts[1] == "-" else parts[1], parts[2],
                details={"source": "user-supplied published release"},
            )
            context.user_data.pop("econ_awaiting", None)
            await update.message.reply_text(
                f'✅ Post-release report saved. Actual: {report["actual"]}; '
                f'forecast: {report.get("forecast") or "N/A"}; '
                f'direction: {report["analysis"]["direction"]}.'
            )
    except Exception as exc:
        await update.message.reply_text(f"Could not save that entry: {exc}\nPlease check the format and try again.")
    return True


async def _attempt_official_actual(event):
    attempts = event.setdefault("actual_poll_attempts", [])
    elapsed = datetime.now(timezone.utc) - ee._parse_dt(event["scheduled_at"])
    due = [minute for minute in POLL_MINUTES if elapsed >= timedelta(minutes=minute) and minute not in attempts]
    if not due:
        return None
    attempt = due[0]
    attempts.append(attempt)
    ee.save_event(event)
    try:
        result = await research.auto_actual_release(event)
    except Exception:
        log.exception("Official actual-data retrieval failed for event %s", event.get("id"))
        return None
    if not result:
        return None
    event = ee.get_event(event["id"])
    event["actual_details"] = result
    event["higher_is_better"] = result.get("higher_is_better")
    if event.get("forecast") is None and result.get("previous_value") is not None:
        event["previous"] = str(result["previous_value"])
    ee.save_event(event)
    return ee.record_actual(
        event["id"], str(result["value"]), notes="Automatically retrieved from the official BLS API.",
        details=result,
    )


async def _send_event_alert(application, event, checkpoint, record, market_chart=None):
    analysis = record["analysis"]
    snapshot = record.get("technical_snapshot") or {}
    scheduled = ee._parse_dt(event["scheduled_at"]).astimezone(WAT)
    market_lines = "\n".join(
        f'{pair}: {bias}' for pair, bias in analysis.get("market_biases", {}).items()
    ) or "No mapped pair bias available."
    technical_text = (
        f'{snapshot.get("market", "Market")}: {snapshot.get("direction", "UNAVAILABLE")} '
        f'| ADX {snapshot.get("adx", "N/A")} | RSI {snapshot.get("rsi", "N/A")} '
        f'| ATR {snapshot.get("atr", "N/A")} | breakout {snapshot.get("breakout", "N/A")}'
        if snapshot.get("status") == "AVAILABLE" else
        "Technical confirmation unavailable: " + snapshot.get("reason", "data unavailable")
    )
    text = (
        f'🚨 ECONOMIC EVENT — {checkpoint.upper()} CHECKPOINT\n\n'
        f'{event["title"]}\nEvent time: {scheduled:%d %b %Y, %H:%M} WAT\n'
        f'Currency: {event.get("currency", "USD")}\n'
        f'Forecast: {event.get("forecast") or "Not supplied"} | Previous: {event.get("previous") or "Not supplied"}\n\n'
        f'Fundamental direction: {analysis["direction"]} | Confidence: {analysis["confidence"]}/100\n'
        f'Technical: {technical_text}\n\nAffected market bias:\n{market_lines}\n\n'
        + "\n".join("• " + reason for reason in analysis["reasons"])
    )
    if checkpoint == "5m":
        if record.get("verification") == "CONFIRMED_UNCHANGED":
            text += "\n\n15-MINUTE FORECAST CONFIRMED — UNCHANGED."
        elif record.get("verification") == "DIRECTION_REVIEWED":
            text += "\n\nDIRECTION REVISED — NEW MATERIAL TECHNICAL EVIDENCE DETECTED."
        elif record.get("verification") == "NO_15M_BASELINE":
            text += "\n\n5-MINUTE VERIFICATION INCOMPLETE — NO 15-MINUTE BASELINE."
        elif record.get("verification") == "UNAVAILABLE":
            text += "\n\nFRESH MARKET DATA UNAVAILABLE — ORIGINAL 15-MINUTE FORECAST PRESERVED, NOT CONFIRMED."
    photo = market_chart or ee.render_event_chart(event, analysis)
    recipients = [event["owner_id"]] if event.get("owner_id") else state.get_users()
    for user_id in recipients:
        try:
            if hasattr(photo, "seek"):
                photo.seek(0)
            await application.bot.send_photo(chat_id=int(user_id), photo=photo, caption=text[:1020])
        except Exception:
            log.exception("Could not deliver economic event alert to user %s", user_id)


async def economic_events_loop(application):
    """Sync the official calendar, run four research checkpoints and verify actual releases."""
    last_import = 0.0
    while True:
        try:
            now = datetime.now(timezone.utc)
            if now.timestamp() - last_import >= 6 * 3600:
                try:
                    await ee.fetch_ics_events()
                    await ee.fetch_fomc_events()
                    await ee.fetch_bea_events()
                except Exception:
                    log.exception("Official economic calendar sync failed")
                last_import = now.timestamp()
            for event in ee.list_events(include_past=False, limit=100, user_id="*"):
                if event.get("status") == "scheduled" and ee._parse_dt(event["scheduled_at"]) <= now:
                    try:
                        report = await _attempt_official_actual(event)
                        if report:
                            log.info("Official actual saved for %s", event.get("title"))
                    except Exception:
                        log.exception("Automatic actual report failed")
                event = ee.get_event(event["id"]) or event
                if event.get("status") == "released" and event.get("post_event_report"):
                    elapsed = now - ee._parse_dt(event["scheduled_at"])
                    attempts = event.setdefault("post_release_attempts", [])
                    for minutes_after in (5, 15, 30):
                        if elapsed >= timedelta(minutes=minutes_after) and minutes_after not in attempts:
                            attempts.append(minutes_after)
                            ee.save_event(event)
                            try:
                                reaction = await research.technical_snapshot(
                                    event, "15m" if minutes_after >= 15 else "5m"
                                )
                                reaction.pop("_frame", None)
                                if reaction.get("status") == "AVAILABLE":
                                    ee.record_market_reaction(event["id"], reaction, minutes_after)
                            except Exception:
                                log.exception("Post-release market reaction check failed")
                            event = ee.get_event(event["id"]) or event
                            attempts = event.setdefault("post_release_attempts", [])
                for checkpoint in ee.due_checkpoints(event, now):
                    if checkpoint in {"24h", "1h"} and event.get("forecast") is None:
                        checked = event.setdefault("model_forecast_attempts", [])
                        if checkpoint not in checked:
                            checked.append(checkpoint)
                            ee.save_event(event)
                            try:
                                model = await research.fetch_bls_model_forecast(event)
                                if model:
                                    event["forecast"] = str(model["forecast"])
                                    event["previous"] = str(model["previous"])
                                    event["higher_is_better"] = model["higher_is_better"]
                                    event["forecast_type"] = model["forecast_type"]
                                    event["forecast_source"] = model["forecast_source"]
                                    ee.save_event(event)
                            except Exception:
                                log.exception("BLS model baseline could not be prepared")
                    snapshot = await research.technical_snapshot(event, checkpoint)
                    frame = snapshot.pop("_frame", None)
                    market_chart = None
                    if frame is not None:
                        try:
                            market_chart = create_market_chart(
                                frame, snapshot.get("market", "MARKET"),
                                snapshot.get("timeframe", "15m"),
                            )
                        except Exception:
                            log.exception("Could not render event market chart")
                    technical_direction = snapshot.get("currency_direction") if snapshot.get("status") == "AVAILABLE" else None
                    record = ee.record_checkpoint(
                        event["id"], checkpoint, technical=technical_direction,
                        technical_snapshot=snapshot,
                        evidence_note=snapshot.get("reason", ""),
                    )
                    await _send_event_alert(application, event, checkpoint, record, market_chart)
        except Exception:
            log.exception("Economic event scheduler iteration failed")
        await asyncio.sleep(20)


def register_event_handlers(application):
    application.add_handler(CommandHandler("events", show_event_center))
