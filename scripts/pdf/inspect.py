"""Inspect the PDF bytes, glyph geometry and rendered pixels, not requested layout."""

from __future__ import annotations

import ctypes
import hashlib
import math
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

import pypdfium2 as pdfium
import pypdfium2.raw as raw
from PIL import ImageStat
from pypdf import PdfReader
from pypdf.generic import ContentStream

from scripts.checks.common import InputError
from scripts.pdf.source import Document

SCALE = 1.5
OPERATORS = re.compile(r"[<>=≤≥≠+−*/∧∨∑⌈⌉]")


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            result.update(block)
    return result.hexdigest()


def normalized(text: str) -> str:
    # Compatibility normalization would erase distinctions in literal math/code.
    return re.sub(r"\s+", "", unicodedata.normalize("NFC", text))


def rectangle(value):
    if not isinstance(value, (tuple, list)) or len(value) != 4 or any(
        type(item) not in (int, float) or not math.isfinite(item) for item in value
    ):
        raise InputError("layout rectangle must contain four finite numbers")
    x0, y0, x1, y1 = value
    if x1 < x0 or y1 < y0:
        raise InputError("layout rectangle is inverted")
    return x0, y0, x1, y1


def contains(outer, inner, tolerance=1.0):
    return (inner[0] >= outer[0] - tolerance and inner[1] >= outer[1] - tolerance
            and inner[2] <= outer[2] + tolerance and inner[3] <= outer[3] + tolerance)


def raster_signal(image, bbox, scale=SCALE):
    x0, y0, x1, y1 = bbox
    area = (max(0, math.floor((x0 - .8) * scale)), max(0, math.floor((y0 - .8) * scale)),
            min(image.width, math.ceil((x1 + .8) * scale)), min(image.height, math.ceil((y1 + .8) * scale)))
    if area[2] <= area[0] or area[3] <= area[1]:
        return False
    with image.crop(area) as crop:
        return max(ImageStat.Stat(crop).stddev) > 1.0


def embedded_font(font):
    font = font.get_object()
    if font.get("/Subtype") == "/Type0":
        return all(embedded_font(item) for item in font.get("/DescendantFonts", []))
    descriptor = font.get("/FontDescriptor")
    return bool(descriptor and any(key in descriptor.get_object() for key in ("/FontFile", "/FontFile2", "/FontFile3")))


