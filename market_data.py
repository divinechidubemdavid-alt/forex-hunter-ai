
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
# Coalesce simultaneous cache misses for the same symbol/interval/output size.
_inflight_locks = {}

SUPPORTED_INTERVALS = {
    "1m": "1min",
    "5m": "5min",
    "15m": "15min",
    "30m": "30min",
    "1h": "1h",
    "4h": "4h",
    "1d": "1day",
    "1day": "1day",
}


class MarketDataError(Exception):
    """Raised when market data cannot safely be used."""


def maximum_candle_age_minutes(duration):
    """A completed candle remains valid for its interval plus provider delay."""
    return duration.total_seconds() / 60 + MAX_CANDLE_AGE_MINUTES


def _normalise_timeframe(timeframe):
    value = str(timeframe).strip().lower()

    if value not in SUPPORTED_INTERVALS:
        raise ValueError(
            "Unsupported timeframe. Use 1m, 5m, 15m, 30m, "
            "1h, 4h, or 1day."
        )

    return value


def _process_candles(values, timeframe):
    required = ["datetime", "open", "high", "low", "close"]

    frame = pd.DataFrame(values)

    if frame.empty or any(column not in frame.columns for column in required):
        raise ValueError("Required candle data is missing.")

    frame = frame[required].copy()

    frame["datetime"] = pd.to_datetime(
        frame["datetime"],
        utc=True,
        errors="coerce",
    )

    for column in ["open", "high", "low", "close"]:
        frame[column] = pd.to_numeric(
            frame[column],
            errors="coerce",
        )

    frame = frame.dropna()
    frame = frame.sort_values("datetime")
    frame = frame.drop_duplicates("datetime")
    frame = frame.set_index("datetime")
    frame = frame[~frame.index.duplicated(keep="last")]

    # Remove the currently forming candle.
    now = pd.Timestamp.now(tz="UTC")

    interval = SUPPORTED_INTERVALS[timeframe]
    duration = pd.Timedelta(interval)

    frame = frame[
        frame.index + duration <= now
    ]

    if len(frame) < 60:
        raise ValueError(
            f"Not enough completed candles. Received {len(frame)}."
        )

    latest_age = (
        now - (frame.index[-1] + duration)
    ).total_seconds() / 60

    # Higher-timeframe candles naturally close less frequently. Apply
    # the freshness allowance to one full candle duration plus the provider
    # publication buffer; otherwise valid 1h/4h/Daily candles are rejected.
    maximum_age_minutes = maximum_candle_age_minutes(duration)
    if latest_age > maximum_age_minutes:
        raise ValueError(
            f"Market data is stale ({latest_age:.1f} minutes old; "
            f"maximum {maximum_age_minutes:.1f})."
        )

    return frame


async def fetch_candles(
    symbol,
    outputsize=1500,
    interval="1min",
    purpose="manual",
):
    """
    Fetch completed candles from Twelve Data.

    interval may be:
    1min, 5min, 15min, 30min, 1h, 4h, or 1day.

    The shared state module controls daily API request allowance.
    """

    if not TWELVE_DATA_API_KEY:
        raise MarketDataError(
            "Twelve Data API key is missing. "
            "Add it to Codespaces Secrets."
        )

    timeframe = _normalise_timeframe(interval)
    provider_interval = SUPPORTED_INTERVALS[timeframe]

    symbol = str(symbol).strip().upper()

    if not symbol:
        raise MarketDataError("A market symbol is required.")

    try:
        outputsize = int(outputsize)
    except (TypeError, ValueError):
        outputsize = 1500

    outputsize = max(60, min(outputsize, 5000))

    cache_key = (symbol, provider_interval, outputsize)
    now = time.time()

    async with _cache_lock:
        request_lock = _inflight_locks.setdefault(cache_key, asyncio.Lock())

    # Re-check the cache after acquiring the per-key lock. A concurrent caller
    # may already have fetched and cached the exact same market data.
    async with request_lock:
        now = time.time()
        async with _cache_lock:
            cached = _cache.get(cache_key)
            if cached and now - cached["time"] < CACHE_TTL_SECONDS:
                return cached["data"].copy()

        allowed, reason = await asyncio.to_thread(
            state.reserve_api_request,
            purpose,
        )

        if not allowed:
            raise MarketDataError(reason)

        params = {
        "symbol": symbol,
        "interval": provider_interval,
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
            raise MarketDataError(
                "Twelve Data returned an unexpected response."
            )

        if payload.get("status") == "error" or "values" not in payload:
            raise MarketDataError(
                str(payload.get("message", "No candle data returned."))
            )

        try:
            frame = _process_candles(
                payload["values"],
                timeframe,
            )

        except (ValueError, TypeError, KeyError) as exc:
            raise MarketDataError(
                f"Could not process candle data: {exc}"
            ) from exc

        async with _cache_lock:
            _cache[cache_key] = {
                "time": time.time(),
                "data": frame.copy(),
            }

        return frame


def resample_candles(frame, timeframe):
    """
    Convert smaller-timeframe candles into larger completed candles.

    This supports the strategy module's higher-timeframe context.
    """

    if frame is None or frame.empty:
        return pd.DataFrame(
            columns=["open", "high", "low", "close"]
        )

    timeframe = _normalise_timeframe(timeframe)

    durations = {
        "1m": "1min",
        "5m": "5min",
        "15m": "15min",
        "30m": "30min",
        "1h": "1h",
        "4h": "4h",
        "1d": "1day",
        "1day": "1day",
    }

    duration = durations[timeframe]

    # Return a copy if the data already uses the requested interval.
    if timeframe == "1m":
        return frame.copy()

    required = ["open", "high", "low", "close"]

    if any(column not in frame.columns for column in required):
        raise ValueError("Candle frame is missing OHLC columns.")

    candles = frame[required].copy()

    if not isinstance(candles.index, pd.DatetimeIndex):
        raise ValueError(
            "Candle data must have a datetime index."
        )

    if candles.index.tz is None:
        candles.index = candles.index.tz_localize("UTC")

    candles = candles.sort_index()

    aggregated = candles.resample(
        duration,
        origin="epoch",
        label="left",
        closed="left",
    ).agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
    }).dropna()

    now = pd.Timestamp.now(tz="UTC")
    candle_duration = pd.Timedelta(duration)

    # Keep only candles whose full timeframe has elapsed.
    aggregated = aggregated[
        aggregated.index + candle_duration <= now
    ]

    return aggregated
