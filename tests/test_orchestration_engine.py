from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
import os
import subprocess
import tempfile
import threading
import unittest
from collections import Counter
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from unittest import mock

from scripts.checks import gate, health, progress, resume
from scripts.checks.common import InputError, parse_data
from scripts.orchestration import contracts, spec
from scripts.orchestration.engine import Engine, Options
from scripts.orchestration.store import FileLock, Journal, atomic_text, digest_json
from tests.test_checks import SourceHandler

# The test source servers listen on 127.0.0.1, which the author contract refuses outside a lab; the tests of the
# guard itself switch it off again.
os.environ["DOCSWARM_ALLOW_LOCAL_URLS"] = "1"

TOPICS = {"T01": "Enquadramento", "T02": "Alternativas e custos"}
TITLE = "Dimensionamento da plataforma de teste"
OPENING = "A decisão em uma página: comparar capacidade e demanda."
CAPTION = "Figura 1. Capacidade alocável e soma dos requests"
CONCLUSION = "Não altere produção sem essa validação."


def make_link(link: Path, target: Path) -> bool:
    """A directory link that needs no privilege: a junction on Windows, a symlink elsewhere."""
    try:
        if os.name == "nt":
            return subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                                  capture_output=True).returncode == 0
        os.symlink(target, link, target_is_directory=True)
        return True
    except OSError:
        return False


def declaration(name: str, kind: str, body: str, *, extra: str = "", sources: int | None = None) -> str:
    sources = (5 if kind == "author" else 0) if sources is None else sources
    return (f"---\nname: {name}\nkind: {kind}\nrole: Papel de {name}\nmodel: auto\nswarm: demo\n"
            f"sources_min: {sources}\n{extra}---\n{body}")


def build_swarm(root: Path, *, deliverables: str = "  - output/document.md\n", extra_brief: str = "",
                max_cycles: int = 3, topics: dict[str, str] | None = None) -> Path:
    for folder in ("agents/authors", "agents/reviewers", "reports", "output", "sources"):
        (root / folder).mkdir(parents=True, exist_ok=True)
    topic_lines = "".join(f"  {key}: {title}\n" for key, title in (topics or TOPICS).items())
    (root / "brief.md").write_text(
        f'---\nswarm_id: demo\nskill_version: "3.6.0"\nmode: document\nmax_cycles: {max_cycles}\n'
        f"quality_contract: editorial-v1\neditorial_reviewer: reviewer-02-clarity\n{extra_brief}"
        f"deliverables:\n{deliverables}topics:\n{topic_lines}---\n# Demonstração\n\nEnquadramento do documento de teste.\n",
        encoding="utf-8")

    def put(name: str, text: str) -> None:
        (root / "agents" / name).write_text(text, encoding="utf-8")

    put("authors/author-01-platform.md", declaration(
        "author-01-platform", "author", "## Missão\nEscrever a plataforma.\n\n## Tópicos\n- T01 Enquadramento\n"))
    put("authors/author-02-operations.md", declaration(
        "author-02-operations", "author", "## Missão\nEscrever a operação.\n\n## Tópicos\n- T02 Alternativas e custos\n"))
    put("reviewers/reviewer-01-facts.md", declaration(
        "reviewer-01-facts", "reviewer", "## Missão\nVerificar fatos.\n", extra="evidence_class: fact\n", sources=5))
    put("reviewers/reviewer-02-clarity.md", declaration(
        "reviewer-02-clarity", "reviewer", "## Missão\nAvaliar a redação integral.\n", extra="evidence_class: form\n"))
    put("coordinator.md", declaration("coordinator", "coordinator", "## Missão\nConsolidar.\n"))
    put("rubber-duck.md", declaration("rubber-duck", "rubber-duck", "## Missão\nAuditar.\n"))
    return root


class Scripted:
    """An honest fake agent: it obeys the contract of whatever task it is handed."""

    def __init__(self, root: Path, base: str, *, grades: dict[tuple[int, str, str], str] | None = None,
                 mutate: Callable[[dict[str, Any], Any], Any] | None = None,
                 surface_grades: dict[int, str] | None = None, sources: Callable[[str, int, int], list[str]] | None = None,
                 ducks: dict[int, dict[str, Any]] | None = None, divergences: dict[int, list[dict[str, str]]] | None = None,
                 tables: Callable[[str, int, int], str] | None = None):
        self.root, self.base = root, base
        self.grades = grades or {}
        self.mutate = mutate
        self.surface_grades = surface_grades or {}
        self.sources = sources
        self.ducks = ducks or {}
        self.divergences = divergences or {}
        self.tables = tables
        self.calls: list[dict[str, Any]] = []
        self.prompts: dict[tuple[str, int], str] = {}
        self.guard = threading.Lock()

    def __call__(self, task: dict[str, Any]) -> Any:
        with self.guard:
            self.calls.append({key: task[key] for key in ("task_id", "kind", "agent", "cycle", "round", "attempt")})
            self.prompts[(task["task_id"], task["attempt"])] = task["prompt"]
        result = getattr(self, task["kind"].replace("-", "_"))(task)
        return self.mutate(task, result) if self.mutate else result

    def author(self, task):
        agent, code, cycle = task["agent"], task["context"]["source_prefix"], task["cycle"]
        urls = self.sources(agent, cycle, task["round"]) if self.sources else \
            [f"{self.base}/{code}/f{index}" for index in range(1, 6)]
        body = "\n\n".join(f"## {key} {title}\n\nTexto original do {agent} para {key}, revisão {cycle}.{task['round']}."
                           for key, title in task["context"]["topics"].items())
        if self.tables:
            body += "\n\n" + self.tables(agent, cycle, task["round"])
        return {"files": [{"path": f"output/sections/{agent}.md", "content": f"# Seção de {agent}\n\n{body}\n"}],
                "sources": [{"id": f"{code}{index:02d}", "title": f"Fonte {index}", "type": "oficial", "url": url}
                            for index, url in enumerate(urls, 1)],
                "notes": ""}

    def consolidation(self, task):
        sections = "\n\n".join(path.read_text(encoding="utf-8")
                               for path in sorted((self.root / "output" / "sections").glob("*.md")))
        document = (f"# {TITLE}\n\n{OPENING}\n\n{sections}\n\n{CAPTION}\n\n## Conclusão\n\n{CONCLUSION}\n\n"
                    "## Bibliografia\n\nVer o índice de fontes.\n")
        return {"document_markdown": document, "divergences": self.divergences.get(task["cycle"], [])}

    def reviewer(self, task):
        cycle, agent = task["cycle"], task["agent"]
        report = {"topics": [{"topic": key, "grade": self.grades.get((cycle, agent, key), "A"),
                              "justification": f"Avaliado em {key}.", "action": "Corrigir o tópico."
                              if self.grades.get((cycle, agent, key), "A") != "A" else ""}
                             for key in task["context"]["topics"]]}
        if task["context"]["evidence_class"] == "fact":
            report["sources_consulted"] = [{"url": f"{self.base}/check/{index}", "finding": "Confirmado."} for index in range(1, 6)]
        if task["context"]["editorial"]:
            grade = self.surface_grades.get(cycle, "A")
            quotes = dict(zip(("titles", "openings", "body", "captions", "conclusions"),
                              (TITLE, OPENING, "Texto original do", CAPTION, CONCLUSION)))
            report["editorial"] = {"surfaces": [{
                "surface": surface, "grade": grade, "location": f"seção {index + 1}", "quote": quote,
                "justification": "A redação identifica o objeto e preserva a condição.",
                "action": "" if grade == "A" else "Reescrever com o objeto específico."}
                for index, (surface, quote) in enumerate(quotes.items())], "findings": []}
        return report

    def rubber_duck(self, task):
        return self.ducks.get(task["cycle"], {"critical": False, "findings": [],
                                              "consistency_notes": "Matriz e avaliações conferem."})

    def narrative(self, task):
        return {"narrative_markdown": "Decisões tomadas e riscos residuais registrados pelo coordenador."}


class EngineCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        server = ThreadingHTTPServer(("127.0.0.1", 0), SourceHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close()))
        self.base = f"http://127.0.0.1:{server.server_address[1]}"
        self.root = build_swarm(Path(self.temporary.name) / "demo")

    def engine(self, **options) -> Engine:
        return Engine(self.root, Options(**options)) if options else Engine(self.root)

    def agent(self, **kwargs) -> Scripted:
        return Scripted(self.root, self.base, **kwargs)

    def finish(self, agent: Scripted | None = None, **options) -> dict[str, Any]:
        return self.engine(**options).run(agent or self.agent())

    def patched(self, **exits):
        """Replace the named scripts' results; every other script still runs for real."""
        original = Engine.run_script

        def run_script(engine, name, command):
            if name in exits:
                code, message = exits[name]
                return {"script": name, "exit_code": code, "seconds": 0.0, "stderr": message}
            return original(engine, name, command)

        return mock.patch.object(Engine, "run_script", run_script)


