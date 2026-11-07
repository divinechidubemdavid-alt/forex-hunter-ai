import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import economic_events as ee


class EconomicEventTests(unittest.TestCase):
    def setUp(self):
        self.store = []
        self.patcher = patch.object(
            ee.state, "get_scanner_state",
            side_effect=lambda key, default=None: self.store if key == "economic_events" else default,
        )
        self.setter = patch.object(
            ee.state, "set_scanner_state",
            side_effect=lambda key, value: self.store.__setitem__(slice(None), value) if key == "economic_events" else None,
        )
        self.patcher.start()
        self.setter.start()
        self.addCleanup(self.patcher.stop)
        self.addCleanup(self.setter.stop)

    def event(self, **kwargs):
        return ee.create_event("US CPI", datetime.now(timezone.utc) + timedelta(hours=25), **kwargs)

    def test_create_and_list_event(self):
        item = self.event(forecast="3.1", previous="3.2")
        self.assertEqual(ee.get_event(item["id"])["title"], "US CPI")
        self.assertEqual(len(ee.list_events()), 1)

    def test_private_events_are_scoped_to_owner(self):
        own = self.event(owner_id="100")
        other = self.event(owner_id="200")
        global_event = self.event()
        self.assertEqual({e["id"] for e in ee.list_events(user_id="100")}, {own["id"], global_event["id"]})
        self.assertEqual({e["id"] for e in ee.list_events(user_id="200")}, {other["id"], global_event["id"]})
        self.assertEqual(len(ee.list_events(user_id="*")), 3)

    def test_no_fabricated_direction_without_preference_metadata(self):
        item = self.event(forecast="3.1", previous="3.2")
        self.assertEqual(ee.directional_analysis(item)["direction"], "NEUTRAL")

    def test_5m_preserves_15m_forecast_without_material_change(self):
        item = self.event(forecast="3.1", previous="3.2")
        baseline_snapshot = {"status": "AVAILABLE", "direction": "BULLISH", "adx": 25, "breakout": "NONE", "volatility_pct": 0.2}
        current_snapshot = {"status": "AVAILABLE", "direction": "BULLISH", "adx": 27, "breakout": "NONE", "volatility_pct": 0.21}
        baseline = ee.record_checkpoint(item["id"], "15m", technical="BULLISH", technical_snapshot=baseline_snapshot)
        verified = ee.record_checkpoint(item["id"], "5m", technical="BULLISH", technical_snapshot=current_snapshot)
        self.assertEqual(verified["verification"], "CONFIRMED_UNCHANGED")
        self.assertEqual(verified["analysis"], baseline["analysis"])

    def test_5m_does_not_claim_confirmation_without_fresh_market_data(self):
        item = self.event(forecast="3.1", previous="3.2")
        baseline_snapshot = {"status": "AVAILABLE", "direction": "BULLISH", "adx": 25, "breakout": "NONE", "volatility_pct": 0.2}
        baseline = ee.record_checkpoint(item["id"], "15m", technical="BULLISH", technical_snapshot=baseline_snapshot)
        verified = ee.record_checkpoint(item["id"], "5m", technical_snapshot={"status": "UNAVAILABLE", "reason": "provider timeout"})
        self.assertEqual(verified["verification"], "UNAVAILABLE")
        self.assertEqual(verified["analysis"], baseline["analysis"])

    def test_5m_revises_only_after_material_market_change(self):
        item = self.event(forecast="3.1", previous="3.2")
        baseline_snapshot = {"status": "AVAILABLE", "direction": "BULLISH", "adx": 25, "breakout": "NONE", "volatility_pct": 0.2}
        changed_snapshot = {"status": "AVAILABLE", "direction": "BEARISH", "adx": 35, "breakout": "DOWNSIDE_BREAKOUT", "volatility_pct": 0.5}
        ee.record_checkpoint(item["id"], "15m", technical="BULLISH", technical_snapshot=baseline_snapshot)
        verified = ee.record_checkpoint(item["id"], "5m", technical="BEARISH", technical_snapshot=changed_snapshot)
        self.assertEqual(verified["verification"], "DIRECTION_REVIEWED")
        self.assertEqual(verified["prior_analysis"]["technical_direction"], "BULLISH")
        self.assertEqual(verified["analysis"]["technical_direction"], "BEARISH")

    def test_post_event_report_saved_with_actual_forecast_previous(self):
        item = self.event(forecast="3.1", previous="3.2")
        report = ee.record_actual(item["id"], "3.4", revised_previous="3.3")
        self.assertEqual(report["actual"], "3.4")
        self.assertEqual(ee.get_event(item["id"])["post_event_report"], report)

    def test_only_high_impact_terms_are_imported(self):
        self.assertTrue(ee.is_high_impact("US Consumer Price Index"))
        self.assertFalse(ee.is_high_impact("Minor regional business survey"))

    def test_post_event_direction_compares_actual_to_forecast(self):
        item = self.event(forecast="3.4", previous="3.2")
        item["higher_is_better"] = True
        ee.save_event(item)
        report = ee.record_actual(item["id"], "3.1")
        self.assertEqual(report["analysis"]["direction"], "BEARISH")
        self.assertIn("forecast", report["analysis"]["reasons"][0])

    def test_post_release_market_reaction_is_saved_and_scored(self):
        item = self.event(forecast="3.1", previous="3.2")
        item["higher_is_better"] = False
        ee.save_event(item)
        ee.record_actual(item["id"], "3.4")
        report = ee.record_market_reaction(
            item["id"],
            {"status": "AVAILABLE", "market": "EUR/USD", "currency_direction": "BEARISH",
             "direction": "BEARISH", "adx": 28, "rsi": 42, "volatility_pct": 0.3,
             "breakout": "NONE", "source": "test"},
            5,
        )
        self.assertEqual(report["forecast_score"], "ALIGNED")
        self.assertEqual(report["forecast_score_value"], 1)
        self.assertEqual(report["market_reactions"][0]["minutes_after"], 5)

    def test_no_consensus_does_not_claim_forecast_score(self):
        item = self.event(previous="1.1")
        item["higher_is_better"] = True
        ee.save_event(item)
        ee.record_actual(item["id"], "1.2")
        report = ee.record_market_reaction(
            item["id"],
            {"status": "AVAILABLE", "market": "EUR/USD", "currency_direction": "BULLISH",
             "direction": "BEARISH", "source": "test"},
            5,
        )
        self.assertEqual(report["forecast_score"], "DIRECTIONAL_ALIGNMENT_ONLY")

    def test_usd_pair_biases_are_inverted_correctly(self):
        item = self.event(forecast="3.4", previous="3.2")
        item["higher_is_better"] = True
        analysis = ee.directional_analysis(item)
        self.assertEqual(analysis["market_biases"]["EUR/USD"], "SELL")
        self.assertEqual(analysis["market_biases"]["USD/JPY"], "BUY")
        self.assertEqual(analysis["market_biases"]["XAU/USD"], "SELL")


if __name__ == "__main__":
    unittest.main()
