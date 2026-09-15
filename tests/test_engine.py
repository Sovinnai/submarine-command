import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from submarine_command import engine as e


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
                    self.assertFalse(set(obj) & {"seed", "actors", "actor", "kind", "rng_trace", "initial_state", "intent", "last_heard", "aware", "bearing_bias", "bulletins", "radio_reliability"})
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
        for minute in (5, 10, 15):
            state["t"] = minute
            # Increase audibility only in this throwaway world to get observations.
            primary["noise"] = 1000
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
        actor = state["actors"][0]
        actor["kind"] = "submerged"
        actor["aware"] = False
        actor["last_heard"] = None
        actor["evaded"] = False
        before = (actor["course"], actor["speed"])
        class NoDetection:
            def u(self, label):
                return 0.999999
        e.opponent_step(state, actor, NoDetection(), False)
        self.assertEqual(before, (actor["course"], actor["speed"]))
        self.assertFalse(actor["aware"])

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
