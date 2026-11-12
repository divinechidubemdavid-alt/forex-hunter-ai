import unittest
from io import BytesIO

import numpy as np
import pandas as pd

from chart import create_market_chart, _supply_demand_zone_bounds
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


class SupplyDemandChartTests(unittest.TestCase):
    def test_chart_renders_with_supply_demand_only(self):
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

    def test_supply_and_demand_zones_are_detected(self):
        zones = _supply_demand_zone_bounds(_candles())
        self.assertTrue(zones)
        self.assertTrue(all(zone["type"] in {"SUPPLY", "DEMAND"} for zone in zones))
        self.assertTrue(all(zone["upper"] > zone["lower"] for zone in zones))
        self.assertLessEqual(sum(zone["type"] == "SUPPLY" for zone in zones), 2)
        self.assertLessEqual(sum(zone["type"] == "DEMAND" for zone in zones), 2)

    def test_strategy_indicators_remain_available_in_analysis_engine(self):
        indicators = calculate_indicators(_candles())
        for column in (
            "ema9", "ema21", "ema50", "rsi", "adx",
            "plus_di", "minus_di", "atr",
            "support", "resistance",
        ):
            self.assertIn(column, indicators.columns)
