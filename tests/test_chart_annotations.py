import unittest

import numpy as np
import pandas as pd

from chart import create_market_chart


class MarketStructureChartTests(unittest.TestCase):
    def test_chart_renders_with_structure_annotations_enabled(self):
        index = pd.date_range(
            "2026-01-01",
            periods=100,
            freq="15min",
            tz="UTC",
        )
        base = 100 + np.linspace(0, 8, 100) + np.sin(np.arange(100) / 3) * 2
        frame = pd.DataFrame(
            {
                "open": base - 0.35,
                "high": base + 1.0 + (np.arange(100) % 4) * 0.08,
                "low": base - 1.0 - (np.arange(100) % 3) * 0.08,
                "close": base + 0.35,
            },
            index=index,
        )

        image = create_market_chart(
            frame,
            "EUR/USD",
            "15m",
            entry=108.0,
            stop_loss=105.0,
            take_profits=[111.0, 114.0, 117.0],
        )

        self.assertEqual(image.read(8), b"\x89PNG\r\n\x1a\n")
        self.assertGreater(image.getbuffer().nbytes, 10_000)
        image.close()


if __name__ == "__main__":
    unittest.main()
