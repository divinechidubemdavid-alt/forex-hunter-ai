import unittest

from economic_event_history import event_conversation, performance_summary


class EconomicEventHistoryTests(unittest.TestCase):
    def test_conversation_includes_checkpoint_and_post_release_records(self):
        event = {
            "history": [
                {
                    "checkpoint": "15m",
                    "created_at": "2026-11-07T10:00:00+00:00",
                    "analysis": {
                        "combined_direction": "BULLISH",
                        "confidence": 72,
                        "reasons": ["Fundamentals and technicals aligned."],
                    },
                },
                {
                    "post_event_report": {
                        "created_at": "2026-11-07T10:30:00+00:00",
                        "analysis": {"direction": "BULLISH", "confidence": 80},
                        "notes": "Official actual recorded.",
                    }
                },
            ]
        }
        rows = event_conversation(event)
        self.assertEqual([row["stage"] for row in rows], ["15M", "POST-RELEASE"])
        self.assertEqual(rows[0]["confidence"], 72)

    def test_performance_only_scores_events_with_market_reaction(self):
        events = [
            {
                "final_15m": {"analysis": {"direction": "BULLISH"}},
                "post_event_report": {
                    "market_reactions": [{"direction": "BULLISH"}]
                },
            },
            {
                "final_15m": {"analysis": {"direction": "BEARISH"}},
                "post_event_report": {
                    "market_reactions": [{"direction": "BULLISH"}]
                },
            },
            {"final_15m": {"analysis": {"direction": "NEUTRAL"}}},
        ]
        result = performance_summary(events)
        self.assertEqual(result["directional_scored"], 2)
        self.assertEqual(result["directional_correct"], 1)
        self.assertEqual(result["directional_accuracy_pct"], 50.0)
        self.assertEqual(result["unscored"], 1)

    def test_performance_scores_each_market_and_each_post_release_horizon(self):
        event = {
            "final_15m": {
                "analysis": {
                    "combined_direction": "BULLISH",
                    "market_biases": {"XAU/USD": "SELL", "GBP/USD": "SELL", "USD/JPY": "BUY"},
                }
            },
            "post_event_report": {
                "analysis": {"combined_direction": "BULLISH"},
                "market_reactions": [
                    {"minutes_after": 1, "market": "XAU/USD", "direction": "BULLISH", "pair_direction": "BEARISH"},
                    {"minutes_after": 5, "market": "XAU/USD", "direction": "BULLISH", "pair_direction": "BEARISH"},
                    {"minutes_after": 1, "market": "GBP/USD", "direction": "BEARISH", "pair_direction": "BULLISH"},
                    {"minutes_after": 1440, "market": "GBP/USD", "direction": "BULLISH", "pair_direction": "BEARISH"},
                    {"minutes_after": 1440, "market": "USD/JPY", "direction": "BULLISH", "pair_direction": "BULLISH"},
                ],
            },
        }
        result = performance_summary([event])
        self.assertEqual(result["by_market"]["XAU/USD"]["accuracy_pct"], 100.0)
        self.assertEqual(result["by_market"]["GBP/USD"]["accuracy_pct"], 50.0)
        self.assertEqual(result["by_market"]["GBP/USD"]["final_24h_accuracy_pct"], 100.0)
        self.assertEqual(result["by_market"]["USD/JPY"]["final_24h_accuracy_pct"], 100.0)
        self.assertEqual(result["by_horizon"]["1m"]["observations"], 2)
        self.assertEqual(result["by_horizon"]["1440m"]["observations"], 3)
        self.assertEqual(result["directional_scored"], 1)
        self.assertEqual(result["directional_correct"], 1)

    def test_checkpoint_conversation_includes_per_market_technical_summary(self):
        rows = event_conversation({
            "history": [{
                "checkpoint": "15m", "created_at": "2026-11-07T10:00:00+00:00",
                "analysis": {"direction": "BULLISH", "confidence": 70, "reasons": []},
                "market_technical_snapshots": {
                    "XAU/USD": {"direction": "BEARISH", "adx": 24},
                    "GBP/USD": {"direction": "BULLISH", "adx": 28},
                },
            }]
        })
        self.assertIn("XAU/USD BEARISH", rows[0]["summary"])
        self.assertIn("GBP/USD BULLISH", rows[0]["summary"])

    def test_events_without_a_final_checkpoint_are_not_counted_as_neutral(self):
        result = performance_summary([{"status": "scheduled"}])
        self.assertEqual(result["not_finalized"], 1)
        self.assertEqual(result["no_clear_direction"], 0)

    def test_trade_r_is_counted_only_when_outcome_is_recorded(self):
        result = performance_summary([
            {"trade_outcome": {"result": "WIN", "r_multiple": 2}},
            {"trade_outcome": {"result": "LOSS", "r_multiple": -1}},
            {"trade_outcome": {"result": "OPEN", "r_multiple": 0}},
        ])
        self.assertEqual(result["trades_scored"], 2)
        self.assertEqual(result["trade_wins"], 1)
        self.assertEqual(result["trade_losses"], 1)
        self.assertEqual(result["net_r"], 1.0)


if __name__ == "__main__":
    unittest.main()
