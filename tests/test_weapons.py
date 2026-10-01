import copy
import json
import math
import unittest
from unittest import mock

from submarine_command import acoustics, engine
from submarine_command.platforms import DART, KESTREL, WARSHIP


class WeaponRuleTests(unittest.TestCase):
    def test_terminal_chances_follow_the_rounds_location(self):
        rule = KESTREL.employment("exercise_heavyweight")
        wartime = KESTREL.employment("wartime_heavyweight")
        exercise_public = rule.public_definition()
        wartime_public = wartime.public_definition()
        wartime_public["id"] = exercise_public["id"]
        self.assertEqual(wartime_public, exercise_public)
        near = engine.terminal_probabilities(
            0.0,
            effect_radius_nm=rule.lethal_radius_nm,
            destruction_radius_nm=rule.destruction_radius_nm,
            destruction_probability=rule.destruction_probability,
            mobility_probability=rule.mobility_probability,
            dud_probability=rule.dud_probability,
        )
        farther = engine.terminal_probabilities(
            rule.destruction_radius_nm,
            effect_radius_nm=rule.lethal_radius_nm,
            destruction_radius_nm=rule.destruction_radius_nm,
            destruction_probability=rule.destruction_probability,
            mobility_probability=rule.mobility_probability,
            dud_probability=rule.dud_probability,
        )
        outside_kill = engine.terminal_probabilities(
            rule.destruction_radius_nm + 0.05,
            effect_radius_nm=rule.lethal_radius_nm,
            destruction_radius_nm=rule.destruction_radius_nm,
            destruction_probability=rule.destruction_probability,
            mobility_probability=rule.mobility_probability,
            dud_probability=rule.dud_probability,
        )
        clear = engine.terminal_probabilities(
            rule.lethal_radius_nm + 0.1,
            effect_radius_nm=rule.lethal_radius_nm,
            destruction_radius_nm=rule.destruction_radius_nm,
            destruction_probability=rule.destruction_probability,
            mobility_probability=rule.mobility_probability,
            dud_probability=rule.dud_probability,
        )
        masked = engine.terminal_probabilities(
            0.0,
            effect_radius_nm=rule.lethal_radius_nm,
            destruction_radius_nm=rule.destruction_radius_nm,
            destruction_probability=rule.destruction_probability,
            mobility_probability=rule.mobility_probability,
            dud_probability=rule.dud_probability,
            mask_penalty=0.45,
        )
        self.assertGreater(near["destroyed"], farther["destroyed"])
        self.assertGreater(near["destroyed"], 0)
        self.assertEqual(outside_kill["destroyed"], 0)
        self.assertGreater(outside_kill["mobility_casualty"], 0)
        self.assertEqual(clear, {"destroyed": 0.0, "mobility_casualty": 0.0, "dud": 0.0})
        self.assertLess(masked["destroyed"], near["destroyed"])

    def test_catalog_keeps_unimplemented_rounds_out_of_the_employment_rules(self):
        published = WARSHIP.public_definition()
        guided = next(item for item in published["weapons_inventory"] if item["id"] == "guided_round")
        lightweight = next(
            item for item in published["weapons_inventory"] if item["id"] == "lightweight_round"
        )
        self.assertFalse(guided["employment_implemented"])
        self.assertTrue(lightweight["employment_implemented"])
        self.assertEqual(
            [rule["id"] for rule in published["employment_rules"]],
            ["lightweight_round"],
        )
        self.assertEqual(DART.employment_rules[0].identifier, "diesel_heavyweight")


