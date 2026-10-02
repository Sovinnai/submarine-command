"""Narrowband spectra: persistence, operating rules, measurement and replay."""
import json
import math
import unittest

from submarine_command import acoustics, engine, spectra
from submarine_command.platforms import (
    BIOLOGIC,
    DART,
    FISHER,
    KESTREL,
    MERCHANT,
    WARSHIP,
)


class FixedDice:
    """A deterministic stand-in. 0.5 is the midpoint of every between() draw."""

    def __init__(self, value=0.5):
        self.value = value

    def u(self, label):
        return self.value

    def between(self, label, low, high):
        return low + (high - low) * self.u(label)


def place(spec, **overrides):
    fields = {
        "id": spec.identifier,
        "x": 0.0,
        "y": 1.0,
        "course": 90.0,
        "speed": 8.0,
        "depth": 0.0,
        "operating_mode": spec.default_mode,
    }
    fields.update(overrides)
    return engine.make_entity(spec, **fields)


class EmissionRuleTests(unittest.TestCase):
    def test_nominal_frequencies_overlap_across_contact_domains(self):
        cross_domain = [
            (FISHER.identifier, "engine_order", BIOLOGIC.identifier, "moan", 8.0),
            (DART.identifier, "electric_motor", WARSHIP.identifier, "auxiliary", 6.0),
            (FISHER.identifier, "gear", BIOLOGIC.identifier, "click", 3.0),
        ]
        domains = {
            FISHER.identifier: FISHER.contact_domain.value,
            BIOLOGIC.identifier: BIOLOGIC.contact_domain.value,
            DART.identifier: DART.contact_domain.value,
            WARSHIP.identifier: WARSHIP.contact_domain.value,
        }
        for left_id, left_family, right_id, right_family, speed in cross_domain:
            left = spectra.nominal_line_hz(left_id, left_family, 1, speed)
            right = spectra.nominal_line_hz(right_id, right_family, 1, speed)
            self.assertEqual(left, right)
            self.assertNotEqual(domains[left_id], domains[right_id])
        # The same nominal line also sits in two different submerged classes.
        self.assertEqual(
            spectra.nominal_line_hz(KESTREL.identifier, "ship_service", 1, 5),
            spectra.nominal_line_hz(DART.identifier, "electric_motor", 1, 5),
        )
        self.assertNotEqual(KESTREL.category, DART.category)

    def test_blade_rate_is_proportional_to_speed_and_otherwise_fixed(self):
        slow = place(MERCHANT, speed=8, operating_mode="service")
        fast = place(MERCHANT, speed=12, operating_mode="service")
        again = place(MERCHANT, speed=12, operating_mode="service")
        slow_lines = spectra.emitted_components(slow)
        fast_lines = spectra.emitted_components(fast)
        self.assertEqual(fast_lines, spectra.emitted_components(again))

        def blade(components):
            return next(line for line in components["lines"] if line["line_id"] == "blade_rate:1")

        self.assertAlmostEqual(blade(fast_lines)["emitted_hz"] / blade(slow_lines)["emitted_hz"], 1.5)
        engine_line = next(line for line in fast_lines["lines"] if line["line_id"] == "engine_order:1")
        engine_slow = next(line for line in slow_lines["lines"] if line["line_id"] == "engine_order:1")
        self.assertAlmostEqual(engine_line["emitted_hz"], engine_slow["emitted_hz"])
        self.assertAlmostEqual(engine_line["emitted_hz"], 47.5)

    def test_operating_mode_gates_families_and_scales_level(self):
        battery = place(DART, id="battery", speed=5, depth=300, operating_mode="battery")
        snorkel = place(DART, id="snorkel", speed=5, depth=50, operating_mode="snorkeling")
        battery_lines = spectra.emitted_components(battery)
        snorkel_lines = spectra.emitted_components(snorkel)
        battery_families = {line["family"] for line in battery_lines["lines"]}
        snorkel_families = {line["family"] for line in snorkel_lines["lines"]}
        self.assertIn("electric_motor", battery_families)
        self.assertNotIn("snorkel_diesel", battery_families)
        self.assertIn("snorkel_diesel", snorkel_families)
        self.assertNotIn("electric_motor", snorkel_families)
        self.assertEqual(battery_lines["offset_fraction"], snorkel_lines["offset_fraction"])

        def blade_level(components):
            return next(
                line["source_level_db"] for line in components["lines"]
                if line["line_id"] == "blade_rate:1"
            )

        expected = 10.0 * math.log10(2.2 / 0.58)
        self.assertAlmostEqual(
            blade_level(snorkel_lines) - blade_level(battery_lines), expected, places=6
        )

    def test_surface_and_own_ship_modes_drop_named_families(self):
        quiet = place(WARSHIP, speed=8, operating_mode="quiet_patrol")
        cruise = place(WARSHIP, speed=8, operating_mode="cruise")
        quiet_families = {line["family"] for line in spectra.emitted_components(quiet)["lines"]}
        cruise_families = {line["family"] for line in spectra.emitted_components(cruise)["lines"]}
        self.assertIn("auxiliary", quiet_families)
        self.assertNotIn("main_engine", quiet_families)
        self.assertIn("main_engine", cruise_families)
        self.assertNotIn("auxiliary", cruise_families)

        planted = place(KESTREL, id="kestrel", x=0, y=0, speed=5, depth=400, operating_mode="standard")
        silenced = place(KESTREL, id="kestrel", x=0, y=0, speed=5, depth=400, operating_mode="quiet")
        self.assertIn("pump", {line["family"] for line in spectra.emitted_components(planted)["lines"]})
        self.assertNotIn("pump", {line["family"] for line in spectra.emitted_components(silenced)["lines"]})

    def test_individual_offset_persists_and_is_not_rerolled_by_mode(self):
        game = engine.initialize("12" * 32)
        actor = game["state"]["actors"][0]
        first = actor["signature"]["offset_fraction"]
        emitted = spectra.emitted_components(actor)["lines"][0]["emitted_hz"]
        actor["operating_mode"] = actor["operating_mode"]
        self.assertEqual(first, spectra.emitted_components(actor)["offset_fraction"])
        self.assertEqual(emitted, spectra.emitted_components(actor)["lines"][0]["emitted_hz"])
        other = engine.initialize("34" * 32)["state"]["actors"][0]["signature"]["offset_fraction"]
        self.assertNotEqual(first, other)


