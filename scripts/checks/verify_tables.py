"""Check explicitly marked arithmetic and inventory rules in Markdown tables.

Supported markers immediately above a table are:

``<!-- check: weighted weights=20,25,55 divisor=5 -->``
``<!-- check: weighted pesos=20,25,55 scale=5 -->``
``<!-- check: sum target=100 -->`` and ``<!-- check: percent -->``
``<!-- check: rows id="inventario" count=71 key=1 pattern="apimv?2?-[a-z0-9-]+" unique=true -->``
``<!-- check: match_keys source="inventario" column=1 -->``
``<!-- check: refs source="inventario" pattern="apimv?2?-[a-z0-9-]+" -->``.

Only marked tables can fail a run.  Unmarked Total/Soma columns are reported
as information because their intended formula is unknowable.
"""

from __future__ import annotations

import argparse
import re
import sys
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any

# Allow ``python3 /repo/scripts/checks/verify_tables.py`` from any cwd.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.checks.common import write_json

MARKER = re.compile(r"<!--\s*check:\s*([^>]+?)\s*-->", re.I)
NUMBER = re.compile(r"^-?(?:\d+(?:[.,]\d+)?|\.\d+)%?$")


def cells(line: str) -> list[str]:
    """Split a simple pipe table row, retaining no Markdown formatting."""
    line = line.strip().strip("|")
    return [re.sub(r"[*`]", "", cell).strip() for cell in line.split("|")]


def is_separator(line: str) -> bool:
    parts = cells(line)
    return bool(parts) and all(re.fullmatch(r":?-{3,}:?", part.replace(" ", "")) for part in parts)


def as_number(value: str) -> Decimal | None:
    value = value.strip().replace(" ", "")
    if not NUMBER.fullmatch(value):
        return None
    return Decimal(value.rstrip("%").replace(",", "."))


def display(value: Decimal) -> str:
    value = value.quantize(Decimal("0.01")).normalize()
    return format(value, "f").rstrip("0").rstrip(".") if "." in format(value, "f") else format(value, "f")


def parse_tables(text: str) -> list[dict[str, Any]]:
    """Return tables and surface check markers that are not attached to one."""
    lines = text.splitlines()
    found: list[dict[str, Any]] = []
    marker: str | None = None
    marker_line: int | None = None
    for index, line in enumerate(lines):
        match = MARKER.search(line)
        if match:
            if marker is not None:
                found.append({"line": marker_line, "marker": marker, "orphan": True})
            marker = match.group(1).strip()
            marker_line = index + 1
            continue
        table_start = "|" in line and index + 1 < len(lines) and is_separator(lines[index + 1])
        if marker is not None and line.strip() and not table_start:
            found.append({"line": marker_line, "marker": marker, "orphan": True})
            marker = None
            marker_line = None
        if not table_start:
            continue
        header = cells(line)
        rows: list[list[str]] = []
        cursor = index + 2
        while cursor < len(lines) and "|" in lines[cursor] and lines[cursor].strip():
            rows.append(cells(lines[cursor]))
            cursor += 1
        if rows:
            found.append({"line": index + 1, "headers": header, "rows": rows, "marker": marker})
        marker = None
        marker_line = None
    if marker is not None:
        found.append({"line": marker_line, "marker": marker, "orphan": True})
    return found


def marker_option(marker: str, name: str) -> str | None:
    match = re.search(
        rf"\b{re.escape(name)}\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|(.+?))(?=\s+\w+\s*=|$)",
        marker,
        re.I,
    )
    if not match:
        return None
    return next(value for value in match.groups() if value is not None).strip()


def table_label(row: list[str], first_numeric: int) -> str:
    """Keep all descriptive cells so repeated alternatives remain distinguishable."""
    return " — ".join(cell for cell in row[:first_numeric] if cell) or (row[0] if row else "")


