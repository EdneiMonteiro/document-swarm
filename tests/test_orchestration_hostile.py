"""What an agent, a person or a second process can do to a run, and what the engine does about it.

Findings from an adversarial review of the executor: every case here either ended the run with an exception,
paid agents twice, or let something through that the rest of the engine takes for granted.
"""

from __future__ import annotations

import _thread
import copy
import json
import os
import threading
import time
import unittest
from collections import Counter
from concurrent.futures import ALL_COMPLETED
from pathlib import Path
from unittest import mock

from scripts.checks.common import InputError
from scripts.checks.gate import EDITORIAL_SURFACES
from scripts.orchestration import contracts, driver
from scripts.orchestration import engine as engine_module
from scripts.orchestration.engine import Engine
from scripts.orchestration.store import Journal
from tests.test_orchestration_engine import EngineCase, Scripted, build_swarm, make_link


class PathContractTests(EngineCase):
    def setUp(self):
        super().setUp()
        self.engine_ = self.engine()
        self.engine_.init()
        self.author = self.engine_.compiled.by_name("author-01-platform")
        self.good = {"files": [{"path": "output/sections/a.md", "content": "# A\n\ntexto\n"}],
                     "sources": [{"id": f"F1{index:02d}", "title": "t", "type": "oficial", "url": f"https://exemplo.test/{index}"}
                                 for index in range(1, 6)]}

    def check(self, files, owners=None):
        result = copy.deepcopy(self.good)
        result["files"] = [{"path": path, "content": "texto"} for path in files]
        return contracts.check_author(result, spec=self.author, code="F1", primary="output/document.md", owners=owners or {})

    def test_a_windows_short_name_is_refused_because_it_names_another_file_by_an_alias(self):
        # NTFS answers INTROD~1.MD with the file introduction-long.md, which another author may own.
        for path in ("output/sections/INTROD~1.MD", "output/sections/LONGFO~1/x.md", "output/sections/a~1.md",
                     "output/sections/x~12.md", "output/sections/AUTHOR~1.MD"):
            with self.subTest(path=path):
                errors, normal = self.check([path])
                self.assertIsNone(normal)
                self.assertTrue(any("unsafe file path" in item for item in errors), errors)
        for path in ("output/sections/a~b.md", "output/sections/til~de.md", "output/sections/~.md"):
            with self.subTest(path=path):
                self.assertEqual(self.check([path])[0], [], "a tilde that is not followed by a digit is not an alias")

    def test_a_name_longer_than_a_file_system_allows_is_refused_by_its_bytes_not_its_characters(self):
        errors, normal = self.check(["output/sections/" + "é" * 60 + ".md"])  # 124 bytes in one component
        self.assertIsNone(normal)
        self.assertTrue(any("unsafe file path" in item for item in errors), errors)
        self.assertEqual(self.check(["output/sections/" + "é" * 20 + ".md"])[0], [])

    def test_a_path_longer_than_the_whole_path_limit_is_refused_even_when_every_part_is_short(self):
        prefix = "output/sections/" + "a" * 60 + "/"
        at_the_limit = prefix + "b" * (contracts.MAX_PATH_CHARS - len(prefix) - len(".md")) + ".md"
        self.assertEqual(len(at_the_limit), contracts.MAX_PATH_CHARS)
        self.assertEqual(self.check([at_the_limit])[0], [], "exactly at the limit is allowed")
        errors, normal = self.check([at_the_limit[:-3] + "b.md"])
        self.assertIsNone(normal)
        self.assertTrue(any("unsafe file path" in item for item in errors), errors)

    def test_a_name_cannot_be_both_a_file_and_a_folder(self):
        # The second write would fail half way through the result, after the first file was already written.
        for files in (["output/sections/x.md", "output/sections/x.md/b.md"],
                      ["output/sections/x.md/b.md", "output/sections/x.md"],
                      ["output/sections/X.md", "output/sections/x.md/b.md"]):
            with self.subTest(files=files):
                errors, normal = self.check(files)
                self.assertIsNone(normal)
                self.assertTrue(any("is a file" in item or "to be a folder" in item for item in errors), errors)
        errors, normal = self.check(["output/sections/new.md/b.md"], {"output/sections/new.md": "author-02-operations"})
        self.assertIsNone(normal)
        self.assertTrue(any("needs output/sections/new.md to be a folder" in item for item in errors), errors)
        errors, normal = self.check(["output/sections/x.md/b.md"], {"output/sections/X.md": "author-02-operations"})
        self.assertIsNone(normal)
        self.assertTrue(any("needs output/sections/X.md to be a folder" in item for item in errors),
                        "names are compared without regard to case: x.md and X.md are one file")
        errors, normal = self.check(["output/sections/other.md"], {"output/sections/other.md/c.md": "author-02-operations"})
        self.assertIsNone(normal)
        self.assertTrue(any("is inside a folder of that name" in item for item in errors), errors)
        self.assertEqual(self.check(["output/sections/x.md", "output/sections/x/b.md", "output/sections/y.md"])[0], [],
                         "unrelated names that merely share a prefix are fine")

    def test_deeply_nested_text_is_a_refusal_not_an_exception(self):
        for text in ("[" * 5000, '{"a":' * 3000, "```json\n" + "[" * 5000 + "\n```", "prefixo " + '{"a":' * 3000 + "}" * 3000):
            with self.subTest(text=text[:20]):
                value, problem = contracts.parse_agent_json(text)
                self.assertIsNone(value)
                self.assertIn("not a JSON object", problem)
        deep: list = []
        for _ in range(5000):
            deep = [deep]
        self.assertFalse(contracts.storable(deep), "a result that deep cannot be stored, and saying so must not raise")

    def test_an_editorial_grade_is_stored_in_its_canonical_form(self):
        # The gate accepts "a" and " B+"; everything downstream indexes the scale with the stored value.
        text = "q"
        surfaces = [{"surface": name, "grade": grade, "location": "l", "quote": text, "justification": "j", "action": "a"}
                    for name, grade in zip(EDITORIAL_SURFACES, ("a", " B+", "A+", "b-", "A"))]
        errors: list[str] = []
        block = contracts.editorial_block({"surfaces": surfaces, "findings": []}, reviewer="reviewer-02-clarity", cycle=1,
                                          text_path="reports/cycle-01-editorial-text.txt", text_sha="0" * 64,
                                          artifacts=[{"path": "output/document.md", "sha256": "0" * 64}], text=text, errors=errors)
        self.assertEqual(errors, [])
        self.assertEqual([item["grade"] for item in block["surfaces"]], ["A", "B+", "A+", "B-", "A"])
        surfaces[0]["grade"] = "Z"
        errors = []
        self.assertIsNone(contracts.editorial_block({"surfaces": surfaces, "findings": []}, reviewer="reviewer-02-clarity",
                                                    cycle=1, text_path="reports/cycle-01-editorial-text.txt", text_sha="0" * 64,
                                                    artifacts=[{"path": "output/document.md", "sha256": "0" * 64}], text=text,
                                                    errors=errors))
        self.assertTrue(any("editorial review is not valid" in item for item in errors), errors)

    def test_a_lowercase_editorial_grade_does_not_break_the_next_cycle(self):
        # Cycle 1 is rejected for another reason; building its feedback used to index the scale with "a".
        agent = self.agent(surface_grades={1: "a"}, grades={(1, "reviewer-01-facts", "T01"): "B"})
        done = self.finish(agent)
        self.assertEqual((done["outcome"], done["cycle"]), ("approved", 2))
        record = json.loads((self.root / "reports" / "cycle-01-reviewer-02-clarity.json").read_text(encoding="utf-8"))
        self.assertEqual({item["grade"] for item in record["editorial"]["surfaces"]}, {"A"})


