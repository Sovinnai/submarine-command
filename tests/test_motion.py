"""Target motion and crew assessments use recorded measurements only."""
import json
import math
import unittest

from submarine_command import engine
from submarine_command.observations import (
    SOUND_SPEED_MPS,
    _fit_error,
    frequency_change_assessment,
    observed_bearing_drift,
    target_motion_estimate,
)
from submarine_command import spectra


FORBIDDEN = {
    "seed", "actors", "actor", "kind", "spec", "rng_trace", "initial_state",
    "intent", "last_heard", "aware", "bearing_bias", "true", "emitted_hz",
}


def _bearing(east, north, contact_east, contact_north):
    return round(math.degrees(math.atan2(contact_east - east, contact_north - north)) % 360) % 360


def _sample(minute, bearing, east, north, source="passive", report=None, window=None, range_nm=None):
    return {
        "elapsed_minutes": minute,
        "time": f"t{minute}",
        "bearing_true": bearing,
        "own_east_nm": east,
        "own_north_nm": north,
        "source": source,
        "report_id": report,
        "evidence_window": window,
        "range_estimate_nm": range_nm,
    }


def _ranges(result, speed, course=None):
    return {
        item["range_nm"]
        for item in result["acceptable_solutions"]
        if item["speed_knots"] == speed and item["course_true"] == course
    }


def _scan(test, obj):
    if isinstance(obj, dict):
        test.assertFalse(set(obj) & FORBIDDEN)
        for value in obj.values():
            _scan(test, value)
    elif isinstance(obj, list):
        for value in obj:
            _scan(test, value)


