"""Export the faithful PowerPoint file from the frames actually captured.

Each slide carries the picture of its page plus nearly transparent hotspots that
reproduce the planned navigation.  The exporter adds no text of its own: titles
and alternative descriptions are taken from the deck and recorded as metadata.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE
from pptx.util import Emu

from scripts.checks.common import InputError
from scripts.presentations.capture import png_size
from scripts.presentations.ooxml import almost_transparent, emu, hide_from_slideshow, link_to_slide, link_to_url, set_notes

BLANK_LAYOUT = 6


def export(document: dict[str, Any], layout: dict[str, Any], frames: list[dict[str, Any]],
           destination: Path) -> dict[str, Any]:
    """Build the image-based deck, verifying each embedded picture as it is added."""
    stage = layout["size_pt"]
    presentation = Presentation()
    presentation.slide_width = Emu(emu(stage["width"]))
    presentation.slide_height = Emu(emu(stage["height"]))
    blank = presentation.slide_layouts[BLANK_LAYOUT]
    captured = {item["page_id"]: item for item in frames}
    if set(captured) != {page["page_id"] for page in layout["pages"]}:
        raise InputError("the captured frames do not cover exactly the planned pages")
    slides, hotspots, objects = {}, [], []
    for page in layout["pages"]:
        slides[page["page_id"]] = presentation.slides.add_slide(blank)
    navigation = {(item["page_id"], item["action_id"]): item for item in layout["navigation"]}
    for page in layout["pages"]:
        slide = slides[page["page_id"]]
        frame = captured[page["page_id"]]
        source = Path(frame["path"])
        pixels = png_size(source)
        if list(pixels) != list(frame["pixels"]):
            raise InputError(f"the frame of {page['page_id']} changed between capture and export")
        if abs(pixels[0] / pixels[1] - stage["width"] / stage["height"]) > 0.001:
            raise InputError(f"the frame of {page['page_id']} does not keep the stage aspect ratio")
        picture = slide.shapes.add_picture(str(source), 0, 0, Emu(emu(stage["width"])), Emu(emu(stage["height"])))
        picture.name = f"frame::{page['page_id']}"
        objects.append({"page_id": page["page_id"], "block_id": f"sys:frame:{page['page_id']}",
                        "native": "picture", "text": ""})
        for item in layout["navigation"]:
            if item["page_id"] != page["page_id"]:
                continue
            region = item["region"]
            shape = slide.shapes.add_shape(
                MSO_SHAPE.RECTANGLE, emu(region["x"]), emu(region["y"]),
                emu(max(region["width"], 1)), emu(max(region["height"], 1)))
            shape.name = f"hotspot::{item['action_id']}"
            almost_transparent(shape)
            shape.text_frame.text = ""
            if item.get("reference_id"):
                link_to_url(shape, item["url"])
            elif item["enabled"]:
                hotspots.append((shape, item["target_page_id"]))
            objects.append({"page_id": page["page_id"], "block_id": f"sys:hotspot:{item['action_id']}",
                            "native": "action-shape", "text": ""})
        set_notes(slide, page["notes"])
        if page["kind"] == "support":
            hide_from_slideshow(slide)
    for shape, target in hotspots:
        link_to_slide(shape, slides[target])
    if set(navigation) != {(item["page_id"], item["action_id"]) for item in layout["navigation"]}:
        raise InputError("the navigation plan changed during the faithful export")
    destination.parent.mkdir(parents=True, exist_ok=True)
    presentation.save(str(destination))
    return {"objects": objects, "frames": [{"page_id": item["page_id"], "pixels": item["pixels"]} for item in frames]}
