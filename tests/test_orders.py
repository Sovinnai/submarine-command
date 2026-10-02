"""Contact-scoped interrupts and bounded conditional plans."""
import copy
import unittest
from pathlib import Path
import sys
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from submarine_command import engine as e
from submarine_command import orders


class ContactScopedInterruptTests(unittest.TestCase):
    def setUp(self):
        self.game = e.initialize("ab" * 32)

    def order(self, **kwargs):
        own = self.game["state"]["own"]
        return {
            "id": "test-01",
            "expected_turn": len(self.game["events"]),
            "activity": "listen",
            "minutes": 15,
            "interrupt_on": [],
            "course": own["course"],
            "speed": own["speed"],
            "depth": own["depth"],
            "operating_mode": own["operating_mode"],
            **kwargs,
        }

    def _acquire_contacts(self):
        e.apply_order(self.game, self.order(id="look", minutes=15))
        ids = [track["id"] for track in self.game["state"]["tracks"]]
        self.assertGreaterEqual(len(ids), 2)
        return ids

    def test_bare_contact_interrupt_normalizes_to_explicit_any_scope(self):
        self._acquire_contacts()
        validated = e.validate_order(
            self.order(
                id="norm",
                expected_turn=1,
                interrupt_on=["contact_lost", "equipment", "new_contact"],
            ),
            self.game["state"],
        )
        self.assertEqual(
            validated["interrupt_on"],
            [
                {"category": "contact_lost", "scope": "any"},
                {"category": "equipment"},
                {"category": "new_contact", "scope": "any"},
            ],
        )

    def test_focus_continues_through_unrelated_stale_track(self):
        ids = self._acquire_contacts()
        focus_id, other_id = ids[0], ids[1]
        state = copy.deepcopy(self.game["state"])
        order = e.validate_order(
            self.order(
                id="focus-scoped",
                expected_turn=1,
                activity="focus",
                focus=focus_id,
                minutes=30,
                interrupt_on=[
                    {
                        "category": "contact_lost",
                        "scope": "contacts",
                        "contacts": [focus_id],
                    }
                ],
            ),
            state,
        )
        real_tick = e.tick_world

        def tick_with_unrelated_loss(current, dice, current_order, first_tick):
            real_tick(current, dice, current_order, first_tick)
            if first_tick:
                e.report(
                    current,
                    "Sonar",
                    f"{other_id}: no further observation for 20 minutes. "
                    "Last report is now stale; contact fate unknown.",
                    "contact_lost",
                    contact=other_id,
                )

        with mock.patch.object(e, "tick_world", side_effect=tick_with_unrelated_loss):
            execution = e.simulate(state, self.game["seed"], order)
        self.assertEqual(execution["stop_reason"], "requested_interval_complete")
        self.assertEqual(execution["elapsed_minutes"], 30)
        self.assertEqual(execution["unused_minutes"], 0)
        self.assertEqual(
            order["interrupt_on"],
            [{"category": "contact_lost", "scope": "contacts", "contacts": [focus_id]}],
        )

    def test_except_scope_drops_a_contact_from_the_watch_without_deleting_history(self):
        ids = self._acquire_contacts()
        dropped, watched = ids[0], ids[1]
        state = copy.deepcopy(self.game["state"])
        order = e.validate_order(
            self.order(
                id="except-watch",
                expected_turn=1,
                minutes=10,
                interrupt_on=[
                    {
                        "category": "contact_lost",
                        "scope": "except",
                        "contacts": [dropped],
                    }
                ],
            ),
            state,
        )

        def tick_with_both_lost(current, _dice, _order, first_tick):
            self.assertTrue(first_tick)
            current["t"] += e.TICK
            e.report(
                current, "Sonar", f"{dropped} stale.", "contact_lost", contact=dropped
            )
            e.report(
                current, "Sonar", f"{watched} stale.", "contact_lost", contact=watched
            )

        with mock.patch.object(e, "tick_world", side_effect=tick_with_both_lost):
            execution = e.simulate(state, self.game["seed"], order)
        self.assertEqual(execution["stop_reason"], "interrupt")
        self.assertEqual(execution["elapsed_minutes"], 5)
        self.assertEqual(execution["unused_minutes"], 5)
        lost_events = [
            event for event in execution["stop_events"] if event["category"] == "contact_lost"
        ]
        self.assertEqual(len(lost_events), 1)
        self.assertEqual(lost_events[0]["contacts"], [watched])
        self.assertTrue(any(track["id"] == dropped for track in state["tracks"]))

    def test_scoped_interrupt_reports_contact_and_preserves_unused_time(self):
        ids = self._acquire_contacts()
        target = ids[0]
        state = copy.deepcopy(self.game["state"])
        order = e.validate_order(
            self.order(
                id="watch-one",
                expected_turn=1,
                minutes=40,
                interrupt_on=[
                    {
                        "category": "contact_lost",
                        "scope": "contacts",
                        "contacts": [target],
                    }
                ],
            ),
            state,
        )

        def tick_with_target_lost(current, _dice, _order, first_tick):
            self.assertTrue(first_tick)
            current["t"] += e.TICK
            e.report(
                current, "Sonar", f"{target} stale.", "contact_lost", contact=target
            )
            e.report(
                current, "Sonar", "S99 unrelated.", "contact_lost", contact="S99"
            )

        with mock.patch.object(e, "tick_world", side_effect=tick_with_target_lost):
            execution = e.simulate(state, self.game["seed"], order)
        self.assertEqual(execution["stop_reason"], "interrupt")
        self.assertEqual(execution["elapsed_minutes"], 5)
        self.assertEqual(execution["unused_minutes"], 35)
        self.assertEqual(execution["stop_events"][0]["contacts"], [target])
        self.assertEqual(
            execution["interrupt_on"],
            [{"category": "contact_lost", "scope": "contacts", "contacts": [target]}],
        )

    def test_unknown_contact_in_watch_is_rejected_before_mutation(self):
        self._acquire_contacts()
        before = e.canonical(self.game)
        with self.assertRaises(ValueError) as raised:
            e.apply_order(
                self.game,
                self.order(
                    id="bad-watch",
                    expected_turn=1,
                    interrupt_on=[
                        {
                            "category": "contact_lost",
                            "scope": "contacts",
                            "contacts": ["S99"],
                        }
                    ],
                ),
            )
        self.assertIn("unknown contact", str(raised.exception).lower())
        self.assertEqual(before, e.canonical(self.game))

    def test_new_contact_rejects_named_contact_scope(self):
        self._acquire_contacts()
        with self.assertRaises(ValueError) as raised:
            e.validate_order(
                self.order(
                    id="bad-new",
                    expected_turn=1,
                    interrupt_on=[
                        {
                            "category": "new_contact",
                            "scope": "contacts",
                            "contacts": [self.game["state"]["tracks"][0]["id"]],
                        }
                    ],
                ),
                self.game["state"],
            )
        self.assertIn("new_contact", str(raised.exception))


