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
from submarine_command import engine as e
from submarine_command.platforms import BIOLOGIC, DART, ENTITY_REGISTRY, MERCHANT


class EngineTests(unittest.TestCase):
    def setUp(self):
        # These are throwaway test seeds; never use or inspect the live seed.
        self.game = e.initialize("ab" * 32)

    def order(self, **kwargs):
        return {"id": "test-01", "minutes": 15, "interrupt_on": [], **kwargs}

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
                    self.assertFalse(set(obj) & {"seed", "actors", "actor", "kind", "spec", "rng_trace", "initial_state", "intent", "last_heard", "aware", "bearing_bias", "bulletins", "radio_reliability"})
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
        # Force reception without touching the propagation model: a source loud
        # enough that signal excess is positive at any range in the scenario.
        loud = (260.0,) * 6
        with mock.patch.object(e, "source_levels", return_value=loud):
            for minute in (5, 10, 15):
                state["t"] = minute
                for _ in range(2):
                    e.observe_contact(state, primary, dice)
        track = state["tracks"][0]
        self.assertLessEqual(len(track["evidence"]), 1)
        self.assertTrue(all(0 <= ob["bearing_true"] < 360 for ob in track["observations"]))

    def test_full_replay_with_radio_maneuvers_and_early_interrupts(self):
        actions = [self.order(id="a", activity="focus", focus="S01", minutes=30, interrupt_on=["new_contact"]),
                   self.order(id="b", course=45, speed=7, minutes=25),
                   self.order(id="c", depth=70, activity="receive", minutes=30),
                   self.order(id="d", activity="mast", minutes=10),
                   self.order(id="e", depth=400, activity="active", minutes=5),
                   self.order(id="f", depth=70, activity="transmit", assessment="unresolved", message="Evidence remains mixed", basis=["R0004"], minutes=40)]
        for order in actions:
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
        result = e.apply_order(
            self.game,
            self.order(
                id="receive-empty",
                activity="receive",
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
        self.assertIn("five-minute", contract["concurrency"]["communications"])
        self.assertIn("20 productive minutes", contract["concurrency"]["repair"])

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
        e.apply_order(self.game, {"id": "finish", "activity": "end", "minutes": 0})
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

            def between(self, label, low, high):
                return low + (high - low) * self.u(label)
        e.opponent_step(state, actor, NoDetection(), False)
        self.assertEqual(before, (actor["course"], actor["speed"]))
        self.assertFalse(actor["aware"])

    def probe(self, own_depth, contact_depth, speed=5.0, contact_speed=4.0, spec=DART,
              mode="battery", range_nm=8.0):
        """Score one geometry in a disposable copy of the world."""
        state = copy.deepcopy(self.game["state"])
        own = dict(state["own"], x=0.0, y=0.0, depth=own_depth, speed=speed)
        contact = e.make_entity(spec, id="probe", x=range_nm, y=0.0, course=270.0,
                                speed=contact_speed, depth=contact_depth)
        contact["operating_mode"] = mode
        return e.acoustic_ledger(dict(state, own=own, t=0), own, contact)

    def test_crossing_the_layer_changes_reception_through_the_environment(self):
        state = self.game["state"]
        layer_feet = e.acoustics.metres_to_feet(
            e.ocean_model(state).layer_depth_m(0, 0))
        deep_contact = layer_feet + 250
        same_side = self.probe(own_depth=layer_feet + 200, contact_depth=deep_contact)
        across = self.probe(own_depth=layer_feet - 200, contact_depth=deep_contact)
        # Worse from the far side of the layer, and worse because the decibel
        # ledger says so, not because a flag was set.
        self.assertLess(across["probability"], same_side["probability"])
        self.assertLess(across["best"]["signal_excess_db"],
                        same_side["best"]["signal_excess_db"])

    def test_the_duct_carries_a_shallow_contact_that_the_deep_boat_hears_otherwise(self):
        state = self.game["state"]
        layer_feet = e.acoustics.metres_to_feet(e.ocean_model(state).layer_depth_m(0, 0))
        in_duct = self.probe(own_depth=layer_feet - 250, contact_depth=0.0,
                             spec=MERCHANT, mode="service", contact_speed=10.0)
        below = self.probe(own_depth=layer_feet + 250, contact_depth=0.0,
                           spec=MERCHANT, mode="service", contact_speed=10.0)
        self.assertEqual(in_duct["best"]["path"], "surface_duct")
        self.assertNotEqual(below["best"]["path"], "surface_duct")

    def test_speed_raises_both_own_noise_and_own_radiated_level(self):
        quiet = self.probe(own_depth=600, contact_depth=600, speed=5.0)
        fast = self.probe(own_depth=600, contact_depth=600, speed=15.0)
        self.assertLess(fast["probability"], quiet["probability"])
        slow_levels = e.source_levels(dict(self.game["state"]["own"], speed=5.0, depth=600))
        fast_levels = e.source_levels(dict(self.game["state"]["own"], speed=15.0, depth=600))
        self.assertTrue(all(f > s for f, s in zip(fast_levels, slow_levels)))

    def test_depth_buys_speed_through_cavitation_inception(self):
        spec = ENTITY_REGISTRY[self.game["state"]["own"]["spec"]]
        shallow = spec.signature.cavitation_speed_knots(100)
        deep = spec.signature.cavitation_speed_knots(700)
        self.assertGreater(deep, shallow)
        mode = spec.mode("standard")
        below = spec.signature.levels_for(mode, shallow - 0.5, 100)
        above = spec.signature.levels_for(mode, shallow + 0.5, 100)
        # The step at inception dominates the small speed change either side.
        self.assertGreater(above[0] - below[0], e.acoustics.CAVITATION_EXCESS_DB - 1)

    def test_opposition_hears_own_ship_through_the_same_environment(self):
        state = copy.deepcopy(self.game["state"])
        own = dict(state["own"], x=0.0, y=0.0, depth=500, speed=5)
        listener = e.make_entity(DART, id="listener", x=6.0, y=0.0, course=270.0,
                                 speed=4.0, depth=500)
        outward = e.acoustic_ledger(dict(state, own=own, t=0), listener, own)
        inward = e.acoustic_ledger(dict(state, own=own, t=0), own, listener)
        # Transmission loss is reciprocal; the detection decision is not, because
        # the two platforms differ in what they radiate and what they can hear.
        self.assertEqual(outward["best"]["transmission_loss_db"],
                         e.band_entry(inward, outward["best"]["band"])["transmission_loss_db"])
        self.assertNotEqual(outward["best"]["source_level_db"], inward["best"]["source_level_db"])

    def test_the_decibel_ledger_never_reaches_a_public_report(self):
        for i in range(6):
            view = e.apply_order(self.game, self.order(id=f"led{i}", minutes=15))
        text = json.dumps(view)
        for leaked in ("signal_excess_db", "acoustic_ledger", "layer_depth_west_m",
                       "transmission_loss_db", "noise_level_db", "thermocline_gradient"):
            self.assertNotIn(leaked, text, leaked)
        # Charted depth is published on purpose; the true sound speed field is not.
        self.assertIn("charted_water_depth_feet", text)
        history = json.dumps(e.public_history(self.game))
        self.assertNotIn("signal_excess_db", history)
        # It is kept privately, so the debrief can explain every detection.
        self.assertTrue(self.game["state"]["acoustic_ledger"])

    def test_prediction_comes_from_the_estimate_and_ages_against_truth(self):
        state = self.game["state"]
        before = e.public_view(self.game)["acoustic_environment"]
        self.assertIn("forecast", before["basis"])
        self.assertEqual(before["age_minutes"], 0)
        e.apply_order(self.game, self.order(id="run", minutes=60, course=90, speed=12))
        aged = e.public_view(self.game)["acoustic_environment"]
        self.assertGreater(aged["age_minutes"], 0)
        self.assertGreater(aged["distance_from_observation_nm"], 5)
        # The published layer depth is the estimate, and it does not quietly
        # track the true layer as the boat runs east.
        self.assertEqual(aged["estimated_layer_depth_feet"], before["estimated_layer_depth_feet"])
        truth = e.acoustics.metres_to_feet(
            e.ocean_model(state).layer_depth_m(state["own"]["x"], state["t"]))
        self.assertNotAlmostEqual(aged["estimated_layer_depth_feet"], truth, delta=1)

    def test_a_profile_observation_replaces_the_estimate_with_a_measurement(self):
        e.apply_order(self.game, self.order(id="run", minutes=60, course=90, speed=12))
        e.apply_order(self.game, self.order(id="bt", activity="sound_profile", minutes=5, speed=5))
        state = self.game["state"]
        estimate = state["profile_estimate"]
        self.assertIn("observation", estimate["source"])
        self.assertEqual(estimate["taken_minutes"], state["t"])
        truth = e.ocean_model(state).layer_depth_m(state["own"]["x"], state["t"])
        # A measurement, not a revelation: close to truth, but not equal to it.
        self.assertLess(abs(estimate["layer_depth_m"] - truth), 5.0)
        view = e.public_view(self.game)["acoustic_environment"]
        self.assertEqual(view["age_minutes"], 0)
        self.assertLess(view["estimated_layer_uncertainty_feet"], 30)

    def test_an_observation_needs_a_speed_the_platform_supports(self):
        with self.assertRaises(ValueError):
            e.validate_order(self.order(activity="sound_profile", speed=16,
                                        operating_mode="high_power"), self.game["state"])

    def test_published_path_status_is_reported_for_every_modelled_path(self):
        view = e.public_view(self.game)["acoustic_environment"]
        for reception in view["predicted_reception"]:
            names = {entry["path"] for entry in reception["paths"]}
            self.assertEqual(names, {"direct", "surface_duct", "shadow_zone",
                                     "bottom_bounce", "convergence_zone"})
            for entry in reception["paths"]:
                self.assertIn(entry["status"], {"supported", "uncertain", "out_of_scope"})
        # This scenario has no depth excess, and says so rather than implying a
        # convergence zone exists.
        zone = next(entry for entry in view["predicted_reception"][0]["paths"]
                    if entry["path"] == "convergence_zone")
        self.assertEqual(zone["status"], "out_of_scope")

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


if __name__ == "__main__":
    unittest.main()
