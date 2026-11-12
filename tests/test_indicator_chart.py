import unittest
from io import BytesIO

import numpy as np
import pandas as pd

from chart import create_market_chart
from strategy import calculate_indicators


def _candles(rows=120):
    index = pd.date_range(
        "2026-10-01", periods=rows, freq="15min", tz="UTC"
    )
    close = 1.1000 + np.linspace(0, 0.004, rows) + np.sin(
        np.arange(rows) / 5
    ) * 0.0005
    open_price = np.concatenate(([close[0] - 0.0001], close[:-1]))
    return pd.DataFrame({
        "open": open_price,
        "high": np.maximum(open_price, close) + 0.0004,
        "low": np.minimum(open_price, close) - 0.0004,
        "close": close,
    }, index=index)


class IndicatorChartTests(unittest.TestCase):
    def test_chart_renders_with_strategy_and_indicator_panels(self):
        frame = _candles()
        chart = create_market_chart(
            frame,
            "EUR/USD",
            "15m",
            entry=float(frame["close"].iloc[-1]),
            stop_loss=float(frame["close"].iloc[-1]) - 0.001,
            take_profits=[
                float(frame["close"].iloc[-1]) + 0.001,
                float(frame["close"].iloc[-1]) + 0.002,
                float(frame["close"].iloc[-1]) + 0.003,
            ],
            strategy="Early Trend Entry",
            signal_stage="EARLY_ENTRY",
        )

        self.assertIsInstance(chart, BytesIO)
        self.assertEqual(chart.read(8), b"\x89PNG\r\n\x1a\n")

    def test_chart_uses_the_strategy_engines_indicator_columns(self):
        indicators = calculate_indicators(_candles())
        for column in (
            "ema9", "ema21", "ema50", "rsi", "adx",
            "plus_di", "minus_di", "atr",
            "support", "resistance",
        ):
            self.assertIn(column, indicators.columns)
        self.assertTrue(np.isfinite(indicators["ema9"].iloc[-1]))
        self.assertTrue(np.isfinite(indicators["ema21"].iloc[-1]))
        self.assertTrue(np.isfinite(indicators["ema50"].iloc[-1]))
        self.assertTrue(np.isfinite(indicators["rsi"].iloc[-1]))
        self.assertTrue(np.isfinite(indicators["adx"].iloc[-1]))
        self.assertTrue(np.isfinite(indicators["atr"].iloc[-1]))


if __name__ == "__main__":
    unittest.main()
