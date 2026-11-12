"""Local evidence ledger. Checks bindings and declared coverage, never truth."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from contextlib import closing, contextmanager
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.checks.common import InputError, write_json_atomic
from scripts.checks.lint_agents import frontmatter_text

CONTRACT = "evidence-v1"
VERSION = 1
KINDS = ("candidate", "inventory", "dispatch", "review", "resolution",
         "cycle", "diagnosis", "extension")
INTENT_EXCUSE_RE = re.compile(
    r"\b(?:foi intencional|era intencional|this was intentional|was intentional)\b",
    re.IGNORECASE,
)
SCHEMA = """
CREATE TABLE events (
    seq INTEGER PRIMARY KEY, kind TEXT NOT NULL, identity TEXT NOT NULL,
    payload TEXT NOT NULL, previous TEXT NOT NULL, sha256 TEXT NOT NULL,
    UNIQUE(kind, identity)
);
CREATE TABLE candidates (
    id TEXT PRIMARY KEY, cycle INTEGER NOT NULL, composition TEXT NOT NULL,
    event INTEGER NOT NULL REFERENCES events(seq)
);
CREATE TABLE artifacts (
    candidate TEXT NOT NULL REFERENCES candidates(id), path TEXT NOT NULL,
    sha256 TEXT NOT NULL, PRIMARY KEY(candidate, path)
);
CREATE TABLE sources (
    candidate TEXT NOT NULL REFERENCES candidates(id), id TEXT NOT NULL,
    path TEXT NOT NULL, sha256 TEXT NOT NULL, PRIMARY KEY(candidate, id)
);
CREATE TABLE claims (
    candidate TEXT NOT NULL REFERENCES candidates(id), id TEXT NOT NULL,
    relation TEXT NOT NULL, PRIMARY KEY(candidate, id)
);
CREATE TABLE occurrences (
    candidate TEXT NOT NULL, id TEXT NOT NULL, claim TEXT NOT NULL,
    start INTEGER NOT NULL, end INTEGER NOT NULL,
    PRIMARY KEY(candidate, id),
    FOREIGN KEY(candidate, claim) REFERENCES claims(candidate, id)
);
CREATE TABLE assessments (
    candidate TEXT NOT NULL, claim TEXT NOT NULL, reviewer TEXT NOT NULL,
    conclusion TEXT NOT NULL, event INTEGER NOT NULL REFERENCES events(seq),
    PRIMARY KEY(candidate, claim, reviewer),
    FOREIGN KEY(candidate, claim) REFERENCES claims(candidate, id)
);
CREATE TRIGGER events_no_update BEFORE UPDATE ON events
BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;
CREATE TRIGGER events_no_delete BEFORE DELETE ON events
BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;
PRAGMA user_version = 1;
"""


def validate_schema(data: Any, name: str) -> None:
    from scripts.checks.jsonschema_lite import validate
    path = Path(__file__).resolve().parents[2] / "schemas/evidence/contract.json"
    schema = json.loads(path.read_text(encoding="utf-8"))
    validate(data, schema["$defs"][name], root=schema)


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def nonempty(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InputError(f"{label} must be a non-empty string")
    return value


def positive(value: Any, label: str) -> int:
    if type(value) is not int or value < 1:
        raise InputError(f"{label} must be a positive integer")
    return value


def identifier(value: Any, label: str) -> str:
    value = nonempty(value, label)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value):
        raise InputError(f"{label} must be a bounded stable identifier")
    return value


def inside(root: Path, name: Any) -> Path:
    name = nonempty(name, "artifact path")
    logical = PurePosixPath(name)
    if ("\\" in name or logical.is_absolute() or PureWindowsPath(name).drive
            or ".." in logical.parts or any(ord(c) < 32 for c in name)):
        raise InputError(f"artifact escapes swarm: {name}")
    path = (root / name).resolve()
    if root not in path.parents:
        raise InputError(f"artifact escapes swarm: {name}")
    return path


def file_bytes(root: Path, name: str) -> bytes:
    path = inside(root, name)
    if not path.is_file() or path.stat().st_size > 100 * 1024 * 1024:
        raise InputError(f"missing or oversized artifact: {name}")
    before = path.stat()
    raw = path.read_bytes()
    after = path.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
        raise InputError(f"artifact changed during verification: {name}")
    return raw


def descriptor(root: Path, name: str) -> dict[str, str]:
    return {"path": name, "sha256": hashlib.sha256(file_bytes(root, name)).hexdigest()}


def checked(root: Path, item: Any) -> bytes:
    if not isinstance(item, dict) or set(item) != {"path", "sha256"}:
        raise InputError("artifact descriptor requires exactly path and sha256")
    raw = file_bytes(root, item["path"])
    if hashlib.sha256(raw).hexdigest() != item["sha256"]:
        raise InputError(f"stale evidence artifact: {item['path']}")
    return raw


def object_file(root: Path, item: dict[str, str]) -> dict[str, Any]:
    data = json.loads(checked(root, item).decode("utf-8"))
    if not isinstance(data, dict):
        raise InputError("evidence input must be a JSON object")
    return data


def brief_data(root: Path) -> dict[str, Any]:
    return frontmatter_text(file_bytes(root, "brief.md").decode("utf-8"))


def required(data: dict[str, Any], root: Path | None = None) -> bool:
    version = re.match(r"^(\d+)\.", str(data.get("skill_version", "")))
    return ("evidence_contract" in data or bool(version and int(version[1]) >= 4)
            or bool(root and ((root / "reports/evidence").exists()
                              or (root / "reports/evidence-policy.json").exists())))


def ledger_paths(root: Path) -> tuple[Path, Path]:
    return inside(root, "reports/evidence/evidence.sqlite"), inside(root, "reports/evidence-policy.json")


@contextmanager
def connect(root: Path, *, write: bool = False):
    database, _ = ledger_paths(root)
    if not database.is_file():
        raise InputError("evidence database is missing; explicit recovery and revalidation required")
    db = None
    try:
        db = sqlite3.connect(database.as_uri() + ("?mode=rw" if write else "?mode=ro"), uri=True, timeout=10)
        db.execute("PRAGMA foreign_keys = ON")
        if db.execute("PRAGMA user_version").fetchone()[0] != VERSION:
            raise InputError("unsupported evidence schema; explicit migration required")
        if db.execute("PRAGMA quick_check").fetchone()[0] != "ok" or db.execute("PRAGMA foreign_key_check").fetchone():
            raise InputError("evidence database integrity check failed")
        if write:
            db.execute("BEGIN IMMEDIATE")
        yield db
        if write:
            db.commit()
    except sqlite3.Error as exc:
        raise InputError(f"evidence database invalid: {exc}") from exc
    finally:
        if db is not None:
            db.close()


def events(db: sqlite3.Connection, root: Path) -> list[dict[str, Any]]:
    _, policy_path = ledger_paths(root)
    policy = json.loads(file_bytes(root, policy_path.relative_to(root).as_posix()))
    rows = []
    previous = digest(policy)
    for seq, kind, key, raw, prior, sha in db.execute(
            "SELECT seq,kind,identity,payload,previous,sha256 FROM events ORDER BY seq"):
        data = json.loads(raw)
        if prior != previous or sha != digest([seq, kind, key, data, prior]):
            raise InputError(f"evidence journal altered at event {seq}")
        rows.append({"seq": seq, "kind": kind, "id": key, "data": data, "sha256": sha})
        previous = sha
    if not rows or rows[0]["kind"] != "policy" or rows[0]["data"] != policy:
        raise InputError("recorded evidence policy is missing or changed")
    return rows


def append(db: sqlite3.Connection, history: list[dict[str, Any]], kind: str, key: str, data: Any) -> int:
    old = next((r for r in history if r["kind"] == kind and r["id"] == key), None)
    if old:
        if old["data"] != data:
            raise InputError(f"immutable {kind} already recorded: {key}")
        return old["seq"]
    seq = len(history) + 1
    previous = history[-1]["sha256"]
    sha = digest([seq, kind, key, data, previous])
    db.execute("INSERT INTO events VALUES (?,?,?,?,?,?)", (seq, kind, key, canonical(data), previous, sha))
    history.append({"seq": seq, "kind": kind, "id": key, "data": data, "sha256": sha})
    return seq


def export(root: Path) -> dict[str, Any]:
    with connect(root) as db:
        history = events(db, root)
    payload = {"schema_version": VERSION, "evidence_contract": CONTRACT, "events": history}
    write_json_atomic(inside(root, "reports/evidence/events.json"), payload)
    return payload


def build_reader_context(
    root: Path,
    brief: dict[str, Any],
    cycle: int,
    composition: str,
    text_desc: dict[str, str],
    artifacts: list[dict[str, str]],
) -> dict[str, Any]:
    from scripts.checks.cycle_artifacts import topic_map
    from scripts.checks.gate import COMPREHENSION_CONTRACT
    audience_raw = brief.get("audience") if "audience" in brief else brief.get("publico")
    if audience_raw is not None:
        if (
            not isinstance(audience_raw, str)
            or not audience_raw.strip()
            or "\n" in audience_raw
            or "\r" in audience_raw
            or len(audience_raw.strip()) > 240
        ):
            raise InputError("brief audience for goldfish reader_context must be a single line up to 240 characters")
        audience = audience_raw.strip()
    else:
        audience = "Leitor do perfil editorial declarado, sem contexto prévio dos bastidores"
    topics = []
    for idx, (tid, label) in enumerate(topic_map(brief).items(), 1):
        if "\n" in label or "\r" in label or len(label.strip()) > 140:
            raise InputError(f"topic label for {tid} must be a single concise line (<= 140 chars) under goldfish-v1")
        topics.append({"id": tid, "ordinal": idx, "label": label.strip()})
    payload: dict[str, Any] = {
        "schema_version": VERSION,
        "comprehension_contract": COMPREHENSION_CONTRACT,
        "review_cycle": cycle,
        "composition_id": composition,
        "mode": str(brief.get("mode", "document")),
        "artifact_type": str(brief.get("artifact_type", "document")),
        "editorial_profile": str(brief.get("editorial_profile") or "principal-cloud-solution-architect"),
        "audience": audience,
        "topics": topics,
        "text": text_desc,
        "deliverables": artifacts,
        "forbidden_paths": [
            "brief.md",
            "reports/evidence-policy.json",
            "sources/sources-index.md",
        ],
    }
    if isinstance(brief.get("pdf_profile"), str) and brief["pdf_profile"].strip():
        payload["pdf_profile"] = brief["pdf_profile"].strip()
    return payload


def initialize(swarm: Path) -> dict[str, Any]:
    from scripts.checks.gate import COMPREHENSION_CONTRACT, requires_goldfish
    root = swarm.resolve(strict=True)
    brief = brief_data(root)
    if brief.get("evidence_contract") != CONTRACT:
        raise InputError("brief must explicitly adopt evidence-v1 before initialization")
    reviewers = brief.get("evidence_reviewers")
    if (not isinstance(reviewers, list) or not reviewers or len(set(reviewers)) != len(reviewers)
            or not all(isinstance(r, str) and r.startswith("reviewer-") for r in reviewers)):
        raise InputError("brief must list unique evidence_reviewers")
    factual = nonempty(brief.get("factual_reviewer"), "factual_reviewer")
    auditor = nonempty(brief.get("evidence_auditor"), "evidence_auditor")
    if factual not in reviewers or auditor in reviewers:
        raise InputError("factual reviewer must be assigned; auditor must be independent")
    goldfish = requires_goldfish(brief)
    editorial_rev = None
    if goldfish:
        editorial_rev = nonempty(brief.get("editorial_reviewer"), "editorial_reviewer")
        if editorial_rev not in reviewers or editorial_rev == factual:
            raise InputError("editorial_reviewer under goldfish-v1 must be an assigned form reviewer distinct from factual_reviewer")
    agent_files = {}
    for path in sorted((root / "agents").rglob("*.md")):
        meta = frontmatter_text(file_bytes(root, path.relative_to(root).as_posix()).decode("utf-8"))
        name = meta.get("name")
        if name in [*reviewers, auditor]:
            if name in agent_files:
                raise InputError(f"duplicate assigned agent: {name}")
            if meta.get("kind") != ("rubber-duck" if name == auditor else "reviewer"):
                raise InputError(f"assigned agent has the wrong kind: {name}")
            agent_files[name] = descriptor(root, path.relative_to(root).as_posix())
    if set(agent_files) != {*reviewers, auditor}:
        raise InputError("every assigned evidence reviewer and auditor needs an agent declaration")
    policy = {"schema_version": VERSION, "evidence_contract": CONTRACT, "approval_grade": "A",
              "max_cycles": positive(brief.get("max_cycles", 10), "max_cycles"),
              "first_review_cycle": max(
                  (int(m[1]) for p in (root / "reports").glob("cycle-*-review.*")
                   if (m := re.fullmatch(r"cycle-(\d+)-review\.(?:yaml|yml|json)", p.name))), default=0) + 1,
              "reviewers": reviewers, "factual_reviewer": factual, "auditor": auditor,
              "agents": agent_files, "deliverables": brief.get("deliverables"),
              "adopted_brief": descriptor(root, "brief.md")}
    if goldfish:
        policy["comprehension_contract"] = COMPREHENSION_CONTRACT
        policy["editorial_reviewer"] = editorial_rev
    if not isinstance(policy["deliverables"], list) or not policy["deliverables"]:
        raise InputError("brief must list final deliverables")
    for name in policy["deliverables"]:
        inside(root, name)
    database, policy_path = ledger_paths(root)
    if database.exists() or policy_path.exists():
        with connect(root) as db:
            saved_history = events(db, root)
            saved = saved_history[0]["data"]
        verify_policy(root, current_policy(saved_history))
        return saved
    database.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(policy_path, policy)
    try:
        with closing(sqlite3.connect(database)) as db, db:
            db.executescript(SCHEMA)
            prior = digest(policy)
            sha = digest([1, "policy", "opening", policy, prior])
            db.execute("INSERT INTO events VALUES (?,?,?,?,?,?)",
                       (1, "policy", "opening", canonical(policy), prior, sha))
    except sqlite3.Error as exc:
        raise InputError(f"cannot initialize evidence database: {exc}") from exc
    export(root)
    return policy


def current_policy(history: list[dict[str, Any]]) -> dict[str, Any]:
    policy = dict(history[0]["data"])
    for row in history:
        if row["kind"] == "extension":
            policy["max_cycles"] = row["data"]["max_cycles"]
    return policy


def verify_policy(root: Path, policy: dict[str, Any]) -> None:
    brief = brief_data(root)
    if brief.get("evidence_contract") != CONTRACT:
        raise InputError("adopted evidence-v1 cannot be removed from the brief")
    if brief.get("max_cycles", 10) != policy["max_cycles"]:
        raise InputError("brief cycle ceiling differs from recorded policy")
    for key, expected in (("evidence_reviewers", policy["reviewers"]),
                          ("factual_reviewer", policy["factual_reviewer"]),
                          ("evidence_auditor", policy["auditor"])):
        if brief.get(key) != expected:
            raise InputError(f"brief {key} differs from recorded policy")
    from scripts.checks.gate import COMPREHENSION_CONTRACT, requires_goldfish
    if policy.get("comprehension_contract") == COMPREHENSION_CONTRACT:
        if not requires_goldfish(brief):
            raise InputError("adopted goldfish-v1 comprehension contract cannot be removed from the brief")
        if brief.get("editorial_reviewer") != policy.get("editorial_reviewer"):
            raise InputError("brief editorial_reviewer differs from recorded policy")
    elif brief.get("comprehension_contract") is not None:
        raise InputError("brief comprehension_contract differs from recorded policy")
    deliverables = brief.get("deliverables")
    if (not isinstance(deliverables, list) or not all(isinstance(p, str) for p in deliverables)
            or sorted(PurePosixPath(p).suffix for p in deliverables) !=
            sorted(PurePosixPath(p).suffix for p in policy["deliverables"])):
        raise InputError("deliverable paths may change composition, but must preserve the adopted formats")
    if len(set(deliverables)) != len(deliverables):
        raise InputError("duplicate final deliverable")
    for name in deliverables:
        inside(root, name)
    for item in policy["agents"].values():
        checked(root, item)


def find(history: list[dict[str, Any]], kind: str, key: str) -> dict[str, Any]:
    for row in history:
        if row["kind"] == kind and row["id"] == key:
            return row["data"]
    raise InputError(f"missing {kind}: {key}")


def candidate(history: list[dict[str, Any]], key: str) -> dict[str, Any]:
    return find(history, "candidate", key)


def verify_candidate(root: Path, data: dict[str, Any]) -> None:
    from scripts.checks.gate import COMPREHENSION_CONTRACT
    for item in [data["source"], data["text"], *data["artifacts"],
                 *[s["artifact"] for s in data["sources"]], *data["bundles"]]:
        checked(root, item)
    if "reader_context" in data:
        rc_obj = object_file(root, data["reader_context"])
        if (
            rc_obj.get("comprehension_contract") != COMPREHENSION_CONTRACT
            or rc_obj.get("text") != data["text"]
            or rc_obj.get("deliverables") != data["artifacts"]
            or "brief.md" not in rc_obj.get("forbidden_paths", [])
        ):
            raise InputError("candidate reader_context is invalid or out of sync with frozen artifacts")
    checked(root, data["manifest"])
    reviewed = " ".join(checked(root, data["text"]).decode("utf-8").split())
    pdfs = [a for a in data["artifacts"] if a["path"].lower().endswith(".pdf")]
    if data.get("presentation"):
        from scripts.checks.presentation_contract import editorial_text
        manifest = object_file(root, data["presentation"]["manifest"])
        if manifest["deck"]["sha256"] != data["source"]["sha256"]:
            raise InputError("presentation source differs from the examined deck")
        layout = object_file(root, manifest["layout"])
        if reviewed != " ".join(editorial_text(layout).split()):
            raise InputError("reviewed text differs from the composed presentation wording")
    elif pdfs:
        for pdf in pdfs:
            folder = PurePosixPath(pdf["path"]).parent
            manifest = json.loads(file_bytes(root, (folder / "manifest.json").as_posix()))
            if manifest["source"]["sha256"] != data["source"]["sha256"]:
                raise InputError("PDF source differs from the examined candidate")
            if reviewed != " ".join(file_bytes(root, (folder / "editorial-text.txt").as_posix()).decode("utf-8").split()):
                raise InputError("reviewed text differs from the inspected PDF text")
    else:
        delivered = "\n".join(checked(root, a).decode("utf-8") for a in data["artifacts"])
        if reviewed != " ".join(delivered.split()) or reviewed != " ".join(checked(root, data["source"]).decode("utf-8").split()):
            raise InputError("reviewed text differs from delivered source wording")
    check_path = root / "sources/sources-check.json"
    if check_path.exists():
        current = json.loads(file_bytes(root, "sources/sources-check.json"))
        sources = {row.get("source_id"): row for row in current.get("results", [])}
        for source in data["sources"]:
            if source["id"] in sources:
                row = sources[source["id"]]
                if row.get("corpus_sha256") != source["artifact"]["sha256"] or row.get("status") != "ok":
                    raise InputError(f"source refreshed or unavailable; dependent evidence stale: {source['id']}")


def diagnosis_required(history: list[dict[str, Any]]) -> bool:
    cycles = [r for r in history if r["kind"] == "cycle"]
    return (len(cycles) >= 2 and not any(r["data"]["gains"] for r in cycles[-2:])
            and not any(r["kind"] == "diagnosis" and r["seq"] > cycles[-1]["seq"] for r in history))


def freeze(swarm: Path, spec: dict[str, Any]) -> dict[str, Any]:
    root = swarm.resolve(strict=True)
    cycle = positive(spec.get("review_cycle"), "review_cycle")
    composition = identifier(spec.get("composition_id"), "composition_id")
    with connect(root, write=True) as db:
        history = events(db, root)
        policy = current_policy(history)
        verify_policy(root, policy)
        if cycle > policy["max_cycles"]:
            raise InputError("review cycle exceeds recorded ceiling; user extension required")
        closed = [r["data"]["cycle"] for r in history if r["kind"] == "cycle"]
        if cycle in closed or cycle != max(closed, default=policy["first_review_cycle"] - 1) + 1:
            raise InputError("review cycles must be consecutive and cannot reopen a closed cycle")
        if diagnosis_required(history):
            raise InputError("diagnosis_required: two cycles without substantive progress")
        artifacts = [descriptor(root, name) for name in brief_data(root)["deliverables"]]
        bundle_names = set(spec.get("bundles", []))
        presentation = None
        settings = brief_data(root).get("presentation")
        if brief_data(root).get("artifact_type") == "presentation":
            if not isinstance(settings, dict):
                raise InputError("presentation settings are required")
            folder = inside(root, settings["deck_path"]).parent
            bundle_names.update(p.relative_to(root).as_posix() for p in folder.rglob("*") if p.is_file())
            presentation = {
                "schema_version": 1, "capability": settings["capability"], "cycle": cycle, "profile": settings["profile"],
                "inputs": descriptor(root, f"reports/cycle-{cycle:02d}-inputs.json"),
                "manifest": descriptor(root, f"reports/cycle-{cycle:02d}-presentation-manifest.json"),
                "inspections": descriptor(root, f"reports/cycle-{cycle:02d}-presentation-inspections.json"),
            }
            inputs = object_file(root, presentation["inputs"])
            bundle_names.update(item["path"] for item in presentation.values() if isinstance(item, dict))
            for name in ("implementation", "profile"):
                bundle_names.add(inputs[name]["path"])
        for item in artifacts:
            if item["path"].lower().endswith(".pdf"):
                folder = inside(root, item["path"]).parent
                for name in ("manifest.json", "inspection.json", "layout.json", "editorial-text.txt"):
                    file_bytes(root, (folder / name).relative_to(root).as_posix())
                bundle_names.update(p.relative_to(root).as_posix() for p in folder.rglob("*") if p.is_file())
        if len(bundle_names) > 1000:
            raise InputError("too many composition bundle files")
        sources = []
        seen = set()
        for source in spec.get("sources", []):
            sid = identifier(source.get("id"), "source id")
            if sid in seen:
                raise InputError(f"duplicate source id: {sid}")
            seen.add(sid)
            sources.append({"id": sid, "artifact": descriptor(root, source["path"])})
        text_desc = descriptor(root, spec["text"])
        data = {"schema_version": VERSION, "evidence_contract": CONTRACT,
                "review_cycle": cycle, "composition_id": composition,
                "source": descriptor(root, spec["source"]), "text": text_desc,
                "artifacts": artifacts, "sources": sources,
                "bundles": [descriptor(root, name) for name in sorted(bundle_names)],
                "policy_sha256": digest(policy)}
        from scripts.checks.gate import COMPREHENSION_CONTRACT
        if policy.get("comprehension_contract") == COMPREHENSION_CONTRACT:
            rc_payload = build_reader_context(root, brief_data(root), cycle, composition, text_desc, artifacts)
            rc_rel = f"reports/evidence/reader-context/{composition}-c{cycle:02d}.json"
            rc_path = inside(root, rc_rel)
            if rc_path.exists():
                if json.loads(file_bytes(root, rc_rel)) != rc_payload:
                    raise InputError("immutable reader context altered")
            else:
                write_json_atomic(rc_path, rc_payload)
            data["reader_context"] = descriptor(root, rc_rel)
        if presentation:
            data["presentation"] = presentation
        key = digest(data)
        manifest_name = f"reports/evidence/candidates/{key}.json"
        manifest_path = inside(root, manifest_name)
        if manifest_path.exists():
            if json.loads(file_bytes(root, manifest_name)) != data:
                raise InputError("immutable candidate manifest altered")
        else:
            write_json_atomic(manifest_path, data)
        data["manifest"] = descriptor(root, manifest_name)
        verify_candidate(root, data)
        seq = append(db, history, "candidate", key, data)
        db.execute("INSERT OR IGNORE INTO candidates VALUES (?,?,?,?)", (key, cycle, composition, seq))
        candidate_items = [data["source"], data["text"], *artifacts, *data["bundles"]]
        if "reader_context" in data:
            candidate_items.append(data["reader_context"])
        for item in candidate_items:
            db.execute("INSERT OR IGNORE INTO artifacts VALUES (?,?,?)", (key, item["path"], item["sha256"]))
        for source in sources:
            db.execute("INSERT OR IGNORE INTO sources VALUES (?,?,?,?)",
                       (key, source["id"], source["artifact"]["path"], source["artifact"]["sha256"]))
    export(root)
    return {"candidate_id": key, **data}


def span(lines: list[str], data: dict[str, Any], label: str) -> set[int]:
    start, end = positive(data.get("start"), f"{label}.start"), positive(data.get("end"), f"{label}.end")
    if end < start or end > len(lines) or data.get("quote") != "\n".join(lines[start - 1:end]):
        raise InputError(f"{label} quote/location differs from candidate text: {start}-{end}")
    return set(range(start, end + 1))


def verify_citations(
    root: Path,
    cand: dict[str, Any],
    citations: Any,
    *,
    require_source: bool = False,
) -> None:
    if not isinstance(citations, list) or not citations or len(citations) > 20:
        raise InputError("contested resolution requires between 1 and 20 verifiable citations")
    cand_lines = checked(root, cand["text"]).decode("utf-8").splitlines()
    sources = {s["id"]: s for s in cand["sources"]}
    has_source_citation = False
    for index, cit in enumerate(citations, 1):
        if not isinstance(cit, dict) or set(cit) != {"target", "start", "end", "quote"}:
            raise InputError(f"contest citation[{index}] requires target, start, end, and quote")
        target = identifier(cit.get("target"), f"citation[{index}].target")
        if target == "candidate":
            span(cand_lines, cit, f"contest candidate citation[{index}]")
        else:
            if target not in sources:
                raise InputError(f"contest citation[{index}] references unknown source: {target}")
            source_lines = checked(root, sources[target]["artifact"]).decode("utf-8").splitlines()
            span(source_lines, cit, f"contest source citation[{index}] ({target})")
            has_source_citation = True
    if require_source and not has_source_citation:
        raise InputError("contesting a factual reviewer finding requires at least one verifiable corpus source citation")


def validate_inventory(root: Path, cand: dict[str, Any], data: dict[str, Any]) -> None:
    validate_schema(data, "inventory")
    if data.get("schema_version") != VERSION or data.get("candidate_id") is None:
        raise InputError("inventory needs schema_version 1 and candidate_id")
    lines = checked(root, cand["text"]).decode("utf-8").splitlines()
    claims = data.get("claims")
    blocks = data.get("blocks")
    if not isinstance(claims, list) or not isinstance(blocks, list) or not blocks:
        raise InputError("inventory requires claims and full-document blocks")
    claim_ids, occurrence_ids = set(), set()
    occurrence_lines = {}
    for claim in claims:
        cid = identifier(claim.get("id"), "claim id")
        if cid in claim_ids:
            raise InputError(f"duplicate claim: {cid}")
        claim_ids.add(cid)
        relation = claim.get("relation")
        if not isinstance(relation, dict) or not relation or not all(
                k in {"subject", "action", "object", "place", "time", "direction", "conditions", "result"}
                and isinstance(v, str) and v.strip() for k, v in relation.items()):
            raise InputError("claim relation needs applicable subject/action/object/place/time/direction/conditions/result")
        occurrences = claim.get("occurrences")
        if not isinstance(occurrences, list) or not occurrences:
            raise InputError(f"claim has no occurrences: {cid}")
        positions = set()
        for item in occurrences:
            oid = identifier(item.get("id"), "occurrence id")
            if oid in occurrence_ids:
                raise InputError(f"duplicate occurrence: {oid}")
            occurrence_ids.add(oid)
            positions.update(span(lines, item, oid))
        occurrence_lines[cid] = positions
    covered = set()
    block_ids = set()
    for block in blocks:
        bid = identifier(block.get("id"), "block id")
        if bid in block_ids or bid in occurrence_ids:
            raise InputError(f"duplicate block: {bid}")
        block_ids.add(bid)
        positions = span(lines, block, bid)
        if positions & covered:
            raise InputError("inventory blocks overlap")
        covered.update(positions)
        classification = block.get("classification")
        if classification not in {"factual", "non_factual", "assumption", "estimate", "interpretation"}:
            raise InputError("block classification must be factual, non_factual, assumption, estimate, interpretation")
        nonempty(block.get("justification"), "block justification")
        refs = block.get("claims")
        if not isinstance(refs, list) or len(set(refs)) != len(refs) or not set(refs) <= claim_ids:
            raise InputError("block has missing or duplicate claim references")
        if classification == "factual" and not refs:
            raise InputError(f"factual block has no claim evidence: {bid}")
        if any(not (occurrence_lines[cid] & positions) for cid in refs):
            raise InputError(f"claim reference has no occurrence in block: {bid}")
    expected = {i for i, line in enumerate(lines, 1) if line.strip()}
    if not expected <= covered:
        raise InputError(f"incomplete inventory coverage; missing lines: {sorted(expected - covered)}")
    if any(not positions <= covered for positions in occurrence_lines.values()):
        raise InputError("claim occurrence lies outside inventoried blocks")


def validate_report(root: Path, cand: dict[str, Any], inventory: dict[str, Any],
                    data: dict[str, Any], reviewer: str, policy: dict[str, Any]) -> None:
    validate_schema(data, "review")
    from scripts.checks.common import normalize_grade
    if (data.get("schema_version") != VERSION or data.get("reviewer") != reviewer
            or data.get("cycle") != cand["review_cycle"] or data.get("scope") != "full_document"
            or data.get("evidence_contract") != CONTRACT):
        raise InputError("every evidence review must declare current cycle, evidence-v1 and full_document scope")
    if data.get("candidate_id") != inventory["candidate_id"]:
        raise InputError("review examined a different candidate")
    nonempty(data.get("justification"), "full review justification")
    findings = data.get("evidence_findings")
    if not isinstance(findings, list):
        raise InputError("review requires explicit evidence_findings")
    if len({f["id"] for f in findings}) != len(findings):
        raise InputError("duplicate finding id")
    all_occurrences = ({o["id"] for c in inventory["claims"] for o in c["occurrences"]}
                       | {b["id"] for b in inventory["blocks"]})
    for finding in findings:
        identifier(finding.get("id"), "finding id")
        identifier(finding.get("family"), "defect family")
        if finding.get("severity") not in {"blocking", "minor"}:
            raise InputError("finding severity must be blocking or minor")
        for label in ("reason", "action", "evidence", "family_inspection"):
            nonempty(finding.get(label), label)
        positions = finding.get("occurrences")
        if not isinstance(positions, list) or not positions or not set(positions) <= all_occurrences:
            raise InputError("finding must enumerate valid affected occurrences")
    from scripts.checks.gate import (
        COMPREHENSION_CONTRACT,
        reject_verbatim_paraphrase,
        validate_blind_paraphrase,
    )
    goldfish = policy.get("comprehension_contract") == COMPREHENSION_CONTRACT
    if reviewer == policy["auditor"]:
        if type(data.get("critical")) is not bool:
            raise InputError("independent audit requires a boolean critical")
        if data["critical"] and not any(f["severity"] == "blocking" for f in findings):
            raise InputError("critical audit must identify a persistent blocking finding")
        if data.get("reviewed_reviewers") != policy["reviewers"]:
            raise InputError("independent auditor must inspect every assigned review")
        if data.get("inventory_complete") is not True:
            raise InputError("auditor must assess full-document inventory completeness")
        if goldfish or "paraphrase_check" in data:
            pcheck = data.get("paraphrase_check")
            if not isinstance(pcheck, dict):
                raise InputError("independent auditor requires paraphrase_check under goldfish-v1")
            if pcheck.get("reviewer") != policy.get("editorial_reviewer"):
                raise InputError("paraphrase_check.reviewer must match policy editorial_reviewer")
            if pcheck.get("brief_sha256") != descriptor(root, "brief.md")["sha256"]:
                raise InputError("paraphrase_check.brief_sha256 differs from current brief.md")
            if type(pcheck.get("matches_brief")) is not bool:
                raise InputError("paraphrase_check.matches_brief must be boolean")
            nonempty(pcheck.get("justification"), "paraphrase_check.justification")
            if not pcheck["matches_brief"] and (
                not data["critical"] or not any(f["severity"] == "blocking" for f in findings)
            ):
                raise InputError("blind paraphrase mismatch with brief.md must set critical=true and record a blocking finding")
        seen_contest = set()
        for ruling in data.get("contest_rulings", []):
            fid = ruling["finding"]
            if fid in seen_contest:
                raise InputError(f"duplicate contest_rulings entry for {fid}")
            seen_contest.add(fid)
            if ruling["target_reviewer"] not in policy["reviewers"]:
                raise InputError(f"contest_rulings references unassigned reviewer: {ruling['target_reviewer']}")
            if ruling["verdict"] == "upheld" and ruling["target_reviewer"] == policy.get("editorial_reviewer"):
                raise InputError("comprehension or clarity findings from editorial_reviewer cannot be contested")
            if INTENT_EXCUSE_RE.search(ruling["justification"]):
                raise InputError("author intent ('foi intencional' / 'this was intentional') is never valid contest evidence")
            verify_citations(
                root,
                cand,
                ruling["citations"],
                require_source=(ruling["verdict"] == "upheld" and ruling["target_reviewer"] == policy["factual_reviewer"]),
            )
        seen_escalation = set()
        for ruling in data.get("escalation_rulings", []):
            fid = ruling["finding"]
            if fid in seen_escalation:
                raise InputError(f"duplicate escalation_rulings entry for {fid}")
            seen_escalation.add(fid)
            if ruling["target_reviewer"] not in policy["reviewers"]:
                raise InputError(f"escalation_rulings references unassigned reviewer: {ruling['target_reviewer']}")
            if INTENT_EXCUSE_RE.search(ruling["justification"]):
                raise InputError("author intent ('foi intencional' / 'this was intentional') is never valid escalation justification")
        return
    rows = data.get("topics")
    if not isinstance(rows, list) or not rows:
        raise InputError("individual review requires topics")
    seen = set()
    for row in rows:
        tid = nonempty(row.get("topic"), "topic")
        if tid in seen or row.get("carry_forward"):
            raise InputError("duplicate or inherited topic cannot certify full-document review")
        seen.add(tid)
        grade = normalize_grade(row.get("grade"))
        nonempty(row.get("justification"), "topic justification")
        if not isinstance(row.get("action"), str) or (grade not in {"A", "A+"} and not row["action"].strip()):
            raise InputError("blocking topic requires actionable correction")
        if grade not in {"A", "A+"} and not (
                any(f["severity"] == "blocking" for f in findings)
                or any(f.get("severity") == "blocking" for f in data.get("editorial", {}).get("findings", []))):
            raise InputError("blocking topic must identify a material finding, not just lower its grade")
    if goldfish and reviewer == policy.get("editorial_reviewer"):
        editorial = data.get("editorial")
        if not isinstance(editorial, dict):
            raise InputError("editorial reviewer under goldfish-v1 must include structured editorial review")
        paraphrase_blocked = validate_blind_paraphrase(editorial, reviewer, required=True)
        paraphrase = editorial["blind_paraphrase"]
        if paraphrase.get("reader_context") != cand.get("reader_context"):
            raise InputError("editorial blind_paraphrase must bind the exact frozen reader_context")
        reviewed_source = " ".join(checked(root, cand["text"]).decode("utf-8").split())
        reject_verbatim_paraphrase(paraphrase, reviewed_source)
        if paraphrase_blocked and not (
            any(f["severity"] == "blocking" for f in findings)
            or any(f.get("severity") == "blocking" for f in editorial.get("findings", []))
        ):
            raise InputError("unconnected_sections in blind_paraphrase requires a blocking finding")
    if reviewer != policy["factual_reviewer"]:
        return
    if data.get("inventory_complete") is not True:
        raise InputError("factual reviewer must inspect inventory completeness against the full candidate")
    checks = data.get("block_checks")
    if not isinstance(checks, list) or {b.get("id") for b in checks} != {b["id"] for b in inventory["blocks"]} or len(checks) != len(inventory["blocks"]):
        raise InputError("factual reviewer must check every block classification")
    for block in checks:
        if type(block.get("accepted")) is not bool:
            raise InputError("block review requires boolean accepted")
        nonempty(block.get("justification"), "block review justification")
    assessments = data.get("assessments")
    claims = {c["id"]: c for c in inventory["claims"]}
    if (not isinstance(assessments, list) or {a.get("claim") for a in assessments} != set(claims)
            or len(assessments) != len(claims)):
        raise InputError("factual assessment must cover every claim")
    sources = {s["id"]: s for s in cand["sources"]}
    for assessment in assessments:
        claim = claims[assessment["claim"]]
        if assessment.get("conclusion") not in {"supported", "contradicted", "uncertain"}:
            raise InputError("assessment conclusion must be supported, contradicted or uncertain")
        nonempty(assessment.get("justification"), "factual justification")
        nonempty(assessment.get("consistency"), "cross-occurrence consistency judgment")
        if assessment.get("occurrences") != [o["id"] for o in claim["occurrences"]]:
            raise InputError("assessment must inspect every occurrence of the claim")
        refs = assessment.get("evidence")
        if not isinstance(refs, list) or not refs:
            raise InputError("each factual assessment requires specific source excerpts")
        for ref in refs:
            if ref.get("source") not in sources:
                raise InputError("factual assessment references an unknown source")
            source_lines = checked(root, sources[ref["source"]]["artifact"]).decode("utf-8").splitlines()
            span(source_lines, ref, "source excerpt")


def register(swarm: Path, kind: str, key: str, path: str | None = None,
             reviewer: str | None = None) -> dict[str, Any]:
    root = swarm.resolve(strict=True)
    if kind not in {"inventory", "dispatch", "review", "resolution"}:
        raise InputError("register kind must be inventory, dispatch, review or resolution")
    with connect(root, write=True) as db:
        history = events(db, root)
        policy = current_policy(history)
        verify_policy(root, policy)
        cand = candidate(history, key)
        verify_candidate(root, cand)
        if any(r["kind"] == "cycle" and r["data"]["cycle"] == cand["review_cycle"] for r in history):
            raise InputError("closed review cycle is immutable")
        event_id = key
        if kind == "dispatch":
            if reviewer not in [*policy["reviewers"], policy["auditor"]]:
                raise InputError(f"reviewer must be one of {', '.join([*policy['reviewers'], policy['auditor']])}")
            if reviewer == policy["auditor"]:
                for name in policy["reviewers"]:
                    find(history, "review", f"{key}:{name}")
            payload = {"candidate_id": key, "reviewer": reviewer,
                       "agent": policy["agents"][reviewer]}
            from scripts.checks.gate import COMPREHENSION_CONTRACT
            if policy.get("comprehension_contract") == COMPREHENSION_CONTRACT:
                if reviewer == policy.get("editorial_reviewer"):
                    payload["isolation"] = COMPREHENSION_CONTRACT
                    payload["fresh_task_required"] = True
                    payload["reader_context"] = cand["reader_context"]
                    payload["forbidden_paths"] = [
                        "brief.md",
                        "reports/evidence-policy.json",
                        "sources/sources-index.md",
                    ]
                else:
                    payload["isolation"] = "full_brief"
            event_id = f"{key}:{reviewer}"
        else:
            src_artifact = descriptor(root, nonempty(path, "record path"))
            data = object_file(root, src_artifact)
            if data.get("candidate_id") != key:
                raise InputError("record candidate_id differs from frozen candidate")
            artifact = src_artifact
            if kind == "inventory":
                validate_inventory(root, cand, data)
                inv_snap_rel = f"reports/evidence/inventories/{key}.json"
                inv_snap_path = inside(root, inv_snap_rel)
                if not inv_snap_path.is_file():
                    write_json_atomic(inv_snap_path, data)
                artifact = descriptor(root, inv_snap_rel)
            elif kind == "review":
                reviewer = data.get("reviewer")
                if reviewer not in [*policy["reviewers"], policy["auditor"]]:
                    raise InputError(f"reviewer must be one of {', '.join([*policy['reviewers'], policy['auditor']])}")
                find(history, "dispatch", f"{key}:{reviewer}")
                inventory = find(history, "inventory", key)["record"]
                validate_report(root, cand, inventory, data, reviewer, policy)
                if reviewer == policy["auditor"]:
                    refs = data.get("review_artifacts")
                    expected = [find(history, "review", f"{key}:{r}")["artifact"] for r in policy["reviewers"]]
                    if refs != expected:
                        raise InputError("auditor must bind the actual original reviewer files")
                event_id = f"{key}:{reviewer}"
            else:
                event_id = verify_resolution(root, history, key, data, policy)
            payload = {"artifact": artifact, "record": data}
        seq = append(db, history, kind, event_id, payload)
        if kind == "inventory":
            for claim in data["claims"]:
                db.execute("INSERT OR IGNORE INTO claims VALUES (?,?,?)", (key, claim["id"], canonical(claim["relation"])))
                for item in claim["occurrences"]:
                    db.execute("INSERT OR IGNORE INTO occurrences VALUES (?,?,?,?,?)",
                               (key, item["id"], claim["id"], item["start"], item["end"]))
        if kind == "review" and reviewer == policy["factual_reviewer"]:
            for item in data["assessments"]:
                db.execute("INSERT OR IGNORE INTO assessments VALUES (?,?,?,?,?)",
                           (key, item["claim"], reviewer, item["conclusion"], seq))
    export(root)
    return payload


def resolution_closes_finding(record: dict[str, Any]) -> bool:
    status = record.get("status", "fixed")
    if status in {"fixed", "contested"}:
        return True
    if status == "escalate":
        return record.get("decision_state") == "decided" and record.get("effect") == "waive_finding"
    return False


def open_findings(history: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    findings = {}
    for row in history:
        if row["kind"] == "review":
            for item in row["data"]["record"]["evidence_findings"]:
                if item["severity"] == "blocking":
                    previous = findings.get(item["id"])
                    if previous and previous["family"] != item["family"]:
                        raise InputError("stable finding ID cannot change defect family")
                    findings[item["id"]] = {
                        **item, "reviewer": previous["reviewer"] if previous else row["data"]["record"]["reviewer"],
                        "occurrences": sorted(set(item["occurrences"]) | set(previous["occurrences"] if previous else [])),
                    }
        elif row["kind"] == "resolution":
            record = row["data"]["record"]
            if resolution_closes_finding(record):
                findings.pop(record["finding"], None)
    return findings


def pending_escalations(history: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    pending: dict[str, dict[str, Any]] = {}
    for row in history:
        if row["kind"] != "resolution":
            continue
        record = row["data"]["record"]
        fid = record["finding"]
        status = record.get("status", "fixed")
        if status == "escalate" and record.get("decision_state") == "pending_user":
            pending[fid] = record
        elif status == "escalate" and record.get("decision_state") == "decided":
            pending.pop(fid, None)
        elif resolution_closes_finding(record):
            pending.pop(fid, None)
    return pending


def special_resolutions(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for row in history:
        if row["kind"] != "resolution":
            continue
        rec = row["data"]["record"]
        status = rec.get("status", "fixed")
        if status in {"contested", "escalate"}:
            entry: dict[str, Any] = {
                "finding": rec["finding"],
                "status": status,
                "candidate_id": rec["candidate_id"],
                "reviewer": rec.get("reviewer", ""),
            }
            if status == "escalate":
                entry["decision_state"] = rec.get("decision_state", "")
                if "effect" in rec:
                    entry["effect"] = rec["effect"]
            items.append(entry)
    return items


def verify_resolution(
    root: Path,
    history: list[dict[str, Any]],
    key: str,
    data: dict[str, Any],
    policy: dict[str, Any],
) -> str:
    finding_id = identifier(data.get("finding"), "finding")
    finding = open_findings(history).get(finding_id)
    if not finding:
        raise InputError("resolution requires an open blocking finding")
    status = data.get("status", "fixed")
    if status not in {"fixed", "contested", "escalate"}:
        raise InputError("resolution status must be fixed, contested or escalate")

    if status == "fixed":
        if data.get("reviewer") not in {finding["reviewer"], policy["auditor"]}:
            raise InputError("only finding owner or independent auditor may resolve it")
        for label in ("correction", "reassessment", "family_inspection"):
            nonempty(data.get(label), label)
        record = find(history, "review", f"{key}:{data['reviewer']}")["record"]
        if any(item["id"] == finding_id for item in record["evidence_findings"]):
            raise InputError("current reassessment still reports the finding")
        factual = find(history, "review", f"{key}:{policy['factual_reviewer']}")["record"]
        if any(a["conclusion"] != "supported" for a in factual["assessments"]) or any(
                not b["accepted"] for b in factual["block_checks"]):
            raise InputError("family cannot close while current factual reassessment still rejects evidence")
        if record.get("critical"):
            raise InputError("family cannot close while independent auditor still records a veto")
        inventory = find(history, "inventory", key)["record"]
        occurrences = ({o["id"] for c in inventory["claims"] for o in c["occurrences"]}
                       | {b["id"] for b in inventory["blocks"]})
        checked_occurrences = data.get("occurrences")
        if not isinstance(checked_occurrences, list) or set(checked_occurrences) != occurrences:
            raise InputError("family resolution must enumerate the whole current occurrence inventory")
        if not set(data.get("previous_occurrences", [])) >= set(finding["occurrences"]):
            raise InputError("resolution must account for every previously affected occurrence")
        return f"{key}:{finding_id}"

    if status == "contested":
        if finding["reviewer"] == policy.get("editorial_reviewer"):
            raise InputError(
                "comprehension or clarity findings from editorial_reviewer cannot be contested; clarify the text or use escalate"
            )
        if data.get("reviewer") != policy["auditor"]:
            raise InputError("contested resolution must be adjudicated by the independent auditor")
        rationale = nonempty(data.get("rationale"), "rationale")
        if len(rationale.strip()) < 30:
            raise InputError("contested rationale must be at least 30 characters")
        if INTENT_EXCUSE_RE.search(rationale):
            raise InputError(
                "author intent ('foi intencional' / 'this was intentional') is never valid contest evidence; cite verifiable text or source lines"
            )
        cand = candidate(history, key)
        verify_citations(
            root,
            cand,
            data.get("citations"),
            require_source=(finding["reviewer"] == policy["factual_reviewer"]),
        )
        audit_record = find(history, "review", f"{key}:{policy['auditor']}")["record"]
        if audit_record.get("critical") or any(item["id"] == finding_id for item in audit_record["evidence_findings"]):
            raise InputError("contested resolution cannot close while independent auditor records a veto or repeats the finding")
        rulings = [r for r in audit_record.get("contest_rulings", []) if r.get("finding") == finding_id]
        if (
            len(rulings) != 1
            or rulings[0].get("verdict") != "upheld"
            or rulings[0].get("target_reviewer") != finding["reviewer"]
        ):
            raise InputError("contested resolution requires an upheld contest_rulings entry from the independent auditor")
        if rulings[0].get("citations") != data.get("citations"):
            raise InputError("contested resolution citations must match the auditor's upheld contest_rulings citations")
        return f"{key}:{finding_id}"

    # status == "escalate"
    decision_state = data.get("decision_state")
    if decision_state not in {"pending_user", "decided"}:
        raise InputError("escalate resolution requires decision_state pending_user or decided")
    nonempty(data.get("missing_decision"), "missing_decision")
    nonempty(data.get("ask_user_question"), "ask_user_question")
    allowed_actors = {*policy["reviewers"], policy["auditor"], "coordinator"}
    if decision_state == "pending_user":
        if data.get("reviewer") not in allowed_actors:
            raise InputError("escalate pending_user must name an assigned reviewer, auditor or coordinator")
        return f"{key}:{finding_id}:escalate_pending"

    nonempty(data.get("user_decision"), "user_decision")
    effect = data.get("effect")
    if effect not in {"update_document", "waive_finding"}:
        raise InputError("decided escalate resolution requires effect update_document or waive_finding")
    if effect == "update_document":
        if data.get("reviewer") not in allowed_actors:
            raise InputError("escalate update_document must name an assigned reviewer, auditor or coordinator")
        return f"{key}:{finding_id}:escalate_decided"

    if data.get("reviewer") != policy["auditor"]:
        raise InputError("escalate waive_finding must be confirmed by the independent auditor")
    if finding["reviewer"] == policy["factual_reviewer"]:
        factual = find(history, "review", f"{key}:{policy['factual_reviewer']}")["record"]
        if any(a["conclusion"] != "supported" for a in factual["assessments"]) or any(
            not b["accepted"] for b in factual["block_checks"]
        ):
            raise InputError("cannot waive unsupported factual claims via escalate; update the document or sources")
    audit_record = find(history, "review", f"{key}:{policy['auditor']}")["record"]
    if audit_record.get("critical") or any(item["id"] == finding_id for item in audit_record["evidence_findings"]):
        raise InputError("escalate waive_finding cannot close while independent auditor records a veto or repeats the finding")
    rulings = [r for r in audit_record.get("escalation_rulings", []) if r.get("finding") == finding_id]
    if (
        len(rulings) != 1
        or rulings[0].get("verdict") != "accepted_out_of_band"
        or rulings[0].get("target_reviewer") != finding["reviewer"]
    ):
        raise InputError(
            "escalate waive_finding requires an accepted_out_of_band escalation_rulings entry from the independent auditor"
        )
    return f"{key}:{finding_id}"


def candidate_overrides(
    history: list[dict[str, Any]],
    key: str,
    originals: list[dict[str, Any]],
    policy: dict[str, Any],
) -> dict[str, Any]:
    audit_rows = [r for r in history if r["kind"] == "review" and r["id"] == f"{key}:{policy['auditor']}"]
    if not audit_rows:
        return {"topics": {}, "claims": set(), "blocks": set()}
    audit_record = audit_rows[0]["data"]["record"]
    if audit_record.get("critical"):
        return {"topics": {}, "claims": set(), "blocks": set()}

    contest_by_finding = {
        r["finding"]: r for r in audit_record.get("contest_rulings", []) if r.get("verdict") == "upheld"
    }
    escalation_by_finding = {
        r["finding"]: r for r in audit_record.get("escalation_rulings", []) if r.get("verdict") == "accepted_out_of_band"
    }
    still_open = open_findings(history)
    originals_by_rev = {rep["reviewer"]: rep for rep in originals}

    overturned_findings_by_rev: dict[str, set[str]] = {}
    pending_ruling_entries: list[tuple[str, str, str, dict[str, Any]]] = []
    for row in history:
        if row["kind"] != "resolution":
            continue
        rec = row["data"]["record"]
        if rec.get("candidate_id") != key:
            continue
        fid = rec["finding"]
        status = rec.get("status", "fixed")
        if status == "contested" and fid in contest_by_finding:
            ruling = contest_by_finding[fid]
            rev = ruling["target_reviewer"]
            overturned_findings_by_rev.setdefault(rev, set()).add(fid)
            pending_ruling_entries.append((rev, fid, "contested", ruling))
        elif (
            status == "escalate"
            and rec.get("decision_state") == "decided"
            and rec.get("effect") == "waive_finding"
            and fid in escalation_by_finding
        ):
            ruling = escalation_by_finding[fid]
            rev = ruling["target_reviewer"]
            overturned_findings_by_rev.setdefault(rev, set()).add(fid)
            pending_ruling_entries.append((rev, fid, "escalate", ruling))

    topics_map: dict[tuple[str, str], dict[str, str]] = {}
    claims_set: set[str] = set()
    blocks_set: set[str] = set()

    for rev, fid, mode, ruling in pending_ruling_entries:
        rep = originals_by_rev.get(rev)
        if rep is None:
            continue
        rev_blocking_ids = {
            f["id"] for f in rep.get("evidence_findings", []) if f.get("severity") == "blocking"
        }
        has_editorial_blocking = any(
            f.get("severity") == "blocking" for f in rep.get("editorial", {}).get("findings", [])
        )
        has_open_owned = any(f["reviewer"] == rev for f in still_open.values())
        if (rev_blocking_ids - overturned_findings_by_rev.get(rev, set())) or has_editorial_blocking or has_open_owned:
            continue
        for tid in ruling.get("overturned_topics", []):
            topics_map[(tid, rev)] = {
                "overturned_reviewer": rev,
                "finding": fid,
                "status": mode,
                "adjudicated_by": policy["auditor"],
            }
        if mode == "contested" and rev == policy["factual_reviewer"]:
            claims_set.update(ruling.get("overturned_claims", []))
            blocks_set.update(ruling.get("overturned_blocks", []))

    return {"topics": topics_map, "claims": claims_set, "blocks": blocks_set}


def compute_consolidated_topics(
    originals: list[dict[str, Any]],
    overrides: dict[str, Any],
) -> list[dict[str, Any]]:
    from scripts.checks.common import GRADE_INDEX, normalize_grade
    topic_order: list[str] = []
    by_topic: dict[str, list[tuple[str, str]]] = {}
    for report in originals:
        for item in report["topics"]:
            tid = item["topic"]
            if tid not in by_topic:
                topic_order.append(tid)
                by_topic[tid] = []
            by_topic[tid].append((normalize_grade(item["grade"]), report["reviewer"]))
    consolidated: list[dict[str, Any]] = []
    for tid in topic_order:
        rows = by_topic[tid]
        if len(rows) != len(originals):
            raise InputError(f"every assigned reviewer must inspect topic {tid}")
        effective_rows: list[tuple[str, str]] = []
        applied_overrides: list[dict[str, str]] = []
        for grade, rev in rows:
            ov = overrides["topics"].get((tid, rev))
            if ov is not None and grade not in {"A", "A+"}:
                applied_overrides.append({**ov, "original_grade": grade})
            else:
                effective_rows.append((grade, rev))
        if not effective_rows:
            effective_rows = rows
            applied_overrides = []
        min_grade, min_rev = min(effective_rows, key=lambda pair: GRADE_INDEX[pair[0]])
        entry: dict[str, Any] = {
            "topico": tid,
            "nota_minima": min_grade,
            "revisor_da_minima": min_rev,
            "bloqueia": min_grade not in {"A", "A+"},
        }
        if applied_overrides:
            entry["contested_override"] = applied_overrides
        consolidated.append(entry)
    return consolidated


def select(swarm: Path, cycle: int, key: str | None = None) -> tuple[str, dict[str, Any]]:
    root = swarm.resolve(strict=True)
    with connect(root) as db:
        history = events(db, root)
        choices = [r for r in history if r["kind"] == "candidate" and r["data"]["review_cycle"] == cycle]
        if key:
            choices = [r for r in choices if r["id"] == key]
        if len(choices) != 1:
            raise InputError("select the exact candidate_id; missing or ambiguous composition")
        return choices[0]["id"], choices[0]["data"]


def verify_delivery(data: dict[str, Any], swarm: Path) -> dict[str, Any]:
    root = swarm.resolve(strict=True)
    if data.get("evidence_contract") != CONTRACT:
        raise InputError("current review must use the adopted evidence-v1 contract")
    key = nonempty(data.get("candidate_id"), "review candidate_id")
    with connect(root) as db:
        history = events(db, root)
        policy = current_policy(history)
        verify_policy(root, policy)
        cand = candidate(history, key)
        recovery = [r for r in history if r["kind"] == "recovery"]
        candidate_event = next(r for r in history if r["kind"] == "candidate" and r["id"] == key)
        if recovery and candidate_event["seq"] < recovery[-1]["seq"]:
            raise InputError("recovery requires a new candidate and full re-audit; old approval is not restored")
        if data["cycle"] != cand["review_cycle"] or data["max_cycles"] != policy["max_cycles"]:
            raise InputError("review cycle/policy differs from the frozen candidate")
        if {a["path"] for a in cand["artifacts"]} != set(brief_data(root)["deliverables"]):
            raise InputError("brief deliverables differ from the examined candidate")
        verify_candidate(root, cand)
        inv = find(history, "inventory", key)
        checked(root, inv["artifact"])
        validate_inventory(root, cand, inv["record"])
        blocked = []
        originals = []
        for name in policy["reviewers"]:
            payload = find(history, "review", f"{key}:{name}")
            if object_file(root, payload["artifact"]) != payload["record"]:
                raise InputError("review record differs from original response")
            validate_report(root, cand, inv["record"], payload["record"], name, policy)
            originals.append(payload["record"])
        if cand.get("presentation") and data.get("presentation") != {
                **cand["presentation"], "reviews": [
                    row for report in originals for row in report.get("presentation_reviews", [])]}:
            raise InputError("presentation consolidation differs from frozen artifacts or original reviews")
        overrides = candidate_overrides(history, key, originals, policy)
        expected_topics = compute_consolidated_topics(originals, overrides)
        expected_by_id = {item["topico"]: item for item in expected_topics}
        matrix = {item.get("topico"): item for item in data["topics"]}
        if set(matrix) != set(expected_by_id) or len(matrix) != len(data["topics"]):
            raise InputError("consolidated topics differ from original reviewers")
        for topic, exp in expected_by_id.items():
            minimum = matrix[topic]
            valid_reviewers = {
                r["reviewer"]
                for r in originals
                for item in r["topics"]
                if item["topic"] == topic
                and item["grade"] == exp["nota_minima"]
                and (topic, r["reviewer"]) not in overrides["topics"]
            } or {
                r["reviewer"]
                for r in originals
                for item in r["topics"]
                if item["topic"] == topic and item["grade"] == exp["nota_minima"]
            }
            if (
                minimum.get("nota_minima") != exp["nota_minima"]
                or minimum.get("bloqueia") != exp["bloqueia"]
                or minimum.get("revisor_da_minima") not in valid_reviewers
                or minimum.get("contested_override") != exp.get("contested_override")
            ):
                raise InputError(f"consolidation changed the original minimum for {topic}")
        for report in originals:
            if report["reviewer"] == policy["factual_reviewer"]:
                for block in report["block_checks"]:
                    if not block["accepted"] and block["id"] not in overrides["blocks"]:
                        blocked.append({"kind": "evidence", "name": block["id"], "grade": ""})
                for item in report["assessments"]:
                    if item["conclusion"] != "supported" and item["claim"] not in overrides["claims"]:
                        blocked.append({"kind": "evidence", "name": item["claim"], "grade": ""})
        from scripts.checks.gate import COMPREHENSION_CONTRACT
        if (
            policy.get("comprehension_contract") == COMPREHENSION_CONTRACT
            and data.get("comprehension_contract") != COMPREHENSION_CONTRACT
        ):
            raise InputError("consolidated review must preserve adopted goldfish-v1 comprehension_contract")
        audit_rows = [r for r in history if r["kind"] == "review" and r["id"] == f"{key}:{policy['auditor']}"]
        if not audit_rows:
            blocked.append({"kind": "evidence", "name": "audit_pending", "grade": ""})
            if data["rubberduck"] != {"critico": False, "achados": [], "status": "audit_pending"}:
                raise InputError("missing independent audit must remain audit_pending")
        else:
            audit = audit_rows[0]["data"]
            checked(root, audit["artifact"])
            validate_report(root, cand, inv["record"], audit["record"], policy["auditor"], policy)
            expected = [find(history, "review", f"{key}:{r}")["artifact"] for r in policy["reviewers"]]
            if audit["record"]["review_artifacts"] != expected:
                raise InputError("audit references different reviewer artifacts")
            expected_duck: dict[str, Any] = {
                "critico": audit["record"]["critical"],
                "achados": audit["record"]["evidence_findings"],
            }
            if "paraphrase_check" in audit["record"]:
                expected_duck["paraphrase_check"] = audit["record"]["paraphrase_check"]
            if "contest_rulings" in audit["record"]:
                expected_duck["contest_rulings"] = audit["record"]["contest_rulings"]
            if "escalation_rulings" in audit["record"]:
                expected_duck["escalation_rulings"] = audit["record"]["escalation_rulings"]
            if data["rubberduck"] != expected_duck:
                raise InputError("consolidation differs from independent audit")
            if audit["record"]["critical"]:
                blocked.append({"kind": "evidence", "name": "critical audit", "grade": ""})
            if audit["record"].get("paraphrase_check", {}).get("matches_brief") is False:
                blocked.append({"kind": "evidence", "name": "blind_paraphrase_mismatch", "grade": ""})
        # Validate historical original records, but not their obsolete delivery files.
        for row in history:
            if row["kind"] in {"inventory", "review", "resolution"}:
                checked(root, row["data"]["artifact"])
                if row["kind"] == "resolution" and object_file(root, row["data"]["artifact"]) != row["data"]["record"]:
                    raise InputError("resolution record differs from original artifact")
        findings = open_findings(history)
        blocked.extend({"kind": "evidence", "name": fkey, "grade": ""} for fkey in findings)
        for esc_id in sorted(pending_escalations(history)):
            blocked.append({"kind": "evidence", "name": f"escalate_pending:{esc_id}", "grade": ""})
        if diagnosis_required(history):
            blocked.append({"kind": "evidence", "name": "diagnosis_required", "grade": ""})
        summary = {"candidate_id": key, "review_cycle": cand["review_cycle"],
                   "composition_id": cand["composition_id"], "claims": len(inv["record"]["claims"]),
                   "blocks": len(inv["record"]["blocks"]), "open_findings": len(findings),
                   "audit": "recorded" if audit_rows else "audit_pending",
                   "diagnosis_required": diagnosis_required(history), "blocked": blocked}
        special = special_resolutions(history)
        if special:
            summary["special_resolutions"] = special
        return summary


def consolidate(swarm: Path, cycle: int, key: str | None = None) -> dict[str, Any]:
    root = swarm.resolve(strict=True)
    key, cand = select(root, cycle, key)
    with connect(root) as db:
        history = events(db, root)
        policy = current_policy(history)
        records = [find(history, "review", f"{key}:{r}")["record"] for r in policy["reviewers"]]
        audit_rows = [r for r in history if r["kind"] == "review" and r["id"] == f"{key}:{policy['auditor']}"]
    overrides = candidate_overrides(history, key, records, policy)
    consolidated_topics = compute_consolidated_topics(records, overrides)
    from scripts.checks.gate import COMPREHENSION_CONTRACT
    brief = brief_data(root)
    result = {"schema_version": 1, "skill_version": brief["skill_version"], "mode": brief.get("mode", "document"),
              "quality_contract": "editorial-v1", "evidence_contract": CONTRACT, "candidate_id": key,
              "cycle": cycle, "max_cycles": policy["max_cycles"], "topics": consolidated_topics,
              "rubberduck": {"critico": False, "achados": [], "status": "audit_pending"}}
    if policy.get("comprehension_contract") == COMPREHENSION_CONTRACT:
        result["comprehension_contract"] = COMPREHENSION_CONTRACT
    for record in records:
        if record["reviewer"] == brief.get("editorial_reviewer"):
            result["editorial"] = record.get("editorial")
    if cand.get("presentation"):
        result["presentation"] = {**cand["presentation"], "reviews": [
            row for record in records for row in record.get("presentation_reviews", [])]}
    if audit_rows:
        audit = audit_rows[0]["data"]["record"]
        duck_out: dict[str, Any] = {"critico": audit["critical"], "achados": audit["evidence_findings"]}
        if "paraphrase_check" in audit:
            duck_out["paraphrase_check"] = audit["paraphrase_check"]
        if "contest_rulings" in audit:
            duck_out["contest_rulings"] = audit["contest_rulings"]
        if "escalation_rulings" in audit:
            duck_out["escalation_rulings"] = audit["escalation_rulings"]
        result["rubberduck"] = duck_out
    verify_delivery(result, root)
    return result


def close_cycle(swarm: Path, review_path: str) -> dict[str, Any]:
    root = swarm.resolve(strict=True)
    artifact = descriptor(root, review_path)
    review = object_file(root, artifact)
    from scripts.checks.gate import evaluate_current
    decision = evaluate_current(review, root)
    with connect(root, write=True) as db:
        history = events(db, root)
        key = review["candidate_id"]
        factual = find(history, "review", f"{key}:{current_policy(history)['factual_reviewer']}")["record"]
        inventory = find(history, "inventory", key)["record"]
        signatures = sorted(digest([claim["relation"], sorted(
            ({"source": ref["source"], "quote": ref["quote"]} for ref in assessment["evidence"]),
            key=canonical)])
                            for claim in inventory["claims"] for assessment in factual["assessments"]
                            if assessment["claim"] == claim["id"] and assessment["conclusion"] == "supported")
        resolved = sorted(
            r["data"]["record"]["finding"]
            for r in history
            if r["kind"] == "resolution" and resolution_closes_finding(r["data"]["record"])
        )
        decided_escalations = sorted(
            r["id"]
            for r in history
            if r["kind"] == "resolution"
            and r["data"]["record"].get("status") == "escalate"
            and r["data"]["record"].get("decision_state") == "decided"
        )
        prior = [r["data"] for r in history if r["kind"] == "cycle" and r["data"]["cycle"] < review["cycle"]]
        previous = prior[-1] if prior else {"supported": [], "resolved": [], "decided_escalations": []}
        gains = {
            "new_supported_evidence": len(set(signatures) - set(previous["supported"])),
            "resolved_findings": len(set(resolved) - set(previous["resolved"])),
            "decided_escalations": len(set(decided_escalations) - set(previous.get("decided_escalations", []))),
        }
        payload = {"cycle": review["cycle"], "candidate_id": key, "review": artifact,
                   "outcome": decision["outcome"], "result": decision, "supported": signatures, "resolved": resolved,
                   "decided_escalations": decided_escalations,
                   "gains": {k: v for k, v in gains.items() if v}}
        append(db, history, "cycle", str(review["cycle"]), payload)
    export(root)
    return payload


def historical_result(data: dict[str, Any], swarm: Path) -> dict[str, Any]:
    root = swarm.resolve(strict=True)
    with connect(root) as db:
        history = events(db, root)
    closed = find(history, "cycle", str(data["cycle"]))
    if closed["candidate_id"] != data.get("candidate_id") or object_file(root, closed["review"]) != data:
        raise InputError("historical review differs from its recorded cycle closure")
    return json.loads(canonical(closed["result"]))


def control(swarm: Path, kind: str, path: str) -> dict[str, Any]:
    root = swarm.resolve(strict=True)
    with connect(root, write=True) as db:
        history = events(db, root)
        data = object_file(root, descriptor(root, path))
        if kind == "diagnosis":
            if not diagnosis_required(history):
                raise InputError("diagnosis is only required after two cycles without progress")
            for label in ("causes", "changed_approach"):
                nonempty(data.get(label), label)
            key = str(max(r["data"]["cycle"] for r in history if r["kind"] == "cycle"))
        elif kind == "extension":
            if data.get("actor") != "user" or data.get("decision") != "extend":
                raise InputError("cycle extension requires explicit recorded user authorization")
            nonempty(data.get("reason"), "extension reason")
            nonempty(data.get("authorization"), "user authorization evidence")
            maximum = positive(data.get("max_cycles"), "max_cycles")
            if maximum <= current_policy(history)["max_cycles"]:
                raise InputError("extension must increase the recorded ceiling")
            key = str(maximum)
        else:
            raise InputError("control must be diagnosis or extension")
        append(db, history, kind, key, data)
    export(root)
    return data


def recover(swarm: Path, path: str, reason: str) -> dict[str, Any]:
    """Restore a checked export explicitly; never restore its approval."""
    root = swarm.resolve(strict=True)
    nonempty(reason, "recovery reason")
    exported = object_file(root, descriptor(root, path))
    if exported.get("schema_version") != VERSION or exported.get("evidence_contract") != CONTRACT:
        raise InputError("unsupported recovery export")
    history = exported.get("events")
    if not isinstance(history, list) or not history:
        raise InputError("recovery export has no events")
    _, policy_path = ledger_paths(root)
    policy = json.loads(file_bytes(root, policy_path.relative_to(root).as_posix()))
    previous = digest(policy)
    for index, row in enumerate(history, 1):
        if (row.get("seq") != index or row.get("sha256") !=
                digest([index, row["kind"], row["id"], row["data"], previous])):
            raise InputError(f"recovery export journal altered at event {index}")
        previous = row["sha256"]
        if row["kind"] in {"inventory", "review", "resolution"}:
            checked(root, row["data"]["artifact"])
    if history[0]["kind"] != "policy" or history[0]["data"] != policy:
        raise InputError("recovery export differs from adopted policy")
    verify_policy(root, current_policy(history))
    database, _ = ledger_paths(root)
    pending = inside(root, "reports/evidence/recovering.sqlite")
    if pending.exists():
        raise InputError("previous incomplete recovery exists; preserve and inspect it explicitly")
    try:
        with closing(sqlite3.connect(pending)) as db, db:
            db.executescript(SCHEMA)
            for row in history:
                prior = digest(policy) if row["seq"] == 1 else history[row["seq"] - 2]["sha256"]
                db.execute("INSERT INTO events VALUES (?,?,?,?,?,?)",
                           (row["seq"], row["kind"], row["id"], canonical(row["data"]), prior, row["sha256"]))
                data = row["data"]
                if row["kind"] == "candidate":
                    key = row["id"]
                    db.execute("INSERT INTO candidates VALUES (?,?,?,?)",
                               (key, data["review_cycle"], data["composition_id"], row["seq"]))
                    candidate_items = [data["source"], data["text"], *data["artifacts"], *data["bundles"]]
                    if "reader_context" in data:
                        candidate_items.append(data["reader_context"])
                    for item in candidate_items:
                        db.execute("INSERT OR IGNORE INTO artifacts VALUES (?,?,?)", (key, item["path"], item["sha256"]))
                    for source in data["sources"]:
                        db.execute("INSERT INTO sources VALUES (?,?,?,?)",
                                   (key, source["id"], source["artifact"]["path"], source["artifact"]["sha256"]))
                elif row["kind"] == "inventory":
                    key = row["id"]
                    for claim in data["record"]["claims"]:
                        db.execute("INSERT INTO claims VALUES (?,?,?)", (key, claim["id"], canonical(claim["relation"])))
                        for item in claim["occurrences"]:
                            db.execute("INSERT INTO occurrences VALUES (?,?,?,?,?)",
                                       (key, item["id"], claim["id"], item["start"], item["end"]))
                elif row["kind"] == "review":
                    record = data["record"]
                    for item in record.get("assessments", []):
                        db.execute("INSERT INTO assessments VALUES (?,?,?,?,?)",
                                   (record["candidate_id"], item["claim"], record["reviewer"], item["conclusion"], row["seq"]))
            append(db, history, "recovery", str(len(history)),
                   {"export": descriptor(root, path), "reason": reason, "approval_restored": False})
        if database.exists():
            archive = database.with_name(f"evidence-before-recovery-{hashlib.sha256(database.read_bytes()).hexdigest()}.sqlite")
            if archive.exists():
                raise InputError("recovery archive already exists; do not overwrite history")
            database.rename(archive)
        pending.rename(database)
    except sqlite3.Error as exc:
        raise InputError(f"recovery failed; incomplete database preserved: {exc}") from exc
    export(root)
    return {"recovered": True, "approval_restored": False, "next": "freeze new candidate and fully re-audit"}


def state(swarm: Path) -> dict[str, Any]:
    root = swarm.resolve(strict=True)
    with connect(root) as db:
        history = events(db, root)
        policy = current_policy(history)
        verify_policy(root, policy)
    return {"evidence_contract": CONTRACT, "max_cycles": policy["max_cycles"],
            "first_review_cycle": policy["first_review_cycle"],
            "review_cycles": len([r for r in history if r["kind"] == "cycle"]),
            "compositions": len([r for r in history if r["kind"] == "candidate"]),
            "open_findings": len(open_findings(history)),
            "pending_escalations": sorted(pending_escalations(history)),
            "diagnosis_required": diagnosis_required(history),
            "closed_cycles": [{"cycle": r["data"]["cycle"], "outcome": r["data"]["outcome"]}
                              for r in history if r["kind"] == "cycle"],
            "candidate_cycles": sorted({r["data"]["review_cycle"] for r in history if r["kind"] == "candidate"}),
            "recovery_pending": bool([r for r in history if r["kind"] == "recovery"] and not
                                     any(r["kind"] == "candidate" and r["seq"] >
                                         max(e["seq"] for e in history if e["kind"] == "recovery") for r in history)),
            "last_event_sha256": history[-1]["sha256"]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("init", "freeze", "inventory", "dispatch", "review", "resolution",
                                             "close-cycle", "diagnosis", "extension", "recover", "export", "state"))
    parser.add_argument("swarm", type=Path)
    parser.add_argument("--input", help="JSON input path relative to swarm")
    parser.add_argument("--candidate", help="exact frozen candidate ID")
    parser.add_argument("--reviewer", help="declared reviewer name, for dispatch recording only")
    parser.add_argument("--reason", help="explicit recovery reason")
    args = parser.parse_args(argv)
    try:
        root = args.swarm.resolve(strict=True)
        if args.command == "init":
            result = initialize(root)
        elif args.command == "freeze":
            result = freeze(root, object_file(root, descriptor(root, nonempty(args.input, "--input"))))
        elif args.command in {"inventory", "dispatch", "review", "resolution"}:
            result = register(root, args.command, nonempty(args.candidate, "--candidate"), args.input, args.reviewer)
        elif args.command == "close-cycle":
            result = close_cycle(root, nonempty(args.input, "--input"))
        elif args.command in {"diagnosis", "extension"}:
            result = control(root, args.command, nonempty(args.input, "--input"))
        elif args.command == "recover":
            result = recover(root, nonempty(args.input, "--input"), nonempty(args.reason, "--reason"))
        elif args.command == "export":
            result = export(root)
        else:
            result = state(root)
        print(json.dumps(result, sort_keys=True, ensure_ascii=True))
        return 0
    except (InputError, OSError, UnicodeError, ValueError, KeyError, TypeError) as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
