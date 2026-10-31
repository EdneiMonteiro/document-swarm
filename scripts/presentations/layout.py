"""Resolve one geometry that the HTML renderer and both exporters reproduce.

The planner never shrinks type or drops words to make content fit: a block that
does not fit its area is returned to the author as a diagnostic.  Supports flow
across pages, and every authored block lands on exactly one of them.
"""

from __future__ import annotations

from typing import Any

from scripts.checks.common import InputError
from scripts.presentations.fonts import FontBook

NAV_PREVIOUS, NAV_NEXT, NAV_INDEX, NAV_BACK = "sys:nav:previous", "sys:nav:next", "sys:nav:index", "sys:nav:back"
CHROME_LABELS = {
    "pt-BR": {"previous": "Anterior", "next": "Próximo", "index": "Sumário", "back": "Voltar a {title}"},
    "pt-PT": {"previous": "Anterior", "next": "Seguinte", "index": "Índice", "back": "Voltar a {title}"},
    "en-US": {"previous": "Previous", "next": "Next", "index": "Contents", "back": "Back to {title}"},
    "en-GB": {"previous": "Previous", "next": "Next", "index": "Contents", "back": "Back to {title}"},
    "es-ES": {"previous": "Anterior", "next": "Siguiente", "index": "Índice", "back": "Volver a {title}"},
}


def plain(runs: list[dict[str, Any]]) -> str:
    return "".join(run["text"] for run in runs)


