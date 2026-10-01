"""Mission assessments attribute debrief accuracy to cited contacts."""

import json
import unittest

from submarine_command import engine as e


class AssessmentAttributionTests(unittest.TestCase):
    """Seed 06x32: S01 is submerged; S03 is a surface track."""

    SEED = "06" * 32

    def setUp(self):
        self.game = e.initialize(self.SEED)
        self.assertEqual(e.contact_kind(self.game["state"]["actors"][0]), "submerged")

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

    def listen_for_contacts(self):
        for index in range(4):
            e.apply_order(self.game, self.order(id=f"listen-{index}", minutes=15))

    def report_for(self, contact_id):
        for entry in self.game["state"]["reports"]:
            if entry.get("contact") == contact_id:
                return entry["id"]
        self.fail(f"No report cites contact {contact_id}")

    def transmit(self, assessment, basis, order_id="send"):
        self.game["state"]["own"]["depth"] = 70.0
        self.game["state"]["own"]["speed"] = 5.0
        self.game["state"]["ordered"]["depth"] = 70.0
        self.game["state"]["ordered"]["speed"] = 5.0
        self.game["state"]["radio_reliability"] = 2
        result = e.apply_order(
            self.game,
            self.order(
                id=order_id,
                activity="transmit",
                link="mast_transmit",
                depth=70,
                speed=5,
                minutes=5,
                assessment=assessment,
                message=f"Assessment {assessment}",
                basis=basis,
            ),
        )
        categories = [entry["category"] for entry in result["reports_this_turn"]]
        self.assertIn("transmission_complete", categories)
        return result

    def end_and_debrief(self):
        e.apply_order(
            self.game,
            {"id": "end", "expected_turn": len(self.game["events"]), "activity": "end"},
        )
        revealed = e.debrief(self.game)
        self.assertTrue(revealed["verification"]["verified"])
        return revealed

    def test_wrong_track_submerged_claim_is_accidentally_correct(self):
        self.listen_for_contacts()
        surface_report = self.report_for("S03")
        track = next(entry for entry in self.game["state"]["tracks"] if entry["id"] == "S03")
        self.assertEqual(
            e.contact_kind(
                next(
                    actor
                    for actor in self.game["state"]["actors"]
                    if actor["id"] == track["actor"]
                )
            ),
            "surface",
        )
        public = self.transmit("submerged_present", [surface_report])
        sent = public["transmitted_assessments"][-1]
        self.assertEqual(sent["scope"], {"kind": "track", "contact": "S03"})
        self.assertNotIn("actor", json.dumps(public))

        revealed = self.end_and_debrief()
        evaluation = revealed["outcomes"]["assessments"][-1]
        self.assertEqual(evaluation["scope"], {"kind": "track", "contact": "S03"})
        self.assertTrue(evaluation["correct_presence"])
        self.assertFalse(evaluation["correct_track_association"])
        self.assertFalse(evaluation["correct_classification"])
        self.assertTrue(evaluation["accidental_correctness"])
        self.assertNotIn("matches_hidden_presence", evaluation)

    def test_correct_track_submerged_claim_is_fully_correct(self):
        self.listen_for_contacts()
        submerged_report = self.report_for("S01")
        self.transmit("submerged_present", [submerged_report])
        evaluation = self.end_and_debrief()["outcomes"]["assessments"][-1]
        self.assertEqual(evaluation["scope"], {"kind": "track", "contact": "S01"})
        self.assertTrue(evaluation["correct_presence"])
        self.assertTrue(evaluation["correct_track_association"])
        self.assertTrue(evaluation["correct_classification"])
        self.assertFalse(evaluation["accidental_correctness"])

    def test_shore_only_basis_stays_unattributed_area_presence(self):
        self.listen_for_contacts()
        self.game["state"]["own"]["depth"] = 70.0
        self.game["state"]["own"]["speed"] = 5.0
        self.game["state"]["ordered"]["depth"] = 70.0
        self.game["state"]["ordered"]["speed"] = 5.0
        self.game["state"]["radio_reliability"] = 2
        e.apply_order(
            self.game,
            self.order(
                id="copy-intel",
                activity="receive",
                link="mast_receive",
                depth=70,
                speed=5,
                minutes=5,
            ),
        )
        shore = next(
            entry
            for entry in self.game["state"]["reports"]
            if entry.get("category") == "message_received"
        )
        self.assertNotIn("contact", shore)
        self.transmit("submerged_present", [shore["id"]], order_id="area-send")
        public = e.public_view(self.game)
        sent = public["transmitted_assessments"][-1]
        self.assertEqual(sent["scope"], {"kind": "area", "contact": None})

        evaluation = self.end_and_debrief()["outcomes"]["assessments"][-1]
        self.assertEqual(evaluation["scope"], {"kind": "area", "contact": None})
        self.assertTrue(evaluation["correct_presence"])
        self.assertIsNone(evaluation["correct_track_association"])
        self.assertIsNone(evaluation["correct_classification"])
        self.assertFalse(evaluation["accidental_correctness"])

    def test_empty_basis_is_area_presence_and_unresolved_is_unscored(self):
        self.listen_for_contacts()
        self.transmit("unresolved", [], order_id="open")
        evaluation = self.end_and_debrief()["outcomes"]["assessments"][-1]
        self.assertEqual(evaluation["scope"], {"kind": "area", "contact": None})
        self.assertIsNone(evaluation["correct_presence"])
        self.assertIsNone(evaluation["correct_track_association"])
        self.assertIsNone(evaluation["correct_classification"])
        self.assertIsNone(evaluation["accidental_correctness"])

    def test_mixed_contact_tracks_are_rejected_before_mutation(self):
        self.listen_for_contacts()
        before = e.canonical(self.game)
        with self.assertRaises(ValueError) as raised:
            e.validate_order(
                self.order(
                    id="mixed",
                    activity="transmit",
                    link="mast_transmit",
                    depth=70,
                    speed=5,
                    assessment="submerged_present",
                    message="Two tracks",
                    basis=[self.report_for("S01"), self.report_for("S03")],
                ),
                self.game["state"],
            )
        self.assertIn("at most one contact track", str(raised.exception))
        self.assertEqual(before, e.canonical(self.game))

    def test_surface_claim_on_surface_track_while_sub_exists(self):
        self.listen_for_contacts()
        self.transmit("surface_or_biologic", [self.report_for("S03")])
        evaluation = self.end_and_debrief()["outcomes"]["assessments"][-1]
        self.assertFalse(evaluation["correct_presence"])
        self.assertFalse(evaluation["correct_track_association"])
        self.assertTrue(evaluation["correct_classification"])
        self.assertFalse(evaluation["accidental_correctness"])

    def test_replay_keeps_attribution_scores(self):
        self.listen_for_contacts()
        self.transmit("submerged_present", [self.report_for("S03")])
        e.apply_order(
            self.game,
            {"id": "end", "expected_turn": len(self.game["events"]), "activity": "end"},
        )
        first = e.debrief(self.game)
        self.assertTrue(first["verification"]["verified"])
        replayed = e.initialize(self.SEED)
        for event in self.game["events"]:
            e.apply_order(replayed, event["order"])
        second = e.debrief(replayed)
        self.assertEqual(
            first["outcomes"]["assessments"],
            second["outcomes"]["assessments"],
        )
        self.assertEqual(e.canonical(self.game["state"]), e.canonical(replayed["state"]))


if __name__ == "__main__":
    unittest.main()
