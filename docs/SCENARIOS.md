# Scenarios

A scenario is a public brief plus the hidden layout drawn when the session is
created. Both published scenarios use rules version 0.12. The same order
validation, movement, acoustic measurement, communications and replay rules
apply. The capability report lists each public brief, clock and own-ship start.
Glass Strait also lists its relief station. Miller Line lists the barrier line.
The report does not list the opposing draw.

```bash
python -m submarine_command capabilities
python -m submarine_command --session .sessions/my-patrol init
python -m submarine_command --session .sessions/miller-line init --scenario miller-line
```

The narrator `start` operation accepts the same scenario id. Omitting it starts
Glass Strait. The first start for an idempotency key keeps that world.

## Operation Glass Strait

Ceasefire verification in the Lydian Passage. Kestrel starts at (0 east, 0 north)
at 0310, on course 090 at 5 knots and 400 feet. The question is whether
submerged traffic is using the patrol corridor. An evidence-based assessment is
due at 0730. At 0800, Kestrel is expected within 3 nautical miles of station
RELIEF (24 east, 0 north).

The hidden primary contact is a merchant, a diesel submarine, or a biologic
group, placed in the corridor with eastbound motion. Three westbound background
contacts are always present: a merchant, a fishing vessel and a surface
combatant. A 0330 shore bulletin agrees with the primary domain on the
probability stored in the scenario module and otherwise names a different
domain. The selection weights are those module parameters. They are not part of
the brief.

## Operation Miller Line

A barrier watch, not a presence assessment and not a transit to a station.
Kestrel starts at 0100 at (-10 east, 0 north), course 000, 5 knots, 400 feet.
The Miller Line is the meridian x=0 from y=-18 to y=+18, ten nautical miles
to the east. North sector is y>0. South sector is y<0. The watch ends at 0700
wherever the boat is. Nothing in the brief assigns a relief station.

The assigned result is an acoustic detection of the submerged boat at or before
the minute it crosses the line. A surface ship crossing the line does not
finish the watch. A detection made only after the crossing, a mast sighting,
and a transmitted presence assessment are not that result. Kestrel may cross to
the east side; the debrief records that it left the assigned side. Weapons
employment is not authorized.

The chart uses one water depth and one sound-speed profile on both sides of
the line. The line is not a change in bathymetry or propagation.

### Hidden layout

The weights and ranges are the fictional parameters in
`submarine_command/scenarios.py`. The brief does not contain the crossing
latitude.

- A diesel submarine starts east of the line, at least 6 nautical miles north
  or south of the center, on a westbound course. If it keeps that course and
  speed it crosses during the watch, and it crosses later than the merchant.
  It does not snorkel on this charge. If it detects Kestrel it evades once
  from its own fix, which can spoil the crossing.
- A merchant starts closer to the line, in the opposite sector, and crosses
  first. It is a real ship, not a false plot.
- A biologic group starts near the center of the line and keeps its published
  vocalization cycle.
- A surface combatant patrols north or south on the east side and does not
  cross the line. A mast transmission can be intercepted. The combatant does
  not turn because of the intercept.

A 0120 shore plot names the north or south sector. It is right on the
probability in the scenario module and otherwise names the opposite sector.
The text does not give a latitude. A 0330 bulletin only restates that the
watch ends at 0700 and that a surface crossing does not close it. Both are
dated reports, copied through the published receive modes.

### Decisions the layout supports

The line is longer than one quiet detection range, and the submerged crossing
is never at the center. Staying at the center hears the middle and can miss
the sector the boat actually uses. Sliding north or south commits the barrier
to one sector before the crossing. The merchant provides an early, loud
crossing in the other sector. The shore plot can agree with that ship or with
the submarine, and it arrives only after its scheduled time plus the latency
of the receive mode used to copy it. Streaming the towed array improves the
listen and makes a turn along the line unstable for the published settling
time. Going east of the line closes the range and leaves the assigned side.
Active sonar and high speed can be heard by the crossing boat. A mast look
does not show the deep boat.

## Debrief

The debrief remains unavailable until the exercise has ended. Glass Strait
reports whether an assessment was sent by the deadline, whether Kestrel was
inside the relief radius at 0800, whether a submerged opponent detected
Kestrel, and any recorded radio intercepts. Miller Line reports whether the
submerged boat crossed, the crossing minute, whether an acoustic observation
of that boat exists at or before that minute, whether the only observations
are later, whether Kestrel went east of the line, and whether the crossing
boat detected Kestrel.
