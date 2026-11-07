import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import economic_events as ee


class EconomicEventTests(unittest.TestCase):
    def setUp(self):
        self.store = []
        self.patcher = patch.object(ee.state, "get_scanner_state", side_effect=lambda key, default=None: self.store if key == "economic_events" else default)
        self.setter = patch.object(ee.state, "set_scanner_state", side_effect=lambda key, value: self.store.__setitem__(slice(None), value) if key == "economic_events" else None)
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

    def test_no_fabricated_direction_without_preference_metadata(self):
        item = self.event(forecast="3.1", previous="3.2")
        self.assertEqual(ee.directional_analysis(item)["direction"], "NEUTRAL")

    def test_5m_preserves_15m_forecast_without_material_change(self):
        item = self.event(forecast="3.1", previous="3.2")
        baseline = ee.record_checkpoint(item["id"], "15m", technical="BULLISH")
        verified = ee.record_checkpoint(item["id"], "5m", technical="BEARISH")
        self.assertEqual(verified["verification"], "CONFIRMED_UNCHANGED")
        self.assertEqual(verified["analysis"], baseline["analysis"])

    def test_5m_flags_material_change_for_review(self):
        item = self.event(forecast="3.1", previous="3.2")
        ee.record_checkpoint(item["id"], "15m")
        verified = ee.record_checkpoint(item["id"], "5m", evidence_note="Official release schedule revised.")
        self.assertEqual(verified["verification"], "REVIEW_REQUIRED")

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


if __name__ == "__main__":
    unittest.main()
