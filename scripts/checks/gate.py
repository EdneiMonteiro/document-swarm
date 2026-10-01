"""Apply the deterministic swarm approval gate to constrained YAML or JSON."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# Allow ``python3 /repo/scripts/checks/gate.py`` from any cwd.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.checks.common import InputError, load_data

SCALE = ("D-", "D", "D+", "C-", "C", "C+", "B-", "B", "B+", "A-", "A", "A+")
GRADE_INDEX = {grade: index for index, grade in enumerate(SCALE)}


def value(item: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in item:
            return item[name]
    return None


def normalize_grade(grade: Any) -> str:
    if not isinstance(grade, str) or grade.strip().upper() not in GRADE_INDEX:
        raise InputError(f"invalid grade: {grade!r}; expected one of {', '.join(SCALE)}")
    return grade.strip().upper()


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


def evaluate(data: Any) -> dict[str, Any]:
    """Return a machine-readable gate decision and its blocking facts."""
    if not isinstance(data, dict):
        raise InputError("review root must be a mapping")
    cycle = value(data, "cycle", "ciclo")
    maximum = value(data, "max_cycles", "max_ciclos", "maximum_cycles")
    if not isinstance(cycle, int) or not isinstance(maximum, int) or cycle < 1 or maximum < 1:
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
    # Historical deck reviews must retain their original blocking criteria.
    blocked.extend(items_below(value(data, "slides", "slide_reviews"), ("slide", "name", "titulo", "title")))
    blocked.extend(items_below(value(data, "deck_dimensions", "deckDimensions"), ("dimension", "dimensao", "name")))
    outcome = "approved" if not blocked else "escalate" if cycle >= maximum else "rejected"
    return {"schema_version": 1, "cycle": cycle, "max_cycles": maximum, "outcome": outcome, "blocked": blocked}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate structured swarm review grades (A or higher is required).")
    parser.add_argument("review", type=Path, help="cycle review .yaml, .yml, or JSON")
    args = parser.parse_args(argv)
    try:
        result = evaluate(load_data(args.review))
    except (OSError, InputError) as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 3
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return {"approved": 0, "rejected": 1, "escalate": 2}[result["outcome"]]


if __name__ == "__main__":
    raise SystemExit(main())
