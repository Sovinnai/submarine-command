"""Published patrol scenarios.

A scenario is the captain's brief plus the hidden layout drawn at
initialization. Movement, acoustics, communications, employment and order
validation stay on rules version 0.15. Glass Strait is a corridor assessment
with a relief station. Miller Line is a barrier watch. Cinder Road is a
wartime convoy attack. Opposing specifications stay out of the public brief.
"""
import math

from .platforms import BIOLOGIC, DART, FISHER, KESTREL, MERCHANT, WARSHIP

GLASS_STRAIT = "glass-strait"
MILLER_LINE = "miller-line"
CINDER_ROAD = "cinder-road"

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

MILLER_MISSION = {
    "title": "Operation Miller Line",
    "own_ship": "Kestrel",
    "setting": "Fictional barrier watch on the Miller Line. This is not a search for an unknown contact and there is no relief station.",
    "task": (
        "Detect a submerged boat as it crosses the Miller Line. "
        "A surface ship may cross the same line. That crossing does not end the watch. "
        "The watch ends at 0700 wherever Kestrel is."
    ),
    "chart": (
        "Local grid in nautical miles: east is +x, north is +y. "
        "The Miller Line is the meridian x=0, from y=-18 to y=+18. "
        "North sector is y>0. South sector is y<0. "
        "One water depth and one sound-speed profile apply on both sides of the line."
    ),
    "orders": (
        "Hold the barrier and observe. No offensive weapons employment is authorized. "
        "Kestrel may cross to the east of the line; that leaves the assigned side and is recorded. "
        "There is no station to reach."
    ),
    "intel": (
        "A submerged westbound transit is expected to cross during the watch, in one sector. "
        "A shore plot delivered by radio may name the sector. The plot can be wrong and is not a track. "
        "A loud surface ship may cross the other sector first."
    ),
    "radio": (
        "A sector plot is scheduled for 0120. An operations reminder is scheduled for 0330. "
        "A mast receive can copy one once its scheduled time has passed. "
        "A buoyant receive adds that mode's published delivery latency and then limits depth and speed until retrieval. "
        "Sending an assessment is not the assigned task. A mast transmission can still be intercepted by a surface combatant inside the published range."
    ),
    "units": "Courses and bearings true; speed in knots; depth in feet; distances in nautical miles.",
    "simplifications": (
        "The crossing boat holds its initial course and speed unless it detects Kestrel, in which case it evades once from its own fix. "
        "The surface ship and the surface combatant hold their initial courses. The biologic group keeps its published cycle. "
        "The debrief counts an acoustic observation of the crossing boat at or before the crossing time. "
        "A later detection, a visual mast sighting, and a transmitted presence assessment are not that result. "
        "The mast does not see a deep boat as a surface contact."
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
            "relief": None if self.relief is None else self.relief.as_state(),
        }


class Scenario:
    def __init__(self, identifier, mission, schedule, own_start, place, navigation_text,
                 geometry=None, offensive_weapons=False,
                 offensive_loadout="exercise_heavyweight"):
        self.identifier = identifier
        self.mission = mission
        self.schedule = schedule
        self.own_start = own_start
        self.place = place
        self.navigation_text = navigation_text
        self.geometry = geometry
        self.offensive_weapons = offensive_weapons
        self.offensive_loadout = offensive_loadout

    def public_entry(self):
        start = self.own_start
        clock = {
            "origin_minutes_past_midnight": self.schedule.clock_origin_minutes,
            "end_minutes": self.schedule.end_minutes,
        }
        if self.schedule.report_due_minutes is not None:
            clock["assessment_deadline_minutes"] = self.schedule.report_due_minutes
        entry = {
            "id": self.identifier,
            "rules_version": "0.15",
            "offensive_loadout": self.offensive_loadout,
            "mission": dict(self.mission),
            "clock": clock,
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
        relief = self.schedule.relief
        if relief is not None:
            entry["relief"] = {
                "name": relief.name,
                "east_nm": relief.x,
                "north_nm": relief.y,
                "radius_nm": relief.radius_nm,
            }
        if self.geometry is not None:
            entry["geometry"] = dict(self.geometry)
        return entry


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
        doctrine="avoid" if kind == "submerged" else "passage",
        observations=[],
        belief={"assessment": "no_contact"},
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
                doctrine="passage",
                observations=[],
                belief={"assessment": "no_contact"},
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


def _miller_navigation(own):
    return (
        f"0100. Local position ({own['x']:g} east, {own['y']:g} north). "
        f"Course {own['course']:03.0f}, speed {own['speed']:g} knots, "
        f"depth {own['depth']:g} feet. "
        "The Miller Line is 10 nautical miles due east. No relief station is assigned."
    )


def _westbound(dice, label):
    return dice.between(f"{label}-course", 260, 280)


def _place_miller(state, dice, make_entity):
    """Hidden Miller Line layout.

    A diesel submarine starts east of the line and will cross it during the
    watch if it keeps the drawn course and speed. Its sector is never the
    center. A merchant starts closer to the line in the opposite sector and
    crosses first. A shore plot names a sector and is wrong on the draw stored
    in this module. The plot text does not contain the crossing latitude.
    """
    north = dice.u("miller:sector") < 0.5
    sign = 1.0 if north else -1.0
    diesel_y = sign * dice.between("miller:diesel-y", 6, 16)
    diesel = make_entity(
        DART,
        id="actor-crosser",
        x=dice.between("miller:diesel-x", 18, 24),
        y=diesel_y,
        course=_westbound(dice, "miller:diesel"),
        speed=dice.between("miller:diesel-speed", 5, 7),
        depth=dice.between("miller:diesel-depth", 280, 520),
        aware=False,
        last_heard=None,
        evaded=False,
        name="Wicket",
        doctrine="avoid",
        observations=[],
        belief={"assessment": "no_contact"},
        intent="Cross the line on the initial course. Evade if an observer is detected.",
    )
    state["actors"].append(diesel)
    merchant_y = -sign * dice.between("miller:merchant-y", 8, 16)
    state["actors"].append(make_entity(
        MERCHANT,
        id="actor-merchant",
        x=dice.between("miller:merchant-x", 8, 12),
        y=merchant_y,
        course=_westbound(dice, "miller:merchant"),
        speed=dice.between("miller:merchant-speed", 10, 12),
        depth=0,
        aware=False,
        last_heard=None,
        evaded=False,
        name="Trestle",
        doctrine="passage",
        observations=[],
        belief={"assessment": "no_contact"},
        intent="Cross the line on the initial course and continue west.",
    ))
    state["actors"].append(make_entity(
        BIOLOGIC,
        id="actor-biologic",
        x=dice.between("miller:biologic-x", -3, 3),
        y=dice.between("miller:biologic-y", -6, 6),
        course=dice.between("miller:biologic-course", 0, 359),
        speed=dice.between("miller:biologic-speed", 1.5, 3.5),
        depth=dice.between("miller:biologic-depth", 150, 400),
        aware=False,
        last_heard=None,
        evaded=False,
        name="Murmur",
        doctrine="passage",
        observations=[],
        belief={"assessment": "no_contact"},
        intent="Remain near the center of the line and cycle vocalization.",
    ))
    patrol_north = dice.u("miller:warship-direction") < 0.5
    state["actors"].append(make_entity(
        WARSHIP,
        id="actor-picket",
        x=dice.between("miller:warship-x", 8, 14),
        y=dice.between("miller:warship-y", -8, 8),
        course=0.0 if patrol_north else 180.0,
        speed=dice.between("miller:warship-speed", 8, 12),
        depth=0,
        aware=False,
        last_heard=None,
        evaded=False,
        name="Picket",
        doctrine="passage",
        observations=[],
        belief={"assessment": "no_contact"},
        intent="Patrol north or south on the east side of the line. Do not cross it.",
    ))
    state["barrier"] = {
        "line_x": 0.0,
        "south_y": -18.0,
        "north_y": 18.0,
        "crosser_id": diesel["id"],
        "crossed": False,
        "crossing_elapsed_minutes": None,
        "own_ship_crossed": False,
    }
    plot_north = north if dice.u("miller:plot") < 0.68 else not north
    sector_name = "north" if plot_north else "south"
    side = "north of center" if plot_north else "south of center"
    state["bulletins"] = [
        {
            "id": "PLOT-0120",
            "available": 20,
            "text": (
                "0120 shore plot: the submerged transit is estimated in the "
                f"{sector_name} sector of the Miller Line, {side}. "
                "Confidence moderate. This is not a track and it does not "
                "give a latitude."
            ),
        },
        {
            "id": "OPS-0330",
            "available": 150,
            "text": (
                "0330 operations update: the barrier watch still ends at 0700. "
                "No relief station is assigned. A surface crossing does not "
                "close the submerged watch."
            ),
        },
    ]


CINDER_MISSION = {
    "title": "Operation Cinder Road",
    "own_ship": "Kestrel",
    "setting": (
        "Fictional wartime patrol against an eastbound coastal convoy. "
        "This is not an exercise classification and not a barrier watch."
    ),
    "task": (
        "Attack a merchant in the convoy with the wartime heavyweight "
        "before the guide passes east of x=30. The patrol ends at 2100."
    ),
    "chart": (
        "Local grid in nautical miles: east is +x, north is +y. "
        "The convoy lane is the parallel y=0, course 090. "
        "One water depth and one sound-speed profile apply across the lane. "
        "The lane is not a change in bathymetry."
    ),
    "orders": (
        "Offensive employment is authorized. An employ order still names the "
        "round, an existing contact, the cited reports, and confirm. "
        "The launch uses that round's published envelope. "
        "There is no relief station."
    ),
    "intel": (
        "The convoy is eastbound on the lane. The escort's side of the column is not known. "
        "A submarine may be screening ahead of the column. "
        "A ship that holds a detection may fire from that detection. "
        "A shore plot does not give a present position."
    ),
    "radio": (
        "A plot reminder is scheduled for 1630 and an operations reminder for 1830. "
        "A mast receive can copy one once its scheduled time has passed. "
        "A buoyant receive adds that mode's published delivery latency. "
        "A mast transmission can be intercepted by the escort inside the published range. "
        "Sending an assessment does not launch a weapon."
    ),
    "units": "Courses and bearings true; speed in knots; depth in feet; distances in nautical miles.",
    "simplifications": (
        "Merchants hold course and speed until a weapon casualty stops them. "
        "The escort holds station unless it fires or a casualty stops it. "
        "A homing round then runs under the published employment rule. "
        "A mast sighting inside the published visual range can read a name."
    ),
}

MILLER_SCHEDULE = Schedule(
    clock_origin_minutes=60,
    report_due_minutes=None,
    end_minutes=360,
    deadline_text="",
    end_text="0700. Barrier watch complete; the debrief can now be requested.",
    end_reason="0700 barrier watch ended",
    relief=None,
)

MILLER_START = {
    "x": -10.0,
    "y": 0.0,
    "course": 0.0,
    "speed": 5.0,
    "depth": 400.0,
    "operating_mode": "standard",
}

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

def _cinder_navigation(own):
    return (
        f"1600. Local position ({own['x']:g} east, {own['y']:g} north). "
        f"Course {own['course']:03.0f}, speed {own['speed']:g} knots, "
        f"depth {own['depth']:g} feet. "
        "The convoy lane is y=0, eastbound. No relief station is assigned."
    )


def _place_cinder(state, dice, make_entity):
    """Hidden wartime convoy.

    Two merchants hold an eastbound column on the lane. The escort is abeam
    on one side and fires if it holds a detection. A diesel screens ahead on
    battery and fires under the same doctrine.
    """
    guide_x = dice.between("cinder:guide-x", -8, -4)
    guide_y = dice.between("cinder:guide-y", -0.5, 0.5)
    guide = make_entity(
        MERCHANT,
        id="actor-guide",
        x=guide_x,
        y=guide_y,
        course=90.0,
        speed=8.0,
        depth=0,
        aware=False,
        last_heard=None,
        evaded=False,
        name="Hasp",
        doctrine="passage",
        observations=[],
        belief={"assessment": "no_contact"},
        intent="Hold the eastbound lane at the column speed.",
    )
    trailer = make_entity(
        MERCHANT,
        id="actor-trailer",
        x=guide_x - 2.0,
        y=guide_y + dice.between("cinder:trailer-y", -0.4, 0.4),
        course=90.0,
        speed=8.0,
        depth=0,
        aware=False,
        last_heard=None,
        evaded=False,
        name="Lanyard",
        doctrine="passage",
        observations=[],
        belief={"assessment": "no_contact"},
        intent="Follow the guide at the column speed.",
    )
    side = 1.0 if dice.u("cinder:escort-side") < 0.5 else -1.0
    escort = make_entity(
        WARSHIP,
        id="actor-escort",
        x=guide_x + dice.between("cinder:escort-x", -1, 1),
        y=guide_y + side * dice.between("cinder:escort-offset", 5, 6.5),
        course=90.0,
        speed=8.0,
        depth=0,
        aware=False,
        last_heard=None,
        evaded=False,
        name="Brand",
        doctrine="engage",
        observations=[],
        belief={"assessment": "no_contact"},
        intent="Stay abeam of the guide. Fire at a held detection.",
    )
    screen = make_entity(
        DART,
        id="actor-screen",
        x=guide_x + dice.between("cinder:screen-x", 7, 11),
        y=guide_y + dice.between("cinder:screen-y", -1.5, 1.5),
        course=90.0,
        speed=5.0,
        depth=dice.between("cinder:screen-depth", 250, 450),
        aware=False,
        last_heard=None,
        evaded=False,
        name="Mote",
        doctrine="engage",
        observations=[],
        belief={"assessment": "no_contact"},
        intent="Screen ahead of the column. Fire at a held detection.",
    )
    state["actors"].extend((guide, trailer, escort, screen))
    state["convoy"] = {
        "lane_north_nm": 0.0,
        "lane_course_true": 90.0,
        "exit_east_nm": 30.0,
        "guide_id": guide["id"],
        "merchant_ids": [guide["id"], trailer["id"]],
        "escort_id": escort["id"],
        "screen_id": screen["id"],
        "exit_elapsed_minutes": None,
        "closest_merchant_nm": min(
            math.hypot(state["own"]["x"] - merchant["x"], state["own"]["y"] - merchant["y"])
            for merchant in (guide, trailer)
        ),
    }
    state["bulletins"] = [
        {
            "id": "PLOT-1630",
            "available": 30,
            "text": (
                "1630 shore plot: the convoy is still estimated eastbound on "
                "the charted lane. Escort station is not known. This is not a "
                "present-position report."
            ),
        },
        {
            "id": "OPS-1830",
            "available": 150,
            "text": (
                "1830 operations update: the wartime heavyweight remains the "
                "authorized attack on a merchant before the guide passes east "
                "of x=30. The exercise round is not loaded. There is no relief "
                "station."
            ),
        },
    ]


CINDER_SCHEDULE = Schedule(
    clock_origin_minutes=960,
    report_due_minutes=None,
    end_minutes=300,
    deadline_text="",
    end_text="2100. Convoy patrol complete; the debrief can now be requested.",
    end_reason="2100 convoy patrol ended",
    relief=None,
)

CINDER_START = {
    "x": 14.0,
    "y": -11.0,
    "course": 0.0,
    "speed": 5.0,
    "depth": 350.0,
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
    MILLER_LINE: Scenario(
        MILLER_LINE,
        MILLER_MISSION,
        MILLER_SCHEDULE,
        MILLER_START,
        _place_miller,
        _miller_navigation,
        geometry={
            "type": "barrier_line",
            "line_east_nm": 0.0,
            "south_nm": -18.0,
            "north_nm": 18.0,
            "sectors": "North is y>0. South is y<0.",
        },
    ),
    CINDER_ROAD: Scenario(
        CINDER_ROAD,
        CINDER_MISSION,
        CINDER_SCHEDULE,
        CINDER_START,
        _place_cinder,
        _cinder_navigation,
        geometry={
            "type": "convoy",
            "lane_north_nm": 0.0,
            "lane_course_true": 90.0,
            "convoy_exit_east_nm": 30.0,
            "offensive_weapons_authorized": True,
            "employment": "wartime_heavyweight",
        },
        offensive_weapons=True,
        offensive_loadout="wartime_heavyweight",
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
