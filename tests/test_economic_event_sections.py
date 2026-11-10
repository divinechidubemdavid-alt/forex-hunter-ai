import unittest
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


if __name__ == "__main__":
    unittest.main()
