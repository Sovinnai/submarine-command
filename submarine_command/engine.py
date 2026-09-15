#!/usr/bin/env python3
"""Glass Strait: fictional, abstract command game; standard-library only.

During play use the CLI, never print session/private.json. Public output is an
explicit whitelist. Each random draw is keyed to a world event, not a query.
"""
import argparse
import copy
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import secrets
import sys
import tempfile

from .locking import session_lock
from .observations import observed_bearing_drift
from .platforms import KESTREL

VERSION = "0.2.0"
TICK = 5
END = 290
REPORT_DUE = 260
KINDS = ("surface", "submerged", "biologic")
CUES = {
    "commercial_rhythm": (0.64, 0.12, 0.06),
    "faint_cycling_tonal": (0.27, 0.69, 0.22),
    "irregular_broadband": (0.09, 0.19, 0.72),
}
CUE_TEXT = {
    "commercial_rhythm": "Repeated machinery rhythm with a commercial-like cadence; not diagnostic.",
    "faint_cycling_tonal": "Faint cycling tonal component; source type remains ambiguous.",
    "irregular_broadband": "Irregular broadband pulses; no stable machinery rhythm resolved.",
    "opening": "Intermittent narrowband component beneath broad background wash; insufficient for identification.",
}
MISSION = {
    "title": "Operation Glass Strait",
    "own_ship": "Kestrel",
    "setting": "Fictional ceasefire verification patrol in the Lydian Passage.",
    "task": "Assess whether submerged traffic is using the patrol corridor and send an evidence-based assessment by 0730.",
    "relief": "Be within 3 nautical miles of station RELIEF (24 east, 0 north) at 0800.",
    "chart": "Local grid in nautical miles: east is +x, north is +y. Patrol corridor: x=0 to 22, y=-6 to +6. All charted water is deep enough for the game envelope.",
    "orders": "Observe and report. Preserve discretion and meet the relief commitment. No offensive weapons employment is authorized in this patrol.",
    "intel": "0200 shore estimate: submerged transit is possible; no reliable identification or contact solution. Commercial and survey traffic also use the passage.",
    "radio": "An intelligence update is scheduled for 0330; a later update for 0510. Messages remain available for retrieval.",
    "units": "Courses and bearings true; speed in knots; depth in feet; distances in nautical miles.",
}


def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def digest(obj):
    return hashlib.sha256(canonical(obj)).hexdigest()


def code_hash():
    root = Path(__file__).parent
    return digest({path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                   for path in sorted(root.glob("*.py"))})


class Dice:
    """HMAC event draws are independent of query/call ordering and auditable."""
    def __init__(self, seed, trace):
        self.key = bytes.fromhex(seed)
        self.trace = trace

    def u(self, label):
        value = (int.from_bytes(hmac.new(self.key, label.encode(), hashlib.sha256).digest()[:8], "big") >> 11) / 2**53
        self.trace.append({"event": label, "u": value})
        return value

    def between(self, label, low, high):
        return low + (high - low) * self.u(label)

    def choose(self, label, options, weights=None):
        weights = weights or [1] * len(options)
        value = self.u(label) * sum(weights)
        for option, weight in zip(options, weights):
            value -= weight
            if value < 0:
                return option
        return options[-1]


def clock(t):
    total = 190 + t
    return f"{total // 60:02d}{total % 60:02d}"


def bearing(a, b):
    return math.degrees(math.atan2(b["x"] - a["x"], b["y"] - a["y"])) % 360


def distance(a, b):
    return math.hypot(a["x"] - b["x"], a["y"] - b["y"])


def move(obj):
    angle = math.radians(obj["course"])
    obj["x"] += math.sin(angle) * obj["speed"] * TICK / 60
    obj["y"] += math.cos(angle) * obj["speed"] * TICK / 60


