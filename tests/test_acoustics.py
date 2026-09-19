"""Validation of the propagation model against closed-form and reference values.

Three kinds of check, because only the first kind can be exact:

  * Closed form. Spreading laws, reciprocity and published absorption values are
    reproduced to within a tight tolerance.
  * Ordinal. Relationships that must hold in a stated direction whatever the
    tuning: a thicker duct traps lower frequencies, a steeper grazing angle
    loses more on a slow bottom, longer integration lowers the threshold.
  * Ballpark. Quantities quoted as ranges in the literature are asserted to fall
    inside those ranges, never to match a particular figure.

A straight-ray, single-bounce model cannot reproduce a parabolic-equation or
normal-mode result, and nothing here pretends it does.
"""
import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from submarine_command import acoustics as a


def profile(layer_feet=400.0, water_feet=8500.0, duct_gradient=0.016,
            thermocline_gradient=-0.08, deep_gradient=0.016):
    return a.layered_profile(1500.0, a.feet_to_metres(layer_feet), duct_gradient,
                             thermocline_gradient, deep_gradient, 710.0,
                             a.feet_to_metres(water_feet))


def environment(layer_feet=400.0, water_feet=8500.0, bottom="fine_sediment",
                sea_state=3, shipping=4, **kwargs):
    return a.Environment(profile(layer_feet, water_feet, **kwargs),
                         a.feet_to_metres(water_feet), bottom, sea_state, shipping)


def path_of(solution, name):
    return next(path for path in solution.paths if path.name == name)


class AbsorptionTests(unittest.TestCase):
    """Thorp's expression against values published for it."""

    def test_published_absorption_values(self):
        for frequency_hz, expected_db_per_kiloyard in ((100.0, 0.0040), (1000.0, 0.0632),
                                                       (10000.0, 1.08)):
            measured = a.absorption_db_per_metre(frequency_hz) * a.METRES_PER_KILOYARD
            self.assertAlmostEqual(measured, expected_db_per_kiloyard, delta=0.01,
                                   msg=f"absorption at {frequency_hz} Hz")

    def test_absorption_increases_with_frequency(self):
        values = [a.absorption_db_per_metre(band.centre_hz) for band in a.BANDS]
        self.assertEqual(values, sorted(values))

    def test_absorption_is_negligible_low_and_dominant_high(self):
        # Over twenty nautical miles: tenths of a decibel at 35 Hz, tens at 7.7 kHz.
        span = a.nautical_miles_to_metres(20)
        self.assertLess(a.absorption_db_per_metre(35.0) * span, 0.5)
        self.assertGreater(a.absorption_db_per_metre(7746.0) * span, 10.0)


class SpreadingTests(unittest.TestCase):
    """Spreading laws, isolated by reading one named path at a time."""

    def test_direct_path_spreads_spherically(self):
        # Flat profile: no refraction, so the direct path is available at any range.
        flat = a.Environment(a.layered_profile(1500.0, 100.0, 0.0, 0.0, 0.0, 710.0, 3000.0),
                             3000.0, "fine_sediment", 3, 4)
        first = path_of(a.transmission_loss(flat, 35.0, 4000.0, 100.0, 100.0), "direct")
        second = path_of(a.transmission_loss(flat, 35.0, 8000.0, 100.0, 100.0), "direct")
        self.assertEqual(first.status, "supported")
        absorption = a.absorption_db_per_metre(35.0) * 4000.0
        self.assertAlmostEqual(second.loss_db - first.loss_db, 6.0206 + absorption, places=2)

    def test_trapped_duct_path_spreads_cylindrically_beyond_the_transition(self):
        env = environment(layer_feet=500.0)
        cutoff = env.profile.duct_cutoff_hz
        frequency = cutoff * 3.0
        near = path_of(a.transmission_loss(env, frequency, 20000.0, 20.0, 30.0), "surface_duct")
        far = path_of(a.transmission_loss(env, frequency, 40000.0, 20.0, 30.0), "surface_duct")
        self.assertEqual(near.status, "supported")
        extra = ((a.DUCT_LEAKAGE_DB_PER_KM * math.sqrt(cutoff / frequency) * 20.0)
                 + a.absorption_db_per_metre(frequency) * 20000.0)
        self.assertAlmostEqual(far.loss_db - near.loss_db, 3.0103 + extra, places=2)

    def test_transmission_loss_is_reciprocal_in_the_two_depths(self):
        env = environment()
        for range_m in (2000.0, 9260.0, 37040.0):
            forward = a.transmission_loss(env, 300.0, range_m, 60.0, 210.0)
            reverse = a.transmission_loss(env, 300.0, range_m, 210.0, 60.0)
            self.assertEqual(forward.loss_db, reverse.loss_db)
            self.assertEqual([p.status for p in forward.paths], [p.status for p in reverse.paths])

    def test_loss_increases_with_range(self):
        env = environment()
        losses = [a.transmission_loss(env, 104.0, a.nautical_miles_to_metres(nm), 150.0, 180.0).loss_db
                  for nm in (2, 5, 10, 20, 30)]
        self.assertEqual(losses, sorted(losses))


