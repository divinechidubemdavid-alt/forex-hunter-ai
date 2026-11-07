import unittest
from datetime import date

from bot import _report_period
from report_charts import create_history_report, create_performance_report


class ImageFirstReportTests(unittest.TestCase):
    def test_custom_range_uses_completion_date_for_closed_signals(self):
        signals = [
            {
                "id": "1",
                "created_at": "2026-09-20T10:00:00+01:00",
                "completed_at": "2026-10-05T12:00:00+01:00",
                "status": "MANUAL_WIN",
                "manual_close_after_tp": 1,
                "symbol": "GBP/USD",
                "direction": "BUY",
                "timeframe": "15m",
                "realised_r": 0.8,
            },
            {
                "id": "2",
                "created_at": "2026-10-31T23:59:59+01:00",
                "completed_at": None,
                "status": "TRACKING",
                "symbol": "XAU/USD",
                "direction": "SELL",
                "timeframe": "1h",
            },
            {
                "id": "3",
                "created_at": "2026-11-01T00:00:00+01:00",
                "completed_at": None,
                "status": "TRACKING",
            },
        ]

        filtered, label = _report_period(
            signals,
            "custom",
            custom_start=date(2026, 10, 1),
            custom_end=date(2026, 10, 31),
        )

        self.assertEqual([item["id"] for item in filtered], ["1", "2"])
        self.assertEqual(label, "01 Oct 2026 – 31 Oct 2026")

    def test_history_report_is_a_png_image(self):
        report = create_history_report(
            [{
                "id": "8",
                "symbol": "GBP/USD",
                "direction": "BUY",
                "timeframe": "15m",
                "status": "MANUAL_WIN",
                "manual_close_after_tp": 1,
                "tp1_hit": True,
                "realised_r": 0.75,
                "created_at": "2026-10-01T10:00:00+01:00",
                "completed_at": "2026-10-01T11:00:00+01:00",
            }],
            period_label="October 2026",
        )
        self.assertEqual(report.read(8), b"\\x89PNG\\r\\n\\x1a\\n")
        report.close()

    def test_performance_report_is_a_png_image(self):
        report = create_performance_report(
            [{
                "status": "MANUAL_WIN",
                "manual_close_after_tp": 2,
                "tp1_hit": True,
                "tp2_hit": True,
                "realised_r": 1.4,
            }],
            period_label="Last 30 days",
        )
        self.assertEqual(report.read(8), b"\\x89PNG\\r\\n\\x1a\\n")
        report.close()


if __name__ == "__main__":
    unittest.main()
