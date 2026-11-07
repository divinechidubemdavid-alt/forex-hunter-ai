"""Optional licensed Trading Economics adapter.

Enable only with an API key and a plan/contract that permits redistribution to
the bot's Telegram users. Without a key, the bot uses its official-source
fallbacks and never fabricates consensus values.
"""
from __future__ import annotations

import os
import asyncio
import time
from datetime import datetime, timedelta, timezone

import httpx

import economic_events as ee

BASE_URL = "https://api.tradingeconomics.com"
_REQUEST_LOCK = asyncio.Lock()
_LAST_REQUEST_AT = 0.0
COUNTRY_CURRENCY = {
    "united states": "USD", "euro area": "EUR", "germany": "EUR",
    "france": "EUR", "italy": "EUR", "spain": "EUR",
    "united kingdom": "GBP", "japan": "JPY", "australia": "AUD",
    "canada": "CAD", "switzerland": "CHF", "new zealand": "NZD",
    "china": "CNY",
}


def configured() -> bool:
    key_present = bool(os.getenv("TRADING_ECONOMICS_API_KEY", "").strip())
    distribution_allowed = os.getenv("TRADING_ECONOMICS_DISTRIBUTION_LICENSED", "").strip().lower() in {"1", "true", "yes"}
    return key_present and distribution_allowed


def _key() -> str:
    value = os.getenv("TRADING_ECONOMICS_API_KEY", "").strip()
    if not value:
        raise RuntimeError("TRADING_ECONOMICS_API_KEY is not configured.")
    return value


def _dt(value: str) -> datetime:
    result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _higher_is_better(title: str):
    name = title.lower()
    lower_is_better = (
        "inflation", "consumer price", "cpi", "pce price", "ppi",
        "unemployment", "jobless claims", "initial claims",
    )
    higher_is_better = (
        "non farm payroll", "nonfarm payroll", "employment change",
        "employment situation", "retail sales", "gdp", "pmi",
        "hourly earnings", "wage", "consumer confidence", "industrial production",
        "interest rate decision", "federal funds rate", "policy rate",
    )
    if any(term in name for term in lower_is_better):
        return False
    if any(term in name for term in higher_is_better):
        return True
    return None


def _consensus(row):
    consensus = str(row.get("Forecast") or "").strip()
    if consensus:
        return consensus, "market_consensus"
    provider = str(row.get("TEForecast") or "").strip()
    if provider:
        return provider, "provider_model_estimate"
    return None, "not_supplied"


async def _get(path: str, params=None):
    global _LAST_REQUEST_AT
    query = dict(params or {})
    query["c"] = _key()
    query["f"] = "json"
    async with _REQUEST_LOCK:
        delay = 0.55 - (time.monotonic() - _LAST_REQUEST_AT)
        if delay > 0:
            await asyncio.sleep(delay)
        async with httpx.AsyncClient(timeout=25, follow_redirects=True) as client:
            response = await client.get(f"{BASE_URL}{path}", params=query)
            response.raise_for_status()
            data = response.json()
        _LAST_REQUEST_AT = time.monotonic()
    if isinstance(data, dict) and data.get("error"):
        raise RuntimeError(str(data["error"]))
    return data