def report(state, department, text, category="routine", **extra):
    entry = {"id": f"R{len(state['reports']) + 1:04d}", "time": clock(state["t"]),
             "elapsed_minutes": state["t"], "department": department,
             "category": category, "text": text, **extra}
    state["reports"].append(entry)
    return entry


def assessments(track):
    result = {}
    for role, prior in (("supervisor", (0.60, 0.30, 0.10)), ("operator", (0.35, 0.50, 0.15))):
        probs = list(prior)
        for evidence in track["evidence"].values():
            like = CUES[evidence]
            probs = [a * b for a, b in zip(probs, like)]
            total = sum(probs)
            probs = [p / total for p in probs]
        if track.get("visual"):
            probs = [0.995, 0.003, 0.002]
        best = max(range(3), key=lambda i: probs[i])
        confidence = "high" if probs[best] >= 0.85 else "moderate" if probs[best] >= 0.65 else "low"
        result[role] = {"favors": KINDS[best], "confidence": confidence}
    result["evidence_relationship"] = "Both assessments use the same observations; they are not independent corroboration."
    return result


def observe_contact(state, actor, dice, mode="passive", opening=False):
    own, t = state["own"], state["t"]
    delta = distance(own, actor)
    window = t // 20
    quality = dice.between(f"acoustic-window:{window}", 0.55, 1.15)
    across = (own["depth"] > state["layer"]) != (actor["depth"] > state["layer"])
    noise = max(0.24, 1 - max(0, own["speed"] - 5) * 0.055)
    if state["pump_fault"]:
        noise *= 0.68
    if mode == "focus":
        track_match = next((tr for tr in state["tracks"] if tr["actor"] == actor["id"]), None)
        noise *= 1.25 if track_match and track_match["id"] == state["focus"] else 0.78
    audibility = actor["noise"] * quality * noise * (0.55 if across else 1)
    probability = min(0.97, max(0.015, audibility * math.exp(-delta / 8)))
    if mode == "active":
        probability = min(0.97, 1.35 * math.exp(-delta / 17))
    if not opening and dice.u(f"detect:{actor['id']}:{t}:{mode}") >= probability:
        return
    track = next((tr for tr in state["tracks"] if tr["actor"] == actor["id"]), None)
    new = track is None
    if new:
        track = {"id": f"S{len(state['tracks']) + 1:02d}", "actor": actor["id"],
                 "first": t, "last": t, "evidence": {}, "observations": [], "visual": None}
        state["tracks"].append(track)
    old_assessment = assessments(track)["supervisor"]
    track["last"] = t
    measured = (bearing(own, actor) + state["bearing_bias"] + dice.between(f"bearing:{actor['id']}:{t}:{mode}", -4, 4)) % 360
    if opening:
        cue = "opening"
    else:
        i = KINDS.index(actor["kind"])
        cue = dice.choose(f"signature:{actor['id']}:{window}", list(CUES), [CUES[key][i] for key in CUES])
        # One evidence contribution per acoustic window; repeated looks do not compound confidence.
        track["evidence"].setdefault(str(window), cue)
    strength = "strong" if audibility / max(delta, 1) > 0.25 else "moderate" if audibility / max(delta, 1) > 0.10 else "weak"
    obs = {"time": clock(t), "elapsed_minutes": t, "bearing_true": round(measured) % 360,
           "own_east_nm": round(own["x"], 3), "own_north_nm": round(own["y"], 3),
           "source": mode, "strength": strength, "description": CUE_TEXT[cue],
           "range_estimate_nm": None, "evidence_window": f"A{window:03d}"}
    if mode == "active":
        obs["range_estimate_nm"] = round(max(0.1, delta * dice.between(f"active-range:{actor['id']}:{t}", 0.88, 1.12)), 1)
    track["observations"].append(obs)
    report(state, "Sonar", f"{track['id']}: bearing {obs['bearing_true']:03d} true, {strength} reception. {obs['description']}",
           "new_contact" if new else "contact_update", contact=track["id"], observation=obs)
    new_assessment = assessments(track)["supervisor"]
    if not new and new_assessment["confidence"] == "high" and new_assessment != old_assessment:
        report(state, "Sonar supervisor", f"{track['id']}: assessment now favors {new_assessment['favors']} with high confidence; this remains an assessment.", "classification_change", contact=track["id"])


