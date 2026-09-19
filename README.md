# Submarine Command

A turn-based submarine command game combining miniature-wargame decisions with
computer-resolved movement and idealized acoustic measurements. Python owns the
hidden world; an LLM can interpret orders and present the watch team's available
evidence. The game never runs against the player's wall clock.

The design uses real kinds of quantities and relationships: frequencies,
harmonics, noise, sound transmission, array geometry and target motion. Resolution
may be abstracted into turns, bands or tables, but measurements have persistent
causes and the captain's decisions must change the world consistently.

**Status: early prototype, version 0.6.** The hidden-state and replay foundation
works, and acoustic reception is resolved through the sonar equation against a
modelled ocean. Measurement fidelity above that — narrowband lines, separate
arrays, target motion — still needs substantial development. Unsupported systems
are explicitly identified in the capability report, and
[docs/ACOUSTICS.md](docs/ACOUSTICS.md) records what the propagation model does
and does not compute.

## Run it

Python 3.11 or newer. No runtime dependencies or paid API access are required.
From a checkout:

```bash
python -m submarine_command capabilities
python -m unittest discover -s tests -v
python -m submarine_command --session .sessions/my-patrol init
python -m submarine_command --session .sessions/my-patrol status
```

`init` generates a new secret seed and starting world. It refuses to replace an
existing session. `status`, `history`, `capabilities`, and `verify` are read-only;
they do not advance time or generate fresh sensor observations.

An editable install also supplies a `submarine-command` executable:

```bash
python -m pip install -e .
submarine-command capabilities
```

## Restricted narrator interface

A model-independent JSON-lines broker exposes public game operations without
giving the narrator a save path. A trusted host can start it with private storage
outside the narrator's filesystem:

```bash
submarine-command-narrator --storage-root /var/lib/submarine-command
```

The broker accepts `capabilities`, `start`, `status`, `history`, `act`, `verify`,
and post-exercise `debrief` requests on standard input and writes one response
per line on standard output. Sessions are addressed by opaque bearer tokens.
There is no session-listing, raw-state, arbitrary-file, or in-play debrief
operation. Narrator orders must include both a unique order ID and the current
`expected_turn`. Session creation requires a trusted-host-generated secret
idempotency key so a lost response can be retried without rerolling the world.

The broker is the application boundary, not an operating-system sandbox. A
narrator that shares its service account or retains unrestricted shell and
filesystem tools could still bypass it. For enforced blindness, run the broker
under a dedicated account or container and give the narrator only the broker's
request channel. See [Narrator protocol](docs/NARRATOR_PROTOCOL.md).

The default session directory is `.sessions/glass-strait` beneath the current
working directory. Live sessions are ignored by Git. No live campaign is shipped
in this repository. The earlier paused chat patrol retains its original engine
and save separately; this project does not alter or implicitly migrate it.

## Execute an order

Write a JSON file outside the tracked source, for example `.sessions/order.json`:

```json
{
  "id": "order-001",
  "expected_turn": 0,
  "activity": "focus",
  "focus": "S01",
  "minutes": 20,
  "interrupt_on": ["new_contact", "classification_change", "equipment", "contact_lost"]
}
```

```bash
python -m submarine_command --session .sessions/my-patrol act --order-file .sessions/order.json
python -m submarine_command --session .sessions/my-patrol verify
```

Supported activities are `listen`, `focus`, `active`, `mast`, `receive`,
`transmit`, `repair`, `sound_profile`, and `end`. Except for `end`, an order may also include
`course`, `speed`, `depth`, and a published `operating_mode`. Mode-specific and
platform-wide envelopes are both validated. Orders run for 5–60 minutes in
five-minute steps, with interruption on the selected events. Every result's
`last_execution` states the requested, elapsed, and unused minutes; its stop
reason and public report IDs explain why control returned. The unused portion is
never resumed automatically.

The engine uses one shared clock. At each step, opposing decisions use the
start-of-step geometry, then own ship and every opposing platform move across the
same five minutes. Maneuver settings apply at the start of the first step;
acceleration, turn rate, and transient depth changes are deliberately abstracted.
Passive or focused observation integration runs concurrently and reports at the
step endpoint. Active means one pulse during the first step, followed by passive
listening.

