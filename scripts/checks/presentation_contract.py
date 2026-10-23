"""Stdlib-only verification of a recorded presentation delivery at gate time.

The reader never imports a browser, Office or a PPTX library.  It reconstructs
the expected pages, navigation and review domain from the authored deck and then
confronts that reconstruction with the recorded manifest, inspections and
reviews.  A manifest that repeats an exporter's omission therefore cannot shrink
what must be covered.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from scripts.checks.common import GRADE_INDEX, InputError, normalize_grade, parse_strict_json

CAPABILITY = "presentation-v1"
FORMATS = ("html-offline", "pptx-faithful", "pptx-editable")
FORMAT_FILES = {
    "html-offline": "index.html",
    "pptx-faithful": "deck-faithful.pptx",
    "pptx-editable": "deck-editable.pptx",
}
TOPIC_DIMENSIONS = ("factual", "decision")
PAGE_DIMENSIONS = ("legibility", "interaction")
REQUIRED_CHECKS = {
    "implementation": ("compatibility", "adversarial-detectors", "lifecycle"),
    "profile": ("component-capability", "visual-font-calibration", "interactive-isolation"),
    "candidate": ("input-policy", "inventory-content", "html-offline", "navigation",
                  "visual", "native-structure", "edit-save-reopen"),
}
KNOWN_STATES = frozenset({"pass", "fail", "pending", "not_evaluated", "unsupported", "stale"})
ROLES = frozenset({"model", "plan", "theme", "format", "runtime", "asset", "font", "license", "readme"})
NAV_PREVIOUS, NAV_NEXT, NAV_INDEX, NAV_BACK = "sys:nav:previous", "sys:nav:next", "sys:nav:index", "sys:nav:back"
MAX_JSON_BYTES = 16 * 1024 * 1024
MAX_BINARY_BYTES = 256 * 1024 * 1024
MAX_FILES = 2000
ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
TOPIC_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")


def logical(name: Any, field: str) -> PurePosixPath:
    """Return a contained relative path, rejecting absolute or escaping names."""
    if not isinstance(name, str) or not name:
        raise InputError(f"{field}: a relative presentation path is required")
    text = name.replace("\\", "/")
    path = PurePosixPath(text)
    if (path.is_absolute() or PureWindowsPath(text).drive or any(ord(ch) < 32 for ch in text)
            or any(part in ("", ".", "..") for part in path.parts) or not path.parts):
        raise InputError(f"{field}: unsafe presentation path {name!r}")
    return path


def mapping(data: Any, field: str) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise InputError(f"{field} must be an object")
    return data


def listing(data: Any, field: str, *, minimum: int = 0, maximum: int = MAX_FILES) -> list[Any]:
    if not isinstance(data, list) or not minimum <= len(data) <= maximum:
        raise InputError(f"{field} must be a list with {minimum} to {maximum} entries")
    return data


def optional_list(data: Any, field: str, *, maximum: int = MAX_FILES) -> list[Any]:
    """Treat an absent optional collection as empty, but reject a wrong type."""
    return listing([] if data is None else data, field, maximum=maximum)


def identifier(value: Any, field: str, pattern: re.Pattern[str] = ID) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise InputError(f"{field}: {value!r} is not a supported identifier")
    return value


def text(value: Any, field: str, *, limit: int = 4000, empty: bool = False) -> str:
    if not isinstance(value, str) or len(value) > limit or (not empty and not value.strip()):
        raise InputError(f"{field} must be bounded {'optional ' if empty else ''}text")
    return value


class Artifacts:
    """Resolve and hash-check files inside one swarm, caching verified bytes."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve(strict=True)
        self._cache: dict[Path, bytes] = {}

    def resolve(self, name: Any, field: str) -> Path:
        path = self.root.joinpath(*logical(name, field).parts)
        if path.is_symlink():
            raise InputError(f"{field}: presentation artifacts must not be links")
        try:
            resolved = path.resolve(strict=True)
        except OSError as exc:
            raise InputError(f"{field}: artifact is unavailable: {name}") from exc
        if self.root not in resolved.parents or not resolved.is_file():
            raise InputError(f"{field}: artifact escapes the swarm or is not a file: {name}")
        return resolved

    def read(self, descriptor: Any, field: str, *, limit: int = MAX_JSON_BYTES,
             allowed: frozenset[str] = frozenset({"path", "sha256"})) -> bytes:
        item = mapping(descriptor, field)
        unexpected = set(item) - allowed
        if unexpected:
            raise InputError(f"{field}: unsupported reference fields {sorted(unexpected)}")
        digest = item.get("sha256")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise InputError(f"{field}: requires a lowercase SHA-256")
        file = self.resolve(item.get("path"), field)
        before = file.stat()
        if before.st_size > limit:
            raise InputError(f"{field}: artifact exceeds its size limit: {item['path']}")
        data = self._cache.get(file)
        if data is None:
            data = file.read_bytes()
            after = file.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise InputError(f"{field}: artifact changed during verification: {item['path']}")
            self._cache[file] = data
        if hashlib.sha256(data).hexdigest() != digest:
            raise InputError(f"presentation evidence is stale; artifact changed: {item['path']}")
        if "bytes" in item and item["bytes"] != len(data):
            raise InputError(f"{field}: recorded byte count does not match the file")
        return data

    def read_json(self, descriptor: Any, field: str) -> dict[str, Any]:
        return mapping(parse_strict_json(self.read(descriptor, field).decode("utf-8"), name=field), field)