def new_world(seed):
    state = {"t": 0, "platform": KESTREL.identifier,
             "own": {"x": 0.0, "y": 0.0, "course": 90.0, "speed": 5.0, "depth": 400.0},
             "actors": [], "tracks": [], "reports": [], "rng_trace": [], "focus": None,
             "pump_fault": False, "repair_progress": 0, "received": [], "sent": [],
             "ended": False, "end_reason": None, "deadline_announced": False, "last_action_report_start": 0}
    dice = Dice(seed, state["rng_trace"])
    state["layer"] = dice.between("initial:layer", 260, 350)
    state["bearing_bias"] = dice.between("initial:bearing-bias", -2, 2)
    state["equipment_susceptibility"] = dice.between("initial:equipment", 0.4, 1.6)
    state["radio_reliability"] = dice.between("initial:radio", 0.58, 0.94)
    kind = dice.choose("initial:primary-kind", KINDS, [0.46, 0.44, 0.10])
    primary = {"id": "actor-a", "kind": kind,
               "x": dice.between("initial:a-x", 5.5, 10), "y": dice.between("initial:a-y", -2.2, 4),
               "course": dice.between("initial:a-course", 70, 115),
               "speed": dice.between("initial:a-speed", 3.5, 8.5),
               "depth": dice.between("initial:a-depth", 200, 650) if kind == "submerged" else 0,
               "noise": {"surface": 1.7, "submerged": 1.05, "biologic": 0.85}[kind],
               "aware": False, "last_heard": None, "evaded": False,
               "name": dice.choose("initial:a-name", ["Cormorant", "Morrow", "Solace"]),
               "intent": "Transit east through the passage; avoid an observer if one is detected." if kind == "submerged" else "Continue the established eastbound passage."}
    state["actors"].append(primary)
    # Background traffic is fixed at initialization too; no adaptive reinforcements.
    for i in range(2):
        state["actors"].append({"id": f"actor-{i+2}", "kind": "surface",
                                "x": dice.between(f"initial:b{i}-x", 15, 29),
                                "y": dice.between(f"initial:b{i}-y", -11, 11),
                                "course": dice.between(f"initial:b{i}-course", 230, 290),
                                "speed": dice.between(f"initial:b{i}-speed", 7, 12),
                                "depth": 0, "noise": 1.55, "aware": False, "last_heard": None,
                                "evaded": False, "name": ["Larkspur", "Bracken"][i],
                                "intent": "Maintain scheduled commercial passage westbound."})
    shore_correct = dice.u("initial:shore-source") < 0.76
    shore_type = kind if shore_correct else dice.choose("initial:shore-error", [k for k in KINDS if k != kind])
    estimate = {"surface": "Coastal watch reports a possible surface vessel in the eastern approach.",
                "submerged": "Coastal watch reports a possible submerged contact in the eastern approach.",
                "biologic": "Coastal watch reports biological activity that may account for some acoustic reports."}[shore_type]
    state["bulletins"] = [
        {"id": "INTEL-0330", "available": 20, "text": "0330 intelligence update: " + estimate + " Source confidence: moderate; exact track unavailable. This is an independent shore report, not confirmed identification."},
        {"id": "OPS-0510", "available": 120, "text": "0510 operations update: assessment deadline 0730 and relief station time 0800 remain unchanged. Commercial schedules are incomplete; absence from the list does not establish military identity."},
    ]
    report(state, "Navigation", "0310. Local position (0 east, 0 north). Course 090, speed 5 knots, depth 400 feet. RELIEF lies 24 nautical miles east.")
    report(state, "Environment", "Last onboard profile places the principal acoustic layer somewhere between 250 and 375 feet. Conditions are variable.")
    report(state, "Engineering", "Propulsion, sonar, communications and the auxiliary plant are available.")
    observe_contact(state, primary, dice, opening=True)
    return state