def weighted_check(table: dict[str, Any], marker: str) -> dict[str, Any]:
    weight_text = marker_option(marker, "weights") or marker_option(marker, "pesos")
    if not weight_text:
        return {"status": "fail", "rule": "weighted", "line": table["line"], "error": "missing weights/pesos"}
    try:
        parts = [part.strip() for part in weight_text.split(",")]
        if not parts or any(not part for part in parts):
            raise ValueError
        weights = [Decimal(part) for part in parts]
    except (ValueError, ArithmeticError):
        return {"status": "fail", "rule": "weighted", "line": table["line"], "error": "invalid weights"}
    headers = table["headers"]
    normalized = [header.lower().strip() for header in headers]
    total_index = next((i for i, h in enumerate(normalized) if "total" in h or "soma" in h), None)
    score_index = next((i for i, h in enumerate(normalized) if "score" in h or "normaliz" in h), None)
    if total_index is None:
        return {"status": "fail", "rule": "weighted", "line": table["line"], "error": "missing Total/Soma column"}
    first_rating = total_index - len(weights)
    if first_rating < 0:
        return {"status": "fail", "rule": "weighted", "line": table["line"], "error": "not enough rating columns"}
    try:
        divisor = Decimal(marker_option(marker, "divisor") or marker_option(marker, "scale") or "5")
    except ArithmeticError:
        return {"status": "fail", "rule": "weighted", "line": table["line"], "error": "invalid divisor/scale"}
    if divisor <= 0:
        return {"status": "fail", "rule": "weighted", "line": table["line"], "error": "divisor/scale must be positive"}
    mismatches: list[dict[str, Any]] = []
    for row_number, row in enumerate(table["rows"], table["line"] + 2):
        label = table_label(row, first_rating)
        required_index = max(total_index, score_index if score_index is not None else total_index)
        if len(row) <= required_index:
            mismatches.append({"line": row_number, "label": label, "error": "row has fewer cells than the marked rule requires"})
            continue
        ratings = [as_number(row[first_rating + i]) for i in range(len(weights))]
        declared = as_number(row[total_index])
        if any(value is None for value in ratings):
            mismatches.append({"line": row_number, "label": label, "error": "rating cell is not numeric"})
            continue
        if declared is None:
            mismatches.append({"line": row_number, "label": label, "error": "declared Total/Soma is not numeric"})
            continue
        calculated = sum((ratings[i] * weights[i] for i in range(len(weights))), Decimal())
        problem: dict[str, Any] = {}
        if declared != calculated:
            problem.update({"declared_total": display(declared), "calculated_total": display(calculated)})
        if score_index is not None and len(row) > score_index:
            declared_score = as_number(row[score_index])
            if declared_score is None:
                problem["error"] = "declared Score/normalization is not numeric"
            else:
                calculated_score = (calculated / divisor).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
                if declared_score != calculated_score:
                    problem.update({"declared_score": display(declared_score), "calculated_score": display(calculated_score)})
        if problem:
            problem.update({"line": row_number, "label": label})
            mismatches.append(problem)
    return {
        "status": "fail" if mismatches else "pass",
        "rule": "weighted",
        "line": table["line"],
        "weights": [display(weight) for weight in weights],
        "divisor": display(divisor),
        "mismatches": mismatches,
    }


def sum_check(table: dict[str, Any], marker: str) -> dict[str, Any]:
    expected_text = marker_option(marker, "target") or marker_option(marker, "expected") or marker_option(marker, "total")
    expected = Decimal(expected_text.replace(",", ".")) if expected_text else Decimal("100")
    headers = table["headers"]
    chosen = marker_option(marker, "column") or marker_option(marker, "coluna")
    if chosen:
        if chosen.isdigit():
            candidate = int(chosen) - 1
            column = candidate if 0 <= candidate < len(headers) else None
        else:
            column = next((i for i, header in enumerate(headers) if header.lower() == chosen.lower()), None)
        if column is None:
            return {"status": "fail", "rule": "sum", "line": table["line"],
                    "error": f"unknown numeric column {chosen!r}"}
    else:
        preferred = ("percent", "%", "peso", "weight", "valor", "value")
        column = next((i for i, header in enumerate(headers) if any(word in header.lower() for word in preferred)), None)
        if column is None:
            column = next((i for i in range(len(headers))
                           if any(len(row) > i and as_number(row[i]) is not None for row in table["rows"])), None)
    if column is None:
        return {"status": "fail", "rule": "sum", "line": table["line"], "error": "no numeric column"}
    total_rows = [
        (number, row) for number, row in enumerate(table["rows"], table["line"] + 2)
        if row and re.match(r"\s*(?:total|soma)\b", row[0], re.I)
    ]
    components = [
        (number, row)
        for number, row in enumerate(table["rows"], table["line"] + 2)
        if (number, row) not in total_rows
    ]
    mismatches: list[dict[str, Any]] = []
    numeric_components: list[Decimal] = []
    for row_number, row in components:
        value = as_number(row[column]) if len(row) > column else None
        if value is None:
            mismatches.append({
                "line": row_number,
                "label": table_label(row, column),
                "error": f"column {headers[column]!r} is not numeric",
            })
        else:
            numeric_components.append(value)
    component_sum = sum(numeric_components, Decimal())
    if components and component_sum != expected:
        mismatches.append({
            "line": table["line"],
            "label": headers[column],
            "calculated_total": display(component_sum),
            "expected_total": display(expected),
        })
    if not components and not total_rows:
        mismatches.append({"line": table["line"], "label": headers[column], "error": "no values to sum"})
    for row_number, row in total_rows:
        declared = as_number(row[column]) if len(row) > column else None
        if declared is None:
            mismatches.append({"line": row_number, "label": table_label(row, column), "error": "missing declared total"})
        elif declared != expected or (components and declared != component_sum):
            mismatches.append({"line": row_number, "label": table_label(row, column), "declared_total": display(declared),
                               "calculated_total": display(component_sum) if components else display(expected),
                               "target": display(expected)})
    return {"status": "fail" if mismatches else "pass", "rule": "sum", "line": table["line"],
            "column": headers[column], "expected": display(expected), "mismatches": mismatches}


