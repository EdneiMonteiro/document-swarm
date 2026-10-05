"""Drive the offline package in a pinned Chromium to capture frames and test behaviour.

The browser runs in an isolated context with no personal profile, no extensions
and no network: anything other than the local package is aborted.  Captures are
measured from the produced PNG, never from the size requested of the browser.
"""

from __future__ import annotations

import os
import struct
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from scripts.checks.common import InputError

NAV_INDEX = "sys:nav:index"

BROWSERS = Path(__file__).resolve().parents[2] / ".venv-presentations" / "browsers"
SCALE = 2
PREPARE = """
() => {
  document.documentElement.style.height = 'auto';
  document.body.style.height = 'auto';
  document.body.style.overflow = 'visible';
  document.body.style.background = '#ffffff';
  const viewport = document.querySelector('.viewport');
  if (viewport) {
    viewport.style.position = 'static';
    viewport.style.inset = 'auto';
    viewport.style.display = 'block';
    viewport.style.overflow = 'visible';
  }
  document.querySelectorAll('.scaler').forEach(node => {
    node.style.transform = 'none';
    node.style.position = 'static';
  });
  document.querySelectorAll('.frame').forEach(node => {
    node.style.width = 'auto';
    node.style.height = 'auto';
  });
  document.querySelectorAll('.app-bar, .notes').forEach(node => { node.style.display = 'none'; });
  document.querySelectorAll('dialog.support').forEach(node => {
    node.style.position = 'static';
    node.style.margin = '0';
    node.style.background = 'transparent';
  });
  document.querySelectorAll('.page').forEach(node => { node.style.boxShadow = 'none'; });
  return true;
}
"""
CLOSE_ALL = """
() => {
  document.querySelectorAll('dialog.support').forEach(node => { if (node.open) { node.close(); } });
  return true;
}
"""
SHOW = """
(options) => {
  const page = document.querySelector(`[data-page-id="${CSS.escape(options.page_id)}"]`);
  if (!page) { return null; }
  const dialog = page.closest('dialog');
  if (dialog && !dialog.open) { dialog.show(); }
  page.parentElement.querySelectorAll('.page').forEach(node => { node.hidden = node !== page; });
  page.scrollIntoView({ block: 'start' });
  return true;
}
"""


NORMALISE = """
() => {
  // The dialog close event is queued, so the runtime refits after SHOW returns.
  document.querySelectorAll('.scaler').forEach(node => {
    node.style.transform = 'none';
    node.style.position = 'static';
  });
  document.querySelectorAll('.frame').forEach(node => {
    node.style.width = 'auto';
    node.style.height = 'auto';
  });
  return true;
}
"""


def png_size(path: Path) -> tuple[int, int]:
    head = path.read_bytes()[:24]
    if head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
        raise InputError(f"{path.name} is not a valid PNG capture")
    return struct.unpack_from(">II", head, 16)


@contextmanager
def browser(*, headless: bool = True):
    """Open an isolated Chromium bound to the provisioned browser directory."""
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(BROWSERS))
    try:
        from playwright.sync_api import sync_playwright
    except ModuleNotFoundError as exc:  # pragma: no cover - surfaced by the CLI
        raise InputError("Playwright is unavailable; prepare the optional presentation environment") from exc
    with sync_playwright() as driver:
        instance = driver.chromium.launch(headless=headless, args=["--disable-extensions", "--no-default-browser-check"])
        try:
            yield driver, instance
        finally:
            instance.close()


@contextmanager
def opened(instance, package: Path, *, width: int, height: int):
    context = instance.new_context(viewport={"width": width, "height": height}, device_scale_factor=SCALE,
                                   offline=True, reduced_motion="reduce", java_script_enabled=True)
    blocked: list[str] = []

    def guard(route, request):
        if request.url.startswith("file:"):
            route.continue_()
        else:
            blocked.append(request.url)
            route.abort()

    context.route("**/*", guard)
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
    page.goto(package.resolve().as_uri(), wait_until="load")
    page.wait_for_function("() => document.documentElement.dataset.runtimeReady === '1'", timeout=15000)
    page.evaluate("() => document.fonts.ready")
    try:
        yield page, errors, blocked
    finally:
        context.close()