class RefractionTests(unittest.TestCase):
    """The layer's effect comes from ray curvature, not from a flag."""

    def test_limiting_range_grows_with_separation_from_the_layer(self):
        shape = profile()
        layer = shape.layer_depth_m
        near = a.refracted_limiting_range_m(shape, layer + 10.0, layer + 10.0)
        far = a.refracted_limiting_range_m(shape, layer + 120.0, layer + 120.0)
        self.assertGreater(far, near)

    def test_limiting_range_is_symmetric_and_depth_dependent(self):
        shape = profile()
        first = a.refracted_limiting_range_m(shape, 60.0, 200.0)
        second = a.refracted_limiting_range_m(shape, 200.0, 60.0)
        self.assertEqual(first, second)
        self.assertNotEqual(first, a.refracted_limiting_range_m(shape, 60.0, 260.0))

    def test_direct_path_closes_and_the_shadow_opens_beyond_the_limit(self):
        env = environment()
        limit = a.refracted_limiting_range_m(env.profile, 150.0, 200.0)
        inside = a.transmission_loss(env, 104.0, limit * 0.5, 150.0, 200.0)
        outside = a.transmission_loss(env, 104.0, limit * 2.0, 150.0, 200.0)
        self.assertEqual(path_of(inside, "direct").status, "supported")
        self.assertEqual(path_of(inside, "shadow_zone").status, "out_of_scope")
        self.assertEqual(path_of(outside, "direct").status, "out_of_scope")
        self.assertEqual(path_of(outside, "shadow_zone").status, "uncertain")

    def test_shadow_excess_grows_then_saturates(self):
        env = environment()
        limit = a.refracted_limiting_range_m(env.profile, 150.0, 200.0)
        excesses = []
        for multiple in (1.5, 3.0, 8.0, 20.0):
            range_m = limit * multiple
            shadow = path_of(a.transmission_loss(env, 104.0, range_m, 150.0, 200.0), "shadow_zone")
            spherical = 20.0 * math.log10(math.hypot(range_m, 50.0))
            excesses.append(shadow.loss_db - spherical
                            - a.absorption_db_per_metre(104.0) * math.hypot(range_m, 50.0))
        self.assertEqual(excesses, sorted(excesses))
        plateau = a.SHADOW_PLATEAU_BASE_DB + a.SHADOW_PLATEAU_SLOPE_DB * math.log10(104.0 / 100.0)
        self.assertLess(excesses[-1], plateau + 0.01)
        self.assertGreater(excesses[-1], plateau * 0.95)