class HostileAnswerTests(EngineCase):
    def first_task(self, engine):
        engine.init()
        return engine.next()["tasks"][0]

    def test_a_deeply_nested_answer_is_a_rejection_the_run_survives(self):
        engine = self.engine()
        task = self.first_task(engine)
        outcome = engine.record(task["task_id"], 1, task["inputs_sha256"], "[" * 5000)
        self.assertEqual((outcome["accepted"], outcome["retry"]), (False, True))
        self.assertTrue(any("not a JSON object" in item for item in outcome["errors"]), outcome["errors"])
        self.assertEqual(Journal(self.root / "reports" / "execution" / "journal.jsonl").count("task_recorded"), 1,
                         "the attempt is counted, so the retry budget advances")

    def test_an_unreadable_answer_with_a_lone_surrogate_is_a_rejection_the_run_survives(self):
        # The excerpt kept of a reply that could not be read never went through `storable`, and a lone surrogate cannot be
        # written as UTF-8: it failed the record of the attempt and, with it, the run.
        cases = {"in the head": ('{"files": [ \ud800 sem fim', 1, 0), "in the tail": ("x" * 400 + "\udc00" + "y" * 10, 0, 1)}
        for label, (text, in_head, in_tail) in cases.items():
            with self.subTest(case=label):
                root = build_swarm(Path(self.temporary.name) / label.replace(" ", "-"))
                engine = Engine(root)
                task = self.first_task(engine)
                outcome = engine.record(task["task_id"], 1, task["inputs_sha256"], text)
                self.assertEqual((outcome["accepted"], outcome["retry"]), (False, True))
                runtime = engine.load_record(task["task_id"])["attempts"][0]["runtime"]
                self.assertEqual((runtime["answer_head"].count("?"), runtime["answer_tail"].count("?")), (in_head, in_tail),
                                 "the surrogate becomes a question mark and the rest of the excerpt is kept")
                self.assertEqual(Journal(root / "reports" / "execution" / "journal.jsonl").count("task_recorded"), 1)

    def test_a_file_that_cannot_be_written_is_a_rejection_not_an_exception(self):
        engine = self.engine()
        task = self.first_task(engine)
        with mock.patch.object(Engine, "materialise", side_effect=OSError("disk full")):
            outcome = engine.record(task["task_id"], 1, task["inputs_sha256"], self.agent().author(task))
        self.assertFalse(outcome["accepted"])
        self.assertTrue(any("could not be written (OSError: disk full)" in item for item in outcome["errors"]), outcome["errors"])
        self.assertIsNone(engine.load_record(task["task_id"])["accepted"], "nothing is accepted that was not written")

    def test_what_the_validators_did_not_foresee_is_still_a_refusal_of_that_answer(self):
        engine = self.engine()
        task = self.first_task(engine)
        with mock.patch.object(Engine, "validate", side_effect=RuntimeError("an unforeseen shape")):
            outcome = engine.record(task["task_id"], 1, task["inputs_sha256"], self.agent().author(task))
        self.assertEqual((outcome["accepted"], outcome["retry"]), (False, True))
        self.assertTrue(any("could not be validated (RuntimeError)" in item for item in outcome["errors"]), outcome["errors"])

    def test_a_file_that_collides_with_a_folder_is_refused_before_anything_is_written(self):
        engine = self.engine()
        task = self.first_task(engine)
        answer = self.agent().author(task)
        answer["files"] = [{"path": "output/sections/x.md", "content": "um"}, {"path": "output/sections/x.md/b.md", "content": "dois"}]
        outcome = engine.record(task["task_id"], 1, task["inputs_sha256"], answer)
        self.assertFalse(outcome["accepted"])
        self.assertTrue(any("to be a folder" in item for item in outcome["errors"]), outcome["errors"])
        self.assertFalse((self.root / "output" / "sections").exists(), "no orphan file is left behind")

    def test_a_path_that_resolves_to_another_name_is_refused(self):
        # The same thing a short name does on NTFS: the write would change a file under a different name.
        real = self.root / "output" / "sections-real"
        real.mkdir(parents=True)
        if not make_link(self.root / "output" / "sections", real):
            self.skipTest("this platform cannot create directory links without privileges")
        engine = self.engine()
        task = self.first_task(engine)
        outcome = engine.record(task["task_id"], 1, task["inputs_sha256"], self.agent().author(task))
        self.assertFalse(outcome["accepted"])
        self.assertTrue(any("is another name for output/sections-real/" in item for item in outcome["errors"]), outcome["errors"])
        self.assertEqual(list(real.iterdir()), [], "nothing was written through the link")

    def test_a_path_too_long_for_the_swarm_folder_is_refused_with_the_reason(self):
        engine = self.engine()
        task = self.first_task(engine)
        with mock.patch.object(engine_module, "MAX_WRITE_PATH", 40):
            outcome = engine.record(task["task_id"], 1, task["inputs_sha256"], self.agent().author(task))
        self.assertFalse(outcome["accepted"])
        self.assertTrue(any("characters long once placed in this swarm folder" in item for item in outcome["errors"]), outcome["errors"])

    def test_one_result_that_cannot_be_recorded_does_not_cost_the_others(self):
        original = Engine.record
        calls: list[str] = []

        def flaky(engine, task_id, attempt, inputs, result, runtime=None):
            calls.append(task_id)
            if len(calls) == 1:
                raise RuntimeError("the disk went away")
            return original(engine, task_id, attempt, inputs, result, runtime)

        events: list[str] = []
        with mock.patch.object(Engine, "record", flaky), self.assertRaisesRegex(RuntimeError, "the disk went away"):
            self.engine().run(self.agent(), on_event=lambda name, data: events.append(name))
        self.assertEqual(len(calls), 2, "the second author's answer was still handed to record")
        recorded = Journal(self.root / "reports" / "execution" / "journal.jsonl").count("task_recorded")
        self.assertEqual(recorded, 1, "and it was recorded, not discarded with the failure")
        self.assertIn("failed", events)


