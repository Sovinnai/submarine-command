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

from . import acoustics, arrays, spectra
from .locking import session_lock
from .observations import (
    ACOUSTIC_BIAS_DEGREES,
    classification_model,
    crew_classification,
    frequency_change_assessment,
    frequency_change_model,
    motion_model,
    observed_bearing_drift,
    target_motion_estimate,
)
from .platforms import (
    BIOLOGIC,
    DART,
    FISHER,
    KESTREL,
    MERCHANT,
    WARSHIP,
    EntityCategory,
    LinkDirection,
    get_spec,
    public_entity_catalog,
)

VERSION = "0.12.0"
TICK = 5
MANEUVER_STEP = 1
END = 290
REPORT_DUE = 260
KINDS = ("surface", "submerged", "biologic")
MISSION = {
    "title": "Operation Glass Strait",
    "own_ship": "Kestrel",
    "setting": "Fictional ceasefire verification patrol in the Lydian Passage.",
    "task": "Assess whether submerged traffic is using the patrol corridor and send an evidence-based assessment by 0730.",
    "relief": "Be within 3 nautical miles of station RELIEF (24 east, 0 north) at 0800.",
    "chart": "Local grid in nautical miles: east is +x, north is +y. Patrol corridor: x=0 to 22, y=-6 to +6. All charted water is deep enough for the game envelope.",
    "orders": "Observe and report. Preserve discretion and meet the relief commitment. No offensive weapons employment is authorized in this patrol.",
    "intel": "0200 shore estimate: submerged transit is possible; no reliable identification or contact solution. Commercial and survey traffic also use the passage.",
    "radio": "An intelligence update is scheduled for 0330; a later update for 0510. Messages remain dated reports available for retrieval. A mast receive can copy one once its scheduled time has passed. A buoyant receive adds that mode's published delivery latency.",
    "units": "Courses and bearings true; speed in knots; depth in feet; distances in nautical miles.",
}
COMMAND_CONTRACT = {
    "player_command_window": {
        "minimum_minutes": 5,
        "maximum_minutes": 60,
        "increment_minutes": TICK,
        "description": "The captain chooses one command window; thinking and reviewing existing reports consume no simulated time.",
    },
    "integration": {
        "step_minutes": TICK,
        "description": "The engine resolves all platform movement and world effects on one shared clock in five-minute steps. Five minutes is sufficient for this prototype's coarse navigation, contact, link, and equipment decisions; acceleration, turn rate, and sub-step event timing are deliberately simplified.",
    },
    "maneuver": {
        "ordered_versus_achieved": "An order states the commanded course, speed, depth, and operating mode. Those become the standing commanded settings; they are not achieved instantly. Actual course, speed, and depth move toward them at the published maneuver rates and are reported separately from the commanded settings.",
        "resolution_step_minutes": MANEUVER_STEP,
        "resolution": "Own-ship motion is integrated in one-minute increments inside each five-minute step so a maneuver crossing an envelope during the step is resolved at the crossing, not at the endpoint. No random draw occurs inside that integration; every world draw remains at the five-minute boundary.",
        "depth_rate": "Ordered depth change is resolved at a rate proportional to actual speed. A boat at bare steerageway changes depth slowly; a boat at high speed changes depth quickly and is also loud.",
        "standing_settings": "Commanded settings persist in world state until the next order replaces them. Every order replaces all of them; an omitted field is rejected, never carried forward silently. An interrupt returns control without altering the commanded settings, and the execution receipt reports both achieved and commanded values.",
        "opposing_platforms": "Opposing entities are not rate-limited in this rules version. Their course, speed, depth, and operating-mode changes still occur only at five-minute boundaries.",
    },
    "concurrency": {
        "maneuver": "Commanded course, speed, depth, and operating mode take effect at the start of the first five-minute step as standing settings. Actual values then transit toward them at the published rates while own ship and opposing platforms move over the same elapsed step.",
        "observation": "Passive or focused acoustic integration occupies each complete five-minute step and runs concurrently with movement and the selected equipment activity. Each listening receiver is evaluated separately at its published depth, coverage and noise. Reception uses the hidden sound-speed profile, water depth and bottom at the source and receiver depths actually held, and reports measured frequencies with quality and uncertainty at the step endpoint. A stored spectrum is not regenerated by reading it again. Shared frequency bias and environmental quality are one draw per 20-minute window; each receiver adds its own calibration bias. A track is one receiver's history. Focused analysis requires an acoustic receiver history; a visual contact cannot be focused.",
        "active": "The active transmission and its observation integration occupy the first five-minute step; later requested steps revert to passive observation.",
        "mast": "Mast deployment, visual observation, and recovery form a five-minute cycle concurrent with movement. The cycle requires the whole five minutes inside the published mast depth and speed envelope; a step spent transiting toward mast depth defers it and reports the achieved and commanded settings. Results are available at the step endpoint.",
        "communications": "Receive and transmit are separate published modes, and the order names one with link. Each attempt occupies one five-minute step, concurrent with movement and passive observation, and counts only when the whole step is inside that mode's depth and speed envelope. A step spent reaching the envelope is deferred and reported. Mast modes raise, attempt, and house the mast inside the cycle. Buoyant receive is receive-only: deployment and retrieval take their published times, the antenna stays streamed and limits later orders until retrieval completes, and a bulletin can be copied only after its scheduled time plus that mode's latency. A usable receive link or an acknowledged transmission completes the task. A failed link may be retried unless radio_failure is an interrupt. Channel quality is one draw per 20-minute window, shared by every mode and shifted by the mode's published reliability offset, so repeated attempts in that window agree. A completed mast transmission radiates. Surface-combatant intercept uses that same draw and the published range. The transmission does not change own-ship acoustic source level.",
        "repair": "Repair needs 20 productive minutes at no more than the published repair speed. Only the minutes actually spent at or below that speed count, so a step spent decelerating earns partial credit. It runs concurrently with movement and passive observation and retains progress across interrupted command windows.",
        "towed_array": "stream_array needs 15 productive minutes at 8 knots or less; recover_array needs 10. Only minutes actually spent at or below that speed count, including a step spent decelerating, and progress is kept across interrupted windows. Hull and flank still listen during those activities. Recovery marks the towed receiver recovering before that step's listening, so the towed array does not keep bearings while it is being recovered. While the array is streaming, streamed or recovering, ordered speed may not exceed the published limit for that state. A turn, or the five minutes after one, marks the towed receiver unstable, including a turn on the step that finishes streaming; the engine does not calculate cable shape or layback.",
        "operating_mode": "A commanded operating mode takes effect once actual speed and depth satisfy that mode's published limits; until then the previous mode remains in force. Own-ship narrowband lines follow the published operating-state spectrum rules, but opposing detection does not use that spectrum. Selecting a quieter plant state carries no direct counter-detection benefit beyond the speed it permits. Opposing detection uses own-ship speed, the shared transmission-loss model, receiver depth and active transmission.",
    },
    "order_fields": {
        "required": ["id", "expected_turn", "activity", "minutes", "course", "speed", "depth", "operating_mode", "interrupt_on"],
        "conditional": {"focus": "focus activity", "link": "receive, transmit, or retrieve activity", "assessment": "transmit activity", "message": "transmit activity", "basis": "transmit activity"},
        "end_order": ["id", "expected_turn", "activity"],
        "description": "Every listed field must be stated explicitly. The engine supplies no default for any order field: an omitted field is rejected before any state changes rather than being filled in from current or previous settings. An empty interrupt_on list means no interrupt is selected, and an empty basis list means the assessment cites no report.",
    },
    "interrupts": "Events are evaluated only at five-minute step boundaries. A stop returns actual and unused time plus every matching event category and report id from that boundary; the unused portion is never continued automatically.",
}
PUBLIC_REPORT_FIELDS = {
    "id", "time", "elapsed_minutes", "department", "category", "text",
    "contact", "observation", "visual", "message_id", "transmitted", "link",
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


def move(obj, minutes, course=None, speed=None):
    """Advance a position over an interval, optionally on a mean course/speed."""
    angle = math.radians(obj["course"] if course is None else course)
    rate = obj["speed"] if speed is None else speed
    obj["x"] += math.sin(angle) * rate * minutes / 60
    obj["y"] += math.cos(angle) * rate * minutes / 60


def approach(current, ordered, rate, minutes):
    """Move a scalar toward its ordered value at a bounded rate, never past it."""
    step = rate * minutes
    return min(ordered, current + step) if current < ordered else max(ordered, current - step)


def course_step(current, ordered, rate, minutes):
    """Return the shortest-arc turn available in the interval and where it lands.

    The landing course is the ordered value itself once the whole remaining
    difference fits in the interval. Adding the increment instead would leave
    floating-point dust a hair off the ordered course, which reads as a
    maneuver still in progress for the rest of the exercise. An exact reversal
    is resolved to port so that replay stays deterministic.
    """
    difference = (ordered - current + 180) % 360 - 180
    limit = rate * minutes
    if abs(difference) <= limit:
        return difference, ordered % 360
    turn = math.copysign(limit, difference)
    return turn, (current + turn) % 360


def advance_own(state, minutes):
    """Integrate own-ship kinematics toward the commanded settings.

    Deliberately free of random draws: every world draw stays keyed to the
    five-minute boundary, so sub-stepping cannot collide two draws on one
    event label. Returns the minutes actually spent inside the mast envelope,
    each communications envelope, and at or below the repair speed.
    """
    own, ordered = state["own"], state["ordered"]
    spec = entity_spec(own)
    rates = spec.maneuver
    envelopes = []
    if spec.mast is not None:
        envelopes.append(("mast", spec.mast.allows))
    if spec.repair_maximum_speed_knots is not None:
        limit = spec.repair_maximum_speed_knots
        envelopes.append(("repair", lambda depth, speed, limit=limit: speed <= limit))
    for mode in spec.communication_modes:
        envelopes.append((mode.identifier, mode.allows))
    if arrays.suite_for(spec.identifier) is not None:
        limit = arrays.TOWED.deploy_speed_knots
        envelopes.append(
            ("towed_array", lambda depth, speed, limit=limit: speed <= limit)
        )
    within = {name: 0.0 for name, _allows in envelopes}
    remaining = minutes
    while remaining > 1e-9:
        span = min(MANEUVER_STEP, remaining)
        remaining -= span
        for name, allows in envelopes:
            if allows(own["depth"], own["speed"]):
                within[name] += span
        if rates is None:
            move(own, span)
            continue
        # The depth rate uses the speed held at the start of the increment,
        # matching the engine's start-of-step convention for every decision.
        depth_rate = rates.depth_rate(own["speed"])
        speed_rate = rates.speed_rate(own["speed"], ordered["speed"])
        turn, new_course = course_step(own["course"], ordered["course"],
                                       rates.turn_degrees_per_minute, span)
        new_speed = approach(own["speed"], ordered["speed"], speed_rate, span)
        # Position uses the mean course and speed over the increment rather
        # than either endpoint, so a turn does not make its whole distance
        # good on the new heading.
        move(own, span, course=(own["course"] + turn / 2) % 360,
             speed=(own["speed"] + new_speed) / 2)
        own["course"] = new_course
        own["speed"] = new_speed
        own["depth"] = approach(own["depth"], ordered["depth"], depth_rate, span)
    return {
        "mast_minutes": within.get("mast", 0.0),
        "repair_minutes": within.get("repair", 0.0),
        "envelopes": within,
    }


def settle_operating_mode(state):
    """Adopt the commanded mode once achieved speed and depth permit it."""
    own, ordered = state["own"], state["ordered"]
    commanded = ordered["operating_mode"]
    if own["operating_mode"] == commanded:
        return
    if not entity_spec(own).mode(commanded).allows(own["speed"], own["depth"]):
        return
    own["operating_mode"] = commanded
    report(state, "Engineering",
           f"Plant is now in the {commanded} operating mode.", "operating_mode")


def maneuver_view(state):
    """Report commanded and achieved settings side by side."""
    own, ordered = state["own"], state["ordered"]
    rates = entity_spec(own).maneuver
    axes = {}
    # Compare the stored values and round only for display: a commanded
    # fractional course must not read as permanently in progress.
    for field, places in (("course", 2), ("speed", 3), ("depth", 2), ("operating_mode", None)):
        achieved = own[field] if places is None else round(own[field], places)
        axes[field] = {"ordered": ordered[field], "achieved": achieved,
                       "in_progress": ordered[field] != own[field]}
    remaining = abs(ordered["depth"] - own["depth"])
    rate = rates.depth_rate(own["speed"]) if rates else 0
    axes["estimated_minutes_to_ordered_depth"] = round(remaining / rate, 1) if remaining and rate else 0
    axes["estimate_basis"] = ("Computed at the present speed. A commanded speed change alters "
                              "the depth rate and therefore this estimate.")
    return axes


def settings_text(state):
    """Report the achieved settings against the ones commanded."""
    own, ordered = state["own"], state["ordered"]
    return (f"Depth {own['depth']:.0f} feet for ordered {ordered['depth']:.0f} feet, "
            f"speed {own['speed']:.1f} knots for ordered {ordered['speed']:.1f} knots.")


def deferred_text(state, activity, inside_minutes, envelope_name="mast"):
    """Explain a deferred activity by the time actually spent in its envelope."""
    return (f"{activity} not carried out this step: {inside_minutes:g} of {TICK} minutes were "
            f"inside the {envelope_name} envelope, and the cycle needs the whole step. {settings_text(state)}")


def steady_text(state):
    own = state["own"]
    return (f"Steady on ordered course {own['course']:03.0f}, speed {own['speed']:.1f} knots, "
            f"depth {own['depth']:.0f} feet, {own['operating_mode']} mode.")


def entity_spec(entity):
    return get_spec(entity["spec"])


def contact_kind(entity):
    return entity_spec(entity).contact_domain.value


def entity_noise(entity):
    return entity_spec(entity).mode(entity["operating_mode"]).relative_noise


def make_entity(spec, **state):
    entity = {"spec": spec.identifier, **spec.initial_components(), **state}
    if not spec.envelope.allows(entity["speed"], entity["depth"]):
        raise ValueError(f"Initial state is outside the {spec.class_name} envelope.")
    if not spec.mode(entity["operating_mode"]).allows(
        entity["speed"], entity["depth"]
    ):
        raise ValueError(f"Initial state is outside the selected operating mode.")
    return entity


def advance_entity_components(entity):
    """Advance consumable resources over one shared integration step."""
    spec = entity_spec(entity)
    for model in spec.resources:
        amount = entity["resources"][model.identifier]
        drain = (
            model.hotel_rate_per_hour
            + model.speed_squared_rate_per_hour * entity["speed"] ** 2
        ) * TICK / 60
        recharge = dict(model.replenishment).get(entity["operating_mode"], 0)
        amount += recharge * TICK / 60 - drain
        entity["resources"][model.identifier] = round(
            min(model.capacity, max(0, amount)), 6
        )


def prepare_actor_step(state, actor, dice):
    """Apply bounded actor behavior without access to the player's hidden truth."""
    spec = entity_spec(actor)
    t = state["t"]
    if spec.category == EntityCategory.DIESEL_SUBMARINE:
        energy = actor["resources"]["battery_energy"]
        if energy <= 24 and actor["operating_mode"] != "snorkeling":
            actor["operating_mode"] = "snorkeling"
            actor["depth"] = 50
            actor["speed"] = min(actor["speed"], 7)
        elif energy >= 82 and actor["operating_mode"] == "snorkeling":
            actor["operating_mode"] = "battery"
            actor["depth"] = 250
    elif spec.category == EntityCategory.BIOLOGIC and t % 20 == 0:
        actor["operating_mode"] = dice.choose(
            f"biologic-mode:{actor['id']}:{t // 20}",
            [mode.identifier for mode in spec.modes],
            [0.35, 0.40, 0.25],
        )
        actor["course"] = (
            actor["course"]
            + dice.between(f"biologic-course:{actor['id']}:{t // 20}", -35, 35)
        ) % 360
        actor["speed"] = dice.between(
            f"biologic-speed:{actor['id']}:{t // 20}",
            spec.envelope.minimum_speed_knots,
            spec.mode(actor["operating_mode"]).maximum_speed_knots,
        )
        actor["depth"] = dice.between(
            f"biologic-depth:{actor['id']}:{t // 20}", 80, 900
        )
    elif spec.category == EntityCategory.FISHING_VESSEL and t % 40 == 0:
        actor["operating_mode"] = dice.choose(
            f"fishing-mode:{actor['id']}:{t // 40}",
            ["transit", "fishing"],
            [0.45, 0.55],
        )
        if actor["operating_mode"] == "fishing":
            actor["speed"] = dice.between(
                f"fishing-speed:{actor['id']}:{t // 40}", 1, 4
            )
            actor["course"] = (
                actor["course"]
                + dice.between(
                    f"fishing-course:{actor['id']}:{t // 40}", -70, 70
                )
            ) % 360


def report(state, department, text, category="routine", **extra):
    entry = {"id": f"R{len(state['reports']) + 1:04d}", "time": clock(state["t"]),
             "elapsed_minutes": state["t"], "department": department,
             "category": category, "text": text, **extra}
    state["reports"].append(entry)
    return entry


def assessments(track, elapsed_minutes=None):
    """Crew classification from recorded evidence. Elapsed time marks stale reports."""
    return crew_classification(track, elapsed_minutes)


def _assessment_decision(role):
    return role["favors"], role["confidence"]


def acoustic_window_db(state, dice):
    """One correlated quality draw per 20-minute window, applied to every contact."""
    quality = dice.between(f"acoustic-window:{state['t'] // 20}", 0.55, 1.15)
    return 10.0 * math.log10(quality)


def _focus_directivity_db(state, actor, mode, array_id):
    if mode != "focus":
        return 0.0
    focused = next((tr for tr in state["tracks"] if tr["id"] == state["focus"]), None)
    if (
        focused
        and focused["actor"] == actor["id"]
        and focused.get("receiver_id") == array_id
    ):
        return 2.0
    return -2.0


def reception_signal_excess(state, source, receiver, mode="passive", extra_di=0.0, array=None, array_spec=None):
    """Signal excess through the hidden column at the depths actually held."""
    own = state["own"]
    listening_own_ship = receiver.get("id") == own.get("id")
    pump = bool(state.get("pump_fault")) if listening_own_ship else False
    water = state["environment"]["true"]["water_depth_feet"]
    if array_spec is None:
        array_id = None if array is None else array.get("id")
        array_spec = arrays.array_spec(receiver.get("spec"), array_id)
    if array is None:
        array = arrays.snapshot(array_spec, receiver, water_depth_feet=water)
    if mode == "active":
        contact = source if source.get("id") != own.get("id") else receiver
        frequency = acoustics.ACTIVE_FREQUENCY_HZ
        gain = arrays.frequency_gain_db(array_spec, frequency)
        loss = acoustics.transmission_loss(
            state["environment"]["true"],
            distance(own, contact),
            own["depth"],
            contact["depth"],
            frequency,
        )
        excess = acoustics.signal_excess_db(
            acoustics.ACTIVE_SOURCE_LEVEL_DB,
            loss.transmission_loss_db,
            acoustics.noise_level_db(
                frequency, own["speed"], pump, array_spec=array_spec
            ),
            array["directivity_index_db"] + extra_di + gain,
            acoustics.ACTIVE_DT_DB,
            two_way=True,
            target_strength=acoustics.target_strength_db(contact_kind(contact)),
        )
        return excess, loss
    frequency = acoustics.representative_frequency_hz(contact_kind(source))
    gain = arrays.frequency_gain_db(array_spec, frequency)
    loss = acoustics.transmission_loss(
        state["environment"]["true"],
        distance(source, receiver),
        source["depth"],
        array["depth_feet"],
        frequency,
    )
    excess = acoustics.signal_excess_db(
        acoustics.source_level_db(contact_kind(source), entity_noise(source)),
        loss.transmission_loss_db,
        acoustics.noise_level_db(
            frequency, receiver["speed"], pump, array_spec=array_spec
        ),
        array["directivity_index_db"] + extra_di + gain,
        acoustics.PASSIVE_DT_DB,
    )
    return excess, loss


def _listen_key(elapsed, mode, array_id):
    return f"{elapsed}:{mode}:{array_id}"


def _look_key(actor_id, array_id, elapsed, mode):
    return f"{actor_id}:{array_id}:{elapsed}:{mode}"


def _track_for_receiver(state, actor_id, array_id):
    return next(
        (
            tr for tr in state["tracks"]
            if tr["actor"] == actor_id and tr.get("receiver_id") == array_id
        ),
        None,
    )


def observe_contact(state, actor, dice, mode="passive", opening=False, array_spec=None):
    own, t = state["own"], state["t"]
    water = state["environment"]["true"]["water_depth_feet"]
    charted = state["environment"]["measured"]["water_depth_feet"]
    if array_spec is None:
        suite = arrays.suite_for(own.get("spec"))
        array_spec = suite[0] if suite else arrays.GENERIC_HULL
    array_id = array_spec.identifier
    look = _look_key(actor["id"], array_id, t, mode)
    state.setdefault("looks", [])
    if look in state["looks"]:
        return
    window = t // spectra.CORRELATION_WINDOW_MINUTES
    evidence_window = f"A{window:03d}"
    track = _track_for_receiver(state, actor["id"], array_id)
    true_bearing = bearing(own, actor)
    towed = state.get("towed") or {}
    unstable = bool(
        array_spec.deployable
        and towed.get("deployment") == arrays.STREAMED
        and arrays.is_unstable(towed, t)
    )
    array = arrays.snapshot(
        array_spec,
        own,
        true_bearing_deg=true_bearing,
        water_depth_feet=water,
        unstable=unstable,
    )
    extra_di = _focus_directivity_db(state, actor, mode, array_id)
    window_db = acoustic_window_db(state, dice)
    measurement = spectra.measure_contact(
        state, actor, own, mode, dice,
        extra_di=extra_di, focused=extra_di > 0, window_db=window_db,
        array=array, array_spec=array_spec, unstable=unstable,
    )
    active_excess = None
    active_detected = False
    if mode == "active":
        active_excess, _loss = reception_signal_excess(
            state, actor, own, mode, extra_di, array=array, array_spec=array_spec,
        )
        active_excess += window_db
        active_detected = dice.u(
            f"detect:{actor['id']}:{t}:{mode}:{array_id}"
        ) < acoustics.detection_probability(active_excess)
    array_bias = state.get("array_bearing_bias", {}).get(array_id, 0.0)
    measured_bearing = (
        true_bearing + state["bearing_bias"] + array_bias
        + dice.between(f"bearing:{actor['id']}:{t}:{mode}:{array_id}", -4, 4)
    ) % 360
    covered = arrays.keeps_observation(array["coverage"])
    detected = covered and (measurement["any_detected"] or active_detected)
    state["looks"].append(look)
    if track is not None:
        track.setdefault("listens", []).append(_listen_key(t, mode, array_id))
    if not opening and not detected:
        return
    if opening and not covered:
        array = dict(array)
        array["coverage"] = arrays.IN_BEAM
    new = track is None
    if new:
        track = {
            "id": f"S{len(state['tracks']) + 1:02d}",
            "actor": actor["id"],
            "receiver_id": array_id,
            "receiver_kind": array_spec.kind,
            "first": t, "last": t, "evidence": {}, "observations": [],
            "visual": None, "listens": [_listen_key(t, mode, array_id)],
        }
        state["tracks"].append(track)
    old_assessment = _assessment_decision(assessments(track, t)["supervisor"])
    track["last"] = t
    if measurement["feature"] and detected:
        track["evidence"][str(window)] = measurement["feature"]
    contributions = []
    if detected and measurement["any_detected"]:
        contributions.append(measurement["best_excess_db"])
    if detected and active_detected:
        contributions.append(active_excess)
    strength = acoustics.reception_strength(max(contributions)) if contributions else "weak"
    public_array = arrays.snapshot(
        array_spec,
        own,
        true_bearing_deg=true_bearing,
        water_depth_feet=charted,
        unstable=unstable,
    )
    public_receiver = arrays.public_receiver(public_array)
    error_sources = arrays.error_sources(array_spec, evidence_window, array["coverage"])
    obs = {
        "time": clock(t),
        "elapsed_minutes": t,
        "bearing_true": round(measured_bearing) % 360,
        "own_east_nm": round(own["x"], 3),
        "own_north_nm": round(own["y"], 3),
        "own_depth_feet": round(own["depth"], 2),
        "own_course_true": round(own["course"] % 360, 2),
        "own_speed_knots": round(own["speed"], 3),
        "source": mode,
        "strength": strength,
        "description": measurement["description"],
        "range_estimate_nm": None,
        "evidence_window": evidence_window,
        "spectrum": measurement["public"],
        "receiver": public_receiver,
        "error_sources": error_sources,
    }
    if mode == "active" and active_detected and detected:
        delta = distance(own, actor)
        obs["range_estimate_nm"] = round(
            max(0.1, delta * dice.between(f"active-range:{actor['id']}:{t}", 0.88, 1.12)),
            1,
        )
    track["observations"].append(obs)
    entry = report(
        state,
        "Sonar",
        (
            f"{track['id']} ({array_id}): bearing {obs['bearing_true']:03d} true, "
            f"{strength} reception, {array['coverage'].replace('_', ' ')}. {obs['description']}"
        ),
        "new_contact" if new else "contact_update",
        contact=track["id"],
        observation=obs,
    )
    obs["report_id"] = entry["id"]
    new_assessment = _assessment_decision(assessments(track, t)["supervisor"])
    if not new and new_assessment[1] == "high" and new_assessment != old_assessment:
        report(
            state,
            "Sonar supervisor",
            f"{track['id']}: assessment now favors {new_assessment[0]} with high confidence; this remains an assessment.",
            "classification_change",
            contact=track["id"],
        )


def new_world(seed):
    state = {"t": 0, "platform": KESTREL.identifier,
             "own": make_entity(KESTREL, id="own-kestrel", x=0.0, y=0.0,
                                course=90.0, speed=5.0, depth=400.0),
             "ordered": {"course": 90.0, "speed": 5.0, "depth": 400.0,
                         "operating_mode": KESTREL.default_mode},
             "actors": [], "tracks": [], "reports": [], "rng_trace": [], "focus": None,
             "pump_fault": False, "repair_progress": 0, "received": [], "sent": [],
             "antennas": initial_antennas(KESTREL), "radio_intercepts": [],
             "towed": arrays.initial_towed_state(KESTREL.identifier),
             "looks": [],
             "ended": False, "end_reason": None, "deadline_announced": False,
             "maneuvering": False, "last_action_report_start": 0}
    dice = Dice(seed, state["rng_trace"])
    state["environment"] = acoustics.initialize_environment(dice)
    state["bearing_bias"] = dice.between(
        "initial:bearing-bias",
        -arrays.ENVIRONMENT_BEARING_BIAS_DEGREES,
        arrays.ENVIRONMENT_BEARING_BIAS_DEGREES,
    )
    combined = ACOUSTIC_BIAS_DEGREES
    shared = state["bearing_bias"]
    state["array_bearing_bias"] = {
        spec.identifier: dice.between(
            f"initial:bearing-bias:{spec.identifier}",
            -combined - shared,
            combined - shared,
        )
        for spec in arrays.suite_for(KESTREL.identifier)
    }
    state["equipment_susceptibility"] = dice.between("initial:equipment", 0.4, 1.6)
    state["radio_reliability"] = dice.between("initial:radio", 0.58, 0.94)
    kind = dice.choose("initial:primary-kind", KINDS, [0.46, 0.44, 0.10])
    primary_spec = {
        "surface": MERCHANT,
        "submerged": DART,
        "biologic": BIOLOGIC,
    }[kind]
    primary = make_entity(
        primary_spec,
        id="actor-a",
        x=dice.between("initial:a-x", 5.5, 10),
        y=dice.between("initial:a-y", -2.2, 4),
        course=dice.between("initial:a-course", 70, 115),
        speed=dice.between(
            "initial:a-speed",
            max(3.5, primary_spec.envelope.minimum_speed_knots),
            min(8.5, primary_spec.mode(primary_spec.default_mode).maximum_speed_knots),
        ),
        depth=(
            dice.between("initial:a-depth", 200, 650)
            if kind == "submerged"
            else dice.between("initial:a-depth", 80, 600)
            if kind == "biologic"
            else 0
        ),
        aware=False,
        last_heard=None,
        evaded=False,
        name=dice.choose("initial:a-name", ["Cormorant", "Morrow", "Solace"]),
        intent=(
            "Transit east through the passage; avoid an observer if one is detected."
            if kind == "submerged"
            else "Continue the established passage."
        ),
    )
    state["actors"].append(primary)
    # Background traffic is fixed at initialization too; no adaptive reinforcements.
    backgrounds = (
        (MERCHANT, "Larkspur"),
        (FISHER, "Bracken"),
        (WARSHIP, "Vigil"),
    )
    for i, (spec, name) in enumerate(backgrounds):
        maximum = min(12, spec.mode(spec.default_mode).maximum_speed_knots)
        state["actors"].append(
            make_entity(
                spec,
                id=f"actor-{i + 2}",
                x=dice.between(f"initial:b{i}-x", 15, 29),
                y=dice.between(f"initial:b{i}-y", -11, 11),
                course=dice.between(f"initial:b{i}-course", 230, 290),
                speed=dice.between(
                    f"initial:b{i}-speed",
                    max(3, spec.envelope.minimum_speed_knots),
                    maximum,
                ),
                depth=0,
                aware=False,
                last_heard=None,
                evaded=False,
                name=name,
                intent="Maintain an established westbound passage.",
            )
        )
    shore_correct = dice.u("initial:shore-source") < 0.76
    shore_type = kind if shore_correct else dice.choose("initial:shore-error", [k for k in KINDS if k != kind])
    estimate = {"surface": "Coastal watch reports a possible surface vessel in the eastern approach.",
                "submerged": "Coastal watch reports a possible submerged contact in the eastern approach.",
                "biologic": "Coastal watch reports biological activity that may account for some acoustic reports."}[shore_type]
    state["bulletins"] = [
        {"id": "INTEL-0330", "available": 20, "text": "0330 intelligence update: " + estimate + " Source confidence: moderate; exact track unavailable. This is an independent shore report, not confirmed identification."},
        {"id": "OPS-0510", "available": 120, "text": "0510 operations update: assessment deadline 0730 and relief station time 0800 remain unchanged. Commercial schedules are incomplete; absence from the list does not establish military identity."},
    ]
    spectra.assign_persistent_signature(state["own"], dice)
    for actor in state["actors"]:
        spectra.assign_persistent_signature(actor, dice)
    report(state, "Navigation", "0310. Local position (0 east, 0 north). Course 090, speed 5 knots, depth 400 feet. RELIEF lies 24 nautical miles east.")
    report(state, "Environment", acoustics.environment_report_text(state["environment"]))
    report(state, "Engineering", "Propulsion, sonar, communications and the auxiliary plant are available.")
    observe_contact(state, primary, dice, opening=True, array_spec=arrays.HULL)
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
    if entity_spec(actor).category not in (
        EntityCategory.SSN,
        EntityCategory.DIESEL_SUBMARINE,
    ):
        return
    own, t = state["own"], state["t"]
    frequency = acoustics.representative_frequency_hz(
        "submerged", "active" if active else "passive"
    )
    array = acoustics.receiving_array(actor)
    loss = acoustics.transmission_loss(
        state["environment"]["true"],
        distance(own, actor),
        own["depth"],
        array["depth_feet"],
        frequency,
    )
    source_level = acoustics.own_ship_source_level_db(own["speed"], active=active)
    noise = acoustics.noise_level_db(
        frequency, actor["speed"], baseline=acoustics.OPPONENT_AMBIENT_NL_AT_1KHZ_DB
    )
    excess = acoustics.signal_excess_db(
        source_level,
        loss.transmission_loss_db,
        noise,
        acoustics.OPPONENT_DIRECTIVITY_DB,
        acoustics.OPPONENT_DT_DB,
    )
    probability = acoustics.detection_probability(
        excess, ceiling=0.70 if not active else 0.98
    )
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
        if contact_kind(actor) != "surface":
            continue
        gap = distance(state["own"], actor)
        chance = max(0, 0.92 - gap / 10)
        if dice.u(f"mast:{actor['id']}:{state['t']}") >= chance:
            continue
        track = next(
            (
                tr for tr in state["tracks"]
                if tr["actor"] == actor["id"] and tr.get("receiver_kind") == "visual"
            ),
            None,
        )
        if track is None:
            # Scope gets a bearing even if acoustic reception was absent.
            # A visual history is never attached to an acoustic receiver track.
            track = {"id": f"S{len(state['tracks'])+1:02d}", "actor": actor["id"], "first": state["t"],
                     "last": state["t"], "evidence": {}, "observations": [], "visual": None,
                     "receiver_id": None, "receiver_kind": "visual"}
            state["tracks"].append(track)
            report(state, "Scope", f"New visual contact designated {track['id']}.", "new_contact")
        b = round((bearing(state["own"], actor) + dice.between(f"mast-bearing:{actor['id']}:{state['t']}", -2, 2)) % 360) % 360
        track["last"] = state["t"]
        visual = {"time": clock(state["t"]), "elapsed_minutes": state["t"], "bearing_true": b,
                  "description": "Surface vessel observed; no military features resolved.",
                  "name_read": actor["name"] if gap < 2.5 else None}
        track["visual"] = visual
        obs = {"time": clock(state["t"]), "elapsed_minutes": state["t"], "bearing_true": b,
               "own_east_nm": round(state["own"]["x"], 3), "own_north_nm": round(state["own"]["y"], 3),
               "own_depth_feet": round(state["own"]["depth"], 2),
               "own_course_true": round(state["own"]["course"] % 360, 2),
               "own_speed_knots": round(state["own"]["speed"], 3),
               "source": "mast", "strength": "visual", "description": visual["description"],
               "range_estimate_nm": None, "evidence_window": None,
               "receiver": {"id": "periscope", "role": "visual", "coverage": "visual"},
               "error_sources": {
                   "environment_window": None,
                   "shared_bearing_bias": None,
                   "array_bearing_bias": None,
                   "frequency_bias_environment": None,
                   "frequency_bias_array": None,
                   "self_noise": None,
                   "coverage": "visual",
                   "correlation": "A visual bearing is not an acoustic receiver history.",
               }}
        track["observations"].append(obs)
        entry = report(state, "Scope", f"{track['id']}, bearing {b:03d}: {visual['description']}", "classification_change", contact=track["id"], visual=visual)
        obs["report_id"] = entry["id"]
        visual["report_id"] = entry["id"]


def initial_antennas(spec):
    """Deployment state for each antenna that has an implemented link mode."""
    antennas = {}
    for mode in spec.communication_modes:
        if mode.antenna in antennas:
            continue
        equipment = next(
            item for item in spec.communications if item.identifier == mode.antenna
        )
        antennas[mode.antenna] = {
            "deployment": equipment.initial_state,
            "progress_minutes": 0,
        }
    return antennas


def link_threshold(state, mode):
    """Hidden patrol reliability shifted by the mode's published offset."""
    return state["radio_reliability"] + mode.reliability_offset


def public_antennas(state):
    """Player-known antenna deployment. Reliability and traffic stay hidden."""
    spec = entity_spec(state["own"])
    published = {}
    for antenna, record in state["antennas"].items():
        modes = [mode for mode in spec.communication_modes if mode.antenna == antenna]
        persisted = next((mode for mode in modes if mode.persists_deployed), None)
        published[antenna] = {
            "deployment": record["deployment"],
            "progress_minutes": record["progress_minutes"],
            "deployment_minutes": persisted.deployment_minutes if persisted else 0,
            "retrieval_minutes": persisted.retrieval_minutes if persisted else 0,
            "persists_deployed": persisted is not None,
            "modes": [mode.identifier for mode in modes],
        }
    return published


def public_sonar_receivers(state):
    """Player-known receiver depth, coverage and deployment."""
    own = state["own"]
    charted = state["environment"]["measured"]["water_depth_feet"]
    towed = state.get("towed") or {}
    unstable = arrays.is_unstable(towed, state["t"])
    rows = []
    suite = arrays.suite_for(own.get("spec")) or (arrays.GENERIC_HULL,)
    for spec in suite:
        snap = arrays.snapshot(
            spec, own, water_depth_feet=charted, unstable=unstable and spec.deployable,
        )
        public = arrays.public_receiver(snap)
        public["listening"] = arrays.is_listening(spec, own)
        public["geometry_note"] = spec.geometry_note
        rows.append(public)
    return rows


def link_snapshot(state, mode, cycle, emitted):
    record = state["antennas"][mode.antenna]
    return {
        "mode": mode.identifier,
        "antenna": mode.antenna,
        "direction": mode.direction.value,
        "deployment": record["deployment"],
        "progress_minutes": record["progress_minutes"],
        "cycle": cycle,
        "emitted": emitted,
        "latency_minutes": mode.latency_minutes,
    }


def _set_antenna(state, antenna, deployment, progress):
    state["antennas"][antenna]["deployment"] = deployment
    state["antennas"][antenna]["progress_minutes"] = progress
    state["own"]["equipment"][antenna] = deployment


def streamed_modes(state):
    """Persisted modes whose antenna is no longer housed."""
    spec = entity_spec(state["own"])
    active = []
    for mode in spec.communication_modes:
        if not mode.persists_deployed:
            continue
        if state["antennas"][mode.antenna]["deployment"] != "stowed":
            active.append(mode)
    return active


def apply_radio_exposure(state, mode, channel):
    """Intercept a radiated transmission on the link's existing window draw.

    No second draw is taken. A surface combatant inside the published range
    intercepts when that draw is below the link threshold plus the published
    margin. Other categories are unchanged by the emission.
    """
    if not mode.emits or mode.intercept_range_nm is None:
        return
    if channel >= link_threshold(state, mode) + mode.intercept_margin:
        return
    own = state["own"]
    for actor in state["actors"]:
        if entity_spec(actor).category != EntityCategory.SURFACE_WARSHIP:
            continue
        if distance(own, actor) > mode.intercept_range_nm:
            continue
        actor["aware"] = True
        actor["last_heard"] = {
            "x": own["x"],
            "y": own["y"],
            "time": state["t"],
            "source": "radio_intercept",
        }
        state["radio_intercepts"].append({
            "actor": actor["id"],
            "time": state["t"],
            "mode": mode.identifier,
        })


def _pending_bulletins(state, mode, elapsed):
    ready_at = elapsed
    return [
        bulletin for bulletin in state["bulletins"]
        if bulletin["available"] + mode.latency_minutes <= ready_at
        and bulletin["id"] not in state["received"]
    ]


def apply_communications(state, dice, order, achieved):
    """Resolve one communications step from the mode the order named."""
    activity = order["activity"]
    if activity not in ("receive", "transmit", "retrieve"):
        return
    mode = entity_spec(state["own"]).link_mode(order["link"])
    inside = achieved["envelopes"].get(mode.identifier, 0.0)
    if inside < TICK:
        report(
            state, "Ship control",
            deferred_text(state, activity, inside, mode.identifier),
            "activity_deferred",
            link=link_snapshot(state, mode, "deferred", False),
        )
        return
    if activity == "retrieve":
        _retrieve_antenna(state, mode)
        return
    record = state["antennas"][mode.antenna]
    if mode.persists_deployed and record["deployment"] != "streamed":
        _deploy_antenna(state, mode)
        return
    _attempt_link(state, dice, order, mode)


def _deploy_antenna(state, mode):
    record = state["antennas"][mode.antenna]
    progress = record["progress_minutes"] + TICK
    if progress >= mode.deployment_minutes:
        _set_antenna(state, mode.antenna, "streamed", 0)
        report(
            state, "Radio",
            f"{mode.antenna} is streamed. A later step inside the "
            f"{mode.identifier} envelope can copy traffic.",
            "antenna_deployed",
            link=link_snapshot(state, mode, "deployed", False),
        )
        return
    _set_antenna(state, mode.antenna, "deploying", progress)
    report(
        state, "Radio",
        f"{mode.antenna} deployment credited {progress:g} of "
        f"{mode.deployment_minutes:g} minutes.",
        "antenna_progress",
        link=link_snapshot(state, mode, "deploying", False),
    )


def _retrieve_antenna(state, mode):
    record = state["antennas"][mode.antenna]
    if record["deployment"] != "retrieving":
        progress = TICK
    else:
        progress = record["progress_minutes"] + TICK
    if progress >= mode.retrieval_minutes:
        _set_antenna(state, mode.antenna, "stowed", 0)
        report(
            state, "Radio",
            f"{mode.antenna} retrieved and housed.",
            "retrieval_complete",
            link=link_snapshot(state, mode, "housed", False),
        )
        return
    _set_antenna(state, mode.antenna, "retrieving", progress)
    report(
        state, "Radio",
        f"{mode.antenna} retrieval credited {progress:g} of "
        f"{mode.retrieval_minutes:g} minutes.",
        "antenna_progress",
        link=link_snapshot(state, mode, "retrieving", False),
    )


def _attempt_link(state, dice, order, mode):
    elapsed = state["t"]
    channel = dice.u(f"radio-window:{elapsed // 20}")
    emitted = bool(mode.emits)
    if emitted:
        apply_radio_exposure(state, mode, channel)
    cycle = "raised_and_housed" if not mode.persists_deployed else "streamed_attempt"
    snapshot = link_snapshot(state, mode, cycle, emitted)
    if channel >= link_threshold(state, mode):
        if emitted:
            text = "Transmission radiated; no usable link this interval; cause undetermined."
        else:
            text = "No usable link this interval; cause undetermined."
        report(state, "Radio", text, "radio_failure", link=snapshot)
        return
    if mode.direction == LinkDirection.RECEIVE:
        pending = _pending_bulletins(state, mode, elapsed)
        for bulletin in pending:
            state["received"].append(bulletin["id"])
            report(
                state, "Radio", bulletin["text"], "message_received",
                message_id=bulletin["id"], link=dict(snapshot),
            )
        if not pending:
            report(
                state, "Radio",
                "Usable link established; no new message available.",
                "radio_empty", link=snapshot,
            )
        return
    sent = {
        "time": clock(elapsed),
        "elapsed_minutes": elapsed,
        "assessment": order["assessment"],
        "text": order["message"],
        "basis": order["basis"],
        "mode": mode.identifier,
    }
    state["sent"].append(sent)
    report(
        state, "Radio",
        "Assessment transmitted and acknowledged. The transmission radiated.",
        "transmission_complete", transmitted=sent, link=snapshot,
    )


def _set_towed(state, deployment, progress=None, unstable_until=None):
    record = state["towed"]
    record["deployment"] = deployment
    if progress is not None:
        record["progress_minutes"] = progress
    if unstable_until is not None:
        record["unstable_until"] = unstable_until
    state["own"]["equipment"][arrays.TOWED_ID] = deployment


def _towed_is_turning(state, course_before):
    return (
        abs((state["own"]["course"] - course_before + 180) % 360 - 180) > 0.05
        or maneuver_view(state)["course"]["in_progress"]
    )


def _update_towed_stability(state, course_before):
    record = state.get("towed")
    if record is None or record["deployment"] not in (arrays.STREAMED, arrays.STREAMING):
        return
    turning = _towed_is_turning(state, course_before)
    listening = record["deployment"] == arrays.STREAMED
    if turning:
        until = state["t"] + arrays.TURN_SETTLE_MINUTES
        if record["unstable_until"] < until:
            was_stable = not arrays.is_unstable(record, state["t"])
            record["unstable_until"] = until
            if listening and was_stable:
                report(
                    state,
                    "Sonar",
                    "Towed array is unstable during the turn. That receiver "
                    "keeps no bearings until it settles; cable shape is not calculated.",
                    "array_unstable",
                )
        return
    if record["unstable_until"] and not arrays.is_unstable(record, state["t"]):
        if listening:
            report(
                state,
                "Sonar",
                "Towed array has settled after the turn.",
                "array_stable",
            )
        record["unstable_until"] = 0


def _begin_towed_activity(state, order):
    """Mark streaming or recovery before listening so the towed receiver does not keep bearings."""
    record = state.get("towed")
    if record is None:
        return
    activity = order["activity"]
    if activity == "stream_array" and record["deployment"] == arrays.STOWED:
        _set_towed(state, arrays.STREAMING, record["progress_minutes"])
    elif activity == "recover_array" and record["deployment"] == arrays.STREAMED:
        _set_towed(state, arrays.RECOVERING, record["progress_minutes"])


def apply_towed_array(state, order, achieved):
    """Stream or recover the towed array using productive minutes at deploy speed."""
    activity = order["activity"]
    record = state.get("towed")
    if record is None:
        report(state, "Sonar", "This platform has no modeled towed array.", "activity_deferred")
        return
    spec = arrays.TOWED
    inside = achieved["envelopes"].get("towed_array", 0.0)
    if inside < TICK:
        report(
            state,
            "Ship control",
            (
                f"{activity} credited {inside:g} of {TICK} minutes at or below "
                f"{spec.deploy_speed_knots:g} knots. {settings_text(state)}"
            ),
            "activity_deferred",
        )
    if activity == "stream_array":
        if record["deployment"] == arrays.STREAMED:
            report(state, "Sonar", "Towed array is already streamed.", "array_streamed")
            return
        progress = record["progress_minutes"] + inside
        if progress >= spec.deployment_minutes:
            _set_towed(state, arrays.STREAMED, 0)
            report(
                state,
                "Sonar",
                "Towed array is streamed. It listens at keel depth plus the "
                "published offset; cable shape is not calculated.",
                "array_streamed",
            )
            if arrays.is_unstable(record, state["t"]):
                report(
                    state,
                    "Sonar",
                    "Towed array is unstable during the turn. That receiver "
                    "keeps no bearings until it settles; cable shape is not calculated.",
                    "array_unstable",
                )
            return
        _set_towed(state, arrays.STREAMING, progress)
        report(
            state,
            "Sonar",
            f"Towed array streaming credited {progress:g} of "
            f"{spec.deployment_minutes:g} minutes.",
            "array_progress",
        )
        return
    if record["deployment"] == arrays.STOWED:
        report(state, "Sonar", "Towed array is already housed.", "array_stowed")
        return
    progress = record["progress_minutes"] + inside
    if progress >= spec.retrieval_minutes:
        _set_towed(state, arrays.STOWED, 0, unstable_until=0)
        report(state, "Sonar", "Towed array recovered and housed.", "array_stowed")
        return
    _set_towed(state, arrays.RECOVERING, progress)
    report(
        state,
        "Sonar",
        f"Towed array recovery credited {progress:g} of "
        f"{spec.retrieval_minutes:g} minutes.",
        "array_progress",
    )


def tick_world(state, dice, order, first_tick):
    state["t"] += TICK
    t = state["t"]
    active = order["activity"] == "active" and first_tick
    # Decisions use the common start-of-step geometry. Movement is then
    # integrated for every platform over the same elapsed interval.
    for actor in state["actors"]:
        prepare_actor_step(state, actor, dice)
        opponent_step(state, actor, dice, active)
    before_course = state["own"]["course"]
    achieved = advance_own(state, TICK)
    settle_operating_mode(state)
    advance_entity_components(state["own"])
    for actor in state["actors"]:
        move(actor, TICK)
        advance_entity_components(actor)
    _begin_towed_activity(state, order)
    _update_towed_stability(state, before_course)
    if active:
        report(state, "Sonar", "One active acoustic transmission made.", "emission")
    mode = "active" if active else "focus" if order["activity"] == "focus" else "passive"
    for spec in arrays.listening_specs(state["own"]):
        listen_mode = mode
        if active and spec.identifier != arrays.HULL_ID:
            listen_mode = "passive"
        for actor in state["actors"]:
            observe_contact(state, actor, dice, listen_mode, array_spec=spec)
    for tr in state["tracks"]:
        if t - tr["last"] == 20:
            report(state, "Sonar", f"{tr['id']}: no further observation for 20 minutes. Last report is now stale; contact fate unknown.", "contact_lost", contact=tr["id"])
    if order["activity"] == "mast" and achieved["mast_minutes"] < TICK:
        # The published cycle is five minutes of deployment, operation and
        # recovery; a step spent reaching the envelope cannot contain one.
        report(state, "Ship control",
               deferred_text(state, order["activity"], achieved["mast_minutes"]),
               "activity_deferred")
    elif order["activity"] == "mast":
        mast_observations(state, dice)
    elif order["activity"] in ("receive", "transmit", "retrieve"):
        apply_communications(state, dice, order, achieved)
    elif order["activity"] in ("stream_array", "recover_array"):
        apply_towed_array(state, order, achieved)
    if order["activity"] == "repair":
        if state["pump_fault"]:
            if achieved["repair_minutes"] < TICK:
                report(state, "Engineering",
                       f"Repair credited {achieved['repair_minutes']:g} of {TICK} minutes at or below "
                       f"{entity_spec(state['own']).repair_maximum_speed_knots:g} knots. {settings_text(state)}",
                       "activity_deferred")
            state["repair_progress"] += achieved["repair_minutes"]
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
    if state["maneuvering"] and not any(
            maneuver_view(state)[field]["in_progress"]
            for field in ("course", "speed", "depth", "operating_mode")):
        state["maneuvering"] = False
        report(state, "Ship control", steady_text(state), "maneuver_complete")
    if t >= REPORT_DUE and not state["deadline_announced"]:
        state["deadline_announced"] = True
        report(state, "Navigation", "0730 assessment deadline reached.", "deadline")
    if t >= END:
        state["ended"] = True
        state["end_reason"] = "0800 relief time reached"
        report(state, "Navigation", "0800. Patrol exercise complete; the debrief can now be requested.", "exercise_end")


def require(order, field):
    """Reject an unstated field rather than choosing a value for the captain."""
    if field not in order:
        raise ValueError(
            f"{field} must be stated explicitly; the engine supplies no default. "
            "No time has elapsed and nothing changed."
        )


def validate_order(raw, state):
    allowed = {"id", "minutes", "course", "speed", "depth", "operating_mode", "activity", "focus", "link", "assessment", "message", "basis", "interrupt_on", "expected_turn"}
    if not isinstance(raw, dict) or set(raw) - allowed:
        raise ValueError("Order must be an object using only the documented fields.")
    if not isinstance(raw.get("id"), str) or not 1 <= len(raw["id"]) <= 80:
        raise ValueError("A unique order id of 1 to 80 characters is required.")
    order = copy.deepcopy(raw)
    # No field is defaulted. An unstated setting is a rejected order, never an
    # assumed one: the engine must not choose a depth, speed, course, plant
    # lineup, duration or interrupt policy that the captain did not state.
    require(order, "activity")
    activity = order["activity"]
    if activity not in ("listen", "focus", "active", "mast", "receive", "transmit", "retrieve", "repair", "stream_array", "recover_array", "end"):
        raise ValueError("Unsupported activity. No time has elapsed; agree on an applicable rule before proceeding.")
    if activity == "end":
        if set(raw) - {"id", "activity", "expected_turn"}:
            raise ValueError("An end order takes only id, expected_turn and activity; it has no duration or other action.")
        return order
    require(order, "minutes")
    minutes = order["minutes"]
    if type(minutes) is not int or not (5 <= minutes <= 60 and minutes % 5 == 0):
        raise ValueError("Use 5 to 60 minutes in five-minute steps.")
    spec = entity_spec(state["own"])
    envelope = spec.envelope
    for field, low, high in (("course", 0, 359.999999),
                             ("speed", envelope.minimum_speed_knots, envelope.maximum_speed_knots),
                             ("depth", envelope.minimum_depth_feet, envelope.maximum_depth_feet)):
        require(order, field)
        value = order[field]
        if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
            raise ValueError(f"{field} must be between {low} and {high} in the fictional game envelope.")
    require(order, "operating_mode")
    selected_mode = order["operating_mode"]
    if not isinstance(selected_mode, str):
        raise ValueError("operating_mode must name a published mode.")
    mode = spec.mode(selected_mode)
    if not mode.allows(order["speed"], order["depth"]):
        raise ValueError(
            f"The {selected_mode} operating mode does not allow the ordered speed/depth."
        )
    for streamed in streamed_modes(state):
        if not streamed.allows(order["depth"], order["speed"]):
            record = state["antennas"][streamed.antenna]
            raise ValueError(
                f"{streamed.antenna} is {record['deployment']} and limits depth to "
                f"{streamed.minimum_depth_feet:g}–{streamed.maximum_depth_feet:g} feet "
                f"and speed to {streamed.maximum_speed_knots:g} knots until it is "
                "retrieved. No time has elapsed."
            )
    towed_limit = arrays.towed_speed_limit_knots(state.get("towed"))
    if towed_limit is not None and order["speed"] > towed_limit:
        record = state["towed"]
        raise ValueError(
            f"towed_array is {record['deployment']} and limits speed to "
            f"{towed_limit:g} knots until it is housed. No time has elapsed."
        )
    if activity == "mast" and (
        spec.mast is None or not spec.mast.allows(order["depth"], order["speed"])
    ):
        if spec.mast is None:
            raise ValueError("This platform has no implemented mast activity.")
        raise ValueError(f"Mast envelope: {spec.mast.minimum_depth_feet:g} to {spec.mast.maximum_depth_feet:g} feet and at most {spec.mast.maximum_speed_knots:g} knots. Include those orders explicitly; no time has elapsed.")
    if activity in ("receive", "transmit", "retrieve"):
        require(order, "link")
        if not isinstance(order["link"], str):
            raise ValueError("link must name a published communications mode.")
        link_mode = spec.link_mode(order["link"])
        if activity == "receive" and link_mode.direction != LinkDirection.RECEIVE:
            raise ValueError(
                f"{link_mode.identifier} is a transmit mode. Name a receive mode. "
                "No time has elapsed."
            )
        if activity == "transmit" and link_mode.direction != LinkDirection.TRANSMIT:
            raise ValueError(
                f"{link_mode.identifier} is receive-only. Name a transmit mode. "
                "No time has elapsed."
            )
        if activity == "retrieve" and not link_mode.persists_deployed:
            raise ValueError(
                f"{link_mode.identifier} houses its antenna inside the link cycle. "
                "No separate retrieve order applies, and no time has elapsed."
            )
        if not link_mode.allows(order["depth"], order["speed"]):
            raise ValueError(
                f"{link_mode.identifier} envelope: {link_mode.minimum_depth_feet:g} to "
                f"{link_mode.maximum_depth_feet:g} feet and at most "
                f"{link_mode.maximum_speed_knots:g} knots. Include those orders "
                "explicitly; no time has elapsed."
            )
        deployment = state["antennas"][link_mode.antenna]["deployment"]
        if activity == "retrieve" and deployment == "stowed":
            raise ValueError(
                f"{link_mode.antenna} is already housed. No time has elapsed."
            )
        if activity == "receive" and link_mode.persists_deployed and deployment == "retrieving":
            raise ValueError(
                f"{link_mode.antenna} retrieval is in progress. Finish that retrieve "
                "order before streaming again. No time has elapsed."
            )
    elif "link" in order:
        raise ValueError("The link field requires receive, transmit, or retrieve activity.")
    if activity == "repair" and (
        spec.repair_maximum_speed_knots is None
        or order["speed"] > spec.repair_maximum_speed_knots
    ):
        if spec.repair_maximum_speed_knots is None:
            raise ValueError("This platform has no implemented repair activity.")
        raise ValueError(f"Repair requires {spec.repair_maximum_speed_knots:g} knots or less in the game.")
    if activity in ("stream_array", "recover_array"):
        record = state.get("towed")
        if record is None:
            raise ValueError("This platform has no modeled towed array. No time has elapsed.")
        if order["speed"] > arrays.TOWED.deploy_speed_knots:
            raise ValueError(
                f"Streaming and recovery require {arrays.TOWED.deploy_speed_knots:g} "
                "knots or less. No time has elapsed."
            )
        deployment = record["deployment"]
        if activity == "stream_array" and deployment in (arrays.STREAMED, arrays.RECOVERING):
            raise ValueError(
                "Towed array is streamed or being recovered. Recover it first "
                "if it is streamed; finish recovery before streaming again. "
                "No time has elapsed."
            )
        if activity == "recover_array" and deployment in (arrays.STOWED, arrays.STREAMING):
            raise ValueError(
                "Towed array is housed or still streaming. Finish streaming "
                "before recovery, or it is already housed. No time has elapsed."
            )
    if activity == "focus":
        require(order, "focus")
        focused = next((tr for tr in state["tracks"] if tr["id"] == order["focus"]), None)
        if focused is None:
            raise ValueError("Focused analysis requires an existing contact id.")
        if focused.get("receiver_id") is None:
            raise ValueError(
                "Focused analysis requires an acoustic receiver history. "
                "A visual contact cannot be focused."
            )
    if "focus" in order and activity != "focus":
        raise ValueError("The focus field requires the focus activity.")
    if activity == "transmit":
        require(order, "assessment")
        require(order, "message")
        if order["assessment"] not in ("submerged_present", "surface_or_biologic", "unresolved"):
            raise ValueError("Transmission requires assessment: submerged_present, surface_or_biologic, or unresolved.")
        if not isinstance(order["message"], str) or not 1 <= len(order["message"]) <= 4000:
            raise ValueError("Transmission requires a message of 1 to 4000 characters.")
        require(order, "basis")
        if not isinstance(order["basis"], list) or any(not isinstance(r, str) or r not in [p["id"] for p in state["reports"]] for r in order["basis"]):
            raise ValueError("Basis must list existing report ids only.")
    elif {"assessment", "message", "basis"} & set(raw):
        raise ValueError("Assessment, message and basis fields require transmit activity.")
    require(order, "interrupt_on")
    valid_interrupts = {"new_contact", "classification_change", "equipment", "deadline", "contact_lost",
                        "message_received", "radio_failure", "maneuver_complete", "activity_deferred",
                        "antenna_deployed", "array_streamed", "array_stowed", "array_unstable",
                        "operating_mode"}
    if not isinstance(order["interrupt_on"], list) or any(x not in valid_interrupts for x in order["interrupt_on"]):
        raise ValueError("Unknown interrupt condition.")
    return order


def simulate(state, seed, order):
    state["last_action_report_start"] = len(state["reports"])
    if order["activity"] == "end":
        state["ended"] = True
        state["end_reason"] = "Captain ended the exercise"
        ending = report(state, "Exercise", "Exercise ended by the captain.", "exercise_end")
        return {"requested_minutes": 0, "elapsed_minutes": 0, "unused_minutes": 0,
                "integration_steps": 0, "stop_reason": "exercise_end",
                "stop_events": [{"category": "exercise_end", "report_ids": [ending["id"]]}],
                "maneuver": maneuver_view(state)}
    requested = order["minutes"]
    start = state["t"]
    dice = Dice(seed, state["rng_trace"])
    ordered = {field: float(order[field]) for field in ("course", "speed", "depth")}
    ordered["operating_mode"] = order["operating_mode"]
    state["ordered"] = ordered
    state["maneuvering"] = any(
        ordered[field] != state["own"][field]
        for field in ("course", "speed", "depth", "operating_mode"))
    state["focus"] = order.get("focus") if order["activity"] == "focus" else None
    stop = "requested_interval_complete"
    stop_events = []
    for i in range(order["minutes"] // TICK):
        before = len(state["reports"])
        tick_world(state, dice, order, first_tick=i == 0)
        new_reports = state["reports"][before:]
        categories = {r["category"] for r in new_reports}
        matched = categories.intersection(order["interrupt_on"])
        if state["ended"]:
            stop = "exercise_end"
            stop_events = _execution_events(new_reports, {"exercise_end"} | matched)
            break
        completed = categories.intersection(
            {"transmission_complete", "message_received", "radio_empty", "repair_complete",
             "retrieval_complete", "array_streamed", "array_stowed"}
        )
        if completed:
            stop = "task_complete"
            stop_events = _execution_events(new_reports, completed | matched)
            break
        if matched:
            stop = "interrupt"
            stop_events = _execution_events(new_reports, matched)
            break
    elapsed = state["t"] - start
    return {"requested_minutes": requested, "elapsed_minutes": elapsed,
            "unused_minutes": requested - elapsed, "integration_steps": elapsed // TICK,
            "stop_reason": stop, "stop_events": stop_events,
            "maneuver": maneuver_view(state)}


def _execution_events(reports, categories):
    """Summarize public reasons for stopping at an integration boundary."""
    return [
        {"category": category,
         "report_ids": [entry["id"] for entry in reports if entry["category"] == category]}
        for category in sorted(categories)
    ]


def capability_report():
    result = KESTREL.public_capabilities()
    result["entity_model"] = {
        "definitions": public_entity_catalog(),
        "own_ship_operating_mode": {
            "speed_limit_enforced": True,
            "radiated_noise_modeled": False,
            "description": (
                "An operating mode caps own-ship speed and selects which of "
                "own ship's defined narrowband lines are emitted. Opposing "
                "detection does not use that spectrum. It uses own-ship speed, "
                "the shared transmission-loss model, receiver depth, range and "
                "active transmission."
            ),
        },
        "maneuver_transients": {
            "own_ship_rate_limited": True,
            "opposing_entities_rate_limited": False,
            "description": (
                "Own-ship course, speed and depth transit toward the commanded "
                "settings at the published rates. Opposing entities change theirs "
                "at five-minute boundaries without a modeled transition."
            ),
        },
        "shared_mechanics": [
            "geometry and simultaneous movement",
            "platform-specific operating envelopes and modes",
            "operating-state acoustic signatures",
            "numeric narrowband spectra with measured frequency and uncertainty",
            "hull, flank and towed receivers with baffles and deployment state",
            "consumable resource advancement",
            "persistent equipment and inventory states",
        ],
        "scenario_selection": (
            "Opposing entity specifications remain hidden until supported "
            "observations identify them."
        ),
    }
    result["narrowband_model"] = spectra.public_model()
    result["target_motion_model"] = motion_model()
    result["classification_model"] = classification_model()
    result["frequency_change_model"] = frequency_change_model()
    return result


def public_view(game):
    state = game["state"]
    own = state["own"]
    remaining = max(0, END - state["t"])
    station = {"x": 24, "y": 0}
    gap = distance(own, station)
    tracks = []
    for tr in state["tracks"]:
        tracks.append({
            "id": tr["id"],
            "receiver": (
                {"id": tr["receiver_id"], "role": tr.get("receiver_kind")}
                if tr.get("receiver_id") else None
            ),
            "first_report": clock(tr["first"]),
            "last_report": clock(tr["last"]),
            "status": "recent" if state["t"] - tr["last"] < 20 else "stale",
            "assessments": assessments(tr, state["t"]),
            "visual": copy.deepcopy(tr["visual"]),
            "observations": copy.deepcopy(tr["observations"]),
            "observed_bearing_drift": observed_bearing_drift(tr["observations"]),
            "target_motion": target_motion_estimate(tr["observations"], state["t"]),
            "frequency_change": frequency_change_assessment(tr["observations"], state["t"]),
            "correlation": (
                "This history is from one receiver. A similar bearing on another "
                "receiver is not automatically the same contact."
            ),
        })
    return {"game": MISSION["title"], "version": VERSION, "turn": len(game["events"]),
            "time": clock(state["t"]), "elapsed_minutes": state["t"], "ended": state["ended"],
            "initial_commitment_sha256": game["commitment"], "turn_receipt_sha256": game["head"],
            "engine_sha256": game["engine_sha256"], "mission": copy.deepcopy(MISSION),
            "command_contract": copy.deepcopy(COMMAND_CONTRACT),
            "platform_capabilities": capability_report(),
            "own_ship": {"name": "Kestrel", "east_nm": round(own["x"], 3), "north_nm": round(own["y"], 3),
                         "course_true": round(own["course"], 2), "speed_knots": round(own["speed"], 3),
                         "depth_feet": round(own["depth"], 2),
                         "operating_mode": own["operating_mode"],
                         "ordered": copy.deepcopy(state["ordered"]),
                         "maneuver": maneuver_view(state),
                         "resources": copy.deepcopy(own["resources"]),
                         "equipment_states": copy.deepcopy(own["equipment"]),
                         "antennas": public_antennas(state),
                         "sonar_receivers": public_sonar_receivers(state),
                         "towed_array": arrays.public_towed(state.get("towed")),
                         "inventory": copy.deepcopy(own["inventory"]),
                         "restrictions": {
                             "exercise": "Observation-only patrol.",
                             "rules_of_engagement": "No offensive weapons employment authorized.",
                             "employment_available": False,
                         },
                         "equipment": "Auxiliary vibration; sonar self-noise elevated" if state["pump_fault"] else "All systems available",
                         "repair_minutes_completed": state["repair_progress"]},
            "navigation": {"relief_distance_nm": round(gap, 2), "relief_bearing_true": round(bearing(own, station)),
                           "minutes_to_relief": remaining,
                           "minimum_average_speed_to_station_knots": round(gap / (remaining / 60), 2) if remaining else None,
                           "minutes_to_assessment_deadline": max(0, REPORT_DUE - state["t"])},
            "contacts": tracks, "transmitted_assessments": copy.deepcopy(state["sent"]),
            "reports_this_turn": copy.deepcopy(state["reports"][state["last_action_report_start"]:]),
            "report_count": len(state["reports"]),
            "acoustic_environment": acoustics.public_environment(
                state["environment"], state["t"]
            ),
            "last_execution": copy.deepcopy(game["events"][-1]["execution"]) if game["events"] else None}


def public_history(game):
    """Return only captain-visible reports and previously submitted orders."""
    reports = [
        {key: copy.deepcopy(value) for key, value in entry.items()
         if key in PUBLIC_REPORT_FIELDS}
        for entry in game["state"]["reports"]
    ]
    return {"reports": reports,
            "orders": [copy.deepcopy(event["raw"]) for event in game["events"]]}


def apply_order(game, raw):
    for event in game["events"]:
        if event["raw"].get("id") == raw.get("id"):
            if canonical(event["raw"]) != canonical(raw):
                raise ValueError("This order id was already used for a different order; nothing changed.")
            return copy.deepcopy(event["public_result"])
    if game["state"]["ended"]:
        raise ValueError("The exercise has ended. Request the debrief or review existing reports.")
    if "expected_turn" not in raw:
        raise ValueError(
            "expected_turn must be stated explicitly; the engine supplies no default. "
            "No time has elapsed and nothing changed."
        )
    if type(raw["expected_turn"]) is not int or raw["expected_turn"] != len(game["events"]):
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
        fsync_directory(path.parent)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def fsync_directory(path):
    """Make a completed directory-entry change durable where POSIX permits."""
    if os.name != "posix":
        return
    descriptor = os.open(Path(path), os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def debrief(game):
    if not game["state"]["ended"]:
        raise ValueError("Debrief is unavailable during play. End the exercise explicitly to reveal it.")
    verification = verify(game)
    state = game["state"]
    timely = [s for s in state["sent"] if s["elapsed_minutes"] <= REPORT_DUE]
    primary_kind = contact_kind(game["initial_state"]["actors"][0])
    evaluations = []
    for sent in state["sent"]:
        accurate = None if sent["assessment"] == "unresolved" else (sent["assessment"] == "submerged_present") == (primary_kind == "submerged")
        evaluations.append({**sent, "matches_hidden_presence": accurate,
                            "interpretation": "Outcome accuracy only; evaluate the judgment against the cited evidence separately."})
    emitters = [state["own"], *state["actors"]]
    return {"spoilers": True, "verification": verification, "seed": game["seed"],
            "initial_state": game["initial_state"], "final_state": state, "events": game["events"],
            "emitter_spectra": [
                {"id": entity["id"], **spectra.truth_snapshot(entity)} for entity in emitters
            ],
            "outcomes": {"assessment_sent_by_deadline": bool(timely),
                         "at_relief_at_0800": distance(state["own"], {"x": 24, "y": 0}) <= 3 if state["t"] == END else None,
                         "opponent_detected_own_ship": any(a["aware"] for a in state["actors"]),
                         "radio_intercepts": copy.deepcopy(state["radio_intercepts"]),
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
        print(json.dumps(capability_report(), indent=2))
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
                    result = public_history(game)
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
