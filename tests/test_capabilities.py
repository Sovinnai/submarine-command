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
        base = {"id": "valid", "expected_turn": 0, "activity": "listen", "minutes": 5,
                "interrupt_on": [], "course": 90, "operating_mode": "high_power",
                "speed": envelope["maximum_speed_knots"],
                "depth": envelope["maximum_depth_feet"]}
        engine.validate_order(base, game["state"])
        with self.assertRaises(ValueError):
            engine.validate_order({**base, "id": "invalid",
                                   "speed": envelope["maximum_speed_knots"] + 1}, game["state"])

    def test_published_link_envelope_is_enforced(self):
        game = engine.initialize("45" * 32)
        communications = KESTREL.public_capabilities()["communications"]
        limits = communications["mast_envelope"]
        raw = {"id": "rx", "expected_turn": 0, "activity": "receive",
               "link": "mast_receive", "minutes": 5, "interrupt_on": [],
               "course": 90, "operating_mode": "standard",
               "depth": limits["maximum_depth_feet"], "speed": limits["maximum_speed_knots"]}
        engine.validate_order(raw, game["state"])
        with self.assertRaises(ValueError):
            engine.validate_order({**raw, "depth": limits["maximum_depth_feet"] + 1}, game["state"])
        buoyant = next(mode for mode in communications["modes"] if mode["id"] == "buoyant_receive")
        deep = {"id": "buoy", "expected_turn": 0, "activity": "receive",
                "link": "buoyant_receive", "minutes": 5, "interrupt_on": [],
                "course": 90, "operating_mode": "standard",
                "depth": buoyant["envelope"]["maximum_depth_feet"],
                "speed": buoyant["envelope"]["maximum_speed_knots"]}
        engine.validate_order(deep, game["state"])
        with self.assertRaises(ValueError):
            engine.validate_order(
                {**deep, "depth": buoyant["envelope"]["minimum_depth_feet"] - 1},
                game["state"],
            )

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
        self.assertTrue(public["environment"]["full_sound_speed_profile_modeled"])
        self.assertTrue(public["environment"]["convergence_zone_modeled"])
        self.assertTrue(public["environment"]["bottom_bounce_modeled"])
        self.assertTrue(public["environment"]["half_channel_modeled"])
        self.assertFalse(public["environment"]["ray_solver_modeled"])
        self.assertEqual(
            public["environment"]["path_status"],
            ["supported", "uncertain", "outside_scope"],
        )
        self.assertFalse(public["weapons"]["employment_modeled"])
        communications = public["communications"]
        self.assertEqual(communications["receive_modes"], ["mast_receive", "buoyant_receive"])
        self.assertEqual(communications["transmit_modes"], ["mast_transmit"])
        self.assertTrue(communications["buoyant_array_modeled"])
        self.assertTrue(communications["submerged_reception_modeled"])
        catalog = KESTREL.public_definition()
        self.assertEqual(
            [mode["id"] for mode in catalog["communication_modes"]],
            [mode["id"] for mode in communications["modes"]],
        )
        for mode in communications["modes"]:
            antenna = next(
                item for item in communications["inventory"] if item["id"] == mode["antenna"]
            )
            self.assertTrue(antenna["implemented"])
        towed = next(item for item in public["sensors"] if item["id"] == "towed_array")
        self.assertFalse(towed["implemented"])
        self.assertTrue(public["measurements"]["numeric_narrowband_frequencies"])
        self.assertTrue(public["sonar"]["spectral_frequencies_modeled"])
        model = engine.capability_report()["narrowband_model"]
        self.assertFalse(model["identity_lookup"])
        self.assertIn("SL - TL - NL + DI - DT", model["signal_excess"])
        self.assertNotIn("fundamental_hz", json.dumps(model))

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
        self.assertGreater(snorkel.relative_noise, battery_mode.relative_noise)
        self.assertLessEqual(snorkel.depth_maximum_feet, 60)

    def test_biologic_is_a_motion_and_signature_entity_without_vessel_components(self):
        self.assertEqual(BIOLOGIC.category, EntityCategory.BIOLOGIC)
        self.assertGreater(BIOLOGIC.envelope.maximum_depth_feet, 0)
        self.assertTrue(all(mode.relative_noise > 0 for mode in BIOLOGIC.modes))
        self.assertEqual(BIOLOGIC.sensors, ())
        self.assertEqual(BIOLOGIC.weapons, ())


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
