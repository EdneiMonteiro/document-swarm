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
3. update directly related documentation;
4. add or update a deterministic fixture when the changed behavior is
   mechanically testable;
5. run a small end-to-end fixture swarm before merge.

## Code

- Scripts in `scripts/checks/` must use Python 3 standard library only.
- Run `python3 -m unittest discover -s tests -v`.
- Scripts assist the coordinator; they must not pretend to replace semantic
  review by authors, reviewers or the rubber duck.

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
