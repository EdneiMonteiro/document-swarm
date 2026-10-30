"""Portable ReportLab/Platypus composition; all authorial text comes from Markdown."""

from __future__ import annotations

import hashlib
import html
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import A4, A5
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.pdfdoc import PDFString
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import BaseDocTemplate, Flowable, Frame, KeepTogether, LongTable, PageBreak, PageTemplate, Paragraph, Spacer, TableStyle
from reportlab.platypus.tableofcontents import TableOfContents
from reportlab.platypus.doctemplate import LayoutError

from scripts.checks.common import InputError
from scripts.pdf.source import Block, Document, Inline

WIDTH, HEIGHT = A4
MARGIN = 51
CONTENT_WIDTH = WIDTH - 2 * MARGIN
INK = colors.HexColor("#17344b")
BLUE = colors.HexColor("#216b9a")
TEAL = colors.HexColor("#087f8c")
ORANGE = colors.HexColor("#c27024")
LIGHT = colors.HexColor("#eef4f8")
GRAY = colors.HexColor("#596a78")
FONTS = Path(__file__).with_name("fonts")
FONT_FILES = {
    "DSBody": "Carlito-Regular.ttf", "DSBold": "Carlito-Bold.ttf",
    "DSItalic": "Carlito-Italic.ttf", "DSBoldItalic": "Carlito-BoldItalic.ttf",
    "DSSerif": "IBMPlexSerif-Regular.ttf", "DSSerifBold": "IBMPlexSerif-Bold.ttf",
    "DSSerifItalic": "IBMPlexSerif-Italic.ttf", "DSSerifBoldItalic": "IBMPlexSerif-BoldItalic.ttf",
    "DSMono": "IBMPlexMono-Regular.ttf", "DSMath": "NotoSansMath-Regular.ttf",
}


@dataclass(frozen=True)
class Profile:
    name: str
    body_size: float
    leading: float
    paragraph_space: float
    chapter_size: float
    cover_size: float
    body_font: str = "DSBody"
    heading_font: str = "DSBold"
    accent_hex: str = "#216b9a"
    secondary_hex: str = "#087f8c"
    cover_bg_hex: str = "#fcfaf4"
    box_bg_hex: str = "#eef4f8"
    chapter_pagebreak: bool = True
    page_size: tuple[float, float] = A4
    margin: float = 51.0
    top_margin: float = 62.0
    bottom_margin: float = 51.0

    @property
    def page_width(self) -> float:
        return float(self.page_size[0])

    @property
    def page_height(self) -> float:
        return float(self.page_size[1])

    @property
    def content_width(self) -> float:
        return float(self.page_size[0] - 2 * self.margin)


PROFILES = {
    "textbook": Profile("textbook", 11.25, 16.2, 9, 21, 30),
    "technical-report": Profile("technical-report", 10.8, 15.1, 8, 19, 27),
    "magazine": Profile(
        "magazine", 11.0, 16.2, 9.5, 22, 31,
        heading_font="DSSerifBold", accent_hex="#a83820", secondary_hex="#1b5e7a",
        cover_bg_hex="#faf6ee", box_bg_hex="#f9f4eb", chapter_pagebreak=False,
    ),
    "book": Profile(
        "book", 10.2, 15.0, 8.0, 17.5, 22.5,
        body_font="DSSerif", heading_font="DSSerifBold",
        accent_hex="#7c2d12", secondary_hex="#1e3a5f",
        cover_bg_hex="#ffffff", box_bg_hex="#ffffff",
        chapter_pagebreak=True, page_size=A5,
        margin=36.0, top_margin=43.0, bottom_margin=47.0,
    ),
    "almanac": Profile(
        "almanac", 9.8, 14.2, 7.0, 16.5, 21.5,
        body_font="DSSerif", heading_font="DSBold",
        accent_hex="#b87314", secondary_hex="#0f766e",
        cover_bg_hex="#ffffff", box_bg_hex="#ffffff",
        chapter_pagebreak=True, page_size=A5,
        margin=36.0, top_margin=43.0, bottom_margin=47.0,
    ),
}


def register_fonts() -> list[dict]:
    manifest = json.loads((FONTS / "manifest.json").read_text(encoding="utf-8"))
    files = {item["file"]: item for item in manifest["files"]}
    for name, filename in FONT_FILES.items():
        raw = (FONTS / filename).read_bytes()
        if hashlib.sha256(raw).hexdigest() != files[filename]["sha256"]:
            raise InputError(f"bundled font checksum mismatch: {filename}")
        if name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(name, str(FONTS / filename)))
    for family in ("carlito", "ibmplexserif", "ibmplexmono", "notosansmath"):
        license_file = f"{family}-OFL.txt"
        if hashlib.sha256((FONTS / license_file).read_bytes()).hexdigest() != files[license_file]["sha256"]:
            raise InputError(f"font license checksum mismatch: {family}")
    pdfmetrics.registerFontFamily("DSBody", normal="DSBody", bold="DSBold", italic="DSItalic", boldItalic="DSBoldItalic")
    pdfmetrics.registerFontFamily("DSSerif", normal="DSSerif", bold="DSSerifBold", italic="DSSerifItalic", boldItalic="DSSerifBoldItalic")
    return [{"file": filename, "sha256": files[filename]["sha256"]} for filename in FONT_FILES.values()]


def font_text(text: str, preferred: str) -> str:
    """Escape before markup; fallback glyphs keep their original Unicode value."""
    chunks = []
    family = preferred
    pending = ""
    for character in text:
        if character == "\n":
            if pending:
                chunks.append(f'<font name="{family}">{html.escape(pending)}</font>')
                pending = ""
            chunks.append("<br/>")
            continue
        actual = preferred
        if ord(character) not in pdfmetrics.getFont(preferred).face.charToGlyph:
            if ord(character) in pdfmetrics.getFont("DSMath").face.charToGlyph:
                actual = "DSMath"
            else:
                raise InputError(f"font coverage missing U+{ord(character):04X}; supply supported literal text, not a replacement glyph")
        if actual != family and pending:
            chunks.append(f'<font name="{family}">{html.escape(pending)}</font>')
            pending = ""
        family = actual
        pending += character
    if pending:
        chunks.append(f'<font name="{family}">{html.escape(pending)}</font>')
    return "".join(chunks)


def markup(runs: list[Inline], default_font="DSBody") -> str:
    output = []
    is_serif = default_font.startswith("DSSerif")
    bold_italic = "DSSerifBoldItalic" if is_serif else "DSBoldItalic"
    bold = "DSSerifBold" if is_serif else "DSBold"
    italic = "DSSerifItalic" if is_serif else "DSItalic"
    for run in runs:
        family = bold_italic if run.bold and run.italic else bold if run.bold else italic if run.italic else "DSMono" if run.code else default_font
        text = font_text(run.text, family)
        if run.link:
            target = unquote(run.link) if run.link.startswith("#") else run.link
            text = f'<link href="{html.escape(target, quote=True)}" color="#216b9a">{text}</link>'
        output.append(text)
    return "".join(output)


def record(canvas, identifier, kind, width, height, *, expected_ink=False, parent=None):
    corners = [canvas.absolutePosition(x, y) for x, y in ((0, 0), (width, 0), (0, height), (width, height))]
    xs, ys = zip(*corners)
    page_height = getattr(canvas._docswarm_document, "page_height", HEIGHT)
    canvas._docswarm_document.placements.append({
        "id": identifier, "kind": kind, "page": canvas.getPageNumber(),
        "bbox": [min(xs), page_height - max(ys), max(xs), page_height - min(ys)],
        "expected_ink": expected_ink, "parent": parent,
    })