def deck_structure(deck: dict[str, Any]) -> dict[str, Any]:
    """Reconstruct the authored pages, blocks and actions the delivery must contain."""
    if deck.get("schema_version") != 1:
        raise InputError("unsupported DeckSpec schema version")
    identifier(deck.get("deck_id"), "deck_id")
    slides, supports, blocks, controls = [], [], {}, {}
    for position, slide in enumerate(listing(deck.get("slides"), "deck slides", minimum=1, maximum=300)):
        item = mapping(slide, "deck slide")
        slide_id = identifier(item.get("slide_id"), "slide_id")
        if any(slide_id == known for known, _ in slides):
            raise InputError(f"duplicate slide identifier {slide_id}")
        topics = listing(item.get("topic_ids"), f"slide {slide_id} topic_ids", minimum=1, maximum=40)
        owned = []
        for block in optional_list(item.get("blocks"), f"slide {slide_id} blocks", maximum=40):
            entry = mapping(block, "deck block")
            block_id = identifier(entry.get("block_id"), "block_id")
            if block_id in blocks:
                raise InputError(f"duplicate block identifier {block_id}")
            blocks[block_id] = slide_id
            owned.append(block_id)
            if entry.get("type") == "control":
                controls[block_id] = slide_id
        slides.append((slide_id, {"topics": [identifier(t, "topic_id", TOPIC_ID) for t in topics],
                                  "blocks": owned, "order": position}))
    for support in optional_list(deck.get("supports"), "deck supports", maximum=300):
        item = mapping(support, "deck support")
        support_id = identifier(item.get("support_id"), "support_id")
        if any(support_id == known for known, _ in supports):
            raise InputError(f"duplicate support identifier {support_id}")
        owned = []
        for block in optional_list(item.get("blocks"), f"support {support_id} blocks", maximum=40):
            entry = mapping(block, "deck block")
            block_id = identifier(entry.get("block_id"), "block_id")
            if block_id in blocks:
                raise InputError(f"duplicate block identifier {block_id}")
            blocks[block_id] = support_id
            owned.append(block_id)
            if entry.get("type") == "control":
                raise InputError(f"support {support_id} may not carry authored controls")
        supports.append((support_id, {"blocks": owned}))

    slide_ids = [slide_id for slide_id, _ in slides]
    support_ids = [support_id for support_id, _ in supports]
    references = set()
    for reference in optional_list(deck.get("references"), "deck references", maximum=400):
        references.add(identifier(mapping(reference, "reference").get("reference_id"), "reference_id"))
    actions, materialisations = {}, []
    for action in optional_list(deck.get("actions"), "deck actions", maximum=400):
        item = mapping(action, "deck action")
        action_id = identifier(item.get("action_id"), "action_id")
        if action_id in actions:
            raise InputError(f"duplicate action identifier {action_id}")
        trigger = identifier(item.get("trigger_block_id"), "trigger_block_id")
        if trigger not in controls:
            raise InputError(f"action {action_id} must be triggered by a control block of a main slide")
        origin = controls[trigger]
        kind = item.get("kind")
        if kind == "goto_slide":
            target = ("slide", identifier(item.get("slide_id"), "slide_id"))
            if target[1] not in slide_ids:
                raise InputError(f"action {action_id} targets an unknown slide")
        elif kind == "open_support":
            target = ("support", identifier(item.get("support_id"), "support_id"))
            if target[1] not in support_ids:
                raise InputError(f"action {action_id} targets an unknown support")
            if (origin, target[1]) not in materialisations:
                materialisations.append((origin, target[1]))
        elif kind == "open_external":
            target = ("reference", identifier(item.get("reference_id"), "reference_id"))
            if target[1] not in references:
                raise InputError(f"action {action_id} targets an unregistered reference")
        else:
            raise InputError(f"action {action_id} has an unsupported kind")
        actions[action_id] = {"origin": origin, "trigger": trigger, "kind": kind, "target": target}
    unreachable = [support_id for support_id in support_ids
                   if not any(support_id == support for _, support in materialisations)]
    if unreachable:
        raise InputError(f"supports are never opened by an authored action: {sorted(unreachable)}")
    order = {slide_id: index for index, slide_id in enumerate(slide_ids)}
    materialisations.sort(key=lambda pair: (order[pair[0]], support_ids.index(pair[1])))
    return {
        "slides": dict(slides), "slide_ids": slide_ids, "supports": dict(supports),
        "support_ids": support_ids, "actions": actions, "materialisations": materialisations,
        "topics": sorted({topic for _, slide in slides for topic in slide["topics"]}),
    }


