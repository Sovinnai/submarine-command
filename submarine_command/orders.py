"""Validated interrupt policies and bounded conditional command plans.

Public-state conditions and contact-scoped watches are evaluated from the same
facts the captain can read. Plans are linear and finite: every branch is
checked before mutation, and an interrupt or ambiguity returns control.
"""
from __future__ import annotations

import copy
import math

from .observations import STALE_MINUTES

TICK = 5
MAX_PLAN_STEPS = 8
MAX_PLAN_MINUTES = 120

CONTACT_INTERRUPT_CATEGORIES = frozenset({
    "new_contact",
    "classification_change",
    "contact_lost",
})
VALID_INTERRUPTS = frozenset({
    "new_contact",
    "classification_change",
    "equipment",
    "deadline",
    "contact_lost",
    "message_received",
    "radio_failure",
    "maneuver_complete",
    "activity_deferred",
    "antenna_deployed",
    "array_streamed",
    "array_stowed",
    "array_unstable",
    "operating_mode",
    "incoming_weapon",
    "weapon_effect",
})
CONTACT_SCOPES = frozenset({"any", "contacts", "except"})
# new_contact has no prior id to name; only an all-contact watch is valid.
NEW_CONTACT_SCOPES = frozenset({"any"})
NAV_COMPARE_OPS = {
    "<=": lambda left, right: left <= right,
    ">=": lambda left, right: left >= right,
    "<": lambda left, right: left < right,
    ">": lambda left, right: left > right,
    "==": lambda left, right: left == right,
}


def known_contact_ids(state):
    return {track["id"] for track in state["tracks"]}


def contact_status(state, contact_id):
    track = next((tr for tr in state["tracks"] if tr["id"] == contact_id), None)
    if track is None:
        return None
    return "recent" if state["t"] - track["last"] < STALE_MINUTES else "stale"


def normalize_interrupt_on(raw_list, state):
    """Expand interrupt watches into an explicit validated policy list."""
    if not isinstance(raw_list, list):
        raise ValueError("interrupt_on must be a list of categories or watch objects.")
    known = known_contact_ids(state)
    normalized = []
    for entry in raw_list:
        normalized.append(_normalize_interrupt_entry(entry, known))
    return normalized


def _normalize_interrupt_entry(entry, known_contacts):
    if isinstance(entry, str):
        if entry not in VALID_INTERRUPTS:
            raise ValueError("Unknown interrupt condition.")
        if entry in CONTACT_INTERRUPT_CATEGORIES:
            return {"category": entry, "scope": "any"}
        return {"category": entry}
    if not isinstance(entry, dict):
        raise ValueError(
            "Each interrupt_on entry must be a category string or a watch object."
        )
    allowed = {"category", "scope", "contacts"}
    if not entry or set(entry) - allowed:
        raise ValueError(
            "An interrupt watch takes only category, and optionally scope and contacts."
        )
    category = entry.get("category")
    if category not in VALID_INTERRUPTS:
        raise ValueError("Unknown interrupt condition.")
    scope = entry.get("scope")
    contacts = entry.get("contacts")
    if category not in CONTACT_INTERRUPT_CATEGORIES:
        if scope is not None or contacts is not None:
            raise ValueError(
                f"{category} is not contact-scoped. Omit scope and contacts."
            )
        return {"category": category}
    if scope is None:
        scope = "any"
    if scope not in CONTACT_SCOPES:
        raise ValueError("Contact interrupt scope must be any, contacts, or except.")
    if category == "new_contact" and scope not in NEW_CONTACT_SCOPES:
        raise ValueError(
            "new_contact watches only the any scope; a new designation has no prior id."
        )
    if scope == "any":
        if contacts is not None:
            raise ValueError("scope any does not take a contacts list.")
        return {"category": category, "scope": "any"}
    if not isinstance(contacts, list) or not contacts:
        raise ValueError(
            f"scope {scope} requires a non-empty contacts list of existing contact ids."
        )
    if any(not isinstance(item, str) for item in contacts):
        raise ValueError("Contact ids in an interrupt watch must be strings.")
    if len(set(contacts)) != len(contacts):
        raise ValueError("Contact ids in an interrupt watch must be unique.")
    missing = [item for item in contacts if item not in known_contacts]
    if missing:
        raise ValueError(
            "Interrupt watch names unknown contact id "
            f"{missing[0]}. History is unchanged; name an existing contact or omit it."
        )
    return {"category": category, "scope": scope, "contacts": list(contacts)}


def interrupt_report_matches(policy, report):
    if report.get("category") != policy["category"]:
        return False
    scope = policy.get("scope")
    if scope is None or scope == "any":
        return True
    contact = report.get("contact")
    if contact is None:
        return False
    if scope == "contacts":
        return contact in policy["contacts"]
    if scope == "except":
        return contact not in policy["contacts"]
    return False


