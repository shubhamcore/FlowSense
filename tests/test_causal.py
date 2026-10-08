import unittest

from production.causal import ExperimentSpec, analyze_continuous, minimum_sample_size_continuous, preregister


class CausalExperimentTests(unittest.TestCase):
    def test_refuses_winner_with_small_sample(self):
        spec = ExperimentSpec("Promo move", "Move display reduces congestion", "congestion", "decrease", minimum_sample_per_arm=30)
        result = analyze_continuous(spec, [42, 41, 43, 42], [34, 35, 33, 34])
        self.assertEqual(result["decision"], "INSUFFICIENT_SAMPLE")
        self.assertFalse(result["sample_sufficient"])

    def test_promote_requires_enough_and_direction(self):
        spec = ExperimentSpec("Promo move", "Move display reduces congestion", "congestion", "decrease", minimum_sample_per_arm=4)
        result = analyze_continuous(spec, [42, 41, 43, 42, 42, 41], [34, 35, 33, 34, 35, 34])
        self.assertEqual(result["decision"], "PROMOTE")
        self.assertEqual(result["data_class"], "MEASURED")

    def test_sample_size_and_preregistration(self):
        self.assertGreater(minimum_sample_size_continuous(standard_deviation=10, minimum_detectable_effect=3), 10)
        spec = ExperimentSpec("x", "h", "dwell", "increase")
        self.assertEqual(preregister(spec)["status"], "PREREGISTERED")


if __name__ == "__main__":
    unittest.main()
