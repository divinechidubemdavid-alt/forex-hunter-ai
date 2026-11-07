"""Economic-event intelligence core.

Calendar ingestion is deliberately source-driven: configure ECONOMIC_CALENDAR_ICS_URL
with a calendar feed whose terms permit the intended use. No event dates or consensus
figures are fabricated. If consensus is absent, model forecasts must be labelled as such.
"""
from __future__ import annotations

import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx

import state

WAT = ZoneInfo("Africa/Lagos")
CHECKPOINTS = (("24h", timedelta(hours=24)), ("1h", timedelta(hours=1)),
               ("15m", timedelta(minutes=15)), ("5m", timedelta(minutes=5)))
HIGH_IMPACT_TERMS = (
    "nonfarm", "payroll", "cpi", "consumer price", "fomc", "federal funds",
    "interest rate", "unemployment", "pce", "gdp", "retail sales",
    "jobless claims", "average hourly earnings", "pmi", "powell",
)


def _now():
    return datetime.now(timezone.utc)


def _parse_dt(value):
    if isinstance(value, datetime):
        result = value
    else:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def is_high_impact(title):
    text = str(title).lower()
    return any(term in text for term in HIGH_IMPACT_TERMS)


def create_event(title, scheduled_at, currency="USD", impact="high",
                 source="user-entered", forecast=None, previous=None,
                 actual=None, notes="", owner_id=None):
    title = str(title).strip()
    if not title:
        raise ValueError("Event title is required.")
    when = _parse_dt(scheduled_at)
    if when < _now() - timedelta(days=2):
        raise ValueError("Event date is too far in the past.")
    event = {
        "id": uuid.uuid4().hex[:12], "title": title,
        "scheduled_at": when.isoformat(), "currency": str(currency).upper(),
        "impact": str(impact).lower(), "source": str(source),
        "forecast": forecast, "previous": previous, "actual": actual,
        "notes": str(notes), "owner_id": str(owner_id) if owner_id is not None else None,
        "status": "scheduled", "checkpoints": {},
        "created_at": _now().isoformat(), "history": [],
    }
    events = state.get_scanner_state("economic_events", []) or []
    events.append(event)
    state.set_scanner_state("economic_events", events[-300:])
    return event


def list_events(include_past=False, limit=30, user_id=None):
    events = state.get_scanner_state("economic_events", []) or []
    now = _now()
    rows = [e for e in events
            if (include_past or _parse_dt(e["scheduled_at"]) >= now - timedelta(hours=2))
            and (e.get("owner_id") is None or str(e.get("owner_id")) == str(user_id))]
    return sorted(rows, key=lambda e: e["scheduled_at"])[:max(1, int(limit))]


def get_event(event_id):
    return next((e for e in (state.get_scanner_state("economic_events", []) or [])
                 if e.get("id") == str(event_id)), None)


def save_event(event):
    events = state.get_scanner_state("economic_events", []) or []
    for i, old in enumerate(events):
        if old.get("id") == event.get("id"):
            events[i] = event
            state.set_scanner_state("economic_events", events)
            return event
    raise KeyError("Economic event not found.")


