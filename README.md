# Submarine Command

A turn-based submarine command game combining miniature-wargame decisions with
computer-resolved movement and idealized acoustic measurements. Python owns the
hidden world; an LLM can interpret orders and present the watch team's available
evidence. The game never runs against the player's wall clock.

The design uses real kinds of quantities and relationships: frequencies,
harmonics, noise, sound transmission, array geometry and target motion. Resolution
may be abstracted into turns, bands or tables, but measurements have persistent
causes and the captain's decisions must change the world consistently.

**Status: early prototype, version 0.2.** The hidden-state and replay foundation
works. The acoustic and platform fidelity still needs substantial development.
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
`transmit`, `repair`, and `end`. Except for `end`, an order may also include
`course`, `speed`, and `depth`. Orders run for 5–60 minutes in five-minute steps,
with interruption on the selected events. Active means one pulse on the first
tick, followed by passive listening. Maneuvers establish the commanded settings
for the first tick; accelerations and transient depth changes are abstracted.

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
| Platform | Fictional Kestrel-class nuclear exercise submarine; capability and validation limits share a definition |
| Sonar | Generic passive reception, focused analysis and active range measurement; signature cues are currently categorical |
| Observations | Noisy bearings, timestamped own positions, measured bearing drift and correlated evidence windows |
| Environment | Uncertain layer and simplified loss across it |
| Communications | Mast receive/transmit with persistent link conditions |
| Opposition | Limited-information detection and a simple evasive response |
| Weapons | Observation-only exercise policy; no inventory or employment resolution |
| Engineering | An auxiliary-noise fault and timed repair |

All current platform values and probabilities are fictional game parameters.
They are not real class specifications. A capability that is not modeled cannot
be invented by the narrator to answer a question or resolve an order.

## Development

- [Game design](docs/DESIGN.md): turn-based play and idealized physical quantities.
- [Architecture](docs/ARCHITECTURE.md): truth, beliefs, observations and audit boundaries.
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