def expected_pages(facts: dict[str, Any], layout: dict[str, Any]) -> list[dict[str, Any]]:
    """Validate the planned pages against the authored structure and numbering rules."""
    pages = listing(layout.get("pages"), "layout pages", minimum=1, maximum=2000)
    declared = []
    for page in pages:
        item = mapping(page, "layout page")
        declared.append((text(item.get("page_id"), "page_id", limit=200), item))
    index_pages = [item for page_id, item in declared if page_id.startswith("sys:index:")]
    if not index_pages:
        raise InputError("the deck requires at least one index page")
    entries: list[str] = []
    for number, item in enumerate(index_pages, 1):
        if item.get("page_id") != f"sys:index:{number}":
            raise InputError("index pages must be numbered contiguously from 1")
        if item.get("kind") != "index" or optional_list(item.get("blocks"), "index blocks", maximum=0):
            raise InputError("an index page carries generated entries, not authored blocks")
        entries.extend(identifier(entry, "index entry") for entry in optional_list(item.get("entries"), "index entries"))
    if entries != facts["slide_ids"]:
        raise InputError("the index must list every main slide exactly once, in authored order")

    expected: list[dict[str, Any]] = [
        {"page_id": f"sys:index:{number}", "kind": "index", "blocks": [], "notes_source": None,
         "entries": [identifier(entry, "index entry") for entry in item["entries"]]}
        for number, item in enumerate(index_pages, 1)
    ]
    for slide_id in facts["slide_ids"]:
        expected.append({"page_id": slide_id, "kind": "slide", "blocks": facts["slides"][slide_id]["blocks"],
                         "notes_source": slide_id, "entries": []})
    for origin, support_id in facts["materialisations"]:
        prefix = f"sys:support:{origin}:{support_id}:"
        materialised = [item for page_id, item in declared if page_id.startswith(prefix)]
        if not materialised:
            raise InputError(f"support {support_id} opened from {origin} has no materialised page")
        carried: list[str] = []
        for number, item in enumerate(materialised, 1):
            if item.get("page_id") != f"{prefix}{number}":
                raise InputError(f"support pages of {prefix} must be numbered contiguously from 1")
            if item.get("kind") != "support":
                raise InputError(f"{item.get('page_id')} must be recorded as a support page")
            page_blocks = [identifier(block, "support block") for block in optional_list(item.get("blocks"), "support blocks", maximum=40)]
            carried.extend(page_blocks)
            expected.append({"page_id": item["page_id"], "kind": "support", "blocks": page_blocks,
                             "notes_source": support_id, "entries": []})
        if carried != facts["supports"][support_id]["blocks"]:
            raise InputError(f"support {support_id} opened from {origin} must paginate all of its blocks exactly once")
    recorded = [page_id for page_id, _ in declared]
    if recorded != [page["page_id"] for page in expected]:
        raise InputError("the planned page inventory differs from the authored deck and its pagination rules")
    for page_id, item in declared:
        if item.get("kind") == "slide":
            if [identifier(block, "slide block") for block in optional_list(item.get("blocks"), "slide blocks", maximum=40)] \
                    != facts["slides"][page_id]["blocks"]:
                raise InputError(f"slide {page_id} must carry its authored blocks exactly once")
            if item.get("notes_source") != page_id:
                raise InputError(f"slide {page_id} must carry its own presenter notes")
        if item.get("kind") == "support" and item.get("notes_source") != page_id.split(":")[3]:
            raise InputError(f"{page_id} must repeat the notes of its support")
        if item.get("kind") == "index" and item.get("notes_source") is not None:
            raise InputError("index pages have empty presenter notes")
    return expected