def directional_analysis(event, technical=None, phase="pre-event"):
    """Transparent rules-based directional bias; never represents a guess as consensus."""
    forecast = event.get("forecast")
    actual = event.get("actual")
    previous = event.get("previous")
    basis = actual if phase == "post-event" and actual is not None else forecast
    comparator = (forecast if forecast is not None else previous) if phase == "post-event" else previous
    currency = event.get("currency", "USD").upper()
    title = event.get("title", "Economic event")
    direction, confidence, reasons = "NEUTRAL", 35, []
    if basis is None:
        reasons.append("No forecast or actual figure supplied; directional bias remains neutral.")
    else:
        try:
            value = float(basis)
            prior = float(comparator) if comparator is not None else None
            # Generic numeric releases cannot be interpreted without knowing whether
            # higher or lower is favourable. Require explicit event metadata.
            favourable = event.get("higher_is_better")
            if prior is not None and favourable is not None:
                positive = (value > prior) == bool(favourable)
                direction = "BULLISH" if positive else "BEARISH"
                confidence = 55
                reference_label = 'forecast' if phase == 'post-event' else 'previous'
                reasons.append(f"{'Higher' if value > prior else 'Lower'} reading versus {reference_label} ({comparator}); event preference metadata applied.")
            else:
                reasons.append("Figure recorded, but its favourable direction is not defined; no directional assumption made.")
        except (TypeError, ValueError):
            reasons.append("Release figure is not numeric; no directional assumption made.")
    if technical in ("BULLISH", "BEARISH"):
        if direction == "NEUTRAL":
            reasons.append(f"Technical context is {technical}, but event evidence is insufficient.")
        elif technical == direction:
            confidence = min(85, confidence + 10)
            reasons.append("Technical context aligns with the event-derived bias.")
        else:
            confidence = max(20, confidence - 15)
            reasons.append(f"Technical context conflicts ({technical}); confidence reduced, not blocked.")
    return {
        "currency": currency, "direction": direction, "confidence": confidence,
        "affected_markets": {
            "USD": ["EUR/USD", "GBP/USD", "USD/JPY", "AUD/USD", "USD/CAD", "USD/CHF", "XAU/USD"],
            "EUR": ["EUR/USD"], "GBP": ["GBP/USD"], "JPY": ["USD/JPY"],
            "AUD": ["AUD/USD"], "CAD": ["USD/CAD"], "CHF": ["USD/CHF"],
        }.get(currency, []),
        "phase": phase, "basis": "actual" if phase == "post-event" and actual is not None else ("forecast" if forecast is not None else "none"),
        "reasons": reasons, "technical_direction": technical or "NOT PROVIDED",
        "disclaimer": "Directional research, not a confirmed trade entry or guaranteed outcome.",
    }


def record_checkpoint(event_id, checkpoint, technical=None, evidence_note="", technical_snapshot=None):
    if checkpoint not in dict(CHECKPOINTS):
        raise ValueError("Unsupported event checkpoint.")
    event = get_event(event_id)
    if not event:
        raise KeyError("Economic event not found.")
    analysis = directional_analysis(event, technical, "pre-event")
    record = {
        "checkpoint": checkpoint, "created_at": _now().isoformat(),
        "analysis": analysis, "evidence_note": str(evidence_note),
        "technical_snapshot": technical_snapshot or {},
    }
    if checkpoint == "15m":
        event["final_15m"] = record
    elif checkpoint == "5m":
        baseline = event.get("final_15m")
        if not baseline:
            record["verification"] = "NO_15M_BASELINE"
        else:
            from economic_research import material_change
            changed, changes = material_change(
                baseline.get("technical_snapshot") or {},
                technical_snapshot or {},
            )
            record["prior_analysis"] = baseline["analysis"]
            record["material_changes"] = changes
            if not changed:
                record["analysis"] = baseline["analysis"]
                record["verification"] = "CONFIRMED_UNCHANGED"
            else:
                record["verification"] = "DIRECTION_REVIEWED"
                record["analysis"] = directional_analysis(
                    event, technical=technical, phase="pre-event"
                )
                record["analysis"]["reasons"].append(
                    "Material 5-minute evidence: " + "; ".join(changes)
                )
                record["analysis"]["confidence"] = min(
                    record["analysis"]["confidence"], baseline["analysis"]["confidence"]
                )
    event.setdefault("checkpoints", {})[checkpoint] = record
    event.setdefault("history", []).append(record)
    save_event(event)
    return record


def record_actual(event_id, actual, revised_previous=None, notes="", details=None):
    event = get_event(event_id)
    if not event:
        raise KeyError("Economic event not found.")
    event["actual"] = actual
    if revised_previous is not None:
        event["previous"] = revised_previous
    event["status"] = "released"
    report = {
        "created_at": _now().isoformat(), "actual": actual,
        "forecast": event.get("forecast"), "previous": event.get("previous"),
        "revised_previous": revised_previous, "notes": str(notes),
        "actual_details": details or {},
        "analysis": directional_analysis(event, phase="post-event"),
    }
    event["post_event_report"] = report
    event.setdefault("history", []).append({"post_event_report": report})
    save_event(event)
    return report


