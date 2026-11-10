import unittest

import economic_events as ee


class EventTradePlanTests(unittest.TestCase):
    def setUp(self):
        self.analysis = {"combined_direction": "BULLISH", "fundamental_direction": "BULLISH", "confidence": 65}

    def snapshot(self, **overrides):
        data = {
            "status": "AVAILABLE",
            "market": "USD/JPY",
            "timeframe": "5m",
            "direction": "BULLISH",
            "currency_direction": "BULLISH",
            "ema_aligned": True,
            "adx": 27.0,
            "atr": 0.001,
            "close": 1.103,
            "signal_candle_high": 1.105,
            "signal_candle_low": 1.100,
            "swing_high": 1.106,
            "swing_low": 1.098,
            "candle_time": "2026-11-10T13:00:00+00:00",
        }
        data.update(overrides)
        return data

    def test_aligned_setup_recommends_one_minute_entry_and_five_minute_confirmation(self):
        plan = ee.build_event_trade_plan(self.analysis, self.snapshot())
        self.assertEqual(plan["status"], "CONDITIONAL_SETUP")
        self.assertEqual(plan["execution_timeframe"], "1m")
        self.assertEqual(plan["confirmation_timeframe"], "5m")
        self.assertEqual(plan["context_timeframe"], "15m")
        self.assertEqual(plan["side"], "BUY")
        self.assertLess(plan["stop_loss"], plan["entry_trigger"])
        self.assertEqual(len(plan["take_profits"]), 3)
        self.assertGreater(plan["take_profits"][0], plan["entry_trigger"])
        self.assertGreater(plan["take_profits"][1], plan["take_profits"][0])
        self.assertGreater(plan["take_profits"][2], plan["take_profits"][1])

    def test_no_clear_direction_never_creates_trade_levels(self):
        plan = ee.build_event_trade_plan({"combined_direction": "NEUTRAL"}, self.snapshot())
        self.assertEqual(plan["status"], "NO_TRADE")
        self.assertNotIn("entry_trigger", plan)

    def test_fundamental_technical_conflict_blocks_trade_plan(self):
        plan = ee.build_event_trade_plan(
            self.analysis, self.snapshot(currency_direction="BEARISH")
        )
        self.assertEqual(plan["status"], "NO_TRADE")

    def test_weak_adx_blocks_trade_plan(self):
        plan = ee.build_event_trade_plan(self.analysis, self.snapshot(adx=18))
        self.assertEqual(plan["status"], "NO_TRADE")

    def test_technical_only_bias_is_not_enough_for_event_trade(self):
        plan = ee.build_event_trade_plan(
            {"combined_direction": "BEARISH", "fundamental_direction": "NEUTRAL", "confidence": 25},
            self.snapshot(),
        )
        self.assertEqual(plan["status"], "NO_TRADE")
        self.assertIn("technical-only", plan["reason"])

    def test_unavailable_market_data_blocks_trade_plan(self):
        plan = ee.build_event_trade_plan(
            self.analysis, {"status": "UNAVAILABLE", "reason": "feed timeout"}
        )
        self.assertEqual(plan["status"], "NO_TRADE")

    def test_bearish_pair_plan_places_stop_above_entry_and_targets_below(self):
        plan = ee.build_event_trade_plan(
            {"combined_direction": "BEARISH", "fundamental_direction": "BEARISH", "confidence": 65},
            self.snapshot(
                direction="BEARISH", currency_direction="BEARISH",
                signal_candle_high=1.105, signal_candle_low=1.100,
                swing_high=1.107, swing_low=1.099,
            ),
        )
        self.assertEqual(plan["status"], "CONDITIONAL_SETUP")
        self.assertEqual(plan["side"], "SELL")
        self.assertGreater(plan["stop_loss"], plan["entry_trigger"])
        self.assertLess(plan["take_profits"][0], plan["entry_trigger"])


if __name__ == "__main__":
    unittest.main()
