# Changelog

All notable changes to this skill are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
the skill uses semantic versioning for behavior changes in `SKILL.md`.

## [3.6.0] - 2026-10-06

### Added

- Opt-in deterministic executor (`python scripts/orchestration`). Code runs the
  cycle that the coordinator used to carry in model turns: authors, consolidation,
  the mechanical checks in parallel, reviewers, the matrix, the rubber duck, the
  gate, the feedback of the next round and the delivery. Agents keep authorship,
  consolidation, independent review and the audit; `gate.py` keeps sole authority
  over approval, and every quality requirement is unchanged. Commands: `init`,
  `next`, `record`, `status`, `run`, `qualify` and `metrics`. Guide:
  `docs/executor.md`, `SKILL.md` section 2.7.
- The engine derives every answer from artifacts and an append-only journal, so a
  crash loses nothing and the same command resumes. It writes the files the
  coordinator flow wrote, which the unmodified legacy readers accept.
- Agents return JSON validated against a per-role contract: exact topic coverage,
  quotes present in the reviewed text, enough sources for fact reviewers, ownership
  of section files, and safe portable paths (no traversal, drive or stream
  characters, reserved Windows names, trailing dots, case collisions or links out
  of the swarm). A malformed answer is refused at once with the exact reason and a
  retry prompt that carries it.
- Verdicts are never trusted from a file. The recorded gate result is a cache bound
  to the review's bytes and stands only if the matrix still derives from the
  reviewers' grades and `gate.py`'s own evaluation reproduces it. An edited or
  forged record, a changed deliverable and an altered rejected cycle are refused
  instead of re-run, and source verdicts are snapshotted per round so the final
  recheck cannot reopen a review for a timestamp.
- `run` executes each agent as one non-interactive `copilot` process, in parallel,
  with only the tools of its role, shell and writes denied, an empty scratch folder,
  no custom instructions and the prompt on stdin. Results are recorded as they
  arrive. A timeout or cancel ends the whole process tree and never waits on a pipe
  an orphan holds. It prints a table every minute and keeps
  `reports/execution/driver.json` as a heartbeat.
- `qualify` checks the CLI with a handful of minimal real calls (stdin prompt, no
  write under read-only tools, no read outside the agent's own folder, a web tool
  under the restriction, parallelism, the model in the usage record) and refuses to
  run without `--yes` because it spends
  credits. `run --plan-only` shows the first agents and their exact commands without
  running anything.
- `health.py` understands executor runs: it reads the heartbeat and the journal,
  classifies them as active, stalled or closed and prints the command that resumes.
- `metrics` decomposes an execution into agent time, code time and idle time, for
  executor journals and for monitor journals.
- `DOCSWARM_NO_REAL_CLI=1` makes any path to the real `copilot` fail. The tests set
  it, so a test, or a rule deliberately disabled by mutation testing, can never
  spend credits.

### Changed

- `verify_sources.py` checks URLs concurrently: 8 at a time and at most 3 per host
  by default (`--workers`, `--per-host`). The report keeps the index order and
  `--workers 1` is the sequential behaviour.
- The prompt of an executor task embeds the JSON schema its answer must obey, so a
  backend without native schema enforcement still sees the contract.
- At most 9 authors are supported by the executor: each owns a range of one hundred
  source identifiers.

### Known limits

- Markdown only, and only for new swarms. Presentations, PDF and the evolution of a
  swarm that already has cycles keep the coordinator flow; the executor refuses them
  before spending anything.
- The `copilot` backend rests on the CLI's documented options and was tested with a
  stand-in CLI. It has not been qualified with real calls: run `qualify`. No paid
  benchmark was run, so there is no promised percentage gain.
- The visual monitor panel is not driven by the executor, and a dead source found by
  the final recheck blocks the delivery until someone replaces it.
- Only run on Windows. The `fcntl` lock and the process-group kill are the Linux and
  macOS paths and have never run; run the test suite there before relying on them.
- Agents with web tools run with `--allow-all-urls` (research cannot work otherwise) and
  receive the brief and the document in the prompt, so a page carrying malicious
  instructions could try to make them put that text in a URL. Path confinement stops
  reads of other files, not this. The coordinator flow has the equivalent exposure.

### Fixed

