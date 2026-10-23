"""List lexical acronym/code candidates for editorial review; never grade them."""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.checks.common import InputError, write_json_atomic

MAX_BYTES = 10 * 1024 * 1024
MAX_OCCURRENCES = 20000
CANDIDATE = re.compile(r"\b(?:[A-Z]{1,4}[0-9]{1,4}|[A-Z]{2,10})\b")
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")


def mask(match: re.Match[str]) -> str:
    return "".join(character if character in "\r\n" else " " for character in match.group())


def inspect_file(path: Path) -> dict[str, Any]:
    if path.suffix.lower() not in {".md", ".txt"}:
        raise InputError("use a UTF-8 Markdown/text document, not a binary PDF or image")
    with path.open("rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise InputError("nomenclature input exceeds 10 MiB; inspect smaller sections")
    source = raw.decode("utf-8-sig")
    visible = re.sub(r"<!--.*?(?:-->|$)", mask, source, flags=re.S)
    visible = re.sub(r"\]\([^\n)]*\)", mask, visible)
    visible = re.sub(r"https?://[^\s<>]+", mask, visible)
    original_lines = source.splitlines()
    is_markdown = path.suffix.lower() == ".md"
    frontmatter = is_markdown and bool(original_lines) and original_lines[0].strip() == "---"
    fence = None
    candidates: dict[str, dict[str, Any]] = {}
    count = 0
    for line_number, line in enumerate(visible.splitlines(), 1):
        if frontmatter:
            if line_number > 1 and line.strip() == "---":
                frontmatter = False
            continue
        boundary = FENCE.match(line) if is_markdown else None
        if boundary:
            marker = boundary.group(1)
            if fence is None:
                fence = marker
            elif marker[0] == fence[0] and len(marker) >= len(fence) and not line[boundary.end():].strip():
                fence = None
            continue
        if fence is not None:
            continue
        for match in CANDIDATE.finditer(line):
            count += 1
            if count > MAX_OCCURRENCES:
                raise InputError("too many nomenclature candidates; inspect smaller sections")
            token = match.group()
            entry = candidates.setdefault(token, {
                "token": token,
                "kind_hint": "letter_number" if any(character.isdigit() for character in token) else "uppercase",
                "first_line": line_number,
                "meaning": None,
                "definition_status": "not_assessed",
                "occurrences": [],
            })
            original = original_lines[line_number - 1]
            entry["occurrences"].append({
                "line": line_number,
                "column": match.start() + 1,
                "context": original[max(0, match.start() - 60):match.end() + 100].strip(),
            })
    return {
        "schema_version": 1,
        "document": str(path),
        "document_sha256": hashlib.sha256(raw).hexdigest(),
        "status": "inspection_only",
        "editorial_approval": "not_evaluated",
        "candidate_count": len(candidates),
        "occurrence_count": count,
        "candidates": list(candidates.values()),
        "limitations": [
            "Lexical candidates can include ordinary uppercase words and legitimate technical identifiers.",
            "Meanings, definition order, consistency and market-standard claims require reviewer judgment.",
            "Markdown frontmatter, fenced code, HTML comments and link destinations are excluded; inline code labels are included.",
            "Images, binary PDFs and mixed-case acronyms are not interpreted; zero candidates is not editorial approval.",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("document", type=Path)
    parser.add_argument("--output", required=True, type=Path, help="inspection JSON; never the input document")
    args = parser.parse_args(argv)
    if args.output.resolve() == args.document.resolve():
        print("ERROR: inspection output must not replace its input", file=sys.stderr)
        return 2
    try:
        report = inspect_file(args.document)
        write_json_atomic(args.output, report)
    except (OSError, UnicodeError, InputError, ValueError) as exc:
        print(f"ERROR: cannot inspect nomenclature: {exc}", file=sys.stderr)
        return 2
    print(f"{report['candidate_count']} lexical candidates; definitions and editorial approval were not evaluated.")
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
