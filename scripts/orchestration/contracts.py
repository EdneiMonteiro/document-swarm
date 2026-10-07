"""What each role must return, how it is validated, and the artifacts it becomes.

Agents never write into the swarm.  They return one JSON object; the executor
validates it against the contract below and, only if it holds, writes the same
files the legacy flow wrote.  Validating at record time turns a mistake that the
coordinator used to meet several turns later, as a gate exit code 3, into an
immediate and specific retry request.

Nothing here assigns a grade.  Grades come from the reviewers' own results; the
code only checks that they are well formed, complete and quoted from the text.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any, Iterable
from urllib.parse import urlsplit

from scripts.checks.common import GRADE_INDEX, InputError, SCALE, normalize_grade
from scripts.checks.gate import EDITORIAL_SURFACES, ORIGINAL_APPROVAL_GRADE, editorial_blockers
from scripts.orchestration.spec import RESERVED_NAMES, AgentSpec

SECTION_ROOTS = ("output/sections/", "output/figures/", "output/assets/")
FILE_EXTENSIONS = {".md", ".svg", ".json", ".txt", ".csv"}
MAX_FILES = 50
MAX_FILE_BYTES = 1024 * 1024
MAX_DOCUMENT_BYTES = 5 * 1024 * 1024
MAX_NARRATIVE_CHARS = 20000
MAX_RESULT_BYTES = 16 * 1024 * 1024
MAX_PATH_CHARS = 120
# A file name limit counts bytes on most file systems (255), and the executor writes a temporary name beside it.
MAX_COMPONENT_BYTES = 100
SHORT_NAME = re.compile(r"~\d")
FORBIDDEN_NAME_CHARACTERS = set('<>:"|?*')
# No pipe or backtick: a URL lands in a Markdown table row that the checkers split on "|".
URL = re.compile(r"^https?://[^\s<>\"'|`\\]+$")
# A source is one row of a Markdown table that verify_sources.py scans for addresses and update_memory.py splits on
# "|", so the text around the address must not carry an address, a pipe or a line break of its own.
SOURCE_TEXT_LIMITS = {"title": 300, "type": 80}
ADDRESS_IN_TEXT = re.compile(r"https?://", re.I)
LINE_BREAKS = {"\x85", "\u2028", "\u2029"}
LOCAL_NAME_SUFFIXES = (".localhost", ".local", ".localdomain", ".internal", ".home.arpa")
# Lab and test switch: the test servers listen on 127.0.0.1.  A real run never sets it.
ALLOW_LOCAL_URLS = "DOCSWARM_ALLOW_LOCAL_URLS"
SEVERITIES = ("critical", "important", "minor")
APPROVING = GRADE_INDEX[ORIGINAL_APPROVAL_GRADE]
# Decision of the skill's owner on 2026-10-07: while the swarm's own excellence and performance are being worked on, a
# new executor swarm approves at A-.  Set this to "A" to restore the original bar for new swarms.  Every review states the
# grade it was judged under, so the reviews already written keep their meaning either way.
PROVISIONAL_APPROVAL_GRADE = "A-"


def source_text_problem(label: str, value: str) -> str | None:
    if len(value) > SOURCE_TEXT_LIMITS[label]:
        return f"is longer than {SOURCE_TEXT_LIMITS[label]} characters"
    if any(ord(character) < 32 or ord(character) == 127 or character in LINE_BREAKS for character in value):
        return "must be one line of plain text"
    if "|" in value:
        return "must not contain a pipe"
    if ADDRESS_IN_TEXT.search(value):
        return "must not contain a web address; the address goes in url"
    return None


def host_problem(url: str) -> str | None:
    """Why a source address must not be requested from this machine, or None.

    Static checks only, with no network access: credentials, names that mean this machine or a private
    network, and addresses that are not public, written in any form a resolver would accept.  A public name
    that resolves to a private address, or that redirects to one, is beyond what this can see.
    """
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").rstrip(".").lower()
        _ = parts.port
    except ValueError:
        return "its host or port is malformed"
    if "@" in parts.netloc:
        return "it carries credentials"
    if not host:
        return "it has no host"
    if host == "localhost" or host.endswith(LOCAL_NAME_SUFFIXES):
        return "it names this machine or a private network"
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if ":" in host:
            return "its host is not a valid address"
        # Resolvers read 2130706433, 0x7f.1, 0177.0.0.1 and 127.1 as addresses although they are not dotted
        # quads, and no public name ends in a purely numeric label.
        if re.fullmatch(r"0x[0-9a-f]+|[0-9]+", host.rsplit(".", 1)[-1]):
            return "its host is a number written in an unusual form"
        return None
    for candidate in (address, getattr(address, "ipv4_mapped", None)):
        if candidate is not None and (not candidate.is_global or candidate.is_multicast):
            return f"{candidate} is not a public address"
    return None


def public_url(url: str) -> bool:
    """A well formed address this machine may request."""
    return bool(URL.match(url)) and (os.environ.get(ALLOW_LOCAL_URLS) == "1" or host_problem(url) is None)


def text_field(value: Any, label: str, errors: list[str], *, empty: bool = False) -> str:
    if not isinstance(value, str) or (not empty and not value.strip()):
        errors.append(f"{label} must be a non-empty string")
        return ""
    return value


@dataclass
class Answer:
    """What a backend hands back for one task: the agent's result and what it can tell about the run.

    ``result`` is a JSON object, or text that contains one, or None when the agent produced nothing.
    ``runtime`` holds the few scalar facts worth keeping (model, seconds, exit code).
    """

    result: Any
    runtime: dict[str, Any] = field(default_factory=dict)


def storable(value: Any) -> bool:
    """True if an agent's result is plain JSON the executor can write back as UTF-8 text.

    One guard in front of every validator: a lone surrogate or a NaN in any string or number
    would otherwise fail later, in whichever write happens to reach it first.
    """
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError):
        return False
    return len(encoded) <= MAX_RESULT_BYTES


def parse_agent_json(text: str) -> tuple[dict[str, Any] | None, str | None]:
    """Recover the JSON object an agent was asked to return, tolerating the usual wrappers.

    A model asked for bare JSON still often adds a sentence or a code fence.  Rejecting that would cost
    a retry of a long agent run for a formatting habit, so the object is taken from the whole text, from
    a fenced block, or from the outermost braces.  Whatever is recovered still has to satisfy the contract.

    A long Markdown string is also often written with its line breaks and tabs unescaped, which strict JSON
    refuses, so they are accepted inside strings.  When nothing parses, the refusal says where the object
    breaks, so that the retry corrects that and not a guess.
    """
    candidates = [text.strip()]
    candidates += [item.strip() for item in re.findall(r"```(?:json)?[ \t]*\r?\n(.*?)\r?\n```", text, re.S | re.I)]
    start, end = text.find("{"), text.rfind("}")
    if start >= 0:
        # An answer cut off before its last brace still shows where it broke.
        candidates.append(text[start:end + 1] if end > start else text[start:])
    reason = ""
    for index, candidate in enumerate(candidates):
        try:
            value = json.loads(candidate, strict=False)
        except RecursionError:
            # Deeply nested text raises RecursionError, which is not a ValueError: an answer made of
            # thousands of brackets must be refused like any other malformed answer, not end the run.
            continue
        except ValueError as exc:
            if index == len(candidates) - 1 and start >= 0:
                reason = (f"{exc.msg} at line {exc.lineno} column {exc.colno}"
                          if isinstance(exc, json.JSONDecodeError) else str(exc))
            continue
        if isinstance(value, dict):
            return value, None
    if reason:
        return None, ("the answer is not a JSON object: the text from its first { is not valid JSON "
                      f"({reason}); reply with only the JSON object that obeys the schema, writing quotes and line "
                      'breaks inside strings as \\" and \\n')
    return None, "the answer is not a JSON object; reply with only the JSON object that obeys the schema"


def clean_runtime(runtime: Any) -> dict[str, Any]:
    """The few scalar facts a backend reports about a run (model, timing); anything else is dropped."""
    clean: dict[str, Any] = {}
    for key, value in (runtime.items() if isinstance(runtime, dict) else []):
        if isinstance(key, str) and (value is None or isinstance(value, (str, int, float, bool))) and len(clean) < 24:
            candidate = {key[:60]: value[:200] if isinstance(value, str) else value}
            if storable(candidate):
                clean.update(candidate)
    return clean


def objects(value: Any, label: str, errors: list[str]) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        errors.append(f"{label} must be a list")
        return []
    rows = []
    for index, item in enumerate(value):
        if isinstance(item, dict):
            rows.append(item)
        else:
            errors.append(f"{label}[{index}] must be an object")
    return rows


def normalise_whitespace(text: str) -> str:
    return " ".join(text.split())


def quote_missing(quote: str, text: str) -> bool:
    return normalise_whitespace(quote) not in normalise_whitespace(text)


def delivery_path(raw: Any, errors: list[str], *, primary: str) -> str | None:
    """A path an agent may propose: relative, contained, portable and of an allowed kind.

    The path becomes a file the executor writes, so every way a name can reach outside its
    folder or collide with another name is refused here: traversal, absolute and drive forms,
    stream and wildcard characters, reserved device names, a trailing dot or space (Windows drops
    both) and a Windows short name such as ``INTROD~1.MD``, which names another file by an
    alias that no comparison of spellings can relate to it.  Names are compared by their NFC form
    for the same reason.
    """
    if not isinstance(raw, str) or not raw.strip():
        errors.append("a file path must be a non-empty string")
        return None
    name = unicodedata.normalize("NFC", raw.replace("\\", "/"))
    logical = PurePosixPath(name)
    # A part made only of dots ("..") ends in a dot, so the trailing-dot rule is also the traversal rule.
    unsafe = (any(ord(character) < 32 for character in name) or logical.is_absolute()
              or len(name) > MAX_PATH_CHARS
              or any(character in FORBIDDEN_NAME_CHARACTERS for character in name)
              or any(part != part.rstrip(". ") or part.split(".")[0].lower() in RESERVED_NAMES
                     or SHORT_NAME.search(part) or len(part.encode("utf-8")) > MAX_COMPONENT_BYTES
                     for part in logical.parts))
    if unsafe:
        errors.append(f"unsafe file path {raw!r}")
        return None
    normal = logical.as_posix()
    if not normal.startswith(SECTION_ROOTS):
        errors.append(f"{normal} is outside the folders an agent may propose: {', '.join(SECTION_ROOTS)}")
        return None
    if logical.suffix.lower() not in FILE_EXTENSIONS:
        errors.append(f"{normal} has an extension that is not allowed ({', '.join(sorted(FILE_EXTENSIONS))})")
        return None
    if normal.casefold() == primary.casefold():
        errors.append(f"{normal} is the consolidated deliverable; only the coordinator produces it")
        return None
    return normal


# -- JSON Schemas (the structural subset the runtime enforces) ---------------

STRING = {"type": "string"}
AUTHOR_SCHEMA = {
    "type": "object", "required": ["files", "sources"],
    "properties": {
        "files": {"type": "array", "items": {"type": "object", "required": ["path", "content"],
                                             "properties": {"path": STRING, "content": STRING}}},
        "sources": {"type": "array", "items": {"type": "object", "required": ["id", "title", "type", "url"],
                                               "properties": {"id": STRING, "title": STRING, "type": STRING, "url": STRING}}},
        "notes": STRING,
    },
}


def consolidation_schema(topics: dict[str, str], authors: Iterable[str]) -> dict[str, Any]:
    """The coordinator's result, with the only values a divergence may name spelled out.

    A divergence is routed to the author and the topic it names, so each must be exactly a declared name and a topic
    id.  A model told only that they are strings writes what reads naturally ("author-01 e author-03", "T01 / T06"),
    and a whole consolidation, minutes of a long agent run, is refused for it.
    """
    return {
        "type": "object", "required": ["document_markdown", "divergences"],
        "properties": {
            "document_markdown": STRING,
            "divergences": {"type": "array", "items": {"type": "object", "required": ["topic", "author", "issue"], "properties": {
                "topic": {"type": "string", "enum": ["", *topics]},
                "author": {"type": "string", "enum": ["", *sorted(authors)]},
                "issue": STRING}}},
        },
    }


DUCK_SCHEMA = {
    "type": "object", "required": ["critical", "findings"],
    "properties": {
        "critical": {"type": "boolean"},
        "findings": {"type": "array", "items": {"type": "object", "required": ["severity", "target", "evidence", "correction"],
                                                "properties": {"severity": {"type": "string", "enum": list(SEVERITIES)},
                                                               "target": STRING, "evidence": STRING, "correction": STRING}}},
        "consistency_notes": STRING,
    },
}
NARRATIVE_SCHEMA = {"type": "object", "required": ["narrative_markdown"], "properties": {"narrative_markdown": STRING}}


def reviewer_schema(topics: Iterable[str], *, editorial: bool, fact: bool) -> dict[str, Any]:
    """The reviewer's result, with the only values ``topic`` may take spelled out.

    A grade is matched to the brief by topic id, so "T01: Enquadramento" (the id and the title, as the prompt lists
    them) is a topic the brief does not have, and a whole assessment, minutes of a long agent run, is refused for it.
    """
    properties: dict[str, Any] = {
        "topics": {"type": "array", "items": {"type": "object", "required": ["topic", "grade", "justification", "action"],
                                              "properties": {"topic": {"type": "string", "enum": list(topics)},
                                                             "grade": {"type": "string", "enum": list(SCALE)},
                                                             "justification": STRING, "action": STRING}}},
    }
    required = ["topics"]
    if fact:
        properties["sources_consulted"] = {"type": "array", "items": {"type": "object", "required": ["url", "finding"],
                                                                      "properties": {"url": STRING, "finding": STRING}}}
        required.append("sources_consulted")
    if editorial:
        properties["editorial"] = {"type": "object", "required": ["surfaces", "findings"], "properties": {
            "surfaces": {"type": "array", "items": {"type": "object", "required": ["surface"], "properties": {
                "surface": {"type": "string", "enum": list(EDITORIAL_SURFACES)}, "grade": {"type": "string", "enum": list(SCALE)},
                "location": STRING, "quote": STRING, "justification": STRING, "action": STRING, "not_applicable": STRING}}},
            "findings": {"type": "array", "items": {"type": "object", "required": ["severity", "location", "quote", "reason", "action"],
                                                    "properties": {"severity": {"type": "string", "enum": ["blocking", "minor"]},
                                                                   "location": STRING, "quote": STRING, "reason": STRING, "action": STRING}}}}}
        required.append("editorial")
    return {"type": "object", "required": required, "properties": properties}


# -- validators --------------------------------------------------------------

def file_collisions(paths: list[str], existing: dict[str, str]) -> list[str]:
    """A name cannot be both a file and a folder: ``x.md`` and ``x.md/b.md`` cannot coexist.

    The second write would fail half way through a result, after the first file was already written.
    Names are compared case-insensitively, like the file systems that matter here.
    """
    folded = {path.casefold(): path for path in [*existing, *paths]}
    problems = []
    for path in paths:
        parts = path.casefold().split("/")
        for end in range(1, len(parts)):
            parent = "/".join(parts[:end])
            if parent in folded:
                problems.append(f"{path} needs {folded[parent]} to be a folder, but that name is a file")
        prefix = path.casefold() + "/"
        problems += [f"{path} is a file, but {other} is inside a folder of that name"
                     for other_folded, other in folded.items() if other_folded.startswith(prefix)]
    return problems


def check_author(result: Any, *, spec: AgentSpec, code: str, primary: str,
                 owners: dict[str, str]) -> tuple[list[str], dict[str, Any] | None]:
    errors: list[str] = []
    if not isinstance(result, dict):
        return ["the result must be a JSON object"], None
    files, seen = [], set()
    holders = {path.casefold(): (path, owner) for path, owner in owners.items()}
    rows = objects(result.get("files"), "files", errors)
    if not rows:
        errors.append("files must contain at least one file")
    if len(rows) > MAX_FILES:
        errors.append(f"files exceeds the limit of {MAX_FILES}")
    for row in rows[:MAX_FILES]:
        path = delivery_path(row.get("path"), errors, primary=primary)
        content = row.get("content")
        if not isinstance(content, str) or not content.strip():
            errors.append(f"{row.get('path')!r} has no content")
            continue
        if "\x00" in content or len(content.encode("utf-8")) > MAX_FILE_BYTES:
            errors.append(f"{row.get('path')!r} is binary or exceeds {MAX_FILE_BYTES} bytes")
            continue
        if path is None:
            continue
        # Case-insensitive file systems make "A.md" and "a.md" one file: ownership and duplicates
        # are decided on the folded name, and the spelling already on record is kept.
        holder = holders.get(path.casefold())
        if holder and holder[1] != spec.name:
            errors.append(f"{holder[0]} belongs to {holder[1]}; an author may only write its own files")
            continue
        if holder:
            path = holder[0]
        if path.casefold() in seen:
            errors.append(f"{path} appears twice in the same result")
            continue
        seen.add(path.casefold())
        files.append({"path": path, "content": content.replace("\r\n", "\n")})
    errors.extend(file_collisions([item["path"] for item in files], owners))
    sources, identifiers, urls = [], set(), set()
    for row in objects(result.get("sources"), "sources", errors):
        entry = {key: text_field(row.get(key), f"sources[].{key}", errors) for key in ("id", "title", "type", "url")}
        if not all(entry.values()):
            continue
        if not re.fullmatch(rf"{re.escape(code)}\d{{2}}", entry["id"]):
            errors.append(f"source id {entry['id']!r} must be {code} followed by two digits "
                          f"(for example {code}01), so it cannot collide with another author")
        if entry["id"] in identifiers:
            errors.append(f"source id {entry['id']!r} is repeated")
        for label in ("title", "type"):
            problem = source_text_problem(label, entry[label])
            if problem:
                errors.append(f"source {entry['id']} {label} {problem}")
        if not URL.match(entry["url"]):
            errors.append(f"source {entry['id']} has a malformed URL")
        elif os.environ.get(ALLOW_LOCAL_URLS) != "1":
            reason = host_problem(entry["url"])
            if reason:
                errors.append(f"source {entry['id']} cannot be used, because {reason}; cite a public page")
        identifiers.add(entry["id"])
        urls.add(entry["url"])
        sources.append(entry)
    if len(urls) < spec.sources_min:
        errors.append(f"{len(urls)} distinct sources were returned, fewer than the {spec.sources_min} this author declares")
    if errors:
        return errors, None
    return [], {"files": files, "sources": sources, "notes": str(result.get("notes") or "")}


def check_consolidation(result: Any, *, topics: dict[str, str],
                        authors: set[str]) -> tuple[list[str], dict[str, Any] | None]:
    errors: list[str] = []
    if not isinstance(result, dict):
        return ["the result must be a JSON object"], None
    document = text_field(result.get("document_markdown"), "document_markdown", errors)
    if document and not re.search(r"(?m)^#{1,6}\s+\S", document):
        errors.append("document_markdown has no Markdown heading")
    if document and len(document.encode("utf-8")) > MAX_DOCUMENT_BYTES:
        errors.append("document_markdown exceeds the supported size")
    divergences = []
    for row in objects(result.get("divergences"), "divergences", errors):
        topic = text_field(row.get("topic"), "divergences[].topic", errors, empty=True)
        author = text_field(row.get("author"), "divergences[].author", errors, empty=True)
        issue = text_field(row.get("issue"), "divergences[].issue", errors)
        if topic and topic not in topics:
            errors.append(f"divergence names unknown topic {topic!r}; use exactly one topic id ({', '.join(topics)}) "
                          "or leave it empty, and write one divergence per topic")
        if author and author not in authors:
            errors.append(f"divergence names unknown author {author!r}; use exactly one declared author name "
                          f"({', '.join(sorted(authors))}) or leave it empty, and write one divergence per author")
        divergences.append({"topic": topic, "author": author, "issue": issue})
    if errors:
        return errors, None
    return [], {"document_markdown": document.replace("\r\n", "\n"), "divergences": divergences}


def editorial_block(result: Any, *, reviewer: str, cycle: int, text_path: str, text_sha: str,
                    artifacts: list[dict[str, str]], text: str, errors: list[str]) -> dict[str, Any] | None:
    """Assemble the editorial-v1 block: deterministic fields from code, judgement from the reviewer."""
    if not isinstance(result, dict):
        errors.append("editorial must be an object")
        return None
    surfaces: list[dict[str, Any]] = []
    for row in objects(result.get("surfaces"), "editorial.surfaces", errors):
        entry: dict[str, Any] = {"surface": row.get("surface")}
        if "not_applicable" in row:
            entry["not_applicable"] = row.get("not_applicable")
        else:
            for key in ("grade", "location", "quote", "justification"):
                entry[key] = row.get(key)
            entry["action"] = row.get("action", "")
            try:
                # The gate accepts "a" and " B+", so the stored grade must be the canonical one: everything
                # downstream indexes the scale with it.
                entry["grade"] = normalize_grade(entry["grade"])
            except InputError:
                pass  # editorial_blockers below reports a missing or invalid grade, naming the surface
        surfaces.append(entry)
    findings = [{key: row.get(key) for key in ("severity", "location", "quote", "reason", "action")}
                for row in objects(result.get("findings"), "editorial.findings", errors)]
    block = {"schema_version": 1, "reviewer": reviewer, "cycle": cycle, "scope": "full_document",
             "text": {"path": text_path, "sha256": text_sha}, "artifacts": artifacts,
             "surfaces": surfaces, "findings": findings}
    if errors:
        return None
    try:
        editorial_blockers({"editorial": block}, cycle, True)
    except InputError as exc:
        errors.append(f"editorial review is not valid: {exc}")
        return None
    for item in [*surfaces, *findings]:
        quote = item.get("quote")
        if isinstance(quote, str) and quote_missing(quote, text):
            errors.append(f"the quote {quote[:70]!r} does not appear in the reviewed text; quote it exactly")
    return None if errors else block


def check_reviewer(result: Any, *, spec: AgentSpec, topics: dict[str, str], cycle: int, editorial: bool,
                   text: str, text_path: str, text_sha: str,
                   artifacts: list[dict[str, str]]) -> tuple[list[str], dict[str, Any] | None]:
    errors: list[str] = []
    if not isinstance(result, dict):
        return ["the result must be a JSON object"], None
    rows, seen = [], set()
    for row in objects(result.get("topics"), "topics", errors):
        topic = text_field(row.get("topic"), "topics[].topic", errors)
        if topic in seen:
            errors.append(f"topic {topic} is graded twice")
        seen.add(topic)
        try:
            grade = normalize_grade(row.get("grade"))
        except InputError as exc:
            errors.append(f"topic {topic}: {exc}")
            continue
        justification = text_field(row.get("justification"), f"topic {topic} justification", errors)
        action = text_field(row.get("action"), f"topic {topic} action", errors, empty=True)
        if GRADE_INDEX[grade] < APPROVING and not action.strip():
            errors.append(f"topic {topic} is below A and needs an actionable correction")
        rows.append({"topic": topic, "grade": grade, "justification": justification, "action": action})
    missing, extra = sorted(set(topics) - seen), sorted(seen - set(topics))
    if missing:
        errors.append(f"these topics were not graded: {', '.join(missing)}")
    if extra:
        errors.append(f"these topics are not in the brief: {', '.join(extra)}; `topic` is exactly one id "
                      f"({', '.join(topics)}), without the title")
    report: dict[str, Any] = {"schema_version": 1, "cycle": cycle, "reviewer": spec.name, "topics": rows}
    if spec.evidence_class == "fact":
        consulted = objects(result.get("sources_consulted"), "sources_consulted", errors)
        urls = {str(row.get("url")) for row in consulted if isinstance(row.get("url"), str) and public_url(row["url"])}
        if len(urls) < spec.sources_min:
            errors.append(f"{len(urls)} distinct sources were consulted, fewer than the {spec.sources_min} this reviewer declares")
        report["sources_consulted"] = [{"url": str(row.get("url")), "finding": str(row.get("finding") or "")} for row in consulted]
    if editorial:
        block = editorial_block(result.get("editorial"), reviewer=spec.name, cycle=cycle, text_path=text_path,
                                text_sha=text_sha, artifacts=artifacts, text=text, errors=errors)
        if block is not None:
            report["editorial"] = block
    elif result.get("editorial") not in (None, {}):
        errors.append("only the reviewer assigned in the brief may submit the editorial assessment")
    return (errors, None) if errors else ([], report)


def check_duck(result: Any) -> tuple[list[str], dict[str, Any] | None]:
    errors: list[str] = []
    if not isinstance(result, dict):
        return ["the result must be a JSON object"], None
    critical = result.get("critical")
    if not isinstance(critical, bool):
        errors.append("critical must be true or false")
    findings = []
    for row in objects(result.get("findings"), "findings", errors):
        severity = row.get("severity")
        if severity not in SEVERITIES:
            errors.append(f"finding severity must be one of {', '.join(SEVERITIES)}")
            continue
        entry = {key: text_field(row.get(key), f"finding {key}", errors) for key in ("target", "evidence", "correction")}
        findings.append({"severity": severity, **entry})
    if isinstance(critical, bool) and critical != any(item["severity"] == "critical" for item in findings):
        errors.append("critical must be true exactly when at least one finding has severity critical")
    if errors:
        return errors, None
    return [], {"critical": critical, "findings": findings, "consistency_notes": str(result.get("consistency_notes") or "")}


def check_narrative(result: Any) -> tuple[list[str], dict[str, Any] | None]:
    errors: list[str] = []
    if not isinstance(result, dict):
        return ["the result must be a JSON object"], None
    text = text_field(result.get("narrative_markdown"), "narrative_markdown", errors)
    if len(text) > MAX_NARRATIVE_CHARS:
        errors.append(f"narrative_markdown exceeds {MAX_NARRATIVE_CHARS} characters")
    return (errors, None) if errors else ([], {"narrative_markdown": text.strip()})


# -- artifacts ---------------------------------------------------------------

def pending_duck() -> dict[str, Any]:
    """The audit that has not happened yet.  It fails closed: a gate run too early blocks."""
    return {"critical": True, "findings": [{
        "severity": "critical", "target": "executor", "evidence": "the independent audit of this cycle has not been recorded",
        "correction": "run the rubber duck for this cycle before the gate"}]}


def render_review(*, cycle: int, max_cycles: int, skill_version: str, topics: dict[str, str],
                  reports: list[dict[str, Any]], duck: dict[str, Any], editorial: dict[str, Any] | None,
                  approval_grade: str = ORIGINAL_APPROVAL_GRADE) -> dict[str, Any]:
    """The consolidated matrix.  The minimum of the reviewers' grades decides each topic against the approval grade."""
    bar = GRADE_INDEX[approval_grade]
    rows = []
    for topic, title in topics.items():
        graded = [(GRADE_INDEX[row["grade"]], report["reviewer"], row["grade"])
                  for report in reports for row in report["topics"] if row["topic"] == topic]
        lowest = min(graded, key=lambda item: item[0])
        rows.append({"topico": topic, "title": title, "nota_minima": lowest[2], "revisor_da_minima": lowest[1],
                     "bloqueia": lowest[0] < bar})
    review: dict[str, Any] = {
        "schema_version": 1, "skill_version": skill_version, "quality_contract": "editorial-v1", "mode": "document",
        "cycle": cycle, "max_cycles": max_cycles, "topics": rows,
        "rubberduck": {"critico": duck["critical"], "achados": duck["findings"]}}
    if approval_grade != ORIGINAL_APPROVAL_GRADE:
        review["approval_grade"] = approval_grade
    if editorial is not None:
        review["editorial"] = editorial
    return review


