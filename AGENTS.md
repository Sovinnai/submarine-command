# Project working agreement

## Product goal

Build a compelling submarine command game for a knowledgeable submariner.
The simulation should support decisions about evidence, mission, time and risk.
Use explicit fictional game parameters for technical values. Clearly separate
implemented mechanics, deliberately simplified mechanics and future work.
The intended format combines a miniatures wargame's discrete command decisions
with computer-resolved movement, acoustic measurements and sound propagation.
It is turn-based, with simulated elapsed time and no real-time input pressure.
See docs/DESIGN.md for the design contract.

## Information boundary

- The engine owns world truth and random outcomes. Narration uses public reports.
- Never print, inspect into model context, or search a live seed, private save,
  actor truth, or debrief while playing. Engine processes may load them internally.
- Test only disposable worlds with test seeds. Never reroll or advance a live
  patrol while debugging a hypothetical alternative.
- An existing report cannot gain new evidence because the captain asks again.
- Keep observations, derived assessments, opponent beliefs, and truth distinct.
- Opponents act on their own observation history, not omniscient ship positions.
- Treat shared-source reports as correlated evidence; repeated LLM narration is
  not an independent sensor observation.

## Changes and replay

- Capabilities, scenario configuration and command validation must agree.
- Unsupported orders fail before state mutation. Do not silently execute a
  different depth, course, transmission or emission.
- Preserve idempotent action ids, atomic saves, and read-only queries.
- Rules changes require a new committed rules version. Do not overwrite a live
  game's engine or weaken its verification to make an old save load.
- Prefer new scenarios over implicit backward compatibility. An explicit save
  migration must document its effect on commitments and require a user request.

## Repository and testing

- Never commit live sessions, private/public save JSON, patrol transcripts,
  debriefs, seeds, archive bundles, local credentials, or the original chat save.
- Run `python -m unittest discover -s tests -v` for engine changes and
  `python scripts/check_repository.py` before publishing.
- Add tests for meaningful behavior: information leaks, physical consistency,
  correlation, invalid actions, replay, and command-envelope boundaries.
- Do not add or run CI. In particular, do not add GitHub Actions workflows,
  scheduled jobs, or workflow dispatches. The user explicitly reserves Actions
  minutes for another project. Run necessary verification locally instead.
- Do not schedule recurring check-ins to poll a pull request. This repository
  runs no CI, and pull-request webhooks already wake a session on real activity,
  so a timer only burns context to re-read an unchanged PR. After opening a pull
  request, report its state once and stop. If it is blocked, say what is blocking
  it once; do not re-arm a timer to look again.
- Track development tasks and acceptance criteria in GitHub issues only:
  https://github.com/Sovinnai/submarine-command/issues
- Do not maintain a TODO, backlog, roadmap or task checklist in source files or
  project documents. Documents describe design, usage and current behavior;
  issues own priorities, dependencies and incomplete work.
- Avoid new dependencies until a concrete modeling or interface need warrants
  them. Research technical APIs using primary documentation.
