# Acoustic model

This records what the propagation model computes, which published relation each
part comes from, where it stops being valid, and which numbers are invented. It
describes current behaviour; priorities and outstanding work live in GitHub
issues.

The model is idealized physics for a playable turn-based game. It is not a
research propagation code and it does not reproduce any real platform's
performance. Every level, gain and threshold below is a fictional game
parameter chosen so that the scenario plays as intended.

## Units and reference levels

Internal computation is SI: metres, hertz, seconds, decibels. Reports convert to
feet, knots and nautical miles at the engine boundary.

| Quantity | Symbol | Reference |
|---|---|---|
| Source level | SL | dB re 1 micropascal at 1 m, in a band's analysis bandwidth |
| Noise level | NL | dB re 1 micropascal, in the same analysis bandwidth |
| Transmission loss | TL | dB relative to 1 m |
| Array gain | DI | dB |
| Detection threshold | DT | dB of signal-to-noise ratio required at the array |
| Target strength | TS | dB |

Noise level and detection threshold are both stated on the analysis bandwidth,
never mixed between spectrum level and band level. A source level and a noise
level in this package can always be subtracted directly.

    Passive   SE = SL - TL - (NL - DI) - DT
    Active    SE = SL - 2*TL + TS - (NL - DI) - DT

Signal excess is the only quantity any detection decision is made from. Own-ship
reception, the opposition's reception of own ship and the active pulse all call
the same function against the same environment.

## Bands

Six processing bands, each with a centre frequency used for propagation and an
analysis bandwidth used for noise and threshold. The analysis bandwidth is
narrower than the band: the low bands represent a narrowband line search, the
high bands a broader one.

| Band | Range (Hz) | Centre (Hz) | Analysis bandwidth (Hz) |
|---|---|---|---|
| b035 | 20–60 | 35 | 1 |
| b104 | 60–180 | 104 | 1 |
| b300 | 180–500 | 300 | 3 |
| b866 | 500–1500 | 866 | 10 |
| b2739 | 1500–5000 | 2739 | 50 |
| b7746 | 5000–12000 | 7746 | 200 |

A contact is scored in every band and detected on the best one; the reported
band is part of the observation. Numeric line frequencies are not modelled, so a
band is as fine as a frequency report gets.

## Environment

A sound-speed profile is four piecewise-linear control points: surface, sonic
layer depth, channel axis and bottom. Propagation never traces a ray through it.
It reads the features closed-form relations need — layer depth, the gradient on
each side, the channel axis, the critical depth.

Charted water depth comes from a coarse bathymetry grid, bilinearly
interpolated. Bottom class is one of `sand`, `fine_sediment` or `rock`, each
with a reflection-loss table against grazing angle. Sea state and shipping
density set the ambient field.

Conditions are sampled **at the path midpoint** and held constant along the
path. Where the passage genuinely changes between the two platforms, this is an
approximation and not a range-dependent solution.

### True conditions against the boat's estimate

The scenario draws the true field once at initialization. The layer deepens
eastward along the passage and oscillates slowly with time; nothing about it is
redrawn during play, so a stale estimate drifts from truth deterministically.

Adjudication always uses truth. Everything published to the captain is predicted
from the onboard profile estimate, which carries its origin, the time and place
it was taken, and its uncertainty. The patrol opens with a forecast, not an
observation. A `sound_profile` order spends one five-minute step to replace it
with a measurement — close to truth, not equal to it — at that position and
time. Running twenty miles east afterwards makes it wrong again, and the
published age and distance-run say so.

## Paths

Each candidate path returns a loss and a status: `supported` when the relation
applies and its preconditions hold, `uncertain` when the approximation is being
stretched, `out_of_scope` when the geometry or the model does not support it.
Paths that carry energy are summed on an intensity basis. Beyond 60 nm every
path is out of scope and the model declines to answer rather than extrapolating.

### direct

Refracted, boundary-free. Spherical spreading plus absorption over the slant
range.

Its availability is set by ray curvature. In a linear sound-speed gradient rays
are circular arcs of radius `c/|g|`. Above the layer, sound speed increases
downward and rays bend upward, so the deepest a boundary-free ray turns is the
layer; below the layer, sound speed decreases downward and rays bend downward,
so the shallowest turn is again the layer. The layer is therefore the common
vertex, and the horizontal reach from a platform to it is the small-angle arc
`sqrt(2 R dz)`. The limiting range is the sum of the two platforms' arcs.

This is where the layer gets its teeth. Crossing it is not a multiplier; it is a
different limiting range, computed from both depths and both gradients, and
beyond that range the direct path simply is not there.

*Valid*: small grazing angles, which is the regime that matters here.
*Not valid*: steep rays, where the small-angle arc understates the range.

### surface_duct

Trapped above the layer. Requires both platforms above the layer depth and a
frequency at or above the duct's cutoff.

Cutoff uses Urick's approximation for the longest trapped wavelength in a mixed
layer, `f = 1500 / (0.008 H^1.5)` with `H` in metres: a thicker duct traps lower
frequencies. A 100 m duct cuts off near 190 Hz, which is the published ballpark.

Loss is spherical to a transition range `sqrt(2 R H)`, cylindrical beyond it,
plus absorption and a leakage term in dB per kilometre scaled by `sqrt(f_c/f)`,
so trapping is leakiest just above cutoff. Within 1.4 times the cutoff the path
is reported `uncertain`.

*Invented*: the leakage rate, the transition range's constant and the
uncertainty band around cutoff.

Surface-reflected paths that are not trapped are not modelled separately; they
are folded into this path and into the shadow term.

