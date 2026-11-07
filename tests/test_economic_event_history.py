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
