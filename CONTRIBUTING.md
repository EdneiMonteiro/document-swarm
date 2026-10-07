# Contributing

## Issues and pull requests

To reduce spam and keep this project maintainable, creating issues and pull
requests is restricted to collaborators with `write` access.

If you are not a collaborator:

- open a Discussion, if enabled; or
- contact the maintainer to propose the change.

For maintainers, use one issue per branch and pull request. Prefer branch names
such as `feat/issue-<n>-<slug>`.

The default branch is protected: deletion and force-pushes are disabled.

## Behavioral changes

`SKILL.md` is executable behavior, not ordinary prose. A pull request that
changes it must:

1. update `CHANGELOG.md`;
2. keep `skill_version` semantically correct;
3. update directly related documentation, enumerating the whole category rather
   than the places you happen to remember: version strings, script tables,
   output trees, agent templates, diagrams and contributor invariants;
4. add or update a deterministic fixture when the changed behavior is
   mechanically testable;
5. run a small end-to-end fixture swarm before merge.

## Code

- Scripts in `scripts/checks/` must use Python 3 standard library only.
- Run `python3 -m unittest discover -s tests -v`.
- Scripts assist the coordinator; they must not pretend to replace semantic
  review by authors, reviewers or the rubber duck.

## Documentation

- `tests/test_docs.py` enforces what used to be checked by hand: every internal
  link and anchor resolves, every fenced block is closed, and every mermaid
  block declares a known diagram with balanced labels.
- Anchors follow GitHub's rule, which removes punctuation without collapsing the
  space it leaves. An em dash therefore produces two hyphens, and renaming a
  heading breaks every link that pointed at it.
- These are structural checks. They prove a diagram parses as a diagram; they
  say nothing about whether it is readable. Render a changed diagram and look at
  it before merging: a valid flowchart can still be an unreadable column with
  labels far from the edges they name.
- Keep the checks honest. Each one ships with a control that plants the defect
  it claims to catch, and a check that cannot fail is worse than no check.

## Editorial approval

- New runs use `quality_contract: editorial-v1` and list their assigned
  `editorial_reviewer` and final `deliverables` in the brief.
- Treat slogans, generic headings, empty metatext and decorative contrasts as a
  family of editorial defects across the full document, not as five banned strings.
- Keep wording evaluation independent from visual/layout evaluation.
- Also distinguish wording quality from factual correctness and decision merit.
  Justifications must examine language, referents, tone and standalone meaning.
- When auditing an unsupported editorial A, return the passage and insufficient
  rationale to its reviewer; do not silently replace that reviewer's grade.
- Preserve facts, logic, scope and caveats during visual evolution, not literal
  unsuitable wording. Rewritten text must be reviewed and bound to the new files.
- Materialize the current guidance when generating/reusing declarations, and
  update existing files explicitly. A guidance-version stamp is not a quality score.
- Gate, monitor, final-report and memory paths must not bypass current-artifact
  validation. Preserve historical review semantics when evolving a document.
- Fixtures encode expected reviewer judgments, not an automated AI-text detector.
  Run `python -m unittest tests.test_editorial -v` for the contract regression.
  The language-calibration pairs retain architectural conditions and include
  all affected surfaces; their judgments are annotated reference answers.
- Keep internal topic/source IDs stable while improving public labels. First-use
  definitions and isolated figures must remain understandable to the stated reader.
- Nomenclature inspection is lexical only: preserve source line locations, avoid
  source edits, exclude declared non-prose spans, and never infer meanings or
  turn zero candidates into editorial approval.

See the [editorial review format](./docs/editorial-review.md).

## Optional PDF engine

- Keep dependencies in `requirements-pdf.txt` and `scripts/pdf`, not in the
  stdlib checks. Core tests must still run without the PDF packages.
- Preserve source wording and Unicode operators; unsupported constructs must
  fail explicitly rather than drop content or substitute characters.
- Bundle only redistributable fonts with original licenses and pinned hashes.
- Preserve the font/license `.gitattributes` rules: upstream line endings and
  whitespace are part of those hashes and must not be reformatted.
- Run `python -m unittest tests.test_pdf_engine -v` using the optional PDF environment.
  These tests include actual PDF defects, not just altered manifest values.
- Inspect rendered reference pages for both profiles. Hash equality is not
  a substitute for visual/editorial review.
- Existing customer artifacts are reference inputs, never test output destinations.

## Optional presentation engine

- Keep `scripts/presentations` and `requirements-presentations.txt` outside the
  stdlib checks; `scripts/checks/presentation_contract.py` must never import a
  browser, Office or python-pptx.
- Rebuild the expected pages, navigation and review coverage from the deck. A
  manifest or an exporter map is evidence of intent, never of what was delivered.
- Inspect the saved files: parse the delivered markup and open the PPTX package.
  Add a detector only with a control that fails on the specific defect.
- Run `python -m unittest tests.test_presentation_contract -v` with the default
  interpreter and `tests.test_presentation_engine` in the optional environment.
- Office rehearsals run only on an interactive Windows session, on copies, and
  never close a PowerPoint the operator owns. Report `not_evaluated` honestly
  when the network isolation of the station cannot be demonstrated.
- Keep `pending`, `not_evaluated`, `unsupported` and `stale` outside the set of
  states that can approve anything.