class MeasurementTests(unittest.TestCase):
    def setUp(self):
        self.game = engine.initialize("ab" * 32)
        self.state = self.game["state"]
        self.own = self.state["own"]
        self.own.update(x=0.0, y=0.0, course=0.0, speed=0.0, depth=400.0)

    def _measure(self, source, dice=None, mode="passive", window_db=0.0, focused=False, extra_di=0.0):
        return spectra.measure_contact(
            self.state, source, self.own, mode, dice or FixedDice(),
            extra_di=extra_di, focused=focused, window_db=window_db,
        )

    def test_closing_raises_frequency_and_opening_lowers_it(self):
        source = place(MERCHANT, id="merchant", x=0.0, y=2.0, course=180.0, speed=12.0, depth=0.0)
        closing = self._measure(source)
        line = next(item for item in closing["detail"]["lines"] if item["line_id"] == "engine_order:1")
        self.assertGreater(line["doppler_hz"], line["emitted_hz"])
        self.assertAlmostEqual(line["measured_hz"], line["doppler_hz"])
        source["course"] = 0.0
        opening = self._measure(source)
        opened = next(item for item in opening["detail"]["lines"] if item["line_id"] == "engine_order:1")
        self.assertLess(opened["doppler_hz"], opened["emitted_hz"])

    def test_public_measurement_is_not_the_emitted_truth(self):
        source = place(
            MERCHANT, id="merchant", x=0.0, y=0.6, course=180.0, speed=12.0, depth=0.0
        )
        measured = self._measure(source)
        public_ids = {"measured_hz", "uncertainty_hz", "quality"}
        self.assertTrue(measured["public"]["lines"])
        for line in measured["public"]["lines"]:
            self.assertEqual(set(line), public_ids)
            self.assertGreater(line["uncertainty_hz"], 0)
        self.assertNotIn("emitted_hz", measured["public"]["evidence_note"])
        detail = next(item for item in measured["detail"]["lines"] if item["detected"])
        self.assertNotEqual(
            round(detail["measured_hz"], 3), round(detail["emitted_hz"], 3)
        )

    def test_closer_geometry_improves_excess_and_tightens_uncertainty(self):
        far = place(MERCHANT, id="far", x=0.0, y=4.0, course=90.0, speed=12.0, depth=0.0)
        near = place(MERCHANT, id="near", x=0.0, y=0.6, course=90.0, speed=12.0, depth=0.0)
        far_line = next(
            item for item in self._measure(far)["detail"]["lines"]
            if item["line_id"] == "engine_order:1"
        )
        near_line = next(
            item for item in self._measure(near)["detail"]["lines"]
            if item["line_id"] == "engine_order:1"
        )
        self.assertGreater(near_line["excess_db"], far_line["excess_db"])
        self.assertLess(near_line["uncertainty_hz"], far_line["uncertainty_hz"])

    def test_window_offset_moves_every_line_and_a_shared_bias_is_common(self):
        left = place(MERCHANT, id="left", x=-1.5, y=1.0, course=90.0, speed=10.0, depth=0.0)
        right = place(FISHER, id="right", x=1.5, y=1.0, course=90.0, speed=8.0, depth=0.0)
        dice = engine.Dice("56" * 32, [])
        quiet = self._measure(left, dice=engine.Dice("56" * 32, []), window_db=0.0)
        loud = self._measure(left, dice=engine.Dice("56" * 32, []), window_db=4.0)
        quiet_line = next(item for item in quiet["detail"]["lines"] if item["line_id"] == "engine_order:1")
        loud_line = next(item for item in loud["detail"]["lines"] if item["line_id"] == "engine_order:1")
        self.assertAlmostEqual(loud_line["excess_db"] - quiet_line["excess_db"], 4.0)

        both = (
            self._measure(left, dice=dice),
            self._measure(right, dice=dice),
        )
        biases = {line["shared_bias_fraction"] for result in both for line in result["detail"]["lines"]}
        self.assertEqual(
            biases,
            {spectra.window_fractional_bias(
                engine.Dice("56" * 32, []), self.state["t"], "hull_array"
            )},
        )

    def test_a_look_cannot_report_a_line_the_emitter_does_not_have(self):
        source = place(DART, id="dart", x=0.0, y=0.8, speed=5, depth=300, operating_mode="battery")
        measured = self._measure(
            source, dice=FixedDice(0.0), mode="focus", focused=True, extra_di=2.0
        )
        emitted = set(measured["detail"]["emitted_line_ids"])
        self.assertNotIn("snorkel_diesel:1", emitted)
        reported = {line["line_id"] for line in measured["detail"]["lines"] if line["detected"]}
        self.assertTrue(reported)
        self.assertTrue(reported <= emitted)
        self.assertLessEqual(len(measured["public"]["lines"]), len(emitted))

    def test_processing_error_has_the_reported_one_sigma_width(self):
        source = place(MERCHANT, id="merchant", x=0.0, y=2.0, course=90.0, speed=12.0, depth=0.0)
        measured = self._measure(source, dice=FixedDice(0.0))
        line = next(item for item in measured["detail"]["lines"] if item["line_id"] == "engine_order:1")
        tone_sigma = spectra.cramer_rao_hz(
            line["excess_db"] + spectra.NARROWBAND_DT_DB, spectra.INTEGRATION_SECONDS["listen"]
        )
        self.assertAlmostEqual(line["processing_error_hz"], -math.sqrt(3.0) * tone_sigma)
        self.assertAlmostEqual(
            spectra.line_error_sample(FixedDice(0.0), "merchant", "engine_order:1", self.state["t"]),
            -math.sqrt(3.0),
        )

    def test_feature_likelihoods_overlap_and_one_look_is_not_an_identification(self):
        for weights in spectra.FEATURE_LIKELIHOOD.values():
            self.assertGreaterEqual(min(weights), 0.15)
            self.assertLessEqual(max(weights), 0.70)
            self.assertAlmostEqual(sum(weights), 1.0)
        for feature in spectra.FEATURE_LIKELIHOOD:
            assessed = engine.assessments({"evidence": {"0": feature}, "visual": None})
            self.assertNotEqual(assessed["supervisor"]["confidence"], "high")
            self.assertIn(assessed["supervisor"]["favors"], ("surface", "submerged", "biologic"))


