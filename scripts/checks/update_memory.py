"""Propose or apply curated cross-swarm source and agent-profile memory.

The default is deliberately read-only for ``memory/``: it writes
``reports/memory-proposal.json`` inside the given swarm.  Applying requires
both ``--apply`` and ``--approve`` to make accidental curation impossible.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
from pathlib import Path
from typing import Any

# Allow ``python3 /repo/scripts/checks/update_memory.py`` from any cwd.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.checks.common import InputError, load_data, write_json
from scripts.checks.gate import evaluate_current, requires_editorial
from scripts.checks.lint_agents import frontmatter

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def cycle_order(path: Path) -> tuple[int, str]:
    match = re.search(r"cycle-(\d+)", path.name, re.I)
    return (int(match.group(1)) if match else -1, path.name)


def source_rows(index: Path) -> list[dict[str, str]]:
    """Extract source metadata from a legacy Markdown source index."""
    rows = []
    headers: dict[str, int] = {}
    for line in index.read_text(encoding="utf-8").splitlines():
        if not line.lstrip().startswith("|"):
            continue
        parts = [part.strip() for part in line.strip().strip("|").split("|")]
        if parts and parts[0].lower() == "id":
            headers = {name.lower(): position for position, name in enumerate(parts)}
            continue
        if not parts or not re.fullmatch(r"F\d+", parts[0], re.I):
            continue
        def column(*names: str) -> str:
            for name in names:
                position = headers.get(name.lower())
                if position is not None and position < len(parts):
                    return parts[position]
            return ""
        url = column("url") or next((part for part in parts if part.startswith(("http://", "https://"))), "")
        status_text = " | ".join(parts)
        topics = column("tópicos cobertos", "topicos cobertos", "tópicos", "topics")
        hostname = urllib.parse.urlparse(url).hostname or ""
        rows.append({
            "id": parts[0],
            "title": column("título", "titulo", "title") or (parts[1] if len(parts) > 1 else ""),
            "url": url,
            "type": column("tipo", "type"),
            "topics": topics,
            "domain": hostname,
            "accessed_at": column("data de acesso", "access date", "checked_at"),
            "legacy_status": status_text,
        })
    return rows


def legacy_status(text: str) -> str:
    """Conservatively translate historical prose into current audit statuses."""
    upper = text.upper()
    if "403" in upper or "429" in upper:
        return "warn"
    if "HTTP 200" in upper or "WEB_FETCH" in upper or "MICROSOFT DOCS FETCH" in upper:
        return "ok"
    return "unknown"


def proposed_sources(swarm: Path) -> list[dict[str, Any]]:
    index = swarm / "sources" / "sources-index.md"
    if not index.exists():
        raise InputError(f"missing source index: {index}")
    rows = source_rows(index)
    if not rows:
        raise InputError(f"source index has no Fxx rows: {index}")
    checks_path = swarm / "sources" / "sources-check.json"
    checks: dict[str, dict[str, Any]] = {}
    if checks_path.exists():
        try:
            payload = json.loads(checks_path.read_text(encoding="utf-8"))
            results = payload.get("results")
            if not isinstance(results, list):
                raise InputError(f"invalid sources check: {checks_path} has no results list")
            checks = {
                item["url"]: item
                for item in results
                if isinstance(item, dict) and item.get("url")
            }
        except (json.JSONDecodeError, OSError, AttributeError) as exc:
            raise InputError(f"invalid sources check {checks_path}: {exc}") from exc
    candidates = []
    for row in rows:
        current = checks.get(row["url"], {})
        candidates.append({
            **row,
            "status": current.get("status", legacy_status(row["legacy_status"])),
            "checked_at": current.get("checked_at") or row["accessed_at"],
            "source": "sources-check.json" if current else "legacy sources-index.md",
            "origin_swarm": swarm.name,
        })
    return candidates


def approved(swarm: Path) -> bool:
    """Only a structured approved gate, or legacy explicit 'Aprovado', qualifies."""
    reviews = (sorted((swarm / "reports").glob("cycle-*-review.y*ml"), key=cycle_order)
               if (swarm / "reports").exists() else [])
    if reviews:
        try:
            return evaluate_current(load_data(reviews[-1]), swarm)["outcome"] == "approved"
        except (InputError, OSError, UnicodeError, json.JSONDecodeError):
            return False
    brief = swarm / "brief.md"
    if brief.exists() and requires_editorial(frontmatter(brief)):
        return False
    final = swarm / "reports" / "final-report.md"
    return bool(final.exists() and re.search(
        r"(?im)^\s*(?:[-*]\s*)?\*\*(?:aprovado|approved)\b", final.read_text(encoding="utf-8")
    ))


def profile_summary(path: Path, swarm_name: str) -> dict[str, Any] | None:
    try:
        metadata = frontmatter(path)
    except (InputError, OSError):
        return None
    text = path.read_text(encoding="utf-8")
    body = text.split("\n---", 2)[-1]
    mission = next((line.strip() for line in body.splitlines() if line.strip() and not line.startswith("#")), "")
    if not metadata.get("name") or not metadata.get("model"):
        return None
    profile = {
        "name": str(metadata["name"]),
        "kind": str(metadata.get("kind", "")),
        "role": str(metadata.get("role", "")),
        "model": str(metadata["model"]),
        "sources_min": metadata.get("sources_min"),
        "summary": mission[:500],
        "origin_swarm": swarm_name,
        "origin_agent": str(path.relative_to(path.parents[next(i for i, p in enumerate(path.parents) if p.name == "agents")])),
    }
    for field in ("context_tier", "reasoning_effort", "model_status", "model_rationale", "derived_from", "editorial_guidance_version"):
        if metadata.get(field) is not None:
            profile[field] = metadata[field]
    return profile


def proposal(swarm: Path) -> dict[str, Any]:
    """Build a proposal without changing global memory."""
    is_approved = approved(swarm)
    profiles = []
    if is_approved:
        profiles = [profile for path in sorted((swarm / "agents").rglob("*.md"))
                    if (profile := profile_summary(path, swarm.name)) is not None]
    return {
        "schema_version": 1,
        "swarm": swarm.name,
        "approved": is_approved,
        "sources": proposed_sources(swarm),
        "profiles": profiles,
        "apply_rule": "Only ok/redirect sources are eligible; profiles require an approved swarm.",
    }


def load_store(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": 1, "sources": [], "profiles": [], "calibration": []}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise InputError("memory store must be an object")
    return {
        "schema_version": 1,
        "sources": list(value.get("sources", [])),
        "profiles": list(value.get("profiles", [])),
        "calibration": list(value.get("calibration", [])),
    }


def apply_proposal(store: dict[str, Any], candidate: dict[str, Any],
                   calibration_notes: list[str] | None = None) -> dict[str, Any]:
    """Merge only curated candidates, retaining provenance and standalone summaries."""
    by_url = {item.get("url"): item for item in store["sources"]}
    for source in candidate["sources"]:
        if source.get("status") in {"ok", "redirect"} and source.get("url"):
            by_url[source["url"]] = {
                key: source.get(key)
                for key in (
                    "id",
                    "title",
                    "type",
                    "url",
                    "topics",
                    "domain",
                    "status",
                    "checked_at",
                    "accessed_at",
                    "source",
                    "origin_swarm",
                )
            }
    by_identity = {(item.get("origin_swarm"), item.get("name")): item for item in store["profiles"]}
    if candidate["approved"]:
        for profile in candidate["profiles"]:
            by_identity[(profile["origin_swarm"], profile["name"])] = profile
    calibration = list(store.get("calibration", []))
    if candidate["approved"]:
        for note in calibration_notes or []:
            item = {"note": note, "origin_swarm": candidate["swarm"]}
            if item not in calibration:
                calibration.append(item)
    return {
        "schema_version": 1,
        "sources": [by_url[key] for key in sorted(by_url)],
        "profiles": [by_identity[key] for key in sorted(by_identity, key=lambda value: (str(value[0]), str(value[1])))],
        "calibration": calibration,
    }


def render_index(store: dict[str, Any]) -> str:
    lines = ["# Curated swarm memory", "", "Generated from `memory/sources.json`; edit via `update_memory.py`.", "",
             "## Verified sources", ""]
    if store["sources"]:
        lines += [
            "| ID | Title | Type | Domain | Topics | Status | Checked | URL | Origin swarm |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        for source in store["sources"]:
            lines.append("| {id} | {title} | {type} | {domain} | {topics} | {status} | {checked} | {url} | {origin} |".format(
                id=source.get("id", ""), title=source.get("title", "").replace("|", "\\|"),
                type=source.get("type", "").replace("|", "\\|"),
                domain=source.get("domain", "").replace("|", "\\|"),
                topics=source.get("topics", "").replace("|", "\\|"),
                status=source.get("status", ""), checked=source.get("checked_at", ""),
                url=source.get("url", ""), origin=source.get("origin_swarm", "")))
    else:
        lines.append("- No curated sources yet.")
    lines += ["", "## Reusable agent profiles", ""]
    if store["profiles"]:
        for profile in store["profiles"]:
            lines.append(f"- **{profile.get('name')}** ({profile.get('kind')}, `{profile.get('model')}`) — "
                         f"{profile.get('summary')} [origin: {profile.get('origin_swarm')}]")
    else:
        lines.append("- No approved profiles yet.")
    lines += ["", "## Calibration notes", ""]
    if store["calibration"]:
        for item in store["calibration"]:
            lines.append(f"- {item.get('note', '')} [origin: {item.get('origin_swarm', '')}]")
    else:
        lines.append("- No approved calibration notes yet.")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Propose or explicitly apply curated swarm memory updates.")
    parser.add_argument("swarm", type=Path)
    parser.add_argument("--memory-dir", type=Path, default=REPOSITORY_ROOT / "memory",
                        help="global memory directory (default: repository memory/)")
    parser.add_argument("--apply", action="store_true", help="apply proposal (also requires --approve)")
    parser.add_argument("--approve", action="store_true", help="confirm human/coordinator approval to apply")
    parser.add_argument("--calibration-note", action="append", default=[],
                        help="repeatable approved calibration observation to store")
    args = parser.parse_args(argv)
    try:
        candidate = proposal(args.swarm)
    except (OSError, InputError, json.JSONDecodeError) as exc:
        print(f"Could not build memory proposal: {exc}", file=sys.stderr)
        return 1
    proposal_path = args.swarm / "reports" / "memory-proposal.json"
    write_json(proposal_path, candidate)
    print(f"Wrote proposal: {proposal_path}")
    if not args.apply:
        return 0
    if not args.approve:
        print("--apply requires --approve; memory was not changed.", file=sys.stderr)
        return 1
    if args.calibration_note and not candidate["approved"]:
        print("Calibration notes require an approved swarm; memory was not changed.", file=sys.stderr)
        return 1
    try:
        store_path = args.memory_dir / "sources.json"
        store = apply_proposal(load_store(store_path), candidate, args.calibration_note)
        write_json(store_path, store)
        args.memory_dir.mkdir(parents=True, exist_ok=True)
        (args.memory_dir / "index.md").write_text(render_index(store), encoding="utf-8")
    except (OSError, InputError, json.JSONDecodeError) as exc:
        print(f"Could not update memory: {exc}", file=sys.stderr)
        return 1
    print(f"Updated {args.memory_dir / 'sources.json'} and {args.memory_dir / 'index.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
