"""Portable ReportLab/Platypus composition; all authorial text comes from Markdown."""

from __future__ import annotations

import hashlib
import html
import json
import math
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import A4
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


PROFILES = {
    "textbook": Profile("textbook", 11.25, 16.2, 9, 21, 30),
    "technical-report": Profile("technical-report", 10.8, 15.1, 8, 19, 27),
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
    for run in runs:
        family = "DSBoldItalic" if run.bold and run.italic else "DSBold" if run.bold else "DSItalic" if run.italic else "DSMono" if run.code else default_font
        text = font_text(run.text, family)
        if run.link:
            target = unquote(run.link) if run.link.startswith("#") else run.link
            text = f'<link href="{html.escape(target, quote=True)}" color="#216b9a">{text}</link>'
        output.append(text)
    return "".join(output)


def record(canvas, identifier, kind, width, height, *, expected_ink=False, parent=None):
    corners = [canvas.absolutePosition(x, y) for x, y in ((0, 0), (width, 0), (0, height), (width, height))]
    xs, ys = zip(*corners)
    canvas._docswarm_document.placements.append({
        "id": identifier, "kind": kind, "page": canvas.getPageNumber(),
        "bbox": [min(xs), HEIGHT - max(ys), max(xs), HEIGHT - min(ys)],
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
    def __init__(self, block: Block, lines=None):
        super().__init__()
        self.block = block
        self.lines = block.text.splitlines() if lines is None else lines
        self.width = CONTENT_WIDTH
        self.height = len(self.lines) * 13 + 16
        self.spaceBefore, self.spaceAfter = 5, 13
        for line in self.lines:
            for character in line:
                if character != "\t" and ord(character) not in pdfmetrics.getFont("DSMono").face.charToGlyph:
                    raise InputError(f"line {block.line}: code glyph U+{ord(character):04X} is unavailable in the monospaced font")
            if pdfmetrics.stringWidth(line.expandtabs(4), "DSMono", 9.3) > CONTENT_WIDTH - 16:
                raise InputError(f"line {block.line}: literal code exceeds page width; wrap it explicitly in the source")

    def wrap(self, width, height):
        if width < self.width - .5:
            raise InputError("literal code has insufficient horizontal space")
        return self.width, self.height

    def split(self, width, height):
        count = int((height - 16) // 13)
        if count < 1:
            return []
        return [CodeBlock(self.block, self.lines[:count]), CodeBlock(self.block, self.lines[count:])]

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
    """Original line artwork reused from the reference book; contains no labels."""

    def __init__(self, identifier, *, compact: bool = False):
        super().__init__()
        self.identifier = identifier
        self.compact = compact
        self.width, self.height = (96, 16) if compact else (CONTENT_WIDTH, 244)

    def draw(self):
        c = self.canv
        record(c, self.identifier, "cover-art", self.width, self.height, expected_ink=True)
        if self.compact:
            c.saveState()
            c.setFillColor(colors.HexColor("#007F89"))
            c.rect(0, 6, 47, 2.5, stroke=0, fill=1)
            c.restoreState()
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


class VectorFigure(Flowable):
    def __init__(self, block: Block):
        super().__init__()
        self.block = block
        self.width = CONTENT_WIDTH
        if block.spec["kind"] == "image":
            from PIL import Image as PilImage
            with PilImage.open(block.spec["resolved_src"]) as im:
                iw, ih = im.size
            max_w = self.width - 16.0
            max_h = float(block.spec.get("max_height") or 240.0)
            scale = min(max_w / max(iw, 1), max_h / max(ih, 1))
            self.draw_w = iw * scale
            self.draw_h = ih * scale
            self.height = self.draw_h + 16.0
        else:
            count = len(block.spec["labels"])
            self.height = 50 + count * 42 if block.spec["kind"] == "bars" else 35 + count * 36 if block.spec["kind"] == "layers" else 24 + math.ceil(count / 3) * 92
        self.spaceAfter = 5

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
        c.setFillColor(colors.HexColor("#f7fafc"))
        c.roundRect(0, 0, self.width, self.height, 8, stroke=0, fill=1)
        if spec["kind"] == "image":
            c.setStrokeColor(colors.HexColor("#b8cfdd"))
            c.setLineWidth(0.8)
            c.roundRect(1.5, 1.5, self.width - 3.0, self.height - 3.0, 6, stroke=1, fill=1)
            x_off = (self.width - self.draw_w) / 2.0
            c.drawImage(spec["resolved_src"], x_off, 8.0, width=self.draw_w, height=self.draw_h, preserveAspectRatio=True, mask="auto")
        elif spec["kind"] == "bars":
            bar_x, bar_width = 165, self.width - 260
            maximum = max(spec["values"])
            for index, (label, value, display) in enumerate(zip(spec["labels"], spec["values"], spec["display_values"])):
                y = self.height - 16 - index * 42
                self.label(f"{self.block.id}-label-{index}", label, 12, y, 140, 35)
                if "60%" in (spec.get("caption", "") + " " + spec.get("unit", "")):
                    c.setFillColor(ORANGE if value > 60 else BLUE)
                else:
                    c.setFillColor([BLUE, TEAL, ORANGE][index % 3])
                width = bar_width * value / maximum
                c.rect(bar_x, y - 23, width, 19, stroke=0, fill=1)
                if width > 0:
                    c.saveState()
                    c.translate(bar_x, y - 23)
                    record(c, f"{self.block.id}-bar-{index}", "bar", width, 19, expected_ink=True, parent=self.block.id)
                    c.restoreState()
                self.label(f"{self.block.id}-value-{index}", display, bar_x + bar_width + 9, y, 75, 33)
            self.label(f"{self.block.id}-unit", spec["unit"], 12, 23, self.width - 24, 19, size=9)
        elif spec["kind"] == "layers":
            for index, label in enumerate(spec["labels"]):
                x = 12 + index * 18
                height = self.height - 24 - index * 36
                c.setFillColor(LIGHT if index % 2 == 0 else colors.white)
                c.setStrokeColor(colors.HexColor("#b8cfdd"))
                c.roundRect(x, 12, self.width - 2 * x, height, 5, stroke=1, fill=1)
                self.label(f"{self.block.id}-label-{index}", label, x + 8, height + 5, self.width - 2 * x - 16, 29)
        else:
            columns = min(3, len(spec["labels"]))
            width = (self.width - 30 - (columns - 1) * 24) / columns
            positions = []
            for index, label in enumerate(spec["labels"]):
                row, column = divmod(index, columns)
                x, y = 15 + column * (width + 24), self.height - 82 - row * 92
                c.setFillColor(LIGHT)
                c.setStrokeColor(colors.HexColor("#b8cfdd"))
                c.roundRect(x, y, width, 66, 6, stroke=1, fill=1)
                self.label(f"{self.block.id}-label-{index}", label, x + 8, y + 54, width - 16, 46, centered=True)
                positions.append((x, y))
            for (x, y), (nx, ny) in zip(positions, positions[1:]):
                if y == ny:
                    self.arrow((x + width + 2, y + 33), (nx - 3, ny + 33))
                else:
                    self.arrow((x + width / 2, y - 2), (nx + width / 2, ny + 69))
        c.restoreState()


class TrackedTable(LongTable):
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
                        return reduced
        return parts

    def draw(self):
        record(self.canv, "table-layout", "table", self._width, self._height)
        super().draw()


def styles(profile: Profile):
    def style(name, font, size, leading, **kwargs):
        options = {"textColor": INK, **kwargs}
        return ParagraphStyle(name, fontName=font, fontSize=size, leading=leading, **options)
    return {
        "paragraph": style("body", "DSBody", profile.body_size, profile.leading, alignment=TA_JUSTIFY, justifyLastLine=0, spaceAfter=profile.paragraph_space),
        "cover-body": style("cover-body", "DSBody", 10.7, 15.1, alignment=TA_JUSTIFY, spaceAfter=12),
        "cover-title": style("cover-title", "DSSerifBold", profile.cover_size, profile.cover_size * 1.26, spaceAfter=15, keepWithNext=True),
        "cover-subtitle": style("cover-subtitle", "DSSerif", 15.5, 21, textColor=TEAL, spaceAfter=20, keepWithNext=True),
        "heading": style("h1", "DSBold", profile.chapter_size, 27, spaceAfter=18, keepWithNext=True),
        "subheading": style("h2", "DSBold", 14, 19, textColor=BLUE, spaceBefore=13, spaceAfter=10, keepWithNext=True),
        "small-heading": style("h3", "DSBold", 11.6, 16, textColor=TEAL, spaceBefore=10, spaceAfter=8, keepWithNext=True),
        "caption": style("caption", "DSBody", 10.1, 14.2, textColor=GRAY, alignment=TA_JUSTIFY, spaceAfter=13),
        "figure-title": style("figure-title", "DSBold", 11.6, 16, textColor=TEAL, spaceBefore=10, spaceAfter=8, keepWithNext=True),
        "formula": style("formula", "DSMath", 12, 18, alignment=TA_CENTER, spaceBefore=7, spaceAfter=9),
        "table-cell": style("table", "DSBody", 9.6, 13.2),
        "table-header": style("table-header", "DSBold", 9.6, 13.2, textColor=colors.white),
        "quote": style("quote", "DSBody", profile.body_size, profile.leading, leftIndent=14, spaceAfter=9),
        "list": style("list", "DSBody", profile.body_size, profile.leading, leftIndent=14, firstLineIndent=0, bulletFontName="DSBody", spaceAfter=7),
    }


class BookDocument(BaseDocTemplate):
    def __init__(self, destination, title, profile, language, *, corporate_cover: bool = False):
        super().__init__(str(destination), pagesize=A4, title=title, author="", leftMargin=MARGIN,
                         rightMargin=MARGIN, topMargin=62, bottomMargin=51, allowSplitting=True,
                         pageCompression=1, invariant=1)
        self.profile, self.language = profile, language
        self.corporate_cover = corporate_cover
        self.current_heading = title
        self.placements = []
        frame = Frame(MARGIN, 51, CONTENT_WIDTH, HEIGHT - 113, id="body", leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
        self.addPageTemplates(PageTemplate(id="document", frames=[frame], onPage=self.background, onPageEnd=self.chrome))

    def beforeDocument(self):
        self.current_heading = self.title
        self.placements = []
        self.canv._docswarm_document = self
        self.canv._doc.Catalog.Lang = PDFString(self.language)

    def background(self, canvas, doc):
        if doc.page == 1:
            canvas.saveState()
            if self.corporate_cover:
                canvas.setFillColor(colors.white)
                canvas.rect(0, 0, WIDTH, HEIGHT, stroke=0, fill=1)
                canvas.setFillColor(colors.HexColor("#F2F6F7"))
                canvas.rect(0, HEIGHT - 102.79, WIDTH, 102.79, stroke=0, fill=1)
                logo_path = Path(__file__).resolve().parent / "ms_logo.png"
                if logo_path.is_file():
                    canvas.drawImage(str(logo_path), MARGIN, HEIGHT - 68.79, width=154.0, height=32.8)
                canvas.setFillColor(colors.HexColor("#007F89"))
                canvas.rect(0, 0, WIDTH, 7, stroke=0, fill=1)
            else:
                canvas.setFillColor(colors.HexColor("#fcfaf4"))
                canvas.rect(0, 0, WIDTH, HEIGHT, stroke=0, fill=1)
                canvas.setFillColor(TEAL)
                canvas.rect(0, 0, WIDTH, 10, stroke=0, fill=1)
            canvas.restoreState()

    def chrome(self, canvas, doc):
        if doc.page <= 1:
            return
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor("#d2e0e9"))
        canvas.line(MARGIN, HEIGHT - 40, WIDTH - MARGIN, HEIGHT - 40)
        heading = self.current_heading
        while pdfmetrics.stringWidth(heading, "DSBody", 8.5) > CONTENT_WIDTH:
            heading = heading[:-1]
        canvas.setFont("DSBody", 8.5)
        canvas.setFillColor(GRAY)
        canvas.drawString(MARGIN, HEIGHT - 31, heading)
        canvas.drawRightString(WIDTH - MARGIN, 29, str(doc.page))
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
    style = styles(profile)
    story = []
    headings = []
    corporate_cover = any(b.kind == "cover-art" and i > 0 for i, b in enumerate(document.blocks))
    if corporate_cover:
        story.append(Spacer(1, 102))
    seen_cover_title = False
    post_cover_art_body_idx = 0
    for position, block in enumerate(document.blocks):
        if block.kind == "pagebreak":
            story.append(PageBreak())
        elif block.kind == "cover-art":
            story.extend([CoverArt(block.id, compact=corporate_cover), Spacer(1, 24)])
        elif block.kind == "toc":
            toc = TableOfContents()
            toc.levelStyles = [ParagraphStyle("toc", fontName="DSBody", fontSize=11.3, leading=17.5, textColor=INK, rightIndent=28, spaceBefore=7, spaceAfter=5)]
            story.extend([toc, PageBreak()])
        elif block.kind == "code":
            story.append(CodeBlock(block))
        elif block.kind == "figure":
            figure = VectorFigure(block)
            if figure.height > HEIGHT - 170:
                raise InputError(f"figure {block.id} is too tall for a page")
            story.append(figure)
        elif block.kind == "table":
            ncols = len(block.rows[0])
            font_sz = 7.8 if ncols >= 8 else 8.1 if ncols >= 7 else 9.0 if ncols >= 5 else 9.6
            lead_sz = round(font_sz * 1.35, 1)
            pad_h = 4 if ncols >= 7 else 5 if ncols >= 6 else 7
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
                for r, row in enumerate(block.rows):
                    txt = "".join(run.text for run in row[c]).strip()
                    max_len = max(max_len, len(txt))
                    has_code = any(run.code for run in row[c])
                    fname = "DSMono" if has_code else ("DSBold" if r == 0 else "DSBody")
                    for tok in txt.split():
                        longest_tok_w = max(longest_tok_w, pdfmetrics.stringWidth(tok, fname, font_sz))
                cap_frac = 0.34 if ncols >= 7 else 0.42
                col_mins.append(min(CONTENT_WIDTH * cap_frac, longest_tok_w + 2 * pad_h + 4.0))
                col_weights.append(max(max_len, 4) ** 0.55)
            total_min = sum(col_mins)
            if total_min >= CONTENT_WIDTH:
                fixed = [i for i, m in enumerate(col_mins) if m <= 68.0]
                flex = [i for i, m in enumerate(col_mins) if m > 68.0]
                fixed_sum = sum(col_mins[i] for i in fixed)
                flex_sum = sum(col_mins[i] for i in flex)
                if flex and fixed_sum < CONTENT_WIDTH * 0.65 and flex_sum > 0:
                    rem_w = CONTENT_WIDTH - fixed_sum
                    col_widths = [
                        col_mins[i] if i in fixed else rem_w * (col_mins[i] / flex_sum)
                        for i in range(ncols)
                    ]
                else:
                    col_widths = [CONTENT_WIDTH * m / total_min for m in col_mins]
            else:
                col_widths = list(col_mins)
                rem = CONTENT_WIDTH - total_min
                total_w = sum(col_weights)
                for c in range(ncols):
                    col_widths[c] += rem * (col_weights[c] / total_w)
            table = TrackedTable(cells, colWidths=col_widths, repeatRows=1, hAlign="LEFT", splitByRow=1, splitInRow=0)
            table_rules = [
                ("BACKGROUND", (0, 0), (-1, 0), BLUE),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT]),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), pad_h), ("RIGHTPADDING", (0, 0), (-1, -1), pad_h),
                ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
            if len(cells) >= 4:
                table_rules.append(("NOSPLIT", (0, -2), (-1, -1)))
            table.setStyle(TableStyle(table_rules))
            story.extend([table, Spacer(1, 13)])
        elif block.kind == "rule":
            story.append(Spacer(1, 9))
        else:
            key = block.kind
            if key == "heading":
                key = "heading" if block.level == 1 else "subheading" if block.level == 2 else "small-heading"
            current = style[key]
            if corporate_cover and block.kind in {"cover-title", "cover-subtitle", "cover-body"}:
                if block.kind == "cover-title":
                    seen_cover_title = True
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
            if block.kind == "list":
                depth = max(1, int(block.spec.get("depth") or 1))
                if depth > 1:
                    indent = 14 + (depth - 1) * 14
                    current = ParagraphStyle(f"list-d{depth}-{block.id}", parent=current, leftIndent=indent, bulletIndent=indent - 10)
            if block.kind == "heading" and block.level == 1 and story and not isinstance(story[-1], PageBreak):
                story.append(PageBreak())
            paragraph = TrackedParagraph(markup(block.runs, current.fontName), current,
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
                    if pdfmetrics.stringWidth(line, "DSMath", current.fontSize) > CONTENT_WIDTH:
                        raise InputError(f"line {block.line}: formula is too wide; provide authored line breaks")
            story.append(paragraph)
    doc = BookDocument(destination, document.title, profile, language, corporate_cover=corporate_cover)
    try:
        doc.multiBuild(story, maxPasses=8)
    except LayoutError as exc:
        raise InputError(f"source block cannot fit its page; restructure it without dropping text: {exc}") from exc
    return {
        "schema_version": 1, "profile": profile_name, "language": language,
        "page_size": list(A4), "body_frame": [MARGIN, 62, WIDTH - MARGIN, HEIGHT - 51],
        "allowed_text_box": [MARGIN - 3, 16, WIDTH - MARGIN + 3, HEIGHT - 20],
        "body_font_size": profile.body_size, "body_leading": profile.leading,
        "placements": doc.placements, "headings": headings, "fonts": font_records,
    }
