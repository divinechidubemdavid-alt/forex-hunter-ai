import unittest

import pandas as pd

from strategy import _market_structure_setup, assess_trend_health


class MarketStructureConfirmationTests(unittest.TestCase):
    def test_bullish_break_and_retest_is_confirmed(self):
        rows = []
        for _ in range(38):
            rows.append({
                "open": 99.0, "high": 100.0,
                "low": 98.0, "close": 99.5, "atr": 1.0,
            })
        rows.append({
            "open": 100.0, "high": 102.0,
            "low": 99.8, "close": 101.5, "atr": 1.0,
        })
        rows.append({
            "open": 100.2, "high": 101.0,
            "low": 100.1, "close": 100.8, "atr": 1.0,
        })
        frame = pd.DataFrame(rows)
        self.assertEqual(_market_structure_setup(frame), "BUY")

    def test_bearish_break_and_retest_is_confirmed(self):
        rows = []
        for _ in range(38):
            rows.append({
                "open": 101.0, "high": 102.0,
                "low": 100.0, "close": 100.5, "atr": 1.0,
            })
        rows.append({
            "open": 100.0, "high": 100.2,
            "low": 98.0, "close": 98.5, "atr": 1.0,
        })
        rows.append({
            "open": 99.8, "high": 99.9,
            "low": 99.0, "close": 99.2, "atr": 1.0,
        })
        frame = pd.DataFrame(rows)
        self.assertEqual(_market_structure_setup(frame), "SELL")

    def test_no_structure_break_returns_none(self):
        rows = [{
            "open": 99.0, "high": 100.0,
            "low": 98.0, "close": 99.5, "atr": 1.0,
        } for _ in range(40)]
        self.assertIsNone(_market_structure_setup(pd.DataFrame(rows)))

    def test_trend_health_assessment_handles_closed_ohlc_data(self):
        closes = [100 + 0.2 * i for i in range(115)]
        closes.extend([120, 117, 114, 111, 108])
        rows = []
        previous = closes[0]
        for close in closes:
            rows.append({
                "open": previous,
                "high": max(previous, close) + 0.3,
                "low": min(previous, close) - 0.3,
                "close": close,
            })
            previous = close
        index = pd.date_range("2026-01-01", periods=len(rows), freq="15min", tz="UTC")
        frame = pd.DataFrame(rows, index=index)
        warning = assess_trend_health(frame, "BUY")
        self.assertIsNotNone(warning)
        self.assertIn(warning["type"], {
            "TREND_WEAKENING", "TREND_REVERSAL_WARNING"
        })


if __name__ == "__main__":
    unittest.main()
