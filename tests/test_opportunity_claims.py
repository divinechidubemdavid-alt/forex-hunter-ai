import unittest
from unittest.mock import patch

import state


class OpportunityClaimTests(unittest.TestCase):
    def setUp(self):
        self.original = state._state["opportunities"]
        state._state["opportunities"] = {
            "101": {
                "id": "101",
                "symbol": "GBP/USD",
                "direction": "BUY",
                "claimed_by": [],
            }
        }

    def tearDown(self):
        state._state["opportunities"] = self.original

    def test_claim_hides_opportunity_from_claiming_user(self):
        with patch.object(state, "_save"):
            self.assertTrue(state.mark_opportunity_claimed("101", 12345))

        item = state.get_opportunity("101")
        self.assertIn("12345", item["claimed_by"])

    def test_claim_does_not_hide_shared_opportunity_from_other_users(self):
        with patch.object(state, "_save"):
            state.mark_opportunity_claimed("101", 12345)

        item = state.get_opportunity("101")
        self.assertNotIn("67890", item["claimed_by"])

    def test_claim_is_idempotent(self):
        with patch.object(state, "_save"):
            state.mark_opportunity_claimed("101", 12345)
            state.mark_opportunity_claimed("101", 12345)

        self.assertEqual(
            state.get_opportunity("101")["claimed_by"],
            ["12345"],
        )

    def test_missing_opportunity_returns_false(self):
        with patch.object(state, "_save"):
            self.assertFalse(state.mark_opportunity_claimed("missing", 12345))


if __name__ == "__main__":
    unittest.main()