def render_sources_index(fragments: list[tuple[str, list[dict[str, str]]]]) -> str:
    lines = ["# Índice de fontes", "",
             "Gerado pelo executor a partir dos resultados dos autores; não edite à mão.", "",
             "| ID | Título | Tipo | URL | Verificado |", "|---|---|---|---|---|"]
    for _author, sources in fragments:
        for source in sorted(sources, key=lambda item: item["id"]):
            title = " ".join(source["title"].split()).replace("|", "\\|")
            kind = " ".join(source["type"].split()).replace("|", "/")
            lines.append(f"| {source['id']} | {title} | {kind} | {source['url']} | pendente de verify_sources.py |")
    return "\n".join(lines) + "\n"


def render_reviewer_md(report: dict[str, Any], topics: dict[str, str]) -> str:
    lines = [f"# Revisão: {report['reviewer']} (ciclo {report['cycle']})", "",
             "| Tópico | Nota | Justificativa | Correção acionável |", "|---|---|---|---|"]
    for row in report["topics"]:
        cells = [row["justification"], row["action"] or "—"]
        cells = [cell.replace("|", "\\|").replace("\n", " ") for cell in cells]
        lines.append(f"| {row['topic']} {topics.get(row['topic'], '')} | {row['grade']} | {cells[0]} | {cells[1]} |")
    lowest = min(report["topics"], key=lambda row: GRADE_INDEX[row["grade"]])
    lines += ["", f"Nota mínima: {lowest['grade']} ({lowest['topic']})."]
    return "\n".join(lines) + "\n"


