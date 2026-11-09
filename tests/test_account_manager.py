import unittest

from account_manager import (
    calculate_account_metrics,
    calculate_position_size,
    create_profile,
)


class AccountManagerTests(unittest.TestCase):
    def setUp(self):
        self.profile = create_profile("123", {
            "name": "Universal Test Account",
            "account_type": "PROP_CHALLENGE",
            "firm_name": "Example Firm",
            "starting_balance": 5000,
            "currency": "USD",
            "rules": {
                "daily_loss_percent": 4,
                "overall_loss_percent": 8,
                "profit_target_percent": 8,
                "drawdown_model": "STATIC",
            },
        })

    def test_profile_keeps_rules_unverified_by_default(self):
        self.assertFalse(self.profile["rules"]["verified"])

    def test_account_metrics_use_equity_for_drawdown(self):
        metrics = calculate_account_metrics(self.profile, 5000, 4850)
        self.assertEqual(metrics["floating_pnl"], -150)
        self.assertEqual(metrics["overall_loss_used"], 150)
        self.assertEqual(metrics["overall_loss_remaining"], 250)
        self.assertEqual(metrics["overall_loss_floor"], 4600)

    def test_position_size_uses_risk_and_stop_distance(self):
        result = calculate_position_size(5000, 0.25, 2.5, 10)
        self.assertAlmostEqual(result["risk_amount"], 12.5)
        self.assertAlmostEqual(result["lots"], 0.5)

    def test_position_size_rejects_excessive_risk(self):
        with self.assertRaises(ValueError):
            calculate_position_size(5000, 6, 2.5, 10)

    def test_invalid_drawdown_model_is_rejected(self):
        with self.assertRaises(ValueError):
            create_profile("123", {
                "name": "Bad Account",
                "account_type": "PERSONAL",
                "starting_balance": 1000,
                "currency": "USD",
                "rules": {"drawdown_model": "UNKNOWN"},
            })


if __name__ == "__main__":
    unittest.main()
