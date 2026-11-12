import unittest

from strategy import _early_entry_score

from trading_styles import (
    AUTOMATIC_TRADING_STYLE,
    STRONG_SIGNALS_TRADING_STYLE,
    is_automatic_style,
    is_strong_signal_style,
    required_higher_timeframes,
    style_has_trade_levels,
    style_label,
    style_timeframes,
    style_score_profile,
    style_minimum_score,
    style_minimum_adx,
)


class TradingStylePolicyTests(unittest.TestCase):
    def test_manual_style_timeframes(self):
        self.assertEqual(style_timeframes("SCALPING"), ("1m", "5m"))
        self.assertEqual(style_timeframes("DAY"), ("15m", "30m"))
        self.assertEqual(style_timeframes("SWING"), ("1h", "4h", "1day"))

    def test_higher_timeframe_confirmation_matrix(self):
        self.assertEqual(required_higher_timeframes("SCALPING", "1m"), ("5m", "15m"))
        self.assertEqual(required_higher_timeframes("SCALPING", "5m"), ("15m", "30m"))
        self.assertEqual(required_higher_timeframes("DAY", "15m"), ("1h", "4h"))
        self.assertEqual(required_higher_timeframes("DAY", "30m"), ("1h", "4h"))
        self.assertEqual(required_higher_timeframes("SWING", "1h"), ("4h", "1day"))
        self.assertEqual(required_higher_timeframes("SWING", "4h"), ("1day",))
        self.assertEqual(required_higher_timeframes("SWING", "1day"), ("1week",))

    def test_scalping_has_no_stop_or_target_plan(self):
        self.assertFalse(style_has_trade_levels("SCALPING"))
        self.assertTrue(style_has_trade_levels("DAY"))
        self.assertTrue(style_has_trade_levels("SWING"))

    def test_automatic_and_strong_signals_are_day_only(self):
        self.assertEqual(AUTOMATIC_TRADING_STYLE, "DAY")
        self.assertEqual(STRONG_SIGNALS_TRADING_STYLE, "DAY")
        self.assertTrue(is_automatic_style("DAY TRADE"))
        self.assertFalse(is_automatic_style("SCALPING"))
        self.assertFalse(is_automatic_style("SWING"))
        self.assertTrue(is_strong_signal_style("DAY"))
        self.assertFalse(is_strong_signal_style("SCALPING"))

    def test_each_style_has_an_independent_scoring_profile(self):
        scalp = style_score_profile("SCALPING")
        day = style_score_profile("DAY")
        swing = style_score_profile("SWING")
        self.assertEqual(sum(scalp.values()), 100)
        self.assertEqual(sum(day.values()), 100)
        self.assertEqual(sum(swing.values()), 100)
        self.assertNotEqual(scalp, day)
        self.assertNotEqual(day, swing)
        self.assertGreater(scalp["candle"], day["candle"])
        self.assertGreater(scalp["rsi"], day["rsi"])
        self.assertGreater(swing["adx"], day["adx"])
        self.assertGreater(swing["ema"], day["ema"])

    def test_early_entry_scoring_uses_each_style_profile(self):
        last = {
            "open": 1.0, "close": 1.1, "high": 1.11, "low": 0.99,
            "candle_range": 0.12, "rsi": 60, "plus_di": 10,
            "minus_di": 20, "ema9": 1.0, "ema21": 1.1,
            "adx": 10, "atr": 0,
        }
        previous = {
            "rsi": 55, "plus_di": 10, "minus_di": 20,
            "ema9": 1.0, "ema21": 1.1, "adx": 11,
        }
        scalp, _ = _early_entry_score(last, previous, "BUY", "TEST", "SCALPING")
        day, _ = _early_entry_score(last, previous, "BUY", "TEST", "DAY")
        swing, _ = _early_entry_score(last, previous, "BUY", "TEST", "SWING")
        self.assertEqual((scalp, day, swing), (65, 55, 55))

    def test_style_specific_qualification_thresholds(self):
        self.assertEqual(style_minimum_score("SCALPING"), 72)
        self.assertEqual(style_minimum_score("DAY"), 70)
        self.assertEqual(style_minimum_score("SWING"), 75)
        self.assertEqual(style_minimum_adx("SCALPING"), 15.0)
        self.assertEqual(style_minimum_adx("DAY"), 20.0)
        self.assertEqual(style_minimum_adx("SWING"), 22.0)

    def test_labels_are_explicit(self):
        self.assertEqual(style_label("SCALPING"), "SCALPING TRADE")
        self.assertEqual(style_label("DAY"), "DAY TRADE")
        self.assertEqual(style_label("SWING"), "SWING TRADE")

    def test_invalid_timeframe_is_rejected(self):
        with self.assertRaises(ValueError):
            required_higher_timeframes("SCALPING", "15m")


if __name__ == "__main__":
    unittest.main()