### shadow_zone

Beyond the direct path's limiting range, spherical spreading plus an excess loss
ramping exponentially to a frequency-dependent plateau. The plateau sits in the
10–20 dB region the literature quotes for duct leakage and shadow-zone
attenuation, and is larger at high frequency because low frequencies leak in
more readily.

This is a **fitted curve, not a diffraction solution**, and is always reported
`uncertain`. It exists so that reception beyond the refracted limit is weak
rather than absent — a decision is always made on a number, never on silence.

*Invented*: the plateau, its frequency slope and the ramp length.

### bottom_bounce

A single bottom reflection by straight-ray image source: the image of the source
sits at `2H - z`, the path length is `hypot(r, 2H - z_a - z_b)`, and the grazing
angle follows from that geometry. Loss is spherical over the slant path, plus
absorption, plus a table lookup of reflection loss against grazing angle for the
bottom class.

The reflection-loss tables are shaped to the standard picture: a fast bottom
(sand, rock) reflects shallow-angle energy well and absorbs steep energy, with
loss rising past the critical angle; a slow, fine-grained bottom loses energy at
every angle.

**This is the model's principal known bias.** Refraction is not applied to the
slant path, so in a strongly refracting profile the true ray reaches the bottom
sooner and steeper than the straight-ray geometry says. The path is therefore
reported `supported` only above 20 degrees grazing, `uncertain` between 2 and 20
degrees, and `out_of_scope` below 2 degrees, where refraction governs the
geometry outright. Multiple bounces are not modelled.

### convergence_zone

Gated on depth excess: the profile must have a critical depth — a depth below
the channel axis at which sound speed returns to its near-surface maximum — and
the water must be deeper than that by a margin. Without it the path reports
`out_of_scope` and says why.

The Lydian Passage has no depth excess, so the game never claims a convergence
zone. Where one exists, the implementation is a range-keyed annulus at 33 nm
spacing, 8 per cent width, with a fixed 10 dB focusing gain — within the
published ranges for deep water. It is a bookkeeping gate, not a computed
caustic, and is never better than `uncertain`.

## Absorption

Thorp's expression in dB per kiloyard, `f` in kilohertz:

    0.1 f^2/(1+f^2) + 40 f^2/(4100+f^2) + 2.75e-4 f^2 + 0.003

Converted to dB per metre. Published for roughly 100 Hz upward; below that it
understates absorption, which is harmless because absorption is negligible
against spreading at those frequencies and ranges. Over twenty nautical miles it
costs under half a decibel at 35 Hz and tens of decibels at 7.7 kHz, which is
what limits the active pulse.

## Noise

**Ambient** is Wenz-shaped: a shipping table dominant below a few hundred hertz
and a wind table dominant above it, combined on an intensity basis, shifted by
shipping density and sea state, then raised to the analysis bandwidth. A
receiver inside the surface duct also hears the duct's trapped shipping noise,
so its noise floor is higher by a per-band increment. That is the real cost of
coming above the layer to work the surface picture.

**Self noise** is a per-band level quoted at the receiver's published quiet
speed, plus a flow-noise rise of 30 dB per decade of speed above it, plus any
equipment-fault penalty. Ambient and self noise combine on an intensity basis.

**Radiated noise** is the hull's signature by band, shifted by the operating
mode's offset and per-band shape, plus 35 dB per decade of speed above the speed
the signature is quoted at, plus a 12 dB step once the propeller cavitates.
Cavitation inception rises linearly with depth, so depth buys speed — a
fictional linear rule standing in for the real pressure relation.

*Invented*: every table entry, both slopes, the cavitation rule and the in-duct
increment.

## Detection

Detection threshold is the energy-detector relation `5 log10(w/t)` plus a
per-receiver detection index, so longer integration and a narrower analysis
bandwidth each lower the threshold by 5 dB per decade. Integration time is the
five-minute step.

Detection probability is a cumulative normal in signal excess: even odds at
SE = 0, with an 8 dB spread absorbing everything the model does not resolve,
bounded to [0.015, 0.97] so that neither certainty nor impossibility is ever
reported.

One environmental fluctuation of up to ±3 dB is drawn per twenty-minute acoustic
window and applied to every contact in it. Reports made in the same conditions
therefore carry a common error and are not independent evidence.

## Determinism

Replay binds the saved world to these numbers, and transcendental functions can
differ in their last bit between platforms and library versions. Every value
that reaches the saved world, a public report or a comparison against a random
draw is rounded first: losses and levels to three decimals, probabilities to
six.

## What is not modelled

- Any ray, normal-mode or parabolic-equation solution of the profile.
- Range-dependent conditions within a single path.
- Refraction of the bottom-bounce slant path, and multiple bottom bounces.
- Numeric narrowband lines, harmonics and measured frequencies.
- Separate arrays, beam patterns, baffles and towed-array geometry.
- Reverberation, surface scattering, internal waves and biologic chorus
  structure.
- Doppler, and any effect of relative motion on a measured frequency.

## Checking it

`tests/test_acoustics.py` validates in three tiers: closed-form results
(spreading laws, reciprocity, published absorption values, the cutoff relation)
to a tight tolerance; ordinal relationships that must hold whatever the tuning;
and quantities quoted as ranges in the literature asserted to fall inside those
ranges. A straight-ray, single-bounce model cannot reproduce a full-wave result
and nothing there pretends otherwise.

`scripts/acoustic_sweep.py` prints detection probability against range, own
depth and speed for each entity in the catalogue, against a disposable world
built from a published test seed. It is the calibration aid used to choose the
fictional levels above; it saves nothing and touches no live session.
