"""Telegram UI and scheduler for Economic Event Intelligence."""
import asyncio
import logging
import os
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CommandHandler

from chart import create_market_chart
import economic_events as ee
import economic_event_history as event_history
import economic_event_visuals as event_visuals
import economic_research as research
import trading_economics as te
import state

log = logging.getLogger(__name__)
WAT = ZoneInfo("Africa/Lagos")
POLL_MINUTES = (2, 5, 15, 30, 60)


def event_center_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add personal event", callback_data="econ:add")],
        [InlineKeyboardButton("📅 Upcoming events", callback_data="econ:upcoming"),
         InlineKeyboardButton("🗂 Event history", callback_data="econ:history")],
        [InlineKeyboardButton("📈 Event performance", callback_data="econ:performance")],
        [InlineKeyboardButton("🔄 Sync official calendar", callback_data="econ:import")],
    ])


async def _sync_calendars():
    if te.configured():
        try:
            return len(await te.fetch_calendar_events()), "Trading Economics global calendar"
        except Exception:
            log.exception("Licensed global calendar sync failed; using official-source fallback")
    total = 0
    successes = 0
    failures = []
    for label, importer in (
        ("BLS", ee.fetch_ics_events),
        ("Federal Reserve", ee.fetch_fomc_events),
        ("BEA", ee.fetch_bea_events),
    ):
        try:
            imported = await importer()
            successes += 1
            total += len(imported)
            log.info("%s calendar sync completed; %d new events imported", label, len(imported))
        except Exception as exc:
            failures.append(label)
            log.exception("%s calendar sync failed", label)
    # A successful importer may return zero because there are no new dates to add.
    # Only report total failure when every official source raised an exception.
    if failures and successes == 0:
        raise RuntimeError("All official calendar imports failed: " + ", ".join(failures))
    source = "official BLS, Federal Reserve and BEA calendars"
    if failures:
        source += " (partial; failed: " + ", ".join(failures) + ")"
    return total, source


def _event_text(event):
    when = ee._parse_dt(event["scheduled_at"]).astimezone(WAT)
    forecast = event.get("forecast")
    forecast_label = event.get("forecast_type", "market consensus" if forecast is not None else "not supplied")
    is_past = ee._parse_dt(event["scheduled_at"]) <= datetime.now(timezone.utc)
    status = event.get("status", "scheduled").upper()
    if is_past and not event.get("post_event_report"):
        status = "AWAITING OUTCOME / EVALUATION PENDING"
    elif is_past and event.get("post_event_report") and not event.get("event_trade"):
        status = "RELEASED — TRADE RESULT MAY BE UNAVAILABLE"
    return (
        f'🗓 {event["title"]}\n'
        f'Currency: {event.get("currency", "USD")} | Impact: {event.get("impact", "high").upper()}\n'
        f'Time: {when:%a %d %b %Y, %H:%M} WAT\n'
        f'Forecast ({forecast_label}): {forecast if forecast is not None else "Not supplied"} | '
        f'Previous: {event.get("previous") or "Not supplied"}\n'
        f'Source: {event.get("provider_source", event.get("source", "unknown"))}\n'
        f'Status: {status}'
    )


async def show_event_center(update, context):
    await update.effective_message.reply_text(
        "🧠 ECONOMIC EVENT INTELLIGENCE\n\n"
        "Official BLS, Federal Reserve and BEA calendars are synced automatically. If a licensed "
        "global provider is configured, the bot also retrieves consensus, actuals, revisions and "
        "economic news across supported currencies. Otherwise, BLS official actuals and clearly "
        "labelled historical-trend estimates are used where supported.\n\n"
        "Technical checks use completed candles and the existing Twelve Data daily safety limit. "
        "No market-consensus figure is invented. All event times are shown in Nigeria time.",
        reply_markup=event_center_keyboard(),
    )


def _events_for_display(events, include_past, now=None):
    """Split event cards strictly at scheduled release time."""
    now = ee._parse_dt(now or datetime.now(timezone.utc))
    rows = [
        event for event in events
        if (ee._parse_dt(event["scheduled_at"]) <= now if include_past
            else ee._parse_dt(event["scheduled_at"]) > now)
    ]
    return sorted(rows, key=lambda event: event.get("scheduled_at", ""), reverse=bool(include_past))