def read_json_retrying(path: Path, attempts: int = 100):
    """Read a file another thread keeps replacing: on Windows a read that meets the replace is refused for a moment."""
    last: Exception | None = None
    for _ in range(attempts):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            last = exc
            time.sleep(0.02)
    raise last  # type: ignore[misc]


class OneRunPerSwarmTests(EngineCase):
    def hold(self, start):
        """Start a first run that stays inside its first agent until released.

        Whatever a test asserts, the run is released and joined before the swarm folder is removed: a run left
        holding the swarm would keep its lock file open and fail the cleanup, and its thread would outlive the test.
        """
        agent = self.agent()
        started, release = threading.Event(), threading.Event()

        def slow(task):
            started.set()
            release.wait(60)
            return agent(task)

        first = threading.Thread(target=lambda: start(slow))
        self.addCleanup(first.join, 120)
        self.addCleanup(release.set)
        first.start()
        self.assertTrue(started.wait(30))
        return agent, first, release

    def test_a_second_run_on_the_same_swarm_is_refused_and_pays_nothing(self):
        results: list[dict] = []
        agent, first, release = self.hold(lambda backend: results.append(self.engine().run(backend)))
        second = self.agent()
        began = time.monotonic()
        with self.assertRaisesRegex(InputError, "another run is already operating this swarm"):
            self.engine().run(second)
        self.assertLess(time.monotonic() - began, 10, "a second run is refused at once; it does not queue behind the first")
        self.assertEqual(second.calls, [], "the refused run paid for nothing")
        release.set()
        first.join(120)
        self.assertEqual(results[0]["outcome"], "approved")
        counts = Counter(call["task_id"] for call in agent.calls)
        self.assertEqual(max(counts.values()), 1, f"no agent was paid twice: {counts}")
        self.assertEqual(self.engine().run(self.agent())["outcome"], "approved", "the lock is released with the run")

    def test_a_refused_run_leaves_the_heartbeat_of_the_run_that_holds_the_swarm_alone(self):
        _agent, first, release = self.hold(lambda backend: driver.execute(
            self.root, backend, parallel=2, tick=3600, beat=0.2, out=lambda text: None, backend_name="first"))
        beat = self.root / "reports" / "execution" / "driver.json"
        before = read_json_retrying(beat)
        with self.assertRaisesRegex(InputError, "another run is already operating this swarm"):
            driver.execute(self.root, self.agent(), parallel=2, tick=3600, out=lambda text: None, backend_name="second")
        after = read_json_retrying(beat)
        self.assertEqual((before["backend"], after["backend"]), ("first", "first"))
        release.set()
        first.join(120)


