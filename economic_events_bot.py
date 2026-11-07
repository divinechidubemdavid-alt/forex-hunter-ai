"""Telegram interface and scheduled delivery for Economic Intelligence."""
import asyncio
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CommandHandler

import economic_events as ee
import state

log = logging.getLogger(__name__)
WAT = ZoneInfo("Africa/Lagos")


def event_center_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add economic event", callback_data="econ:add")],
        [InlineKeyboardButton("📅 Upcoming events", callback_data="econ:upcoming"),
         InlineKeyboardButton("🗂 Event history", callback_data="econ:history")],
        [InlineKeyboardButton("📥 Import official calendar feed", callback_data="econ:import")],
    ])


def _event_text(event):
    when = ee._parse_dt(event["scheduled_at"]).astimezone(WAT)
    return (
        f'🗓 {event["title"]}\n'
        f'Currency: {event.get("currency", "USD")} | Impact: {event.get("impact", "high").upper()}\n'
        f'Time: {when:%a %d %b %Y, %H:%M} WAT\n'
        f'Forecast: {event.get("forecast") or "Not supplied"} | '
        f'Previous: {event.get("previous") or "Not supplied"}\n'
        f'Source: {event.get("source", "unknown")}\n'
        f'Status: {event.get("status", "scheduled").upper()}'
    )


async def show_event_center(update, context):
    await update.effective_message.reply_text(
        "🧠 ECONOMIC EVENT INTELLIGENCE\n\n"
        "Manage scheduled high-impact releases, review event research, "
        "view post-release reports and keep a permanent event history.\n\n"
        "Calendar feeds must be HTTPS sources whose terms permit your use. "
        "No unreleased figures or consensus forecasts are invented.\n\n"
        "Choose an action:",
        reply_markup=event_center_keyboard(),
    )


async def _show_list(chat, include_past):
    rows = ee.list_events(include_past=include_past, limit=20)
    if not rows:
        await chat.send_message("No economic events are saved yet. Add an event or configure a rights-cleared calendar feed.")
        return
    for event in rows:
        buttons = [
            InlineKeyboardButton("🔎 Analysis", callback_data=f'econ:view:{event["id"]}')
        ]
        if event.get("post_event_report"):
            buttons.append(InlineKeyboardButton("📊 Post-event report", callback_data=f'econ:post:{event["id"]}'))
        await chat.send_message(_event_text(event), reply_markup=InlineKeyboardMarkup([buttons]))


async def handle_event_callback(update, context):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data == "econ:add":
        context.user_data["econ_awaiting"] = "event"
        await query.message.reply_text(
            "Send the event in this format (all times WAT):\n"
            "YYYY-MM-DD HH:MM | CURRENCY | EVENT TITLE | FORECAST | PREVIOUS | HIGHER_IS_BETTER\n\n"
            "Example: 2026-12-11 16:30 | USD | US CPI | 3.1 | 3.2 | no\n"
            "Use - when forecast/previous is unavailable. For inflation, lower is usually better, so use no. "
            "This is your supplied data, not an independently verified consensus."
        )
    elif data == "econ:upcoming":
        await _show_list(query.message.chat, False)
    elif data == "econ:history":
        await _show_list(query.message.chat, True)
    elif data == "econ:import":
        try:
            imported = await ee.fetch_ics_events()
            await query.message.reply_text(
                f"Calendar import complete: {len(imported)} new high-impact event(s)."
                if imported else
                "No new events imported. Set ECONOMIC_CALENDAR_ICS_URL to a rights-cleared HTTPS ICS feed in FadeHost/Codespaces environment settings."
            )
        except Exception as exc:
            log.exception("Economic calendar import failed")
            await query.message.reply_text(f"Calendar import failed safely: {exc}")
    elif data.startswith("econ:view:"):
        event = ee.get_event(data.split(":", 2)[2])
        if not event:
            await query.message.reply_text("That event could not be found.")
            return
        analysis = ee.directional_analysis(event)
        caption = (
            _event_text(event) + "\n\n"
            f'PRE-EVENT DIRECTION: {analysis["direction"]}\n'
            f'Confidence: {analysis["confidence"]}/100\n'
            + "\n".join("• " + x for x in analysis["reasons"])
            + "\n\n" + analysis["disclaimer"]
        )
        photo = ee.render_event_chart(event, analysis)
        await query.message.reply_photo(photo=photo, caption=caption[:1020])
        await query.message.reply_text(
            "Event actions:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✍️ Record actual release", callback_data=f'econ:actual:{event["id"]}')],
                [InlineKeyboardButton("📊 View post-event report", callback_data=f'econ:post:{event["id"]}')],
            ]),
        )
    elif data.startswith("econ:actual:"):
        event_id = data.split(":", 2)[2]
        if not ee.get_event(event_id):
            await query.message.reply_text("Event not found.")
            return
        context.user_data["econ_awaiting"] = "actual:" + event_id
        await query.message.reply_text(
            "Send actual release data as: ACTUAL | REVISED_PREVIOUS (or -) | NOTES\n"
            "Example: 3.4 | 3.3 | CPI hotter than expected.\n"
            "Enter only figures from a published release."
        )
    elif data.startswith("econ:post:"):
        event = ee.get_event(data.split(":", 2)[2])
        report = event.get("post_event_report") if event else None
        if not report:
            await query.message.reply_text("No post-event report exists yet. Record the published actual figure first.")
            return
        analysis = report["analysis"]
        photo = ee.render_event_chart(event, analysis)
        caption = (
            f'📊 POST-EVENT REPORT — {event["title"]}\n'
            f'Actual: {report["actual"]} | Forecast: {report.get("forecast") or "N/A"} | '
            f'Previous: {report.get("previous") or "N/A"}\n'
            f'Direction: {analysis["direction"]} | Confidence: {analysis["confidence"]}/100\n'
            + "\n".join("• " + x for x in analysis["reasons"])
        )
        await query.message.reply_photo(photo=photo, caption=caption[:1020])


