import unittest

import bot


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


if __name__ == "__main__":
    unittest.main()
