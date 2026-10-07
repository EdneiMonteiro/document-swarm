from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from typing import Any

from tests.test_orchestration_engine import EngineCase, Scripted, build_swarm

ROOT = Path(__file__).resolve().parents[1]


def cli(*args: str, payload: Any = None, raw: bytes | None = None) -> tuple[int, str, str]:
    """One invocation of the command line, exactly as a backend would make it."""
    data = raw if raw is not None else (json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None)
    done = subprocess.run([sys.executable, "-S", "-m", "scripts.orchestration", *args], cwd=ROOT, input=data,
                          capture_output=True, env={**os.environ, "PYTHONUTF8": "1"}, timeout=300)
    return done.returncode, done.stdout.decode("utf-8"), done.stderr.decode("utf-8")


def drive(root: Path, agent: Scripted, *, limit: int = 80) -> dict[str, Any]:
    """The loop a backend runs: ask for work, run the agents, hand every result back."""
    for _ in range(limit):
        code, out, err = cli("next", str(root))
        assert code == 0, err
        directive = json.loads(out)
        if directive["status"] != "agents":
            return directive
        for task in directive["tasks"]:
            code, out, err = cli("record", str(root), payload={
                "task_id": task["task_id"], "attempt": task["attempt"], "inputs_sha256": task["inputs_sha256"],
                "result": agent(task), "runtime": {"resolved_model": "modelo-de-teste"}})
            assert code == 0, err
    raise AssertionError("the run did not settle")


