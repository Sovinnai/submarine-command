import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import submarine_command
from submarine_command import engine
from submarine_command.observations import observed_bearing_drift
from submarine_command.platforms import KESTREL


class CapabilityTests(unittest.TestCase):
    def test_exported_package_version_matches_engine_rules(self):
        self.assertEqual(submarine_command.__version__, engine.VERSION)

    def test_brief_and_validation_use_the_same_platform_limits(self):
        game = engine.initialize("34" * 32)
        published = engine.public_view(game)["platform_capabilities"]
        self.assertEqual(published["propulsion"], "nuclear")
        self.assertEqual(game["state"]["platform"], published["platform_id"])
        envelope = published["operating_envelope"]
        engine.validate_order({"id": "valid", "minutes": 5,
                               "speed": envelope["maximum_speed_knots"],
                               "depth": envelope["maximum_depth_feet"]}, game["state"])
        with self.assertRaises(ValueError):
            engine.validate_order({"id": "invalid", "speed": envelope["maximum_speed_knots"] + 1}, game["state"])

    def test_published_link_envelope_is_enforced(self):
        game = engine.initialize("45" * 32)
        limits = KESTREL.public_capabilities()["communications"]["mast_envelope"]
        raw = {"id": "rx", "activity": "receive", "depth": limits["maximum_depth_feet"], "speed": limits["maximum_speed_knots"]}
        engine.validate_order(raw, game["state"])
        with self.assertRaises(ValueError):
            engine.validate_order({**raw, "depth": limits["maximum_depth_feet"] + 1}, game["state"])

    def test_capabilities_command_needs_no_game_and_creates_no_save(self):
        with tempfile.TemporaryDirectory() as temporary:
            location = Path(temporary) / "should-not-exist"
            result = subprocess.run([sys.executable, "-m", "submarine_command", "--session", str(location), "capabilities"], capture_output=True, text=True, check=True)
            public = json.loads(result.stdout)
            self.assertEqual(public["platform_id"], KESTREL.identifier)
            self.assertFalse(location.exists())

    def test_unimplemented_capabilities_are_explicit(self):
        public = KESTREL.public_capabilities()
        self.assertFalse(public["sonar"]["towed_array_modeled"])
        self.assertFalse(public["environment"]["convergence_zone_modeled"])
        self.assertFalse(public["weapons"]["employment_modeled"])
        self.assertFalse(public["measurements"]["numeric_narrowband_frequencies"])


class BearingDriftTests(unittest.TestCase):
    def obs(self, minute, bearing):
        return {"elapsed_minutes": minute, "bearing_true": bearing, "time": str(minute)}

    def test_single_observation_or_same_time_has_no_drift(self):
        self.assertEqual(observed_bearing_drift([self.obs(0, 95)])["status"], "undetermined")
        self.assertEqual(observed_bearing_drift([self.obs(0, 95), self.obs(0, 97)])["status"], "undetermined")

    def test_drift_wraps_north_in_either_direction(self):
        right = observed_bearing_drift([self.obs(0, 358), self.obs(10, 3)])
        left = observed_bearing_drift([self.obs(0, 3), self.obs(10, 358)])
        self.assertEqual((right["direction"], right["change_degrees"], right["rate_degrees_per_minute"]), ("right", 5, 0.5))
        self.assertEqual((left["direction"], left["change_degrees"], left["rate_degrees_per_minute"]), ("left", -5, -0.5))

    def test_small_change_and_old_history_do_not_create_confident_trend(self):
        self.assertEqual(observed_bearing_drift([self.obs(0, 95), self.obs(5, 97)])["direction"], "unresolved")
        self.assertEqual(observed_bearing_drift([self.obs(0, 95), self.obs(40, 110)])["status"], "undetermined")

    def test_drift_needs_only_observations(self):
        sample = [self.obs(0, 95), self.obs(10, 100)]
        before = json.dumps(sample)
        result = observed_bearing_drift(sample)
        self.assertEqual(result["direction"], "right")
        self.assertEqual(json.dumps(sample), before)


if __name__ == "__main__":
    unittest.main()
