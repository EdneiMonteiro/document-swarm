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
    raw = brief_path.read_text(encoding="utf-8")
    data = frontmatter_text(raw)
    if not data.get("topics"):
        body_topics: dict[str, str] = {}
        for match in re.finditer(
            r"^\s*[-*]\s*(?:\*\*|`)?(T\d{2,3})\s*(?:[-—–:]+\s*)?([^*\n:`]+)(?:\*\*|`)?(?:\s*[:—–-]\s*([^\n]+))?",
            raw,
            re.MULTILINE,
        ):
            tid = match.group(1).strip()
            t_title = (match.group(2) or "").strip(" *-—–:")
            if not t_title and match.group(3):
                t_title = match.group(3).strip()
            if tid not in body_topics:
                body_topics[tid] = t_title or tid
        if body_topics:
            data["topics"] = body_topics
    return data


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


def _sync_editorial_text_from_pdf(root: Path, cycle: int, brief: dict[str, Any] | None = None) -> Path | None:
    candidates: list[Path] = []
    if brief:
        deliverables = brief.get("deliverables")
        if isinstance(deliverables, list):
            for item in deliverables:
                if isinstance(item, str) and item.lower().endswith(".pdf"):
                    pdf_path = root.joinpath(*PurePosixPath(item.replace("\\", "/")).parts)
                    cand = pdf_path.parent / "editorial-text.txt"
                    if cand.is_file():
                        candidates.append(cand)
    cycle_cand = root / "output" / f"pdf-cycle-{cycle:02d}" / "editorial-text.txt"
    if cycle_cand.is_file() and cycle_cand not in candidates:
        candidates.append(cycle_cand)
    if candidates:
        best = max(candidates, key=lambda p: p.stat().st_mtime) if cycle_cand in candidates and len(candidates) > 1 else candidates[0]
        reports = root / "reports"
        reports.mkdir(parents=True, exist_ok=True)
        editorial_txt = reports / f"cycle-{cycle:02d}-editorial-text.txt"
        editorial_txt.write_bytes(best.read_bytes())
        return editorial_txt
    return None