def matching_interrupt_reports(reports, interrupt_on):
    matched = []
    for report in reports:
        if any(interrupt_report_matches(policy, report) for policy in interrupt_on):
            matched.append(report)
    return matched


def execution_events_from_reports(reports):
    """Summarize public stop reasons, naming contacts when a report carries one."""
    by_category = {}
    for entry in reports:
        by_category.setdefault(entry["category"], []).append(entry)
    events = []
    for category in sorted(by_category):
        group = by_category[category]
        item = {
            "category": category,
            "report_ids": [entry["id"] for entry in group],
        }
        contacts = [entry["contact"] for entry in group if entry.get("contact")]
        if contacts:
            # Preserve first-seen order while dropping duplicates.
            seen = []
            for contact in contacts:
                if contact not in seen:
                    seen.append(contact)
            item["contacts"] = seen
        events.append(item)
    return events


def validate_condition(condition, state, navigation_keys):
    if not isinstance(condition, dict) or len(condition) != 1:
        raise ValueError(
            "A condition must be one object with exactly one of: contact, navigation, "
            "all, any, not."
        )
    kind, body = next(iter(condition.items()))
    if kind == "contact":
        if not isinstance(body, dict) or set(body) != {"id", "status"}:
            raise ValueError("A contact condition takes id and status.")
        contact_id = body["id"]
        status = body["status"]
        if not isinstance(contact_id, str):
            raise ValueError("contact.id must be a string.")
        if status not in ("recent", "stale"):
            raise ValueError("contact.status must be recent or stale.")
        if contact_id not in known_contact_ids(state):
            raise ValueError(
                f"Condition names unknown contact id {contact_id}. No time has elapsed."
            )
        return {"contact": {"id": contact_id, "status": status}}
    if kind == "navigation":
        if not isinstance(body, dict) or set(body) != {"field", "op", "value"}:
            raise ValueError("A navigation condition takes field, op and value.")
        field = body["field"]
        op = body["op"]
        value = body["value"]
        if field not in navigation_keys:
            raise ValueError(
                f"Navigation field {field} is not published for this scenario."
            )
        if op not in NAV_COMPARE_OPS:
            raise ValueError("Navigation op must be one of <=, >=, <, >, ==.")
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError("Navigation comparison value must be a finite number.")
        return {"navigation": {"field": field, "op": op, "value": float(value)}}
    if kind in ("all", "any"):
        if not isinstance(body, list) or not body:
            raise ValueError(f"{kind} requires a non-empty list of conditions.")
        if len(body) > 6:
            raise ValueError(f"{kind} is limited to 6 nested conditions.")
        return {kind: [validate_condition(item, state, navigation_keys) for item in body]}
    if kind == "not":
        return {"not": validate_condition(body, state, navigation_keys)}
    raise ValueError(
        "A condition must be one of: contact, navigation, all, any, not."
    )


class ConditionAmbiguous(ValueError):
    """A public-state condition cannot be decided from published values."""


def evaluate_condition(condition, state, navigation):
    """Return True/False, or raise ConditionAmbiguous when public data is missing."""
    kind, body = next(iter(condition.items()))
    if kind == "contact":
        status = contact_status(state, body["id"])
        if status is None:
            raise ConditionAmbiguous(
                f"Contact {body['id']} is no longer present in the public track list."
            )
        return status == body["status"]
    if kind == "navigation":
        if body["field"] not in navigation:
            raise ConditionAmbiguous(
                f"Navigation field {body['field']} is not available."
            )
        left = navigation[body["field"]]
        if left is None:
            raise ConditionAmbiguous(
                f"Navigation field {body['field']} has no numeric value to compare."
            )
        if type(left) not in (int, float):
            raise ConditionAmbiguous(
                f"Navigation field {body['field']} is not numeric."
            )
        return NAV_COMPARE_OPS[body["op"]](float(left), body["value"])
    if kind == "all":
        return all(evaluate_condition(item, state, navigation) for item in body)
    if kind == "any":
        return any(evaluate_condition(item, state, navigation) for item in body)
    if kind == "not":
        return not evaluate_condition(body, state, navigation)
    raise ConditionAmbiguous("Unsupported condition form.")


PLAN_STEP_ID_PREFIX = "#"


def plan_step_action_id(plan_id, index):
    """Stable per-step action id in a namespace top-level orders cannot use."""
    step_id = f"{PLAN_STEP_ID_PREFIX}{plan_id}:{index}"
    if len(step_id) > 80:
        raise ValueError(
            "Plan id is too long to derive per-step action ids "
            f"(need room for '{PLAN_STEP_ID_PREFIX}:' and the step index). "
            "No time has elapsed."
        )
    return step_id


