import unittest

from market_scope import (
    get_session_markets,
    get_session_name,
    get_strong_signal_markets,
    is_market_allowed,
)


MARKETS = {
    "XAU/USD": "XAU/USD",
    "EUR/USD": "EUR/USD",
    "GBP/USD": "GBP/USD",
    "USD/JPY": "USD/JPY",
    "BTC/USD": "BTC/USD",
}

WEEKDAY_EXPECTED = [
    "XAU/USD", "EUR/USD", "GBP/USD", "USD/JPY",
]


class MarketSessionScopeTests(unittest.TestCase):
    def test_weekdays_include_gold_and_three_retained_forex_pairs(self):
        for weekday in range(5):
            with self.subTest(weekday=weekday):
                self.assertEqual(get_session_markets(MARKETS, weekday), WEEKDAY_EXPECTED)
                self.assertEqual(get_session_name(weekday), "weekday")

    def test_saturday_and_sunday_include_bitcoin_only(self):
        for weekday in (5, 6):
            with self.subTest(weekday=weekday):
                self.assertEqual(get_session_markets(MARKETS, weekday), ["BTC/USD"])
                self.assertEqual(get_session_name(weekday), "weekend")

    def test_strong_signals_uses_same_session_allow_list(self):
        self.assertEqual(get_strong_signal_markets(MARKETS, 2), WEEKDAY_EXPECTED)
        self.assertEqual(get_strong_signal_markets(MARKETS, 5), ["BTC/USD"])

    def test_session_membership_rejects_cross_session_markets(self):
        self.assertFalse(is_market_allowed("BTC/USD", 0))
        self.assertFalse(is_market_allowed("EUR/USD", 5))
        self.assertFalse(is_market_allowed("XAU/USD", 6))
        self.assertTrue(is_market_allowed("BTC/USD", 6))
        self.assertTrue(is_market_allowed("XAU/USD", 1))

    def test_missing_configured_symbols_are_not_returned(self):
        forex_only = {key: value for key, value in MARKETS.items() if key != "BTC/USD"}
        self.assertEqual(get_session_markets(forex_only, 5), [])
        self.assertNotIn("BTC/USD", get_session_markets(forex_only, 1))


if __name__ == "__main__":
    unittest.main()
