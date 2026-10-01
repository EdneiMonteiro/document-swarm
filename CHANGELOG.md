# Changelog

All notable changes to this skill are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
the skill uses semantic versioning for behavior changes in `SKILL.md`.

## [Unreleased]

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
