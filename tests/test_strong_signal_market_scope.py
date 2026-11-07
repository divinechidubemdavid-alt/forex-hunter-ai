import unittest

from market_scope import get_strong_signal_markets


MARKETS = {
    "XAU/USD": "XAU/USD",
    "EUR/USD": "EUR/USD",
    "GBP/USD": "GBP/USD",
    "USD/JPY": "USD/JPY",
    "AUD/USD": "AUD/USD",
    "USD/CAD": "USD/CAD",
    "USD/CHF": "USD/CHF",
    "BTC/USD": "BTC/USD",
}


class StrongSignalMarketScopeTests(unittest.TestCase):
    def test_saturday_scans_bitcoin_only(self):
        self.assertEqual(get_strong_signal_markets(MARKETS, 5), ["BTC/USD"])

    def test_sunday_scans_bitcoin_only(self):
        self.assertEqual(get_strong_signal_markets(MARKETS, 6), ["BTC/USD"])

    def test_weekdays_scan_every_configured_market(self):
        expected = list(MARKETS)
        for weekday in range(5):
            with self.subTest(weekday=weekday):
                self.assertEqual(get_strong_signal_markets(MARKETS, weekday), expected)

    def test_weekend_without_bitcoin_has_no_eligible_markets(self):
        forex_only = {key: value for key, value in MARKETS.items() if key != "BTC/USD"}
        self.assertEqual(get_strong_signal_markets(forex_only, 5), [])


if __name__ == "__main__":
    unittest.main()
