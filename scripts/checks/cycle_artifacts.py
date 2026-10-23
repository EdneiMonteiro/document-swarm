"""Deterministic assembly, cycle diffing, and review consolidation with zero LLM tokens."""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
import sys
from pathlib import Path, PurePosixPath
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.checks.common import (
    GRADE_INDEX, InputError, load_data, normalize_grade, write_json_atomic,
)
from scripts.checks.gate import (
    _duck_contested, editorial_blockers, requires_editorial, value,
)
from scripts.checks.lint_agents import frontmatter_text


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def descriptor(root: Path, path: Path) -> dict[str, str]:
    resolved = path.resolve(strict=True)
    if root not in resolved.parents:
        raise InputError(f"artifact escapes swarm root: {path}")
    return {"path": resolved.relative_to(root).as_posix(), "sha256": sha256_file(resolved)}


def load_brief(root: Path) -> dict[str, Any]:
    brief_path = root / "brief.md"
    if not brief_path.is_file():
        raise InputError("swarm is missing brief.md")
    return frontmatter_text(brief_path.read_text(encoding="utf-8"))


def primary_markdown_deliverable(root: Path, brief: dict[str, Any]) -> Path:
    deliverables = brief.get("deliverables")
    if isinstance(deliverables, list):
        for item in deliverables:
            if isinstance(item, str) and item.lower().endswith(".md"):
                return root.joinpath(*PurePosixPath(item.replace("\\", "/")).parts)
    candidates = sorted(
        path for path in (root / "output").glob("*.md")
        if path.is_file()
    )
    if candidates:
        return candidates[0]
    return root / "output" / "document.md"


def topic_map(brief: dict[str, Any]) -> dict[str, str]:
    topics = brief.get("topics")
    if isinstance(topics, dict):
        return {str(k): str(v) for k, v in topics.items()}
    if isinstance(topics, list):
        result: dict[str, str] = {}
        for index, item in enumerate(topics, 1):
            if isinstance(item, dict):
                tid = str(value(item, "topico", "topic", "id", "name") or f"T{index:02d}")
                title = str(item.get("title", tid))
                result[tid] = title
            else:
                text = str(item)
                if ":" in text:
                    tid, title = text.split(":", 1)
                    result[tid.strip()] = title.strip()
                else:
                    result[text.strip()] = text.strip()
        return result
    return {}


def _pdf_page_hashes(root: Path, cycle: int, brief: dict[str, Any]) -> dict[int, str]:
    candidates: list[Path] = []
    deliverables = brief.get("deliverables")
    if isinstance(deliverables, list):
        for item in deliverables:
            if isinstance(item, str) and item.lower().endswith(".pdf"):
                pdf_path = root.joinpath(*PurePosixPath(item.replace("\\", "/")).parts)
                if f"cycle-{cycle:02d}" in pdf_path.as_posix() or cycle == 1:
                    candidates.append(pdf_path.parent / "inspection.json")
    candidates.append(root / "output" / f"pdf-cycle-{cycle:02d}" / "inspection.json")
    for path in candidates:
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                pages = data.get("pages", [])
                if isinstance(pages, list):
                    return {
                        int(p["page"]): hashlib.sha256(
                            json.dumps(p, sort_keys=True, ensure_ascii=False).encode("utf-8")
                        ).hexdigest()
                        for p in pages
                        if isinstance(p, dict) and isinstance(p.get("page"), int)
                    }
            except (OSError, ValueError, TypeError):
                continue
    return {}


