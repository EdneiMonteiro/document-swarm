"""Small stdlib-only helpers shared by deterministic swarm checks.

The YAML reader intentionally accepts only the constrained mapping/list/scalar
form emitted by swarm review files.  JSON is accepted unchanged because it is
a YAML subset and is preferable when values need escaping.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any


class InputError(ValueError):
    """Raised when a constrained review artifact cannot be decoded."""


SCALE = ("D-", "D", "D+", "C-", "C", "C+", "B-", "B", "B+", "A-", "A", "A+")
GRADE_INDEX = {grade: index for index, grade in enumerate(SCALE)}


def normalize_grade(grade: Any) -> str:
    """Return the canonical grade label, rejecting anything outside the scale."""
    if not isinstance(grade, str) or grade.strip().upper() not in GRADE_INDEX:
        raise InputError(f"invalid grade: {grade!r}; expected one of {', '.join(SCALE)}")
    return grade.strip().upper()


def load_data(path: Path) -> Any:
    """Load JSON or a deliberately small, safe YAML subset from *path*."""
    return parse_data(path.read_text(encoding="utf-8"))


def parse_data(text: str) -> Any:
    """Decode a single captured document without rereading a changing file."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    return parse_yaml(text)


def parse_scalar(value: str) -> Any:
    value = value.strip()
    if not value or value in {"[]", "{}"}:
        return [] if value == "[]" else ({} if value == "{}" else "")
    if value.lower() in {"true", "false"}:
        return value.lower() == "true"
    if value.lower() in {"null", "none", "~"}:
        return None
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        return value[1:-1]
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        pass
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    if re.fullmatch(r"-?(?:\d+\.\d*|\d*\.\d+)", value):
        return float(value)
    return value


def parse_yaml(text: str) -> Any:
    """Parse mappings and lists with indentation, scalars, and ``#`` comments.

    This is not a general YAML parser: anchors, tags, multiline strings and
    flow collections other than JSON are intentionally rejected/unsupported.
    """
    lines: list[tuple[int, str, int]] = []
    for number, raw in enumerate(text.splitlines(), 1):
        if not raw.strip() or raw.lstrip().startswith("#") or raw.strip() == "---":
            continue
        if "\t" in raw[: len(raw) - len(raw.lstrip())]:
            raise InputError(f"line {number}: tabs are not supported")
        indent = len(raw) - len(raw.lstrip(" "))
        content = raw.strip()
        if " #" in content:
            content = content.split(" #", 1)[0].rstrip()
        lines.append((indent, content, number))
    if not lines:
        raise InputError("empty input")

    def parse_block(position: int, indent: int) -> tuple[Any, int]:
        if position >= len(lines) or lines[position][0] < indent:
            raise InputError("invalid indentation")
        is_list = lines[position][1].startswith("- ")
        result: Any = [] if is_list else {}
        while position < len(lines):
            current_indent, content, number = lines[position]
            if current_indent < indent:
                break
            if current_indent != indent:
                raise InputError(f"line {number}: unexpected indentation")
            if is_list:
                if not content.startswith("- "):
                    raise InputError(f"line {number}: mixed list and mapping")
                payload = content[2:].strip()
                if not payload:
                    if position + 1 >= len(lines) or lines[position + 1][0] <= indent:
                        result.append(None)
                        position += 1
                    else:
                        child, position = parse_block(position + 1, lines[position + 1][0])
                        result.append(child)
                    continue
                if ":" not in payload:
                    result.append(parse_scalar(payload))
                    position += 1
                    continue
                key, value = payload.split(":", 1)
                item: dict[str, Any] = {key.strip(): parse_scalar(value)}
                position += 1
                if position < len(lines) and lines[position][0] > indent:
                    child_indent = lines[position][0]
                    child, position = parse_block(position, child_indent)
                    if isinstance(child, dict):
                        item.update(child)
                    else:
                        raise InputError(f"line {number}: list item continuation must be mapping")
                result.append(item)
                continue
            if content.startswith("- ") or ":" not in content:
                raise InputError(f"line {number}: expected key: value")
            key, value = content.split(":", 1)
            key, value = key.strip(), value.strip()
            if not key:
                raise InputError(f"line {number}: empty key")
            position += 1
            if value:
                result[key] = parse_scalar(value)
            elif position < len(lines) and lines[position][0] > indent:
                result[key], position = parse_block(position, lines[position][0])
            else:
                result[key] = None
        return result, position

    data, position = parse_block(0, lines[0][0])
    if position != len(lines):
        raise InputError(f"line {lines[position][2]}: unread input")
    return data


def parse_strict_json(text: str, *, name: str = "JSON") -> Any:
    """Decode JSON while rejecting duplicate keys and non-finite numbers."""
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        seen: set[str] = set()
        for key, _ in items:
            if key in seen:
                raise InputError(f"{name}: duplicate key {key!r}")
            seen.add(key)
        return dict(items)

    def constant(literal: str) -> Any:
        raise InputError(f"{name}: {literal} is not a finite JSON number")

    try:
        return json.loads(text, object_pairs_hook=pairs, parse_constant=constant)
    except json.JSONDecodeError as exc:
        raise InputError(f"{name}: invalid JSON: {exc}") from exc


def write_json(path: Path, data: Any) -> None:
    """Write stable, readable JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_json_atomic(path: Path, data: Any) -> None:
    """Publish a complete JSON file, replacing only the requested destination."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=f".{path.name}.", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(data, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
