import unittest
from unittest.mock import patch

import pandas as pd

import strategy


def _indicator_frame(rows=100):
    index = pd.date_range(
        "2026-01-01", periods=rows, freq="15min", tz="UTC"
    )
    frame = pd.DataFrame({
        "open": [100.0] * rows,
        "high": [100.5] * rows,
        "low": [99.5] * rows,
        "close": [100.1] * rows,
        "ema9": [100.0] * rows,
        "ema21": [99.9] * rows,
        "atr": [1.0] * rows,
        "adx": [12.0] * rows,
        "rsi": [49.0] * rows,
        "plus_di": [18.0] * rows,
        "minus_di": [20.0] * rows,
        "support": [99.0] * rows,
        "resistance": [101.0] * rows,
        "breakout_high": [101.0] * rows,
        "breakout_low": [99.0] * rows,
        "candle_range": [1.0] * rows,
        "body": [0.1] * rows,
    }, index=index)
    return frame


class EarlyTrendEntryTests(unittest.TestCase):
    def test_support_rejection_can_identify_early_buy(self):
        frame = _indicator_frame(100)
        previous = frame.index[-2]
        current = frame.index[-1]

        frame.loc[previous, "rsi"] = 46.0
        frame.loc[previous, "plus_di"] = 16.0
        frame.loc[previous, "minus_di"] = 22.0

        frame.loc[current, "open"] = 100.0
        frame.loc[current, "high"] = 101.0
        frame.loc[current, "low"] = 99.0
        frame.loc[current, "close"] = 100.8
        frame.loc[current, "candle_range"] = 2.0
        frame.loc[current, "body"] = 0.9
        frame.loc[current, "support"] = 99.0
        frame.loc[current, "rsi"] = 49.0
        frame.loc[current, "plus_di"] = 18.0
        frame.loc[current, "minus_di"] = 20.0

        direction, setup = strategy._early_trend_setup(frame)

        self.assertEqual(direction, "BUY")
        self.assertEqual(setup, "SUPPORT_REVERSAL")

    def test_early_entry_can_qualify_below_legacy_adx_minimum(self):
        frame = _indicator_frame()
        with (
            patch.object(strategy, "resample_candles", return_value=frame),
            patch.object(strategy, "calculate_indicators", return_value=frame),
            patch.object(strategy, "_higher_timeframe_context", return_value="UNAVAILABLE"),
            patch.object(strategy, "_trend_setup", return_value=None),
            patch.object(strategy, "_breakout_retest_setup", return_value=None),
            patch.object(strategy, "_early_trend_setup", return_value=("BUY", "SUPPORT_REVERSAL")),
            patch.object(strategy, "_early_entry_score", return_value=(82, ["early reversal"])),
            patch.object(strategy, "_technical_stop", return_value=99.0),
        ):
            result = strategy.analyze(frame, "15m")

        self.assertEqual(result["status"], "BUY")
        self.assertEqual(result["signal_stage"], "EARLY_ENTRY")
        self.assertEqual(result["strategy"], "Early Trend Entry")
        self.assertLess(result["adx"], strategy.ADX_MINIMUM)
        self.assertEqual(len(result["take_profits"]), 3)

    def test_extended_move_is_rejected_instead_of_chased(self):
        frame = _indicator_frame()
        frame.loc[frame.index[-1], "close"] = 102.0
        frame.loc[frame.index[-1], "ema9"] = 100.0

        with (
            patch.object(strategy, "resample_candles", return_value=frame),
            patch.object(strategy, "calculate_indicators", return_value=frame),
            patch.object(strategy, "_higher_timeframe_context", return_value="UNAVAILABLE"),
            patch.object(strategy, "_trend_setup", return_value=None),
            patch.object(strategy, "_breakout_retest_setup", return_value=None),
            patch.object(strategy, "_early_trend_setup", return_value=("BUY", "BULLISH_PULLBACK")),
        ):
            result = strategy.analyze(frame, "15m")

        self.assertEqual(result["status"], "WAIT")
        self.assertEqual(result["signal_stage"], "MOVE_EXTENDED")
        self.assertIn("WAIT FOR RETRACEMENT", result["reason"])


if __name__ == "__main__":
    unittest.main()
