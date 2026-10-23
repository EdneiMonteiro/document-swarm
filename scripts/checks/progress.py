"""Read-only, stdlib projection of swarm artifacts for the visual monitor."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.checks.common import InputError, parse_data
from scripts.checks.gate import GRADE_INDEX, SCALE, evaluate, evaluate_current, normalize_grade, value
from scripts.checks.lint_agents import frontmatter_text

MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_ARTIFACTS = 1000
KINDS = {"author", "reviewer", "coordinator", "rubber-duck"}


def bounded_file(root: Path, path: Path) -> bytes:
    resolved = path.resolve()
    if resolved != root and root not in resolved.parents:
        raise InputError(f"artifact escapes swarm: {path.name}")
    if not resolved.is_file() or resolved.stat().st_size > MAX_FILE_BYTES:
        raise InputError(f"artifact is missing or exceeds size limit: {path.name}")
    with resolved.open("rb") as stream:
        raw = stream.read(MAX_FILE_BYTES + 1)
    if len(raw) > MAX_FILE_BYTES:
        raise InputError(f"artifact exceeds size limit: {path.name}")
    return raw


def mapping(raw: bytes, name: str) -> dict[str, Any]:
    data = parse_data(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise InputError(f"{name}: expected an object")
    return data


def integer(value: Any, name: str) -> int:
    if type(value) is not int or value < 1:
        raise InputError(f"{name} must be a positive integer")
    return value


def text(value: Any, name: str, *, empty: bool = False) -> str:
    if not isinstance(value, str) or len(value) > 12000 or (not empty and not value.strip()):
        raise InputError(f"{name} must be a bounded {'optional' if empty else 'non-empty'} string")
    return value


def reviewer_data(raw: bytes, cycle: int) -> dict[str, Any]:
    data = mapping(raw, "reviewer report")
    if data.get("schema_version") != 1 or integer(data.get("cycle"), "cycle") != cycle:
        raise InputError("reviewer report has an unsupported schema or a different cycle")
    reviewer = text(data.get("reviewer"), "reviewer")
    topics = data.get("topics")
    if not isinstance(topics, list) or not topics or len(topics) > 1000:
        raise InputError("reviewer topics must be a non-empty bounded list")
    seen = set()
    rows = []
    for item in topics:
        if not isinstance(item, dict):
            raise InputError("reviewer topic must be an object")
        topic = text(item.get("topic"), "topic")
        if topic in seen:
            raise InputError(f"duplicate topic {topic}")
        seen.add(topic)
        rows.append({
            "topic": topic,
            "reviewer": reviewer,
            "grade": normalize_grade(item.get("grade")),
            "justification": text(item.get("justification"), "justification"),
            "action": text(item.get("action"), "action", empty=True),
        })
    return {"reviewer": reviewer, "rows": rows}


def snapshot(swarm: Path) -> dict[str, Any]:
    root = swarm.resolve(strict=True)
    captured: dict[Path, bytes] = {}
    quality_files: set[Path] = set()

    def read(path: Path) -> bytes:
        if path not in captured:
            captured[path] = bounded_file(root, path)
        return captured[path]

    brief_path = root / "brief.md"
    brief_raw = read(brief_path)
    brief_text = brief_raw.decode("utf-8")
    brief = frontmatter_text(brief_text)
    swarm_id = text(brief.get("swarm_id"), "swarm_id")
    maximum = integer(brief.get("max_cycles"), "max_cycles")
    heading = re.search(r"(?m)^#\s+(.+)$", brief_text)
    result: dict[str, Any] = {
        "schema_version": 1,
        "swarm_id": swarm_id,
        "title": heading.group(1).strip() if heading else swarm_id,
        "skill_version": str(brief.get("skill_version", "not recorded")),
        "max_cycles": maximum,
        "monitor_enabled": brief.get("monitor", True) is not False,
        "demo": brief.get("demo") is True,
        "grade_scale": list(SCALE),
        "agents": [],
        "cycles": [],
        "sources": {"status": "pending", "counts": {}},
        "artifacts": [],
        "warnings": [],
    }
    warnings = result["warnings"]
    artifact_paths: set[Path] = {brief_path}

    def load(path: Path) -> tuple[bytes, dict[str, Any]]:
        quality_files.add(path)
        raw = read(path)
        return raw, mapping(raw, path.name)

    agent_paths = sorted((root / "agents").rglob("*.md"))
    if len(agent_paths) > MAX_ARTIFACTS:
        raise InputError("too many agent declarations")
    agent_ids = set()
    for path in agent_paths:
        try:
            data = frontmatter_text(read(path).decode("utf-8"))
            name = text(data.get("name"), "agent name")
            if name in agent_ids:
                raise InputError(f"duplicate agent name {name}")
            if data.get("swarm") != swarm_id or data.get("kind") not in KINDS:
                raise InputError("agent swarm or kind does not match this document")
            agent_ids.add(name)
            result["agents"].append({
                "id": name, "kind": data["kind"],
                "role": text(data.get("role", data["kind"]), "agent role"),
                "declared_model": text(data.get("model"), "declared model"),
                "path": path.relative_to(root).as_posix(),
            })
            artifact_paths.add(path)
        except (OSError, UnicodeError, InputError) as exc:
            warnings.append(f"{path.name}: {exc}")

    reports = root / "reports"
    cycles: dict[int, dict[str, Any]] = {}
    files = sorted(reports.glob("cycle-*"))
    if len(files) > MAX_ARTIFACTS:
        raise InputError("too many cycle artifacts")
    for path in files:
        match = re.fullmatch(r"cycle-(\d+)-(review\.ya?ml|reviewer-.+\.json|gate\.json|tables-check\.json)", path.name)
        if not match:
            continue
        number = integer(int(match.group(1)), "cycle number")
        cycle = cycles.setdefault(number, {
            "cycle": number, "topics": [], "reviews": [], "issues": [],
            "gate": {"status": "not_recorded"}, "tables": {"status": "pending"},
            "consistent": True,
        })
        try:
            raw, data = load(path)
            artifact_paths.add(path)
            suffix = match.group(2)
            if suffix.startswith("review."):
                if "review_sha256" in cycle:
                    raise InputError("multiple consolidated reviews for the same cycle")
                integer(value(data, "cycle", "ciclo"), "review cycle")
                integer(value(data, "max_cycles", "max_ciclos", "maximum_cycles"), "review max_cycles")
                decision = evaluate(data)
                if decision["cycle"] != number:
                    raise InputError("review cycle does not match its filename")
                cycle["review_sha256"] = hashlib.sha256(raw).hexdigest()
                cycle["review_file"] = path.name
                cycle["computed"] = decision
                cycle["review_data"] = data
                seen = set()
                for item in value(data, "topics", "topicos"):
                    name = text(value(item, "topico", "topic", "name"), "topic")
                    if name in seen:
                        raise InputError(f"duplicate consolidated topic {name}")
                    seen.add(name)
                    cycle["topics"].append({
                        "id": name, "title": text(item.get("title", name), "topic title"),
                        "grade": normalize_grade(value(item, "nota_minima", "minimum_grade", "grade", "nota")),
                        "reviewer": text(value(item, "revisor_da_minima", "minimum_reviewer", "reviewer"), "reviewer"),
                        "blocks": value(item, "bloqueia", "blocks"),
                    })
            elif suffix.startswith("reviewer-"):
                report = reviewer_data(raw, number)
                if any(row["reviewer"] == report["reviewer"] for row in cycle["reviews"]):
                    raise InputError(f"duplicate reviewer {report['reviewer']}")
                cycle["reviews"].extend(report["rows"])
            elif suffix == "gate.json":
                cycle["recorded_gate"] = data
            else:
                failures = data.get("failures")
                if type(failures) is not int or failures < 0:
                    raise InputError("table failures must be a non-negative integer")
                cycle["tables"] = {"status": "fail" if failures else "ok", "failures": failures}
        except (OSError, UnicodeError, InputError) as exc:
            cycle["issues"].append(f"{path.name}: {exc}")
            cycle["consistent"] = False

    for number, cycle in sorted(cycles.items()):
        record = cycle.pop("recorded_gate", None)
        decision = cycle.pop("computed", None)
        review_data = cycle.pop("review_data", None)
        editorial_error = None
        if review_data is not None and number == max(cycles):
            try:
                decision = evaluate_current(review_data, root)
            except (InputError, OSError, UnicodeError, json.JSONDecodeError) as exc:
                editorial_error = str(exc)
                cycle["issues"].append(editorial_error)
        if record is not None:
            if record.get("schema_version") != 1:
                cycle["gate"] = {"status": "invalid", "error": "unsupported gate record"}
            elif not decision or record.get("review_sha256") != cycle.get("review_sha256"):
                cycle["gate"] = {"status": "stale", "error": "review is missing, invalid or changed"}
            elif (record.get("review_file") != cycle.get("review_file") or record.get("result") != decision
                  or type(record.get("exit_code")) is not int
                  or record["exit_code"] != {"approved": 0, "rejected": 1, "escalate": 2}[decision["outcome"]]):
                cycle["gate"] = {"status": "invalid", "error": "gate record disagrees with the review"}
            else:
                cycle["gate"] = {
                    "status": "verified", "outcome": decision["outcome"],
                    "exit_code": record["exit_code"], "blocked": decision["blocked"],
                    "recorded_at": record.get("recorded_at"),
                }
        if editorial_error is not None:
            cycle["gate"] = {"status": "stale" if record else "invalid", "error": editorial_error}
        by_topic: dict[str, list[dict[str, Any]]] = {}
        for row in cycle["reviews"]:
            by_topic.setdefault(row["topic"], []).append(row)
        consolidated = {item["id"]: item for item in cycle["topics"]}
        for topic, rows in by_topic.items():
            minimum = min(rows, key=lambda row: GRADE_INDEX[row["grade"]])["grade"]
            item = consolidated.get(topic)
            if item and (item["grade"] != minimum or not any(
                row["reviewer"] == item["reviewer"] and row["grade"] == minimum for row in rows
            )):
                cycle["issues"].append(f"{topic}: individual minimum disagrees with consolidated review")
            elif not item and decision:
                cycle["issues"].append(f"{topic}: individual review has no consolidated topic")
        if cycle["issues"] or cycle["gate"]["status"] in {"stale", "invalid"}:
            cycle["consistent"] = False
        cycle["individual_reviews"] = "recorded" if cycle["reviews"] else "not_recorded"
        result["cycles"].append(cycle)

    source_path = root / "sources" / "sources-check.json"
    if source_path.exists():
        try:
            _, data = load(source_path)
            counts = data.get("counts")
            if not isinstance(counts, dict) or not counts:
                raise InputError("source counts are missing")
            clean = {}
            for key in ("ok", "redirect", "warn", "fail"):
                count = counts.get(key, 0)
                if type(count) is not int or count < 0:
                    raise InputError(f"invalid source count {key}")
                clean[key] = count
            result["sources"] = {"status": "fail" if clean["fail"] else "warn" if clean["warn"] else "ok", "counts": clean}
            artifact_paths.add(source_path)
        except (OSError, UnicodeError, InputError) as exc:
            result["sources"] = {"status": "invalid", "counts": {}}
            warnings.append(f"sources-check.json: {exc}")

    extra = (list(reports.glob("cycle-*.md")) + list(reports.glob("cycle-*-nomenclature.json"))
             + list((root / "output").rglob("*.md")))
    if len(extra) + len(artifact_paths) > MAX_ARTIFACTS:
        raise InputError("too many displayable artifacts")
    artifact_paths.update(extra)
    if (reports / "final-report.md").exists():
        artifact_paths.add(reports / "final-report.md")
    for path in sorted(artifact_paths):
        try:
            raw = read(path)
            relative = path.relative_to(root).as_posix()
            result["artifacts"].append({
                "id": hashlib.sha256(relative.encode("utf-8")).hexdigest()[:24],
                "path": relative, "name": path.name, "bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
            })
        except (OSError, InputError) as exc:
            warnings.append(f"{path.name}: {exc}")
    for path in sorted(quality_files):
        try:
            changed = path in captured and bounded_file(root, path) != captured[path]
        except (OSError, InputError):
            changed = True
        if not changed:
            continue
        message = f"{path.name}: changed during observation; refresh required"
        warnings.append(message)
        match = re.match(r"cycle-(\d+)-", path.name)
        affected = [item for item in result["cycles"] if not match or item["cycle"] == int(match.group(1))]
        for item in affected:
            item["consistent"] = False
            item["issues"].append(message)
            item["gate"] = {"status": "stale", "error": message}
        if path == source_path:
            result["sources"]["status"] = "stale"
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read swarm artifacts for the local progress monitor; never modifies the swarm.")
    parser.add_argument("swarm", type=Path)
    args = parser.parse_args(argv)
    try:
        data = snapshot(args.swarm)
    except (OSError, UnicodeError, InputError) as exc:
        print(f"ERROR: cannot read progress artifacts: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(data, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
