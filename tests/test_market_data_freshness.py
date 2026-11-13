import asyncio
import unittest
from unittest.mock import patch

import pandas as pd

import market_data
from market_data import maximum_candle_age_minutes


class MarketDataRequestCoalescingTests(unittest.TestCase):
    def test_simultaneous_identical_requests_share_one_provider_call(self):
        market_data._cache.clear()
        market_data._inflight_locks.clear()
        calls = {"provider": 0, "reserved": 0}
        now = pd.Timestamp.now(tz="UTC").floor("min")
        values = []
        for offset in range(65, 0, -1):
            stamp = now - pd.Timedelta(minutes=offset)
            price = 100 + offset / 100
            values.append({
                "datetime": stamp.strftime("%Y-%m-%d %H:%M:%S"),
                "open": str(price), "high": str(price + 0.2),
                "low": str(price - 0.2), "close": str(price + 0.1),
            })

        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return {"values": values}

        class Client:
            def __init__(self, timeout=None):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def get(self, url, params=None):
                calls["provider"] += 1
                await asyncio.sleep(0.02)
                return Response()

        def reserve(_purpose):
            calls["reserved"] += 1
            return True, "Request reserved."

        async def run():
            return await asyncio.gather(
                market_data.fetch_candles("GBP/USD", 60, "1m", "auto"),
                market_data.fetch_candles("GBP/USD", 60, "1m", "monitor"),
            )

        with patch.object(market_data.httpx, "AsyncClient", Client), patch.object(
            market_data.state, "reserve_api_request", side_effect=reserve
        ):
            frames = asyncio.run(run())

        self.assertEqual(calls["provider"], 1)
        self.assertEqual(calls["reserved"], 1)
        self.assertEqual(len(frames[0]), len(frames[1]))


class HigherTimeframeFreshnessTests(unittest.TestCase):
    def test_one_minute_data_keeps_short_freshness_window(self):
        self.assertEqual(
            maximum_candle_age_minutes(pd.Timedelta("1min")),
            21.0,
        )

    def test_four_hour_candle_is_not_marked_stale_after_twenty_minutes(self):
        self.assertEqual(
            maximum_candle_age_minutes(pd.Timedelta("4h")),
            260.0,
        )

    def test_daily_candle_remains_valid_until_next_completed_day(self):
        self.assertEqual(
            maximum_candle_age_minutes(pd.Timedelta("1day")),
            1460.0,
        )


if __name__ == "__main__":
    unittest.main()