class HarmonicRelationTests(unittest.TestCase):
    def line(self, frequency, uncertainty=0.3):
        return {"measured_hz": frequency, "uncertainty_hz": uncertainty, "quality": "moderate"}

    def test_a_missing_fundamental_is_inferred_from_higher_harmonics(self):
        relations = spectra.harmonic_relations([self.line(100.0), self.line(150.0)])
        self.assertEqual(len(relations), 1)
        self.assertEqual(relations[0]["multiples"], [2, 3])
        self.assertAlmostEqual(relations[0]["fundamental_hz"], 50.0, places=3)
        feature = spectra.spectral_feature({
            "lines": [self.line(100.0), self.line(150.0)],
            "broadband": [],
            "harmonic_relations": relations,
        })
        self.assertEqual(feature, "harmonic_set")

    def test_a_detected_fundamental_is_preferred_to_a_lower_invented_one(self):
        relations = spectra.harmonic_relations([self.line(50.0), self.line(100.0), self.line(150.0)])
        self.assertEqual(relations[0]["multiples"], [1, 2, 3])
        self.assertAlmostEqual(relations[0]["fundamental_hz"], 50.0, places=3)
        octave = spectra.harmonic_relations([self.line(100.0), self.line(200.0)])
        self.assertEqual(octave[0]["multiples"], [1, 2])
        self.assertAlmostEqual(octave[0]["fundamental_hz"], 100.0, places=3)

    def test_unrelated_lines_are_not_called_harmonics(self):
        self.assertEqual(
            spectra.harmonic_relations([self.line(50.0, 0.2), self.line(73.0, 0.2)]),
            [],
        )