class EmploymentOrderTests(unittest.TestCase):
    def setUp(self):
        self.game = engine.initialize("57" * 32)

    def order(self, **kwargs):
        own = self.game["state"]["own"]
        return {
            "id": "employ-1",
            "expected_turn": len(self.game["events"]),
            "activity": "employ",
            "minutes": 5,
            "interrupt_on": [],
            "course": own["course"],
            "speed": own["speed"],
            "depth": own["depth"],
            "operating_mode": own["operating_mode"],
            "weapon": "noise_maker",
            "target": "none",
            "basis": [],
            "confirm": True,
            **kwargs,
        }

    def test_unauthorized_offensive_order_changes_nothing(self):
        self.game["state"]["own"]["depth"] = 200
        self.game["state"]["ordered"]["depth"] = 200
        self._add_contact()
        before = engine.canonical(self.game)
        inventory = copy.deepcopy(self.game["state"]["own"]["inventory"])
        with self.assertRaises(ValueError) as raised:
            engine.apply_order(self.game, self.order(
                weapon="exercise_heavyweight", target="S99", depth=200, confirm=False,
            ))
        self.assertIn("Fire control", str(raised.exception))
        self.assertIn("Confirm the order", str(raised.exception))
        self.assertEqual(before, engine.canonical(self.game))
        self.assertEqual(inventory, self.game["state"]["own"]["inventory"])
        self.assertEqual(self.game["events"], [])
        engine.apply_order(self.game, self.order(
            id="confirmed", weapon="exercise_heavyweight", target="S99",
            depth=200, confirm=True,
        ))
        self.assertEqual(self.game["state"]["own"]["inventory"]["exercise_heavyweight"], 9)
        self.assertEqual(self.game["state"]["weapon_runs"][0]["status"], "running")
        self.assertFalse(self.game["state"]["weapon_runs"][0]["within_patrol_authorization"])
        self.assertFalse(any(actor.get("casualty") for actor in self.game["state"]["actors"]))

    def test_empty_inventory_is_rejected_before_time_passes(self):
        self.game["state"]["authorization"]["offensive_weapons"] = True
        self.game["state"]["own"]["inventory"]["exercise_heavyweight"] = 0
        self._add_contact()
        before = engine.canonical(self.game)
        with self.assertRaises(ValueError) as raised:
            engine.apply_order(self.game, self.order(
                weapon="exercise_heavyweight", target="S99", depth=200,
            ))
        self.assertIn("inventory", str(raised.exception))
        self.assertNotIn("not authorized", str(raised.exception))
        self.assertEqual(before, engine.canonical(self.game))

    def test_invalid_weapon_target_and_envelope_are_rejected(self):
        before = engine.canonical(self.game)
        with self.assertRaises(ValueError) as raised:
            engine.apply_order(self.game, self.order(weapon="guided_round"))
        self.assertIn("no employment rule", str(raised.exception))
        with self.assertRaises(ValueError) as raised:
            engine.apply_order(self.game, self.order(target="S99"))
        self.assertIn("target none", str(raised.exception))
        with self.assertRaises(ValueError) as raised:
            engine.apply_order(self.game, self.order(weapon="exercise_heavyweight", speed=12, depth=200))
        self.assertIn("envelope", str(raised.exception))
        with self.assertRaises(ValueError):
            engine.apply_order(self.game, self.order(activity="listen", weapon="noise_maker"))
        self.assertEqual(before, engine.canonical(self.game))

    def test_countermeasure_expends_one_and_replays(self):
        first = engine.apply_order(self.game, self.order())
        self.assertEqual(self.game["state"]["own"]["inventory"]["noise_maker"], 11)
        self.assertEqual(self.game["state"]["own"]["inventory"]["mobile_decoy"], 6)
        launched = next(
            report for report in first["reports_this_turn"]
            if report["category"] == "weapon_resolved"
        )
        self.assertEqual(launched["weapon"]["outcome"], "deployed")
        self.assertEqual(launched["weapon"]["inventory_remaining"], 11)
        self.assertNotIn("engagements", first)
        self.assertNotIn("probability", launched)
        self.assertTrue(engine.verify(self.game)["verified"])
        again = engine.initialize("57" * 32)
        engine.apply_order(again, self.order(expected_turn=0))
        self.assertEqual(
            self.game["state"]["engagements"], again["state"]["engagements"]
        )
        self.assertEqual(
            self.game["state"]["own"]["inventory"], again["state"]["own"]["inventory"]
        )

    def test_a_deep_heavyweight_order_defers_without_expending_a_round(self):
        self.game["state"]["authorization"]["offensive_weapons"] = True
        self._add_contact()
        before_trace = [
            entry["event"] for entry in self.game["state"]["rng_trace"]
        ]
        result = engine.apply_order(self.game, self.order(
            weapon="exercise_heavyweight", target="S99", depth=200,
        ))
        self.assertEqual(self.game["state"]["own"]["inventory"]["exercise_heavyweight"], 10)
        self.assertIn("activity_deferred", [report["category"] for report in result["reports_this_turn"]])
        self.assertFalse(any(
            event.startswith("weapon:")
            for event in (entry["event"] for entry in self.game["state"]["rng_trace"])
            if event not in before_trace
        ))

    def _add_contact(self):
        actor = self.game["state"]["actors"][0]
        self.game["state"]["tracks"].append({
            "id": "S99",
            "actor": actor["id"],
            "first": 0,
            "last": 0,
            "evidence": {},
            "observations": [{
                "bearing_true": 90,
                "range_estimate_nm": 2.0,
                "report_id": self.game["state"]["reports"][0]["id"],
                "elapsed_minutes": 0,
            }],
            "visual": None,
        })