class CommandLineTests(EngineCase):
    def test_a_whole_run_through_the_command_line_is_approved_and_leaves_what_the_gate_reads(self):
        agent = self.agent()
        code, out, err = cli("init", str(self.root))
        self.assertEqual(code, 0, err)
        plan = json.loads(out)
        self.assertTrue(plan["ok"])
        self.assertEqual(plan["agents"], 6)
        done = drive(self.root, agent)
        self.assertEqual((done["status"], done["outcome"], done["cycle"]), ("done", "approved", 1))
        self.assertTrue((self.root / "reports" / "final-report.md").is_file())
        code, out, err = cli("status", str(self.root))
        self.assertEqual(code, 0, err)
        status = json.loads(out)
        self.assertEqual((status["accepted"], status["rejected"], status["null_results"]), (7, 0, 0))
        record = json.loads((self.root / "reports" / "execution" / "results" /
                             "c01.r0.authors.author-01-platform.json").read_text(encoding="utf-8"))
        self.assertEqual(record["attempts"][0]["runtime"], {"resolved_model": "modelo-de-teste"})

    def test_the_answer_is_utf_8_json_with_the_text_intact(self):
        cli("init", str(self.root))
        code, out, err = cli("next", str(self.root))
        self.assertEqual(code, 0, err)
        directive = json.loads(out)
        self.assertEqual(directive["status"], "agents")
        self.assertIn("Execução determinística", directive["tasks"][0]["prompt"])
        self.assertNotIn("\\u00e7", out, "accents are written as text, not escaped")
        code, pretty, _ = cli("status", str(self.root), "--pretty")
        self.assertEqual(code, 0)
        self.assertIn("\n  ", pretty)

    def test_init_refuses_what_the_executor_cannot_run_with_exit_2_and_the_reason(self):
        root = build_swarm(Path(self.temporary.name) / "deck", extra_brief="artifact_type: presentation\n")
        code, out, err = cli("init", str(root))
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("presentation swarms are not supported", err)
        self.assertFalse((root / "reports" / "execution").exists(), "a refused swarm is left untouched")

    def test_a_declared_model_that_is_not_available_is_refused_before_any_agent_runs(self):
        path = self.root / "agents" / "authors" / "author-01-platform.md"
        path.write_text(path.read_text(encoding="utf-8").replace("model: auto", "model: modelo-fantasma"), encoding="utf-8")
        code, _, err = cli("init", str(self.root), "--models", "modelo-real-1,modelo-real-2")
        self.assertEqual(code, 2)
        self.assertIn("modelo-fantasma", err)
        code, out, err = cli("init", str(self.root), "--models", "modelo-fantasma")
        self.assertEqual(code, 0, err)

    def test_the_limits_given_at_init_are_kept_for_every_later_call(self):
        sources = lambda agent, cycle, round_number: [f"{self.base}/{agent}/{i}" for i in range(1, 5)] + [f"{self.base}/missing"]
        agent = self.agent(sources=sources)
        code, _, err = cli("init", str(self.root), "--max-repairs", "1")
        self.assertEqual(code, 0, err)
        blocked = drive(self.root, agent)
        self.assertEqual((blocked["status"], blocked["kind"]), ("blocked", "checks_failed"))
        self.assertIn("after 1 repair round", blocked["detail"], "next was called without the flag and still honoured it")
        plan = json.loads((self.root / "reports" / "execution" / "plan.json").read_text(encoding="utf-8"))
        self.assertEqual(plan["options"], {"max_attempts": 2, "max_repairs": 1, "max_cycles": None, "approval_grade": None})

    def test_the_cycle_ceiling_given_at_init_replaces_the_briefs_and_is_kept_for_every_later_call(self):
        code, _, err = cli("init", str(self.root), "--max-cycles", "9")
        self.assertEqual(code, 0, err)
        plan = json.loads((self.root / "reports" / "execution" / "plan.json").read_text(encoding="utf-8"))
        self.assertEqual((plan["max_cycles"], plan["options"]["max_cycles"]), (9, 9))
        code, out, err = cli("status", str(self.root))
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["max_cycles"], 9, "status was called without the flag and still honours it")
        code, _, err = cli("init", str(self.root))
        self.assertEqual(code, 0, err)
        code, out, _ = cli("status", str(self.root))
        self.assertEqual(json.loads(out)["max_cycles"], 9, "an init without the flag keeps the ceiling that was set")
        code, _, err = cli("init", str(self.root), "--max-cycles", "4")
        self.assertEqual(code, 0, err)
        code, out, _ = cli("status", str(self.root))
        self.assertEqual(json.loads(out)["max_cycles"], 4, "a new value replaces it")

    def test_a_ceiling_that_is_not_positive_is_refused_at_the_command_line(self):
        for value in ("0", "-2"):
            with self.subTest(value=value):
                code, _, err = cli("init", str(self.root), "--max-cycles", value)
                self.assertEqual(code, 2)
                self.assertIn("max_cycles must be a positive integer", err)
        self.assertFalse((self.root / "reports" / "execution" / "plan.json").exists())

    def test_the_approval_grade_given_at_init_is_kept_for_every_later_call_and_a_change_is_journaled(self):
        def grade() -> str:
            code, out, err = cli("status", str(self.root))
            self.assertEqual(code, 0, err)
            return json.loads(out)["approval_grade"]

        code, _, err = cli("init", str(self.root))
        self.assertEqual(code, 0, err)
        self.assertEqual(grade(), "A-", "a new swarm takes the skill's current policy")
        code, _, err = cli("init", str(self.root), "--approval-grade", "A")
        self.assertEqual(code, 0, err)
        plan = json.loads((self.root / "reports" / "execution" / "plan.json").read_text(encoding="utf-8"))
        self.assertEqual((plan["approval_grade"], plan["options"]["approval_grade"]), ("A", "A"))
        self.assertEqual(grade(), "A", "status was called without the flag and still honours it")
        code, _, err = cli("init", str(self.root))
        self.assertEqual(code, 0, err)
        self.assertEqual(grade(), "A", "an init without the flag keeps the grade that was set")
        plan = json.loads((self.root / "reports" / "execution" / "plan.json").read_text(encoding="utf-8"))
        self.assertEqual(plan["options"]["approval_grade"], "A", "and the plan still says it was set by a person")
        code, _, err = cli("init", str(self.root), "--approval-grade", "A-")
        self.assertEqual(code, 0, err)
        self.assertEqual(grade(), "A-")
        events = [json.loads(line) for line in (self.root / "reports" / "execution" / "journal.jsonl")
                  .read_text(encoding="utf-8").splitlines()]
        changes = [(item["previous"], item["current"]) for item in events if item["event"] == "approval_grade_changed"]
        self.assertEqual(changes, [("A-", "A"), ("A", "A-")])

    def test_a_plan_that_was_edited_is_refused_with_the_remedy_and_started_again_by_stating_the_policy(self):
        code, _, err = cli("init", str(self.root), "--approval-grade", "A")
        self.assertEqual(code, 0, err)
        path = self.root / "reports" / "execution" / "plan.json"
        plan = json.loads(path.read_text(encoding="utf-8"))
        plan["approval_grade"] = plan["options"]["approval_grade"] = "A-"
        path.write_text(json.dumps(plan), encoding="utf-8")
        for command in ("next", "status", "init"):
            with self.subTest(command=command):
                code, _, err = cli(command, str(self.root))
                self.assertEqual(code, 2, err)
                self.assertIn("was edited", err)
                self.assertIn("--approval-grade A- or --approval-grade A", err)
        code, _, err = cli("init", str(self.root), "--approval-grade", "A")
        self.assertEqual(code, 0, err)
        code, out, err = cli("status", str(self.root))
        self.assertEqual((code, json.loads(out)["approval_grade"]), (0, "A"), err)
        events = [json.loads(line) for line in (self.root / "reports" / "execution" / "journal.jsonl")
                  .read_text(encoding="utf-8").splitlines()]
        [recovered] = [item for item in events if item["event"] == "plan_recovered"]
        self.assertEqual(recovered["approval_grade"], "A")

    def test_an_unreadable_answer_with_a_lone_surrogate_is_refused_by_record_not_raised(self):
        # A payload is JSON text, and "\ud800" in it is a perfectly valid way to write a lone surrogate.
        cli("init", str(self.root))
        code, out, err = cli("next", str(self.root))
        self.assertEqual(code, 0, err)
        task = json.loads(out)["tasks"][0]
        payload = ('{"task_id": "%s", "attempt": 1, "inputs_sha256": "%s", "result": "{\\"files\\": [ \\ud800 sem fim"}'
                   % (task["task_id"], task["inputs_sha256"]))
        code, out, err = cli("record", str(self.root), raw=payload.encode("ascii"))
        self.assertEqual(code, 0, err)
        answer = json.loads(out)
        self.assertEqual((answer["accepted"], answer["retry"]), (False, True))

    def test_a_grade_other_than_a_minus_or_a_is_refused_at_the_command_line(self):
        for value in ("B+", "A+", "a"):
            with self.subTest(value=value):
                code, _, err = cli("init", str(self.root), "--approval-grade", value)
                self.assertEqual(code, 2)
                self.assertIn("invalid choice", err)
        self.assertFalse((self.root / "reports" / "execution" / "plan.json").exists())

    def test_invalid_limits_are_refused(self):
        for flag, value in (("--max-attempts", "0"), ("--max-repairs", "-1")):
            with self.subTest(flag=flag):
                code, _, err = cli("init", str(self.root), flag, value)
                self.assertEqual(code, 2)
                self.assertIn("max_attempts must be a positive integer", err)

    def test_record_refuses_a_payload_it_cannot_trust_without_touching_the_swarm(self):
        cli("init", str(self.root))
        journal = self.root / "reports" / "execution" / "journal.jsonl"
        before = journal.read_bytes()
        good = {"task_id": "c01.r0.authors.author-01-platform", "attempt": 1, "inputs_sha256": "0" * 64, "result": None}
        cases = (
            (b"not json", "not valid JSON"),
            (b"[1, 2]", "must be an object"),
            (json.dumps({k: v for k, v in good.items() if k != "result"}).encode(), "must be an object"),
            (json.dumps({**good, "attempt": "1"}).encode(), "attempt an integer"),
            (json.dumps({**good, "attempt": True}).encode(), "attempt an integer"),
            (json.dumps({**good, "task_id": 7}).encode(), "must be strings"),
        )
        for raw, fragment in cases:
            with self.subTest(fragment=fragment, raw=raw[:30]):
                code, out, err = cli("record", str(self.root), raw=raw)
                self.assertEqual(code, 2)
                self.assertIn(fragment, err)
        self.assertEqual(journal.read_bytes(), before)

    def test_record_answers_a_stale_task_instead_of_failing(self):
        cli("init", str(self.root))
        code, out, err = cli("record", str(self.root), payload={
            "task_id": "c09.r0.authors.nobody", "attempt": 1, "inputs_sha256": "0" * 64, "result": {"files": []}})
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["stale"], True)

    def test_a_null_result_is_a_normal_answer_that_asks_for_another_attempt(self):
        cli("init", str(self.root))
        _, out, _ = cli("next", str(self.root))
        task = json.loads(out)["tasks"][0]
        code, out, err = cli("record", str(self.root), payload={
            "task_id": task["task_id"], "attempt": task["attempt"], "inputs_sha256": task["inputs_sha256"], "result": None})
        self.assertEqual(code, 0, err)
        answer = json.loads(out)
        self.assertEqual((answer["accepted"], answer["retry"]), (False, True))
        _, out, _ = cli("next", str(self.root))
        retried = next(item for item in json.loads(out)["tasks"] if item["task_id"] == task["task_id"])
        self.assertEqual(retried["attempt"], 2)

    def test_it_also_runs_as_a_directory_from_any_working_directory(self):
        done = subprocess.run([sys.executable, "-S", str(ROOT / "scripts" / "orchestration"), "init", str(self.root)],
                              cwd=self.temporary.name, capture_output=True, env={**os.environ, "PYTHONUTF8": "1"},
                              timeout=120)
        self.assertEqual(done.returncode, 0, done.stderr.decode("utf-8"))
        self.assertTrue(json.loads(done.stdout.decode("utf-8"))["ok"])

    def test_a_swarm_that_does_not_exist_is_an_error_not_a_traceback(self):
        code, out, err = cli("next", str(Path(self.temporary.name) / "ghost"))
        self.assertEqual(code, 2)
        self.assertTrue(err.startswith("ERROR:"), err)
        self.assertNotIn("Traceback", err)

    def test_a_result_with_accents_survives_the_trip_through_stdin(self):
        cli("init", str(self.root))
        _, out, _ = cli("next", str(self.root))
        task = next(item for item in json.loads(out)["tasks"] if item["agent"] == "author-01-platform")
        result = self.agent().author(task)
        result["files"][0]["content"] = "# Seção\n\nAvaliação da operação: não há ressalvas.\n"
        code, out, err = cli("record", str(self.root), payload={
            "task_id": task["task_id"], "attempt": 1, "inputs_sha256": task["inputs_sha256"], "result": result})
        self.assertEqual(code, 0, err)
        self.assertTrue(json.loads(out)["accepted"])
        written = (self.root / "output" / "sections" / "author-01-platform.md").read_text(encoding="utf-8")
        self.assertIn("Avaliação da operação: não há ressalvas.", written)

    def test_refusing_a_swarm_creates_nothing_inside_it(self):
        before = sorted(str(item.relative_to(self.root)) for item in self.root.rglob("*"))
        root = build_swarm(Path(self.temporary.name) / "legacy")
        (root / "reports" / "cycle-01-review.yaml").write_text("{}", encoding="utf-8")
        snapshot = sorted(str(item.relative_to(root)) for item in root.rglob("*"))
        code, _, err = cli("init", str(root))
        self.assertEqual(code, 2)
        self.assertIn("starts new swarms only", err)
        self.assertEqual(sorted(str(item.relative_to(root)) for item in root.rglob("*")), snapshot)
        self.assertEqual(before, sorted(str(item.relative_to(self.root)) for item in self.root.rglob("*")))


if __name__ == "__main__":
    unittest.main()
