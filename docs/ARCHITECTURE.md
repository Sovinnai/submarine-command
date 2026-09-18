# Architecture

## Responsibilities

| Component | Owns | May expose to the narrator |
|---|---|---|
| Public scenario/platform | Mission, known equipment, operating limits, applicable orders | All player-known setup and implemented capabilities |
| World state | Actors, physical positions, signatures, equipment condition, environment, seed | Nothing directly |
| Observer models | Received measurements, missed detections, uncertainty and correlation | Dated measurements and their quality |
| Belief/assessment models | Crew estimates and each opposing commander's information | Only the player's assessments and observed opponent behavior |
| Adjudicator | Validated actions, time transitions, event-keyed random draws | Execution receipts and resulting observations |
| Narrator broker | Private session paths, verified loading, persistence and public operation dispatch | Opaque session identifiers and whitelisted operation results |
| Narrator | Natural-language order interpretation, explanations and report presentation | Evidence-grounded prose |
| Debrief | Revealed initial state, rules version, action log and outcome analysis | Full history only after the exercise ends and reveal is requested |

The current implementation combines most engine responsibilities in engine.py.
Immutable entity definitions, observation-derived calculations and process
locking are separate modules. Vessel and biologic definitions use the same
geometry, motion, operating-mode and signature contract. Optional resource,
equipment and inventory components prevent biologics from acquiring meaningless
vessel systems. docs/DESIGN.md defines the intended gameplay and abstraction
contract.

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
not prove the physics realistic or the probabilities calibrated.

The restricted JSON-lines narrator broker exposes only capabilities, session
creation, public status and history, validated actions, verification and a
post-exercise debrief. It has no raw-save, path, session-listing or arbitrary-file
operation. Opaque session identifiers prevent path selection but are bearer
tokens, not an authorization system.

The process boundary becomes enforced isolation only when the narrator cannot
open the broker's storage independently. A trusted host must run the broker
under a separate account, container or equivalent filesystem boundary and grant
the narrator only its request channel. Starting the broker inside an agent that
retains shell access under the same account remains policy-only isolation.

## Known simplifications

- One generic acoustic receiver with automatic track/cross-sensor association.
- Constant settings within five-minute ticks; no turn-rate or acceleration model.
- Simplified range attenuation and layer loss instead of path-dependent propagation.
- Categorical signature evidence rather than numeric narrowband spectra.
- Crew disagreement from different priors applied to shared evidence.
- Descriptive endpoint bearing drift rather than a TMA fit or statistical solution.
- A short nuclear patrol with no modeled reactor endurance constraint; diesel
  battery and snorkeling tradeoffs use explicit fictional rates.
- A single auxiliary fault, a simple opposing reaction and no weapons-resolution model.
- Surface traffic and biologics have bounded operating-state behavior, not full
  navigation, tactical doctrine or ecological simulation.

These are visible limits in the current capability report. Narration cannot
claim systems or observations that the engine does not implement. Development
work and its acceptance criteria are tracked in
[GitHub issues](https://github.com/Sovinnai/submarine-command/issues).