async def handle_event_text(update, context):
    mode = context.user_data.get("econ_awaiting")
    if not mode:
        return False
    raw = (update.message.text or "").strip()
    try:
        if mode == "event":
            parts = [p.strip() for p in raw.split("|")]
            if len(parts) != 6:
                raise ValueError("Please send all six fields separated by |.")
            date_text, currency, title, forecast, previous, higher = parts
            when = datetime.strptime(date_text, "%Y-%m-%d %H:%M").replace(tzinfo=WAT)
            event = ee.create_event(
                title, when, currency=currency, forecast=None if forecast == "-" else forecast,
                previous=None if previous == "-" else previous,
            )
            event["higher_is_better"] = higher.lower() in {"yes", "y", "true", "1"}
            ee.save_event(event)
            context.user_data.pop("econ_awaiting", None)
            await update.message.reply_text("✅ Event saved. Its event analysis, checkpoints and history are now available in Economic Events.")
        elif mode.startswith("actual:"):
            event_id = mode.split(":", 1)[1]
            parts = [p.strip() for p in raw.split("|", 2)]
            if len(parts) != 3:
                raise ValueError("Please use ACTUAL | REVISED_PREVIOUS (or -) | NOTES.")
            report = ee.record_actual(event_id, parts[0], None if parts[1] == "-" else parts[1], parts[2])
            context.user_data.pop("econ_awaiting", None)
            await update.message.reply_text(
                f'✅ Post-event report saved. Actual: {report["actual"]}; '
                f'forecast: {report.get("forecast") or "N/A"}; '
                f'direction: {report["analysis"]["direction"]}.'
            )
    except Exception as exc:
        await update.message.reply_text(f"Could not save that entry: {exc}\nPlease check the format and try again.")
    return True


async def economic_events_loop(application):
    """Send four checkpoint alerts, each with an event-specific visual and text."""
    last_import = 0.0
    while True:
        try:
            now = datetime.now(timezone.utc)
            if (now.timestamp() - last_import) >= 6 * 3600:
                try:
                    await ee.fetch_ics_events()
                except Exception:
                    log.exception("Scheduled economic calendar sync failed")
                last_import = now.timestamp()
            for event in ee.list_events(include_past=False, limit=100):
                for checkpoint in ee.due_checkpoints(event, now):
                    record = ee.record_checkpoint(event["id"], checkpoint)
                    analysis = record["analysis"]
                    photo = ee.render_event_chart(event, analysis)
                    scheduled = ee._parse_dt(event["scheduled_at"]).astimezone(WAT)
                    text = (
                        f'🚨 ECONOMIC EVENT — {checkpoint.upper()} CHECKPOINT\n\n'
                        f'{event["title"]}\n'
                        f'Event time: {scheduled:%d %b %Y, %H:%M} WAT\n'
                        f'Currency: {event.get("currency", "USD")}\n'
                        f'Forecast: {event.get("forecast") or "Not supplied"} | '
                        f'Previous: {event.get("previous") or "Not supplied"}\n\n'
                        f'Fundamental direction: {analysis["direction"]}\n'
                        f'Confidence: {analysis["confidence"]}/100\n'
                        f'Technical context: {analysis["technical_direction"]}\n'
                        + "\n".join("• " + reason for reason in analysis["reasons"])
                    )
                    if checkpoint == "5m":
                        text += "\n\n" + (
                            "15-MINUTE FORECAST CONFIRMED — UNCHANGED."
                            if record.get("verification") == "CONFIRMED_UNCHANGED"
                            else "Verification requires review of the newly supplied evidence."
                        )
                    for user_id in state.get_users():
                        try:
                            await application.bot.send_photo(
                                chat_id=int(user_id), photo=photo,
                                caption=text[:1020],
                            )
                        except Exception:
                            log.exception("Could not deliver economic event alert to user %s", user_id)
        except Exception:
            log.exception("Economic event scheduler iteration failed")
        await asyncio.sleep(20)


def register_event_handlers(application):
    application.add_handler(CommandHandler("events", show_event_center))
