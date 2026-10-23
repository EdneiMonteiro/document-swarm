"""Shared PowerPoint helpers for both exporters.

Everything here works on the saved package model: hidden slides, notes, click
actions and the nearly transparent fill used by the hotspots that sit over a
captured frame.  A shape with no fill is not clickable inside its area in
PowerPoint, so the faithful deck uses a 1% alpha fill instead.
"""

from __future__ import annotations

from typing import Any

from pptx.oxml.ns import qn
from pptx.util import Emu, Pt

from scripts.checks.common import InputError

EMU_PER_POINT = 12700
CONNECTION_SITE = {"top": 0, "left": 1, "bottom": 2, "right": 3}


def emu(points: float) -> int:
    return int(round(points * EMU_PER_POINT))


def box(element: dict[str, Any]) -> tuple[int, int, int, int]:
    item = element["box"]
    return emu(item["x"]), emu(item["y"]), emu(max(item["width"], 1)), emu(max(item["height"], 1))


def hide_from_slideshow(slide) -> None:
    """Mark a support page as hidden in the normal advance sequence."""
    slide._element.set("show", "0")


def set_notes(slide, notes: str) -> None:
    frame = slide.notes_slide.notes_text_frame
    frame.text = notes
    for paragraph in frame.paragraphs:
        for run in paragraph.runs:
            run.font.size = Pt(11)


def almost_transparent(shape, colour: str = "FFFFFF") -> None:
    """Apply a 1% alpha solid fill so the hotspot stays clickable but invisible."""
    shape.fill.solid()
    fill = shape.fill._xPr.find(qn("a:solidFill"))
    if fill is None:
        raise InputError("the hotspot did not receive a solid fill")
    for child in list(fill):
        fill.remove(child)
    colour_element = fill.makeelement(qn("a:srgbClr"), {"val": colour})
    alpha = colour_element.makeelement(qn("a:alpha"), {"val": "1000"})
    colour_element.append(alpha)
    fill.append(colour_element)
    shape.line.fill.background()


def add_arrow_head(connector) -> None:
    """Give a connector the same arrow the HTML composition draws."""
    line = connector.line._get_or_add_ln()
    for tag in ("a:tailEnd",):
        existing = line.find(qn(tag))
        if existing is not None:
            line.remove(existing)
        line.append(line.makeelement(qn(tag), {"type": "triangle", "w": "med", "len": "med"}))


def set_description(shape, text: str) -> None:
    """Record the authored alternative description on a non-text shape."""
    shape._element._nvXxPr.cNvPr.set("descr", text)


def link_to_slide(shape, slide) -> None:
    shape.click_action.target_slide = slide


def link_to_url(shape, url: str) -> None:
    if not url.startswith("https://"):
        raise InputError("only https references may be linked from a presentation")
    shape.click_action.hyperlink.address = url


def disable_autofit(frame) -> None:
    body = frame._txBody.find(qn("a:bodyPr"))
    for tag in ("a:normAutofit", "a:spAutoFit"):
        found = body.find(qn(tag))
        if found is not None:
            body.remove(found)
    body.append(body.makeelement(qn("a:noAutofit"), {}))


def frame_margins(frame, padding: float = 0.0) -> None:
    frame.margin_left = frame.margin_right = Emu(emu(padding))
    frame.margin_top = frame.margin_bottom = Emu(emu(padding))
    frame.word_wrap = True
    disable_autofit(frame)
