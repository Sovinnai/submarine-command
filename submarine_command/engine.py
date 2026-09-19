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

from . import acoustics
from .locking import session_lock
from .observations import observed_bearing_drift
from .platforms import (
    BIOLOGIC,
    DART,
    FISHER,
    KESTREL,
    MERCHANT,
    WARSHIP,
    EntityCategory,
    get_spec,
    public_entity_catalog,
)

VERSION = "0.6.0"
TICK = 5
END = 290
REPORT_DUE = 260
KINDS = ("surface", "submerged", "biologic")
CUES = {
    "commercial_rhythm": (0.64, 0.12, 0.06),
    "faint_cycling_tonal": (0.27, 0.69, 0.22),
    "irregular_broadband": (0.09, 0.19, 0.72),
}
# Which bands can carry each classification cue. A cue heard only in the lowest
# bands cannot be a broadband one; the evidence a track accumulates is limited by
# the part of the spectrum that actually arrived.
CUE_BANDS = {
    "commercial_rhythm": ("b104", "b300", "b866"),
    "faint_cycling_tonal": ("b035", "b104", "b300"),
    "irregular_broadband": ("b866", "b2739", "b7746"),
}
INTEGRATION_SECONDS = TICK * 60
FOCUS_GAIN_DB = 3.0
UNFOCUSED_PENALTY_DB = -1.5
PUMP_FAULT_SELF_NOISE_DB = 6.0
ENVIRONMENT_FLUCTUATION_DB = 3.0
ACTIVE_SOURCE_LEVEL_DB = 215.0
PROFILE_PREDICTION_RANGE_NM = 10.0
PROFILE_PREDICTION_DEPTHS_FEET = (0.0, 600.0)
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
    "chart": "Local grid in nautical miles: east is +x, north is +y. Patrol corridor: x=0 to 22, y=-6 to +6. Charted depth runs from about 8,500 feet in the south-west to under 3,000 feet over the north-eastern shoal; all of it clears the game envelope, but the bottom is close enough in the north-east to change bottom-reflected propagation.",
    "orders": "Observe and report. Preserve discretion and meet the relief commitment. No offensive weapons employment is authorized in this patrol.",
    "intel": "0200 shore estimate: submerged transit is possible; no reliable identification or contact solution. Commercial and survey traffic also use the passage.",
    "radio": "An intelligence update is scheduled for 0330; a later update for 0510. Messages remain available for retrieval.",
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
    "concurrency": {
        "maneuver": "Course, speed, and depth settings take effect at the start of the first five-minute step and remain in force. Own ship and opposing platforms then move over the same elapsed step.",
        "observation": "Passive or focused acoustic integration occupies each complete five-minute step and runs concurrently with movement and the selected equipment activity. Results are reported at the step endpoint.",
        "active": "The active transmission and its observation integration occupy the first five-minute step; later requested steps revert to passive observation.",
        "mast": "Mast deployment, visual observation, and recovery form a five-minute cycle concurrent with movement. Results are available at the step endpoint.",
        "communications": "Each receive or transmit link attempt occupies one five-minute step, includes mast deployment and recovery, and runs concurrently with movement and passive observation. A usable receive link or acknowledged transmission completes the task; a failed link may be retried in a later step unless selected as an interrupt.",
        "sound_profile": "A sound-speed observation occupies the first five-minute step, runs concurrently with movement and passive observation, and completes the task. It replaces the onboard profile estimate with a measurement at that position and time; the estimate then ages while the true field keeps changing.",
        "repair": "Repair needs 20 productive minutes at no more than the published repair speed. It runs concurrently with movement and passive observation and retains progress across interrupted command windows.",
    },
    "interrupts": "Events are evaluated only at five-minute step boundaries. A stop returns actual and unused time plus every matching event category and report id from that boundary; the unused portion is never continued automatically.",
}
PUBLIC_REPORT_FIELDS = {
    "id", "time", "elapsed_minutes", "department", "category", "text",
    "contact", "observation", "visual", "message_id", "transmitted",
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


def entity_spec(entity):
    return get_spec(entity["spec"])


def contact_kind(entity):
    return entity_spec(entity).contact_domain.value


def source_levels(entity):
    """Radiated band levels for this hull's state, speed and depth."""
    spec = entity_spec(entity)
    return spec.signature.levels_for(
        spec.mode(entity["operating_mode"]), entity["speed"], entity["depth"])


def ocean_model(state):
    """Rebuild the true environment field from committed scenario parameters."""
    ocean = state["ocean"]
    grid = ocean["bathymetry"]
    return acoustics.OceanModel(
        bathymetry=acoustics.Bathymetry(
            grid["origin_east_nm"], grid["origin_north_nm"], grid["spacing_nm"],
            tuple(tuple(row) for row in grid["depths_m"])),
        bottom=ocean["bottom"], sea_state=ocean["sea_state"],
        shipping_density=ocean["shipping_density"],
        surface_speed_mps=ocean["surface_speed_mps"],
        layer_depth_west_m=ocean["layer_depth_west_m"],
        layer_depth_east_m=ocean["layer_depth_east_m"],
        layer_variation_m=ocean["layer_variation_m"],
        layer_period_minutes=ocean["layer_period_minutes"],
        west_east_span_nm=ocean["west_east_span_nm"],
        duct_gradient=ocean["duct_gradient"],
        thermocline_gradient=ocean["thermocline_gradient"],
        deep_gradient=ocean["deep_gradient"],
        thermocline_thickness_m=ocean["thermocline_thickness_m"])


def true_environment(state, first, second):
    """True conditions sampled at the midpoint of a path. Adjudication only."""
    return ocean_model(state).at((first["x"] + second["x"]) / 2.0,
                                 (first["y"] + second["y"]) / 2.0, state["t"])


def estimated_environment(state, east_nm, north_nm):
    """What the boat believes, from its last profile and the chart.

    Charted depth, bottom class and observed sea state are known. Only the sound
    speed structure comes from the estimate, and it does not age itself: the
    estimate is a fixed profile while the true field moves away from it.
    """
    estimate = state["profile_estimate"]
    ocean = state["ocean"]
    depth_m = ocean_model(state).bathymetry.depth_m(east_nm, north_nm)
    profile = acoustics.layered_profile(
        estimate["surface_speed_mps"], estimate["layer_depth_m"],
        estimate["duct_gradient"], estimate["thermocline_gradient"],
        estimate["deep_gradient"], estimate["thermocline_thickness_m"], depth_m)
    return acoustics.Environment(profile, depth_m, ocean["bottom"],
                                 ocean["sea_state"], ocean["shipping_density"])


def self_noise_levels(entity, receiver, penalty_db=0.0):
    """Self noise at the array, by band, for this speed and equipment state."""
    rise = acoustics.flow_noise_rise_db(entity["speed"], receiver.quiet_speed_knots)
    return tuple(acoustics.quantise(level + rise + penalty_db)
                 for level in receiver.self_noise_db)


def acoustic_ledger(state, listener, source, *, fluctuation_db=0.0, gain_offset_db=0.0,
                    noise_penalty_db=0.0, active=False):
    """Resolve one listener/source pair through the sonar equation.

    Returns the per-band decibel ledger and the band with the greatest signal
    excess, or None when the listener has no receiver or no path carries energy.
    This is the only place in the game where a detection opportunity is scored;
    own ship, the opposition and the active pulse all come through here.
    """
    receiver = entity_spec(listener).receiver
    if receiver is None:
        return None
    environment = true_environment(state, listener, source)
    range_nm = distance(listener, source)
    range_m = acoustics.nautical_miles_to_metres(range_nm)
    listener_depth_m = acoustics.feet_to_metres(listener["depth"])
    source_depth_m = acoustics.feet_to_metres(source["depth"])
    self_noise = self_noise_levels(listener, receiver, noise_penalty_db)
    if active:
        bands = (acoustics.ACTIVE_BAND,)
        indices = (acoustics.BAND_IDS.index(acoustics.ACTIVE_BAND.identifier),)
        levels = (ACTIVE_SOURCE_LEVEL_DB,)
        target_strength = entity_spec(source).target_strength_db
    else:
        bands = acoustics.BANDS
        indices = tuple(range(len(acoustics.BANDS)))
        levels = source_levels(source)
        target_strength = None
    entries = []
    for band, index in zip(bands, indices):
        solution = acoustics.transmission_loss(
            environment, band.centre_hz, range_m, listener_depth_m, source_depth_m)
        if solution.loss_db is None:
            # Same shape as a scored band, so a caller can read any entry
            # without having to know which branch produced it.
            entries.append({"band": band.identifier, "signal_excess_db": None,
                            "transmission_loss_db": None, "noise_level_db": None,
                            "source_level_db": None, "array_gain_db": None,
                            "detection_threshold_db": None,
                            "path": None, "path_status": "out_of_scope"})
            continue
        loss = acoustics.quantise(solution.loss_db + fluctuation_db)
        noise = acoustics.combine_db(
            acoustics.ambient_noise_db(environment, band, listener_depth_m),
            self_noise[index])
        gain = receiver.array_gain_db[index] + gain_offset_db
        threshold = acoustics.detection_threshold_db(
            band, INTEGRATION_SECONDS, receiver.detection_index_db)
        excess = acoustics.signal_excess_db(
            levels[0] if active else levels[index], loss, noise, gain, threshold,
            target_strength)
        entries.append({"band": band.identifier, "signal_excess_db": excess,
                        "transmission_loss_db": loss, "noise_level_db": noise,
                        "source_level_db": levels[0] if active else levels[index],
                        "array_gain_db": acoustics.quantise(gain),
                        "detection_threshold_db": threshold,
                        "path": solution.dominant, "path_status": solution.status})
    scored = [entry for entry in entries if entry["signal_excess_db"] is not None]
    if not scored:
        return None
    best = max(scored, key=lambda entry: entry["signal_excess_db"])
    return {"range_nm": acoustics.quantise(range_nm, 3), "best": best, "bands": entries,
            "probability": acoustics.detection_probability(best["signal_excess_db"]),
            "water_depth_feet": round(acoustics.metres_to_feet(environment.water_depth_m)),
            "layer_depth_feet": round(acoustics.metres_to_feet(environment.profile.layer_depth_m), 1)}


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
    window = t // 20
    # One environmental fluctuation per acoustic window, shared by every contact
    # in it: reports made in the same conditions carry a common error, so two of
    # them are not two independent measurements.
    fluctuation = dice.between(f"acoustic-window:{window}", -ENVIRONMENT_FLUCTUATION_DB,
                               ENVIRONMENT_FLUCTUATION_DB)
    gain_offset = 0.0
    if mode == "focus":
        track_match = next((tr for tr in state["tracks"] if tr["actor"] == actor["id"]), None)
        focused = track_match is not None and track_match["id"] == state["focus"]
        gain_offset = FOCUS_GAIN_DB if focused else UNFOCUSED_PENALTY_DB
    ledger = acoustic_ledger(
        state, own, actor, fluctuation_db=fluctuation, gain_offset_db=gain_offset,
        noise_penalty_db=PUMP_FAULT_SELF_NOISE_DB if state["pump_fault"] else 0.0,
        active=mode == "active")
    if ledger is None:
        return
    probability = ledger["probability"]
    if not opening and dice.u(f"detect:{actor['id']}:{t}:{mode}") >= probability:
        return
    delta = ledger["range_nm"]
    excess = ledger["best"]["signal_excess_db"]
    band = acoustics.BAND_BY_ID[ledger["best"]["band"]]
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
        # A cue can only be reported from a part of the spectrum that arrived.
        available = [key for key in CUES if band.identifier in CUE_BANDS[key]] or list(CUES)
        i = KINDS.index(contact_kind(actor))
        cue = dice.choose(f"signature:{actor['id']}:{window}", available,
                          [CUES[key][i] for key in available])
        # One evidence contribution per acoustic window; repeated looks do not compound confidence.
        track["evidence"].setdefault(str(window), cue)
    strength = "strong" if excess >= 10 else "moderate" if excess >= 3 else "weak"
    obs = {"time": clock(t), "elapsed_minutes": t, "bearing_true": round(measured) % 360,
           "own_east_nm": round(own["x"], 3), "own_north_nm": round(own["y"], 3),
           "own_depth_feet": own["depth"],
           "source": mode, "strength": strength, "description": CUE_TEXT[cue],
           "band": band.identifier, "band_label": band.label,
           "range_estimate_nm": None, "evidence_window": f"A{window:03d}"}
    if mode == "active":
        obs["range_estimate_nm"] = round(max(0.1, delta * dice.between(f"active-range:{actor['id']}:{t}", 0.88, 1.12)), 1)
    track["observations"].append(obs)
    # The decibel ledger is world truth. It is kept for the debrief and never
    # reaches a public report.
    state["acoustic_ledger"].append({"elapsed_minutes": t, "contact": track["id"],
                                     "actor": actor["id"], "mode": mode, **ledger})
    report(state, "Sonar",
           f"{track['id']}: bearing {obs['bearing_true']:03d} true, {strength} reception in the "
           f"{band.label} band. {obs['description']}",
           "new_contact" if new else "contact_update", contact=track["id"], observation=obs)
    new_assessment = assessments(track)["supervisor"]
    if not new and new_assessment["confidence"] == "high" and new_assessment != old_assessment:
        report(state, "Sonar supervisor", f"{track['id']}: assessment now favors {new_assessment['favors']} with high confidence; this remains an assessment.", "classification_change", contact=track["id"])


def band_entry(ledger, band_identifier):
    """One band's row of a ledger, found by name rather than by position."""
    return next(entry for entry in ledger["bands"] if entry["band"] == band_identifier)


def take_sound_profile(state, dice):
    """Replace the onboard profile estimate by sampling the true water column.

    A measurement, not a revelation: the layer depth is read with a residual
    error, and the estimate is then fixed while the true field keeps moving.
    """
    own, t = state["own"], state["t"]
    model = ocean_model(state)
    truth = model.at(own["x"], own["y"], t)
    error_m = dice.between(f"profile-error:{t}", -4.5, 4.5)
    estimate = state["profile_estimate"]
    estimate.update({
        "layer_depth_m": acoustics.quantise(max(5.0, truth.profile.layer_depth_m + error_m), 2),
        "uncertainty_m": acoustics.quantise(5.0, 2),
        "surface_speed_mps": state["ocean"]["surface_speed_mps"],
        "duct_gradient": state["ocean"]["duct_gradient"],
        "thermocline_gradient": state["ocean"]["thermocline_gradient"],
        "deep_gradient": state["ocean"]["deep_gradient"],
        "thermocline_thickness_m": state["ocean"]["thermocline_thickness_m"],
        "taken_minutes": t, "taken_east_nm": round(own["x"], 3),
        "taken_north_nm": round(own["y"], 3),
        "source": "onboard sound-speed profile observation",
    })
    layer_feet = acoustics.metres_to_feet(estimate["layer_depth_m"])
    cutoff = acoustics.layered_profile(
        estimate["surface_speed_mps"], estimate["layer_depth_m"], estimate["duct_gradient"],
        estimate["thermocline_gradient"], estimate["deep_gradient"],
        estimate["thermocline_thickness_m"], truth.water_depth_m).duct_cutoff_hz
    report(state, "Environment",
           f"Sound-speed observation complete. Layer depth measured at {layer_feet:.0f} feet "
           f"plus or minus {acoustics.metres_to_feet(estimate['uncertainty_m']):.0f}; "
           f"surface duct traps above about {cutoff:.0f} Hz. The estimate applies to this position "
           "and time and will age.",
           "profile_complete")


# Charted depth in metres across the operating area, on a coarse grid. The
# passage is deep in the south-west and shoals toward the north-east; every
# charted square still clears the game's depth envelope.
PASSAGE_BATHYMETRY = {
    "origin_east_nm": -4.0, "origin_north_nm": -14.0, "spacing_nm": 8.0,
    "depths_m": [
        [2600, 2600, 2500, 2300, 2000, 1800],
        [2600, 2550, 2450, 2200, 1900, 1600],
        [2500, 2450, 2350, 2050, 1700, 1300],
        [2300, 2200, 2000, 1700, 1300, 950],
        [2000, 1900, 1700, 1400, 1000, 700],
    ],
}


def initial_ocean(dice):
    """Draw the scenario's true conditions once, at initialization.

    Nothing about the ocean is redrawn during play. The layer deepens eastward
    and breathes slowly, so an estimate goes stale by the boat's own movement as
    much as by the clock.
    """
    return {
        "bathymetry": PASSAGE_BATHYMETRY,
        "bottom": "fine_sediment",
        "sea_state": int(dice.choose("initial:sea-state", [2, 3, 4], [0.3, 0.45, 0.25])),
        "shipping_density": 4,
        "surface_speed_mps": acoustics.quantise(dice.between("initial:surface-speed", 1495, 1505), 2),
        "layer_depth_west_m": acoustics.quantise(
            acoustics.feet_to_metres(dice.between("initial:layer-west", 330, 385)), 2),
        "layer_depth_east_m": acoustics.quantise(
            acoustics.feet_to_metres(dice.between("initial:layer-east", 450, 525)), 2),
        "layer_variation_m": acoustics.quantise(
            acoustics.feet_to_metres(dice.between("initial:layer-variation", 8, 20)), 2),
        "layer_period_minutes": 480.0,
        "west_east_span_nm": 24.0,
        "duct_gradient": 0.016,
        "thermocline_gradient": acoustics.quantise(dice.between("initial:thermocline", -0.095, -0.062), 4),
        "deep_gradient": 0.016,
        "thermocline_thickness_m": 710.0,
    }


def initial_profile_estimate(state, dice):
    """The pre-patrol forecast: the right shape, the wrong layer depth.

    It is deliberately worse than an observation, and it carries where and when
    it was made so its age is visible rather than implied.
    """
    ocean = state["ocean"]
    nominal = (ocean["layer_depth_west_m"] + ocean["layer_depth_east_m"]) / 2.0
    error = acoustics.feet_to_metres(dice.between("initial:profile-error", -55, 55))
    return {
        "layer_depth_m": acoustics.quantise(max(5.0, nominal + error), 2),
        "uncertainty_m": acoustics.quantise(acoustics.feet_to_metres(60.0), 2),
        "surface_speed_mps": ocean["surface_speed_mps"],
        "duct_gradient": ocean["duct_gradient"],
        "thermocline_gradient": ocean["thermocline_gradient"],
        "deep_gradient": ocean["deep_gradient"],
        "thermocline_thickness_m": ocean["thermocline_thickness_m"],
        "taken_minutes": 0, "taken_east_nm": 0.0, "taken_north_nm": 0.0,
        "source": "pre-patrol forecast, not an onboard observation",
    }


def new_world(seed):
    state = {"t": 0, "platform": KESTREL.identifier,
             "own": make_entity(KESTREL, id="own-kestrel", x=0.0, y=0.0,
                                course=90.0, speed=5.0, depth=400.0),
             "actors": [], "tracks": [], "reports": [], "rng_trace": [], "focus": None,
             "pump_fault": False, "repair_progress": 0, "received": [], "sent": [],
             "acoustic_ledger": [],
             "ended": False, "end_reason": None, "deadline_announced": False, "last_action_report_start": 0}
    dice = Dice(seed, state["rng_trace"])
    state["ocean"] = initial_ocean(dice)
    state["profile_estimate"] = initial_profile_estimate(state, dice)
    state["bearing_bias"] = dice.between("initial:bearing-bias", -2, 2)
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
    report(state, "Navigation", "0310. Local position (0 east, 0 north). Course 090, speed 5 knots, depth 400 feet. RELIEF lies 24 nautical miles east.")
    estimate = state["profile_estimate"]
    report(state, "Environment",
           f"Pre-patrol forecast places the acoustic layer near "
           f"{acoustics.metres_to_feet(estimate['layer_depth_m']):.0f} feet, plus or minus "
           f"{acoustics.metres_to_feet(estimate['uncertainty_m']):.0f}. It is a forecast, not an "
           "observation, and the layer is known to deepen eastward through the passage. A sound-speed "
           "observation will replace it with a measurement at one position and time.")
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
    if entity_spec(actor).category not in (
        EntityCategory.SSN,
        EntityCategory.DIESEL_SUBMARINE,
    ):
        return
    own, t = state["own"], state["t"]
    # The opposition hears own ship through the same environment and the same
    # sonar equation, using its own receiver and own-ship's radiated levels.
    window = t // 20
    fluctuation = dice.between(f"acoustic-window:{window}", -ENVIRONMENT_FLUCTUATION_DB,
                               ENVIRONMENT_FLUCTUATION_DB)
    ledger = acoustic_ledger(state, actor, own, fluctuation_db=fluctuation)
    probability = ledger["probability"] if ledger else 0.0
    if active and ledger is not None:
        # An active transmission is loud and deliberate. The pulse reaches the
        # opposition one way, at the projector's source level, in its own band.
        entry = band_entry(ledger, acoustics.ACTIVE_BAND.identifier)
        if entry["transmission_loss_db"] is not None:
            excess = acoustics.signal_excess_db(
                ACTIVE_SOURCE_LEVEL_DB, entry["transmission_loss_db"],
                entry["noise_level_db"], entry["array_gain_db"],
                entry["detection_threshold_db"])
            probability = max(probability, acoustics.detection_probability(excess))
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
    # Decisions use the common start-of-step geometry. Movement is then
    # integrated for every platform over the same elapsed interval.
    for actor in state["actors"]:
        prepare_actor_step(state, actor, dice)
        opponent_step(state, actor, dice, active)
    move(state["own"])
    advance_entity_components(state["own"])
    for actor in state["actors"]:
        move(actor)
        advance_entity_components(actor)
    if active:
        report(state, "Sonar", "One active acoustic transmission made.", "emission")
    mode = "active" if active else "focus" if order["activity"] == "focus" else "passive"
    for actor in state["actors"]:
        observe_contact(state, actor, dice, mode)
    for tr in state["tracks"]:
        if t - tr["last"] == 20:
            report(state, "Sonar", f"{tr['id']}: no further observation for 20 minutes. Last report is now stale; contact fate unknown.", "contact_lost", contact=tr["id"])
    if order["activity"] == "sound_profile" and first_tick:
        take_sound_profile(state, dice)
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
    allowed = {"id", "minutes", "course", "speed", "depth", "operating_mode", "activity", "focus", "assessment", "message", "basis", "interrupt_on", "expected_turn"}
    if not isinstance(raw, dict) or set(raw) - allowed:
        raise ValueError("Order must be an object using only the documented fields.")
    if not isinstance(raw.get("id"), str) or not 1 <= len(raw["id"]) <= 80:
        raise ValueError("A unique order id of 1 to 80 characters is required.")
    order = copy.deepcopy(raw)
    order.setdefault("activity", "listen")
    activity = order["activity"]
    if activity not in ("listen", "focus", "active", "mast", "receive", "transmit", "repair", "sound_profile", "end"):
        raise ValueError("Unsupported activity. No time has elapsed; agree on an applicable rule before proceeding.")
    order.setdefault("minutes", 0 if activity == "end" else 15)
    minutes = order["minutes"]
    if type(minutes) is not int or (minutes != 0 if activity == "end" else not (5 <= minutes <= 60 and minutes % 5 == 0)):
        raise ValueError("Use 5 to 60 minutes in five-minute steps, or zero for end.")
    if activity == "end":
        if set(raw) - {"id", "activity", "minutes", "expected_turn"}:
            raise ValueError("An end order cannot include other actions.")
        return order
    spec = entity_spec(state["own"])
    envelope = spec.envelope
    for field, low, high in (("course", 0, 359.999999),
                             ("speed", envelope.minimum_speed_knots, envelope.maximum_speed_knots),
                             ("depth", envelope.minimum_depth_feet, envelope.maximum_depth_feet)):
        value = order.setdefault(field, state["own"][field])
        if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
            raise ValueError(f"{field} must be between {low} and {high} in the fictional game envelope.")
    selected_mode = order.setdefault("operating_mode", state["own"]["operating_mode"])
    if not isinstance(selected_mode, str):
        raise ValueError("operating_mode must name a published mode.")
    mode = spec.mode(selected_mode)
    if not mode.allows(order["speed"], order["depth"]):
        raise ValueError(
            f"The {selected_mode} operating mode does not allow the ordered speed/depth."
        )
    if activity in ("mast", "receive", "transmit") and (
        spec.mast is None or not spec.mast.allows(order["depth"], order["speed"])
    ):
        if spec.mast is None:
            raise ValueError("This platform has no implemented mast/link activity.")
        raise ValueError(f"Mast/link envelope: {spec.mast.minimum_depth_feet:g} to {spec.mast.maximum_depth_feet:g} feet and at most {spec.mast.maximum_speed_knots:g} knots. Include those orders explicitly; no time has elapsed.")
    if activity == "repair" and (
        spec.repair_maximum_speed_knots is None
        or order["speed"] > spec.repair_maximum_speed_knots
    ):
        if spec.repair_maximum_speed_knots is None:
            raise ValueError("This platform has no implemented repair activity.")
        raise ValueError(f"Repair requires {spec.repair_maximum_speed_knots:g} knots or less in the game.")
    if activity == "sound_profile" and (
        spec.profile_maximum_speed_knots is None
        or order["speed"] > spec.profile_maximum_speed_knots
    ):
        if spec.profile_maximum_speed_knots is None:
            raise ValueError("This platform has no implemented sound-speed observation.")
        raise ValueError(
            f"A sound-speed observation requires {spec.profile_maximum_speed_knots:g} knots or less."
        )
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
    requested = order["minutes"]
    if order["activity"] == "end":
        state["ended"] = True
        state["end_reason"] = "Captain ended the exercise"
        ending = report(state, "Exercise", "Exercise ended by the captain.", "exercise_end")
        return {"requested_minutes": 0, "elapsed_minutes": 0, "unused_minutes": 0,
                "integration_steps": 0, "stop_reason": "exercise_end",
                "stop_events": [{"category": "exercise_end", "report_ids": [ending["id"]]}]}
    start = state["t"]
    dice = Dice(seed, state["rng_trace"])
    for field in ("course", "speed", "depth"):
        state["own"][field] = float(order[field])
    state["own"]["operating_mode"] = order["operating_mode"]
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
             "profile_complete"}
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
            "stop_reason": stop, "stop_events": stop_events}


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
        "shared_mechanics": [
            "geometry and simultaneous movement",
            "platform-specific operating envelopes and modes",
            "operating-state acoustic signatures",
            "consumable resource advancement",
            "persistent equipment and inventory states",
        ],
        "scenario_selection": (
            "Opposing entity specifications remain hidden until supported "
            "observations identify them."
        ),
    }
    result["acoustic_model"] = {
        "reference_levels": {
            "source_level": "dB re 1 micropascal at 1 m, in a band's analysis bandwidth",
            "noise_level": "dB re 1 micropascal, in the same analysis bandwidth",
            "transmission_loss": "dB relative to 1 m",
            "array_gain": "dB", "target_strength": "dB",
        },
        "passive_equation": "SE = SL - TL - (NL - DI) - DT",
        "active_equation": "SE = SL - 2*TL + TS - (NL - DI) - DT",
        "bands": acoustics.public_band_catalogue(),
        "integration_seconds_per_step": INTEGRATION_SECONDS,
        "detection_probability": (
            f"Cumulative normal in signal excess, {acoustics.SIGNAL_EXCESS_SIGMA_DB:g} dB spread, "
            f"bounded to [{acoustics.MINIMUM_DETECTION_PROBABILITY}, "
            f"{acoustics.MAXIMUM_DETECTION_PROBABILITY}]."
        ),
        "shared_environment": (
            "Own-ship reception, the opposition's reception of own ship and the active pulse are "
            "resolved by the same function against the same environment."
        ),
        "environment_estimate": (
            "Adjudication uses true conditions. Everything published to the captain is predicted "
            "from the onboard profile estimate, whose age, origin and uncertainty are reported."
        ),
        "bottom_classes": {name: value["description"] for name, value in acoustics.BOTTOM_TYPES.items()},
        "correlated_error": (
            "One environmental fluctuation per twenty-minute acoustic window applies to every "
            "contact in it; repeated looks in one window are not independent evidence."
        ),
        "not_modelled": [
            "ray or normal-mode solution of the profile",
            "range-dependent conditions within a single path",
            "narrowband line structure and measured frequencies",
            "separate arrays, beam patterns and baffles",
            "reverberation, surface scattering and internal waves",
        ],
    }
    return result