class TrackedParagraph(Paragraph):
    def __init__(self, *args, element_id=None, element_kind="paragraph", parent_id=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.element_id = element_id
        self.element_kind = element_kind
        self.parent_id = parent_id

    def split(self, width, height):
        parts = super().split(width, height)
        for part in parts:
            part.element_id = self.element_id
            part.element_kind = self.element_kind
            part.parent_id = self.parent_id
        return parts

    def draw(self):
        if self.element_id:
            record(self.canv, self.element_id, self.element_kind, self.width, self.height, parent=self.parent_id)
        super().draw()


class CodeBlock(Flowable):
    def __init__(self, block: Block, lines=None, *, content_width: float = CONTENT_WIDTH):
        super().__init__()
        self.block = block
        self.lines = block.text.splitlines() if lines is None else lines
        self.content_width = content_width
        self.width = content_width
        self.height = len(self.lines) * 13 + 16
        self.spaceBefore, self.spaceAfter = 5, 13
        for line in self.lines:
            for character in line:
                if character != "\t" and ord(character) not in pdfmetrics.getFont("DSMono").face.charToGlyph:
                    raise InputError(f"line {block.line}: code glyph U+{ord(character):04X} is unavailable in the monospaced font")
            if pdfmetrics.stringWidth(line.expandtabs(4), "DSMono", 9.3) > self.width - 16:
                raise InputError(f"line {block.line}: literal code exceeds page width; wrap it explicitly in the source")

    def wrap(self, width, height):
        if width < self.width - .5:
            raise InputError("literal code has insufficient horizontal space")
        return self.width, self.height

    def split(self, width, height):
        count = int((height - 16) // 13)
        if count < 1:
            return []
        return [CodeBlock(self.block, self.lines[:count], content_width=self.content_width), CodeBlock(self.block, self.lines[count:], content_width=self.content_width)]

    def draw(self):
        c = self.canv
        record(c, self.block.id, "code", self.width, self.height)
        c.setFillColor(LIGHT)
        c.roundRect(0, 0, self.width, self.height, 4, stroke=0, fill=1)
        c.setFillColor(INK)
        c.setFont("DSMono", 9.3)
        for index, line in enumerate(self.lines):
            c.drawString(8, self.height - 17 - index * 13, line.expandtabs(4))


class CoverArt(Flowable):
    """Profile- and title-aware cover artwork supporting hero photography and generative vector plates."""

    def __init__(self, identifier, *, compact: bool = False, profile: Profile | None = None, spec: dict | None = None, title: str = ""):
        super().__init__()
        self.identifier = identifier
        self.compact = compact
        self.profile = profile
        self.spec = spec or {}
        self.title = title or ""
        cw = profile.content_width if profile else CONTENT_WIDTH
        ch = 268 if (profile and profile.content_width < 400) else 244
        self.width, self.height = (96, 16) if compact else (cw, ch)

    def _draw_hero_image_cover(self, c, img_path: str):
        from PIL import Image as PilImage
        with PilImage.open(img_path) as im:
            iw, ih = im.size
        accent = colors.HexColor(self.spec.get("accent") or (self.profile.accent_hex if self.profile else "#7c2d12"))
        secondary = colors.HexColor(self.spec.get("secondary") or (self.profile.secondary_hex if self.profile else "#1e3a5f"))
        box_bg = colors.HexColor(self.profile.box_bg_hex if self.profile else "#f6f2ea")
        seed = hashlib.sha256(self.title.encode("utf-8")).digest()

        c.saveState()
        # Outer decorative editorial plate with subtle offset shadow
        c.setFillColor(box_bg)
        c.setStrokeColor(secondary)
        c.setLineWidth(1.0)
        c.roundRect(6, 14, self.width - 12, self.height - 20, 8, stroke=1, fill=1)

        # Dynamic background accent stripes seeded by document title
        c.setStrokeColor(accent)
        c.setLineWidth(0.75)
        stripe_count = 5 + (seed[0] % 4)
        for idx in range(stripe_count):
            y_pos = 24 + idx * ((self.height - 44) / max(1, stripe_count - 1))
            c.line(16, y_pos, 48 + (seed[(idx + 1) % len(seed)] % 28), y_pos)
            c.line(self.width - 48 - (seed[(idx + 3) % len(seed)] % 28), y_pos, self.width - 16, y_pos)

        # Compute centered hero image box with matte frame
        pad_w = 48.0 if self.width < 400 else 84.0
        avail_w = self.width - pad_w
        avail_h = self.height - 44.0
        scale = min(avail_w / max(iw, 1), avail_h / max(ih, 1))
        draw_w = iw * scale
        draw_h = ih * scale
        x0 = (self.width - draw_w) / 2.0
        y0 = (self.height - draw_h) / 2.0 + 2.0

        # Offset accent shadow behind the photo
        c.setFillColor(accent)
        c.roundRect(x0 + 5, y0 - 5, draw_w, draw_h, 4, stroke=0, fill=1)
        # White photo mount
        c.setFillColor(colors.white)
        c.setStrokeColor(INK)
        c.setLineWidth(1.2)
        c.rect(x0 - 4, y0 - 4, draw_w + 8, draw_h + 8, stroke=1, fill=1)
        c.drawImage(img_path, x0, y0, width=draw_w, height=draw_h, preserveAspectRatio=True, mask="auto")

        # Corner registration ticks & bottom accent bar
        c.setStrokeColor(accent)
        c.setLineWidth(1.8)
        c.line(x0 - 10, y0 + draw_h + 8, x0 + 18, y0 + draw_h + 8)
        c.line(x0 - 10, y0 + draw_h + 8, x0 - 10, y0 + draw_h - 16)
        c.line(x0 + draw_w + 10, y0 - 8, x0 + draw_w - 18, y0 - 8)
        c.line(x0 + draw_w + 10, y0 - 8, x0 + draw_w + 10, y0 + 16)
        c.setStrokeColor(secondary)
        c.setLineWidth(2.2)
        c.line(42, 6, self.width - 42, 6)
        c.restoreState()

    def _draw_generative_editorial_cover(self, c):
        accent = colors.HexColor(self.spec.get("accent") or (self.profile.accent_hex if self.profile else "#7c2d12"))
        secondary = colors.HexColor(self.spec.get("secondary") or (self.profile.secondary_hex if self.profile else "#1e3a5f"))
        box_bg = colors.HexColor(self.profile.box_bg_hex if self.profile else "#f6f2ea")
        seed = hashlib.sha256(self.title.encode("utf-8")).digest()

        c.saveState()
        c.setFillColor(box_bg)
        c.setStrokeColor(INK)
        c.setLineWidth(1.1)
        c.roundRect(8, 14, self.width - 16, self.height - 20, 8, stroke=1, fill=1)

        # Celestial / sun disc positioned deterministically from title seed
        sun_x = 110 + (seed[0] % int(max(40, self.width - 220)))
        sun_y = 135 + (seed[1] % 45)
        sun_r = 34 + (seed[2] % 16)
        c.setFillColor(accent)
        c.setStrokeColor(INK)
        c.setLineWidth(1.0)
        c.circle(sun_x, sun_y, sun_r, stroke=1, fill=1)

        # Layered mountain / horizon silhouettes
        for layer_idx, fill_col in enumerate((secondary, INK)):
            p = c.beginPath()
            base_y = 34 + layer_idx * 14
            p.moveTo(16, base_y)
            steps = 7
            step_w = (self.width - 32) / steps
            for s in range(steps + 1):
                px = 16 + s * step_w
                h_var = seed[(s + layer_idx * 5 + 3) % len(seed)] % (68 - layer_idx * 20)
                py = base_y + 28 + h_var if 0 < s < steps else base_y + 18
                p.lineTo(px, py)
            p.lineTo(self.width - 16, 22)
            p.lineTo(16, 22)
            p.close()
            c.setFillColor(fill_col)
            c.drawPath(p, stroke=1, fill=1)

        # Perspective road / geometric rays converging toward horizon
        vanish_x = sun_x
        vanish_y = 58
        c.setStrokeColor(colors.HexColor("#fcfaf4"))
        c.setLineWidth(1.3)
        for offset in (-110, -55, 0, 55, 110):
            c.line(vanish_x, vanish_y, vanish_x + offset * 1.8, 22)
        c.setStrokeColor(accent)
        c.setLineWidth(2.0)
        c.line(48, 6, self.width - 48, 6)
        c.restoreState()

    def draw(self):
        c = self.canv
        record(c, self.identifier, "cover-art", self.width, self.height, expected_ink=True)
        if self.compact:
            c.saveState()
            c.setFillColor(colors.HexColor("#007F89"))
            c.rect(0, 6, 47, 2.5, stroke=0, fill=1)
            c.restoreState()
            return
        img_src = self.spec.get("resolved_src")
        if img_src and Path(img_src).is_file():
            self._draw_hero_image_cover(c, img_src)
            return
        if (self.profile and self.profile.name in {"book", "magazine", "almanac"}) or self.spec.get("style") in {"editorial-poster", "mountain-horizon", "compass-almanac"}:
            self._draw_generative_editorial_cover(c)
            return
        c.saveState()
        c.translate(-16, 0)
        c.setStrokeColor(INK)
        c.setLineWidth(1.25)

        def polygon(points, fill):
            p = c.beginPath()
            p.moveTo(*points[0])
            for point in points[1:]:
                p.lineTo(*point)
            p.close()
            c.setFillColor(fill)
            c.drawPath(p, stroke=1, fill=1)

        paper, shade = colors.HexColor("#fcfaf4"), colors.HexColor("#e7e7dd")
        polygon([(95, 177), (355, 177), (430, 217), (170, 217)], paper)
        polygon([(355, 42), (430, 82), (430, 217), (355, 177)], shade)
        polygon([(95, 42), (355, 42), (355, 177), (95, 177)], paper)
        c.setLineWidth(.48)
        for step in range(1, 29):
            x = 95 + step * 9
            c.line(x, 177, x + 75, 217)
        for step in range(1, 31):
            y = 42 + step * 4.3
            c.line(355, y, 430, y + 40)
        c.setLineWidth(.8)
        c.rect(106, 67, 237, 94, stroke=1, fill=0)
        for step in range(25):
            x = 110 + step * 9.4
            c.line(x, 47, x, 60)
            c.line(x, 163, x, 172)
        for x in (123, 195, 267):
            y, side, dx, dy = 85, 47, 15, 9
            polygon([(x, y + side), (x + side, y + side), (x + side + dx, y + side + dy), (x + dx, y + side + dy)], paper)
            polygon([(x + side, y), (x + side + dx, y + dy), (x + side + dx, y + side + dy), (x + side, y + side)], shade)
            polygon([(x, y), (x + side, y), (x + side, y + side), (x, y + side)], paper)
            c.setLineWidth(.4)
            for step in range(1, 10):
                c.line(x + side, y + step * 4.5, x + side + dx, y + dy + step * 4.5)
            c.setLineWidth(.65)
            c.line(x + 7, y + 7, x + 19, y + 7)
            c.line(x + 7, y + 7, x + 7, y + 19)
            c.line(x + 40, y + 40, x + 28, y + 40)
            c.line(x + 40, y + 40, x + 40, y + 28)
        c.setStrokeColor(TEAL)
        c.line(146, 31, 374, 31)
        c.restoreState()


class ChapterArt(Flowable):
    """Dedicated Chapter Opener engraving plate (Frontispício de Capítulo)."""

    def __init__(self, identifier: str, *, profile: Profile, spec: dict):
        super().__init__()
        self.identifier = identifier
        self.profile = profile
        self.spec = spec or {}
        self.width = profile.content_width
        is_a5 = self.width < 400
        default_h = 250.0 if is_a5 else 316.0
        self.height = float(self.spec.get("max_height") or default_h)

    def wrap(self, availWidth, availHeight):
        return self.width, self.height

    def draw(self):
        c = self.canv
        doc = getattr(c, "_docswarm_document", None)
        if doc is not None:
            doc.chapter_opener_pages.add(int(c.getPageNumber()))
        record(c, self.identifier, "chapter-art", self.width, self.height, expected_ink=True)
        img_path = self.spec.get("resolved_src")
        if not img_path or not Path(img_path).is_file():
            return
        from PIL import Image as PilImage
        with PilImage.open(img_path) as im:
            iw, ih = im.size
        accent = colors.HexColor(self.profile.accent_hex)
        secondary = colors.HexColor(self.profile.secondary_hex)
        box_bg = colors.HexColor(self.profile.box_bg_hex)
        mid_x = self.width / 2.0
        top_rule_y = self.height - 8.0

        c.saveState()
        # Ornamental divider between chapter title and engraving
        c.setStrokeColor(accent)
        c.setLineWidth(1.0)
        c.line(mid_x - 68, top_rule_y, mid_x - 10, top_rule_y)
        c.line(mid_x + 10, top_rule_y, mid_x + 68, top_rule_y)
        c.setFillColor(accent)
        c.circle(mid_x, top_rule_y, 2.8, stroke=0, fill=1)
        c.circle(mid_x - 6, top_rule_y, 1.4, stroke=0, fill=1)
        c.circle(mid_x + 6, top_rule_y, 1.4, stroke=0, fill=1)

        # Outer editorial mat plate
        plate_y = 6.0
        plate_h = self.height - 24.0
        plate_pad_x = 8.0 if self.width < 400 else 14.0
        plate_w = self.width - 2.0 * plate_pad_x
        c.setFillColor(box_bg)
        c.setStrokeColor(secondary)
        c.setLineWidth(0.85)
        c.roundRect(plate_pad_x, plate_y, plate_w, plate_h, 6, stroke=1, fill=1)

        # Compute centered engraving dimensions inside the mat
        avail_w = plate_w - (24.0 if self.width < 400 else 36.0)
        avail_h = plate_h - (22.0 if self.width < 400 else 28.0)
        scale = min(avail_w / max(iw, 1), avail_h / max(ih, 1))
        draw_w = iw * scale
        draw_h = ih * scale
        x0 = (self.width - draw_w) / 2.0
        y0 = plate_y + (plate_h - draw_h) / 2.0

        c.setFillColor(colors.white)
        c.setStrokeColor(INK)
        c.setLineWidth(1.0)
        c.rect(x0 - 3.5, y0 - 3.5, draw_w + 7.0, draw_h + 7.0, stroke=1, fill=1)
        c.drawImage(img_path, x0, y0, width=draw_w, height=draw_h, preserveAspectRatio=True, mask="auto")

        # Corner ticks in accent color
        c.setStrokeColor(accent)
        c.setLineWidth(1.4)
        c.line(x0 - 7, y0 + draw_h + 6, x0 + 12, y0 + draw_h + 6)
        c.line(x0 - 7, y0 + draw_h + 6, x0 - 7, y0 + draw_h - 12)
        c.line(x0 + draw_w + 7, y0 - 6, x0 + draw_w - 12, y0 - 6)
        c.line(x0 + draw_w + 7, y0 - 6, x0 + draw_w + 7, y0 + 12)
        c.restoreState()


class TitlePagePlate(Flowable):
    """Marks a Folio Society typographic Title Page or Colophon Page and draws a printer's vignette."""

    def __init__(self, identifier: str, *, profile: Profile, height: float = 72.0):
        super().__init__()
        self.identifier = identifier
        self.profile = profile
        self.width = profile.content_width
        self.height = height

    def wrap(self, availWidth, availHeight):
        return self.width, self.height

    def draw(self):
        c = self.canv
        record(c, self.identifier, "title-plate", self.width, self.height)
        doc = getattr(c, "_docswarm_document", None)
        if doc is not None:
            doc.title_pages.add(int(c.getPageNumber()))
        accent = colors.HexColor(self.profile.accent_hex)
        secondary = colors.HexColor(self.profile.secondary_hex)
        mid_x = self.width / 2.0
        mid_y = self.height / 2.0
        c.saveState()
        # Horizontal double rules flanking the printer's emblem
        c.setStrokeColor(secondary)
        c.setLineWidth(0.8)
        c.line(mid_x - 95, mid_y, mid_x - 24, mid_y)
        c.line(mid_x + 24, mid_y, mid_x + 95, mid_y)
        # Outer and inner concentric emblem rings
        c.setStrokeColor(accent)
        c.setLineWidth(1.1)
        c.circle(mid_x, mid_y, 14.0, stroke=1, fill=0)
        c.setStrokeColor(secondary)
        c.setLineWidth(0.7)
        c.circle(mid_x, mid_y, 9.5, stroke=1, fill=0)
        # Central compass / diamond motif
        p = c.beginPath()
        p.moveTo(mid_x, mid_y + 6.5)
        p.lineTo(mid_x + 6.5, mid_y)
        p.lineTo(mid_x, mid_y - 6.5)
        p.lineTo(mid_x - 6.5, mid_y)
        p.close()
        c.setFillColor(accent)
        c.drawPath(p, stroke=0, fill=1)
        c.setFillColor(colors.white)
        c.circle(mid_x, mid_y, 2.0, stroke=0, fill=1)
        c.restoreState()


class FleuronDivider(Flowable):
    """Subtle Folio Society ornamental divider between subsections."""

    def __init__(self, profile: Profile):
        super().__init__()
        self.profile = profile
        self.width = profile.content_width
        self.height = 14.0

    def wrap(self, availWidth, availHeight):
        return self.width, self.height

    def draw(self):
        c = self.canv
        accent = colors.HexColor(self.profile.accent_hex)
        secondary = colors.HexColor(self.profile.secondary_hex)
        mid_x = self.width / 2.0
        mid_y = self.height / 2.0
        c.saveState()
        c.setStrokeColor(secondary)
        c.setLineWidth(0.55)
        c.line(mid_x - 44, mid_y, mid_x - 12, mid_y)
        c.line(mid_x + 12, mid_y, mid_x + 44, mid_y)
        c.setFillColor(accent)
        c.circle(mid_x, mid_y, 2.2, stroke=0, fill=1)
        c.circle(mid_x - 6, mid_y, 1.2, stroke=0, fill=1)
        c.circle(mid_x + 6, mid_y, 1.2, stroke=0, fill=1)
        c.restoreState()


class FootnoteRule(Flowable):
    """Classic editorial footnote separator rule."""

    def __init__(self, profile: Profile):
        super().__init__()
        self.profile = profile
        self.width = profile.content_width
        self.height = 8.0

    def wrap(self, availWidth, availHeight):
        return self.width, self.height

    def draw(self):
        c = self.canv
        c.saveState()
        c.setStrokeColor(colors.HexColor(self.profile.secondary_hex))
        c.setLineWidth(0.55)
        c.line(0, 3.0, 76.0, 3.0)
        c.restoreState()


class BackCoverMarker(Flowable):
    """Marks the current page as a dedicated Back Cover (Contracapa) and draws its editorial frame."""

    def __init__(self, identifier: str, profile: Profile):
        super().__init__()
        self.identifier = identifier
        self.profile = profile
        self.width = profile.content_width
        self.height = 8.0

    def draw(self):
        c = self.canv
        doc = getattr(c, "_docswarm_document", None)
        if doc is not None:
            doc.back_cover_pages.add(c.getPageNumber())
        pw, ph = self.profile.page_width, self.profile.page_height
        accent = colors.HexColor(self.profile.accent_hex)
        secondary = colors.HexColor(self.profile.secondary_hex)
        c.saveState()
        # Top and bottom accent bars on the back cover
        x0, y0 = c.absolutePosition(0, 0)
        c.translate(-x0, -y0)
        c.setFillColor(colors.HexColor(self.profile.cover_bg_hex))
        c.rect(0, 0, pw, ph, stroke=0, fill=1)
        if self.profile.name not in {"book", "almanac"}:
            c.setFillColor(accent)
            c.rect(0, ph - 8, pw, 8, stroke=0, fill=1)
            c.setFillColor(secondary)
            c.rect(0, 0, pw, 10, stroke=0, fill=1)
        c.setStrokeColor(secondary)
        c.setLineWidth(0.75)
        c.roundRect(14, 18, pw - 28, ph - 34, 6, stroke=1, fill=0)
        c.restoreState()


class VectorFigure(Flowable):
    def __init__(self, block: Block, *, content_width: float = CONTENT_WIDTH, page_height: float = HEIGHT):
        super().__init__()
        self.block = block
        self.width = content_width
        self.is_compact = self.width < 400
        if block.spec["kind"] == "image":
            from PIL import Image as PilImage
            with PilImage.open(block.spec["resolved_src"]) as im:
                iw, ih = im.size
            max_w = self.width - 16.0
            raw_max_h = float(block.spec.get("max_height") or 240.0)
            max_h = min(raw_max_h * 0.78, page_height * 0.42) if self.is_compact else raw_max_h
            scale = min(max_w / max(iw, 1), max_h / max(ih, 1))
            self.draw_w = iw * scale
            self.draw_h = ih * scale
            self.height = self.draw_h + 14.0
        else:
            count = len(block.spec["labels"])
            row_h = 36 if self.is_compact else 42
            layer_h = 32 if self.is_compact else 36
            flow_h = 80 if self.is_compact else 92
            self.height = (
                (44 if self.is_compact else 50) + count * row_h
                if block.spec["kind"] == "bars"
                else (30 if self.is_compact else 35) + count * layer_h
                if block.spec["kind"] == "layers"
                else 20 + math.ceil(count / 3) * flow_h
            )
        self.spaceAfter = 5
        self.keepWithNext = True

    def label(self, identifier, text, x, y_top, width, available, *, size=10, centered=False):
        p = TrackedParagraph(font_text(text, "DSBold"), ParagraphStyle(
            "figure-label", fontName="DSBold", fontSize=size, leading=size * 1.28,
            textColor=INK, alignment=TA_CENTER if centered else TA_LEFT,
        ), element_id=identifier, element_kind="figure-label", parent_id=self.block.id)
        _, height = p.wrap(width, available)
        if height > available + .1:
            raise InputError(f"figure {self.block.id}: label does not fit; shorten or restructure the authored figure")
        p.drawOn(self.canv, x, y_top - height)

    def arrow(self, start, end):
        c = self.canv
        c.setStrokeColor(TEAL)
        c.setFillColor(TEAL)
        c.setLineWidth(1.4)
        c.line(*start, *end)
        angle = math.atan2(end[1] - start[1], end[0] - start[0])
        p = c.beginPath()
        p.moveTo(*end)
        for direction in (angle + 2.65, angle - 2.65):
            p.lineTo(end[0] + math.cos(direction) * 6, end[1] + math.sin(direction) * 6)
        p.close()
        c.drawPath(p, fill=1, stroke=0)

    def draw(self):
        c = self.canv
        spec = self.block.spec
        record(c, self.block.id, "figure", self.width, self.height, expected_ink=True)
        c.saveState()
        c.setFillColor(colors.white if (spec["kind"] == "image" and self.is_compact) else colors.HexColor("#f7fafc"))
        c.roundRect(0, 0, self.width, self.height, 8, stroke=0, fill=1)
        lbl_sz = 8.4 if self.is_compact else 10
        if spec["kind"] == "image":
            c.setStrokeColor(colors.HexColor("#b8cfdd"))
            c.setLineWidth(0.8)
            c.roundRect(1.5, 1.5, self.width - 3.0, self.height - 3.0, 6, stroke=1, fill=1)
            x_off = (self.width - self.draw_w) / 2.0
            c.drawImage(spec["resolved_src"], x_off, 7.0, width=self.draw_w, height=self.draw_h, preserveAspectRatio=True, mask="auto")
        elif spec["kind"] == "bars":
            row_h = 36 if self.is_compact else 42
            lbl_w = 112 if self.is_compact else 140
            bar_x = 128 if self.is_compact else 165
            val_w = 58 if self.is_compact else 75
            bar_width = self.width - bar_x - val_w - 14 if self.is_compact else self.width - 260
            maximum = max(spec["values"])
            for index, (label, value, display) in enumerate(zip(spec["labels"], spec["values"], spec["display_values"])):
                y = self.height - 14 - index * row_h
                self.label(f"{self.block.id}-label-{index}", label, 10 if self.is_compact else 12, y, lbl_w, 33, size=lbl_sz)
                if "60%" in (spec.get("caption", "") + " " + spec.get("unit", "")):
                    c.setFillColor(ORANGE if value > 60 else BLUE)
                else:
                    c.setFillColor([BLUE, TEAL, ORANGE][index % 3])
                width = bar_width * value / maximum
                c.rect(bar_x, y - 21, width, 17 if self.is_compact else 19, stroke=0, fill=1)
                if width > 0:
                    c.saveState()
                    c.translate(bar_x, y - 21)
                    record(c, f"{self.block.id}-bar-{index}", "bar", width, 17 if self.is_compact else 19, expected_ink=True, parent=self.block.id)
                    c.restoreState()
                self.label(f"{self.block.id}-value-{index}", display, bar_x + bar_width + 7, y, val_w, 31, size=lbl_sz)
            self.label(f"{self.block.id}-unit", spec["unit"], 10 if self.is_compact else 12, 21, self.width - 20, 19, size=8.0 if self.is_compact else 9)
        elif spec["kind"] == "layers":
            step_x = 12 if self.is_compact else 18
            step_h = 32 if self.is_compact else 36
            for index, label in enumerate(spec["labels"]):
                x = 10 + index * step_x
                height = self.height - 22 - index * step_h
                c.setFillColor(LIGHT if index % 2 == 0 else colors.white)
                c.setStrokeColor(colors.HexColor("#b8cfdd"))
                c.roundRect(x, 10, self.width - 2 * x, height, 5, stroke=1, fill=1)
                self.label(f"{self.block.id}-label-{index}", label, x + 8, height + 4, self.width - 2 * x - 16, 27, size=lbl_sz)
        else:
            columns = min(3, len(spec["labels"]))
            gap = 14 if self.is_compact else 24
            mx = 10 if self.is_compact else 15
            row_step = 80 if self.is_compact else 92
            box_h = 58 if self.is_compact else 66
            width = (self.width - 2 * mx - (columns - 1) * gap) / columns
            positions = []
            for index, label in enumerate(spec["labels"]):
                row, column = divmod(index, columns)
                x, y = mx + column * (width + gap), self.height - (box_h + 14) - row * row_step
                c.setFillColor(LIGHT)
                c.setStrokeColor(colors.HexColor("#b8cfdd"))
                c.roundRect(x, y, width, box_h, 6, stroke=1, fill=1)
                self.label(f"{self.block.id}-label-{index}", label, x + 6, y + box_h - 10, width - 12, box_h - 16, size=lbl_sz, centered=True)
                positions.append((x, y))
            for (x, y), (nx, ny) in zip(positions, positions[1:]):
                if y == ny:
                    self.arrow((x + width + 2, y + box_h / 2), (nx - 3, ny + box_h / 2))
                else:
                    self.arrow((x + width / 2, y - 2), (nx + width / 2, ny + box_h + 3))
        c.restoreState()


class TrackedTable(LongTable):
    def __init__(self, *args, element_id="table-layout", element_kind="table", **kwargs):
        super().__init__(*args, **kwargs)
        self.element_id = element_id
        self.element_kind = element_kind

    def split(self, availWidth, availHeight):
        parts = super().split(availWidth, availHeight)
        if len(parts) == 2 and self._nrows > 3:
            repeat = self.repeatRows if isinstance(self.repeatRows, int) else len(self.repeatRows or ())
            second_data_rows = parts[1]._nrows - repeat
            first_data_rows = parts[0]._nrows - repeat
            if second_data_rows == 1 and first_data_rows >= 2 and len(self._argH) >= parts[0]._nrows:
                last_first_row_h = self._argH[parts[0]._nrows - 1] or 20
                if availHeight > last_first_row_h + 10:
                    reduced = super().split(availWidth, availHeight - last_first_row_h - 1)
                    if len(reduced) == 2 and (reduced[1]._nrows - repeat) >= 2:
                        parts = reduced
        for part in parts:
            part.element_id = self.element_id
            part.element_kind = self.element_kind
        return parts

    def draw(self):
        record(self.canv, self.element_id, self.element_kind, self._width, self._height)
        super().draw()


def styles(profile: Profile):
    accent = colors.HexColor(profile.accent_hex)
    secondary = colors.HexColor(profile.secondary_hex)
    h1_color = accent if profile.name in {"magazine", "book", "almanac"} else INK
    h1_space_before = 18 if not profile.chapter_pagebreak else 0
    is_a5 = profile.content_width < 400
    body_font = profile.body_font

    def style(name, font, size, leading, **kwargs):
        options = {"textColor": INK, **kwargs}
        return ParagraphStyle(name, fontName=font, fontSize=size, leading=leading, **options)
    return {
        "paragraph": style("body", body_font, profile.body_size, profile.leading, alignment=TA_JUSTIFY, justifyLastLine=0, spaceAfter=profile.paragraph_space),
        "cover-body": style("cover-body", body_font, profile.body_size if is_a5 else 10.7, profile.leading if is_a5 else 15.1, alignment=TA_JUSTIFY, spaceAfter=profile.paragraph_space if is_a5 else 12),
        "cover-title": style("cover-title", "DSSerifBold", profile.cover_size, round(profile.cover_size * 1.26, 1), textColor=h1_color, alignment=TA_CENTER if is_a5 else TA_LEFT, spaceAfter=12 if is_a5 else 15, keepWithNext=True),
        "cover-subtitle": style("cover-subtitle", "DSSerifItalic" if is_a5 else "DSSerif", 12.2 if is_a5 else 15.5, 17.2 if is_a5 else 21, textColor=secondary, alignment=TA_CENTER if is_a5 else TA_LEFT, spaceAfter=16 if is_a5 else 20, keepWithNext=True),
        "heading": style("h1", profile.heading_font, profile.chapter_size, round(profile.chapter_size * 1.28, 1) if is_a5 else 27, textColor=h1_color, spaceBefore=h1_space_before, spaceAfter=13 if is_a5 else 18, keepWithNext=True),
        "subheading": style("h2", "DSSerifBold" if profile.name == "book" else "DSBold", 12.2 if is_a5 else 14, 16.5 if is_a5 else 19, textColor=accent, spaceBefore=10 if is_a5 else 13, spaceAfter=7 if is_a5 else 10, keepWithNext=True),
        "small-heading": style("small-heading", "DSBold", 10.5 if is_a5 else 11.6, 14.5 if is_a5 else 16, textColor=secondary, spaceBefore=8 if is_a5 else 10, spaceAfter=6 if is_a5 else 8, keepWithNext=True),
        "caption": style("caption", body_font if profile.name == "book" else "DSBody", 8.9 if is_a5 else 10.1, 12.6 if is_a5 else 14.2, textColor=GRAY, alignment=TA_JUSTIFY, spaceAfter=10 if is_a5 else 13),
        "figure-title": style("figure-title", "DSBold", 10.2 if is_a5 else 11.6, 14.2 if is_a5 else 16, textColor=secondary, spaceBefore=8 if is_a5 else 10, spaceAfter=6 if is_a5 else 8, keepWithNext=True),
        "formula": style("formula", "DSMath", 11 if is_a5 else 12, 16 if is_a5 else 18, alignment=TA_CENTER, spaceBefore=6 if is_a5 else 7, spaceAfter=8 if is_a5 else 9),
        "table-cell": style("table", "DSBody", 8.4 if is_a5 else 9.6, 11.6 if is_a5 else 13.2),
        "table-header": style("table-header", "DSBold", 8.4 if is_a5 else 9.6, 11.6 if is_a5 else 13.2, textColor=colors.white),
        "quote": style("quote", body_font, profile.body_size, profile.leading, alignment=TA_JUSTIFY, justifyLastLine=0, leftIndent=14 if is_a5 else 16, spaceAfter=7 if is_a5 else 9),
        "list": style("list", body_font, profile.body_size, profile.leading, alignment=TA_JUSTIFY, justifyLastLine=0, leftIndent=15 if is_a5 else 17, firstLineIndent=0, bulletIndent=0, bulletFontName=body_font, spaceAfter=6 if is_a5 else 7),
    }


class BookDocument(BaseDocTemplate):
    def __init__(self, destination, title, profile: Profile, language, *, corporate_cover: bool = False):
        super().__init__(str(destination), pagesize=profile.page_size, title=title, author="",
                         leftMargin=profile.margin, rightMargin=profile.margin,
                         topMargin=profile.top_margin, bottomMargin=profile.bottom_margin,
                         allowSplitting=True, pageCompression=1, invariant=1)
        self.profile, self.language = profile, language
        self.page_width = profile.page_width
        self.page_height = profile.page_height
        self.margin = profile.margin
        self.content_width = profile.content_width
        self.corporate_cover = corporate_cover
        self.current_heading = title
        self.placements = []
        self.back_cover_pages: set[int] = set()
        self.chapter_opener_pages: set[int] = set()
        self.title_pages: set[int] = set()
        frame = Frame(
            profile.margin, profile.bottom_margin, profile.content_width,
            profile.page_height - profile.top_margin - profile.bottom_margin,
            id="body", leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0,
        )
        self.addPageTemplates(PageTemplate(id="document", frames=[frame], onPage=self.background, onPageEnd=self.chrome))

    def beforeDocument(self):
        self.current_heading = self.title
        self.placements = []
        self.back_cover_pages = set()
        self.chapter_opener_pages = set()
        self.title_pages = set()
        self.canv._docswarm_document = self
        self.canv._doc.Catalog.Lang = PDFString(self.language)

    def background(self, canvas, doc):
        pw, ph = self.page_width, self.page_height
        if doc.page == 1:
            canvas.saveState()
            if self.corporate_cover:
                canvas.setFillColor(colors.white)
                canvas.rect(0, 0, pw, ph, stroke=0, fill=1)
                canvas.setFillColor(colors.HexColor("#F2F6F7"))
                canvas.rect(0, ph - 102.79, pw, 102.79, stroke=0, fill=1)
                logo_path = Path(__file__).resolve().parent / "ms_logo.png"
                if logo_path.is_file():
                    canvas.drawImage(str(logo_path), self.margin, ph - 68.79, width=154.0, height=32.8)
                canvas.setFillColor(colors.HexColor("#007F89"))
                canvas.rect(0, 0, pw, 7, stroke=0, fill=1)
            else:
                canvas.setFillColor(colors.HexColor(self.profile.cover_bg_hex))
                canvas.rect(0, 0, pw, ph, stroke=0, fill=1)
                if self.profile.name not in {"book", "almanac"}:
                    canvas.setFillColor(colors.HexColor(self.profile.secondary_hex))
                    canvas.rect(0, 0, pw, 10, stroke=0, fill=1)
                    if self.profile.name == "magazine":
                        canvas.setFillColor(colors.HexColor(self.profile.accent_hex))
                        canvas.rect(0, ph - 8, pw, 8, stroke=0, fill=1)
                if self.profile.name in {"book", "almanac"}:
                    canvas.setStrokeColor(colors.HexColor(self.profile.secondary_hex))
                    canvas.setLineWidth(0.75)
                    canvas.roundRect(14, 18, pw - 28, ph - 34, 6, stroke=1, fill=0)
                    canvas.setStrokeColor(colors.HexColor(self.profile.accent_hex))
                    canvas.setLineWidth(1.2)
                    mid_x = pw / 2.0
                    canvas.line(self.margin + 18, 36, mid_x - 16, 36)
                    canvas.line(mid_x + 16, 36, pw - self.margin - 18, 36)
                    canvas.setFillColor(colors.HexColor(self.profile.accent_hex))
                    canvas.circle(mid_x, 36, 3.5, stroke=0, fill=1)
                    canvas.circle(mid_x - 8, 36, 1.8, stroke=0, fill=1)
                    canvas.circle(mid_x + 8, 36, 1.8, stroke=0, fill=1)
            canvas.restoreState()

    def chrome(self, canvas, doc):
        if doc.page <= 1 or doc.page in self.back_cover_pages or doc.page in self.title_pages:
            return
        pw, ph = self.page_width, self.page_height
        line_y = ph - (self.profile.top_margin - 14)
        text_y = line_y + 5
        page_y = max(20, self.profile.bottom_margin - 24)
        is_book_folio = self.profile.name in {"book", "almanac"}
        hdr_font = "DSSerifItalic" if self.profile.name == "book" else (self.profile.body_font if is_book_folio else "DSBody")
        folio_font = self.profile.body_font if is_book_folio else "DSBody"
        canvas.saveState()
        if is_book_folio:
            # Subtle inner binding margin rule (left edge on recto/odd pages, right edge on verso/even pages)
            gutter_x = 12.0 if (doc.page % 2 == 1) else (pw - 12.0)
            canvas.setStrokeColor(colors.HexColor("#ebe4d4"))
            canvas.setLineWidth(0.5)
            canvas.line(gutter_x, self.profile.bottom_margin, gutter_x, ph - self.profile.top_margin)
        if doc.page not in self.chapter_opener_pages:
            canvas.setStrokeColor(colors.HexColor("#dfd7c6" if is_book_folio else "#d2e0e9"))
            canvas.setLineWidth(0.6)
            canvas.line(self.margin, line_y, pw - self.margin, line_y)
            # Verso (even) pages display book title on the left; Recto (odd) pages display chapter heading on the right
            raw_hdr = self.title if (is_book_folio and doc.page % 2 == 0) else self.current_heading
            heading = raw_hdr
            if pdfmetrics.stringWidth(heading, hdr_font, 8.0) > self.content_width:
                words = heading.split()
                trimmed = []
                for word in words:
                    candidate = " ".join([*trimmed, word]) + "..."
                    if pdfmetrics.stringWidth(candidate, hdr_font, 8.0) > self.content_width:
                        break
                    trimmed.append(word)
                heading = (" ".join(trimmed) + "...") if trimmed else heading[:50]
            canvas.setFont(hdr_font, 8.0)
            canvas.setFillColor(GRAY)
            if is_book_folio and doc.page % 2 == 1:
                canvas.drawRightString(pw - self.margin, text_y, heading)
            else:
                canvas.drawString(self.margin, text_y, heading)
        canvas.setFont(folio_font, 8.2 if is_book_folio else 8.0)
        canvas.setFillColor(GRAY)
        if is_book_folio and doc.page in self.chapter_opener_pages:
            canvas.drawCentredString(pw / 2.0, page_y, str(doc.page))
        elif is_book_folio and doc.page % 2 == 0:
            canvas.drawString(self.margin, page_y, str(doc.page))
        else:
            canvas.drawRightString(pw - self.margin, page_y, str(doc.page))
        canvas.restoreState()

    def afterFlowable(self, flowable):
        if getattr(flowable, "source_anchor", None):
            self.canv.bookmarkPage(flowable.source_anchor)
        if getattr(flowable, "heading_key", None):
            key = flowable.heading_key
            title = flowable.heading_text
            self.current_heading = title
            self.canv.bookmarkPage(key)
            self.canv.addOutlineEntry(title, key, 0, False)
            self.notify("TOCEntry", (0, title, self.page, key))


def compose(document: Document, destination: Path, profile_name: str, language: str) -> dict:
    if profile_name not in PROFILES:
        raise InputError(f"unknown PDF profile {profile_name}")
    if language not in {"pt-BR", "pt-PT", "en-US", "en-GB", "es-ES"}:
        raise InputError("unsupported PDF language; use pt-BR, pt-PT, en-US, en-GB or es-ES")
    font_records = register_fonts()
    profile = PROFILES[profile_name]
    cw = profile.content_width
    ph = profile.page_height
    pw = profile.page_width
    is_a5 = cw < 400
    style = styles(profile)
    story = []
    headings = []
    corporate_cover = any(b.kind == "cover-art" and i > 0 for i, b in enumerate(document.blocks))
    standalone_book_cover = not corporate_cover and profile.name in {"book", "almanac"}
    front_cover_closed = False
    if corporate_cover:
        story.append(Spacer(1, 102))
    seen_cover_title = False
    title_page_emitted = False
    chapter_first_para_pending = False
    prev_was_body_para = False
    prev_block_kind = ""
    post_cover_art_body_idx = 0
    doc_subtitle_text = next((b.text.strip() for b in document.blocks if b.kind == "cover-subtitle"), "")

    def _append_pagebreak():
        while story and isinstance(story[-1], Spacer):
            story.pop()
        if story and not isinstance(story[-1], PageBreak):
            story.append(PageBreak())

    def _emit_folio_title_page():
        nonlocal title_page_emitted
        if not standalone_book_cover or title_page_emitted:
            return
        title_page_emitted = True
        accent_col = colors.HexColor(profile.accent_hex)
        sec_col = colors.HexColor(profile.secondary_hex)
        tp_kicker = "COLEÇÃO EDITORIAL ILUSTRADA" if language.startswith("pt") else "ILLUSTRATED COLLECTOR EDITION"
        tp_imprint = "EDIÇÃO ILUSTRADA" if language.startswith("pt") else "ILLUSTRATED EDITION"
        story.append(Spacer(1, 52 if is_a5 else 68))
        story.append(Paragraph(
            font_text(tp_kicker, "DSBold"),
            ParagraphStyle("tp-kicker", fontName="DSBold", fontSize=7.8 if is_a5 else 8.8, leading=11.0, textColor=sec_col, alignment=TA_CENTER, spaceAfter=18),
        ))
        story.append(Paragraph(
            font_text(document.title, "DSSerifBold"),
            ParagraphStyle("tp-title", fontName="DSSerifBold", fontSize=18.5 if is_a5 else 22.5, leading=24.0 if is_a5 else 29.0, textColor=accent_col, alignment=TA_CENTER, spaceAfter=10),
        ))
        if doc_subtitle_text:
            story.append(Paragraph(
                font_text(doc_subtitle_text, "DSSerifItalic"),
                ParagraphStyle("tp-sub", fontName="DSSerifItalic", fontSize=10.4 if is_a5 else 12.2, leading=14.8 if is_a5 else 17.0, textColor=INK, alignment=TA_CENTER, leftIndent=18, rightIndent=18, spaceAfter=28),
            ))
        story.append(Spacer(1, 20 if is_a5 else 28))
        story.append(TitlePagePlate("folio-title-page-plate", profile=profile, height=84.0 if is_a5 else 96.0))
        story.append(Spacer(1, 58 if is_a5 else 76))
        story.append(Paragraph(
            font_text(tp_imprint, "DSBold"),
            ParagraphStyle("tp-imprint", fontName="DSBold", fontSize=7.5 if is_a5 else 8.4, leading=11.0, textColor=GRAY, alignment=TA_CENTER),
        ))
        _append_pagebreak()

    for position, block in enumerate(document.blocks):
        if standalone_book_cover and not front_cover_closed and seen_cover_title and block.kind not in {"cover-art", "cover-title", "cover-subtitle", "pagebreak"}:
            _append_pagebreak()
            front_cover_closed = True
            _emit_folio_title_page()
        if block.kind == "pagebreak":
            _append_pagebreak()
            front_cover_closed = True
            _emit_folio_title_page()
            prev_was_body_para = False
            prev_block_kind = "pagebreak"
        elif block.kind == "cover-art":
            cover_spec = dict(block.spec)
            if not cover_spec.get("resolved_src") and profile.name in {"book", "magazine", "almanac"}:
                first_img = next(
                    (b.spec["resolved_src"] for b in document.blocks if b.kind == "figure" and b.spec.get("kind") == "image" and b.spec.get("resolved_src")),
                    None,
                )
                if first_img:
                    cover_spec["resolved_src"] = first_img
            story.extend([CoverArt(block.id, compact=corporate_cover, profile=profile, spec=cover_spec, title=document.title), Spacer(1, 18 if is_a5 else 24)])
            prev_was_body_para = False
            prev_block_kind = "cover-art"
        elif block.kind == "back-cover":
            _append_pagebreak()
            if standalone_book_cover:
                # Emit Folio Society Typographic Colophon page immediately before the Back Cover
                col_hdr = "COLOFÃO TIPOGRÁFICO" if not language.startswith("pt") else "COLOFÃO EDITORIAL E TIPOGRÁFICO"
                col_body = (
                    "Este volume foi composto na família tipográfica IBM Plex Serif, desenhada por Mike Abbink e Bold Monday, "
                    "com olho médio amplo, serifas firmes e entrelinha calibrada para impressão em papel Offset Chandra 90 g/m2 "
                    "de alta alvura e excelente opacidade, valorizando o contraste das hachuras em bico de pena e a nitidez "
                    "cromática das pranchas. As gravuras de abertura de capítulo e ilustrações originais foram desenhadas "
                    "para esta edição, acompanhadas de documentação fotográfica e iconográfica verificada."
                )
                story.extend([
                    Spacer(1, 86 if is_a5 else 110),
                    TitlePagePlate(f"{block.id}-colophon-plate", profile=profile, height=64.0),
                    Spacer(1, 18),
                    Paragraph(
                        font_text(col_hdr, "DSBold"),
                        ParagraphStyle(f"col-hdr-{block.id}", fontName="DSBold", fontSize=8.2, leading=12.0, textColor=colors.HexColor(profile.accent_hex), alignment=TA_CENTER, spaceAfter=10),
                    ),
                    Paragraph(
                        font_text(col_body, "DSSerifItalic"),
                        ParagraphStyle(f"col-body-{block.id}", fontName="DSSerifItalic", fontSize=8.8 if is_a5 else 9.6, leading=13.6 if is_a5 else 15.0, textColor=INK, alignment=TA_CENTER, leftIndent=24, rightIndent=24),
                    ),
                    PageBreak(),
                ])
            story.extend([BackCoverMarker(block.id, profile), Spacer(1, 44 if is_a5 else 56)])
            accent = colors.HexColor(profile.accent_hex)
            secondary = colors.HexColor(profile.secondary_hex)
            box_bg = colors.HexColor(profile.box_bg_hex)
            bc_indent = 24 if is_a5 else 36
            spec = block.spec
            if spec.get("kicker"):
                kicker_style = ParagraphStyle(
                    f"bc-k-{block.id}", fontName="DSBold", fontSize=7.8 if is_a5 else 8.6,
                    leading=11.0 if is_a5 else 12.5, textColor=accent, alignment=TA_CENTER,
                    leftIndent=bc_indent, rightIndent=bc_indent, spaceAfter=10,
                )
                story.append(TrackedParagraph(
                    font_text(spec["kicker"], "DSBold"), kicker_style,
                    element_id=f"{block.id}-kicker", element_kind="back-cover-kicker",
                ))
            if spec.get("title"):
                bc_t_style = ParagraphStyle(
                    f"bc-t-{block.id}", fontName="DSSerifBold", fontSize=14.0 if is_a5 else 17.0,
                    leading=18.2 if is_a5 else 22.0, textColor=INK, alignment=TA_CENTER,
                    leftIndent=bc_indent, rightIndent=bc_indent, spaceAfter=4,
                )
                story.append(TrackedParagraph(
                    font_text(spec["title"], "DSSerifBold"), bc_t_style,
                    element_id=f"{block.id}-title", element_kind="back-cover-title",
                ))
            if spec.get("subtitle"):
                bc_sub_style = ParagraphStyle(
                    f"bc-sub-{block.id}", fontName="DSSerifItalic", fontSize=9.4 if is_a5 else 10.8,
                    leading=13.2 if is_a5 else 15.0, textColor=secondary, alignment=TA_CENTER,
                    leftIndent=bc_indent, rightIndent=bc_indent, spaceAfter=18,
                )
                story.append(TrackedParagraph(
                    font_text(spec["subtitle"], "DSSerifItalic"), bc_sub_style,
                    element_id=f"{block.id}-subtitle", element_kind="back-cover-subtitle",
                ))
            blurbs = spec.get("blurbs") or []
            if blurbs:
                blurb_table_id = f"{block.id}-blurbs"
                b_rows = []
                for idx, blurb in enumerate(blurbs):
                    bq_style = ParagraphStyle(
                        f"bc-bq-{block.id}-{idx}", fontName="DSSerifItalic", fontSize=9.2 if is_a5 else 10.2,
                        leading=13.4 if is_a5 else 14.8, textColor=accent, alignment=TA_CENTER,
                    )
                    b_rows.append([TrackedParagraph(
                        font_text(blurb["quote"], "DSSerifItalic"), bq_style,
                        element_id=f"{block.id}-blurb-{idx}-q", element_kind="back-cover-blurb", parent_id=blurb_table_id,
                    )])
                    if blurb.get("source"):
                        bs_style = ParagraphStyle(
                            f"bc-bs-{block.id}-{idx}", fontName="DSBold", fontSize=7.8 if is_a5 else 8.6,
                            leading=11.0 if is_a5 else 12.2, textColor=secondary, alignment=TA_CENTER,
                        )
                        b_rows.append([TrackedParagraph(
                            font_text(blurb["source"], "DSBold"), bs_style,
                            element_id=f"{block.id}-blurb-{idx}-s", element_kind="back-cover-blurb-source", parent_id=blurb_table_id,
                        )])
                b_table = TrackedTable(
                    b_rows, colWidths=[cw - 2 * bc_indent], repeatRows=0, hAlign="CENTER",
                    splitByRow=1, splitInRow=0, element_id=blurb_table_id, element_kind="back-cover-blurbs",
                )
                b_table.setStyle(TableStyle([
                    ("LINEABOVE", (0, 0), (-1, 0), 0.8, accent),
                    ("LINEBELOW", (0, -1), (-1, -1), 0.8, accent),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                    ("TOPPADDING", (0, 0), (-1, -1), 7),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                ]))
                story.extend([b_table, Spacer(1, 16)])
            syn_style = ParagraphStyle(
                f"bc-syn-{block.id}", fontName=profile.body_font, fontSize=9.6 if is_a5 else 10.5,
                leading=14.4 if is_a5 else 15.6, textColor=INK, alignment=TA_JUSTIFY,
                leftIndent=bc_indent, rightIndent=bc_indent, spaceAfter=18,
            )
            story.append(TrackedParagraph(
                font_text(spec["synopsis"], profile.body_font), syn_style,
                element_id=f"{block.id}-synopsis", element_kind="back-cover-synopsis",
            ))
            highlights = spec.get("highlights") or []
            if highlights:
                hl_style = ParagraphStyle(
                    f"bc-hl-{block.id}", fontName=profile.body_font, fontSize=8.6 if is_a5 else 9.4,
                    leading=12.2 if is_a5 else 13.4, textColor=INK, leftIndent=bc_indent + 10, rightIndent=bc_indent,
                    firstLineIndent=0, bulletFontName=profile.body_font, spaceAfter=4,
                )
                for idx, hl in enumerate(highlights):
                    story.append(TrackedParagraph(
                        font_text(hl, profile.body_font), hl_style,
                        element_id=f"{block.id}-hl-{idx}", element_kind="back-cover-highlight",
                        bulletText="•",
                    ))
                story.append(Spacer(1, 12))
            if spec.get("colophon"):
                col_style = ParagraphStyle(
                    f"bc-col-{block.id}", fontName="DSBold", fontSize=7.8 if is_a5 else 8.5,
                    leading=11.0 if is_a5 else 12.0, textColor=GRAY, alignment=TA_CENTER,
                    leftIndent=bc_indent, rightIndent=bc_indent, spaceBefore=24,
                )
                story.append(TrackedParagraph(
                    font_text(spec["colophon"], "DSBold"), col_style,
                    element_id=f"{block.id}-colophon", element_kind="back-cover-colophon",
                ))
        elif block.kind == "toc":
            toc = TableOfContents()
            toc_font = profile.body_font if profile.name == "book" else "DSBody"
            toc.levelStyles = [ParagraphStyle("toc", fontName=toc_font, fontSize=9.8 if is_a5 else 11.3, leading=14.8 if is_a5 else 17.5, textColor=INK, rightIndent=22 if is_a5 else 28, spaceBefore=5 if is_a5 else 7, spaceAfter=4 if is_a5 else 5)]
            story.extend([toc, PageBreak()])
            if standalone_book_cover:
                plate_entries = []
                chap_idx = 0
                for b_item in document.blocks:
                    if b_item.kind == "chapter-art":
                        chap_idx += 1
                        cap_txt = (b_item.spec or {}).get("caption") or f"Gravura do Capítulo {chap_idx}"
                        short_cap = cap_txt.split(":")[0].strip() if ":" in cap_txt else cap_txt[:68]
                        plate_entries.append((f"Prancha {chap_idx:02d}", f"Abertura do Capítulo {chap_idx}: {short_cap}"))
                    elif b_item.kind == "figure" and (b_item.spec or {}).get("kind") == "image" and (b_item.spec or {}).get("title"):
                        plate_entries.append(("Ilustração", str(b_item.spec["title"])))
                if plate_entries:
                    loi_title = "Lista de Gravuras e Ilustrações" if language.startswith("pt") else "List of Illustrations"
                    story.append(TrackedParagraph(
                        font_text(loi_title, "DSSerifBold"),
                        ParagraphStyle("loi-h1", fontName="DSSerifBold", fontSize=14.0 if is_a5 else 17.0, leading=18.0, textColor=colors.HexColor(profile.accent_hex), spaceAfter=6),
                        element_id=f"{block.id}-loi-h1", element_kind="loi-heading",
                    ))
                    loi_item_style = ParagraphStyle(
                        "loi-item", fontName=profile.body_font, fontSize=7.8 if is_a5 else 9.0,
                        leading=11.0 if is_a5 else 13.0, textColor=INK, spaceAfter=2.0,
                    )
                    for idx_loi, (p_tag, p_title) in enumerate(plate_entries):
                        clean_p_title = re.sub(r"^Figura\s+\d+\s*[-–:]\s*", "", p_title, flags=re.IGNORECASE)
                        line_html = f'<font name="DSBold" color="{profile.secondary_hex}">{font_text(p_tag, "DSBold")}.</font> {font_text(clean_p_title, profile.body_font)}'
                        story.append(TrackedParagraph(line_html, loi_item_style, element_id=f"{block.id}-loi-{idx_loi}", element_kind="loi-item"))
                    _append_pagebreak()
            prev_was_body_para = False
            prev_block_kind = "toc"
        elif block.kind == "chapter-art":
            story.append(ChapterArt(block.id, profile=profile, spec=block.spec))
            if block.spec.get("caption"):
                cap_font = "DSSerifItalic" if profile.name == "book" else "DSItalic"
                cap_style = ParagraphStyle(
                    f"ca-cap-{block.id}", fontName=cap_font, fontSize=8.9 if is_a5 else 10.0,
                    leading=13.0 if is_a5 else 14.4, textColor=INK, alignment=TA_CENTER,
                    leftIndent=14 if is_a5 else 22, rightIndent=14 if is_a5 else 22,
                    spaceBefore=8, spaceAfter=4,
                )
                story.append(TrackedParagraph(
                    font_text(block.spec["caption"], cap_font), cap_style,
                    element_id=f"{block.id}-caption", element_kind="chapter-art-caption",
                ))
            if block.spec.get("credit"):
                cred_style = ParagraphStyle(
                    f"ca-cred-{block.id}", fontName="DSBody", fontSize=7.4 if is_a5 else 8.2,
                    leading=10.4 if is_a5 else 11.5, textColor=GRAY, alignment=TA_CENTER,
                    leftIndent=14 if is_a5 else 22, rightIndent=14 if is_a5 else 22,
                    spaceBefore=2, spaceAfter=6,
                )
                story.append(TrackedParagraph(
                    font_text(block.spec["credit"], "DSBody"), cred_style,
                    element_id=f"{block.id}-credit", element_kind="chapter-art-credit",
                ))
            _append_pagebreak()
        elif block.kind == "footnote":
            fn_font = profile.body_font
            fn_style = ParagraphStyle(
                f"fn-{block.id}", fontName=fn_font, fontSize=8.0 if is_a5 else 8.8,
                leading=11.4 if is_a5 else 12.6, textColor=INK, alignment=TA_JUSTIFY,
                justifyLastLine=0, leftIndent=0, rightIndent=0, spaceAfter=3.5,
            )
            fn_paras = []
            for idx, n_item in enumerate(block.spec.get("notes", [])):
                num_lbl = font_text(str(n_item["number"]), "DSSerifBold" if profile.name == "book" else "DSBold")
                term_lbl = font_text(str(n_item["term"]), "DSSerifItalic" if profile.name == "book" else "DSItalic") if n_item.get("term") else ""
                txt_lbl = font_text(str(n_item["text"]), fn_font)
                prefix_html = f'<font name="DSBold" color="{profile.accent_hex}">[{num_lbl}]</font> '
                if term_lbl:
                    prefix_html += f'<font name="{"DSSerifBoldItalic" if profile.name == "book" else "DSBoldItalic"}">{term_lbl}:</font> '
                fn_paras.append(TrackedParagraph(
                    prefix_html + txt_lbl, fn_style,
                    element_id=f"{block.id}-n{idx}", element_kind="footnote-item",
                ))
            if fn_paras:
                story.append(KeepTogether([Spacer(1, 4), FootnoteRule(profile), Spacer(1, 2), fn_paras[0]]))
                for fn_p in fn_paras[1:]:
                    story.append(KeepTogether([fn_p]))
        elif block.kind == "code":
            story.append(CodeBlock(block, content_width=cw))
        elif block.kind == "figure":
            figure = VectorFigure(block, content_width=cw, page_height=ph)
            if figure.height > ph - (125 if is_a5 else 170):
                raise InputError(f"figure {block.id} is too tall for a page")
            story.append(figure)
        elif block.kind == "pullquote":
            accent = colors.HexColor(profile.accent_hex)
            secondary = colors.HexColor(profile.secondary_hex)
            box_bg = colors.HexColor(profile.box_bg_hex)
            q_style = ParagraphStyle(
                f"pq-q-{block.id}", fontName="DSSerifBold", fontSize=10.6 if is_a5 else 12.4, leading=15.2 if is_a5 else 17.8,
                textColor=INK, alignment=TA_CENTER,
            )
            pq_rows = [[TrackedParagraph(
                font_text(block.spec["quote"], "DSSerifBold"), q_style,
                element_id=f"{block.id}-quote", element_kind="pullquote-quote", parent_id=block.id,
            )]]
            if block.spec.get("attribution"):
                attr_font = "DSSerifItalic" if profile.name == "book" else "DSItalic"
                a_style = ParagraphStyle(
                    f"pq-a-{block.id}", fontName=attr_font, fontSize=8.8 if is_a5 else 10.0, leading=12.6 if is_a5 else 14.2,
                    textColor=secondary, alignment=TA_CENTER,
                )
                pq_rows.append([TrackedParagraph(
                    font_text(block.spec["attribution"], attr_font), a_style,
                    element_id=f"{block.id}-attr", element_kind="pullquote-attr", parent_id=block.id,
                )])
            pq_table = TrackedTable(
                pq_rows, colWidths=[cw], repeatRows=0, hAlign="LEFT",
                splitByRow=1, splitInRow=0, element_id=block.id, element_kind="pullquote",
            )
            pq_table.setStyle(TableStyle([
                ("NOSPLIT", (0, 0), (-1, -1)),
                ("BACKGROUND", (0, 0), (-1, -1), box_bg),
                ("LINEABOVE", (0, 0), (-1, 0), 2.0, accent),
                ("LINEBELOW", (0, -1), (-1, -1), 1.5, accent),
                ("LEFTPADDING", (0, 0), (-1, -1), 14 if is_a5 else 18),
                ("RIGHTPADDING", (0, 0), (-1, -1), 14 if is_a5 else 18),
                ("TOPPADDING", (0, 0), (-1, 0), 9 if is_a5 else 11),
                ("BOTTOMPADDING", (0, -1), (-1, -1), 9 if is_a5 else 11),
            ]))
            story.extend([Spacer(1, 5), pq_table, Spacer(1, 10 if is_a5 else 12)])
        elif block.kind == "callout":
            tone_map = {
                "curiosity": colors.HexColor("#b87314"),
                "backstage": colors.HexColor("#a83820"),
                "key-concept": colors.HexColor("#0f766e"),
                "intro": colors.HexColor("#0f766e"),
            }
            tone_color = tone_map.get(block.spec.get("kind", "info"), colors.HexColor(profile.secondary_hex))
            box_bg = colors.HexColor(profile.box_bg_hex)
            t_style = ParagraphStyle(
                f"co-t-{block.id}", fontName="DSBold", fontSize=9.8 if is_a5 else 11.0, leading=13.6 if is_a5 else 15.0, textColor=tone_color,
            )
            b_style = ParagraphStyle(
                f"co-b-{block.id}", fontName=profile.body_font, fontSize=9.2 if is_a5 else 10.3, leading=13.4 if is_a5 else 14.8,
                textColor=INK, alignment=TA_JUSTIFY,
            )
            co_rows = [
                [TrackedParagraph(
                    font_text(block.spec["title"], "DSBold"), t_style,
                    element_id=f"{block.id}-title", element_kind="callout-title", parent_id=block.id,
                )],
                [TrackedParagraph(
                    font_text(block.spec["body"], profile.body_font), b_style,
                    element_id=f"{block.id}-body", element_kind="callout-body", parent_id=block.id,
                )],
            ]
            co_table = TrackedTable(
                co_rows, colWidths=[cw], repeatRows=0, hAlign="LEFT",
                splitByRow=1, splitInRow=0, element_id=block.id, element_kind="callout",
            )
            co_table.setStyle(TableStyle([
                ("NOSPLIT", (0, 0), (-1, -1)),
                ("BACKGROUND", (0, 0), (-1, -1), box_bg),
                ("LINEBEFORE", (0, 0), (0, -1), 3.5, tone_color),
                ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#d2dce4")),
                ("LEFTPADDING", (0, 0), (-1, -1), 11 if is_a5 else 14),
                ("RIGHTPADDING", (0, 0), (-1, -1), 11 if is_a5 else 14),
                ("TOPPADDING", (0, 0), (-1, 0), 7 if is_a5 else 9),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 3),
                ("TOPPADDING", (0, 1), (-1, 1), 3),
                ("BOTTOMPADDING", (0, 1), (-1, 1), 8 if is_a5 else 10),
            ]))
            story.extend([Spacer(1, 5), co_table, Spacer(1, 10 if is_a5 else 12)])
        elif block.kind == "factbox":
            accent = colors.HexColor(profile.accent_hex)
            box_bg = colors.HexColor(profile.box_bg_hex)
            hdr_p = TrackedParagraph(
                font_text(block.spec["title"], "DSBold"),
                ParagraphStyle(f"fb-t-{block.id}", fontName="DSBold", fontSize=9.4 if is_a5 else 10.4, leading=13.0 if is_a5 else 14.2, textColor=colors.white),
                element_id=f"{block.id}-title", element_kind="factbox-title", parent_id=block.id,
            )
            k_style = ParagraphStyle(f"fb-k-{block.id}", fontName="DSBold", fontSize=8.5 if is_a5 else 9.6, leading=12.0 if is_a5 else 13.4, textColor=INK)
            v_style = ParagraphStyle(f"fb-v-{block.id}", fontName=profile.body_font, fontSize=8.5 if is_a5 else 9.6, leading=12.0 if is_a5 else 13.4, textColor=INK)
            fb_rows = [[hdr_p, ""]]
            for idx, item in enumerate(block.spec["items"]):
                fb_rows.append([
                    TrackedParagraph(
                        font_text(item["label"], "DSBold"), k_style,
                        element_id=f"{block.id}-r{idx}-k", element_kind="factbox-label", parent_id=block.id,
                    ),
                    TrackedParagraph(
                        font_text(item["value"], profile.body_font), v_style,
                        element_id=f"{block.id}-r{idx}-v", element_kind="factbox-value", parent_id=block.id,
                    ),
                ])
            fb_table = TrackedTable(
                fb_rows, colWidths=[cw * 0.34, cw * 0.66],
                repeatRows=1, hAlign="LEFT", splitByRow=1, splitInRow=0,
                element_id=block.id, element_kind="factbox",
            )
            fb_table.setStyle(TableStyle([
                ("SPAN", (0, 0), (1, 0)),
                ("BACKGROUND", (0, 0), (1, 0), accent),
                ("ROWBACKGROUNDS", (0, 1), (1, -1), [colors.white, box_bg]),
                ("BOX", (0, 0), (1, -1), 0.7, accent),
                ("INNERGRID", (0, 1), (1, -1), 0.4, colors.HexColor("#d6e0e8")),
                ("VALIGN", (0, 0), (1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (1, -1), 8 if is_a5 else 10),
                ("RIGHTPADDING", (0, 0), (1, -1), 8 if is_a5 else 10),
                ("TOPPADDING", (0, 0), (1, -1), 5 if is_a5 else 6),
                ("BOTTOMPADDING", (0, 0), (1, -1), 5 if is_a5 else 6),
            ]))
            story.extend([Spacer(1, 5), fb_table, Spacer(1, 6 if block.spec.get("caption") else (10 if is_a5 else 12))])
        elif block.kind == "table":
            ncols = len(block.rows[0])
            if is_a5:
                font_sz = 6.8 if ncols >= 8 else 7.1 if ncols >= 7 else 7.6 if ncols >= 5 else 8.2
                pad_h = 3.0 if ncols >= 7 else 4.0 if ncols >= 5 else 5.0
                fixed_thresh = 48.0
            else:
                font_sz = 7.8 if ncols >= 8 else 8.1 if ncols >= 7 else 9.0 if ncols >= 5 else 9.6
                pad_h = 4 if ncols >= 7 else 5 if ncols >= 6 else 7
                fixed_thresh = 68.0
            lead_sz = round(font_sz * 1.35, 1)
            cell_style = ParagraphStyle(f"tc-{block.id}", parent=style["table-cell"], fontSize=font_sz, leading=lead_sz)
            hdr_style = ParagraphStyle(f"th-{block.id}", parent=style["table-header"], fontSize=font_sz, leading=lead_sz)
            cells = []
            for r, row in enumerate(block.rows):
                cells.append([TrackedParagraph(markup(runs, "DSBold" if r == 0 else "DSBody"),
                                               hdr_style if r == 0 else cell_style,
                                               element_id=f"{block.id}-r{r}-c{c}", element_kind="table-cell",
                                               parent_id=block.id)
                              for c, runs in enumerate(row)])
            col_weights = []
            col_mins = []
            for c in range(ncols):
                max_len = 1
                longest_tok_w = 0.0
                has_url = False
                for r, row in enumerate(block.rows):
                    txt = "".join(run.text for run in row[c]).strip()
                    max_len = max(max_len, len(txt))
                    if r > 0 and ("http://" in txt or "https://" in txt):
                        has_url = True
                    has_code = any(run.code for run in row[c])
                    fname = "DSMono" if has_code else ("DSBold" if r == 0 else "DSBody")
                    for tok in txt.split():
                        longest_tok_w = max(longest_tok_w, pdfmetrics.stringWidth(tok, fname, font_sz))
                if has_url:
                    cap_frac = 0.30 if is_a5 else 0.34
                else:
                    cap_frac = 0.34 if ncols >= 7 else 0.42
                col_mins.append(min(cw * cap_frac, longest_tok_w + 2 * pad_h + 4.0))
                col_weights.append(max(max_len, 4) ** 0.55)
            total_min = sum(col_mins)
            if total_min >= cw:
                fixed = [i for i, m in enumerate(col_mins) if m <= fixed_thresh]
                flex = [i for i, m in enumerate(col_mins) if m > fixed_thresh]
                fixed_sum = sum(col_mins[i] for i in fixed)
                flex_sum = sum(col_mins[i] for i in flex)
                flex_w_sum = sum(col_weights[i] for i in flex)
                if flex and fixed_sum < cw * 0.65 and flex_sum > 0 and flex_w_sum > 0:
                    rem_w = cw - fixed_sum
                    col_widths = [
                        col_mins[i] if i in fixed else rem_w * (0.5 * (col_mins[i] / flex_sum) + 0.5 * (col_weights[i] / flex_w_sum))
                        for i in range(ncols)
                    ]
                else:
                    col_widths = [cw * m / total_min for m in col_mins]
            else:
                col_widths = list(col_mins)
                rem = cw - total_min
                total_w = sum(col_weights)
                for c in range(ncols):
                    col_widths[c] += rem * (col_weights[c] / total_w)
            table = TrackedTable(cells, colWidths=col_widths, repeatRows=1, hAlign="LEFT", splitByRow=1, splitInRow=0, element_id=f"{block.id}-layout")
            table_rules = [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(profile.accent_hex)),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor(profile.box_bg_hex)]),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), pad_h), ("RIGHTPADDING", (0, 0), (-1, -1), pad_h),
                ("TOPPADDING", (0, 0), (-1, -1), 4 if is_a5 else 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 4 if is_a5 else 6),
            ]
            if len(cells) >= 4:
                table_rules.append(("NOSPLIT", (0, -2), (-1, -1)))
            table.setStyle(TableStyle(table_rules))
            story.extend([table, Spacer(1, 10 if is_a5 else 13)])
        elif block.kind == "rule":
            story.append(Spacer(1, 9))
        else:
            key = block.kind
            if key == "heading":
                key = "heading" if block.level == 1 else "subheading" if block.level == 2 else "small-heading"
            current = style[key]
            if block.kind == "cover-title":
                seen_cover_title = True
            if corporate_cover and block.kind in {"cover-title", "cover-subtitle", "cover-body"}:
                if block.kind == "cover-title":
                    current = ParagraphStyle(f"corp-title-{block.id}", fontName="DSBold", fontSize=27.5, leading=35.5,
                                             textColor=colors.HexColor("#193A59"), alignment=TA_LEFT, spaceAfter=30, keepWithNext=True)
                elif block.kind == "cover-subtitle":
                    current = ParagraphStyle(f"corp-sub-{block.id}", fontName="DSBody", fontSize=13.0, leading=18.5,
                                             textColor=colors.HexColor("#536477"), alignment=TA_LEFT, spaceAfter=48, keepWithNext=True)
                elif not seen_cover_title:
                    current = ParagraphStyle(f"corp-kicker-{block.id}", fontName="DSBold", fontSize=13.0, leading=17.0,
                                             textColor=colors.HexColor("#007F89"), alignment=TA_LEFT, spaceAfter=52, keepWithNext=True)
                else:
                    post_cover_art_body_idx += 1
                    txt_clean = block.text.strip()
                    if post_cover_art_body_idx == 1:
                        current = ParagraphStyle(f"corp-auth1-{block.id}", fontName="DSBold", fontSize=21.0, leading=26.0,
                                                 textColor=colors.HexColor("#193A59"), alignment=TA_LEFT, spaceAfter=4)
                    elif "@" in txt_clean and len(txt_clean) <= 60:
                        current = ParagraphStyle(f"corp-email-{block.id}", fontName="DSBody", fontSize=11.5, leading=15.5,
                                                 textColor=colors.HexColor("#007F89"), alignment=TA_LEFT, spaceAfter=8)
                    elif txt_clean.isupper() and len(txt_clean) <= 30:
                        current = ParagraphStyle(f"corp-auth3-{block.id}", fontName="DSBold", fontSize=11.0, leading=15.0,
                                                 textColor=colors.HexColor("#536477"), alignment=TA_LEFT, spaceAfter=72)
                    elif len(txt_clean) <= 45:
                        current = ParagraphStyle(f"corp-auth2-{block.id}", fontName="DSBody", fontSize=13.0, leading=17.0,
                                                 textColor=colors.HexColor("#193A59"), alignment=TA_LEFT, spaceAfter=8)
                    else:
                        current = ParagraphStyle(f"corp-meta-{block.id}", fontName="DSBody", fontSize=10.8, leading=15.2,
                                                 textColor=colors.HexColor("#536477"), alignment=TA_LEFT, spaceAfter=10)
            if block.kind == "caption" and position + 1 < len(document.blocks) and document.blocks[position + 1].kind == "caption":
                current = ParagraphStyle(f"credit-{block.id}", parent=current, keepWithNext=True, spaceAfter=2.0)
            if block.kind == "list":
                depth = max(1, int(block.spec.get("depth") or 1))
                if depth > 1:
                    step = 15 if is_a5 else 17
                    indent = step + (depth - 1) * step
                    current = ParagraphStyle(f"list-d{depth}-{block.id}", parent=current, leftIndent=indent, bulletIndent=indent - step)
            if block.kind == "heading" and block.level == 1 and profile.chapter_pagebreak and story and not isinstance(story[-1], PageBreak):
                _append_pagebreak()
            if block.kind == "heading" and block.level == 1 and re.match(r"^\s*\d+\.", block.text):
                chapter_first_para_pending = True
            if (
                profile.name in {"book", "almanac"}
                and block.kind == "heading"
                and block.level == 2
                and prev_block_kind not in {"heading", "chapter-art", "pagebreak", "toc", ""}
            ):
                story.extend([Spacer(1, 4), FleuronDivider(profile), Spacer(1, 2)])
            has_chapter_art = (
                block.kind == "heading"
                and block.level == 1
                and position + 1 < len(document.blocks)
                and document.blocks[position + 1].kind == "chapter-art"
            )
            if has_chapter_art:
                story.append(Spacer(1, 18 if is_a5 else 26))
                next_spec = document.blocks[position + 1].spec or {}
                kicker_txt = next_spec.get("kicker")
                if not kicker_txt:
                    m_num = re.match(r"^\s*(\d+)\.", block.text)
                    if m_num:
                        kicker_txt = f"CAPÍTULO {m_num.group(1)}" if language.startswith("pt") else f"CHAPTER {m_num.group(1)}"
                if kicker_txt:
                    k_style = ParagraphStyle(
                        f"ca-kicker-{block.id}", fontName="DSBold", fontSize=8.2 if is_a5 else 9.2,
                        leading=11.5 if is_a5 else 13.0, textColor=colors.HexColor(profile.secondary_hex),
                        alignment=TA_CENTER, spaceAfter=6,
                    )
                    story.append(TrackedParagraph(
                        font_text(kicker_txt, "DSBold"), k_style,
                        element_id=f"{block.id}-kicker", element_kind="chapter-opener-kicker",
                    ))
                h1_font = "DSSerifBold" if profile.name in {"book", "almanac"} else profile.heading_font
                current = ParagraphStyle(
                    f"ca-h1-{block.id}", parent=current, fontName=h1_font,
                    fontSize=16.2 if is_a5 else 20.5, leading=21.2 if is_a5 else 26.5,
                    alignment=TA_CENTER, spaceBefore=0, spaceAfter=10 if is_a5 else 14, keepWithNext=False,
                )
            para_markup = markup(block.runs, current.fontName)
            if block.kind == "paragraph" and profile.name == "book":
                if chapter_first_para_pending and block.runs and block.runs[0].text.strip():
                    chapter_first_para_pending = False
                    first_run = block.runs[0]
                    stripped_txt = first_run.text.lstrip()
                    lead_ws = first_run.text[: len(first_run.text) - len(stripped_txt)]
                    first_ch = stripped_txt[0]
                    rest_first_run = stripped_txt[1:]
                    m_word = re.match(r"^(\S+)(.*)$", rest_first_run, re.DOTALL)
                    if m_word:
                        word_tail, after_word = m_word.group(1), m_word.group(2)
                        initial_html = (
                            f'{font_text(lead_ws, current.fontName)}'
                            f'<font name="DSSerifBold" size="{round(current.fontSize * 1.48, 1)}" color="{profile.accent_hex}">'
                            f'{font_text(first_ch, "DSSerifBold")}</font>'
                            f'<font name="DSSerifBold" color="{profile.accent_hex}">{font_text(word_tail, "DSSerifBold")}</font>'
                            f'{font_text(after_word, current.fontName)}'
                        )
                    else:
                        initial_html = (
                            f'{font_text(lead_ws, current.fontName)}'
                            f'<font name="DSSerifBold" size="{round(current.fontSize * 1.48, 1)}" color="{profile.accent_hex}">'
                            f'{font_text(first_ch, "DSSerifBold")}</font>'
                            f'{font_text(rest_first_run, current.fontName)}'
                        )
                    tail_markup = markup(block.runs[1:], current.fontName) if len(block.runs) > 1 else ""
                    para_markup = initial_html + tail_markup
                    current = ParagraphStyle(f"dropcap-{block.id}", parent=current, firstLineIndent=0, spaceAfter=6.0)
                elif prev_was_body_para:
                    current = ParagraphStyle(f"indent-{block.id}", parent=current, firstLineIndent=10.5 if is_a5 else 12.0, spaceAfter=5.5)
            paragraph = TrackedParagraph(para_markup, current,
                                         element_id=block.id, element_kind=block.kind,
                                         bulletText=block.spec.get("marker"))
            if "anchor" in block.spec:
                paragraph.source_anchor = block.spec["anchor"]
            if block.kind == "heading" and block.level == 1:
                is_toc_heading = position + 1 < len(document.blocks) and document.blocks[position + 1].kind == "toc"
                if not is_toc_heading:
                    paragraph.heading_key = block.id
                    paragraph.heading_text = block.text
                    headings.append({"id": block.id, "title": block.text})
            if block.kind == "formula":
                for line in block.text.splitlines():
                    if pdfmetrics.stringWidth(line, "DSMath", current.fontSize) > cw:
                        raise InputError(f"line {block.line}: formula is too wide; provide authored line breaks")
            story.append(paragraph)
            prev_was_body_para = (block.kind == "paragraph")
            prev_block_kind = block.kind
        if block.kind not in {"paragraph", "heading", "list", "quote", "caption", "figure-title", "formula", "cover-title", "cover-subtitle", "cover-body"}:
            prev_was_body_para = False
            prev_block_kind = block.kind
    doc = BookDocument(destination, document.title, profile, language, corporate_cover=corporate_cover)
    try:
        doc.multiBuild(story, maxPasses=8)
    except LayoutError as exc:
        raise InputError(f"source block cannot fit its page; restructure it without dropping text: {exc}") from exc
    return {
        "schema_version": 1, "profile": profile_name, "language": language,
        "page_size": list(profile.page_size),
        "body_frame": [profile.margin, profile.top_margin, pw - profile.margin, ph - profile.bottom_margin],
        "allowed_text_box": [profile.margin - 3, 14, pw - profile.margin + 3, ph - 16],
        "body_font_size": profile.body_size, "body_leading": profile.leading,
        "placements": doc.placements, "headings": headings, "fonts": font_records,
    }
