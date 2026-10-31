"""Inspect the files that were actually saved, not the map the exporters emitted.

The HTML reader parses the delivered markup; the PowerPoint reader opens the
package as a ZIP and walks its XML.  Neither trusts the manifest: both rebuild
the inventory from the bytes on disk and compare it with the authored deck.
"""

from __future__ import annotations

import re
import unicodedata
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from scripts.checks.common import InputError

A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
EMU_PER_POINT = 12700
MAX_PACKAGE_BYTES = 256 * 1024 * 1024
MAX_ENTRIES = 4000
MAX_EXPANSION = 60


def normalise(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", value)).strip()


def squeeze(value: str) -> str:
    """Compare wording without layout whitespace, preserving operators and accents."""
    return re.sub(r"\s+", "", unicodedata.normalize("NFC", value))


class Markup(HTMLParser):
    """Collect the delivered elements, controls and external references."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[dict[str, Any]] = []
        self.blocks: dict[tuple[str, str], dict[str, Any]] = {}
        self.pages: list[dict[str, Any]] = []
        self.controls: dict[tuple[str, str], dict[str, Any]] = {}
        self.resources: list[str] = []
        self.policy: str | None = None
        self.page_stack: list[str] = []
        self.dialogs: list[dict[str, str]] = []
        self.scripts: list[str] = []
        self.decoration = 0

    def handle_starttag(self, tag: str, attributes: list[tuple[str, str | None]]) -> None:
        item = {key: (value or "") for key, value in attributes}
        if item.get("aria-hidden") == "true":
            self.decoration += 1
            self.stack.append({"key": None, "tag": tag, "decoration": True})
        if tag == "meta" and item.get("http-equiv", "").lower() == "content-security-policy":
            self.policy = item.get("content", "")
        for key in ("src", "href"):
            if key in item and tag != "a":
                self.resources.append(item[key])
        if tag == "a" and "href" in item:
            self.resources.append(item["href"])
        if tag == "script":
            self.scripts.append(item.get("src", "inline"))
        if tag == "dialog":
            self.dialogs.append({"origin": item.get("data-origin", ""),
                                 "support_id": item.get("data-support-id", "")})
        if "data-page-id" in item:
            self.page_stack.append(item["data-page-id"])
            self.pages.append({"page_id": item["data-page-id"], "kind": item.get("data-kind", ""),
                               "label": item.get("aria-label", ""), "hidden": "hidden" in item})
        page = self.page_stack[-1] if self.page_stack else ""
        if "data-block-id" in item:
            key = (page, item["data-block-id"])
            self.blocks[key] = {"tag": tag, "text": [], "attributes": item}
            self.stack.append({"key": key, "tag": tag})
        if "data-action-id" in item:
            self.controls[(page, item["data-action-id"])] = {
                "tag": tag, "target": item.get("data-target"), "href": item.get("href"),
                "disabled": "disabled" in item, "reference": item.get("data-reference-id"),
                "rel": item.get("rel", ""), "text": [],
            }
        if tag in ("br",) and self.stack:
            owner = next((entry for entry in reversed(self.stack) if entry["key"] is not None), None)
            if owner is not None:
                self.blocks[owner["key"]]["text"].append(" ")
        if tag == "img" and self.stack:
            owner = next((entry for entry in reversed(self.stack) if entry["key"] is not None), None)
            if owner is not None:
                self.blocks[owner["key"]]["text"].append(item.get("alt", ""))

    def handle_endtag(self, tag: str) -> None:
        if self.stack and self.stack[-1]["tag"] == tag:
            entry = self.stack.pop()
            if entry.get("decoration"):
                self.decoration -= 1
        if self.page_stack and tag in ("section",):
            self.page_stack.pop()

    def handle_data(self, data: str) -> None:
        if self.decoration:
            return
        owner = next((entry for entry in reversed(self.stack) if entry["key"] is not None), None)
        if owner is not None:
            self.blocks[owner["key"]]["text"].append(data)
        page = self.page_stack[-1] if self.page_stack else ""
        for (holder, _), control in self.controls.items():
            if holder == page:
                control["text"].append(data)


def fragments_of(element: dict[str, Any]) -> list[str]:
    """Return the atomic strings a delivery must preserve for one element."""
    items = element.get("fragments")
    if items:
        return [item for item in items if item.strip()]
    text = element.get("text", "")
    return [text] if text.strip() else []


def inspect_html(package: Path, layout: dict[str, Any]) -> dict[str, Any]:
    """Verify the delivered HTML carries every block, control and local resource."""
    findings: list[dict[str, Any]] = []
    parser = Markup()
    parser.feed(package.read_text(encoding="utf-8"))
    if not parser.policy or "default-src 'none'" not in parser.policy or "'unsafe-inline'" in parser.policy:
        findings.append({"code": "weak_content_policy", "observed": parser.policy})
    for resource in parser.resources:
        if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", resource) and not resource.startswith("https://"):
            findings.append({"code": "unsupported_scheme", "resource": resource})
        elif resource.startswith("https://"):
            continue
        elif not (package.parent / resource).is_file():
            findings.append({"code": "missing_local_resource", "resource": resource})
    for script in parser.scripts:
        if script == "inline":
            continue
        if not (package.parent / script).is_file():
            findings.append({"code": "missing_script", "resource": script})
    delivered = {item["page_id"] for item in parser.pages}
    planned = {page["page_id"] for page in layout["pages"]}
    if delivered != planned:
        findings.append({"code": "page_inventory", "missing": sorted(planned - delivered)[:4],
                         "unexpected": sorted(delivered - planned)[:4]})
    for element in layout["elements"]:
        if element.get("type") in ("diagram", "diagram-connector", "chrome", "index-entry"):
            continue
        key = (element["page_id"], element["block_id"])
        block = parser.blocks.get(key)
        if block is None:
            findings.append({"code": "missing_block", "page": element["page_id"], "block": element["block_id"]})
            continue
        observed = squeeze("".join(block["text"]))
        for fragment in fragments_of(element):
            if squeeze(fragment) not in observed:
                findings.append({"code": "text_mismatch", "page": element["page_id"],
                                 "block": element["block_id"], "expected": fragment[:120],
                                 "observed": observed[:120]})
    for item in layout["navigation"]:
        control = parser.controls.get((item["page_id"], item["action_id"]))
        if control is None:
            findings.append({"code": "missing_control", "page": item["page_id"], "action": item["action_id"]})
            continue
        if item.get("reference_id"):
            if control["href"] != item["url"] or "noopener" not in control["rel"]:
                findings.append({"code": "external_reference", "page": item["page_id"],
                                 "action": item["action_id"], "observed": control["href"]})
        elif item["enabled"]:
            if control["target"] != item["target_page_id"] or control["disabled"]:
                findings.append({"code": "wrong_destination", "page": item["page_id"],
                                 "action": item["action_id"], "observed": control["target"]})
        elif not control["disabled"] or control["target"] is not None:
            findings.append({"code": "enabled_extreme", "page": item["page_id"], "action": item["action_id"]})
    materialisations = {(page["origin"], page["support_id"]) for page in layout["pages"] if page["kind"] == "support"}
    delivered_dialogs = {(item["origin"], item["support_id"]) for item in parser.dialogs}
    if delivered_dialogs != materialisations:
        findings.append({"code": "support_materialisation", "expected": sorted(materialisations)[:4],
                         "observed": sorted(delivered_dialogs)[:4]})
    return {"findings": findings, "observations": {"pages": len(parser.pages),
                                                   "controls": len(parser.controls),
                                                   "policy": parser.policy}}


def read_package(path: Path) -> dict[str, bytes]:
    """Open a PPTX safely: bounded entries, bounded expansion and contained names."""
    if path.stat().st_size > MAX_PACKAGE_BYTES:
        raise InputError(f"{path.name} exceeds the supported package size")
    parts: dict[str, bytes] = {}
    with zipfile.ZipFile(path) as package:
        entries = package.infolist()
        if len(entries) > MAX_ENTRIES:
            raise InputError(f"{path.name} declares too many parts")
        compressed = sum(item.compress_size for item in entries) or 1
        if sum(item.file_size for item in entries) > compressed * MAX_EXPANSION:
            raise InputError(f"{path.name} expands beyond the supported ratio")
        for item in entries:
            name = item.filename
            if name.endswith("/"):
                continue
            if name.startswith("/") or ".." in name.split("/") or "\\" in name or ":" in name:
                raise InputError(f"{path.name} contains an unsafe part name: {name}")
            parts[name] = package.read(item)
    return parts


def slide_order(parts: dict[str, bytes]) -> list[str]:
    from xml.etree import ElementTree

    presentation = ElementTree.fromstring(parts["ppt/presentation.xml"])
    relations = ElementTree.fromstring(parts["ppt/_rels/presentation.xml.rels"])
    targets = {item.get("Id"): item.get("Target") for item in relations}
    order = []
    for item in presentation.iter(f"{P}sldId"):
        target = targets[item.get(f"{R}id")].replace("../", "")
        order.append(f"ppt/{target}" if not target.startswith("ppt/") else target)
    return order


def shape_text(node) -> str:
    return "".join(piece.text or "" for piece in node.iter(f"{A}t"))


def shape_box(node) -> dict[str, float] | None:
    transform = node.find(f".//{A}xfrm")
    if transform is None:
        return None
    offset, extent = transform.find(f"{A}off"), transform.find(f"{A}ext")
    if offset is None or extent is None:
        return None
    return {"x": int(offset.get("x")) / EMU_PER_POINT, "y": int(offset.get("y")) / EMU_PER_POINT,
            "width": int(extent.get("cx")) / EMU_PER_POINT, "height": int(extent.get("cy")) / EMU_PER_POINT}


def inspect_pptx(path: Path, layout: dict[str, Any], *, editable: bool) -> dict[str, Any]:
    """Read the saved package and confront its objects with the authored deck."""
    from xml.etree import ElementTree

    findings: list[dict[str, Any]] = []
    parts = read_package(path)
    for name in parts:
        if name.startswith("ppt/embeddings/") or "vbaProject" in name or name.endswith(".xlsx"):
            findings.append({"code": "embedded_object", "part": name})
    for name, data in parts.items():
        if name.endswith(".rels"):
            for relation in ElementTree.fromstring(data):
                mode = relation.get("TargetMode", "Internal")
                target = relation.get("Target", "")
                kind = relation.get("Type", "").rsplit("/", 1)[-1]
                if mode == "External" and kind != "hyperlink":
                    findings.append({"code": "external_relationship", "part": name, "target": target})
                if mode == "External" and not target.startswith("https://"):
                    findings.append({"code": "insecure_hyperlink", "part": name, "target": target})
    slides = slide_order(parts)
    planned = layout["pages"]
    if len(slides) != len(planned):
        findings.append({"code": "slide_count", "expected": len(planned), "observed": len(slides)})
    stage = layout["size_pt"]
    presentation = ElementTree.fromstring(parts["ppt/presentation.xml"])
    size = presentation.find(f"{P}sldSz")
    if abs(int(size.get("cx")) / EMU_PER_POINT - stage["width"]) > 0.5:
        findings.append({"code": "stage_size", "observed": int(size.get("cx")) / EMU_PER_POINT})
    allowed = {"x": -0.5, "y": -0.5, "right": stage["width"] + 0.5, "bottom": stage["height"] + 0.5}
    by_page: dict[str, list[dict[str, Any]]] = {}
    for element in layout["elements"]:
        by_page.setdefault(element["page_id"], []).append(element)
    observations = {"slides": len(slides), "shapes": 0, "connectors": 0, "pictures": 0, "tables": 0}

    for index, (part, page) in enumerate(zip(slides, planned), 1):
        root = ElementTree.fromstring(parts[part])
        hidden = root.get("show") == "0"
        if hidden != (page["kind"] == "support"):
            findings.append({"code": "slideshow_visibility", "page": page["page_id"], "hidden": hidden})
        tree = root.find(f"{P}cSld").find(f"{P}spTree")
        texts, boxes, descriptions = [], [], []
        for node in tree.iter(f"{P}cNvPr"):
            descriptions.append(node.get("descr") or "")
        for node in tree:
            tag = node.tag
            if tag in (f"{P}sp", f"{P}pic", f"{P}graphicFrame", f"{P}cxnSp"):
                box = shape_box(node)
                text = shape_text(node)
                texts.append(text)
                if box is not None:
                    boxes.append((tag, box, text))
                if tag == f"{P}sp":
                    observations["shapes"] += 1
                elif tag == f"{P}pic":
                    observations["pictures"] += 1
                elif tag == f"{P}cxnSp":
                    observations["connectors"] += 1
                elif node.find(f".//{A}tbl") is not None:
                    observations["tables"] += 1
        combined = squeeze(" ".join(texts))
        haystack = combined + "\x00" + squeeze(" ".join(descriptions))
        notes_part = f"ppt/notesSlides/notesSlide{index}.xml"
        notes = squeeze(shape_text(ElementTree.fromstring(parts[notes_part]))) if notes_part in parts else ""
        expected_notes = squeeze(page["notes"])
        if expected_notes and expected_notes not in notes:
            findings.append({"code": "missing_notes", "page": page["page_id"]})
        if not expected_notes and notes.strip(f"{index}"):
            findings.append({"code": "unexpected_notes", "page": page["page_id"], "observed": notes[:80]})
        for tag, box, _ in boxes:
            if (box["x"] < allowed["x"] or box["y"] < allowed["y"]
                    or box["x"] + box["width"] > allowed["right"]
                    or box["y"] + box["height"] > allowed["bottom"]):
                findings.append({"code": "outside_stage", "page": page["page_id"], "box": box})
        if editable:
            for element in by_page.get(page["page_id"], []):
                if element.get("type") in ("diagram", "diagram-connector"):
                    continue
                for fragment in fragments_of(element):
                    if squeeze(fragment) not in haystack:
                        findings.append({"code": "missing_native_text", "page": page["page_id"],
                                         "block": element["block_id"], "expected": fragment[:100]})
            planned_connectors = [item for item in by_page.get(page["page_id"], [])
                                  if item.get("type") == "diagram-connector"]
            anchored = 0
            for node in tree.iter(f"{P}cxnSp"):
                start = node.find(f".//{A}stCxn")
                end = node.find(f".//{A}endCxn")
                if start is not None and end is not None:
                    anchored += 1
            if anchored != len(planned_connectors):
                findings.append({"code": "unanchored_connectors", "page": page["page_id"],
                                 "expected": len(planned_connectors), "observed": anchored})
            if any(node.tag == f"{P}pic" for node in tree) and not any(
                    item.get("type") == "image" for item in by_page.get(page["page_id"], [])):
                findings.append({"code": "unexpected_picture", "page": page["page_id"]})
        else:
            pictures = [node for node in tree if node.tag == f"{P}pic"]
            if len(pictures) != 1:
                findings.append({"code": "frame_count", "page": page["page_id"], "observed": len(pictures)})
            if combined:
                findings.append({"code": "unexpected_text_in_faithful", "page": page["page_id"],
                                 "observed": combined[:80]})
        actions = 0
        for node in tree.iter(f"{A}hlinkClick"):
            if node.get(f"{R}id") or node.get("action"):
                actions += 1
        expected_actions = len([item for item in layout["navigation"] if item["page_id"] == page["page_id"]
                                and (item["enabled"] or item.get("reference_id"))])
        if actions < expected_actions:
            findings.append({"code": "missing_actions", "page": page["page_id"],
                             "expected": expected_actions, "observed": actions})
    return {"findings": findings, "observations": observations}


def compare_images(reference: Path, candidate: Path, *, tolerance: float) -> dict[str, Any]:
    """Compare two renderings and report the measured difference."""
    try:
        from PIL import Image, ImageChops, ImageStat
    except ModuleNotFoundError as exc:  # pragma: no cover - surfaced by the CLI
        raise InputError("Pillow is unavailable; prepare the optional presentation environment") from exc
    with Image.open(reference) as first, Image.open(candidate) as second:
        left = first.convert("RGB")
        right = second.convert("RGB").resize(left.size)
        difference = ImageChops.difference(left, right)
        statistics = ImageStat.Stat(difference)
        mean = max(statistics.mean)
        changed = sum(1 for pixel in difference.convert("L").getdata() if pixel > 32)
        ratio = changed / (left.size[0] * left.size[1])
    return {"mean_difference": round(mean, 3), "changed_ratio": round(ratio, 5),
            "within_tolerance": ratio <= tolerance}
