import unittest
from unittest.mock import patch

import config
import state
from auto_scanner import _eligible_markets
from bot import _active_market_tracked_signals
from market_scope import get_session_markets, is_market_allowed
from zoneinfo import ZoneInfo
from datetime import datetime


class MarketScopeTests(unittest.TestCase):
    def test_only_requested_markets_are_configured(self):
        self.assertEqual(
            set(config.MARKETS),
            {"XAU/USD", "GBP/USD", "USD/JPY", "BTC/USD"},
        )

    def test_weekdays_include_gold_and_three_forex_pairs_only(self):
        expected = ["XAU/USD", "GBP/USD", "USD/JPY"]
        self.assertEqual(get_session_markets(config.MARKETS, 0), expected)
        self.assertNotIn("BTC/USD", get_session_markets(config.MARKETS, 0))

    def test_weekends_are_bitcoin_only(self):
        self.assertEqual(get_session_markets(config.MARKETS, 5), ["BTC/USD"])
        self.assertTrue(is_market_allowed("BTC/USD", 6))
        self.assertFalse(is_market_allowed("XAU/USD", 6))

    def test_gold_has_half_priority_setting(self):
        self.assertEqual(config.GOLD_BUDGET_SHARE, 0.5)

    def test_automatic_scanner_uses_only_retained_weekday_markets(self):
        now = datetime(2026, 11, 9, 12, 0, tzinfo=ZoneInfo("Africa/Lagos"))
        self.assertEqual(
            _eligible_markets(now),
            ["XAU/USD", "GBP/USD", "USD/JPY"],
        )

    def test_automatic_scanner_is_bitcoin_only_on_weekends(self):
        now = datetime(2026, 11, 14, 12, 0, tzinfo=ZoneInfo("Africa/Lagos"))
        self.assertEqual(_eligible_markets(now), ["BTC/USD"])


class ActiveTrackedMarketScopeTests(unittest.TestCase):
    def test_retired_market_signals_are_hidden_from_active_tracking_not_deleted(self):
        rows = [
            {"id": "1", "symbol": "XAU/USD", "status": "TRACKING"},
            {"id": "2", "symbol": "EUR/USD", "status": "TRACKING"},
            {"id": "3", "symbol": "GBP/USD", "status": "TRACKING"},
        ]
        with patch("bot.state.get_tracked_signals", return_value=rows):
            visible = _active_market_tracked_signals("123")
        self.assertEqual([item["id"] for item in visible], ["1", "3"])
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[1]["symbol"], "EUR/USD")


class ApiBudgetProtectionTests(unittest.TestCase):
    def _fresh_state(self):
        return state._empty_state()

    def test_background_jobs_stop_at_reserved_manual_allowance(self):
        fresh = self._fresh_state()
        fresh["api_requests"] = (
            config.TWELVE_DATA_DAILY_CREDIT_LIMIT - config.API_RESERVE_REQUESTS
        )
        with patch.object(state, "_state", fresh):
            for purpose in ("auto", "monitor", "gold_monitor"):
                allowed, reason = state.reserve_api_request(purpose)
                self.assertFalse(allowed, purpose)
                self.assertIn("reserved daily API allowance", reason)

    def test_gold_background_pool_is_capped_at_half(self):
        fresh = self._fresh_state()
        fresh["api_requests"] = 350
        fresh["api_gold_monitor_requests"] = 350
        fresh["last_request_epoch"] = 0.0
        with patch.object(state, "_state", fresh):
            allowed, reason = state.reserve_api_request("gold_monitor")
        self.assertFalse(allowed)
        self.assertIn("Gold monitoring reached", reason)

    def test_other_background_pool_is_capped_at_half(self):
        fresh = self._fresh_state()
        fresh["api_requests"] = 350
        fresh["api_other_background_requests"] = 350
        fresh["last_request_epoch"] = 0.0
        with patch.object(state, "_state", fresh):
            allowed, reason = state.reserve_api_request("auto")
        self.assertFalse(allowed)
        self.assertIn("Other background monitoring reached", reason)

    def test_gold_request_increments_only_gold_pool(self):
        fresh = self._fresh_state()
        fresh["last_request_epoch"] = 0.0
        with patch.object(state, "_state", fresh), patch.object(state, "_save"):
            allowed, _ = state.reserve_api_request("gold_monitor")
        self.assertTrue(allowed)
        self.assertEqual(fresh["api_requests"], 1)
        self.assertEqual(fresh["api_gold_monitor_requests"], 1)
        self.assertEqual(fresh["api_other_background_requests"], 0)

    def test_manual_analysis_can_use_reserved_allowance(self):
        fresh = self._fresh_state()
        fresh["api_requests"] = (
            config.TWELVE_DATA_DAILY_CREDIT_LIMIT - config.API_RESERVE_REQUESTS
        )
        fresh["last_request_epoch"] = 0.0
        with patch.object(state, "_state", fresh), patch.object(state, "_save"):
            allowed, _ = state.reserve_api_request("manual")
        self.assertTrue(allowed)

    def test_old_user_preferences_drop_retired_pairs(self):
        fresh = self._fresh_state()
        fresh["users"]["123"] = {
            "markets": [
                "XAU/USD", "EUR/USD", "AUD/USD", "USD/CAD",
                "USD/CHF", "BTC/USD",
            ]
        }
        with patch.object(state, "_state", fresh), patch.object(state, "_save"):
            user = state.ensure_user("123")
        self.assertEqual(
            user["markets"],
            ["XAU/USD", "BTC/USD"],
        )

    def test_utc_day_rollover_resets_all_api_pools(self):
        fresh = self._fresh_state()
        fresh.update({
            "date": "2026-11-12",
            "api_requests": 700,
            "api_gold_monitor_requests": 350,
            "api_other_background_requests": 350,
            "last_request_epoch": 1794438000.0,
        })
        with patch.object(state, "_state", fresh), patch.object(
            state, "_today", return_value="2026-11-12"
        ):
            state._refresh_daily_counter()
        self.assertEqual(fresh["api_requests"], 0)
        self.assertEqual(fresh["api_gold_monitor_requests"], 0)
        self.assertEqual(fresh["api_other_background_requests"], 0)
        self.assertEqual(fresh["last_request_epoch"], 0.0)

    def test_migrating_old_local_date_preserves_same_utc_day_requests(self):
        from datetime import datetime, timezone

        fresh = self._fresh_state()
        current_utc = datetime(2026, 11, 11, 23, 30, tzinfo=timezone.utc)
        fresh.update({
            "date": "2026-11-12",
            "api_requests": 12,
            "api_gold_monitor_requests": 5,
            "api_other_background_requests": 7,
            "last_request_epoch": current_utc.timestamp(),
        })
        with patch.object(state, "_state", fresh), patch.object(
            state, "_today", return_value="2026-11-11"
        ):
            state._refresh_daily_counter()
        self.assertEqual(fresh["date"], "2026-11-11")
        self.assertEqual(fresh["api_requests"], 12)
        self.assertEqual(fresh["api_gold_monitor_requests"], 5)
        self.assertEqual(fresh["api_other_background_requests"], 7)


if __name__ == "__main__":
    unittest.main()
