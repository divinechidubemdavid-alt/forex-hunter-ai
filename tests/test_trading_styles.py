import unittest

from trading_styles import (
    AUTOMATIC_TRADING_STYLE,
    STRONG_SIGNALS_TRADING_STYLE,
    is_automatic_style,
    is_strong_signal_style,
    required_higher_timeframes,
    style_has_trade_levels,
    style_label,
    style_timeframes,
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

    def test_labels_are_explicit(self):
        self.assertEqual(style_label("SCALPING"), "SCALPING TRADE")
        self.assertEqual(style_label("DAY"), "DAY TRADE")
        self.assertEqual(style_label("SWING"), "SWING TRADE")

    def test_invalid_timeframe_is_rejected(self):
        with self.assertRaises(ValueError):
            required_higher_timeframes("SCALPING", "15m")


if __name__ == "__main__":
    unittest.main()
