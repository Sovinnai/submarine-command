# Narrowband spectra

This is the frequency-measurement model for rules version 0.8. It uses the
transmission-loss function in [Acoustic environment](ACOUSTICS.md). The values
are fictional game parameters. The module is `submarine_command.spectra`.

The captain can ask which frequencies were measured. The answer is a stored
spectrum: frequency in Hz, a strong/moderate/weak quality, and a one-sigma
uncertainty. Reading that report again does not draw a new spectrum, add lines,
or create a second piece of evidence.

## Truth and evidence

Each emitter is assigned a signature at initialization:

- tonal families, including integer harmonics
- broadband shapes
- one fractional frequency offset, drawn once
- one level bias in dB, drawn once

Those offsets do not change. Operating mode and speed change which families
are emitted and where a blade-rate family sits. They do not reroll the emitter.

A contact report contains only the measurement. Emitted frequency, family
name, source level and offset stay in the hidden state until the debrief.
Crew assessments use overlapping feature likelihoods. A measured frequency is
not an identity, and no likelihood row belongs to only one contact domain.

Nominal frequencies overlap on purpose. A line near 52 Hz is in both the
fishing-vessel and biologic templates. A line near 60 Hz is in both the quiet
surface-auxiliary and submerged-electric templates. A line near 2.2 kHz is in
both the fishing-gear and biologic-click templates. An individual offset moves
a particular hull slightly, without giving any one frequency a private owner.

## Operating-state rules

These rules are exhaustive. Nothing else turns a line on or off.

- A family or broadband shape is emitted only in the operating modes listed
  for it. An unlisted mode emits neither that family nor a substitute.
- A blade-rate fundamental is the family's hertz-per-knot coefficient times
  speed in knots. Below the family's minimum speed it is absent.
- Harmonic n is n times the fundamental actually emitted, after the persistent
  offset.
- Source level starts from the family's fictional reference. It then adds
  10 log10 of the mode's published relative noise when the family follows mode
  noise, an optional `exponent * log10(speed / reference speed)` term, harmonic
  rolloff of a fixed dB per step, and the emitter's persistent level bias.

Examples of the mode gate: a diesel boat on battery emits the electric-motor
family and does not emit the snorkel-diesel family; snorkeling does the
opposite. A quiet surface patrol emits the auxiliary family and does not emit
the main-engine family. Fishing gear and a biologic moan are mode-gated in the
same way. Own-ship quiet operation drops the pump line. Opposing forces do not
receive that own-ship spectrum; their detection remains the speed-based
broadband model documented in the capability report.

## Measurement

For each emitted line, at the source and receiver depths actually held:

1. Propagate with the shared transmission-loss model at the emitted frequency.
2. Form noise from the shared ambient and self-noise model. If the same
   emitter has broadband covering the line, power-sum that received broadband
   density into the noise.
3. Signal excess is `SL − TL − NL + DI − DT`. Narrowband DT is 4 dB.
   Broadband analysis bands use 8 dB and the same noise level at the band
   center; this rules version does not integrate a noise density across the
   band. Directivity is the hull value, plus the existing focus adjustment.
4. Detection probability is the same logistic used for other reception, with
   its ceiling and with no independent floor.
5. The center frequency, before the processing sample, is the horizontal
   Doppler shift `f (c + v_receiver) / (c + v_source)`. `c` is the true sound
   speed at receiver depth. `v_receiver` is positive toward the source and
   `v_source` is positive away from the receiver. Vertical Doppler is not
   modeled.
6. Reported uncertainty is the root-sum-square of the tone term
   `1 / (T sqrt(2 SNR))`, the root-mean-square of the shared fractional bias,
   and `f * 0.4 kn / c` for unresolved radial motion. SNR here is
   `SL − TL − NL + DI` plus the shared window offset. `T` is 60 seconds on a
   normal listen or an active step, 180 seconds when focus is on that contact,
   and 20 seconds when focus is on something else. The processing sample is a
   uniform draw on [-1, 1] scaled by sqrt(3), so its standard deviation equals
   that tone term.

Broadband is reported in fixed analysis bands, 10–200 Hz, 200–1000 Hz and
1000–5000 Hz, rather than by the emitter's private band edges.

Harmonic relations are inferred from the measured frequencies and their
uncertainties. A detected line may be any harmonic from 1 through 8, so lines
at 2f and 3f imply the missing fundamental. The relation is a statement about
the measurement. When an active pulse and a passive spectrum are both detected
on the same step, the reported reception strength is the louder of the two.

## Correlation

A 20-minute window has one environmental quality offset, applied to every
contact, and one fractional frequency bias of ±0.0015, applied to every line
of every contact. Each line also has one processing-error sample for that
window. A later integration in the window reuses those draws. Signal excess
can still change with range, depth, speed and mode, so a line can cross the
fixed detection draw without becoming a new independent roll.

The crew's belief update keeps one feature per window, taken from the latest
look. A repeated look does not multiply that update. Two assessments of the
same observations are not independent corroboration.

Re-reading status, history or an existing report performs no draw and cannot
add a line.
