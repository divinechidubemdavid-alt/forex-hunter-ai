"""Official-source economic release research and market confirmation.

BLS is used for U.S. labour and CPI releases. Values are fetched from the
public BLS API and matched to the event's reference month; no market consensus
is fabricated. Technical snapshots use the existing Twelve Data budget guard.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx

import market_data
import state
from strategy import calculate_indicators

BLS_API = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
BLS_SERIES = {
    "payroll": "CES0000000001",
    "unemployment": "LNS14000000",
    "cpi": "CUSR0000SA0",
    "hourly_earnings": "CES0500000003",
}
ET = ZoneInfo("America/New_York")

def _event_kind(title: str) -> str | None:
    t = title.lower()
    if "employment situation" in t or "nonfarm" in t or "payroll" in t:
        return "payroll"
    if "unemployment rate" in t:
        return "unemployment"
    if "consumer price" in t or "cpi" in t:
        return "cpi"
    if "average hourly earnings" in t or "hourly earnings" in t:
        return "hourly_earnings"
    return None

def _period_key(row):
    period = str(row.get("period", ""))
    if not period.startswith("M") or period == "M13":
        return None
    try:
        return (int(row["year"]), int(period[1:]))
    except (KeyError, TypeError, ValueError):
        return None


async def fetch_bls_model_forecast(event: dict) -> dict | None:
    """Create a clearly labelled trend baseline from recent official BLS observations."""
    kind = _event_kind(event.get("title", ""))
    if not kind:
        return None
    now = datetime.now(timezone.utc)
    payload = {
        "seriesid": [BLS_SERIES[kind]],
        "startyear": str(now.year - 2),
        "endyear": str(now.year),
    }
    async with httpx.AsyncClient(timeout=25) as client:
        response = await client.post(BLS_API, json=payload)
        response.raise_for_status()
        body = response.json()
    if body.get("status") != "REQUEST_SUCCEEDED":
        return None
    series = body.get("Results", {}).get("series", [])
    if not series:
        return None
    rows = [row for row in series[0].get("data", []) if _period_key(row)]
    rows.sort(key=_period_key)
    values = { _period_key(row): float(row["value"].replace(",", "")) for row in rows }
    periods = sorted(values)
    if kind == "unemployment":
        rates = [values[p] for p in periods[-3:]]
        if len(rates) < 3:
            return None
        previous = rates[-1]
        forecast = sum(rates) / len(rates)
        label = "Unemployment rate (%)"
        higher_is_better = False
    elif kind == "cpi":
        yoy = []
        for year, month in periods:
            prior = (year - 1, month)
            if prior in values:
                yoy.append((year, month, (values[(year, month)] / values[prior] - 1) * 100))
        if len(yoy) < 3:
            return None
        previous = yoy[-1][2]
        forecast = sum(item[2] for item in yoy[-3:]) / 3
        label = "CPI year-over-year change (%)"
        higher_is_better = False
    else:
        changes = []
        for index in range(1, len(periods)):
            old = values[periods[index - 1]]
            new = values[periods[index]]
            if kind == "payroll":
                changes.append(new - old)
            elif old:
                changes.append((new / old - 1) * 100)
        if len(changes) < 3:
            return None
        previous = changes[-1]
        forecast = sum(changes[-3:]) / 3
        label = "Monthly nonfarm payroll change (thousands)" if kind == "payroll" else "Average hourly earnings monthly change (%)"
        higher_is_better = True
    return {
        "forecast": round(forecast, 4),
        "previous": round(previous, 4),
        "label": label,
        "forecast_type": "model_estimate",
        "forecast_source": "BLS official historical data; trailing three-release average",
        "higher_is_better": higher_is_better,
        "source_url": "https://www.bls.gov/developers/",
    }

async def fetch_bls_release_actual(event: dict) -> dict | None:
    """Fetch one batch of official BLS series and return only a matching release."""
    kind = _event_kind(event.get("title", ""))
    if not kind:
        return None
    scheduled = datetime.fromisoformat(event["scheduled_at"].replace("Z", "+00:00"))
    if scheduled.tzinfo is None:
        scheduled = scheduled.replace(tzinfo=timezone.utc)
    reference = scheduled.astimezone(ET)
    ref_month = reference.month - 1 or 12
    ref_year = reference.year if reference.month > 1 else reference.year - 1
    start_year = str(ref_year - 2)
    end_year = str(ref_year)
    payload = {
        "seriesid": list(BLS_SERIES.values()),
        "startyear": start_year,
        "endyear": end_year,
    }
    async with httpx.AsyncClient(timeout=25) as client:
        response = await client.post(BLS_API, json=payload)
        response.raise_for_status()
        body = response.json()
    if body.get("status") != "REQUEST_SUCCEEDED":
        raise RuntimeError("BLS API did not confirm request success.")
    series = {x.get("seriesID"): x.get("data", []) for x in body.get("Results", {}).get("series", [])}
    wanted = BLS_SERIES[kind]
    rows = series.get(wanted, [])
    by_period = {_period_key(row): row for row in rows if _period_key(row)}
    current = by_period.get((ref_year, ref_month))
    prior = by_period.get((ref_year - 1, ref_month - 12)) if ref_month > 1 else by_period.get((ref_year - 1, 12))
    if not current:
        return None
    try:
        value = float(current["value"].replace(",", ""))
    except (KeyError, TypeError, ValueError):
        return None
    actual = value
    previous_value = None
    unit = current.get("footnotes", [])
    prior_month = ref_month - 1 or 12
    prior_year = ref_year if ref_month > 1 else ref_year - 1
    prior_row = by_period.get((prior_year, prior_month))
    prior2_month = prior_month - 1 or 12
    prior2_year = prior_year if prior_month > 1 else prior_year - 1
    prior2_row = by_period.get((prior2_year, prior2_month))
    if kind == "payroll":
        if not prior_row or not prior2_row:
            return None
        prior_level = float(prior_row["value"].replace(",", ""))
        prior2_level = float(prior2_row["value"].replace(",", ""))
        actual = value - prior_level
        previous_value = prior_level - prior2_level
        label = "Monthly nonfarm payroll change (thousands)"
    elif kind == "cpi":
        year_ago = by_period.get((ref_year - 1, ref_month))
        prior_year_ago = by_period.get((prior_year - 1, prior_month))
        if not year_ago or not prior_row or not prior_year_ago:
            return None
        old = float(year_ago["value"].replace(",", ""))
        prior_old = float(prior_year_ago["value"].replace(",", ""))
        actual = (value / old - 1) * 100 if old else None
        previous_value = (float(prior_row["value"].replace(",", "")) / prior_old - 1) * 100 if prior_old else None
        label = "CPI year-over-year change (%)"
    elif kind == "unemployment":
        if not prior_row:
            return None
        previous_value = float(prior_row["value"].replace(",", ""))
        label = "Unemployment rate (%)"
    else:
        if not prior_row or not prior2_row:
            return None
        prior_level = float(prior_row["value"].replace(",", ""))
        prior2_level = float(prior2_row["value"].replace(",", ""))
        actual = (value / prior_level - 1) * 100 if prior_level else None
        previous_value = (prior_level / prior2_level - 1) * 100 if prior2_level else None
        label = "Average hourly earnings month-over-month change (%)"
    if actual is None:
        return None
    return {
        "value": round(actual, 4), "previous_value": round(previous_value, 4) if previous_value is not None else None, "label": label,
        "series_id": wanted, "reference_period": f"{ref_year}-{ref_month:02d}",
        "source": "U.S. Bureau of Labor Statistics API",
        "source_url": "https://www.bls.gov/developers/",
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "footnotes": unit,
        "higher_is_better": kind in {"payroll", "hourly_earnings"},
    }

def _market_for_currency(currency: str) -> str | None:
    return {
        "USD": "EUR/USD", "EUR": "EUR/USD", "GBP": "GBP/USD",
        "JPY": "USD/JPY", "AUD": "AUD/USD", "CAD": "USD/CAD",
        "CHF": "USD/CHF", "XAU": "XAU/USD",
    }.get(str(currency).upper())

async def technical_snapshot(event: dict, checkpoint: str) -> dict:
    """Run closed-candle EMA/ADX/RSI/ATR and breakout-retest context.

    Event checks are charged to the existing local Twelve Data safety ceiling.
    No event-specific request can bypass the shared 800/day counter.
    """
    market = _market_for_currency(event.get("currency", "USD"))
    if not market:
        return {"status": "UNAVAILABLE", "reason": "No configured proxy market for this currency."}
    timeframe = "5m" if checkpoint in {"15m", "5m"} else "15m"
    try:
        frame = await market_data.fetch_candles(
            market, outputsize=180, interval=timeframe, purpose="auto"
        )
        data = calculate_indicators(frame).dropna()
        if len(data) < 40:
            return {"status": "UNAVAILABLE", "reason": "Insufficient completed candles.", "market": market}
        last = data.iloc[-1]
        previous = data.iloc[-2]
        if last["ema9"] > last["ema21"] and last["plus_di"] > last["minus_di"]:
            direction = "BULLISH"
        elif last["ema9"] < last["ema21"] and last["minus_di"] > last["plus_di"]:
            direction = "BEARISH"
        else:
            direction = "NEUTRAL"
        aligned = direction != "NEUTRAL" and (
            (direction == "BULLISH" and previous["ema9"] > previous["ema21"]) or
            (direction == "BEARISH" and previous["ema9"] < previous["ema21"])
        )
        close = float(last["close"])
        atr = float(last["atr"])
        recent = data.iloc[-21:-1]
        breakout = "NONE"
        if close > float(recent["high"].max()):
            breakout = "UPSIDE_BREAKOUT"
        elif close < float(recent["low"].min()):
            breakout = "DOWNSIDE_BREAKOUT"
        return {
            "status": "AVAILABLE", "market": market, "timeframe": timeframe,
            "direction": direction, "currency_direction": ({"BULLISH": "BEARISH", "BEARISH": "BULLISH"}.get(direction, direction) if market.split("/")[1] == str(event.get("currency", "USD")).upper() else direction), "ema_aligned": bool(aligned),
            "adx": round(float(last["adx"]), 2), "rsi": round(float(last["rsi"]), 2),
            "atr": round(atr, 6), "close": close, "candle_time": data.index[-1].isoformat(),
            "breakout": breakout,
            "volatility_pct": round(atr / close * 100, 4) if close else None,
            "source": "Twelve Data completed candles", "_frame": frame,
        }
    except Exception as exc:
        return {"status": "UNAVAILABLE", "market": market, "reason": str(exc)}

def material_change(baseline: dict, current: dict) -> tuple[bool, list[str]]:
    """Ignore ordinary noise; only confirm material market/regime changes."""
    changes = []
    if current.get("status") != "AVAILABLE":
        return False, changes
    if baseline.get("direction") != current.get("direction"):
        changes.append("technical direction changed")
    old_adx, new_adx = baseline.get("adx"), current.get("adx")
    if old_adx is not None and new_adx is not None and abs(new_adx - old_adx) >= 8:
        changes.append("ADX regime changed materially")
    if current.get("breakout") in {"UPSIDE_BREAKOUT", "DOWNSIDE_BREAKOUT"} and current.get("breakout") != baseline.get("breakout"):
        changes.append("completed-candle range breakout detected")
    old_vol, new_vol = baseline.get("volatility_pct"), current.get("volatility_pct")
    if old_vol and new_vol and new_vol >= old_vol * 1.8:
        changes.append("volatility expanded materially")
    return bool(changes), changes

async def auto_actual_release(event: dict) -> dict | None:
    """Prefer licensed global calendar actuals; fall back to official BLS API."""
    try:
        import trading_economics
        if trading_economics.configured() and event.get("provider_calendar_id"):
            result = await trading_economics.fetch_event_actual(event)
            if result:
                return result
    except Exception:
        # Do not silently invent or replace an official value; try the supported BLS source.
        pass
    return await fetch_bls_release_actual(event)