class SurfaceDuctTests(unittest.TestCase):
    def test_a_thicker_duct_traps_lower_frequencies(self):
        cutoffs = [profile(layer_feet=feet).duct_cutoff_hz for feet in (150, 300, 500, 700)]
        self.assertEqual(cutoffs, sorted(cutoffs, reverse=True))

    def test_cutoff_matches_the_published_relation(self):
        shape = profile(layer_feet=300.0)
        thickness = shape.layer_depth_m
        self.assertAlmostEqual(shape.duct_cutoff_hz,
                               1500.0 / (0.008 * thickness ** 1.5), places=1)

    def test_a_hundred_metre_duct_cuts_off_in_the_published_band(self):
        # A duct of roughly one hundred metres is quoted as trapping from a
        # couple of hundred hertz upward.
        cutoff = profile(layer_feet=a.metres_to_feet(100.0)).duct_cutoff_hz
        self.assertTrue(150.0 <= cutoff <= 250.0, cutoff)

    def test_below_cutoff_and_below_the_layer_the_duct_carries_nothing(self):
        env = environment(layer_feet=400.0)
        cutoff = env.profile.duct_cutoff_hz
        below = a.transmission_loss(env, cutoff * 0.5, 20000.0, 30.0, 40.0)
        self.assertEqual(path_of(below, "surface_duct").status, "out_of_scope")
        deep = a.transmission_loss(env, cutoff * 3.0, 20000.0, 30.0, 400.0)
        self.assertEqual(path_of(deep, "surface_duct").status, "out_of_scope")
        self.assertIn("above the layer", path_of(deep, "surface_duct").note)

    def test_trapping_near_cutoff_is_reported_as_uncertain(self):
        env = environment(layer_feet=400.0)
        cutoff = env.profile.duct_cutoff_hz
        marginal = a.transmission_loss(env, cutoff * 1.05, 20000.0, 30.0, 40.0)
        confident = a.transmission_loss(env, cutoff * 4.0, 20000.0, 30.0, 40.0)
        self.assertEqual(path_of(marginal, "surface_duct").status, "uncertain")
        self.assertEqual(path_of(confident, "surface_duct").status, "supported")


class BottomTests(unittest.TestCase):
    def test_slow_bottom_loses_more_at_every_angle_than_a_fast_one(self):
        for angle in (2.0, 10.0, 25.0, 60.0):
            self.assertGreater(a.bottom_loss_db("fine_sediment", angle),
                               a.bottom_loss_db("sand", angle))
            self.assertGreater(a.bottom_loss_db("sand", angle),
                               a.bottom_loss_db("rock", angle))

    def test_loss_rises_with_grazing_angle(self):
        for bottom in a.BOTTOM_TYPES:
            losses = [a.bottom_loss_db(bottom, angle) for angle in (0, 5, 10, 20, 45, 90)]
            self.assertEqual(losses, sorted(losses), bottom)

    def test_shallower_water_steepens_the_bounce_and_costs_more(self):
        deep = environment(water_feet=9000.0)
        shallow = environment(water_feet=3000.0)
        range_m = a.nautical_miles_to_metres(10)
        deep_path = path_of(a.transmission_loss(deep, 104.0, range_m, 150.0, 150.0), "bottom_bounce")
        shallow_path = path_of(a.transmission_loss(shallow, 104.0, range_m, 150.0, 150.0), "bottom_bounce")
        self.assertIn("degrees grazing", deep_path.note)
        self.assertGreater(deep_path.loss_db, shallow_path.loss_db)

    def test_very_shallow_grazing_is_out_of_scope_not_quietly_wrong(self):
        env = environment(water_feet=3000.0)
        far = a.transmission_loss(env, 104.0, a.nautical_miles_to_metres(40), 150.0, 150.0)
        bounce = path_of(far, "bottom_bounce")
        self.assertEqual(bounce.status, "out_of_scope")
        self.assertIn("refraction governs", bounce.note)

    def test_steep_bounces_are_supported_and_shallow_ones_uncertain(self):
        env = environment(water_feet=6000.0)
        steep = path_of(a.transmission_loss(env, 104.0, 2000.0, 150.0, 150.0), "bottom_bounce")
        shallow = path_of(a.transmission_loss(env, 104.0, 40000.0, 150.0, 150.0), "bottom_bounce")
        self.assertEqual(steep.status, "supported")
        self.assertEqual(shallow.status, "uncertain")


