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

import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

from scripts.checks.common import GRADE_INDEX, InputError, SCALE, normalize_grade
from scripts.checks.gate import EDITORIAL_SURFACES, editorial_blockers
from scripts.orchestration.spec import AgentSpec

SECTION_ROOTS = ("output/sections/", "output/figures/", "output/assets/")
FILE_EXTENSIONS = {".md", ".svg", ".json", ".txt", ".csv"}
MAX_FILES = 50
MAX_FILE_BYTES = 1024 * 1024
MAX_DOCUMENT_BYTES = 5 * 1024 * 1024
MAX_NARRATIVE_CHARS = 20000
MAX_RESULT_BYTES = 16 * 1024 * 1024
MAX_PATH_CHARS = 120
FORBIDDEN_NAME_CHARACTERS = set('<>:"|?*')
RESERVED_NAMES = {"con", "prn", "aux", "nul", *(f"com{n}" for n in range(1, 10)), *(f"lpt{n}" for n in range(1, 10))}
# No pipe or backtick: a URL lands in a Markdown table row that the checkers split on "|".
URL = re.compile(r"^https?://[^\s<>\"'|`\\]+$")
SEVERITIES = ("critical", "important", "minor")
APPROVING = GRADE_INDEX["A"]


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
    """
    candidates = [text.strip()]
    candidates += [item.strip() for item in re.findall(r"```(?:json)?[ \t]*\r?\n(.*?)\r?\n```", text, re.S | re.I)]
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        candidates.append(text[start:end + 1])
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(value, dict):
            return value, None
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
    stream and wildcard characters, reserved device names and a trailing dot or space
    (Windows drops both).  Names are compared by their NFC form for the same reason.
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
CONSOLIDATION_SCHEMA = {
    "type": "object", "required": ["document_markdown", "divergences"],
    "properties": {
        "document_markdown": STRING,
        "divergences": {"type": "array", "items": {"type": "object", "required": ["topic", "author", "issue"],
                                                   "properties": {"topic": STRING, "author": STRING, "issue": STRING}}},
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


def reviewer_schema(*, editorial: bool, fact: bool) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "topics": {"type": "array", "items": {"type": "object", "required": ["topic", "grade", "justification", "action"],
                                              "properties": {"topic": STRING, "grade": {"type": "string", "enum": list(SCALE)},
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
        if not URL.match(entry["url"]):
            errors.append(f"source {entry['id']} has a malformed URL")
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
            errors.append(f"divergence names unknown topic {topic!r}")
        if author and author not in authors:
            errors.append(f"divergence names unknown author {author!r}")
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
        errors.append(f"these topics are not in the brief: {', '.join(extra)}")
    report: dict[str, Any] = {"schema_version": 1, "cycle": cycle, "reviewer": spec.name, "topics": rows}
    if spec.evidence_class == "fact":
        consulted = objects(result.get("sources_consulted"), "sources_consulted", errors)
        urls = {str(row.get("url")) for row in consulted if isinstance(row.get("url"), str) and URL.match(row["url"])}
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
                  reports: list[dict[str, Any]], duck: dict[str, Any], editorial: dict[str, Any] | None) -> dict[str, Any]:
    """The consolidated matrix.  The minimum of the reviewers' grades decides each topic."""
    rows = []
    for topic, title in topics.items():
        graded = [(GRADE_INDEX[row["grade"]], report["reviewer"], row["grade"])
                  for report in reports for row in report["topics"] if row["topic"] == topic]
        lowest = min(graded, key=lambda item: item[0])
        rows.append({"topico": topic, "title": title, "nota_minima": lowest[2], "revisor_da_minima": lowest[1],
                     "bloqueia": lowest[0] < APPROVING})
    review: dict[str, Any] = {
        "schema_version": 1, "skill_version": skill_version, "quality_contract": "editorial-v1", "mode": "document",
        "cycle": cycle, "max_cycles": max_cycles, "topics": rows,
        "rubberduck": {"critico": duck["critical"], "achados": duck["findings"]}}
    if editorial is not None:
        review["editorial"] = editorial
    return review


def render_sources_index(fragments: list[tuple[str, list[dict[str, str]]]]) -> str:
    lines = ["# Índice de fontes", "",
             "Gerado pelo executor a partir dos resultados dos autores; não edite à mão.", "",
             "| ID | Título | Tipo | URL | Verificado |", "|---|---|---|---|---|"]
    for _author, sources in fragments:
        for source in sorted(sources, key=lambda item: item["id"]):
            title = source["title"].replace("|", "\\|").replace("\n", " ")
            lines.append(f"| {source['id']} | {title} | {source['type'].replace('|', '/')} | {source['url']} | pendente de verify_sources.py |")
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
