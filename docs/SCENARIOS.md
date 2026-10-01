# Scenarios

A scenario is a public brief plus the hidden layout drawn when the session is
created. Both published scenarios use rules version 0.12. The same order
validation, movement, acoustic measurement, communications and replay rules
apply. The capability report lists the public brief, clock, relief station and
own-ship start for each scenario. It does not list the opposing draw.

```bash
python -m submarine_command capabilities
python -m submarine_command --session .sessions/my-patrol init
python -m submarine_command --session .sessions/harrow-bank init --scenario harrow-bank
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

## Operation Harrow Bank

Fisheries surveillance on the Harrow Bank. Kestrel starts at (8 east, -6 north)
at 0210, on course 000 at 4 knots and 400 feet, south of the bank rectangle
x=0 to 16, y=0 to 12. The question is whether submerged traffic is on the bank.
An evidence-based assessment is due at 0630. At 0700, Kestrel is expected within
3 nautical miles of station SOUTHING (2 east, -12 north).

SOUTHING is behind the opening position. From the start, the minimum speed that
meets the station is low. Going north onto the bank spends the time required to
come back south. The navigation report states the current distance and the speed
required for the time remaining.

The chart uses one water depth and one sound-speed profile everywhere. The bank
is an operating area, not a change in bathymetry or propagation.

### Hidden layout

One of three layouts is drawn. The weights are the fictional parameters in
`submarine_command/scenarios.py`. The brief does not say which layout was drawn.

- A diesel submarine on the bank, two fishing vessels, an eastbound merchant
  and a westbound surface combatant. The diesel starts on battery. Its initial
  charge is drawn from a low band so that, at the drawn speed, the published
  battery drain reaches the snorkeling rule before 0630. Snorkeling sets depth
  to 50 feet, limits speed to that mode, and changes the emitted spectrum.
  The initial course is generally north, so the boat is leaving the bank while
  the charge runs down.
- No submarine. A biologic group is the primary contact, with the same fishing,
  merchant and combatant traffic.
- No submarine and no biologic group. The primary contact is a fishing vessel,
  with a second fishing vessel, the merchant and the combatant.

The first actor is the subject of the debrief's presence score.
`submerged_present` matches only when that actor is submerged.
`surface_or_biologic` matches when it is not. `unresolved` is not scored as
either. Accuracy is not a judgment of whether the cited reports supported the
call.

A 0310 shore bulletin agrees with that presence question on the same shore
probability Glass Strait uses, and otherwise states the opposite. It does not
name a track. A 0510 bulletin restates the deadline and the station. Both are
dated reports, available through the published receive modes, not new sensor
observations.

The surface combatant starts more than the published 15-nautical-mile
mast-intercept range from Kestrel's opening position, and inside that range of
the northern bank (8 east, 6 north). It holds its initial course, so the range
changes as it moves. An intercept is recorded. The combatant does not turn
because of it.

### Decisions the layout supports

Staying south preserves the station and leaves the bank's quiet traffic to the
hull and flank at opening range. Closing north, or streaming the towed array
while closing, spends time and can raise own-ship speed. A submerged opponent
that detects Kestrel evades from its own noisy fix; the evasion draw is then
limited by the mode already in force. Waiting can produce a snorkeling spectrum
on a diesel that is present, and it can also let that contact draw north while
the station stays south. A mast look does not turn a snorkel into a visual
surface contact. A mast transmission from the northern bank can fall inside the
combatant's intercept range when a transmission from the opening position does
not.

Fishing vessels still change between transit and fishing. A biologic group
still changes course, speed, depth and vocalization. Those cycles are
alternative explanations for a changing sound. They are not identification.

## Debrief

The debrief remains unavailable until the exercise has ended. It then reports
whether an assessment was sent by the scenario deadline, whether Kestrel was
inside the relief radius at the scenario end time, whether a submerged opponent
detected Kestrel, and any recorded radio intercepts. Glass Strait also keeps
the `at_relief_at_0800` field. Harrow Bank reports the station check as
`at_relief_station` for SOUTHING at 0700.