def initialize(seed=None):
    seed = seed or secrets.token_hex(32)
    state = new_world(seed)
    package = {"engine_sha256": code_hash(), "seed": seed, "initial_state": state}
    commitment = digest(package)
    return {"format": 1, "version": VERSION, "engine_sha256": code_hash(), "seed": seed,
            "initial_state": copy.deepcopy(state), "state": state,
            "commitment": commitment, "events": [], "head": commitment}


def opponent_step(state, actor, dice, active):
    if actor["kind"] != "submerged":
        return
    own, t = state["own"], state["t"]
    gap = distance(own, actor)
    probability = min(0.70, (0.04 + max(0, own["speed"] - 4) * 0.025) * math.exp(-gap / 10))
    if active:
        probability = min(0.98, 1.5 * math.exp(-gap / 20))
    if dice.u(f"opponent-hears:{actor['id']}:{t}") < probability:
        actor["aware"] = True
        actor["last_heard"] = {"x": own["x"] + dice.between(f"opponent-fix-x:{t}", -2, 2),
                               "y": own["y"] + dice.between(f"opponent-fix-y:{t}", -2, 2), "time": t}
    if actor["aware"] and not actor["evaded"]:
        # Only its last detected, noisy fix informs the decision; no access to future own-ship moves.
        actor["course"] = bearing(actor["last_heard"], actor)
        actor["speed"] = dice.between(f"opponent-evasion-speed:{t}", 6, 10)
        actor["evaded"] = True


def mast_observations(state, dice):
    for actor in state["actors"]:
        if actor["kind"] != "surface":
            continue
        gap = distance(state["own"], actor)
        chance = max(0, 0.92 - gap / 10)
        if dice.u(f"mast:{actor['id']}:{state['t']}") >= chance:
            continue
        track = next((tr for tr in state["tracks"] if tr["actor"] == actor["id"]), None)
        if track is None:
            # Scope gets a bearing even if acoustic reception was absent.
            track = {"id": f"S{len(state['tracks'])+1:02d}", "actor": actor["id"], "first": state["t"],
                     "last": state["t"], "evidence": {}, "observations": [], "visual": None}
            state["tracks"].append(track)
            report(state, "Scope", f"New visual contact designated {track['id']}.", "new_contact")
        b = round((bearing(state["own"], actor) + dice.between(f"mast-bearing:{actor['id']}:{state['t']}", -2, 2)) % 360) % 360
        track["last"] = state["t"]
        visual = {"time": clock(state["t"]), "bearing_true": b,
                  "description": "Surface vessel observed; no military features resolved.",
                  "name_read": actor["name"] if gap < 2.5 else None}
        track["visual"] = visual
        obs = {"time": clock(state["t"]), "elapsed_minutes": state["t"], "bearing_true": b,
               "own_east_nm": round(state["own"]["x"], 3), "own_north_nm": round(state["own"]["y"], 3),
               "source": "mast", "strength": "visual", "description": visual["description"],
               "range_estimate_nm": None, "evidence_window": None}
        track["observations"].append(obs)
        report(state, "Scope", f"{track['id']}, bearing {b:03d}: {visual['description']}", "classification_change", contact=track["id"], visual=visual)