FLUSH = "() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))"


def settle(page, page_id: str, *, normalise: bool) -> bool:
    """Close any dialog, let its queued close event run, then assert the page."""
    page.evaluate(CLOSE_ALL)
    page.evaluate(FLUSH)
    if page.evaluate(SHOW, {"page_id": page_id}) is None:
        return False
    page.wait_for_function(
        "(id) => { const node = document.querySelector(`[data-page-id=\"${CSS.escape(id)}\"]`);"
        " return node && !node.hidden; }", arg=page_id, timeout=5000)
    if normalise:
        page.evaluate(NORMALISE)
    return True


def tap(page, page_id: str, locator, *, attempts: int = 4) -> None:
    """Click a control, re-settling the page because the runtime restores state asynchronously."""
    last: Exception | None = None
    for _ in range(attempts):
        settle(page, page_id, normalise=False)
        try:
            locator.click(timeout=4000)
            return
        except Exception as exc:  # Playwright timeout; retried with a fresh settle.
            last = exc
    raise InputError(f"the control on {page_id} never became operable: {last}")


def capture(package: Path, layout: dict[str, Any], destination: Path) -> list[dict[str, Any]]:
    """Render every logical page to PNG and report the measured image size."""
    stage = layout["size_pt"]
    width, height = int(round(stage["width"] * 96 / 72)), int(round(stage["height"] * 96 / 72))
    destination.mkdir(parents=True, exist_ok=True)
    frames = []
    with browser() as (_, instance), opened(instance, package, width=width + 160, height=height + 160) as (page, errors, blocked):
        page.evaluate(PREPARE)
        for index, item in enumerate(layout["pages"], 1):
            if not settle(page, item["page_id"], normalise=True):
                raise InputError(f"the HTML package has no page {item['page_id']}")
            target = destination / f"frame-{index:03d}.png"
            page.locator(f'[data-page-id="{item["page_id"]}"]').first.screenshot(path=str(target), scale="device")
            measured = png_size(target)
            if measured != (width * SCALE, height * SCALE):
                raise InputError(
                    f"the capture of {item['page_id']} measured {measured[0]}x{measured[1]} pixels "
                    f"instead of {width * SCALE}x{height * SCALE}")
            frames.append({"page_id": item["page_id"], "path": target, "pixels": list(measured)})
        if errors:
            raise InputError(f"the offline package reported runtime errors: {errors[:3]}")
        if blocked:
            raise InputError(f"the offline package attempted non-local requests: {blocked[:3]}")
    return frames


