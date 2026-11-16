import unittest
from unittest.mock import patch

import state


class PlatformSignalTrackingTests(unittest.TestCase):
    def setUp(self):
        self.saved_state = state._state
        self.test_state = state._empty_state()
        self.state_patch = patch.object(state, "_state", self.test_state)
        self.state_patch.start()
        self.save_patch = patch.object(state, "_save")
        self.save_patch.start()

    def tearDown(self):
        self.save_patch.stop()
        self.state_patch.stop()

    def _signal(self):
        return {
            "symbol": "XAU/USD",
            "direction": "BUY",
            "timeframe": "15m",
            "entry": 2000.0,
            "stop_loss": 1990.0,
            "tp1": 2010.0,
            "tp2": 2020.0,
            "tp3": 2030.0,
            "candle_time": "2026-11-16T10:00:00+00:00",
        }

    def test_system_signal_is_created_once_per_scanner_key(self):
        first = state.add_system_signal("unique-gold-signal", self._signal())
        second = state.add_system_signal("unique-gold-signal", self._signal())

        self.assertEqual(first["id"], second["id"])
        self.assertTrue(first["system_managed"])
        self.assertEqual(len(state.get_system_tracked_signals()), 1)

    def test_system_signal_does_not_enter_a_users_personal_tracking(self):
        state.add_system_signal("unique-gold-signal", self._signal())

        self.assertEqual(state.get_tracked_signals("12345"), [])
        self.assertEqual(len(state.get_system_tracked_signals()), 1)

    def test_stop_after_tp1_is_a_partial_win_not_a_total_loss(self):
        signal = state.add_system_signal("unique-gold-signal", self._signal())
        state.update_signal(signal["id"], {"tp1_hit": True, "realised_r": -0.3333})

        completed = state.complete_signal(
            signal["id"], "STOP_LOSS", "Remaining position reached stop."
        )

        self.assertTrue(completed)
        result = state.get_signal(signal["id"], "__system__")
        self.assertEqual(result["status"], "PARTIAL_WIN")
        self.assertAlmostEqual(result["realised_r"], -0.3333, places=4)

    def test_stop_before_any_target_remains_a_loss(self):
        signal = state.add_system_signal("unique-gold-signal", self._signal())

        completed = state.complete_signal(
            signal["id"], "STOP_LOSS", "Stop reached before TP1."
        )

        self.assertTrue(completed)
        result = state.get_signal(signal["id"], "__system__")
        self.assertEqual(result["status"], "STOP_LOSS")


if __name__ == "__main__":
    unittest.main()
