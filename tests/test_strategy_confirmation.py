import unittest

import pandas as pd

from strategy import assess_trend_health


class TrendHealthTests(unittest.TestCase):
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