def resolve_column(headers: list[str], chosen: str | None, default: int | None = 0) -> int | None:
    if chosen is None:
        return default
    if chosen.isdigit():
        candidate = int(chosen) - 1
        return candidate if 0 <= candidate < len(headers) else None
    return next((i for i, header in enumerate(headers) if header.lower() == chosen.lower()), None)


def extract_key(cell: str, pattern: re.Pattern[str] | None) -> str:
    if pattern is not None:
        match = pattern.search(cell)
        return match.group(0).strip() if match else ""
    return cell.strip()


def data_rows(table: dict[str, Any]) -> list[tuple[int, list[str]]]:
    return [
        (number, row)
        for number, row in enumerate(table["rows"], table["line"] + 2)
        if not (row and re.match(r"\s*(?:total|soma)\b", row[0], re.I))
    ]


def rows_check(table: dict[str, Any], marker: str) -> dict[str, Any]:
    table_id = marker_option(marker, "id")
    count_text = marker_option(marker, "count") or marker_option(marker, "expected") or marker_option(marker, "total")
    expected_count: int | None = None
    if count_text is not None:
        if not count_text.isdigit() or int(count_text) < 1:
            return {"status": "fail", "rule": "rows", "line": table["line"], "error": "invalid row count"}
        expected_count = int(count_text)
    key_opt = marker_option(marker, "key") or marker_option(marker, "column") or marker_option(marker, "coluna")
    key_col = resolve_column(table["headers"], key_opt, default=0)
    if key_col is None:
        return {"status": "fail", "rule": "rows", "line": table["line"], "error": f"unknown key column {key_opt!r}"}
    pattern_text = marker_option(marker, "pattern")
    compiled_pattern: re.Pattern[str] | None = None
    if pattern_text is not None:
        try:
            compiled_pattern = re.compile(pattern_text)
        except re.error as exc:
            return {"status": "fail", "rule": "rows", "line": table["line"], "error": f"invalid pattern: {exc}"}
    unique_required = (marker_option(marker, "unique") or "true").lower() == "true"
    components = data_rows(table)
    mismatches: list[dict[str, Any]] = []
    keys: list[str] = []
    seen_keys: set[str] = set()
    for row_number, row in components:
        if len(row) != len(table["headers"]):
            mismatches.append({
                "line": row_number,
                "label": row[0] if row else "",
                "error": f"expected {len(table['headers'])} cells, found {len(row)}",
            })
            continue
        key_val = extract_key(row[key_col], compiled_pattern)
        if unique_required:
            if not key_val:
                mismatches.append({"line": row_number, "label": row[key_col], "error": "empty or unmatched key cell"})
            elif key_val.lower() in seen_keys:
                mismatches.append({"line": row_number, "label": key_val, "error": f"duplicate key {key_val!r}"})
            else:
                seen_keys.add(key_val.lower())
                keys.append(key_val)
        elif key_val:
            keys.append(key_val)
    if expected_count is not None and len(components) != expected_count:
        mismatches.append({
            "line": table["line"],
            "calculated_rows": str(len(components)),
            "expected_rows": str(expected_count),
        })
    result: dict[str, Any] = {
        "status": "fail" if mismatches else "pass",
        "rule": "rows",
        "line": table["line"],
        "rows": len(components),
        "keys": keys,
        "mismatches": mismatches,
    }
    if table_id:
        result["id"] = table_id
    if pattern_text:
        result["pattern"] = pattern_text
    return result


