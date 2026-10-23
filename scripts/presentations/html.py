"""Render the offline HTML package from the frozen deck and layout plan.

The renderer only arranges what the plan already resolved.  Nothing is fetched
over the network, no script comes from the deck, and geometry is emitted into a
separate stylesheet so the package keeps a strict policy without inline styles.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from scripts.checks.common import InputError
from scripts.presentations.fonts import FACES, FONT_ROOT

RUNTIME = Path(__file__).resolve().parent / "runtime"
INSTRUCTIONS = {"pt-BR": "pt-BR", "pt-PT": "pt-BR", "en-US": "en-US", "en-GB": "en-US", "es-ES": "es-ES"}
POSITION = {
    "pt-BR": "Página {current} de {total}", "pt-PT": "Página {current} de {total}",
    "en-US": "Page {current} of {total}", "en-GB": "Page {current} of {total}",
    "es-ES": "Página {current} de {total}",
}
NOTES_LABEL = {"pt-BR": "Notas", "pt-PT": "Notas", "en-US": "Notes", "en-GB": "Notes", "es-ES": "Notas"}
POLICY = ("default-src 'none'; img-src 'self'; font-src 'self'; style-src 'self'; "
          "script-src 'self'; connect-src 'none'; base-uri 'none'; form-action 'none'; object-src 'none'")


def esc(value: str, *, attribute: bool = False) -> str:
    out = value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return out.replace('"', "&quot;").replace("'", "&#39;") if attribute else out


def runs_html(runs: list[dict[str, Any]]) -> str:
    parts = []
    for run in runs:
        text = esc(run["text"])
        if run.get("code"):
            text = f"<code>{text}</code>"
        if run.get("bold"):
            text = f"<strong>{text}</strong>"
        if run.get("italic"):
            text = f"<em>{text}</em>"
        parts.append(text)
    return "".join(parts)


class Renderer:
    def __init__(self, document: dict[str, Any], layout: dict[str, Any]) -> None:
        self.document = document
        self.deck = document["deck"]
        self.theme = document["theme"]
        self.layout = layout
        self.language = self.deck["language"]
        self.rules: list[str] = []
        self.counter = 0
        self.by_page: dict[str, list[dict[str, Any]]] = {}
        for element in layout["elements"]:
            self.by_page.setdefault(element["page_id"], []).append(element)
        self.navigation: dict[tuple[str, str], dict[str, Any]] = {
            (item["page_id"], item["action_id"]): item for item in layout["navigation"]}

    def rule(self, declarations: str) -> str:
        """Register a generated rule and return the identifier that carries it."""
        self.counter += 1
        name = f"e{self.counter}"
        self.rules.append(f"#{name}{{{declarations}}}")
        return name

    @staticmethod
    def geometry(box: dict[str, float]) -> str:
        return f"left:{box['x']}pt;top:{box['y']}pt;width:{box['width']}pt;height:{box['height']}pt"

    def typography(self, token: str) -> str:
        item = self.theme["typography"][token]
        family = "DeckMono" if item["family"] == "mono" else "DeckSans"
        return (f"font-family:'{family}';font-size:{item['size']}pt;line-height:{item['leading']}pt;"
                f"font-weight:{700 if item.get('bold') else 400};color:{self.theme['palette'][item['colour']]}")

    def control(self, page_id: str, element: dict[str, Any], classes: str) -> str:
        action_id = element["action_id"]
        record = self.navigation.get((page_id, action_id))
        if record is None:
            raise InputError(f"control {action_id} on {page_id} has no planned destination")
        name = self.rule(f"{self.geometry(element['box'])};{self.typography('control')}")
        attributes = (f'id="{name}" class="element control {classes}" '
                      f'data-block-id="{esc(element["block_id"], attribute=True)}" '
                      f'data-action-id="{esc(action_id, attribute=True)}"')
        label = esc(element["text"])
        if record.get("reference_id"):
            return (f'<a {attributes} href="{esc(record["url"], attribute=True)}" target="_blank" '
                    f'rel="noopener noreferrer" data-reference-id="{esc(record["reference_id"], attribute=True)}">'
                    f"{label}</a>")
        if not record["enabled"]:
            return f'<button {attributes} type="button" disabled>{label}</button>'
        return (f'<button {attributes} type="button" '
                f'data-target="{esc(record["target_page_id"], attribute=True)}">{label}</button>')

    def element(self, page_id: str, element: dict[str, Any]) -> str:
        kind = element.get("type")
        box, block_id = element["box"], esc(element["block_id"], attribute=True)
        if kind == "title":
            name = self.rule(f"{self.geometry(box)};{self.typography('title')}")
            return f'<h1 id="{name}" class="element title" data-block-id="{block_id}">{esc(element["text"])}</h1>'
        if kind == "text":
            token = element.get("style_token") or "body"
            name = self.rule(f"{self.geometry(box)};{self.typography(token)}")
            paragraphs = []
            for paragraph in element["paragraphs"]:
                marker = {"bullet": "&#8226;", "number": "&#8211;"}.get(paragraph["marker"] or "", "")
                prefix = f'<span class="marker" aria-hidden="true">{marker}</span>' if marker else ""
                body = "<br>".join(runs_html(line["runs"]) for line in paragraph["lines"])
                inner = self.rule(f"padding-left:{paragraph['indent']}pt")
                paragraphs.append(f'<p id="{inner}">{prefix}{body}</p>')
            return f'<div id="{name}" class="element text" data-block-id="{block_id}">{"".join(paragraphs)}</div>'
        if kind == "code":
            name = self.rule(f"{self.geometry(box)};{self.typography('code')}")
            body = "\n".join(esc(line) for line in element["lines"])
            return f'<pre id="{name}" class="element code" data-block-id="{block_id}"><code>{body}</code></pre>'
        if kind == "table":
            name = self.rule(f"{self.geometry(box)};{self.typography('table')}")
            head = "".join(f'<th scope="col">{"<br>".join(runs_html(line["runs"]) for line in cell)}</th>'
                           for cell in element["rows"][0]["cells"])
            body = "".join(
                "<tr>" + "".join(f'<td>{"<br>".join(runs_html(line["runs"]) for line in cell)}</td>'
                                 for cell in row["cells"]) + "</tr>"
                for row in element["rows"][1:])
            return (f'<div id="{name}" class="element table" data-block-id="{block_id}">'
                    f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>")
        if kind == "image":
            name = self.rule(self.geometry(box))
            source = f"assets/{Path(element['file']).name}"
            return (f'<div id="{name}" class="element picture" data-block-id="{block_id}">'
                    f'<img src="{esc(source, attribute=True)}" alt="{esc(element["alt"], attribute=True)}" '
                    f'width="{round(box["width"])}" height="{round(box["height"])}"></div>')
        if kind in ("shape", "diagram-node"):
            name = self.rule(f"{self.geometry(box)};{self.typography('shape')}")
            lines = "<br>".join(runs_html(line["runs"]) for line in element.get("lines", []))
            return (f'<div id="{name}" class="element shape {element.get("shape", "rect")}" '
                    f'data-block-id="{block_id}">{lines}</div>')
        if kind == "diagram-connector":
            points = " ".join(f"{point[0]},{point[1]}" for point in element["points"])
            return (f'<polyline points="{points}" data-block-id="{block_id}" '
                    f'data-from="{esc(element["from"], attribute=True)}" '
                    f'data-to="{esc(element["to"], attribute=True)}"></polyline>')
        if kind == "index-entry":
            return self.control(page_id, element, "entry")
        if kind in ("control", "chrome"):
            return self.control(page_id, element, "chrome" if kind == "chrome" else "authored")
        if kind == "diagram":
            return ""
        raise InputError(f"the HTML renderer cannot compose the element type {kind!r}")

    def page(self, page: dict[str, Any], *, hidden: bool) -> str:
        elements = sorted(self.by_page.get(page["page_id"], []), key=lambda item: item["reading_order"])
        drawn = [self.element(page["page_id"], item) for item in elements
                 if item.get("type") != "diagram-connector"]
        connectors = [self.element(page["page_id"], item) for item in elements
                      if item.get("type") == "diagram-connector"]
        width, height = self.layout["size_pt"]["width"], self.layout["size_pt"]["height"]
        overlay = ""
        if connectors:
            overlay = (f'<svg class="connectors" viewBox="0 0 {width} {height}" aria-hidden="true" focusable="false">'
                       '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
                       'markerHeight="7" orient="auto-start-reverse">'
                       '<path d="M 0 0 L 10 5 L 0 10 z" fill="#087f8c"></path></marker></defs>'
                       + "".join(connectors) + "</svg>")
        name = self.rule(f"width:{width}pt;height:{height}pt")
        return (f'<section id="{name}" class="page" data-page-id="{esc(page["page_id"], attribute=True)}" '
                f'data-kind="{page["kind"]}" aria-label="{esc(page["title"], attribute=True)}"'
                f'{" hidden" if hidden else ""}>' + "".join(drawn) + overlay + "</section>")

    def render(self) -> tuple[str, str]:
        width, height = self.layout["size_pt"]["width"], self.layout["size_pt"]["height"]
        main = [self.page(page, hidden=index > 0)
                for index, page in enumerate(p for p in self.layout["pages"] if p["kind"] != "support")]
        supports: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for page in self.layout["pages"]:
            if page["kind"] == "support":
                supports.setdefault((page["origin"], page["support_id"]), []).append(page)
        dialogs = []
        for (origin, support_id), group in supports.items():
            body = "".join(self.page(page, hidden=index > 0) for index, page in enumerate(group))
            name = self.rule(f"width:{width}pt;height:{height}pt")
            dialogs.append(
                f'<dialog class="support" data-origin="{esc(origin, attribute=True)}" '
                f'data-support-id="{esc(support_id, attribute=True)}" '
                f'aria-label="{esc(group[0]["title"], attribute=True)}">'
                f'<div class="frame"><div id="{name}" class="scaler">{body}</div></div></dialog>')
        stage = self.rule(f"width:{width}pt;height:{height}pt")
        payload = json.dumps({
            "deck_id": self.deck["deck_id"], "language": self.language,
            "labels": {"position": POSITION[self.language]},
            "notes": {page["page_id"]: page["notes"] for page in self.layout["pages"]},
        }, ensure_ascii=False).replace("<", "\\u003c").replace("&", "\\u0026")
        document = (
            f'<!DOCTYPE html>\n<html lang="{esc(self.language, attribute=True)}">\n<head>\n'
            '<meta charset="utf-8">\n'
            f'<meta http-equiv="Content-Security-Policy" content="{POLICY}">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            f"<title>{esc(self.deck['title'])}</title>\n"
            '<link rel="stylesheet" href="runtime/app.css">\n'
            '<link rel="stylesheet" href="runtime/pages.css">\n</head>\n<body>\n'
            f'<header class="app-bar"><strong>{esc(self.deck["title"])}</strong>'
            '<span class="spacer"></span><span id="position"></span>'
            '<button id="notes-toggle" type="button" aria-pressed="false" aria-controls="notes">'
            f'{esc(NOTES_LABEL[self.language])}</button></header>\n'
            f'<main class="viewport"><div class="frame"><div id="{stage}" class="scaler">'
            + "".join(main) + "</div></div></main>\n" + "".join(dialogs) + "\n"
            f'<aside class="notes" id="notes" hidden><h2>{esc(NOTES_LABEL[self.language])}</h2>'
            '<p id="notes-body"></p></aside>\n'
            f'<script id="deck-data" type="application/json">{payload}</script>\n'
            '<script src="runtime/app.js"></script>\n</body>\n</html>\n')
        return document, "\n".join(self.rules) + "\n"


def write_package(document: dict[str, Any], layout: dict[str, Any], destination: Path) -> list[Path]:
    """Write index.html plus every local resource the package needs to run offline."""
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "runtime").mkdir(exist_ok=True)
    (destination / "fonts").mkdir(exist_ok=True)
    written = []
    for name in ("app.css", "app.js"):
        shutil.copy2(RUNTIME / name, destination / "runtime" / name)
        written.append(destination / "runtime" / name)
    for name in FACES.values():
        shutil.copy2(FONT_ROOT / name, destination / "fonts" / name)
        written.append(destination / "fonts" / name)
    for name in ("carlito-OFL.txt", "ibmplexmono-OFL.txt"):
        shutil.copy2(FONT_ROOT / name, destination / "fonts" / name)
        written.append(destination / "fonts" / name)
    if document["assets"]:
        (destination / "assets").mkdir(exist_ok=True)
        for asset in document["assets"].values():
            shutil.copy2(asset["file"], destination / "assets" / asset["file"].name)
            written.append(destination / "assets" / asset["file"].name)
    template = (RUNTIME / f"instructions-{INSTRUCTIONS[document['deck']['language']]}.txt").read_text(encoding="utf-8")
    (destination / "LEIA-ME.txt").write_text(template.format(title=document["deck"]["title"]), encoding="utf-8")
    written.append(destination / "LEIA-ME.txt")
    page, rules = Renderer(document, layout).render()
    (destination / "runtime" / "pages.css").write_text(rules, encoding="utf-8")
    written.append(destination / "runtime" / "pages.css")
    (destination / "index.html").write_text(page, encoding="utf-8")
    written.append(destination / "index.html")
    return written