def validate_plan(
    raw_plan, state, validate_command, navigation_keys, plan_id, project_envelopes
):
    """Validate every reachable step against deterministic envelope projection.

    Conditions are checked against the opening public facts. Command envelopes
    are checked on every reachable when/else path using only deterministic
    equipment, antenna and inventory effects—never random draws, tracks or
    reports—so validation cannot reveal hidden detections.
    """
    if not isinstance(raw_plan, dict) or set(raw_plan) != {"steps"}:
        raise ValueError("A plan takes only a steps list.")
    steps = raw_plan["steps"]
    if not isinstance(steps, list) or not steps:
        raise ValueError("A plan requires a non-empty steps list.")
    if len(steps) > MAX_PLAN_STEPS:
        raise ValueError(f"A plan is limited to {MAX_PLAN_STEPS} steps.")
    prelim = []
    total_minutes = 0
    for index, raw_step in enumerate(steps):
        meta, command_raw = _parse_plan_step(raw_step, state, navigation_keys, index)
        minutes = command_raw.get("minutes")
        if type(minutes) is not int:
            raise ValueError(f"Plan step {index} command requires minutes.")
        total_minutes += minutes
        prelim.append((meta, command_raw))
    if total_minutes > MAX_PLAN_MINUTES:
        raise ValueError(
            f"A plan's requested minutes may not exceed {MAX_PLAN_MINUTES}."
        )

    stored_commands = [None] * len(prelim)

    def bind_command(index, command_raw, projected):
        synthetic = {
            **command_raw,
            "id": plan_step_action_id(plan_id, index),
            "expected_turn": 0,
        }
        validated = validate_command(synthetic, projected)
        command = {
            key: value for key, value in validated.items() if key != "expected_turn"
        }
        if stored_commands[index] is None:
            stored_commands[index] = command
        return command

    def explore(index, projected):
        if index >= len(prelim):
            return
        meta, command_raw = prelim[index]
        else_action = meta.get("else")
        if "when" in meta and else_action == "skip":
            explore(index + 1, copy.deepcopy(projected))
            command = bind_command(index, command_raw, projected)
            ran = copy.deepcopy(projected)
            project_envelopes(ran, command)
            explore(index + 1, ran)
            return
        if "when" in meta and else_action == "stop":
            command = bind_command(index, command_raw, projected)
            ran = copy.deepcopy(projected)
            project_envelopes(ran, command)
            explore(index + 1, ran)
            return
        command = bind_command(index, command_raw, projected)
        project_envelopes(projected, command)
        explore(index + 1, projected)

    explore(0, copy.deepcopy(state))

    normalized_steps = []
    for index, (meta, _command_raw) in enumerate(prelim):
        if stored_commands[index] is None:
            raise ValueError(
                f"Plan step {index} has no reachable execution path to validate."
            )
        step = dict(meta)
        step["command"] = stored_commands[index]
        normalized_steps.append(step)
    return {"steps": normalized_steps}


def _parse_plan_step(raw_step, condition_state, navigation_keys, index):
    if not isinstance(raw_step, dict):
        raise ValueError(f"Plan step {index} must be an object.")
    allowed = {"label", "when", "else", "command", "stop_when"}
    if set(raw_step) - allowed:
        raise ValueError(
            f"Plan step {index} uses unsupported fields. "
            "Allowed: label, when, else, command, stop_when."
        )
    if "command" not in raw_step:
        raise ValueError(f"Plan step {index} requires command.")
    meta = {}
    if "label" in raw_step:
        label = raw_step["label"]
        if not isinstance(label, str) or not 1 <= len(label) <= 40:
            raise ValueError(f"Plan step {index} label must be 1 to 40 characters.")
        meta["label"] = label
    if "when" in raw_step:
        meta["when"] = validate_condition(
            raw_step["when"], condition_state, navigation_keys
        )
        else_action = raw_step.get("else", "stop")
        if else_action not in ("stop", "skip"):
            raise ValueError(
                f"Plan step {index} else must be stop or skip when when is stated."
            )
        meta["else"] = else_action
    elif "else" in raw_step:
        raise ValueError(f"Plan step {index} else requires a when condition.")
    if "stop_when" in raw_step:
        meta["stop_when"] = validate_condition(
            raw_step["stop_when"], condition_state, navigation_keys
        )
    command_raw = copy.deepcopy(raw_step["command"])
    if not isinstance(command_raw, dict):
        raise ValueError(f"Plan step {index} command must be an object.")
    if "id" in command_raw or "expected_turn" in command_raw or "plan" in command_raw:
        raise ValueError(
            f"Plan step {index} command must not include id, expected_turn or plan."
        )
    if command_raw.get("activity") == "end":
        raise ValueError(
            f"Plan step {index} cannot use activity end; end the exercise with a "
            "separate end order."
        )
    return meta, command_raw


def remaining_plan_steps(steps_from_index):
    remaining = []
    for index, step in steps_from_index:
        item = {"index": index}
        if "label" in step:
            item["label"] = step["label"]
        remaining.append(item)
    return remaining