async def fetch_calendar_events(days_ahead: int = 30) -> list[dict]:
    """Import high-impact global events and enrich matching official calendar rows."""
    now = datetime.now(timezone.utc)
    start = (now - timedelta(hours=2)).date().isoformat()
    end = (now + timedelta(days=max(1, min(int(days_ahead), 45)))).date().isoformat()
    rows = await _get(
        f"/calendar/country/All/{start}/{end}",
        {"importance": 3, "values": "true"},
    )
    if not isinstance(rows, list):
        raise RuntimeError("Trading Economics calendar response was not a list.")
    events = ee.list_events(include_past=True, limit=300, user_id="*")
    imported = []
    for row in rows:
        country = str(row.get("Country") or "").strip()
        currency = COUNTRY_CURRENCY.get(country.lower())
        title = str(row.get("Event") or row.get("Category") or "").strip()
        if not currency or not title or not ee.is_high_impact(title):
            continue
        try:
            when = _dt(row["Date"])
        except (KeyError, TypeError, ValueError):
            continue
        calendar_id = str(row.get("CalendarId") or "")
        existing = next(
            (item for item in events if calendar_id and item.get("provider_calendar_id") == calendar_id),
            None,
        )
        if existing is None:
            existing = next(
                (
                    item for item in events
                    if not item.get("owner_id")
                    and item.get("currency") == currency
                    and abs((ee._parse_dt(item["scheduled_at"]) - when).total_seconds()) <= 60
                ),
                None,
            )
        forecast, forecast_type = _consensus(row)
        previous = str(row.get("Previous") or "").strip() or None
        revised = str(row.get("Revised") or "").strip() or None
        if existing is None:
            existing = ee.create_event(
                title, when, currency=currency, impact="high",
                source=str(row.get("Source") or "Trading Economics"),
                forecast=forecast, previous=previous,
            )
            imported.append(existing)
            events.append(existing)
        existing.update({
            "provider_calendar_id": calendar_id,
            "provider_country": country,
            "provider_category": row.get("Category"),
            "provider_source": row.get("Source"),
            "provider_source_url": row.get("SourceURL"),
            "provider_revised_previous": revised,
            "provider_date_precision": row.get("DateSpan"),
            "provider_importance": row.get("Importance"),
            "forecast": forecast if forecast is not None else existing.get("forecast"),
            "forecast_type": forecast_type if forecast is not None else existing.get("forecast_type", "not_supplied"),
            "forecast_source": "Trading Economics representative economist consensus" if forecast_type == "market_consensus" else "Trading Economics model estimate" if forecast_type == "provider_model_estimate" else None,
            "previous": previous if previous is not None else existing.get("previous"),
            "higher_is_better": _higher_is_better(title),
        })
        ee.save_event(existing)
        actual = str(row.get("Actual") or "").strip()
        if actual and not existing.get("post_event_report"):
            ee.record_actual(
                existing["id"], actual,
                notes="Actual retrieved from the configured Trading Economics calendar.",
                details={
                    "source": "Trading Economics calendar; source provider: " + str(row.get("Source") or "not specified"),
                    "source_url": row.get("SourceURL"),
                    "provider": "Trading Economics",
                    "calendar_id": calendar_id,
                },
            )
            if revised is not None:
                saved = ee.get_event(existing["id"])
                saved["post_event_report"]["revised_previous"] = revised
                saved.setdefault("history", []).append({"provider_revision": revised})
                ee.save_event(saved)
    return imported


async def fetch_event_actual(event: dict) -> dict | None:
    calendar_id = str(event.get("provider_calendar_id") or "")
    if not configured() or not calendar_id:
        return None
    rows = await _get(f"/calendar/calendarid/{calendar_id}")
    if isinstance(rows, dict):
        rows = [rows]
    if not rows:
        return None
    row = rows[0]
    actual = str(row.get("Actual") or "").strip()
    if not actual:
        return None
    consensus, forecast_type = _consensus(row)
    return {
        "value": actual,
        "previous_value": str(row.get("Previous") or "").strip() or None,
        "revised_previous": str(row.get("Revised") or "").strip() or None,
        "forecast": consensus,
        "forecast_type": forecast_type,
        "higher_is_better": _higher_is_better(str(row.get("Event") or event.get("title", ""))),
        "source": "Trading Economics calendar; official source: " + str(row.get("Source") or "not specified"),
        "source_url": row.get("SourceURL"),
        "provider": "Trading Economics",
        "calendar_id": calendar_id,
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
    }


async def fetch_event_news(event: dict, limit: int = 3) -> list[dict]:
    if not configured():
        return []
    country = str(event.get("provider_country") or "").strip()
    if not country:
        return []
    rows = await _get(f"/news/country/{country}")
    if not isinstance(rows, list):
        return []
    result = []
    for row in rows[:max(1, min(int(limit), 5))]:
        result.append({
            "title": str(row.get("title") or "").strip(),
            "description": str(row.get("description") or "").strip()[:500],
            "date": row.get("date"),
            "importance": row.get("importance"),
            "source": "Trading Economics news",
            "url": row.get("url"),
        })
    return [item for item in result if item["title"]]
