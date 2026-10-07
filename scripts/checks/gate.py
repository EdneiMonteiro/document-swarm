"""Apply the deterministic swarm approval gate to constrained YAML or JSON."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

# Allow ``python3 /repo/scripts/checks/gate.py`` from any cwd.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.checks.common import (
    GRADE_INDEX, InputError, SCALE, load_data, normalize_grade, parse_data, write_json_atomic,
)
from scripts.checks.lint_agents import frontmatter_text
from scripts.checks.pdf_contract import verify_pdf_inspections
from scripts.checks.presentation_contract import verify_presentation

EDITORIAL_SURFACES = ("titles", "openings", "body", "captions", "conclusions")
CRITICAL_SEVERITIES = {"critical", "critico", "crítico"}


def requires_editorial(data: dict[str, Any]) -> bool:
    version = re.match(r"^(\d+)\.(\d+)(?:\.|$)", str(data.get("skill_version", "")))
    return ("editorial" in data or data.get("quality_contract") == "editorial-v1"
            or bool(version and tuple(map(int, version.groups())) >= (3, 2)))


def required_text(item: dict[str, Any], key: str) -> str:
    text = item.get(key)
    if not isinstance(text, str) or not text.strip():
        raise InputError(f"editorial requires non-empty {key}")
    return text


def artifact_descriptor(item: Any) -> tuple[str, str]:
    if not isinstance(item, dict):
        raise InputError("editorial artifact must be an object")
    name = required_text(item, "path").replace("\\", "/")
    logical = PurePosixPath(name)
    if (any(ord(character) < 32 for character in name)
            or logical.is_absolute() or PureWindowsPath(name).drive or ".." in logical.parts
            or not logical.parts or logical.parts[0] not in {"output", "reports"}):
        raise InputError("editorial artifact must be inside swarm output/ or reports/")
    digest = item.get("sha256")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise InputError("editorial artifact requires a lowercase SHA-256")
    return logical.as_posix(), digest


def editorial_blockers(data: dict[str, Any], cycle: int, required: bool) -> list[dict[str, str]]:
    if not required:
        return []
    editorial = data.get("editorial")
    if (not isinstance(editorial, dict) or type(editorial.get("schema_version")) is not int
            or editorial["schema_version"] != 1):
        raise InputError("a structured editorial-v1 review is required")
    if editorial.get("scope") != "full_document" or type(editorial.get("cycle")) is not int or editorial["cycle"] != cycle:
        raise InputError("editorial review must cover the full current document and current cycle")
    reviewer = required_text(editorial, "reviewer")
    if not re.fullmatch(r"reviewer-[A-Za-z0-9_-]+", reviewer):
        raise InputError("editorial reviewer must use its declared reviewer name")
    artifact_descriptor(editorial.get("text"))
    artifacts = editorial.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise InputError("editorial review must identify the actual delivery artifacts")
    paths = [artifact_descriptor(item)[0] for item in artifacts]
    if len(set(paths)) != len(paths):
        raise InputError("duplicate editorial delivery artifact")
    surfaces = editorial.get("surfaces")
    if (not isinstance(surfaces, list)
            or not all(isinstance(item, dict) and isinstance(item.get("surface"), str) for item in surfaces)
            or {item["surface"] for item in surfaces} != set(EDITORIAL_SURFACES)):
        raise InputError(f"editorial review must cover {', '.join(EDITORIAL_SURFACES)}")
    if len(surfaces) != len(EDITORIAL_SURFACES):
        raise InputError("duplicate editorial surface")
    blocked = []
    for item in surfaces:
        if not isinstance(item, dict):
            raise InputError("editorial surface must be an object")
        surface = item["surface"]
        if "not_applicable" in item:
            if surface != "captions" or "grade" in item:
                raise InputError("only absent captions may be marked not_applicable without a grade")
            required_text(item, "not_applicable")
            continue
        grade = normalize_grade(item.get("grade"))
        for key in ("location", "quote", "justification"):
            required_text(item, key)
        if not isinstance(item.get("action"), str):
            raise InputError("editorial surface requires an action string")
        if GRADE_INDEX[grade] < GRADE_INDEX["A"]:
            required_text(item, "action")
            blocked.append({"kind": "editorial", "name": surface, "grade": grade, "reviewer": reviewer})
    findings = editorial.get("findings")
    if not isinstance(findings, list):
        raise InputError("editorial findings must be explicitly recorded as a list")
    for item in findings:
        if not isinstance(item, dict) or item.get("severity") not in {"blocking", "minor"}:
            raise InputError("editorial finding severity must be blocking or minor")
        for key in ("location", "quote", "reason", "action"):
            required_text(item, key)
        if item["severity"] == "blocking":
            blocked.append({"kind": "editorial", "name": item["location"], "grade": "", "reviewer": reviewer})
    return blocked


def verify_editorial_artifacts(data: dict[str, Any], swarm: Path, brief: dict[str, Any]) -> None:
    """Check declared evidence, never assign a writing-quality grade."""
    editorial = data["editorial"]
    root = swarm.resolve(strict=True)

    def resolve(name: str) -> Path:
        destination = root.joinpath(*PurePosixPath(name).parts).resolve(strict=True)
        if root not in destination.parents:
            raise InputError(f"editorial artifact escapes swarm: {name}")
        return destination

    def checked(item: dict[str, Any], *, text: bool = False) -> str | None:
        name, digest = artifact_descriptor(item)
        destination = resolve(name)
        before = destination.stat()
        if not destination.is_file() or before.st_size > (10 if text else 100) * 1024 * 1024:
            raise InputError(f"editorial artifact is invalid or exceeds its size limit: {name}")
        calculated = hashlib.sha256()
        chunks = []
        with destination.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                calculated.update(chunk)
                if text:
                    chunks.append(chunk)
        after = destination.stat()
        if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
            raise InputError(f"editorial artifact changed during verification: {name}")
        if calculated.hexdigest() != digest:
            raise InputError(f"editorial review is stale; artifact changed: {name}")
        return b"".join(chunks).decode("utf-8") if text else None

    expected = brief.get("deliverables")
    if not isinstance(expected, list) or not expected or not all(isinstance(item, str) and item.strip() for item in expected):
        raise InputError("brief must list the final deliverables for editorial review")
    expected_paths = {artifact_descriptor({"path": item, "sha256": "0" * 64})[0] for item in expected}
    actual_paths = {artifact_descriptor(item)[0] for item in editorial["artifacts"]}
    if expected_paths != actual_paths or len(expected_paths) != len(expected):
        raise InputError("editorial artifact set must exactly match brief deliverables")
    if brief.get("editorial_reviewer") != editorial["reviewer"]:
        raise InputError("editorial assessment must come from the reviewer assigned in the brief")
    report_name = f"reports/cycle-{editorial['cycle']:02d}-{editorial['reviewer']}.json"
    report_path = resolve(report_name)
    if report_path.stat().st_size > 2 * 1024 * 1024:
        raise InputError("individual editorial review exceeds size limit")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if (not isinstance(report, dict) or type(report.get("schema_version")) is not int or report["schema_version"] != 1
            or type(report.get("cycle")) is not int or report["cycle"] != editorial["cycle"]
            or report.get("reviewer") != editorial["reviewer"] or report.get("editorial") != editorial):
        raise InputError("consolidated editorial assessment differs from the individual reviewer JSON")
    captured_text = checked(editorial["text"], text=True)
    if captured_text is None:
        raise InputError("reviewed editorial text was not captured")
    source = " ".join(captured_text.split())
    for item in editorial["artifacts"]:
        checked(item)
    for item in [*editorial["surfaces"], *editorial["findings"]]:
        if "quote" in item and " ".join(item["quote"].split()) not in source:
            raise InputError(f"editorial evidence quote is absent from the reviewed text: {item.get('location', '')}")


def evaluate_current(data: Any, swarm: Path | None) -> dict[str, Any]:
    brief = {}
    if swarm is not None and (swarm / "brief.md").exists():
        brief = frontmatter_text((swarm / "brief.md").read_text(encoding="utf-8"))
    if "quality_contract" in brief and brief["quality_contract"] != "editorial-v1":
        raise InputError("unknown brief quality contract")
    result = evaluate(data, require_editorial=requires_editorial(brief))
    if requires_editorial(data) or requires_editorial(brief):
        if swarm is None or not brief:
            raise InputError("editorial validation requires the swarm brief and artifacts")
        verify_editorial_artifacts(data, swarm, brief)
    if "pdf_inspections" in data and (swarm is None or not brief):
        raise InputError("PDF validation requires the swarm brief and artifacts")
    if "presentation" in data and (swarm is None or not brief):
        raise InputError("presentation validation requires the swarm brief and artifacts")
    if swarm is not None:
        result["blocked"].extend(verify_pdf_inspections(data, swarm, brief))
        result["blocked"].extend(verify_presentation(data, swarm, brief))
        if result["blocked"]:
            result["outcome"] = "escalate" if result["cycle"] >= result["max_cycles"] else "rejected"
    return result


def value(item: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in item:
            return item[name]
    return None


def items_below(section: Any, labels: tuple[str, ...]) -> list[dict[str, str]]:
    if section is None:
        return []
    if isinstance(section, dict):
        source = []
        for name, item in section.items():
            if isinstance(item, dict):
                source.append({labels[0]: name, **item})
            else:
                source.append({labels[0]: name, "grade": item})
    else:
        source = section
    if not isinstance(source, (list, tuple)):
        raise InputError("slides/deck_dimensions must be a list or mapping")
    blocked = []
    for ordinal, item in enumerate(source, 1):
        if not isinstance(item, dict):
            raise InputError("grade item must be a mapping")
        grade = normalize_grade(value(item, "nota_minima", "grade", "minimum_grade", "nota"))
        label = value(item, *labels) or str(ordinal)
        blocks = value(item, "bloqueia", "blocks")
        if blocks is not None and not isinstance(blocks, bool):
            raise InputError("slide/dimension bloqueia/blocks must be boolean when present")
        if GRADE_INDEX[grade] < GRADE_INDEX["A"] or blocks:
            blocked.append({"kind": labels[0], "name": str(label), "grade": grade})
    return blocked


def evaluate(data: Any, *, require_editorial: bool = False) -> dict[str, Any]:
    """Return a machine-readable gate decision and its blocking facts."""
    if not isinstance(data, dict):
        raise InputError("review root must be a mapping")
    if "quality_contract" in data and data["quality_contract"] != "editorial-v1":
        raise InputError("unknown quality contract")
    cycle = value(data, "cycle", "ciclo")
    maximum = value(data, "max_cycles", "max_ciclos", "maximum_cycles")
    if type(cycle) is not int or type(maximum) is not int or cycle < 1 or maximum < 1:
        raise InputError("cycle/max_cycles must be positive integers")
    topics = value(data, "topics", "topicos")
    if not isinstance(topics, list) or not topics:
        raise InputError("topics/topicos must be a non-empty list")
    blocked: list[dict[str, str]] = []
    for item in topics:
        if not isinstance(item, dict):
            raise InputError("each topic must be a mapping")
        grade = normalize_grade(value(item, "nota_minima", "minimum_grade", "grade", "nota"))
        name = value(item, "topico", "topic", "name")
        reviewer = value(item, "revisor_da_minima", "minimum_reviewer", "reviewer")
        if not name or not reviewer:
            raise InputError("topic requires topico/topic and revisor_da_minima/reviewer")
        blocks = value(item, "bloqueia", "blocks")
        if not isinstance(blocks, bool):
            raise InputError("topic requires boolean bloqueia/blocks")
        if GRADE_INDEX[grade] < GRADE_INDEX["A"] or blocks:
            blocked.append({"kind": "topic", "name": str(name), "grade": grade, "reviewer": str(reviewer)})
    duck = value(data, "rubberduck", "rubber_duck")
    if not isinstance(duck, dict):
        raise InputError("rubberduck is required and must be a mapping")
    critical = value(duck, "critico", "critical")
    findings = value(duck, "achados", "findings")
    if not isinstance(critical, bool) or not isinstance(findings, list):
        raise InputError("rubberduck requires boolean critico/critical and list achados/findings")
    # A critical finding vetoes the delivery.  The flag and the findings are two statements of one fact: a
    # matrix that says "not critical" beside a finding marked critical has dropped the veto, so it is refused.
    if not critical and any(isinstance(item, dict) and str(value(item, "severity", "severidade") or "").strip().casefold()
                            in CRITICAL_SEVERITIES for item in findings):
        raise InputError("rubberduck.critico is false but a finding is marked critical; the veto cannot be dropped")
    if critical:
        blocked.append({"kind": "rubberduck", "name": "critical finding", "grade": ""})
    blocked.extend(editorial_blockers(data, cycle, require_editorial or requires_editorial(data)))
    # Historical deck reviews must retain their original blocking criteria.
    blocked.extend(items_below(value(data, "slides", "slide_reviews"), ("slide", "name", "titulo", "title")))
    blocked.extend(items_below(value(data, "deck_dimensions", "deckDimensions"), ("dimension", "dimensao", "name")))
    outcome = "approved" if not blocked else "escalate" if cycle >= maximum else "rejected"
    return {"schema_version": 1, "cycle": cycle, "max_cycles": maximum, "outcome": outcome, "blocked": blocked}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate structured swarm review grades (A or higher is required).")
    parser.add_argument("review", type=Path, help="cycle review .yaml, .yml, or JSON")
    parser.add_argument("--output", type=Path, help="optional recorded gate result, bound to the exact review bytes")
    args = parser.parse_args(argv)
    if args.output and args.output.resolve() == args.review.resolve():
        print("INVALID: output must not replace the review input", file=sys.stderr)
        return 3
    raw = None
    result = None
    error = None
    try:
        raw = args.review.read_bytes()
        data = parse_data(raw.decode("utf-8"))
        swarm = args.review.resolve().parent.parent if args.review.parent.name.casefold() == "reports" else None
        result = evaluate_current(data, swarm)
        code = {"approved": 0, "rejected": 1, "escalate": 2}[result["outcome"]]
    except (OSError, UnicodeError, InputError, json.JSONDecodeError) as exc:
        error = str(exc)
        code = 3
        print(f"INVALID: {error}", file=sys.stderr)
    except Exception as exc:
        # The gate decides approval.  An internal failure is not a verdict: it must not leave through Python's
        # default exit status 1, which means "rejected", nor be mistaken for one.
        error = f"internal error: {type(exc).__name__}: {exc}"
        code = 3
        print(f"INVALID: {error}", file=sys.stderr)
    if result is not None:
        print(json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True))
    if args.output:
        try:
            write_json_atomic(args.output, {
                "schema_version": 1,
                "recorded_at": datetime.now(timezone.utc).isoformat(),
                "review_file": args.review.name,
                "review_sha256": hashlib.sha256(raw).hexdigest() if raw is not None else None,
                "exit_code": code,
                "result": result,
                "error": error,
            })
        except (OSError, ValueError) as exc:
            print(f"INVALID: cannot record gate result: {exc}", file=sys.stderr)
            return 3
    return code


if __name__ == "__main__":
    raise SystemExit(main())
