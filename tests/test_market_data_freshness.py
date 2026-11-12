import unittest

import pandas as pd

from market_data import maximum_candle_age_minutes


class HigherTimeframeFreshnessTests(unittest.TestCase):
    def test_one_minute_data_keeps_short_freshness_window(self):
        self.assertEqual(
            maximum_candle_age_minutes(pd.Timedelta("1min")),
            21.0,
        )

    def test_four_hour_candle_is_not_marked_stale_after_twenty_minutes(self):
        self.assertEqual(
            maximum_candle_age_minutes(pd.Timedelta("4h")),
            260.0,
        )

    def test_daily_candle_remains_valid_until_next_completed_day(self):
        self.assertEqual(
            maximum_candle_age_minutes(pd.Timedelta("1day")),
            1460.0,
        )


if __name__ == "__main__":
    unittest.main()