class MotionEstimateTests(unittest.TestCase):
    def test_one_time_does_not_estimate_motion(self):
        single = target_motion_estimate([_sample(0, 90, 0, 0, report="R1")], 0)
        same_time = target_motion_estimate([
            _sample(0, 90, 0, 0, report="R1"),
            _sample(0, 92, 0, 0, report="R2"),
        ], 0)
        for result in (single, same_time):
            self.assertEqual(result["status"], "insufficient_observations")
            self.assertEqual(result["acceptable_solutions"], [])
            self.assertFalse(result["target_maneuver"]["confirmed"])
            self.assertIsNone(result["crew"]["operator"]["emphasized_solution"])

    def test_estimate_reads_measurement_fields_only(self):
        clean = [
            _sample(0, 90, 0, 0, report="R1", window="A000"),
            _sample(30, 90, 0, 0, report="R2", window="A001"),
            _sample(60, 90, 0, 0, report="R3", window="A003"),
        ]
        poisoned = [{
            **item,
            "course": 12,
            "speed": 40,
            "x": 99,
            "y": -4,
            "actor": "actor-a",
            "kind": "submerged",
            "true": {"course": 12},
        } for item in clean]
        before = json.dumps(clean)
        estimated = target_motion_estimate(clean, 60)
        self.assertEqual(json.dumps(clean), before)
        self.assertEqual(estimated, target_motion_estimate(poisoned, 60))
        self.assertFalse(estimated["assumptions"]["hidden_motion_used"])
        _scan(self, estimated)

    def test_constant_bearings_keep_an_ambiguous_family(self):
        observations = [
            _sample(0, 90, 0, 0, report="R1", window="A000"),
            _sample(30, 90, 0, 0, report="R2", window="A001"),
            _sample(60, 90, 0, 0, report="R3", window="A003"),
        ]
        result = target_motion_estimate(observations, 60)
        self.assertEqual(result["status"], "underdetermined")
        self.assertGreater(result["acceptable_solution_count"], 1)
        self.assertLess(min(_ranges(result, 0)), max(_ranges(result, 0)))
        self.assertFalse(result["target_maneuver"]["confirmed"])
        self.assertEqual(result["target_maneuver"]["assessment"], "not_indicated")
        operator = result["crew"]["operator"]["emphasized_solution"]
        supervisor = result["crew"]["supervisor"]["emphasized_solution"]
        self.assertEqual(operator["speed_knots"], 0)
        self.assertNotEqual(supervisor["speed_knots"], 0)
        self.assertEqual(result["crew"]["shared_report_ids"], ["R1", "R2", "R3"])
        self.assertIn("same reports", " ".join(result["crew"]["competing_interpretations"]))
        repeated = []
        for item in observations:
            repeated.extend([item, dict(item), dict(item)])
        again = target_motion_estimate(repeated, 60)
        self.assertEqual(again["status"], result["status"])
        self.assertEqual(again["acceptable_solutions"], result["acceptable_solutions"])

    def test_bearing_drift_is_not_a_target_maneuver(self):
        observations = [
            _sample(0, 90, 0, 0, report="R1"),
            _sample(10, 92, 0, 0, report="R2"),
            _sample(20, 91, 0, 0, report="R3"),
            _sample(30, 94, 0, 0, report="R4"),
        ]
        drift = observed_bearing_drift(observations)
        motion = target_motion_estimate(observations, 30)
        self.assertEqual(drift["direction"], "right")
        self.assertFalse(motion["assumptions"]["bearing_drift_used"])
        self.assertFalse(motion["target_maneuver"]["confirmed"])
        self.assertEqual(motion["target_maneuver"]["assessment"], "not_indicated")
        self.assertIn(0, {item["speed_knots"] for item in motion["acceptable_solutions"]})
        self.assertNotIn(drift["interpretation"], json.dumps(motion["target_maneuver"]))

    def test_own_ship_motion_is_not_a_target_maneuver(self):
        contact = (13.0, 0.0)
        legs = [(0, 0.0, -5.0), (30, 0.0, 0.0), (60, 3.0, 0.0)]
        observations = [
            _sample(
                minute,
                _bearing(east, north, *contact),
                east,
                north,
                report=f"R{index + 1}",
                window=f"A{index:03d}",
            )
            for index, (minute, east, north) in enumerate(legs)
        ]
        straight = [
            _sample(minute, 90, 3.0, 0.0, report=f"S{index + 1}")
            for index, (minute, _east, _north) in enumerate(legs)
        ]
        drift = observed_bearing_drift(observations)
        early = observed_bearing_drift(observations[:2])
        turned = target_motion_estimate(observations, 60)
        held = target_motion_estimate(straight, 60)
        # The recent 30-minute drift is steady. The earlier bearing change is
        # own-ship motion, and the motion fit still has to carry it.
        self.assertEqual(early["direction"], "right")
        self.assertEqual(drift["direction"], "unresolved")
        self.assertIn(10, _ranges(turned, 0))
        self.assertNotIn(1, _ranges(turned, 0))
        self.assertNotIn(24, _ranges(turned, 0))
        self.assertTrue({1, 24} <= _ranges(held, 0))
        self.assertLess(
            max(_ranges(turned, 0)) - min(_ranges(turned, 0)),
            max(_ranges(held, 0)) - min(_ranges(held, 0)),
        )
        self.assertEqual(held["status"], "underdetermined")
        self.assertNotEqual(turned["status"], "underdetermined")
        self.assertFalse(turned["target_maneuver"]["confirmed"])
        self.assertEqual(turned["target_maneuver"]["assessment"], "not_indicated")
        self.assertEqual(turned["geometry"]["baseline"], "own_ship_course_change")

    def test_timestamps_change_which_solutions_fit(self):
        def legs(times):
            return [
                _sample(minute, bearing, 0, 0, report=f"R{index + 1}")
                for index, (minute, bearing) in enumerate(zip(times, (80, 90, 100)))
            ]
        slow = target_motion_estimate(legs((0, 60, 120)), 120)
        fast = target_motion_estimate(legs((0, 10, 20)), 20)

        def speed_per_mile(result):
            return min(
                item["speed_knots"] / item["range_nm"]
                for item in result["acceptable_solutions"]
            )

        self.assertNotEqual(slow["acceptable_solutions"], fast["acceptable_solutions"])
        self.assertLess(speed_per_mile(slow), speed_per_mile(fast))
        self.assertFalse(slow["target_maneuver"]["confirmed"])
        self.assertFalse(fast["target_maneuver"]["confirmed"])

    def test_mast_gate_is_tighter_than_the_acoustic_gate(self):
        def legs(source):
            return [
                _sample(minute, bearing, 0, 0, source=source, report=f"R{index + 1}")
                for index, (minute, bearing) in enumerate(((0, 90), (10, 94), (20, 90)))
            ]
        passive = target_motion_estimate(legs("passive"), 20)
        mast = target_motion_estimate(legs("mast"), 20)
        self.assertTrue(_ranges(passive, 0))
        self.assertEqual(_ranges(mast, 0), set())
        self.assertLess(mast["acceptable_solution_count"], passive["acceptable_solution_count"])
        self.assertFalse(mast["target_maneuver"]["confirmed"])
        self.assertEqual(mast["target_maneuver"]["assessment"], "not_indicated")

    def test_active_range_limits_range_and_leaves_speed_open(self):
        observations = [
            _sample(0, 90, 0, 0, report="R1"),
            _sample(30, 90, 0, 0, report="R2"),
            _sample(60, 90, 0, 0, report="R3", range_nm=10),
        ]
        result = target_motion_estimate(observations, 60)
        ranges = {item["range_nm"] for item in result["acceptable_solutions"]}
        speeds = {item["speed_knots"] for item in result["acceptable_solutions"]}
        self.assertEqual(ranges, {10})
        self.assertGreater(len(speeds), 1)
        self.assertEqual(result["status"], "underdetermined")
        self.assertFalse(result["target_maneuver"]["confirmed"])

    def test_reversed_bearings_are_not_a_confirmed_maneuver(self):
        observations = [
            _sample(0, 0, 0, 0, report="R1"),
            _sample(10, 180, 0, 0, report="R2"),
            _sample(20, 0, 0, 0, report="R3"),
        ]
        result = target_motion_estimate(observations, 20)
        self.assertEqual(result["status"], "inconsistent_with_constant_motion")
        self.assertFalse(result["target_maneuver"]["confirmed"])
        text = " ".join(result["crew"]["competing_interpretations"])
        self.assertIn("Measurement error", text)
        self.assertIn("course or speed change", text)
        self.assertIn("does not confirm", text)

    def test_drift_history_limit_does_not_discard_older_bearings(self):
        observations = [_sample(0, 90, 0, 0, report="R1"), _sample(40, 96, 0, 0, report="R2")]
        self.assertEqual(observed_bearing_drift(observations)["status"], "undetermined")
        motion = target_motion_estimate(observations, 40)
        self.assertNotEqual(motion["status"], "insufficient_observations")
        self.assertEqual(motion["measurements"]["report_ids"], ["R1", "R2"])
        self.assertEqual(motion["measurements"]["stale_report_ids"], ["R1"])
        self.assertIn("R1", " ".join(motion["crew"]["competing_interpretations"]))

    def test_repeated_bearings_in_one_window_do_not_tighten_the_gate(self):
        later = [
            _sample(30, 90, 0, 4, report="R2", window="A001"),
            _sample(60, 90, 3, 4, report="R3", window="A003"),
        ]
        latest_only = [
            _sample(10, 96, 0.4, 0.2, report="R1b", window="A000"),
            *later,
        ]
        repeated = [
            _sample(0, 90, 0, 0, report="R1", window="A000"),
            *latest_only,
        ]
        both = target_motion_estimate(repeated, 60)
        single = target_motion_estimate(latest_only, 60)
        self.assertEqual(both["acceptable_solutions"], single["acceptable_solutions"])
        self.assertGreater(both["acceptable_solution_count"], 0)
        self.assertIn("R1", both["measurements"]["report_ids"])

    def test_successive_turns_use_the_full_course_span(self):
        points = [(0.0, 0.0)]
        course = 0.0
        for _ in range(3):
            east, north = points[-1]
            radians = math.radians(course)
            points.append((east + 2.0 * math.sin(radians), north + 2.0 * math.cos(radians)))
            course += 15.0
        contact = (8.0, 0.0)
        observations = [
            _sample(
                index * 30,
                _bearing(east, north, *contact),
                east,
                north,
                report=f"R{index + 1}",
                window=f"A{index:03d}",
            )
            for index, (east, north) in enumerate(points)
        ]
        result = target_motion_estimate(observations, 90)
        self.assertGreaterEqual(result["geometry"]["own_ship_course_change_degrees"], 30)
        self.assertEqual(result["geometry"]["baseline"], "own_ship_course_change")
        self.assertNotEqual(result["status"], "underdetermined")
        self.assertFalse(result["target_maneuver"]["confirmed"])

    def test_fractional_shared_bias_keeps_a_cell_the_integer_grid_drops(self):
        # The later look is the range reference, 2 nm from a stopped contact.
        # The earlier look is close aboard, so a one-degree bias miss swings
        # that bearing by more than the gate. Integer biases fail; 1.55 degrees fits.
        bias = 1.55
        contact = (2.0, 0.0)
        legs = ((0, 1.85, 0.0, -0.5), (40, 0.0, 0.0, 0.0))
        observations = []
        for minute, east, north, independent in legs:
            true = math.degrees(math.atan2(contact[0] - east, contact[1] - north)) % 360
            observations.append(_sample(
                minute,
                (true + bias + independent) % 360,
                east,
                north,
                report=f"R{minute}",
                window=f"A{minute // 20:03d}",
            ))
        result = target_motion_estimate(observations, 40)
        self.assertIn(2, _ranges(result, 0))
        samples = [
            {
                "elapsed_minutes": item["elapsed_minutes"],
                "bearing_true": item["bearing_true"],
                "own_east_nm": item["own_east_nm"],
                "own_north_nm": item["own_north_nm"],
                "source": item["source"],
                "evidence_window": item["evidence_window"],
                "range_estimate_nm": None,
            }
            for item in observations
        ]
        for integer_bias in (-2, -1, 0, 1, 2):
            fitted = _fit_error(samples, 2, None, 0, integer_bias)
            self.assertTrue(fitted is None or fitted[1] > 0)