def diff_cycles(swarm: Path, cycle: int) -> dict[str, Any]:
    """Compute a deterministic diff between cycle N-1 and cycle N."""
    root = swarm.resolve(strict=True)
    brief = load_brief(root)
    topics = topic_map(brief)
    reports = root / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    curr_txt_path = reports / f"cycle-{cycle:02d}-editorial-text.txt"
    if not curr_txt_path.is_file():
        primary = primary_markdown_deliverable(root, brief)
        if primary.is_file():
            curr_txt_path.write_text(primary.read_text(encoding="utf-8"), encoding="utf-8")
    curr_lines = curr_txt_path.read_text(encoding="utf-8").splitlines() if curr_txt_path.is_file() else []
    if cycle <= 1:
        payload = {
            "schema_version": 1,
            "cycle": cycle,
            "previous_cycle": None,
            "has_changes": True,
            "changed_line_count": len(curr_lines),
            "changed_topics": list(topics.keys()),
            "unchanged_topics": [],
            "changed_pages": [],
            "hunks": [],
        }
        write_json_atomic(reports / f"cycle-{cycle:02d}-diff.json", payload)
        return payload

    prev_cycle = cycle - 1
    prev_txt_path = reports / f"cycle-{prev_cycle:02d}-editorial-text.txt"
    prev_lines = prev_txt_path.read_text(encoding="utf-8").splitlines() if prev_txt_path.is_file() else []
    diff_lines = list(
        difflib.unified_diff(
            prev_lines,
            curr_lines,
            fromfile=f"cycle-{prev_cycle:02d}-editorial-text.txt",
            tofile=f"cycle-{cycle:02d}-editorial-text.txt",
            lineterm="",
            n=2,
        )
    )
    changed_text_chunks = [
        line[1:]
        for line in diff_lines
        if (line.startswith("+") or line.startswith("-"))
        and not line.startswith("+++")
        and not line.startswith("---")
    ]
    matcher = difflib.SequenceMatcher(None, prev_lines, curr_lines)
    enclosing_headings: list[str] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        for pos in range(min(j1, len(curr_lines) - 1), -1, -1):
            if curr_lines[pos].lstrip().startswith("#"):
                enclosing_headings.append(curr_lines[pos])
                if curr_lines[pos].lstrip().startswith("# ") and not curr_lines[pos].lstrip().startswith("## "):
                    break
        for pos in range(min(i1, len(prev_lines) - 1), -1, -1):
            if prev_lines[pos].lstrip().startswith("#"):
                enclosing_headings.append(prev_lines[pos])
                if prev_lines[pos].lstrip().startswith("# ") and not prev_lines[pos].lstrip().startswith("## "):
                    break
    changed_blob = "\n".join([*changed_text_chunks, *enclosing_headings]).lower()
    changed_topics: list[str] = []
    unchanged_topics: list[str] = []
    if not changed_text_chunks:
        unchanged_topics = list(topics.keys())
    else:
        for index, (tid, title) in enumerate(topics.items(), 1):
            tokens = [
                w.lower()
                for w in re.findall(r"[A-Za-zÀ-ÿ0-9]{4,}", title)
                if w.lower() not in {"para", "como", "pelos", "pelas", "entre", "sobre", "com", "dos", "das"}
            ]
            chapter_markers = (f"{tid.lower()}", f"capítulo {index}", f"seção {index}.", f"## {index}.", f"# {index}.")
            if any(m in changed_blob for m in chapter_markers) or any(tok in changed_blob for tok in tokens[:4]):
                changed_topics.append(tid)
            else:
                unchanged_topics.append(tid)
        if not changed_topics and topics:
            changed_topics = list(topics.keys())
            unchanged_topics = []

    prev_pages = _pdf_page_hashes(root, prev_cycle, brief)
    curr_pages = _pdf_page_hashes(root, cycle, brief)
    changed_pages = sorted(
        page for page, digest in curr_pages.items()
        if prev_pages.get(page) != digest
    ) if prev_pages and curr_pages else []

    payload = {
        "schema_version": 1,
        "cycle": cycle,
        "previous_cycle": prev_cycle,
        "has_changes": bool(changed_text_chunks),
        "changed_line_count": len(changed_text_chunks),
        "changed_topics": changed_topics,
        "unchanged_topics": unchanged_topics,
        "changed_pages": changed_pages,
        "hunks": diff_lines[:400],
    }
    write_json_atomic(reports / f"cycle-{cycle:02d}-diff.json", payload)
    md_lines = [
        f"# Diff determinístico — Ciclo {cycle} vs Ciclo {prev_cycle}",
        "",
        f"- **Linhas alteradas:** {len(changed_text_chunks)}",
        f"- **Tópicos com alteração detectada:** {', '.join(changed_topics) or 'nenhum'}",
        f"- **Tópicos sem alteração:** {', '.join(unchanged_topics) or 'nenhum'}",
        f"- **Páginas PDF alteradas:** {', '.join(str(p) for p in changed_pages) or 'n/a'}",
        "",
        "```diff",
        *diff_lines[:200],
        "```",
        "",
    ]
    (reports / f"cycle-{cycle:02d}-diff.md").write_text("\n".join(md_lines), encoding="utf-8")
    return payload


