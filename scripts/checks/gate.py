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
REGRESSION_CAUSES = ("text_changed_regression", "factual_or_cross_table_contradiction")


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
        if item.get("severity") == "minor" and GRADE_INDEX[grade] < GRADE_INDEX["A"]:
            raise InputError("editorial surface with minor severity cannot be graded below A")
        if GRADE_INDEX[grade] < GRADE_INDEX["A"]:
            required_text(item, "action")
            blocked.append({"kind": "editorial", "name": surface, "grade": grade, "reviewer": reviewer})
    findings = editorial.get("findings")
    if not isinstance(findings, list):
        raise InputError("editorial findings must be explicitly recorded as a list")
    has_blocking_finding = False
    for item in findings:
        if not isinstance(item, dict) or item.get("severity") not in {"blocking", "minor"}:
            raise InputError("editorial finding severity must be blocking or minor")
        for key in ("location", "quote", "reason", "action"):
            required_text(item, key)
        if item["severity"] == "blocking":
            has_blocking_finding = True
            blocked.append({"kind": "editorial", "name": item["location"], "grade": "", "reviewer": reviewer})
    if findings and not has_blocking_finding and blocked:
        raise InputError("editorial surface cannot be graded below A when all editorial findings are minor")
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
    findings = editorial.get("findings", [])
    only_minor_findings = bool(findings) and all(item.get("severity") == "minor" for item in findings)
    for row in report.get("topics", []):
        if not isinstance(row, dict) or "grade" not in row:
            continue
        grade = normalize_grade(row.get("grade"))
        if row.get("severity") == "minor" and GRADE_INDEX[grade] < GRADE_INDEX["A"]:
            raise InputError("minor topic finding cannot produce a grade below A")
        if only_minor_findings and GRADE_INDEX[grade] < GRADE_INDEX["A"]:
            raise InputError("editorial reviewer cannot grade a topic below A when all editorial findings are minor")


def _normalized_cycle_text(root: Path, cycle: int, review_or_report: dict[str, Any] | None = None) -> str | None:
    candidates: list[Path] = [root / "reports" / f"cycle-{cycle:02d}-editorial-text.txt"]
    if isinstance(review_or_report, dict):
        editorial = review_or_report.get("editorial")
        if isinstance(editorial, dict) and isinstance(editorial.get("text"), dict):
            path_text = editorial["text"].get("path")
            if isinstance(path_text, str) and path_text.strip():
                candidates.append(root.joinpath(*PurePosixPath(path_text.replace("\\", "/")).parts))
    for candidate in candidates:
        try:
            resolved = candidate.resolve(strict=True)
            if root in resolved.parents and resolved.is_file() and resolved.stat().st_size <= 10 * 1024 * 1024:
                return " ".join(resolved.read_text(encoding="utf-8").split())
        except (OSError, UnicodeError):
            continue
    return None


def _duck_contested(prev_review: dict[str, Any] | None, reviewer: str, target_id: str) -> bool:
    if not isinstance(prev_review, dict):
        return False
    duck = value(prev_review, "rubberduck", "rubber_duck")
    if not isinstance(duck, dict):
        return False
    findings = value(duck, "achados", "findings")
    critical = bool(value(duck, "critico", "critical"))
    if isinstance(findings, list) and findings:
        for item in findings:
            if isinstance(item, dict):
                blob = " ".join(str(v) for v in item.values() if v is not None)
                reviewer_match = (
                    not any(k in item for k in ("reviewer", "target", "alvo"))
                    or reviewer in blob
                )
                target_match = (
                    not any(k in item for k in ("topic", "topico", "topics", "affected_topics", "surface"))
                    or target_id in blob
                )
                if reviewer_match and target_match and (reviewer in blob or target_id in blob):
                    return True
            elif isinstance(item, str) and (reviewer in item or target_id in item):
                return True
        return False
    return critical


