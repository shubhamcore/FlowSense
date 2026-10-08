import unittest

from flowsense.core import (
    Zone,
    apply_homography,
    assign_zone,
    bottleneck_score,
    dead_zone_score,
    dwell_events,
    journey_summary,
    simulate_layout,
    solve_homography,
)


class FlowSenseCoreTests(unittest.TestCase):
    def setUp(self):
        self.zones = [
            Zone("a", "A", ((0, 0), (10, 0), (10, 10), (0, 10))),
            Zone("b", "B", ((10, 0), (20, 0), (20, 10), (10, 10))),
        ]

    def test_zone_assignment(self):
        self.assertEqual(assign_zone((2, 5), self.zones), "a")
        self.assertEqual(assign_zone((15, 5), self.zones), "b")
        self.assertIsNone(assign_zone((30, 5), self.zones))

    def test_dwell_classification_ignores_transit(self):
        points = [
            {"t": 0, "x": 1, "y": 1},
            {"t": 1, "x": 2, "y": 2},
            {"t": 2, "x": 12, "y": 2},
            {"t": 8, "x": 13, "y": 3},
            {"t": 9, "x": 22, "y": 3},
        ]
        events = dwell_events(points, self.zones)
        self.assertEqual(events[0]["classification"], "TRANSIT")
        self.assertEqual(events[1]["classification"], "DWELL")
        self.assertEqual(events[1]["duration"], 6)

    def test_journey_summary(self):
        points = [
            {"t": 0, "x": 1, "y": 1},
            {"t": 6, "x": 2, "y": 2},
            {"t": 7, "x": 12, "y": 2},
            {"t": 14, "x": 13, "y": 3},
        ]
        journey = journey_summary("visitor_1042", points, self.zones)
        self.assertEqual(journey["track_id"], "visitor_1042")
        self.assertEqual(journey["zones"], ["a", "b"])
        self.assertEqual(journey["stops"], 2)

    def test_bottleneck_and_dead_zone_scores(self):
        self.assertGreaterEqual(bottleneck_score({"density": 1, "dwell": 1, "speed_reduction": 1, "persistence": 1, "occupancy": 1}), 99)
        self.assertEqual(dead_zone_score({"traffic_gap": 1, "dwell_gap": 1, "visibility_gap": 1, "connectivity_gap": 1}), 100)

    def test_homography_identity(self):
        source = [(0, 0), (1, 0), (1, 1), (0, 1)]
        target = [(0, 0), (1, 0), (1, 1), (0, 1)]
        h = solve_homography(source, target)
        x, y = apply_homography((.25, .75), h)
        self.assertAlmostEqual(x, .25, places=5)
        self.assertAlmostEqual(y, .75, places=5)

    def test_simulation_is_explicit(self):
        result = simulate_layout({"congestion": 42, "dead_zone_exposure": 68, "journey_efficiency": 71}, display_moved=True)
        self.assertLess(result["congestion"], 42)
        self.assertEqual(result["label"], "SIMULATED — NOT OBSERVED RESULT")


if __name__ == "__main__":
    unittest.main()
