# Changelog

All notable changes to this skill are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
the skill uses semantic versioning for behavior changes in `SKILL.md`.

## [Unreleased]

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
