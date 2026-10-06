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
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from scripts.checks.common import GRADE_INDEX, InputError, parse_data
from scripts.checks.gate import artifact_descriptor, evaluate_current, requires_editorial
from scripts.checks.lint_agents import frontmatter_text
from scripts.orchestration import contracts, prompts, spec as specs
from scripts.orchestration.spec import AgentSpec
from scripts.orchestration.store import (FileLock, Journal, atomic_json, atomic_text, digest_file, digest_json,
                                         digest_text, read_json)

SKILL_ROOT = Path(__file__).resolve().parents[2]
CHECKS = SKILL_ROOT / "scripts" / "checks"
FRONTMATTER = re.compile(r"\A---[ \t]*\r?\n.*?\r?\n---[ \t]*(?:\r?\n|$)", re.S)
NARRATIVE_MARKER = "<!-- COORDINATOR: Explain decisions, residual risks, and evidence not represented above. -->"
APPROVING = GRADE_INDEX["A"]
UNSUPPORTED = (".pdf", ".pptx", ".html", ".htm")
MAX_ADVANCE_STEPS = 64
# What each script's exit code means.  1 is a finding for the verifiers and the gate, but a plain
# failure for the report and the memory proposal; treating it alike reported "done" over a failure.
OK_EXIT = {"sources": (0, 1), "tables": (0, 1), "nomenclature": (0,), "gate": (0, 1, 2),
           "report": (0,), "memory": (0,)}
GATE_CODES = {"approved": 0, "rejected": 1, "escalate": 2}


