"""Parse authored Markdown into a bounded, renderer-independent document model."""

from __future__ import annotations

import hashlib
import json
import math
import re
from urllib.parse import unquote
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from scripts.checks.common import InputError
from scripts.checks.verify_tables import MARKER

MAX_SOURCE_BYTES = 5 * 1024 * 1024
ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,79}$")
INLINE_REF_RE = re.compile(r"\s*(?:\[\s*F\d{1,3}(?:\s*[,;]\s*F\d{1,3})*\s*\])+")
STANDALONE_REF_RE = re.compile(r"^\[\s*F\d{1,3}\s*\]$")


def clean_editorial_text(text: str, *, strip_refs: bool = False) -> str:
    if not strip_refs or not text:
        return text
    if STANDALONE_REF_RE.fullmatch(text.strip()):
        return text
    cleaned = INLINE_REF_RE.sub("", text)
    cleaned = re.sub(r" +([,.;:?!])", r"\1", cleaned)
    return cleaned


def clean_inline_runs(runs: list[Inline], *, strip_refs: bool = False) -> list[Inline]:
    if not strip_refs or not runs:
        return runs
    combined = "".join(r.text for r in runs).strip()
    if STANDALONE_REF_RE.fullmatch(combined):
        return runs
    out: list[Inline] = []
    for run in runs:
        if run.code:
            out.append(run)
            continue
        cleaned = INLINE_REF_RE.sub("", run.text)
        cleaned = re.sub(r" +([,.;:?!])", r"\1", cleaned)
        if cleaned and cleaned[0] in ",.;:?!" and out and not out[-1].code:
            out[-1].text = out[-1].text.rstrip(" ")
        if cleaned or run.text == "":
            out.append(Inline(cleaned, run.bold, run.italic, run.code, run.link))
    return out or [Inline("")]


def check_editorial_title(text: str, name: str, *, max_len: int = 120) -> str:
    value = nonempty(text, name)
    if len(value) > max_len:
        raise InputError(f"{name}: title ({len(value)} chars) exceeds {max_len} chars; write a concise editorial title")
    return value


@dataclass
class Inline:
    text: str
    bold: bool = False
    italic: bool = False
    code: bool = False
    link: str | None = None


@dataclass
class Block:
    id: str
    kind: str
    runs: list[Inline] = field(default_factory=list)
    level: int = 0
    rows: list[list[list[Inline]]] = field(default_factory=list)
    spec: dict[str, Any] = field(default_factory=dict)
    line: int = 0

    @property
    def text(self) -> str:
        return "".join(run.text for run in self.runs)


@dataclass
class Document:
    title: str
    blocks: list[Block]
    source_name: str
    source_sha256: str
    expected: dict[str, dict[str, Any]]
    links: list[str]


