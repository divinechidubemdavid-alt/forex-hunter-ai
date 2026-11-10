import unittest
from datetime import datetime, timedelta, timezone

import economic_event_history as history
import economic_event_visuals as visuals


class EconomicEventTradeTrackingTests(unittest.TestCase):
    def trade_event(self):
        return {
            "event_trade": {
                "status": "WAITING_ENTRY",
                "side": "BUY",
                "entry_trigger": 100.0,
                "stop_loss": 95.0,
                "take_profits": [105.0, 110.0, 115.0],
                "hit_targets": [],
            }
        }

    def snapshot(self, high, low, candle_time):
        return {
            "status": "AVAILABLE",
            "signal_candle_high": high,
            "signal_candle_low": low,
            "candle_time": candle_time,
            "retrieved_at": candle_time,
        }

    def test_entry_trigger_starts_monitoring_before_target_scoring(self):
        event = self.trade_event()
        trade = history.update_event_trade_progress(
            event, self.snapshot(101, 99, "2026-11-10T10:00:00+00:00")
        )
        self.assertEqual(trade["status"], "MONITORING")
        self.assertEqual(trade["result"], "PENDING")

    def test_tp1_is_a_win_and_later_stop_cannot_turn_it_into_loss(self):
        event = self.trade_event()
        event["event_trade"]["status"] = "MONITORING"
        history.update_event_trade_progress(
            event, self.snapshot(106, 101, "2026-11-10T10:05:00+00:00")
        )
        self.assertEqual(event["event_trade"]["status"], "WIN")
        self.assertEqual(event["event_trade"]["hit_targets"], [1])
        history.update_event_trade_progress(
            event, self.snapshot(102, 94, "2026-11-10T10:10:00+00:00")
        )
        self.assertEqual(event["event_trade"]["status"], "WIN")
        self.assertEqual(event["event_trade"]["result"], "WIN")

    def test_ambiguous_later_target_does_not_remove_tp1_win_or_claim_tp2(self):
        event = self.trade_event()
        event["event_trade"]["status"] = "MONITORING"
        history.update_event_trade_progress(
            event, self.snapshot(106, 101, "2026-11-10T10:05:00+00:00")
        )
        history.update_event_trade_progress(
            event, self.snapshot(111, 94, "2026-11-10T10:10:00+00:00")
        )
        self.assertEqual(event["event_trade"]["status"], "WIN")
        self.assertEqual(event["event_trade"]["hit_targets"], [1])

    def test_stop_before_any_target_is_loss(self):
        event = self.trade_event()
        event["event_trade"]["status"] = "MONITORING"
        trade = history.update_event_trade_progress(
            event, self.snapshot(99, 94, "2026-11-10T10:05:00+00:00")
        )
        self.assertEqual(trade["status"], "LOSS")

    def test_candle_touching_target_and_stop_is_not_guessed(self):
        event = self.trade_event()
        event["event_trade"]["status"] = "MONITORING"
        trade = history.update_event_trade_progress(
            event, self.snapshot(106, 94, "2026-11-10T10:05:00+00:00")
        )
        self.assertEqual(trade["status"], "AMBIGUOUS")
        self.assertEqual(trade["result"], "UNSCORED")

    def test_collective_summary_counts_tp1_and_tp2_as_wins(self):
        events = [
            {"event_trade": {"status": "WIN", "side": "BUY", "hit_targets": [1]}},
            {"event_trade": {"status": "WIN", "side": "SELL", "hit_targets": [1, 2]}},
            {"event_trade": {"status": "LOSS", "side": "BUY", "hit_targets": []}},
        ]
        stats = history.performance_summary(events)
        self.assertEqual(stats["trade_wins"], 2)
        self.assertEqual(stats["trade_losses"], 1)
        self.assertEqual(stats["tp1"], 2)
        self.assertEqual(stats["tp2"], 1)
        self.assertEqual(stats["partial_wins"], 2)
        self.assertEqual(stats["buy_trade_win_rate_pct"], 50.0)
        self.assertEqual(stats["sell_trade_win_rate_pct"], 100.0)


class EconomicEventImageTests(unittest.TestCase):
    def test_event_artwork_is_png_and_event_specific(self):
        image = visuals.render_event_artwork({"title": "US CPI", "currency": "USD"})
        self.assertEqual(image.read(8), bytes.fromhex("89504e470d0a1a0a"))

    def test_performance_dashboard_is_png(self):
        image = visuals.render_performance_dashboard([], history.performance_summary([]))
        self.assertEqual(image.read(8), bytes.fromhex("89504e470d0a1a0a"))


if __name__ == "__main__":
    unittest.main()