def render_review_md(review: dict[str, Any]) -> str:
    lines = [f"# Revisão consolidada do ciclo {review['cycle']}", "",
             "| Tópico | Nota mínima | Revisor da mínima | Bloqueia |", "|---|---|---|---|"]
    for row in review["topics"]:
        lines.append(f"| {row['topico']} {row['title']} | {row['nota_minima']} | {row['revisor_da_minima']} | "
                     f"{'sim' if row['bloqueia'] else 'não'} |")
    lines += ["", f"Rubber duck crítico: {'sim' if review['rubberduck']['critico'] else 'não'}."]
    return "\n".join(lines) + "\n"


def render_duck_md(duck: dict[str, Any], cycle: int) -> str:
    lines = [f"# Auditoria independente do ciclo {cycle}", "", f"Achado crítico: {'sim' if duck['critical'] else 'não'}.", ""]
    for severity in SEVERITIES:
        group = [item for item in duck["findings"] if item["severity"] == severity]
        if group:
            lines.append(f"## {severity.capitalize()}")
            for item in group:
                lines.append(f"- **{item['target']}**: {item['evidence']} → {item['correction']}")
            lines.append("")
    if duck.get("consistency_notes"):
        lines += ["## Consistência", duck["consistency_notes"], ""]
    return "\n".join(lines).rstrip() + "\n"
