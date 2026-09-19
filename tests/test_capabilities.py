from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import submarine_command
from submarine_command import engine
from submarine_command.observations import observed_bearing_drift
from submarine_command.platforms import (
    BIOLOGIC,
    DART,
    ENTITY_SPECS,
    KESTREL,
    EntityCategory,
)


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
                               "operating_mode": "high_power",
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
        self.assertFalse(public["sonar"]["beam_pattern_modeled"])
        self.assertFalse(public["weapons"]["employment_modeled"])
        self.assertFalse(public["measurements"]["numeric_narrowband_frequencies"])
        environment = public["environment"]
        self.assertFalse(environment["ray_solver_modeled"])
        self.assertFalse(environment["reverberation_modeled"])
        # Every modelled path states what it is, and the convergence zone is not
        # advertised as a computed one.
        self.assertEqual(set(environment["path_status_reported"]),
                         {"supported", "uncertain", "out_of_scope"})
        for name in ("direct", "surface_duct", "shadow_zone", "bottom_bounce", "convergence_zone"):
            self.assertIn(name, environment["paths"])
        self.assertIn("no caustic structure", environment["paths"]["convergence_zone"])

    def test_every_supported_activity_publishes_the_envelope_it_is_validated_against(self):
        game = engine.initialize("56" * 32)
        published = engine.capability_report()["activities"]
        self.assertEqual(published["supported"], list(engine.SUPPORTED_ACTIVITIES))
        for name in engine.SUPPORTED_ACTIVITIES:
            self.assertIn(name, published["envelopes"], name)
        # A speed-limited activity is accepted at its published limit and
        # rejected just above it, so the published number is the enforced one
        # rather than a description of it.
        for name in ("repair", "sound_profile"):
            limit = published["envelopes"][name]["maximum_speed_knots"]
            self.assertIsNotNone(limit, name)
            engine.validate_order({"id": f"{name}-at-limit", "activity": name,
                                   "minutes": 5, "speed": limit}, game["state"])
            with self.assertRaises(ValueError, msg=name):
                engine.validate_order({"id": f"{name}-over", "activity": name, "minutes": 5,
                                       "speed": limit + 0.5, "operating_mode": "high_power"},
                                      game["state"])

    def test_link_activities_publish_the_mast_envelope_that_gates_them(self):
        game = engine.initialize("67" * 32)
        envelopes = engine.capability_report()["activities"]["envelopes"]
        for name in ("mast", "receive", "transmit"):
            mast = envelopes[name]["mast_envelope"]
            self.assertEqual(mast, asdict(KESTREL.mast), name)
        limits = envelopes["receive"]["mast_envelope"]
        engine.validate_order({"id": "rx", "activity": "receive",
                               "depth": limits["maximum_depth_feet"],
                               "speed": limits["maximum_speed_knots"]}, game["state"])
        with self.assertRaises(ValueError):
            engine.validate_order({"id": "rx-fast", "activity": "receive",
                                   "depth": limits["maximum_depth_feet"],
                                   "speed": limits["maximum_speed_knots"] + 1}, game["state"])

    def test_catalog_defines_every_requested_entity_category(self):
        self.assertEqual(
            {spec.category for spec in ENTITY_SPECS},
            {
                EntityCategory.SSN,
                EntityCategory.DIESEL_SUBMARINE,
                EntityCategory.MERCHANT,
                EntityCategory.SURFACE_WARSHIP,
                EntityCategory.FISHING_VESSEL,
                EntityCategory.BIOLOGIC,
            },
        )
        for spec in ENTITY_SPECS:
            initial = spec.initial_components()
            self.assertIn(initial["operating_mode"], {
                mode.identifier for mode in spec.modes
            })

    def test_diesel_configuration_has_real_energy_and_snorkel_tradeoffs(self):
        battery = DART.resources[0].public_definition()
        self.assertGreater(battery["consumption_per_hour"]["hotel"], 0)
        self.assertGreater(
            battery["replenishment_per_hour_by_mode"]["snorkeling"],
            battery["replenishment_per_hour_by_mode"]["aip"],
        )
        snorkel = DART.mode("snorkeling")
        battery_mode = DART.mode("battery")
        self.assertGreater(snorkel.level_offset_db, battery_mode.level_offset_db)
        self.assertLessEqual(snorkel.depth_maximum_feet, 60)
        # Snorkeling changes the shape of the signature, not only its level.
        snorkelling_levels = DART.signature.levels_for(snorkel)
        battery_levels = DART.signature.levels_for(battery_mode)
        self.assertGreater(snorkelling_levels[0] - battery_levels[0],
                           snorkelling_levels[-1] - battery_levels[-1])

    def test_biologic_is_a_motion_and_signature_entity_without_vessel_components(self):
        self.assertEqual(BIOLOGIC.category, EntityCategory.BIOLOGIC)
        self.assertGreater(BIOLOGIC.envelope.maximum_depth_feet, 0)
        self.assertEqual(BIOLOGIC.sensors, ())
        self.assertEqual(BIOLOGIC.weapons, ())
        self.assertIsNone(BIOLOGIC.receiver)
        # A vocalizing group radiates high, where machinery radiates low. The
        # shape of the signature, not a label, separates the two.
        levels = BIOLOGIC.signature.source_level_db
        self.assertGreater(levels[-1], levels[0])
        self.assertLess(KESTREL.signature.source_level_db[-1],
                        KESTREL.signature.source_level_db[0])


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