async def _event_artwork_photo(event):
    """Reuse the exact event artwork across Upcoming, History and Performance."""
    artwork = event.get("event_artwork") or {}
    if artwork.get("file_id"):
        return artwork["file_id"]
    return event_visuals.render_event_artwork(event)


async def _send_event_artwork(chat, event, caption=None):
    """Deliver and persist the event's original artwork identity."""
    photo = await _event_artwork_photo(event)
    sender = getattr(chat, "send_photo", None) or getattr(chat, "reply_photo")
    sent = await sender(
        photo=photo,
        caption=(caption or f'🎨 EVENT ARTWORK — {event.get("title", "Economic event")}'),
    )
    if sent.photo and not (event.get("event_artwork") or {}).get("file_id"):
        event["event_artwork"] = {
            "file_id": sent.photo[-1].file_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "kind": "event_artwork",
        }
        ee.save_event(event)
    return sent


async def _show_list(chat, include_past, user_id):
    # Display sections are mutually exclusive at the exact scheduled release time.
    all_rows = ee.list_events(include_past=True, limit=300, user_id=user_id)
    rows = _events_for_display(all_rows, include_past)[:25]
    if not rows:
        label = "past events" if include_past else "upcoming events"
        await chat.send_message(f"No {label} are saved yet. Official calendar sync runs automatically.")
        return
    for event in rows:
        buttons = [InlineKeyboardButton("🔎 Analysis & history", callback_data=f'econ:view:{event["id"]}')]
        if event.get("post_event_report"):
            buttons.append(InlineKeyboardButton("📊 Post-event report", callback_data=f'econ:post:{event["id"]}'))
        photo = await _event_artwork_photo(event)
        try:
            sent = await chat.send_photo(
                photo=photo, caption=_event_text(event),
                reply_markup=InlineKeyboardMarkup([buttons]),
            )
            if sent.photo and not (event.get("event_artwork") or {}).get("file_id"):
                event["event_artwork"] = {
                    "file_id": sent.photo[-1].file_id,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "kind": "event_artwork",
                }
                ee.save_event(event)
        except Exception:
            log.exception("Could not deliver event artwork for %s", event.get("id"))
            await chat.send_message(_event_text(event), reply_markup=InlineKeyboardMarkup([buttons]))


def _owns_event(event, user_id):
    return bool(event) and (
        event.get("owner_id") is None or str(event.get("owner_id")) == str(user_id)
    )


def _can_edit_event(event, user_id):
    if not event:
        return False
    if event.get("owner_id") is not None:
        return str(event.get("owner_id")) == str(user_id)
    owner_id = os.getenv("BOT_OWNER_ID", "").strip()
    return bool(owner_id) and str(user_id) == owner_id


def _latest_market_chart(event):
    """Find the newest real candlestick image, never an event-summary card."""
    for item in reversed(event.get("chart_images") or []):
        if item.get("chart_type") == "market_candlestick" or (
            item.get("market") and item.get("timeframe")
        ):
            return item
    return None


async def _event_chart_photo(event, refresh_market=False):
    """Prefer the saved live-market chart; optionally create one on demand.

    A labelled event-summary image is the last-resort fallback when market data
    is unavailable. It is never mistaken for a candlestick chart.
    """
    saved = _latest_market_chart(event)
    if saved and saved.get("file_id"):
        return saved["file_id"], {
            "chart_type": "market_candlestick",
            "market": saved.get("market"),
            "timeframe": saved.get("timeframe"),
        }
    if refresh_market:
        snapshot = await research.technical_snapshot(event, "15m")
        frame = snapshot.pop("_frame", None)
        if frame is not None:
            photo = create_market_chart(
                frame, snapshot.get("market", "MARKET"),
                snapshot.get("timeframe", "15m"),
            )
            return photo, {
                "chart_type": "market_candlestick",
                "market": snapshot.get("market"),
                "timeframe": snapshot.get("timeframe", "15m"),
            }
    analysis = ee.directional_analysis(event, phase=(
        "post-event" if event.get("actual") is not None else "pre-event"
    ))
    return ee.render_event_chart(event, analysis), {"chart_type": "event_summary"}


