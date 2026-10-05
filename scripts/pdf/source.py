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


def figure_spec(data: Any, name: str) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise InputError(f"figure {name}: JSON object required")
    allowed = {"kind", "title", "caption", "labels", "values", "display_values", "unit"}
    if set(data) - allowed:
        raise InputError(f"figure {name}: unsupported fields {sorted(set(data) - allowed)}")
    if data.get("kind") not in ("flow", "layers", "bars"):
        raise InputError(f"figure {name}: kind must be flow, layers or bars")
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
    return data


def directive(state, start, end, silent):
    line = state.src[state.bMarks[start] + state.tShift[start]:state.eMarks[start]].strip()
    annotation = MARKER.fullmatch(line)
    if annotation:
        words = annotation[1].split()
        if not words or words[0].lower() not in {"weighted", "sum", "percent"}:
            raise InputError(f"line {start + 1}: unsupported table-check annotation")
        if not silent:
            state.line = start + 1
        return True
    match = re.fullmatch(r":::(cover-art|toc|pagebreak|figure(?: [A-Za-z][\w-]*)?|formula(?: [A-Za-z][\w-]*)?)", line)
    if not match:
        return False
    if silent:
        return True
    kind, _, name = match[1].partition(" ")
    stop = start + 1
    payload = ""
    if kind in {"figure", "formula"}:
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


def inlines(token) -> list[Inline]:
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
    return runs


def read_source(path: Path) -> Document:
    from markdown_it import MarkdownIt

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
            if kind in {"figure", "formula"}:
                if name in used_ids:
                    raise InputError(f"duplicate block identifier {name}")
                used_ids.add(name)
                try:
                    spec = json.loads(payload)
                except json.JSONDecodeError as exc:
                    raise InputError(f"line {line}: {kind} requires valid JSON: {exc}") from exc
                if kind == "figure":
                    spec = figure_spec(spec, name)
                    add(Block(f"{identifier}-title", "figure-title", [Inline(spec["title"])], line=line))
                    add(Block(identifier, "figure", spec=spec, line=line))
                    add(Block(f"{identifier}-caption", "caption", [Inline(spec["caption"])], line=line))
                    for position, label in enumerate(spec["labels"]):
                        expected[f"{identifier}-label-{position}"] = {"kind": "figure-label", "text": label, "line": line}
                    for position, value in enumerate(spec.get("display_values", [])):
                        expected[f"{identifier}-value-{position}"] = {"kind": "figure-value", "text": value, "line": line}
                    if spec.get("unit"):
                        expected[f"{identifier}-unit"] = {"kind": "figure-unit", "text": spec["unit"], "line": line}
                else:
                    if not isinstance(spec, dict) or set(spec) != {"expression", "caption"}:
                        raise InputError(f"line {line}: formula requires expression and caption only")
                    expression = nonempty(spec["expression"], "formula expression")
                    add(Block(identifier, "formula", [Inline(expression)], line=line))
                    add(Block(f"{identifier}-caption", "caption", [Inline(nonempty(spec["caption"], "formula caption"))], line=line))
            else:
                if kind == "pagebreak":
                    on_cover = False
                add(Block(identifier, kind, line=line))
        elif token.type == "heading_open":
            level = int(token.tag[1])
            runs = inlines(tokens[index + 1])
            kind = "cover-title" if first_title is None else "cover-subtitle" if on_cover and level == 2 else "heading"
            if first_title is None:
                if level != 1:
                    raise InputError("the first heading must be the document's level-one title")
                first_title = "".join(run.text for run in runs)
            label = "".join(run.text for run in runs)
            anchor = re.sub(r"[^\w -]", "", label.lower()).replace(" ", "-")
            count = anchors.get(anchor, 0)
            anchors[anchor] = count + 1
            if count:
                anchor = f"{anchor}-{count}"
            add(Block(identifier, kind, runs, level=level, spec={"anchor": anchor}, line=line))
            index += 2
        elif token.type == "paragraph_open":
            runs = inlines(tokens[index + 1])
            if any(run.text.lstrip().startswith(":::") for run in runs):
                raise InputError(f"line {line}: unsupported or malformed document directive")
            kind = "cover-body" if on_cover else "list" if list_stack else "quote" if quote_depth else "paragraph"
            add(Block(identifier, kind, runs, spec={"marker": list_marker}, line=line))
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
                    row.append(inlines(current))
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
