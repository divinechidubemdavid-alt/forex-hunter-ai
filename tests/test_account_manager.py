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

    def test_personal_profile_does_not_retain_prop_firm_name(self):
        profile = create_profile("123", {
            "name": "Personal",
            "account_type": "PERSONAL",
            "firm_name": "Should not be stored",
            "starting_balance": 1000,
            "currency": "USD",
            "rules": {"daily_loss_percent": 2},
        })
        self.assertEqual(profile["firm_name"], "")

    def test_position_count_splits_total_risk_and_lots(self):
        trade = {
            "status": "BUY",
            "symbol": "EUR/USD",
            "timeframe": "15m",
            "entry": 1.1000,
            "stop_loss": 1.0950,
            "take_profits": [1.1050, 1.1100, 1.1150],
        }
        plan = calculate_account_trade_plan(self.profile, trade, 1, 10000, positions_count=4)
        self.assertEqual(plan["positions_count"], 4)
        self.assertAlmostEqual(plan["risk_amount"], 50)
        self.assertAlmostEqual(plan["per_position_risk_amount"], 12.5)
        self.assertAlmostEqual(plan["lots"], 1)
        self.assertAlmostEqual(plan["per_position_lots"], 0.25)
        self.assertEqual(plan["stop_loss"], trade["stop_loss"])
        self.assertEqual(plan["take_profits"], trade["take_profits"])

    def test_position_count_must_be_whole_number_between_one_and_twenty(self):
        trade = {
            "status": "BUY",
            "symbol": "EUR/USD",
            "timeframe": "15m",
            "entry": 1.1000,
            "stop_loss": 1.0950,
            "take_profits": [1.1050, 1.1100, 1.1150],
        }
        for count in (0, 21, 1.5, "many", True):
            with self.subTest(count=count):
                with self.assertRaises(ValueError):
                    calculate_account_trade_plan(self.profile, trade, 1, 10000, positions_count=count)

    def test_invalid_drawdown_model_is_rejected(self):
        with self.assertRaises(ValueError):
            create_profile("123", {
                "name": "Bad Account",
                "account_type": "PROP_CHALLENGE",
                "starting_balance": 1000,
                "currency": "USD",
                "rules": {"drawdown_model": "UNKNOWN"},
            })

    def test_account_type_accepts_instant_funding_label(self):
        from account_manager import normalise_account_type
        self.assertEqual(normalise_account_type("Instant Funding"), "FUNDED_PROP")

    def test_account_type_accepts_personal_account_label(self):
        from account_manager import normalise_account_type
        self.assertEqual(normalise_account_type("Personal Account"), "PERSONAL")

    def test_registration_sequence_skips_firm_name_for_personal_account(self):
        from account_manager import account_registration_steps
        steps = account_registration_steps("PERSONAL")
        self.assertEqual(steps[:3], ["name", "account_type", "starting_balance"])
        self.assertIn("currency", steps)


    def test_personal_account_registration_only_asks_for_daily_loss(self):
        from account_manager import account_registration_steps
        self.assertEqual(
            account_registration_steps("PERSONAL"),
            ["name", "account_type", "starting_balance", "currency", "daily_loss_percent"],
        )

    def test_prop_challenge_registration_asks_for_all_firm_limits(self):
        from account_manager import account_registration_steps
        self.assertEqual(
            account_registration_steps("PROP_CHALLENGE"),
            ["name", "account_type", "firm_name", "starting_balance", "currency",
             "daily_loss_percent", "overall_loss_percent", "profit_target_percent", "drawdown_model"],
        )

    def test_instant_funding_registration_asks_for_all_firm_limits(self):
        from account_manager import account_registration_steps
        self.assertEqual(
            account_registration_steps("FUNDED_PROP"),
            ["name", "account_type", "firm_name", "starting_balance", "currency",
             "daily_loss_percent", "overall_loss_percent", "profit_target_percent", "drawdown_model"],
        )

    def test_personal_profile_does_not_keep_prop_firm_limits(self):
        profile = create_profile("123", {
            "name": "Personal Trading",
            "account_type": "PERSONAL",
            "starting_balance": 1000,
            "currency": "USD",
            "rules": {
                "daily_loss_percent": 3,
                "overall_loss_percent": 10,
                "profit_target_percent": 8,
                "drawdown_model": "TRAILING",
            },
        })
        self.assertEqual(profile["rules"]["daily_loss_percent"], 3)
        self.assertIsNone(profile["rules"]["overall_loss_percent"])
        self.assertIsNone(profile["rules"]["profit_target_percent"])
        self.assertIsNone(profile["rules"]["drawdown_model"])

    def test_registration_sequence_keeps_firm_name_for_prop_accounts(self):
        from account_manager import account_registration_steps
        steps = account_registration_steps("PROP_CHALLENGE")
        self.assertEqual(steps[:3], ["name", "account_type", "firm_name"])


if __name__ == "__main__":
    unittest.main()