class Planner:
    def __init__(self, document: dict[str, Any], profile: str) -> None:
        self.deck = document["deck"]
        self.facts = document["facts"]
        self.theme = document["theme"]
        self.assets = document["assets"]
        self.references = document["references"]
        self.document = document
        self.profile = profile
        self.fonts = FontBook()
        self.labels = CHROME_LABELS[self.deck["language"]]
        self.stage = (self.deck["size_pt"]["width"], self.deck["size_pt"]["height"])
        self.pages: list[dict[str, Any]] = []
        self.elements: list[dict[str, Any]] = []
        self.navigation: list[dict[str, Any]] = []
        self._order = 0

    # -- measurement -------------------------------------------------------
    def style(self, token: str) -> dict[str, Any]:
        style = self.theme["typography"].get(token)
        if style is None:
            raise InputError(f"the theme has no typography token {token}")
        return style

    def face(self, style: dict[str, Any], run: dict[str, Any]):
        mono = run.get("code") or style["family"] == "mono"
        return self.fonts.face("mono" if mono else "sans",
                               bold=bool(run.get("bold") or style.get("bold")) and not mono,
                               italic=bool(run.get("italic")) and not mono)

    def tokens(self, runs: list[dict[str, Any]], style: dict[str, Any]) -> list[dict[str, Any]]:
        result = []
        for run in runs:
            for index, segment in enumerate(run["text"].split("\n")):
                if index:
                    result.append({"break": True})
                for word in segment.replace("\t", "    ").split(" "):
                    result.append({"text": word, "run": run,
                                   "width": self.face(style, run).width(word, style["size"]) if word else 0.0,
                                   "space": self.face(style, run).width(" ", style["size"])})
        return result

    def wrap(self, runs: list[dict[str, Any]], token: str, width: float) -> list[dict[str, Any]]:
        """Break *runs* into lines that fit *width*, preserving every word."""
        style = self.style(token)
        if width <= 0:
            raise InputError("a text block received no usable width")
        lines: list[dict[str, Any]] = [{"pieces": [], "width": 0.0}]
        for item in self.tokens(runs, style):
            if item.get("break"):
                lines.append({"pieces": [], "width": 0.0})
                continue
            if not item["text"]:
                continue
            if item["width"] > width:
                raise InputError(
                    f"the word {item['text']!r} does not fit its area at {style['size']}pt; "
                    "shorten the text or choose a wider area")
            gap = item["space"] if lines[-1]["pieces"] else 0.0
            if lines[-1]["width"] + gap + item["width"] > width:
                lines.append({"pieces": [], "width": 0.0})
                gap = 0.0
            if gap:
                lines[-1]["pieces"].append({"text": " ", "run": lines[-1]["pieces"][-1]["run"]})
            lines[-1]["pieces"].append({"text": item["text"], "run": item["run"]})
            lines[-1]["width"] += gap + item["width"]
        rendered = [line for line in lines if line["pieces"]]
        if not rendered:
            raise InputError("a text block resolved to no visible line")
        return [{"text": "".join(piece["text"] for piece in line["pieces"]),
                 "width": round(line["width"], 3),
                 "runs": [{"text": piece["text"], **{key: bool(piece["run"].get(key))
                                                     for key in ("bold", "italic", "code")}}
                          for piece in line["pieces"]]}
                for line in rendered]

    # -- block geometry ----------------------------------------------------
    def measure(self, block: dict[str, Any], width: float, available: float | None = None) -> dict[str, Any]:
        metrics = self.theme["metrics"]
        kind = block["type"]
        padding = metrics["padding"]
        if kind == "text":
            paragraphs, height = [], 0.0
            for paragraph in block["data"]["paragraphs"]:
                indent = (paragraph.get("level", 0) + (1 if paragraph.get("marker") else 0)) * metrics["gutter"]
                lines = self.wrap(paragraph["runs"], block.get("style_token", "body"), width - indent)
                leading = self.style(block.get("style_token", "body"))["leading"]
                paragraphs.append({"indent": indent, "marker": paragraph.get("marker"), "lines": lines})
                height += len(lines) * leading
            height += (len(paragraphs) - 1) * metrics["padding"]
            return {"height": height, "paragraphs": paragraphs}
        if kind == "table":
            columns = len(block["data"]["header"])
            column_width = (width - columns * 2 * padding) / columns
            rows, height = [], 0.0
            for index, cells in enumerate([block["data"]["header"], *block["data"]["rows"]]):
                if len(cells) != columns:
                    raise InputError(f"table {block['block_id']} has a ragged row")
                token = "table-header" if index == 0 else "table"
                wrapped = [self.wrap(cell, token, column_width) for cell in cells]
                row_height = max(len(lines) for lines in wrapped) * self.style(token)["leading"] + 2 * padding
                rows.append({"cells": wrapped, "height": row_height, "header": index == 0})
                height += row_height
            return {"height": height, "rows": rows, "column_width": column_width}
        if kind == "code":
            style = self.style("code")
            face = self.fonts.face("mono")
            for line in block["data"]["lines"]:
                if face.width(line.replace("\t", "    "), style["size"]) > width - 2 * padding:
                    raise InputError(
                        f"literal code in {block['block_id']} exceeds its area; wrap it explicitly in the deck")
            return {"height": len(block["data"]["lines"]) * style["leading"] + 2 * padding,
                    "lines": [line.replace("\t", "    ") for line in block["data"]["lines"]]}
        if kind == "image":
            asset = self.assets[block["data"]["asset_id"]]
            pixels = asset["pixels"]
            # Assets are placed at their intrinsic size (96 px per inch), only scaled
            # down to respect the area width. The planner never invents a display size.
            natural = (pixels[0] * 0.75, pixels[1] * 0.75)
            scale = min(1.0, width / natural[0])
            return {"height": natural[1] * scale, "width": natural[0] * scale,
                    "aspect": pixels[0] / pixels[1]}
        if kind == "control":
            label = block["data"]["label"]
            style = self.style("control")
            return {"height": metrics["control_height"],
                    "width": self.fonts.face("sans", bold=True).width(label, style["size"])
                             + 2 * metrics["control_padding"] + 4}
        if kind == "shape":
            lines = self.wrap(block["data"]["text"], "shape", width - 2 * padding) if block["data"].get("text") else []
            return {"height": max(metrics["control_height"], len(lines) * self.style("shape")["leading"] + 2 * padding),
                    "lines": lines}
        if kind == "diagram":
            nodes = block["data"]["nodes"]
            height = max(node["box"]["y"] + node["box"]["height"] for node in nodes)
            if max(node["box"]["x"] + node["box"]["width"] for node in nodes) > width + 0.5:
                raise InputError(f"diagram {block['block_id']} is wider than its area")
            return {"height": height, "nodes": nodes}
        raise InputError(f"unsupported block type {kind}")

    def emit(self, page_id: str, block_id: str, native: str, box: dict[str, float],
             payload: dict[str, Any] | None = None, *, reading_order: int | None = None) -> None:
        if (box["x"] < -0.5 or box["y"] < -0.5 or box["x"] + box["width"] > self.stage[0] + 0.5
                or box["y"] + box["height"] > self.stage[1] + 0.5):
            raise InputError(f"{block_id} on {page_id} falls outside the stage; correct the deck or the theme")
        self._order += 1
        self.elements.append({
            "page_id": page_id, "block_id": block_id, "native": native,
            "box": {key: round(value, 3) for key, value in box.items()},
            "reading_order": reading_order or self._order, **(payload or {}),
        })

    def place(self, page_id: str, block: dict[str, Any], box: dict[str, float], measured: dict[str, Any]) -> None:
        kind = block["type"]
        payload = {"type": kind, "style_token": block.get("style_token")}
        if kind == "text":
            payload["paragraphs"] = measured["paragraphs"]
            payload["fragments"] = [line["text"] for paragraph in measured["paragraphs"]
                                    for line in paragraph["lines"]]
            payload["text"] = "\n".join(line["text"] for paragraph in measured["paragraphs"] for line in paragraph["lines"])
            self.emit(page_id, block["block_id"], "text", box, payload, reading_order=block.get("reading_order"))
        elif kind == "table":
            payload["fragments"] = [line["text"] for row in measured["rows"]
                                    for cell in row["cells"] for line in cell]
            payload.update(rows=measured["rows"], column_width=measured["column_width"],
                           text="\n".join(" ".join(line["text"] for lines in row["cells"] for line in lines)
                                          for row in measured["rows"]))
            self.emit(page_id, block["block_id"], "table", box, payload, reading_order=block.get("reading_order"))
        elif kind == "code":
            payload.update(lines=measured["lines"], text="\n".join(measured["lines"]),
                           fragments=list(measured["lines"]))
            self.emit(page_id, block["block_id"], "code", box, payload, reading_order=block.get("reading_order"))
        elif kind == "image":
            asset = self.assets[block["data"]["asset_id"]]
            payload.update(asset_id=asset["asset_id"], alt=block["data"]["alt"], media_type=asset["media_type"],
                           file=str(asset["file"]), text=block["data"]["alt"],
                           fragments=[block["data"]["alt"]])
            self.emit(page_id, block["block_id"], "picture", box, payload, reading_order=block.get("reading_order"))
        elif kind == "shape":
            payload["fragments"] = [line["text"] for line in measured["lines"]]
            payload.update(shape=block["data"]["shape"], lines=measured["lines"],
                           text=plain(block["data"].get("text", [])) if block["data"].get("text") else "")
            self.emit(page_id, block["block_id"], "shape", box, payload, reading_order=block.get("reading_order"))
        elif kind == "control":
            payload.update(action_id=block["data"]["action_id"], text=block["data"]["label"],
                           fragments=[block["data"]["label"]])
            self.emit(page_id, block["block_id"], "control", box, payload, reading_order=block.get("reading_order"))
        elif kind == "diagram":
            self.emit(page_id, block["block_id"], "group", box,
                      {"type": "diagram", "text": ""}, reading_order=block.get("reading_order"))
            nodes = {}
            for node in measured["nodes"]:
                node_box = {"x": box["x"] + node["box"]["x"], "y": box["y"] + node["box"]["y"],
                            "width": node["box"]["width"], "height": node["box"]["height"]}
                nodes[node["node_id"]] = node_box
                lines = self.wrap(node["text"], "shape", node_box["width"] - 2 * self.theme["metrics"]["padding"])
                if len(lines) * self.style("shape")["leading"] > node_box["height"]:
                    raise InputError(f"node {node['node_id']} does not fit its declared box")
                self.emit(page_id, f"{block['block_id']}::{node['node_id']}", "shape", node_box,
                          {"type": "diagram-node", "shape": node["shape"], "lines": lines,
                           "fragments": [line["text"] for line in lines],
                           "text": plain(node["text"]), "parent": block["block_id"]})
            for connector in block["data"].get("connectors", []):
                self.emit(page_id, f"{block['block_id']}::{connector['connector_id']}", "connector",
                          self._connector_box(nodes[connector["from"]], nodes[connector["to"]]),
                          {"type": "diagram-connector", "kind": connector["kind"], "text": "",
                           "from": f"{block['block_id']}::{connector['from']}",
                           "to": f"{block['block_id']}::{connector['to']}",
                           "from_port": connector["from_port"], "to_port": connector["to_port"],
                           "points": self._points(nodes[connector["from"]], connector["from_port"],
                                                  nodes[connector["to"]], connector["to_port"], connector["kind"]),
                           "parent": block["block_id"]})

    @staticmethod
    def _port(box: dict[str, float], port: str) -> tuple[float, float]:
        return {
            "left": (box["x"], box["y"] + box["height"] / 2),
            "right": (box["x"] + box["width"], box["y"] + box["height"] / 2),
            "top": (box["x"] + box["width"] / 2, box["y"]),
            "bottom": (box["x"] + box["width"] / 2, box["y"] + box["height"]),
        }[port]

    def _points(self, start: dict[str, float], from_port: str, end: dict[str, float],
                to_port: str, kind: str) -> list[list[float]]:
        a, b = self._port(start, from_port), self._port(end, to_port)
        if kind == "straight":
            return [[round(a[0], 3), round(a[1], 3)], [round(b[0], 3), round(b[1], 3)]]
        middle = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        bend = [middle[0], a[1]], [middle[0], b[1]]
        if from_port in ("top", "bottom"):
            bend = [a[0], middle[1]], [b[0], middle[1]]
        return [[round(value, 3) for value in point] for point in (a, *bend, b)]

    def _connector_box(self, start: dict[str, float], end: dict[str, float]) -> dict[str, float]:
        xs = [start["x"], start["x"] + start["width"], end["x"], end["x"] + end["width"]]
        ys = [start["y"], start["y"] + start["height"], end["y"], end["y"] + end["height"]]
        return {"x": min(xs), "y": min(ys), "width": max(max(xs) - min(xs), 1.0), "height": max(max(ys) - min(ys), 1.0)}

    # -- pages -------------------------------------------------------------
    def areas(self, layout_name: str) -> dict[str, dict[str, float]]:
        layout = self.theme["layouts"].get(layout_name)
        if layout is None:
            raise InputError(f"unknown layout {layout_name}")
        return layout["areas"]

    def title(self, page_id: str, runs: list[dict[str, Any]], area: dict[str, float]) -> None:
        lines = self.wrap(runs, "title", area["width"])
        height = len(lines) * self.style("title")["leading"]
        if height > area["height"] + 0.5:
            raise InputError(f"the title of {page_id} does not fit its area; shorten it or change the layout")
        self.emit(page_id, f"sys:title:{page_id}", "title", {**area, "height": height},
                  {"type": "title", "lines": lines, "fragments": [line["text"] for line in lines],
                   "text": plain(runs)}, reading_order=0)

    def flow(self, page_id: str, blocks: list[dict[str, Any]], areas: dict[str, dict[str, float]],
             *, paginate: bool) -> list[list[dict[str, Any]]]:
        """Place *blocks* inside their areas, optionally splitting into pages."""
        pages: list[list[dict[str, Any]]] = [[]]
        cursors = {name: area["y"] for name, area in areas.items()}
        for block in blocks:
            placement = block["layout"]
            if "box" in placement:
                if paginate:
                    raise InputError(f"support block {block['block_id']} must use a layout area, not a fixed box")
                box = dict(placement["box"])
                self.place(page_id, block, box, self.measure(block, box["width"], box["height"]))
                pages[-1].append(block)
                continue
            area = areas[placement["area"]]
            bottom = area["y"] + area["height"]
            measured = self.measure(block, area["width"], bottom - cursors[placement["area"]])
            top = cursors[placement["area"]]
            if top + measured["height"] > bottom + 0.5:
                if not paginate or not pages[-1]:
                    raise InputError(
                        f"block {block['block_id']} overflows the area {placement['area']}; "
                        "shorten the content or move it to another page")
                pages.append([])
                cursors = {name: item["y"] for name, item in areas.items()}
                top = cursors[placement["area"]]
                measured = self.measure(block, area["width"], bottom - top)
                if top + measured["height"] > bottom + 0.5:
                    raise InputError(f"block {block['block_id']} does not fit an empty {placement['area']} area")
            self.place(page_id, block, {"x": area["x"], "y": top,
                                        "width": measured.get("width", area["width"]),
                                        "height": measured["height"]}, measured)
            cursors[placement["area"]] = top + measured["height"] + self.theme["metrics"]["gutter"]
            pages[-1].append(block)
        return pages

    def chrome(self, page_id: str, area: dict[str, float], controls: list[tuple[str, str, str | None]]) -> None:
        style, metrics = self.style("control"), self.theme["metrics"]
        cursor = area["x"]
        for action_id, label, target in controls:
            width = self.fonts.face("sans", bold=True).width(label, style["size"]) + 2 * metrics["control_padding"] + 4
            box = {"x": cursor, "y": area["y"], "width": width, "height": metrics["control_height"]}
            self.emit(page_id, f"{page_id}::{action_id}", "control", box,
                      {"type": "chrome", "action_id": action_id, "text": label,
                       "fragments": [label], "enabled": target is not None})
            self.navigation.append({"page_id": page_id, "action_id": action_id, "enabled": target is not None,
                                    "target_page_id": target, "name": label,
                                    "region": {key: round(value, 3) for key, value in box.items()}})
            cursor += width + metrics["gutter"]
        if cursor - metrics["gutter"] > area["x"] + area["width"] + 0.5:
            raise InputError(f"the navigation controls of {page_id} do not fit their area")

    def run(self) -> dict[str, Any]:
        facts, deck = self.facts, self.deck
        slide_titles = {slide["slide_id"]: plain(slide["title"]) for slide in deck["slides"]}
        index_areas = self.areas("title-and-body")
        row = self.style("body")["leading"] + self.theme["metrics"]["padding"]
        per_page = max(1, int(index_areas["body"]["height"] // row))
        chunks = [facts["slide_ids"][start:start + per_page] for start in range(0, len(facts["slide_ids"]), per_page)] or [[]]
        index_ids = [f"sys:index:{number}" for number in range(1, len(chunks) + 1)]
        sequence = index_ids + facts["slide_ids"]

        for number, entries in enumerate(chunks, 1):
            page_id = f"sys:index:{number}"
            self.title(page_id, [{"text": deck["index"]["title"]}], index_areas["title"])
            top = index_areas["body"]["y"]
            for entry in entries:
                label = slide_titles[entry]
                box = {"x": index_areas["body"]["x"], "y": top, "width": index_areas["body"]["width"],
                       "height": self.style("body")["leading"]}
                lines = self.wrap([{"text": label}], "body", box["width"] - 2 * self.theme["metrics"]["padding"])
                if len(lines) > 1:
                    raise InputError(f"the index entry for {entry} does not fit one line")
                self.emit(page_id, f"sys:index:entry:{entry}", "control", box,
                          {"type": "index-entry", "action_id": f"sys:index:entry:{entry}",
                           "text": label, "enabled": True, "lines": lines, "fragments": [label]})
                self.navigation.append({"page_id": page_id, "action_id": f"sys:index:entry:{entry}",
                                        "enabled": True, "target_page_id": entry, "name": label,
                                        "region": {key: round(value, 3) for key, value in box.items()}})
                top += row
            position = sequence.index(page_id)
            self.chrome(page_id, index_areas["controls"], [
                (NAV_PREVIOUS, self.labels["previous"], sequence[position - 1] if position else None),
                (NAV_NEXT, self.labels["next"], sequence[position + 1] if position + 1 < len(sequence) else None),
            ])
            self.pages.append({"page_id": page_id, "kind": "index", "number": number, "entries": entries,
                               "blocks": [], "notes_source": None, "notes": "",
                               "title": deck["index"]["title"]})

        by_id = {slide["slide_id"]: slide for slide in deck["slides"]}
        for slide_id in facts["slide_ids"]:
            slide = by_id[slide_id]
            areas = self.areas(slide["layout"])
            self.title(slide_id, slide["title"], areas["title"])
            self.flow(slide_id, slide["blocks"], areas, paginate=False)
            position = sequence.index(slide_id)
            self.chrome(slide_id, areas["controls"], [
                (NAV_PREVIOUS, self.labels["previous"], sequence[position - 1] if position else None),
                (NAV_NEXT, self.labels["next"], sequence[position + 1] if position + 1 < len(sequence) else None),
                (NAV_INDEX, self.labels["index"], index_ids[0]),
            ])
            for block in slide["blocks"]:
                if block["type"] == "control":
                    action = facts["actions"][block["data"]["action_id"]]
                    kind, target = action["target"]
                    entry = {"page_id": slide_id, "action_id": block["data"]["action_id"], "enabled": True,
                             "name": block["data"]["label"],
                             "region": next(item["box"] for item in self.elements
                                            if item["page_id"] == slide_id and item["block_id"] == block["block_id"])}
                    if kind == "reference":
                        entry.update(target_page_id=None, reference_id=target,
                                     url=self.references[target]["url"])
                    else:
                        entry["target_page_id"] = target if kind == "slide" else f"sys:support:{slide_id}:{target}:1"
                    self.navigation.append(entry)
            self.pages.append({"page_id": slide_id, "kind": "slide", "slide_id": slide_id, "entries": [],
                               "blocks": [block["block_id"] for block in slide["blocks"]],
                               "notes_source": slide_id, "notes": slide["presenter_notes"],
                               "title": slide_titles[slide_id]})

        supports = {support["support_id"]: support for support in deck.get("supports", [])}
        for origin, support_id in facts["materialisations"]:
            support = supports[support_id]
            areas = self.areas(support["layout"])
            probe = Planner(self.document, self.profile)
            grouped = probe.flow("probe", support["blocks"], areas, paginate=True)
            total = len(grouped)
            for number, blocks in enumerate(grouped, 1):
                page_id = f"sys:support:{origin}:{support_id}:{number}"
                self.title(page_id, support["title"], areas["title"])
                self.flow(page_id, blocks, areas, paginate=False)
                self.chrome(page_id, areas["controls"], [
                    (NAV_PREVIOUS, self.labels["previous"],
                     f"sys:support:{origin}:{support_id}:{number - 1}" if number > 1 else None),
                    (NAV_NEXT, self.labels["next"],
                     f"sys:support:{origin}:{support_id}:{number + 1}" if number < total else None),
                    (NAV_BACK, self.labels["back"].format(title=slide_titles[origin]), origin),
                ])
                self.pages.append({"page_id": page_id, "kind": "support", "number": number, "origin": origin,
                                   "support_id": support_id, "entries": [],
                                   "blocks": [block["block_id"] for block in blocks],
                                   "notes_source": support_id, "notes": support["presenter_notes"],
                                   "title": plain(support["title"])})

        return {
            "schema_version": 1, "profile": self.profile, "deck_id": deck["deck_id"],
            "language": deck["language"], "deck_sha256": self.document["sha256"],
            "theme_sha256": self.theme["sha256"], "theme_ref": deck["theme_ref"],
            "size_pt": {"width": self.stage[0], "height": self.stage[1]},
            "fonts": self.fonts.descriptors(), "pages": self.pages,
            "elements": self.elements, "navigation": self.navigation,
        }


def plan(document: dict[str, Any], profile: str) -> dict[str, Any]:
    """Return the LayoutPlan consumed unchanged by all three exporters."""
    return Planner(document, profile).run()
