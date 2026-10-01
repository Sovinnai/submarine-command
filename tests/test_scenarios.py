"""Published scenarios keep their briefs public and their layouts hidden."""
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from submarine_command import engine, narrator
from submarine_command.platforms import DART, EntityCategory
from submarine_command.scenarios import CINDER_ROAD, GLASS_MISSION, MILLER_LINE


GLASS_SEED = "ab" * 32
HIDDEN_NAMES = ("Wicket", "Trestle", "Murmur", "Picket")


def hold_order(game, minutes=60):
    """Stay on the west side of the line. Minimum speed is 2 knots."""
    return {
        "id": f"hold-{len(game['events'])}",
        "expected_turn": len(game["events"]),
        "activity": "listen",
        "minutes": minutes,
        "course": 180,
        "speed": 2,
        "depth": 400,
        "operating_mode": "quiet",
        "interrupt_on": [],
    }


def hours_until_line(actor):
    rate = math.sin(math.radians(actor["course"])) * actor["speed"]
    if rate >= -0.1:
        return None
    return actor["x"] / -rate


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


class MillerLineTests(unittest.TestCase):
    def test_unknown_scenario_is_rejected_before_a_world_is_drawn(self):
        with self.assertRaises(ValueError) as caught:
            engine.initialize(scenario="not-a-patrol")
        self.assertIn("miller-line", str(caught.exception))
        self.assertIn("glass-strait", str(caught.exception))

    def test_opening_brief_is_a_barrier_and_hides_the_crossing(self):
        game = engine.initialize(f"{0:064x}", MILLER_LINE)
        public = engine.public_view(game)
        visible = json.dumps({
            "public": public,
            "history": engine.public_history(game),
            "catalog": engine.capability_report(),
        })
        self.assertEqual(public["game"], "Operation Miller Line")
        self.assertEqual(public["time"], "0100")
        self.assertNotIn("relief_station", public["navigation"])
        self.assertEqual(public["navigation"]["barrier_line_east_nm"], 0)
        self.assertEqual(public["navigation"]["east_of_line_nm"], -10)
        self.assertEqual(public["navigation"]["minutes_remaining"], 360)
        self.assertIn("No relief station", public["reports_this_turn"][0]["text"])
        for name in HIDDEN_NAMES:
            self.assertNotIn(name, visible)
        self.assertNotIn("crosser_id", visible)
        self.assertNotIn("crossing_elapsed_minutes", visible)
        diesel = game["state"]["actors"][0]
        self.assertNotIn(f"{diesel['y']:.3f}", visible)
        self.assertNotIn(str(round(diesel["y"], 2)), visible)

    def test_same_seed_replays_and_glass_strait_is_a_different_mission(self):
        first = engine.initialize(f"{1:064x}", MILLER_LINE)
        second = engine.initialize(f"{1:064x}", MILLER_LINE)
        glass = engine.initialize(f"{1:064x}")
        self.assertEqual(
            engine.canonical(first["initial_state"]),
            engine.canonical(second["initial_state"]),
        )
        self.assertNotEqual(
            first["initial_state"]["mission"]["title"],
            glass["initial_state"]["mission"]["title"],
        )
        engine.apply_order(first, hold_order(first, minutes=10))
        self.assertTrue(engine.verify(first)["verified"])

    def test_the_submarine_crosses_one_sector_after_the_merchant_crosses_the_other(self):
        plot_right = 0
        plot_wrong = 0
        for index in range(40):
            state = engine.new_world(f"{index:064x}", MILLER_LINE)
            diesel = state["actors"][0]
            merchant = next(actor for actor in state["actors"] if actor["name"] == "Trestle")
            warship = next(
                actor for actor in state["actors"]
                if engine.entity_spec(actor).category == EntityCategory.SURFACE_WARSHIP
            )
            self.assertEqual(diesel["spec"], DART.identifier)
            self.assertGreaterEqual(abs(diesel["y"]), 6)
            self.assertLessEqual(abs(diesel["y"]), 16)
            self.assertLess(diesel["y"] * merchant["y"], 0)
            self.assertGreater(diesel["x"], 0)
            self.assertGreater(merchant["x"], 0)
            self.assertGreater(warship["x"], 0)
            self.assertIn(warship["course"], (0.0, 180.0))
            diesel_hours = hours_until_line(diesel)
            merchant_hours = hours_until_line(merchant)
            self.assertIsNotNone(diesel_hours)
            self.assertLess(merchant_hours, diesel_hours)
            self.assertLess(diesel_hours, 6)
            self.assertLess(merchant_hours, 1.5)
            self.assertGreater(diesel["resources"]["battery_energy"], 60)
            plot = state["bulletins"][0]["text"]
            self.assertNotIn(str(round(diesel["y"], 1)), plot)
            named_north = "north sector" in plot
            if named_north == (diesel["y"] > 0):
                plot_right += 1
            else:
                plot_wrong += 1
        self.assertGreater(plot_right, plot_wrong)
        self.assertGreater(plot_wrong, 4)

    def test_holding_the_west_side_records_the_crossing_and_not_a_station(self):
        game = engine.initialize(f"{4:064x}", MILLER_LINE)
        while not game["state"]["ended"]:
            engine.apply_order(game, hold_order(game))
        self.assertEqual(game["state"]["t"], 360)
        self.assertLess(game["state"]["own"]["x"], 0)
        brief = engine.debrief(game)
        outcomes = brief["outcomes"]
        self.assertEqual(outcomes["objective"], "barrier_line")
        self.assertNotIn("at_relief_station", outcomes)
        self.assertNotIn("assessment_sent_by_deadline", outcomes)
        self.assertFalse(outcomes["own_ship_crossed_the_line"])
        self.assertTrue(outcomes["crossed_the_line"])
        self.assertGreater(outcomes["crossing_elapsed_minutes"], 120)
        self.assertLess(outcomes["crossing_elapsed_minutes"], 360)
        crossing = outcomes["crossing_elapsed_minutes"]
        times = engine._acoustic_observation_times(game["state"], "actor-crosser")
        before = any(stamp <= crossing for stamp in times)
        self.assertEqual(outcomes["detected_at_or_before_crossing"], before)
        self.assertFalse(any(
            report["category"] == "deadline" for report in game["state"]["reports"]
        ))
        self.assertTrue(any(
            report["category"] == "exercise_end" and "0700" in report["text"]
            for report in game["state"]["reports"]
        ))

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