def public_report(entry):
    """Strip a world report down to the fields the captain may see."""
    return {key: copy.deepcopy(value) for key, value in entry.items()
            if key in PUBLIC_REPORT_FIELDS}


def acoustic_prediction(state):
    """Predicted propagation from the boat's own estimate, never from truth.

    Charted depth, bottom class and sea state are known; the sound speed
    structure is whatever the last profile said. Every number here is therefore
    as wrong as the estimate is, which is the point of publishing its age and the
    distance the boat has run since it was taken.
    """
    own, t = state["own"], state["t"]
    estimate = state["profile_estimate"]
    environment = estimated_environment(state, own["x"], own["y"])
    profile = environment.profile
    own_depth_m = acoustics.feet_to_metres(own["depth"])
    reference_range_m = acoustics.nautical_miles_to_metres(PROFILE_PREDICTION_RANGE_NM)
    band = acoustics.BAND_BY_ID["b300"]
    reception = []
    for source_feet in PROFILE_PREDICTION_DEPTHS_FEET:
        source_m = acoustics.feet_to_metres(source_feet)
        limit = acoustics.refracted_limiting_range_m(profile, own_depth_m, source_m)
        solution = acoustics.transmission_loss(
            environment, band.centre_hz, reference_range_m, own_depth_m, source_m)
        reception.append({
            "assumed_source_depth_feet": source_feet,
            "shares_own_side_of_layer":
                (own_depth_m <= profile.layer_depth_m) == (source_m <= profile.layer_depth_m),
            "refracted_path_limit_nm": round(acoustics.metres_to_nautical_miles(limit), 2),
            "paths": solution.public_definition()["paths"],
        })
    travelled = math.hypot(own["x"] - estimate["taken_east_nm"], own["y"] - estimate["taken_north_nm"])
    return {
        "basis": estimate["source"],
        "taken_time": clock(estimate["taken_minutes"]),
        "age_minutes": t - estimate["taken_minutes"],
        "distance_from_observation_nm": round(travelled, 2),
        "estimated_layer_depth_feet": round(acoustics.metres_to_feet(estimate["layer_depth_m"])),
        "estimated_layer_uncertainty_feet": round(acoustics.metres_to_feet(estimate["uncertainty_m"])),
        "charted_water_depth_feet": round(acoustics.metres_to_feet(environment.water_depth_m)),
        "bottom": environment.bottom,
        "bottom_description": acoustics.BOTTOM_TYPES[environment.bottom]["description"],
        "sea_state": environment.sea_state,
        "surface_duct": {
            "present": profile.has_surface_duct,
            "cutoff_hz": profile.duct_cutoff_hz,
            "note": "A duct of the estimated thickness traps only frequencies above its cutoff.",
        },
        "prediction_geometry": {"range_nm": PROFILE_PREDICTION_RANGE_NM,
                                "frequency_hz": band.centre_hz,
                                "own_depth_feet": own["depth"]},
        "predicted_reception": reception,
        "caveat": ("Predicted from the onboard estimate. Adjudication uses true conditions, which "
                   "vary along the passage and with time; a stale or distant estimate will disagree."),
    }