def expected_navigation(facts: dict[str, Any], pages: list[dict[str, Any]]) -> dict[tuple[str, str], str | None]:
    """Rebuild every control the delivery must offer, including disabled extremes."""
    sequence = [page["page_id"] for page in pages if page["kind"] in ("index", "slide")]
    graph: dict[tuple[str, str], str | None] = {}
    for position, page_id in enumerate(sequence):
        graph[(page_id, NAV_PREVIOUS)] = sequence[position - 1] if position else None
        graph[(page_id, NAV_NEXT)] = sequence[position + 1] if position + 1 < len(sequence) else None
    for page in pages:
        if page["kind"] == "index":
            for entry in page["entries"]:
                graph[(page["page_id"], f"sys:index:entry:{entry}")] = entry
        elif page["kind"] == "slide":
            graph[(page["page_id"], NAV_INDEX)] = "sys:index:1"
    for origin, support_id in facts["materialisations"]:
        prefix = f"sys:support:{origin}:{support_id}:"
        materialised = [page["page_id"] for page in pages if page["page_id"].startswith(prefix)]
        for position, page_id in enumerate(materialised):
            graph[(page_id, NAV_PREVIOUS)] = materialised[position - 1] if position else None
            graph[(page_id, NAV_NEXT)] = materialised[position + 1] if position + 1 < len(materialised) else None
            graph[(page_id, NAV_BACK)] = origin
    for action_id, action in facts["actions"].items():
        kind, target = action["target"]
        if kind == "support":
            target = f"sys:support:{action['origin']}:{target}:1"
        graph[(action["origin"], action_id)] = target if kind != "reference" else f"reference:{target}"
    return graph


def verify_layout_navigation(layout: dict[str, Any], graph: dict[tuple[str, str], str | None]) -> None:
    recorded: dict[tuple[str, str], str | None] = {}
    for control in listing(layout.get("navigation"), "layout navigation", maximum=8000):
        item = mapping(control, "navigation control")
        key = (text(item.get("page_id"), "page_id", limit=200), text(item.get("action_id"), "action_id", limit=200))
        if key in recorded:
            raise InputError(f"duplicate navigation control {key[1]} on {key[0]}")
        enabled = item.get("enabled")
        if type(enabled) is not bool:
            raise InputError("every navigation control declares whether it is enabled")
        target = item.get("target_page_id")
        reference = item.get("reference_id")
        if reference is not None:
            target = f"reference:{identifier(reference, 'reference_id')}"
        if not enabled and target is not None:
            raise InputError(f"disabled control {key[1]} on {key[0]} must not carry a destination")
        if enabled and target is None:
            raise InputError(f"enabled control {key[1]} on {key[0]} requires a destination")
        text(item.get("name"), f"name of {key[1]}")
        recorded[key] = target
    if recorded != graph:
        missing = sorted(key for key in graph if key not in recorded)
        extra = sorted(key for key in recorded if key not in graph)
        wrong = sorted(key for key in graph if key in recorded and recorded[key] != graph[key])
        raise InputError("planned navigation differs from the reconstructed graph; "
                         f"missing={missing[:4]} unexpected={extra[:4]} mismatched={wrong[:4]}")