@dataclass(frozen=True)
class Options:
    max_attempts: int = 2
    max_repairs: int = 2
    script_timeout: int = 900


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
        self._loaded = False

    # -- configuration -------------------------------------------------------
    def adopt_plan_options(self) -> None:
        """A swarm keeps the limits it was initialised with, so each call needs no flags."""
        path = self.exec / "plan.json"
        if not path.is_file():
            return
        stored = read_json(path).get("options", {})
        attempts, repairs = stored.get("max_attempts"), stored.get("max_repairs")
        self.options = Options(max_attempts=attempts if type(attempts) is int and attempts >= 1 else self.options.max_attempts,
                               max_repairs=repairs if type(repairs) is int and repairs >= 0 else self.options.max_repairs,
                               script_timeout=self.options.script_timeout)

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
        self.max_cycles = maximum
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
        if self.editorial and not self.editorial_name:
            problems.append("the editorial contract needs editorial_reviewer in the brief")
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

    def task_id(self, cycle: int, round_number: int, stage: str, agent: str) -> str:
        return f"c{cycle:02d}.r{round_number}.{stage}.{agent}"

    def record_path(self, task_id: str) -> Path:
        return self.exec / "results" / f"{task_id}.json"

    def load_record(self, task_id: str) -> dict[str, Any] | None:
        path = self.record_path(task_id)
        return read_json(path) if path.is_file() else None

    def owners(self) -> dict[str, str]:
        path = self.exec / "ownership.json"
        return read_json(path) if path.is_file() else {}

    # -- setup ---------------------------------------------------------------
    def init(self, *, models: list[str] | None = None) -> dict[str, Any]:
        if type(self.options.max_attempts) is not int or self.options.max_attempts < 1 \
                or type(self.options.max_repairs) is not int or self.options.max_repairs < 0:
            raise InputError("max_attempts must be a positive integer and max_repairs a non-negative integer")
        # Everything that can refuse the swarm runs before the first file is created in it.
        compiled = self.load(models=models)
        if not self.journal.events() and any((self.root / "reports").glob("cycle-*")):
            raise InputError("this swarm already holds cycle artifacts from the coordinator flow; "
                             "the executor starts new swarms only")
        with self.lock.held():
            plan = {
                "schema_version": 1, "swarm_id": self.swarm_id, "skill_version": self.skill_version,
                "max_cycles": self.max_cycles, "topics": self.topics, "deliverable": self.primary,
                "editorial_reviewer": self.editorial_name, "brief_sha256": self.brief_sha,
                "options": {"max_attempts": self.options.max_attempts, "max_repairs": self.options.max_repairs},
                "agents": [item.public() for item in sorted(compiled.specs, key=lambda item: item.name)],
                "warnings": compiled.warnings,
            }
            plan["plan_sha256"] = digest_json(plan)
            self.write_json("reports/execution/plan.json", plan)
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
        for reviewer in self.spec_of("reviewer"):
            path = f"reports/{tag}-{reviewer.name}.json"
            if not self.has(path):
                continue
            report = read_json(self.root / path)
            for row in report.get("topics", []):
                if GRADE_INDEX[row["grade"]] < APPROVING:
                    items.append({"kind": "topic", "topic": row["topic"], "reviewer": reviewer.name, "grade": row["grade"],
                                  "justification": row["justification"], "action": row["action"]})
            editorial = report.get("editorial")
            if editorial:
                for surface in editorial["surfaces"]:
                    if "grade" in surface and GRADE_INDEX[surface["grade"]] < APPROVING:
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
        latest = None
        for path in sorted((self.exec / "results").glob(f"c{previous:02d}.*.consolidation.*.json")):
            record = read_json(path)
            if record.get("accepted") is not None:
                latest = record
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
        if not items or {item["kind"] for item in items} & {"editorial", "duck", "check"}:
            return authors
        blocked = {item["topic"] for item in items if item["kind"] == "topic" and item.get("topic")}
        blocked |= {item["topic"] for item in items if item["kind"] == "divergence" and item.get("topic")}
        named = {item["author"] for item in items if item["kind"] == "divergence" and item.get("author")}

        def included(agent: AgentSpec) -> bool:
            if agent.name in named:
                return True
            owned = self.owned_topics(agent)
            # An author whose topics cannot be recognised is kept: unknown ownership is not "not mine".
            return owned is None or bool(owned & blocked)

        return [item for item in authors if included(item)] or authors

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

        def build(attempt: int, errors: list[str]) -> str:
            extra = f"\n\nArquivos de apoio já entregues pelos autores: {', '.join(assets)}." if assets else ""
            return prompts.consolidation(coordinator, swarm_id=self.swarm_id, cycle=cycle, round_number=round_number,
                                         task_id=task_id, attempt=attempt, previous_errors=errors,
                                         brief_body=self.brief_body, topics=self.topics, sections=written,
                                         sources_index=index + extra, feedback=feedback["markdown"],
                                         previous_document=previous, schema=contracts.CONSOLIDATION_SCHEMA)

        return Task(task_id, "consolidation", "consolidation", coordinator, cycle, round_number, inputs, build,
                    contracts.CONSOLIDATION_SCHEMA, {"topics": self.topics, "deliverable": self.primary})

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
                  "checks": self.check_hashes(cycle, round_number), "topics": self.topics, "cycle": cycle,
                  "round": round_number}
        task_id = self.task_id(cycle, round_number, "reviewers", agent.name)
        schema = contracts.reviewer_schema(editorial=editorial, fact=agent.evidence_class == "fact")

        def build(attempt: int, errors: list[str]) -> str:
            return prompts.reviewer(agent, swarm_id=self.swarm_id, cycle=cycle, round_number=round_number,
                                    task_id=task_id, attempt=attempt, previous_errors=errors, brief_body=self.brief_body,
                                    topics=self.topics, document=document, primary=self.primary, sources_index=index,
                                    checks=checks, editorial=editorial, schema=schema)

        return Task(task_id, "reviewers", "reviewer", agent, cycle, round_number, inputs, build, schema,
                    {"topics": self.topics, "deliverable": self.primary, "editorial": editorial,
                     "evidence_class": agent.evidence_class, "sources_min": agent.sources_min})

    def reviewer_reports(self, cycle: int) -> list[dict[str, Any]]:
        return [read_json(self.root / f"reports/{self.tag(cycle)}-{item.name}.json") for item in self.spec_of("reviewer")]

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
                                       editorial=self.editorial_of(reports))

    def build_duck(self, cycle: int, round_number: int) -> Task:
        agent = self.spec_of("rubber-duck")[0]
        document = self.read(self.primary)
        reports = self.reviewer_reports(cycle)
        review = self.render_review(cycle, None)
        checks = self.checks_text(cycle, round_number)
        inputs = {"brief": self.brief_sha, "spec": agent.sha256, "document": digest_text(document),
                  "review": digest_json(review), "reports": digest_json(reports),
                  "checks": self.check_hashes(cycle, round_number), "cycle": cycle, "round": round_number}
        task_id = self.task_id(cycle, round_number, "rubber-duck", agent.name)

        def build(attempt: int, errors: list[str]) -> str:
            return prompts.rubber_duck(agent, swarm_id=self.swarm_id, cycle=cycle, round_number=round_number,
                                       task_id=task_id, attempt=attempt, previous_errors=errors,
                                       brief_body=self.brief_body, document=document, primary=self.primary,
                                       review=review, reports=reports, checks=checks, schema=contracts.DUCK_SCHEMA)

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
                return "done", record["accepted"], []
            attempts = record["attempts"]
            errors = attempts[-1]["errors"] if attempts else []
            if len(attempts) >= self.options.max_attempts:
                return "exhausted", len(attempts), errors
            return "retry", len(attempts) + 1, errors
        return "new", 1, []

    def directive(self, tasks: list[Task], cycle: int, round_number: int, stage: str) -> dict[str, Any] | None:
        """The tasks of one stage that still need an agent; None when the stage is complete."""
        pending, exhausted = [], []
        for task in tasks:
            state, attempt, errors = self.attempt_state(task)
            if state == "exhausted":
                exhausted.append({"task_id": task.task_id, "errors": errors})
            elif state != "done":
                pending.append((task, attempt, errors))
        if exhausted:
            return {"status": "blocked", "kind": "task_failed", "cycle": cycle, "stage": stage,
                    "detail": "an agent could not produce a valid result within the attempt limit; a person must decide",
                    "tasks": exhausted}
        if not pending:
            return None
        issued = []
        for task, attempt, errors in pending:
            prompt = task.build(attempt, errors)
            if not any(item.get("task_id") == task.task_id and item.get("attempt") == attempt
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

    def stage_checks(self, cycle: int, round_number: int) -> dict[str, Any] | None:
        tag = self.tag(cycle)
        inputs = digest_json({"document": self.sha(self.primary), "index": self.sha("sources/sources-index.md")})
        commands = {
            "sources": [str(CHECKS / "verify_sources.py"), "sources/sources-index.md", "--output", "sources/sources-check.json"],
            "tables": [str(CHECKS / "verify_tables.py"), self.primary, "--output", f"reports/{tag}-tables-check.json"],
            "nomenclature": [str(CHECKS / "inspect_nomenclature.py"), self.text_path(cycle), "--output",
                             f"reports/{tag}-nomenclature.json"],
        }
        finished = {item["script"] for item in self.journal.find("script_finished", cycle=cycle, round=round_number,
                                                                  inputs_sha256=inputs)
                    if item["exit_code"] in OK_EXIT[item["script"]]}
        todo = {name: cmd for name, cmd in commands.items() if name not in finished}
        if todo:
            with ThreadPoolExecutor(max_workers=len(todo)) as pool:
                results = list(pool.map(lambda pair: self.run_script(pair[0], pair[1]), todo.items()))
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
                items.append({"kind": "check", "check": "fontes", "detail": "estas URLs falharam e devem ser removidas "
                              "ou substituídas: " + "; ".join(f"{row['url']} ({row.get('http_status', row.get('error', '?'))})" for row in fails)})
        tables = f"reports/{tag}-tables-check.json"
        if self.has(tables) and read_json(self.root / tables).get("failures", 0):
            items.append({"kind": "check", "check": "tabelas",
                          "detail": f"{read_json(self.root / tables)['failures']} tabela(s) marcada(s) não fecham as contas; corrija os valores"})
        if not items:
            return None
        repairs = self.journal.count("repair_started", cycle=cycle)
        if repairs >= self.options.max_repairs:
            return {"status": "blocked", "kind": "checks_failed", "cycle": cycle,
                    "detail": f"the mechanical checks still fail after {repairs} repair round(s)", "items": items}
        previous = self.feedback(cycle, repairs)
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
        duck = self.accepted_duck(cycle)
        text = self.review_text(cycle, duck)
        review = self.render_review(cycle, duck)
        path = f"reports/{self.tag(cycle)}-review.yaml"
        if self.sha(path) != digest_text(text):
            self.write(path, text)
            self.write(f"reports/{self.tag(cycle)}-review.md", contracts.render_review_md(review))
            self.journal.append("matrix_written", cycle=cycle, duck_recorded=duck is not None, review_sha256=digest_text(text))

    def accepted_duck(self, cycle: int) -> dict[str, Any] | None:
        latest = None
        for path in sorted((self.exec / "results").glob(f"c{cycle:02d}.r*.rubber-duck.*.json")):
            record = read_json(path)
            if record.get("accepted") is not None:
                latest = record
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
        result = self.run_script("gate", [str(CHECKS / "gate.py"), review, "--output", gate])
        self.journal.append("gate_run", cycle=cycle, exit_code=result["exit_code"], seconds=result["seconds"])
        if result["exit_code"] not in OK_EXIT["gate"]:
            return {"status": "failed", "kind": "gate_invalid", "cycle": cycle, "detail": result["stderr"]}
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

        if outcome == "approved" and not finished("sources"):
            result = self.run_script("verify_sources", [str(CHECKS / "verify_sources.py"), "sources/sources-index.md",
                                                        "--output", "sources/sources-check.json", "--force"])
            ran = result["exit_code"] in OK_EXIT["sources"]
            dead = [row["url"] for row in read_json(self.root / "sources/sources-check.json").get("results", [])
                    if row.get("status") == "fail"] if ran and self.has("sources/sources-check.json") else []
            # Settled only when the recheck ran and found nothing dead: a person who fixes the cause, or
            # a site that recovers, is picked up by the next call instead of being skipped for good.
            self.journal.append("delivery_step", step="sources", gate_sha=gate_sha, ok=ran and not dead,
                                exit_code=result["exit_code"], seconds=result["seconds"], detail=result["stderr"],
                                failed=dead)
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
        record = self.load_record(task.task_id)
        narrative = record["result"]["narrative_markdown"]
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
            if attempt != expected:
                return {"accepted": False, "stale": True, "retry": False,
                        "errors": [f"expected attempt {expected}, received {attempt}"]}
            parse_problem = None
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
                errors, normal = self.validate(task, result)
            record = self.load_record(task_id)
            if not record or record["inputs_sha256"] != task.inputs_sha256:
                record = {"task_id": task_id, "stage": task.stage, "cycle": task.cycle, "round": task.round,
                          "inputs_sha256": task.inputs_sha256, "attempts": [], "accepted": None, "result": None}
            issued = next((item for item in self.journal.find("task_issued", task_id=task_id, attempt=attempt)), None)
            seconds = round(self.clock() - datetime.fromisoformat(issued["at"].replace("Z", "+00:00")).timestamp(), 3) \
                if issued else None
            missing = result is None and not parse_problem
            entry = {"attempt": attempt, "outcome": "accepted" if not errors else ("null" if missing else "rejected"),
                     "errors": errors, "result_sha256": digest_json(result) if storable else None,
                     "runtime": runtime or {}}
            record["attempts"].append(entry)
            if not errors:
                self.materialise(task, normal)
                record["accepted"], record["result"] = attempt, normal
            self.write_json(f"reports/execution/results/{task_id}.json", record)
            retry = bool(errors) and len(record["attempts"]) < self.options.max_attempts
            self.journal.append("task_recorded", task_id=task_id, stage=task.stage, kind=task.kind, agent=task.spec.name,
                                cycle=task.cycle, round=task.round, attempt=attempt, outcome=entry["outcome"],
                                errors=errors, seconds=seconds, runtime=runtime or {})
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
                    self.contained(item["path"])
                except InputError as exc:
                    errors.append(str(exc))
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
                "tasks_issued": len(issued), "tasks_recorded": len(recorded),
                "accepted": sum(1 for item in recorded if item["outcome"] == "accepted"),
                "rejected": sum(1 for item in recorded if item["outcome"] == "rejected"),
                "null_results": sum(1 for item in recorded if item["outcome"] == "null"),
                "repairs": sum(1 for item in events if item["event"] == "repair_started"),
                "finished": [item for item in events if item["event"] == "run_finished"]}

    def run(self, backend: Callable[[dict[str, Any]], Any], *, parallel: int = 4) -> dict[str, Any]:
        """Drive the whole run in-process with ``backend`` answering each agent task."""
        self.init()
        for _ in range(500):
            directive = self.next()
            if directive["status"] != "agents":
                return directive
            def answer(task: dict[str, Any]) -> Any:
                try:
                    return backend(task)
                except Exception:  # an agent that fails is a null result, never a crash of the run
                    return None
            with ThreadPoolExecutor(max_workers=max(1, parallel)) as pool:
                answers = list(pool.map(answer, directive["tasks"]))
            for task, result in zip(directive["tasks"], answers):
                self.record(task["task_id"], task["attempt"], task["inputs_sha256"], result)
        raise InputError("the run did not finish within the step limit")