async def fetch_ics_events(url=None):
    """Import VEVENT entries from a configured, rights-cleared ICS calendar feed."""
    feed_url = url or os.getenv(
        "ECONOMIC_CALENDAR_ICS_URL",
        "https://www.bls.gov/schedule/news_release/bls.ics",
    ).strip()
    if not feed_url:
        return []
    if not feed_url.startswith("https://"):
        raise ValueError("Economic calendar feed must use HTTPS.")
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        response = await client.get(feed_url)
        response.raise_for_status()
    text = response.text.replace("\r\n ", "").replace("\n ", "")
    blocks = re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", text, re.S)
    imported = []
    for block in blocks:
        fields = {}
        for line in block.splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            field_name = key.split(";", 1)[0]
            fields[field_name] = value.strip()
            if field_name == "DTSTART" and "TZID=" in key:
                fields["DTSTART_TZID"] = key.split("TZID=", 1)[1].split(";", 1)[0]
        title = fields.get("SUMMARY", "").strip()
        raw = fields.get("DTSTART", "")
        if not title or not raw or not is_high_impact(title):
            continue
        try:
            if re.fullmatch(r"\d{8}T\d{6}Z", raw):
                when = datetime.strptime(raw, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
            elif re.fullmatch(r"\d{8}T\d{6}", raw):
                zone_name = fields.get("DTSTART_TZID", "UTC")
                when = datetime.strptime(raw, "%Y%m%dT%H%M%S").replace(
                    tzinfo=ZoneInfo(zone_name)
                ).astimezone(timezone.utc)
            else:
                when = datetime.strptime(raw, "%Y%m%d").replace(tzinfo=timezone.utc)
            key = (title.casefold(), when.isoformat())
            existing = {(e["title"].casefold(), e["scheduled_at"]) for e in list_events(True, 300)}
            if key not in existing:
                imported.append(create_event(title, when, currency="USD", source=feed_url))
        except ValueError:
            continue
    return imported


def due_checkpoints(event, now=None):
    now = _parse_dt(now or _now())
    scheduled = _parse_dt(event["scheduled_at"])
    due = []
    for label, offset in CHECKPOINTS:
        target = scheduled - offset
        if target <= now < target + timedelta(minutes=1) and label not in event.get("checkpoints", {}):
            due.append(label)
    return due


def render_event_chart(event, analysis):
    """Return a small event-specific evidence card suitable for Telegram photo delivery."""
    from io import BytesIO
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(10, 5), facecolor="#101613")
    ax.set_facecolor("#101613")
    ax.axis("off")
    ax.text(.04, .88, "FOREX HUNTER AI • ECONOMIC INTELLIGENCE",
            color="#D6AD45", fontsize=11, weight="bold", transform=ax.transAxes)
    ax.text(.04, .68, event["title"][:75], color="white", fontsize=19,
            weight="bold", transform=ax.transAxes)
    ax.text(.04, .47, f'{analysis["currency"]}  |  {analysis["direction"]}',
            color="#39FF88" if analysis["direction"] == "BULLISH" else "#FF5263" if analysis["direction"] == "BEARISH" else "#D6AD45",
            fontsize=23, weight="bold", transform=ax.transAxes)
    ax.text(.04, .29, f'Confidence: {analysis["confidence"]}/100  •  {analysis["phase"]}',
            color="white", fontsize=12, transform=ax.transAxes)
    ax.text(.04, .10, f'Scheduled: {_parse_dt(event["scheduled_at"]).astimezone(WAT):%d %b %Y, %H:%M} WAT',
            color="#AAB7AE", fontsize=10, transform=ax.transAxes)
    out = BytesIO()
    fig.savefig(out, format="png", dpi=130, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    out.seek(0)
    out.name = "economic_event.png"
    return out