def verify_inventory(artifacts: Artifacts, manifest: dict[str, Any], output_root: PurePosixPath) -> dict[str, str]:
    """Confront the declared file list with the real contents of the output root."""
    files = listing(manifest.get("files"), "manifest files", minimum=1)
    declared: dict[str, str] = {}
    for entry in files:
        item = mapping(entry, "manifest file")
        if item.get("role") not in ROLES:
            raise InputError(f"manifest file {item.get('path')!r} has no authorised role")
        path = logical(item.get("path"), "manifest file")
        if path.parts[:len(output_root.parts)] != output_root.parts:
            raise InputError("every delivered file must live inside the presentation output root")
        if path.as_posix() in declared:
            raise InputError(f"duplicate manifest file {path.as_posix()}")
        artifacts.read(item, "manifest file", limit=MAX_BINARY_BYTES,
                       allowed=frozenset({"path", "sha256", "bytes", "role"}))
        declared[path.as_posix()] = item["sha256"]
    root = artifacts.root.joinpath(*output_root.parts)
    if not root.is_dir():
        raise InputError("the presentation output root is missing")
    actual = set()
    for item in sorted(root.rglob("*")):
        if item.is_symlink():
            raise InputError(f"the delivery must not contain links: {item.name}")
        if item.is_file():
            actual.add(PurePosixPath(item.relative_to(artifacts.root).as_posix()).as_posix())
        if len(actual) > MAX_FILES:
            raise InputError("the delivery exceeds the supported file count")
    if actual != set(declared):
        unexpected = sorted(actual - set(declared))
        absent = sorted(set(declared) - actual)
        raise InputError(f"the delivered files differ from the manifest; unexpected={unexpected[:4]} missing={absent[:4]}")
    return declared


def verify_inspections(report: dict[str, Any], subjects: dict[str, str], cycle: int,
                       profile: str) -> list[dict[str, str]]:
    """Require every catalogued check, binding each scope to its own subject."""
    if report.get("schema_version") != 1 or report.get("inspector") != "docswarm-presentation":
        raise InputError("unsupported presentation inspection report")
    if report.get("capability") != CAPABILITY or report.get("profile") != profile:
        raise InputError("the inspection report does not match the declared capability or profile")
    seen: dict[tuple[str, str], dict[str, Any]] = {}
    for entry in listing(report.get("reports"), "inspection reports", minimum=1, maximum=200):
        item = mapping(entry, "inspection report")
        scope = item.get("scope")
        if scope not in REQUIRED_CHECKS:
            raise InputError(f"unsupported inspection scope {scope!r}")
        check = item.get("check_id")
        if check not in REQUIRED_CHECKS[scope]:
            raise InputError(f"unsupported inspection check {check!r} for scope {scope}")
        if (scope, check) in seen:
            raise InputError(f"duplicate inspection report for {scope}/{check}")
        if item.get("subject_sha256") != subjects[scope]:
            raise InputError(f"{scope}/{check} is bound to another subject; re-run it for this delivery")
        if scope == "candidate" and item.get("cycle") != cycle:
            raise InputError(f"candidate inspection {check} belongs to another cycle")
        text(item.get("inspector_version"), f"{scope}/{check} inspector_version", limit=64)
        status = item.get("status")
        if status not in KNOWN_STATES:
            raise InputError(f"{scope}/{check} has an unknown status {status!r}")
        findings = listing(item.get("findings"), f"{scope}/{check} findings", maximum=4000)
        if (status == "pass" and findings) or (status == "fail" and not findings):
            raise InputError(f"{scope}/{check} status contradicts its findings")
        seen[(scope, check)] = item
    blocked = []
    for scope, checks in REQUIRED_CHECKS.items():
        for check in checks:
            item = seen.get((scope, check))
            if item is None:
                raise InputError(f"the mandatory inspection {scope}/{check} was not executed")
            if item["status"] != "pass" and item["status"] != "fail":
                raise InputError(f"{scope}/{check} is recorded as {item['status']}, which never approves a delivery")
            if item["status"] == "fail":
                blocked.append({"kind": "presentation", "name": f"{scope}/{check}", "grade": "",
                                "reviewer": "mechanical inspection"})
    return blocked


def review_domain(topics: list[str], pages: list[dict[str, Any]]) -> set[tuple[str, ...]]:
    page_ids = [page["page_id"] for page in pages]
    domain = {(dimension, topic) for dimension in TOPIC_DIMENSIONS for topic in topics}
    domain |= {(dimension, fmt, page) for dimension in PAGE_DIMENSIONS for fmt in FORMATS for page in page_ids}
    domain |= {("editability", "pptx-editable", page) for page in page_ids}
    return domain