class ReportBoundaryTests(unittest.TestCase):
    def test_rereading_a_spectrum_does_not_add_lines_or_evidence(self):
        game = engine.initialize("ab" * 32)
        actor = game["state"]["actors"][0]
        actor.update(x=0.5, y=0.0)
        state = game["state"]
        state["t"] = 5
        dice = engine.Dice(game["seed"], state["rng_trace"])
        engine.observe_contact(state, actor, dice)
        track = next(item for item in state["tracks"] if item["actor"] == actor["id"])
        before_lines = [dict(line) for line in track["observations"][-1]["spectrum"]["lines"]]
        before_evidence = dict(track["evidence"])
        before_trace = len(state["rng_trace"])
        before_reports = len(state["reports"])
        engine.observe_contact(state, actor, dice)
        self.assertEqual(track["observations"][-1]["spectrum"]["lines"], before_lines)
        self.assertEqual(track["evidence"], before_evidence)
        self.assertEqual(len(state["rng_trace"]), before_trace)
        self.assertEqual(len(state["reports"]), before_reports)

    def test_public_status_hides_emitted_truth_and_debrief_reveals_it(self):
        game = engine.initialize("ab" * 32)
        engine.apply_order(game, {
            "id": "listen", "expected_turn": 0, "activity": "listen", "minutes": 5,
            "interrupt_on": [], "course": 90, "speed": 5, "depth": 400,
            "operating_mode": "standard",
        })
        view = engine.public_view(game)
        rendered = engine.canonical(view).decode()
        for secret in (
            "emitted_hz", "offset_fraction", "snorkel_diesel", "blade_rate", "source_level_db",
        ):
            self.assertNotIn(secret, rendered)
        for contact in view["contacts"]:
            for observation in contact["observations"]:
                if "spectrum" not in observation:
                    continue
                self.assertIn("evidence_note", observation["spectrum"])
                for line in observation["spectrum"]["lines"]:
                    self.assertGreater(line["uncertainty_hz"], 0)
                    self.assertIn(line["quality"], ("strong", "moderate", "weak"))
        before = engine.canonical(game)
        self.assertEqual(view, engine.public_view(game))
        self.assertEqual(before, engine.canonical(game))

        engine.apply_order(game, {"id": "finish", "expected_turn": 1, "activity": "end"})
        revealed = engine.debrief(game)
        self.assertTrue(any(item["lines"] for item in revealed["emitter_spectra"]))
        self.assertIn("emitted_hz", revealed["emitter_spectra"][0]["lines"][0])

    def test_replay_reproduces_the_measured_spectrum(self):
        order = {
            "id": "listen", "expected_turn": 0, "activity": "listen", "minutes": 15,
            "interrupt_on": [], "course": 40, "speed": 8, "depth": 250,
            "operating_mode": "standard",
        }
        first = engine.initialize("cd" * 32)
        second = engine.initialize("cd" * 32)
        engine.apply_order(first, order)
        engine.apply_order(second, order)
        self.assertEqual(engine.canonical(first["state"]), engine.canonical(second["state"]))
        self.assertTrue(engine.verify(first)["verified"])
        spectra_first = [
            observation.get("spectrum")
            for contact in engine.public_view(first)["contacts"]
            for observation in contact["observations"]
        ]
        spectra_second = [
            observation.get("spectrum")
            for contact in engine.public_view(second)["contacts"]
            for observation in contact["observations"]
        ]
        self.assertEqual(spectra_first, spectra_second)
        self.assertTrue(spectra_first)