class CinderRoadTests(unittest.TestCase):
    def test_opening_brief_publishes_attack_geometry_and_hides_the_convoy(self):
        game = engine.initialize(f"{0:064x}", CINDER_ROAD)
        public = engine.public_view(game)
        visible = json.dumps({
            "public": public,
            "history": engine.public_history(game),
        })
        self.assertEqual(public["game"], "Operation Cinder Road")
        self.assertEqual(public["time"], "1600")
        self.assertEqual(public["navigation"]["attack_range_nm"], 3)
        self.assertEqual(public["navigation"]["escort_clear_nm"], 6)
        self.assertEqual(public["navigation"]["convoy_exit_east_nm"], 30)
        self.assertNotIn("relief_station", public["navigation"])
        self.assertFalse(public["own_ship"]["restrictions"]["employment_available"])
        self.assertIn("Weapon flight", public["own_ship"]["restrictions"]["exercise"])
        for name in ("Hasp", "Lanyard", "Brand", "Mote"):
            self.assertNotIn(name, visible)
        self.assertNotIn("escort_id", visible)
        self.assertNotIn("closest_merchant_nm", visible)
        guide = next(actor for actor in game["state"]["actors"] if actor["id"] == "actor-guide")
        self.assertNotIn(f"{guide['x']:.3f}", visible)

    def test_escort_side_decides_whether_the_same_range_is_an_attack(self):
        game = engine.initialize(f"{2:064x}", CINDER_ROAD)
        state = game["state"]
        guide = next(actor for actor in state["actors"] if actor["id"] == "actor-guide")
        escort = next(actor for actor in state["actors"] if actor["id"] == "actor-escort")
        side = 1.0 if escort["y"] > guide["y"] else -1.0
        state["own"]["x"] = guide["x"]
        state["own"]["y"] = guide["y"] - side * 2.5
        state["own"]["speed"] = 6
        state["own"]["depth"] = 300
        engine.update_convoy(state)
        self.assertTrue(state["convoy"]["achieved"])
        state["convoy"]["achieved"] = False
        state["convoy"]["attack_elapsed_minutes"] = None
        state["own"]["y"] = guide["y"] + side * 2.5
        engine.update_convoy(state)
        self.assertFalse(state["convoy"]["achieved"])
        self.assertLess(engine.distance(state["own"], escort), 6)

    def test_column_geometry_and_a_safe_side_approach_score_without_a_weapon(self):
        for index in range(20):
            state = engine.new_world(f"{index:064x}", CINDER_ROAD)
            guide = next(actor for actor in state["actors"] if actor["id"] == "actor-guide")
            trailer = next(actor for actor in state["actors"] if actor["id"] == "actor-trailer")
            escort = next(actor for actor in state["actors"] if actor["id"] == "actor-escort")
            screen = next(actor for actor in state["actors"] if actor["id"] == "actor-screen")
            self.assertGreater(guide["x"], trailer["x"])
            self.assertGreater(screen["x"], guide["x"])
            self.assertGreaterEqual(abs(escort["y"] - guide["y"]), 5)
            self.assertLessEqual(abs(escort["y"] - guide["y"]), 6.5)
            for actor in (guide, trailer, escort, screen):
                self.assertEqual(actor["course"], 90)
        game = engine.initialize(f"{5:064x}", CINDER_ROAD)
        while not game["state"]["ended"] and not game["state"]["convoy"]["achieved"]:
            state = game["state"]
            guide = next(actor for actor in state["actors"] if actor["id"] == "actor-guide")
            escort = next(actor for actor in state["actors"] if actor["id"] == "actor-escort")
            side = 1.0 if escort["y"] > guide["y"] else -1.0
            aim_x = guide["x"] + 6
            aim_y = guide["y"] - side * 2.2
            course = engine.bearing(state["own"], {"x": aim_x, "y": aim_y})
            gap = engine.distance(state["own"], {"x": aim_x, "y": aim_y})
            engine.apply_order(game, {
                "id": f"close-{len(game['events'])}",
                "expected_turn": len(game["events"]),
                "activity": "listen",
                "minutes": 20,
                "course": course,
                "speed": 6 if gap < 4 else 12,
                "depth": 300,
                "operating_mode": "standard",
                "interrupt_on": [],
            })
        self.assertTrue(game["state"]["convoy"]["achieved"])
        engine.apply_order(game, {
            "id": "end-cinder",
            "expected_turn": len(game["events"]),
            "activity": "end",
        })
        outcomes = engine.debrief(game)["outcomes"]
        self.assertEqual(outcomes["objective"], "convoy_attack_geometry")
        self.assertTrue(outcomes["attack_geometry_reached"])
        self.assertEqual(outcomes["weapon_flight"], "not resolved")
        self.assertLessEqual(outcomes["closest_merchant_nm"], 3)


class NarratorScenarioTests(unittest.TestCase):
    def test_start_keeps_the_named_scenario_on_retry(self):
        with tempfile.TemporaryDirectory() as folder:
            store = narrator.SessionStore(Path(folder))
            key = "b2" * 16
            started = store.start(key, MILLER_LINE)
            self.assertEqual(started["status"]["scenario"], MILLER_LINE)
            self.assertEqual(store.start(key, MILLER_LINE), started)
            self.assertEqual(store.start(key)["status"]["scenario"], MILLER_LINE)
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
