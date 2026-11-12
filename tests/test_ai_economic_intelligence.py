import unittest

import ai_economic_intelligence as ai


class AIEconomicIntelligenceTests(unittest.TestCase):
    def test_mixed_evidence_never_becomes_directional_fundamental_bias(self):
        result = ai._validate_result({
            "currency_evidence": {
                "classification": "MIXED",
                "explanation": "Inflation is sticky but growth is slowing.",
            },
            "fundamental_direction": "BULLISH",
            "confidence": 82,
        }, "USD")
        self.assertEqual(result["currency_evidence"]["classification"], "MIXED")
        self.assertEqual(result["fundamental_direction"], "NEUTRAL")

    def test_bullish_currency_evidence_is_combined_with_conflicting_technical_view(self):
        result = ai._validate_result({
            "currency_evidence": {"classification": "BULLISH", "explanation": "Evidence supports the currency."},
            "fundamental_direction": "BULLISH",
            "confidence": 70,
            "risk_factors": ["Central-bank guidance may change."],
        }, "USD")
        combined = ai._combine(result, "BEARISH")
        self.assertEqual(combined["fundamental_direction"], "BULLISH")
        self.assertEqual(combined["combined_direction"], "BULLISH")
        self.assertLess(combined["confidence"], 70)
        self.assertEqual(combined["market_biases"]["EUR/USD"], "SELL")
        self.assertEqual(combined["market_biases"]["USD/JPY"], "BUY")
        self.assertEqual(
            set(combined["market_biases"]),
            {"EUR/USD", "GBP/USD", "USD/JPY", "XAU/USD"},
        )

    def test_inconclusive_evidence_does_not_create_a_fundamental_trade_direction(self):
        result = ai._validate_result({
            "currency_evidence": {"classification": "INCONCLUSIVE"},
            "fundamental_direction": "BEARISH",
            "confidence": 90,
        }, "EUR")
        combined = ai._combine(result, None)
        self.assertEqual(combined["fundamental_direction"], "NEUTRAL")
        self.assertEqual(combined["combined_direction"], "NEUTRAL")
        self.assertEqual(combined["confidence"], 30)


if __name__ == "__main__":
    unittest.main()