class ClassificationTests(unittest.TestCase):
    def spectrum(self):
        return {
            "lines": [{
                "measured_hz": 2200.0,
                "uncertainty_hz": 1.2,
                "quality": "moderate",
                "emitted_hz": 2190.0,
            }],
            "broadband": [],
            "harmonic_relations": [],
        }

    def observation(self, report, minute):
        return {
            "elapsed_minutes": minute,
            "time": str(minute),
            "evidence_window": "A000",
            "report_id": report,
            "spectrum": self.spectrum(),
        }

    def track(self, observations):
        return {
            "evidence": {"0": "high_band_energy"},
            "visual": None,
            "observations": observations,
            "actor": "actor-a",
            "kind": "submerged",
            "course": 12,
        }

    def test_likelihoods_cite_shared_lines_and_overlap(self):
        assessed = engine.assessments(self.track([self.observation("R0007", 5)]), 10)
        repeated = engine.assessments(
            self.track([self.observation("R0007", 5), self.observation("R0008", 10)]),
            10,
        )
        self.assertEqual(
            assessed["supervisor"]["likelihoods"],
            repeated["supervisor"]["likelihoods"],
        )
        self.assertEqual(repeated["evidence_relationship"]["shared_report_ids"], ["R0007", "R0008"])
        self.assertEqual(
            repeated["supervisor"]["supporting_reports"],
            repeated["operator"]["supporting_reports"],
        )
        self.assertEqual(repeated["supervisor"]["favors"], "surface")
        self.assertEqual(repeated["operator"]["favors"], "submerged")
        cited = repeated["cited_evidence"][0]
        self.assertEqual(cited["measured_lines"][0]["measured_hz"], 2200.0)
        self.assertGreater(min(cited["family_likelihoods"].values()), 0)
        self.assertEqual(cited["domains_with_likelihood"], 3)
        self.assertAlmostEqual(sum(repeated["supervisor"]["likelihoods"].values()), 1.0)
        self.assertFalse(repeated["evidence_relationship"]["independent_corroboration"])
        self.assertIn("same reports", " ".join(repeated["evidence_relationship"]["competing_interpretations"]))
        self.assertNotIn("emitted_hz", json.dumps(repeated))
        _scan(self, repeated)
        stale = engine.assessments(
            self.track([self.observation("R0007", 5), self.observation("R0008", 10)]),
            40,
        )
        self.assertEqual(stale["evidence_relationship"]["stale_report_ids"], ["R0007", "R0008"])
        self.assertIn("Stale reports", stale["supervisor"]["reading"])

    def test_prior_without_a_report_is_labeled_as_a_prior(self):
        assessed = engine.assessments({"evidence": {}, "visual": None, "observations": []}, 0)
        self.assertEqual(assessed["supervisor"]["basis"], "prior_only")
        self.assertEqual(assessed["supervisor"]["supporting_reports"], [])
        self.assertEqual(assessed["evidence_relationship"]["shared_report_ids"], [])
        self.assertIn("priors", assessed["evidence_relationship"]["competing_interpretations"][0])

    def test_visual_replaces_the_acoustic_update_for_both_roles(self):
        track = self.track([self.observation("R0007", 5)])
        track["visual"] = {"report_id": "R0009", "elapsed_minutes": 15, "description": "Surface vessel observed"}
        assessed = engine.assessments(track, 15)
        self.assertEqual(assessed["supervisor"]["likelihoods"], assessed["operator"]["likelihoods"])
        self.assertEqual(assessed["supervisor"]["basis"], "visual")
        self.assertEqual(assessed["supervisor"]["favors"], "surface")
        self.assertEqual(assessed["supervisor"]["confidence"], "high")
        self.assertEqual(assessed["supervisor"]["supporting_reports"], ["R0009"])
        self.assertEqual(
            assessed["evidence_relationship"]["acoustic_reports_replaced_by_visual"],
            ["R0007"],
        )
        self.assertFalse(assessed["evidence_relationship"]["independent_corroboration"])

    def test_an_empty_later_spectrum_does_not_replace_the_cited_lines(self):
        first = self.observation("R0007", 5)
        empty = self.observation("R0008", 10)
        empty["spectrum"] = {"lines": [], "broadband": [], "harmonic_relations": []}
        assessed = engine.assessments(self.track([first, empty]), 10)
        cited = assessed["cited_evidence"][0]
        self.assertEqual(cited["feature"], "high_band_energy")
        self.assertEqual(cited["measured_lines"][0]["measured_hz"], 2200.0)
        self.assertEqual(cited["report_ids"], ["R0007"])
        self.assertNotIn("R0008", assessed["evidence_relationship"]["shared_report_ids"])

    def test_visual_report_is_not_listed_as_replaced_acoustic_evidence(self):
        track = self.track([self.observation("R0007", 5)])
        track["observations"].append({
            "elapsed_minutes": 15,
            "time": "15",
            "source": "mast",
            "report_id": "R0009",
            "bearing_true": 90,
        })
        track["visual"] = {
            "report_id": "R0009",
            "elapsed_minutes": 15,
            "description": "Surface vessel observed",
        }
        assessed = engine.assessments(track, 15)
        replaced = assessed["evidence_relationship"]["acoustic_reports_replaced_by_visual"]
        self.assertEqual(replaced, ["R0007"])
        self.assertNotIn("R0009", replaced)