- `resume.py` no longer asks a presentation swarm to "compose" forever. It looked for
  `output/*.md`, which a presentation never has, so every watchdog tick projected a
  recomposition: one recorded run took five R5 recoveries and rebuilt the deck three
  times (`presentation-cycle-03-r2`, `-r3`, `-r4`). The delivery is now the list the
  brief declares in `deliverables`, validated like the gate validates it, and each
  declared file is hash-bound evidence of the projection. Swarms that declare none keep
  the original Markdown lookup.
- Preserve native Space activation on focused presentation controls, scope the
  position counter to support pages, and prevent delayed dialog-close events
  from undoing subsequent navigation.
- Keep disabled HTML controls fully opaque and add alternative descriptions to
  saved PowerPoint controls and the faithful deck's page images.
- Exercise keyboard activation against a non-adjacent index destination, with
  deliberately broken keyboard and support-counter controls in regression tests.

## [3.5.0] - 2026-10-05

### Added

- Execution health measured outside the agent loop: the monitor extension tracks
  the age of the newest observation and publishes `health.json` per execution,
  so a wedged session that keeps reporting `processing` is still seen as stalled.
- `health.py`: stdlib health table composed from artifacts, the resume projection
  and the extension record; prints "not observed" instead of a plausible value.
- `resume.py`: stdlib projection of the next deterministic step of the cycle
  contract plus a durable `reports/resume.json` bound to artifact hashes, so a
  fresh session can continue and a changed artifact forces a recomputation.
- Watchdog protocol in `SKILL.md` section 2.6: a five-minute scheduled prompt
  that prints the table, a recovery catalogue R1 to R5, forbidden actions and
  ceilings of one action per tick, two per agent per cycle and six per execution.
- `docswarm_monitor` operation `recovery`, journaled per event and capped by the
  extension, plus a recovery section in `final-report.md` so a resumed execution
  never looks clean.
- Stall and lost-observation indicators in the monitor panel.

### Changed

- The health classification no longer trusts the session label over the
  measurement: `processing` beyond the threshold with no running agent is
  reported as stalled, and a running agent earns grace only up to three
  thresholds.
- A health file the extension stopped refreshing is discarded in favour of
  artifact ages, and the report says so.
- The monitor's internal read failure channel was renamed from `health_error`
  to `reader_error`, to separate it from execution health.

## [3.4.0] - 2026-10-05

### Added

- Optional presentation delivery: one authored DeckSpec produces offline
  interactive HTML, an image-faithful PowerPoint file and an editable PowerPoint
  file with native title, text, tables, shapes and anchored connectors.
- Strict JSON deck schema with a stdlib schema subset validator, a redistributable
  neutral theme and standard-library TrueType metrics shared by all three outputs.
- One resolved composition plan: pages, lines, reading order, support pagination
  per origin and a reconstructed navigation graph including disabled extremes.
- Offline HTML runtime with a strict content policy, no inline styles, modal
  support dialogs, focus return, keyboard sequence and separate presenter notes.
- Artifact inspection of the saved files: delivered markup, OOXML inventory,
  notes, hidden support slides, anchored connectors, stage bounds, package safety
  and real browser interaction in a pinned Chromium.
- Environment qualification that measures the installed PowerPoint, rehearses
  edit/save/reopen on copies and calibrates the visual difference per page.
- `presentation_contract.py`: stdlib gate validation that rebuilds pages,
  materialisations, navigation and the review domain from the deck itself.
- `preflight`, `build`, `inspect` and `publish` operations plus acceptance
  recording and an all-or-nothing promotion to a new destination.

### Changed

- `artifact_type: presentation` selects the new delivery; absent it, historical
  entries stay documental and legacy slide fields keep their original meaning.
- Presentation reviews cover a closed set of dimensions per topic, per page and
  per format; none of them accepts `not_applicable`, and `editorial-v1` still
  applies to the full delivered text.
- Non-terminal inspection states (`pending`, `not_evaluated`, `unsupported`,
  `stale`) never approve: the gate returns 3 instead of a grade.
- The document path is unchanged and gains no mandatory dependency; the
  presentation libraries stay outside `scripts/checks`.

## [3.3.0] - 2026-10-05

### Added

- Optional portable Markdown-to-PDF engine using ReportLab/Platypus, with textbook
  and technical-report profiles sharing a common visual identity.
- Original vector cover artwork, justified flowing text, navigable contents,
  multipage tables, literal code, Unicode formulas and source-authored vector figures.