def verify_regression_proof(
    regression: Any,
    *,
    target: str,
    reviewer: str,
    brief: dict[str, Any],
    curr_text: str | None,
    prev_text: str | None,
    editorial_surface: bool = False,
) -> None:
    if not isinstance(regression, dict):
        raise InputError(f"grade downgrade from >= A requires a structured regression block for {target}")
    cause = regression.get("cause")
    if cause not in REGRESSION_CAUSES:
        raise InputError(f"regression cause for {target} must be one of {', '.join(REGRESSION_CAUSES)}")
    quote = required_text(regression, "quote")
    required_text(regression, "evidence")
    norm_quote = " ".join(quote.split())
    if curr_text is not None and norm_quote not in curr_text:
        raise InputError(f"regression quote for {target} is absent from the current reviewed text")
    if cause == "text_changed_regression":
        previous_quote = regression.get("previous_quote")
        if not isinstance(previous_quote, str):
            raise InputError(f"text_changed_regression for {target} requires a previous_quote string")
        norm_prev = " ".join(previous_quote.split())
        if norm_prev and norm_prev == norm_quote:
            raise InputError(f"text_changed_regression for {target} requires quote to differ from previous_quote")
        if prev_text is not None:
            if norm_prev and norm_prev not in prev_text:
                raise InputError(f"previous_quote for {target} is absent from the previous cycle text")
            if norm_quote in prev_text:
                raise InputError(f"regression quote for {target} already existed unchanged in the previous cycle text")
    elif cause == "factual_or_cross_table_contradiction":
        if editorial_surface:
            raise InputError(
                f"editorial surface {target} cannot be downgraded from >= A on unchanged text without text_changed_regression"
            )
        if reviewer == brief.get("editorial_reviewer"):
            conflicting = required_text(regression, "conflicting_quote")
            norm_conflict = " ".join(conflicting.split())
            if norm_conflict == norm_quote:
                raise InputError(f"conflicting_quote for {target} must differ from quote")
            if curr_text is not None and norm_conflict not in curr_text:
                raise InputError(f"conflicting_quote for {target} is absent from the current reviewed text")


