"""Rehearse the delivered decks in the installed PowerPoint, on a copy.

Microsoft does not support Office automation in a non-interactive service, so
this module only runs on an interactive Windows session and refuses to continue
otherwise.  It never touches the delivered files: every rehearsal works on a
copy, and the network isolation of the station is evidence the operator supplies.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from scripts.checks.common import InputError, parse_strict_json

PP_SAVE_AS_PNG = 18
MS_TRUE = -1


class Unavailable(InputError):
    """Raised when the Office rehearsal cannot run on this machine."""


@contextmanager
def application():
    if sys.platform != "win32":
        raise Unavailable("the PowerPoint rehearsal requires Windows")
    try:
        import pythoncom  # type: ignore
        import win32com.client  # type: ignore
    except ModuleNotFoundError as exc:
        raise Unavailable("pywin32 is unavailable; prepare the optional presentation environment") from exc
    pythoncom.CoInitialize()
    try:
        instance = win32com.client.DispatchEx("PowerPoint.Application")
    except Exception as exc:
        raise Unavailable(f"PowerPoint could not be started: {exc}") from exc
    owned = []
    try:
        yield instance, owned
    finally:
        for presentation in owned:
            try:
                presentation.Close()
            except Exception:
                pass
        try:
            # Only this instance is closed; a PowerPoint opened by the operator is untouched.
            instance.Quit()
        except Exception:
            pass
        pythoncom.CoUninitialize()


def isolation_evidence(path: Path | None) -> dict[str, Any]:
    """Read the operator's statement that the station had no outbound network."""
    if path is None or not path.is_file():
        return {"status": "not_evaluated",
                "reason": "no network isolation evidence was supplied for the rehearsal window"}
    data = parse_strict_json(path.read_text(encoding="utf-8"), name="isolation evidence")
    required = ("schema_version", "responsible", "window_start", "window_end", "control", "verified_blocked")
    if not isinstance(data, dict) or any(key not in data for key in required):
        raise InputError("the isolation evidence must declare responsible, window, control and verification")
    if data.get("schema_version") != 1 or data.get("verified_blocked") is not True:
        return {"status": "fail", "reason": "the isolation control was not verified as blocking", **data}
    return {"status": "pass", **data}


def export_slides(instance, source: Path, destination: Path, width: int, height: int) -> list[dict[str, Any]]:
    """Export each slide through PowerPoint and measure the produced image."""
    from scripts.presentations.capture import png_size

    destination.mkdir(parents=True, exist_ok=True)
    # COM resolves nothing for us: Office needs an absolute path.
    presentation = instance.Presentations.Open(str(source.resolve()), WithWindow=False, ReadOnly=True)
    frames = []
    try:
        for index in range(1, presentation.Slides.Count + 1):
            target = destination / f"office-{index:03d}.png"
            presentation.Slides(index).Export(str(target.resolve()), "PNG", width, height)
            if not target.is_file():
                raise InputError(f"PowerPoint did not write the export of slide {index}")
            frames.append({"slide": index, "path": target, "pixels": list(png_size(target))})
    finally:
        presentation.Close()
    return frames


def read_actions(instance, source: Path) -> list[dict[str, Any]]:
    """Read the click actions PowerPoint itself reports for every shape."""
    presentation = instance.Presentations.Open(str(source.resolve()), WithWindow=False, ReadOnly=True)
    actions = []
    try:
        for index in range(1, presentation.Slides.Count + 1):
            slide = presentation.Slides(index)
            for shape in slide.Shapes:
                setting = shape.ActionSettings(1)
                record = {"slide": index, "name": str(shape.Name), "action": int(setting.Action)}
                if record["action"] == 7:  # ppActionHyperlink
                    link = setting.Hyperlink
                    record["sub_address"] = str(link.SubAddress)
                    record["address"] = str(link.Address)
                try:
                    record["fill_visible"] = bool(shape.Fill.Visible == MS_TRUE)
                    record["fill_transparency"] = round(float(shape.Fill.Transparency), 4)
                except Exception:
                    record["fill_visible"] = None
                actions.append(record)
            actions.append({"slide": index, "hidden": bool(slide.SlideShowTransition.Hidden == MS_TRUE)})
    finally:
        presentation.Close()
    return actions