class PublicContactTests(unittest.TestCase):
    def test_public_contact_keeps_drift_and_motion_apart(self):
        game = engine.initialize("ab" * 32)
        view = engine.public_view(game)
        contact = view["contacts"][0]
        self.assertNotIn("course_speed_solution", contact)
        self.assertEqual(
            contact["target_motion"],
            target_motion_estimate(contact["observations"], view["elapsed_minutes"]),
        )
        self.assertEqual(
            contact["observed_bearing_drift"],
            observed_bearing_drift(contact["observations"]),
        )
        self.assertFalse(contact["target_motion"]["target_maneuver"]["confirmed"])
        self.assertFalse(contact["target_motion"]["assumptions"]["hidden_motion_used"])
        self.assertFalse(contact["assessments"]["evidence_relationship"]["independent_corroboration"])
        report_ids = {entry["id"] for entry in game["state"]["reports"]}
        self.assertIn(contact["observations"][0]["report_id"], report_ids)
        _scan(self, contact["target_motion"])
        _scan(self, contact["assessments"])
        model = view["platform_capabilities"]["target_motion_model"]
        self.assertFalse(model["hidden_motion_used"])
        self.assertFalse(model["confirmed_target_maneuver"])
        self.assertIn("target_motion", view["platform_capabilities"]["measurements"])
        self.assertEqual(
            contact["frequency_change"],
            frequency_change_assessment(contact["observations"], view["elapsed_minutes"]),
        )
        change = contact["frequency_change"]
        self.assertFalse(change["confirmed_maneuver"])
        self.assertFalse(change["assumptions"]["hidden_motion_used"])
        self.assertFalse(change["assumptions"]["aspect_modeled"])
        self.assertEqual(change["assumptions"]["correlation_window_minutes"], 20)
        self.assertEqual(change["assumptions"]["operator_sigma"], 2.0)
        self.assertEqual(change["assumptions"]["supervisor_sigma"], 3.0)
        if change["measurements"]["spectrum_count"] < 2:
            self.assertEqual(change["status"], "not_indicated")
        _scan(self, change)
        self.assertNotIn("_ratio", json.dumps(change))
        published = view["platform_capabilities"]["frequency_change_model"]
        self.assertFalse(published["aspect_modeled"])
        self.assertEqual(published["correlation_window_minutes"], spectra.CORRELATION_WINDOW_MINUTES)
        self.assertEqual(spectra.CORRELATION_WINDOW_MINUTES, 20)
        self.assertIn("frequency_change", view["platform_capabilities"]["measurements"])


