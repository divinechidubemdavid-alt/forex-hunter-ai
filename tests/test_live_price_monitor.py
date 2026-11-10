import unittest
from unittest.mock import AsyncMock, patch

import signal_monitor


def make_signal(direction="BUY"):
    if direction == "BUY":
        return {
            "id": "42", "symbol": "XAU/USD", "direction": "BUY",
            "status": "TRACKING", "entry": 2000, "stop_loss": 1990,
            "tp1": 2010, "tp2": 2020, "tp3": 2030,
            "current_price": 2005,
            "last_price_timestamp": "2026-11-10T10:00:00+00:00",
            "tp1_hit": False, "tp2_hit": False, "tp3_hit": False,
        }
    return {
        "id": "43", "symbol": "XAU/USD", "direction": "SELL",
        "status": "TRACKING", "entry": 2000, "stop_loss": 2010,
        "tp1": 1990, "tp2": 1980, "tp3": 1970,
        "current_price": 1995,
        "last_price_timestamp": "2026-11-10T10:00:00+00:00",
        "tp1_hit": False, "tp2_hit": False, "tp3_hit": False,
    }


class LivePriceMilestoneTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.signal = make_signal()
        self.updates = []
        self.events = []
        self.completions = []
        self.update_patch = patch.object(
            signal_monitor.state, "update_signal",
            side_effect=self.capture_update,
        )
        self.event_patch = patch.object(
            signal_monitor.state, "add_signal_event",
            side_effect=lambda *args, **kwargs: self.events.append(args),
        )
        self.complete_patch = patch.object(
            signal_monitor.state, "complete_signal",
            side_effect=lambda *args, **kwargs: self.completions.append(args),
        )
        self.settings_patch = patch.object(
            signal_monitor.state, "get_user_settings",
            return_value={"tracking_notification_mode": "silent"},
        )
        self.notify_patch = patch.object(
            signal_monitor, "_notify", new_callable=AsyncMock,
        )
        for item in (
            self.update_patch, self.event_patch, self.complete_patch,
            self.settings_patch, self.notify_patch,
        ):
            item.start()
        self.addCleanup(self.update_patch.stop)
        self.addCleanup(self.event_patch.stop)
        self.addCleanup(self.complete_patch.stop)
        self.addCleanup(self.settings_patch.stop)
        self.addCleanup(self.notify_patch.stop)

    def capture_update(self, signal_id, updates):
        self.updates.append(dict(updates))
        self.signal.update(updates)
        return dict(self.signal)

    async def test_buy_tick_records_all_targets_even_if_price_gaps_to_tp3(self):
        await signal_monitor._check_one_price(
            object(), "user", self.signal,
            {"price": 2031, "bid": 2030.8, "timestamp": 1794304860},
        )
        self.assertTrue(self.signal["tp1_hit"])
        self.assertTrue(self.signal["tp2_hit"])
        self.assertTrue(self.signal["tp3_hit"])
        self.assertAlmostEqual(self.signal["realised_r"], 2.0)
        self.assertEqual([event[1] for event in self.events], ["TP1", "TP2", "TP3"])
        self.assertEqual(self.completions[0][1], "TP3")

    async def test_buy_uses_bid_for_long_exit_levels(self):
        await signal_monitor._check_one_price(
            object(), "user", self.signal,
            {"price": 2012, "bid": 2009.5, "ask": 2012.5, "timestamp": 1794304861},
        )
        self.assertFalse(self.signal.get("tp1_hit", False))

    async def test_sell_uses_ask_for_short_exit_levels(self):
        signal = make_signal("SELL")
        await signal_monitor._check_one_price(
            object(), "user", signal,
            {"price": 1988, "bid": 1987.5, "ask": 1990.5, "timestamp": 1794304862},
        )
        self.assertFalse(signal.get("tp1_hit", False))

    async def test_sell_records_target_when_ask_reaches_level(self):
        signal = make_signal("SELL")
        await signal_monitor._check_one_price(
            object(), "user", signal,
            {"price": 1988, "bid": 1987.5, "ask": 1989.5, "timestamp": 1794304863},
        )
        self.assertTrue(signal["tp1_hit"])

    async def test_out_of_order_quote_is_ignored(self):
        await signal_monitor._check_one_price(
            object(), "user", self.signal,
            {"price": 2020, "bid": 2020, "timestamp": 1794304800},
        )
        self.assertEqual(self.updates, [])
        self.assertEqual(self.events, [])

    async def test_stop_loss_is_recorded(self):
        await signal_monitor._check_one_price(
            object(), "user", self.signal,
            {"price": 1989, "bid": 1989, "timestamp": 1794304864},
        )
        self.assertTrue(self.signal.get("stop_loss_hit"))
        self.assertEqual(self.completions[0][1], "STOP_LOSS")

    async def test_price_gap_crossing_target_and_stop_is_ambiguous(self):
        self.signal["current_price"] = 2035
        await signal_monitor._check_one_price(
            object(), "user", self.signal,
            {"price": 1989, "bid": 1989, "timestamp": 1794304865},
        )
        self.assertEqual(self.completions[0][1], "AMBIGUOUS")
        self.assertEqual(self.events[0][1], "AMBIGUOUS")


if __name__ == "__main__":
    unittest.main()
