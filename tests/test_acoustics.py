"""Limiting cases for the documented acoustic approximations."""
import math
import unittest

from submarine_command import acoustics as a
from submarine_command.engine import Dice


def summer_column(**kwargs):
    return a.deep_water_test_environment(
        water_depth_feet=2400,
        mixed_layer_feet=180,
        thermocline_base_feet=520,
        surface_c=18.0,
        deep_c=6.0,
        **kwargs,
    )


class MackenzieAndThorpTests(unittest.TestCase):
    def test_mackenzie_increases_with_temperature_and_depth(self):
        cold = a.sound_speed_mackenzie(0.0, 35.0, 0.0)
        warm = a.sound_speed_mackenzie(10.0, 35.0, 0.0)
        deep = a.sound_speed_mackenzie(10.0, 35.0, 1000.0)
        self.assertAlmostEqual(cold, 1448.96, places=2)
        self.assertAlmostEqual(warm, 1489.8034, places=2)
        self.assertGreater(warm, cold)
        self.assertGreater(deep, warm)

    def test_thorp_matches_1967_formula_after_unit_conversion(self):
        def kyd(frequency_khz):
            f2 = frequency_khz ** 2
            return 0.1 * f2 / (1.0 + f2) + 40.0 * f2 / (4100.0 + f2)

        self.assertAlmostEqual(a.thorp_absorption_db_per_km(1000.0), kyd(1.0) * 1.0936, places=6)
        self.assertAlmostEqual(a.thorp_absorption_db_per_km(10000.0), kyd(10.0) * 1.0936, places=6)
        self.assertGreater(a.thorp_absorption_db_per_km(10000.0), a.thorp_absorption_db_per_km(100.0))


