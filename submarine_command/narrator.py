#!/usr/bin/env python3
"""Restricted JSON-lines interface for an evidence-limited narrator.

The trusted host runs this process and gives an untrusted narrator only its
stdin and stdout. The narrator receives opaque session identifiers, never save
paths. OS-level process or container isolation is still required when the
narrator would otherwise have filesystem or shell access as the service user.
"""
import argparse
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sys

from . import engine
from .locking import session_lock
from .platforms import KESTREL

PROTOCOL_VERSION = 1
MAX_REQUEST_BYTES = 64 * 1024
SESSION_ID = re.compile(r"[A-Za-z0-9_-]{43}")
IDEMPOTENCY_KEY = re.compile(r"[A-Za-z0-9_-]{32,200}")
REQUEST_FIELDS = {"v", "request_id", "op", "session_id", "params"}
OPERATIONS = {"capabilities", "start", "status", "history", "act", "verify", "debrief"}


class NarratorError(Exception):
    """A public protocol error with a stable, non-sensitive code."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


class SessionStore:
    """Persist private worlds beneath one trusted root and expose public views."""

    def __init__(self, root):
        root = Path(root).expanduser()
        if root.is_symlink():
            raise OSError("The storage root cannot be a symbolic link.")
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if os.name == "posix":
            root.chmod(0o700)
        self.root = root.resolve()
        self._service_key = self._load_or_create_service_key()

    def _load_or_create_service_key(self):
        path = self.root / "service.key"
        with session_lock(self.root / "service.lock"):
            if path.is_symlink():
                raise OSError("The service key cannot be a symbolic link.")
            if path.exists():
                key = path.read_bytes()
                if len(key) != 32:
                    raise OSError("The service key is invalid.")
                return key
            key = secrets.token_bytes(32)
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            descriptor = os.open(path, flags, 0o600)
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(key)
                    stream.flush()
                    os.fsync(stream.fileno())
            except BaseException:
                path.unlink(missing_ok=True)
                raise
            engine.fsync_directory(self.root)
            return key

    def _id_for_key(self, idempotency_key):
        if (
            not isinstance(idempotency_key, str)
            or IDEMPOTENCY_KEY.fullmatch(idempotency_key) is None
        ):
            raise NarratorError(
                "invalid_request",
                "start requires a secret URL-safe idempotency_key of 32 to 200 characters.",
            )
        token = hmac.new(
            self._service_key, idempotency_key.encode("utf-8"), hashlib.sha256
        ).digest()
        return base64.urlsafe_b64encode(token).rstrip(b"=").decode("ascii")

    def _folder(self, session_id, require_existing=True):
        if not isinstance(session_id, str) or SESSION_ID.fullmatch(session_id) is None:
            raise NarratorError("unknown_session", "The session identifier is invalid or unknown.")
        folder = self.root / session_id
        if folder.is_symlink():
            raise NarratorError("unknown_session", "The session identifier is invalid or unknown.")
        if require_existing and not folder.is_dir():
            raise NarratorError("unknown_session", "The session identifier is invalid or unknown.")
        return folder

    @staticmethod
    def _private_path(folder):
        path = folder / "private.json"
        if path.is_symlink():
            raise NarratorError("session_unavailable", "The session could not be verified.")
        return path

    def _load(self, folder):
        path = self._private_path(folder)
        try:
            game = json.loads(path.read_text(encoding="utf-8"))
            engine.verify(game)
            return game
        except NarratorError:
            raise
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
            raise NarratorError(
                "session_unavailable", "The session could not be verified."
            ) from error

    def capabilities(self):
        return {
            "interface": {
                "protocol": "submarine-command-narrator",
                "version": PROTOCOL_VERSION,
                "operations": sorted(OPERATIONS),
                "session_identifiers": "opaque bearer tokens; no session listing is available",
                "orders": "act requires the current expected_turn and a unique order id",
                "debrief": "available only after the exercise has ended",
            },
            "platform": KESTREL.public_capabilities(),
        }

    def start(self, idempotency_key):
        session_id = self._id_for_key(idempotency_key)
        folder = self._folder(session_id, require_existing=False)
        created = not folder.exists()
        folder.mkdir(mode=0o700, exist_ok=True)
        if os.name == "posix":
            folder.chmod(0o700)
        if created:
            engine.fsync_directory(self.root)
        with session_lock(folder / "session.lock"):
            path = self._private_path(folder)
            if path.exists():
                game = self._load(folder)
            else:
                game = engine.initialize()
                engine.atomic_json(path, game)
            return {"session_id": session_id, "status": engine.public_view(game)}

    def status(self, session_id):
        folder = self._folder(session_id)
        with session_lock(folder / "session.lock"):
            return engine.public_view(self._load(folder))

    def history(self, session_id):
        folder = self._folder(session_id)
        with session_lock(folder / "session.lock"):
            return engine.public_history(self._load(folder))

    def verify(self, session_id):
        folder = self._folder(session_id)
        with session_lock(folder / "session.lock"):
            return engine.verify(self._load(folder))

    def act(self, session_id, order):
        if not isinstance(order, dict):
            raise NarratorError("invalid_request", "act requires one order object.")
        if "expected_turn" not in order:
            raise NarratorError(
                "invalid_request", "Narrator orders must include the current expected_turn."
            )
        folder = self._folder(session_id)
        with session_lock(folder / "session.lock"):
            game = self._load(folder)
            try:
                result = engine.apply_order(game, order)
            except ValueError as error:
                raise NarratorError("order_rejected", str(error)) from error
            engine.atomic_json(self._private_path(folder), game)
            return result

    def debrief(self, session_id):
        folder = self._folder(session_id)
        with session_lock(folder / "session.lock"):
            game = self._load(folder)
            if not game["state"]["ended"]:
                raise NarratorError(
                    "debrief_unavailable",
                    "Debrief is unavailable until the exercise has ended.",
                )
            return engine.debrief(game)


def _reject_constant(_value):
    raise ValueError("Non-finite JSON numbers are not accepted.")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON object keys are not accepted.")
        result[key] = value
    return result


def parse_request(raw):
    try:
        request = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise NarratorError("invalid_json", "Request must be one valid JSON object.") from error
    if not isinstance(request, dict) or set(request) - REQUEST_FIELDS:
        raise NarratorError("invalid_request", "Request contains unsupported fields.")
    if request.get("v") != PROTOCOL_VERSION:
        raise NarratorError("unsupported_version", "Unsupported protocol version.")
    request_id = request.get("request_id")
    if not isinstance(request_id, str) or not 1 <= len(request_id) <= 80:
        raise NarratorError("invalid_request", "request_id must be 1 to 80 characters.")
    operation = request.get("op")
    if operation not in OPERATIONS:
        raise NarratorError("unknown_operation", "The requested operation is unavailable.")
    params = request.get("params", {})
    if not isinstance(params, dict):
        raise NarratorError("invalid_request", "params must be an object.")
    return request_id, operation, request.get("session_id"), params


def dispatch(store, raw):
    request_id = None
    try:
        request_id, operation, session_id, params = parse_request(raw)
        if operation == "capabilities":
            _require_shape(session_id, params, set())
            result = store.capabilities()
        elif operation == "start":
            _require_shape(session_id, params, {"idempotency_key"})
            result = store.start(params["idempotency_key"])
        else:
            if session_id is None:
                raise NarratorError("invalid_request", "This operation requires session_id.")
            if operation == "act":
                _require_shape(session_id, params, {"order"}, session_required=True)
                result = store.act(session_id, params["order"])
            else:
                _require_shape(session_id, params, set(), session_required=True)
                result = getattr(store, operation)(session_id)
        return {"v": PROTOCOL_VERSION, "request_id": request_id, "ok": True, "result": result}
    except NarratorError as error:
        return {
            "v": PROTOCOL_VERSION,
            "request_id": request_id,
            "ok": False,
            "error": {
                "code": error.code,
                "message": error.message,
                "action_not_committed": True,
            },
        }
    except (OSError, KeyError, TypeError, ValueError):
        return {
            "v": PROTOCOL_VERSION,
            "request_id": request_id,
            "ok": False,
            "error": {
                "code": "internal_error",
                "message": "The request could not be processed; no private state was displayed.",
                "action_not_committed": True,
            },
        }


def _require_shape(session_id, params, fields, session_required=False):
    if set(params) != fields:
        raise NarratorError("invalid_request", "params does not match this operation.")
    if session_required:
        if session_id is None:
            raise NarratorError("invalid_request", "This operation requires session_id.")
    elif session_id is not None:
        raise NarratorError("invalid_request", "This operation does not accept session_id.")


def serve(store, source=None, destination=None):
    source = source or sys.stdin.buffer
    destination = destination or sys.stdout
    while True:
        raw = source.readline(MAX_REQUEST_BYTES + 1)
        if not raw:
            return 0
        if len(raw) > MAX_REQUEST_BYTES:
            while raw and not raw.endswith(b"\n"):
                raw = source.readline(MAX_REQUEST_BYTES + 1)
            response = {
                "v": PROTOCOL_VERSION,
                "request_id": None,
                "ok": False,
                "error": {
                    "code": "request_too_large",
                    "message": f"Requests are limited to {MAX_REQUEST_BYTES} bytes.",
                    "action_not_committed": True,
                },
            }
        else:
            response = dispatch(store, raw)
        destination.write(json.dumps(response, ensure_ascii=True, allow_nan=False) + "\n")
        destination.flush()


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Run the restricted JSON-lines narrator interface."
    )
    parser.add_argument(
        "--storage-root",
        required=True,
        help="Trusted private storage; do not expose this path to the narrator.",
    )
    args = parser.parse_args(argv)
    try:
        store = SessionStore(args.storage_root)
    except OSError:
        print("Unable to initialize private narrator storage.", file=sys.stderr)
        return 2
    return serve(store)


if __name__ == "__main__":
    raise SystemExit(main())
