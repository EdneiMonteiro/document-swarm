"""Build one presentation candidate: compose, export and inspect, in that order.

The builder publishes a candidate directory only after the three formats exist
and have been inspected.  It never overwrites an evaluated delivery: the
destination must be new.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from scripts.checks.common import InputError, write_json_atomic
from scripts.checks.presentation_contract import CAPABILITY, FORMAT_FILES, FORMATS
from scripts.presentations import capture, inspect, pptx_editable, pptx_faithful
from scripts.presentations.html import write_package
from scripts.presentations.layout import plan
from scripts.presentations.model import digest, read_deck

ENGINE = "docswarm-presentation"
ENGINE_VERSION = "1.0"
ROLES = {
    "deck.json": "model", "layout.json": "plan", "theme.json": "theme",
    "LEIA-ME.txt": "readme", "index.html": "format",
    "deck-faithful.pptx": "format", "deck-editable.pptx": "format",
}


def role_of(relative: str) -> str:
    if relative in ROLES:
        return ROLES[relative]
    if relative.startswith("runtime/"):
        return "runtime"
    if relative.startswith("assets/"):
        return "asset"
    if relative.startswith("fonts/"):
        return "license" if relative.endswith(".txt") else "font"
    raise InputError(f"the delivery contains a file with no authorised role: {relative}")


def inventory(root: Path, output_root: Path) -> list[dict[str, Any]]:
    files = []
    for item in sorted(root.rglob("*")):
        if item.is_symlink():
            raise InputError(f"the delivery must not contain links: {item.name}")
        if not item.is_file():
            continue
        relative = item.relative_to(root).as_posix()
        files.append({"path": (output_root / relative).as_posix(), "role": role_of(relative),
                      "sha256": digest(item), "bytes": item.stat().st_size})
    return files


def build(deck_path: Path, swarm: Path, destination: Path, *, profile: str, cycle: int,
          assets_root: Path | None = None) -> dict[str, Any]:
    """Compose the three formats, inspect them and publish the candidate output."""
    swarm = swarm.resolve(strict=True)
    destination = destination if destination.is_absolute() else (swarm / destination)
    if destination.exists():
        raise InputError("the presentation destination must be new; use another cycle directory")
    output_root = destination.resolve().relative_to(swarm)
    if output_root.parts[0] != "output":
        raise InputError("the presentation output must live under the swarm output directory")
    document = read_deck(deck_path, assets_root=assets_root)
    layout = plan(document, profile)

    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".docswarm-presentation-", dir=destination.parent))
    published = False
    try:
        write_package(document, layout, staging)
        shutil.copy2(document["source"], staging / "deck.json")
        shutil.copy2(document["theme"]["source"], staging / "theme.json")
        write_json_atomic(staging / "layout.json", layout)
        frames_dir = staging.parent / f"{staging.name}-frames"
        frames = capture.capture(staging / "index.html", layout, frames_dir)
        try:
            editable = pptx_editable.export(document, layout, staging / FORMAT_FILES["pptx-editable"])
            faithful = pptx_faithful.export(document, layout, frames, staging / FORMAT_FILES["pptx-faithful"])
        finally:
            shutil.rmtree(frames_dir, ignore_errors=True)

        checks = [
            {"check_id": "input-policy", "findings": [],
             "observations": {"deck_sha256": document["sha256"], "theme": document["theme"]["theme_id"],
                              "assets": sorted(document["assets"])}},
            {"check_id": "html-offline", **inspect.inspect_html(staging / "index.html", layout)},
            {"check_id": "navigation", **capture.exercise(staging / "index.html", layout)},
            {"check_id": "native-structure",
             **inspect.inspect_pptx(staging / FORMAT_FILES["pptx-editable"], layout, editable=True)},
            {"check_id": "visual",
             **inspect.inspect_pptx(staging / FORMAT_FILES["pptx-faithful"], layout, editable=False)},
        ]
        files = inventory(staging, output_root)
        declared = {item["path"] for item in files}
        for name in FORMATS:
            if (output_root / FORMAT_FILES[name]).as_posix() not in declared:
                raise InputError(f"the candidate is missing the {name} deliverable")
        checks.append({"check_id": "inventory-content", "findings": [], "observations": {
            "files": len(files), "pages": len(layout["pages"]),
            "editable_objects": len(editable["objects"]), "faithful_objects": len(faithful["objects"])}})
        os.rename(staging, destination)
        published = True
    finally:
        if not published:
            shutil.rmtree(staging, ignore_errors=True)

    files = inventory(destination, output_root)
    manifest = {
        "schema_version": 1, "capability": CAPABILITY, "cycle": cycle, "profile": profile,
        "generator": ENGINE, "generator_version": ENGINE_VERSION,
        "deck": {"path": (output_root / "deck.json").as_posix(), "sha256": digest(destination / "deck.json")},
        "layout": {"path": (output_root / "layout.json").as_posix(), "sha256": digest(destination / "layout.json")},
        "formats": {name: {"path": (output_root / FORMAT_FILES[name]).as_posix(),
                           "sha256": digest(destination / FORMAT_FILES[name])} for name in FORMATS},
        "files": files,
        "pages": [{"page_id": page["page_id"], "kind": page["kind"]} for page in layout["pages"]],
        "objects": {"pptx-editable": editable["objects"], "pptx-faithful": faithful["objects"]},
    }
    return {"destination": destination, "layout": layout, "document": document,
            "manifest": manifest, "checks": checks, "output_root": output_root}


def editorial_text(layout: dict[str, Any]) -> str:
    """Collect every delivered word, including labels, supports and notes."""
    lines: list[str] = []
    by_page: dict[str, list[dict[str, Any]]] = {}
    for element in layout["elements"]:
        by_page.setdefault(element["page_id"], []).append(element)
    for page in layout["pages"]:
        lines.append(f"# {page['page_id']} ({page['kind']})")
        for element in sorted(by_page.get(page["page_id"], []), key=lambda item: item["reading_order"]):
            text = element.get("text", "")
            if text:
                lines.append(text)
        if page["notes"]:
            lines.append(f"[notas de {page['notes_source']}] {page['notes']}")
        lines.append("")
    return "\n".join(lines)