def verify_reviews(artifacts: Artifacts, block: dict[str, Any], cycle: int,
                   domain: set[tuple[str, ...]]) -> list[dict[str, str]]:
    rows = listing(block.get("reviews"), "presentation reviews", minimum=1, maximum=20000)
    covered: dict[tuple[str, ...], dict[str, Any]] = {}
    by_reviewer: dict[str, list[dict[str, Any]]] = {}
    for entry in rows:
        item = mapping(entry, "presentation review")
        unexpected = set(item) - {"reviewer", "dimension", "topic_id", "format", "page_id",
                                  "grade", "justification", "action"}
        if unexpected:
            raise InputError(f"unsupported presentation review fields {sorted(unexpected)}")
        reviewer = text(item.get("reviewer"), "reviewer", limit=120)
        if not re.fullmatch(r"reviewer-[A-Za-z0-9_-]+", reviewer):
            raise InputError("a presentation grade must carry its declared reviewer name")
        dimension = item.get("dimension")
        if dimension in TOPIC_DIMENSIONS:
            key = (dimension, identifier(item.get("topic_id"), "topic_id", TOPIC_ID))
            if "format" in item or "page_id" in item:
                raise InputError(f"{dimension} is graded per topic, not per page")
        elif dimension in PAGE_DIMENSIONS or dimension == "editability":
            if "topic_id" in item:
                raise InputError(f"{dimension} is graded per page, not per topic")
            key = (dimension, text(item.get("format"), "format", limit=40),
                   text(item.get("page_id"), "page_id", limit=200))
        else:
            raise InputError(f"unsupported presentation dimension {dimension!r}")
        if key not in domain:
            raise InputError(f"presentation grade outside the expected domain: {key}")
        grade = normalize_grade(item.get("grade"))
        text(item.get("justification"), f"justification for {key}")
        if not isinstance(item.get("action"), str):
            raise InputError(f"the grade for {key} requires an action string")
        if GRADE_INDEX[grade] < GRADE_INDEX["A"] and not item["action"].strip():
            raise InputError(f"a grade below A for {key} requires an actionable correction")
        if key in covered:
            raise InputError(f"duplicate presentation grade for {key}")
        covered[key] = {**item, "grade": grade}
        by_reviewer.setdefault(reviewer, []).append(item)
    if set(covered) != domain:
        missing = sorted(domain - set(covered))
        raise InputError(f"the presentation review leaves {len(missing)} required positions uncovered: {missing[:4]}")
    graded = {row["dimension"] for row in covered.values()}
    if graded != set(TOPIC_DIMENSIONS) | set(PAGE_DIMENSIONS) | {"editability"}:
        raise InputError("every presentation dimension requires a designated reviewer")
    for reviewer, rows in by_reviewer.items():
        individual = artifacts.read_json(
            {"path": f"reports/cycle-{cycle:02d}-{reviewer}.json",
             "sha256": hashlib.sha256(artifacts.resolve(
                 f"reports/cycle-{cycle:02d}-{reviewer}.json", "reviewer report").read_bytes()).hexdigest()},
            f"review of {reviewer}")
        if individual.get("presentation_reviews") != rows:
            raise InputError(f"the consolidated presentation grades differ from the report of {reviewer}")
    return [{"kind": "presentation", "name": f"{'/'.join(str(part) for part in key)}",
             "grade": row["grade"], "reviewer": row["reviewer"]}
            for key, row in sorted(covered.items()) if GRADE_INDEX[row["grade"]] < GRADE_INDEX["A"]]


