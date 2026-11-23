import unittest
from unittest.mock import AsyncMock, patch

import gold_monitor


class SpecialMonitorSeparationTests(unittest.IsolatedAsyncioTestCase):
    async def test_special_monitor_publishes_conditions_not_trade_alerts(self):
        result = {
            "symbol": "XAU/USD",
            "timeframe": "5m",
            "condition": "UPTREND",
            "direction": "UP",
            "detail": "EMA and directional movement are aligned.",
            "price": 2000.0,
            "adx": 25.0,
            "rsi": 58.0,
            "atr": 5.0,
            "candle_time": "2026-11-23T08:00:00+00:00",
            "breakout_watch": None,
            "candles": object(),
        }

        with (
            patch.object(gold_monitor.state, "get_scanner_state", return_value={}),
            patch.object(gold_monitor.state, "set_scanner_state"),
            patch.object(gold_monitor, "classify_gold_market", return_value=result),
            patch.object(gold_monitor, "_send_gold_update", new=AsyncMock()) as update,
        ):
            await gold_monitor._analyse_and_notify(
                object(), "5m", object(), symbol="XAU/USD"
            )

        update.assert_awaited_once()
        self.assertFalse(hasattr(gold_monitor, "_send_early_alert"))
        self.assertFalse(hasattr(gold_monitor, "analyze"))

    def test_special_update_is_explicitly_not_a_trade_instruction(self):
        message = gold_monitor._notification_text({
            "symbol": "XAU/USD",
            "timeframe": "5m",
            "condition": "UPTREND",
            "direction": "UP",
            "price": 2000.0,
            "adx": 25.0,
            "rsi": 58.0,
            "detail": "Trend conditions are aligned.",
        })
        self.assertIn("SPECIAL MONITORING UPDATE", message)
        self.assertIn("not a BUY/SELL signal", message)


if __name__ == "__main__":
    unittest.main()
