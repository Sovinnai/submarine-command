"""Published scenarios keep their briefs public and their layouts hidden."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from submarine_command import engine, narrator, spectra
from submarine_command.platforms import BIOLOGIC, DART, EntityCategory
from submarine_command.scenarios import GLASS_MISSION, HARROW_BANK


DIESEL_SEED = f"{0:064x}"
SURFACE_SEED = f"{3:064x}"
BIOLOGIC_SEED = f"{5:064x}"
CAPPED_SEED = f"{27:064x}"
GLASS_SEED = "ab" * 32
HIDDEN_NAMES = ("Plover", "Nettle", "Gorse", "Halyard", "Cresset", "Chorus")


def listen_order(game, minutes=20, **overrides):
    order = {
        "id": f"listen-{len(game['events'])}",
        "expected_turn": len(game["events"]),
        "activity": "listen",
        "minutes": minutes,
        "course": 0,
        "speed": 4,
        "depth": 400,
        "operating_mode": "standard",
        "interrupt_on": [],
    }
    order.update(overrides)
    return order


def end_order(game):
    return {
        "id": "end-exercise",
        "expected_turn": len(game["events"]),
        "activity": "end",
    }


class GlassStraitRegressionTests(unittest.TestCase):
    def test_known_seed_keeps_the_corridor_layout(self):
        state = engine.new_world(GLASS_SEED)
        self.assertEqual(state["scenario"], "glass-strait")
        self.assertEqual(state["mission"], GLASS_MISSION)
        self.assertEqual(engine.world_clock(state), "0310")
        laid_out = [
            (actor["id"], actor["name"], round(actor["x"], 3), round(actor["y"], 3))
            for actor in state["actors"]
        ]
        self.assertEqual(laid_out, [
            ("actor-a", "Morrow", 7.351, -1.837),
            ("actor-2", "Larkspur", 25.266, 3.656),
            ("actor-3", "Bracken", 17.818, 10.036),
            ("actor-4", "Vigil", 25.854, -8.536),
        ])
        self.assertEqual(
            state["reports"][0]["text"],
            "0310. Local position (0 east, 0 north). Course 090, speed 5 knots, "
            "depth 400 feet. RELIEF lies 24 nautical miles east.",
        )
        self.assertEqual(state["bulletins"][0]["available"], 20)
        self.assertEqual(state["bulletins"][1]["available"], 120)

    def test_public_brief_and_debrief_keep_the_0800_station(self):
        game = engine.initialize(GLASS_SEED)
        public = engine.public_view(game)
        self.assertEqual(public["scenario"], "glass-strait")
        self.assertEqual(public["game"], "Operation Glass Strait")
        self.assertEqual(public["navigation"]["relief_station"], "RELIEF")
        self.assertEqual(public["navigation"]["relief_distance_nm"], 24)
        self.assertNotIn("simplifications", public["mission"])
        engine.apply_order(game, {
            "id": "end", "expected_turn": 0, "activity": "end",
        })
        outcomes = engine.debrief(game)["outcomes"]
        self.assertIn("at_relief_at_0800", outcomes)
        self.assertIsNone(outcomes["at_relief_at_0800"])
        self.assertEqual(outcomes["relief_station"], "RELIEF")


class HarrowBankTests(unittest.TestCase):
    def test_unknown_scenario_is_rejected_before_a_world_is_drawn(self):
        with self.assertRaises(ValueError) as caught:
            engine.initialize(scenario="not-a-patrol")
        self.assertIn("harrow-bank", str(caught.exception))
        self.assertIn("glass-strait", str(caught.exception))

    def test_opening_brief_hides_the_layout(self):
        game = engine.initialize(DIESEL_SEED, HARROW_BANK)
        public = engine.public_view(game)
        history = engine.public_history(game)
        catalog = json.dumps(engine.capability_report())
        visible = json.dumps({"public": public, "history": history})
        self.assertEqual(public["game"], "Operation Harrow Bank")
        self.assertEqual(public["time"], "0210")
        self.assertEqual(public["navigation"]["relief_station"], "SOUTHING")
        self.assertEqual(public["navigation"]["relief_distance_nm"], 8.49)
        self.assertEqual(public["navigation"]["relief_bearing_true"], 225)
        self.assertIn("mast does not report", public["mission"]["simplifications"])
        for name in HIDDEN_NAMES:
            self.assertNotIn(name, visible)
            self.assertNotIn(name, catalog)
        self.assertNotIn("Fisheries patrol reports", visible)
        self.assertNotIn("harrow:case", visible)
        self.assertNotIn("Plover", catalog)
        diesel = next(actor for actor in game["state"]["actors"] if actor["spec"] == DART.identifier)
        self.assertNotIn(str(diesel["resources"]["battery_energy"]), visible)

    def test_same_seed_replays_and_a_different_scenario_does_not(self):
        first = engine.initialize(DIESEL_SEED, HARROW_BANK)
        second = engine.initialize(DIESEL_SEED, HARROW_BANK)
        glass = engine.initialize(DIESEL_SEED)
        self.assertEqual(engine.canonical(first["initial_state"]), engine.canonical(second["initial_state"]))
        self.assertNotEqual(first["initial_state"]["mission"]["title"], glass["initial_state"]["mission"]["title"])
        engine.apply_order(first, listen_order(first, minutes=10, id="first-leg"))
        self.assertTrue(engine.verify(first)["verified"])

    def test_warship_starts_outside_intercept_range_and_inside_it_on_the_bank(self):
        for index in range(20):
            state = engine.new_world(f"{index:064x}", HARROW_BANK)
            warship = next(
                actor for actor in state["actors"]
                if engine.entity_spec(actor).category == EntityCategory.SURFACE_WARSHIP
            )
            self.assertGreater(engine.distance(state["own"], warship), 15)
            self.assertLessEqual(engine.distance({"x": 8, "y": 6}, warship), 15)

    def test_each_layout_is_drawn_and_only_the_diesel_layout_is_submerged(self):
        counts = {"diesel": 0, "biologic": 0, "surface": 0}
        for index in range(120):
            state = engine.new_world(f"{index:064x}", HARROW_BANK)
            primary = state["actors"][0]
            submerged = [
                actor for actor in state["actors"]
                if engine.entity_spec(actor).category == EntityCategory.DIESEL_SUBMARINE
            ]
            if primary["spec"] == DART.identifier:
                counts["diesel"] += 1
                self.assertEqual(len(submerged), 1)
                battery = primary["resources"]["battery_energy"]
                self.assertGreater(battery, 24)
                self.assertLessEqual(battery, 32)
                self.assertGreaterEqual(primary["speed"], 6)
                self.assertLessEqual(primary["speed"], 7)
            elif primary["spec"] == BIOLOGIC.identifier:
                counts["biologic"] += 1
                self.assertEqual(submerged, [])
            else:
                counts["surface"] += 1
                self.assertEqual(submerged, [])
                self.assertEqual(
                    engine.entity_spec(primary).category,
                    EntityCategory.FISHING_VESSEL,
                )
        self.assertGreater(counts["diesel"], 30)
        self.assertGreater(counts["biologic"], 20)
        self.assertGreater(counts["surface"], 15)

    def test_diesel_reaches_snorkeling_before_the_deadline_without_a_visual_mast_contact(self):
        game = engine.initialize(CAPPED_SEED, HARROW_BANK)
        diesel = next(actor for actor in game["state"]["actors"] if actor["spec"] == DART.identifier)
        before = {line["family"] for line in spectra.emitted_components(diesel)["lines"]}
        self.assertIn("electric_motor", before)
        self.assertNotIn("snorkel_diesel", before)
        while diesel["operating_mode"] != "snorkeling":
            self.assertLess(game["state"]["t"], 260)
            engine.apply_order(game, listen_order(game))
            diesel = next(actor for actor in game["state"]["actors"] if actor["spec"] == DART.identifier)
        self.assertLess(game["state"]["t"], 260)
        self.assertEqual(diesel["depth"], 50)
        self.assertLessEqual(diesel["speed"], 7)
        after = {line["family"] for line in spectra.emitted_components(diesel)["lines"]}
        self.assertIn("snorkel_diesel", after)
        self.assertNotIn("electric_motor", after)
        for actor in game["state"]["actors"]:
            if actor["id"] != diesel["id"]:
                actor["x"] += 40
        game["state"]["own"]["x"] = diesel["x"]
        game["state"]["own"]["y"] = diesel["y"] + 1
        seen = engine.mast_observations(
            game["state"], engine.Dice(game["seed"], game["state"]["rng_trace"])
        )
        self.assertEqual(seen, 0)
        self.assertFalse(any(
            track["actor"] == diesel["id"] and track.get("receiver_kind") == "visual"
            for track in game["state"]["tracks"]
        ))

    def test_deadline_and_presence_score_follow_the_harrow_schedule(self):
        game = engine.initialize(SURFACE_SEED, HARROW_BANK)
        while not any(report["category"] == "deadline" for report in game["state"]["reports"]):
            self.assertLess(game["state"]["t"], 290)
            engine.apply_order(game, listen_order(game, minutes=20))
        self.assertFalse(game["state"]["ended"])
        deadline = [
            report for report in game["state"]["reports"] if report["category"] == "deadline"
        ]
        self.assertEqual(deadline[0]["text"], "0630 assessment deadline reached.")
        self.assertEqual(deadline[0]["time"], "0630")
        engine.apply_order(game, end_order(game))
        brief = engine.debrief(game)
        self.assertEqual(brief["scenario"], HARROW_BANK)
        self.assertNotIn("at_relief_at_0800", brief["outcomes"])
        self.assertEqual(brief["outcomes"]["relief_station"], "SOUTHING")
        self.assertIsNone(brief["outcomes"]["at_relief_station"])
        relief = brief["initial_state"]["schedule"]["relief"]
        self.assertEqual((relief["x"], relief["y"], relief["radius_nm"]), (2.0, -12.0, 3.0))
        self.assertNotEqual(
            engine.contact_kind(brief["initial_state"]["actors"][0]),
            "submerged",
        )

    def test_cli_rejects_an_unknown_scenario_without_creating_a_save(self):
        with tempfile.TemporaryDirectory() as folder:
            session = Path(folder) / "patrol"
            result = subprocess.run(
                [sys.executable, "-m", "submarine_command",
                 "--session", str(session), "init", "--scenario", "nope"],
                check=False, capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("Unknown scenario", result.stdout)
            self.assertFalse((session / "private.json").exists())


class NarratorScenarioTests(unittest.TestCase):
    def test_start_keeps_the_named_scenario_on_retry(self):
        with tempfile.TemporaryDirectory() as folder:
            store = narrator.SessionStore(Path(folder))
            key = "b2" * 16
            started = store.start(key, HARROW_BANK)
            self.assertEqual(started["status"]["scenario"], HARROW_BANK)
            self.assertEqual(store.start(key, HARROW_BANK), started)
            self.assertEqual(store.start(key)["status"]["scenario"], HARROW_BANK)
            with self.assertRaises(narrator.NarratorError) as caught:
                store.start(key, "glass-strait")
            self.assertEqual(caught.exception.code, "invalid_request")
            request = json.dumps({
                "v": 1,
                "request_id": "bad-scenario",
                "op": "start",
                "params": {"idempotency_key": "c3" * 16, "scenario": "nope"},
            }).encode()
            response = narrator.dispatch(store, request)
            self.assertFalse(response["ok"])
            self.assertEqual(response["error"]["code"], "invalid_request")
            self.assertTrue(response["error"]["action_not_committed"])
