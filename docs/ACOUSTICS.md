# Acoustic environment

This is the implemented transmission model for rules version 0.7. It is
idealized physics for a turn-based game. It does not reproduce classified
sonar performance or a research-grade ocean-acoustics solver.

The engine module is `submarine_command.acoustics`. Own-ship reception and
opposing detection call the same function on the same hidden water column.

## What the captain can know

Public status reports a dated onboard estimate: a temperature-derived sound-speed
profile, charted water depth, charted bottom type, mixed-layer and thermocline
estimates, profile age, and which path families the model will evaluate for that
estimate. Adjudication uses the hidden true column. Asking for the environment
again does not create a new measurement.

There is no bathythermograph order in this rules version. Age increases with
elapsed time.

## Units and reference levels

| Quantity | Unit / reference |
|---|---|
| Display depth | feet |
| Display range | nautical miles |
| Internal length | meters |
| Frequency | Hz |
| Sound speed | m/s |
| Transmission loss | dB re 1 m |
| Source level | dB re 1 µPa at 1 m |
| Noise level | dB re 1 µPa |
| Salinity | 35 PSU, uniform |

Internal calculations convert feet and nautical miles explicitly.

## Water column

Sound speed at each sample is Mackenzie (1981) from temperature, 35 PSU and
depth in meters. A summer-like Glass Strait column has a mixed layer, a
thermocline and a pressure-dominated deep gradient. Water depth is drawn once
at initialization and is deep enough for the published hull envelope, typically
too shallow for a conjugate-depth convergence zone.

Bottom types are `mud`, `sand` and `rock` with documented sound-speed ratio,
density ratio and a fictional frequency-dependent sediment loss. Reflection uses
a two-fluid Rayleigh pressure coefficient: density ratio times `sin γ` against
the transmitted vertical slowness `sqrt((c1/c2)² − cos² γ)`. Grazing angle `γ`
is from the horizontal. At normal incidence this is `(Z2 − Z1)/(Z2 + Z1)`.

The onboard estimate is the same kind of column with temperature and layer-depth
error, a rounded charted depth, and a charted bottom that is usually but not
always the true type.

## Path approximations

Each reception evaluates five paths and uses the lowest-loss path that
contributes. A path is `supported`, `uncertain`, or `outside_scope`. Uncertain
paths still supply a loss; outside-scope paths do not.

**Direct.** Geometric slant range, spherical spreading `20 log10(r/1 m)` and
Thorp absorption. A thermocline crossing adds a range-growing extra loss. At
long range that path is marked uncertain (an estimated shadow), not silently
treated as a clear detection.

**Surface duct.** Requires a mixed layer at least 10 m thick with at least
2 m/s of sound-speed drop into the thermocline, and a frequency at or above an
idealized cutoff that falls as the layer thickens or the contrast grows. Both
depths in the layer: cylindrical spreading after a transition equal to the layer
depth. One depth below: leaky coupling whose path length includes the
out-of-layer vertical, so a leak cannot beat the geometric slant at near-zero
horizontal range.

**Half-channel.** Only when sound speed increases through the whole column.
Cylindrical spreading with water depth as the channel height. A summer
thermocline is outside this approximation's scope.

**Bottom bounce.** One image: path length `sqrt(R² + (2H − zs − zr)²)` plus
Rayleigh interface loss and sediment loss. Very low grazing angles are
uncertain because roughness is not modeled. Multiple bounces are not summed.

**First convergence zone.** Only when a deep sound-channel axis exists, both
depths are above that axis, and a conjugate depth on the deep branch exists for
each sound speed. A match on the same deep branch, including a source already
below the axis, is not a first CZ. Range is the constant-gradient turning
identity `R = sqrt(2 c Δz / g)` from each depth to its conjugate, summed.
Inside a fractional annulus the path is supported with a modest focusing
credit; the edge is uncertain; other ranges, deep sources and shallow columns
are outside scope. A turning range shorter than 15 nautical miles is not treated
as a first CZ. The public column report asks whether any patrol-relevant depth
above the axis has such a conjugate, not a single 80-foot probe.

Thorp (1967) absorption is converted from dB/kyd to dB/km by 1.0936. The
formula is used below 50 kHz, which covers the game's representative bands.

## Detection

Passive signal excess is `SL − TL − NL + DI − DT`. Active is two-way TL plus a
fictional target strength. Detection probability is the logistic of that excess
with a published ceiling and no independent floor, so inaudible geometry does
not still produce a contact. Source levels and thresholds are published
fictional game parameters. Representative frequencies are 120 Hz submerged,
80 Hz surface, 2500 Hz biologic and 3500 Hz active, until numeric spectra exist.

Receiver depth is the platform's present keel depth. Changing depth changes
loss through this same environment. A towed array is inventory only.

Opposing detection uses the same TL function. Own-ship source level still
follows speed rather than operating mode.

A 20-minute acoustic window applies one correlated quality draw to every
contact. Repeated looks inside that window are not independent evidence.

## Selected limiting cases the tests enforce

- Mackenzie sound speed increases with temperature and with depth at fixed
  temperature.
- Thorp absorption at 10 kHz exceeds absorption at 100 Hz, matching the 1967
  formula after the kyd-to-km conversion.
- Spherical spreading is 60 dB at 1 km and 80 dB at 10 km.
- Ducted loss grows more slowly with range than spherical loss.
- Crossing a thermocline raises direct-path loss relative to same-side geometry
  at equal range.
- Rock bottom bounce loses less than mud at the same grazing geometry.
- Glass Strait water has no conjugate-depth CZ; a 4000 m test column does, near
  the turning range, for depths above the axis.
- A source or receiver below the sound-channel axis is not given a first-CZ path.
- Public CZ scope follows whether any patrol-relevant depth above the axis has
  a conjugate, not a single 80-foot probe.
- Leaky duct coupling includes the out-of-layer vertical and does not beat the
  geometric slant at near-zero horizontal range.
- A winter isothermal column supports half-channel spreading; a summer
  thermocline does not.
- Transmission loss is reciprocal in source and receiver depth.
- Public status exposes the measured profile, never the true column.
