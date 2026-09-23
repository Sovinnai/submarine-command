import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from submarine_command import acoustics, engine as e, spectra
from submarine_command.platforms import BIOLOGIC, DART, ENTITY_REGISTRY


class EngineTests(unittest.TestCase):
    def setUp(self):
        # These are throwaway test seeds; never use or inspect the live seed.
        self.game = e.initialize("ab" * 32)

    def order(self, **kwargs):
        """Every field is stated: the engine defaults none of them."""
        own = self.game["state"]["own"]
        return {"id": "test-01", "expected_turn": len(self.game["events"]),
                "activity": "listen", "minutes": 15, "interrupt_on": [],
                "course": own["course"], "speed": own["speed"], "depth": own["depth"],
                "operating_mode": own["operating_mode"], **kwargs}

    def test_seed_reproducibility_and_distinct_worlds(self):
        self.assertEqual(e.canonical(self.game), e.canonical(e.initialize("ab" * 32)))
        self.assertNotEqual(self.game["commitment"], e.initialize("cd" * 32)["commitment"])

    def test_reports_do_not_mutate_or_roll(self):
        before = e.canonical(self.game)
        first = e.public_view(self.game)
        for _ in range(10):
            self.assertEqual(first, e.public_view(self.game))
        e.verify(self.game)
        self.assertEqual(before, e.canonical(self.game))

    def test_public_fields_exclude_private_world(self):
        for i in range(8):
            view = e.apply_order(self.game, self.order(id=f"p{i}", minutes=15))
            def scan(obj):
                if isinstance(obj, dict):
                    self.assertFalse(set(obj) & {
                        "seed", "actors", "actor", "kind", "spec", "rng_trace",
                        "initial_state", "intent", "last_heard", "aware",
                        "bearing_bias", "bulletins", "radio_reliability", "radio_intercepts", "true",
                        "signature", "listens", "emitted_hz", "offset_fraction",
                        "family", "source_level_db", "detail",
                    })
                    for value in obj.values():
                        scan(value)
                elif isinstance(obj, list):
                    for value in obj:
                        scan(value)
            scan(view)
            self.assertNotIn(self.game["seed"], json.dumps(view))

    def test_duplicate_order_is_cached_without_mutation(self):
        raw = self.order()
        first = e.apply_order(self.game, raw)
        e.apply_order(self.game, self.order(id="later", minutes=5))
        before = e.canonical(self.game)
        self.assertEqual(first, e.apply_order(self.game, raw))
        self.assertEqual(before, e.canonical(self.game))
        with self.assertRaises(ValueError):
            e.apply_order(self.game, self.order(minutes=30))
        self.assertEqual(before, e.canonical(self.game))

    def test_invalid_orders_do_not_change_world(self):
        bad = [self.order(minutes=-5), self.order(depth=5000), self.order(minutes=True),
               self.order(activity="receive"), self.order(activity="attack"),
               self.order(activity="listen", assessment="unresolved"),
               self.order(speed=float("nan")), self.order(focus="S01"),
               self.order(unrecognized="reroll"), self.order(expected_turn=20),
               self.order(activity="transmit", depth=70, assessment="unresolved", message="Test", basis=["R9999"])]
        before = e.canonical(self.game)
        for raw in bad:
            with self.assertRaises(ValueError):
                e.apply_order(self.game, raw)
            self.assertEqual(before, e.canonical(self.game))

    def test_event_draws_are_keyed_not_query_sequence(self):
        dice = e.Dice("12" * 32, [])
        first = dice.u("sensor:time-20")
        for i in range(50):
            dice.u(f"unrelated:{i}")
        self.assertEqual(first, dice.u("sensor:time-20"))
        self.assertTrue(0 <= first < 1)

    def test_repeated_evidence_window_does_not_multiply_confidence(self):
        state = self.game["state"]
        state["t"] = 5
        dice = e.Dice(self.game["seed"], state["rng_trace"])
        primary = state["actors"][0]
        primary.update(x=0.4, y=0.0)
        with mock.patch.object(acoustics, "detection_probability", return_value=0.99):
            for minute in (5, 10, 15):
                state["t"] = minute
                before = len(state["tracks"][0]["observations"])
                e.observe_contact(state, primary, dice)
                self.assertEqual(len(state["tracks"][0]["observations"]), before + 1)
                e.observe_contact(state, primary, dice)
                self.assertEqual(len(state["tracks"][0]["observations"]), before + 1)
        track = state["tracks"][0]
        self.assertLessEqual(len(track["evidence"]), 1)
        stored = track["observations"][-1]["spectrum"]
        self.assertEqual(stored, track["observations"][-1]["spectrum"])
        self.assertTrue(all(0 <= ob["bearing_true"] < 360 for ob in track["observations"]))
        self.assertTrue(all("emitted_hz" not in line for ob in track["observations"] for line in ob["spectrum"]["lines"]))

    def test_active_reception_strength_includes_the_echo(self):
        state = self.game["state"]
        actor = state["actors"][0]
        state["t"] = 5
        dice = e.Dice(self.game["seed"], state["rng_trace"])
        weak_spectrum = {
            "public": {
                "lines": [],
                "broadband": [],
                "harmonic_relations": [],
                "evidence_note": "Measured frequencies are evidence. They do not identify the source.",
            },
            "feature": None,
            "any_detected": True,
            "best_excess_db": -2.0,
            "strength": "weak",
            "description": "No discrete frequency line resolved. Frequencies are measurements, not an identification.",
            "detail": {"lines": [], "broadband": [], "emitted_line_ids": []},
        }
        with (
            mock.patch.object(spectra, "measure_contact", return_value=weak_spectrum),
            mock.patch.object(e, "reception_signal_excess", return_value=(18.0, None)),
            mock.patch.object(e, "acoustic_window_db", return_value=0.0),
            mock.patch.object(acoustics, "detection_probability", return_value=1.0),
        ):
            e.observe_contact(state, actor, dice, mode="active")
        observation = state["tracks"][0]["observations"][-1]
        self.assertEqual(observation["source"], "active")
        self.assertEqual(observation["strength"], "strong")
        self.assertIsNotNone(observation["range_estimate_nm"])
        self.assertNotIn("best_excess_db", observation)

    def test_full_replay_with_radio_maneuvers_and_early_interrupts(self):
        actions = [self.order(id="a", activity="focus", focus="S01", minutes=30, interrupt_on=["new_contact"]),
                   self.order(id="b", course=45, speed=7, minutes=25),
                   self.order(id="c", depth=70, activity="receive", link="mast_receive", minutes=30),
                   self.order(id="d", activity="mast", depth=70, speed=5, minutes=10),
                   self.order(id="e", depth=400, activity="active", minutes=5),
                   self.order(id="f", depth=70, activity="transmit", link="mast_transmit", assessment="unresolved", message="Evidence remains mixed", basis=["R0004"], minutes=40)]
        for order in actions:
            order["expected_turn"] = len(self.game["events"])
            e.apply_order(self.game, order)
            self.assertTrue(e.verify(self.game)["verified"])
        while not self.game["state"]["ended"]:
            e.apply_order(self.game, self.order(id=f"wait-{len(self.game['events'])}", minutes=60))
        self.assertEqual(self.game["state"]["t"], e.END)
        self.assertTrue(e.debrief(self.game)["verification"]["verified"])

    def test_execution_receipt_reports_actual_unused_time_and_all_interrupts(self):
        state = copy.deepcopy(self.game["state"])
        order = e.validate_order(
            self.order(minutes=30, interrupt_on=["equipment", "deadline"]), state
        )

        def interrupting_tick(current, _dice, _order, first_tick):
            self.assertTrue(first_tick)
            current["t"] += e.TICK
            e.report(current, "Engineering", "Test fault.", "equipment")
            e.report(current, "Navigation", "Test deadline.", "deadline")

        with mock.patch.object(e, "tick_world", side_effect=interrupting_tick):
            execution = e.simulate(state, self.game["seed"], order)
        self.assertEqual(execution["requested_minutes"], 30)
        self.assertEqual(execution["elapsed_minutes"], 5)
        self.assertEqual(execution["unused_minutes"], 25)
        self.assertEqual(execution["integration_steps"], 1)
        self.assertEqual(execution["stop_reason"], "interrupt")
        self.assertEqual(
            [event["category"] for event in execution["stop_events"]],
            ["deadline", "equipment"],
        )
        self.assertTrue(all(event["report_ids"] for event in execution["stop_events"]))

    def test_usable_empty_receive_completes_one_five_minute_link_cycle(self):
        self.game["state"]["radio_reliability"] = 1
        # Already inside the mast envelope: the cycle costs its five minutes
        # and no more. Reaching the envelope is covered separately below.
        self.game["state"]["own"]["depth"] = 70
        self.game["state"]["ordered"]["depth"] = 70
        result = e.apply_order(
            self.game,
            self.order(
                id="receive-empty",
                activity="receive",
                link="mast_receive",
                depth=70,
                speed=5,
                minutes=60,
                interrupt_on=[],
            ),
        )
        execution = result["last_execution"]
        self.assertEqual(execution["stop_reason"], "task_complete")
        self.assertEqual(execution["elapsed_minutes"], 5)
        self.assertEqual(execution["unused_minutes"], 55)
        self.assertEqual(execution["stop_events"][0]["category"], "radio_empty")

    def test_platforms_move_after_opponent_decisions_on_shared_step(self):
        state = copy.deepcopy(self.game["state"])
        initial_own_x = state["own"]["x"]
        initial_actor_positions = [(actor["x"], actor["y"]) for actor in state["actors"]]
        seen_own_positions = []
        order = e.validate_order(self.order(minutes=5), state)

        def capture_start_geometry(current, _actor, _dice, _active):
            seen_own_positions.append(current["own"]["x"])

        with (
            mock.patch.object(e, "opponent_step", side_effect=capture_start_geometry),
            mock.patch.object(e, "observe_contact"),
        ):
            e.tick_world(state, e.Dice(self.game["seed"], state["rng_trace"]), order, True)
        self.assertEqual(seen_own_positions, [initial_own_x] * len(state["actors"]))
        self.assertNotEqual(state["own"]["x"], initial_own_x)
        self.assertTrue(
            all(
                (actor["x"], actor["y"]) != initial
                for actor, initial in zip(state["actors"], initial_actor_positions)
            )
        )

    def test_public_command_contract_documents_resolution_and_concurrency(self):
        contract = e.public_view(self.game)["command_contract"]
        self.assertEqual(contract["integration"]["step_minutes"], e.TICK)
        self.assertIn("same elapsed step", contract["concurrency"]["maneuver"])
        self.assertEqual(contract["maneuver"]["resolution_step_minutes"], e.MANEUVER_STEP)
        self.assertIn("not achieved instantly", contract["maneuver"]["ordered_versus_achieved"])
        self.assertFalse(contract["maneuver"]["opposing_platforms"].startswith("Opposing entities are rate"))
        for field in ("course", "speed", "depth", "operating_mode", "interrupt_on", "expected_turn"):
            self.assertIn(field, contract["order_fields"]["required"])
        self.assertIn("five-minute", contract["concurrency"]["communications"])
        self.assertIn("20 productive minutes", contract["concurrency"]["repair"])


    # --- ordered/achieved maneuver transients ---

    def test_ordered_settings_are_not_achieved_instantly(self):
        result = e.apply_order(self.game, self.order(
            id="dive", course=270, speed=18, depth=800, minutes=5,
            operating_mode="high_power"))
        maneuver = result["last_execution"]["maneuver"]
        for field in ("speed", "depth"):
            self.assertTrue(maneuver[field]["in_progress"])
            self.assertNotEqual(maneuver[field]["achieved"], maneuver[field]["ordered"])
        self.assertEqual(maneuver["speed"]["achieved"], 10)  # 1 knot per minute
        self.assertLess(maneuver["depth"]["achieved"], 800)

    def test_depth_rate_scales_with_speed(self):
        slow = e.initialize("ab" * 32)
        fast = e.initialize("ab" * 32)
        common = dict(activity="listen", minutes=5, interrupt_on=[], course=90, depth=900)
        for game, speed, mode in ((slow, 2, "quiet"), (fast, 14, "standard")):
            game["state"]["own"]["speed"] = float(speed)
            e.apply_order(game, {"id": "d", "expected_turn": 0,
                                 "speed": speed, "operating_mode": mode, **common})
        self.assertLess(slow["state"]["own"]["depth"], fast["state"]["own"]["depth"])
        # Five feet per minute per knot, held at a constant speed.
        self.assertAlmostEqual(fast["state"]["own"]["depth"], 400 + 5 * 14 * 5)

    def test_a_turn_does_not_make_its_distance_good_on_the_new_course(self):
        before = self.game["state"]["own"]["x"]
        e.apply_order(self.game, self.order(id="reverse", course=270, minutes=5))
        own = self.game["state"]["own"]
        leg = 5 * e.TICK / 60  # one full step at five knots
        self.assertEqual(round(own["course"]), 270)
        # A 180 at 60 degrees per minute spends three of the five minutes
        # turning, so well under half the leg is made good to the west and the
        # boat is carried north out of its original track.
        self.assertGreater(own["x"], before - leg / 2)
        self.assertGreater(own["y"], 0)

    def test_achieved_values_never_overshoot_the_ordered_settings(self):
        for depth, speed, course in ((60, 18, 359.5), (900, 2, 0.5), (400, 9, 180)):
            game = e.initialize("77" * 32)
            for turn in range(2):
                e.apply_order(game, {"id": f"m{turn}", "expected_turn": turn,
                                     "activity": "listen", "minutes": 60, "interrupt_on": [],
                                     "course": course, "speed": speed, "depth": depth,
                                     "operating_mode": "high_power"})
            own, spec = game["state"]["own"], e.entity_spec(game["state"]["own"])
            self.assertAlmostEqual(own["depth"], depth)
            self.assertAlmostEqual(own["speed"], speed)
            self.assertEqual(own["course"], course)
            self.assertTrue(spec.envelope.allows(own["speed"], own["depth"]))

    def test_completing_a_turn_lands_exactly_on_the_ordered_course(self):
        """Float dust a hair off the ordered course reads as a maneuver forever.

        The achieved course would display correctly while `in_progress` stayed
        true, so `maneuver_complete` never fired and a window selecting it as an
        interrupt ran to its full requested length.
        """
        for course in (0.1, 359.9, 105.555, 45.0):
            game = e.initialize("ab" * 32)
            result = e.apply_order(game, {
                "id": "turn", "expected_turn": 0, "activity": "listen",
                "minutes": 60, "interrupt_on": ["maneuver_complete"],
                "course": course, "speed": 5, "depth": 400,
                "operating_mode": "standard"})
            execution = result["last_execution"]
            self.assertEqual(game["state"]["own"]["course"], course)
            self.assertFalse(execution["maneuver"]["course"]["in_progress"])
            self.assertFalse(game["state"]["maneuvering"])
            self.assertEqual(execution["stop_reason"], "interrupt")
            self.assertLess(execution["elapsed_minutes"], 60)

    def test_own_ship_operating_mode_noise_is_declared_unmodeled(self):
        published = e.capability_report()["entity_model"]["own_ship_operating_mode"]
        self.assertTrue(published["speed_limit_enforced"])
        self.assertFalse(published["radiated_noise_modeled"])
        contract = e.COMMAND_CONTRACT["concurrency"]["operating_mode"]
        self.assertIn("opposing detection does not use that spectrum", contract)

    # --- envelope crossings ---

    def test_link_is_deferred_until_the_boat_reaches_mast_depth(self):
        self.game["state"]["radio_reliability"] = 1
        result = e.apply_order(self.game, self.order(
            id="rx", activity="receive", link="mast_receive", depth=70, speed=8, minutes=5))
        categories = [entry["category"] for entry in result["reports_this_turn"]]
        self.assertIn("activity_deferred", categories)
        self.assertNotIn("radio_empty", categories)
        self.assertNotIn("message_received", categories)
        deferred = next(r for r in result["reports_this_turn"] if r["category"] == "activity_deferred")
        self.assertIn("for ordered 70 feet", deferred["text"])
        self.assertGreater(self.game["state"]["own"]["depth"], 70)

    def test_link_completes_once_the_whole_step_is_inside_the_envelope(self):
        self.game["state"]["radio_reliability"] = 1
        turn, linked = 0, False
        while not linked and turn < 8:
            result = e.apply_order(self.game, self.order(
                id=f"rx{turn}", expected_turn=turn, activity="receive",
                link="mast_receive", depth=70, speed=8, minutes=15))
            linked = any(r["category"] in ("radio_empty", "message_received")
                         for r in result["reports_this_turn"])
            turn += 1
        self.assertTrue(linked)
        self.assertEqual(self.game["state"]["own"]["depth"], 70)
        self.assertGreater(self.game["state"]["t"], e.TICK)

    def test_repair_credits_only_minutes_within_the_speed_limit(self):
        self.game["state"]["pump_fault"] = True
        self.game["state"]["own"]["speed"] = 18.0
        result = e.apply_order(self.game, self.order(
            id="fix", activity="repair", speed=10, depth=400, minutes=5,
            operating_mode="high_power"))
        # Decelerating 18 -> 10 at 2 knots per minute earns only the last minute.
        self.assertEqual(self.game["state"]["repair_progress"], 1)
        self.assertIn("activity_deferred",
                      [r["category"] for r in result["reports_this_turn"]])

    def test_operating_mode_waits_for_a_speed_its_limits_allow(self):
        # 18 knots is 11 above the quiet limit; deceleration cannot clear it
        # within one step, so the quiet lineup cannot take effect in one either.
        self.game["state"]["own"]["speed"] = 18.0
        self.game["state"]["own"]["operating_mode"] = "high_power"
        result = e.apply_order(self.game, self.order(
            id="quiet", speed=7, depth=400, minutes=5, operating_mode="quiet"))
        maneuver = result["last_execution"]["maneuver"]
        self.assertEqual(maneuver["operating_mode"]["ordered"], "quiet")
        self.assertEqual(maneuver["operating_mode"]["achieved"], "high_power")
        self.assertEqual(maneuver["speed"]["achieved"], 8)
        self.assertTrue(maneuver["operating_mode"]["in_progress"])
        e.apply_order(self.game, self.order(
            id="quiet-2", expected_turn=1, speed=7, depth=400, minutes=15,
            operating_mode="quiet"))
        self.assertEqual(self.game["state"]["own"]["operating_mode"], "quiet")

    # --- interrupted maneuvers ---

    def test_interrupt_reports_achieved_and_ordered_and_keeps_the_maneuver(self):
        result = e.apply_order(self.game, self.order(
            id="deep", depth=900, minutes=60,
            interrupt_on=["new_contact", "contact_lost", "classification_change"]))
        execution = result["last_execution"]
        self.assertEqual(execution["stop_reason"], "interrupt")
        maneuver = execution["maneuver"]
        self.assertEqual(maneuver["depth"]["ordered"], 900)
        self.assertLess(maneuver["depth"]["achieved"], 900)
        self.assertTrue(maneuver["depth"]["in_progress"])
        # The interrupt returns control; it does not quietly cancel the descent.
        self.assertEqual(self.game["state"]["ordered"]["depth"], 900)

    def test_restating_the_ordered_depth_continues_the_descent(self):
        e.apply_order(self.game, self.order(id="deep", depth=900, minutes=5))
        passing = self.game["state"]["own"]["depth"]
        e.apply_order(self.game, self.order(
            id="deep-2", expected_turn=1, depth=900, minutes=5))
        self.assertGreater(self.game["state"]["own"]["depth"], passing)

    def test_a_new_ordered_depth_replaces_the_maneuver_in_progress(self):
        e.apply_order(self.game, self.order(id="deep", depth=900, minutes=5))
        passing = self.game["state"]["own"]["depth"]
        self.assertGreater(passing, 400)
        e.apply_order(self.game, self.order(
            id="level", expected_turn=1, depth=400, minutes=10))
        self.assertLess(self.game["state"]["own"]["depth"], passing)
        self.assertEqual(self.game["state"]["ordered"]["depth"], 400)

    def test_steadying_on_the_ordered_settings_reports_once(self):
        first = e.apply_order(self.game, self.order(
            id="up", depth=300, speed=6, minutes=60, interrupt_on=["maneuver_complete"]))
        self.assertEqual(first["last_execution"]["stop_reason"], "interrupt")
        steady = [r for r in first["reports_this_turn"] if r["category"] == "maneuver_complete"]
        self.assertEqual(len(steady), 1)
        self.assertIn("depth 300 feet", steady[0]["text"])
        second = e.apply_order(self.game, self.order(
            id="hold", expected_turn=1, depth=300, speed=6, minutes=30,
            interrupt_on=["maneuver_complete"]))
        self.assertNotIn("maneuver_complete",
                         [r["category"] for r in second["reports_this_turn"]])

    # --- depth-dependent observation ---

    def test_layer_crossing_uses_the_depth_actually_held(self):
        """Observation must read the depth the boat reached, not the one ordered.

        Both worlds end the step at 850 feet. One was ordered to 60 feet, on
        the far side of the mixed layer, and got 50 feet of it at 2 knots. If the
        engine credited the ordered depth the two would diverge.
        """
        ordered_shallow = e.initialize("ab" * 32)
        held_deep = e.initialize("ab" * 32)
        layer = held_deep["state"]["environment"]["true"]["mixed_layer_depth_feet"]
        common = dict(activity="listen", minutes=5, interrupt_on=[], course=90,
                      speed=2, operating_mode="standard")
        for game, depth in ((ordered_shallow, 60), (held_deep, 850)):
            game["state"]["own"].update(speed=2.0, depth=900.0)
            game["state"]["ordered"].update(speed=2.0, depth=900.0)
            e.apply_order(game, {"id": "d", "expected_turn": 0, "depth": depth, **common})
        self.assertEqual(ordered_shallow["state"]["own"]["depth"], 850)
        self.assertEqual(held_deep["state"]["own"]["depth"], 850)
        self.assertLess(60, layer)
        self.assertGreater(850, layer)
        self.assertEqual(
            [r["text"] for r in ordered_shallow["state"]["reports"] if r["department"] == "Sonar"],
            [r["text"] for r in held_deep["state"]["reports"] if r["department"] == "Sonar"])

    def test_public_environment_is_the_dated_estimate(self):
        from submarine_command import acoustics
        view = e.public_view(self.game)
        public = view["acoustic_environment"]
        measured = self.game["state"]["environment"]["measured"]
        self.assertNotIn("true", public)
        self.assertEqual(
            public["estimated_mixed_layer_depth_feet"],
            round(measured["mixed_layer_depth_feet"], 1),
        )
        self.assertEqual(
            public["sound_speed_profile"],
            acoustics.public_environment(self.game["state"]["environment"], 0)["sound_speed_profile"],
        )
        hidden = copy.deepcopy(self.game)
        hidden["state"]["environment"]["true"]["water_depth_feet"] = 1234.56789
        self.assertNotIn("1234.56789", json.dumps(e.public_view(hidden)))
        self.assertGreater(public["profile_age_minutes"], 0)
        e.apply_order(self.game, self.order(id="age", minutes=20))
        aged = e.public_view(self.game)["acoustic_environment"]["profile_age_minutes"]
        self.assertAlmostEqual(aged, public["profile_age_minutes"] + 20)

    def test_receiver_depth_changes_signal_excess_in_the_shared_column(self):
        from submarine_command import acoustics
        state = copy.deepcopy(self.game["state"])
        actor = state["actors"][0]
        actor.update(x=6.0, y=0.0, depth=60.0)
        layer = state["environment"]["true"]["mixed_layer_depth_feet"]
        in_layer = min(80.0, max(40.0, layer * 0.4))
        below = min(850.0, layer + 250.0)
        state["own"]["depth"] = in_layer
        shallow, _ = e.reception_signal_excess(state, actor, state["own"])
        state["own"]["depth"] = below
        deep, own_loss = e.reception_signal_excess(state, actor, state["own"])
        self.assertNotAlmostEqual(shallow, deep)
        frequency = acoustics.representative_frequency_hz(e.contact_kind(actor))
        swapped = acoustics.transmission_loss(
            state["environment"]["true"], e.distance(state["own"], actor),
            actor["depth"], state["own"]["depth"], frequency,
        )
        self.assertAlmostEqual(own_loss.transmission_loss_db, swapped.transmission_loss_db)

    # --- audit invariants ---

    def test_kinematic_substeps_draw_no_randomness(self):
        before = len(self.game["state"]["rng_trace"])
        e.apply_order(self.game, self.order(
            id="swing", course=300, speed=18, depth=900, minutes=5,
            operating_mode="high_power"))
        moving = len(self.game["state"]["rng_trace"]) - before
        steady = e.initialize("ab" * 32)
        before = len(steady["state"]["rng_trace"])
        e.apply_order(steady, {"id": "swing", "expected_turn": 0, "activity": "listen",
                               "minutes": 5, "interrupt_on": [], "course": 90, "speed": 5,
                               "depth": 400, "operating_mode": "standard"})
        self.assertEqual(moving, len(steady["state"]["rng_trace"]) - before)

    def test_maneuvers_replay_from_the_seed(self):
        for turn, (course, speed, depth) in enumerate(
                ((270, 18, 900), (45, 2, 60), (180, 9, 500))):
            e.apply_order(self.game, self.order(
                id=f"leg-{turn}", expected_turn=turn, course=course, speed=speed,
                depth=depth, minutes=30, operating_mode="high_power"))
            self.assertTrue(e.verify(self.game)["verified"])

    # --- no defaults ---

    def test_every_order_field_must_be_stated(self):
        complete = self.order(id="complete")
        e.validate_order(complete, self.game["state"])
        for field in ("activity", "minutes", "course", "speed", "depth",
                      "operating_mode", "interrupt_on"):
            partial = {k: v for k, v in complete.items() if k != field}
            with self.assertRaises(ValueError) as raised:
                e.validate_order(partial, self.game["state"])
            self.assertIn(field, str(raised.exception))
            self.assertIn("no default", str(raised.exception))

    def test_an_omitted_field_changes_nothing(self):
        before = e.canonical(self.game)
        with self.assertRaises(ValueError):
            e.apply_order(self.game, {"id": "bare", "expected_turn": 0})
        with self.assertRaises(ValueError):
            e.apply_order(self.game, {k: v for k, v in self.order(id="x").items()
                                      if k != "expected_turn"})
        self.assertEqual(before, e.canonical(self.game))

    def test_transmit_and_focus_fields_must_be_stated(self):
        e.apply_order(self.game, self.order(id="look", activity="focus", focus="S01"))
        with self.assertRaises(ValueError):
            e.validate_order(self.order(id="f", expected_turn=1, activity="focus"),
                             self.game["state"])
        transmit = self.order(id="t", expected_turn=1, activity="transmit", link="mast_transmit",
                              depth=70, speed=5, assessment="unresolved",
                              message="Mixed evidence", basis=[])
        e.validate_order(transmit, self.game["state"])
        for field in ("assessment", "message", "basis", "link"):
            with self.assertRaises(ValueError):
                e.validate_order({k: v for k, v in transmit.items() if k != field},
                                 self.game["state"])

    def test_an_end_order_carries_no_duration(self):
        with self.assertRaises(ValueError):
            e.validate_order({"id": "stop", "expected_turn": 0, "activity": "end",
                              "minutes": 0}, self.game["state"])
        e.validate_order({"id": "stop", "expected_turn": 0, "activity": "end"},
                         self.game["state"])

    def test_state_and_transcript_tampering_detected(self):
        e.apply_order(self.game, self.order())
        altered = copy.deepcopy(self.game)
        altered["state"]["actors"][0]["x"] += 1
        with self.assertRaises(ValueError):
            e.verify(altered)
        altered = copy.deepcopy(self.game)
        altered["events"][0]["public_result"]["time"] = "9999"
        with self.assertRaises(ValueError):
            e.verify(altered)

    def test_debrief_requires_explicit_end(self):
        with self.assertRaises(ValueError):
            e.debrief(self.game)
        e.apply_order(self.game, {"id": "finish", "expected_turn": len(self.game["events"]), "activity": "end"})
        self.assertTrue(e.debrief(self.game)["spoilers"])
        with self.assertRaises(ValueError):
            e.apply_order(self.game, self.order())

    def test_unaware_opponent_does_not_react_to_true_position(self):
        state = copy.deepcopy(self.game["state"])
        actor = e.make_entity(
            DART,
            id="test-diesel",
            x=5,
            y=0,
            course=90,
            speed=5,
            depth=300,
            aware=False,
            last_heard=None,
            evaded=False,
        )
        before = (actor["course"], actor["speed"])
        class NoDetection:
            def u(self, label):
                return 0.999999
        e.opponent_step(state, actor, NoDetection(), False)
        self.assertEqual(before, (actor["course"], actor["speed"]))
        self.assertFalse(actor["aware"])

    def test_detection_has_no_probability_floor(self):
        """Inaudible geometry cannot still produce a real detection."""
        from submarine_command import acoustics
        self.assertLess(acoustics.detection_probability(-80.0), 1e-6)
        self.assertLess(acoustics.detection_probability(-80.0, ceiling=0.70), 1e-6)
        state = copy.deepcopy(self.game["state"])
        actor = e.make_entity(
            DART,
            id="distant-diesel",
            x=40,
            y=0,
            course=90,
            speed=4,
            depth=300,
            aware=False,
            last_heard=None,
            evaded=False,
        )

        class FloorDraw:
            def u(self, label):
                return 0.001

            def between(self, label, low, high):
                return (low + high) / 2

        with mock.patch.object(acoustics, "signal_excess_db", return_value=-80.0):
            e.opponent_step(state, actor, FloorDraw(), False)
        self.assertFalse(actor["aware"])
        self.assertIsNone(actor["last_heard"])

        listening = copy.deepcopy(self.game["state"])
        primary = listening["actors"][0]
        before = len(listening["tracks"][0]["observations"])
        with mock.patch.object(acoustics, "signal_excess_db", return_value=-80.0):
            with mock.patch.object(e, "acoustic_window_db", return_value=0.0):
                e.observe_contact(listening, primary, FloorDraw())
        self.assertEqual(len(listening["tracks"][0]["observations"]), before)

    def test_every_world_actor_uses_registered_components_and_valid_geometry(self):
        state = self.game["state"]
        for entity in [state["own"], *state["actors"]]:
            self.assertIn(entity["spec"], ENTITY_REGISTRY)
            spec = e.entity_spec(entity)
            self.assertTrue(spec.envelope.allows(entity["speed"], entity["depth"]))
            self.assertIn(entity["operating_mode"], {
                mode.identifier for mode in spec.modes
            })
            self.assertEqual(
                set(entity["resources"]),
                {resource.identifier for resource in spec.resources},
            )

    def test_diesel_energy_consumes_submerged_and_recharges_while_snorkeling(self):
        actor = e.make_entity(
            DART,
            id="energy-test",
            x=0,
            y=0,
            course=0,
            speed=4,
            depth=300,
        )
        initial = actor["resources"]["battery_energy"]
        e.advance_entity_components(actor)
        submerged = actor["resources"]["battery_energy"]
        self.assertLess(submerged, initial)
        actor["operating_mode"] = "snorkeling"
        actor["depth"] = 50
        e.advance_entity_components(actor)
        self.assertGreater(actor["resources"]["battery_energy"], submerged)

    def test_low_energy_diesel_snorkels_before_shared_movement(self):
        state = copy.deepcopy(self.game["state"])
        actor = e.make_entity(
            DART,
            id="low-energy",
            x=0,
            y=0,
            course=0,
            speed=9,
            depth=400,
        )
        actor["resources"]["battery_energy"] = 20
        state["t"] = 20
        e.prepare_actor_step(state, actor, e.Dice(self.game["seed"], []))
        self.assertEqual(actor["operating_mode"], "snorkeling")
        self.assertEqual(actor["depth"], 50)
        self.assertLessEqual(actor["speed"], 7)

    def test_biologic_behavior_is_replayable_and_uses_entity_modes(self):
        base = e.make_entity(
            BIOLOGIC,
            id="bio-test",
            x=0,
            y=0,
            course=90,
            speed=3,
            depth=300,
        )
        state = copy.deepcopy(self.game["state"])
        state["t"] = 20
        first = copy.deepcopy(base)
        second = copy.deepcopy(base)
        e.prepare_actor_step(state, first, e.Dice("19" * 32, []))
        e.prepare_actor_step(state, second, e.Dice("19" * 32, []))
        self.assertEqual(first, second)
        self.assertTrue(
            BIOLOGIC.mode(first["operating_mode"]).allows(
                first["speed"], first["depth"]
            )
        )

    def test_mode_envelope_rejection_is_atomic(self):
        before = e.canonical(self.game)
        with self.assertRaises(ValueError):
            e.apply_order(
                self.game,
                self.order(operating_mode="quiet", speed=12),
            )
        self.assertEqual(before, e.canonical(self.game))

    def test_cli_resume_and_no_reinitialization(self):
        with tempfile.TemporaryDirectory() as temp:
            command = [sys.executable, "-m", "submarine_command", "--session", temp]
            created = subprocess.run(command + ["init"], capture_output=True, text=True, check=True)
            before = (Path(temp) / "private.json").read_bytes()
            status = subprocess.run(command + ["status"], capture_output=True, text=True, check=True)
            self.assertEqual(json.loads(created.stdout), json.loads(status.stdout))
            rejected = subprocess.run(command + ["init"], capture_output=True, text=True)
            self.assertEqual(rejected.returncode, 2)
            self.assertEqual(before, (Path(temp) / "private.json").read_bytes())
            self.assertNotIn("seed", status.stdout)


