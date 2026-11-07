"""Economic-event intelligence core.

Calendar ingestion is deliberately source-driven: configure ECONOMIC_CALENDAR_ICS_URL
with a calendar feed whose terms permit the intended use. No event dates or consensus
figures are fabricated. If consensus is absent, model forecasts must be labelled as such.
"""
from __future__ import annotations

import os
import re
import html
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx

import state

WAT = ZoneInfo("Africa/Lagos")
CHECKPOINTS = (("24h", timedelta(hours=24)), ("1h", timedelta(hours=1)),
               ("15m", timedelta(minutes=15)), ("5m", timedelta(minutes=5)))
HIGH_IMPACT_TERMS = (
    "nonfarm", "payroll", "employment situation", "job openings",
    "producer price index", "cpi", "consumer price", "fomc", "federal funds",
    "interest rate", "unemployment rate", "pce", "gdp", "retail sales",
    "jobless claims", "average hourly earnings", "pmi", "powell",
    "personal income and outlays", "personal consumption expenditures",
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
    if "state employment and unemployment" in text:
        return False
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
            and (user_id == "*" or e.get("owner_id") is None or str(e.get("owner_id")) == str(user_id))]
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


def _to_number(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "").upper()
    match = re.search(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)\s*([KMBT]?)", text)
    if not match:
        return None
    number = float(match.group(0).rstrip("KMBT").strip())
    suffix = match.group(1)
    scale = {"": 1, "K": 1_000, "M": 1_000_000, "B": 1_000_000_000, "T": 1_000_000_000_000}[suffix]
    return number * scale


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
            value = _to_number(basis)
            prior = _to_number(comparator) if comparator is not None else None
            if value is None:
                raise ValueError("Release value is not numeric.")
            # Generic numeric releases cannot be interpreted without knowing whether
            # higher or lower is favourable. Require explicit event metadata.
            favourable = event.get("higher_is_better")
            if prior is not None and favourable is not None:
                reference_label = 'forecast' if phase == 'post-event' and forecast is not None else 'previous'
                if value == prior:
                    direction = "NEUTRAL"
                    reasons.append(f"Reading unchanged versus {reference_label} ({comparator}); no directional surprise.")
                else:
                    positive = (value > prior) == bool(favourable)
                    direction = "BULLISH" if positive else "BEARISH"
                    confidence = 55
                    reasons.append(f"{'Higher' if value > prior else 'Lower'} reading versus {reference_label} ({comparator}); event preference metadata applied.")
            else:
                reasons.append("Figure recorded, but its favourable direction is not defined; no directional assumption made.")
        except (TypeError, ValueError):
            reasons.append("Release figure is not numeric; no directional assumption made.")
    if phase == "post-event":
        companion_directions = []
        for metric in (event.get("actual_details") or {}).get("metrics", []):
            metric_value = _to_number(metric.get("value"))
            metric_previous = _to_number(metric.get("previous_value"))
            favourable = metric.get("higher_is_better")
            if metric_value is None or metric_previous is None or favourable is None:
                continue
            if metric_value == metric_previous:
                metric_direction = "NEUTRAL"
            else:
                metric_positive = (metric_value > metric_previous) == bool(favourable)
                metric_direction = "BULLISH" if metric_positive else "BEARISH"
            companion_directions.append(metric_direction)
            reasons.append(
                f'Companion {metric.get("name", "indicator")}: {metric.get("value")} '
                f'versus {metric.get("previous_value")} ({metric_direction.lower()} for {currency}).'
            )
        directional_companions = [item for item in companion_directions if item in {"BULLISH", "BEARISH"}]
        if direction in {"BULLISH", "BEARISH"} and directional_companions:
            if any(item != direction for item in directional_companions):
                confidence = max(20, confidence - 8)
                reasons.append("Mixed companion labour data reduced confidence in the primary release direction.")
            elif len(directional_companions) >= 2:
                confidence = min(90, confidence + 5)
                reasons.append("Companion labour indicators support the primary release direction.")

    fundamental_direction = direction
    if technical in ("BULLISH", "BEARISH"):
        if fundamental_direction == "NEUTRAL":
            direction = technical
            confidence = 25
            reasons.append(
                f"Fundamental event evidence is insufficient; {technical} is shown as a low-confidence technical-only bias."
            )
        elif technical == fundamental_direction:
            confidence = min(85, confidence + 10)
            reasons.append("Technical context aligns with the fundamental event-derived bias.")
        else:
            confidence = max(20, confidence - 15)
            reasons.append(f"Technical context conflicts ({technical}); fundamental direction is retained and confidence reduced, not blocked.")
    else:
        direction = fundamental_direction
    combined_direction = direction
    pairs = {
        "USD": ["EUR/USD", "GBP/USD", "USD/JPY", "AUD/USD", "USD/CAD", "USD/CHF", "XAU/USD"],
        "EUR": ["EUR/USD"], "GBP": ["GBP/USD"], "JPY": ["USD/JPY"],
        "AUD": ["AUD/USD"], "CAD": ["USD/CAD"], "CHF": ["USD/CHF"],
    }.get(currency, [])
    market_biases = {}
    for pair in pairs:
        base, quote = pair.split("/")
        if currency == base:
            pair_direction = direction
        elif currency == quote:
            pair_direction = {"BULLISH": "BEARISH", "BEARISH": "BULLISH"}.get(direction, direction)
        else:
            pair_direction = "NEUTRAL"
        market_biases[pair] = (
            "BUY" if pair_direction == "BULLISH" else
            "SELL" if pair_direction == "BEARISH" else "NEUTRAL"
        )
    return {
        "currency": currency, "direction": combined_direction,
        "fundamental_direction": fundamental_direction,
        "combined_direction": combined_direction,
        "confidence": confidence,
        "affected_markets": pairs, "market_biases": market_biases,
        "phase": phase, "basis": "actual" if phase == "post-event" and actual is not None else ("forecast" if forecast is not None else "none"),
        "reasons": reasons, "technical_direction": technical or "NOT PROVIDED",
        "disclaimer": "Directional research, not a confirmed trade entry or guaranteed outcome.",
    }