async def handle_event_callback(update, context):
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    user_id = str(update.effective_user.id)
    if data == "econ:add":
        context.user_data["econ_awaiting"] = "event"
        await query.message.reply_text(
            "Send a personal event in WAT:\n"
            "YYYY-MM-DD HH:MM | CURRENCY | EVENT TITLE | FORECAST | PREVIOUS | HIGHER_SUPPORTS_CURRENCY\n"
            "Example: 2026-12-11 16:30 | USD | US CPI | 3.1 | 3.2 | no\n"
            "Use - for unknown figures. This event and its alerts are private to your Telegram account."
        )
    elif data == "econ:upcoming":
        await _show_list(query.message.chat, False, user_id)
    elif data == "econ:history":
        await _show_list(query.message.chat, True, user_id)
    elif data == "econ:performance":
        rows = _events_for_display(
            ee.list_events(include_past=True, limit=300, user_id=user_id),
            include_past=True,
        )
        stats = event_history.performance_summary(rows)
        dashboard = event_visuals.render_performance_dashboard(rows, stats)
        await query.message.reply_photo(
            photo=dashboard,
            caption=(
                "📊 ECONOMIC EVENT PERFORMANCE — individual event results and the collective report.\n"
                "Illustrated metrics are calculated only from saved evidence. TP1, TP2 and TP3 each count as wins."
            ),
        )
        # Keep the event's own visual identity and its real market chart together.
        for item in rows[:25]:
            analysis = (
                (item.get("final_15m") or item.get("checkpoints", {}).get("15m") or {}).get("analysis")
                or (item.get("post_event_report") or {}).get("analysis")
                or ee.directional_analysis(item, phase="post-event" if item.get("actual") is not None else "pre-event")
            )
            artwork = await _event_artwork_photo(item)
            try:
                sent_art = await query.message.reply_photo(
                    photo=artwork,
                    caption=f'🗂 {item.get("title", "Economic event")} — individual event identity\n'
                            f'Direction: {analysis.get("combined_direction", analysis.get("direction", "UNSCORED"))} | '
                            f'Confidence: {analysis.get("confidence", "N/A")}/100',
                )
                if sent_art.photo and not (item.get("event_artwork") or {}).get("file_id"):
                    item["event_artwork"] = {
                        "file_id": sent_art.photo[-1].file_id,
                        "created_at": datetime.now(timezone.utc).isoformat(),
                        "kind": "event_artwork",
                    }
                    ee.save_event(item)
            except Exception:
                log.exception("Could not display event artwork in performance for %s", item.get("id"))
            chart, chart_meta = await _event_chart_photo(item, refresh_market=False)
            try:
                if hasattr(chart, "seek"):
                    chart.seek(0)
                sent_chart = await query.message.reply_photo(
                    photo=chart,
                    caption=f'📈 {item.get("title", "Economic event")} — '
                            f'{chart_meta.get("market", "Event summary")} '
                            f'{chart_meta.get("timeframe", "")} '
                            f'[{chart_meta.get("chart_type", "event_summary")}]',
                )
                if sent_chart.photo:
                    item.setdefault("chart_images", []).append({
                        "file_id": sent_chart.photo[-1].file_id,
                        "checkpoint": "performance_view",
                        "created_at": datetime.now(timezone.utc).isoformat(),
                        "event_id": item["id"],
                        **chart_meta,
                    })
                    item["chart_images"] = item["chart_images"][-20:]
                    ee.save_event(item)
            except Exception:
                log.exception("Could not display performance chart for event %s", item.get("id"))
    elif data == "econ:import":
        try:
            total, source = await _sync_calendars()
            await query.message.reply_text(
                f"{source} sync complete: {total} new high-impact event(s)."
                if total else f"{source} is up to date; no new high-impact events were added."
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
            if not _can_edit_event(event, user_id):
                await query.message.reply_text(
                    "Only the bot owner can manually edit a shared official event. "
                    "Automatic provider actuals remain enabled."
                )
                return
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
            photo, chart_meta = await _event_chart_photo(event, refresh_market=True)
            caption = (
                f'📊 POST-RELEASE REPORT — {event["title"]}\n'
                f'Actual: {report["actual"]} | Forecast: {report.get("forecast") or "N/A"} | '
                f'Previous: {report.get("previous") or "N/A"}\n'
                f'Source: {report.get("actual_details", {}).get("source", "user supplied")}\n'
                f'Fundamental: {analysis.get("fundamental_direction", analysis["direction"])} | '
                f'Combined: {analysis.get("combined_direction", analysis["direction"])} | '
                f'Confidence: {analysis["confidence"]}/100\n'
                f'Forecast score: {report.get("forecast_score", "UNSCORED")}\n'
                + "\n".join(
                    f'Market reaction +{item["minutes_after"]}m: {item["market"]} {item["direction"]} '
                    f'(ADX {item.get("adx")}, RSI {item.get("rsi")})'
                    for item in report.get("market_reactions", [])
                )
                + "\n" + "\n".join("• " + x for x in analysis["reasons"])
            )
            await _send_event_artwork(
                query.message,
                event,
                caption=f'🎨 {event["title"]} — original event artwork (retained in history)',
            )
            sent_chart = await query.message.reply_photo(photo=photo, caption=caption[:1020])
            if sent_chart.photo:
                event.setdefault("chart_images", []).append({
                    "file_id": sent_chart.photo[-1].file_id,
                    "checkpoint": "post_event_report",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "event_id": event_id,
                    **chart_meta,
                })
                event["chart_images"] = event["chart_images"][-20:]
                ee.save_event(event)
            return
        report = event.get("post_event_report")
        checkpoints = event.get("checkpoints", {})
        if report:
            analysis = report.get("analysis") or ee.directional_analysis(event, phase="post-event")
        elif checkpoints.get("5m"):
            analysis = checkpoints["5m"].get("analysis") or ee.directional_analysis(event)
        elif checkpoints.get("15m"):
            analysis = checkpoints["15m"].get("analysis") or ee.directional_analysis(event)
        else:
            analysis = ee.directional_analysis(event)
        checkpoint_text = "\n".join(
            f'{name}: {item.get("verification", item.get("analysis", {}).get("direction", "saved"))}'
            for name, item in checkpoints.items()
        ) or "No checkpoint reports saved yet."
        news_text = "\n".join(
            f'• {item.get("title", "Headline")}: {item.get("description", "")[:150]}'
            for item in event.get("research_news", [])[:3]
        )
        yields_text = "\n".join(
            f'{item.get("country")}: {item.get("yield_pct")}% 10Y'
            for item in event.get("bond_yield_context", [])
        )
        caption = (
            _event_text(event) + "\n\n"
            f'FUNDAMENTAL BIAS: {analysis.get("fundamental_direction", analysis["direction"])}\n'
            f'TECHNICAL BIAS: {analysis.get("technical_direction", "NOT PROVIDED")}\n'
            f'COMBINED DIRECTION: {analysis.get("combined_direction", analysis["direction"])} '
            f'({analysis["confidence"]}/100)\n'
            + "\n".join("• " + x for x in analysis["reasons"])
            + "\n\nAFFECTED PAIR BIASES:\n"
            + "\n".join(f'{pair}: {bias}' for pair, bias in analysis.get("market_biases", {}).items())
            + "\n\nCHECKPOINT HISTORY:\n" + checkpoint_text
            + ("\n\nLATEST NEWS:\n" + news_text if news_text else "")
            + ("\n\n10Y YIELDS:\n" + yields_text if yields_text else "")
            + ("\n\nPost-release report: available." if report else "\n\nPost-release report: awaiting official actual data.")
        )
        latest_checkpoint = checkpoints.get("5m") or checkpoints.get("15m") or {}
        history_plan = latest_checkpoint.get("trade_plan") or {}
        caption += (
            "\n\nTRADE TIMEFRAME: "
            f'{history_plan.get("execution_timeframe", "1m")} entry / '
            f'{history_plan.get("confirmation_timeframe", "5m")} confirmation / '
            f'{history_plan.get("context_timeframe", "15m")} context'
            f'\n{history_plan.get("context_note", "15m is wider chart context; automated confirmation uses 5m.")}'
            f'\nTRADE PLAN: {history_plan.get("status", "NOT YET AVAILABLE")} — '
            f'{history_plan.get("reason", "Await the final review.")}'
        )
        if history_plan.get("status") == "CONDITIONAL_SETUP":
            targets = history_plan.get("take_profits", [])
            caption += (
                f'\n{history_plan.get("side")} {history_plan.get("market")} '
                f'Entry {history_plan.get("entry_trigger")} | SL {history_plan.get("stop_loss")}'
                f' | TP1 {targets[0]} | TP2 {targets[1]} | TP3 {targets[2]}'
            )
        await _send_event_artwork(
            query.message,
            event,
            caption=f'🎨 {event["title"]} — event identity artwork',
        )
        photo, chart_meta = await _event_chart_photo(event, refresh_market=True)
        sent_chart = await query.message.reply_photo(photo=photo, caption=caption[:1020])
        if len(caption) > 1020:
            await query.message.reply_text(caption[1020:4096])
        if sent_chart.photo:
            event.setdefault("chart_images", []).append({
                "file_id": sent_chart.photo[-1].file_id,
                "checkpoint": "history_view",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "event_id": event_id,
                **chart_meta,
            })
            event["chart_images"] = event["chart_images"][-20:]
            ee.save_event(event)
        conversation = event_history.event_conversation(event)
        if conversation:
            lines = []
            for item in conversation[-12:]:
                line = f'{item["stage"]}: {item["direction"]}'
                if item.get("confidence") is not None:
                    line += f' ({item["confidence"]}/100)'
                if item.get("verification"):
                    line += f' — {item["verification"]}'
                if item.get("summary"):
                    line += f'\n{item["summary"][:220]}'
                lines.append(line)
            await query.message.reply_text("🧠 SAVED EVENT RESEARCH / AI HISTORY\n\n" + "\n\n".join(lines))
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
                raise ValueError("Enter yes if a higher reading normally supports the currency, or no if a lower reading does.")
            event = ee.create_event(
                title, when, currency=currency, forecast=None if forecast == "-" else forecast,
                previous=None if previous == "-" else previous, owner_id=user_id,
            )
            event["higher_is_better"] = higher_is_better
            if forecast != "-":
                event["forecast_type"] = "user_supplied"
                event["forecast_source"] = "User supplied event input"
            ee.save_event(event)
            context.user_data.pop("econ_awaiting", None)
            await update.message.reply_text("✅ Personal event saved. Only your account will receive its alerts.")
        elif mode.startswith("actual:"):
            event_id = mode.split(":", 1)[1]
            event = ee.get_event(event_id)
            if not _can_edit_event(event, user_id):
                raise ValueError("You cannot edit this shared event. Only its owner or the configured bot owner can enter actuals.")
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


async def _refresh_provider_event(event):
    """Refresh consensus, previous/revision and any newly published actual."""
    if not te.configured() or not event.get("provider_calendar_id"):
        return event
    snapshot = await te.fetch_event_snapshot(event)
    if not snapshot:
        return event
    if snapshot.get("forecast") is not None:
        event["forecast"] = snapshot["forecast"]
        event["forecast_type"] = snapshot.get("forecast_type", event.get("forecast_type"))
        event["forecast_source"] = "Trading Economics"
    if snapshot.get("previous_value") is not None:
        event["previous"] = snapshot["previous_value"]
    if snapshot.get("higher_is_better") is not None:
        event["higher_is_better"] = snapshot["higher_is_better"]
    if snapshot.get("revised_previous") is not None:
        event["provider_revised_previous"] = snapshot["revised_previous"]
    event["provider_source"] = snapshot.get("source")
    event["provider_source_url"] = snapshot.get("source_url")
    event["provider_context_checked_at"] = snapshot.get("retrieved_at")
    if snapshot.get("value") and not event.get("post_event_report"):
        event["actual_details"] = snapshot
        ee.save_event(event)
        report = ee.record_actual(
            event["id"], snapshot["value"],
            notes="Actual retrieved from the licensed global economic calendar.",
            details=snapshot,
        )
        if snapshot.get("revised_previous") is not None:
            saved = ee.get_event(event["id"])
            report["revised_previous"] = snapshot["revised_previous"]
            saved["post_event_report"] = report
            saved.setdefault("history", []).append({"provider_revision": snapshot["revised_previous"]})
            ee.save_event(saved)
    else:
        ee.save_event(event)
    return ee.get_event(event["id"]) or event


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
    if result.get("forecast") is not None:
        event["forecast"] = result["forecast"]
        event["forecast_type"] = result.get("forecast_type", event.get("forecast_type"))
    if result.get("previous_value") is not None:
        event["previous"] = str(result["previous_value"])
    ee.save_event(event)
    report = ee.record_actual(
        event["id"], str(result["value"]),
        notes="Automatically retrieved from the configured economic data source.",
        details=result,
    )
    if result.get("revised_previous") is not None:
        saved = ee.get_event(event["id"])
        report["revised_previous"] = result["revised_previous"]
        saved["post_event_report"] = report
        saved.setdefault("history", []).append({"provider_revision": result["revised_previous"]})
        ee.save_event(saved)
    return report


async def _send_event_alert(application, event, checkpoint, record, market_chart=None):
    analysis = record["analysis"]
    snapshot = record.get("technical_snapshot") or {}
    scheduled = ee._parse_dt(event["scheduled_at"]).astimezone(WAT)
    market_lines = "\n".join(
        f'{pair}: {bias}' for pair, bias in analysis.get("market_biases", {}).items()
    ) or "No mapped pair bias available."
    technical_text = (
        f'{snapshot.get("market", "Market")} pair: {snapshot.get("direction", "UNAVAILABLE")} '
        f'| {event.get("currency", "USD")} strength: {snapshot.get("currency_direction", "UNAVAILABLE")} '
        f'| ADX {snapshot.get("adx", "N/A")} | RSI {snapshot.get("rsi", "N/A")} '
        f'| ATR {snapshot.get("atr", "N/A")} | breakout {snapshot.get("breakout", "N/A")}'
        if snapshot.get("status") == "AVAILABLE" else
        "Technical confirmation unavailable: " + snapshot.get("reason", "data unavailable")
    )
    news_lines = "\n".join(
        f'• {item.get("title", "Headline")}: {item.get("description", "")[:180]}'
        for item in event.get("research_news", [])[:3]
    )
    yield_lines = "\n".join(
        f'{item.get("country")}: 10Y {item.get("yield_pct")}% '
        f'(daily {item.get("daily_change")}, weekly {item.get("weekly_change")})'
        for item in event.get("bond_yield_context", [])
    )
    text = (
        f'🚨 ECONOMIC EVENT — {checkpoint.upper()} CHECKPOINT\n\n'
        f'{event["title"]}\nEvent time: {scheduled:%d %b %Y, %H:%M} WAT\n'
        f'Currency: {event.get("currency", "USD")}\n'
        f'Forecast ({event.get("forecast_type", "user supplied" if event.get("forecast") else "not supplied")}): {event.get("forecast") or "Not supplied"} | Previous: {event.get("previous") or "Not supplied"}\n\n'
        f'Fundamental direction: {analysis.get("fundamental_direction", analysis["direction"])}\n'
        f'Technical direction: {analysis.get("technical_direction", "NOT PROVIDED")}\n'
        f'Combined forecast: {analysis.get("combined_direction", analysis["direction"])} | Confidence: {analysis["confidence"]}/100\n'
        f'Technical: {technical_text}\n\nAffected market bias:\n{market_lines}\n\n'
        + "\n".join("• " + reason for reason in analysis["reasons"])
        + ("\n\nLATEST ECONOMIC NEWS:\n" + news_lines if news_lines else "")
        + ("\n\n10Y GOVERNMENT BOND YIELDS:\n" + yield_lines if yield_lines else "")
    )
    trade_plan = record.get("trade_plan") or {}
    text += (
        "\n\nTRADE TIMEFRAME & ENTRY PLAN:"
        f"\nDirection: {trade_plan.get('side', 'NO TRADE')} | Status: {trade_plan.get('status', 'NO_TRADE')}"
        f"\nExecution: {trade_plan.get('execution_timeframe', '1m')} | "
        f"Confirmation: {trade_plan.get('confirmation_timeframe', '5m')} | "
        f"Context: {trade_plan.get('context_timeframe', '15m')}"
        f"\n{trade_plan.get('context_note', 'Use 15m for wider structure; automated confirmation is 5m.')}"
        f"\nReason: {trade_plan.get('reason', 'Await the final 15m review.')}"
    )
    if trade_plan.get("status") == "CONDITIONAL_SETUP":
        targets = trade_plan.get("take_profits", [])
        text += (
            f"\nMarket: {trade_plan.get('market')} | Entry trigger: {trade_plan.get('entry_trigger')}"
            f"\nStop-loss: {trade_plan.get('stop_loss')} | "
            f"TP1: {targets[0]} | TP2: {targets[1]} | TP3: {targets[2]}"
            f"\nEntry rule: {trade_plan.get('entry_condition')}"
            f"\nInvalidation: {trade_plan.get('invalidation')}"
            f"\n{trade_plan.get('execution_note')}"
        )
    if checkpoint == "5m":
        if record.get("verification") == "CONFIRMED_UNCHANGED":
            text += "\n\n15-MINUTE FORECAST CONFIRMED — UNCHANGED."
        elif record.get("verification") == "DIRECTION_REVISED":
            text += "\n\nDIRECTION REVISED — NEW MATERIAL EVIDENCE DETECTED."
        elif record.get("verification") == "REVIEWED_MATERIAL_CHANGE":
            text += "\n\nMATERIAL TECHNICAL/RISK CHANGE — COMBINED DIRECTION UNCHANGED; REVIEWED."
        elif record.get("verification") == "NO_15M_BASELINE":
            text += "\n\n5-MINUTE VERIFICATION INCOMPLETE — NO 15-MINUTE BASELINE."
        elif record.get("verification") == "TECHNICAL_ONLY_CONFIRMED":
            text += "\n\nTECHNICAL CHECK UNCHANGED — NO LICENSED GLOBAL NEWS/CONSENSUS FEED CONFIGURED; 15-MINUTE FORECAST PRESERVED."
        elif record.get("verification") == "UNAVAILABLE":
            text += "\n\nFRESH EVENT/MARKET EVIDENCE UNAVAILABLE — ORIGINAL 15-MINUTE FORECAST PRESERVED, NOT CONFIRMED."
    photo = market_chart or ee.render_event_chart(event, analysis)
    recipients = [event["owner_id"]] if event.get("owner_id") else state.get_users()
    for user_id in recipients:
        try:
            if hasattr(photo, "seek"):
                photo.seek(0)
            sent = await application.bot.send_photo(chat_id=int(user_id), photo=photo, caption=text[:1020])
            if len(text) > 1020:
                await application.bot.send_message(chat_id=int(user_id), text=text[1020:4096])
            if sent.photo:
                latest = ee.get_event(event["id"]) or event
                latest.setdefault("chart_images", []).append({
                    "file_id": sent.photo[-1].file_id,
                    "checkpoint": checkpoint,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "market": snapshot.get("market"),
                    "timeframe": snapshot.get("timeframe"),
                    "chart_type": "market_candlestick" if market_chart is not None else "event_summary",
                })
                latest["chart_images"] = latest["chart_images"][-20:]
                ee.save_event(latest)
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
                    await _sync_calendars()
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
                    if te.configured() and event.get("provider_calendar_id"):
                        try:
                            event = await _refresh_provider_event(event)
                            news = await te.fetch_event_news(event, limit=3)
                            if news:
                                event["research_news"] = news
                            if checkpoint in {"24h", "1h"}:
                                yields = await te.fetch_bond_yield_context(event)
                                if yields:
                                    event["bond_yield_context"] = yields
                            event["provider_context_status"] = "AVAILABLE"
                            event["provider_context_checked_at"] = datetime.now(timezone.utc).isoformat()
                            ee.save_event(event)
                        except Exception:
                            event["provider_context_status"] = "UNAVAILABLE"
                            ee.save_event(event)
                            log.exception("Economic event/news/yield refresh unavailable for %s", event.get("title"))
                    else:
                        try:
                            model = await research.fetch_bls_model_forecast(event)
                            if model:
                                if event.get("forecast") is None or event.get("forecast_type") == "model_estimate":
                                    event["forecast"] = str(model["forecast"])
                                    event["previous"] = str(model["previous"])
                                    event["higher_is_better"] = model["higher_is_better"]
                                    event["forecast_type"] = model["forecast_type"]
                                    event["forecast_source"] = model["forecast_source"]
                                event["provider_context_status"] = "OFFICIAL_FALLBACK"
                            else:
                                event["provider_context_status"] = "NOT_CONFIGURED"
                            if te.configured() and checkpoint in {"24h", "1h"}:
                                yields = await te.fetch_bond_yield_context(event)
                                if yields:
                                    event["bond_yield_context"] = yields
                            ee.save_event(event)
                        except Exception:
                            event["provider_context_status"] = "UNAVAILABLE"
                            ee.save_event(event)
                            log.exception("Official fallback event context unavailable for %s", event.get("title"))
                    event = ee.get_event(event["id"]) or event
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
                    if checkpoint in {"15m", "5m"}:
                        if checkpoint == "5m" and record.get("verification") == "NO_15M_BASELINE":
                            record["trade_plan"] = {
                                "status": "NO_TRADE", "execution_timeframe": "1m",
                                "alternative_execution_timeframe": "5m",
                                "confirmation_timeframe": "5m", "context_timeframe": "15m",
                                "reason": "No saved 15-minute final review; the 5-minute check cannot create an independent event trade plan.",
                            }
                        else:
                            record["trade_plan"] = ee.build_event_trade_plan(record["analysis"], snapshot)
                        saved_event = ee.get_event(event["id"]) or event
                        saved_event.setdefault("checkpoints", {})[checkpoint] = record
                        if saved_event.get("history"):
                            saved_event["history"][-1] = record
                        # Start automatic hypothetical trade tracking only after the
                        # 5m verification confirms a qualified conditional setup.
                        if checkpoint == "5m" and record.get("trade_plan", {}).get("status") == "CONDITIONAL_SETUP":
                            plan = record["trade_plan"]
                            if not saved_event.get("event_trade"):
                                saved_event["event_trade"] = {
                                    "status": "WAITING_ENTRY",
                                    "result": "PENDING",
                                    "side": plan.get("side"),
                                    "market": plan.get("market"),
                                    "entry_trigger": plan.get("entry_trigger"),
                                    "stop_loss": plan.get("stop_loss"),
                                    "take_profits": plan.get("take_profits", [])[:3],
                                    "hit_targets": [],
                                    "created_at": datetime.now(timezone.utc).isoformat(),
                                    "source_checkpoint": "5m",
                                    "tracking_type": "AUTOMATIC_MARKET_SIMULATION",
                                    "note": "Waiting for the entry trigger; this is not a broker order.",
                                }
                        ee.save_event(saved_event)
                    await _send_event_alert(application, event, checkpoint, record, market_chart)
            # Monitor qualified event setups from completed 5m candles. This is
            # price-path tracking only: no broker order is opened or closed.
            for tracked in ee.list_events(include_past=True, limit=300, user_id="*"):
                trade = tracked.get("event_trade") or {}
                if trade.get("status") not in {"WAITING_ENTRY", "MONITORING", "WIN"}:
                    continue
                if 3 in trade.get("hit_targets", []):
                    continue
                if ee._parse_dt(tracked["scheduled_at"]) > now:
                    continue
                last_check = trade.get("last_monitor_request_at")
                if last_check:
                    try:
                        if now - ee._parse_dt(last_check) < timedelta(minutes=5):
                            continue
                    except (TypeError, ValueError):
                        pass
                trade["last_monitor_request_at"] = now.isoformat()
                tracked["event_trade"] = trade
                ee.save_event(tracked)
                try:
                    monitor = await research.technical_snapshot(tracked, "15m")
                    monitor.pop("_frame", None)
                    if monitor.get("status") == "AVAILABLE":
                        previous_status = trade.get("status")
                        previous_targets = list(trade.get("hit_targets", []))
                        event_history.update_event_trade_progress(tracked, monitor)
                        updated_trade = tracked.get("event_trade") or trade
                        updated_trade["last_monitor_request_at"] = now.isoformat()
                        ee.save_event(tracked)
                        if (updated_trade.get("status"), updated_trade.get("hit_targets")) != (previous_status, previous_targets):
                            log.info(
                                "Economic event trade progress %s: %s %s",
                                tracked.get("title"), updated_trade.get("status"),
                                updated_trade.get("hit_targets"),
                            )
                except Exception:
                    log.exception("Economic event trade monitoring failed for %s", tracked.get("id"))
        except Exception:
            log.exception("Economic event scheduler iteration failed")
        await asyncio.sleep(20)


async def show_my_telegram_id(update, context):
    await update.effective_message.reply_text(
        f"Your Telegram user ID is: {update.effective_user.id}\n"
        "Set BOT_OWNER_ID to this value in the host environment only if you need to manually edit shared official event records."
    )


def register_event_handlers(application):
    application.add_handler(CommandHandler("events", show_event_center))
    application.add_handler(CommandHandler("myid", show_my_telegram_id))