def exercise(package: Path, layout: dict[str, Any]) -> dict[str, Any]:
    """Operate the real interface: navigation, dialogs, keyboard and focus return."""
    stage = layout["size_pt"]
    width, height = int(round(stage["width"] * 96 / 72)), int(round(stage["height"] * 96 / 72))
    findings: list[dict[str, Any]] = []
    observations: dict[str, Any] = {}
    expected = {(item["page_id"], item["action_id"]): item for item in layout["navigation"]}
    with browser() as (_, instance), opened(instance, package, width=width, height=height) as (page, errors, blocked):
        def visible() -> str | None:
            return page.evaluate(
                "() => { const open = document.querySelector('dialog.support[open]');"
                " const scope = open || document.querySelector('.viewport');"
                " const node = Array.from(scope.querySelectorAll('.page')).find(item => !item.hidden);"
                " return node ? node.dataset.pageId : null; }")

        def show(page_id: str) -> None:
            settle(page, page_id, normalise=False)

        for (page_id, action_id), record in sorted(expected.items()):
            show(page_id)
            locator = page.locator(f'[data-page-id="{page_id}"] [data-action-id="{action_id}"]').first
            if locator.count() == 0:
                findings.append({"code": "missing_control", "page": page_id, "action": action_id})
                continue
            name = locator.evaluate("node => node.textContent.trim()")
            if name != record["name"]:
                findings.append({"code": "control_name", "page": page_id, "action": action_id, "observed": name})
            disabled = locator.evaluate("node => node.disabled === true")
            if disabled == bool(record["enabled"]):
                findings.append({"code": "control_state", "page": page_id, "action": action_id,
                                 "enabled_expected": record["enabled"]})
                continue
            if record.get("reference_id"):
                href = locator.get_attribute("href")
                if href != record["url"] or locator.get_attribute("target") != "_blank":
                    findings.append({"code": "external_link", "page": page_id, "action": action_id, "observed": href})
                continue
            if not record["enabled"]:
                if locator.get_attribute("data-target") is not None:
                    findings.append({"code": "disabled_has_target", "page": page_id, "action": action_id})
                continue
            show(page_id)
            tap(page, page_id, locator)
            reached = visible()
            if reached != record["target_page_id"]:
                findings.append({"code": "wrong_destination", "page": page_id, "action": action_id,
                                 "expected": record["target_page_id"], "observed": reached})

        # Focus management and keyboard behaviour on a real support dialog.
        support = next((item for item in layout["navigation"]
                        if (item.get("target_page_id") or "").startswith("sys:support:")), None)
        if support is not None:
            show(support["page_id"])
            trigger = page.locator(f'[data-page-id="{support["page_id"]}"] [data-action-id="{support["action_id"]}"]').first
            settle(page, support["page_id"], normalise=False)
            trigger.focus()
            tap(page, support["page_id"], trigger)
            observations["dialog_modal"] = page.evaluate(
                "() => { const d = document.querySelector('dialog.support[open]');"
                " return d ? d.matches(':modal') : false; }")
            observations["focus_inside_dialog"] = page.evaluate(
                "() => { const d = document.querySelector('dialog.support[open]');"
                " return !!d && d.contains(document.activeElement); }")
            before = visible()
            page.keyboard.press("ArrowRight")
            observations["dialog_paginates"] = visible() != before
            page.keyboard.press("Escape")
            observations["escape_closes"] = page.evaluate(
                "() => document.querySelector('dialog.support[open]') === null")
            observations["focus_returned"] = page.evaluate(
                "(id) => document.activeElement && document.activeElement.dataset.actionId === id",
                support["action_id"])
            if not all(observations.get(key) for key in
                       ("dialog_modal", "focus_inside_dialog", "dialog_paginates", "escape_closes", "focus_returned")):
                findings.append({"code": "dialog_behaviour", "observations": observations})

        sequence = [item["page_id"] for item in layout["pages"] if item["kind"] in ("index", "slide")]
        anchor = next((item["page_id"] for item in layout["pages"] if item["kind"] == "slide"), sequence[0])
        show(anchor)
        index_control = page.locator(f'[data-page-id="{anchor}"] [data-action-id="{NAV_INDEX}"]').first
        if index_control.count():
            # Reach the index through the interface so the runtime owns the position.
            tap(page, anchor, index_control)
        start = visible()
        page.keyboard.press("ArrowRight")
        observations["keyboard_from"] = start
        observations["keyboard_advance"] = visible()
        position = sequence.index(start) if start in sequence else 0
        wanted = sequence[min(position + 1, len(sequence) - 1)]
        if observations["keyboard_advance"] != wanted:
            findings.append({"code": "keyboard_sequence", "from": start,
                             "expected": wanted, "observed": observations["keyboard_advance"]})
        observations["runtime_errors"] = errors
        observations["blocked_requests"] = blocked
        if errors or blocked:
            findings.append({"code": "non_local_or_failing_runtime", "errors": errors[:3], "requests": blocked[:3]})
    return {"findings": findings, "observations": observations}
