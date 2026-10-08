import unittest

from production.actions import ActionStore, OperationalAction
from production.flow_genome import build_flow_genome, genome_similarity, learn_from_peers


class MoatTests(unittest.TestCase):
    def test_action_loop_transitions_and_measurement_guardrail(self):
        store = ActionStore()
        action = OperationalAction("t", "s", "Move display", "conflict", "move 1m", "owner", "2026-10-12", "congestion", "move reduces congestion")
        created = store.create(action)
        self.assertEqual(created["status"], "OPEN")
        completed = store.complete(action.id)
        self.assertEqual(completed["status"], "AWAITING_MEASUREMENT")
        measured = store.measure(action.id, [42, 41, 43], [34, 35, 33], minimum_sample_per_arm=30)
        self.assertEqual(measured["status"], "AWAITING_MORE_DATA")

    def test_genome_similarity_and_peer_learning(self):
        current = build_flow_genome(store_id="current", journeys=[{"pattern": "a"}] * 5 + [{"pattern": "b"}] * 5, sections=[{"id": "a", "dwell": 80}], bottlenecks=[{"name": "x", "score": 50}], dead_zones=[{"name": "z", "score": 40, "opportunity": 60}], data_class="SYNTHETIC").as_dict()
        peer = dict(current)
        peer["store_id"] = "peer"
        peer["validated_outcomes"] = [{"intervention": "Move end-cap", "decision": "PROMOTE", "effect": {"congestion": -.1}}]
        self.assertGreater(genome_similarity(current, peer), .9)
        self.assertEqual(learn_from_peers(current, [peer], minimum_similarity=.5)[0]["transferability"], "HYPOTHESIS")


if __name__ == "__main__":
    unittest.main()
