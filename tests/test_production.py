import unittest
from datetime import datetime, timedelta, timezone

from production.calibration import calibrate
from production.handoff import TrackEntry, TrackExit, match_handoff
from production.storage import RetentionPolicy, is_expired, tenant_key


class ProductionUpgradeTests(unittest.TestCase):
    def test_calibration_quality(self):
        points = [(0, 0), (1, 0), (1, 1), (0, 1), (.5, .5)]
        result = calibrate(points, points)
        self.assertEqual(result.quality, "GOOD")
        self.assertAlmostEqual(result.reprojection_error_m, 0, places=4)

    def test_handoff_uses_spatiotemporal_gate(self):
        exit = TrackExit("cam-a", "local-1", 10, 0, 0, 1, 0)
        entry = TrackEntry("cam-b", "local-9", 12, 1, 0, 1, 0)
        result = match_handoff(exit, entry, portal_center=(0.5, 0), store_secret="test")
        self.assertIsNotNone(result)
        self.assertTrue(result.journey_key.startswith("j_"))
        self.assertEqual(result.method, "SPATIAL_TEMPORAL_GATE")
        self.assertIsNone(match_handoff(exit, TrackEntry("cam-b", "local-9", 30, 1, 0, 1, 0), portal_center=(.5, 0)))

    def test_storage_is_tenant_scoped_and_safe(self):
        self.assertEqual(tenant_key("t1", "s1", "raw", "camera.mp4"), "tenants/t1/stores/s1/raw/camera.mp4")
        with self.assertRaises(ValueError):
            tenant_key("t1", "s1", "raw", "../escape.mp4")
        old = datetime.now(timezone.utc) - timedelta(hours=25)
        self.assertTrue(is_expired(old, retention_seconds=24 * 3600))
        self.assertFalse(is_expired(old, retention_seconds=48 * 3600))


if __name__ == "__main__":
    unittest.main()