def assemble(swarm: Path, cycle: int, *, from_sections: bool = False) -> dict[str, Any]:
    """Assemble output/<doc>.md from output/sections/*.md (if requested) and prepare cycle diff."""
    root = swarm.resolve(strict=True)
    brief = load_brief(root)
    primary = primary_markdown_deliverable(root, brief)
    primary.parent.mkdir(parents=True, exist_ok=True)
    sections_dir = root / "output" / "sections"
    section_files = sorted(sections_dir.glob("*.md")) if sections_dir.is_dir() else []
    if section_files and (from_sections or not primary.is_file()):
        parts = [path.read_text(encoding="utf-8").strip() for path in section_files if path.is_file()]
        primary.write_text("\n\n".join(part for part in parts if part) + "\n", encoding="utf-8")
    if not primary.is_file():
        raise InputError(f"primary deliverable does not exist: {primary.relative_to(root).as_posix()}")

    reports = root / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    editorial_txt = reports / f"cycle-{cycle:02d}-editorial-text.txt"
    pdf_txt = root / "output" / f"pdf-cycle-{cycle:02d}" / "editorial-text.txt"
    if pdf_txt.is_file():
        editorial_txt.write_bytes(pdf_txt.read_bytes())
    elif from_sections or not editorial_txt.is_file():
        editorial_txt.write_bytes(primary.read_bytes())

    diff_payload = diff_cycles(root, cycle)
    return {
        "schema_version": 1,
        "cycle": cycle,
        "primary": descriptor(root, primary),
        "editorial_text": descriptor(root, editorial_txt),
        "diff": diff_payload,
    }


def render_reviewer_markdown(report: dict[str, Any], topics: dict[str, str]) -> str:
    reviewer = report.get("reviewer", "reviewer")
    cycle = report.get("cycle", 1)
    lines = [
        f"# Parecer do Revisor `{reviewer}` — Ciclo {cycle}",
        "",
        "| Tópico | Nota | Justificativa | Correção acionável |",
        "| --- | :---: | --- | --- |",
    ]
    for row in report.get("topics", []):
        if not isinstance(row, dict):
            continue
        tid = str(row.get("topic", ""))
        title = topics.get(tid, tid)
        label = f"{tid} — {title}" if title and title != tid else tid
        grade = str(row.get("grade", ""))
        just = str(row.get("justification", "")).replace("\n", " ").strip()
        act = str(row.get("action", "")).replace("\n", " ").strip() or "Nenhuma correção obrigatória."
        lines.append(f"| {label} | {grade} | {just} | {act} |")
    editorial = report.get("editorial")
    if isinstance(editorial, dict):
        lines.extend([
            "",
            "## Superfícies Editoriais (`editorial-v1`)",
            "",
            "| Superfície | Nota | Localização | Justificativa | Ação |",
            "| --- | :---: | --- | --- | --- |",
        ])
        for surf in editorial.get("surfaces", []):
            if not isinstance(surf, dict):
                continue
            name = str(surf.get("surface", ""))
            if "not_applicable" in surf:
                lines.append(f"| {name} | n/a | — | {surf['not_applicable']} | — |")
            else:
                lines.append(
                    f"| {name} | {surf.get('grade', '')} | {surf.get('location', '')} "
                    f"| {surf.get('justification', '')} | {surf.get('action', '') or '—'} |"
                )
    return "\n".join(lines) + "\n"


