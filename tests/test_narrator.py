import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from submarine_command import engine
from submarine_command import narrator


def narrator_order(**kwargs):
    """A fully stated order; the engine defaults no field."""
    return {"activity": "listen", "minutes": 5, "interrupt_on": [], "course": 90,
            "speed": 5, "depth": 400, "operating_mode": "standard", **kwargs}


class NarratorInterfaceTests(unittest.TestCase):
    TEST_SEED = "ef" * 32
    START_KEY = "a1" * 16

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = narrator.SessionStore(Path(self.temp.name) / "private-service")
        initializer = engine.initialize
        patcher = mock.patch.object(
            narrator.engine,
            "initialize",
            side_effect=lambda: initializer(self.TEST_SEED),
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.started = self.store.start(self.START_KEY)
        self.session_id = self.started["session_id"]

    def request(self, operation, params=None, session=True, request_id="request-1"):
        value = {
            "v": 1,
            "request_id": request_id,
            "op": operation,
            "params": params or {},
        }
        if session:
            value["session_id"] = self.session_id
        return narrator.dispatch(self.store, json.dumps(value).encode())

    def assert_public(self, value):
        forbidden = {
            "seed", "actors", "actor", "kind", "spec", "rng_trace", "initial_state",
            "intent", "last_heard", "aware", "bearing_bias", "bulletins",
            "radio_reliability", "radio_intercepts", "true",
        }

        def scan(item):
            if isinstance(item, dict):
                self.assertFalse(set(item).intersection(forbidden))
                for child in item.values():
                    scan(child)
            elif isinstance(item, list):
                for child in item:
                    scan(child)

        scan(value)
        self.assertNotIn(self.TEST_SEED, json.dumps(value))
        self.assertNotIn(str(self.store.root), json.dumps(value))

    def test_all_in_play_operations_are_public(self):
        self.assert_public(self.started)
        capability = self.request("capabilities", session=False)
        status = self.request("status")
        history = self.request("history")
        verification = self.request("verify")
        action = self.request(
            "act",
            {
                "order": narrator_order(id="narrator-order-1", expected_turn=0)
            },
        )
        for response in (capability, status, history, verification, action):
            self.assertTrue(response["ok"])
            self.assert_public(response)

    def test_read_only_operations_do_not_change_private_save(self):
        private = self.store.root / self.session_id / "private.json"
        before = private.read_bytes()
        self.request("status")
        self.request("history")
        self.request("verify")
        self.request("capabilities", session=False)
        self.assertEqual(before, private.read_bytes())

    def test_start_and_action_retries_are_idempotent(self):
        private = self.store.root / self.session_id / "private.json"
        before = private.read_bytes()
        retry_start = self.store.start(self.START_KEY)
        self.assertEqual(self.started, retry_start)
        self.assertEqual(before, private.read_bytes())

        params = {"order": narrator_order(id="retry-order", expected_turn=0)}
        first = self.request("act", params)
        saved = private.read_bytes()
        second = self.request("act", params, request_id="request-2")
        self.assertEqual(first["result"], second["result"])
        self.assertEqual(saved, private.read_bytes())

    def test_narrator_orders_require_expected_turn_and_reject_stale_state(self):
        missing = self.request(
            "act", {"order": narrator_order(id="missing-turn")}
        )
        self.assertFalse(missing["ok"])
        self.assertEqual("invalid_request", missing["error"]["code"])

        stale = self.request(
            "act",
            {
                "order": narrator_order(id="stale-turn", expected_turn=99)
            },
        )
        self.assertFalse(stale["ok"])
        self.assertEqual("order_rejected", stale["error"]["code"])
        self.assertTrue(stale["error"]["action_not_committed"])

    def test_debrief_is_denied_until_exercise_ends(self):
        denied = self.request("debrief")
        self.assertFalse(denied["ok"])
        self.assertEqual("debrief_unavailable", denied["error"]["code"])
        self.assert_public(denied)

        ended = self.request(
            "act",
            {
                "order": {
                    "id": "end-session",
                    "expected_turn": 0,
                    "activity": "end",
                }
            },
        )
        self.assertTrue(ended["ok"])
        revealed = self.request("debrief")
        self.assertTrue(revealed["ok"])
        self.assertTrue(revealed["result"]["spoilers"])

    def test_protocol_rejects_unsafe_or_malformed_requests(self):
        cases = [
            b'{"v":1,"request_id":"x","op":"status","op":"history"}',
            b'{"v":1,"request_id":"x","op":"status","params":{"n":NaN}}',
            b'{"v":1,"request_id":"x","op":"raw_state"}',
            b'{"v":1,"request_id":"x","op":"status","extra":true}',
        ]
        for raw in cases:
            response = narrator.dispatch(self.store, raw)
            self.assertFalse(response["ok"])
            self.assert_public(response)

        traversal = {
            "v": 1,
            "request_id": "x",
            "op": "status",
            "session_id": "../../private",
            "params": {},
        }
        response = narrator.dispatch(self.store, json.dumps(traversal).encode())
        self.assertFalse(response["ok"])
        self.assertEqual("unknown_session", response["error"]["code"])
        self.assert_public(response)

        weak_start = {
            "v": 1,
            "request_id": "x",
            "op": "start",
            "params": {"idempotency_key": "guessable"},
        }
        response = narrator.dispatch(self.store, json.dumps(weak_start).encode())
        self.assertFalse(response["ok"])
        self.assertEqual("invalid_request", response["error"]["code"])

    def test_corrupt_save_returns_only_generic_error(self):
        private = self.store.root / self.session_id / "private.json"
        private.write_text('{"seed":"do-not-leak","broken":true}', encoding="utf-8")
        response = self.request("status")
        self.assertFalse(response["ok"])
        self.assertEqual("session_unavailable", response["error"]["code"])
        self.assertNotIn("do-not-leak", json.dumps(response))
        self.assertNotIn(str(private), json.dumps(response))

    @unittest.skipUnless(os.name == "posix", "POSIX permission bits only")
    def test_private_storage_permissions(self):
        folder = self.store.root / self.session_id
        self.assertEqual(0o700, self.store.root.stat().st_mode & 0o777)
        self.assertEqual(0o700, folder.stat().st_mode & 0o777)
        self.assertEqual(0o600, (self.store.root / "service.key").stat().st_mode & 0o777)
        self.assertEqual(0o600, (folder / "private.json").stat().st_mode & 0o777)

    def test_stdio_rejects_oversized_line_and_recovers(self):
        valid = json.dumps(
            {
                "v": 1,
                "request_id": "after-large",
                "op": "capabilities",
                "params": {},
            }
        ).encode()
        source = io.BytesIO(b"x" * (narrator.MAX_REQUEST_BYTES + 1) + b"\n" + valid + b"\n")
        destination = io.StringIO()
        narrator.serve(self.store, source, destination)
        responses = [json.loads(line) for line in destination.getvalue().splitlines()]
        self.assertEqual("request_too_large", responses[0]["error"]["code"])
        self.assertTrue(responses[1]["ok"])
        self.assertEqual("after-large", responses[1]["request_id"])

    def test_stdio_safely_escapes_non_scalar_request_id(self):
        source = io.BytesIO(
            b'{"v":1,"request_id":"\\ud800","op":"capabilities","params":{}}\n'
        )
        destination = io.StringIO()
        narrator.serve(self.store, source, destination)
        response = json.loads(destination.getvalue())
        self.assertTrue(response["ok"])
        self.assertEqual("\ud800", response["request_id"])


if __name__ == "__main__":
    unittest.main()