class PipelineTests(EngineCase):
    def test_a_clean_swarm_is_approved_in_one_cycle(self):
        done = self.finish()
        self.assertEqual((done["status"], done["outcome"], done["cycle"]), ("done", "approved", 1))
        for artifact in ("output/document.md", "sources/sources-index.md", "reports/cycle-01-editorial-text.txt",
                         "reports/cycle-01-reviewer-01-facts.json", "reports/cycle-01-reviewer-02-clarity.json",
                         "reports/cycle-01-review.yaml", "reports/cycle-01-rubberduck.md", "reports/cycle-01-gate.json",
                         "reports/cycle-01-tables-check.json", "reports/cycle-01-nomenclature.json",
                         "sources/sources-check.json", "reports/final-report.md", "reports/memory-proposal.json"):
            self.assertTrue((self.root / artifact).is_file(), artifact)
        report = (self.root / "reports" / "final-report.md").read_text(encoding="utf-8")
        self.assertIn("riscos residuais registrados pelo coordenador", report)
        self.assertNotIn("<!-- COORDINATOR", report)

    def test_what_the_engine_writes_passes_the_unmodified_legacy_readers(self):
        self.finish()
        review = self.root / "reports" / "cycle-01-review.yaml"
        self.assertEqual(gate.evaluate_current(parse_data(review.read_text(encoding="utf-8")), self.root)["outcome"],
                         "approved")
        snapshot = progress.snapshot(self.root)
        [cycle] = snapshot["cycles"]
        self.assertEqual(cycle["issues"], [])
        self.assertTrue(cycle["consistent"])
        self.assertEqual((cycle["gate"]["status"], cycle["gate"]["outcome"]), ("verified", "approved"))
        self.assertEqual({row["id"] for row in snapshot["agents"]},
                         {"author-01-platform", "author-02-operations", "reviewer-01-facts",
                          "reviewer-02-clarity", "coordinator", "rubber-duck"})
        self.assertTrue(resume.project(self.root)["complete"])
        self.assertEqual(health.compose(self.root)["state"], "closed")

    def test_the_gate_decision_is_the_legacy_gates_not_the_engines(self):
        self.finish()
        record = json.loads((self.root / "reports" / "cycle-01-gate.json").read_text(encoding="utf-8"))
        self.assertEqual(record["exit_code"], 0)
        self.assertEqual(record["result"]["outcome"], "approved")
        self.assertEqual(record["review_file"], "cycle-01-review.yaml")

    def test_independent_agents_are_dispatched_together(self):
        engine = self.engine()
        engine.init()
        first = engine.next()
        self.assertEqual(first["status"], "agents")
        self.assertEqual({task["agent"] for task in first["tasks"]}, {"author-01-platform", "author-02-operations"})
        engine.run(self.agent())
        agent = self.agent()
        later = self.engine()
        later.run(agent)
        self.assertEqual(agent.calls, [], "a finished run asks nothing again")

    def test_the_agents_get_no_tool_that_writes_or_runs_commands(self):
        engine = self.engine()
        engine.init()
        for task in engine.next()["tasks"]:
            self.assertFalse(set(task["tools"]) & {"create", "edit", "powershell", "apply_patch", "bash"})

    def test_every_prompt_carries_the_schema_its_answer_must_obey(self):
        seen = []
        agent = self.agent()

        def spy(task):
            rendered = json.dumps(task["schema"], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            seen.append((task["kind"], rendered in task["prompt"]))
            return agent(task)

        self.engine().run(spy)
        self.assertEqual({kind for kind, _ in seen}, {"author", "consolidation", "reviewer", "rubber-duck", "narrative"})
        self.assertTrue(all(found for _, found in seen), "a backend without native schema enforcement still sees the contract")

    def test_a_task_carries_everything_a_backend_needs_and_nothing_secret(self):
        engine = self.engine()
        engine.init()
        task = engine.next()["tasks"][0]
        for key in ("task_id", "attempt", "inputs_sha256", "kind", "label", "prompt", "schema", "model", "context"):
            self.assertIn(key, task)
        self.assertTrue(task["label"].endswith(f".a{task['attempt']}"))
        self.assertIn("Dimensionamento", engine.brief_body + "Dimensionamento")
        self.assertNotIn("sk-", task["prompt"])


class DeterminismTests(EngineCase):
    def test_the_same_state_yields_byte_identical_prompts(self):
        first = self.engine()
        first.init()
        left = first.next()["tasks"]
        right = self.engine().next()["tasks"]
        self.assertEqual([task["prompt"] for task in left], [task["prompt"] for task in right])
        self.assertEqual([task["inputs_sha256"] for task in left], [task["inputs_sha256"] for task in right])

    def test_a_changed_declaration_changes_the_task_identity(self):
        engine = self.engine()
        engine.init()
        before = {task["agent"]: task["inputs_sha256"] for task in engine.next()["tasks"]}
        path = self.root / "agents" / "authors" / "author-01-platform.md"
        path.write_text(path.read_text(encoding="utf-8") + "\nNova exigência.\n", encoding="utf-8")
        after = {task["agent"]: task["inputs_sha256"] for task in self.engine().next()["tasks"]}
        self.assertNotEqual(before["author-01-platform"], after["author-01-platform"])
        self.assertEqual(before["author-02-operations"], after["author-02-operations"])

    def test_no_prompt_contains_a_timestamp_or_random_identifier(self):
        engine = self.engine()
        engine.init()
        prompt = engine.next()["tasks"][0]["prompt"]
        self.assertNotRegex(prompt, r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")
        self.assertNotRegex(prompt, r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


class FeedbackTests(EngineCase):
    def test_a_rejected_cycle_redispatches_only_the_author_of_the_blocked_topic(self):
        agent = self.agent(grades={(1, "reviewer-01-facts", "T02"): "B+"})
        done = self.finish(agent)
        self.assertEqual((done["outcome"], done["cycle"]), ("approved", 2))
        second = sorted({call["agent"] for call in agent.calls if call["cycle"] == 2 and call["kind"] == "author"})
        self.assertEqual(second, ["author-02-operations"])
        prompt = next(text for (task, _), text in agent.prompts.items() if task.startswith("c02.r0.authors."))
        self.assertIn("Pendências obrigatórias", prompt)
        self.assertIn("Tópico T02 (B+, reviewer-01-facts)", prompt)
        self.assertIn("Texto original do author-02-operations", prompt, "the author sees its own previous section")

    def test_an_editorial_block_goes_to_every_author_because_it_spans_the_document(self):
        agent = self.agent(surface_grades={1: "B+"})
        done = self.finish(agent)
        self.assertEqual((done["outcome"], done["cycle"]), ("approved", 2))
        second = sorted({call["agent"] for call in agent.calls if call["cycle"] == 2 and call["kind"] == "author"})
        self.assertEqual(second, ["author-01-platform", "author-02-operations"])
        prompt = next(text for (task, _), text in agent.prompts.items() if task.startswith("c02.r0.authors."))
        self.assertIn("Redação, titles (B+)", prompt, "the author is told which surface failed and why")

    def test_a_blocking_editorial_finding_reaches_the_authors_even_with_every_surface_at_A(self):
        def blocking(task, result):
            if task["agent"] == "reviewer-02-clarity" and task["cycle"] == 1:
                result = copy.deepcopy(result)
                result["editorial"]["findings"] = [{"severity": "blocking", "location": "seção 1", "quote": OPENING,
                                                    "reason": "A abertura promete mais do que o texto sustenta.",
                                                    "action": "Reduzir a promessa ao que a seção demonstra."}]
            return result

        agent = self.agent(mutate=blocking)
        done = self.finish(agent)
        self.assertEqual((done["outcome"], done["cycle"]), ("approved", 2))
        prompt = next(text for (task, _), text in agent.prompts.items() if task.startswith("c02.r0.authors."))
        self.assertIn("A abertura promete mais do que o texto sustenta.", prompt)

    def test_an_audit_finding_that_rejected_the_cycle_reaches_every_author(self):
        critical = {"critical": True, "consistency_notes": "", "findings": [
            {"severity": "critical", "target": "T02", "evidence": "A conta de custos não fecha com a premissa.",
             "correction": "Refazer a estimativa com a premissa declarada."}]}
        agent = self.agent(ducks={1: critical})
        done = self.finish(agent)
        self.assertEqual((done["outcome"], done["cycle"]), ("approved", 2))
        second = sorted({call["agent"] for call in agent.calls if call["cycle"] == 2 and call["kind"] == "author"})
        self.assertEqual(second, ["author-01-platform", "author-02-operations"])
        prompt = next(text for (task, _), text in agent.prompts.items() if task.startswith("c02.r0.authors."))
        self.assertIn("Auditoria (critical), T02", prompt)

    def test_an_item_that_spans_the_document_widens_the_round_even_when_a_topic_is_also_blocked(self):
        critical = {"critical": True, "consistency_notes": "", "findings": [
            {"severity": "critical", "target": "T02", "evidence": "A conta de custos não fecha.",
             "correction": "Refazer a estimativa."}]}
        for label, options in (("editorial", {"surface_grades": {1: "B+"}}), ("audit", {"ducks": {1: critical}})):
            with self.subTest(item=label):
                root = build_swarm(Path(self.temporary.name) / label)
                agent = Scripted(root, self.base, grades={(1, "reviewer-01-facts", "T02"): "B+"}, **options)
                Engine(root).run(agent)
                second = sorted({call["agent"] for call in agent.calls if call["cycle"] == 2 and call["kind"] == "author"})
                self.assertEqual(second, ["author-01-platform", "author-02-operations"],
                                 "a blocked topic must not narrow a round that also owes the whole document")

    def test_a_divergence_the_consolidator_found_goes_back_to_the_author_it_names(self):
        divergence = [{"topic": "", "author": "author-02-operations", "issue": "Os custos contradizem o enquadramento."}]
        agent = self.agent(grades={(1, "reviewer-01-facts", "T01"): "B+"}, divergences={1: divergence})
        done = self.finish(agent)
        self.assertEqual((done["outcome"], done["cycle"]), ("approved", 2))
        second = sorted({call["agent"] for call in agent.calls if call["cycle"] == 2 and call["kind"] == "author"})
        self.assertEqual(second, ["author-01-platform", "author-02-operations"],
                         "the owner of the blocked topic and the author the divergence names")
        prompt = next(text for (task, _), text in agent.prompts.items() if task.startswith("c02.r0.authors.author-02"))
        self.assertIn("Divergência do consolidador em geral (author-02-operations)", prompt)

    def test_a_divergence_on_a_topic_sends_that_topics_owner_back_to_work(self):
        divergence = [{"topic": "T02", "author": "", "issue": "O tópico T02 contradiz o T01."}]
        agent = self.agent(grades={(1, "reviewer-01-facts", "T01"): "B+"}, divergences={1: divergence})
        self.finish(agent)
        second = sorted({call["agent"] for call in agent.calls if call["cycle"] == 2 and call["kind"] == "author"})
        self.assertEqual(second, ["author-01-platform", "author-02-operations"])

    def test_an_author_whose_topics_cannot_be_recognised_is_kept_not_dropped(self):
        (self.root / "agents" / "authors" / "author-03-visual.md").write_text(
            declaration("author-03-visual", "author", "## Missão\nCuidar da arte.\n"), encoding="utf-8")
        agent = self.agent(grades={(1, "reviewer-01-facts", "T02"): "B+"})
        self.finish(agent)
        second = sorted({call["agent"] for call in agent.calls if call["cycle"] == 2 and call["kind"] == "author"})
        self.assertEqual(second, ["author-02-operations", "author-03-visual"],
                         "unknown ownership is not 'not mine'; the owner of T01 is not asked again")

    def test_a_blocked_topic_nobody_owns_goes_to_every_author(self):
        root = build_swarm(Path(self.temporary.name) / "orphan", topics={**TOPICS, "T03": "Governança"})
        agent = Scripted(root, self.base, grades={(1, "reviewer-01-facts", "T03"): "B+"})
        done = Engine(root).run(agent)
        self.assertEqual((done["outcome"], done["cycle"]), ("approved", 2))
        second = sorted({call["agent"] for call in agent.calls if call["cycle"] == 2 and call["kind"] == "author"})
        self.assertEqual(second, ["author-01-platform", "author-02-operations"],
                         "a pending item must never be left without an author")

    def test_every_reviewer_runs_again_in_the_next_cycle(self):
        agent = self.agent(grades={(1, "reviewer-01-facts", "T02"): "B+"})
        self.finish(agent)
        for cycle in (1, 2):
            reviewers = {call["agent"] for call in agent.calls if call["cycle"] == cycle and call["kind"] == "reviewer"}
            self.assertEqual(reviewers, {"reviewer-01-facts", "reviewer-02-clarity"})

    def test_the_lowest_grade_decides_a_topic_and_the_gate_still_rules(self):
        agent = self.agent(grades={(1, "reviewer-02-clarity", "T01"): "A-"}, mutate=None)
        engine = self.engine()
        engine.run(agent)
        first = parse_data((self.root / "reports" / "cycle-01-review.yaml").read_text(encoding="utf-8"))
        row = next(item for item in first["topics"] if item["topico"] == "T01")
        self.assertEqual((row["nota_minima"], row["revisor_da_minima"], row["bloqueia"]), ("A-", "reviewer-02-clarity", True))
        record = json.loads((self.root / "reports" / "cycle-01-gate.json").read_text(encoding="utf-8"))
        self.assertEqual(record["exit_code"], 1, "A- does not approve")


class ValidationTests(EngineCase):
    def broken_quote(self, task, result):
        if task["agent"] == "reviewer-02-clarity" and task["attempt"] == 1:
            result = copy.deepcopy(result)
            result["editorial"]["surfaces"][0]["quote"] = "uma frase que não existe no documento"
        return result

    def test_a_quote_that_is_not_in_the_text_is_refused_at_once_with_the_exact_reason(self):
        agent = self.agent(mutate=self.broken_quote)
        done = self.finish(agent)
        self.assertEqual(done["outcome"], "approved")
        events = [item for item in Journal(self.root / "reports" / "execution" / "journal.jsonl").events()
                  if item["event"] == "task_recorded" and item["agent"] == "reviewer-02-clarity"]
        self.assertEqual([item["outcome"] for item in events], ["rejected", "accepted"])
        self.assertIn("does not appear in the reviewed text", events[0]["errors"][0])
        retry = agent.prompts[("c01.r0.reviewers.reviewer-02-clarity", 2)]
        self.assertIn("a tentativa anterior foi recusada", retry)
        self.assertIn("uma frase que não existe", retry)

    def test_the_retry_prompt_differs_so_the_runtime_cannot_replay_the_failure(self):
        agent = self.agent(mutate=self.broken_quote)
        self.finish(agent)
        first = agent.prompts[("c01.r0.reviewers.reviewer-02-clarity", 1)]
        second = agent.prompts[("c01.r0.reviewers.reviewer-02-clarity", 2)]
        self.assertNotEqual(first, second)
        labels = {call["task_id"] + f".a{call['attempt']}" for call in agent.calls}
        self.assertIn("c01.r0.reviewers.reviewer-02-clarity.a1", labels)
        self.assertIn("c01.r0.reviewers.reviewer-02-clarity.a2", labels)

    def test_an_agent_that_never_produces_a_valid_result_blocks_for_a_person(self):
        def fail(task, result):
            return None if task["agent"] == "author-01-platform" else result

        engine = self.engine()
        outcome = engine.run(self.agent(mutate=fail))
        self.assertEqual((outcome["status"], outcome["kind"]), ("blocked", "task_failed"))
        self.assertEqual([item["task_id"] for item in outcome["tasks"]], ["c01.r0.authors.author-01-platform"])
        before = len(Journal(self.root / "reports" / "execution" / "journal.jsonl").events())
        again = self.engine().next()
        self.assertEqual((again["status"], again["kind"]), ("blocked", "task_failed"))
        issued = [item for item in Journal(self.root / "reports" / "execution" / "journal.jsonl").events()[before:]
                  if item["event"] == "task_issued"]
        self.assertEqual(issued, [], "a blocked run does not spend more attempts")
        accepted = engine.load_record("c01.r0.authors.author-02-operations")
        self.assertIsNotNone(accepted["accepted"], "the other author's valid work is kept")

    def test_a_result_for_changed_inputs_is_stale_and_changes_nothing(self):
        engine = self.engine()
        engine.init()
        task = engine.next()["tasks"][0]
        outcome = engine.record(task["task_id"], task["attempt"], "0" * 64, self.agent().author(task))
        self.assertTrue(outcome["stale"])
        self.assertFalse(engine.record_path(task["task_id"]).exists())
        self.assertEqual(Journal(self.root / "reports" / "execution" / "journal.jsonl").count("task_stale"), 1)

    def test_a_wrong_attempt_number_is_not_recorded(self):
        engine = self.engine()
        engine.init()
        task = engine.next()["tasks"][0]
        outcome = engine.record(task["task_id"], 2, task["inputs_sha256"], self.agent().author(task))
        self.assertTrue(outcome["stale"])
        self.assertIn("expected attempt 1", outcome["errors"][0])

    def test_recording_the_same_accepted_result_twice_is_harmless(self):
        engine = self.engine()
        engine.init()
        task = engine.next()["tasks"][0]
        result = self.agent().author(task)
        self.assertTrue(engine.record(task["task_id"], 1, task["inputs_sha256"], result)["accepted"])
        again = engine.record(task["task_id"], 1, task["inputs_sha256"], result)
        self.assertTrue(again["accepted"] and again["duplicate"])

    def test_an_unknown_task_is_stale_not_a_crash(self):
        engine = self.engine()
        engine.init()
        self.assertTrue(engine.record("c09.r0.authors.nobody", 1, "0" * 64, {})["stale"])
        self.assertTrue(engine.record("garbage", 1, "0" * 64, {})["stale"])


class ContractTests(EngineCase):
    def setUp(self):
        super().setUp()
        self.engine_ = self.engine()
        self.engine_.init()
        self.author = self.engine_.compiled.by_name("author-01-platform")
        self.good = {"files": [{"path": "output/sections/a.md", "content": "# A\n\ntexto\n"}],
                     "sources": [{"id": f"F1{index:02d}", "title": "t", "type": "oficial", "url": f"https://exemplo.test/{index}"}
                                 for index in range(1, 6)]}

    def check(self, result, owners=None):
        return contracts.check_author(result, spec=self.author, code="F1", primary="output/document.md", owners=owners or {})

    def test_a_valid_author_result_is_accepted(self):
        errors, normal = self.check(self.good)
        self.assertEqual(errors, [])
        self.assertEqual(normal["files"][0]["path"], "output/sections/a.md")

    def test_an_author_cannot_write_the_deliverable_outside_its_folders_or_another_authors_file(self):
        for path, owners, fragment in (
                ("output/document.md", {}, "outside the folders"),
                ("../escape.md", {}, "unsafe"),
                ("/etc/passwd", {}, "unsafe"),
                ("reports/cycle-01-gate.json", {}, "outside the folders"),
                ("output/sections/run.exe", {}, "extension"),
                ("output/sections/a.md", {"output/sections/a.md": "author-02-operations"}, "belongs to author-02-operations")):
            with self.subTest(path=path):
                result = copy.deepcopy(self.good)
                result["files"][0]["path"] = path
                errors, normal = self.check(result, owners)
                self.assertIsNone(normal)
                self.assertTrue(any(fragment in item for item in errors), errors)

    def test_the_consolidated_deliverable_is_reserved_for_the_coordinator_even_inside_an_allowed_folder(self):
        for primary in ("output/sections/a.md", "output/sections/A.MD"):
            with self.subTest(primary=primary):
                errors, normal = contracts.check_author(self.good, spec=self.author, code="F1", primary=primary, owners={})
                self.assertIsNone(normal)
                self.assertTrue(any("consolidated deliverable" in item for item in errors), errors)

    def test_a_path_cannot_leave_its_folder_by_any_spelling(self):
        # Each of these becomes a file the executor writes; "output/sections/../../brief.md" starts inside
        # an allowed folder and would overwrite the brief.
        for path in ("output/sections/../../brief.md", "output\\sections\\..\\..\\brief.md", "//server/share/a.md",
                     "C:/temp/a.md", "output/sections/a:stream.md", "output/sections/a*.md", "output/sections/a?.md",
                     "output/sections/con.md", "output/sections/NUL.txt", "output/sections/lpt1.csv",
                     "output/sections/folder./a.md", "output/sections/a.md.", "output/sections/a.md ",
                     "output/sections/" + "x" * 130 + ".md", "output/sections/a\nb.md"):
            with self.subTest(path=path):
                result = copy.deepcopy(self.good)
                result["files"][0]["path"] = path
                errors, normal = self.check(result)
                self.assertIsNone(normal)
                self.assertTrue(any("unsafe file path" in item for item in errors), errors)
        accepted = copy.deepcopy(self.good)
        accepted["files"][0]["path"] = "output\\sections\\console-notes.md"
        self.assertEqual(self.check(accepted)[0], [], "a name that merely starts like a device name is fine")

    def test_names_that_differ_only_by_case_are_one_file_with_one_owner(self):
        mine = {"output/sections/Overview.md": "author-01-platform"}
        theirs = {"output/sections/Overview.md": "author-02-operations"}
        for candidate in ("output/sections/overview.md", "output/sections/OVERVIEW.md", "output/sections/Overview.md"):
            with self.subTest(candidate=candidate):
                result = copy.deepcopy(self.good)
                result["files"][0]["path"] = candidate
                errors, normal = self.check(result, theirs)
                self.assertIsNone(normal)
                self.assertTrue(any("belongs to author-02-operations" in item for item in errors), errors)
                errors, normal = self.check(result, mine)
                self.assertEqual(errors, [])
                self.assertEqual(normal["files"][0]["path"], "output/sections/Overview.md", "the spelling on record is kept")
        twice = copy.deepcopy(self.good)
        twice["files"] = [{"path": "output/sections/a.md", "content": "x"}, {"path": "output/sections/A.md", "content": "y"}]
        errors, normal = self.check(twice)
        self.assertIsNone(normal)
        self.assertTrue(any("appears twice" in item for item in errors), errors)

    def test_the_text_of_a_source_cannot_smuggle_rows_addresses_or_pipes_into_the_index(self):
        # verify_sources.py reads every address in the index and update_memory.py splits its rows on "|":
        # a title or a type that carries either one, or a line break, adds a source nobody vetted.
        hostile = (("title", "Legit | x | https://atacante.test/"), ("title", "a https://atacante.test/x b"),
                   ("title", "a HTTP://ATACANTE.TEST/x"), ("type", "oficial\n| F199 | x | y | https://atacante.test/ | z |"),
                   ("title", "a\u2028b"), ("title", "a\u0085b"), ("title", "x\x00y"), ("title", "a\x7fb"), ("type", "a|b"),
                   ("title", "t" * 301), ("type", "t" * 81))
        for label, value in hostile:
            with self.subTest(label=label, value=value[:24]):
                result = copy.deepcopy(self.good)
                result["sources"][0][label] = value
                errors, normal = self.check(result)
                self.assertIsNone(normal)
                self.assertTrue(any(f"source F101 {label}" in item for item in errors), errors)
        fine = copy.deepcopy(self.good)
        fine["sources"][0].update(title="RFC 9110: HTTP Semantics (2022), seção 3 — ação", type="norma/oficial")
        self.assertEqual(self.check(fine)[0], [], "ordinary punctuation, accents and slashes are fine")

    def test_the_index_renderer_keeps_every_source_on_one_row_even_for_text_the_contract_never_saw(self):
        index = contracts.render_sources_index([("a", [{"id": "F101", "title": "a\nb | c", "type": "x\ny|z",
                                                        "url": "https://exemplo.test/1"}])])
        rows = [line for line in index.splitlines() if line.startswith("| F1")]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].count("|") - rows[0].count("\\|"), 6, "five cells, and no pipe of the text splits a cell")

    def test_a_source_address_must_be_one_this_machine_may_request(self):
        refused = {
            "http://127.0.0.1:2375/version": "not a public address", "http://[::1]/x": "not a public address",
            "http://169.254.169.254/latest/meta-data": "not a public address", "http://10.0.0.5/x": "not a public address",
            "http://192.168.1.1/": "not a public address", "http://172.16.0.9/": "not a public address",
            "http://100.64.0.1/": "not a public address", "http://0.0.0.0/": "not a public address",
            "http://[::ffff:127.0.0.1]/": "not a public address", "http://[fc00::1]/": "not a public address",
            "http://[fe80::1%25eth0]/": "not a public address", "http://224.0.0.1/": "not a public address",
            "http://localhost/admin": "names this machine", "http://LOCALHOST./x": "names this machine",
            "http://a.localhost/": "names this machine", "http://printer.local/": "names this machine",
            "http://host.internal/x": "names this machine",
            "http://2130706433/": "unusual form", "http://0x7f.0.0.1/": "unusual form", "http://127.1/": "unusual form",
            "http://0177.0.0.1/": "unusual form", "http://0x7f000001/": "unusual form",
            "http://usuario:senha@exemplo.test/": "credentials", "http://exemplo.test@127.0.0.1/": "credentials",
            "https://exemplo.test:99999/x": "malformed",
        }
        with mock.patch.dict(os.environ, {contracts.ALLOW_LOCAL_URLS: "0"}):
            for url, fragment in refused.items():
                with self.subTest(url=url):
                    result = copy.deepcopy(self.good)
                    result["sources"][0]["url"] = url
                    errors, normal = self.check(result)
                    self.assertIsNone(normal)
                    self.assertTrue(any("source F101 cannot be used" in item and fragment in item for item in errors), errors)
            for url in ("https://learn.microsoft.com/en-us/azure/", "http://93.184.216.34/", "https://8.8.8.8/",
                        "https://[2606:4700:4700::1111]/dns", "https://exemplo.test:8443/x?y=1", "https://xn--80ak6aa92e.com/"):
                with self.subTest(url=url):
                    result = copy.deepcopy(self.good)
                    result["sources"][0]["url"] = url
                    self.assertEqual(self.check(result)[0], [], "a public page is accepted")
        with mock.patch.dict(os.environ, {contracts.ALLOW_LOCAL_URLS: "1"}):
            result = copy.deepcopy(self.good)
            result["sources"][0]["url"] = "http://127.0.0.1:8000/x"
            self.assertEqual(self.check(result)[0], [], "the lab switch lets a local test server be cited")

    def test_a_source_a_fact_reviewer_consulted_must_also_be_a_public_address_to_count(self):
        reviewer = self.engine_.compiled.by_name("reviewer-01-facts")
        consulted = [{"url": f"https://exemplo.test/{index}", "finding": "ok"} for index in range(1, 5)]
        consulted.append({"url": "http://127.0.0.1:2375/version", "finding": "ok"})
        result = {"topics": [{"topic": key, "grade": "A", "justification": "ok", "action": ""} for key in TOPICS],
                  "sources_consulted": consulted}
        arguments = dict(spec=reviewer, topics=TOPICS, cycle=1, editorial=False, text="", text_path="t", text_sha="0", artifacts=[])
        with mock.patch.dict(os.environ, {contracts.ALLOW_LOCAL_URLS: "0"}):
            errors, normal = contracts.check_reviewer(result, **arguments)
            self.assertIsNone(normal)
            self.assertTrue(any("4 distinct sources were consulted" in item for item in errors), errors)
        with mock.patch.dict(os.environ, {contracts.ALLOW_LOCAL_URLS: "1"}):
            self.assertEqual(contracts.check_reviewer(result, **arguments)[0], [])

    def test_a_url_with_a_pipe_cannot_split_the_sources_table(self):
        result = copy.deepcopy(self.good)
        result["sources"][0]["url"] = "https://exemplo.test/a|F199|forged"
        errors, normal = self.check(result)
        self.assertIsNone(normal)
        self.assertTrue(any("malformed URL" in item for item in errors), errors)

    def test_a_decomposed_name_is_stored_in_its_composed_form(self):
        result = copy.deepcopy(self.good)
        result["files"][0]["path"] = "output/sections/cafe\u0301.md"
        errors, normal = self.check(result)
        self.assertEqual((errors, normal["files"][0]["path"]), ([], "output/sections/caf\u00e9.md"))

    def test_the_size_of_what_an_author_may_send_is_bounded(self):
        many = copy.deepcopy(self.good)
        many["files"] = [{"path": f"output/sections/f{index}.md", "content": "x"} for index in range(contracts.MAX_FILES + 1)]
        self.assertTrue(any("exceeds the limit" in item for item in self.check(many)[0]))
        for content, fragment in (("   ", "has no content"), ("a\x00b", "binary or exceeds"),
                                  ("x" * (contracts.MAX_FILE_BYTES + 1), "binary or exceeds")):
            with self.subTest(fragment=fragment, size=len(content)):
                result = copy.deepcopy(self.good)
                result["files"][0]["content"] = content
                self.assertTrue(any(fragment in item for item in self.check(result)[0]))

    def test_a_consolidation_must_be_a_markdown_document_with_known_topics_and_authors(self):
        authors = {"author-01-platform", "author-02-operations"}

        def run(result):
            return contracts.check_consolidation(result, topics=TOPICS, authors=authors)[0]

        good = {"document_markdown": "# Título\n\nTexto.\n", "divergences": []}
        self.assertEqual(run(good), [])
        self.assertEqual(run({**good, "divergences": [{"topic": "", "author": "", "issue": "Geral."}]}), [])
        self.assertTrue(any("no Markdown heading" in item for item in run({**good, "document_markdown": "só texto"})))
        self.assertTrue(any("unknown topic" in item for item in
                            run({**good, "divergences": [{"topic": "T09", "author": "", "issue": "x"}]})))
        self.assertTrue(any("unknown author" in item for item in
                            run({**good, "divergences": [{"topic": "", "author": "ghost", "issue": "x"}]})))
        self.assertTrue(run({**good, "divergences": [{"topic": "", "author": "", "issue": ""}]}), "an issue is required")
        huge = "# T\n" + "x" * (contracts.MAX_DOCUMENT_BYTES + 1)
        self.assertTrue(any("supported size" in item for item in run({**good, "document_markdown": huge})))

    def test_an_editorial_block_the_gate_would_reject_is_refused_at_once(self):
        editorial = self.engine_.compiled.by_name("reviewer-02-clarity")
        topics = [{"topic": key, "grade": "A", "justification": "ok", "action": ""} for key in TOPICS]
        errors, normal = contracts.check_reviewer(
            {"topics": topics, "editorial": {"surfaces": [], "findings": []}}, spec=editorial, topics=TOPICS, cycle=1,
            editorial=True, text="Texto revisado", text_path="reports/t.txt", text_sha="0" * 64, artifacts=[])
        self.assertIsNone(normal)
        self.assertTrue(any("editorial review is not valid" in item for item in errors), errors)

    def test_storable_means_plain_json_within_the_limit(self):
        self.assertTrue(contracts.storable({"a": [1, "é", None, True, 2.5]}))
        for bad in ("\ud800", {"a": float("nan")}, {"a": float("inf")}, {1, 2}, object()):
            with self.subTest(bad=repr(bad)):
                self.assertFalse(contracts.storable(bad))
        self.assertFalse(contracts.storable("x" * (contracts.MAX_RESULT_BYTES + 1)))

    def test_a_narrative_must_be_text_within_the_limit(self):
        self.assertEqual(contracts.check_narrative({"narrative_markdown": " Texto "})[1], {"narrative_markdown": "Texto"})
        self.assertTrue(contracts.check_narrative({"narrative_markdown": ""})[0])
        self.assertTrue(contracts.check_narrative([])[0])
        too_long = {"narrative_markdown": "x" * (contracts.MAX_NARRATIVE_CHARS + 1)}
        self.assertTrue(any("exceeds" in item for item in contracts.check_narrative(too_long)[0]))

    def test_sources_must_be_distinct_in_range_and_well_formed(self):
        for change, fragment in (
                (lambda r: r["sources"].pop(), "fewer than the 5"),
                (lambda r: r["sources"][0].update(id="F201"), "followed by two digits"),
                (lambda r: r["sources"][1].update(id="F101"), "is repeated"),
                (lambda r: r["sources"][0].update(url="ftp://x"), "malformed URL"),
                (lambda r: r["sources"][2].update(url=r["sources"][3]["url"]), "fewer than the 5")):
            with self.subTest(fragment=fragment):
                result = copy.deepcopy(self.good)
                change(result)
                self.assertTrue(any(fragment in item for item in self.check(result)[0]))

    def test_an_author_with_no_citation_duty_may_return_no_sources(self):
        visual = spec.AgentSpec(**{**self.author.__dict__, "name": "author-04-visual", "sources_min": 0})
        errors, _ = contracts.check_author({**self.good, "sources": []}, spec=visual, code="F4",
                                           primary="output/document.md", owners={})
        self.assertEqual(errors, [])

    def test_malformed_results_never_crash_the_validator(self):
        for result in (None, [], "text", {}, {"files": "x", "sources": 3}, {"files": [1], "sources": [None]}):
            with self.subTest(result=result):
                self.assertTrue(self.check(result)[0])

    def test_reviewer_results_must_grade_exactly_the_topics_with_valid_grades(self):
        reviewer = self.engine_.compiled.by_name("reviewer-02-clarity")

        def run(report):
            return contracts.check_reviewer(report, spec=reviewer, topics=TOPICS, cycle=1, editorial=False, text="x",
                                            text_path="reports/t.txt", text_sha="0" * 64, artifacts=[])[0]

        row = lambda topic, grade="A", action="": {"topic": topic, "grade": grade, "justification": "ok", "action": action}
        self.assertEqual(run({"topics": [row("T01"), row("T02")]}), [])
        self.assertTrue(any("not graded: T02" in item for item in run({"topics": [row("T01")]})))
        self.assertTrue(any("not in the brief: T09" in item for item in run({"topics": [row("T01"), row("T02"), row("T09")]})))
        self.assertTrue(any("graded twice" in item for item in run({"topics": [row("T01"), row("T01"), row("T02")]})))
        self.assertTrue(any("grade" in item for item in run({"topics": [row("T01", "S"), row("T02")]})))
        self.assertTrue(any("needs an actionable correction" in item for item in run({"topics": [row("T01", "B+"), row("T02")]})))
        self.assertEqual(run({"topics": [row("T01", "B+", "Corrigir"), row("T02")]}), [])

    def test_only_the_assigned_reviewer_may_submit_the_editorial_assessment(self):
        facts = self.engine_.compiled.by_name("reviewer-01-facts")
        errors, _ = contracts.check_reviewer(
            {"topics": [{"topic": key, "grade": "A", "justification": "ok", "action": ""} for key in TOPICS],
             "sources_consulted": [{"url": f"https://e.test/{i}", "finding": "ok"} for i in range(5)],
             "editorial": {"surfaces": [], "findings": []}},
            spec=facts, topics=TOPICS, cycle=1, editorial=False, text="x", text_path="reports/t.txt",
            text_sha="0" * 64, artifacts=[])
        self.assertTrue(any("only the reviewer assigned in the brief" in item for item in errors))

    def test_a_fact_reviewer_must_have_consulted_enough_sources(self):
        facts = self.engine_.compiled.by_name("reviewer-01-facts")
        errors, _ = contracts.check_reviewer(
            {"topics": [{"topic": key, "grade": "A", "justification": "ok", "action": ""} for key in TOPICS],
             "sources_consulted": [{"url": "https://e.test/1", "finding": "ok"}]},
            spec=facts, topics=TOPICS, cycle=1, editorial=False, text="x", text_path="reports/t.txt",
            text_sha="0" * 64, artifacts=[])
        self.assertTrue(any("fewer than the 5" in item for item in errors))

    def test_the_audit_flag_must_agree_with_its_findings(self):
        finding = {"severity": "critical", "target": "t", "evidence": "e", "correction": "c"}
        self.assertTrue(contracts.check_duck({"critical": False, "findings": [finding]})[0])
        self.assertTrue(contracts.check_duck({"critical": True, "findings": []})[0])
        self.assertTrue(contracts.check_duck({"critical": "yes", "findings": []})[0])
        self.assertEqual(contracts.check_duck({"critical": True, "findings": [finding]})[0], [])
        self.assertEqual(contracts.check_duck({"critical": False, "findings": []})[0], [])
        bad = dict(finding, severity="catastrophic")
        self.assertTrue(contracts.check_duck({"critical": False, "findings": [bad]})[0])


class ResumeTests(EngineCase):
    def test_a_new_process_continues_without_asking_again_for_accepted_work(self):
        first = self.engine()
        first.init()
        tasks = first.next()["tasks"]
        agent = self.agent()
        done = next(task for task in tasks if task["agent"] == "author-01-platform")
        self.assertTrue(first.record(done["task_id"], 1, done["inputs_sha256"], agent(done))["accepted"])
        resumed = self.engine().next()
        self.assertEqual([task["agent"] for task in resumed["tasks"]], ["author-02-operations"])
        finished = self.engine().run(agent)
        self.assertEqual(finished["outcome"], "approved")
        author_calls = [call for call in agent.calls if call["agent"] == "author-01-platform"]
        self.assertEqual(len(author_calls), 1, "an accepted author is never paid for twice")

    def test_a_finished_run_is_idempotent(self):
        self.finish()
        journal = Journal(self.root / "reports" / "execution" / "journal.jsonl")
        before = len(journal.events())
        again = self.engine().next()
        self.assertEqual((again["status"], again["outcome"]), ("done", "approved"))
        self.assertEqual(len(journal.events()), before, "calling next on a finished run appends nothing")

    def test_a_mechanical_stage_is_not_repeated_after_a_restart(self):
        self.finish()
        journal = Journal(self.root / "reports" / "execution" / "journal.jsonl")
        scripts = journal.count("script_finished")
        self.engine().next()
        self.assertEqual(journal.count("script_finished"), scripts)
        self.assertEqual(scripts, 3, "sources, tables and nomenclature ran once each")

    def test_the_mechanical_checks_run_in_parallel_in_one_step(self):
        agent = self.agent()
        self.finish(agent)
        events = [item for item in Journal(self.root / "reports" / "execution" / "journal.jsonl").events()
                  if item["event"] == "script_finished" and item["cycle"] == 1]
        self.assertEqual({item["script"] for item in events}, {"sources", "tables", "nomenclature"})

    def test_the_journal_records_timing_for_every_agent_task(self):
        self.finish()
        recorded = [item for item in Journal(self.root / "reports" / "execution" / "journal.jsonl").events()
                    if item["event"] == "task_recorded"]
        self.assertEqual(len(recorded), 7)
        self.assertTrue(all(isinstance(item["seconds"], (int, float)) and item["seconds"] >= 0 for item in recorded))
        self.assertEqual({item["kind"] for item in recorded}, {"author", "consolidation", "reviewer", "rubber-duck", "narrative"})

    def test_status_summarises_the_run_from_the_journal(self):
        self.finish()
        status = self.engine().status()
        self.assertEqual((status["accepted"], status["rejected"], status["null_results"]), (7, 0, 0))
        self.assertEqual(status["finished"][0]["outcome"], "approved")


class RepairTests(EngineCase):
    def unreachable_source(self, base):
        def sources(agent, cycle, round_number):
            urls = [f"{base}/{agent}/{index}" for index in range(1, 5)]
            return urls + [f"{base}/missing" if round_number == 0 else f"{base}/{agent}/ok"]
        return sources

    def test_a_dead_source_sends_a_repair_round_to_the_authors_and_converges(self):
        agent = self.agent(sources=self.unreachable_source(self.base))
        engine = self.engine()
        done = engine.run(agent)
        self.assertEqual((done["outcome"], done["cycle"]), ("approved", 1))
        self.assertEqual(engine.status()["repairs"], 1)
        repaired = next(text for (task, _), text in agent.prompts.items() if task.startswith("c01.r1.authors."))
        self.assertIn("Verificação mecânica (fontes)", repaired)
        self.assertIn("/missing", repaired)
        self.assertTrue((self.root / "reports" / "execution" / "feedback" / "c01.r1.json").is_file())

    def test_checks_that_never_pass_block_for_a_person_at_the_repair_ceiling(self):
        sources = lambda agent, cycle, round_number: [f"{self.base}/{agent}/{i}" for i in range(1, 5)] + [f"{self.base}/missing"]
        outcome = self.engine(max_repairs=1).run(self.agent(sources=sources))
        self.assertEqual((outcome["status"], outcome["kind"]), ("blocked", "checks_failed"))
        self.assertIn("1 repair round", outcome["detail"])
        self.assertTrue(any("/missing" in item["detail"] for item in outcome["items"]))

    def test_reviewers_are_not_paid_for_while_the_checks_are_failing(self):
        sources = lambda agent, cycle, round_number: [f"{self.base}/{agent}/{i}" for i in range(1, 5)] + [f"{self.base}/missing"]
        agent = self.agent(sources=sources)
        self.engine(max_repairs=1).run(agent)
        self.assertEqual([call for call in agent.calls if call["kind"] == "reviewer"], [])

    def test_a_marked_table_that_does_not_add_up_sends_a_repair_round_to_the_authors(self):
        def table(agent, cycle, round_number):
            last = 30 if round_number == 0 else 40
            return (f'<!-- check: sum column="Percentual" target=100 -->\n| Item | Percentual |\n|---|---:|\n'
                    f"| A | 60 |\n| B | {last} |")

        agent = self.agent(tables=table)
        done = self.engine().run(agent)
        self.assertEqual((done["outcome"], done["cycle"]), ("approved", 1))
        repaired = next(text for (task, _), text in agent.prompts.items() if task.startswith("c01.r1.authors."))
        self.assertIn("Verificação mecânica (tabelas)", repaired)
        self.assertEqual([call for call in agent.calls if call["kind"] == "reviewer" and call["round"] == 0], [],
                         "reviewers only ever see the repaired document")

    def test_a_repair_round_keeps_what_the_cycle_already_owed_the_authors(self):
        def sources(agent, cycle, round_number):
            dead = cycle == 2 and round_number == 0
            return [f"{self.base}/{agent}/{i}" for i in range(1, 5)] + [f"{self.base}/{'missing' if dead else agent + '/ok'}"]

        agent = self.agent(grades={(1, "reviewer-01-facts", "T02"): "B+"}, sources=sources)
        done = self.engine().run(agent)
        self.assertEqual((done["outcome"], done["cycle"]), ("approved", 2))
        repaired = next(text for (task, _), text in agent.prompts.items() if task.startswith("c02.r1.authors.author-02"))
        self.assertIn("Tópico T02 (B+, reviewer-01-facts)", repaired, "the reviewers' pending item survives the repair")
        self.assertIn("Verificação mecânica (fontes)", repaired)
        widened = sorted({call["agent"] for call in agent.calls
                          if call["cycle"] == 2 and call["round"] == 1 and call["kind"] == "author"})
        self.assertEqual(widened, ["author-01-platform", "author-02-operations"],
                         "a failing check is about the whole document, so every author repairs")

    def test_the_three_mechanical_checks_really_run_at_the_same_time(self):
        meeting = threading.Barrier(3, timeout=20)
        original = Engine.run_script

        def meet(engine, name, command):
            if name in ("sources", "tables", "nomenclature"):
                meeting.wait()
            return original(engine, name, command)

        with mock.patch.object(Engine, "run_script", meet):
            done = self.finish()
        self.assertEqual(done["outcome"], "approved")

    def test_a_nomenclature_check_that_cannot_run_is_a_failure_not_a_pass(self):
        with self.patched(nomenclature=(1, "cannot read the text")):
            outcome = self.engine().run(self.agent())
        self.assertEqual((outcome["status"], outcome["kind"], outcome["script"]), ("failed", "script_error", "nomenclature"))


class RecordTests(EngineCase):
    def first_task(self, engine):
        engine.init()
        return engine.next()["tasks"][0]

    def test_a_null_result_asks_for_one_more_attempt_and_then_stops_asking(self):
        engine = self.engine()
        task = self.first_task(engine)
        first = engine.record(task["task_id"], 1, task["inputs_sha256"], None)
        self.assertEqual((first["accepted"], first["retry"]), (False, True))
        engine.next()  # a retry is asked for, and so issued, by the next directive
        second = engine.record(task["task_id"], 2, task["inputs_sha256"], None)
        self.assertEqual((second["accepted"], second["retry"]), (False, False))
        status = engine.status()
        self.assertEqual((status["null_results"], status["rejected"], status["accepted"]), (2, 0, 0))

    def test_an_invalid_result_is_counted_as_rejected_not_as_missing(self):
        engine = self.engine()
        task = self.first_task(engine)
        outcome = engine.record(task["task_id"], 1, task["inputs_sha256"], {"files": []})
        self.assertEqual((outcome["accepted"], outcome["retry"]), (False, True))
        status = engine.status()
        self.assertEqual((status["null_results"], status["rejected"]), (0, 1))

    def test_a_result_that_cannot_be_stored_is_refused_not_a_crash(self):
        engine = self.engine()
        task = self.first_task(engine)
        lone = {"files": [{"path": "output/sections/a.md", "content": "texto \ud800 quebrado"}], "sources": []}
        for attempt, bad in enumerate((lone, {"files": [], "sources": [], "x": float("nan")}), 1):
            if attempt > 1:
                engine.next()
            outcome = engine.record(task["task_id"], attempt, task["inputs_sha256"], bad)
            self.assertFalse(outcome["accepted"])
            self.assertTrue(any("plain JSON text" in item for item in outcome["errors"]), outcome["errors"])
        self.assertEqual(engine.load_record(task["task_id"])["accepted"], None, "the record on disk is still readable")
        self.assertFalse((self.root / "output" / "sections").exists(), "nothing of a refused result is written")

    def test_what_a_backend_reports_about_a_run_is_kept_only_as_plain_scalars(self):
        engine = self.engine()
        task = self.first_task(engine)
        engine.record(task["task_id"], 1, task["inputs_sha256"], self.agent().author(task),
                      runtime={"resolved_model": "modelo-x", "active_ms": 1234, "nested": {"a": 1}, "bad": "\ud800", 3: "x"})
        runtime = engine.load_record(task["task_id"])["attempts"][0]["runtime"]
        self.assertEqual(runtime, {"resolved_model": "modelo-x", "active_ms": 1234})

    def test_a_json_answer_wrapped_in_prose_or_a_fence_is_recovered_not_retried(self):
        wrappers = {"bare": "{plain}", "fenced": "Segue o resultado:\n```json\n{plain}\n```\nFim.",
                    "prose": "Aqui está a entrega. {plain} Espero que ajude.", "padded": "\n\n  {plain}  \n",
                    "braces in the prose": "Use as chaves {assim}.\n```json\n{plain}\n```\nFim {x}"}
        for label, template in wrappers.items():
            with self.subTest(wrapper=label):
                root = build_swarm(Path(self.temporary.name) / label.replace(" ", "-"))
                engine = Engine(root)
                engine.init()
                task = engine.next()["tasks"][0]
                plain = json.dumps(Scripted(root, self.base).author(task), ensure_ascii=False)
                outcome = engine.record(task["task_id"], 1, task["inputs_sha256"], template.replace("{plain}", plain))
                self.assertTrue(outcome["accepted"], outcome)

    def test_an_answer_that_is_not_json_is_a_rejection_with_the_reason_not_a_missing_result(self):
        engine = self.engine()
        task = self.first_task(engine)
        for attempt, text in enumerate(("Não consegui concluir a tarefa.", "{ isto não é json }"), 1):
            if attempt > 1:
                engine.next()
            outcome = engine.record(task["task_id"], attempt, task["inputs_sha256"], text)
            self.assertFalse(outcome["accepted"])
            self.assertTrue(any("not a JSON object" in item for item in outcome["errors"]), outcome["errors"])
        status = engine.status()
        self.assertEqual((status["rejected"], status["null_results"]), (2, 0))

    def test_a_json_value_that_is_not_an_object_is_not_an_answer(self):
        for text in ("[1, 2]", "42", '"texto"', "null"):
            with self.subTest(text=text):
                value, problem = contracts.parse_agent_json(text)
                self.assertIsNone(value)
                self.assertIn("not a JSON object", problem)
        self.assertEqual(contracts.parse_agent_json(' {"a": 1} ')[0], {"a": 1})

    def test_a_changed_brief_reopens_the_work_and_the_record_starts_afresh(self):
        engine = self.engine()
        task = self.first_task(engine)
        agent = self.agent()
        self.assertTrue(engine.record(task["task_id"], 1, task["inputs_sha256"], agent(task))["accepted"])
        brief = self.root / "brief.md"
        brief.write_text(brief.read_text(encoding="utf-8") + "\nUm novo requisito do enquadramento.\n", encoding="utf-8")
        again = self.engine().next()
        reopened = next(item for item in again["tasks"] if item["task_id"] == task["task_id"])
        self.assertEqual(reopened["attempt"], 1)
        self.assertNotEqual(reopened["inputs_sha256"], task["inputs_sha256"])
        later = self.engine()
        self.assertTrue(later.record(reopened["task_id"], 1, reopened["inputs_sha256"], agent(reopened))["accepted"])
        self.assertEqual(len(later.load_record(task["task_id"])["attempts"]), 1,
                         "new inputs start a new record instead of piling onto the old one")

    def test_a_folder_that_links_outside_the_swarm_cannot_carry_a_write_out_of_it(self):
        outside = Path(self.temporary.name) / "outside"
        outside.mkdir()
        if not make_link(self.root / "output" / "sections", outside):
            self.skipTest("this platform cannot create directory links without privileges")
        engine = self.engine()
        engine.init()
        task = next(item for item in engine.next()["tasks"] if item["agent"] == "author-01-platform")
        outcome = engine.record(task["task_id"], 1, task["inputs_sha256"], self.agent().author(task))
        self.assertFalse(outcome["accepted"])
        self.assertTrue(any("resolves outside the swarm folder" in item for item in outcome["errors"]), outcome["errors"])
        self.assertEqual(list(outside.iterdir()), [])


class ScopeTests(EngineCase):
    def refused(self, root: Path, message: str):
        with self.assertRaisesRegex(InputError, message):
            Engine(root).init()

    def test_a_presentation_swarm_is_refused_not_half_run(self):
        root = build_swarm(Path(self.temporary.name) / "p", extra_brief="artifact_type: presentation\n")
        self.refused(root, "presentation swarms are not supported")

    def test_a_non_markdown_deliverable_is_refused(self):
        root = build_swarm(Path(self.temporary.name) / "pdf", deliverables="  - output/document.pdf\n")
        self.refused(root, "only a Markdown deliverable")

    def test_more_than_one_deliverable_is_refused(self):
        root = build_swarm(Path(self.temporary.name) / "two", deliverables="  - output/a.md\n  - output/b.md\n")
        self.refused(root, "exactly one Markdown deliverable")

    def test_a_brief_in_presentation_mode_is_refused(self):
        root = build_swarm(Path(self.temporary.name) / "deck")
        brief = (root / "brief.md").read_text(encoding="utf-8").replace("mode: document", "mode: presentation")
        (root / "brief.md").write_text(brief, encoding="utf-8")
        self.refused(root, "presentation swarms are not supported")

    def test_a_deliverable_outside_the_swarm_is_refused(self):
        root = build_swarm(Path(self.temporary.name) / "out", deliverables="  - ../elsewhere.md\n")
        self.refused(root, "outside swarm|inside swarm|escape|output/")

    def test_a_deliverable_that_is_not_under_output_is_refused(self):
        root = build_swarm(Path(self.temporary.name) / "reports", deliverables="  - reports/document.md\n")
        self.refused(root, "must live under output/")

    def test_a_brief_without_a_positive_integer_ceiling_is_refused(self):
        for index, value in enumerate(("0", "-1", '"3"', "true", "2.5")):
            with self.subTest(max_cycles=value):
                root = build_swarm(Path(self.temporary.name) / f"ceiling{index}", max_cycles=value)
                self.refused(root, "positive integer max_cycles")

    def test_the_editorial_contract_needs_a_named_reviewer(self):
        root = build_swarm(Path(self.temporary.name) / "noeditor")
        brief = (root / "brief.md").read_text(encoding="utf-8").replace("editorial_reviewer: reviewer-02-clarity\n", "")
        (root / "brief.md").write_text(brief, encoding="utf-8")
        self.refused(root, "needs editorial_reviewer")

    def test_a_swarm_that_already_has_coordinator_flow_cycles_is_refused(self):
        (self.root / "reports" / "cycle-01-review.yaml").write_text("{}", encoding="utf-8")
        self.refused(self.root, "starts new swarms only")

    def test_a_brief_without_topics_is_refused(self):
        root = build_swarm(Path(self.temporary.name) / "notopics")
        brief = (root / "brief.md").read_text(encoding="utf-8")
        start, end = brief.index("topics:"), brief.index("---\n#")
        (root / "brief.md").write_text(brief[:start] + brief[end:], encoding="utf-8")
        self.refused(root, "declare topics")

    def test_every_problem_in_the_swarm_is_reported_before_any_agent_is_paid_for(self):
        root = build_swarm(Path(self.temporary.name) / "many")
        (root / "agents" / "rubber-duck.md").unlink()
        (root / "agents" / "coordinator.md").unlink()
        with self.assertRaises(InputError) as raised:
            Engine(root).init()
        self.assertIn("no rubber duck", str(raised.exception))
        self.assertIn("no coordinator", str(raised.exception))


class RobustnessTests(EngineCase):
    def test_the_matrix_fails_closed_until_the_audit_is_recorded(self):
        no_audit = lambda task, result: None if task["kind"] == "rubber-duck" else result
        outcome = self.engine().run(self.agent(mutate=no_audit))
        self.assertEqual(outcome["kind"], "task_failed")
        review = self.root / "reports" / "cycle-01-review.yaml"
        self.assertTrue(parse_data(review.read_text(encoding="utf-8"))["rubberduck"]["critico"])
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertNotEqual(gate.main([str(review)]), 0, "a gate run too early must not approve")

    def test_a_failing_memory_proposal_is_a_warning_not_a_false_success(self):
        # Regression: exit 1 meant "findings" for the verifiers, so a failed update_memory reported done silently.
        with self.patched(update_memory=(1, "boom")):
            done = self.finish()
        self.assertEqual(done["outcome"], "approved")
        self.assertTrue(any("memory proposal was not generated: boom" in item for item in done["warnings"]))
        again = self.engine().next()
        self.assertEqual(again["status"], "done")
        self.assertEqual(len(again["warnings"]), 1)
        journal = Journal(self.root / "reports" / "execution" / "journal.jsonl")
        self.assertEqual(journal.count("delivery_step", step="memory"), 1, "housekeeping is tried once")

    def test_a_failing_final_report_is_a_failure_and_is_retried_not_skipped(self):
        with self.patched(final_report=(1, "report exploded")):
            outcome = self.engine().run(self.agent())
        self.assertEqual((outcome["status"], outcome["kind"]), ("failed", "script_error"))
        self.assertIn("report exploded", outcome["detail"])
        self.assertFalse((self.root / "reports" / "final-report.md").exists())
        recovered = self.engine().run(self.agent())
        self.assertEqual((recovered["status"], recovered["outcome"]), ("done", "approved"))
        self.assertTrue((self.root / "reports" / "final-report.md").is_file())

    def test_a_script_that_cannot_run_is_reported_and_retried(self):
        with self.patched(tables=(2, "cannot read the document")):
            outcome = self.engine().run(self.agent())
        self.assertEqual((outcome["status"], outcome["kind"], outcome["script"]), ("failed", "script_error", "tables"))
        resumed = self.engine().run(self.agent())
        self.assertEqual((resumed["status"], resumed["outcome"]), ("done", "approved"),
                         "the next call retries the script and the run finishes")

    def test_an_invalid_gate_result_is_a_failure_not_an_approval(self):
        with self.patched(gate=(3, "INVALID: boom")):
            outcome = self.engine().run(self.agent())
        self.assertEqual((outcome["status"], outcome["kind"]), ("failed", "gate_invalid"))
        self.assertFalse((self.root / "reports" / "final-report.md").exists())

    def test_an_escalated_run_delivers_the_report_but_proposes_no_memory(self):
        root = build_swarm(Path(self.temporary.name) / "esc", max_cycles=1)
        agent = Scripted(root, self.base, grades={(1, "reviewer-01-facts", "T02"): "B+"})
        done = Engine(root).run(agent)
        self.assertEqual((done["status"], done["outcome"]), ("done", "escalated"))
        self.assertTrue((root / "reports" / "final-report.md").is_file())
        self.assertFalse((root / "reports" / "memory-proposal.json").exists())
        journal = Journal(root / "reports" / "execution" / "journal.jsonl")
        self.assertEqual(journal.count("delivery_step", step="sources"), 0,
                         "an escalation is delivered with its report; the final recheck belongs to an approval")
        snapshot = progress.snapshot(root)
        self.assertEqual(snapshot["cycles"][0]["gate"]["outcome"], "escalate")
        self.assertIn("riscos residuais", (root / "reports" / "final-report.md").read_text(encoding="utf-8"))

    def test_the_cycle_ceiling_is_the_briefs_not_the_engines(self):
        root = build_swarm(Path(self.temporary.name) / "three", max_cycles=3)
        stubborn = Scripted(root, self.base, grades={(cycle, "reviewer-01-facts", "T02"): "B+" for cycle in (1, 2, 3)})
        done = Engine(root).run(stubborn)
        self.assertEqual((done["outcome"], done["cycle"]), ("escalated", 3))

    def test_a_torn_final_journal_line_is_recovered_and_corruption_elsewhere_is_not_hidden(self):
        path = Path(self.temporary.name) / "journal.jsonl"
        journal = Journal(path)
        journal.append("first")
        with open(path, "a", encoding="utf-8") as stream:
            stream.write('{"seq": 2, "event": "torn')
        self.assertEqual([item["event"] for item in journal.events()], ["first"])
        journal.append("second")
        self.assertEqual([item["event"] for item in Journal(path).events()], ["first", "second"])
        path.write_text('{"event": "ok"}\nnot json\n{"event": "after"}\n', encoding="utf-8")
        with self.assertRaises(InputError):
            Journal(path).events()

    def test_a_second_operation_cannot_enter_while_the_swarm_is_locked(self):
        path = Path(self.temporary.name) / "locks" / ".lock"
        with FileLock(path).held():
            with self.assertRaisesRegex(InputError, "another executor operation"):
                with FileLock(path).held(timeout=0.3):
                    self.fail("the second holder must not get in")
        with FileLock(path).held(timeout=1):
            pass

    def test_tampering_with_the_matrix_is_overwritten_and_the_gate_runs_again(self):
        self.finish()
        review = self.root / "reports" / "cycle-01-review.yaml"
        honest = review.read_text(encoding="utf-8")
        forged = honest.replace('"nota_minima": "A"', '"nota_minima": "C"', 1)
        self.assertNotEqual(forged, honest)
        review.write_text(forged, encoding="utf-8")
        outcome = self.engine().next()
        self.assertEqual((outcome["status"], outcome["outcome"]), ("done", "approved"))
        self.assertEqual(review.read_text(encoding="utf-8"), honest, "the matrix is rederived from the reviewers' own grades")
        record = json.loads((self.root / "reports" / "cycle-01-gate.json").read_text(encoding="utf-8"))
        self.assertEqual(record["review_sha256"], hashlib.sha256(review.read_bytes()).hexdigest(),
                         "the gate was run again for the bytes now on disk")

    def test_a_gate_record_whose_exit_code_contradicts_its_result_is_rewritten(self):
        self.finish()
        gate_path = self.root / "reports" / "cycle-01-gate.json"
        record = json.loads(gate_path.read_text(encoding="utf-8"))
        record["exit_code"] = 1
        gate_path.write_text(json.dumps(record), encoding="utf-8")
        self.assertEqual(self.engine().next()["outcome"], "approved")
        self.assertEqual(json.loads(gate_path.read_text(encoding="utf-8"))["exit_code"], 0,
                         "the legacy readers would call the inconsistent record invalid; the gate wrote it again")

    def test_the_engine_itself_refuses_to_write_outside_the_swarm(self):
        engine = self.engine()
        engine.init()
        for relative in ("../escape.txt", "output/../../escape.txt"):
            with self.subTest(relative=relative), self.assertRaisesRegex(InputError, "resolves outside the swarm folder"):
                engine.write(relative, "x")
        self.assertFalse((Path(self.temporary.name) / "escape.txt").exists())

    def test_a_deleted_matrix_is_rebuilt_from_the_reviewers_and_the_verdict_stands(self):
        agent = self.agent()
        self.finish(agent)
        review = self.root / "reports" / "cycle-01-review.yaml"
        honest = review.read_bytes()
        review.unlink()
        calls = len(agent.calls)
        outcome = self.engine().next()
        self.assertEqual((outcome["status"], outcome["outcome"]), ("done", "approved"))
        self.assertEqual(review.read_bytes(), honest, "the same grades and audit give the same bytes")
        self.assertEqual(len(agent.calls), calls)

    def drive_until_recorded(self, engine: Engine, agent: Scripted, kind: str, cycle: int = 1) -> None:
        """Run the swarm by hand until every task of the directive that holds a ``kind`` result has been recorded."""
        for _ in range(60):
            directive = engine.next()
            self.assertEqual(directive["status"], "agents", f"the run ended before a {kind} result: {directive}")
            found = False
            for task in directive["tasks"]:
                outcome = engine.record(task["task_id"], task["attempt"], task["inputs_sha256"], agent(task))
                self.assertTrue(outcome["accepted"], outcome)
                found = found or (task["kind"] == kind and task["cycle"] == cycle)
            if found:
                return
        self.fail(f"no {kind} result was recorded")

    def test_a_reviewer_file_edited_after_the_fact_is_restored_and_changes_no_verdict(self):
        # The reviewers' own accepted results are the grades.  The files under reports/ are a copy for the
        # legacy readers: an edit there, up or down, is undone and counts for nothing.
        agent = self.agent()
        self.finish(agent)
        report = self.root / "reports" / "cycle-01-reviewer-01-facts.json"
        honest = report.read_bytes()
        data = json.loads(honest)
        data["topics"][0]["grade"], data["topics"][0]["action"] = "B", "Corrigir."
        report.write_text(json.dumps(data), encoding="utf-8")
        calls = len(agent.calls)
        outcome = self.engine().next()
        self.assertEqual((outcome["status"], outcome["outcome"], outcome["cycle"]), ("done", "approved", 1))
        self.assertEqual(report.read_bytes(), honest, "rewritten from the verified result")
        self.assertEqual(len(agent.calls), calls, "an edited copy costs no agent")

    def test_a_grade_raised_in_a_reviewer_file_before_the_verdict_never_reaches_the_matrix(self):
        path = self.root / "reports" / "cycle-01-reviewer-01-facts.json"
        upgraded, seen_later = [], []

        def raise_the_grade(task, result):
            if task["kind"] == "rubber-duck" and task["cycle"] == 1:
                data = json.loads(path.read_text(encoding="utf-8"))
                for row in data["topics"]:
                    row["grade"], row["action"] = "A", ""
                path.write_text(json.dumps(data), encoding="utf-8")
                upgraded.append(path)
            if task["kind"] == "author" and task["cycle"] == 2 and not seen_later:
                # Cycle 1 has been judged but nothing has been delivered yet.
                seen_later.append({row["topic"]: row["grade"] for row in json.loads(path.read_text(encoding="utf-8"))["topics"]})
            return result

        agent = self.agent(grades={(1, "reviewer-01-facts", "T02"): "B+"}, mutate=raise_the_grade)
        done = self.finish(agent)
        self.assertEqual(len(upgraded), 1)
        first = json.loads((self.root / "reports" / "cycle-01-gate.json").read_text(encoding="utf-8"))
        self.assertEqual((first["exit_code"], first["result"]["outcome"]), (1, "rejected"),
                         "the reviewer said B+ for T02, so cycle 1 is rejected whatever the file says now")
        matrix = parse_data((self.root / "reports" / "cycle-01-review.yaml").read_text(encoding="utf-8"))
        self.assertEqual({row["topico"]: row["nota_minima"] for row in matrix["topics"]}["T02"], "B+")
        self.assertEqual(seen_later, [{"T01": "A", "T02": "B+"}],
                         "the matrix stage already rewrote the file, so a reader mid-run sees what was accepted")
        self.assertEqual((done["outcome"], done["cycle"]), ("approved", 2), "the work is redone, not approved")

    def test_a_forged_gate_record_does_not_approve_what_the_gate_would_not(self):
        root = build_swarm(Path(self.temporary.name) / "forged", max_cycles=1)
        agent = Scripted(root, self.base, grades={(1, "reviewer-01-facts", "T02"): "B+"})
        self.assertEqual(Engine(root).run(agent)["outcome"], "escalated")
        gate_path = root / "reports" / "cycle-01-gate.json"
        record = json.loads(gate_path.read_text(encoding="utf-8"))
        record["exit_code"], record["result"]["outcome"], record["result"]["blocked"] = 0, "approved", []
        gate_path.write_text(json.dumps(record), encoding="utf-8")
        outcome = Engine(root).next()
        self.assertEqual((outcome["status"], outcome["outcome"]), ("done", "escalated"))
        restored = json.loads(gate_path.read_text(encoding="utf-8"))
        self.assertEqual((restored["exit_code"], restored["result"]["outcome"]), (2, "escalate"),
                         "the gate was run again and its own result replaced the forged one")

    def test_a_deliverable_edited_after_approval_blocks_until_it_is_restored(self):
        agent = self.agent()
        self.finish(agent)
        document = self.root / "output" / "document.md"
        approved = document.read_bytes()
        document.write_bytes(approved + "\nParágrafo acrescentado depois da aprovação.\n".encode("utf-8"))
        calls = len(agent.calls)
        outcome = self.engine().next()
        self.assertEqual((outcome["status"], outcome["kind"]), ("blocked", "deliverable_changed"))
        self.assertIn("output/document.md", outcome["detail"])
        self.assertEqual(len(agent.calls), calls, "an edit costs no agent; it asks for a decision")
        document.write_bytes(approved)
        restored = self.engine().next()
        self.assertEqual((restored["status"], restored["outcome"]), ("done", "approved"))

    def test_the_final_recheck_of_the_sources_is_forced_and_the_cycle_check_is_not(self):
        commands: list[list[str]] = []
        original = Engine.run_script

        def spy(engine, name, command):
            commands.append([name, *command])
            return original(engine, name, command)

        with mock.patch.object(Engine, "run_script", spy):
            self.finish()
        cycle_check = [item for item in commands if item[0] == "sources"]
        final_check = [item for item in commands if item[0] == "verify_sources"]
        self.assertEqual(len(cycle_check), 1)
        self.assertEqual(len(final_check), 1)
        self.assertNotIn("--force", cycle_check[0])
        self.assertIn("--force", final_check[0])

    def test_a_source_that_fails_the_final_recheck_blocks_the_delivery(self):
        original = Engine.run_script

        def dies_at_the_end(engine, name, command):
            result = original(engine, name, command)
            if name == "verify_sources":
                path = engine.root / "sources" / "sources-check.json"
                report = json.loads(path.read_text(encoding="utf-8"))
                report["results"][0]["status"] = "fail"
                path.write_text(json.dumps(report), encoding="utf-8")
            return result

        with mock.patch.object(Engine, "run_script", dies_at_the_end):
            outcome = self.engine().run(self.agent())
            again = self.engine().next()
        self.assertEqual((outcome["status"], outcome["kind"]), ("blocked", "final_sources_failed"))
        self.assertEqual((again["status"], again["kind"]), ("blocked", "final_sources_failed"),
                         "asking again does not forget a dead source")
        self.assertFalse((self.root / "reports" / "final-report.md").exists(), "nothing is delivered over a dead source")
        recovered = self.engine().run(self.agent())
        self.assertEqual((recovered["status"], recovered["outcome"]), ("done", "approved"),
                         "a source that answers again is picked up by the next call")
        self.assertTrue((self.root / "reports" / "final-report.md").is_file())

    def test_what_the_final_recheck_finds_does_not_reopen_the_review_that_approved(self):
        original = Engine.run_script

        def drifts(engine, name, command):
            result = original(engine, name, command)
            if name == "verify_sources":
                path = engine.root / "sources" / "sources-check.json"
                report = json.loads(path.read_text(encoding="utf-8"))
                report["results"][0]["status"] = "warn"
                path.write_text(json.dumps(report), encoding="utf-8")
            return result

        agent = self.agent()
        with mock.patch.object(Engine, "run_script", drifts):
            done = self.engine().run(agent)
        self.assertEqual(done["outcome"], "approved")
        self.assertEqual(Counter(call["kind"] for call in agent.calls),
                         Counter({"author": 2, "consolidation": 1, "reviewer": 2, "rubber-duck": 1, "narrative": 1}),
                         "what the recheck found after approval costs no agent, not even inside the same run")
        calls = len(agent.calls)
        again = self.engine().run(agent)
        self.assertEqual((again["status"], again["outcome"]), ("done", "approved"))
        self.assertEqual(len(agent.calls), calls, "and asking again costs none either")

    def test_a_rejected_cycle_whose_record_was_altered_blocks_instead_of_being_run_again(self):
        agent = self.agent(grades={(1, "reviewer-01-facts", "T02"): "B+"})
        self.assertEqual(self.finish(agent)["cycle"], 2)
        review = self.root / "reports" / "cycle-01-review.yaml"
        review.write_text(review.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        calls = len(agent.calls)
        outcome = self.engine().next()
        self.assertEqual((outcome["status"], outcome["kind"], outcome["cycle"]), ("blocked", "history_altered", 1))
        self.assertIn("cycle-01-review.yaml", outcome["detail"])
        self.assertEqual(len(agent.calls), calls)

    def results(self, name: str) -> Path:
        return self.root / "reports" / "execution" / "results" / f"{name}.json"

    def test_an_accepted_result_edited_in_its_record_blocks_the_stage_instead_of_being_trusted(self):
        agent = self.agent(grades={(1, "reviewer-01-facts", "T02"): "B+"})
        self.drive_until_recorded(self.engine(), agent, "rubber-duck")
        path = self.results("c01.r0.reviewers.reviewer-01-facts")
        honest = path.read_bytes()
        record = json.loads(honest)
        for row in record["result"]["topics"]:
            row["grade"], row["action"] = "A", ""
        path.write_text(json.dumps(record), encoding="utf-8")
        calls = len(agent.calls)
        outcome = self.engine().next()
        self.assertEqual((outcome["status"], outcome["kind"]), ("blocked", "result_altered"))
        self.assertEqual(outcome["tasks"], ["c01.r0.reviewers.reviewer-01-facts"])
        self.assertEqual(len(agent.calls), calls, "it asks for a decision; it does not pay for the agent again")
        self.assertFalse((self.root / "reports" / "cycle-01-gate.json").exists(), "no verdict rests on the edited grade")
        engine = self.engine()
        engine.init()
        task = engine.task_for("c01.r0.reviewers.reviewer-01-facts")
        refused = engine.record(task.task_id, 1, task.inputs_sha256, {})
        self.assertFalse(refused["accepted"])
        self.assertIn("no longer matches the journal", refused["errors"][0])
        path.write_bytes(honest)
        done = self.engine().run(agent)
        self.assertEqual((done["outcome"], done["cycle"]), ("approved", 2),
                         "with the honest record back, the B+ rejects cycle 1 and the work is redone")

    def test_a_veto_erased_from_the_record_of_the_audit_is_not_believed(self):
        veto = {"critical": True, "consistency_notes": "", "findings": [{
            "severity": "critical", "target": "T01", "evidence": "A tabela contradiz o texto.", "correction": "Refazer a conta."}]}
        agent = self.agent(ducks={1: veto})
        self.drive_until_recorded(self.engine(), agent, "rubber-duck")
        path = self.results("c01.r0.rubber-duck.rubber-duck")
        record = json.loads(path.read_text(encoding="utf-8"))
        self.assertTrue(record["result"]["critical"])
        record["result"] = {"critical": False, "findings": [], "consistency_notes": ""}
        path.write_text(json.dumps(record), encoding="utf-8")
        outcome = self.engine().next()
        self.assertEqual((outcome["status"], outcome["kind"]), ("blocked", "result_altered"))
        matrix = parse_data((self.root / "reports" / "cycle-01-review.yaml").read_text(encoding="utf-8"))
        self.assertTrue(matrix["rubberduck"]["critico"], "while the audit cannot be verified the matrix fails closed")
        self.assertFalse((self.root / "reports" / "cycle-01-gate.json").exists())

    def test_a_deliverable_edited_together_with_its_stored_consolidation_is_still_caught(self):
        agent = self.agent()
        self.finish(agent)
        document = self.root / "output" / "document.md"
        edited = document.read_text(encoding="utf-8") + "\nParágrafo acrescentado depois da aprovação.\n"
        document.write_text(edited, encoding="utf-8")
        path = self.results("c01.r0.consolidation.coordinator")
        record = json.loads(path.read_text(encoding="utf-8"))
        record["result"]["document_markdown"] = edited
        path.write_text(json.dumps(record), encoding="utf-8")
        calls = len(agent.calls)
        outcome = self.engine().next()
        self.assertEqual((outcome["status"], outcome["kind"]), ("blocked", "result_altered"),
                         "hiding the edit by also editing the record that the drift check compares against")
        self.assertEqual(len(agent.calls), calls)

    def test_a_result_with_no_journal_digest_stands_and_one_with_a_digest_must_match_it(self):
        engine = self.engine()
        engine.init()
        result = {"critical": False, "findings": [], "consistency_notes": ""}
        record = {"task_id": "c01.r0.rubber-duck.rubber-duck", "stage": "rubber-duck", "cycle": 1, "round": 0,
                  "inputs_sha256": "0" * 64, "attempts": [], "accepted": 1, "result": result}
        # A crash between writing the record and journaling it leaves no digest; there is nothing to compare.
        self.assertFalse(engine.result_altered(record))
        self.assertEqual(engine.accepted_result(record), result)
        engine.journal.append("task_recorded", task_id=record["task_id"], attempt=1, outcome="accepted",
                              accepted_sha256=digest_json(result))
        self.assertFalse(engine.result_altered(record))
        record["result"] = {**result, "critical": True}
        self.assertTrue(engine.result_altered(record))
        self.assertIsNone(engine.accepted_result(record))
        self.assertIsNone(engine.accepted_result({**record, "accepted": None}), "no accepted attempt, no result")

    def test_the_latest_round_is_the_largest_number_not_the_last_in_alphabetical_order(self):
        engine = self.engine()
        engine.init()
        for round_number in (2, 10, 3):
            name = f"c01.r{round_number}.reviewers.reviewer-01-facts"
            self.results(name).parent.mkdir(parents=True, exist_ok=True)
            self.results(name).write_text(json.dumps({
                "task_id": name, "stage": "reviewers", "cycle": 1, "round": round_number, "inputs_sha256": "0" * 64,
                "attempts": [], "accepted": 1, "result": {"round": round_number}}), encoding="utf-8")
        self.assertEqual(engine.latest_accepted(1, "reviewers", "reviewer-01-facts")["result"]["round"], 10,
                         "as text r10 sorts before r2, and a run allowed ten repairs would use a stale assessment")
        self.assertIsNone(engine.latest_accepted(1, "reviewers", "reviewer-02-clarity"), "another agent's records are not mixed in")
        self.assertIsNone(engine.latest_accepted(2, "reviewers", "reviewer-01-facts"), "nor another cycle's")

    def test_a_result_is_taken_only_for_an_attempt_the_engine_issued(self):
        engine = self.engine()
        engine.init()
        task = engine.task_for("c01.r0.authors.author-01-platform")
        answer = self.agent().author({"agent": task.spec.name, "cycle": 1, "round": 0, "context": task.context})
        outcome = engine.record(task.task_id, 1, task.inputs_sha256, answer)
        self.assertEqual((outcome["accepted"], outcome["stale"]), (False, True))
        self.assertIn("never issued", outcome["errors"][0])
        self.assertFalse((self.root / "output" / "sections").exists(), "nothing was written for work nobody asked for")
        self.assertFalse(self.results(task.task_id).exists(), "and no record either")
        issued = {item["task_id"] for item in engine.next()["tasks"]}
        self.assertIn(task.task_id, issued)
        self.assertTrue(engine.record(task.task_id, 1, task.inputs_sha256, answer)["accepted"],
                        "once the engine has issued it, the same call is accepted")

    def test_the_identity_of_a_review_covers_the_source_index_its_prompt_shows(self):
        self.finish()
        engine = self.engine()
        engine.init()
        reviewer = engine.compiled.by_name("reviewer-01-facts")
        before = engine.build_reviewer(reviewer, 1, 0).inputs_sha256
        fragment = self.root / "sources" / "fragments" / "author-01-platform.json"
        sources = json.loads(fragment.read_text(encoding="utf-8"))
        sources[0]["title"] = "Outra fonte"
        fragment.write_text(json.dumps(sources), encoding="utf-8")
        again = self.engine()
        again.init()
        self.assertNotEqual(again.build_reviewer(reviewer, 1, 0).inputs_sha256, before,
                            "an answer given to a different index would otherwise be accepted as current")

    def test_a_source_index_edited_after_the_final_recheck_blocks_until_it_is_restored(self):
        agent = self.agent()
        self.finish(agent)
        index = self.root / "sources" / "sources-index.md"
        honest = index.read_bytes()
        index.write_bytes(honest + "| F999 | Intrusa | oficial | https://exemplo.test/x | pendente |\n".encode("utf-8"))
        calls = len(agent.calls)
        outcome = self.engine().next()
        self.assertEqual((outcome["status"], outcome["kind"]), ("blocked", "final_sources_changed"))
        self.assertIn("sources/sources-index.md", outcome["detail"])
        self.assertEqual(len(agent.calls), calls)
        index.write_bytes(honest)
        restored = self.engine().next()
        self.assertEqual((restored["status"], restored["outcome"]), ("done", "approved"))

    def test_a_crash_between_the_narrative_and_its_journal_entry_neither_duplicates_it_nor_pays_twice(self):
        agent = self.agent()
        self.finish(agent)
        journal = self.root / "reports" / "execution" / "journal.jsonl"
        kept = []
        for line in journal.read_text(encoding="utf-8").splitlines():
            item = json.loads(line)
            # What a crash right after the narrative went into the report would not have written yet.
            if item["event"] == "run_finished" or (item["event"] == "delivery_step" and item.get("step") in ("narrative", "memory")):
                continue
            kept.append(line)
        journal.write_text("\n".join(kept) + "\n", encoding="utf-8")
        narrative = "Decisões tomadas e riscos residuais registrados pelo coordenador."
        report = self.root / "reports" / "final-report.md"
        self.assertEqual(report.read_text(encoding="utf-8").count(narrative), 1)
        calls = len(agent.calls)
        outcome = self.engine().next()
        self.assertEqual((outcome["status"], outcome["outcome"]), ("done", "approved"))
        self.assertEqual(report.read_text(encoding="utf-8").count(narrative), 1, "inserted once, not twice")
        self.assertEqual(len(agent.calls), calls, "the accepted narrative is reused, so no agent runs again")

    def test_a_broken_progress_hook_never_aborts_a_run(self):
        seen = []

        def broken(name, data):
            seen.append(name)
            raise RuntimeError("the display crashed")

        done = self.engine().run(self.agent(), on_event=broken)
        self.assertEqual((done["status"], done["outcome"]), ("done", "approved"))
        self.assertIn("finished", seen)

    def test_an_agent_that_raises_is_a_null_result_not_a_crash_of_the_run(self):
        def explode(task, result):
            if task["agent"] == "rubber-duck":
                raise RuntimeError("provider timeout")
            return result

        outcome = self.engine().run(self.agent(mutate=explode))
        self.assertEqual((outcome["status"], outcome["kind"]), ("blocked", "task_failed"))


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)

    def test_canonical_hashing_ignores_key_order_and_keeps_text_exact(self):
        self.assertEqual(digest_json({"b": 1, "a": [1, 2]}), digest_json({"a": [1, 2], "b": 1}))
        self.assertNotEqual(digest_json({"a": "é"}), digest_json({"a": "e"}))
        self.assertNotEqual(digest_json({"a": 1}), digest_json({"a": 2}))
        self.assertNotEqual(digest_json([1, 2]), digest_json([2, 1]))

    def test_a_write_outlasts_a_file_that_a_reader_is_holding(self):
        target = self.folder / "a.txt"
        real, calls = os.replace, []

        def held_twice(source, destination):
            calls.append(source)
            if len(calls) < 3:
                raise PermissionError("held by a scanner")
            return real(source, destination)

        with mock.patch("scripts.orchestration.store.os.replace", held_twice), \
                mock.patch("scripts.orchestration.store.time.sleep"):
            atomic_text(target, "ok\n")
        self.assertEqual((target.read_text(encoding="utf-8"), len(calls)), ("ok\n", 3))
        self.assertEqual([item.name for item in self.folder.iterdir()], ["a.txt"], "no temporary file is left behind")

    def test_a_file_that_stays_held_fails_loudly_and_leaves_nothing_behind(self):
        calls = []

        def always_held(source, destination):
            calls.append(source)
            raise PermissionError("held")

        with mock.patch("scripts.orchestration.store.os.replace", always_held), \
                mock.patch("scripts.orchestration.store.time.sleep"):
            with self.assertRaises(PermissionError):
                atomic_text(self.folder / "a.txt", "never\n")
        self.assertEqual(len(calls), 5)
        self.assertEqual(list(self.folder.iterdir()), [], "neither the file nor its temporary copy exists")

    def test_the_lock_is_released_when_the_holder_fails(self):
        path = self.folder / ".lock"
        with self.assertRaises(RuntimeError):
            with FileLock(path).held():
                raise RuntimeError("the holder crashed")
        with FileLock(path).held(timeout=1):
            pass

    def test_the_journal_numbers_events_in_order_across_instances(self):
        path = self.folder / "journal.jsonl"
        Journal(path).append("one")
        Journal(path).append("two")
        events = Journal(path).events()
        self.assertEqual([(item["seq"], item["event"]) for item in events], [(1, "one"), (2, "two")])
        self.assertEqual(Journal(path).count("two"), 1)
        self.assertEqual(Journal(path).find("one", seq=2), [])


if __name__ == "__main__":
    unittest.main()
