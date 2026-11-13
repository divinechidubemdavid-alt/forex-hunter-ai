import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import pandas as pd

import economic_research as research
from market_data import MarketDataError


class EconomicResearchMarketScopeTests(unittest.TestCase):
    def test_only_retained_market_proxies_are_available(self):
        self.assertEqual(research._market_for_currency("USD"), "XAU/USD")
        self.assertIsNone(research._market_for_currency("EUR"))
        self.assertEqual(research._market_for_currency("GBP"), "GBP/USD")
        self.assertEqual(research._market_for_currency("JPY"), "USD/JPY")
        self.assertEqual(research._market_for_currency("XAU"), "XAU/USD")

    def test_official_bls_actual_is_preferred_before_optional_paid_provider(self):
        import trading_economics
        official = {"value": "250", "source": "U.S. Bureau of Labor Statistics"}
        paid = AsyncMock(return_value={"value": "251", "source": "Trading Economics"})
        with patch.object(research, "fetch_bls_release_actual", new=AsyncMock(return_value=official)), patch.object(
            trading_economics, "configured", return_value=True
        ), patch.object(trading_economics, "fetch_event_actual", new=paid):
            result = asyncio.run(research.auto_actual_release({"title": "Nonfarm Payrolls"}))
        self.assertEqual(result, official)
        paid.assert_not_awaited()

    def test_optional_paid_actual_is_fallback_when_no_official_value_exists(self):
        import trading_economics
        paid_result = {"value": "251", "source": "Trading Economics"}
        paid = AsyncMock(return_value=paid_result)
        with patch.object(research, "fetch_bls_release_actual", new=AsyncMock(return_value=None)), patch.object(
            trading_economics, "configured", return_value=True
        ), patch.object(trading_economics, "fetch_event_actual", new=paid):
            result = asyncio.run(research.auto_actual_release({
                "title": "Nonfarm Payrolls", "provider_calendar_id": "123"
            }))
        self.assertEqual(result, paid_result)
        paid.assert_awaited_once()

    def test_market_snapshot_waits_for_shared_api_cooldown_then_retries(self):
        frame = pd.DataFrame({
            "close": [100.0] * 45, "high": [101.0] * 45, "low": [99.0] * 45,
            "ema9": [101.0] * 45, "ema21": [100.0] * 45,
            "plus_di": [25.0] * 45, "minus_di": [10.0] * 45,
            "adx": [25.0] * 45, "rsi": [55.0] * 45, "atr": [1.0] * 45,
        }, index=pd.date_range("2026-11-01", periods=45, freq="min", tz="UTC"))
        fetch = AsyncMock(side_effect=[
            MarketDataError("API cooldown active. Try again in 0.0 seconds."),
            frame,
        ])
        with patch.object(research.market_data, "fetch_candles", fetch), patch.object(
            research, "calculate_indicators", return_value=frame
        ), patch.object(research.asyncio, "sleep", new=AsyncMock()) as sleep:
            result = asyncio.run(research.technical_snapshot(
                {"currency": "USD"}, "15m", market="GBP/USD"
            ))
        self.assertEqual(result["status"], "AVAILABLE")
        self.assertEqual(result["market"], "GBP/USD")
        self.assertEqual(fetch.await_count, 2)
        sleep.assert_awaited_once()

    def test_retired_currency_pairs_have_no_event_research_proxy(self):
        for currency in ("AUD", "CAD", "CHF", "EUR"):
            with self.subTest(currency=currency):
                self.assertIsNone(research._market_for_currency(currency))


if __name__ == "__main__":
    unittest.main()
