import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

import auto_scanner
import strategy
from trading_styles import (
    AUTOMATIC_TRADING_STYLE,
    STRONG_SIGNALS_TRADING_STYLE,
    required_higher_timeframes,
)


def _market_frame():
    index = pd.date_range("2026-01-01", periods=120, freq="min", tz="UTC")
    close = np.linspace(100.0, 101.0, len(index))
    return pd.DataFrame({
        "open": close - 0.05,
        "high": close + 0.10,
        "low": close - 0.10,
        "close": close,
        "ema9": close + 0.05,
        "ema21": close,
        "plus_di": np.full(len(index), 25.0),
        "minus_di": np.full(len(index), 10.0),
        "adx": np.full(len(index), 30.0),
        "rsi": np.full(len(index), 58.0),
        "atr": np.full(len(index), 0.20),
        "resistance": close + 0.5,
        "support": close - 0.5,
        "breakout_high": close + 0.5,
        "breakout_low": close - 0.5,
        "candle_range": np.full(len(index), 0.20),
    }, index=index)


class MultiStyleStrategyTests(unittest.TestCase):
    def _run_analysis(self, style, timeframe, confirmations):
        frame = _market_frame()
        with (
            patch.object(strategy, "resample_candles", return_value=frame),
            patch.object(strategy, "calculate_indicators", return_value=frame),
            patch.object(strategy, "_higher_timeframe_context", return_value="BULLISH"),
            patch.object(strategy, "_higher_timeframe_confirmations", return_value=confirmations),
            patch.object(strategy, "_trend_setup", return_value="BUY"),
            patch.object(strategy, "_breakout_retest_setup", return_value=None),
            patch.object(strategy, "_early_trend_setup", return_value=(None, None)),
            patch.object(strategy, "_move_is_extended", return_value=False),
            patch.object(strategy, "_calculate_score", return_value=(90, ["test confluence"])),
            patch.object(strategy, "_technical_stop", return_value=99.0),
        ):
            return strategy.analyze(frame, timeframe, trading_style=style)

    def test_scalping_returns_no_stop_or_target_fields(self):
        result = self._run_analysis(
            "SCALPING", "1m", {"5m": "BULLISH", "15m": "BULLISH"}
        )
        self.assertEqual(result["status"], "BUY")
        self.assertEqual(result["trading_style"], "SCALPING")
        self.assertNotIn("stop_loss", result)
        self.assertNotIn("take_profits", result)
        self.assertNotIn("risk_distance", result)
        message = strategy.format_signal("EUR/USD", result)
        self.assertIn("SCALPING TRADE", message)
        self.assertNotIn("Stop Loss:", message)
        self.assertNotIn("TP1", message)
        self.assertNotIn("TP2", message)
        self.assertNotIn("TP3", message)

    def test_day_trade_keeps_structural_stop_and_three_targets(self):
        result = self._run_analysis(
            "DAY", "15m", {"1h": "BULLISH", "4h": "BULLISH"}
        )
        self.assertEqual(result["status"], "BUY")
        self.assertEqual(result["trading_style"], "DAY")
        self.assertEqual(result["stop_loss"], 99.0)
        self.assertEqual(len(result["take_profits"]), 3)

    def test_missing_higher_timeframe_confirmation_blocks_signal(self):
        result = self._run_analysis(
            "DAY", "15m", {"1h": "BULLISH", "4h": "UNAVAILABLE"}
        )
        self.assertEqual(result["status"], "WAIT")
        self.assertEqual(result["signal_stage"], "HTF_UNCONFIRMED")
        self.assertIn("No trade signal was issued", result["reason"])

    def test_all_required_confirmation_mappings_are_preserved(self):
        self.assertEqual(required_higher_timeframes("SCALPING", "1m"), ("5m", "15m"))
        self.assertEqual(required_higher_timeframes("SCALPING", "5m"), ("15m", "30m"))
        self.assertEqual(required_higher_timeframes("DAY", "15m"), ("1h", "4h"))
        self.assertEqual(required_higher_timeframes("DAY", "30m"), ("1h", "4h"))
        self.assertEqual(required_higher_timeframes("SWING", "1h"), ("4h", "1day"))
        self.assertEqual(required_higher_timeframes("SWING", "4h"), ("1day",))
        self.assertEqual(required_higher_timeframes("SWING", "1day"), ("1week",))

    def test_automatic_and_strong_signal_engines_are_day_only(self):
        self.assertEqual(AUTOMATIC_TRADING_STYLE, "DAY")
        self.assertEqual(STRONG_SIGNALS_TRADING_STYLE, "DAY")

    def test_btc_is_available_on_weekdays_and_only_market_on_weekends(self):
        weekday = auto_scanner._eligible_markets(pd.Timestamp("2026-11-09 12:00", tz="Africa/Lagos"))
        weekend = auto_scanner._eligible_markets(pd.Timestamp("2026-11-14 12:00", tz="Africa/Lagos"))
        self.assertIn("BTC/USD", weekday)
        self.assertEqual(weekend, ["BTC/USD"])


if __name__ == "__main__":
    unittest.main()
