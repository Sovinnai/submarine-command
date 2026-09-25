"""Idealized hull, flank and towed receivers for the selected platform.

Coverage, frequency response, self-noise and deployment are published fictional
game parameters. Towed-array geometry is a depth offset, a relative-bearing
mask and a speed/turn stability rule. The engine does not calculate cable
length, layback, the shape of the array during a turn, or a displaced array
position. Bearings are taken at the hull.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


IN_BEAM = "in_beam"
ENDFIRE = "endfire"
BAFFLE = "baffle"
UNSTABLE = "unstable"
STOWED = "stowed"

HULL_ID = "hull_array"
FLANK_ID = "flank_array"
TOWED_ID = "towed_array"

STREAMING = "streaming"
STREAMED = "streamed"
RECOVERING = "recovering"

OWN_SHIP_MOUNTED_NOISE = "own-ship-mounted"
TOWED_NOISE = "towed-array"
ENVIRONMENT_BEARING = "own-ship-environment"

SURFACE_CLEARANCE_FEET = 40.0
BOTTOM_CLEARANCE_FEET = 40.0
TURN_SETTLE_MINUTES = 5
PUMP_FAULT_DB = 3.5
FREQUENCY_FLOOR_HZ = 20.0
FLOW_NOISE_COEFFICIENT = 0.055
FLOW_NOISE_FACTOR_FLOOR = 0.24
UNSTABLE_SELF_NOISE_DB = 6.0
ENVIRONMENT_BEARING_BIAS_DEGREES = 1.0


def _in_arc(relative_deg, start_deg, end_deg):
    """True when relative bearing is on the closed arc from start to end, clockwise."""
    rel = relative_deg % 360.0
    start = start_deg % 360.0
    end = end_deg % 360.0
    if start <= end:
        return start <= rel <= end
    return rel >= start or rel <= end


def _in_any_arc(relative_deg, arcs):
    return any(_in_arc(relative_deg, start, end) for start, end in arcs)


@dataclass(frozen=True)
class ArraySpec:
    identifier: str
    kind: str
    description: str
    directivity_index_db: float
    beam_arcs: tuple[tuple[float, float], ...]
    endfire_arcs: tuple[tuple[float, float], ...] = ()
    endfire_directivity_db: float | None = None
    design_frequency_hz: float = 250.0
    depth_offset_feet: float = 0.0
    deployable: bool = False
    deployment_minutes: int = 0
    retrieval_minutes: int = 0
    deploy_speed_knots: float | None = None
    stable_speed_knots: float | None = None
    pump_coupling: float = 1.0
    flow_scale: float = 1.0
    quiet_speed_knots: float = 5.0
    frequency_gain_slope: float = 8.0
    frequency_gain_high_slope: float | None = None
    frequency_gain_pivot_hz: float | None = None
    frequency_gain_min_db: float = -12.0
    frequency_gain_max_db: float = 4.0
    self_noise_family: str = OWN_SHIP_MOUNTED_NOISE
    geometry_note: str = ""

    def public_definition(self):
        return {
            "id": self.identifier,
            "role": self.kind,
            "description": self.description,
            "directivity_index_db": self.directivity_index_db,
            "beam_arcs_relative_deg": [
                {"from": start, "to": end} for start, end in self.beam_arcs
            ],
            "endfire_arcs_relative_deg": [
                {"from": start, "to": end} for start, end in self.endfire_arcs
            ],
            "endfire_directivity_db": self.endfire_directivity_db,
            "design_frequency_hz": self.design_frequency_hz,
            "depth_offset_feet": self.depth_offset_feet,
            "deployable": self.deployable,
            "deployment_minutes": self.deployment_minutes,
            "retrieval_minutes": self.retrieval_minutes,
            "deploy_speed_knots": self.deploy_speed_knots,
            "stable_speed_knots": self.stable_speed_knots,
            "pump_coupling": self.pump_coupling,
            "flow_scale": self.flow_scale,
            "quiet_speed_knots": self.quiet_speed_knots,
            "frequency_response": {
                "floor_hz": FREQUENCY_FLOOR_HZ,
                "slope_db_per_decade": self.frequency_gain_slope,
                "high_slope_db_per_decade": self.frequency_gain_high_slope,
                "pivot_hz": self.frequency_gain_pivot_hz,
                "min_gain_db": self.frequency_gain_min_db,
                "max_gain_db": self.frequency_gain_max_db,
            },
            "self_noise": {
                "family": self.self_noise_family,
                "flow_coefficient": FLOW_NOISE_COEFFICIENT,
                "factor_floor": FLOW_NOISE_FACTOR_FLOOR,
                "pump_fault_db": PUMP_FAULT_DB,
                "unstable_extra_db": UNSTABLE_SELF_NOISE_DB,
            },
            "self_noise_family": self.self_noise_family,
            "geometry_note": self.geometry_note,
        }


HULL = ArraySpec(
    identifier=HULL_ID,
    kind="hull",
    description=(
        "Idealized spherical bow receiver at keel depth. An aft baffle is deaf. "
        "Response favors mid and high frequencies."
    ),
    directivity_index_db=8.0,
    beam_arcs=((220.0, 140.0),),
    design_frequency_hz=800.0,
    pump_coupling=1.0,
    flow_scale=1.0,
    self_noise_family=OWN_SHIP_MOUNTED_NOISE,
    geometry_note="Receiver depth is present keel depth. There is no horizontal offset.",
)

FLANK = ArraySpec(
    identifier=FLANK_ID,
    kind="flank",
    description=(
        "Idealized combined port and starboard flank receiver at keel depth. "
        "Bow and stern gaps are baffles. Response favors the low-to-mid band."
    ),
    directivity_index_db=11.0,
    beam_arcs=((20.0, 160.0), (200.0, 340.0)),
    design_frequency_hz=250.0,
    pump_coupling=0.5,
    flow_scale=0.65,
    frequency_gain_slope=6.0,
    frequency_gain_min_db=-8.0,
    frequency_gain_max_db=3.0,
    self_noise_family=OWN_SHIP_MOUNTED_NOISE,
    geometry_note=(
        "Port and starboard flanks are one receiver with two beam sectors. "
        "Receiver depth is present keel depth. There is no horizontal offset."
    ),
)

TOWED = ArraySpec(
    identifier=TOWED_ID,
    kind="towed",
    description=(
        "Idealized deployable line array. Broadside beams hear; ahead and astern "
        "are endfire. Response favors low frequencies."
    ),
    directivity_index_db=14.0,
    beam_arcs=((40.0, 140.0), (220.0, 320.0)),
    endfire_arcs=((320.0, 40.0), (140.0, 220.0)),
    endfire_directivity_db=6.0,
    design_frequency_hz=80.0,
    depth_offset_feet=120.0,
    deployable=True,
    deployment_minutes=15,
    retrieval_minutes=10,
    deploy_speed_knots=8.0,
    stable_speed_knots=12.0,
    pump_coupling=0.12,
    flow_scale=1.4,
    quiet_speed_knots=8.0,
    frequency_gain_slope=3.0,
    frequency_gain_high_slope=-10.0,
    frequency_gain_pivot_hz=400.0,
    frequency_gain_min_db=-16.0,
    frequency_gain_max_db=4.0,
    self_noise_family=TOWED_NOISE,
    geometry_note=(
        "Receiver depth is keel depth plus 120 feet, clipped 40 feet below the "
        "surface and 40 feet above the water depth. Bearings are taken at the "
        "hull. Cable length, layback, the shape of the array during a turn and "
        "a displaced array position are not calculated. A turn, or the five "
        "minutes after one, marks the receiver unstable instead of walking a "
        "bearing around a bent array."
    ),
)

GENERIC_HULL = ArraySpec(
    identifier="integrated_passive",
    kind="hull",
    description="Generic hull-like receiver used by platforms without a modeled suite.",
    directivity_index_db=5.0,
    beam_arcs=((0.0, 360.0),),
    design_frequency_hz=200.0,
    pump_coupling=1.0,
    flow_scale=1.0,
    self_noise_family=OWN_SHIP_MOUNTED_NOISE,
    geometry_note="Receiver depth is present keel depth.",
)

SUITES = {
    "kestrel-ssn-v2": (HULL, FLANK, TOWED),
}


def suite_for(platform_id):
    return SUITES.get(platform_id)


def array_spec(platform_id, array_id):
    suite = suite_for(platform_id)
    if suite is None:
        if array_id in (GENERIC_HULL.identifier, None, HULL_ID):
            return GENERIC_HULL
        raise ValueError(f"Unknown receiver {array_id!r}.")
    if array_id is None:
        return suite[0]
    try:
        return next(item for item in suite if item.identifier == array_id)
    except StopIteration as error:
        raise ValueError(f"Unknown receiver {array_id!r} for {platform_id}.") from error


def relative_bearing_deg(own_course_deg, true_bearing_deg):
    return (true_bearing_deg - own_course_deg) % 360.0


def coverage_status(spec, relative_deg, unstable=False):
    if spec.deployable and unstable:
        return UNSTABLE
    if _in_any_arc(relative_deg, spec.beam_arcs):
        return IN_BEAM
    if spec.endfire_arcs and _in_any_arc(relative_deg, spec.endfire_arcs):
        return ENDFIRE
    if spec.beam_arcs == ((0.0, 360.0),):
        return IN_BEAM
    return BAFFLE


def keeps_observation(status):
    return status in (IN_BEAM, ENDFIRE)


def receiver_depth_feet(spec, keel_feet, water_depth_feet):
    depth = keel_feet + spec.depth_offset_feet
    ceiling = SURFACE_CLEARANCE_FEET
    floor = water_depth_feet - BOTTOM_CLEARANCE_FEET
    if floor < ceiling:
        return min(max(keel_feet, ceiling), water_depth_feet)
    return min(max(depth, ceiling), floor)


def frequency_gain_db(spec, frequency_hz):
    """Fictional receiver response relative to the design band, clipped."""
    frequency = max(frequency_hz, FREQUENCY_FLOOR_HZ)
    design = spec.design_frequency_hz
    pivot = spec.frequency_gain_pivot_hz
    if pivot is not None and frequency > pivot:
        gain = (
            spec.frequency_gain_slope * math.log10(pivot / design)
            + spec.frequency_gain_high_slope * math.log10(frequency / pivot)
        )
    else:
        gain = spec.frequency_gain_slope * math.log10(frequency / design)
    return max(spec.frequency_gain_min_db, min(spec.frequency_gain_max_db, gain))


def self_noise_adjustment_db(spec, speed_knots, pump_fault=False, unstable=False):
    slope = FLOW_NOISE_COEFFICIENT * spec.flow_scale
    factor = max(
        FLOW_NOISE_FACTOR_FLOOR,
        1.0 - max(0.0, speed_knots - spec.quiet_speed_knots) * slope,
    )
    extra = spec.pump_coupling * PUMP_FAULT_DB if pump_fault else 0.0
    if unstable:
        extra += UNSTABLE_SELF_NOISE_DB
    return -10.0 * math.log10(factor) + extra


def directivity_db(spec, coverage, extra_di=0.0):
    if coverage == ENDFIRE and spec.endfire_directivity_db is not None:
        base = spec.endfire_directivity_db
    else:
        base = spec.directivity_index_db
    return base + extra_di


def equipment_state(entity, array_id):
    return entity.get("equipment", {}).get(array_id, "available")


def is_listening(spec, entity):
    if not spec.deployable:
        return equipment_state(entity, spec.identifier) not in (STOWED, STREAMING, RECOVERING)
    return equipment_state(entity, spec.identifier) == STREAMED


def listening_specs(entity):
    suite = suite_for(entity.get("spec"))
    if suite is None:
        return (GENERIC_HULL,)
    return tuple(spec for spec in suite if is_listening(spec, entity))


def is_unstable(record, elapsed_minutes):
    """True through the turn and the complete five-minute step that settles it."""
    until = 0 if record is None else record.get("unstable_until") or 0
    return bool(until) and elapsed_minutes <= until


def snapshot(
    spec,
    entity,
    true_bearing_deg=None,
    water_depth_feet=2000.0,
    unstable=False,
    extra_di=0.0,
):
    relative = None
    coverage = IN_BEAM
    if true_bearing_deg is not None:
        relative = relative_bearing_deg(entity["course"], true_bearing_deg)
        coverage = coverage_status(spec, relative, unstable=unstable and spec.deployable)
    elif spec.deployable and unstable:
        coverage = UNSTABLE
    depth = receiver_depth_feet(spec, entity["depth"], water_depth_feet)
    state = equipment_state(entity, spec.identifier)
    if spec.deployable and state != STREAMED:
        coverage = STOWED if state == STOWED else state
    return {
        "id": spec.identifier,
        "kind": spec.kind,
        "depth_feet": depth,
        "directivity_index_db": directivity_db(spec, coverage, extra_di),
        "design_frequency_hz": spec.design_frequency_hz,
        "state": state,
        "coverage": coverage,
        "relative_bearing_deg": None if relative is None else round(relative, 2),
        "self_noise_family": spec.self_noise_family,
        "deployable": spec.deployable,
        "geometry_note": spec.geometry_note,
        "note": spec.description,
    }


def error_sources(spec, evidence_window, coverage):
    return {
        "environment_window": evidence_window,
        "shared_bearing_bias": ENVIRONMENT_BEARING,
        "array_bearing_bias": spec.identifier,
        "frequency_bias_environment": evidence_window,
        "frequency_bias_array": spec.identifier,
        "self_noise": spec.self_noise_family,
        "coverage": coverage,
        "correlation": (
            "Reports that share an environment window have one quality offset "
            "and one environmental frequency bias. Reports that share an "
            f"array_bearing_bias of {spec.identifier} share that receiver's "
            "bearing bias. Hull and flank share own-ship-mounted self-noise; "
            "the towed array does not. A history on another receiver is not "
            "automatically the same contact."
        ),
    }


def public_receiver(snapshot_row):
    return {
        "id": snapshot_row["id"],
        "role": snapshot_row["kind"],
        "depth_feet": round(snapshot_row["depth_feet"], 2),
        "state": snapshot_row["state"],
        "coverage": snapshot_row["coverage"],
        "directivity_index_db": snapshot_row["directivity_index_db"],
    }


def public_sonar_capabilities(platform_id):
    suite = suite_for(platform_id)
    if suite is None:
        return {
            "towed_array_modeled": False,
            "separate_array_geometry_modeled": False,
            "contact_correlation_across_receivers": "automatic",
        }
    towed = next((item for item in suite if item.deployable), None)
    return {
        "implemented": [
            "hull, flank and towed receivers",
            "baffles, endfire and deployment state",
            "focused contact analysis",
            "numeric narrowband frequency measurements",
            "one active pulse with an imperfect range measurement on the hull receiver",
        ],
        "towed_array_modeled": towed is not None,
        "separate_array_geometry_modeled": True,
        "spectral_frequencies_modeled": True,
        "receivers": [item.public_definition() for item in suite],
        "towed_array_geometry": towed.geometry_note if towed else None,
        "deployment": {
            "stream_activity": "stream_array",
            "recover_activity": "recover_array",
            "deployment_minutes": towed.deployment_minutes if towed else None,
            "retrieval_minutes": towed.retrieval_minutes if towed else None,
            "deploy_speed_knots": towed.deploy_speed_knots if towed else None,
            "stable_speed_knots": towed.stable_speed_knots if towed else None,
            "turn_settle_minutes": TURN_SETTLE_MINUTES,
            "progress": (
                "Streaming and recovery credit only minutes actually spent at or "
                "below the deploy speed, retain progress across interrupted "
                "windows, and occupy the selected activity while hull and flank "
                "still listen. Recovery marks the towed receiver recovering before "
                "that step's listening, so it does not keep bearings while it is "
                "being recovered."
            ),
        },
        "contact_correlation_across_receivers": (
            "A track is one receiver's history. Hull, flank and towed detections "
            "are not automatically the same contact. A visual sighting is its own "
            "history. Shared error sources are listed on each observation. Focused "
            "analysis requires an acoustic receiver history."
        ),
        "frequency_response_rule": (
            "Gain is slope_db_per_decade times log10(frequency / design_frequency_hz). "
            "Above pivot_hz the published high slope applies. The result is clipped "
            "to min_gain_db and max_gain_db. Frequencies below floor_hz use the floor."
        ),
        "self_noise_rule": (
            "Self-noise rises with speed above quiet_speed_knots as -10 log10 of "
            "max(factor_floor, 1 - (speed - quiet_speed_knots) * flow_coefficient * "
            "flow_scale), plus pump_coupling * pump_fault_db when the pump is "
            "degraded, plus unstable_extra_db while the towed receiver is unstable."
        ),
        "bearing_bias_model": {
            "combined_degrees": 2,
            "environment_degrees": ENVIRONMENT_BEARING_BIAS_DEGREES,
            "rule": (
                "Each reported acoustic bearing adds one environment bias of at most "
                f"{ENVIRONMENT_BEARING_BIAS_DEGREES:g} degree and one receiver bias "
                "drawn so the sum stays within 2 degrees. The motion estimator "
                "searches that combined 2-degree bias."
            ),
        },
        "active_receiver": HULL_ID,
        "relative_bearing": (
            "Coverage uses relative bearing, 000 at the bow, increasing clockwise. "
            "A baffle produces no kept observation. Endfire still receives with "
            "the published reduced directivity."
        ),
    }


def initial_towed_state(platform_id):
    suite = suite_for(platform_id)
    if suite is None or not any(item.deployable for item in suite):
        return None
    return {
        "deployment": STOWED,
        "progress_minutes": 0,
        "unstable_until": 0,
    }


def towed_speed_limit_knots(record, spec=TOWED):
    if record is None or record["deployment"] == STOWED:
        return None
    if record["deployment"] in (STREAMING, RECOVERING):
        return spec.deploy_speed_knots
    if record["deployment"] == STREAMED:
        return spec.stable_speed_knots
    return spec.deploy_speed_knots


def public_towed(record, spec=TOWED):
    if record is None:
        return None
    return {
        "id": spec.identifier,
        "deployment": record["deployment"],
        "progress_minutes": record["progress_minutes"],
        "deployment_minutes": spec.deployment_minutes,
        "retrieval_minutes": spec.retrieval_minutes,
        "deploy_speed_knots": spec.deploy_speed_knots,
        "stable_speed_knots": spec.stable_speed_knots,
        "unstable_until": record["unstable_until"],
        "geometry_note": spec.geometry_note,
    }
