import copy
import json
import unittest
from unittest import mock

from submarine_command import acoustics, engine
from submarine_command.platforms import DART, KESTREL, WARSHIP


class WeaponRuleTests(unittest.TestCase):
    def test_hit_probability_follows_range_aim_error_and_mask(self):
        rule = KESTREL.employment("exercise_heavyweight")
        near = engine.employment_probability(rule, rule.minimum_range_nm, 0.0, 0.0)
        far = engine.employment_probability(rule, rule.maximum_range_nm, 0.0, 0.0)
        self.assertGreater(near, far)
        self.assertEqual(
            engine.employment_probability(rule, rule.maximum_range_nm + 0.1, 0.0),
            0.0,
        )
        self.assertEqual(
            engine.employment_probability(rule, 2.0, rule.lethal_radius_nm + 0.01),
            0.0,
        )
        self.assertLess(
            engine.employment_probability(rule, 2.0, 0.0, rule.mask_penalty or 0.45),
            engine.employment_probability(rule, 2.0, 0.0, 0.0),
        )

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
            **kwargs,
        }

    def test_unauthorized_offensive_order_changes_nothing(self):
        before = engine.canonical(self.game)
        inventory = copy.deepcopy(self.game["state"]["own"]["inventory"])
        with self.assertRaises(ValueError) as raised:
            engine.apply_order(self.game, self.order(
                weapon="exercise_heavyweight", target="S99", depth=200,
            ))
        self.assertIn("not authorized", str(raised.exception))
        self.assertEqual(before, engine.canonical(self.game))
        self.assertEqual(inventory, self.game["state"]["own"]["inventory"])
        self.assertEqual(self.game["events"], [])

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
        }
        raw.update(kwargs)
        return raw

    def test_hit_and_miss_are_deterministic_and_persistent(self):
        twin = engine.initialize("68" * 32)
        twin["state"]["authorization"]["offensive_weapons"] = True
        twin["state"]["own"]["depth"] = 200
        twin["state"]["ordered"]["depth"] = 200
        twin["state"]["tracks"].append(copy.deepcopy(self.game["state"]["tracks"][-1]))
        with mock.patch.object(engine, "employment_probability", return_value=0.0):
            engine.apply_order(self.game, self.order(id="miss"))
            engine.apply_order(twin, self.order(id="miss", expected_turn=0))
        self.assertEqual(self.game["state"]["engagements"], twin["state"]["engagements"])
        self.assertNotIn("casualty", self.game["state"]["actors"][0])
        before_ids = [actor["id"] for actor in self.game["state"]["actors"]]
        with mock.patch.object(engine, "employment_probability", return_value=1.0):
            engine.apply_order(self.game, self.order(id="hit"))
        target = self.game["state"]["actors"][0]
        self.assertEqual(target["casualty"]["effect"], "mobility_casualty")
        self.assertEqual(len(self.game["state"]["actors"]), len(before_ids))
        frozen = (target["x"], target["y"], copy.deepcopy(target["casualty"]))
        listen = self.order(
            id="listen", activity="listen", depth=200,
        )
        del listen["weapon"]
        del listen["target"]
        del listen["basis"]
        engine.apply_order(self.game, listen)
        self.assertEqual((target["x"], target["y"]), frozen[:2])
        self.assertEqual(target["casualty"], frozen[2])
        self.assertEqual(target["speed"], 0.0)

    def test_debrief_separates_decision_evidence_luck_and_engine_errors(self):
        with mock.patch.object(engine, "employment_probability", return_value=1.0):
            engine.apply_order(self.game, self.order())
        adjudication = engine._adjudication(self.game)
        self.assertEqual(
            set(adjudication),
            {"decision_quality", "observed_evidence", "luck", "engine_errors"},
        )
        self.assertEqual(adjudication["engine_errors"], [])
        decision = adjudication["decision_quality"][0]
        self.assertEqual(decision["judgment"], "cited_contact_evidence")
        self.assertNotIn("draw", decision)
        self.assertNotIn("probability", decision)
        self.assertEqual(adjudication["observed_evidence"][0]["basis"], self.order()["basis"])
        self.assertEqual(adjudication["luck"][0]["outcome"], "detonation")
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

        with mock.patch.object(acoustics, "detection_probability", return_value=0.0):
            engine.opponent_step(self.state, actor, Zero(self.game["seed"], []), False)
        self.assertNotIn("casualty", self.own)
        self.assertEqual(actor["inventory"]["diesel_heavyweight"], held - 1)
        shot = self.state["engagements"][-1]
        self.assertEqual(shot["outcome"], "no_detonation")
        self.assertEqual(shot["probability"], 0.0)
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
            last_heard={"x": self.own["x"], "y": self.own["y"], "time": self.state["t"], "source": "acoustic"},
        )
        bare = engine.employment_probability(rule, 1.0, 0.0, 0.0)
        masked = engine.employment_probability(rule, 1.0, 0.0, 0.45)
        self.assertGreater(bare, 0.5)
        self.assertLess(masked, 0.5)

        class Half:
            def __init__(self):
                self.trace = []

            def u(self, label):
                value = 0.5 if label.startswith("weapon:") else 0.99
                self.trace.append({"event": label, "u": value})
                return value

            def between(self, label, low, high):
                return low

        with mock.patch.object(acoustics, "detection_probability", return_value=0.0):
            engine.opponent_step(self.state, actor, Half(), False)
        self.assertIn("casualty", self.own)
        fresh = engine.initialize("79" * 32)
        fresh["state"]["own"]["mask_until"] = fresh["state"]["t"] + 10
        fresh["state"]["own"]["mask_penalty"] = 0.45
        actor["inventory"]["diesel_heavyweight"] += 1
        actor["casualty"] = None
        # The shooter object still carries the previous busy timer and the spent round.
        actor["weapon_busy_until"] = 0
        actor["inventory"]["diesel_heavyweight"] = 8
        if "casualty" in actor:
            del actor["casualty"]
        with mock.patch.object(acoustics, "detection_probability", return_value=0.0):
            engine.opponent_step(fresh["state"], actor, Half(), False)
        self.assertNotIn("casualty", fresh["state"]["own"])
        self.assertEqual(fresh["state"]["engagements"][-1]["outcome"], "no_detonation")


if __name__ == "__main__":
    unittest.main()
