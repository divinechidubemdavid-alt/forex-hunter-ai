import unittest
from unittest.mock import AsyncMock, patch
from datetime import datetime, timezone

import economic_events_bot as bot


class EconomicEventSectionTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)

    def test_future_events_only_appear_in_upcoming(self):
        events = [
            {"id": "past", "scheduled_at": "2026-11-10T11:59:00+00:00"},
            {"id": "future", "scheduled_at": "2026-11-10T12:01:00+00:00"},
        ]
        upcoming = bot._events_for_display(events, False, self.now)
        history = bot._events_for_display(events, True, self.now)
        self.assertEqual([item["id"] for item in upcoming], ["future"])
        self.assertEqual([item["id"] for item in history], ["past"])

    def test_event_moves_to_history_at_exact_release_time(self):
        events = [{"id": "released", "scheduled_at": "2026-11-10T12:00:00+00:00"}]
        self.assertEqual(bot._events_for_display(events, False, self.now), [])
        self.assertEqual([item["id"] for item in bot._events_for_display(events, True, self.now)], ["released"])


class EconomicEventCalendarSourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_free_official_sources_run_before_optional_paid_enrichment(self):
        calls = []
        async def bls():
            calls.append("BLS")
            return ["bls-event"]
        async def fomc():
            calls.append("Federal Reserve")
            return ["fed-event"]
        async def bea():
            calls.append("BEA")
            return ["bea-event"]
        async def licensed():
            calls.append("licensed")
            return ["licensed-event"]

        with patch.object(bot.ee, "fetch_ics_events", new=bls), patch.object(
            bot.ee, "fetch_fomc_events", new=fomc
        ), patch.object(bot.ee, "fetch_bea_events", new=bea), patch.object(
            bot.te, "configured", return_value=True
        ), patch.object(bot.te, "fetch_calendar_events", new=licensed):
            total, source = await bot._sync_calendars()

        self.assertEqual(calls, ["BLS", "Federal Reserve", "BEA", "licensed"])
        self.assertEqual(total, 4)
        self.assertIn("free official", source)
        self.assertIn("optional licensed", source)

    async def test_official_calendar_sources_work_without_paid_provider(self):
        with patch.object(bot.ee, "fetch_ics_events", new=AsyncMock(return_value=[])), patch.object(
            bot.ee, "fetch_fomc_events", new=AsyncMock(return_value=["fomc"])
        ), patch.object(bot.ee, "fetch_bea_events", new=AsyncMock(return_value=[])), patch.object(
            bot.te, "configured", return_value=False
        ), patch.object(bot.te, "fetch_calendar_events", new=AsyncMock()) as licensed:
            total, source = await bot._sync_calendars()
        self.assertEqual(total, 1)
        self.assertIn("free official", source)
        licensed.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