def rehearse_editing(instance, source: Path, workspace: Path) -> dict[str, Any]:
    """Change text, a table cell and a shape position, then reopen and verify."""
    copy = workspace / "rehearsal-copy.pptx"
    shutil.copy2(source, copy)
    marker = "VERIFICACAO-DE-EDICAO"
    observations: dict[str, Any] = {"marker": marker}
    presentation = instance.Presentations.Open(str(copy.resolve()), WithWindow=False)
    try:
        edited_text = edited_cell = moved = None
        for index in range(1, presentation.Slides.Count + 1):
            slide = presentation.Slides(index)
            for shape in slide.Shapes:
                if edited_text is None and shape.HasTextFrame and shape.TextFrame.HasText:
                    shape.TextFrame.TextRange.Text = marker
                    edited_text = (index, str(shape.Name))
                if edited_cell is None and shape.HasTable:
                    shape.Table.Cell(1, 1).Shape.TextFrame.TextRange.Text = marker
                    edited_cell = (index, str(shape.Name))
                if moved is None and shape.Connector == MS_TRUE:
                    begin = shape.ConnectorFormat.BeginConnectedShape
                    begin.Left = float(begin.Left) + 24
                    moved = (index, str(begin.Name), float(begin.Left))
            if edited_text and edited_cell and moved:
                break
        observations.update(edited_text=edited_text, edited_cell=edited_cell, moved=moved)
        presentation.Save()
    finally:
        presentation.Close()
    reopened = instance.Presentations.Open(str(copy.resolve()), WithWindow=False, ReadOnly=True)
    try:
        found_text = found_cell = False
        connector_attached = None
        position = None
        for index in range(1, reopened.Slides.Count + 1):
            slide = reopened.Slides(index)
            for shape in slide.Shapes:
                if shape.HasTextFrame and shape.TextFrame.HasText and marker in str(shape.TextFrame.TextRange.Text):
                    found_text = True
                if shape.HasTable and marker in str(shape.Table.Cell(1, 1).Shape.TextFrame.TextRange.Text):
                    found_cell = True
                if not observations["moved"]:
                    continue
                target_slide, target_name, _ = observations["moved"]
                if index == target_slide and str(shape.Name) == target_name:
                    position = float(shape.Left)
                if index == target_slide and shape.Connector == MS_TRUE:
                    attached = str(shape.ConnectorFormat.BeginConnectedShape.Name) == target_name
                    connector_attached = attached or bool(connector_attached)
        observations.update(text_persisted=found_text, cell_persisted=found_cell,
                            connector_attached=connector_attached, moved_position=position,
                            move_persisted=position is not None
                            and abs(position - observations["moved"][2]) < 0.5)
    finally:
        reopened.Close()
    return observations


def rehearse(editable: Path, faithful: Path, layout: dict[str, Any], workspace: Path,
             *, isolation: Path | None = None) -> dict[str, Any]:
    """Run the whole Office rehearsal and report measured observations."""
    stage = layout["size_pt"]
    width, height = int(round(stage["width"] * 96 / 72)), int(round(stage["height"] * 96 / 72))
    workspace.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {"isolation": isolation_evidence(isolation), "findings": []}
    with application() as (instance, _):
        for name, source in (("editable", editable), ("faithful", faithful)):
            copy = workspace / f"{name}.pptx"
            shutil.copy2(source, copy)
            frames = export_slides(instance, copy, workspace / f"{name}-frames", width, height)
            if len(frames) != len(layout["pages"]):
                report["findings"].append({"code": "office_slide_count", "deck": name, "observed": len(frames)})
            for frame in frames:
                if frame["pixels"] != [width, height]:
                    report["findings"].append({"code": "office_export_size", "deck": name,
                                               "slide": frame["slide"], "observed": frame["pixels"]})
            report[f"{name}_frames"] = [{"slide": item["slide"], "path": str(item["path"]),
                                         "pixels": item["pixels"]} for item in frames]
            report[f"{name}_actions"] = read_actions(instance, copy)
        report["editing"] = rehearse_editing(instance, workspace / "editable.pptx", workspace)
    editing = report["editing"]
    for key in ("text_persisted", "cell_persisted", "connector_attached", "move_persisted"):
        if not editing.get(key):
            report["findings"].append({"code": "edit_not_persisted", "observation": key})
    hidden = {item["slide"]: item["hidden"] for item in report["faithful_actions"] if "hidden" in item}
    for index, page in enumerate(layout["pages"], 1):
        if hidden.get(index) != (page["kind"] == "support"):
            report["findings"].append({"code": "office_visibility", "page": page["page_id"],
                                       "observed": hidden.get(index)})
    clickable = [item for item in report["faithful_actions"]
                 if item.get("action") == 7 and item.get("fill_visible") is False]
    if clickable:
        report["findings"].append({"code": "hotspot_without_fill", "count": len(clickable)})
    return report
