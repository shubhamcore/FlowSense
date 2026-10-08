import unittest

from production.counterfactual import Intervention, active_learning_priority, journey_entropy, rank_interventions


class CounterfactualTests(unittest.TestCase):
    def test_action_frontier_is_uncertainty_aware(self):
        items = [
            Intervention("a", "Move promo", "reduce path conflict", -23.8, .62, .95, 2, "Protect 1.2m path", ("speed -43%", "density +184%")),
            Intervention("b", "Move checkout", "reduce queue spillback", -17.0, .88, .3, 5, "Do not reduce checkout capacity", ("queue 11m",)),
        ]
        ranked = rank_interventions(items)
        self.assertEqual(ranked[0]["id"], "a")
        self.assertEqual(ranked[0]["data_class"], "SIMULATED")
        self.assertEqual(len(ranked[0]["expected_range"]), 2)

    def test_entropy_and_learning_priority(self):
        self.assertAlmostEqual(journey_entropy({"a": 50, "b": 50}), 1.0)
        self.assertGreater(active_learning_priority(confidence=.35, affected_visitors=327, intervention_effort=3)["priority"], 50)


if __name__ == "__main__":
    unittest.main()