class SpreadingAndPathTests(unittest.TestCase):
    def test_spherical_spreading_reference_levels(self):
        self.assertAlmostEqual(a.spherical_spreading_db(1000.0), 60.0)
        self.assertAlmostEqual(a.spherical_spreading_db(10000.0), 80.0)

    def test_loss_increases_with_range(self):
        column = summer_column()
        near = a.transmission_loss(column, 2.0, 80, 80, 120.0)
        far = a.transmission_loss(column, 20.0, 80, 80, 120.0)
        self.assertGreater(far.transmission_loss_db, near.transmission_loss_db)

    def test_high_frequency_has_more_absorption_at_long_range(self):
        column = summer_column()
        low = a.transmission_loss(column, 25.0, 80, 80, 100.0)
        high = a.transmission_loss(column, 25.0, 80, 80, 10000.0)
        direct_low = next(p for p in low.paths if p.path == a.PATH_DIRECT)
        direct_high = next(p for p in high.paths if p.path == a.PATH_DIRECT)
        self.assertGreater(direct_high.transmission_loss_db, direct_low.transmission_loss_db)

    def test_duct_grows_slower_than_spherical_spreading(self):
        column = summer_column()
        duct_near = a.transmission_loss(column, 5.0, 60, 60, 400.0)
        duct_far = a.transmission_loss(column, 20.0, 60, 60, 400.0)
        duct_n = next(p for p in duct_near.paths if p.path == a.PATH_SURFACE_DUCT)
        duct_f = next(p for p in duct_far.paths if p.path == a.PATH_SURFACE_DUCT)
        self.assertEqual(duct_n.status, a.SUPPORTED)
        self.assertEqual(duct_f.status, a.SUPPORTED)
        spherical_delta = a.spherical_spreading_db(a.nm_to_meters(20)) - a.spherical_spreading_db(a.nm_to_meters(5))
        self.assertLess(duct_f.transmission_loss_db - duct_n.transmission_loss_db, spherical_delta)

    def test_thermocline_crossing_raises_direct_loss(self):
        column = summer_column()
        layer = column["mixed_layer_depth_feet"]
        same = a.transmission_loss(column, 12.0, 60, 80, 120.0)
        across = a.transmission_loss(column, 12.0, 60, layer + 300, 120.0)
        same_direct = next(p for p in same.paths if p.path == a.PATH_DIRECT)
        across_direct = next(p for p in across.paths if p.path == a.PATH_DIRECT)
        self.assertGreater(across_direct.transmission_loss_db, same_direct.transmission_loss_db)
        self.assertIn(across_direct.status, {a.SUPPORTED, a.UNCERTAIN})

    def test_rock_reflects_better_than_mud(self):
        mud = summer_column(bottom_type="mud")
        rock = summer_column(bottom_type="rock")
        mud_path = next(p for p in a.transmission_loss(mud, 8.0, 200, 200, 200.0).paths if p.path == a.PATH_BOTTOM_BOUNCE)
        rock_path = next(p for p in a.transmission_loss(rock, 8.0, 200, 200, 200.0).paths if p.path == a.PATH_BOTTOM_BOUNCE)
        self.assertEqual(mud_path.status, a.SUPPORTED)
        self.assertEqual(rock_path.status, a.SUPPORTED)
        self.assertGreater(mud_path.transmission_loss_db, rock_path.transmission_loss_db)

    def test_rayleigh_normal_incidence_matches_impedance_form(self):
        rock = a.BOTTOM_TYPES["rock"]
        density_ratio = rock["density_ratio"]
        speed_ratio = rock["sound_speed_ratio"]
        impedance_r = (density_ratio * speed_ratio - 1.0) / (density_ratio * speed_ratio + 1.0)
        expected = -20.0 * math.log10(abs(impedance_r))
        self.assertAlmostEqual(a.rayleigh_bottom_loss_db(90.0, rock, 0.0), expected, places=4)

    def test_glass_strait_has_no_conjugate_cz(self):
        column = summer_column()
        result = a.transmission_loss(column, 32.0, 80, 80, 120.0)
        cz = next(p for p in result.paths if p.path == a.PATH_CONVERGENCE_ZONE)
        self.assertEqual(cz.status, a.OUTSIDE_SCOPE)
        self.assertIsNone(cz.transmission_loss_db)
        self.assertIn("conjugate", cz.reason.lower())

    def test_deep_water_cz_near_turning_range(self):
        column = a.deep_water_test_environment()
        structure = a.profile_structure(
            column["sound_speed_profile"],
            a.feet_to_meters(column["mixed_layer_depth_feet"]),
            a.feet_to_meters(column["thermocline_base_feet"]),
            a.feet_to_meters(column["water_depth_feet"]),
        )
        source_m = a.feet_to_meters(80)
        source_c = a.interpolate_ssp(column["sound_speed_profile"], source_m)
        conjugate = a.conjugate_depth_m(column["sound_speed_profile"], structure, source_c)
        self.assertIsNotNone(conjugate)
        cz_m = 2.0 * math.sqrt(
            2.0 * source_c * (conjugate - source_m) / structure["deep_gradient_s"]
        )
        inside = a.transmission_loss(column, cz_m / a.METERS_PER_NAUTICAL_MILE, 80, 80, 120.0)
        far = a.transmission_loss(column, 5.0, 80, 80, 120.0)
        cz_in = next(p for p in inside.paths if p.path == a.PATH_CONVERGENCE_ZONE)
        cz_far = next(p for p in far.paths if p.path == a.PATH_CONVERGENCE_ZONE)
        self.assertEqual(cz_in.status, a.SUPPORTED)
        self.assertEqual(cz_far.status, a.OUTSIDE_SCOPE)

    def test_winter_column_supports_half_channel_not_a_summer_label(self):
        winter = a.winter_half_channel_environment()
        summer = summer_column()
        winter_path = next(p for p in a.transmission_loss(winter, 10.0, 80, 80, 200.0).paths if p.path == a.PATH_HALF_CHANNEL)
        summer_path = next(p for p in a.transmission_loss(summer, 10.0, 80, 80, 200.0).paths if p.path == a.PATH_HALF_CHANNEL)
        self.assertEqual(winter_path.status, a.SUPPORTED)
        self.assertEqual(summer_path.status, a.OUTSIDE_SCOPE)
        self.assertIsNone(summer_path.transmission_loss_db)

    def test_transmission_is_reciprocal(self):
        column = summer_column()
        forward = a.transmission_loss(column, 11.0, 80, 500, 150.0)
        backward = a.transmission_loss(column, 11.0, 500, 80, 150.0)
        self.assertAlmostEqual(forward.transmission_loss_db, backward.transmission_loss_db)
        for left, right in zip(forward.paths, backward.paths):
            self.assertEqual(left.path, right.path)
            self.assertEqual(left.status, right.status)
            self.assertEqual(left.transmission_loss_db, right.transmission_loss_db)

    def test_every_path_uses_the_published_status_vocabulary(self):
        for column in (summer_column(), a.deep_water_test_environment(), a.winter_half_channel_environment()):
            result = a.transmission_loss(column, 15.0, 100, 400, 250.0)
            self.assertEqual({path.path for path in result.paths}, set(a.PATHS))
            for path in result.paths:
                self.assertIn(path.status, {a.SUPPORTED, a.UNCERTAIN, a.OUTSIDE_SCOPE})
                if path.status == a.OUTSIDE_SCOPE:
                    self.assertIsNone(path.transmission_loss_db)
                else:
                    self.assertIsNotNone(path.transmission_loss_db)

    def test_cutoff_falls_as_the_duct_thickens(self):
        self.assertGreater(a.duct_cutoff_hz(20.0, 4.0), a.duct_cutoff_hz(80.0, 4.0))
        self.assertGreater(a.duct_cutoff_hz(40.0, 2.0), a.duct_cutoff_hz(40.0, 12.0))


class EnvironmentEstimateTests(unittest.TestCase):
    def test_true_and_measured_columns_are_distinct(self):
        environment = a.initialize_environment(Dice("ab" * 32, []))
        public = a.public_environment(environment, 0)
        self.assertNotEqual(
            [
                (round(p["depth_m"], 2), round(p["sound_speed_m_s"], 3))
                for p in environment["true"]["sound_speed_profile"]
            ],
            [
                (round(p["depth_m"], 2), round(p["sound_speed_m_s"], 3))
                for p in environment["measured"]["sound_speed_profile"]
            ],
        )
        self.assertEqual(
            public["estimated_mixed_layer_depth_feet"],
            round(environment["measured"]["mixed_layer_depth_feet"], 1),
        )
        self.assertGreater(a.profile_age_minutes(environment, 40), a.profile_age_minutes(environment, 0))
        self.assertEqual(
            public["path_model"][a.PATH_CONVERGENCE_ZONE]["status"],
            a.OUTSIDE_SCOPE,
        )

    def test_public_path_model_does_not_use_the_true_column(self):
        environment = a.initialize_environment(Dice("cd" * 32, []))
        environment["true"]["water_depth_feet"] = 13100
        environment["true"]["sound_speed_profile"] = a.deep_water_test_environment()["sound_speed_profile"]
        environment["true"]["mixed_layer_depth_feet"] = 180
        environment["true"]["thermocline_base_feet"] = 2200
        public = a.public_environment(environment, 0)
        self.assertEqual(public["path_model"][a.PATH_CONVERGENCE_ZONE]["status"], a.OUTSIDE_SCOPE)


if __name__ == "__main__":
    unittest.main()