def inspect_pdf(document: Document, pdf: Path, layout: dict, previews: Path) -> dict:
    if (not isinstance(layout, dict) or type(layout.get("schema_version")) is not int
            or layout["schema_version"] != 1 or not isinstance(layout.get("placements"), list)):
        raise InputError("unsupported PDF layout manifest")
    if pdf.stat().st_size > 100 * 1024 * 1024:
        raise InputError("PDF exceeds the 100 MiB inspection limit")
    allowed = rectangle(layout.get("allowed_text_box"))
    page_size = layout.get("page_size")
    if (not isinstance(page_size, list) or len(page_size) != 2
            or any(type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= 2000 for value in page_size)):
        raise InputError("invalid PDF layout page size")
    if (type(layout.get("body_leading")) not in (int, float) or not math.isfinite(layout["body_leading"])
            or layout["body_leading"] <= 0):
        raise InputError("invalid PDF layout leading")
    if (not isinstance(layout.get("headings"), list)
            or not all(isinstance(item, dict) and isinstance(item.get("title"), str) for item in layout["headings"])):
        raise InputError("invalid PDF layout headings")
    placements = layout["placements"]
    for placement in placements:
        if (not isinstance(placement, dict) or not isinstance(placement.get("id"), str)
                or not placement["id"] or not isinstance(placement.get("kind"), str)):
            raise InputError("invalid PDF layout placement")
        rectangle(placement.get("bbox"))
        if type(placement.get("page")) is not int or placement["page"] < 1:
            raise InputError("invalid page number in layout manifest")
    by_page = defaultdict(list)
    for placement in placements:
        by_page[placement["page"]].append(placement)
    errors = []
    texts = defaultdict(list)
    pages = []
    preview_records = []
    unpainted = []
    bounds = []
    vector_failures = []
    overlap = []
    literal_errors = []
    justification_lines = 0
    aligned_lines = 0
    all_text = []
    previews.mkdir(parents=True, exist_ok=True)
    pdf_hash = digest(pdf)
    with pdfium.PdfDocument(pdf) as output:
        if not 1 <= len(output) <= 300:
            raise InputError("PDF must contain between 1 and 300 pages")
        for index in range(len(output)):
            number = index + 1
            page = output[index]
            width, height = page.get_size()
            if not all(math.isfinite(value) and 0 < value <= 2000 for value in (width, height)):
                page.close()
                raise InputError("PDF page exceeds safe rasterization dimensions")
            if abs(width - page_size[0]) > 1 or abs(height - page_size[1]) > 1:
                errors.append({"code": "page_size", "page": number})
            text_page = page.get_textpage()
            bitmap = page.render(scale=SCALE)
            image = bitmap.to_pil().convert("RGB")
            try:
                text = text_page.get_text_range(errors="strict")
                all_text.append(text)
                filename = f"page-{number:03d}.png"
                image.save(previews / filename)
                preview_records.append({
                    "path": f"previews/{filename}", "sha256": digest(previews / filename),
                    "width": image.width, "height": image.height, "page": number,
                })
                char_count = text_page.count_chars()
                for char_index in range(char_count):
                    codepoint = raw.FPDFText_GetUnicode(text_page, char_index)
                    if not codepoint:
                        continue
                    character = chr(codepoint)
                    if character.isspace() or unicodedata.category(character).startswith("C"):
                        continue
                    left, bottom, right, top = text_page.get_charbox(char_index)
                    box = [left, height - top, right, height - bottom]
                    if right <= left or top <= bottom:
                        unpainted.append({"page": number, "character": character, "bbox": box, "reason": "empty_glyph_box"})
                        continue
                    if not contains(allowed, box):
                        bounds.append({"page": number, "character": character, "bbox": box})
                    channels = [ctypes.c_uint() for _ in range(4)]
                    has_color = raw.FPDFText_GetFillColor(text_page, char_index, *(ctypes.byref(item) for item in channels))
                    invisible = has_color and channels[3].value == 0
                    if invisible or not raster_signal(image, box):
                        unpainted.append({"page": number, "character": character, "bbox": box,
                                          "reason": "transparent" if invisible else "no_raster_contrast"})
                objects = []
                for item in page.get_objects(filter=[raw.FPDF_PAGEOBJ_PATH]):
                    left, bottom, right, top = item.get_bounds()
                    objects.append([left, height - top, right, height - bottom])
                for placement in by_page[number]:
                    box = rectangle(placement["bbox"])
                    if not contains([0, 0, width, height], box):
                        bounds.append({"page": number, "id": placement["id"], "bbox": box})
                    x0, y0, x1, y1 = box
                    local = text_page.get_text_bounded(
                        left=max(0, x0 - 1.5), bottom=max(0, height - y1 - 2.5),
                        right=min(width, x1 + 1.5), top=min(height, height - y0 + 2.5),
                        errors="strict",
                    )
                    texts[placement["id"]].append(local)
                    if placement.get("expected_ink"):
                        path_count = sum(contains(box, item, 2) for item in objects)
                        signal = raster_signal(image, box)
                        if not signal or not path_count:
                            vector_failures.append({"page": number, "id": placement["id"],
                                                    "raster_signal": signal, "vector_paths": path_count})
                    if placement["kind"] == "paragraph":
                        text_page.count_rects()
                        line_boxes = []
                        for rect_index in range(text_page.count_rects()):
                            left, bottom, right, top = text_page.get_rect(rect_index)
                            current = [left, height - top, right, height - bottom]
                            if contains(box, current, 2.5) and right - left > (x1 - x0) * .65:
                                line_boxes.append(current)
                        line_boxes.sort(key=lambda item: item[1])
                        for current, following in zip(line_boxes, line_boxes[1:]):
                            if 6 < following[1] - current[1] < layout["body_leading"] * 1.6:
                                justification_lines += 1
                                aligned_lines += abs(current[2] - x1) <= 3
                root_items = [item for item in by_page[number] if not item.get("parent")]
                for a_index, first in enumerate(root_items):
                    a = first["bbox"]
                    for second in root_items[a_index + 1:]:
                        b = second["bbox"]
                        intersection_width = min(a[2], b[2]) - max(a[0], b[0])
                        intersection_height = min(a[3], b[3]) - max(a[1], b[1])
                        if intersection_width > 2 and intersection_height > 2:
                            overlap.append({"page": number, "first": first["id"], "second": second["id"]})
                pages.append({"page": number, "width": width, "height": height, "characters": char_count,
                              "vector_paths": len(objects)})
            finally:
                image.close()
                bitmap.close()
                text_page.close()
                page.close()
    missing = []
    for identifier, expected in document.expected.items():
        actual = "\n".join(texts.get(identifier, []))
        if expected["text"].strip() and normalized(expected["text"]) not in normalized(actual):
            missing.append({"id": identifier, "kind": expected["kind"], "source_line": expected["line"],
                            "expected": expected["text"], "observed": actual})
        if expected["kind"] in {"formula", "code"}:
            wanted = Counter(OPERATORS.findall(expected["text"]))
            found = Counter(OPERATORS.findall(actual))
            if wanted != found:
                literal_errors.append({"id": identifier, "kind": expected["kind"],
                                       "expected_operators": dict(wanted), "observed_operators": dict(found)})
    if missing:
        errors.append({"code": "missing_text", "count": len(missing)})
    if literal_errors:
        errors.append({"code": "changed_operators", "count": len(literal_errors)})
    if bounds:
        errors.append({"code": "out_of_bounds", "count": len(bounds)})
    if unpainted:
        errors.append({"code": "invisible_text", "count": len(unpainted)})
    if vector_failures:
        errors.append({"code": "missing_figure_ink", "count": len(vector_failures)})
    if overlap:
        errors.append({"code": "overlapping_layout", "count": len(overlap)})
    if any(item["page"] > len(pages) for item in placements):
        errors.append({"code": "missing_layout_page"})
    if justification_lines >= 10 and aligned_lines / justification_lines < .9:
        errors.append({"code": "nonjustified_prose", "lines": justification_lines, "aligned": aligned_lines})
    reader = PdfReader(pdf)
    uris = []
    unembedded = set()
    for number, page in enumerate(reader.pages, 1):
        for reference in page.get("/Annots", []):
            action = reference.get_object().get("/A")
            if action and action.get("/URI"):
                uris.append(str(action["/URI"]))
        current_font = None
        fonts = page.get("/Resources", {}).get("/Font", {})
        fonts = fonts.get_object() if hasattr(fonts, "get_object") else fonts
        for operands, operator in ContentStream(page.get_contents(), reader).operations:
            if operator == b"Tf":
                current_font = operands[0]
            elif operator in {b"Tj", b"TJ", b"'", b'"'} and current_font in fonts:
                if not embedded_font(fonts[current_font]):
                    unembedded.add(f"page {number}: {current_font}")
    missing_links = [link for link in document.links if not link.startswith("#") and link not in uris]
    if missing_links:
        errors.append({"code": "missing_links", "links": missing_links})
    if unembedded:
        errors.append({"code": "unembedded_fonts", "fonts": sorted(unembedded)})
    outline = []
    def visit(items):
        for item in items:
            if isinstance(item, list):
                visit(item)
            else:
                target = reader.get_destination_page_number(item)
                outline.append({"title": item.title, "page": target + 1 if isinstance(target, int) and target >= 0 else None})
    visit(reader.outline)
    wanted_headings = [item["title"] for item in layout["headings"]]
    if [item["title"] for item in outline] != wanted_headings or any(
        type(item["page"]) is not int or not 1 <= item["page"] <= len(pages) for item in outline
    ):
        errors.append({"code": "invalid_navigation"})
    if digest(pdf) != pdf_hash:
        raise InputError("PDF changed during inspection")
    return {
        "schema_version": 1, "inspector": "docswarm-pdf", "status": "pass" if not errors else "fail",
        "editorial_approval": "not_evaluated", "source_sha256": document.source_sha256,
        "pdf_sha256": pdf_hash, "pages": pages, "page_count": len(pages),
        "errors": errors, "missing_text": missing, "changed_operators": literal_errors,
        "out_of_bounds": bounds, "invisible_text": unpainted,
        "figure_failures": vector_failures, "layout_overlaps": overlap,
        "justification": {"eligible_lines": justification_lines, "aligned_lines": aligned_lines,
                          "ratio": aligned_lines / justification_lines if justification_lines else None},
        "links": sorted(set(uris)), "outline": outline, "previews": preview_records,
        "extracted_text": "\n\n".join(all_text),
        "limitations": [
            "Mechanical fidelity and raster contrast do not establish editorial quality or correctness of the source.",
            "Whitespace is ignored for text correspondence; operators and Unicode characters are not discarded.",
            "Complex backgrounds can affect contrast checks; human reviewers must inspect the PNG pages.",
            "Declared figure geometry is checked against PDF paths and pixels, not against semantic diagram meaning.",
        ],
    }