def tick_world(state, dice, order, first_tick):
    state["t"] += TICK
    t = state["t"]
    active = order["activity"] == "active" and first_tick
    move(state["own"])
    for actor in state["actors"]:
        opponent_step(state, actor, dice, active)
        move(actor)
    if active:
        report(state, "Sonar", "One active acoustic transmission made.", "emission")
    mode = "active" if active else "focus" if order["activity"] == "focus" else "passive"
    for actor in state["actors"]:
        observe_contact(state, actor, dice, mode)
    for tr in state["tracks"]:
        if t - tr["last"] == 20:
            report(state, "Sonar", f"{tr['id']}: no further observation for 20 minutes. Last report is now stale; contact fate unknown.", "contact_lost", contact=tr["id"])
    if order["activity"] == "mast":
        mast_observations(state, dice)
    if order["activity"] in ("receive", "transmit"):
        channel = dice.u(f"radio-window:{t//20}")
        if channel >= state["radio_reliability"]:
            report(state, "Radio", "No usable link this interval; cause undetermined.", "radio_failure")
        elif order["activity"] == "receive":
            pending = [b for b in state["bulletins"] if b["available"] <= t and b["id"] not in state["received"]]
            for bulletin in pending:
                state["received"].append(bulletin["id"])
                report(state, "Radio", bulletin["text"], "message_received", message_id=bulletin["id"])
            if not pending:
                report(state, "Radio", "Usable link established; no new message available.", "radio_empty")
        else:
            sent = {"time": clock(t), "elapsed_minutes": t, "assessment": order["assessment"], "text": order["message"], "basis": order["basis"]}
            state["sent"].append(sent)
            report(state, "Radio", "Assessment transmitted and acknowledged.", "transmission_complete", transmitted=sent)
    if order["activity"] == "repair":
        if state["pump_fault"]:
            state["repair_progress"] += TICK
            if state["repair_progress"] >= 20:
                state["pump_fault"] = False
                state["repair_progress"] = 0
                report(state, "Engineering", "Auxiliary vibration corrected; sonar self-noise back to normal.", "repair_complete")
        elif first_tick:
            report(state, "Engineering", "No reported fault requires repair.")
    elif not state["pump_fault"]:
        hazard = 0.003 * state["equipment_susceptibility"] * (1.8 if state["own"]["speed"] > 12 else 1)
        if dice.u(f"equipment:{t}") < hazard:
            state["pump_fault"] = True
            state["repair_progress"] = 0
            report(state, "Engineering", "Auxiliary pump vibration has increased. Propulsion remains available; sonar self-noise is elevated. Repair requires 20 minutes at 10 knots or less.", "equipment")
    if t >= REPORT_DUE and not state["deadline_announced"]:
        state["deadline_announced"] = True
        report(state, "Navigation", "0730 assessment deadline reached.", "deadline")
    if t >= END:
        state["ended"] = True
        state["end_reason"] = "0800 relief time reached"
        report(state, "Navigation", "0800. Patrol exercise complete; the debrief can now be requested.", "exercise_end")


