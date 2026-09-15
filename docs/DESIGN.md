# Game design

## Format

Submarine Command combines miniature-wargame command decisions with a computer's
ability to maintain hidden state and resolve physical relationships. Play is
turn-based. A turn represents an explicit period of simulated time, not a race
against a wall clock. The captain can think, consult existing reports and issue
orders without a reaction-time penalty.

This is the design contract. The README and capability report describe what the
current prototype implements; GitHub issues track implementation work.

## Command and resolution

The captain chooses intent, actions, a duration and interrupt conditions. All
entities advance through that interval on the same simulated clock. Opposing
forces do not freeze while the player moves. Meaningful observations or events
can interrupt the interval and return control to the captain.

The computer can use small internal integration steps while the player makes
larger command decisions. Five-minute ticks are the current prototype's rule;
tick size is a modeling choice rather than the essential identity of the game.
Duration, geometry, operating state and environmental conditions affect outcomes.

The interface presents what the boat can know: own-ship status, observed
bearings and frequency lines, uncertain classifications, messages and mission
constraints. A tactical display depicts observations and assessed uncertainty.
Truth appears separately in the after-action review.

## Idealized physical quantities

| Subject | Quantities that give decisions meaning | Permitted abstraction |
|---|---|---|
| Motion | Position, course, speed, depth and elapsed time | Discrete integration and bounded maneuver rules |
| Signatures | Persistent emitted frequencies, harmonics, broadband noise and operating-state effects | Fictional overlapping signature families rather than a real intelligence database |
| Propagation | Frequency-dependent transmission loss, an SSP, source/receiver depths, water depth and bottom effects | Documented bands, path approximations or lookup tables that preserve relevant relationships |
| Sensors | Array position/depth, directional coverage, noise, integration time and measurement uncertainty | Idealized beam patterns, signal-to-noise thresholds and receiver models |
| Classification | Measured lines and bearings, their persistence, uncertainty, correlation and prior information | Small explicit belief models, with uncertainty retained |
| Communications | Distinct reception/transmission modes, antenna state, restrictions and latency | Turn-resolved links with persistent channel conditions |
| Resources | Known inventory, equipment state and time spent on tasks | Bounded resource tracks and abstract effect resolution |

Use Hz and documented dB references for acoustics. Show depth in feet, speed in
knots and navigation distance in nautical miles. Internal calculations may use
SI with explicit conversions. Values can be fictional or idealized while still
representing real categories of measurement.

An abstraction must preserve the relationship relevant to a decision. A changed
depth or array state cannot matter only because narration says it matters.
Likewise, a frequency label cannot change between turns merely to support a
new interpretation. Capabilities and environmental estimates are established
before they are needed for adjudication.

## Uncertainty and randomness

The world has a definite history. The captain receives imperfect evidence about
it. Contact existence and identity remain persistent; sensor observations may
miss, distort or ambiguously represent what is there.

Random draws resolve uncertainties conditional on the modeled situation. They
do not replace every physical relationship with an independent coin flip.
Shared environmental or sensor errors create correlated reports. Reviewing the
same measurement or hearing two people interpret it does not add independent
evidence. All random outcomes can be replayed from the committed seed and orders.

The narrator interprets orders and presents evidence. It does not invent actual
contact characteristics, silently choose favorable rolls, or add an enemy to
compensate for an unexpectedly successful decision.

## What makes a good session

A good session produces consequential choices about information, time, mission
and risk. Several decisions may be reasonable on the available evidence. Quiet
intervals and unresolved contacts are legitimate outcomes. The debrief explains
what happened and distinguishes decision quality from luck and modeling errors.

The physical detail serves those choices. Abstraction is useful when it keeps
play manageable without destroying the causal connection between orders and
outcomes. A system's modeled limitations must be visible in the capability
report rather than concealed behind a confident narrative.
