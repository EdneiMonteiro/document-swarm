"""Generate a deterministic final-report.md skeleton from swarm artifacts."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

# Allow ``python3 /repo/scripts/checks/final_report.py`` from any cwd.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.checks.common import InputError, load_data
from scripts.checks.gate import evaluate


def find_artifacts(swarm: Path, name: str) -> list[Path]:
    return sorted(path for path in swarm.rglob(name) if ".venv" not in path.parts and "node_modules" not in path.parts)


def cycle_order(path: Path) -> tuple[int, str]:
    """Order cycle artifacts numerically instead of lexicographically."""
    match = re.search(r"cycle-(\d+)", path.name, re.I)
    return (int(match.group(1)) if match else -1, path.name)


def skill_version(brief: Path) -> str:
    if not brief.exists():
        return "unknown"
    match = re.search(r"(?im)^\s*(?:-\s*)?(?:\*\*)?skill_version(?:\*\*)?\s*:\s*`?([^`\n]+)", brief.read_text(encoding="utf-8"))
    return match.group(1).strip().strip("`'\"") if match else "unknown"


def collect_json(paths: list[Path], artifact_name: str) -> list[dict[str, Any]]:
    items = []
    if not paths:
        raise InputError(f"missing required {artifact_name} artifact")
    for path in paths:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise InputError(f"invalid {artifact_name} artifact {path}: {exc}") from exc
        if not isinstance(value, dict):
            raise InputError(f"invalid {artifact_name} artifact {path}: expected JSON object")
        value["_artifact"] = str(path)
        items.append(value)
    return items


def render(swarm: Path) -> str:
    """Derive the non-narrative portion of a final report deterministically."""
    reports = swarm / "reports"
    review_paths = sorted(reports.glob("cycle-*-review.y*ml"), key=cycle_order) if reports.exists() else []
    if not review_paths:
        raise InputError("missing required structured cycle review YAML")
    reviews = []
    for path in review_paths:
        try:
            reviews.append((path, load_data(path)))
        except (OSError, InputError) as exc:
            raise InputError(f"invalid review YAML {path}: {exc}") from exc
    gate_results = []
    for path, review in reviews:
        try:
            result = evaluate(review)
        except InputError as exc:
            raise InputError(f"invalid review YAML {path}: {exc}") from exc
        result["artifact"] = str(path.relative_to(swarm))
        gate_results.append(result)
    latest = reviews[-1][1] if reviews else {}
    if gate_results[-1]["outcome"] not in {"approved", "escalate"}:
        raise InputError(f"latest gate outcome is {gate_results[-1]['outcome']}; final report is only valid after approval or escalation")
    version = skill_version(swarm / "brief.md")
    if version == "unknown":
        raise InputError("brief.md is missing required skill_version")
    outcome = gate_results[-1]["outcome"]
    source_reports = collect_json(find_artifacts(swarm, "sources-check.json"), "sources-check.json")
    table_paths = sorted(find_artifacts(swarm, "*tables-check.json"), key=cycle_order)
    table_reports = collect_json(table_paths, "tables-check.json")
    source_counts = {"ok": 0, "redirect": 0, "warn": 0, "fail": 0}
    for report in source_reports:
        if not isinstance(report.get("counts"), dict):
            raise InputError(f"invalid sources-check.json artifact {report['_artifact']}: missing counts")
        for status, count in report.get("counts", {}).items():
            if status in source_counts:
                if not isinstance(count, int):
                    raise InputError(f"invalid sources-check.json artifact {report['_artifact']}: count {status} is not integer")
                source_counts[status] += count
    if outcome == "approved" and source_counts["fail"]:
        raise InputError(f"sources check still has {source_counts['fail']} failed URL(s)")
    for report in table_reports:
        if not isinstance(report.get("failures"), int):
            raise InputError(f"invalid tables-check.json artifact {report['_artifact']}: missing failures")
    latest_table_failures = table_reports[-1]["failures"]
    if outcome == "approved" and latest_table_failures:
        raise InputError(f"latest table check still has {latest_table_failures} failure(s)")
    duck = latest.get("rubberduck", latest.get("rubber_duck", {}))
    lines = [
        "# Final report",
        "",
        "## Deterministic facts",
        f"- **skill_version:** {version}",
        f"- **cycles with structured reviews:** {len(reviews)}",
        f"- **deterministic outcome:** {outcome}",
        "",
        "### Final topic grades",
    ]
    topics = latest.get("topics", latest.get("topicos", [])) if isinstance(latest, dict) else []
    if isinstance(topics, list) and topics:
        lines += ["| Topic | Minimum grade | Minimum reviewer | Blocks |", "| --- | --- | --- | --- |"]
        for item in topics:
            if isinstance(item, dict):
                lines.append("| {0} | {1} | {2} | {3} |".format(
                    item.get("topico", item.get("topic", "")),
                    item.get("nota_minima", item.get("grade", "")),
                    item.get("revisor_da_minima", item.get("reviewer", "")),
                    item.get("bloqueia", item.get("blocks", False)),
                ))
    else:
        lines.append("- No structured topic review found.")
    # Retain legacy deck sections only when the review actually contains them.
    for title, key, label in (("Final slide grades", "slides", "Slide"), ("Final deck dimensions", "deck_dimensions", "Dimension")):
        if key not in latest:
            continue
        lines += ["", f"### {title}"]
        values = latest.get(key, []) if isinstance(latest, dict) else []
        if isinstance(values, dict):
            values = list(values.values())
        if values:
            lines += [f"| {label} | Grade |", "| --- | --- |"]
            for item in values:
                if isinstance(item, dict):
                    lines.append(f"| {item.get('slide', item.get('dimension', item.get('name', '')))} | "
                                 f"{item.get('nota_minima', item.get('grade', ''))} |")
        else:
            lines.append("- Not applicable.")
    lines += [
        "",
        "### Final rubber-duck state",
        f"- Critical: {duck.get('critico', duck.get('critical', 'unknown'))}",
        f"- Findings: {json.dumps(duck.get('achados', duck.get('findings', [])), ensure_ascii=False, sort_keys=True)}",
        "",
        "### Sources",
        "- Artifacts: {0}; ok={ok}, redirect={redirect}, warn={warn}, fail={fail}".format(len(source_reports), **source_counts),
        f"- Final disposition: {'blocking failures remain for escalation' if source_counts['fail'] else 'no failed URLs'}",
        "",
        "### Table checks",
    ]
    if table_reports:
        for report in table_reports:
            lines.append(f"- `{report['_artifact']}`: failures={report.get('failures', 'unknown')}")
        lines.append(f"- Final disposition: latest failures={latest_table_failures}")
    else:
        lines.append("- No table-check artifact found.")
    if outcome == "escalate" and (source_counts["fail"] or latest_table_failures):
        lines += ["", "## Escalation blockers", ""]
        if source_counts["fail"]:
            lines.append(f"- Source verification: {source_counts['fail']} failed URL(s) remain.")
            for report in source_reports:
                for item in report.get("results", []):
                    if isinstance(item, dict) and item.get("status") == "fail":
                        detail = item.get("http_status") or item.get("error") or "unknown failure"
                        lines.append(f"- Failed source `{item.get('url', 'unknown URL')}`: {detail}")
        if latest_table_failures:
            lines.append(f"- Latest table verification: {latest_table_failures} failure(s) remain.")
    lines += ["", "### Gate outcomes"]
    if gate_results:
        for result in gate_results:
            lines.append(f"- `{result['artifact']}`: **{result['outcome']}**"
                         + (f" — {result['error']}" if "error" in result else ""))
    else:
        lines.append("- No structured gate artifact found.")
    lines += [
        "",
        "## Coordinator narrative (complete manually)",
        "<!-- COORDINATOR: Explain decisions, residual risks, and evidence not represented above. -->",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate a deterministic final-report.md skeleton for a swarm.")
    parser.add_argument("swarm", type=Path)
    parser.add_argument("--output", type=Path, help="default: <swarm>/reports/final-report.md")
    parser.add_argument("--force", action="store_true", help="replace an existing output")
    args = parser.parse_args(argv)
    output = args.output or args.swarm / "reports" / "final-report.md"
    if output.exists() and not args.force:
        print(f"Refusing to overwrite {output}; use --force.", file=sys.stderr)
        return 1
    try:
        rendered = render(args.swarm)
    except (OSError, InputError) as exc:
        print(f"ERROR: cannot generate final report: {exc}", file=sys.stderr)
        return 1
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding="utf-8")
    print(f"Wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
