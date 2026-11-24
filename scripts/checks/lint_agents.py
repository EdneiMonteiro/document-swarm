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

VALID_REASONING_EFFORTS = frozenset({"low", "medium", "high", "xhigh", "max"})
WRITING_KINDS = frozenset({"author", "reviewer", "rubber-duck"})
ROLE_RECOMMENDED_EFFORT = {
    "coordinator": "low",
    "author": "medium",
    "reviewer": "low",
    "rubber-duck": "low",
}


def infer_model_family(model: str) -> str | None:
    """Infer canonical provider family from a model identifier when recognizable."""
    if not isinstance(model, str) or not model.strip():
        return None
    lowered = model.strip().lower()
    if lowered.startswith("claude"):
        return "anthropic"
    if lowered.startswith(("gpt", "o1", "o3", "o4", "codex")) or "codex" in lowered:
        return "openai"
    if lowered.startswith("gemini"):
        return "google"
    if lowered.startswith("grok"):
        return "xai"
    if lowered.startswith(("mai", "phi")):
        return "microsoft"
    return None


def frontmatter(path: Path) -> dict:
    """Read the initial ``---`` YAML block from an agent Markdown file."""
    return frontmatter_text(path.read_text(encoding="utf-8"))


def frontmatter_text(text: str) -> dict:
    """Decode captured frontmatter without reopening its source file."""
    match = re.match(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|$)", text, re.S)
    if not match:
        raise InputError("missing YAML frontmatter")
    seen = set()
    for key in re.findall(r"(?m)^([^\s:#][^:\r\n]*):", match.group(1)):
        key = key.strip()
        if key in seen:
            raise InputError(f"duplicate frontmatter field: {key}")
        seen.add(key)
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
        if "reasoning_effort" in data:
            effort = data.get("reasoning_effort")
            if not isinstance(effort, str) or effort.strip() not in VALID_REASONING_EFFORTS:
                allowed = ", ".join(sorted(VALID_REASONING_EFFORTS))
                errors.append(f"{path}: reasoning_effort must be one of {{{allowed}}}, got {effort!r}")
        if "fallback_model" in data:
            fb = data.get("fallback_model")
            primary = data.get("model") if isinstance(data.get("model"), str) else ""
            if not isinstance(fb, str) or not fb.strip():
                errors.append(f"{path}: optional field 'fallback_model' must be a non-empty string when present")
            elif fb.strip() == primary.strip():
                errors.append(f"{path}: fallback_model '{fb}' must differ from primary model '{primary}'")
            else:
                primary_family = infer_model_family(primary)
                fb_family = infer_model_family(fb)
                if primary_family is not None and fb_family != primary_family:
                    errors.append(
                        f"{path}: fallback_model '{fb}' (family={fb_family!r}) must preserve primary model family '{primary_family}'"
                    )
        if "tools" in data:
            tools = data.get("tools")
            if not isinstance(tools, list) or not tools or not all(isinstance(t, str) and t.strip() for t in tools):
                errors.append(f"{path}: optional field 'tools' must be a non-empty list of non-empty strings")
            else:
                tool_set = {t.strip() for t in tools}
                kind = data.get("kind")
                model = data.get("model") if isinstance(data.get("model"), str) else ""
                if (
                    kind in WRITING_KINDS
                    and "codex" in model.strip().lower()
                    and bool(tool_set & {"create", "edit"})
                    and "apply_patch" not in tool_set
                ):
                    errors.append(
                        f"{path}: openai/codex model '{model}' with explicit write tools must include 'apply_patch'"
                    )
    return errors


def preflight_tools(paths: list[Path], available_tools: list[str] | None = None) -> list[str]:
    """Validate agent declarations and optional CLI --available-tools compatibility."""
    errors = lint(paths)
    if available_tools is None:
        return errors
    avail = {item.strip() for item in available_tools if isinstance(item, str) and item.strip()}
    for path in paths:
        try:
            data = frontmatter(path)
        except (OSError, InputError):
            continue
        kind = data.get("kind")
        model = data.get("model") if isinstance(data.get("model"), str) else ""
        if kind == "coordinator" and "task" not in avail:
            errors.append(f"{path}: coordinator requires 'task' in available_tools")
        if kind in WRITING_KINDS:
            is_codex = "codex" in model.strip().lower()
            if is_codex and "apply_patch" not in avail:
                errors.append(f"{path}: openai/codex agent '{data.get('name')}' ({model}) requires 'apply_patch' in available_tools")
            elif not is_codex and not (avail & {"create", "edit", "apply_patch"}):
                errors.append(f"{path}: agent '{data.get('name')}' ({model}) requires write tools ('create'/'edit') in available_tools")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Lint required Markdown agent frontmatter fields.")
    parser.add_argument("path", type=Path, help="agent file or swarm/agents directory")
    parser.add_argument("--strict", action="store_true", help="require optional model provenance fields")
    parser.add_argument("--available-tools", nargs="+", help="optional CLI tool allowlist to validate")
    args = parser.parse_args(argv)
    paths = [args.path] if args.path.is_file() else sorted(args.path.rglob("*.md"))
    if not paths:
        print(f"No agent Markdown files under {args.path}", file=sys.stderr)
        return 1
    errors = preflight_tools(paths, args.available_tools)
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
