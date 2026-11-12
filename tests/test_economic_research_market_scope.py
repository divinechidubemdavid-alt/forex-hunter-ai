import unittest

import economic_research as research


class EconomicResearchMarketScopeTests(unittest.TestCase):
    def test_only_retained_market_proxies_are_available(self):
        self.assertEqual(research._market_for_currency("USD"), "EUR/USD")
        self.assertEqual(research._market_for_currency("EUR"), "EUR/USD")
        self.assertEqual(research._market_for_currency("GBP"), "GBP/USD")
        self.assertEqual(research._market_for_currency("JPY"), "USD/JPY")
        self.assertEqual(research._market_for_currency("XAU"), "XAU/USD")

    def test_retired_currency_pairs_have_no_event_research_proxy(self):
        for currency in ("AUD", "CAD", "CHF"):
            with self.subTest(currency=currency):
                self.assertIsNone(research._market_for_currency(currency))


if __name__ == "__main__":
    unittest.main()