def nonempty(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 50000:
        raise InputError(f"{name} must be non-empty bounded text")
    return value


def figure_spec(data: Any, name: str, source_dir: Path | None = None, *, strip_refs: bool = False) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise InputError(f"figure {name}: JSON object required")
    if data.get("kind") == "image":
        allowed_img = {"kind", "title", "caption", "src", "max_height", "credit"}
        if set(data) - allowed_img:
            raise InputError(f"figure {name}: unsupported fields {sorted(set(data) - allowed_img)}")
        for key in ("title", "caption", "src"):
            nonempty(data.get(key), f"figure {name} {key}")
        if "credit" in data and data["credit"] is not None:
            nonempty(data["credit"], f"figure {name} credit")
        raw_src = str(data["src"]).replace("\\", "/")
        candidates = []
        if source_dir is not None:
            candidates.append((source_dir / raw_src).resolve())
            candidates.append((source_dir.parent / raw_src).resolve())
        resolved = next((p for p in candidates if p.is_file()), None)
        if resolved is None or resolved.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
            raise InputError(f"figure {name}: image file not found or unsupported format: {data['src']}")
        max_h = data.get("max_height", 240)
        if type(max_h) not in (int, float) or not (40 <= float(max_h) <= 520):
            raise InputError(f"figure {name}: max_height must be between 40 and 520")
        out = dict(data)
        out["title"] = clean_editorial_text(out["title"], strip_refs=strip_refs)
        out["caption"] = clean_editorial_text(out["caption"], strip_refs=strip_refs)
        if out.get("credit"):
            out["credit"] = clean_editorial_text(out["credit"], strip_refs=strip_refs)
        out["resolved_src"] = str(resolved)
        out["labels"] = []
        return out
    allowed = {"kind", "title", "caption", "labels", "values", "display_values", "unit"}
    if set(data) - allowed:
        raise InputError(f"figure {name}: unsupported fields {sorted(set(data) - allowed)}")
    if data.get("kind") not in ("flow", "layers", "bars"):
        raise InputError(f"figure {name}: kind must be flow, layers, bars or image")
    for key in ("title", "caption"):
        nonempty(data.get(key), f"figure {name} {key}")
    labels = data.get("labels")
    if not isinstance(labels, list) or not 1 <= len(labels) <= 12:
        raise InputError(f"figure {name}: 1 to 12 labels are required")
    for label in labels:
        nonempty(label, f"figure {name} label")
    if data["kind"] == "bars":
        values = data.get("values")
        display = data.get("display_values")
        if not isinstance(values, list) or len(values) != len(labels) or any(
            type(value) not in (int, float) or not math.isfinite(value) or value < 0 for value in values
        ):
            raise InputError(f"figure {name}: finite non-negative values must match labels")
        if not any(value > 0 for value in values):
            raise InputError(f"figure {name}: at least one positive value is required")
        if not isinstance(display, list) or len(display) != len(labels):
            raise InputError(f"figure {name}: source-authored display_values must match values")
        for value in display:
            nonempty(value, f"figure {name} display value")
        nonempty(data.get("unit"), f"figure {name} unit")
    elif set(data) & {"values", "display_values", "unit"}:
        raise InputError(f"figure {name}: numeric fields apply only to bars")
    out = dict(data)
    out["title"] = clean_editorial_text(out["title"], strip_refs=strip_refs)
    out["caption"] = clean_editorial_text(out["caption"], strip_refs=strip_refs)
    out["labels"] = [clean_editorial_text(lbl, strip_refs=strip_refs) for lbl in out["labels"]]
    return out


def pullquote_spec(data: Any, name: str, *, strip_refs: bool = False) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise InputError(f"pullquote {name}: JSON object required")
    allowed = {"quote", "attribution"}
    if set(data) - allowed:
        raise InputError(f"pullquote {name}: unsupported fields {sorted(set(data) - allowed)}")
    quote = clean_editorial_text(nonempty(data.get("quote"), f"pullquote {name} quote"), strip_refs=strip_refs)
    attribution = (
        clean_editorial_text(nonempty(data["attribution"], f"pullquote {name} attribution"), strip_refs=strip_refs)
        if data.get("attribution")
        else ""
    )
    return {"quote": quote, "attribution": attribution}


def callout_spec(data: Any, name: str, *, strip_refs: bool = False) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise InputError(f"callout {name}: JSON object required")
    allowed = {"kind", "title", "body"}
    if set(data) - allowed:
        raise InputError(f"callout {name}: unsupported fields {sorted(set(data) - allowed)}")
    kind = str(data.get("kind") or "info")
    if kind not in {"intro", "curiosity", "backstage", "key-concept", "note", "info"}:
        raise InputError(f"callout {name}: kind must be intro, curiosity, backstage, key-concept, note or info")
    title = clean_editorial_text(nonempty(data.get("title"), f"callout {name} title"), strip_refs=strip_refs)
    body = clean_editorial_text(nonempty(data.get("body"), f"callout {name} body"), strip_refs=strip_refs)
    return {"kind": kind, "title": title, "body": body}


def factbox_spec(data: Any, name: str, *, strip_refs: bool = False) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise InputError(f"factbox {name}: JSON object required")
    allowed = {"title", "items", "caption"}
    if set(data) - allowed:
        raise InputError(f"factbox {name}: unsupported fields {sorted(set(data) - allowed)}")
    title = clean_editorial_text(nonempty(data.get("title"), f"factbox {name} title"), strip_refs=strip_refs)
    raw_items = data.get("items")
    if not isinstance(raw_items, list) or not 1 <= len(raw_items) <= 20:
        raise InputError(f"factbox {name}: 1 to 20 items are required")
    items = []
    for index, item in enumerate(raw_items):
        if not isinstance(item, dict) or set(item) != {"label", "value"}:
            raise InputError(f"factbox {name} item {index}: label and value are required")
        items.append({
            "label": clean_editorial_text(nonempty(item.get("label"), f"factbox {name} item {index} label"), strip_refs=strip_refs),
            "value": clean_editorial_text(nonempty(item.get("value"), f"factbox {name} item {index} value"), strip_refs=strip_refs),
        })
    caption = (
        clean_editorial_text(nonempty(data["caption"], f"factbox {name} caption"), strip_refs=strip_refs)
        if data.get("caption")
        else ""
    )
    return {"title": title, "items": items, "caption": caption}


def back_cover_spec(data: Any, name: str, *, strip_refs: bool = False) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise InputError(f"back-cover {name}: JSON object required")
    allowed = {"kicker", "title", "subtitle", "synopsis", "blurbs", "highlights", "colophon"}
    if set(data) - allowed:
        raise InputError(f"back-cover {name}: unsupported fields {sorted(set(data) - allowed)}")
    synopsis = clean_editorial_text(nonempty(data.get("synopsis"), f"back-cover {name} synopsis"), strip_refs=strip_refs)
    if len(synopsis) > 520:
        raise InputError(f"back-cover {name}: synopsis ({len(synopsis)} chars) exceeds 520 chars; keep back-cover synopsis concise")
    out: dict[str, Any] = {"synopsis": synopsis}
    for key in ("kicker", "title", "subtitle", "colophon"):
        if data.get(key):
            val = clean_editorial_text(nonempty(data[key], f"back-cover {name} {key}"), strip_refs=strip_refs)
            if re.search(r"\bde\s+estante\b", val, re.IGNORECASE):
                raise InputError(f"back-cover {name} {key}: do not use briefing meta-phrase 'de estante'")
            out[key] = val
    raw_blurbs = data.get("blurbs") or []
    if not isinstance(raw_blurbs, list) or len(raw_blurbs) > 2:
        raise InputError(f"back-cover {name}: blurbs must be a list of at most 2 short items")
    blurbs = []
    for idx, item in enumerate(raw_blurbs):
        if isinstance(item, str):
            q = clean_editorial_text(nonempty(item, f"back-cover {name} blurb {idx}"), strip_refs=strip_refs)
            blurbs.append({"quote": q, "source": ""})
        elif isinstance(item, dict) and set(item) <= {"quote", "source"} and "quote" in item:
            blurbs.append({
                "quote": clean_editorial_text(nonempty(item["quote"], f"back-cover {name} blurb {idx} quote"), strip_refs=strip_refs),
                "source": clean_editorial_text(nonempty(item["source"], f"back-cover {name} blurb {idx} source"), strip_refs=strip_refs) if item.get("source") else "",
            })
        else:
            raise InputError(f"back-cover {name} blurb {idx}: requires quote and optional source")
    out["blurbs"] = blurbs
    raw_hl = data.get("highlights") or []
    if not isinstance(raw_hl, list) or len(raw_hl) > 4:
        raise InputError(f"back-cover {name}: highlights must be a list of up to 4 strings")
    out["highlights"] = [clean_editorial_text(nonempty(h, f"back-cover {name} highlight {i}"), strip_refs=strip_refs) for i, h in enumerate(raw_hl)]
    return out


def cover_art_spec(data: Any, source_dir: Path | None = None) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise InputError("cover-art: JSON object required when payload is provided")
    allowed = {"style", "src", "accent", "secondary", "motif"}
    if set(data) - allowed:
        raise InputError(f"cover-art: unsupported fields {sorted(set(data) - allowed)}")
    out = dict(data)
    if "src" in data and data["src"] is not None:
        nonempty(data["src"], "cover-art src")
        raw_src = str(data["src"]).replace("\\", "/")
        candidates = []
        if source_dir is not None:
            candidates.append((source_dir / raw_src).resolve())
            candidates.append((source_dir.parent / raw_src).resolve())
        resolved = next((p for p in candidates if p.is_file()), None)
        if resolved is None or resolved.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
            raise InputError(f"cover-art: image file not found or unsupported format: {data['src']}")
        out["resolved_src"] = str(resolved)
    return out


def chapter_art_spec(data: Any, name: str, source_dir: Path | None = None, *, strip_refs: bool = False) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise InputError(f"chapter-art {name}: JSON object required")
    allowed = {"src", "caption", "credit", "kicker", "max_height"}
    if set(data) - allowed:
        raise InputError(f"chapter-art {name}: unsupported fields {sorted(set(data) - allowed)}")
    src = nonempty(data.get("src"), f"chapter-art {name} src")
    raw_src = str(src).replace("\\", "/")
    candidates = []
    if source_dir is not None:
        candidates.append((source_dir / raw_src).resolve())
        candidates.append((source_dir.parent / raw_src).resolve())
    resolved = next((p for p in candidates if p.is_file()), None)
    if resolved is None or resolved.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
        raise InputError(f"chapter-art {name}: image file not found or unsupported format: {src}")
    out: dict[str, Any] = {"src": raw_src, "resolved_src": str(resolved)}
    for key in ("caption", "credit", "kicker"):
        if data.get(key):
            out[key] = clean_editorial_text(nonempty(data[key], f"chapter-art {name} {key}"), strip_refs=strip_refs)
    if "max_height" in data and data["max_height"] is not None:
        out["max_height"] = float(data["max_height"])
    return out


def footnote_spec(data: Any, name: str, *, strip_refs: bool = False) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise InputError(f"footnote {name}: JSON object required")
    allowed = {"notes"}
    if set(data) - allowed:
        raise InputError(f"footnote {name}: unsupported fields {sorted(set(data) - allowed)}; use 'notes' list")
    raw_notes = data.get("notes")
    if not isinstance(raw_notes, list) or not 1 <= len(raw_notes) <= 8:
        raise InputError(f"footnote {name}: 'notes' must be a list of 1 to 8 footnote entries")
    notes = []
    for idx, item in enumerate(raw_notes):
        if not isinstance(item, dict) or not set(item) <= {"number", "term", "text"} or "text" not in item:
            raise InputError(f"footnote {name} item {idx}: requires 'text' and optional 'number', 'term'")
        num_str = str(item.get("number") or (idx + 1)).strip()
        term_str = clean_editorial_text(nonempty(item["term"], f"footnote {name} item {idx} term"), strip_refs=strip_refs) if item.get("term") else ""
        txt_str = clean_editorial_text(nonempty(item["text"], f"footnote {name} item {idx} text"), strip_refs=strip_refs)
        notes.append({"number": num_str, "term": term_str, "text": txt_str})
    return {"notes": notes}


def directive(state, start, end, silent):
    line = state.src[state.bMarks[start] + state.tShift[start]:state.eMarks[start]].strip()
    annotation = MARKER.fullmatch(line)
    if annotation:
        words = annotation[1].split()
        if not words or words[0].lower() not in {"weighted", "sum", "percent", "porcent", "rows", "match_keys", "refs", "external_json"}:
            raise InputError(f"line {start + 1}: unsupported table-check annotation")
        if not silent:
            state.line = start + 1
        return True
    match = re.fullmatch(
        r":::(cover-art|back-cover|toc|pagebreak|(?:chapter-art|figure|formula|pullquote|callout|factbox|footnote)(?: [A-Za-z][\w-]*)?)",
        line,
    )
    if not match:
        return False
    if silent:
        return True
    kind, _, name = match[1].partition(" ")
    stop = start + 1
    payload = ""
    if kind in {"cover-art", "back-cover", "chapter-art"} and stop < end:
        next_line = state.src[state.bMarks[stop]:state.eMarks[stop]].strip()
        if next_line.startswith("{"):
            lines = []
            while stop < end:
                content = state.src[state.bMarks[stop]:state.eMarks[stop]]
                if content.strip() == ":::":
                    break
                lines.append(content)
                stop += 1
            if stop == end:
                raise InputError(f"line {start + 1}: unclosed {kind} block")
            stop += 1
            payload = "\n".join(lines)
        elif kind in {"back-cover", "chapter-art"}:
            raise InputError(f"line {start + 1}: {kind} requires a JSON payload")
    elif kind in {"back-cover", "chapter-art"}:
        raise InputError(f"line {start + 1}: {kind} requires a JSON payload")
    elif kind in {"figure", "formula", "pullquote", "callout", "factbox", "footnote"}:
        if not ID.fullmatch(name):
            raise InputError(f"line {start + 1}: {kind} requires a unique identifier")
        lines = []
        while stop < end:
            content = state.src[state.bMarks[stop]:state.eMarks[stop]]
            if content.strip() == ":::":
                break
            lines.append(content)
            stop += 1
        if stop == end:
            raise InputError(f"line {start + 1}: unclosed {kind} block")
        stop += 1
        payload = "\n".join(lines)
    token = state.push("docswarm", "", 0)
    token.meta = {"kind": kind, "name": name, "payload": payload}
    token.map = [start, stop]
    state.line = stop
    return True


def inlines(token, *, strip_refs: bool = False) -> list[Inline]:
    runs = []
    bold = italic = 0
    link = None
    for item in token.children or []:
        if item.type == "strong_open":
            bold += 1
        elif item.type == "strong_close":
            bold -= 1
        elif item.type == "em_open":
            italic += 1
        elif item.type == "em_close":
            italic -= 1
        elif item.type == "link_open":
            link = item.attrGet("href")
            if not isinstance(link, str) or not (re.match(r"^https?://", link) or (link.startswith("#") and len(link) > 1)):
                raise InputError("only explicit http(s) or local heading links are supported")
        elif item.type == "link_close":
            link = None
        elif item.type in {"text", "code_inline", "softbreak", "hardbreak"}:
            value = " " if item.type == "softbreak" else "\n" if item.type == "hardbreak" else item.content
            runs.append(Inline(value, bool(bold), bool(italic), item.type == "code_inline", link))
        else:
            raise InputError(f"unsupported inline Markdown: {item.type}; supply a supported authored block")
    return clean_inline_runs(runs, strip_refs=strip_refs)


def read_source(path: Path, *, profile: str | None = None) -> Document:
    from markdown_it import MarkdownIt

    strip_refs = profile in {"book", "almanac", "magazine"}
    with path.open("rb") as stream:
        raw = stream.read(MAX_SOURCE_BYTES + 1)
    if len(raw) > MAX_SOURCE_BYTES:
        raise InputError("PDF Markdown exceeds the 5 MiB limit")
    source = raw.decode("utf-8-sig")
    if "\x00" in source:
        raise InputError("NUL characters are not supported in PDF sources")
    # Tokenize raw HTML so unsupported tags/comments fail instead of becoming customer text.
    md = MarkdownIt("commonmark", {"html": True, "typographer": False}).enable("table")
    md.block.ruler.before("fence", "docswarm", directive, {"alt": ["paragraph", "reference", "blockquote", "list"]})
    tokens = md.parse(source)
    blocks = []
    expected = {}
    links = []
    used_ids = set()
    anchors = {}
    list_stack = []
    list_marker = None
    quote_depth = 0
    first_title = None
    on_cover = True
    index = 0

    def add(block: Block):
        blocks.append(block)
        if block.runs:
            expected[block.id] = {"kind": block.kind, "text": block.text, "line": block.line}
            for run in block.runs:
                if run.link and run.link not in links:
                    links.append(run.link)

    while index < len(tokens):
        token = tokens[index]
        line = (token.map or [0])[0] + 1
        identifier = f"b{len(blocks) + 1:04d}"
        if token.type == "docswarm":
            kind, name, payload = (token.meta[key] for key in ("kind", "name", "payload"))
            if kind in {"figure", "formula", "pullquote", "callout", "factbox", "footnote"}:
                if name in used_ids:
                    raise InputError(f"duplicate block identifier {name}")
                used_ids.add(name)
                try:
                    spec = json.loads(payload)
                except json.JSONDecodeError as exc:
                    raise InputError(f"line {line}: {kind} requires valid JSON: {exc}") from exc
                if kind == "figure":
                    spec = figure_spec(spec, name, source_dir=path.resolve().parent, strip_refs=strip_refs)
                    add(Block(f"{identifier}-title", "figure-title", [Inline(spec["title"])], line=line))
                    add(Block(identifier, "figure", spec=spec, line=line))
                    if spec.get("credit"):
                        add(Block(f"{identifier}-credit", "caption", [Inline(spec["credit"], italic=True)], line=line))
                    add(Block(f"{identifier}-caption", "caption", [Inline(spec["caption"])], line=line))
                    for position, label in enumerate(spec["labels"]):
                        expected[f"{identifier}-label-{position}"] = {"kind": "figure-label", "text": label, "line": line}
                    for position, value in enumerate(spec.get("display_values", [])):
                        expected[f"{identifier}-value-{position}"] = {"kind": "figure-value", "text": value, "line": line}
                    if spec.get("unit"):
                        expected[f"{identifier}-unit"] = {"kind": "figure-unit", "text": spec["unit"], "line": line}
                elif kind == "formula":
                    if not isinstance(spec, dict) or set(spec) != {"expression", "caption"}:
                        raise InputError(f"line {line}: formula requires expression and caption only")
                    expression = nonempty(spec["expression"], "formula expression")
                    add(Block(identifier, "formula", [Inline(expression)], line=line))
                    add(Block(f"{identifier}-caption", "caption", [Inline(clean_editorial_text(nonempty(spec["caption"], "formula caption"), strip_refs=strip_refs))], line=line))
                elif kind == "pullquote":
                    spec = pullquote_spec(spec, name, strip_refs=strip_refs)
                    add(Block(identifier, "pullquote", spec=spec, line=line))
                    expected[f"{identifier}-quote"] = {"kind": "pullquote-quote", "text": spec["quote"], "line": line}
                    if spec.get("attribution"):
                        expected[f"{identifier}-attr"] = {"kind": "pullquote-attr", "text": spec["attribution"], "line": line}
                elif kind == "callout":
                    spec = callout_spec(spec, name, strip_refs=strip_refs)
                    add(Block(identifier, "callout", spec=spec, line=line))
                    expected[f"{identifier}-title"] = {"kind": "callout-title", "text": spec["title"], "line": line}
                    expected[f"{identifier}-body"] = {"kind": "callout-body", "text": spec["body"], "line": line}
                elif kind == "footnote":
                    spec = footnote_spec(spec, name, strip_refs=strip_refs)
                    add(Block(identifier, "footnote", spec=spec, line=line))
                    for idx, n_item in enumerate(spec["notes"]):
                        expected[f"{identifier}-n{idx}"] = {"kind": "footnote-item", "text": n_item["text"], "line": line}
                else:
                    spec = factbox_spec(spec, name, strip_refs=strip_refs)
                    add(Block(identifier, "factbox", spec=spec, line=line))
                    expected[f"{identifier}-title"] = {"kind": "factbox-title", "text": spec["title"], "line": line}
                    for idx, item in enumerate(spec["items"]):
                        expected[f"{identifier}-r{idx}-k"] = {"kind": "factbox-label", "text": item["label"], "line": line}
                        expected[f"{identifier}-r{idx}-v"] = {"kind": "factbox-value", "text": item["value"], "line": line}
                    if spec.get("caption"):
                        add(Block(f"{identifier}-caption", "caption", [Inline(spec["caption"])], line=line))
            elif kind == "back-cover":
                on_cover = False
                try:
                    raw_spec = json.loads(payload)
                except json.JSONDecodeError as exc:
                    raise InputError(f"line {line}: back-cover requires valid JSON: {exc}") from exc
                spec = back_cover_spec(raw_spec, identifier, strip_refs=strip_refs)
                if not spec.get("title") and first_title:
                    spec["title"] = first_title
                add(Block(identifier, "back-cover", spec=spec, line=line))
                if spec.get("kicker"):
                    expected[f"{identifier}-kicker"] = {"kind": "back-cover-kicker", "text": spec["kicker"], "line": line}
                if spec.get("title"):
                    expected[f"{identifier}-title"] = {"kind": "back-cover-title", "text": spec["title"], "line": line}
                if spec.get("subtitle"):
                    expected[f"{identifier}-subtitle"] = {"kind": "back-cover-subtitle", "text": spec["subtitle"], "line": line}
                expected[f"{identifier}-synopsis"] = {"kind": "back-cover-synopsis", "text": spec["synopsis"], "line": line}
                for idx, blurb in enumerate(spec.get("blurbs", [])):
                    expected[f"{identifier}-blurb-{idx}-q"] = {"kind": "back-cover-blurb", "text": blurb["quote"], "line": line}
                    if blurb.get("source"):
                        expected[f"{identifier}-blurb-{idx}-s"] = {"kind": "back-cover-blurb-source", "text": blurb["source"], "line": line}
                for idx, hl in enumerate(spec.get("highlights", [])):
                    expected[f"{identifier}-hl-{idx}"] = {"kind": "back-cover-highlight", "text": hl, "line": line}
                if spec.get("colophon"):
                    expected[f"{identifier}-colophon"] = {"kind": "back-cover-colophon", "text": spec["colophon"], "line": line}
            elif kind == "chapter-art":
                on_cover = False
                try:
                    raw_spec = json.loads(payload)
                except json.JSONDecodeError as exc:
                    raise InputError(f"line {line}: chapter-art requires valid JSON: {exc}") from exc
                spec = chapter_art_spec(raw_spec, name or identifier, source_dir=path.resolve().parent, strip_refs=strip_refs)
                add(Block(identifier, "chapter-art", spec=spec, line=line))
                if spec.get("caption"):
                    expected[f"{identifier}-caption"] = {"kind": "chapter-art-caption", "text": spec["caption"], "line": line}
            else:
                if kind == "pagebreak":
                    on_cover = False
                spec = {}
                if kind == "cover-art" and payload.strip():
                    try:
                        raw_spec = json.loads(payload)
                    except json.JSONDecodeError as exc:
                        raise InputError(f"line {line}: cover-art requires valid JSON: {exc}") from exc
                    spec = cover_art_spec(raw_spec, source_dir=path.resolve().parent)
                add(Block(identifier, kind, spec=spec, line=line))
        elif token.type == "heading_open":
            level = int(token.tag[1])
            runs = inlines(tokens[index + 1], strip_refs=strip_refs)
            kind = "cover-title" if first_title is None else "cover-subtitle" if on_cover and level == 2 else "heading"
            label = "".join(run.text for run in runs)
            if kind == "cover-title":
                check_editorial_title(label, f"line {line} cover title", max_len=95)
            elif kind == "cover-subtitle":
                check_editorial_title(label, f"line {line} cover subtitle", max_len=135)
            elif level == 1:
                check_editorial_title(label, f"line {line} chapter heading", max_len=95)
            else:
                check_editorial_title(label, f"line {line} section heading", max_len=115)
            if first_title is None:
                if level != 1:
                    raise InputError("the first heading must be the document's level-one title")
                first_title = label
            anchor = re.sub(r"[^\w -]", "", label.lower()).replace(" ", "-")
            count = anchors.get(anchor, 0)
            anchors[anchor] = count + 1
            if count:
                anchor = f"{anchor}-{count}"
            add(Block(identifier, kind, runs, level=level, spec={"anchor": anchor}, line=line))
            index += 2
        elif token.type == "paragraph_open":
            runs = inlines(tokens[index + 1], strip_refs=strip_refs)
            if any(run.text.lstrip().startswith(":::") for run in runs):
                raise InputError(f"line {line}: unsupported or malformed document directive")
            kind = "cover-body" if on_cover else "list" if list_stack else "quote" if quote_depth else "paragraph"
            add(Block(identifier, kind, runs, spec={"marker": list_marker, "depth": len(list_stack)}, line=line))
            list_marker = None
            index += 2
        elif token.type in {"bullet_list_open", "ordered_list_open"}:
            list_stack.append({"ordered": token.type == "ordered_list_open", "next": int(token.attrGet("start") or 1)})
        elif token.type == "list_item_open":
            current = list_stack[-1]
            list_marker = f"{current['next']}." if current["ordered"] else "•"
            current["next"] += 1
        elif token.type in {"bullet_list_close", "ordered_list_close"}:
            list_stack.pop()
        elif token.type in {"blockquote_open", "blockquote_close"}:
            quote_depth += 1 if token.type == "blockquote_open" else -1
        elif token.type in {"fence", "code_block"}:
            if on_cover:
                on_cover = False
            if token.info.strip() in {"math", "formula"}:
                add(Block(identifier, "formula", [Inline(token.content.rstrip("\n"))], line=line))
            else:
                add(Block(identifier, "code", [Inline(token.content.rstrip("\n"), code=True)], line=line))
        elif token.type == "table_open":
            rows = []
            row = []
            index += 1
            while index < len(tokens) and tokens[index].type != "table_close":
                current = tokens[index]
                if current.type == "tr_open":
                    row = []
                elif current.type == "inline":
                    row.append(inlines(current, strip_refs=strip_refs))
                elif current.type == "tr_close":
                    rows.append(row)
                index += 1
            if not rows or any(len(row) != len(rows[0]) for row in rows) or len(rows[0]) > 12:
                raise InputError(f"line {line}: invalid or overly wide table")
            block = Block(identifier, "table", rows=rows, line=line)
            add(block)
            for r, cells in enumerate(rows):
                for c, runs in enumerate(cells):
                    expected[f"{identifier}-r{r}-c{c}"] = {"kind": "table-cell", "text": "".join(run.text for run in runs), "line": line}
                    for run in runs:
                        if run.link and run.link not in links:
                            links.append(run.link)
        elif token.type == "hr":
            add(Block(identifier, "rule", line=line))
        elif token.type not in {"list_item_close", "paragraph_close", "heading_close"}:
            raise InputError(f"line {line}: unsupported Markdown block {token.type}")
        index += 1
    if not first_title or not first_title.strip():
        raise InputError("source requires an authored # title")
    if len(blocks) > 10000:
        raise InputError("source exceeds 10,000 layout blocks")
    valid_anchors = {block.spec["anchor"] for block in blocks if "anchor" in block.spec}
    for link in links:
        if link.startswith("#") and unquote(link[1:]) not in valid_anchors:
            raise InputError(f"unresolved source heading link: {link}")
    if not any(block.kind == "pagebreak" for block in blocks):
        for block in blocks:
            if block.kind == "cover-body":
                block.kind = "paragraph"
            elif block.kind == "cover-subtitle":
                block.kind = "heading"
    return Document(first_title, blocks, path.name, hashlib.sha256(raw).hexdigest(), expected, links)
