import unittest
from datetime import datetime
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from gold_monitor import (
    _gold_monitor_cycle,
    GOLD_FETCH_SCHEDULE,
    GOLD_SOURCE_OUTPUTSIZE,
    _breakout_retest,
    _trendline_breakout,
    classify_gold_market,
    estimated_daily_gold_requests,
)


def _candles(mode, count=140):
    x = np.arange(count, dtype=float)
    if mode == "up":
        close = 2000.0 + x * 0.8
        open_price = close - 0.15
    elif mode == "down":
        close = 2200.0 - x * 0.8
        open_price = close + 0.15
    else:
        close = 2000.0 + np.sin(x * 1.3) * 0.12
        open_price = close - np.cos(x * 1.3) * 0.03

    high = np.maximum(open_price, close) + 0.35
    low = np.minimum(open_price, close) - 0.35
    index = pd.date_range(
        "2026-10-01", periods=count, freq="min", tz="UTC"
    )
    return pd.DataFrame(
        {
            "open": open_price,
            "high": high,
            "low": low,
            "close": close,
        },
        index=index,
    )


class GoldConditionClassificationTests(unittest.TestCase):
    def test_uptrend_is_reported_without_trade_levels(self):
        result = classify_gold_market(_candles("up"), "1m")
        self.assertEqual(result["condition"], "UPTREND")
        self.assertEqual(result["direction"], "UP")
        self.assertNotIn("entry", result)
        self.assertNotIn("stop_loss", result)
        self.assertNotIn("take_profits", result)

    def test_downtrend_is_reported_without_trade_levels(self):
        result = classify_gold_market(_candles("down"), "5m")
        self.assertEqual(result["condition"], "DOWNTREND")
        self.assertEqual(result["direction"], "DOWN")

    def test_consolidation_is_reported_as_a_market_condition(self):
        result = classify_gold_market(_candles("range"), "15m")
        self.assertEqual(result["condition"], "CONSOLIDATION / RANGE")
        self.assertEqual(result["direction"], "NEUTRAL")

    def test_rejects_insufficient_candles(self):
        with self.assertRaisesRegex(ValueError, "at least 80"):
            classify_gold_market(_candles("up", count=40), "15m")

    def test_rejects_empty_frame(self):
        with self.assertRaisesRegex(ValueError, "empty"):
            classify_gold_market(pd.DataFrame(), "1h")


class GoldStructureDetectionTests(unittest.TestCase):
    def test_confirmed_descending_trendline_breakout(self):
        count = 40
        close = np.full(count, 99.5)
        open_price = close - 0.05
        high = np.full(count, 100.0)
        low = np.full(count, 99.0)
        high[10] = 110.0
        high[20] = 108.0
        close[-2] = 104.3
        open_price[-2] = 104.1
        close[-1] = 104.5
        open_price[-1] = 104.2
        high[-1] = 105.0
        low[-1] = 104.0
        frame = pd.DataFrame(
            {"open": open_price, "high": high, "low": low, "close": close}
        )
        result = _trendline_breakout(frame, atr=0.5)
        self.assertIsNotNone(result)
        self.assertEqual(result["direction"], "UP")

    def test_breakout_retest_requires_rejection_in_breakout_direction(self):
        frame = pd.DataFrame(
            {
                "open": [100.4],
                "high": [101.0],
                "low": [99.9],
                "close": [100.6],
            }
        )
        self.assertEqual(
            _breakout_retest(
                frame, {"direction": "UP", "level": 100.0}, atr=1.0
            ),
            "UP",
        )

    def test_breakout_retest_does_not_confirm_wrong_close(self):
        frame = pd.DataFrame(
            {
                "open": [100.4],
                "high": [101.0],
                "low": [99.9],
                "close": [99.8],
            }
        )
        self.assertIsNone(
            _breakout_retest(
                frame, {"direction": "UP", "level": 100.0}, atr=1.0
            )
        )


class GoldApiScheduleTests(unittest.TestCase):
    def test_seven_timeframes_are_covered_by_shared_feed_and_due_fetches(self):
        self.assertEqual(
            set(GOLD_FETCH_SCHEDULE),
            {"m1_source", "30m", "1h", "4h", "1day"},
        )
        self.assertEqual(GOLD_SOURCE_OUTPUTSIZE, 2000)

    def test_estimated_gold_requests_fit_half_background_budget(self):
        self.assertEqual(estimated_daily_gold_requests(), 307)
        self.assertLessEqual(
            estimated_daily_gold_requests(),
            350,
        )

    def test_weekend_cycle_makes_no_gold_api_request(self):
        class App:
            pass

        saturday = datetime(
            2026, 11, 14, 12, 0, tzinfo=ZoneInfo("Africa/Lagos")
        ).timestamp()
        with patch("gold_monitor.fetch_candles", new_callable=AsyncMock) as fetch:
            import asyncio
            asyncio.run(_gold_monitor_cycle(App(), now_epoch=saturday))
        fetch.assert_not_awaited()

    def test_single_m1_feed_can_be_resampled_for_m5_and_m15(self):
        from market_data import resample_candles

        now = pd.Timestamp.now(tz="UTC").floor("min")
        index = pd.date_range(
            end=now - pd.Timedelta(minutes=1),
            periods=GOLD_SOURCE_OUTPUTSIZE,
            freq="min",
        )
        close = np.linspace(2000.0, 2010.0, len(index))
        source = pd.DataFrame(
            {
                "open": close - 0.05,
                "high": close + 0.20,
                "low": close - 0.20,
                "close": close,
            },
            index=index,
        )
        five_minute = resample_candles(source, "5m")
        fifteen_minute = resample_candles(source, "15m")
        self.assertGreaterEqual(len(five_minute), 300)
        self.assertGreaterEqual(len(fifteen_minute), 100)


if __name__ == "__main__":
    unittest.main()
