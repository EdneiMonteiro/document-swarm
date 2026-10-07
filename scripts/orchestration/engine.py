"""The deterministic engine of a document swarm.

``next()`` advances the cycle as far as code alone can: it runs the mechanical
checks in parallel, merges the reviewers' grades, applies the gate and derives the
feedback for the next round.  It stops only when an agent is needed (and says
which) or when the work is finished or needs a person.  ``record()`` takes an
agent's result, validates it against the contract and persists it.

Every answer is derived from artifacts on disk and from the journal, so a crash
between two calls loses nothing: calling ``next()`` again recomputes the same state.
The engine never assigns a grade and never approves a delivery; ``gate.py`` does.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
import unicodedata
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterator

from scripts.checks.common import GRADE_INDEX, InputError, parse_data
from scripts.checks.gate import (APPROVAL_GRADES, ORIGINAL_APPROVAL_GRADE, artifact_descriptor, evaluate_current,
                                 requires_editorial)
from scripts.checks.lint_agents import frontmatter_text
from scripts.orchestration import contracts, prompts, spec as specs
from scripts.orchestration.spec import AgentSpec
from scripts.orchestration.store import (FileLock, Journal, atomic_json, atomic_text, digest_file, digest_json,
                                         digest_text, read_json)

SKILL_ROOT = Path(__file__).resolve().parents[2]
CHECKS = SKILL_ROOT / "scripts" / "checks"
FRONTMATTER = re.compile(r"\A---[ \t]*\r?\n.*?\r?\n---[ \t]*(?:\r?\n|$)", re.S)
NARRATIVE_MARKER = "<!-- COORDINATOR: Explain decisions, residual risks, and evidence not represented above. -->"
UNSUPPORTED = (".pdf", ".pptx", ".html", ".htm")
MAX_ADVANCE_STEPS = 64
# How much of an answer that could not be read is kept, at each end, to tell a reply that was cut off from a chatty one.
ANSWER_EXCERPT = 300
# What each script's exit code means.  1 is a finding for the verifiers and the gate, but a plain
# failure for the report and the memory proposal; treating it alike reported "done" over a failure.
OK_EXIT = {"sources": (0, 1), "tables": (0, 1), "nomenclature": (0,), "gate": (0, 1, 2),
           "report": (0,), "memory": (0,)}
GATE_CODES = {"approved": 0, "rejected": 1, "escalate": 2}
# Not a status any script uses: what a checker is given when it ended without leaving a fresh report.
NO_REPORT = 70
# Windows refuses a path of 260 characters unless long paths are enabled, and every file is written through a
# temporary name beside it.  The limits below leave room for that; POSIX allows far more.
MAX_WRITE_PATH = 235 if os.name == "nt" else 1000
ENGINE_PATH_RESERVE = len("reports/execution/usage/c01.r0.rubber-duck..a1.json") + 16
EDITORIAL_REVIEWER = re.compile(r"reviewer-[A-Za-z0-9_-]+")  # the gate refuses any other name


@dataclass(frozen=True)
class Options:
    max_attempts: int = 2
    max_repairs: int = 2
    script_timeout: int = 900
    # Replaces the brief's ceiling and is kept in the plan.  Editing the brief to raise it would change what every
    # task depends on and make the current cycle be paid for again.
    max_cycles: int | None = None
    # The grade every topic and editorial surface must reach.  Like the ceiling it is kept in the plan, and for the same
    # reason it is not set by editing the brief of a swarm that already has paid work.
    approval_grade: str | None = None


@dataclass
class Task:
    task_id: str
    stage: str
    kind: str
    spec: AgentSpec
    cycle: int
    round: int
    inputs: dict[str, Any]
    build: Callable[[int, list[str]], str]
    schema: dict[str, Any]
    context: dict[str, Any]

    @property
    def inputs_sha256(self) -> str:
        return digest_json(self.inputs)


class Engine:
    def __init__(self, swarm: Path, options: Options = Options(), clock: Callable[[], float] = time.time) -> None:
        self.root = swarm.resolve(strict=True)
        self.options = options
        self.clock = clock
        self.exec = self.root / "reports" / "execution"
        self.journal = Journal(self.exec / "journal.jsonl", clock)
        self.lock = FileLock(self.exec / ".lock")
        self.run_lock = FileLock(self.exec / ".run.lock")
        self._loaded = False

    # -- configuration -------------------------------------------------------
    def adopt_plan_options(self) -> None:
        """A swarm keeps the limits it was initialised with, so each call needs no flags."""
        path = self.exec / "plan.json"
        if not path.is_file():
            return
        stored = read_json(path).get("options", {})
        attempts, repairs, cycles = stored.get("max_attempts"), stored.get("max_repairs"), stored.get("max_cycles")
        grade = stored.get("approval_grade")
        self.options = Options(max_attempts=attempts if type(attempts) is int and attempts >= 1 else self.options.max_attempts,
                               max_repairs=repairs if type(repairs) is int and repairs >= 0 else self.options.max_repairs,
                               script_timeout=self.options.script_timeout,
                               max_cycles=cycles if type(cycles) is int and cycles >= 1 else self.options.max_cycles,
                               approval_grade=grade if grade in APPROVAL_GRADES else self.options.approval_grade)

    def stored_ceiling(self) -> int | None:
        """The ceiling a person set with ``--max-cycles`` on an earlier call, which stays until another is given."""
        path = self.exec / "plan.json"
        plan = read_json(path) if path.is_file() else None
        options = plan.get("options") if isinstance(plan, dict) else None
        value = options.get("max_cycles") if isinstance(options, dict) else None
        return value if type(value) is int and value >= 1 else None

    def recorded_ceiling(self) -> int | None:
        """The ceiling the plan on disk was written with, whatever set it."""
        path = self.exec / "plan.json"
        plan = read_json(path) if path.is_file() else None
        value = plan.get("max_cycles") if isinstance(plan, dict) else None
        return value if type(value) is int and value >= 1 else None

    def stored_approval_grade(self) -> str | None:
        """The grade a person set with ``--approval-grade`` on an earlier call, which stays until another is given."""
        path = self.exec / "plan.json"
        plan = read_json(path) if path.is_file() else None
        options = plan.get("options") if isinstance(plan, dict) else None
        value = options.get("approval_grade") if isinstance(options, dict) else None
        return value if value in APPROVAL_GRADES else None

    def recorded_approval_grade(self) -> str | None:
        """The grade the plan on disk was written under; a plan from before the field existed was written under A."""
        path = self.exec / "plan.json"
        if not path.is_file():
            return None
        plan = read_json(path)
        value = plan.get("approval_grade") if isinstance(plan, dict) else None
        return value if value in APPROVAL_GRADES else ORIGINAL_APPROVAL_GRADE

    def load(self, *, models: list[str] | None = None, strict: bool = True, adopt: bool = False) -> specs.Compiled:
        if adopt:
            self.adopt_plan_options()
        brief_path = self.root / "brief.md"
        if not brief_path.is_file():
            raise InputError("the swarm has no brief.md")
        self.brief_text = brief_path.read_text(encoding="utf-8")
        self.brief = frontmatter_text(self.brief_text)
        self.brief_body = FRONTMATTER.sub("", self.brief_text, count=1).strip()
        self.brief_sha = digest_text(self.brief_text)
        problems: list[str] = []
        if self.brief.get("artifact_type") == "presentation" or self.brief.get("mode") == "presentation":
            problems.append("presentation swarms are not supported by the executor yet; use the coordinator flow")
        deliverables = self.brief.get("deliverables")
        if not isinstance(deliverables, list) or len(deliverables) != 1:
            problems.append("the executor needs exactly one Markdown deliverable declared in the brief")
            self.primary = ""
        else:
            try:
                self.primary = artifact_descriptor({"path": deliverables[0], "sha256": "0" * 64})[0]
            except InputError as exc:
                problems.append(str(exc))
                self.primary = ""
            if self.primary and (not self.primary.lower().endswith(".md") or self.primary.lower().endswith(UNSUPPORTED)):
                problems.append(f"{self.primary}: only a Markdown deliverable is supported by the executor yet")
            if self.primary and not self.primary.startswith("output/"):
                problems.append("the deliverable must live under output/")
        self.swarm_id = str(self.brief.get("swarm_id") or self.root.name)
        self.skill_version = str(self.brief.get("skill_version") or "not recorded")
        maximum = self.brief.get("max_cycles")
        if type(maximum) is not int or maximum < 1:
            problems.append("the brief needs a positive integer max_cycles")
            maximum = 1
        self.brief_max_cycles = maximum
        self.max_cycles = self.options.max_cycles if self.options.max_cycles is not None else maximum
        declared = self.brief.get("approval_grade")
        if declared is not None and declared not in APPROVAL_GRADES:
            problems.append(f"the brief's approval_grade must be one of {', '.join(APPROVAL_GRADES)}")
            declared = None
        # What a person set with the option wins, then the brief, then the grade the swarm already runs under (a plan
        # from before the field existed ran under A), and only a swarm with no plan yet takes the provisional policy.
        self.approval_grade = (self.options.approval_grade or declared or self.recorded_approval_grade()
                               or contracts.PROVISIONAL_APPROVAL_GRADE)
        self.approval_index = GRADE_INDEX[self.approval_grade]
        try:
            self.topics = specs.parse_topics(self.brief)
        except InputError as exc:
            problems.append(str(exc))
            self.topics = {}
        compiled = specs.compile_agents(self.root, models=models)
        problems.extend(compiled.errors)
        problems.extend(specs.check_roster(compiled, self.brief))
        self.editorial = requires_editorial(self.brief)
        self.editorial_name = self.brief.get("editorial_reviewer") if self.editorial else None
        # Everything the executor renders is an editorial-v1 review, so a brief that is not one cannot be
        # approved: refuse it now, before the first agent is paid for, not at the gate after the whole cycle.
        if not self.editorial:
            problems.append("the executor delivers editorial-v1 reviews only: declare quality_contract: editorial-v1 "
                            "in the brief")
        elif not self.editorial_name:
            problems.append("the editorial contract needs editorial_reviewer in the brief")
        elif not (isinstance(self.editorial_name, str) and EDITORIAL_REVIEWER.fullmatch(self.editorial_name)):
            problems.append("editorial_reviewer must be named reviewer-<something>: the gate refuses any other name")
        longest = max((len(item.name) for item in compiled.specs), default=0)
        reach = len(str(self.root)) + 1 + ENGINE_PATH_RESERVE + longest
        if os.name == "nt" and reach > 259:
            problems.append(f"the swarm folder path has {len(str(self.root))} characters and the executor needs about "
                            f"{reach - len(str(self.root))} more for its own files, past the 260 Windows allows by default; "
                            "move the swarm to a shorter folder")
        self.compiled = compiled
        self.problems = problems
        if strict and problems:
            raise InputError("; ".join(problems))
        self._loaded = True
        return compiled

    def spec_of(self, kind: str) -> list[AgentSpec]:
        return sorted(self.compiled.of(kind), key=lambda item: item.name)

    # -- paths and records ---------------------------------------------------
    def tag(self, cycle: int) -> str:
        return f"cycle-{cycle:02d}"

    def has(self, relative: str) -> bool:
        return (self.root / relative).is_file()

    def read(self, relative: str) -> str:
        return (self.root / relative).read_text(encoding="utf-8")

    def sha(self, relative: str) -> str | None:
        path = self.root / relative
        return digest_file(path) if path.is_file() else None

    def contained(self, relative: str) -> Path:
        """The path inside the swarm, or an error: nothing is written through a link or a ``..``."""
        target = (self.root / relative).resolve()
        try:
            target.relative_to(self.root)
        except ValueError:
            raise InputError(f"{relative} resolves outside the swarm folder") from None
        return target

    def write(self, relative: str, text: str) -> str:
        return atomic_text(self.contained(relative), text if text.endswith("\n") else text + "\n")

    def write_json(self, relative: str, value: Any) -> str:
        return atomic_json(self.contained(relative), value)

    def write_if_changed(self, relative: str, text: str) -> bool:
        text = text if text.endswith("\n") else text + "\n"
        if self.sha(relative) == digest_text(text):
            return False
        self.write(relative, text)
        return True

    def write_json_if_changed(self, relative: str, value: Any) -> bool:
        if self.sha(relative) == digest_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"):
            return False
        self.write_json(relative, value)
        return True

    def task_id(self, cycle: int, round_number: int, stage: str, agent: str) -> str:
        return f"c{cycle:02d}.r{round_number}.{stage}.{agent}"

    def record_path(self, task_id: str) -> Path:
        return self.exec / "results" / f"{task_id}.json"

    def load_record(self, task_id: str) -> dict[str, Any] | None:
        path = self.record_path(task_id)
        return read_json(path) if path.is_file() else None

    def result_altered(self, record: dict[str, Any]) -> bool:
        """True if the stored result is no longer the one the journal attested when it was accepted.

        The record file holds the only copy of an accepted result, so an edit to it would change a grade,
        a veto or the reference text of the deliverable without leaving a trace.  The digest of the
        accepted result goes into the journal in the same call that accepts it.  A record with no digest
        (a crash between the two writes) has nothing to compare and stands.
        """
        attempt = record.get("accepted")
        if attempt is None:
            return False
        events = self.journal.find("task_recorded", task_id=record["task_id"], attempt=attempt, outcome="accepted")
        digests = {item.get("accepted_sha256") for item in events} - {None}
        return bool(digests) and digest_json(record["result"]) not in digests

    def accepted_result(self, record: dict[str, Any] | None) -> dict[str, Any] | None:
        """The accepted result of a record, or None if there is none or the stored copy was altered."""
        if not record or record.get("accepted") is None or self.result_altered(record):
            return None
        return record["result"]

    def latest_accepted(self, cycle: int, stage: str, agent: str | None = None) -> dict[str, Any] | None:
        """The verified record of the latest round of one stage in a cycle, optionally of one agent.

        Rounds are compared as numbers: as text, ``r10`` would sort before ``r2``.
        """
        pattern = re.compile(rf"c{cycle:02d}\.r(\d+)\.{re.escape(stage)}\.(.+)\.json")
        best: tuple[int, dict[str, Any]] | None = None
        for path in (self.exec / "results").glob(f"c{cycle:02d}.r*.{stage}.*.json"):
            match = pattern.fullmatch(path.name)
            if not match or (agent is not None and match.group(2) != agent):
                continue
            record = read_json(path)
            round_number = int(match.group(1))
            if self.accepted_result(record) is not None and (best is None or round_number > best[0]):
                best = (round_number, record)
        return best[1] if best else None

    def owners(self) -> dict[str, str]:
        path = self.exec / "ownership.json"
        return read_json(path) if path.is_file() else {}

    # -- setup ---------------------------------------------------------------
    def init(self, *, models: list[str] | None = None) -> dict[str, Any]:
        if type(self.options.max_attempts) is not int or self.options.max_attempts < 1 \
                or type(self.options.max_repairs) is not int or self.options.max_repairs < 0:
            raise InputError("max_attempts must be a positive integer and max_repairs a non-negative integer")
        if self.options.max_cycles is not None and (type(self.options.max_cycles) is not int or self.options.max_cycles < 1):
            raise InputError("max_cycles must be a positive integer")
        if self.options.max_cycles is None:
            self.options = replace(self.options, max_cycles=self.stored_ceiling())
        if self.options.approval_grade is not None and self.options.approval_grade not in APPROVAL_GRADES:
            raise InputError(f"approval_grade must be one of {', '.join(APPROVAL_GRADES)}")
        if self.options.approval_grade is None:
            self.options = replace(self.options, approval_grade=self.stored_approval_grade())
        # Everything that can refuse the swarm runs before the first file is created in it.
        compiled = self.load(models=models)
        if not self.journal.events() and any((self.root / "reports").glob("cycle-*")):
            raise InputError("this swarm already holds cycle artifacts from the coordinator flow; "
                             "the executor starts new swarms only")
        with self.lock.held():
            previous = self.recorded_ceiling()
            previous_grade = self.recorded_approval_grade()
            plan = {
                "schema_version": 1, "swarm_id": self.swarm_id, "skill_version": self.skill_version,
                "max_cycles": self.max_cycles, "approval_grade": self.approval_grade, "topics": self.topics,
                "deliverable": self.primary,
                "editorial_reviewer": self.editorial_name, "brief_sha256": self.brief_sha,
                "options": {"max_attempts": self.options.max_attempts, "max_repairs": self.options.max_repairs,
                            "max_cycles": self.options.max_cycles, "approval_grade": self.options.approval_grade},
                "agents": [item.public() for item in sorted(compiled.specs, key=lambda item: item.name)],
                "warnings": compiled.warnings,
            }
            plan["plan_sha256"] = digest_json(plan)
            self.write_json("reports/execution/plan.json", plan)
            if previous is not None and previous != self.max_cycles:
                # Raising the ceiling is a person's decision to keep going below the bar: it stays on the record.
                self.journal.append("max_cycles_changed", previous=previous, current=self.max_cycles,
                                    brief=self.brief_max_cycles)
            if previous_grade is not None and previous_grade != self.approval_grade:
                # So is a lower approval grade: the journal says from which grade to which, and when.
                self.journal.append("approval_grade_changed", previous=previous_grade, current=self.approval_grade)
            event = "run_started" if not self.journal.find("run_started") else "plan_refreshed"
            self.journal.append(event, plan_sha256=plan["plan_sha256"], agents=len(compiled.specs))
            return {"ok": True, "plan_sha256": plan["plan_sha256"], "agents": len(compiled.specs),
                    "warnings": compiled.warnings}

    # -- feedback ------------------------------------------------------------
    def owned_topics(self, agent: AgentSpec) -> set[str] | None:
        text = agent.owned_topics_text
        found = set()
        for key, title in self.topics.items():
            if re.search(rf"(?<![A-Za-z0-9_-]){re.escape(key)}(?![A-Za-z0-9_-])", text) or \
                    (title and title.lower() in text.lower()):
                found.add(key)
        return found or None

    def cycle_feedback(self, previous: int) -> dict[str, Any]:
        """Mandatory pending items after a rejected cycle, taken from its own artifacts."""
        tag = self.tag(previous)
        items: list[dict[str, Any]] = []
        # A cycle can only have been rejected on a matrix derived from every reviewer's verified result, so a
        # reviewer missing here is not a gap to skip: someone changed the record between two calls.
        for report in self.reviewer_reports(previous):
            reviewer_name = report["reviewer"]
            for row in report.get("topics", []):
                if GRADE_INDEX[row["grade"]] < self.approval_index:
                    items.append({"kind": "topic", "topic": row["topic"], "reviewer": reviewer_name, "grade": row["grade"],
                                  "justification": row["justification"], "action": row["action"]})
            editorial = report.get("editorial")
            if editorial:
                for surface in editorial["surfaces"]:
                    if "grade" in surface and GRADE_INDEX[surface["grade"]] < self.approval_index:
                        items.append({"kind": "editorial", "surface": surface["surface"], "grade": surface["grade"],
                                      "location": surface["location"], "quote": surface["quote"],
                                      "justification": surface["justification"], "action": surface["action"]})
                for finding in editorial["findings"]:
                    if finding["severity"] == "blocking":
                        items.append({"kind": "editorial", "surface": "finding", "grade": "",
                                      "location": finding["location"], "quote": finding["quote"],
                                      "justification": finding["reason"], "action": finding["action"]})
        review_path = f"reports/{tag}-review.yaml"
        if self.has(review_path):
            for finding in parse_data(self.read(review_path))["rubberduck"]["achados"]:
                if isinstance(finding, dict) and finding.get("severity") in ("critical", "important"):
                    items.append({"kind": "duck", **finding})
        latest = self.latest_accepted(previous, "consolidation")
        if latest is not None:
            for row in latest["result"]["divergences"]:
                items.append({"kind": "divergence", **row})
        return {"items": items, "markdown": self.feedback_markdown(items)}

    @staticmethod
    def feedback_markdown(items: list[dict[str, Any]]) -> str:
        lines = []
        for item in items:
            kind = item["kind"]
            if kind == "topic":
                lines.append(f"- Tópico {item['topic']} ({item['grade']}, {item['reviewer']}): {item['justification']} "
                             f"Correção: {item['action']}")
            elif kind == "editorial":
                lines.append(f"- Redação, {item['surface']} ({item['grade'] or 'achado bloqueante'}) em «{item['location']}»: "
                             f"{item['justification']} Correção: {item['action']}")
            elif kind == "duck":
                lines.append(f"- Auditoria ({item['severity']}), {item['target']}: {item['evidence']} Correção: {item['correction']}")
            elif kind == "divergence":
                lines.append(f"- Divergência do consolidador em {item.get('topic') or 'geral'} "
                             f"({item.get('author') or 'sem autor'}): {item['issue']}")
            elif kind == "check":
                lines.append(f"- Verificação mecânica ({item['check']}): {item['detail']}")
        return "\n".join(lines)

    def feedback(self, cycle: int, round_number: int) -> dict[str, Any]:
        path = self.exec / "feedback" / f"c{cycle:02d}.r{round_number}.json"
        if path.is_file():
            return read_json(path)
        if round_number != 0:
            raise InputError("repair feedback is stored when the repair starts")
        data = {"items": [], "markdown": ""} if cycle == 1 else self.cycle_feedback(cycle - 1)
        atomic_json(path, data)
        return data

    def select_authors(self, feedback: dict[str, Any]) -> list[AgentSpec]:
        authors = self.spec_of("author")
        items = feedback["items"]
        if not items or {item["kind"] for item in items} & {"editorial", "duck"}:
            return authors
        blocked = {item["topic"] for item in items if item["kind"] == "topic" and item.get("topic")}
        blocked |= {item["topic"] for item in items if item["kind"] == "divergence" and item.get("topic")}
        named = {item["author"] for item in items if item["kind"] == "divergence" and item.get("author")}
        for item in items:
            if item["kind"] == "check":
                if not item.get("authors"):
                    return authors  # a failing check nobody can be blamed for is about the whole document
                named |= set(item["authors"])

        def included(agent: AgentSpec) -> bool:
            if agent.name in named:
                return True
            owned = self.owned_topics(agent)
            # An author whose topics cannot be recognised is kept: unknown ownership is not "not mine".
            return owned is None or bool(owned & blocked)

        return [item for item in authors if included(item)] or authors

    def authors_of_check(self, item: dict[str, Any]) -> set[str] | None:
        """The authors a failing mechanical check goes back to, or None when that cannot be told.

        A dead address belongs to the authors whose source lists hold it.  A table that does not close belongs to
        the author whose section holds it, found by its header row in the consolidated document.  Anything that
        cannot be pinned on someone is about the whole document and goes to everyone: unknown is not "not mine".
        """
        if item.get("check") == "fontes" and item.get("urls"):
            found: set[str] = set()
            for url in item["urls"]:
                holders = {agent.name for agent in self.spec_of("author")
                           if self.has(f"sources/fragments/{agent.name}.json")
                           and any(isinstance(row, dict) and row.get("url") == url
                                   for row in read_json(self.root / f"sources/fragments/{agent.name}.json"))}
                if not holders:
                    return None
                found |= holders
            return found
        if item.get("check") == "tabelas" and item.get("lines"):
            document = self.read(self.primary).splitlines()
            owners = self.owners()
            found = set()
            for number in item["lines"]:
                if not 1 <= number <= len(document) or not document[number - 1].strip():
                    return None
                header = document[number - 1].strip()
                holders = {owner for path, owner in owners.items()
                           if path.lower().endswith(".md") and self.has(path) and header in self.read(path)}
                if len(holders) != 1:
                    return None
                found |= holders
            return found
        return None

    # -- task builders (pure functions of the current files) ----------------
    def author_code(self, agent: AgentSpec) -> str:
        """The author's range of source ids: F1 yields F101..F199, F2 yields F201..F299.

        The identifiers stay in the plain ``Fxx`` form the memory proposal reads, and they
        cannot collide because every author has its own hundred.
        """
        return f"F{[item.name for item in self.spec_of('author')].index(agent.name) + 1}"

    def author_files(self, agent: AgentSpec) -> list[tuple[str, str]]:
        owned = sorted(path for path, owner in self.owners().items() if owner == agent.name and self.has(path))
        return [(path, self.read(path)) for path in owned if path.lower().endswith((".md", ".json", ".txt", ".csv"))]

    def sections(self) -> tuple[list[tuple[str, str]], list[str]]:
        owners = self.owners()
        paths = sorted(path for path in owners if self.has(path))
        written = [(path, self.read(path)) for path in paths if path.lower().endswith(".md")]
        assets = [path for path in paths if not path.lower().endswith(".md")]
        return written, assets

    def sources_text(self) -> str:
        fragments = []
        for agent in self.spec_of("author"):
            path = f"sources/fragments/{agent.name}.json"
            if self.has(path):
                fragments.append((agent.name, read_json(self.root / path)))
        return contracts.render_sources_index(fragments)

    def build_author(self, agent: AgentSpec, cycle: int, round_number: int) -> Task:
        feedback = self.feedback(cycle, round_number)
        files = self.author_files(agent)
        # What the author wrote earlier appears in the prompt but never in the identity of the
        # task: an input that changes when the task succeeds would reopen it forever.
        inputs = {"brief": self.brief_sha, "spec": agent.sha256, "feedback": digest_text(feedback["markdown"]),
                  "cycle": cycle, "round": round_number, "topics": self.topics}
        task_id = self.task_id(cycle, round_number, "authors", agent.name)
        code = self.author_code(agent)

        def build(attempt: int, errors: list[str]) -> str:
            return prompts.author(agent, swarm_id=self.swarm_id, cycle=cycle, round_number=round_number,
                                  task_id=task_id, attempt=attempt, previous_errors=errors, brief_body=self.brief_body,
                                  topics=self.topics, primary=self.primary, code=code,
                                  feedback=feedback["markdown"], current_files=files, schema=contracts.AUTHOR_SCHEMA)

        return Task(task_id, "authors", "author", agent, cycle, round_number, inputs, build, contracts.AUTHOR_SCHEMA,
                    {"topics": self.topics, "deliverable": self.primary, "source_prefix": code,
                     "sources_min": agent.sources_min})

    def previous_document(self, cycle: int) -> str:
        """The previous cycle's document, from a snapshot that no later step overwrites."""
        path = f"reports/execution/documents/c{cycle - 1:02d}.md"
        return self.read(path) if cycle > 1 and self.has(path) else ""

    def build_consolidation(self, cycle: int, round_number: int) -> Task:
        coordinator = self.spec_of("coordinator")[0]
        feedback = self.feedback(cycle, round_number)
        written, assets = self.sections()
        previous = self.previous_document(cycle)
        index = self.sources_text()
        inputs = {"brief": self.brief_sha, "spec": coordinator.sha256, "feedback": digest_text(feedback["markdown"]),
                  "cycle": cycle, "round": round_number, "sections": {path: digest_text(text) for path, text in written},
                  "assets": assets, "index": digest_text(index), "previous": digest_text(previous)}
        task_id = self.task_id(cycle, round_number, "consolidation", coordinator.name)
        authors = [item.name for item in self.spec_of("author")]
        schema = contracts.consolidation_schema(self.topics, authors)

        def build(attempt: int, errors: list[str]) -> str:
            extra = f"\n\nArquivos de apoio já entregues pelos autores: {', '.join(assets)}." if assets else ""
            return prompts.consolidation(coordinator, swarm_id=self.swarm_id, cycle=cycle, round_number=round_number,
                                         task_id=task_id, attempt=attempt, previous_errors=errors,
                                         brief_body=self.brief_body, topics=self.topics, authors=authors,
                                         sections=written, sources_index=index + extra, feedback=feedback["markdown"],
                                         previous_document=previous, schema=schema)

        return Task(task_id, "consolidation", "consolidation", coordinator, cycle, round_number, inputs, build,
                    schema, {"topics": self.topics, "deliverable": self.primary, "authors": authors})

    def snapshot_path(self, cycle: int, round_number: int) -> str:
        return f"reports/execution/checks/c{cycle:02d}.r{round_number}.sources.json"

    def snapshot_sources(self, cycle: int, round_number: int) -> None:
        """Freeze the source verdicts this round's reviewers judge.

        The delivery rechecks every URL and rewrites ``sources/sources-check.json``.  A task identity
        that read that file would change after approval and reopen the review for a timestamp.
        """
        path = self.snapshot_path(cycle, round_number)
        if not self.has(path) and self.has("sources/sources-check.json"):
            self.write(path, self.read("sources/sources-check.json"))

    def checks_text(self, cycle: int, round_number: int) -> str:
        lines = []
        snapshot = self.snapshot_path(cycle, round_number)
        if self.has(snapshot):
            report = read_json(self.root / snapshot)
            counts = report.get("counts", {})
            lines.append("Fontes: " + ", ".join(f"{key}={counts.get(key, 0)}" for key in ("ok", "redirect", "warn", "fail")))
            for item in report.get("results", []):
                if item.get("status") != "ok":
                    lines.append(f"  - {item.get('status', '?').upper()} {item.get('http_status', item.get('error', ''))} {item.get('url')}")
        tables = f"reports/{self.tag(cycle)}-tables-check.json"
        if self.has(tables):
            lines.append(f"Tabelas marcadas: {read_json(self.root / tables).get('failures', '?')} falha(s)")
        nomenclature = f"reports/{self.tag(cycle)}-nomenclature.json"
        if self.has(nomenclature):
            data = read_json(self.root / nomenclature)
            candidates = data.get("candidates", []) if isinstance(data, dict) else []
            lines.append(f"Nomenclatura: {len(candidates)} candidato(s) lexical(is), apenas apoio; não é avaliação semântica.")
        return "\n".join(lines) or "Nenhuma verificação mecânica registrada."

    def check_hashes(self, cycle: int, round_number: int) -> dict[str, str | None]:
        return {"sources": self.sha(self.snapshot_path(cycle, round_number)),
                "tables": self.sha(f"reports/{self.tag(cycle)}-tables-check.json"),
                "nomenclature": self.sha(f"reports/{self.tag(cycle)}-nomenclature.json")}

    def text_path(self, cycle: int) -> str:
        return f"reports/{self.tag(cycle)}-editorial-text.txt"

    def build_reviewer(self, agent: AgentSpec, cycle: int, round_number: int) -> Task:
        document = self.read(self.primary)
        editorial = self.editorial and agent.name == self.editorial_name
        index = self.sources_text()
        checks = self.checks_text(cycle, round_number)
        inputs = {"brief": self.brief_sha, "spec": agent.sha256, "document": digest_text(document),
                  "index": digest_text(index), "checks": self.check_hashes(cycle, round_number), "topics": self.topics,
                  "cycle": cycle, "round": round_number}
        task_id = self.task_id(cycle, round_number, "reviewers", agent.name)
        schema = contracts.reviewer_schema(self.topics, editorial=editorial, fact=agent.evidence_class == "fact")

        def build(attempt: int, errors: list[str]) -> str:
            return prompts.reviewer(agent, swarm_id=self.swarm_id, cycle=cycle, round_number=round_number,
                                    task_id=task_id, attempt=attempt, previous_errors=errors, brief_body=self.brief_body,
                                    topics=self.topics, document=document, primary=self.primary, sources_index=index,
                                    checks=checks, editorial=editorial, schema=schema)

        return Task(task_id, "reviewers", "reviewer", agent, cycle, round_number, inputs, build, schema,
                    {"topics": self.topics, "deliverable": self.primary, "editorial": editorial,
                     "evidence_class": agent.evidence_class, "sources_min": agent.sources_min})

    def reviewer_reports(self, cycle: int) -> list[dict[str, Any]]:
        """Each reviewer's accepted assessment of the cycle, taken from the verified result records.

        The files under reports/ are a copy for the legacy readers.  Grades are never read back from them:
        a grade edited there would otherwise reach the matrix and the gate.
        """
        reports = []
        for item in self.spec_of("reviewer"):
            record = self.latest_accepted(cycle, "reviewers", item.name)
            if record is None:
                raise InputError(f"reviewer {item.name} has no verified assessment for cycle {cycle}")
            reports.append(record["result"])
        return reports

    def restore_reviews(self, cycle: int) -> None:
        """Rewrite the reviewers' files from their verified results, so what legacy readers see is what was accepted."""
        tag = self.tag(cycle)
        for report in self.reviewer_reports(cycle):
            self.write_json_if_changed(f"reports/{tag}-{report['reviewer']}.json", report)
            self.write_if_changed(f"reports/{tag}-{report['reviewer']}.md", contracts.render_reviewer_md(report, self.topics))

    def editorial_of(self, reports: list[dict[str, Any]]) -> dict[str, Any] | None:
        if not self.editorial:
            return None
        for report in reports:
            if report["reviewer"] == self.editorial_name:
                return report.get("editorial")
        raise InputError("the editorial reviewer has not delivered an assessment")

    def render_review(self, cycle: int, duck: dict[str, Any] | None) -> dict[str, Any]:
        reports = self.reviewer_reports(cycle)
        return contracts.render_review(cycle=cycle, max_cycles=self.max_cycles, skill_version=self.skill_version,
                                       topics=self.topics, reports=reports, duck=duck or contracts.pending_duck(),
                                       editorial=self.editorial_of(reports), approval_grade=self.approval_grade)

    def build_duck(self, cycle: int, round_number: int) -> Task:
        agent = self.spec_of("rubber-duck")[0]
        document = self.read(self.primary)
        reports = self.reviewer_reports(cycle)
        review = self.render_review(cycle, None)
        # The matrix is rendered before the audit exists, so its audit section is a marker that fails the gate closed.
        # Shown to the auditor it reads as a critical defect of the matrix (the first real run vetoed a cycle for it),
        # so the auditor sees the matrix without it.  The identity of the task still comes from the whole matrix, minus
        # the ceiling: raising it does not change what the auditor examined, so it must not make the audit be paid again.
        shown = {key: value for key, value in review.items() if key != "rubberduck"}
        audited = {key: value for key, value in review.items() if key != "max_cycles"}
        checks = self.checks_text(cycle, round_number)
        inputs = {"brief": self.brief_sha, "spec": agent.sha256, "document": digest_text(document),
                  "review": digest_json(audited), "reports": digest_json(reports),
                  "checks": self.check_hashes(cycle, round_number), "cycle": cycle, "round": round_number}
        task_id = self.task_id(cycle, round_number, "rubber-duck", agent.name)

        def build(attempt: int, errors: list[str]) -> str:
            return prompts.rubber_duck(agent, swarm_id=self.swarm_id, cycle=cycle, round_number=round_number,
                                       task_id=task_id, attempt=attempt, previous_errors=errors,
                                       brief_body=self.brief_body, document=document, primary=self.primary,
                                       review=shown, reports=reports, checks=checks, schema=contracts.DUCK_SCHEMA)

        return Task(task_id, "rubber-duck", "rubber-duck", agent, cycle, round_number, inputs, build,
                    contracts.DUCK_SCHEMA, {"deliverable": self.primary})

    def build_narrative(self, cycle: int, round_number: int, outcome: str) -> Task:
        agent = self.spec_of("coordinator")[0]
        facts = self.read("reports/final-report.md")
        inputs = {"brief": self.brief_sha, "spec": agent.sha256, "facts": digest_text(facts), "outcome": outcome, "cycle": cycle}
        task_id = self.task_id(cycle, round_number, "narrative", agent.name)

        def build(attempt: int, errors: list[str]) -> str:
            return prompts.narrative(agent, swarm_id=self.swarm_id, cycle=cycle, round_number=round_number,
                                     task_id=task_id, attempt=attempt, previous_errors=errors,
                                     brief_body=self.brief_body, facts=facts, outcome=outcome,
                                     schema=contracts.NARRATIVE_SCHEMA)

        return Task(task_id, "narrative", "narrative", agent, cycle, round_number, inputs, build,
                    contracts.NARRATIVE_SCHEMA, {"outcome": outcome})

    # -- directives ----------------------------------------------------------
    def attempt_state(self, task: Task) -> tuple[str, int, list[str]]:
        record = self.load_record(task.task_id)
        if record and record["inputs_sha256"] == task.inputs_sha256:
            if record["accepted"] is not None:
                return ("altered" if self.result_altered(record) else "done"), record["accepted"], []
            attempts = record["attempts"]
            errors = attempts[-1]["errors"] if attempts else []
            if len(attempts) >= self.options.max_attempts:
                return "exhausted", len(attempts), errors
            return "retry", len(attempts) + 1, errors
        return "new", 1, []

    def directive(self, tasks: list[Task], cycle: int, round_number: int, stage: str) -> dict[str, Any] | None:
        """The tasks of one stage that still need an agent; None when the stage is complete."""
        pending, exhausted, altered = [], [], []
        for task in tasks:
            state, attempt, errors = self.attempt_state(task)
            if state == "altered":
                altered.append(task.task_id)
            elif state == "exhausted":
                exhausted.append({"task_id": task.task_id, "errors": errors})
            elif state != "done":
                pending.append((task, attempt, errors))
        if altered:
            # Neither trusted nor silently paid for again: a person decides whether the file or the journal is right.
            return {"status": "blocked", "kind": "result_altered", "cycle": cycle, "stage": stage,
                    "detail": "the stored result of " + ", ".join(altered) + " no longer matches what the journal attested "
                              "when it was accepted; restore reports/execution/results/<task>.json from a backup, or "
                              "delete it so that the agent runs again", "tasks": altered}
        if exhausted:
            return {"status": "blocked", "kind": "task_failed", "cycle": cycle, "stage": stage,
                    "detail": "an agent could not produce a valid result within the attempt limit; a person must decide",
                    "tasks": exhausted}
        if not pending:
            return None
        issued = []
        for task, attempt, errors in pending:
            prompt = task.build(attempt, errors)
            # Keyed by the identity as well: a task asked again after its inputs changed is a new issue, and the time
            # its record is measured from is the time of that one, not of an earlier one that someone else paid for.
            if not any(item.get("attempt") == attempt and item.get("inputs_sha256") == task.inputs_sha256
                       for item in self.journal.find("task_issued", task_id=task.task_id)):
                self.journal.append("task_issued", task_id=task.task_id, stage=stage, kind=task.kind, agent=task.spec.name,
                                    cycle=cycle, round=round_number, attempt=attempt, inputs_sha256=task.inputs_sha256,
                                    prompt_sha256=digest_text(prompt))
            issued.append({
                "task_id": task.task_id, "attempt": attempt, "inputs_sha256": task.inputs_sha256, "kind": task.kind,
                "stage": stage, "agent": task.spec.name, "cycle": cycle, "round": round_number,
                "label": f"{task.task_id}.a{attempt}", "prompt": prompt, "schema": task.schema,
                "model": task.spec.model, "reasoning_effort": task.spec.reasoning_effort,
                "context_tier": task.spec.context_tier, "tools": list(task.spec.tools), "context": task.context})
        return {"status": "agents", "cycle": cycle, "round": round_number, "stage": stage, "tasks": issued}

    # -- mechanical work -----------------------------------------------------
    def run_script(self, name: str, command: list[str]) -> dict[str, Any]:
        started = self.clock()
        done = subprocess.run([sys.executable, *command], cwd=self.root, capture_output=True, text=True,
                              encoding="utf-8", timeout=self.options.script_timeout,
                              env={**os.environ, "PYTHONUTF8": "1"})
        return {"script": name, "exit_code": done.returncode, "seconds": round(self.clock() - started, 3),
                "stderr": done.stderr.strip()[-800:]}

    def stamp(self, relative: str) -> tuple[int, int] | None:
        try:
            info = self.contained(relative).stat()
        except OSError:
            return None
        return info.st_mtime_ns, info.st_size

    def run_checked(self, name: str, command: list[str], output: str) -> dict[str, Any]:
        """Run a script that must leave a fresh, readable JSON report at ``output``.

        Python ends an uncaught exception with status 1, the status the verifiers use for "findings".  A
        checker that crashed therefore looks like one that ran and found something, and the report on disk is
        whatever an earlier run left.  A report this run did not rewrite, or that is not JSON, means the run did
        not complete, whatever the exit status says.
        """
        before = self.stamp(output)
        result = self.run_script(name, command)
        written = self.stamp(output) not in (None, before)
        if written:
            try:
                read_json(self.root / output)
            except (InputError, OSError, ValueError):
                written = False
        if written:
            return result
        reason = f"{Path(output).name} was not written by this run"
        return {**result, "exit_code": NO_REPORT, "original_exit_code": result["exit_code"],
                "stderr": (reason + (": " + result["stderr"] if result["stderr"] else ""))[-800:]}

    def stage_checks(self, cycle: int, round_number: int) -> dict[str, Any] | None:
        tag = self.tag(cycle)
        inputs = digest_json({"document": self.sha(self.primary), "index": self.sha("sources/sources-index.md")})
        reports = {"sources": "sources/sources-check.json", "tables": f"reports/{tag}-tables-check.json",
                   "nomenclature": f"reports/{tag}-nomenclature.json"}
        commands = {
            "sources": [str(CHECKS / "verify_sources.py"), "sources/sources-index.md", "--output", reports["sources"]],
            "tables": [str(CHECKS / "verify_tables.py"), self.primary, "--output", reports["tables"]],
            "nomenclature": [str(CHECKS / "inspect_nomenclature.py"), self.text_path(cycle), "--output",
                             reports["nomenclature"]],
        }
        finished = {item["script"] for item in self.journal.find("script_finished", cycle=cycle, round=round_number,
                                                                  inputs_sha256=inputs)
                    if item["exit_code"] in OK_EXIT[item["script"]]}
        todo = {name: cmd for name, cmd in commands.items() if name not in finished}
        if todo:
            with ThreadPoolExecutor(max_workers=len(todo)) as pool:
                results = list(pool.map(lambda pair: self.run_checked(pair[0], pair[1], reports[pair[0]]), todo.items()))
            for result in results:
                self.journal.append("script_finished", cycle=cycle, round=round_number, inputs_sha256=inputs, **result)
            for result in results:
                if result["exit_code"] not in OK_EXIT[result["script"]]:
                    return {"status": "failed", "kind": "script_error", "cycle": cycle, "script": result["script"],
                            "detail": result["stderr"] or f"exit code {result['exit_code']}"}
        self.snapshot_sources(cycle, round_number)
        items = []
        if self.has("sources/sources-check.json"):
            fails = [row for row in read_json(self.root / "sources/sources-check.json").get("results", [])
                     if row.get("status") == "fail"]
            if fails:
                items.append({"kind": "check", "check": "fontes", "urls": [row["url"] for row in fails],
                              "detail": "estas URLs falharam e devem ser removidas ou substituídas: "
                                        + "; ".join(f"{row['url']} ({row.get('http_status', row.get('error', '?'))})" for row in fails)})
        tables = f"reports/{tag}-tables-check.json"
        if self.has(tables) and read_json(self.root / tables).get("failures", 0):
            report = read_json(self.root / tables)
            failing = [check["line"] for check in report.get("checks", [])
                       if check.get("status") == "fail" and type(check.get("line")) is int]
            items.append({"kind": "check", "check": "tabelas", "lines": failing,
                          "detail": f"{report['failures']} tabela(s) marcada(s) não fecham as contas; corrija os valores"})
        if not items:
            return None
        repairs = self.journal.count("repair_started", cycle=cycle)
        if repairs >= self.options.max_repairs:
            return {"status": "blocked", "kind": "checks_failed", "cycle": cycle,
                    "detail": f"the mechanical checks still fail after {repairs} repair round(s)", "items": items}
        previous = self.feedback(cycle, repairs)
        # Who the checks go back to is decided now and stored with the feedback: asked again after an author has
        # repaired, the sources that named it are gone, and the selection would change under a round in progress.
        for item in items:
            owners = self.authors_of_check(item)
            if owners is not None:
                item["authors"] = sorted(owners)
        merged = [item for item in previous["items"] if item["kind"] != "check"] + items
        data = {"items": merged, "markdown": self.feedback_markdown(merged)}
        atomic_json(self.exec / "feedback" / f"c{cycle:02d}.r{repairs + 1}.json", data)
        self.journal.append("repair_started", cycle=cycle, round=repairs + 1, items=len(items))
        return {"status": "repair"}

    def review_text(self, cycle: int, duck: dict[str, Any] | None) -> str:
        return json.dumps(self.render_review(cycle, duck), ensure_ascii=False, indent=2, sort_keys=True) + "\n"

    def review_is_derived(self, cycle: int) -> bool:
        """True if the matrix on disk is what the reviewers' grades and the audit produce now."""
        try:
            expected = self.review_text(cycle, self.accepted_duck(cycle))
        except (InputError, OSError, KeyError, ValueError):
            return False
        return digest_text(expected) == self.sha(f"reports/{self.tag(cycle)}-review.yaml")

    def stage_matrix(self, cycle: int) -> None:
        """Write the consolidated matrix.  It is always consistent with the reviewers and the audit."""
        self.restore_reviews(cycle)
        duck = self.accepted_duck(cycle)
        text = self.review_text(cycle, duck)
        review = self.render_review(cycle, duck)
        path = f"reports/{self.tag(cycle)}-review.yaml"
        if self.sha(path) != digest_text(text):
            self.write(path, text)
            self.write(f"reports/{self.tag(cycle)}-review.md", contracts.render_review_md(review))
            self.journal.append("matrix_written", cycle=cycle, duck_recorded=duck is not None, review_sha256=digest_text(text))

    def accepted_duck(self, cycle: int) -> dict[str, Any] | None:
        latest = self.latest_accepted(cycle, "rubber-duck")
        if latest is None:
            return None
        # The audit counts only for the reviews it audited.
        expected = self.build_duck(latest["cycle"], latest["round"])
        return latest["result"] if latest["inputs_sha256"] == expected.inputs_sha256 else None

    def recorded_outcome(self, cycle: int) -> str | None:
        """What the gate recorded for the exact bytes of this cycle's review.  Nothing is recomputed."""
        review, gate = f"reports/{self.tag(cycle)}-review.yaml", f"reports/{self.tag(cycle)}-gate.json"
        if not (self.has(review) and self.has(gate)):
            return None
        record = read_json(self.root / gate)
        if record.get("review_sha256") != self.sha(review):
            return None
        outcome = (record.get("result") or {}).get("outcome")
        return outcome if outcome in GATE_CODES and record.get("exit_code") == GATE_CODES[outcome] else None

    def verified_outcome(self, cycle: int) -> str | None:
        """The recorded outcome, but only if it can be reproduced now.

        The gate record is a cache bound to the review's bytes and nothing more.  It stands only if
        the matrix still derives from the reviewers' grades and the gate's own evaluation, run on
        the files as they are, yields the very result that was recorded.  Approval therefore never
        rests on a file that was edited, forged or written by an older version of the gate.
        """
        outcome = self.recorded_outcome(cycle)
        if outcome is None or not self.review_is_derived(cycle):
            return None
        try:
            decision = evaluate_current(parse_data(self.read(f"reports/{self.tag(cycle)}-review.yaml")), self.root)
        except (InputError, OSError, ValueError):
            return None
        record = read_json(self.root / f"reports/{self.tag(cycle)}-gate.json")
        return outcome if record.get("result") == decision else None

    def stage_gate(self, cycle: int) -> dict[str, Any] | None:
        if self.verified_outcome(cycle) is not None:
            return None
        review, gate = f"reports/{self.tag(cycle)}-review.yaml", f"reports/{self.tag(cycle)}-gate.json"
        result = self.run_checked("gate", [str(CHECKS / "gate.py"), review, "--output", gate], gate)
        self.journal.append("gate_run", cycle=cycle, exit_code=result["exit_code"], seconds=result["seconds"])
        if result["exit_code"] not in OK_EXIT["gate"]:
            return {"status": "failed", "kind": "gate_invalid", "cycle": cycle,
                    "detail": result["stderr"] or f"exit code {result['exit_code']}"}
        if self.verified_outcome(cycle) is None:
            # Without this a gate whose record cannot be reproduced would be run again until the step limit.
            return {"status": "failed", "kind": "gate_invalid", "cycle": cycle,
                    "detail": "the gate ran, but its record does not reproduce from the review on disk"}
        return None

    # -- the cycle -----------------------------------------------------------
    def current_cycle(self) -> int:
        for cycle in range(1, self.max_cycles + 1):
            if self.recorded_outcome(cycle) != "rejected":
                return cycle
        return self.max_cycles + 1

    def started(self, cycle: int) -> bool:
        pattern = f"c{cycle:02d}.*"
        return any(next((self.exec / folder).glob(pattern), None) is not None for folder in ("feedback", "results"))

    def round_of(self, cycle: int) -> int:
        return self.journal.count("repair_started", cycle=cycle)

    def advance(self) -> dict[str, Any]:
        for _ in range(MAX_ADVANCE_STEPS):
            cycle = self.current_cycle()
            if cycle > self.max_cycles:
                return {"status": "blocked", "kind": "max_cycles", "detail": "the cycle ceiling was passed without a verdict"}
            if self.started(cycle + 1):
                return {"status": "blocked", "kind": "history_altered", "cycle": cycle,
                        "detail": f"cycle {cycle + 1} began after cycle {cycle} was rejected, but the recorded verdict of "
                                  f"cycle {cycle} no longer matches its review; a person must inspect "
                                  f"reports/{self.tag(cycle)}-review.yaml and reports/{self.tag(cycle)}-gate.json"}
            recorded, outcome = self.recorded_outcome(cycle), self.verified_outcome(cycle)
            if recorded in ("approved", "escalate") and outcome is None:
                sha = self.sha(f"reports/{self.tag(cycle)}-review.yaml")
                if not self.journal.find("verdict_withdrawn", cycle=cycle, review_sha256=sha):
                    self.journal.append("verdict_withdrawn", cycle=cycle, recorded=recorded, review_sha256=sha,
                                        reason="the recorded verdict cannot be reproduced from the reviewers' grades")
            if outcome == "approved":
                return self.deliver(cycle, "approved")
            if outcome == "escalate":
                return self.deliver(cycle, "escalated")
            round_number = self.round_of(cycle)
            stage = self.run_cycle(cycle, round_number)
            if stage is not None and stage.get("status") != "repair":
                return stage
        raise InputError("the engine did not settle; this is a defect in the stage logic")

    def deliverable_drift(self, cycle: int, round_number: int) -> dict[str, Any] | None:
        """Block if the deliverable is no longer what the coordinator's accepted consolidation wrote."""
        record = self.load_record(self.task_id(cycle, round_number, "consolidation", self.spec_of("coordinator")[0].name))
        if not record or record["accepted"] is None:
            return None
        document = record["result"]["document_markdown"]
        expected = digest_text(document if document.endswith("\n") else document + "\n")
        changed = [path for path in (self.primary, self.text_path(cycle)) if self.sha(path) != expected]
        if not changed:
            return None
        return {"status": "blocked", "kind": "deliverable_changed", "cycle": cycle,
                "detail": f"{', '.join(changed)} no longer matches what the coordinator delivered for this round, so "
                          "the reviews and the verdict no longer describe it; restore the file or start a new cycle"}

    def run_cycle(self, cycle: int, round_number: int) -> dict[str, Any] | None:
        feedback = self.feedback(cycle, round_number)
        found = self.directive([self.build_author(item, cycle, round_number) for item in self.select_authors(feedback)],
                               cycle, round_number, "authors")
        if found:
            return found
        found = self.directive([self.build_consolidation(cycle, round_number)], cycle, round_number, "consolidation")
        if found:
            return found
        drifted = self.deliverable_drift(cycle, round_number)
        if drifted:
            return drifted
        checked = self.stage_checks(cycle, round_number)
        if checked:
            return checked
        found = self.directive([self.build_reviewer(item, cycle, round_number) for item in self.spec_of("reviewer")],
                               cycle, round_number, "reviewers")
        if found:
            return found
        self.stage_matrix(cycle)
        found = self.directive([self.build_duck(cycle, round_number)], cycle, round_number, "rubber-duck")
        if found:
            return found
        self.stage_matrix(cycle)
        return self.stage_gate(cycle)

    # -- delivery ------------------------------------------------------------
    def deliver(self, cycle: int, outcome: str) -> dict[str, Any]:
        # Keyed by the bytes of the review the gate judged, not by the gate record: running the gate
        # again on an identical review rewrites a timestamp and must not redo the delivery.
        gate_sha = read_json(self.root / f"reports/{self.tag(cycle)}-gate.json")["review_sha256"]
        warnings: list[str] = []
        for earlier in range(1, cycle + 1):
            # Legacy readers show these files: keep them equal to what was accepted.  A cycle that cannot be
            # restored matters only if it is the delivered one, and then the verdict could not have been verified.
            try:
                self.restore_reviews(earlier)
            except InputError:
                if earlier == cycle:
                    raise

        def finished(step: str) -> bool:
            """Done only if it succeeded.  The memory proposal is housekeeping: it is tried once."""
            return any(item.get("ok") or step == "memory"
                       for item in self.journal.find("delivery_step", step=step, gate_sha=gate_sha))

        def run(step: str, name: str, command: list[str]) -> dict[str, Any] | None:
            result = self.run_script(name, command)
            ok = result["exit_code"] in OK_EXIT[step]
            self.journal.append("delivery_step", step=step, gate_sha=gate_sha, ok=ok, exit_code=result["exit_code"],
                                seconds=result["seconds"], detail=result["stderr"])
            if not ok and step != "memory":
                return {"status": "failed", "kind": "script_error", "script": name,
                        "detail": result["stderr"] or f"exit code {result['exit_code']}"}
            return None

        index_sha = self.sha("sources/sources-index.md")
        settled = [item for item in self.journal.find("delivery_step", step="sources", gate_sha=gate_sha) if item.get("ok")]
        if outcome == "approved" and settled and settled[-1].get("index_sha256") not in (None, index_sha):
            return {"status": "blocked", "kind": "final_sources_changed", "cycle": cycle,
                    "detail": "sources/sources-index.md changed after the final recheck of the approved delivery, so the "
                              "recheck and the final report no longer describe it; restore the file, or start a new "
                              "cycle so that the sources are reviewed again"}
        if outcome == "approved" and not finished("sources"):
            result = self.run_checked("verify_sources", [str(CHECKS / "verify_sources.py"), "sources/sources-index.md",
                                                         "--output", "sources/sources-check.json", "--force"],
                                      "sources/sources-check.json")
            ran = result["exit_code"] in OK_EXIT["sources"]
            dead = [row["url"] for row in read_json(self.root / "sources/sources-check.json").get("results", [])
                    if row.get("status") == "fail"] if ran and self.has("sources/sources-check.json") else []
            # Settled only when the recheck ran and found nothing dead: a person who fixes the cause, or
            # a site that recovers, is picked up by the next call instead of being skipped for good.
            self.journal.append("delivery_step", step="sources", gate_sha=gate_sha, ok=ran and not dead,
                                exit_code=result["exit_code"], seconds=result["seconds"], detail=result["stderr"],
                                failed=dead, index_sha256=index_sha)
            if not ran:
                return {"status": "failed", "kind": "script_error", "script": "verify_sources",
                        "detail": result["stderr"] or f"exit code {result['exit_code']}"}
            if dead:
                return {"status": "blocked", "kind": "final_sources_failed", "cycle": cycle,
                        "detail": "the final recheck found dead sources after approval; the recheck runs again on the "
                                  "next call, and a source that stays dead must be replaced and re-reviewed: "
                                  + ", ".join(dead)}
        if not finished("report"):
            failure = run("report", "final_report", [str(CHECKS / "final_report.py"), str(self.root), "--force"])
            if failure:
                return failure
        if not finished("narrative"):
            if NARRATIVE_MARKER not in self.read("reports/final-report.md"):
                # A crash between writing the narrative into the report and journaling the step leaves a report
                # with no marker.  Rebuilding the facts brings it back, so the narrative goes in exactly once, and
                # the facts being the same the accepted narrative is reused instead of paid for again.
                failure = run("report", "final_report", [str(CHECKS / "final_report.py"), str(self.root), "--force"])
                if failure:
                    return failure
            # Inserting the narrative rewrites the report the task read, so a finished task is
            # settled by the journal and never rebuilt from the changed report.
            task = self.build_narrative(cycle, self.round_of(cycle), outcome)
            found = self.directive([task], cycle, self.round_of(cycle), "narrative")
            if found:
                return found
            self.insert_narrative(task)
            self.journal.append("delivery_step", step="narrative", gate_sha=gate_sha, ok=True)
        if outcome == "approved" and not finished("memory"):
            run("memory", "update_memory", [str(CHECKS / "update_memory.py"), str(self.root)])
        for item in self.journal.find("delivery_step", step="memory", gate_sha=gate_sha):
            if not item.get("ok"):
                warnings.append(f"the memory proposal was not generated: {item.get('detail') or 'exit ' + str(item.get('exit_code'))}")
        if not any(item.get("outcome") == outcome and item.get("cycle") == cycle
                   for item in self.journal.find("run_finished")):
            self.journal.append("run_finished", outcome=outcome, cycle=cycle, warnings=len(warnings))
        return {"status": "done", "outcome": outcome, "cycle": cycle, "gate_sha256": gate_sha,
                "report": "reports/final-report.md", "warnings": warnings}

    def insert_narrative(self, task: Task) -> None:
        # Reached only after the stage directive verified this very record, so it is read as it is.
        narrative = self.load_record(task.task_id)["result"]["narrative_markdown"]
        report = self.read("reports/final-report.md")
        if NARRATIVE_MARKER in report:
            report = report.replace(NARRATIVE_MARKER, narrative)
        else:
            report = report.rstrip() + "\n\n" + narrative
        self.write("reports/final-report.md", report)

    # -- public api ----------------------------------------------------------
    def next(self) -> dict[str, Any]:
        with self.lock.held():
            self.load(adopt=True)
            before = len(self.journal.events())
            directive = self.advance()
            advanced = [{key: item[key] for key in ("event", "cycle", "script", "step", "exit_code") if key in item}
                        for item in self.journal.events()[before:] if item["event"] != "task_issued"]
            directive["advanced"] = advanced
            return directive

    def record(self, task_id: str, attempt: int, inputs_sha256: str, result: Any,
               runtime: dict[str, Any] | None = None) -> dict[str, Any]:
        runtime = contracts.clean_runtime(runtime)
        with self.lock.held():
            self.load(adopt=True)
            task = self.task_for(task_id)
            if task is None or task.inputs_sha256 != inputs_sha256:
                self.journal.append("task_stale", task_id=task_id, attempt=attempt)
                return {"accepted": False, "stale": True, "retry": False,
                        "errors": ["the swarm changed after this task was issued; ask for the next directive"]}
            state, expected, _ = self.attempt_state(task)
            if state == "done":
                return {"accepted": True, "duplicate": True, "retry": False, "errors": []}
            if state == "altered":
                return {"accepted": False, "stale": False, "retry": False,
                        "errors": ["the stored result of this task no longer matches the journal; ask for the next directive"]}
            if attempt != expected:
                return {"accepted": False, "stale": True, "retry": False,
                        "errors": [f"expected attempt {expected}, received {attempt}"]}
            # A result is accepted only for an attempt this engine issued: the identity of a task is public,
            # so a caller that merely computes it must not be able to seed results for work nobody asked for.
            issued = next((item for item in self.journal.find("task_issued", task_id=task_id, attempt=attempt,
                                                             inputs_sha256=inputs_sha256)), None)
            if issued is None:
                return {"accepted": False, "stale": True, "retry": False,
                        "errors": ["this attempt was never issued; ask for the next directive"]}
            parse_problem = None
            answer = result if isinstance(result, str) else None
            if isinstance(result, str):
                result, parse_problem = contracts.parse_agent_json(result)
            storable = result is not None and contracts.storable(result)
            if parse_problem:
                errors, normal = [parse_problem], None
            elif result is None:
                errors, normal = ["the agent returned no result"], None
            elif not storable:
                errors, normal = ["the result is not plain JSON text the executor can store (invalid text encoding, "
                                  "NaN, or larger than the limit)"], None
            else:
                try:
                    errors, normal = self.validate(task, result)
                except Exception as exc:
                    # The result is untrusted text: whatever the validators did not foresee in it is still a
                    # refusal of that answer, never an exception that ends the run and drops its siblings' results.
                    errors, normal = [f"the result could not be validated ({type(exc).__name__}); "
                                      "reply with a plainer JSON object"], None
            if not errors:
                try:
                    self.materialise(task, normal)
                except (OSError, ValueError) as exc:
                    errors, normal = [f"the result could not be written ({type(exc).__name__}: {exc})"], None
            record = self.load_record(task_id)
            if not record or record["inputs_sha256"] != task.inputs_sha256:
                record = {"task_id": task_id, "stage": task.stage, "cycle": task.cycle, "round": task.round,
                          "inputs_sha256": task.inputs_sha256, "attempts": [], "accepted": None, "result": None}
            seconds = round(self.clock() - datetime.fromisoformat(issued["at"].replace("Z", "+00:00")).timestamp(), 3)
            missing = result is None and not parse_problem
            attempt_runtime = dict(runtime or {})
            if parse_problem and answer:
                # Without the answer itself there is no telling a reply that was cut off from one that was chatty.
                # It stays in the record of the attempt, not in the journal, which holds only what the run did.
                attempt_runtime.update(answer_head=answer[:ANSWER_EXCERPT], answer_tail=answer[-ANSWER_EXCERPT:]
                                       if len(answer) > ANSWER_EXCERPT else "")
            entry = {"attempt": attempt, "outcome": "accepted" if not errors else ("null" if missing else "rejected"),
                     "errors": errors, "result_sha256": digest_json(result) if storable else None,
                     "runtime": attempt_runtime}
            record["attempts"].append(entry)
            if not errors:
                record["accepted"], record["result"] = attempt, normal
            self.write_json(f"reports/execution/results/{task_id}.json", record)
            retry = bool(errors) and len(record["attempts"]) < self.options.max_attempts
            attested = {} if errors else {"accepted_sha256": digest_json(normal)}
            self.journal.append("task_recorded", task_id=task_id, stage=task.stage, kind=task.kind, agent=task.spec.name,
                                cycle=task.cycle, round=task.round, attempt=attempt, outcome=entry["outcome"],
                                errors=errors, seconds=seconds, runtime=runtime or {}, **attested)
            return {"accepted": not errors, "retry": retry, "errors": errors}

    def task_for(self, task_id: str) -> Task | None:
        match = re.fullmatch(r"c(\d+)\.r(\d+)\.([a-z-]+)\.(.+)", task_id)
        if not match:
            return None
        cycle, round_number, stage, agent = int(match.group(1)), int(match.group(2)), match.group(3), match.group(4)
        try:
            if stage == "authors":
                return self.build_author(self.compiled.by_name(agent), cycle, round_number)
            if stage == "consolidation":
                return self.build_consolidation(cycle, round_number)
            if stage == "reviewers":
                return self.build_reviewer(self.compiled.by_name(agent), cycle, round_number)
            if stage == "rubber-duck":
                return self.build_duck(cycle, round_number)
            if stage == "narrative":
                outcome = (self.recorded_outcome(cycle) or "")
                return self.build_narrative(cycle, round_number, "approved" if outcome == "approved" else "escalated")
        except (InputError, OSError, KeyError, ValueError):
            return None
        return None

    def validate(self, task: Task, result: Any) -> tuple[list[str], dict[str, Any] | None]:
        if task.kind == "author":
            errors, normal = contracts.check_author(result, spec=task.spec, code=task.context["source_prefix"],
                                                    primary=self.primary, owners=self.owners())
            for item in (normal or {}).get("files", []):
                try:
                    target = self.contained(item["path"])
                except InputError as exc:
                    errors.append(str(exc))
                    continue
                real = target.relative_to(self.root).as_posix()
                # contained() resolves the path as the file system does: a Windows short name or a link comes
                # back as the real name, which is the file the write would change and may belong to someone else.
                if unicodedata.normalize("NFC", real).casefold() != unicodedata.normalize("NFC", item["path"]).casefold():
                    errors.append(f"{item['path']} is another name for {real} (a short name or a link); use the real name")
                elif len(str(target)) > MAX_WRITE_PATH:
                    errors.append(f"{item['path']} would be {len(str(target))} characters long once placed in this swarm "
                                  f"folder, past the {MAX_WRITE_PATH} the executor can write; use a shorter name")
            return (errors, None) if errors else (errors, normal)
        if task.kind == "consolidation":
            return contracts.check_consolidation(result, topics=self.topics,
                                                 authors={item.name for item in self.spec_of("author")})
        if task.kind == "reviewer":
            text = self.read(self.text_path(task.cycle))
            return contracts.check_reviewer(
                result, spec=task.spec, topics=self.topics, cycle=task.cycle, editorial=task.context["editorial"],
                text=text, text_path=self.text_path(task.cycle), text_sha=self.sha(self.text_path(task.cycle)),
                artifacts=[{"path": self.primary, "sha256": self.sha(self.primary)}])
        if task.kind == "rubber-duck":
            return contracts.check_duck(result)
        return contracts.check_narrative(result)

    def materialise(self, task: Task, result: dict[str, Any]) -> None:
        """Write the accepted result as the files the legacy flow wrote."""
        tag = self.tag(task.cycle)
        if task.kind == "author":
            owners = self.owners()
            for item in result["files"]:
                self.write(item["path"], item["content"])
                owners[item["path"]] = task.spec.name
            self.write_json("reports/execution/ownership.json", owners)
            self.write_json(f"sources/fragments/{task.spec.name}.json", result["sources"])
        elif task.kind == "consolidation":
            document = result["document_markdown"]
            self.write(self.primary, document)
            self.write(f"reports/execution/documents/c{task.cycle:02d}.md", document)
            self.write("sources/sources-index.md", self.sources_text())
            self.write(self.text_path(task.cycle), document)
            self.write(f"reports/{tag}-authors.md", self.authors_report(task))
        elif task.kind == "reviewer":
            self.write_json(f"reports/{tag}-{task.spec.name}.json", result)
            self.write(f"reports/{tag}-{task.spec.name}.md", contracts.render_reviewer_md(result, self.topics))
        elif task.kind == "rubber-duck":
            self.write(f"reports/{tag}-rubberduck.md", contracts.render_duck_md(result, task.cycle))

    def authors_report(self, task: Task) -> str:
        lines = [f"# Rodada de autores do ciclo {task.cycle}", "",
                 "Gerado pelo executor a partir dos resultados registrados.", "",
                 "| Autor | Modelo | Arquivos |", "|---|---|---|"]
        owners = self.owners()
        for agent in self.spec_of("author"):
            files = ", ".join(sorted(path for path, owner in owners.items() if owner == agent.name)) or "—"
            lines.append(f"| {agent.name} | {agent.model} | {files} |")
        return "\n".join(lines) + "\n"

    def status(self) -> dict[str, Any]:
        self.load(strict=False, adopt=True)
        events = self.journal.events()
        issued = {(item["task_id"], item["attempt"]) for item in events if item["event"] == "task_issued"}
        recorded = [item for item in events if item["event"] == "task_recorded"]
        return {"swarm": self.swarm_id, "problems": self.problems, "cycle": self.current_cycle(),
                "max_cycles": self.max_cycles, "approval_grade": self.approval_grade,
                "tasks_issued": len(issued), "tasks_recorded": len(recorded),
                "accepted": sum(1 for item in recorded if item["outcome"] == "accepted"),
                "rejected": sum(1 for item in recorded if item["outcome"] == "rejected"),
                "null_results": sum(1 for item in recorded if item["outcome"] == "null"),
                "repairs": sum(1 for item in events if item["event"] == "repair_started"),
                "finished": [item for item in events if item["event"] == "run_finished"]}

    @contextmanager
    def hold_run(self) -> Iterator[None]:
        """Exclusive use of the swarm for a whole run.

        Each ``next`` and ``record`` takes the swarm's lock for itself, which keeps the files consistent but not the
        spending: two runs would each be handed the same pending agents and pay for them twice.
        """
        with self.run_lock.held(timeout=0, busy="another run is already operating this swarm, and two runs would pay "
                                                 "every agent twice; wait for it or stop it, then run the same command again"):
            yield

    def run(self, backend: Callable[[dict[str, Any]], Any], *, parallel: int = 4, models: list[str] | None = None,
            on_event: Callable[[str, dict[str, Any]], None] | None = None) -> dict[str, Any]:
        """Drive the whole run in-process: ``backend`` answers each agent task, and each answer is recorded as it arrives.

        The backend returns an ``Answer`` or a bare result.  Recording one result before the slower
        agents of the same step finish means a crash loses only the agents that were still running.
        ``on_event`` is told about each directive, task start and task finish; a failure there never
        interrupts the run.  Only one run can operate a swarm at a time.
        """
        with self.hold_run():
            return self.drive(backend, parallel=parallel, models=models, on_event=on_event)

    def drive(self, backend: Callable[[dict[str, Any]], Any], *, parallel: int = 4, models: list[str] | None = None,
              on_event: Callable[[str, dict[str, Any]], None] | None = None) -> dict[str, Any]:
        """The run loop, for a caller that already holds the run (see ``hold_run``)."""
        def emit(name: str, **data: Any) -> None:
            if on_event is not None:
                try:
                    on_event(name, data)
                except Exception:  # a broken progress display must not abort a paid run
                    pass

        self.init(models=models)
        for _ in range(500):
            directive = self.next()
            emit("directive", directive=directive)
            if directive["status"] != "agents":
                return directive
            self.run_stage(backend, directive["tasks"], parallel, emit)
        raise InputError("the run did not finish within the step limit")

    def mark_started(self, task: dict[str, Any]) -> None:
        """Journal the moment an agent really begins, which is later than the moment it was issued.

        A run that stops and is resumed hours later runs again the tasks it had issued, so measuring from the issue
        would count the pause as agent time.  A measurement is never worth stopping a paid agent: a failure is ignored.
        """
        try:
            with self.lock.held():
                self.journal.append("task_started", task_id=task["task_id"], attempt=task["attempt"], agent=task["agent"])
        except (InputError, OSError):
            pass

    def run_stage(self, backend: Callable[[dict[str, Any]], Any], tasks: list[dict[str, Any]], parallel: int,
                  emit: Callable[..., None]) -> None:
        """Run the agents of one step and record each answer the moment it arrives.

        One answer that cannot be recorded does not cost the others: they are recorded first and the
        failure is raised at the end.  An interruption stops the agents that are still running at once,
        before anything waits for them, and keeps what had already finished.
        """
        stopping = threading.Event()

        def answer(task: dict[str, Any]) -> Any:
            if stopping.is_set():
                return None  # an interrupt arrived while this task was still queued: nothing new is started
            # Announced when the worker begins, not when the task is queued: with fewer workers than tasks
            # the others are waiting, and a table that lists them as running would be wrong.
            emit("started", task=task)
            self.mark_started(task)
            try:
                return backend(task)
            except Exception:  # an agent that fails is a null result, never a crash of the run
                return None

        pool = ThreadPoolExecutor(max_workers=max(1, parallel))
        futures: dict[Any, dict[str, Any]] = {}
        recorded: set[Any] = set()
        failures: list[Exception] = []

        def take(future: Any) -> None:
            task = futures[future]
            try:
                value = future.result()
                result, runtime = (value.result, value.runtime) if isinstance(value, contracts.Answer) else (value, None)
                outcome = self.record(task["task_id"], task["attempt"], task["inputs_sha256"], result, runtime)
            except Exception as exc:
                failures.append(exc)
                emit("failed", task=task, error=f"{type(exc).__name__}: {exc}")
            else:
                emit("finished", task=task, outcome=outcome, runtime=runtime or {})
            recorded.add(future)

        try:
            for task in tasks:
                futures[pool.submit(answer, task)] = task
            pending = set(futures)
            while pending:
                # A timeout keeps the wait interruptible.  Without one, Windows delivers Ctrl+C only after
                # every agent has finished, which is exactly when stopping them no longer matters.
                done, pending = wait(pending, timeout=0.5, return_when=FIRST_COMPLETED)
                for future in done:
                    take(future)
        except BaseException:
            # What had already finished is paid for and is recorded.  What is still running is stopped before
            # anything waits for it, and the answers a cancelled agent returns afterwards are not results.
            stopping.set()
            finished = [future for future in futures if future.done() and future not in recorded]
            cancel = getattr(backend, "cancel", None)
            if cancel is not None:
                cancel()
            for future in finished:
                take(future)
            pool.shutdown(wait=False, cancel_futures=True)
            raise
        pool.shutdown(wait=True)
        if failures:
            raise failures[0]
