"""Load the authored deck, theme and assets without accepting executable syntax."""

from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path
from typing import Any

from scripts.checks.common import InputError, parse_strict_json
from scripts.checks.jsonschema_lite import validate
from scripts.checks.presentation_contract import deck_structure, logical

SCHEMA_ROOT = Path(__file__).resolve().parents[2] / "schemas" / "presentation"
THEME_ROOT = Path(__file__).resolve().parent / "themes"
MAX_DECK_BYTES = 4 * 1024 * 1024
MAX_ASSET_BYTES = 24 * 1024 * 1024


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            result.update(block)
    return result.hexdigest()


def image_size(path: Path, media_type: str) -> tuple[int, int]:
    """Return the intrinsic pixel size read from the image itself."""
    with path.open("rb") as stream:
        head = stream.read(32)
        if media_type == "image/png":
            if head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
                raise InputError(f"{path.name} is not a valid PNG asset")
            return struct.unpack_from(">II", head, 16)
        stream.seek(0)
        if stream.read(2) != b"\xff\xd8":
            raise InputError(f"{path.name} is not a valid JPEG asset")
        while True:
            marker = stream.read(2)
            if len(marker) != 2 or marker[0] != 0xFF:
                raise InputError(f"{path.name} has a malformed JPEG structure")
            length = struct.unpack(">H", stream.read(2))[0]
            if 0xC0 <= marker[1] <= 0xCF and marker[1] not in (0xC4, 0xC8, 0xCC):
                body = stream.read(5)
                height, width = struct.unpack_from(">HH", body, 1)
                return width, height
            stream.seek(length - 2, 1)


def read_theme(theme_id: str) -> dict[str, Any]:
    path = THEME_ROOT / f"{theme_id}.json"
    if not path.is_file() or logical(f"{theme_id}.json", "theme").parent.parts:
        raise InputError(f"unknown presentation theme: {theme_id}")
    theme = parse_strict_json(path.read_text(encoding="utf-8"), name="theme")
    if not isinstance(theme, dict) or theme.get("schema_version") != 1 or theme.get("theme_id") != theme_id:
        raise InputError("unsupported presentation theme")
    for section in ("palette", "typography", "metrics", "layouts"):
        if not isinstance(theme.get(section), dict) or not theme[section]:
            raise InputError(f"the theme requires a {section} section")
    for name, style in theme["typography"].items():
        if style.get("colour") not in theme["palette"]:
            raise InputError(f"typography token {name} refers to an unknown colour")
        if not isinstance(style.get("size"), (int, float)) or not 6 <= style["size"] <= 96:
            raise InputError(f"typography token {name} has an unsupported size")
    for name, layout in theme["layouts"].items():
        areas = layout.get("areas")
        if not isinstance(areas, dict) or not areas:
            raise InputError(f"layout {name} declares no areas")
        for area, box in areas.items():
            if sorted(box) != ["height", "width", "x", "y"]:
                raise InputError(f"area {name}.{area} must declare x, y, width and height")
    theme["sha256"] = digest(path)
    theme["source"] = path
    return theme


def read_deck(path: Path, *, assets_root: Path | None = None) -> dict[str, Any]:
    """Return the validated deck together with its reconstructed structure."""
    path = path.resolve(strict=True)
    if path.stat().st_size > MAX_DECK_BYTES:
        raise InputError("the deck exceeds the supported size")
    deck = parse_strict_json(path.read_text(encoding="utf-8"), name="deck")
    schema = json.loads((SCHEMA_ROOT / "deck.schema.json").read_text(encoding="utf-8"))
    validate(deck, schema, path="deck")
    facts = deck_structure(deck)
    theme = read_theme(deck["theme_ref"])

    layouts, assets = theme["layouts"], {}
    root = (assets_root or path.parent).resolve()
    for asset in deck.get("assets", []):
        file = root.joinpath(*logical(asset["path"], "asset path").parts).resolve()
        if root not in file.parents and file.parent != root:
            raise InputError(f"asset {asset['asset_id']} lies outside the authorised asset root")
        if not file.is_file() or file.is_symlink() or file.stat().st_size > MAX_ASSET_BYTES:
            raise InputError(f"asset {asset['asset_id']} is missing, linked or oversized")
        if digest(file) != asset["sha256"]:
            raise InputError(f"asset {asset['asset_id']} does not match its recorded hash")
        if "render" not in asset["permitted_uses"]:
            raise InputError(f"asset {asset['asset_id']} is not authorised for rendering")
        width, height = image_size(file, asset["media_type"])
        assets[asset["asset_id"]] = {**asset, "file": file, "pixels": (width, height)}

    used_assets, used_actions = set(), set()
    for page in [*deck["slides"], *deck.get("supports", [])]:
        name = page.get("slide_id") or page["support_id"]
        if page["layout"] not in layouts:
            raise InputError(f"page {name} requests the unknown layout {page['layout']}")
        areas = layouts[page["layout"]]["areas"]
        for block in page["blocks"]:
            placement = block["layout"]
            if "area" in placement and placement["area"] not in areas:
                raise InputError(f"block {block['block_id']} targets the unknown area {placement['area']}")
            if block["type"] == "image":
                used_assets.add(block["data"]["asset_id"])
            if block["type"] == "control":
                used_actions.add(block["data"]["action_id"])
            if block["type"] == "diagram":
                nodes = {node["node_id"] for node in block["data"]["nodes"]}
                if len(nodes) != len(block["data"]["nodes"]):
                    raise InputError(f"diagram {block['block_id']} repeats a node identifier")
                for connector in block["data"].get("connectors", []):
                    if connector["from"] not in nodes or connector["to"] not in nodes:
                        raise InputError(f"connector {connector['connector_id']} references an unknown node")
    missing = used_assets - set(assets)
    if missing:
        raise InputError(f"blocks reference undeclared assets: {sorted(missing)}")
    unused = set(assets) - used_assets
    if unused:
        raise InputError(f"declared assets are never rendered: {sorted(unused)}")
    orphan = used_actions - set(facts["actions"])
    if orphan:
        raise InputError(f"control blocks reference undeclared actions: {sorted(orphan)}")
    idle = set(facts["actions"]) - used_actions
    if idle:
        raise InputError(f"actions have no control block to trigger them: {sorted(idle)}")
    references = {item["reference_id"]: item for item in deck.get("references", [])}
    for action in facts["actions"].values():
        if action["kind"] == "open_external" and "url" not in references[action["target"][1]]:
            raise InputError(f"an external action requires an https reference: {action['target'][1]}")
    return {"deck": deck, "facts": facts, "theme": theme, "assets": assets,
            "references": references, "sha256": digest(path), "source": path}
