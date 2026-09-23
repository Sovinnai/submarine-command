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

In rules version 0.9, a command window is 5–60 minutes in five-minute increments.
Five minutes is the resolution at which the world reports and at which every
random draw is keyed. Events are therefore located at step boundaries. Opposing
decisions use start-of-step information, then every platform moves over the same
elapsed step. No side receives a frozen movement interval.

Own-ship motion is integrated in one-minute increments inside each step so that
an ordered maneuver crossing an operating envelope is resolved at the crossing
rather than at the endpoint. That integration is deterministic: no random draw
occurs inside it, which keeps every draw keyed to one world event at one time.

An order states commanded course, speed, depth and operating mode. Those become
the standing commanded settings at the beginning of the first step and persist
until a later order replaces them, but they are not achieved instantly. Actual
values transit toward them at published fictional rates, and the two are always
reported separately. Depth rate is proportional to actual speed, because a
submarine changes depth with planes: coming shallow quickly also means going
fast, and therefore being loud. A commanded operating mode takes effect only
once actual speed and depth satisfy that mode's limits.

An activity waits on achieved settings rather than ordered ones. Mast and link
cycles need the whole five-minute step inside the mast envelope; repair credits
only the minutes actually spent within its speed limit. An interrupt returns
control without altering the commanded settings, and reports what was achieved
against what was ordered.

The engine supplies no default for any order field. An unstated duration, depth,
course, speed, plant lineup or interrupt policy is a rejected order, not an
assumed one: the engine must never choose a setting the captain did not state.

Opposing entities are not rate-limited in this rules version. Their settings
still change only at five-minute boundaries, and the capability report says so.
Acoustic integration runs during every complete step, concurrently with movement
and the selected equipment activity, and reports at its endpoint. An active pulse
occupies the first integration step. Mast observation and each link attempt are
five-minute deployment, operation and recovery cycles. A usable receive link or
acknowledged transmission completes the communications task. Repair requires
twenty productive minutes within its operating envelope and retains progress
between command windows.

At each boundary, exercise completion, task completion and selected interrupts
return control. The execution receipt gives requested, actual and unused minutes
and identifies every stopping event by public report ID. Remaining time is
discarded, never silently continued. Reading status, history, capabilities or an
existing report is outside simulated time and performs no random draw.

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

## Target motion and crew assessments

Rules version 0.9 estimates contact motion from recorded bearings, their
timestamps and provenance, and the own-ship positions stored with those
bearings. The fit assumes constant course and speed on a published grid.
Acoustic bearings share one bias. Measurements that share an evidence window
are not independent looks. An active range, when one was reported, constrains
range by the published measurement factor. Every grid point that meets the
error model stays in the family. The fit does not read hidden contact course,
speed or position.

Observed bearing drift remains a separate description of the measured bearing
change over at most 30 minutes. A bearing change that own-ship motion and the
stated measurement error can carry is not reported as a confirmed target
maneuver. When no constant-motion solution fits, a maneuver is one competing
explanation beside measurement error and contact misassociation.

Classification multiplies each role's prior by the overlapping spectral
feature recorded for each evidence window. The feature comes from that
window's latest measured spectrum. The assessment cites the reports and the
measured lines, uncertainties and qualities. Both roles cite that same set.
A report at least 20 minutes old is marked stale and remains in the history.
A visual observation replaces the acoustic update for both roles.

## What makes a good session

A good session produces consequential choices about information, time, mission
and risk. Several decisions may be reasonable on the available evidence. Quiet
intervals and unresolved contacts are legitimate outcomes. The debrief explains
what happened and distinguishes decision quality from luck and modeling errors.

The physical detail serves those choices. Abstraction is useful when it keeps
play manageable without destroying the causal connection between orders and
outcomes. A system's modeled limitations must be visible in the capability
report rather than concealed behind a confident narrative.