def verify_cycle_stability(data: dict[str, Any], swarm: Path, brief: dict[str, Any]) -> None:
    """Ensure previously approved (>= A) topics and surfaces are not downgraded without regression proof."""
    cycle = value(data, "cycle", "ciclo")
    if type(cycle) is not int or cycle <= 1:
        return
    root = swarm.resolve(strict=True)
    reports_dir = root / "reports"
    if not reports_dir.is_dir():
        return
    prev_cycle = cycle - 1
    prev_review: dict[str, Any] | None = None
    for suffix in (".yaml", ".yml", ".json"):
        candidate = reports_dir / f"cycle-{prev_cycle:02d}-review{suffix}"
        if candidate.is_file():
            loaded = load_data(candidate)
            if isinstance(loaded, dict):
                prev_review = loaded
            break
    curr_text = _normalized_cycle_text(root, cycle, data)
    prev_text = _normalized_cycle_text(root, prev_cycle, prev_review)
    checked_pairs: set[tuple[str, str]] = set()
    for curr_path in sorted(reports_dir.glob(f"cycle-{cycle:02d}-reviewer-*.json")):
        if curr_path.stat().st_size > 2 * 1024 * 1024:
            raise InputError(f"individual review exceeds size limit: {curr_path.name}")
        curr_report = json.loads(curr_path.read_text(encoding="utf-8"))
        if not isinstance(curr_report, dict):
            continue
        reviewer = str(curr_report.get("reviewer") or "")
        for row in curr_report.get("topics", []):
            if isinstance(row, dict) and "grade" in row:
                grade = normalize_grade(row.get("grade"))
                if row.get("severity") == "minor" and GRADE_INDEX[grade] < GRADE_INDEX["A"]:
                    raise InputError("minor topic finding cannot produce a grade below A")
        prev_path = reports_dir / f"cycle-{prev_cycle:02d}-{reviewer}.json"
        if not reviewer or not prev_path.is_file() or prev_path.stat().st_size > 2 * 1024 * 1024:
            continue
        prev_report = json.loads(prev_path.read_text(encoding="utf-8"))
        if not isinstance(prev_report, dict):
            continue
        local_prev_text = prev_text or _normalized_cycle_text(root, prev_cycle, prev_report)
        local_curr_text = curr_text or _normalized_cycle_text(root, cycle, curr_report)
        prev_topics = {
            str(item["topic"]): normalize_grade(item["grade"])
            for item in prev_report.get("topics", [])
            if isinstance(item, dict) and "topic" in item and "grade" in item
        }
        for row in curr_report.get("topics", []):
            if not isinstance(row, dict) or "topic" not in row or "grade" not in row:
                continue
            topic_id = str(row["topic"])
            curr_grade = normalize_grade(row["grade"])
            prev_grade = prev_topics.get(topic_id)
            checked_pairs.add((reviewer, topic_id))
            if (
                prev_grade is not None
                and GRADE_INDEX[prev_grade] >= GRADE_INDEX["A"]
                and GRADE_INDEX[curr_grade] < GRADE_INDEX["A"]
                and not _duck_contested(prev_review, reviewer, topic_id)
            ):
                verify_regression_proof(
                    row.get("regression"),
                    target=f"{reviewer}:{topic_id}",
                    reviewer=reviewer,
                    brief=brief,
                    curr_text=local_curr_text,
                    prev_text=local_prev_text,
                )
        prev_editorial = prev_report.get("editorial")
        curr_editorial = curr_report.get("editorial")
        if isinstance(prev_editorial, dict) and isinstance(curr_editorial, dict):
            prev_surfaces = {
                str(item["surface"]): normalize_grade(item["grade"])
                for item in prev_editorial.get("surfaces", [])
                if isinstance(item, dict) and "surface" in item and "grade" in item
            }
            for item in curr_editorial.get("surfaces", []):
                if not isinstance(item, dict) or "surface" not in item or "grade" not in item:
                    continue
                surface = str(item["surface"])
                curr_grade = normalize_grade(item["grade"])
                prev_grade = prev_surfaces.get(surface)
                if (
                    prev_grade is not None
                    and GRADE_INDEX[prev_grade] >= GRADE_INDEX["A"]
                    and GRADE_INDEX[curr_grade] < GRADE_INDEX["A"]
                    and not _duck_contested(prev_review, reviewer, surface)
                ):
                    verify_regression_proof(
                        item.get("regression"),
                        target=f"{reviewer}:editorial.{surface}",
                        reviewer=reviewer,
                        brief=brief,
                        curr_text=local_curr_text,
                        prev_text=local_prev_text,
                        editorial_surface=True,
                    )
    if isinstance(prev_review, dict):
        prev_consolidated = {
            str(value(item, "topico", "topic", "name")): normalize_grade(
                value(item, "nota_minima", "minimum_grade", "grade", "nota")
            )
            for item in (value(prev_review, "topics", "topicos") or [])
            if isinstance(item, dict) and value(item, "topico", "topic", "name")
        }
        for item in value(data, "topics", "topicos") or []:
            if not isinstance(item, dict):
                continue
            topic_id = str(value(item, "topico", "topic", "name") or "")
            reviewer = str(value(item, "revisor_da_minima", "minimum_reviewer", "reviewer") or "")
            curr_grade = normalize_grade(value(item, "nota_minima", "minimum_grade", "grade", "nota"))
            prev_grade = prev_consolidated.get(topic_id)
            if (
                (reviewer, topic_id) not in checked_pairs
                and prev_grade is not None
                and GRADE_INDEX[prev_grade] >= GRADE_INDEX["A"]
                and GRADE_INDEX[curr_grade] < GRADE_INDEX["A"]
                and not _duck_contested(prev_review, reviewer, topic_id)
            ):
                verify_regression_proof(
                    item.get("regression"),
                    target=f"{reviewer}:{topic_id}",
                    reviewer=reviewer,
                    brief=brief,
                    curr_text=curr_text,
                    prev_text=prev_text,
                )


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
    if swarm is not None:
        verify_cycle_stability(data, swarm, brief)
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