def _event_evidence(event):
    return {
        "title": event.get("title"),
        "currency": event.get("currency"),
        "scheduled_at": event.get("scheduled_at"),
        "forecast": event.get("forecast"),
        "forecast_type": event.get("forecast_type"),
        "previous": event.get("previous"),
        "actual": event.get("actual"),
        "provider_context_status": event.get("provider_context_status", "NOT_CONFIGURED"),
        "news_titles": sorted({
            str(item.get("title", "")).strip()
            for item in event.get("research_news", [])
            if item.get("title")
        }),
    }


def _material_event_change(previous, current):
    changes = []
    if not previous:
        return changes
    if previous.get("title") != current.get("title") or previous.get("currency") != current.get("currency"):
        changes.append("event identity/details changed")
    if previous.get("scheduled_at") != current.get("scheduled_at"):
        changes.append("event schedule details changed")
    if previous.get("actual") is None and current.get("actual") is not None:
        changes.append("actual release figure became available")
    if (
        previous.get("provider_context_status") != current.get("provider_context_status")
        and current.get("provider_context_status") in {"AVAILABLE", "OFFICIAL_FALLBACK"}
    ):
        changes.append("fresh economic/news context became available")
    if previous.get("previous") != current.get("previous"):
        changes.append("previous-period figure or revision changed")
    old_forecast = previous.get("forecast")
    new_forecast = current.get("forecast")
    if old_forecast != new_forecast:
        old_number, new_number = _to_number(old_forecast), _to_number(new_forecast)
        if old_number is None or new_number is None:
            if old_forecast != new_forecast:
                changes.append("forecast value/source changed")
        elif abs(new_number - old_number) >= max(0.01, abs(old_number) * 0.002):
            changes.append("consensus/model forecast changed materially")
    old_news = set(previous.get("news_titles") or [])
    new_news = set(current.get("news_titles") or [])
    if new_news - old_news:
        changes.append("new provider economic-news headline(s) detected")
    return changes


