import unittest
from unittest.mock import patch

import config
import state
from market_scope import get_session_markets, is_market_allowed


class MarketScopeTests(unittest.TestCase):
    def test_only_requested_markets_are_configured(self):
        self.assertEqual(
            set(config.MARKETS),
            {"XAU/USD", "EUR/USD", "GBP/USD", "USD/JPY", "BTC/USD"},
        )

    def test_weekdays_include_gold_and_three_forex_pairs_only(self):
        expected = ["XAU/USD", "EUR/USD", "GBP/USD", "USD/JPY"]
        self.assertEqual(get_session_markets(config.MARKETS, 0), expected)
        self.assertNotIn("BTC/USD", get_session_markets(config.MARKETS, 0))

    def test_weekends_are_bitcoin_only(self):
        self.assertEqual(get_session_markets(config.MARKETS, 5), ["BTC/USD"])
        self.assertTrue(is_market_allowed("BTC/USD", 6))
        self.assertFalse(is_market_allowed("XAU/USD", 6))

    def test_gold_has_half_priority_setting(self):
        self.assertEqual(config.GOLD_BUDGET_SHARE, 0.5)


class ApiBudgetProtectionTests(unittest.TestCase):
    def test_background_jobs_stop_at_reserved_manual_allowance(self):
        fresh_state = state._empty_state()
        fresh_state["api_requests"] = (
            config.TWELVE_DATA_DAILY_CREDIT_LIMIT - config.API_RESERVE_REQUESTS
        )
        with patch.object(state, "_state", fresh_state):
            for purpose in ("auto", "monitor", "gold_monitor"):
                allowed, reason = state.reserve_api_request(purpose)
                self.assertFalse(allowed, purpose)
                self.assertIn("reserved daily API allowance", reason)

    def test_manual_analysis_can_use_reserved_allowance(self):
        fresh_state = state._empty_state()
        fresh_state["api_requests"] = (
            config.TWELVE_DATA_DAILY_CREDIT_LIMIT - config.API_RESERVE_REQUESTS
        )
        fresh_state["last_request_epoch"] = 0.0
        with patch.object(state, "_state", fresh_state), patch.object(
            state, "_save"
        ):
            allowed, _ = state.reserve_api_request("manual")
        self.assertTrue(allowed)


if __name__ == "__main__":
    unittest.main()
