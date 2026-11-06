import unittest

from signal_monitor import _realised_r
from report_charts import (
    create_performance_report,
    create_tracked_report,
    create_history_report,
)


class RealisedRTests(unittest.TestCase):
    def test_open_tp1_is_positive_one_third(self):
        self.assertAlmostEqual(_realised_r([True, False, False]), 1 / 3)

    def test_tp1_then_stop_loss_is_negative_one_third(self):
        self.assertAlmostEqual(
            _realised_r([True, False, False], stop_hit=True), -1 / 3
        )

    def test_open_tp1_tp2_is_positive_one_r(self):
        self.assertAlmostEqual(_realised_r([True, True, False]), 1.0)

    def test_tp1_tp2_then_stop_loss_is_two_thirds(self):
        self.assertAlmostEqual(
            _realised_r([True, True, False], stop_hit=True), 2 / 3
        )

    def test_stop_loss_without_targets_is_negative_one_r(self):
        self.assertAlmostEqual(
            _realised_r([False, False, False], stop_hit=True), -1.0
        )

    def test_all_three_targets_is_positive_two_r(self):
        self.assertAlmostEqual(_realised_r([True, True, True]), 2.0)


class VisualReportTests(unittest.TestCase):
    def test_all_three_reports_generate_png_images(self):
        sample = [{
            "id": "1", "symbol": "XAU/USD", "direction": "BUY",
            "status": "TRACKING", "entry": 2000, "stop_loss": 1990,
            "tp1": 2010, "tp2": 2020, "tp3": 2030,
            "tp1_hit": True, "realised_r": 1 / 3, "score": 82,
        }]
        for report in (
            create_performance_report(sample),
            create_tracked_report(sample),
            create_history_report(sample),
        ):
            self.assertEqual(report.read(8), b"\x89PNG\r\n\x1a\n")


if __name__ == "__main__":
    unittest.main()