See [the presentation guide](./docs/presentations.md).

## Visual monitor

- The extension uses the Copilot-provided SDK and Node.js 20+ built-ins; the UI
  uses local HTML/CSS/SVG/JavaScript without npm dependencies or a CDN.
- Run `node --test .github/extensions/document-swarm-monitor/tests/monitor.test.mjs`.
- Keep SDK integration separate from the state reducer and HTTP renderer.
- Do not add agent-control APIs, collect prompts or persist viewer credentials.
- Observe `cancelled` separately from successful completion; runtime availability,
  review grades, gate results and delivery closure are different facts.
- Preserve recorded execution history. Do not infer missing historical grades.
- Validate actual rendering and at least one native task/follow-up, not only
  synthetic events. Keep fixtures explicitly identified as demonstrations.
- Installer tests must use isolated homes and prove links do not delete their
  destinations or replace unrelated user directories.

See [monitor architecture and development](./docs/monitor.md).

## Execution watchdog

- Stall detection belongs to the extension process, outside the agent loop; a
  wedged loop would never run a scheduled prompt, so nothing inside the session
  can be the only detector.
- The age of the newest observation decides the state; the session label only
  explains it. Never report `active` because the label says `processing`.
- An absent measurement is reported as not observed. Do not substitute a
  plausible value, and do not trust a `health.json` the extension stopped
  refreshing.
- `health.py` and `resume.py` are stdlib-only projections that must never write
  into the swarm, assign a grade, close a cycle or approve delivery.
- Recovery ceilings are enforced by the extension, not by prose: two per agent
  per cycle and six per execution. Exceeding one must be an explicit error.
- Every recovery is journaled and surfaced in `final-report.md`. A resumed
  execution must never look like a clean execution.
- `resume.py` follows the cycle contract in `SKILL.md`. Changing that contract
  requires changing `resume.py`, and `MONITOR_PHASES` must keep matching the
  extension's `PHASES`.
- Run `python -m unittest tests.test_watchdog -v`, including the stall fixtures,
  the ceiling cases and the negative controls.

See [execution health and resume](./docs/monitor.md#saúde-da-execução-e-retomada).

## Deterministic executor

- `scripts/orchestration/` is stdlib-only, like the checks. The engine derives every
  answer from artifacts and the journal, never from memory, so a crash between two
  calls loses nothing. Keep it that way: a decision that cannot be recomputed from
  disk does not belong in the engine.
- The executor never assigns a grade and never approves a delivery. Grades come from
  the reviewers' own results and approval comes from `gate.py`. A recorded verdict is
  a cache that stands only if `gate.evaluate_current` reproduces it now.
- Agents return JSON and never write. Every path, count and string that reaches the
  disk from an agent result is validated first. When a defect is found in one member
  of a family (a path form, a limit, a type), enumerate and fix the whole family.
  What no validator foresaw is still a refusal of that attempt, never an exception.
- A checker exits 3 when it crashes. Python gives an uncaught exception 1, which is the
  status of "findings" (and of "rejected" for the gate), so a crash must never leave
  through the default; the engine also requires a fresh, readable report from each one.
- A selection or a decision that a repair changes must be stored when it is made.
  Recomputed on every call, it changes under the round it governs (the authors a
  failing check goes back to are decided once, from the sources that named them).
- Every rule needs a test that fails when the rule is disabled. Disable the rule by
  hand, or with a script that applies one textual mutation to a copy of `scripts/`
  and `tests/`, run the test, and expect it to fail. A rule no test notices is a rule
  without a test. Mutate on a copy, never in place. A test can also stop pinning its
  rule when a later rule answers first, so re-run the mutations after a change that
  adds a second line of defence.
- To stub a checker in an engine test, use `EngineCase.patched()`. It leaves the fresh
  report a real run would leave, because the engine treats a checker that wrote none as
  crashed before it reads the exit status. A test that replaces `run_script` without a
  report passes whatever the table of accepted exit codes says. Pass
  `leaves_report=False` to test exactly that crash.
- A test that runs a swarm in a background thread releases and joins it in
  `addCleanup`, and reads `driver.json` with retries: the heartbeat is replaced every
  beat, and on Windows a read that meets the replace is refused.
- Tests never call a model. They use `tests/fake_copilot.py`, and
  `DOCSWARM_NO_REAL_CLI=1` (set by `tests/test_orchestration_backend.py`) makes any
  path to the real `copilot` fail, including under mutation testing. Only
  `qualify --yes` makes real calls; do not run it, or `run`, without being told to.
  The test source servers listen on 127.0.0.1, which the author contract refuses, so
  `tests/test_orchestration_engine.py` sets `DOCSWARM_ALLOW_LOCAL_URLS=1`; the tests of
  the guard itself switch it off again.
- Keep the engine suites sequential with other suites that share `tests/.work`:
  `python -m unittest tests.test_orchestration_engine tests.test_orchestration_backend
  tests.test_orchestration_cli tests.test_orchestration_hostile
  tests.test_checker_crashes tests.test_executor_health -v`.
- A change to the cycle contract in `SKILL.md` needs the same change in `engine.py`,
  `resume.py` and `health.py`.

See [the executor guide](./docs/executor.md).