def validate_order(raw, state):
    allowed = {"id", "minutes", "course", "speed", "depth", "activity", "focus", "assessment", "message", "basis", "interrupt_on", "expected_turn"}
    if not isinstance(raw, dict) or set(raw) - allowed:
        raise ValueError("Order must be an object using only the documented fields.")
    if not isinstance(raw.get("id"), str) or not 1 <= len(raw["id"]) <= 80:
        raise ValueError("A unique order id of 1 to 80 characters is required.")
    order = copy.deepcopy(raw)
    order.setdefault("activity", "listen")
    activity = order["activity"]
    if activity not in ("listen", "focus", "active", "mast", "receive", "transmit", "repair", "end"):
        raise ValueError("Unsupported activity. No time has elapsed; agree on an applicable rule before proceeding.")
    order.setdefault("minutes", 0 if activity == "end" else 15)
    minutes = order["minutes"]
    if type(minutes) is not int or (minutes != 0 if activity == "end" else not (5 <= minutes <= 60 and minutes % 5 == 0)):
        raise ValueError("Use 5 to 60 minutes in five-minute steps, or zero for end.")
    if activity == "end":
        if set(raw) - {"id", "activity", "minutes", "expected_turn"}:
            raise ValueError("An end order cannot include other actions.")
        return order
    envelope = KESTREL.envelope
    for field, low, high in (("course", 0, 359.999999),
                             ("speed", envelope.minimum_speed_knots, envelope.maximum_speed_knots),
                             ("depth", envelope.minimum_depth_feet, envelope.maximum_depth_feet)):
        value = order.setdefault(field, state["own"][field])
        if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
            raise ValueError(f"{field} must be between {low} and {high} in the fictional game envelope.")
    if activity in ("mast", "receive", "transmit") and not KESTREL.mast.allows(order["depth"], order["speed"]):
        raise ValueError(f"Mast/link envelope: {KESTREL.mast.minimum_depth_feet:g} to {KESTREL.mast.maximum_depth_feet:g} feet and at most {KESTREL.mast.maximum_speed_knots:g} knots. Include those orders explicitly; no time has elapsed.")
    if activity == "repair" and order["speed"] > KESTREL.repair_maximum_speed_knots:
        raise ValueError(f"Repair requires {KESTREL.repair_maximum_speed_knots:g} knots or less in the game.")
    if activity == "focus" and order.get("focus") not in [tr["id"] for tr in state["tracks"]]:
        raise ValueError("Focused analysis requires an existing contact id.")
    if "focus" in order and activity != "focus":
        raise ValueError("The focus field requires the focus activity.")
    if activity == "transmit":
        if order.get("assessment") not in ("submerged_present", "surface_or_biologic", "unresolved"):
            raise ValueError("Transmission requires assessment: submerged_present, surface_or_biologic, or unresolved.")
        if not isinstance(order.get("message"), str) or not 1 <= len(order["message"]) <= 4000:
            raise ValueError("Transmission requires a message of 1 to 4000 characters.")
        order.setdefault("basis", [])
        if not isinstance(order["basis"], list) or any(not isinstance(r, str) or r not in [p["id"] for p in state["reports"]] for r in order["basis"]):
            raise ValueError("Basis must list existing report ids only.")
    elif {"assessment", "message", "basis"} & set(raw):
        raise ValueError("Assessment, message and basis fields require transmit activity.")
    order.setdefault("interrupt_on", ["new_contact", "classification_change", "equipment", "deadline"])
    valid_interrupts = {"new_contact", "classification_change", "equipment", "deadline", "contact_lost", "message_received", "radio_failure"}
    if not isinstance(order["interrupt_on"], list) or any(x not in valid_interrupts for x in order["interrupt_on"]):
        raise ValueError("Unknown interrupt condition.")
    return order