class CommunicationsTests(unittest.TestCase):
    def setUp(self):
        self.game = e.initialize("ab" * 32)

    def order(self, **kwargs):
        own = self.game["state"]["own"]
        return {"id": "comm-01", "expected_turn": len(self.game["events"]),
                "activity": "receive", "minutes": 5, "interrupt_on": [],
                "course": own["course"], "speed": own["speed"], "depth": own["depth"],
                "operating_mode": own["operating_mode"], **kwargs}

    def place(self, depth, speed=5):
        self.game["state"]["own"]["depth"] = float(depth)
        self.game["state"]["own"]["speed"] = float(speed)
        self.game["state"]["ordered"]["depth"] = float(depth)
        self.game["state"]["ordered"]["speed"] = float(speed)

    def warship(self):
        return next(
            actor for actor in self.game["state"]["actors"]
            if e.entity_spec(actor).category == e.EntityCategory.SURFACE_WARSHIP
        )

    def radio_draws(self):
        return [
            entry for entry in self.game["state"]["rng_trace"]
            if str(entry["event"]).startswith("radio-window:")
        ]

    def test_receive_and_transmit_modes_reject_the_other_direction(self):
        self.place(70)
        before = e.canonical(self.game)
        with self.assertRaises(ValueError):
            e.validate_order(self.order(link="mast_transmit"), self.game["state"])
        with self.assertRaises(ValueError):
            e.validate_order(self.order(
                activity="transmit", link="buoyant_receive", depth=200, speed=5,
                assessment="unresolved", message="Report", basis=[],
            ), self.game["state"])
        with self.assertRaises(ValueError):
            e.validate_order(self.order(activity="retrieve", link="mast_receive"), self.game["state"])
        with self.assertRaises(ValueError):
            e.validate_order(
                self.order(activity="listen", link="mast_receive"), self.game["state"]
            )
        self.assertEqual(before, e.canonical(self.game))

    def test_link_must_be_named_and_envelopes_differ(self):
        before = e.canonical(self.game)
        with self.assertRaises(ValueError) as raised:
            e.validate_order(self.order(depth=70, speed=5), self.game["state"])
        self.assertIn("link", str(raised.exception))
        self.assertIn("no default", str(raised.exception))
        e.validate_order(
            self.order(link="mast_receive", depth=70, speed=8), self.game["state"]
        )
        with self.assertRaises(ValueError):
            e.validate_order(
                self.order(link="buoyant_receive", depth=70, speed=5),
                self.game["state"],
            )
        e.validate_order(
            self.order(link="buoyant_receive", depth=200, speed=6), self.game["state"]
        )
        with self.assertRaises(ValueError):
            e.validate_order(
                self.order(link="mast_receive", depth=200, speed=5),
                self.game["state"],
            )
        self.assertEqual(before, e.canonical(self.game))

    def test_repeated_attempts_in_one_window_share_the_channel_draw(self):
        self.place(70)
        self.game["state"]["radio_reliability"] = 0
        first = e.apply_order(self.game, self.order(
            id="fail-1", link="mast_receive", depth=70, speed=5))
        second = e.apply_order(self.game, self.order(
            id="fail-2", link="mast_receive", depth=70, speed=5))
        self.assertIn("radio_failure", [r["category"] for r in first["reports_this_turn"]])
        self.assertIn("radio_failure", [r["category"] for r in second["reports_this_turn"]])
        draws = self.radio_draws()
        self.assertEqual([entry["event"] for entry in draws], ["radio-window:0", "radio-window:0"])
        self.assertEqual(draws[0]["u"], draws[1]["u"])
        self.game["state"]["antennas"]["buoyant_receive_array"]["deployment"] = "streamed"
        self.game["state"]["own"]["equipment"]["buoyant_receive_array"] = "streamed"
        self.place(200)
        third = e.apply_order(self.game, self.order(
            id="fail-3", link="buoyant_receive", depth=200, speed=5))
        self.assertIn("radio_failure", [r["category"] for r in third["reports_this_turn"]])
        draws = self.radio_draws()
        self.assertEqual(draws[2]["event"], "radio-window:0")
        self.assertEqual(draws[0]["u"], draws[2]["u"])

    def test_a_deferred_link_draws_no_channel(self):
        before = len(self.radio_draws())
        e.apply_order(self.game, self.order(
            id="deep-rx", link="mast_receive", depth=70, speed=5, minutes=5))
        self.assertEqual(before, len(self.radio_draws()))
        self.assertEqual(self.game["state"]["antennas"]["radio_mast"]["deployment"], "stowed")
        self.assertTrue(e.verify(self.game)["verified"])

    def test_mast_receive_copies_the_dated_bulletin_without_adding_truth(self):
        self.place(70)
        self.game["state"]["radio_reliability"] = 1
        note = (
            "Dated exercise note: treat the passage as empty of submerged traffic. "
            "This note can be wrong."
        )
        self.game["state"]["bulletins"].append(
            {"id": "NOTE-WRONG", "available": 0, "text": note}
        )
        hidden_name = self.game["state"]["actors"][0]["name"]
        result = e.apply_order(self.game, self.order(
            id="copy", link="mast_receive", depth=70, speed=5))
        copied = [
            report for report in result["reports_this_turn"]
            if report["category"] == "message_received"
        ]
        self.assertEqual([report["text"] for report in copied], [note])
        self.assertNotIn(hidden_name, note)
        self.assertFalse(copied[0]["link"]["emitted"])
        self.assertEqual(copied[0]["link"]["cycle"], "raised_and_housed")
        self.assertNotIn(hidden_name, json.dumps(result))
        self.assertNotIn("radio_intercepts", json.dumps(result))
        self.assertFalse(self.warship()["aware"])

    def test_mast_transmission_radiates_and_can_be_intercepted(self):
        self.place(70)
        channel = e.Dice(self.game["seed"], []).u("radio-window:0")
        # The link fails, and the same draw still clears the published intercept margin.
        self.game["state"]["radio_reliability"] = channel - 0.05
        ship = self.warship()
        ship.update(x=1.0, y=0.0, course=90.0, speed=3.0, aware=False, last_heard=None)
        merchant = next(
            actor for actor in self.game["state"]["actors"]
            if e.entity_spec(actor).category == e.EntityCategory.MERCHANT
        )
        merchant.update(x=8.0, y=0.0, aware=False)
        result = e.apply_order(self.game, self.order(
            id="send", activity="transmit", link="mast_transmit", depth=70, speed=5,
            assessment="unresolved", message="Evidence remains mixed", basis=[]))
        sent = next(
            report for report in result["reports_this_turn"]
            if report["category"] == "radio_failure"
        )
        self.assertIn("radiated", sent["text"])
        self.assertTrue(sent["link"]["emitted"])
        self.assertEqual(sent["link"]["deployment"], "stowed")
        self.assertTrue(ship["aware"])
        self.assertEqual(ship["last_heard"]["source"], "radio_intercept")
        self.assertFalse(merchant["aware"])
        self.assertEqual(len(self.game["state"]["radio_intercepts"]), 1)
        self.assertNotIn("radio_intercepts", json.dumps(result))
        ship.update(x=40.0, y=0.0, course=90.0, speed=3.0, aware=False, last_heard=None)
        self.game["state"]["radio_intercepts"].clear()
        e.apply_order(self.game, self.order(
            id="send-far", activity="transmit", link="mast_transmit", depth=70, speed=5,
            assessment="unresolved", message="Still mixed", basis=[]))
        self.assertFalse(ship["aware"])
        self.assertEqual(self.game["state"]["radio_intercepts"], [])
        draws = self.radio_draws()
        self.assertEqual(draws[0]["u"], draws[1]["u"])
        self.assertFalse(any("intercept" in entry["event"] for entry in self.game["state"]["rng_trace"]))

    def test_buoyant_reception_waits_for_deployment_and_latency(self):
        self.game["state"]["radio_reliability"] = 2
        deployed = e.apply_order(self.game, self.order(
            id="stream", link="buoyant_receive", depth=400, speed=5, minutes=30,
            interrupt_on=["antenna_deployed"]))
        self.assertEqual(deployed["last_execution"]["elapsed_minutes"], 10)
        self.assertEqual(deployed["last_execution"]["stop_reason"], "interrupt")
        antenna = deployed["own_ship"]["antennas"]["buoyant_receive_array"]
        self.assertEqual(antenna["deployment"], "streamed")
        self.assertEqual(antenna["progress_minutes"], 0)
        self.assertEqual(self.radio_draws(), [])
        early = e.apply_order(self.game, self.order(
            id="early", link="buoyant_receive", depth=400, speed=5))
        self.assertIn("radio_empty", [r["category"] for r in early["reports_this_turn"]])
        self.assertNotIn(
            "message_received", [r["category"] for r in early["reports_this_turn"]]
        )
        e.apply_order(self.game, self.order(
            id="wait", activity="listen", depth=400, speed=5, minutes=20))
        # Scheduled 0330 traffic plus 15 minutes of buoyant latency is due at 35.
        self.assertGreaterEqual(self.game["state"]["t"], 35)
        copied = e.apply_order(self.game, self.order(
            id="late", link="buoyant_receive", depth=400, speed=5))
        messages = [
            report for report in copied["reports_this_turn"]
            if report["category"] == "message_received"
        ]
        bulletin = self.game["state"]["bulletins"][0]
        self.assertEqual(messages[0]["text"], bulletin["text"])
        self.assertFalse(messages[0]["link"]["emitted"])
        self.assertEqual(
            [report["message_id"] for report in messages],
            [bulletin["id"]],
        )
        before = e.canonical(self.game)
        self.assertEqual(copied, e.public_view(self.game))
        e.public_history(self.game)
        self.assertEqual(before, e.canonical(self.game))

    def test_streamed_antenna_blocks_orders_outside_its_envelope_until_retrieval(self):
        e.apply_order(self.game, self.order(
            id="deepen", activity="listen", depth=800, speed=5, minutes=20))
        self.assertEqual(self.game["state"]["own"]["depth"], 800)
        deferred = e.apply_order(self.game, self.order(
            id="approach", link="buoyant_receive", depth=200, speed=5, minutes=15))
        self.assertIn(
            "activity_deferred",
            [report["category"] for report in deferred["reports_this_turn"]],
        )
        self.assertEqual(
            self.game["state"]["antennas"]["buoyant_receive_array"]["deployment"],
            "stowed",
        )
        self.assertEqual(
            self.game["state"]["antennas"]["buoyant_receive_array"]["progress_minutes"],
            0,
        )
        streamed = e.apply_order(self.game, self.order(
            id="out", link="buoyant_receive", depth=200, speed=5, minutes=60,
            interrupt_on=["antenna_deployed"]))
        self.assertEqual(streamed["last_execution"]["elapsed_minutes"], 10)
        self.assertEqual(
            self.game["state"]["antennas"]["buoyant_receive_array"]["deployment"],
            "streamed",
        )
        before = e.canonical(self.game)
        with self.assertRaises(ValueError):
            e.apply_order(self.game, self.order(
                id="leave", activity="listen", depth=800, speed=5, minutes=5))
        with self.assertRaises(ValueError):
            e.apply_order(self.game, self.order(
                id="mast", link="mast_receive", depth=70, speed=5))
        self.assertEqual(before, e.canonical(self.game))
        housed = e.apply_order(self.game, self.order(
            id="house", activity="retrieve", link="buoyant_receive",
            depth=200, speed=5, minutes=30))
        self.assertEqual(housed["last_execution"]["stop_reason"], "task_complete")
        self.assertEqual(housed["last_execution"]["elapsed_minutes"], 10)
        self.assertEqual(
            self.game["state"]["antennas"]["buoyant_receive_array"]["deployment"],
            "stowed",
        )
        e.validate_order(self.order(
            id="free", activity="listen", depth=800, speed=5, minutes=5,
        ), self.game["state"])
        self.assertTrue(e.verify(self.game)["verified"])

    def test_retrieval_in_progress_rejects_another_receive(self):
        e.apply_order(self.game, self.order(
            id="out", link="buoyant_receive", depth=400, speed=5, minutes=10))
        e.apply_order(self.game, self.order(
            id="half", activity="retrieve", link="buoyant_receive",
            depth=200, speed=5, minutes=5))
        self.assertEqual(
            self.game["state"]["antennas"]["buoyant_receive_array"]["deployment"],
            "retrieving",
        )
        before = e.canonical(self.game)
        with self.assertRaises(ValueError):
            e.apply_order(self.game, self.order(
                id="again", link="buoyant_receive", depth=200, speed=5))
        self.assertEqual(before, e.canonical(self.game))
        status = e.public_view(self.game)
        self.assertEqual(status, e.public_view(self.game))
        self.assertEqual(
            status["own_ship"]["antennas"]["buoyant_receive_array"]["progress_minutes"],
            5,
        )
        self.assertTrue(e.verify(self.game)["verified"])


if __name__ == "__main__":
    unittest.main()
