# Changelog

All notable changes to this skill are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
the skill uses semantic versioning for behavior changes in `SKILL.md`.

## [Unreleased]

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