def match_keys_check(table: dict[str, Any], marker: str, registry: dict[str, dict[str, Any]]) -> dict[str, Any]:
    source_id = marker_option(marker, "source") or marker_option(marker, "from")
    if not source_id or source_id not in registry:
        return {"status": "fail", "rule": "match_keys", "line": table["line"],
                "error": f"unknown source table id {source_id!r}"}
    source_entry = registry[source_id]
    col_opt = marker_option(marker, "column") or marker_option(marker, "coluna") or marker_option(marker, "key")
    key_col = resolve_column(table["headers"], col_opt, default=0)
    if key_col is None:
        return {"status": "fail", "rule": "match_keys", "line": table["line"],
                "error": f"unknown key column {col_opt!r}"}
    pattern_text = marker_option(marker, "pattern") or source_entry.get("pattern")
    compiled_pattern: re.Pattern[str] | None = None
    if pattern_text is not None:
        try:
            compiled_pattern = re.compile(pattern_text)
        except re.error as exc:
            return {"status": "fail", "rule": "match_keys", "line": table["line"], "error": f"invalid pattern: {exc}"}
    order_required = (marker_option(marker, "order") or "true").lower() == "true"
    components = data_rows(table)
    mismatches: list[dict[str, Any]] = []
    actual_keys: list[str] = []
    for row_number, row in components:
        if len(row) != len(table["headers"]):
            mismatches.append({
                "line": row_number,
                "label": row[0] if row else "",
                "error": f"expected {len(table['headers'])} cells, found {len(row)}",
            })
            continue
        key_val = extract_key(row[key_col], compiled_pattern)
        if not key_val:
            mismatches.append({"line": row_number, "label": row[key_col], "error": "empty or unmatched key cell"})
        else:
            actual_keys.append(key_val)
    expected_keys: list[str] = source_entry.get("keys", [])
    expected_lower = [item.lower() for item in expected_keys]
    actual_lower = [item.lower() for item in actual_keys]
    expected_set = set(expected_lower)
    actual_set = set(actual_lower)
    unknown = [actual_keys[i] for i, key in enumerate(actual_lower) if key not in expected_set]
    missing = [expected_keys[i] for i, key in enumerate(expected_lower) if key not in actual_set]
    if unknown:
        mismatches.append({"line": table["line"], "unknown_keys": unknown})
    if missing:
        mismatches.append({"line": table["line"], "missing_keys": missing})
    if not unknown and not missing and order_required and actual_lower != expected_lower:
        mismatches.append({"line": table["line"], "error": f"key order differs from source {source_id!r}"})
    return {
        "status": "fail" if mismatches else "pass",
        "rule": "match_keys",
        "line": table["line"],
        "source": source_id,
        "mismatches": mismatches,
    }


def refs_check(table: dict[str, Any], marker: str, registry: dict[str, dict[str, Any]]) -> dict[str, Any]:
    source_id = marker_option(marker, "source") or marker_option(marker, "from")
    if not source_id or source_id not in registry:
        return {"status": "fail", "rule": "refs", "line": table["line"],
                "error": f"unknown source table id {source_id!r}"}
    source_entry = registry[source_id]
    pattern_text = marker_option(marker, "pattern") or source_entry.get("pattern")
    if not pattern_text:
        return {"status": "fail", "rule": "refs", "line": table["line"], "error": "missing pattern for refs check"}
    try:
        compiled = re.compile(pattern_text)
    except re.error as exc:
        return {"status": "fail", "rule": "refs", "line": table["line"], "error": f"invalid pattern: {exc}"}
    col_opt = marker_option(marker, "column") or marker_option(marker, "coluna")
    if col_opt and col_opt != "*":
        col_idx = resolve_column(table["headers"], col_opt, default=None)
        if col_idx is None:
            return {"status": "fail", "rule": "refs", "line": table["line"],
                    "error": f"unknown column {col_opt!r}"}
        columns = [col_idx]
    else:
        columns = list(range(len(table["headers"])))
    valid_keys = {item.lower() for item in source_entry.get("keys", [])}
    mismatches: list[dict[str, Any]] = []
    for row_number, row in enumerate(table["rows"], table["line"] + 2):
        matched = {
            match.group(0).strip()
            for col in columns
            if col < len(row)
            for match in compiled.finditer(row[col])
            if match.group(0).strip()
        }
        unknown = sorted(item for item in matched if item.lower() not in valid_keys)
        if unknown:
            mismatches.append({"line": row_number, "label": row[0] if row else "", "unknown_refs": unknown})
    return {
        "status": "fail" if mismatches else "pass",
        "rule": "refs",
        "line": table["line"],
        "source": source_id,
        "mismatches": mismatches,
    }