def own_self_noise(state):
    """Own radiated and received noise at the present speed and plant state."""
    own = state["own"]
    spec = entity_spec(own)
    receiver = spec.receiver
    levels = source_levels(own)
    result = {
        "radiated_source_level_db_by_band": dict(zip(acoustics.BAND_IDS, levels)),
        "reference": "dB re 1 micropascal at 1 m in each band's analysis bandwidth; fictional game parameter.",
    }
    if receiver is not None:
        penalty = PUMP_FAULT_SELF_NOISE_DB if state["pump_fault"] else 0.0
        result["self_noise_db_by_band"] = dict(
            zip(acoustics.BAND_IDS, self_noise_levels(own, receiver, penalty)))
        result["flow_noise_rise_db"] = acoustics.flow_noise_rise_db(
            own["speed"], receiver.quiet_speed_knots)
        result["quiet_speed_knots"] = receiver.quiet_speed_knots
    inception = spec.signature.cavitation_speed_knots(own["depth"])
    if inception is not None:
        result["cavitation_inception_speed_knots"] = inception
        result["cavitating"] = own["speed"] > inception
        result["cavitation_note"] = (
            "Cavitation inception rises with depth. Above it, radiated noise steps up by "
            f"{acoustics.CAVITATION_EXCESS_DB:g} dB in every band.")
    return result


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
            "command_contract": copy.deepcopy(COMMAND_CONTRACT),
            "platform_capabilities": capability_report(),
            "own_ship": {"name": "Kestrel", "east_nm": round(own["x"], 3), "north_nm": round(own["y"], 3),
                         "course_true": own["course"], "speed_knots": own["speed"], "depth_feet": own["depth"],
                         "operating_mode": own["operating_mode"],
                         "resources": copy.deepcopy(own["resources"]),
                         "equipment_states": copy.deepcopy(own["equipment"]),
                         "inventory": copy.deepcopy(own["inventory"]),
                         "restrictions": {
                             "exercise": "Observation-only patrol.",
                             "rules_of_engagement": "No offensive weapons employment authorized.",
                             "employment_available": False,
                         },
                         "acoustics": own_self_noise(state),
                         "equipment": "Auxiliary vibration; sonar self-noise elevated" if state["pump_fault"] else "All systems available",
                         "repair_minutes_completed": state["repair_progress"]},
            "navigation": {"relief_distance_nm": round(gap, 2), "relief_bearing_true": round(bearing(own, station)),
                           "minutes_to_relief": remaining,
                           "minimum_average_speed_to_station_knots": round(gap / (remaining / 60), 2) if remaining else None,
                           "minutes_to_assessment_deadline": max(0, REPORT_DUE - state["t"])},
            "acoustic_environment": acoustic_prediction(state),
            "contacts": tracks, "transmitted_assessments": copy.deepcopy(state["sent"]),
            "reports_this_turn": [public_report(entry)
                                  for entry in state["reports"][state["last_action_report_start"]:]],
            "report_count": len(state["reports"]),
            "last_execution": copy.deepcopy(game["events"][-1]["execution"]) if game["events"] else None}


def public_history(game):
    """Return only captain-visible reports and previously submitted orders."""
    reports = [public_report(entry) for entry in game["state"]["reports"]]
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
    return {"spoilers": True, "verification": verification, "seed": game["seed"],
            "initial_state": game["initial_state"], "final_state": state, "events": game["events"],
            "acoustic_ledger": copy.deepcopy(state["acoustic_ledger"]),
            "acoustic_ledger_note": (
                "Every detection opportunity that produced a report, in decibels: source level, "
                "transmission loss and the path that carried it, noise, array gain, threshold and "
                "the resulting signal excess. Compare it against what the boat predicted from its "
                "profile estimate at the time."),
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