def _tone(frequency, quality="moderate", uncertainty=0.05):
    return {"measured_hz": frequency, "uncertainty_hz": uncertainty, "quality": quality}


def _look(minute, report, lines, window="W000", east=0.0, north=0.0, bearing=90.0, course=0.0, speed=0.0):
    return {
        "elapsed_minutes": minute,
        "time": f"t{minute}",
        "bearing_true": bearing,
        "own_east_nm": east,
        "own_north_nm": north,
        "own_course_true": course,
        "own_speed_knots": speed,
        "evidence_window": window,
        "report_id": report,
        "spectrum": {"lines": lines},
    }


class FrequencyChangeTests(unittest.TestCase):
    def test_same_window_shift_splits_the_two_sigma_gates(self):
        observations = [
            _look(0, "R1", [_tone(60.0, "moderate")]),
            _look(5, "R2", [_tone(60.030, "weak")]),
        ]
        result = frequency_change_assessment(observations, 5)
        operator = result["crew"]["operator"]
        supervisor = result["crew"]["supervisor"]
        self.assertEqual(result["status"], "indicated")
        self.assertFalse(result["confirmed_maneuver"])
        self.assertTrue(operator["indicated"])
        self.assertFalse(supervisor["indicated"])
        self.assertEqual(operator["cue"], "radial_rate")
        self.assertEqual(operator["confidence"], "low")
        self.assertEqual(operator["supporting_reports"], ["R1", "R2"])
        self.assertEqual(supervisor["supporting_reports"], [])
        line = result["comparisons"][0]["matched_lines"][0]
        self.assertGreater(line["sigma_ratio"], 2)
        self.assertLess(line["sigma_ratio"], 3)
        self.assertEqual(line["cue"], "radial_rate")
        self.assertTrue(result["comparisons"][0]["same_evidence_window"])
        self.assertTrue(result["comparisons"][0]["own_ship_doppler_removed"])
        self.assertAlmostEqual(result["comparisons"][0]["own_ship_closing_change_knots"], 0.0)
        reading = operator["reading"]
        self.assertIn("Quality on that line changed from moderate to weak", reading)
        self.assertIn("2-sigma", " ".join(result["crew"]["competing_interpretations"]))
        self.assertIn("3-sigma", " ".join(result["crew"]["competing_interpretations"]))
        self.assertIn("aspect", " ".join(result["crew"]["competing_interpretations"]))
        _scan(self, result)

    def test_cross_window_shift_stays_inside_both_gates(self):
        early = frequency_change_assessment([
            _look(0, "R1", [_tone(60.0)], window="W000"),
            _look(20, "R2", [_tone(60.030)], window="W001"),
        ], 20)
        self.assertEqual(early["status"], "not_indicated")
        self.assertFalse(early["crew"]["operator"]["indicated"])
        self.assertFalse(early["crew"]["supervisor"]["indicated"])
        self.assertFalse(early["comparisons"][0]["same_evidence_window"])
        self.assertLess(early["comparisons"][0]["matched_lines"][0]["sigma_ratio"], 2)
        self.assertEqual(early["comparisons"][0]["matched_lines"][0]["cue"], "within_error")

    def test_a_later_look_in_the_new_window_can_cross_the_higher_gate(self):
        missed = [
            _look(0, "R1", [_tone(60.0)], window="W000"),
            _look(20, "R2", [_tone(60.030)], window="W001"),
        ]
        caught = missed + [_look(25, "R3", [_tone(60.075)], window="W001")]
        before = frequency_change_assessment(missed, 20)
        after = frequency_change_assessment(caught, 25)
        self.assertEqual(before["status"], "not_indicated")
        self.assertEqual(after["status"], "indicated")
        self.assertTrue(after["crew"]["operator"]["indicated"])
        self.assertTrue(after["crew"]["supervisor"]["indicated"])
        self.assertEqual(after["crew"]["supervisor"]["cue"], "radial_rate")
        self.assertEqual(after["crew"]["supervisor"]["confidence"], "low")
        self.assertEqual(after["crew"]["operator"]["supporting_reports"], ["R2", "R3"])
        self.assertEqual(after["crew"]["supervisor"]["supporting_reports"], ["R2", "R3"])
        same_window = [
            item for item in after["comparisons"] if item["same_evidence_window"]
        ]
        self.assertEqual(len(same_window), 1)
        self.assertGreater(same_window[0]["matched_lines"][0]["sigma_ratio"], 3)
        crossed = [
            item for item in after["comparisons"] if not item["same_evidence_window"]
        ]
        self.assertTrue(all(line["cue"] == "within_error" for item in crossed for line in item["matched_lines"]))

    def test_a_shift_inside_the_motion_floor_is_not_called(self):
        result = frequency_change_assessment([
            _look(0, "R1", [_tone(60.0)]),
            _look(5, "R2", [_tone(60.005)]),
        ], 5)
        self.assertEqual(result["status"], "not_indicated")
        self.assertEqual(result["crew"]["operator"]["confidence"], "not_indicated")
        self.assertEqual(result["comparisons"][0]["matched_lines"][0]["cue"], "within_error")
        self.assertIn("inside both gates", " ".join(result["crew"]["competing_interpretations"]))

    def test_quality_alone_does_not_call_a_change(self):
        result = frequency_change_assessment([
            _look(0, "R1", [_tone(60.0, "moderate")]),
            _look(5, "R2", [_tone(60.0, "strong")]),
        ], 5)
        self.assertEqual(result["status"], "not_indicated")
        self.assertEqual(result["comparisons"][0]["matched_lines"][0]["residual_hz"], 0.0)
        self.assertNotIn("Quality", result["crew"]["operator"]["reading"])

    def test_harmonic_jump_is_an_emitted_frequency_change(self):
        result = frequency_change_assessment([
            _look(0, "R1", [_tone(11.2), _tone(22.4)]),
            _look(5, "R2", [_tone(33.6), _tone(16.8)]),
        ], 5)
        pairs = [
            (line["from_hz"], line["to_hz"])
            for line in result["comparisons"][0]["matched_lines"]
        ]
        self.assertEqual(pairs, [(11.2, 16.8), (22.4, 33.6)])
        self.assertEqual(result["comparisons"][0]["unmatched_earlier"], 0)
        self.assertEqual(result["comparisons"][0]["unmatched_later"], 0)
        self.assertTrue(all(line["cue"] == "emitted_frequency" for line in result["comparisons"][0]["matched_lines"]))
        self.assertEqual(result["crew"]["operator"]["confidence"], "high")
        self.assertEqual(result["crew"]["supervisor"]["confidence"], "high")
        self.assertEqual(result["crew"]["operator"]["cue"], "emitted_frequency")
        self.assertEqual(result["crew"]["supervisor"]["cue"], "emitted_frequency")
        self.assertIn("emitted", " ".join(result["crew"]["competing_interpretations"]).lower())
        single = frequency_change_assessment([
            _look(0, "R1", [_tone(11.2)]),
            _look(5, "R2", [_tone(16.8)]),
        ], 5)
        self.assertEqual(single["crew"]["operator"]["cue"], "emitted_frequency")
        self.assertEqual(single["crew"]["operator"]["confidence"], "moderate")

    def test_own_ship_doppler_is_removed_before_the_call(self):
        closing = 10.0 * spectra.KNOTS_TO_METERS_PER_SECOND
        shifted = 60.0 * (1.0 + closing / SOUND_SPEED_MPS)
        east = 10.0 * 5.0 / 60.0
        held = [
            _look(0, "R1", [_tone(60.0)], east=0.0, course=90, speed=0),
            _look(5, "R2", [_tone(60.0)], east=0.0, course=90, speed=0),
            _look(10, "R3", [_tone(60.0)], east=east, course=90, speed=10),
        ]
        followed = [
            _look(0, "R1", [_tone(60.0)], east=0.0, course=90, speed=0),
            _look(5, "R2", [_tone(60.0)], east=0.0, course=90, speed=0),
            _look(10, "R3", [_tone(shifted)], east=east, course=90, speed=10),
        ]
        compensated = frequency_change_assessment(followed, 10)
        raw = frequency_change_assessment(held, 10)
        self.assertEqual(compensated["status"], "not_indicated")
        self.assertTrue(compensated["comparisons"][-1]["own_ship_doppler_removed"])
        self.assertAlmostEqual(compensated["comparisons"][-1]["own_ship_closing_change_knots"], 10.0, places=3)
        self.assertEqual(compensated["comparisons"][-1]["matched_lines"][0]["residual_hz"], 0.0)
        self.assertEqual(raw["status"], "indicated")
        self.assertTrue(raw["crew"]["supervisor"]["indicated"])
        self.assertEqual(raw["crew"]["supervisor"]["cue"], "radial_rate")
        self.assertLess(raw["comparisons"][-1]["matched_lines"][0]["residual_hz"], 0)
        self.assertGreater(abs(raw["comparisons"][-1]["matched_lines"][0]["own_ship_doppler_hz"]), 0.1)

    def test_the_first_pair_uses_recorded_endpoint_speed(self):
        closing = 10.0 * spectra.KNOTS_TO_METERS_PER_SECOND
        shifted = 60.0 * (1.0 + closing / SOUND_SPEED_MPS)
        corrected = frequency_change_assessment([
            _look(0, "R1", [_tone(60.0)], course=90, speed=0),
            _look(5, "R2", [_tone(shifted)], east=10.0 * 5.0 / 60.0, course=90, speed=10),
        ], 5)
        self.assertEqual(corrected["status"], "not_indicated")
        self.assertTrue(corrected["comparisons"][0]["own_ship_doppler_removed"])
        self.assertAlmostEqual(corrected["comparisons"][0]["own_ship_closing_change_knots"], 10.0, places=2)
        self.assertEqual(corrected["comparisons"][0]["matched_lines"][0]["residual_hz"], 0.0)
        missing = []
        for look in (
            _look(0, "R1", [_tone(60.0)]),
            _look(5, "R2", [_tone(shifted)], east=10.0 * 5.0 / 60.0),
        ):
            look["own_course_true"] = None
            look["own_speed_knots"] = None
            missing.append(look)
        uncorrected = frequency_change_assessment(missing, 5)
        self.assertFalse(uncorrected["comparisons"][0]["own_ship_doppler_removed"])
        self.assertEqual(uncorrected["status"], "indicated")

    def test_same_window_reused_processing_error_cancels(self):
        same = frequency_change_assessment([
            _look(0, "R1", [_tone(60.0, uncertainty=0.2)]),
            _look(5, "R2", [_tone(60.04, uncertainty=0.2)]),
        ], 5)
        crossed = frequency_change_assessment([
            _look(0, "R1", [_tone(60.0, uncertainty=0.2)], window="W000"),
            _look(20, "R2", [_tone(60.04, uncertainty=0.2)], window="W001"),
        ], 20)
        self.assertTrue(same["crew"]["supervisor"]["indicated"])
        self.assertFalse(crossed["crew"]["operator"]["indicated"])
        self.assertLess(
            same["comparisons"][0]["matched_lines"][0]["sigma_hz"],
            crossed["comparisons"][0]["matched_lines"][0]["sigma_hz"],
        )

    def test_hidden_fields_and_looks_without_lines_do_not_change_the_call(self):
        clean = [
            _look(0, "R1", [_tone(60.0)]),
            {"elapsed_minutes": 3, "time": "t3", "bearing_true": 90, "report_id": "Rgap"},
            _look(5, "R2", [_tone(60.030)]),
        ]
        poisoned = []
        for observation in clean:
            copy = dict(observation)
            copy.update({
                "course": 270,
                "speed": 18,
                "aspect": "bow",
                "actor": "actor-a",
                "kind": "submerged",
                "emitted_hz": 12.0,
                "true": {"course": 270},
            })
            spectrum = copy.get("spectrum")
            if spectrum:
                copy["spectrum"] = {
                    "lines": [{**line, "emitted_hz": 59.0, "doppler_hz": 1.0, "aspect": "bow"} for line in spectrum["lines"]],
                    "source_level_db": 140,
                }
            poisoned.append(copy)
        self.assertEqual(
            frequency_change_assessment(clean, 5),
            frequency_change_assessment(poisoned, 5),
        )
        published = json.dumps(frequency_change_assessment(poisoned, 5))
        self.assertNotIn("emitted_hz", published)
        self.assertNotIn("actor-a", published)
        _scan(self, frequency_change_assessment(poisoned, 5))

    def test_stale_reports_remain_in_the_history(self):
        result = frequency_change_assessment([
            _look(0, "R1", [_tone(11.2)]),
            _look(5, "R2", [_tone(16.8)]),
        ], 40)
        self.assertEqual(result["status"], "indicated")
        self.assertEqual(result["measurements"]["report_ids"], ["R1", "R2"])
        self.assertEqual(result["measurements"]["stale_report_ids"], ["R1", "R2"])
        self.assertEqual(result["crew"]["stale_report_ids"], ["R1", "R2"])
        self.assertIn("stale", " ".join(result["crew"]["competing_interpretations"]))

    def test_one_spectrum_has_no_comparison(self):
        result = frequency_change_assessment([
            _look(0, "R1", [_tone(60.0)]),
            {"elapsed_minutes": 5, "spectrum": {"lines": [{"measured_hz": "sixty"}]}},
        ], 5)
        self.assertEqual(result["status"], "not_indicated")
        self.assertEqual(result["measurements"]["spectrum_count"], 1)
        self.assertEqual(result["comparisons"], [])
        self.assertFalse(result["confirmed_maneuver"])


if __name__ == "__main__":
    unittest.main()