class InterruptTests(EngineCase):
    def test_ctrl_c_stops_the_agents_that_are_running_at_once_and_keeps_what_had_finished(self):
        scripted = self.agent()
        cancelled = threading.Event()

        class Backend:
            name = "hangs"

            def __call__(self, task):
                if task["agent"] == "author-01-platform":
                    return scripted(task)
                cancelled.wait(60)  # the other agent runs until it is stopped
                return None

            def cancel(self):
                cancelled.set()

        timer = threading.Timer(2.0, _thread.interrupt_main)
        timer.start()
        started = time.monotonic()
        try:
            with self.assertRaises(KeyboardInterrupt):
                self.engine().run(Backend())
        finally:
            timer.cancel()
        self.assertLess(time.monotonic() - started, 30, "the interrupt was heard while the agent was still running")
        self.assertTrue(cancelled.is_set(), "the running agent was stopped, not waited for")
        journal = Journal(self.root / "reports" / "execution" / "journal.jsonl")
        recorded = [item["agent"] for item in journal.find("task_recorded")]
        self.assertEqual(recorded, ["author-01-platform"], "the finished agent's paid result is kept; the stopped one is not counted")

    def test_an_agent_that_is_still_waiting_for_a_worker_is_not_started_after_the_interrupt(self):
        started, cancelled = [], threading.Event()

        class Backend:
            name = "one at a time"

            def __call__(self, task):
                started.append(task["agent"])
                cancelled.wait(60)
                return None

            def cancel(self):
                cancelled.set()
                time.sleep(1.0)  # long enough for the freed worker to take the next agent off the queue

        timer = threading.Timer(2.0, _thread.interrupt_main)
        timer.start()
        try:
            with self.assertRaises(KeyboardInterrupt):
                self.engine().run(Backend(), parallel=1)
        finally:
            timer.cancel()
        self.assertEqual(started, ["author-01-platform"], "the second author was queued: nothing new starts once the run is stopping")

    def test_what_had_already_finished_when_the_interrupt_arrived_is_recorded_before_the_run_stops(self):
        real_wait = engine_module.wait

        def interrupted_once_the_agents_have_finished(pending, timeout=None, return_when=None):
            real_wait(pending, timeout=30, return_when=ALL_COMPLETED)
            raise KeyboardInterrupt

        with mock.patch.object(engine_module, "wait", interrupted_once_the_agents_have_finished):
            with self.assertRaises(KeyboardInterrupt):
                self.engine().run(self.agent())
        journal = Journal(self.root / "reports" / "execution" / "journal.jsonl")
        self.assertEqual(sorted(item["agent"] for item in journal.find("task_recorded")),
                         ["author-01-platform", "author-02-operations"], "the paid results that had arrived are kept")