class ConsequenceTests(unittest.TestCase):
    def setUp(self):
        self.game = engine.initialize("68" * 32)
        self.game["state"]["authorization"]["offensive_weapons"] = True
        self.game["state"]["own"]["depth"] = 200
        self.game["state"]["ordered"]["depth"] = 200
        actor = self.game["state"]["actors"][0]
        self.game["state"]["tracks"].append({
            "id": "S99",
            "actor": actor["id"],
            "first": 0,
            "last": 0,
            "evidence": {},
            "observations": [{
                "bearing_true": 90,
                "range_estimate_nm": 2.0,
                "report_id": self.game["state"]["reports"][0]["id"],
                "elapsed_minutes": 0,
            }],
            "visual": None,
        })

    def order(self, **kwargs):
        own = self.game["state"]["own"]
        raw = {
            "id": "shot",
            "expected_turn": len(self.game["events"]),
            "activity": "employ",
            "minutes": 5,
            "interrupt_on": [],
            "course": own["course"],
            "speed": own["speed"],
            "depth": 200,
            "operating_mode": own["operating_mode"],
            "weapon": "exercise_heavyweight",
            "target": "S99",
            "basis": [self.game["state"]["reports"][0]["id"]],
            "confirm": True,
        }
        raw.update(kwargs)
        return raw

    def _place_ahead(self, run, actor, gap):
        angle = math.radians(run["course"])
        actor["x"] = run["x"] + math.sin(angle) * gap
        actor["y"] = run["y"] + math.cos(angle) * gap

    def test_a_launch_runs_and_a_later_close_approach_can_destroy(self):
        twin = engine.initialize("68" * 32)
        twin["state"]["authorization"]["offensive_weapons"] = True
        twin["state"]["own"]["depth"] = 200
        twin["state"]["ordered"]["depth"] = 200
        twin["state"]["tracks"].append(copy.deepcopy(self.game["state"]["tracks"][-1]))
        engine.apply_order(self.game, self.order(id="run"))
        engine.apply_order(twin, self.order(id="run", expected_turn=0))
        self.assertEqual(self.game["state"]["engagements"], twin["state"]["engagements"])
        self.assertEqual(self.game["state"]["weapon_runs"][0]["status"], "running")
        self.assertNotIn("casualty", self.game["state"]["actors"][0])
        before_ids = [actor["id"] for actor in self.game["state"]["actors"]]
        run = self.game["state"]["weapon_runs"][0]
        target = self.game["state"]["actors"][0]
        self._place_ahead(run, target, 0.55)
        for other in self.game["state"]["actors"]:
            if other is not target:
                other.update(x=80.0, y=80.0)
        run["integrate"] = True

        class Certain:
            def u(self, label):
                return 0.0

            def between(self, label, low, high):
                return low

        starts = engine._position_snapshot(self.game["state"])
        engine.advance_weapon_runs(self.game["state"], Certain(), starts, starts)
        self.assertEqual(target["casualty"]["effect"], "destroyed")
        self.assertTrue(target["destroyed"])
        self.assertEqual([actor["id"] for actor in self.game["state"]["actors"]], before_ids)
        frozen = (target["x"], target["y"], copy.deepcopy(target["casualty"]))
        self.assertEqual((target["x"], target["y"], target["casualty"]), frozen)

    def test_debrief_separates_decision_evidence_luck_and_engine_errors(self):
        self.game["state"]["authorization"]["offensive_weapons"] = False
        engine.apply_order(self.game, self.order())
        run = self.game["state"]["weapon_runs"][0]
        target = self.game["state"]["actors"][0]
        self._place_ahead(run, target, 0.55)
        for other in self.game["state"]["actors"]:
            if other is not target:
                other.update(x=80.0, y=80.0)
        run["integrate"] = True

        class Certain:
            def u(self, label):
                return 0.0

        starts = engine._position_snapshot(self.game["state"])
        engine.advance_weapon_runs(self.game["state"], Certain(), starts, starts)
        adjudication = engine._adjudication(self.game)
        self.assertEqual(
            set(adjudication),
            {"decision_quality", "observed_evidence", "luck", "engine_errors"},
        )
        self.assertEqual(adjudication["engine_errors"], [])
        decision = adjudication["decision_quality"][0]
        self.assertEqual(decision["judgment"], "confirmed_outside_authorization")
        self.assertNotIn("draw", decision)
        self.assertNotIn("probability", decision)
        self.assertEqual(adjudication["observed_evidence"][0]["basis"], self.order()["basis"])
        self.assertEqual(adjudication["luck"][0]["outcome"], "destroyed")
        self.assertIn("probability", adjudication["luck"][0])
        self.assertIn("draw", adjudication["luck"][0])

        clean = engine.initialize("68" * 32)
        engine.apply_order(clean, {
            "id": "decoy",
            "expected_turn": 0,
            "activity": "employ",
            "minutes": 5,
            "interrupt_on": [],
            "course": clean["state"]["own"]["course"],
            "speed": clean["state"]["own"]["speed"],
            "depth": clean["state"]["own"]["depth"],
            "operating_mode": clean["state"]["own"]["operating_mode"],
            "weapon": "mobile_decoy",
            "target": "none",
            "basis": [],
            "confirm": True,
        })
        engine.apply_order(clean, {"id": "end", "expected_turn": 1, "activity": "end"})
        revealed = engine.debrief(clean)
        self.assertEqual(revealed["adjudication"]["engine_errors"], [])
        self.assertEqual(revealed["adjudication"]["luck"], [])
        self.assertEqual(
            revealed["adjudication"]["decision_quality"][0]["judgment"],
            "countermeasure_deployment",
        )
        self.assertEqual(revealed["verification"]["verified"], True)


