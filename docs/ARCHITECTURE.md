# Architecture

## Responsibilities

| Component | Owns | May expose to the narrator |
|---|---|---|
| Public scenario/platform | Mission, known equipment, operating limits, applicable orders | All player-known setup and implemented capabilities |
| World state | Actors, physical positions, signatures, equipment condition, environment, seed | Nothing directly |
| Observer models | Received measurements, missed detections, uncertainty and correlation | Dated measurements and their quality |
| Belief/assessment models | Crew estimates and each opposing commander's information | Only the player's assessments and observed opponent behavior |
| Adjudicator | Validated actions, time transitions, event-keyed random draws | Execution receipts and resulting observations |
| Narrator | Natural-language order interpretation, explanations and report presentation | Evidence-grounded prose |
| Debrief | Revealed initial state, rules version, action log and outcome analysis | Full history only after the exercise ends and reveal is requested |

The current implementation combines most engine responsibilities in engine.py.
Platforms, observation-derived calculations and process locking are separate
modules. docs/DESIGN.md defines the intended gameplay and abstraction contract.

## Randomness

A newly initialized game obtains 256 bits of secret entropy. HMAC-SHA256 derives
event-specific draws from that seed and labels containing actor, time and event
type. Asking a question does not invoke a sensor model or change a counter.
Acoustic and radio conditions persist inside defined windows; repeated attempts
in a window do not become independent evidence merely through repetition.

The initial commitment binds the seed, initial world and all package Python
source files. Each action receipt binds its order, execution, previous receipt
and resulting state hash. Verification rebuilds the initial world and replays
the action transcript, including cached public outputs.

This makes rewriting detectable against a previously published receipt. It does
not prove the physics realistic, the probabilities calibrated, or the narrator
incapable of opening a file. Enforced isolation requires a process/service that
exposes only allowed commands and has no raw-save endpoint during play.

## Known simplifications

- One generic acoustic receiver with automatic track/cross-sensor association.
- Constant settings within five-minute ticks; no turn-rate or acceleration model.
- Simplified range attenuation and layer loss instead of path-dependent propagation.
- Categorical signature evidence rather than numeric narrowband spectra.
- Crew disagreement from different priors applied to shared evidence.
- Descriptive endpoint bearing drift rather than a TMA fit or statistical solution.
- A short nuclear patrol with no modeled energy endurance constraint.
- A single auxiliary fault, a simple opposing reaction and no weapons-resolution model.

These are visible limits in the current capability report. Narration cannot
claim systems or observations that the engine does not implement. Development
work and its acceptance criteria are tracked in
[GitHub issues](https://github.com/Sovinnai/submarine-command/issues).