Mast observation and each communications attempt are five-minute deployment,
operation, and recovery cycles concurrent with movement and passive observation.
A usable receive link—whether or not a message is waiting—or an acknowledged
transmission completes that task and returns control. A failed link consumes its
five minutes and is retried during the same command window unless `radio_failure`
is an interrupt. Repair requires 20 productive minutes at 10 knots or less,
persists across command windows, and is concurrent with movement and observation.
The public `command_contract` reports these rules in machine-readable form.

A transmission requires `assessment` (`submerged_present`,
`surface_or_biologic`, or `unresolved`), a `message`, and optionally a `basis`
list containing existing report IDs. Link activity requires the published
mast depth and speed envelope. These are in-game messages only.

Retry an uncertain operation using its **identical order JSON and id**. The engine
returns the cached result without applying it again. A later order needs a new
id. A retry of an older order returns its historical result; use `status` for the
current display. Invalid orders leave the saved state untouched.

To end an exercise early, submit a zero-minute `end` order. At the normal ending
or after that order, `debrief` reveals the seed, truth, action log and replay
verification. During active play, debrief refuses to reveal anything.

## Current implementation

| Area | Behavior |
|---|---|
| Hidden state | Persistent seed, fixed initial contacts, event-keyed random draws, replay verification, public-only outputs |
| Entity model | Shared specifications and operating state for an SSN, diesel/AIP submarine, merchant, surface warship, fishing vessel and biologic group |
| Own platform | Fictional Kestrel-class nuclear exercise submarine; capability, mode and validation limits share one definition |
| Sonar | One combined passive receiver resolved per band through `SE = SL - TL - (NL - DI) - DT`; focused analysis; an active pulse with two-way loss and target strength. Signature cues are still categorical, restricted to the bands that actually arrived |
| Observations | Noisy bearings, timestamped own positions and depth, the band a contact was heard in, measured bearing drift and correlated evidence windows |
| Environment | A piecewise-linear sound-speed profile, charted bathymetry, bottom class, sea state and shipping drive frequency-dependent transmission loss over named paths — direct, surface duct, shadow zone, bottom bounce and a gated convergence zone — each reporting whether it is supported, uncertain or out of the model's scope |
| Environmental knowledge | True conditions vary along the passage and with time and are used for adjudication; the captain sees predictions from an onboard profile estimate carrying its age, origin and uncertainty. A `sound_profile` order buys a fresh measurement for one five-minute step |
| Own-ship noise | Radiated level rises with speed and steps at cavitation inception, which itself rises with depth; self noise rises with speed; a receiver inside the duct sits in a louder noise field |
| Communications | Mast receive/transmit with persistent link conditions |
| Opposition | Limited-information detection and a simple evasive response |
| Resources | Diesel battery use and snorkeling recharge, vessel fuel consumption, and persistent inventories advance on the shared clock |
| Weapons | Fictional inventories are explicit; observation-only policy and unavailable employment are distinct |
| Engineering | An auxiliary-noise fault and timed repair |
| Narrator interface | Restricted local JSON-lines broker with opaque sessions and public-only operations |

All current platform values and probabilities are fictional game parameters.
They are not real class specifications. A capability that is not modeled cannot
be invented by the narrator to answer a question or resolve an order.

## Development

- [Game design](docs/DESIGN.md): turn-based play and idealized physical quantities.
- [Architecture](docs/ARCHITECTURE.md): truth, beliefs, observations and audit boundaries.
- [Narrator protocol](docs/NARRATOR_PROTOCOL.md): restricted operations and deployment boundary.
- [GitHub issues](https://github.com/Sovinnai/submarine-command/issues): all development tasks, priorities and acceptance criteria.
- [Working agreement](AGENTS.md): implementation and narration invariants.

```bash
python -m unittest discover -s tests -v
python scripts/check_repository.py
```

This repository has no CI or GitHub Actions workflows. Checks run locally to
preserve Actions minutes. Development tasks live in issues, not project files.
Local validation was performed on Linux; the Windows locking branch is included
but has not yet been exercised on a Windows runner.