def preflight_markdown(
    target: Path,
    *,
    require_pdf_directives: bool = False,
) -> dict[str, Any]:
    """Run sub-second preflight checks on a Markdown section or assembled document."""
    text = target.read_text(encoding="utf-8")
    lines = text.splitlines()
    issues: list[dict[str, Any]] = []
    in_fence = False
    directive_kind: str | None = None
    directive_line = 0
    directive_buf: list[str] = []

    unsupported_glyphs = {
        "\u00b2": "^2",
        "\u00b3": "^3",
        "\u00b9": "^1",
        "\u014d": "o",
        "\u016b": "u",
        "\u221e": "Infini",
    }

    for lineno, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if directive_kind is not None:
            if stripped == ":::":
                payload = "\n".join(directive_buf).strip()
                if directive_kind == "figure" and payload:
                    try:
                        spec = json.loads(payload)
                        labels = spec.get("labels", []) if isinstance(spec, dict) else []
                        kind = spec.get("kind", "") if isinstance(spec, dict) else ""
                        if kind == "image" and isinstance(spec, dict):
                            raw_src = str(spec.get("src") or "").replace("\\", "/")
                            candidates = [
                                (target.parent / raw_src).resolve(),
                                (target.parent.parent / raw_src).resolve(),
                            ]
                            resolved = next((p for p in candidates if p.is_file()), None)
                            if resolved is None or resolved.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
                                issues.append({
                                    "code": "missing_figure_image",
                                    "line": directive_line,
                                    "message": f"figure image file not found or unsupported format (.png/.jpg required): {spec.get('src')}",
                                })
                        elif kind == "flow" and isinstance(labels, list) and labels:
                            max_chars = max(18, int(310 / max(len(labels), 1)) - 4)
                            for lbl in labels:
                                if isinstance(lbl, str) and len(lbl) > max_chars:
                                    issues.append({
                                        "code": "figure_label_overflow",
                                        "line": directive_line,
                                        "message": (
                                            f"flow figure label '{lbl[:32]}...' ({len(lbl)} chars) exceeds safe width "
                                            f"(~{max_chars} chars for {len(labels)} boxes); shorten label and move details to caption"
                                        ),
                                    })
                    except json.JSONDecodeError as exc:
                        issues.append({
                            "code": "invalid_figure_json",
                            "line": directive_line,
                            "message": f"invalid figure JSON: {exc}",
                        })
                elif directive_kind == "formula" and payload:
                    try:
                        spec = json.loads(payload)
                        expr = str(spec.get("expression", "")) if isinstance(spec, dict) else ""
                        for fline in expr.splitlines():
                            if len(fline) > 74:
                                issues.append({
                                    "code": "formula_line_too_wide",
                                    "line": directive_line,
                                    "message": f"formula line ({len(fline)} chars) exceeds 74 chars; insert \\n line break inside expression",
                                })
                    except json.JSONDecodeError as exc:
                        issues.append({
                            "code": "invalid_formula_json",
                            "line": directive_line,
                            "message": f"invalid formula JSON: {exc}",
                        })
                elif directive_kind in {"pullquote", "callout", "factbox"} and payload:
                    try:
                        spec = json.loads(payload)
                        if not isinstance(spec, dict):
                            raise ValueError("JSON object required")
                        if directive_kind == "pullquote" and not str(spec.get("quote") or "").strip():
                            issues.append({
                                "code": "invalid_pullquote_json",
                                "line": directive_line,
                                "message": "pullquote requires a non-empty 'quote' field",
                            })
                        elif directive_kind == "callout" and (
                            not str(spec.get("title") or "").strip() or not str(spec.get("body") or "").strip()
                        ):
                            issues.append({
                                "code": "invalid_callout_json",
                                "line": directive_line,
                                "message": "callout requires non-empty 'title' and 'body' fields",
                            })
                        elif directive_kind == "factbox":
                            items = spec.get("items")
                            if not str(spec.get("title") or "").strip() or not isinstance(items, list) or not items:
                                issues.append({
                                    "code": "invalid_factbox_json",
                                    "line": directive_line,
                                    "message": "factbox requires a non-empty 'title' and a non-empty 'items' list",
                                })
                    except (json.JSONDecodeError, ValueError) as exc:
                        issues.append({
                            "code": f"invalid_{directive_kind}_json",
                            "line": directive_line,
                            "message": f"invalid {directive_kind} JSON: {exc}",
                        })
                elif directive_kind == "cover-art" and payload:
                    try:
                        spec = json.loads(payload)
                        if not isinstance(spec, dict):
                            raise ValueError("JSON object required")
                        if spec.get("src"):
                            raw_src = str(spec["src"]).replace("\\", "/")
                            candidates = [
                                (target.parent / raw_src).resolve(),
                                (target.parent.parent / raw_src).resolve(),
                            ]
                            resolved = next((p for p in candidates if p.is_file()), None)
                            if resolved is None or resolved.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
                                issues.append({
                                    "code": "missing_cover_image",
                                    "line": directive_line,
                                    "message": f"cover-art image file not found or unsupported format: {spec.get('src')}",
                                })
                    except (json.JSONDecodeError, ValueError) as exc:
                        issues.append({
                            "code": "invalid_cover_art_json",
                            "line": directive_line,
                            "message": f"invalid cover-art JSON: {exc}",
                        })
                elif directive_kind == "back-cover" and payload:
                    try:
                        spec = json.loads(payload)
                        if not isinstance(spec, dict) or not str(spec.get("synopsis") or "").strip():
                            raise ValueError("back-cover requires a JSON object with non-empty 'synopsis'")
                    except (json.JSONDecodeError, ValueError) as exc:
                        issues.append({
                            "code": "invalid_back_cover_json",
                            "line": directive_line,
                            "message": f"invalid back-cover JSON: {exc}",
                        })
                elif directive_kind == "chapter-art" and payload:
                    try:
                        spec = json.loads(payload)
                        if not isinstance(spec, dict) or not str(spec.get("src") or "").strip():
                            raise ValueError("chapter-art requires a JSON object with non-empty 'src'")
                        raw_src = str(spec["src"]).replace("\\", "/")
                        candidates = [
                            (target.parent / raw_src).resolve(),
                            (target.parent.parent / raw_src).resolve(),
                        ]
                        resolved = next((p for p in candidates if p.is_file()), None)
                        if resolved is None or resolved.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
                            issues.append({
                                "code": "missing_chapter_art_image",
                                "line": directive_line,
                                "message": f"chapter-art image file not found or unsupported format: {spec.get('src')}",
                            })
                    except (json.JSONDecodeError, ValueError) as exc:
                        issues.append({
                            "code": "invalid_chapter_art_json",
                            "line": directive_line,
                            "message": f"invalid chapter-art JSON: {exc}",
                        })
                elif directive_kind == "footnote" and payload:
                    try:
                        spec = json.loads(payload)
                        if not isinstance(spec, dict) or not isinstance(spec.get("notes"), list) or not spec.get("notes"):
                            raise ValueError("footnote requires a JSON object with a non-empty 'notes' list")
                    except (json.JSONDecodeError, ValueError) as exc:
                        issues.append({
                            "code": "invalid_footnote_json",
                            "line": directive_line,
                            "message": f"invalid footnote JSON: {exc}",
                        })
                directive_kind = None
                directive_buf = []
                continue
            else:
                directive_buf.append(line)

        elif stripped == ":::cover-art" and lineno < len(lines) and lines[lineno].strip().startswith("{"):
            directive_kind = "cover-art"
            directive_line = lineno
            directive_buf = []
            continue
        elif stripped.startswith(":::back-cover"):
            directive_kind = "back-cover"
            directive_line = lineno
            directive_buf = []
            continue
        elif stripped.startswith(":::chapter-art"):
            directive_kind = "chapter-art"
            directive_line = lineno
            directive_buf = []
            continue
        elif stripped.startswith(":::figure"):
            directive_kind = "figure"
            directive_line = lineno
            directive_buf = []
            continue
        elif stripped.startswith(":::formula"):
            directive_kind = "formula"
            directive_line = lineno
            directive_buf = []
            continue
        elif stripped.startswith(":::pullquote"):
            directive_kind = "pullquote"
            directive_line = lineno
            directive_buf = []
            continue
        elif stripped.startswith(":::callout"):
            directive_kind = "callout"
            directive_line = lineno
            directive_buf = []
            continue
        elif stripped.startswith(":::factbox"):
            directive_kind = "factbox"
            directive_line = lineno
            directive_buf = []
            continue
        elif stripped.startswith(":::footnote"):
            directive_kind = "footnote"
            directive_line = lineno
            directive_buf = []
            continue

        if stripped.startswith("#"):
            m_head = re.match(r"^(#{1,3})\s+(.+)$", stripped)
            if m_head:
                h_level = len(m_head.group(1))
                h_text = m_head.group(2).strip()
                max_h_len = 90 if h_level == 1 else 105 if h_level == 2 else 120
                if len(h_text) > max_h_len:
                    issues.append({
                        "code": "heading_too_long",
                        "line": lineno,
                        "message": (
                            f"level-{h_level} heading ({len(h_text)} chars) exceeds {max_h_len} chars: '{h_text[:55]}...'; "
                            "write a concise editorial title instead of copying a long syllabus description"
                        ),
                    })

        if "`:::" in line:
            issues.append({
                "code": "inline_directive_colon",
                "line": lineno,
                "message": "inline `:::...` token found inside paragraph; remove `:::` from prose so PDF parser does not reject it",
            })
        if re.search(r"\$[^$\n]+\$|\\(?:cdot|sum|omega|theta|Delta|text|frac|propto)\b", line):
            issues.append({
                "code": "raw_latex_in_prose",
                "line": lineno,
                "message": "raw LaTeX math ($...$ or \\cdot/\\sum) found in prose; use plain Unicode text or a :::formula block",
            })
        if "\u2014" in line or "\u2013" in line:
            issues.append({
                "code": "long_dash",
                "line": lineno,
                "message": "long dash (U+2014/U+2013) found in authorial text; use commas, colons, or parentheses",
            })
        for bad_ch, replacement in unsupported_glyphs.items():
            if bad_ch in line:
                issues.append({
                    "code": "unsupported_pdf_glyph",
                    "line": lineno,
                    "message": f"unsupported PDF font glyph U+{ord(bad_ch):04X} ({bad_ch!r}); use '{replacement}' instead",
                })
        for ch in line:
            cp = ord(ch)
            if 0x3000 <= cp <= 0x9FFF:
                issues.append({
                    "code": "unsupported_cjk_glyph",
                    "line": lineno,
                    "message": f"CJK glyph U+{cp:04X} ({ch!r}) is missing from bundled PDF fonts; transliterate to Latin script",
                })
                break

    if require_pdf_directives:
        if ":::cover-art" not in text or ":::pagebreak" not in text:
            issues.append({
                "code": "missing_cover_directives",
                "line": 1,
                "message": "PDF document is missing :::cover-art and :::pagebreak at the cover boundary (causes cover-body style leak and unembedded /F1 fonts)",
            })

    return {
        "schema_version": 1,
        "path": str(target),
        "status": "fail" if issues else "pass",
        "issue_count": len(issues),
        "issues": issues[:50],
    }