def _build_pdf_inspections(root: Path, brief: dict[str, Any], primary: Path) -> list[dict[str, Any]]:
    deliverables = brief.get("deliverables")
    if not isinstance(deliverables, list):
        return []
    inspections: list[dict[str, Any]] = []
    for item in deliverables:
        if not isinstance(item, str) or not item.lower().endswith(".pdf"):
            continue
        pdf_path = root.joinpath(*PurePosixPath(item.replace("\\", "/")).parts)
        manifest_path = pdf_path.parent / "manifest.json"
        inspection_path = pdf_path.parent / "inspection.json"
        if pdf_path.is_file() and manifest_path.is_file() and inspection_path.is_file():
            inspections.append({
                "source": descriptor(root, primary),
                "pdf": descriptor(root, pdf_path),
                "manifest": descriptor(root, manifest_path),
                "inspection": descriptor(root, inspection_path),
            })
    return inspections


def consolidate(
    swarm: Path,
    cycle: int,
    *,
    carry_unchanged: bool = True,
    require_rubberduck: bool = False,
) -> dict[str, Any]:
    """Consolidate individual reviewer JSONs into cycle-0N-review.yaml and markdown reports."""
    root = swarm.resolve(strict=True)
    brief = load_brief(root)
    topics = topic_map(brief)
    reports_dir = root / "reports"
    if not reports_dir.is_dir():
        raise InputError("reports directory does not exist")
    primary = primary_markdown_deliverable(root, brief)
    editorial_txt = reports_dir / f"cycle-{cycle:02d}-editorial-text.txt"
    if not editorial_txt.is_file() and primary.is_file():
        editorial_txt.write_bytes(primary.read_bytes())

    diff_path = reports_dir / f"cycle-{cycle:02d}-diff.json"
    diff_data = (
        json.loads(diff_path.read_text(encoding="utf-8"))
        if diff_path.is_file()
        else diff_cycles(root, cycle)
    )
    unchanged_set = set(diff_data.get("unchanged_topics", []))

    prev_review: dict[str, Any] | None = None
    if cycle > 1:
        for suffix in (".yaml", ".yml", ".json"):
            cand = reports_dir / f"cycle-{cycle - 1:02d}-review{suffix}"
            if cand.is_file():
                loaded = load_data(cand)
                if isinstance(loaded, dict):
                    prev_review = loaded
                break

    reviewer_paths = sorted(reports_dir.glob(f"cycle-{cycle:02d}-reviewer-*.json"))
    if not reviewer_paths:
        raise InputError(f"no reviewer JSON files found for cycle {cycle}")

    reviewer_reports: list[dict[str, Any]] = []
    editorial_block: dict[str, Any] | None = None
    editorial_reviewer = brief.get("editorial_reviewer")

    for path in reviewer_paths:
        report = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(report, dict):
            raise InputError(f"invalid reviewer report {path.name}")
        reviewer = str(report.get("reviewer") or "")
        if cycle > 1 and carry_unchanged:
            prev_path = reports_dir / f"cycle-{cycle - 1:02d}-{reviewer}.json"
            if prev_path.is_file():
                prev_report = json.loads(prev_path.read_text(encoding="utf-8"))
                prev_by_topic = {
                    str(item["topic"]): item
                    for item in prev_report.get("topics", [])
                    if isinstance(item, dict) and "topic" in item and "grade" in item
                }
                curr_by_topic = {
                    str(item["topic"]): item
                    for item in report.get("topics", [])
                    if isinstance(item, dict) and "topic" in item
                }
                merged_topics: list[dict[str, Any]] = []
                ordered_ids = list(topics.keys()) or list(prev_by_topic.keys())
                for tid in ordered_ids:
                    curr_item = curr_by_topic.get(tid)
                    prev_item = prev_by_topic.get(tid)
                    if (
                        (curr_item is None or curr_item.get("carry_forward") is True)
                        and prev_item is not None
                        and tid in unchanged_set
                        and GRADE_INDEX[normalize_grade(prev_item["grade"])] >= GRADE_INDEX["A"]
                        and not _duck_contested(prev_review, reviewer, tid)
                    ):
                        carried = dict(prev_item)
                        carried.pop("carry_forward", None)
                        merged_topics.append(carried)
                    elif curr_item is not None:
                        cleaned = dict(curr_item)
                        cleaned.pop("carry_forward", None)
                        merged_topics.append(cleaned)
                if merged_topics:
                    report["topics"] = merged_topics

        if reviewer == editorial_reviewer and isinstance(report.get("editorial"), dict):
            ed = report["editorial"]
            ed["cycle"] = cycle
            ed["reviewer"] = reviewer
            ed["scope"] = "full_document"
            if editorial_txt.is_file():
                ed["text"] = descriptor(root, editorial_txt)
            deliverables = brief.get("deliverables")
            if isinstance(deliverables, list) and deliverables:
                ed["artifacts"] = [
                    descriptor(root, root.joinpath(*PurePosixPath(str(item).replace("\\", "/")).parts))
                    for item in deliverables
                    if isinstance(item, str)
                    and root.joinpath(*PurePosixPath(str(item).replace("\\", "/")).parts).is_file()
                ]
            editorial_block = ed

        write_json_atomic(path, report)
        md_path = reports_dir / f"cycle-{cycle:02d}-{reviewer}.md"
        md_path.write_text(render_reviewer_markdown(report, topics), encoding="utf-8")
        reviewer_reports.append(report)

    ordered_topic_ids = list(topics.keys())
    if not ordered_topic_ids:
        for rep in reviewer_reports:
            for row in rep.get("topics", []):
                if isinstance(row, dict) and "topic" in row and str(row["topic"]) not in ordered_topic_ids:
                    ordered_topic_ids.append(str(row["topic"]))

    consolidated_topics: list[dict[str, Any]] = []
    for tid in ordered_topic_ids:
        candidates: list[tuple[int, str, str]] = []
        for rep in reviewer_reports:
            rev_name = str(rep.get("reviewer", ""))
            for row in rep.get("topics", []):
                if isinstance(row, dict) and str(row.get("topic")) == tid:
                    grade = normalize_grade(row.get("grade"))
                    candidates.append((GRADE_INDEX[grade], grade, rev_name))
        if not candidates:
            raise InputError(f"topic {tid} is missing from reviewer reports")
        min_idx, min_grade, min_reviewer = min(candidates, key=lambda item: item[0])
        entry: dict[str, Any] = {
            "topico": tid,
            "nota_minima": min_grade,
            "revisor_da_minima": min_reviewer,
            "bloqueia": min_idx < GRADE_INDEX["A"],
        }
        if tid in topics and topics[tid] != tid:
            entry["title"] = topics[tid]
        consolidated_topics.append(entry)

    review_data: dict[str, Any] = {
        "schema_version": 1,
        "skill_version": str(brief.get("skill_version", "3.5.0")),
        "mode": str(brief.get("mode", "document")),
        "cycle": cycle,
        "max_cycles": int(brief.get("max_cycles", 5)),
        "topics": consolidated_topics,
    }
    if requires_editorial(brief) or editorial_block is not None:
        review_data["quality_contract"] = "editorial-v1"
        if editorial_block is not None:
            review_data["editorial"] = editorial_block

    pdf_inspections = _build_pdf_inspections(root, brief, primary)
    if pdf_inspections:
        review_data["pdf_inspections"] = pdf_inspections

    ed_blocked = (
        editorial_blockers(review_data, cycle, requires_editorial(brief) or editorial_block is not None)
        if editorial_block is not None
        else []
    )
    has_blockers = any(item["bloqueia"] for item in consolidated_topics) or bool(ed_blocked)
    duck_md_path = reports_dir / f"cycle-{cycle:02d}-rubberduck.md"
    if has_blockers and not require_rubberduck:
        review_data["rubberduck"] = {
            "critico": False,
            "achados": [],
            "skipped_reason": "matrix_rejected",
        }
        if not duck_md_path.is_file():
            duck_md_path.write_text(
                f"# Parecer do rubber duck: Ciclo {cycle}\n\n"
                f"Auditoria independente dispensada neste ciclo (`skipped_reason: matrix_rejected`) "
                f"porque a matriz consolidada dos revisores já reprova a candidata ({sum(t['bloqueia'] for t in consolidated_topics)} "
                f"tópico(s) e {len(ed_blocked)} bloqueio(s) editorial(is)). O despacho do `rubber-duck` é obrigatório "
                f"na candidata que atingir zero bloqueios na matriz.\n",
                encoding="utf-8",
            )
    else:
        review_data["rubberduck"] = {"critico": False, "achados": []}

    review_yaml_path = reports_dir / f"cycle-{cycle:02d}-review.yaml"
    write_json_atomic(review_yaml_path, review_data)

    review_md_lines = [
        f"# Consolidação da Revisão — Ciclo {cycle}",
        "",
        "| Tópico | Nota Mínima | Revisor da Mínima | Bloqueia (`< A`) |",
        "| --- | :---: | --- | :---: |",
    ]
    for item in consolidated_topics:
        label = f"{item['topico']} — {item['title']}" if "title" in item else item["topico"]
        review_md_lines.append(
            f"| {label} | **{item['nota_minima']}** | `{item['revisor_da_minima']}` "
            f"| {'Sim' if item['bloqueia'] else 'Não'} |"
        )
    (reports_dir / f"cycle-{cycle:02d}-review.md").write_text("\n".join(review_md_lines) + "\n", encoding="utf-8")
    return review_data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Assemble sections, compute diffs, and consolidate cycle reviews.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_assemble = subparsers.add_parser("assemble", help="Assemble sections and compute cycle diff")
    p_assemble.add_argument("swarm", type=Path, help="Swarm root directory")
    p_assemble.add_argument("--cycle", type=int, required=True, help="Cycle number")
    p_assemble.add_argument("--from-sections", action="store_true", help="Concatenate output/sections/*.md")

    p_diff = subparsers.add_parser("diff", help="Compute deterministic diff between cycle N-1 and cycle N")
    p_diff.add_argument("swarm", type=Path, help="Swarm root directory")
    p_diff.add_argument("--cycle", type=int, required=True, help="Cycle number")

    p_consolidate = subparsers.add_parser("consolidate", help="Consolidate reviewer JSONs into review.yaml and .md")
    p_consolidate.add_argument("swarm", type=Path, help="Swarm root directory")
    p_consolidate.add_argument("--cycle", type=int, required=True, help="Cycle number")
    p_consolidate.add_argument("--no-carry", action="store_true", help="Disable automatic carry-forward of unchanged >=A topics")
    p_consolidate.add_argument("--require-rubberduck", action="store_true", help="Require full rubber-duck dispatch even when rejected")

    args = parser.parse_args(argv)
    try:
        if args.command == "assemble":
            result = assemble(args.swarm, args.cycle, from_sections=args.from_sections)
        elif args.command == "diff":
            result = diff_cycles(args.swarm, args.cycle)
        else:
            result = consolidate(
                args.swarm,
                args.cycle,
                carry_unchanged=not args.no_carry,
                require_rubberduck=args.require_rubberduck,
            )
    except (OSError, UnicodeError, InputError, ValueError) as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 3
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
