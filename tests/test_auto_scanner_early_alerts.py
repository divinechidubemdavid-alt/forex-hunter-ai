import unittest
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import auto_scanner


class AutomaticEarlyAlertTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.app = SimpleNamespace(bot=SimpleNamespace(
            send_photo=AsyncMock(),
            send_message=AsyncMock(),
        ))
        self.base_result = {
            "status": "WAIT",
            "signal_stage": "EARLY_WATCH",
            "direction": "BUY",
            "score": 62,
            "reason": "Price is testing support; bullish momentum is improving.",
            "candle_time": "2026-11-23T08:00:00+00:00",
            "candles": None,
        }

    async def scan(self, result):
        with (
            patch.object(auto_scanner, "_eligible_markets", return_value=["XAU/USD"]),
            patch.dict(auto_scanner.MARKETS, {"XAU/USD": "XAU/USD"}),
            patch.object(auto_scanner, "_fetch_scanner_frame", new=AsyncMock(return_value=object())),
            patch.object(auto_scanner, "analyze", return_value=result),
        ):
            await auto_scanner._scan_market(self.app, "XAU/USD")

    async def test_watch_stage_is_routed_to_early_alert_not_confirmed_signal(self):
        with (
            patch.object(auto_scanner, "_send_early_alert", new=AsyncMock(return_value=1)) as early,
            patch.object(auto_scanner, "_send_signal", new=AsyncMock()) as confirmed,
        ):
            await self.scan(dict(self.base_result))
        early.assert_awaited_once()
        confirmed.assert_not_awaited()

    async def test_candidate_stage_is_routed_to_early_alert(self):
        candidate = dict(
            self.base_result,
            signal_stage="EARLY_ENTRY_CANDIDATE",
            score=74,
            entry=2000.0,
            stop_loss=1990.0,
            invalidation_rule="Invalidate below 1990.",
        )
        with (
            patch.object(auto_scanner, "_send_early_alert", new=AsyncMock(return_value=1)) as early,
            patch.object(auto_scanner, "_send_signal", new=AsyncMock()) as confirmed,
        ):
            await self.scan(candidate)
        early.assert_awaited_once()
        confirmed.assert_not_awaited()

    async def test_incomplete_candidate_is_not_sent(self):
        candidate = dict(
            self.base_result,
            signal_stage="EARLY_ENTRY_CANDIDATE",
            score=74,
            entry=None,
            stop_loss=None,
        )
        with (
            patch.object(auto_scanner, "_send_early_alert", new=AsyncMock()) as early,
            patch.object(auto_scanner, "_send_signal", new=AsyncMock()) as confirmed,
        ):
            await self.scan(candidate)
        early.assert_not_awaited()
        confirmed.assert_not_awaited()

    async def test_confirmed_signal_below_threshold_is_still_blocked(self):
        confirmed_result = dict(
            self.base_result,
            status="BUY",
            signal_stage="CONFIRMED",
            score=auto_scanner.AUTO_SCAN_MIN_SCORE - 1,
        )
        with (
            patch.object(auto_scanner, "_send_early_alert", new=AsyncMock()) as early,
            patch.object(auto_scanner, "_send_signal", new=AsyncMock()) as confirmed,
        ):
            await self.scan(confirmed_result)
        early.assert_not_awaited()
        confirmed.assert_not_awaited()

    async def test_confirmed_signal_at_threshold_keeps_existing_delivery_path(self):
        confirmed_result = dict(
            self.base_result,
            status="SELL",
            signal_stage="CONFIRMED",
            score=auto_scanner.AUTO_SCAN_MIN_SCORE,
        )
        with (
            patch.object(auto_scanner, "_send_early_alert", new=AsyncMock()) as early,
            patch.object(auto_scanner, "_send_signal", new=AsyncMock(return_value=1)) as confirmed,
        ):
            await self.scan(confirmed_result)
        early.assert_not_awaited()
        confirmed.assert_awaited_once()

    async def test_early_delivery_does_not_create_opportunity_or_performance_record(self):
        candidate = dict(
            self.base_result,
            signal_stage="EARLY_ENTRY_CANDIDATE",
            score=74,
            entry=2000.0,
            stop_loss=1990.0,
        )
        with (
            patch.object(auto_scanner, "TELEGRAM_CHANNEL_ID", None),
            patch.object(auto_scanner.state, "get_users", return_value=["123"]),
            patch.object(auto_scanner.state, "get_last_alert", return_value=None),
            patch.object(auto_scanner.state, "set_last_alert") as set_alert,
            patch.object(auto_scanner.state, "add_opportunity") as add_opportunity,
            patch.object(auto_scanner.state, "add_system_signal") as add_system,
            patch.object(auto_scanner, "create_market_chart", return_value=BytesIO(b"chart")),
        ):
            delivered = await auto_scanner._send_early_alert(
                self.app, "XAU/USD", candidate
            )

        self.assertEqual(delivered, 1)
        self.app.bot.send_photo.assert_awaited_once()
        caption = self.app.bot.send_photo.await_args.kwargs["caption"]
        self.assertIn("PROVISIONAL EARLY ENTRY", caption)
        self.assertIn("NOT CONFIRMED", caption)
        add_opportunity.assert_not_called()
        add_system.assert_not_called()
        set_alert.assert_called_once()

    async def test_alert_key_changes_when_stage_upgrades(self):
        watch = dict(self.base_result)
        candidate = dict(
            self.base_result,
            signal_stage="EARLY_ENTRY_CANDIDATE",
        )
        self.assertNotEqual(
            auto_scanner._early_signal_key("XAU/USD", watch),
            auto_scanner._early_signal_key("XAU/USD", candidate),
        )


if __name__ == "__main__":
    unittest.main()
