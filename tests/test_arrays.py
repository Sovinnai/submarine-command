"""Array coverage, deployment state and observation provenance."""
import unittest

from submarine_command import arrays, engine as e
from submarine_command.platforms import KESTREL


class CoverageTests(unittest.TestCase):
    def test_hull_aft_baffle_and_bow_are_distinct(self):
        self.assertEqual(arrays.coverage_status(arrays.HULL, 0.0), arrays.IN_BEAM)
        self.assertEqual(arrays.coverage_status(arrays.HULL, 180.0), arrays.BAFFLE)
        self.assertFalse(arrays.keeps_observation(arrays.BAFFLE))

    def test_flank_hears_the_beam_and_not_the_bow(self):
        self.assertEqual(arrays.coverage_status(arrays.FLANK, 90.0), arrays.IN_BEAM)
        self.assertEqual(arrays.coverage_status(arrays.FLANK, 270.0), arrays.IN_BEAM)
        self.assertEqual(arrays.coverage_status(arrays.FLANK, 0.0), arrays.BAFFLE)
        self.assertEqual(arrays.coverage_status(arrays.FLANK, 180.0), arrays.BAFFLE)

    def test_towed_endfire_is_not_a_baffle(self):
        self.assertEqual(arrays.coverage_status(arrays.TOWED, 90.0), arrays.IN_BEAM)
        self.assertEqual(arrays.coverage_status(arrays.TOWED, 0.0), arrays.ENDFIRE)
        self.assertEqual(arrays.coverage_status(arrays.TOWED, 180.0), arrays.ENDFIRE)
        self.assertTrue(arrays.keeps_observation(arrays.ENDFIRE))
        self.assertEqual(
            arrays.coverage_status(arrays.TOWED, 90.0, unstable=True),
            arrays.UNSTABLE,
        )
        self.assertFalse(arrays.keeps_observation(arrays.UNSTABLE))

    def test_towed_depth_is_an_offset_not_a_cable(self):
        self.assertEqual(arrays.receiver_depth_feet(arrays.HULL, 400.0, 2400.0), 400.0)
        self.assertEqual(arrays.receiver_depth_feet(arrays.TOWED, 400.0, 2400.0), 520.0)
        self.assertGreater(arrays.receiver_depth_feet(arrays.TOWED, 60.0, 2400.0), 60.0)
        self.assertLess(arrays.receiver_depth_feet(arrays.TOWED, 900.0, 940.0), 940.0)
        self.assertGreaterEqual(arrays.receiver_depth_feet(arrays.TOWED, 60.0, 2400.0), 40.0)

    def test_frequency_response_splits_the_receivers(self):
        low = 80.0
        high = 2500.0
        self.assertGreater(
            arrays.frequency_gain_db(arrays.TOWED, low),
            arrays.frequency_gain_db(arrays.HULL, low),
        )
        self.assertGreater(
            arrays.frequency_gain_db(arrays.HULL, high),
            arrays.frequency_gain_db(arrays.TOWED, high),
        )

    def test_pump_fault_couples_more_to_hull_than_towed(self):
        hull = arrays.self_noise_adjustment_db(arrays.HULL, 5.0, pump_fault=True)
        towed = arrays.self_noise_adjustment_db(arrays.TOWED, 5.0, pump_fault=True)
        self.assertGreater(hull, towed)


class DeploymentTests(unittest.TestCase):
    def order(self, game, **kwargs):
        own = game["state"]["own"]
        return {
            "id": kwargs.pop("id", "arr"),
            "expected_turn": len(game["events"]),
            "activity": "listen",
            "minutes": 15,
            "interrupt_on": [],
            "course": own["course"],
            "speed": own["speed"],
            "depth": own["depth"],
            "operating_mode": own["operating_mode"],
            **kwargs,
        }

    def test_stream_and_recover_take_published_time_and_speed(self):
        game = e.initialize("ab" * 32)
        public = KESTREL.public_capabilities()["sonar"]
        self.assertTrue(public["towed_array_modeled"])
        self.assertEqual(public["deployment"]["deployment_minutes"], 15)
        self.assertEqual(public["deployment"]["retrieval_minutes"], 10)
        with self.assertRaises(ValueError):
            e.validate_order(
                self.order(game, activity="stream_array", speed=12, id="fast"),
                game["state"],
            )
        result = e.apply_order(
            game, self.order(game, activity="stream_array", speed=6, minutes=20, id="stream")
        )
        self.assertEqual(game["state"]["towed"]["deployment"], arrays.STREAMED)
        self.assertEqual(result["last_execution"]["stop_reason"], "task_complete")
        self.assertEqual(game["state"]["own"]["equipment"]["towed_array"], arrays.STREAMED)
        self.assertTrue(
            any(row["id"] == "towed_array" and row["listening"] for row in result["own_ship"]["sonar_receivers"])
        )
        with self.assertRaises(ValueError):
            e.validate_order(
                self.order(game, speed=15, id="sprint"),
                game["state"],
            )
        recovered = e.apply_order(
            game, self.order(game, activity="recover_array", speed=6, minutes=15, id="recover")
        )
        self.assertEqual(game["state"]["towed"]["deployment"], arrays.STOWED)
        self.assertEqual(recovered["last_execution"]["stop_reason"], "task_complete")
        self.assertFalse(
            any(row["id"] == "towed_array" and row["listening"] for row in recovered["own_ship"]["sonar_receivers"])
        )

    def test_unsupported_towed_orders_do_not_mutate(self):
        game = e.initialize("cd" * 32)
        before = e.canonical(game)
        with self.assertRaises(ValueError):
            e.apply_order(game, self.order(game, activity="recover_array", speed=6, id="early"))
        with self.assertRaises(ValueError):
            e.apply_order(game, self.order(game, activity="stream_array", speed=18, id="loud"))
        self.assertEqual(before, e.canonical(game))

    def test_partial_stream_progress_is_kept(self):
        game = e.initialize("ef" * 32)
        e.apply_order(
            game,
            self.order(game, activity="stream_array", speed=6, minutes=5, id="part"),
        )
        self.assertEqual(game["state"]["towed"]["deployment"], arrays.STREAMING)
        self.assertEqual(game["state"]["towed"]["progress_minutes"], 5)
        e.apply_order(
            game,
            self.order(game, activity="stream_array", speed=6, minutes=15, id="finish"),
        )
        self.assertEqual(game["state"]["towed"]["deployment"], arrays.STREAMED)

    def test_turn_marks_towed_unstable_without_cable_geometry(self):
        game = e.initialize("aa" * 32)
        e.apply_order(
            game, self.order(game, activity="stream_array", speed=6, minutes=20, id="stream")
        )
        e.apply_order(
            game,
            self.order(game, course=180, speed=6, minutes=5, id="turn"),
        )
        self.assertGreater(game["state"]["towed"]["unstable_until"], game["state"]["t"] - 5)
        self.assertTrue(
            any(entry["category"] == "array_unstable" for entry in game["state"]["reports"])
        )


