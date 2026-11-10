import unittest

import economic_events_bot as bot


class EconomicEventChartDeliveryTests(unittest.TestCase):
    def test_history_prefers_real_market_chart_over_later_summary_card(self):
        event = {
            "chart_images": [
                {
                    "file_id": "market-chart-id",
                    "chart_type": "market_candlestick",
                    "market": "EUR/USD",
                    "timeframe": "5m",
                },
                {
                    "file_id": "summary-card-id",
                    "chart_type": "event_summary",
                    "checkpoint": "history_view",
                },
            ]
        }
        self.assertEqual(
            bot._latest_market_chart(event)["file_id"],
            "market-chart-id",
        )

    def test_legacy_market_chart_metadata_is_supported(self):
        event = {
            "chart_images": [
                {
                    "file_id": "legacy-market-chart-id",
                    "market": "XAU/USD",
                    "timeframe": "15m",
                }
            ]
        }
        self.assertEqual(
            bot._latest_market_chart(event)["file_id"],
            "legacy-market-chart-id",
        )

    def test_summary_only_history_is_not_mistaken_for_market_chart(self):
        event = {
            "chart_images": [
                {"file_id": "summary-card-id", "chart_type": "event_summary"}
            ]
        }
        self.assertIsNone(bot._latest_market_chart(event))


if __name__ == "__main__":
    unittest.main()