- Bundled unmodified OFL fonts, their licenses and pinned upstream hash manifest.
- Artifact-based PDFium inspection of text/operator fidelity, glyph bounds,
  raster visibility, table/figure layout, links, navigation and font embedding.
- PDF/PNG/inspection/manifest bundles and stdlib gate validation of their hashes.
- `docswarm_pdf` render/inspect extension and opt-in personal installation.
- Reference fixtures and deliberately damaged PDFs covering text/operator loss,
  missing pages, invisible text, margin overflow and erased vector ink.

### Changed

- New PDF deliveries are composed and mechanically inspected before full
  editorial/visual review. The engine neither rewrites source content nor grades it.
- PDF dependencies remain optional and outside `scripts/checks`; existing
  pre-3.3 historical documents keep their original validation contract.
- Installation examples, workflow diagrams and output trees include the optional
  PDF bundle and the current inspection/review order.
- Git preserves original font and license bytes, preventing platform line-ending
  conversion from invalidating the pinned artifact hashes.

## [3.2.2] - 2026-10-02

### Changed

- Public documents prefer descriptive names; required acronyms and local codes
  need first-use definitions, consistent meanings and self-contained visual legends.
- Internal swarm IDs remain in control artifacts. Renderers consume approved
  labels instead of inventing abbreviations during composition.
- Clarity review and rubber-duck audits explicitly cover opaque codes, late
  definitions, conflicting meanings and unsupported claims of market standards.
- Declaration templates and existing-declaration guidance include nomenclature
  duties without adding agents or changing the `editorial-v1` data contract.
- Declaration linting rejects duplicate frontmatter keys instead of silently
  allowing a stale guidance version or model field to override the intended value.

### Added

- Stdlib `inspect_nomenclature.py` reports lexical candidates and source locations
  without guessing definitions, assigning grades or claiming complete coverage.
- Nine annotated nomenclature calibration cases, including correctly defined
  local codes, and scanner/reader/editorial regression coverage.

## [3.2.1] - 2026-10-02

### Changed

- Editorial justifications now explicitly address wording, referents, tone and
  standalone meaning, independently of technical correctness or decision quality.
- Authoring guidance converts internal directions into stated application
  conditions, limitations and consequences without inventing customer requirements.
- Visual evolutions preserve facts, logic, scope and caveats while permitting
  wording corrections, followed by renewed review and artifact binding.
- Rubber-duck audits compare grades with passages and justifications and return
  unsupported editorial approvals to the responsible reviewer without silently
  replacing the grade.
- Declaration templates and profile-reuse metadata carry the guidance version;
  existing declarations must be updated explicitly.

### Added

- Annotated calibration pairs across titles, openings, prose, cards, captions and
  conclusions, including a technically valid shared-AKS recommendation with
  unsuitable wording and an acceptable negative statement.
- Regression cases distinguish annotated editorial judgments from mechanical
  schema/hash validation. The `editorial-v1` data contract is unchanged.

## [3.2.0] - 2026-10-02

### Added

- Explicit `editorial-v1` review contract for new documents and evolutions,
  covering titles, openings, prose, captions and conclusions.
- Current-cycle, full-document editorial assessments with concrete quotations,
  independent wording grades and unresolved-finding vetoes.
- Delivery/text hash verification and agreement with the assigned reviewer's
  original JSON before approval, final reporting or profile reuse.
- Annotated generic/specific wording fixtures and end-to-end editorial gate tests.

### Changed

- Authors and coordinators must keep editorial wording in the source document;
  renderers cannot introduce unreviewed slogans, headings or callouts.
- Visual quality and historical approval no longer substitute for the current
  editorial assessment. Targeted/layout-only editorial reviews are invalid.
- Monitor, final report and memory eligibility share current-artifact validation.
- Historical pre-contract reviews keep their original semantics; existing PDFs
  and prior grades are not silently rewritten.

The checks validate review records and artifact identity, not AI authorship or
writing quality inferred from a blacklist. Semantic judgment remains explicit.

## [3.1.0] - 2026-10-01

### Added

- Read-only local swarm monitor with agent graph, live runtime states, cycles,
  per-reviewer scores, artifact details and execution history.
- Compact 720x480 default panel with a short indicator strip, accessible tabs,
  in-panel scrolling and an expand/compact control.
- DPI-aware fitting of the native Copilot canvas window on Windows, bound to the
  host process and a unique execution title. Expansion/compaction resize only
  that window; ordinary browser tabs retain host-managed sizing.