def check_file(path: Path) -> dict[str, Any]:
    """Validate every marked table in a Markdown document."""
    checks: list[dict[str, Any]] = []
    tables = parse_tables(path.read_text(encoding="utf-8"))
    registry: dict[str, dict[str, Any]] = {}
    precomputed_rows: dict[int, dict[str, Any]] = {}
    for table in tables:
        marker = table.get("marker")
        if not table.get("orphan") and marker and re.search(r"^\s*rows\b", marker, re.I):
            result = rows_check(table, marker)
            precomputed_rows[table["line"]] = result
            if result.get("id") and "keys" in result:
                registry[result["id"]] = result
    for table in tables:
        marker = table["marker"]
        if table.get("orphan"):
            checks.append({
                "status": "fail",
                "rule": marker.split()[0] if marker else "unknown",
                "line": table["line"],
                "error": "check marker is not followed by a Markdown table",
            })
        elif marker and re.search(r"^\s*rows\b", marker, re.I):
            result = dict(precomputed_rows[table["line"]])
            result.pop("keys", None)
            checks.append(result)
        elif marker and re.search(r"^\s*match_keys\b", marker, re.I):
            checks.append(match_keys_check(table, marker, registry))
        elif marker and re.search(r"^\s*refs\b", marker, re.I):
            checks.append(refs_check(table, marker, registry))
        elif marker and re.search(r"\bweighted\b", marker, re.I):
            checks.append(weighted_check(table, marker))
        elif marker and (re.search(r"\b(sum|percent|porcent)", marker, re.I)):
            checks.append(sum_check(table, marker))
        elif marker:
            checks.append({"status": "fail", "rule": marker.split()[0], "line": table["line"],
                           "error": f"unknown check rule: {marker}"})
        elif any(("total" in h.lower() or "soma" in h.lower()) for h in table["headers"]):
            checks.append({"status": "info", "rule": "unmarked-total", "line": table["line"],
                           "message": "Total/Soma heuristic not enforced without a check marker"})
    return {"schema_version": 1, "document": str(path), "checks": checks,
            "failures": sum(check["status"] == "fail" for check in checks)}


def paths_from_inputs(inputs: list[Path]) -> list[Path]:
    """Expand document paths and directories deterministically."""
    paths: list[Path] = []
    for item in inputs:
        if item.is_dir():
            paths.extend(sorted(item.rglob("*.md")))
        else:
            paths.append(item)
    return paths


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate marked Markdown-table arithmetic.")
    parser.add_argument("documents", nargs="+", type=Path, help="Markdown files or directories")
    parser.add_argument("--output", type=Path, help="aggregate JSON report path")
    args = parser.parse_args(argv)
    paths = paths_from_inputs(args.documents)
    if not paths:
        print("ERROR: no Markdown documents found", file=sys.stderr)
        return 2
    reports = []
    for path in paths:
        try:
            reports.append(check_file(path))
        except OSError as exc:
            reports.append({"schema_version": 1, "document": str(path), "checks": [
                {"status": "fail", "rule": "read", "line": 0, "error": str(exc)}
            ], "failures": 1})
    report = reports[0] if len(reports) == 1 else {
        "schema_version": 1, "documents": reports, "failures": sum(item["failures"] for item in reports)
    }
    output = args.output or (paths[0].with_suffix(".tables-check.json") if len(paths) == 1 else Path("tables-check.json"))
    write_json(output, report)
    for document in reports:
        for check in document["checks"]:
            print(f"{check['status'].upper():5} {document['document']} line {check['line']}: {check['rule']}"
                  + (f" ({len(check.get('mismatches', []))} mismatch(es))" if "mismatches" in check else ""))
    print(f"Wrote {output}")
    return 1 if sum(item["failures"] for item in reports) else 0


if __name__ == "__main__":
    raise SystemExit(main())
