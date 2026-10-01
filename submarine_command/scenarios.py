"""Published patrol scenarios.

A scenario is the captain's brief plus the hidden layout drawn at
initialization. Movement, acoustics, communications and order validation stay
on rules version 0.12. Opposing specifications stay out of the public brief.
"""
import math

from .platforms import BIOLOGIC, DART, FISHER, KESTREL, MERCHANT, WARSHIP

GLASS_STRAIT = "glass-strait"
HARROW_BANK = "harrow-bank"

GLASS_MISSION = {
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

HARROW_MISSION = {
    "title": "Operation Harrow Bank",
    "own_ship": "Kestrel",
    "setting": "Fictional fisheries-surveillance exercise on the Harrow Bank.",
    "task": "Decide whether submerged traffic is on the bank and send an evidence-based assessment by 0630.",
    "relief": "Be within 3 nautical miles of station SOUTHING (2 east, -12 north) at 0700.",
    "chart": (
        "Local grid in nautical miles: east is +x, north is +y. "
        "Harrow Bank operating area: x=0 to 16, y=0 to 12. "
        "Own ship starts south of that area. "
        "One water depth and one sound-speed profile apply on the whole chart. "
        "The bank is an operating area, not a modeled change in bathymetry."
    ),
    "orders": "Observe and report. Preserve discretion and meet the relief commitment. No offensive weapons employment is authorized in this patrol.",
    "intel": (
        "Shore authorities asked for a check of the bank and did not provide a contact solution. "
        "Fishing traffic is expected. A diesel submarine is one possibility, not an established fact. "
        "A later bulletin may be wrong and does not identify a track."
    ),
    "radio": (
        "An intelligence update is scheduled for 0310; a later update for 0510. "
        "Messages remain dated reports available for retrieval. "
        "A mast receive can copy one once its scheduled time has passed. "
        "A buoyant receive adds that mode's published delivery latency. "
        "A completed mast transmission can be intercepted by a surface combatant inside the published range."
    ),
    "units": "Courses and bearings true; speed in knots; depth in feet; distances in nautical miles.",
    "simplifications": (
        "A diesel submarine that reaches its low-battery rule changes depth, speed limit and emitted spectrum at a five-minute boundary. "
        "The mast does not report that state as a visual surface contact. "
        "Only a submerged opponent maneuvers after detecting own ship. "
        "A surface combatant that intercepts a mast transmission is recorded and does not change course because of that intercept. "
        "Fishing vessels and biologics keep their published course, speed and depth cycles. Other traffic holds its initial course and speed."
    ),
}


class Relief:
    def __init__(self, name, x, y, radius_nm=3.0):
        self.name = name
        self.x = x
        self.y = y
        self.radius_nm = radius_nm

    def as_state(self):
        return {
            "name": self.name,
            "x": self.x,
            "y": self.y,
            "radius_nm": self.radius_nm,
        }


class Schedule:
    def __init__(self, clock_origin_minutes, report_due_minutes, end_minutes,
                 deadline_text, end_text, end_reason, relief):
        self.clock_origin_minutes = clock_origin_minutes
        self.report_due_minutes = report_due_minutes
        self.end_minutes = end_minutes
        self.deadline_text = deadline_text
        self.end_text = end_text
        self.end_reason = end_reason
        self.relief = relief

    def as_state(self):
        return {
            "clock_origin_minutes": self.clock_origin_minutes,
            "report_due_minutes": self.report_due_minutes,
            "end_minutes": self.end_minutes,
            "deadline_text": self.deadline_text,
            "end_text": self.end_text,
            "end_reason": self.end_reason,
            "relief": self.relief.as_state(),
        }


class Scenario:
    def __init__(self, identifier, mission, schedule, own_start, place, navigation_text):
        self.identifier = identifier
        self.mission = mission
        self.schedule = schedule
        self.own_start = own_start
        self.place = place
        self.navigation_text = navigation_text

    def public_entry(self):
        relief = self.schedule.relief
        start = self.own_start
        return {
            "id": self.identifier,
            "rules_version": "0.12",
            "mission": dict(self.mission),
            "clock": {
                "origin_minutes_past_midnight": self.schedule.clock_origin_minutes,
                "assessment_deadline_minutes": self.schedule.report_due_minutes,
                "end_minutes": self.schedule.end_minutes,
            },
            "relief": {
                "name": relief.name,
                "east_nm": relief.x,
                "north_nm": relief.y,
                "radius_nm": relief.radius_nm,
            },
            "own_ship_start": {
                "east_nm": start["x"],
                "north_nm": start["y"],
                "course_true": start["course"],
                "speed_knots": start["speed"],
                "depth_feet": start["depth"],
                "operating_mode": start["operating_mode"],
            },
            "selection": (
                "Opposing entities are drawn at initialization and remain hidden "
                "until supported observations identify them. The brief does not "
                "state which draw this patrol received."
            ),
        }


def _glass_navigation(_own):
    return ("0310. Local position (0 east, 0 north). Course 090, speed 5 knots, "
            "depth 400 feet. RELIEF lies 24 nautical miles east.")


def _place_glass(state, dice, make_entity):
    """Hidden Glass Strait layout. Dice labels and order are part of replay."""
    kinds = ("surface", "submerged", "biologic")
    kind = dice.choose("initial:primary-kind", kinds, [0.46, 0.44, 0.10])
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
    shore_type = kind if shore_correct else dice.choose(
        "initial:shore-error", [item for item in kinds if item != kind]
    )
    estimate = {
        "surface": "Coastal watch reports a possible surface vessel in the eastern approach.",
        "submerged": "Coastal watch reports a possible submerged contact in the eastern approach.",
        "biologic": "Coastal watch reports biological activity that may account for some acoustic reports.",
    }[shore_type]
    state["bulletins"] = [
        {"id": "INTEL-0330", "available": 20, "text": "0330 intelligence update: " + estimate + " Source confidence: moderate; exact track unavailable. This is an independent shore report, not confirmed identification."},
        {"id": "OPS-0510", "available": 120, "text": "0510 operations update: assessment deadline 0730 and relief station time 0800 remain unchanged. Commercial schedules are incomplete; absence from the list does not establish military identity."},
    ]


def _harrow_navigation(own):
    relief = HARROW_SCHEDULE.relief
    east = relief.x - own["x"]
    north = relief.y - own["y"]
    gap = math.hypot(east, north)
    bearing = math.degrees(math.atan2(east, north)) % 360
    return (
        f"0210. Local position ({own['x']:g} east, {own['y']:g} north). "
        f"Course {own['course']:03.0f}, speed {own['speed']:g} knots, "
        f"depth {own['depth']:g} feet. "
        f"{relief.name} bears {bearing:.0f} true, {gap:.2f} nautical miles."
    )


def _place_harrow(state, dice, make_entity):
    """Hidden Harrow Bank layout.

    The patrol question is whether a diesel submarine is among the bank
    traffic. When one is present it starts on battery, with a fictional charge
    low enough that the published drain reaches the snorkeling rule before the
    assessment deadline if it keeps the drawn speed. Fishing traffic is always
    present. A biologic group is an alternative explanation, not an extra
    submarine. The warship starts outside mast-intercept range of the opening
    position and inside that range of the northern bank.
    """
    case = dice.choose(
        "harrow:case",
        ("diesel", "biologic", "surface"),
        (0.42, 0.33, 0.25),
    )
    submerged = case == "diesel"
    if case == "diesel":
        battery = dice.between("harrow:battery", 28, 32)
        primary = make_entity(
            DART,
            id="actor-a",
            x=dice.between("harrow:diesel-x", 6, 11),
            y=dice.between("harrow:diesel-y", 2, 5),
            course=dice.between("harrow:diesel-course", 350, 370) % 360,
            speed=dice.between("harrow:diesel-speed", 6, 7),
            depth=dice.between("harrow:diesel-depth", 180, 450),
            resources={"battery_energy": battery},
            aware=False,
            last_heard=None,
            evaded=False,
            name="Plover",
            intent=(
                "Continue the initial course on battery until the battery "
                "requires a charge, then snorkel. Avoid an observer if one is detected."
            ),
        )
    elif case == "biologic":
        primary = make_entity(
            BIOLOGIC,
            id="actor-a",
            x=dice.between("harrow:biologic-x", 7, 12),
            y=dice.between("harrow:biologic-y", 3, 8),
            course=dice.between("harrow:biologic-course", 0, 359),
            speed=dice.between("harrow:biologic-speed", 1.5, 4),
            depth=dice.between("harrow:biologic-depth", 120, 500),
            aware=False,
            last_heard=None,
            evaded=False,
            name="Chorus",
            intent="Vocalize and shift course, speed and depth on the published cycle.",
        )
    else:
        primary = _fishing_vessel(
            make_entity, dice, "harrow:primary-fisher", "actor-a", "Nettle",
            "Work the bank, shifting between transit and fishing.",
        )
    state["actors"].append(primary)
    if case == "surface":
        state["actors"].append(_fishing_vessel(
            make_entity, dice, "harrow:fisher", "actor-2", "Gorse",
            "Work the bank, shifting between transit and fishing.",
        ))
    else:
        state["actors"].append(_fishing_vessel(
            make_entity, dice, "harrow:fisher-a", "actor-2", "Nettle",
            "Work the bank, shifting between transit and fishing.",
        ))
        state["actors"].append(_fishing_vessel(
            make_entity, dice, "harrow:fisher-b", "actor-3", "Gorse",
            "Work the bank, shifting between transit and fishing.",
        ))
    state["actors"].append(make_entity(
        MERCHANT,
        id="actor-merchant",
        x=dice.between("harrow:merchant-x", 1, 4),
        y=dice.between("harrow:merchant-y", 4, 8),
        course=dice.between("harrow:merchant-course", 80, 100),
        speed=dice.between("harrow:merchant-speed", 9, 12),
        depth=0,
        aware=False,
        last_heard=None,
        evaded=False,
        name="Halyard",
        intent="Continue an eastbound passage across the bank.",
    ))
    state["actors"].append(make_entity(
        WARSHIP,
        id="actor-warship",
        x=dice.between("harrow:warship-x", 16, 20),
        y=dice.between("harrow:warship-y", 9, 13),
        course=dice.between("harrow:warship-course", 250, 280),
        speed=dice.between("harrow:warship-speed", 8, 12),
        depth=0,
        aware=False,
        last_heard=None,
        evaded=False,
        name="Cresset",
        intent="Hold the initial westbound course across the northern bank.",
    ))
    shore_correct = dice.u("harrow:shore-source") < 0.76
    if submerged == shore_correct:
        estimate = (
            "Fisheries patrol reports a possible submerged contact among the bank traffic."
            if submerged
            else "Fisheries patrol reports no separate submerged contact; bank traffic may be surface or biologic."
        )
    else:
        estimate = (
            "Fisheries patrol reports no separate submerged contact; bank traffic may be surface or biologic."
            if submerged
            else "Fisheries patrol reports a possible submerged contact among the bank traffic."
        )
    state["bulletins"] = [
        {
            "id": "INTEL-0310",
            "available": 60,
            "text": (
                "0310 intelligence update: " + estimate +
                " Source confidence: moderate; exact track unavailable. "
                "This is an independent shore report, not confirmed identification."
            ),
        },
        {
            "id": "OPS-0510",
            "available": 180,
            "text": (
                "0510 operations update: assessment deadline 0630 and relief "
                "station time 0700 remain unchanged. A change in received sound "
                "is a new measurement. It does not by itself establish a submarine."
            ),
        },
    ]


def _fishing_vessel(make_entity, dice, label, actor_id, name, intent):
    return make_entity(
        FISHER,
        id=actor_id,
        x=dice.between(f"{label}-x", 2, 14),
        y=dice.between(f"{label}-y", 1, 10),
        course=dice.between(f"{label}-course", 0, 359),
        speed=dice.between(f"{label}-speed", 3, 6),
        depth=0,
        aware=False,
        last_heard=None,
        evaded=False,
        name=name,
        intent=intent,
    )


HARROW_SCHEDULE = Schedule(
    clock_origin_minutes=130,
    report_due_minutes=260,
    end_minutes=290,
    deadline_text="0630 assessment deadline reached.",
    end_text="0700. Patrol exercise complete; the debrief can now be requested.",
    end_reason="0700 relief time reached",
    relief=Relief("SOUTHING", 2.0, -12.0, 3.0),
)

GLASS_SCHEDULE = Schedule(
    clock_origin_minutes=190,
    report_due_minutes=260,
    end_minutes=290,
    deadline_text="0730 assessment deadline reached.",
    end_text="0800. Patrol exercise complete; the debrief can now be requested.",
    end_reason="0800 relief time reached",
    relief=Relief("RELIEF", 24.0, 0.0, 3.0),
)

GLASS_START = {
    "x": 0.0,
    "y": 0.0,
    "course": 90.0,
    "speed": 5.0,
    "depth": 400.0,
    "operating_mode": KESTREL.default_mode,
}

HARROW_START = {
    "x": 8.0,
    "y": -6.0,
    "course": 0.0,
    "speed": 4.0,
    "depth": 400.0,
    "operating_mode": "standard",
}

SCENARIOS = {
    GLASS_STRAIT: Scenario(
        GLASS_STRAIT,
        GLASS_MISSION,
        GLASS_SCHEDULE,
        GLASS_START,
        _place_glass,
        _glass_navigation,
    ),
    HARROW_BANK: Scenario(
        HARROW_BANK,
        HARROW_MISSION,
        HARROW_SCHEDULE,
        HARROW_START,
        _place_harrow,
        _harrow_navigation,
    ),
}


def require_scenario(identifier):
    try:
        return SCENARIOS[identifier]
    except KeyError as error:
        known = ", ".join(sorted(SCENARIOS))
        raise ValueError(
            f"Unknown scenario {identifier!r}. Published scenarios: {known}."
        ) from error


def public_catalog():
    return [scenario.public_entry() for scenario in SCENARIOS.values()]
