import unittest

from account_manager import (
    calculate_account_metrics,
    calculate_position_size,
    calculate_account_trade_plan,
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

    def test_trailing_drawdown_is_not_falsely_calculated_from_starting_balance(self):
        profile = create_profile("123", {
            "name": "Trailing Account",
            "account_type": "PROP_CHALLENGE",
            "starting_balance": 5000,
            "currency": "USD",
            "rules": {
                "overall_loss_percent": 8,
                "drawdown_model": "TRAILING",
            },
        })
        metrics = calculate_account_metrics(profile, 5100, 4950)
        self.assertIsNone(metrics["overall_loss_used"])
        self.assertIsNone(metrics["overall_loss_remaining"])
        self.assertIsNone(metrics["overall_loss_floor"])
        self.assertFalse(metrics["overall_loss_tracking_available"])
        self.assertFalse(metrics["daily_loss_tracking_available"])

    def test_position_size_uses_risk_and_stop_distance(self):
        result = calculate_position_size(5000, 0.25, 2.5, 10)
        self.assertAlmostEqual(result["risk_amount"], 12.5)
        self.assertAlmostEqual(result["lots"], 0.5)

    def test_account_trade_plan_uses_saved_equity_and_strategy_levels(self):
        profile = dict(self.profile)
        profile["snapshot"] = {"equity": 4925}
        trade = {
            "status": "BUY",
            "symbol": "EUR/USD",
            "timeframe": "15m",
            "entry": 1.1000,
            "stop_loss": 1.0950,
            "take_profits": [1.1050, 1.1100, 1.1150],
        }
        result = calculate_account_trade_plan(profile, trade, 0.5, 10000)
        self.assertEqual(result["equity"], 4925)
        self.assertEqual(result["equity_source"], "latest manually saved equity snapshot")
        self.assertAlmostEqual(result["risk_amount"], 24.625)
        self.assertAlmostEqual(result["lots"], 0.4925)
        self.assertEqual(result["take_profits"], trade["take_profits"])

    def test_account_trade_plan_falls_back_to_starting_balance_without_snapshot(self):
        trade = {
            "status": "SELL",
            "symbol": "XAU/USD",
            "timeframe": "5m",
            "entry": 2000,
            "stop_loss": 2010,
            "take_profits": [1990, 1980, 1970],
        }
        result = calculate_account_trade_plan(self.profile, trade, 1, 1)
        self.assertEqual(result["equity"], 5000)
        self.assertIn("starting balance", result["equity_source"])
        self.assertAlmostEqual(result["lots"], 5)

    def test_account_trade_plan_rejects_wait_and_wrong_side_stop(self):
        trade = {
            "status": "WAIT",
            "entry": 1.1,
            "stop_loss": 1.09,
            "take_profits": [1.11, 1.12, 1.13],
        }
        with self.assertRaises(ValueError):
            calculate_account_trade_plan(self.profile, trade, 1, 10)
        trade["status"] = "BUY"
        trade["stop_loss"] = 1.11
        with self.assertRaises(ValueError):
            calculate_account_trade_plan(self.profile, trade, 1, 10)

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