class ProvenanceTests(unittest.TestCase):
    def test_opening_contact_is_hull_and_names_error_sources(self):
        game = e.initialize("ab" * 32)
        view = e.public_view(game)
        contact = view["contacts"][0]
        self.assertEqual(contact["receiver"]["id"], "hull_array")
        self.assertEqual(contact["receiver"]["role"], "hull")
        observation = contact["observations"][0]
        self.assertEqual(observation["receiver"]["id"], "hull_array")
        sources = observation["error_sources"]
        self.assertEqual(sources["array_bearing_bias"], "hull_array")
        self.assertEqual(sources["self_noise"], arrays.OWN_SHIP_MOUNTED_NOISE)
        self.assertEqual(sources["shared_bearing_bias"], arrays.ENVIRONMENT_BEARING)
        self.assertIn("not automatically the same contact", sources["correlation"])
        self.assertNotIn("actor", contact)

    def test_hull_and_flank_are_separate_histories(self):
        game = e.initialize("ab" * 32)
        state = game["state"]
        actor = state["actors"][0]
        actor.update(x=0.5, y=0.0)
        state["own"].update(course=0.0, x=0.0, y=0.0)
        state["t"] = 5
        dice = e.Dice(game["seed"], state["rng_trace"])
        relative = arrays.relative_bearing_deg(0.0, e.bearing(state["own"], actor))
        self.assertEqual(arrays.coverage_status(arrays.FLANK, relative), arrays.IN_BEAM)
        self.assertEqual(arrays.coverage_status(arrays.HULL, relative), arrays.IN_BEAM)
        from unittest import mock
        from submarine_command import acoustics
        with mock.patch.object(acoustics, "detection_probability", return_value=0.99):
            e.observe_contact(state, actor, dice, array_spec=arrays.HULL)
            e.observe_contact(state, actor, dice, array_spec=arrays.FLANK)
        hull = e._track_for_receiver(state, actor["id"], "hull_array")
        flank = e._track_for_receiver(state, actor["id"], "flank_array")
        self.assertIsNotNone(flank)
        self.assertNotEqual(hull["id"], flank["id"])
        self.assertEqual(
            hull["observations"][-1]["error_sources"]["self_noise"],
            flank["observations"][-1]["error_sources"]["self_noise"],
        )
        self.assertNotEqual(
            hull["observations"][-1]["error_sources"]["array_bearing_bias"],
            flank["observations"][-1]["error_sources"]["array_bearing_bias"],
        )

    def test_reread_does_not_add_a_second_look(self):
        game = e.initialize("ab" * 32)
        state = game["state"]
        actor = state["actors"][0]
        actor.update(x=0.4, y=0.0)
        state["t"] = 5
        dice = e.Dice(game["seed"], state["rng_trace"])
        e.observe_contact(state, actor, dice, array_spec=arrays.HULL)
        track = e._track_for_receiver(state, actor["id"], "hull_array")
        before = len(track["observations"])
        before_trace = len(state["rng_trace"])
        e.observe_contact(state, actor, dice, array_spec=arrays.HULL)
        self.assertEqual(len(track["observations"]), before)
        self.assertEqual(len(state["rng_trace"]), before_trace)

    def test_focus_does_not_raise_another_receiver_of_the_same_emitter(self):
        game = e.initialize("ab" * 32)
        state = game["state"]
        hull = next(tr for tr in state["tracks"] if tr.get("receiver_id") == "hull_array")
        self.assertEqual(
            e._focus_directivity_db(state, {"id": hull["actor"]}, "focus", "hull_array"),
            -2.0,
        )
        state["focus"] = hull["id"]
        self.assertEqual(
            e._focus_directivity_db(state, {"id": hull["actor"]}, "focus", "hull_array"),
            2.0,
        )
        self.assertEqual(
            e._focus_directivity_db(state, {"id": hull["actor"]}, "focus", "flank_array"),
            -2.0,
        )


if __name__ == "__main__":
    unittest.main()
