import unittest
import os
import asyncio
from unittest.mock import patch, AsyncMock
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import economic_events as ee
import trading_economics as te


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

    def test_checkpoint_scheduler_catches_up_brief_downtime_without_late_event_alerts(self):
        now = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)
        event = {"scheduled_at": (now + timedelta(hours=23, minutes=45)).isoformat(), "checkpoints": {}}
        self.assertEqual(ee.due_checkpoints(event, now), ["24h"])
        event = {"scheduled_at": (now + timedelta(minutes=4)).isoformat(), "checkpoints": {}}
        self.assertEqual(ee.due_checkpoints(event, now), ["5m"])
        event = {"scheduled_at": (now + timedelta(minutes=2)).isoformat(), "checkpoints": {}}
        self.assertEqual(ee.due_checkpoints(event, now), [])

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

    def test_technical_conflict_reduces_confidence_without_overriding_fundamentals(self):
        item = self.event(forecast="3.4", previous="3.2")
        item["higher_is_better"] = True
        analysis = ee.directional_analysis(item, technical="BEARISH")
        self.assertEqual(analysis["fundamental_direction"], "BULLISH")
        self.assertEqual(analysis["combined_direction"], "BULLISH")
        self.assertLess(analysis["confidence"], 55)

    def test_technical_only_bias_is_labelled_low_confidence_when_fundamentals_missing(self):
        item = self.event()
        analysis = ee.directional_analysis(item, technical="BEARISH")
        self.assertEqual(analysis["fundamental_direction"], "NEUTRAL")
        self.assertEqual(analysis["combined_direction"], "BEARISH")
        self.assertEqual(analysis["confidence"], 25)

    def test_no_fabricated_direction_without_preference_metadata(self):
        item = self.event(forecast="3.1", previous="3.2")
        self.assertEqual(ee.directional_analysis(item)["direction"], "NEUTRAL")

    def test_5m_preserves_15m_forecast_without_material_change(self):
        item = self.event(forecast="3.1", previous="3.2")
        item["provider_context_status"] = "OFFICIAL_FALLBACK"
        ee.save_event(item)
        baseline_snapshot = {"status": "AVAILABLE", "direction": "BULLISH", "adx": 25, "breakout": "NONE", "volatility_pct": 0.2}
        current_snapshot = {"status": "AVAILABLE", "direction": "BULLISH", "adx": 27, "breakout": "NONE", "volatility_pct": 0.21}
        baseline = ee.record_checkpoint(item["id"], "15m", technical="BULLISH", technical_snapshot=baseline_snapshot)
        verified = ee.record_checkpoint(item["id"], "5m", technical="BULLISH", technical_snapshot=current_snapshot)
        self.assertEqual(verified["verification"], "CONFIRMED_UNCHANGED")
        self.assertEqual(verified["analysis"], baseline["analysis"])

    def test_5m_reports_technical_only_confirmation_when_global_context_is_not_configured(self):
        item = self.event(forecast="3.1", previous="3.2")
        baseline_snapshot = {"status": "AVAILABLE", "direction": "BULLISH", "adx": 25, "breakout": "NONE", "volatility_pct": 0.2}
        current_snapshot = {"status": "AVAILABLE", "direction": "BULLISH", "adx": 26, "breakout": "NONE", "volatility_pct": 0.21}
        ee.record_checkpoint(item["id"], "15m", technical="BULLISH", technical_snapshot=baseline_snapshot)
        verified = ee.record_checkpoint(item["id"], "5m", technical="BULLISH", technical_snapshot=current_snapshot)
        self.assertEqual(verified["verification"], "TECHNICAL_ONLY_CONFIRMED")

    def test_5m_does_not_claim_confirmation_without_fresh_market_data(self):
        item = self.event(forecast="3.1", previous="3.2")
        baseline_snapshot = {"status": "AVAILABLE", "direction": "BULLISH", "adx": 25, "breakout": "NONE", "volatility_pct": 0.2}
        baseline = ee.record_checkpoint(item["id"], "15m", technical="BULLISH", technical_snapshot=baseline_snapshot)
        verified = ee.record_checkpoint(item["id"], "5m", technical_snapshot={"status": "UNAVAILABLE", "reason": "provider timeout"})
        self.assertEqual(verified["verification"], "UNAVAILABLE")
        self.assertEqual(verified["analysis"], baseline["analysis"])

    def test_ordinary_ema_flip_with_weak_adx_does_not_revise_forecast(self):
        item = self.event(forecast="3.1", previous="3.2")
        item["provider_context_status"] = "OFFICIAL_FALLBACK"
        ee.save_event(item)
        baseline_snapshot = {"status": "AVAILABLE", "direction": "BULLISH", "adx": 18, "breakout": "NONE", "volatility_pct": 0.2}
        noisy_flip = {"status": "AVAILABLE", "direction": "BEARISH", "adx": 17, "breakout": "NONE", "volatility_pct": 0.21}
        baseline = ee.record_checkpoint(item["id"], "15m", technical="BULLISH", technical_snapshot=baseline_snapshot)
        verified = ee.record_checkpoint(item["id"], "5m", technical="BEARISH", technical_snapshot=noisy_flip)
        self.assertEqual(verified["verification"], "CONFIRMED_UNCHANGED")
        self.assertEqual(verified["analysis"], baseline["analysis"])

    def test_material_adx_change_without_direction_flip_is_reviewed_not_revised(self):
        item = self.event(forecast="3.1", previous="3.2")
        baseline_snapshot = {"status": "AVAILABLE", "direction": "BULLISH", "adx": 20, "breakout": "NONE", "volatility_pct": 0.2}
        stronger_trend = {"status": "AVAILABLE", "direction": "BULLISH", "adx": 30, "breakout": "NONE", "volatility_pct": 0.21}
        ee.record_checkpoint(item["id"], "15m", technical="BULLISH", technical_snapshot=baseline_snapshot)
        verified = ee.record_checkpoint(item["id"], "5m", technical="BULLISH", technical_snapshot=stronger_trend)
        self.assertEqual(verified["verification"], "REVIEWED_MATERIAL_CHANGE")
        self.assertEqual(verified["analysis"]["combined_direction"], "BULLISH")

    def test_five_minute_consensus_change_can_revise_combined_direction(self):
        item = self.event(forecast="3.1", previous="3.2")
        item["higher_is_better"] = False
        baseline_snapshot = {"status": "AVAILABLE", "direction": "BULLISH", "adx": 25, "breakout": "NONE", "volatility_pct": 0.2}
        ee.record_checkpoint(item["id"], "15m", technical="BULLISH", technical_snapshot=baseline_snapshot)
        item = ee.get_event(item["id"])
        item["forecast"] = "3.3"
        ee.save_event(item)
        current_snapshot = {"status": "AVAILABLE", "direction": "BULLISH", "adx": 26, "breakout": "NONE", "volatility_pct": 0.21}
        verified = ee.record_checkpoint(item["id"], "5m", technical="BULLISH", technical_snapshot=current_snapshot)
        self.assertEqual(verified["verification"], "DIRECTION_REVISED")
        self.assertEqual(verified["analysis"]["combined_direction"], "BEARISH")
        self.assertIn("consensus/model forecast changed materially", verified["material_changes"])

    def test_new_economic_news_is_reviewed_without_fabricating_direction(self):
        item = self.event(forecast="3.1", previous="3.2")
        item["higher_is_better"] = False
        baseline_snapshot = {"status": "AVAILABLE", "direction": "BULLISH", "adx": 25, "breakout": "NONE", "volatility_pct": 0.2}
        ee.record_checkpoint(item["id"], "15m", technical="BULLISH", technical_snapshot=baseline_snapshot)
        item = ee.get_event(item["id"])
        item["research_news"] = [{"title": "New central bank development", "description": "Headline text"}]
        ee.save_event(item)
        current_snapshot = {"status": "AVAILABLE", "direction": "BULLISH", "adx": 26, "breakout": "NONE", "volatility_pct": 0.21}
        verified = ee.record_checkpoint(item["id"], "5m", technical="BULLISH", technical_snapshot=current_snapshot)
        self.assertEqual(verified["verification"], "REVIEWED_MATERIAL_CHANGE")
        self.assertEqual(verified["analysis"]["combined_direction"], "BULLISH")
        self.assertIn("new provider economic-news headline(s) detected", verified["material_changes"])

    def test_5m_revises_only_after_material_market_change(self):
        item = self.event(forecast="3.1", previous="3.2")
        baseline_snapshot = {"status": "AVAILABLE", "direction": "BULLISH", "adx": 25, "breakout": "NONE", "volatility_pct": 0.2}
        changed_snapshot = {"status": "AVAILABLE", "direction": "BEARISH", "adx": 35, "breakout": "DOWNSIDE_BREAKOUT", "volatility_pct": 0.5}
        ee.record_checkpoint(item["id"], "15m", technical="BULLISH", technical_snapshot=baseline_snapshot)
        verified = ee.record_checkpoint(item["id"], "5m", technical="BEARISH", technical_snapshot=changed_snapshot)
        self.assertEqual(verified["verification"], "DIRECTION_REVISED")
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
        self.assertEqual(report["forecast_score"], "Consensus-forecast alignment: 1/1 windows (100%)")
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
        self.assertEqual(report["forecast_score"], "Directional alignment only: 1/1 windows (100%)")

    def test_global_provider_requires_distribution_licence_flag(self):
        with patch.dict(os.environ, {"TRADING_ECONOMICS_API_KEY": "test-key", "TRADING_ECONOMICS_DISTRIBUTION_LICENSED": "false"}):
            self.assertFalse(te.configured())
        with patch.dict(os.environ, {"TRADING_ECONOMICS_API_KEY": "test-key", "TRADING_ECONOMICS_DISTRIBUTION_LICENSED": "true"}):
            self.assertTrue(te.configured())

    def test_directional_analysis_parses_provider_units(self):
        item = self.event(forecast="180K", previous="175K")
        item["higher_is_better"] = True
        self.assertEqual(ee.directional_analysis(item)["direction"], "BULLISH")
        item["actual"] = "170K"
        self.assertEqual(ee.directional_analysis(item, phase="post-event")["direction"], "BEARISH")

    def test_provider_calendar_imports_consensus_and_deduplicates(self):
        when = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        row = {
            "CalendarId": "te-123", "Date": when, "Country": "United States",
            "Event": "Non Farm Payrolls", "Category": "Employment",
            "Importance": 3, "Forecast": "180K", "Previous": "175K",
            "Revised": "170K", "Actual": "", "Source": "Bureau of Labor Statistics",
            "SourceURL": "https://www.bls.gov/",
        }
        with patch.object(te, "_get", new=AsyncMock(return_value=[row])):
            imported = asyncio.run(te.fetch_calendar_events(30))
            repeated = asyncio.run(te.fetch_calendar_events(30))
        self.assertEqual(len(imported), 1)
        self.assertEqual(len(repeated), 0)
        saved = ee.get_event(imported[0]["id"])
        self.assertEqual(saved["provider_calendar_id"], "te-123")
        self.assertEqual(saved["forecast"], "180K")
        self.assertEqual(saved["forecast_type"], "market_consensus")
        self.assertEqual(saved["previous"], "175K")
        self.assertEqual(saved["provider_revised_previous"], "170K")

    def test_provider_bond_yield_context_is_currency_specific(self):
        event = self.event()
        event["currency"] = "USD"
        rows = [
            {"Country": "United States", "Name": "US 10Y", "Last": 4.25,
             "DailyChange": 0.02, "WeeklyChange": 0.08, "MonthlyChange": 0.1,
             "Date": "2026-11-07T12:00:00"},
            {"Country": "United Kingdom", "Name": "UK 10Y", "Last": 4.1},
        ]
        with patch.dict(os.environ, {"TRADING_ECONOMICS_API_KEY": "key", "TRADING_ECONOMICS_DISTRIBUTION_LICENSED": "true"}), patch.object(te, "_get", new=AsyncMock(return_value=rows)):
            yields = asyncio.run(te.fetch_bond_yield_context(event))
        self.assertEqual(len(yields), 1)
        self.assertEqual(yields[0]["country"], "United States")
        self.assertEqual(yields[0]["yield_pct"], 4.25)

    def test_provider_actual_retrieval_includes_revision_and_source(self):
        event = self.event(forecast="180K", previous="175K")
        event["provider_calendar_id"] = "te-456"
        row = [{
            "CalendarId": "te-456", "Event": "Non Farm Payrolls",
            "Actual": "190K", "Previous": "180K", "Revised": "175K",
            "Forecast": "185K", "Source": "BLS", "SourceURL": "https://www.bls.gov/",
        }]
        with patch.dict(os.environ, {"TRADING_ECONOMICS_API_KEY": "key", "TRADING_ECONOMICS_DISTRIBUTION_LICENSED": "true"}), patch.object(te, "_get", new=AsyncMock(return_value=row)):
            actual = asyncio.run(te.fetch_event_actual(event))
        self.assertEqual(actual["value"], "190K")
        self.assertEqual(actual["previous_value"], "180K")
        self.assertEqual(actual["revised_previous"], "175K")
        self.assertEqual(actual["higher_is_better"], True)

    def test_bls_state_unemployment_is_not_misclassified_as_national_event(self):
        self.assertFalse(ee.is_high_impact("State Employment and Unemployment (Monthly)"))
        self.assertTrue(ee.is_high_impact("Employment Situation for October"))
        self.assertTrue(ee.is_high_impact("Unemployment Rate"))

    def test_companion_labour_metrics_adjust_post_release_confidence(self):
        item = self.event(forecast="180K", previous="175K")
        item["higher_is_better"] = True
        item["actual_details"] = {
            "metrics": [{
                "name": "Unemployment rate", "value": 4.4,
                "previous_value": 4.3, "higher_is_better": False,
            }]
        }
        ee.save_event(item)
        report = ee.record_actual(item["id"], "190K")
        self.assertEqual(report["analysis"]["direction"], "BULLISH")
        self.assertLess(report["analysis"]["confidence"], 55)
        self.assertTrue(any("Companion Unemployment rate" in reason for reason in report["analysis"]["reasons"]))

    def test_provider_values_support_units(self):
        self.assertEqual(ee._to_number("175K"), 175000)
        self.assertEqual(ee._to_number("4.2%"), 4.2)
        self.assertEqual(ee._to_number("$1.2B"), 1_200_000_000)

    def test_provider_consensus_is_preferred_to_provider_model_estimate(self):
        self.assertEqual(te._consensus({"Forecast": "3.1%", "TEForecast": "3.2%"}), ("3.1%", "market_consensus"))
        self.assertEqual(te._consensus({"Forecast": "", "TEForecast": "3.2%"}), ("3.2%", "provider_model_estimate"))

    def test_provider_event_semantics_are_event_specific(self):
        self.assertFalse(te._higher_is_better("US CPI YoY"))
        self.assertFalse(te._higher_is_better("Unemployment Rate"))
        self.assertTrue(te._higher_is_better("Non Farm Payrolls"))

    def test_usd_pair_biases_are_inverted_correctly(self):
        item = self.event(forecast="3.4", previous="3.2")
        item["higher_is_better"] = True
        analysis = ee.directional_analysis(item)
        self.assertEqual(analysis["market_biases"]["EUR/USD"], "SELL")
        self.assertEqual(analysis["market_biases"]["USD/JPY"], "BUY")
        self.assertEqual(analysis["market_biases"]["XAU/USD"], "SELL")


if __name__ == "__main__":
    unittest.main()