def verify_presentation(review: dict[str, Any], swarm: Path, brief: dict[str, Any]) -> list[dict[str, str]]:
    """Return blocking facts, raising InputError when the record cannot be trusted."""
    artifact_type = brief.get("artifact_type", "document")
    if artifact_type not in ("document", "presentation"):
        raise InputError(f"unsupported artifact_type {artifact_type!r}")
    declared = brief.get("presentation")
    recorded = review.get("presentation")
    if (declared is not None) != (artifact_type == "presentation"):
        raise InputError("a presentation brief requires artifact_type: presentation and its presentation block")
    if recorded is not None and artifact_type != "presentation":
        raise InputError("a document brief cannot carry a presentation review")
    if artifact_type != "presentation":
        return []
    if "slides" in review or "deck_dimensions" in review:
        raise InputError("legacy slide fields cannot be combined with the presentation contract")

    settings = mapping(declared, "brief presentation")
    if settings.get("schema_version") != 1 or settings.get("capability") != CAPABILITY:
        raise InputError("unsupported presentation capability; this gate cannot approve it")
    profile = identifier(settings.get("profile"), "presentation profile", re.compile(r"^[a-z][a-z0-9-]{0,63}$"))
    if list(settings.get("required_formats") or []) != list(FORMATS):
        raise InputError(f"a presentation delivers exactly {', '.join(FORMATS)}")
    deck_path = logical(settings.get("deck_path"), "deck_path")
    output_root = deck_path.parent
    if deck_path.name != "deck.json" or not output_root.parts or output_root.parts[0] != "output":
        raise InputError("deck_path must be output/<presentation directory>/deck.json")

    block = mapping(recorded, "review presentation")
    if block.get("schema_version") != 1 or block.get("capability") != CAPABILITY:
        raise InputError("unsupported presentation review contract")
    cycle = review.get("cycle", review.get("ciclo"))
    if type(cycle) is not int or block.get("cycle") != cycle:
        raise InputError("the presentation review must declare the current cycle")
    if block.get("profile") != profile:
        raise InputError("the presentation review was produced for another environment profile")

    artifacts = Artifacts(swarm)
    manifest = artifacts.read_json(block.get("manifest"), "presentation manifest")
    inspections = artifacts.read_json(block.get("inspections"), "presentation inspections")
    inputs = artifacts.read_json(block.get("inputs"), "presentation inputs")
    if manifest.get("schema_version") != 1 or manifest.get("capability") != CAPABILITY:
        raise InputError("unsupported presentation manifest")
    if manifest.get("cycle") != cycle or manifest.get("profile") != profile:
        raise InputError("the manifest belongs to another cycle or profile")
    if manifest.get("inputs") != block["inputs"]:
        raise InputError("the manifest and the review reference different input inventories")
    if inputs.get("schema_version") != 1 or inputs.get("capability") != CAPABILITY:
        raise InputError("unsupported presentation input inventory")

    declared_files = verify_inventory(artifacts, manifest, output_root)
    for name, descriptor in (("deck", manifest.get("deck")), ("layout", manifest.get("layout"))):
        path = logical(mapping(descriptor, f"manifest {name}").get("path"), f"manifest {name}")
        if declared_files.get(path.as_posix()) != descriptor.get("sha256"):
            raise InputError(f"the manifest {name} is not part of the verified delivery inventory")
    formats = mapping(manifest.get("formats"), "manifest formats")
    if set(formats) != set(FORMATS):
        raise InputError(f"the manifest must deliver exactly {', '.join(FORMATS)}")
    for name, descriptor in formats.items():
        path = logical(mapping(descriptor, f"format {name}").get("path"), f"format {name}")
        if path != output_root / FORMAT_FILES[name]:
            raise InputError(f"format {name} must be delivered as {FORMAT_FILES[name]} in the output root")
        if declared_files.get(path.as_posix()) != descriptor.get("sha256"):
            raise InputError(f"format {name} is not part of the verified delivery inventory")

    deck = mapping(parse_strict_json(artifacts.read(manifest["deck"], "deck").decode("utf-8"), name="deck"), "deck")
    layout = mapping(parse_strict_json(artifacts.read(manifest["layout"], "layout").decode("utf-8"), name="layout"), "layout")
    if layout.get("schema_version") != 1 or layout.get("profile") != profile:
        raise InputError("unsupported layout plan")
    if layout.get("deck_sha256") != manifest["deck"]["sha256"]:
        raise InputError("the layout plan was derived from another deck")
    facts = deck_structure(deck)
    pages = expected_pages(facts, layout)
    verify_layout_navigation(layout, expected_navigation(facts, pages))
    recorded_pages = [mapping(page, "manifest page").get("page_id")
                      for page in listing(manifest.get("pages"), "manifest pages", minimum=1, maximum=2000)]
    if recorded_pages != [page["page_id"] for page in pages]:
        raise InputError("the manifest page inventory differs from the reconstructed deck")

    topics = []
    for item in listing(review.get("topics", review.get("topicos")), "review topics", minimum=1, maximum=200):
        entry = mapping(item, "review topic")
        topics.append(identifier(entry.get("topico", entry.get("topic")), "topic", TOPIC_ID))
    if sorted(set(topics)) != facts["topics"]:
        raise InputError("the deck must cover exactly the topics graded by the review")

    subjects = {"candidate": block["manifest"]["sha256"]}
    for scope in ("implementation", "profile"):
        descriptor = mapping(inputs.get(scope), f"inputs {scope}")
        artifacts.read(descriptor, f"{scope} qualification")
        subjects[scope] = descriptor["sha256"]
    blocked = verify_inspections(inspections, subjects, cycle, profile)
    blocked.extend(verify_reviews(artifacts, block, cycle, review_domain(topics, pages)))
    return blocked