def diff_cycles(swarm: Path, cycle: int) -> dict[str, Any]:
    """Compute a deterministic diff between cycle N-1 and cycle N."""
    root = swarm.resolve(strict=True)
    brief = load_brief(root)
    topics = topic_map(brief)
    reports = root / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    _sync_editorial_text_from_pdf(root, cycle, brief)
    primary = primary_markdown_deliverable(root, brief)
    curr_src_path = reports / f"cycle-{cycle:02d}-source.md"
    if primary.is_file() and not curr_src_path.is_file():
        curr_src_path.write_bytes(primary.read_bytes())
    curr_txt_path = reports / f"cycle-{cycle:02d}-editorial-text.txt"
    if not curr_txt_path.is_file() and primary.is_file():
        curr_txt_path.write_text(primary.read_text(encoding="utf-8"), encoding="utf-8")

    # Prefer comparing cycle-0(N-1)-source.md vs cycle-0N-source.md so headings (# / ##)
    # and line counts are always in the same format even before/after PDF rendering.
    curr_lines = (
        curr_src_path.read_text(encoding="utf-8").splitlines()
        if curr_src_path.is_file()
        else curr_txt_path.read_text(encoding="utf-8").splitlines()
        if curr_txt_path.is_file()
        else []
    )
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
    _sync_editorial_text_from_pdf(root, prev_cycle)
    prev_src_path = reports / f"cycle-{prev_cycle:02d}-source.md"
    prev_txt_path = reports / f"cycle-{prev_cycle:02d}-editorial-text.txt"
    if prev_src_path.is_file() and curr_src_path.is_file():
        prev_lines = prev_src_path.read_text(encoding="utf-8").splitlines()
        from_label = f"cycle-{prev_cycle:02d}-source.md"
        to_label = f"cycle-{cycle:02d}-source.md"
    else:
        prev_lines = prev_txt_path.read_text(encoding="utf-8").splitlines() if prev_txt_path.is_file() else []
        curr_lines = curr_txt_path.read_text(encoding="utf-8").splitlines() if curr_txt_path.is_file() else []
        from_label = f"cycle-{prev_cycle:02d}-editorial-text.txt"
        to_label = f"cycle-{cycle:02d}-editorial-text.txt"

    diff_lines = list(
        difflib.unified_diff(
            prev_lines,
            curr_lines,
            fromfile=from_label,
            tofile=to_label,
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
    if section_files and (
        from_sections
        or not primary.is_file()
        or any(p.stat().st_mtime > primary.stat().st_mtime for p in section_files)
    ):
        parts = [path.read_text(encoding="utf-8").strip() for path in section_files if path.is_file()]
        primary.write_text("\n\n".join(part for part in parts if part) + "\n", encoding="utf-8")
    if not primary.is_file():
        raise InputError(f"primary deliverable does not exist: {primary.relative_to(root).as_posix()}")

    reports = root / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    source_snapshot = reports / f"cycle-{cycle:02d}-source.md"
    source_snapshot.write_bytes(primary.read_bytes())
    editorial_txt = reports / f"cycle-{cycle:02d}-editorial-text.txt"
    synced = _sync_editorial_text_from_pdf(root, cycle, brief)
    if synced is None and (from_sections or not editorial_txt.is_file()):
        editorial_txt.write_bytes(primary.read_bytes())

    has_pdf = bool(brief.get("pdf_engine")) or any(
        isinstance(item, str) and item.lower().endswith(".pdf")
        for item in (brief.get("deliverables") or [])
    )
    preflight = preflight_markdown(primary, require_pdf_directives=has_pdf)
    diff_payload = diff_cycles(root, cycle)
    return {
        "schema_version": 1,
        "cycle": cycle,
        "primary": descriptor(root, primary),
        "editorial_text": descriptor(root, editorial_txt),
        "preflight": preflight,
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
        if "cycle" not in report:
            report["cycle"] = cycle
        reviewer = str(report.get("reviewer") or "")
        for item in report.get("topics", []):
            if isinstance(item, dict):
                if "topic" not in item and "id" in item:
                    item["topic"] = item.pop("id")
                item.pop("title", None)
                if "action" not in item or item["action"] is None:
                    item["action"] = ""
        if isinstance(report.get("editorial"), dict):
            ed_obj = report["editorial"]
            raw_surfaces = ed_obj.get("surfaces")
            if isinstance(raw_surfaces, dict):
                ed_obj["surfaces"] = [
                    dict(val, surface=str(key))
                    for key, val in raw_surfaces.items()
                    if isinstance(val, dict)
                ]
            top_findings = list(ed_obj.get("findings") or [])
            for surf in ed_obj.get("surfaces", []):
                if not isinstance(surf, dict):
                    continue
                nested_findings = surf.pop("findings", [])
                if isinstance(nested_findings, list):
                    for nf in nested_findings:
                        if isinstance(nf, dict):
                            top_findings.append(nf)
                if "not_applicable" not in surf:
                    if not surf.get("location"):
                        surf["location"] = f"Superfície {surf.get('surface', 'document')}"
                    if "action" not in surf:
                        surf["action"] = ""
            for finding in top_findings:
                if isinstance(finding, dict):
                    if "reason" not in finding and "justification" in finding:
                        finding["reason"] = finding.pop("justification")
                    sev_raw = str(finding.get("severity") or "blocking").lower()
                    finding["severity"] = "minor" if sev_raw == "minor" else "blocking"
                    finding.pop("surface", None)
            ed_obj["findings"] = top_findings
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
            ed.setdefault("schema_version", 1)
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

        for row in report.get("topics", []):
            if not isinstance(row, dict) or "grade" not in row:
                continue
            g_norm = normalize_grade(row.get("grade"))
            act_str = str(row.get("action") or "").strip()
            if GRADE_INDEX[g_norm] < GRADE_INDEX["A"]:
                if not act_str or act_str.lower() == "carry_forward" or re.match(r"^aprovad[oa]\b", act_str, re.IGNORECASE):
                    raise InputError(
                        f"{path.name}: topic {row.get('topic')} has blocking grade {g_norm} "
                        f"but non-blocking action '{act_str[:60]}' (A- or lower requires an actionable blocking defect)"
                    )

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

    p_preflight = subparsers.add_parser("preflight", help="Run fast preflight checks on a Markdown file")
    p_preflight.add_argument("target", type=Path, help="Markdown file to check")
    p_preflight.add_argument("--pdf", action="store_true", help="Require cover-art and pagebreak directives")

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
        elif args.command == "preflight":
            result = preflight_markdown(args.target, require_pdf_directives=args.pdf)
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
    print(json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True))
    pf = result if args.command == "preflight" else result.get("preflight") if isinstance(result, dict) else None
    if isinstance(pf, dict) and pf.get("status") == "fail":
        issues = pf.get("issues") or []
        summary = ", ".join(f"L{it.get('line')}:{it.get('code')}" for it in issues[:10])
        print(
            f"PREFLIGHT FAIL ({pf.get('issue_count', len(issues))} issues): {summary}",
            file=sys.stderr,
        )
        if args.command == "preflight":
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