class ConvergenceZoneTests(unittest.TestCase):
    def test_no_depth_excess_means_no_convergence_zone_claim(self):
        env = environment(water_feet=8500.0)
        solution = a.transmission_loss(env, 104.0, a.nautical_miles_to_metres(33), 150.0, 150.0)
        zone = path_of(solution, "convergence_zone")
        self.assertEqual(zone.status, "out_of_scope")
        self.assertIsNone(zone.loss_db)

    def test_depth_excess_opens_an_annulus_at_the_published_range(self):
        # A profile whose deep gradient does return sound speed to its surface
        # value, in water deep enough to clear the critical depth.
        deep = a.layered_profile(1500.0, 120.0, 0.016, -0.08, 0.017, 710.0, 5500.0)
        self.assertIsNotNone(deep.critical_depth_m)
        env = a.Environment(deep, 5500.0, "fine_sediment", 3, 4)
        centre = a.CONVERGENCE_ZONE_SPACING_M
        inside = path_of(a.transmission_loss(env, 104.0, centre, 150.0, 150.0), "convergence_zone")
        between = path_of(a.transmission_loss(env, 104.0, centre * 0.6, 150.0, 150.0), "convergence_zone")
        self.assertEqual(inside.status, "uncertain")
        self.assertEqual(between.status, "out_of_scope")
        # The first zone sits in the thirty to thirty-five nautical mile band the
        # literature quotes for deep water.
        self.assertTrue(30.0 <= a.metres_to_nautical_miles(centre) <= 35.0)


class NoiseTests(unittest.TestCase):
    def test_shipping_dominates_low_and_wind_dominates_high(self):
        env = environment()
        low, high = a.BAND_BY_ID["b035"], a.BAND_BY_ID["b2739"]
        self.assertGreater(a.SHIPPING_SPECTRUM_DB[low.identifier], a.WIND_SPECTRUM_DB[low.identifier])
        self.assertGreater(a.WIND_SPECTRUM_DB[high.identifier], a.SHIPPING_SPECTRUM_DB[high.identifier])
        calm = a.ambient_noise_db(environment(sea_state=1), high)
        rough = a.ambient_noise_db(environment(sea_state=5), high)
        self.assertGreater(rough, calm)
        self.assertAlmostEqual(a.ambient_noise_db(env, low),
                               a.ambient_noise_db(environment(sea_state=1), low), delta=0.2)

    def test_a_receiver_inside_the_duct_sits_in_a_louder_noise_field(self):
        env = environment(layer_feet=400.0)
        band = a.BAND_BY_ID["b104"]
        inside = a.ambient_noise_db(env, band, a.feet_to_metres(150.0))
        outside = a.ambient_noise_db(env, band, a.feet_to_metres(700.0))
        self.assertAlmostEqual(inside - outside, a.DUCT_AMBIENT_EXCESS_DB["b104"], places=2)

    def test_flow_noise_is_flat_below_the_quiet_speed_and_rises_above_it(self):
        self.assertEqual(a.flow_noise_rise_db(3.0, 5.0), 0.0)
        self.assertEqual(a.flow_noise_rise_db(5.0, 5.0), 0.0)
        self.assertAlmostEqual(a.flow_noise_rise_db(10.0, 5.0),
                               a.SELF_NOISE_SPEED_SLOPE_DB * math.log10(2.0), places=2)
        self.assertGreater(a.flow_noise_rise_db(15.0, 5.0), a.flow_noise_rise_db(10.0, 5.0))

    def test_levels_combine_on_an_intensity_basis(self):
        self.assertAlmostEqual(a.combine_db(60.0, 60.0), 63.01, places=2)
        self.assertAlmostEqual(a.combine_db(80.0, 40.0), 80.0, places=2)