class AspectTests(unittest.TestCase):
    def setUp(self):
        self.game = engine.initialize("ab" * 32)
        self.state = self.game["state"]
        self.state["own"].update(x=0.0, y=0.0, course=0.0, speed=0.0, depth=400.0)

    def _measure(self, course, north=2.0):
        source = place(
            MERCHANT, id="merchant", x=0.0, y=north, course=course,
            speed=12.0, depth=0.0, operating_mode="service",
        )
        return spectra.measure_contact(
            self.state, source, self.state["own"], "passive", FixedDice(),
        )

    def _blade(self, measured):
        return next(
            line for line in measured["detail"]["lines"] if line["line_id"] == "blade_rate:1"
        )

    def _low_band(self, measured):
        return next(band for band in measured["detail"]["broadband"] if band["low_hz"] == 10.0)

    def test_every_class_uses_the_same_overlapping_curve(self):
        curves = {template.aspect for template in spectra.SIGNATURES.values()}
        self.assertEqual(curves, {acoustics.SHARED_ASPECT_CURVE})
        self.assertEqual(acoustics.aspect_adjustment_db(0), -5.0)
        self.assertEqual(acoustics.aspect_adjustment_db(90), 0.0)
        self.assertEqual(acoustics.aspect_adjustment_db(180), -2.5)
        self.assertEqual(acoustics.aspect_adjustment_db(None), 0.0)
        self.assertAlmostEqual(acoustics.aspect_adjustment_db(45), -2.5)

    def test_beam_is_louder_than_bow_at_equal_range_and_speed(self):
        bow = self._measure(180)
        beam = self._measure(90)
        stern = self._measure(0)
        other_beam = self._measure(270)
        self.assertAlmostEqual(bow["aspect_deg"], 0.0)
        self.assertAlmostEqual(beam["aspect_deg"], 90.0)
        self.assertAlmostEqual(stern["aspect_deg"], 180.0)
        self.assertAlmostEqual(beam["aspect_adjustment_db"], other_beam["aspect_adjustment_db"])
        self.assertAlmostEqual(
            self._blade(beam)["source_level_db"] - self._blade(bow)["source_level_db"], 5.0
        )
        self.assertAlmostEqual(
            self._blade(beam)["source_level_db"] - self._blade(stern)["source_level_db"], 2.5
        )
        self.assertAlmostEqual(self._blade(bow)["emitted_hz"], self._blade(beam)["emitted_hz"])
        self.assertAlmostEqual(
            self._low_band(beam)["excess_db"] - self._low_band(bow)["excess_db"], 5.0
        )
        self.assertAlmostEqual(
            self._low_band(beam)["source_level_db"] - self._low_band(bow)["source_level_db"], 5.0
        )

    def test_unchanged_aspect_leaves_source_level_unchanged(self):
        first = self._measure(90)
        second = self._measure(90)
        self.assertEqual(first["aspect_adjustment_db"], second["aspect_adjustment_db"])
        self.assertEqual(
            [line["source_level_db"] for line in first["detail"]["lines"]],
            [line["source_level_db"] for line in second["detail"]["lines"]],
        )
        self.assertEqual(
            [band["excess_db"] for band in first["detail"]["broadband"]],
            [band["excess_db"] for band in second["detail"]["broadband"]],
        )

    def test_constant_speed_turn_can_change_quality_without_a_blade_rate_shift(self):
        found = None
        for north in (1.0, 2.0, 3.0, 4.0, 6.0, 8.0, 10.0, 12.0, 16.0, 20.0):
            bow = self._measure(180, north)
            beam = self._measure(90, north)
            for left, right in zip(bow["detail"]["broadband"], beam["detail"]["broadband"]):
                if left["detected"] and right["detected"] and left["quality"] != right["quality"]:
                    found = (bow, beam, left, right)
                    break
            if found:
                break
        self.assertIsNotNone(found)
        bow, beam, quiet, loud = found
        self.assertAlmostEqual(self._blade(bow)["emitted_hz"], self._blade(beam)["emitted_hz"])
        self.assertNotEqual(quiet["quality"], loud["quality"])
        self.assertAlmostEqual(loud["excess_db"] - quiet["excess_db"], 5.0)

    def test_contact_report_states_the_level_change_and_not_the_angle(self):
        state = self.state
        state["actors"] = []
        state["tracks"] = []
        state["looks"] = []
        source = place(
            MERCHANT, id="merchant-turn", x=0.0, y=1.2, course=90.0,
            speed=12.0, depth=0.0, operating_mode="service",
        )
        spectra.assign_persistent_signature(
            source, engine.Dice(self.game["seed"], state["rng_trace"])
        )
        state["actors"].append(source)
        dice = engine.Dice(self.game["seed"], state["rng_trace"])
        before = len(state["reports"])
        engine.observe_contact(state, source, dice)
        source["course"] = 180.0
        state["t"] = 5
        engine.observe_contact(state, source, dice)
        sonar = state["reports"][before:]
        sonar = [entry for entry in sonar if entry["department"] == "Sonar"]
        self.assertEqual(len(sonar), 2)
        update = sonar[-1]
        self.assertIn("Received level changed", update["text"])
        self.assertIn("-5.0 dB", update["text"])
        level_note = update["text"].split("Received level changed", 1)[1].lower()
        self.assertNotIn("aspect", level_note)
        self.assertNotIn("bow", level_note)
        self.assertNotIn("beam", level_note)
        self.assertNotIn("stern", level_note)
        self.assertNotRegex(level_note, r"\d+(\.\d+)? deg")
        change = update["observation"]["level_change"]
        self.assertNotIn("aspect_deg", change)
        self.assertNotIn("source_level_db", change)
        self.assertTrue(any(item["change_db"] == -5.0 for item in change["broadband"]))
        rendered = engine.canonical(engine.public_view(self.game)).decode()
        self.assertNotIn("aspect_deg", rendered)
        self.assertNotIn("level_memory", rendered)
        self.assertNotIn("source_level_db", rendered)
        self.assertIn("level_memory", state["tracks"][0])
        self.assertIsInstance(state["tracks"][0]["level_memory"]["lines"], list)
        self.assertNotIn("line_id", json.dumps(state["tracks"][0]["level_memory"]))

    def test_focus_does_not_change_received_level(self):
        source = place(
            MERCHANT, id="merchant", x=0.0, y=2.0, course=90.0,
            speed=12.0, depth=0.0, operating_mode="service",
        )
        listen = spectra.measure_contact(
            self.state, source, self.state["own"], "passive", FixedDice(),
        )
        focused = spectra.measure_contact(
            self.state, source, self.state["own"], "focus", FixedDice(), focused=True,
        )
        elsewhere = spectra.measure_contact(
            self.state, source, self.state["own"], "focus", FixedDice(), focused=False,
        )

        def excess(measured, line_id):
            return next(
                line["excess_db"] for line in measured["detail"]["lines"]
                if line["line_id"] == line_id
            )

        self.assertAlmostEqual(
            excess(listen, "engine_order:1"), excess(focused, "engine_order:1")
        )
        self.assertAlmostEqual(
            excess(listen, "engine_order:1"), excess(elsewhere, "engine_order:1")
        )
        self.assertEqual(
            spectra.integration_seconds("passive", False),
            spectra.INTEGRATION_SECONDS["listen"],
        )
        self.assertGreater(
            spectra.integration_seconds("focus", True),
            spectra.integration_seconds("passive", False),
        )
        self.assertLess(
            spectra.integration_seconds("focus", False),
            spectra.integration_seconds("passive", False),
        )

    def test_level_change_follows_measured_frequency_not_hidden_identity(self):
        previous = {
            "report_id": "R1",
            "lines": [
                {"measured_hz": 50.0, "quality": "moderate", "excess_db": 12.0},
                {"measured_hz": 100.0, "quality": "strong", "excess_db": 20.0},
            ],
            "bands": {},
        }
        measurement = {
            "detail": {
                "lines": [
                    {
                        "line_id": "family-high:1",
                        "detected": True,
                        "measured_hz": 50.2,
                        "quality": "weak",
                        "excess_db": 4.0,
                    },
                    {
                        "line_id": "family-low:1",
                        "detected": True,
                        "measured_hz": 100.1,
                        "quality": "strong",
                        "excess_db": 19.5,
                    },
                ],
                "broadband": [],
            }
        }
        change = spectra.received_level_change(previous, measurement)
        by_frequency = {line["to_hz"]: line for line in change["lines"]}
        self.assertEqual(by_frequency[50.2]["from_hz"], 50.0)
        self.assertEqual(by_frequency[50.2]["quality_from"], "moderate")
        self.assertAlmostEqual(by_frequency[50.2]["change_db"], -8.0)
        self.assertEqual(by_frequency[100.1]["from_hz"], 100.0)
        self.assertAlmostEqual(by_frequency[100.1]["change_db"], -0.5)
        jumped = {
            "detail": {
                "lines": [{
                    "line_id": "blade_rate:1",
                    "detected": True,
                    "measured_hz": 70.0,
                    "quality": "strong",
                    "excess_db": 10.0,
                }],
                "broadband": [],
            }
        }
        lone = {
            "report_id": "R1",
            "lines": [{"measured_hz": 50.0, "quality": "strong", "excess_db": 15.0}],
            "bands": {},
        }
        self.assertIsNone(spectra.received_level_change(lone, jumped))


if __name__ == "__main__":
    unittest.main()
