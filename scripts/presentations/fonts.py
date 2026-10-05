"""Read TrueType advance widths and vertical metrics using only the standard library.

Text measurement must be identical for the planner, the HTML renderer and the
PPTX exporter, so the three never disagree about where a line breaks.  Reading
the real font avoids guessing an average character width.
"""

from __future__ import annotations

import hashlib
import json
import struct
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from scripts.checks.common import InputError

FONT_ROOT = Path(__file__).resolve().parents[1] / "pdf" / "fonts"
FACES = {
    "sans": "Carlito-Regular.ttf",
    "sans-bold": "Carlito-Bold.ttf",
    "sans-italic": "Carlito-Italic.ttf",
    "sans-bold-italic": "Carlito-BoldItalic.ttf",
    "mono": "IBMPlexMono-Regular.ttf",
}
FAMILIES = {"sans": "Carlito", "mono": "IBM Plex Mono"}
MAX_FONT_BYTES = 8 * 1024 * 1024


def _tables(data: bytes) -> dict[bytes, tuple[int, int]]:
    if len(data) < 12:
        raise InputError("truncated font file")
    count = struct.unpack_from(">H", data, 4)[0]
    if not 1 <= count <= 512 or 12 + count * 16 > len(data):
        raise InputError("unsupported font table directory")
    result = {}
    for index in range(count):
        tag, _, offset, length = struct.unpack_from(">4sIII", data, 12 + index * 16)
        if offset + length > len(data):
            raise InputError(f"font table {tag!r} lies outside the file")
        result[tag] = (offset, length)
    return result


def _cmap(data: bytes, offset: int) -> dict[int, int]:
    count = struct.unpack_from(">H", data, offset + 2)[0]
    best = None
    for index in range(count):
        platform, encoding, sub = struct.unpack_from(">HHI", data, offset + 4 + index * 8)
        rank = {(3, 10): 3, (3, 1): 2, (0, 4): 1, (0, 3): 1}.get((platform, encoding), 0)
        if rank and (best is None or rank > best[0]):
            best = (rank, offset + sub)
    if best is None:
        raise InputError("the font has no supported Unicode character map")
    table = best[1]
    fmt = struct.unpack_from(">H", data, table)[0]
    mapping: dict[int, int] = {}
    if fmt == 4:
        segments = struct.unpack_from(">H", data, table + 6)[0] // 2
        ends = struct.unpack_from(f">{segments}H", data, table + 14)
        starts = struct.unpack_from(f">{segments}H", data, table + 16 + segments * 2)
        deltas = struct.unpack_from(f">{segments}h", data, table + 16 + segments * 4)
        range_base = table + 16 + segments * 6
        offsets = struct.unpack_from(f">{segments}H", data, range_base)
        for index in range(segments):
            for code in range(starts[index], min(ends[index], 0xFFFF) + 1):
                if offsets[index] == 0:
                    glyph = (code + deltas[index]) & 0xFFFF
                else:
                    position = range_base + index * 2 + offsets[index] + (code - starts[index]) * 2
                    if position + 2 > len(data):
                        continue
                    glyph = struct.unpack_from(">H", data, position)[0]
                    if glyph:
                        glyph = (glyph + deltas[index]) & 0xFFFF
                if glyph:
                    mapping[code] = glyph
    elif fmt == 12:
        groups = struct.unpack_from(">I", data, table + 12)[0]
        for index in range(min(groups, 200000)):
            start, end, glyph = struct.unpack_from(">III", data, table + 16 + index * 12)
            for code in range(start, min(end, start + 4096) + 1):
                mapping[code] = glyph + code - start
    else:
        raise InputError(f"unsupported character map format {fmt}")
    return mapping


@dataclass(frozen=True)
class Face:
    """Advance widths and vertical metrics of one font file, in em units."""

    key: str
    file: str
    family: str
    units: int
    ascent: float
    descent: float
    widths: dict[int, int]
    cmap: dict[int, int]
    default: int

    def advance(self, character: str) -> float:
        glyph = self.cmap.get(ord(character))
        if glyph is None:
            raise InputError(
                f"{self.family} has no glyph for U+{ord(character):04X}; use supported literal text")
        return self.widths.get(glyph, self.default) / self.units

    def width(self, text: str, size: float) -> float:
        """Return the advance of *text* at *size* points, ignoring kerning."""
        total = 0.0
        for character in text:
            if unicodedata.category(character) in ("Cc", "Cf") and character != "\t":
                raise InputError("control characters are not supported in presentation text")
            total += self.advance(" " if character == "\t" else character)
        return total * size


def load_face(key: str) -> Face:
    name = FACES[key]
    path = FONT_ROOT / name
    if not path.is_file() or path.stat().st_size > MAX_FONT_BYTES:
        raise InputError(f"bundled font face is missing or oversized: {name}")
    data = path.read_bytes()
    manifest = json.loads((FONT_ROOT / "manifest.json").read_text(encoding="utf-8"))
    expected = next((item for item in manifest["files"] if item["file"] == name), None)
    if expected is None or hashlib.sha256(data).hexdigest() != expected["sha256"]:
        raise InputError(f"font face {name} does not match its pinned hash; re-provision the fonts")
    tables = _tables(data)
    for tag in (b"head", b"hhea", b"hmtx", b"cmap"):
        if tag not in tables:
            raise InputError(f"font {name} lacks the required table {tag.decode()}")
    units = struct.unpack_from(">H", data, tables[b"head"][0] + 18)[0]
    ascent, descent = struct.unpack_from(">hh", data, tables[b"hhea"][0] + 4)
    metrics = struct.unpack_from(">H", data, tables[b"hhea"][0] + 34)[0]
    offset = tables[b"hmtx"][0]
    widths = {index: struct.unpack_from(">H", data, offset + index * 4)[0] for index in range(metrics)}
    if not units or not widths:
        raise InputError(f"font {name} declares no usable metrics")
    return Face(key=key, file=name, family=FAMILIES["mono" if key == "mono" else "sans"], units=units,
                ascent=ascent / units, descent=abs(descent) / units, widths=widths,
                cmap=_cmap(data, tables[b"cmap"][0]), default=widths[metrics - 1])


class FontBook:
    """Lazily load the bundled faces and report their provenance."""

    def __init__(self) -> None:
        self._faces: dict[str, Face] = {}

    def face(self, family: str = "sans", *, bold: bool = False, italic: bool = False) -> Face:
        key = "mono" if family == "mono" else "sans" + ("-bold" if bold else "") + ("-italic" if italic else "")
        if key not in FACES:
            raise InputError(f"unsupported font face {key}")
        if key not in self._faces:
            self._faces[key] = load_face(key)
        return self._faces[key]

    def descriptors(self) -> list[dict[str, object]]:
        manifest = json.loads((FONT_ROOT / "manifest.json").read_text(encoding="utf-8"))
        wanted = set(FACES.values())
        return [{"file": item["file"], "family": FAMILIES["mono" if "Mono" in item["file"] else "sans"],
                 "sha256": item["sha256"], "bytes": item["bytes"], "license": f"{item['family']}-OFL.txt"}
                for item in manifest["files"] if item["file"] in wanted]

    def licenses(self) -> list[str]:
        manifest = json.loads((FONT_ROOT / "manifest.json").read_text(encoding="utf-8"))
        families = {item["family"] for item in manifest["files"] if item["file"] in set(FACES.values())}
        return sorted(f"{family}-OFL.txt" for family in families)
