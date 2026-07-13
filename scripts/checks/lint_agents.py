"""Validate agent Markdown frontmatter without external YAML dependencies."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# Allow ``python3 /repo/scripts/checks/lint_agents.py`` from any cwd.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.checks.common import InputError, parse_yaml


def frontmatter(path: Path) -> dict:
    """Read the initial ``---`` YAML block from an agent Markdown file."""
    text = path.read_text(encoding="utf-8")
    match = re.match(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|$)", text, re.S)
    if not match:
        raise InputError("missing YAML frontmatter")
    data = parse_yaml(match.group(1))
    if not isinstance(data, dict):
        raise InputError("frontmatter must be a mapping")
    return data


def expected_swarm(path: Path) -> str | None:
    """Find the swarm folder immediately containing an ``agents`` directory."""
    for parent in path.parents:
        if parent.name == "agents":
            return parent.parent.name
    return None


def lint(paths: list[Path]) -> list[str]:
    """Return all agent frontmatter violations."""
    errors: list[str] = []
    for path in paths:
        try:
            data = frontmatter(path)
        except (OSError, InputError) as exc:
            errors.append(f"{path}: {exc}")
            continue
        for field in ("name", "kind", "model", "swarm"):
            if not isinstance(data.get(field), str) or not data[field].strip():
                errors.append(f"{path}: missing or empty required field '{field}'")
        expected = expected_swarm(path)
        if expected and data.get("swarm") != expected:
            errors.append(f"{path}: swarm must be '{expected}', got {data.get('swarm')!r}")
        for field in ("model_status", "model_rationale", "context_tier"):
            if field in data and (not isinstance(data[field], str) or not data[field].strip()):
                errors.append(f"{path}: optional field '{field}' must be a non-empty string when present")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Lint required Markdown agent frontmatter fields.")
    parser.add_argument("path", type=Path, help="agent file or swarm/agents directory")
    parser.add_argument("--strict", action="store_true", help="require optional model provenance fields")
    args = parser.parse_args(argv)
    paths = [args.path] if args.path.is_file() else sorted(args.path.rglob("*.md"))
    if not paths:
        print(f"No agent Markdown files under {args.path}", file=sys.stderr)
        return 1
    errors = lint(paths)
    if args.strict:
        for path in paths:
            try:
                data = frontmatter(path)
            except (OSError, InputError):
                continue
            for field in ("model_status", "model_rationale", "context_tier"):
                if field not in data:
                    errors.append(f"{path}: strict mode requires '{field}'")
    if errors:
        print("\n".join(f"ERROR {error}" for error in errors), file=sys.stderr)
        return 1
    print(f"OK: {len(paths)} agent file(s) passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