def simulate(state, seed, order):
    state["last_action_report_start"] = len(state["reports"])
    if order["activity"] == "end":
        state["ended"] = True
        state["end_reason"] = "Captain ended the exercise"
        report(state, "Exercise", "Exercise ended by the captain.", "exercise_end")
        return {"elapsed_minutes": 0, "stop_reason": "exercise_end"}
    start = state["t"]
    dice = Dice(seed, state["rng_trace"])
    for field in ("course", "speed", "depth"):
        state["own"][field] = float(order[field])
    state["focus"] = order.get("focus") if order["activity"] == "focus" else None
    stop = "requested_interval_complete"
    for i in range(order["minutes"] // TICK):
        before = len(state["reports"])
        tick_world(state, dice, order, first_tick=i == 0)
        categories = {r["category"] for r in state["reports"][before:]}
        if state["ended"]:
            stop = "exercise_end"
            break
        if "transmission_complete" in categories or "message_received" in categories or "repair_complete" in categories:
            stop = "task_complete"
            break
        matched = categories.intersection(order["interrupt_on"])
        if matched:
            stop = "interrupt:" + ",".join(sorted(matched))
            break
    return {"elapsed_minutes": state["t"] - start, "stop_reason": stop}


def public_view(game):
    state = game["state"]
    own = state["own"]
    remaining = max(0, END - state["t"])
    station = {"x": 24, "y": 0}
    gap = distance(own, station)
    tracks = []
    for tr in state["tracks"]:
        tracks.append({"id": tr["id"], "first_report": clock(tr["first"]), "last_report": clock(tr["last"]),
                       "status": "recent" if state["t"] - tr["last"] < 20 else "stale",
                       "assessments": assessments(tr), "visual": copy.deepcopy(tr["visual"]),
                       "observations": copy.deepcopy(tr["observations"]),
                       "observed_bearing_drift": observed_bearing_drift(tr["observations"]),
                       "course_speed_solution": "Not established by the engine; use reported bearings and own-ship positions to assess possibilities."})
    return {"game": MISSION["title"], "version": VERSION, "turn": len(game["events"]),
            "time": clock(state["t"]), "elapsed_minutes": state["t"], "ended": state["ended"],
            "initial_commitment_sha256": game["commitment"], "turn_receipt_sha256": game["head"],
            "engine_sha256": game["engine_sha256"], "mission": copy.deepcopy(MISSION),
            "platform_capabilities": KESTREL.public_capabilities(),
            "own_ship": {"name": "Kestrel", "east_nm": round(own["x"], 3), "north_nm": round(own["y"], 3),
                         "course_true": own["course"], "speed_knots": own["speed"], "depth_feet": own["depth"],
                         "equipment": "Auxiliary vibration; sonar self-noise elevated" if state["pump_fault"] else "All systems available",
                         "repair_minutes_completed": state["repair_progress"]},
            "navigation": {"relief_distance_nm": round(gap, 2), "relief_bearing_true": round(bearing(own, station)),
                           "minutes_to_relief": remaining,
                           "minimum_average_speed_to_station_knots": round(gap / (remaining / 60), 2) if remaining else None,
                           "minutes_to_assessment_deadline": max(0, REPORT_DUE - state["t"])},
            "contacts": tracks, "transmitted_assessments": copy.deepcopy(state["sent"]),
            "reports_this_turn": copy.deepcopy(state["reports"][state["last_action_report_start"]:]),
            "report_count": len(state["reports"]),
            "last_execution": copy.deepcopy(game["events"][-1]["execution"]) if game["events"] else None}


def apply_order(game, raw):
    for event in game["events"]:
        if event["raw"].get("id") == raw.get("id"):
            if canonical(event["raw"]) != canonical(raw):
                raise ValueError("This order id was already used for a different order; nothing changed.")
            return copy.deepcopy(event["public_result"])
    if game["state"]["ended"]:
        raise ValueError("The exercise has ended. Request the debrief or review existing reports.")
    if "expected_turn" in raw and (type(raw["expected_turn"]) is not int or raw["expected_turn"] != len(game["events"])):
        raise ValueError("The expected turn is stale; review the current report before giving an order.")
    order = validate_order(raw, game["state"])
    # All validation precedes mutation. CLI adds an atomic write and file lock.
    execution = simulate(game["state"], game["seed"], order)
    event = {"raw": copy.deepcopy(raw), "order": order, "execution": execution,
             "state_sha256": digest(game["state"]), "previous": game["head"]}
    event["receipt"] = digest(event)
    game["head"] = event["receipt"]
    game["events"].append(event)
    result = public_view(game)
    event["public_result"] = copy.deepcopy(result)
    return result


def verify(game):
    if code_hash() != game["engine_sha256"]:
        raise ValueError("The engine differs from its committed version; play paused without changing the save.")
    seal = digest({"engine_sha256": game["engine_sha256"], "seed": game["seed"], "initial_state": game["initial_state"]})
    if seal != game["commitment"]:
        raise ValueError("Initial commitment mismatch.")
    if canonical(new_world(game["seed"])) != canonical(game["initial_state"]):
        raise ValueError("Seed does not regenerate the committed initial state.")
    replay = {k: copy.deepcopy(v) for k, v in game.items() if k not in ("state", "events", "head")}
    replay.update(state=copy.deepcopy(game["initial_state"]), events=[], head=game["commitment"])
    for event in game["events"]:
        result = apply_order(replay, event["raw"])
        if canonical(replay["events"][-1]) != canonical(event) or canonical(result) != canonical(event["public_result"]):
            raise ValueError("Event replay mismatch.")
    if canonical(replay["state"]) != canonical(game["state"]) or replay["head"] != game["head"]:
        raise ValueError("Current state does not match replay.")
    return {"verified": True, "turns_replayed": len(game["events"]),
            "initial_commitment_sha256": game["commitment"], "turn_receipt_sha256": game["head"]}


def atomic_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(canonical(obj))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def debrief(game):
    if not game["state"]["ended"]:
        raise ValueError("Debrief is unavailable during play. End the exercise explicitly to reveal it.")
    verification = verify(game)
    state = game["state"]
    timely = [s for s in state["sent"] if s["elapsed_minutes"] <= REPORT_DUE]
    primary_kind = game["initial_state"]["actors"][0]["kind"]
    evaluations = []
    for sent in state["sent"]:
        accurate = None if sent["assessment"] == "unresolved" else (sent["assessment"] == "submerged_present") == (primary_kind == "submerged")
        evaluations.append({**sent, "matches_hidden_presence": accurate,
                            "interpretation": "Outcome accuracy only; evaluate the judgment against the cited evidence separately."})
    return {"spoilers": True, "verification": verification, "seed": game["seed"],
            "initial_state": game["initial_state"], "final_state": state, "events": game["events"],
            "outcomes": {"assessment_sent_by_deadline": bool(timely),
                         "at_relief_at_0800": distance(state["own"], {"x": 24, "y": 0}) <= 3 if state["t"] == END else None,
                         "opponent_detected_own_ship": any(a["aware"] for a in state["actors"]),
                         "assessments": evaluations}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", default=str(Path.cwd() / ".sessions" / "glass-strait"))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    sub.add_parser("status")
    sub.add_parser("verify")
    sub.add_parser("history")
    sub.add_parser("debrief")
    sub.add_parser("capabilities")
    action = sub.add_parser("act")
    action.add_argument("--order-file", required=True)
    args = parser.parse_args()
    if args.command == "capabilities":
        print(json.dumps(KESTREL.public_capabilities(), indent=2))
        return 0
    folder = Path(args.session)
    folder.mkdir(parents=True, exist_ok=True)
    save = folder / "private.json"
    try:
        with session_lock(folder / "session.lock"):
            if args.command == "init":
                if save.exists():
                    raise ValueError("A session already exists; it has not been rerolled. Use status to resume.")
                game = initialize()
                atomic_json(save, game)
                result = public_view(game)
                atomic_json(folder / "public.json", result)
            else:
                if not save.exists():
                    raise ValueError("No session exists at this path.")
                game = json.loads(save.read_text())
                integrity = verify(game)
                if args.command == "status":
                    result = public_view(game)
                elif args.command == "verify":
                    result = integrity
                elif args.command == "history":
                    result = {"reports": game["state"]["reports"], "orders": [e["raw"] for e in game["events"]]}
                elif args.command == "act":
                    raw = json.loads(Path(args.order_file).read_text())
                    if not isinstance(raw, dict):
                        raise ValueError("Order JSON must be an object.")
                    result = apply_order(game, raw)
                    atomic_json(save, game)
                    atomic_json(folder / "public.json", public_view(game))
                else:
                    result = debrief(game)
            print(json.dumps(result, indent=2, ensure_ascii=False))
    except (ValueError, KeyError, TypeError, OSError) as error:
        # No traceback or private object is emitted on a malformed save/order.
        safe = str(error) if isinstance(error, ValueError) else "Unable to process the save or order; no private state was displayed."
        print(json.dumps({"error": safe, "action_not_committed": True}))
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
