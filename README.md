# Submarine Command

A turn-based submarine command game combining miniature-wargame decisions with
computer-resolved movement and idealized acoustic measurements. Python owns the
hidden world; an LLM can interpret orders and present the watch team's available
evidence. The game never runs against the player's wall clock.

The design uses real kinds of quantities and relationships: frequencies,
harmonics, noise, sound transmission, array geometry and target motion. Resolution
may be abstracted into turns, bands or tables, but measurements have persistent
causes and the captain's decisions must change the world consistently.

**Status: early prototype, version 0.10.** The hidden-state and replay foundation
works. Acoustic transmission uses a documented sound-speed profile and path
approximations. Contact reports include measured narrowband frequencies, line
quality and uncertainty. Array geometry is still simplified.
Unsupported systems are explicitly identified in the capability report.

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
  "course": 90,
  "speed": 5,
  "depth": 400,
  "operating_mode": "standard",
  "interrupt_on": ["new_contact", "classification_change", "equipment", "contact_lost"]
}
```

```bash
python -m submarine_command --session .sessions/my-patrol act --order-file .sessions/order.json
python -m submarine_command --session .sessions/my-patrol verify
```

Supported activities are `listen`, `focus`, `active`, `mast`, `receive`,
`transmit`, `repair`, and `end`. **Every field must be stated.** The engine
supplies no default for any of them: an order missing `course`, `speed`,
`depth`, `operating_mode`, `minutes`, `activity`, `interrupt_on` or
`expected_turn` is rejected before anything changes, rather than being filled in
from the current settings. An empty `interrupt_on` means no interrupt was
selected, and an empty `basis` means the assessment cites no report. An `end`
order takes only `id`, `expected_turn` and `activity`. Mode-specific and
platform-wide envelopes are both validated. Orders run for 5–60 minutes in
five-minute steps, with interruption on the selected events. Every result's
`last_execution` states the requested, elapsed, and unused minutes; its stop
reason and public report IDs explain why control returned. The unused portion is
never resumed automatically.

The engine uses one shared clock. At each step, opposing decisions use the
start-of-step geometry, then own ship and every opposing platform move across the
same five minutes. Commanded course, speed, depth and operating mode become the
standing settings at the start of the first step, but they are not achieved
instantly: actual values transit toward them at the published maneuver rates,
resolved on one-minute increments inside each step. Depth rate is proportional to
actual speed, so coming shallow quickly also means going fast and being loud.
`own_ship.maneuver` and `last_execution.maneuver` report the achieved value, the
commanded value and whether the maneuver is still in progress. An interrupt
returns control without cancelling it, but the next order must restate the
commanded settings like any other. Opposing entities are not rate-limited in this
rules version; the capability report states that.
Passive or focused observation integration runs concurrently and reports at the
step endpoint. Active means one pulse during the first step, followed by passive
listening.

Mast observation and each communications attempt are five-minute deployment,
operation, and recovery cycles concurrent with movement and passive observation.
A usable receive link—whether or not a message is waiting—or an acknowledged
transmission completes that task and returns control. A failed link consumes its
five minutes and is retried during the same command window unless `radio_failure`
is an interrupt. A cycle requires the whole step inside the mast envelope, so a
step spent transiting toward mast depth is reported as `activity_deferred` with
the achieved and ordered settings, and costs its five minutes. Repair requires 20
productive minutes at 10 knots or less, counting only the minutes actually spent
at or below that speed, persists across command windows, and is concurrent with
movement and observation.
The public `command_contract` reports these rules in machine-readable form.

A transmission requires `assessment` (`submerged_present`,
`surface_or_biologic`, or `unresolved`), a `message`, and a `basis` list of
existing report IDs. Like every other field, `basis` must be stated: an empty
list is the explicit declaration that the assessment cites no report. Link
activity requires the published mast depth and speed envelope. These are in-game
messages only.

Retry an uncertain operation using its **identical order JSON and id**. The engine
returns the cached result without applying it again. A later order needs a new
id. A retry of an older order returns its historical result; use `status` for the
current display. Invalid orders leave the saved state untouched.

To end an exercise early, submit an `end` order carrying only `id`,
`expected_turn` and `activity`; it has no duration. At the normal ending
or after that order, `debrief` reveals the seed, truth, action log and replay
verification. During active play, debrief refuses to reveal anything.

## Current implementation

| Area | Behavior |
|---|---|
| Hidden state | Persistent seed, fixed initial contacts, event-keyed random draws, replay verification, public-only outputs |
| Entity model | Shared specifications and operating state for an SSN, diesel/AIP submarine, merchant, surface warship, fishing vessel and biologic group |
| Own platform | Fictional Kestrel-class nuclear exercise submarine; capability, mode and validation limits share one definition |
| Maneuver | Ordered course, speed, depth and mode resolved over simulated time at published turn, acceleration and speed-proportional depth rates; achieved and ordered reported separately |
| Sonar | Generic passive reception, focused analysis, active range measurement, and numeric narrowband frequencies with quality and uncertainty |
| Observations | Noisy bearings, timestamped own positions, measured bearing drift, and a separate constant-motion solution family |
| Classification | Overlapping spectral features tied to measured lines, cited reports, and two priors |
| Frequency change | Crew indication from successive frequencies after own-ship Doppler is removed; aspect does not change level |
| Environment | Hidden sound-speed profile, water depth and bottom; dated onboard estimate; frequency-dependent path approximations |
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
- [Acoustic environment](docs/ACOUSTICS.md): sound-speed profile, path approximations, units and recorded assumptions.
- [Narrowband spectra](docs/SPECTRA.md): persistent emitter lines, operating-state rules and noisy frequency measurements.
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