- Copilot canvas extension with automatic local-browser fallback, explicit task
  correlation, native lifecycle observation and task-state reconciliation.
- Versioned per-execution journals and atomic snapshots, with replay, writer
  ownership and stale-observation handling.
- Structured individual reviewer artifacts and a stdlib artifact projection reader.
- Optional `gate.py --output` records tied to the exact review bytes.
- Native Node, Python, installer and isolated demonstration fixtures.

### Changed

- The skill publishes actual coordinator milestones and registers dispatches
  before invoking the real task tools. The monitor never controls agents.
- Installers register the personal extension by default, with document-only
  opt-out, managed-link preflight and non-destructive collision handling.
- Personal registration yields to a project copy to avoid duplicate global tool
  names when both extension sources are loaded.
- Idle agents are labeled as available rather than implying a user response.
  Principal-session activity is observed separately, follow-up correlation uses
  native task/turn identities, and new finish requests wait for `session.idle`
  instead of freezing observation before the runtime settles.
- Python-to-Node transport explicitly preserves UTF-8. Machine-readable JSON
  output is encoding-independent; files retain readable UTF-8 text.
- Source documents, approvals and historical deliverables remain authoritative;
  unavailable monitoring never relaxes the document quality gate.

## [3.0.0] - 2026-10-01

### Removed

- Presentation creation and evolution, PPTX triggers, slide-author and deck-builder
  templates, content/design reviewer variants, rendering workflow and deck example.
- Presentation toolchain checks and installation from both installers, including
  `-WithPresentation` and `--with-presentation`.

### Changed

- The skill now creates and evolves documents only; the Principal Cloud Solution
  Architect editorial profile and document quality workflow remain in place.
- Document final reports omit slide and deck sections when the review has no such
  fields. Historical deck reviews retain their original grades and blocking rules.
- Unsupported installer arguments fail before installation instead of being ignored.
- Existing deliverables, reports and historical changelog entries are preserved;
  previously installed presentation tools are not uninstalled.

## [2.1.0] - 2026-10-01

### Added

- Shared Principal Cloud Solution Architect editorial profile for cloud
  architecture documents and presentations, with audience-specific depth.
- Explicit editorial profile in new briefs and role-specific contracts in
  declarative agents, including reused profiles.

### Changed

- Existing authors apply the profile; clarity, decision-completeness and factual
  reviewers assess their own dimensions without rewriting the content.
- Editorial criteria feed the existing topic grades and quality gate instead of
  introducing a separate editor, agent or approval stage.
- Coordinators align voice and terminology without inventing facts or silently
  resolving technical disagreements.
- Slide authors preserve decision context and visible conditions; deck builders
  preserve meaning, sources, caveats and speaker notes from the specs.
- Instruction-only updates preserve historical deliverables and approvals;
  applying the profile to existing content requires the evolution workflow.
- README and document/deck examples describe the new editorial responsibilities.

## [2.0.0] - 2026-07-13

### Added

- Deterministic source verification with cache and auditable JSON.
- Deterministic arithmetic checks for marked Markdown tables.
- Structured review YAML and an executable quality gate for documents and decks.
- Generated factual skeleton for `final-report.md`.
- Agent frontmatter linting and session model provenance rules.
- Curated cross-swarm memory with proposal-first updates.
- Skill version stamps in briefs and final reports.
- Python stdlib test suite and real arithmetic regression fixtures.

### Changed

- Evidence requirements are now proportional to the reviewer role.
- The cycle order now runs source and table checks before reviewers.
- The final decision follows `gate.py` exit codes instead of prose interpretation.
- Escalation now generates the deterministic final report before notifying the user.
- Unattached table-check markers fail instead of silently disabling arithmetic checks.
- README is an entry point; `SKILL.md` is the sole behavioral specification.
- Repository paths use `/` except in Windows-specific commands.

## [1.2.0] - 2026-07-07

### Added

- PowerPoint presentation mode with slide authors, deck builder, content review,
  multimodal design review, native rendering and speaker notes.
- Optional presentation toolchain installation.

## [1.1.0] - 2026-06-29

### Added

- Explicit model selection, reasoning effort, context tier and rationale per
  declarative agent.
- Document evolution workflow and worked CoE de Nuvem example.

## [1.0.0] - 2026-06-29

### Added

- Initial standalone Document Swarm skill with authors, reviewers, coordinator,
  rubber duck, evidence tracking and the `A` quality gate.
