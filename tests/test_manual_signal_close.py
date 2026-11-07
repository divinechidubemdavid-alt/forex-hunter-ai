import unittest
from unittest.mock import patch

import state


class ManualTargetCloseTests(unittest.TestCase):
    def setUp(self):
        self.original_state = state._state
        state._state = {
            "signals": {
                "7": {
                    "id": "7", "user_id": "123", "symbol": "GBP/USD",
                    "direction": "BUY", "entry": 1.2500, "stop_loss": 1.2490,
                    "current_price": 1.2510, "tp1_hit": True,
                    "tp2_hit": False, "tp3_hit": False,
                    "realised_r": 1 / 3, "status": "TRACKING", "events": [],
                }
            },
            "signal_events": [],
        }

    def tearDown(self):
        state._state = self.original_state

    def test_manual_close_after_tp1_is_saved_as_win(self):
        with patch.object(state, "_save"):
            result = state.close_signal_after_target("7", "123")
        self.assertEqual(result["status"], "MANUAL_WIN")
        self.assertEqual(result["manual_close_after_tp"], 1)
        self.assertAlmostEqual(result["realised_r"], 1.0)
        self.assertEqual(result["manual_exit_price"], 1.2510)
        self.assertEqual(state._state["signal_events"][-1]["type"], "MANUAL_WIN")

    def test_manual_close_after_tp2_is_saved_as_win(self):
        state._state["signals"]["7"].update({
            "tp2_hit": True, "current_price": 1.2520, "realised_r": 1.0,
        })
        with patch.object(state, "_save"):
            result = state.close_signal_after_target("7", "123")
        self.assertEqual(result["status"], "MANUAL_WIN")
        self.assertEqual(result["manual_close_after_tp"], 2)
        self.assertAlmostEqual(result["realised_r"], 5 / 3)

    def test_cannot_record_manual_win_before_tp1(self):
        signal = state._state["signals"]["7"]
        signal["tp1_hit"] = False
        with patch.object(state, "_save"):
            result = state.close_signal_after_target("7", "123")
        self.assertIsNone(result)
        self.assertEqual(signal["status"], "TRACKING")

    def test_user_cannot_close_another_users_signal(self):
        with patch.object(state, "_save"):
            result = state.close_signal_after_target("7", "999")
        self.assertIsNone(result)
        self.assertEqual(state._state["signals"]["7"]["status"], "TRACKING")


if __name__ == "__main__":
    unittest.main()
