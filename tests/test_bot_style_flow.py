import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import bot


class MenuNavigationInputFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_menu_button_cancels_pending_account_risk_and_opens_menu(self):
        context = SimpleNamespace(user_data={"account_risk_pending": True})
        message = SimpleNamespace(
            text="🔎 Market Scanner",
            reply_text=AsyncMock(),
        )
        update = SimpleNamespace(
            message=message,
            effective_user=SimpleNamespace(id=123),
        )

        with patch("bot.show_market_scanner", new_callable=AsyncMock) as show:
            await bot.handle_text(update, context)

        show.assert_awaited_once_with(update, context)
        self.assertNotIn("account_risk_pending", context.user_data)
        message.reply_text.assert_awaited_once()


class ManualStyleFlowKeyboardTests(unittest.TestCase):
    def test_style_is_the_first_manual_choice(self):
        markup = bot._style_keyboard()
        callbacks = [
            button.callback_data
            for row in markup.inline_keyboard
            for button in row
        ]
        self.assertEqual(
            callbacks,
            ["style:SCALPING", "style:DAY", "style:SWING"],
        )

    def test_each_style_shows_only_its_entry_timeframes(self):
        expected = {
            "SCALPING": ["tf:SCALPING:1m", "tf:SCALPING:5m"],
            "DAY": ["tf:DAY:15m", "tf:DAY:30m"],
            "SWING": ["tf:SWING:1h", "tf:SWING:4h", "tf:SWING:1day"],
        }
        for style, wanted in expected.items():
            with self.subTest(style=style):
                markup = bot._timeframe_keyboard(style)
                callbacks = [
                    button.callback_data
                    for row in markup.inline_keyboard
                    for button in row
                ]
                self.assertEqual(callbacks, wanted)

    def test_market_choice_keeps_style_and_timeframe_for_generate_step(self):
        markup = bot._market_keyboard(["EUR/USD", "BTC/USD"], "SWING", "1h")
        callbacks = [
            button.callback_data
            for row in markup.inline_keyboard
            for button in row
        ]
        self.assertEqual(
            callbacks,
            ["market:SWING:1h:EUR/USD", "market:SWING:1h:BTC/USD"],
        )


    def test_menu_navigation_clears_pending_numeric_and_wizard_inputs(self):
        class Context:
            user_data = {
                "account_risk_pending": True,
                "account_plan_pending": {"risk_percent": 1.0},
                "account_snapshot_id": "account-1",
                "account_wizard": {"step": "starting_balance"},
                "awaiting_custom_risk": True,
                "econ_awaiting": "actual:event-1",
                "report_date_range": {"type": "history"},
                "unrelated_preference": "keep",
            }

        context = Context()
        cleared = bot._reset_pending_input_for_menu_navigation(
            context, "🔎 Market Scanner"
        )

        self.assertEqual(set(cleared), set(bot.PENDING_INPUT_KEYS))
        self.assertEqual(context.user_data, {"unrelated_preference": "keep"})

    def test_regular_text_does_not_cancel_a_pending_input(self):
        class Context:
            user_data = {"account_risk_pending": True}

        context = Context()
        cleared = bot._reset_pending_input_for_menu_navigation(
            context, "5000 | 1 | 2.5 | 10"
        )

        self.assertEqual(cleared, [])
        self.assertTrue(context.user_data["account_risk_pending"])


if __name__ == "__main__":
    unittest.main()
