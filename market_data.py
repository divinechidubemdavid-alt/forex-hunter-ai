

import asyncio
import time

import httpx
import pandas as pd

import state
from config import (
    TWELVE_DATA_API_KEY,
    CACHE_TTL_SECONDS,
    MAX_CANDLE_AGE_MINUTES,
)

API_URL = "https://api.twelvedata.com/time_series"

_cache = {}
_cache_lock = asyncio.Lock()


class MarketDataError(Exception):
    pass


async def fetch_candles(symbol, outputsize=1500):
    """Fetch completed one-minute candles for one selected market."""
    if not TWELVE_DATA_API_KEY:
        raise MarketDataError(
            "Twelve Data API key is missing. Add it to Codespaces Secrets."
        )

    now = time.time()

    async with _cache_lock:
        cached = _cache.get(symbol)
        if cached and now - cached["time"] < CACHE_TTL_SECONDS:
            return cached["data"].copy()

    allowed, reason = await asyncio.to_thread(state.reserve_api_request)
    if not allowed:
        raise MarketDataError(reason)

    params = {
        "symbol": symbol,
        "interval": "1min",
        "outputsize": outputsize,
        "apikey": TWELVE_DATA_API_KEY,
        "format": "JSON",
        "timezone": "UTC",
    }

    try:
        async with httpx.AsyncClient(timeout=25) as client:
            response = await client.get(API_URL, params=params)
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise MarketDataError(
            f"Twelve Data request failed: {exc}"
        ) from exc

    if not isinstance(payload, dict):
        raise MarketDataError("Twelve Data returned an unexpected response.")

    if payload.get("status") == "error" or "values" not in payload:
        raise MarketDataError(
            str(payload.get("message", "No candle data returned."))
        )

    try:
        frame = pd.DataFrame(payload["values"])
        required = ["datetime", "open", "high", "low", "close"]

        if any(column not in frame.columns for column in required):
            raise ValueError("Required candle columns are missing.")

        frame = frame[required].copy()
        frame["datetime"] = pd.to_datetime(
            frame["datetime"], utc=True, errors="coerce"
        )

        for column in ["open", "high", "low", "close"]:
            frame[column] = pd.to_numeric(
                frame[column], errors="coerce"
            )

        frame = frame.dropna().sort_values("datetime")
        frame = frame.drop_duplicates("datetime")
        frame = frame.set_index("datetime")
        frame = frame[~frame.index.duplicated(keep="last")]

        # Exclude the currently forming one-minute candle.
        current_minute = pd.Timestamp.now(tz="UTC").floor("min")
        frame = frame[frame.index < current_minute]

        if len(frame) < 60:
            raise ValueError("Not enough completed candles were returned.")

        age_minutes = (
            pd.Timestamp.now(tz="UTC") - frame.index[-1]
        ).total_seconds() / 60

        if age_minutes > MAX_CANDLE_AGE_MINUTES:
            raise ValueError(
                f"Market data is stale ({age_minutes:.1f} minutes old)."
            )

    except (ValueError, TypeError, KeyError) as exc:
        raise MarketDataError(
            f"Could not process candle data: {exc}"
        ) from exc

    async with _cache_lock:
        _cache[symbol] = {
            "time": time.time(),
            "data": frame.copy(),
        }

    return frame


def resample_candles(frame, timeframe):
    """Build completed 1m, 5m, or 15m candles from one-minute data."""
    if timeframe == "1m":
        return frame.copy()

    minutes = {"5m": 5, "15m": 15}.get(timeframe)
    if minutes is None:
        raise ValueError("Timeframe must be 1m, 5m, or 15m.")

    aggregated = frame.resample(
        f"{minutes}min",
        origin="epoch",
        label="left",
        closed="left",
    ).agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
    }).dropna()

    # Keep only candles whose full timeframe has elapsed.
    now = pd.Timestamp.now(tz="UTC")
    aggregated = aggregated[
        aggregated.index + pd.Timedelta(minutes=minutes) <= now
    ]

    return aggregated