class OppositionTests(unittest.TestCase):
    def setUp(self):
        self.game = engine.initialize("79" * 32)
        self.state = self.game["state"]
        self.own = self.state["own"]

    def test_engage_doctrine_shoots_at_its_fix_rather_than_the_true_position(self):
        actor = engine.make_entity(
            DART,
            id="hunter",
            x=1.0,
            y=0.0,
            course=270,
            speed=4,
            depth=200,
            aware=True,
            evaded=True,
            doctrine="engage",
            last_heard={"x": 0.0, "y": 40.0, "time": self.state["t"], "source": "acoustic"},
            observations=[],
            belief={"assessment": "contact_held"},
        )
        held = actor["inventory"]["diesel_heavyweight"]
        dice = engine.Dice(self.game["seed"], [])

        class Zero(dice.__class__):
            def u(self, label):
                self.trace.append({"event": label, "u": 0.0})
                return 0.0

        self.own.update(x=8.0, y=0.0)
        with mock.patch.object(acoustics, "detection_probability", return_value=0.0):
            engine.opponent_step(self.state, actor, Zero(self.game["seed"], []), False)
        self.assertEqual(actor["inventory"]["diesel_heavyweight"], held - 1)
        shot = self.state["engagements"][-1]
        self.assertEqual(shot["outcome"], "running")
        self.assertIsNone(shot["draw"])
        run = self.state["weapon_runs"][-1]
        self.assertAlmostEqual(run["course"], engine.bearing(actor, actor["last_heard"]), places=5)
        for other in self.state["actors"]:
            other.update(x=40.0, y=-40.0)
        run["integrate"] = True
        starts = engine._position_snapshot(self.state)
        engine.advance_weapon_runs(self.state, Zero(self.game["seed"], []), starts, starts)
        self.assertNotIn("casualty", self.own)
        self.assertEqual(len(self.state["actors"]), 4)

    def test_stale_or_absent_belief_does_not_fire(self):
        actor = engine.make_entity(
            DART,
            id="hunter",
            x=1.0,
            y=0.0,
            course=90,
            speed=4,
            depth=200,
            aware=True,
            evaded=True,
            doctrine="engage",
            last_heard={"x": self.own["x"], "y": self.own["y"], "time": self.state["t"] - 20, "source": "acoustic"},
        )
        held = actor["inventory"]["diesel_heavyweight"]
        with mock.patch.object(acoustics, "detection_probability", return_value=0.0):
            engine.opponent_step(self.state, actor, engine.Dice(self.game["seed"], []), False)
        self.assertEqual(actor["inventory"]["diesel_heavyweight"], held)
        self.assertFalse(any(
            entry["event"].startswith("weapon:") for entry in self.state["rng_trace"]
        ))
        actor["aware"] = False
        actor["last_heard"] = None
        engine.opponent_step(self.state, actor, engine.Dice(self.game["seed"], []), False)
        self.assertEqual(actor["inventory"]["diesel_heavyweight"], held)

    def test_a_decoy_replaces_the_fix_the_opponent_keeps(self):
        actor = engine.make_entity(
            DART,
            id="listener",
            x=3.0,
            y=1.0,
            course=90,
            speed=4,
            depth=250,
            aware=False,
            evaded=True,
            doctrine="avoid",
            last_heard=None,
        )
        self.state["countermeasures_active"].append({
            "effect": "seduce",
            "weapon": "mobile_decoy",
            "x": 12.0,
            "y": 9.0,
            "until": self.state["t"] + 15,
            "seduce_probability": 0.70,
        })

        class Zero:
            def __init__(self):
                self.trace = []

            def u(self, label):
                self.trace.append({"event": label, "u": 0.0})
                return 0.0

            def between(self, label, low, high):
                return low

        with mock.patch.object(acoustics, "detection_probability", return_value=1.0):
            engine.opponent_step(self.state, actor, Zero(), False)
        self.assertEqual(actor["last_heard"]["source"], "decoy")
        self.assertEqual((actor["last_heard"]["x"], actor["last_heard"]["y"]), (12.0, 9.0))
        self.assertNotEqual(
            (actor["last_heard"]["x"], actor["last_heard"]["y"]),
            (self.own["x"], self.own["y"]),
        )
        self.assertEqual(actor["belief"]["source"], "decoy")
        self.assertEqual(self.state["engagements"][-1]["kind"], "seduction")

    def test_passage_warship_does_not_search_or_shoot(self):
        ship = next(
            actor for actor in self.state["actors"]
            if engine.entity_spec(actor).category == engine.EntityCategory.SURFACE_WARSHIP
        )
        ship.update(x=1.0, y=0.0, aware=True, last_heard={
            "x": self.own["x"], "y": self.own["y"], "time": self.state["t"], "source": "acoustic",
        })
        held = ship["inventory"]["lightweight_round"]
        before = len(self.state["rng_trace"])
        engine.opponent_step(self.state, ship, engine.Dice(self.game["seed"], self.state["rng_trace"]), False)
        self.assertEqual(ship["inventory"]["lightweight_round"], held)
        self.assertEqual(before, len(self.state["rng_trace"]))

    def test_radio_intercept_records_a_bearing_fix_at_the_published_range(self):
        ship = next(
            actor for actor in self.state["actors"]
            if engine.entity_spec(actor).category == engine.EntityCategory.SURFACE_WARSHIP
        )
        ship.update(x=1.0, y=0.0, aware=False, last_heard=None)
        self.own.update(x=0.0, y=0.0)
        self.state["radio_reliability"] = 0.9
        mode = KESTREL.link_mode("mast_transmit")
        engine.apply_radio_exposure(self.state, mode, 0.0)
        heard = ship["last_heard"]
        self.assertEqual(heard["source"], "radio_intercept")
        self.assertAlmostEqual(
            engine.distance(ship, heard), mode.intercept_range_nm, places=6
        )
        self.assertGreater(engine.distance(self.own, heard), 1.0)
        self.assertEqual(ship["belief"]["assessment"], "contact_held")
        self.assertEqual(ship["observations"][-1]["source"], "radio_intercept")

    def test_mask_reduces_a_later_incoming_round(self):
        rule = DART.employment("diesel_heavyweight")
        bare = engine.terminal_probabilities(
            0.0,
            effect_radius_nm=rule.lethal_radius_nm,
            destruction_radius_nm=rule.destruction_radius_nm,
            destruction_probability=rule.destruction_probability,
            mobility_probability=rule.mobility_probability,
            dud_probability=rule.dud_probability,
        )
        masked = engine.terminal_probabilities(
            0.0,
            effect_radius_nm=rule.lethal_radius_nm,
            destruction_radius_nm=rule.destruction_radius_nm,
            destruction_probability=rule.destruction_probability,
            mobility_probability=rule.mobility_probability,
            dud_probability=rule.dud_probability,
            mask_penalty=0.45,
        )
        draw = (sum(bare.values()) + sum(masked.values())) / 2
        self.assertLess(draw, sum(bare.values()))
        self.assertGreater(draw, sum(masked.values()))
        self.own.update(x=100.0, y=100.0)

        def planted(host):
            host["weapon_runs"] = [{
                "id": "W99",
                "status": "running",
                "weapon": rule.identifier,
                "shooter": "hunter",
                "x": 99.95,
                "y": 100.0,
                "course": 90.0,
                "speed": rule.run_speed_knots,
                "distance_run_nm": rule.minimum_range_nm,
                "maximum_range_nm": rule.maximum_range_nm,
                "minimum_range_nm": rule.minimum_range_nm,
                "seeker_range_nm": rule.seeker_range_nm,
                "effect_radius_nm": rule.lethal_radius_nm,
                "destruction_radius_nm": rule.destruction_radius_nm,
                "destruction_probability": rule.destruction_probability,
                "mobility_probability": rule.mobility_probability,
                "dud_probability": rule.dud_probability,
                "target_label": "own-ship",
                "order_id": None,
                "basis": [],
                "contact_report_ids": [],
                "aim_used_measured_range": None,
                "within_patrol_authorization": True,
                "integrate": True,
                "launched_at": host["t"],
                "track": [],
            }]

        class Fixed:
            def __init__(self):
                self.trace = []

            def u(self, label):
                self.trace.append({"event": label, "u": draw})
                return draw

        planted(self.state)
        starts = engine._position_snapshot(self.state)
        engine.advance_weapon_runs(self.state, Fixed(), starts, starts)
        self.assertIn("casualty", self.own)
        fresh = engine.initialize("79" * 32)
        fresh["state"]["own"].update(x=100.0, y=100.0, mask_until=fresh["state"]["t"] + 10, mask_penalty=0.45)
        planted(fresh["state"])
        starts = engine._position_snapshot(fresh["state"])
        engine.advance_weapon_runs(fresh["state"], Fixed(), starts, starts)
        self.assertNotIn("casualty", fresh["state"]["own"])
        self.assertEqual(fresh["state"]["weapon_runs"][0]["status"], "running")


if __name__ == "__main__":
    unittest.main()