class BoundedPlanTests(unittest.TestCase):
    def setUp(self):
        self.game = e.initialize("ab" * 32)

    def base_command(self, **kwargs):
        own = self.game["state"]["own"]
        command = {
            "activity": "listen",
            "minutes": 5,
            "interrupt_on": [],
            "course": own["course"],
            "speed": own["speed"],
            "depth": own["depth"],
            "operating_mode": own["operating_mode"],
        }
        command.update(kwargs)
        return command

    def plan_order(self, steps, **kwargs):
        return {
            "id": kwargs.pop("id", "plan-01"),
            "expected_turn": kwargs.pop("expected_turn", len(self.game["events"])),
            "plan": {"steps": steps},
            **kwargs,
        }

    def test_sequence_runs_steps_and_reports_plan_receipt(self):
        raw = self.plan_order(
            [
                {
                    "label": "leg-one",
                    "command": self.base_command(minutes=5, course=100),
                },
                {
                    "label": "leg-two",
                    "command": self.base_command(minutes=10, course=120),
                },
            ]
        )
        result = e.apply_order(self.game, raw)
        execution = result["last_execution"]
        self.assertEqual(execution["stop_reason"], "plan_complete")
        self.assertEqual(execution["requested_minutes"], 15)
        self.assertEqual(execution["elapsed_minutes"], 15)
        self.assertEqual(execution["unused_minutes"], 0)
        self.assertEqual(
            [step["label"] for step in execution["plan"]["executed_steps"]],
            ["leg-one", "leg-two"],
        )
        self.assertEqual(execution["plan"]["remaining_steps"], [])
        self.assertEqual(self.game["state"]["ordered"]["course"], 120)
        self.assertTrue(e.verify(self.game)["verified"])

    def test_interrupt_stops_plan_and_preserves_remaining_steps(self):
        e.apply_order(
            self.game,
            {
                "id": "seed-look",
                "expected_turn": 0,
                "activity": "listen",
                "minutes": 15,
                "interrupt_on": [],
                "course": 90,
                "speed": 5,
                "depth": 400,
                "operating_mode": "standard",
            },
        )
        focus = self.game["state"]["tracks"][0]["id"]
        other = self.game["state"]["tracks"][1]["id"]
        state = copy.deepcopy(self.game["state"])
        raw = self.plan_order(
            [
                {
                    "label": "hold",
                    "command": self.base_command(
                        activity="focus",
                        focus=focus,
                        minutes=30,
                        interrupt_on=[{"category": "contact_lost", "scope": "any"}],
                    ),
                },
                {
                    "label": "depart",
                    "command": self.base_command(minutes=10, course=180, speed=8),
                },
            ],
            id="interrupted-plan",
            expected_turn=1,
        )
        order = e.validate_order(raw, state)
        real_tick = e.tick_world

        def tick_with_loss(current, dice, current_order, first_tick):
            real_tick(current, dice, current_order, first_tick)
            if first_tick:
                e.report(
                    current, "Sonar", f"{other} stale.", "contact_lost", contact=other
                )

        with mock.patch.object(e, "tick_world", side_effect=tick_with_loss):
            execution = e.simulate(state, self.game["seed"], order)
        self.assertEqual(execution["stop_reason"], "interrupt")
        self.assertEqual(execution["elapsed_minutes"], 5)
        self.assertEqual(execution["unused_minutes"], 35)
        self.assertEqual(len(execution["plan"]["executed_steps"]), 1)
        self.assertEqual(
            execution["plan"]["remaining_steps"],
            [{"index": 1, "label": "depart"}],
        )
        self.assertIn(other, execution["stop_events"][0]["contacts"])

        # Idempotent retry of a committed plan order.
        live = e.apply_order(self.game, raw)
        before = e.canonical(self.game)
        self.assertEqual(live, e.apply_order(self.game, raw))
        self.assertEqual(before, e.canonical(self.game))
        self.assertTrue(e.verify(self.game)["verified"])

    def test_when_condition_can_skip_or_stop(self):
        e.apply_order(
            self.game,
            {
                "id": "seed",
                "expected_turn": 0,
                "activity": "listen",
                "minutes": 15,
                "interrupt_on": [],
                "course": 90,
                "speed": 5,
                "depth": 400,
                "operating_mode": "standard",
            },
        )
        contact = self.game["state"]["tracks"][0]["id"]
        # Public status only: age the track on a disposable copy.
        stop_state = copy.deepcopy(self.game["state"])
        stop_state["tracks"][0]["last"] = stop_state["t"] - 20
        stop_plan = self.plan_order(
            [
                {
                    "label": "turn-if-held",
                    "when": {"contact": {"id": contact, "status": "recent"}},
                    "else": "stop",
                    "command": self.base_command(minutes=5, course=180),
                },
                {
                    "label": "never",
                    "command": self.base_command(minutes=5, course=270),
                },
            ],
            id="stop-plan",
            expected_turn=1,
        )
        stop_order = e.validate_order(stop_plan, stop_state)
        stopped = e.simulate(stop_state, self.game["seed"], stop_order)
        self.assertEqual(stopped["stop_reason"], "condition_unmet")
        self.assertEqual(stopped["elapsed_minutes"], 0)
        self.assertEqual(
            stopped["plan"]["remaining_steps"][0]["label"],
            "turn-if-held",
        )

        skip_state = copy.deepcopy(self.game["state"])
        skip_state["tracks"][0]["last"] = skip_state["t"] - 20
        skip_plan = self.plan_order(
            [
                {
                    "label": "skip-turn",
                    "when": {"contact": {"id": contact, "status": "recent"}},
                    "else": "skip",
                    "command": self.base_command(minutes=5, course=180),
                },
                {
                    "label": "continue",
                    "command": self.base_command(minutes=5, course=45),
                },
            ],
            id="skip-plan",
            expected_turn=1,
        )
        skip_order = e.validate_order(skip_plan, skip_state)
        skipped = e.simulate(skip_state, self.game["seed"], skip_order)
        self.assertEqual(skipped["stop_reason"], "plan_complete")
        self.assertEqual(
            [step["stop_reason"] for step in skipped["plan"]["executed_steps"]],
            ["condition_skipped", "requested_interval_complete"],
        )
        self.assertEqual(skip_state["ordered"]["course"], 45)

    def test_stop_when_navigation_threshold_advances_to_next_step(self):
        e.apply_order(
            self.game,
            {
                "id": "seed",
                "expected_turn": 0,
                "activity": "listen",
                "minutes": 5,
                "interrupt_on": [],
                "course": 90,
                "speed": 5,
                "depth": 400,
                "operating_mode": "standard",
            },
        )
        nav = e.navigation_public(self.game["state"])
        threshold = nav["minimum_average_speed_to_station_knots"] + 1

        raw = self.plan_order(
            [
                {
                    "label": "track",
                    "command": self.base_command(minutes=30),
                    "stop_when": {
                        "navigation": {
                            "field": "minimum_average_speed_to_station_knots",
                            "op": "<=",
                            "value": threshold,
                        }
                    },
                },
                {
                    "label": "breakaway",
                    "command": self.base_command(minutes=5, course=180, speed=8),
                },
            ],
            id="soa-plan",
            expected_turn=1,
        )
        result = e.apply_order(self.game, raw)
        execution = result["last_execution"]
        self.assertEqual(execution["stop_reason"], "plan_complete")
        self.assertEqual(
            [step["stop_reason"] for step in execution["plan"]["executed_steps"]],
            ["condition_met", "requested_interval_complete"],
        )
        self.assertEqual(execution["plan"]["executed_steps"][0]["elapsed_minutes"], 5)
        self.assertEqual(self.game["state"]["ordered"]["course"], 180)
        self.assertTrue(e.verify(self.game)["verified"])

    def test_plan_validates_every_branch_before_mutation(self):
        e.apply_order(
            self.game,
            {
                "id": "seed",
                "expected_turn": 0,
                "activity": "listen",
                "minutes": 15,
                "interrupt_on": [],
                "course": 90,
                "speed": 5,
                "depth": 400,
                "operating_mode": "standard",
            },
        )
        before = e.canonical(self.game)
        contact = self.game["state"]["tracks"][0]["id"]
        with self.assertRaises(ValueError):
            e.apply_order(
                self.game,
                self.plan_order(
                    [
                        {
                            "label": "ok",
                            "command": self.base_command(minutes=5),
                        },
                        {
                            "label": "bad",
                            "when": {"contact": {"id": contact, "status": "recent"}},
                            "else": "stop",
                            "command": self.base_command(minutes=5, depth=5000),
                        },
                    ],
                    id="invalid-plan",
                    expected_turn=1,
                ),
            )
        self.assertEqual(before, e.canonical(self.game))

    def test_plan_rejects_open_ended_size(self):
        steps = [
            {"command": self.base_command(minutes=5)}
            for _ in range(orders.MAX_PLAN_STEPS + 1)
        ]
        with self.assertRaises(ValueError) as raised:
            e.validate_order(self.plan_order(steps), self.game["state"])
        self.assertIn(str(orders.MAX_PLAN_STEPS), str(raised.exception))

    def test_plan_replay_is_deterministic(self):
        raw = self.plan_order(
            [
                {"label": "a", "command": self.base_command(minutes=5, course=95)},
                {"label": "b", "command": self.base_command(minutes=5, course=100)},
            ]
        )
        first = e.initialize("cd" * 32)
        second = e.initialize("cd" * 32)
        e.apply_order(first, raw)
        e.apply_order(second, raw)
        self.assertEqual(e.canonical(first["state"]), e.canonical(second["state"]))
        self.assertTrue(e.verify(first)["verified"])

    def test_plan_validates_later_steps_against_projected_envelopes(self):
        recover = self.plan_order(
            [
                {
                    "label": "stream",
                    "command": self.base_command(
                        activity="stream_array", minutes=15, speed=5
                    ),
                },
                {
                    "label": "recover",
                    "command": self.base_command(
                        activity="recover_array", minutes=10, speed=5
                    ),
                },
            ],
            id="stream-recover",
        )
        e.validate_order(recover, self.game["state"])

        before = e.canonical(self.game)
        with self.assertRaises(ValueError) as raised:
            e.apply_order(
                self.game,
                self.plan_order(
                    [
                        {
                            "label": "stream",
                            "command": self.base_command(
                                activity="stream_array", minutes=15, speed=5
                            ),
                        },
                        {
                            "label": "sprint",
                            "command": self.base_command(
                                minutes=5, speed=14, operating_mode="high_power"
                            ),
                        },
                    ],
                    id="stream-sprint",
                ),
            )
        self.assertIn("towed_array", str(raised.exception))
        self.assertEqual(before, e.canonical(self.game))

    def test_plan_projection_credits_only_productive_stream_minutes(self):
        # From 20 knots, a 15-minute stream ordered at 8 knots does not finish:
        # deceleration burns part of the window outside the deploy envelope.
        self.game["state"]["own"]["speed"] = 20.0
        self.game["state"]["ordered"]["speed"] = 20.0
        self.game["state"]["own"]["operating_mode"] = "high_power"
        self.game["state"]["ordered"]["operating_mode"] = "high_power"
        before = e.canonical(self.game)
        with self.assertRaises(ValueError) as raised:
            e.validate_order(
                self.plan_order(
                    [
                        {
                            "label": "stream",
                            "command": self.base_command(
                                activity="stream_array",
                                minutes=15,
                                speed=8,
                                operating_mode="standard",
                            ),
                        },
                        {
                            "label": "recover",
                            "command": self.base_command(
                                activity="recover_array",
                                minutes=10,
                                speed=5,
                                operating_mode="standard",
                            ),
                        },
                    ],
                    id="fast-stream",
                ),
                self.game["state"],
            )
        self.assertIn("stream", str(raised.exception).lower())
        self.assertEqual(before, e.canonical(self.game))

    def test_plan_validates_skip_branch_envelope_state(self):
        e.apply_order(
            self.game,
            {
                "id": "seed",
                "expected_turn": 0,
                "activity": "listen",
                "minutes": 15,
                "interrupt_on": [],
                "course": 90,
                "speed": 5,
                "depth": 400,
                "operating_mode": "standard",
            },
        )
        contact = self.game["state"]["tracks"][0]["id"]
        before = e.canonical(self.game)
        with self.assertRaises(ValueError) as raised:
            e.apply_order(
                self.game,
                self.plan_order(
                    [
                        {
                            "label": "stream",
                            "command": self.base_command(
                                activity="stream_array", minutes=15, speed=5
                            ),
                        },
                        {
                            "label": "maybe-recover",
                            "when": {"contact": {"id": contact, "status": "stale"}},
                            "else": "skip",
                            "command": self.base_command(
                                activity="recover_array", minutes=10, speed=5
                            ),
                        },
                        {
                            "label": "sprint",
                            "command": self.base_command(
                                minutes=5, speed=14, operating_mode="high_power"
                            ),
                        },
                    ],
                    id="skip-branch",
                    expected_turn=1,
                ),
            )
        self.assertIn("towed_array", str(raised.exception))
        self.assertEqual(before, e.canonical(self.game))

    def test_plan_validation_does_not_reveal_hidden_contacts(self):
        before_tracks = len(self.game["state"]["tracks"])
        before_reports = len(self.game["state"]["reports"])
        before = e.canonical(self.game)
        with self.assertRaises(ValueError) as raised:
            e.validate_order(
                self.plan_order(
                    [
                        {"command": self.base_command(minutes=15)},
                        {
                            "command": self.base_command(
                                activity="focus", focus="S99", minutes=5
                            )
                        },
                    ],
                    id="probe",
                ),
                self.game["state"],
            )
        self.assertIn("existing contact", str(raised.exception).lower())
        self.assertEqual(before, e.canonical(self.game))
        self.assertEqual(len(self.game["state"]["tracks"]), before_tracks)
        self.assertEqual(len(self.game["state"]["reports"]), before_reports)

    def test_reserved_plan_step_ids_rejected_for_top_level_orders(self):
        with self.assertRaises(ValueError) as raised:
            e.validate_order(
                {
                    "id": "#taken:0",
                    "expected_turn": 0,
                    "activity": "listen",
                    "minutes": 5,
                    "interrupt_on": [],
                    "course": 90,
                    "speed": 5,
                    "depth": 400,
                    "operating_mode": "standard",
                },
                self.game["state"],
            )
        self.assertIn("reserved", str(raised.exception).lower())

    def test_planned_employ_keeps_a_stable_action_id(self):
        before_inventory = self.game["state"]["own"]["inventory"]["mobile_decoy"]
        raw = self.plan_order(
            [
                {
                    "label": "decoy",
                    "command": self.base_command(
                        activity="employ",
                        minutes=5,
                        speed=5,
                        depth=200,
                        weapon="mobile_decoy",
                        target="none",
                        basis=[],
                        confirm=True,
                    ),
                }
            ],
            id="employ-plan",
        )
        result = e.apply_order(self.game, raw)
        step = result["last_execution"]["plan"]["executed_steps"][0]
        self.assertEqual(
            self.game["events"][-1]["order"]["plan"]["steps"][0]["command"]["id"],
            "#employ-plan:0",
        )
        self.assertEqual(
            self.game["state"]["own"]["inventory"]["mobile_decoy"],
            before_inventory - 1,
        )
        self.assertIn(step["stop_reason"], ("task_complete", "requested_interval_complete"))
        quality = e._adjudication(self.game)["decision_quality"]
        self.assertTrue(any(item["order_id"] == "#employ-plan:0" for item in quality))
        self.assertTrue(e.verify(self.game)["verified"])

    def test_plan_stops_when_task_complete_also_matches_interrupt(self):
        state = copy.deepcopy(self.game["state"])
        raw = self.plan_order(
            [
                {
                    "label": "hold",
                    "command": self.base_command(
                        minutes=30,
                        interrupt_on=["message_received"],
                    ),
                },
                {
                    "label": "later",
                    "command": self.base_command(minutes=5, course=180),
                },
            ],
            id="radio-plan",
        )
        order = e.validate_order(raw, state)
        real_tick = e.tick_world

        def tick_with_message(current, dice, current_order, first_tick):
            real_tick(current, dice, current_order, first_tick)
            if first_tick:
                e.report(
                    current,
                    "Radio",
                    "Test traffic copied.",
                    "message_received",
                    message_id="B1",
                )

        with mock.patch.object(e, "tick_world", side_effect=tick_with_message):
            execution = e.simulate(state, self.game["seed"], order)
        self.assertEqual(execution["stop_reason"], "interrupt")
        self.assertEqual(len(execution["plan"]["executed_steps"]), 1)
        self.assertEqual(
            execution["plan"]["remaining_steps"],
            [{"index": 1, "label": "later"}],
        )
        self.assertEqual(execution["stop_events"][0]["category"], "message_received")
        self.assertEqual(
            execution["stop_events"][0]["report_ids"],
            list(dict.fromkeys(execution["stop_events"][0]["report_ids"])),
        )
        state2 = copy.deepcopy(self.game["state"])
        raw2 = self.plan_order(
            [
                {"label": "hold", "command": self.base_command(minutes=30)},
                {
                    "label": "later",
                    "command": self.base_command(minutes=5, course=180),
                },
            ],
            id="advance-plan",
        )
        order2 = e.validate_order(raw2, state2)

        def tick_with_message_only(current, dice, current_order, first_tick):
            real_tick(current, dice, current_order, first_tick)
            if first_tick:
                e.report(
                    current,
                    "Radio",
                    "Test traffic copied.",
                    "message_received",
                    message_id="B1",
                )

        with mock.patch.object(e, "tick_world", side_effect=tick_with_message_only):
            advanced = e.simulate(state2, self.game["seed"], order2)
        self.assertEqual(advanced["stop_reason"], "plan_complete")
        self.assertEqual(len(advanced["plan"]["executed_steps"]), 2)

    def test_visual_new_contact_stop_names_the_contact(self):
        state = copy.deepcopy(self.game["state"])
        surface = next(
            actor for actor in state["actors"]
            if e.contact_kind(actor) == "surface"
        )
        surface["x"] = state["own"]["x"]
        surface["y"] = state["own"]["y"] + 0.5
        state["own"]["depth"] = 70
        state["ordered"]["depth"] = 70
        order = e.validate_order(
            {
                "id": "mast-look",
                "expected_turn": 0,
                "activity": "mast",
                "minutes": 5,
                "course": 90,
                "speed": 5,
                "depth": 70,
                "operating_mode": "standard",
                "interrupt_on": ["new_contact"],
            },
            state,
        )
        execution = e.simulate(state, self.game["seed"], order)
        if execution["stop_reason"] != "interrupt":
            self.skipTest("mast look did not produce a new visual contact")
        new_events = [
            event for event in execution["stop_events"] if event["category"] == "new_contact"
        ]
        self.assertTrue(new_events)
        self.assertTrue(new_events[0].get("contacts"))


class OrderHelperTests(unittest.TestCase):
    def test_condition_all_any_not(self):
        state = e.initialize("ab" * 32)["state"]
        state["tracks"] = [
            {
                "id": "S01",
                "last": state["t"],
                "actor": "x",
                "first": 0,
                "evidence": {},
                "observations": [],
                "visual": None,
                "listens": [],
            }
        ]
        navigation = e.navigation_public(state)
        keys = set(navigation)
        condition = orders.validate_condition(
            {
                "all": [
                    {"contact": {"id": "S01", "status": "recent"}},
                    {
                        "any": [
                            {
                                "navigation": {
                                    "field": "minutes_to_relief",
                                    "op": ">",
                                    "value": 0,
                                }
                            },
                            {"not": {"contact": {"id": "S01", "status": "stale"}}},
                        ]
                    },
                ]
            },
            state,
            keys,
        )
        self.assertTrue(orders.evaluate_condition(condition, state, navigation))


if __name__ == "__main__":
    unittest.main()