def record_checkpoint(event_id, checkpoint, technical=None, evidence_note="", technical_snapshot=None, event_evidence=None):
    if checkpoint not in dict(CHECKPOINTS):
        raise ValueError("Unsupported event checkpoint.")
    event = get_event(event_id)
    if not event:
        raise KeyError("Economic event not found.")
    phase = "post-event" if event.get("actual") is not None else "pre-event"
    analysis = directional_analysis(event, technical, phase)
    current_event_evidence = event_evidence or _event_evidence(event)
    record = {
        "checkpoint": checkpoint, "created_at": _now().isoformat(),
        "analysis": analysis, "evidence_note": str(evidence_note),
        "technical_snapshot": technical_snapshot or {},
        "event_evidence": current_event_evidence,
    }
    if checkpoint == "15m":
        event["final_15m"] = record
    elif checkpoint == "5m":
        baseline = event.get("final_15m")
        if not baseline:
            record["verification"] = "NO_15M_BASELINE"
        else:
            record["prior_analysis"] = baseline["analysis"]
            event_changes = _material_event_change(
                baseline.get("event_evidence") or {},
                current_event_evidence,
            )
            baseline_technical = baseline.get("technical_snapshot") or {}
            current_technical = technical_snapshot or {}
            technical_available = (
                baseline_technical.get("status") == "AVAILABLE"
                and current_technical.get("status") == "AVAILABLE"
            )
            technical_changes = []
            if technical_available:
                from economic_research import material_change
                _, technical_changes = material_change(baseline_technical, current_technical)
            changes = event_changes + technical_changes
            record["material_changes"] = changes
            provider_status = current_event_evidence.get("provider_context_status", "NOT_CONFIGURED")
            if (
                (not technical_available or provider_status == "UNAVAILABLE")
                and not event_changes
            ):
                record["analysis"] = baseline["analysis"]
                record["verification"] = "UNAVAILABLE"
            elif not changes:
                record["analysis"] = baseline["analysis"]
                record["verification"] = (
                    "TECHNICAL_ONLY_CONFIRMED"
                    if provider_status == "NOT_CONFIGURED"
                    else "CONFIRMED_UNCHANGED"
                )
            else:
                record["analysis"] = directional_analysis(event, technical=technical, phase=phase)
                old_direction = baseline["analysis"].get(
                    "combined_direction", baseline["analysis"].get("direction")
                )
                new_direction = record["analysis"].get(
                    "combined_direction", record["analysis"].get("direction")
                )
                record["verification"] = (
                    "DIRECTION_REVISED" if new_direction != old_direction
                    else "REVIEWED_MATERIAL_CHANGE"
                )
                record["analysis"]["reasons"].append(
                    "Material 5-minute evidence: " + "; ".join(changes)
                )
                if not technical_available:
                    record["analysis"]["reasons"].append(
                        "Technical verification unavailable at this checkpoint; event-data change was reviewed separately."
                    )
                if any("volatility" in item for item in changes):
                    record["analysis"]["confidence"] = max(
                        20, record["analysis"]["confidence"] - 10
                    )
                    record["analysis"]["reasons"].append(
                        "Volatility expansion increases event risk; avoid chasing the initial spike."
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
    original_previous = event.get("previous")
    if revised_previous is not None:
        event["previous"] = revised_previous
    event["status"] = "released"
    report = {
        "created_at": _now().isoformat(), "actual": actual,
        "forecast": event.get("forecast"), "forecast_type": event.get("forecast_type", "market_consensus" if event.get("forecast") is not None else None),
        "previous": original_previous,
        "revised_previous": revised_previous, "notes": str(notes),
        "actual_details": details or {},
        "analysis": directional_analysis(event, phase="post-event"),
    }
    report["forecast_score"] = (
        "AWAITING_MARKET_REACTION"
        if event.get("forecast") is not None and report["analysis"]["direction"] in {"BULLISH", "BEARISH"}
        else "NO_CONSENSUS_FORECAST" if event.get("forecast") is None
        else "UNSCORED"
    )
    event["post_event_report"] = report
    event.setdefault("history", []).append({"post_event_report": report})
    save_event(event)
    return report


def record_market_reaction(event_id, snapshot, minutes_after):
    event = get_event(event_id)
    if not event or not event.get("post_event_report"):
        raise KeyError("Post-event report not found.")
    report = event["post_event_report"]
    reaction = {
        "minutes_after": int(minutes_after),
        "observed_at": _now().isoformat(),
        "market": snapshot.get("market"),
        "direction": snapshot.get("currency_direction", "UNAVAILABLE"),
        "pair_direction": snapshot.get("direction", "UNAVAILABLE"),
        "adx": snapshot.get("adx"),
        "rsi": snapshot.get("rsi"),
        "volatility_pct": snapshot.get("volatility_pct"),
        "breakout": snapshot.get("breakout"),
        "source": snapshot.get("source", "unavailable"),
    }
    report.setdefault("market_reactions", []).append(reaction)
    fundamental = report.get("analysis", {}).get("direction")
    observed = reaction["direction"]
    if fundamental in {"BULLISH", "BEARISH"} and observed in {"BULLISH", "BEARISH"}:
        if report.get("forecast") is None:
            report["forecast_score"] = "DIRECTIONAL_ALIGNMENT_ONLY" if fundamental == observed else "DIRECTIONAL_CONFLICT"
        elif report.get("forecast_type") in {"model_estimate", "provider_model_estimate"}:
            report["forecast_score"] = "MODEL_ESTIMATE_ALIGNED" if fundamental == observed else "MODEL_ESTIMATE_NOT_ALIGNED"
        else:
            report["forecast_score"] = "ALIGNED" if fundamental == observed else "NOT_ALIGNED"
        report["forecast_score_value"] = 1 if fundamental == observed else 0
    else:
        report["forecast_score"] = "UNSCORED"
        report["forecast_score_value"] = None
    event["post_event_report"] = report
    event.setdefault("history", []).append({"market_reaction": reaction, "forecast_score": report["forecast_score"]})
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


async def fetch_fomc_events():
    """Import scheduled FOMC decision dates from the Federal Reserve's official calendar."""
    url = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        response = await client.get(url)
        response.raise_for_status()
    page = html.unescape(response.text)
    imported = []
    sections = [
        match.group(0) for match in re.finditer(
            r"<h[1-6][^>]*>\s*20\d{2} FOMC Meetings.*?(?=<h[1-6][^>]*>\s*20\d{2} FOMC Meetings|$)",
            page, re.I | re.S,
        )
    ]
    month_numbers = {
        "january": 1, "february": 2, "march": 3, "april": 4,
        "may": 5, "june": 6, "july": 7, "august": 8,
        "september": 9, "october": 10, "november": 11, "december": 12,
    }
    existing = {
        (event["title"].casefold(), event["scheduled_at"])
        for event in list_events(True, 300)
    }
    for section in sections:
        year_text = re.search(r"<h[1-6][^>]*>\s*(20\d{2}) FOMC Meetings", section, re.I)
        if not year_text:
            continue
        year = int(year_text.group(1))
        text = re.sub(r"<[^>]+>", " ", section)
        text = re.sub(r"\s+", " ", html.unescape(text))
        pattern = r"\b(" + "|".join(month_numbers) + r")\s+(\d{1,2})\s*[-–]\s*(\d{1,2})\b"
        for match in re.finditer(pattern, text, re.I):
            month = month_numbers[match.group(1).lower()]
            decision_day = int(match.group(3))
            try:
                local = datetime(year, month, decision_day, 14, 0, tzinfo=ZoneInfo("America/New_York"))
            except ValueError:
                continue
            title = "FOMC Rate Decision and Policy Statement"
            when = local.astimezone(timezone.utc)
            key = (title.casefold(), when.isoformat())
            if key not in existing and when >= _now() - timedelta(days=2):
                imported.append(create_event(
                    title, when, currency="USD", impact="high",
                    source="Federal Reserve official FOMC calendar",
                ))
                existing.add(key)
    return imported


async def fetch_bea_events():
    """Import GDP and Personal Income/Outlays dates from the official BEA schedule."""
    url = "https://www.bea.gov/news/schedule/full"
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        response = await client.get(url)
        response.raise_for_status()
    page = response.text
    year = _now().year
    imported = []
    existing = {
        (event["title"].casefold(), event["scheduled_at"])
        for event in list_events(True, 300)
    }
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", page, re.I | re.S):
        cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.I | re.S)
        if len(cells) < 3:
            continue
        values = [
            re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", cell))).strip()
            for cell in cells
        ]
        date_match = re.search(
            r"\b(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+(\d{1,2})\b",
            values[0], re.I,
        )
        time_match = re.search(r"\b(\d{1,2}:\d{2})\s*(AM|PM)\b", values[0], re.I)
        title = values[2]
        if not date_match or not time_match or not is_high_impact(title):
            continue
        month_text = date_match.group(1).lower()[:3]
        month = {
            "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
            "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
        }[month_text]
        hour, minute = map(int, time_match.group(1).split(":"))
        if time_match.group(2).upper() == "PM" and hour != 12:
            hour += 12
        elif time_match.group(2).upper() == "AM" and hour == 12:
            hour = 0
        try:
            local = datetime(year, month, int(date_match.group(2)), hour, minute,
                             tzinfo=ZoneInfo("America/New_York"))
        except ValueError:
            continue
        when = local.astimezone(timezone.utc)
        key = (title.casefold(), when.isoformat())
        if when >= _now() - timedelta(days=2) and key not in existing:
            imported.append(create_event(
                title, when, currency="USD", impact="high",
                source="U.S. Bureau of Economic Analysis official release schedule",
            ))
            existing.add(key)
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