class ThresholdAndDecisionTests(unittest.TestCase):
    def test_longer_integration_and_narrower_analysis_lower_the_threshold(self):
        band = a.BAND_BY_ID["b104"]
        wide = a.BAND_BY_ID["b7746"]
        self.assertLess(a.detection_threshold_db(band, 600.0, 8.0),
                        a.detection_threshold_db(band, 60.0, 8.0))
        self.assertLess(a.detection_threshold_db(band, 300.0, 8.0),
                        a.detection_threshold_db(wide, 300.0, 8.0))

    def test_signal_excess_follows_the_sonar_equation(self):
        self.assertEqual(a.signal_excess_db(140.0, 80.0, 65.0, 20.0, 5.0), 10.0)
        # Active adds the second traverse and the target's strength.
        self.assertEqual(a.signal_excess_db(215.0, 80.0, 65.0, 20.0, 5.0, 15.0), 20.0)
        self.assertIsNone(a.signal_excess_db(140.0, None, 65.0, 20.0, 5.0))

    def test_detection_probability_is_even_at_zero_excess_and_bounded(self):
        self.assertAlmostEqual(a.detection_probability(0.0), 0.5, places=3)
        self.assertEqual(a.detection_probability(-200.0), a.MINIMUM_DETECTION_PROBABILITY)
        self.assertEqual(a.detection_probability(200.0), a.MAXIMUM_DETECTION_PROBABILITY)
        rising = [a.detection_probability(excess) for excess in (-20, -10, -3, 0, 3, 10, 20)]
        self.assertEqual(rising, sorted(rising))

    def test_one_sigma_of_excess_moves_probability_by_the_normal_amount(self):
        self.assertAlmostEqual(a.detection_probability(a.SIGNAL_EXCESS_SIGMA_DB), 0.841, places=3)


class DeterminismTests(unittest.TestCase):
    """Replay binds the saved world to these numbers, so they are quantised."""

    def test_repeated_evaluation_is_identical(self):
        env = environment()
        first = a.transmission_loss(env, 300.0, 12345.6, 123.4, 234.5)
        second = a.transmission_loss(env, 300.0, 12345.6, 123.4, 234.5)
        self.assertEqual(first.loss_db, second.loss_db)
        self.assertEqual([p.loss_db for p in first.paths], [p.loss_db for p in second.paths])

    def test_stored_values_are_quantised(self):
        env = environment()
        solution = a.transmission_loss(env, 300.0, 12345.6, 123.4, 234.5)
        self.assertEqual(solution.loss_db, round(solution.loss_db, 3))
        self.assertEqual(a.detection_probability(1.2345), round(a.detection_probability(1.2345), 6))

    def test_every_band_has_a_distinct_analysis_bandwidth_entry(self):
        self.assertEqual(len(a.BANDS), len(set(a.BAND_IDS)))
        for band in a.BANDS:
            self.assertLessEqual(band.analysis_bandwidth_hz, band.high_hz - band.low_hz)
            self.assertTrue(band.low_hz < band.centre_hz < band.high_hz)
            self.assertIn(band.identifier, a.SHIPPING_SPECTRUM_DB)
            self.assertIn(band.identifier, a.WIND_SPECTRUM_DB)
            self.assertIn(band.identifier, a.DUCT_AMBIENT_EXCESS_DB)


class NoPathTests(unittest.TestCase):
    def test_beyond_the_modelled_range_the_answer_is_declined_not_extrapolated(self):
        env = environment()
        beyond = a.MAXIMUM_MODELLED_RANGE_M * 1.5
        solution = a.transmission_loss(env, 104.0, beyond, 150.0, 150.0)
        self.assertIsNone(solution.loss_db)
        self.assertEqual(solution.status, "out_of_scope")
        self.assertIsNone(solution.dominant)
        self.assertTrue(all(path.status == "out_of_scope" for path in solution.paths))
        self.assertIn("does not answer", solution.paths[0].note)

    def test_inside_the_modelled_range_some_path_always_carries_energy(self):
        # Leakage into the shadow means reception is weak, never absent, so a
        # detection decision is always made on a number rather than on silence.
        env = environment()
        for nm in (1, 10, 40, 59):
            solution = a.transmission_loss(env, 104.0, a.nautical_miles_to_metres(nm), 150.0, 600.0)
            self.assertIsNotNone(solution.loss_db, nm)


if __name__ == "__main__":
    unittest.main()