class BriefTests(EngineCase):
    def edit_brief(self, old: str, new: str) -> None:
        brief = self.root / "brief.md"
        text = brief.read_text(encoding="utf-8")
        self.assertIn(old, text)
        brief.write_text(text.replace(old, new), encoding="utf-8")

    def test_a_brief_that_is_not_editorial_v1_is_refused_before_any_agent_is_paid_for(self):
        # The executor renders every review as editorial-v1, so such a brief could only fail at the gate.
        self.edit_brief('skill_version: "3.6.0"', 'skill_version: "3.1.0"')
        self.edit_brief("quality_contract: editorial-v1\n", "")
        with self.assertRaisesRegex(InputError, "editorial-v1 reviews only"):
            self.engine().init()
        self.assertFalse((self.root / "reports" / "execution").exists(), "nothing was written")

    def test_an_editorial_reviewer_the_gate_would_refuse_by_name_is_refused_at_init(self):
        self.edit_brief("editorial_reviewer: reviewer-02-clarity", "editorial_reviewer: clarity-judge")
        path = self.root / "agents" / "reviewers" / "reviewer-02-clarity.md"
        path.write_text(path.read_text(encoding="utf-8").replace("name: reviewer-02-clarity", "name: clarity-judge"), encoding="utf-8")
        with self.assertRaisesRegex(InputError, "editorial_reviewer must be named reviewer-"):
            self.engine().init()

    @unittest.skipUnless(os.name == "nt", "the default 260 character limit is a Windows matter")
    def test_a_swarm_folder_too_deep_for_windows_is_refused_with_the_remedy(self):
        # Long enough that the executor's own files cannot fit, short enough that the fixture can still be built.
        filler = 190 - len(self.temporary.name) - len("\\demo") - 1
        if filler < 1:
            self.skipTest("the temporary folder is already too long to build the fixture")
        root = build_swarm(Path(self.temporary.name) / ("x" * filler) / "demo")
        self.assertTrue(170 < len(str(root)) < 221, len(str(root)))
        with self.assertRaisesRegex(InputError, "move the swarm to a shorter folder"):
            Engine(root).init()


if __name__ == "__main__":
    unittest.main()
