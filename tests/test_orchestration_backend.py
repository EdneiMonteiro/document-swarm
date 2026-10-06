from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from scripts.checks.common import InputError
from scripts.orchestration import driver, qualify
from scripts.orchestration.backend import CopilotCli, models_from_usage
from scripts.orchestration.contracts import Answer
from scripts.orchestration.engine import Engine
from scripts.orchestration.store import Journal
from tests.test_orchestration_engine import EngineCase, Scripted, build_swarm

# No test may reach a model: a broken guard under test must fail the test, not spend credits.
os.environ["DOCSWARM_NO_REAL_CLI"] = "1"

ROOT = Path(__file__).resolve().parents[1]
FAKE = Path(__file__).with_name("fake_copilot.py")


def task_for(**overrides: Any) -> dict[str, Any]:
    base = {"task_id": "c01.r0.authors.author-01-platform", "label": "c01.r0.authors.author-01-platform.a1",
            "prompt": "Responda com o objeto JSON.", "model": "modelo-pedido", "reasoning_effort": "high",
            "context_tier": "long_context", "tools": ["view", "glob", "grep", "web_search", "web_fetch"]}
    return {**base, **overrides}


def alive(pid: int) -> bool:
    if os.name == "nt":
        listing = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
        return str(pid) in listing.split()
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


class CommandTests(unittest.TestCase):
    def backend(self):
        return CopilotCli(executable=["copilot"])

    def test_the_prompt_never_reaches_the_command_line(self):
        argv = self.backend().command(task_for(prompt="segredo do documento " * 500))
        self.assertNotIn("segredo", " ".join(argv))

    def test_every_agent_runs_unattended_with_only_its_own_instructions(self):
        argv = self.backend().command(task_for())
        for flag in ("-s", "--no-ask-user", "--no-custom-instructions", "--no-color", "--no-auto-update",
                     "--disallow-temp-dir", "--allow-all-tools"):
            self.assertIn(flag, argv)

    def test_shell_and_file_writes_are_denied_on_top_of_the_tool_filter(self):
        argv = self.backend().command(task_for())
        denied = [argv[index + 1] for index, item in enumerate(argv) if item == "--deny-tool"]
        self.assertEqual(sorted(denied), ["shell", "write"])

    def test_only_the_tools_of_the_role_exist(self):
        author = self.backend().command(task_for())
        start = author.index("--available-tools") + 1
        self.assertEqual(author[start:start + 5], ["view", "glob", "grep", "web_search", "web_fetch"])
        self.assertIn("--allow-all-urls", author)
        form = self.backend().command(task_for(tools=["view", "glob", "grep", "rg"]))
        self.assertNotIn("--allow-all-urls", form, "a role with no web tool needs no URL permission")
        self.assertNotIn("web_fetch", form)

    def test_an_empty_tool_list_is_refused_because_it_could_mean_no_restriction(self):
        with self.assertRaisesRegex(InputError, "no tools"):
            self.backend().command(task_for(tools=[]))

    def test_model_effort_and_context_are_passed_only_when_declared(self):
        argv = self.backend().command(task_for())
        self.assertEqual(argv[argv.index("--model") + 1], "modelo-pedido")
        self.assertEqual(argv[argv.index("--reasoning-effort") + 1], "high")
        self.assertEqual(argv[argv.index("--context") + 1], "long_context")
        bare = self.backend().command(task_for(model=None, reasoning_effort=None, context_tier=None))
        for flag in ("--model", "--reasoning-effort", "--context"):
            self.assertNotIn(flag, bare)

    def test_the_usage_file_is_requested_when_a_place_for_it_is_given(self):
        argv = self.backend().command(task_for(), Path("uso.json"))
        self.assertEqual(argv[argv.index("--usage-output-file") + 1], "uso.json")
        self.assertNotIn("--usage-output-file", self.backend().command(task_for()))

    def test_the_executable_prefix_comes_first(self):
        argv = CopilotCli(executable=["python", "wrapper.py"]).command(task_for())
        self.assertEqual(argv[:2], ["python", "wrapper.py"])

    def test_without_copilot_on_the_path_the_error_says_what_to_do(self):
        with mock.patch.dict(os.environ, {"DOCSWARM_NO_REAL_CLI": "0"}), \
                mock.patch("scripts.orchestration.backend.shutil.which", return_value=None):
            with self.assertRaisesRegex(InputError, "not on PATH"):
                CopilotCli()

    def test_the_real_cli_can_be_forbidden_so_that_a_test_can_never_spend_credits(self):
        with mock.patch.dict(os.environ, {"DOCSWARM_NO_REAL_CLI": "1"}), \
                mock.patch("scripts.orchestration.backend.shutil.which", return_value="C:/real/copilot.exe"):
            with self.assertRaisesRegex(InputError, "disabled in this environment"):
                CopilotCli()
            self.assertEqual(CopilotCli(executable=["fake"]).executable, ["fake"], "an explicit executable is still allowed")
        with mock.patch.dict(os.environ, {"DOCSWARM_NO_REAL_CLI": "0"}), \
                mock.patch("scripts.orchestration.backend.shutil.which", return_value="C:/real/copilot.exe"):
            self.assertEqual(CopilotCli().executable, ["C:/real/copilot.exe"])

    def test_models_are_read_from_a_usage_record_wherever_it_nests_them(self):
        self.assertEqual(models_from_usage({"modelMetrics": {"gpt-a": {}, "claude-b": {}}}), ["claude-b", "gpt-a"])
        self.assertEqual(models_from_usage({"calls": [{"model": "m1"}, {"detail": {"model_id": "m2"}}]}), ["m1", "m2"])
        self.assertEqual(models_from_usage({"total": 3}), [])
        self.assertEqual(models_from_usage("not a record"), [])


class BackendCase(EngineCase):
    def setUp(self):
        super().setUp()
        self.log = Path(self.temporary.name) / "calls.jsonl"
        self.state = Path(self.temporary.name) / "state"
        self.state.mkdir()

    def environment(self, **extra: Any) -> dict[str, str]:
        return {"FAKE_COPILOT_LOG": str(self.log), "FAKE_COPILOT_ROOT": str(self.root), "FAKE_COPILOT_BASE": self.base,
                "FAKE_COPILOT_STATE": str(self.state), **{key: str(value) for key, value in extra.items()}}

    def fake(self, *, timeout: float = 300.0, **extra: Any) -> CopilotCli:
        return CopilotCli(executable=[sys.executable, "-S", str(FAKE)], timeout=timeout,
                          usage_dir=self.root / "reports" / "execution" / "usage", environment=self.environment(**extra))

    def calls(self) -> list[dict[str, Any]]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]

    def first_task(self) -> dict[str, Any]:
        engine = self.engine()
        engine.init()
        return engine.next()["tasks"][0]


class ProcessTests(BackendCase):
    def test_a_whole_swarm_runs_through_copilot_processes_and_is_approved(self):
        backend = self.fake()
        done = driver.execute(self.root, backend, parallel=4, tick=3600, out=lambda text: None, backend_name=backend.name)
        self.assertEqual((done["status"], done["outcome"], done["cycle"]), ("done", "approved", 1))
        calls = self.calls()
        self.assertEqual(len(calls), 7)
        for call in calls:
            argv = call["argv"]
            self.assertIn("--available-tools", argv)
            self.assertEqual(sorted(argv[index + 1] for index, item in enumerate(argv) if item == "--deny-tool"),
                             ["shell", "write"])
            self.assertGreater(call["prompt_bytes"], 1000, "the prompt arrived on stdin")
            self.assertFalse(any("Execução determinística" in item for item in argv), "and not on the command line")
            self.assertEqual(call["entries"], [], "the agent starts in an empty folder")
            self.assertFalse(Path(call["cwd"]).exists(), "which is removed afterwards")
            self.assertFalse(Path(call["cwd"]).is_relative_to(self.root), "and is not inside the swarm")
        by_prompt = {tuple(call["argv"][call["argv"].index("--available-tools") + 1:][:1]) for call in calls}
        self.assertEqual(by_prompt, {("view",)})
        webbed = [call for call in calls if "web_fetch" in call["argv"]]
        self.assertGreater(len(webbed), 0)
        self.assertTrue(all("--allow-all-urls" in call["argv"] for call in webbed))
        self.assertTrue(all("--allow-all-urls" not in call["argv"] for call in calls if "web_fetch" not in call["argv"]))
        usage = self.root / "reports" / "execution" / "usage" / "c01.r0.authors.author-01-platform.a1.json"
        self.assertTrue(usage.is_file(), "the raw usage record is kept next to the result")
        record = json.loads((self.root / "reports" / "execution" / "results" /
                             "c01.r0.authors.author-01-platform.json").read_text(encoding="utf-8"))
        runtime = record["attempts"][0]["runtime"]
        self.assertEqual((runtime["backend"], runtime["exit_code"], runtime["models_seen"]), ("copilot-cli", 0, "auto"))
        self.assertNotIn("model_mismatch", runtime)

    def test_the_agents_of_one_step_start_together(self):
        backend = self.fake(FAKE_COPILOT_SLEEP=3)
        engine = self.engine()
        engine.init()
        tasks = engine.next()["tasks"]
        threads = [threading.Thread(target=backend, args=(item,)) for item in tasks]
        started = time.monotonic()
        for item in threads:
            item.start()
        for item in threads:
            item.join()
        wall = time.monotonic() - started
        stamps = sorted(call["at"] for call in self.calls())
        self.assertEqual(len(stamps), 2)
        self.assertLess(stamps[1] - stamps[0], 2.5, "one by one, the second would start after the first finished waiting")
        self.assertLess(wall, 7.5, "two waits of three seconds did not add up")

    def test_a_process_that_fails_once_is_retried_and_the_run_still_delivers(self):
        backend = self.fake(FAKE_COPILOT_FAIL="author-02-operations")
        done = driver.execute(self.root, backend, parallel=4, tick=3600, out=lambda text: None)
        self.assertEqual(done["outcome"], "approved")
        events = [item for item in (json.loads(line) for line in
                                    (self.root / "reports" / "execution" / "journal.jsonl").read_text(encoding="utf-8").splitlines())
                  if item["event"] == "task_recorded" and item["agent"] == "author-02-operations"]
        self.assertEqual([item["outcome"] for item in events], ["null", "accepted"])
        self.assertEqual(events[0]["runtime"]["error"], "exit 1")
        self.assertIn("simulated failure", events[0]["runtime"]["stderr"])
        self.assertEqual(len(self.calls()), 8)

    def test_an_answer_wrapped_in_a_fence_or_prose_is_accepted(self):
        for wrap in ("fence", "prose"):
            with self.subTest(wrap=wrap):
                root = build_swarm(Path(self.temporary.name) / wrap)
                engine = Engine(root)
                engine.init()
                task = engine.next()["tasks"][0]
                environment = {**self.environment(FAKE_COPILOT_WRAP=wrap), "FAKE_COPILOT_ROOT": str(root)}
                answer = CopilotCli(executable=[sys.executable, "-S", str(FAKE)], environment=environment)(task)
                self.assertIsInstance(answer.result, str)
                outcome = engine.record(task["task_id"], task["attempt"], task["inputs_sha256"], answer.result, answer.runtime)
                self.assertTrue(outcome["accepted"], outcome)

    def test_a_timeout_stops_the_process(self):
        backend = self.fake(timeout=8, FAKE_COPILOT_SLEEP=60)
        started = time.monotonic()
        answer = backend(self.first_task())
        self.assertIsNone(answer.result)
        self.assertEqual(answer.runtime["error"], "timeout")
        self.assertLess(time.monotonic() - started, 40)
        pid = self.calls()[0]["pid"]
        deadline = time.monotonic() + 10
        while alive(pid) and time.monotonic() < deadline:
            time.sleep(0.2)
        self.assertFalse(alive(pid), "the timed-out process is gone, not left running and billing")

    def test_a_timeout_ends_what_the_process_started_too(self):
        backend = self.fake(timeout=8, FAKE_COPILOT_SLEEP=60, FAKE_COPILOT_CHILD=1)
        started = time.monotonic()
        backend(self.first_task())
        self.assertLess(time.monotonic() - started, 40,
                        "a child holding the pipes open must not keep the run waiting for as long as it lives")
        children = [item["child"] for item in self.calls() if "child" in item]
        self.assertEqual(len(children), 1)
        deadline = time.monotonic() + 10
        while alive(children[0]) and time.monotonic() < deadline:
            time.sleep(0.2)
        self.assertFalse(alive(children[0]), "a child of the CLI (a shell, a tool server) must not outlive its agent")

    def test_an_orphan_that_keeps_the_pipes_open_cannot_hold_the_run_hostage(self):
        backend = self.fake(timeout=6, FAKE_COPILOT_SLEEP=60, FAKE_COPILOT_CHILD="orphan")
        backend.drain_timeout = 2
        started = time.monotonic()
        answer = backend(self.first_task())
        elapsed = time.monotonic() - started
        orphans = [item["child"] for item in self.calls() if "child" in item]
        try:
            self.assertEqual(answer.runtime["error"], "timeout")
            self.assertLess(elapsed, 28, "after the kill the run waits a bounded time for the pipes, not for as long as "
                                         "the orphan lives (about forty seconds here)")
        finally:
            for pid in orphans:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
                elif alive(pid):
                    os.kill(pid, 9)

    def test_a_result_is_recorded_the_moment_it_arrives_not_when_the_slowest_agent_finishes(self):
        journal = Journal(self.root / "reports" / "execution" / "journal.jsonl")

        class Slow:
            def __call__(inner, task):
                if task["agent"] == "author-01-platform":
                    deadline = time.monotonic() + 25
                    while time.monotonic() < deadline and not journal.find("task_recorded", agent="author-02-operations"):
                        time.sleep(0.2)
                    raise KeyboardInterrupt
                return Scripted(self.root, self.base)(task)

        with self.assertRaises(KeyboardInterrupt):
            driver.execute(self.root, Slow(), parallel=4, tick=3600, out=lambda text: None)
        accepted = [item["agent"] for item in journal.find("task_recorded") if item["outcome"] == "accepted"]
        self.assertEqual(accepted, ["author-02-operations"],
                         "the fast agent's work was already on disk when the slow one was interrupted")

    def test_no_output_and_a_failing_exit_are_missing_results_with_the_reason(self):
        empty = self.fake(FAKE_COPILOT_EMPTY=1)(self.first_task())
        self.assertIsNone(empty.result)
        self.assertEqual(empty.runtime["error"], "empty output")
        failing = self.fake(FAKE_COPILOT_FAIL="Execução determinística")(self.first_task())
        self.assertIsNone(failing.result)
        self.assertEqual(failing.runtime["error"], "exit 1")
        self.assertIn("simulated failure", failing.runtime["stderr"])
        partial = self.fake(FAKE_COPILOT_FAIL_WITH_OUTPUT=1)(self.first_task())
        self.assertIsNone(partial.result, "what a process printed before it failed is not an answer")
        self.assertEqual(partial.runtime["error"], "exit 1")

    def test_a_model_other_than_the_requested_one_is_flagged_not_hidden(self):
        task = self.first_task()
        task["model"] = "modelo-pedido"
        flagged = self.fake(FAKE_COPILOT_MODEL="outro-modelo")(task)
        self.assertEqual(flagged.runtime["models_seen"], "outro-modelo")
        self.assertTrue(flagged.runtime["model_mismatch"])
        honest = self.fake()(task)
        self.assertEqual(honest.runtime["models_seen"], "modelo-pedido")
        self.assertNotIn("model_mismatch", honest.runtime)
        task["model"] = "auto"
        self.assertNotIn("model_mismatch", self.fake(FAKE_COPILOT_MODEL="qualquer")(task).runtime,
                         "auto lets the CLI choose, so any model is the requested one")

    def test_cancel_stops_what_is_running_and_refuses_what_is_new(self):
        backend = self.fake(FAKE_COPILOT_SLEEP=60)
        task = self.first_task()
        results: list[Answer] = []
        worker = threading.Thread(target=lambda: results.append(backend(task)))
        worker.start()
        deadline = time.monotonic() + 30
        while not self.calls() and time.monotonic() < deadline:
            time.sleep(0.2)
        self.assertTrue(self.calls(), "the process started")
        backend.cancel()
        worker.join(timeout=30)
        self.assertFalse(worker.is_alive())
        self.assertEqual(results[0].runtime["error"], "cancelled")
        spawned = len(self.calls())
        self.assertEqual(backend(task).runtime["error"], "cancelled")
        self.assertEqual(len(self.calls()), spawned, "nothing new is started after a cancel")

    def test_a_large_prompt_travels_by_stdin_intact(self):
        prompt = ("Documento de teste com acentuação: não, é, ç, ü. " * 20 + "\n") * 400
        self.assertGreater(len(prompt.encode("utf-8")), 400_000)
        answer = self.fake(FAKE_COPILOT_MODE="echo")(task_for(prompt=prompt))
        value = json.loads(answer.result)
        self.assertEqual(value["bytes"], len(prompt.encode("utf-8")))
        self.assertEqual(value["sha256"], hashlib.sha256(prompt.encode("utf-8")).hexdigest())

    def test_a_cli_that_cannot_start_is_reported_not_raised(self):
        answer = CopilotCli(executable=["no-such-binary-for-the-tests"])(task_for(label="x"))
        self.assertIsNone(answer.result)
        self.assertIn("cannot start the CLI", answer.runtime["error"])

    def test_a_run_interrupted_by_the_user_says_so_and_cancels_the_agents(self):
        cancelled = []

        class Backend:
            name = "interruptible"

            def __call__(self, task):
                raise KeyboardInterrupt

            def cancel(self):
                cancelled.append(True)

        with self.assertRaises(KeyboardInterrupt):
            driver.execute(self.root, Backend(), parallel=2, tick=3600, out=lambda text: None)
        beat = json.loads((self.root / "reports" / "execution" / "driver.json").read_text(encoding="utf-8"))
        self.assertEqual(beat["state"], "interrupted")
        self.assertIn("execute o mesmo comando", beat["detail"])
        self.assertEqual(cancelled, [True])
        again = driver.execute(self.root, self.fake(), parallel=4, tick=3600, out=lambda text: None)
        self.assertEqual(again["outcome"], "approved", "the same command resumes where it stopped")


class MonitorTests(EngineCase):
    def make(self, **kwargs):
        clock = kwargs.pop("clock", None) or (lambda: 1_000_000.0)
        return driver.Monitor(self.root, backend="teste", parallel=4, out=kwargs.pop("out", lambda text: None),
                              clock=clock, **kwargs)

    def beat(self):
        return json.loads((self.root / "reports" / "execution" / "driver.json").read_text(encoding="utf-8"))

    def test_the_heartbeat_follows_the_agents_that_are_running(self):
        monitor = self.make()
        monitor.event("directive", {"directive": {"status": "agents", "cycle": 1, "stage": "authors"}})
        monitor.event("started", {"task": {"label": "c01.r0.authors.a.a1", "agent": "author-01", "model": "m"}})
        beat = self.beat()
        self.assertEqual((beat["state"], beat["stage"], beat["cycle"], beat["pid"]), ("running", "authors", 1, os.getpid()))
        self.assertEqual([item["agent"] for item in beat["running"]], ["author-01"])
        monitor.event("finished", {"task": {"label": "c01.r0.authors.a.a1", "agent": "author-01"},
                                   "outcome": {"accepted": True, "retry": False}})
        beat = self.beat()
        self.assertEqual(beat["running"], [])
        self.assertIn("author-01 aceito", beat["last"])

    def test_a_refused_attempt_says_whether_another_one_follows(self):
        monitor = self.make()
        for outcome, expected in (({"accepted": False, "retry": True}, "recusado, nova tentativa"),
                                  ({"accepted": False, "retry": False}, "recusado")):
            monitor.event("finished", {"task": {"label": "x", "agent": "a"}, "outcome": outcome})
            self.assertTrue(self.beat()["last"].endswith(expected), self.beat()["last"])

    def test_the_outcome_and_the_reason_for_a_stop_are_recorded(self):
        monitor = self.make()
        monitor.event("directive", {"directive": {"status": "done", "outcome": "approved", "cycle": 2}})
        self.assertEqual((self.beat()["state"], self.beat()["detail"]), ("done", "approved no ciclo 2"))
        monitor.event("directive", {"directive": {"status": "blocked", "kind": "task_failed", "detail": "um agente falhou"}})
        self.assertEqual((self.beat()["state"], self.beat()["detail"]), ("blocked", "um agente falhou"))
        monitor.event("directive", {"directive": {"status": "failed", "kind": "script_error", "detail": "script quebrou"}})
        self.assertEqual((self.beat()["state"], self.beat()["detail"]), ("failed", "script quebrou"))

    def test_the_table_names_the_agents_how_long_they_run_and_what_was_recorded(self):
        now = [1_000_000.0]
        monitor = self.make(clock=lambda: now[0])
        monitor.event("directive", {"directive": {"status": "agents", "cycle": 1, "stage": "reviewers"}})
        monitor.event("started", {"task": {"label": "L1", "agent": "reviewer-01-facts", "model": "m"}})
        monitor.event("started", {"task": {"label": "L2", "agent": "reviewer-02-clarity", "model": "m"}})
        now[0] += 300
        table = monitor.table()
        for text in ("Document Swarm · demo", "executor determinístico", "em andamento", "ciclo 1 de 3 · reviewers",
                     "reviewer-01-facts 5 min", "reviewer-02-clarity 5 min", "(2 de 4 simultâneos)",
                     "0 aceitos", "nenhum ainda"):
            self.assertIn(text, table)

    def test_a_table_is_printed_every_tick_and_once_more_at_the_end(self):
        printed: list[str] = []
        monitor = self.make(tick=0.2, beat=0.05, out=printed.append, clock=time.time)
        monitor.start()
        time.sleep(0.8)
        monitor.finish("done", "approved no ciclo 1")
        self.assertGreaterEqual(len(printed), 2)
        self.assertIn("encerrado", printed[-1])
        self.assertEqual(self.beat()["state"], "done")

    def test_a_broken_terminal_or_table_never_stops_the_run(self):
        def broken(text):
            raise BrokenPipeError

        monitor = self.make(tick=0.1, beat=0.05, out=broken, clock=time.time)
        monitor.start()
        time.sleep(0.4)
        monitor.finish("done")
        self.assertEqual(self.beat()["state"], "done")


class RunCommandTests(BackendCase):
    def run_cli(self, *args: str, extra_env: dict[str, str] | None = None, root: Path | None = None):
        environment = {**os.environ, "PYTHONUTF8": "1", **self.environment(), **(extra_env or {})}
        done = subprocess.run([sys.executable, "-S", "-m", "scripts.orchestration", "run", str(root or self.root), *args],
                              cwd=ROOT, capture_output=True, env=environment, timeout=600)
        return done.returncode, done.stdout.decode("utf-8"), done.stderr.decode("utf-8")

    fake_args = ("--copilot", sys.executable, "--copilot-arg=-S", f"--copilot-arg={FAKE}")

    def test_run_delivers_prints_the_table_and_exits_zero_for_an_approval(self):
        code, out, err = self.run_cli(*self.fake_args, "--tick", "3600")
        self.assertEqual(code, 0, err)
        self.assertIn("encerrado", out)
        self.assertIn("approved no ciclo 1", out)
        self.assertTrue((self.root / "reports" / "final-report.md").is_file())

    def test_json_keeps_stdout_for_the_answer_and_sends_the_tables_to_stderr(self):
        code, out, err = self.run_cli(*self.fake_args, "--tick", "3600", "--json")
        self.assertEqual(code, 0, err)
        answer = json.loads(out)
        self.assertEqual((answer["status"], answer["outcome"]), ("done", "approved"))
        self.assertIn("Document Swarm", err)

    def test_an_escalation_exits_one_because_a_person_must_decide(self):
        root = build_swarm(Path(self.temporary.name) / "esc", max_cycles=1)
        grades = json.dumps({"1|reviewer-01-facts|T02": "B+"})
        code, out, err = self.run_cli(*self.fake_args, "--tick", "3600", "--json", root=root,
                                      extra_env={"FAKE_COPILOT_ROOT": str(root), "FAKE_COPILOT_GRADES": grades})
        self.assertEqual(code, 1, err)
        self.assertEqual(json.loads(out)["outcome"], "escalated")

    def test_a_run_that_cannot_proceed_exits_three_and_says_why(self):
        code, out, err = self.run_cli(*self.fake_args, "--tick", "3600", "--max-attempts", "1",
                                      extra_env={"FAKE_COPILOT_EMPTY": "1"})
        self.assertEqual(code, 3, err)
        self.assertIn("blocked", err)
        self.assertIn("bloqueado", out)

    def test_plan_only_shows_the_commands_and_runs_nothing(self):
        code, out, err = self.run_cli(*self.fake_args, "--plan-only")
        self.assertEqual(code, 0, err)
        plan = json.loads(out)
        self.assertFalse(plan["spends_credits"])
        self.assertEqual({item["agent"] for item in plan["tasks"]}, {"author-01-platform", "author-02-operations"})
        first = plan["tasks"][0]
        self.assertGreater(first["prompt_bytes"], 1000)
        self.assertIn("--available-tools", first["command"])
        self.assertEqual(first["command"][:3], [sys.executable, "-S", str(FAKE)])
        self.assertEqual(self.calls(), [], "no agent was started")

    def test_the_wrapper_arguments_need_a_wrapper(self):
        code, _, err = self.run_cli("--copilot-arg=-S")
        self.assertEqual(code, 2)
        self.assertIn("--copilot-arg only makes sense together with --copilot", err)

    def test_a_missing_copilot_is_an_error_with_the_remedy(self):
        empty = Path(self.temporary.name) / "empty-path"
        empty.mkdir()
        code, _, err = self.run_cli(extra_env={"PATH": str(empty), "DOCSWARM_NO_REAL_CLI": "0"})
        self.assertEqual(code, 2)
        self.assertIn("not on PATH", err)

    def test_the_same_command_resumes_a_stopped_run(self):
        code, _, err = self.run_cli(*self.fake_args, "--tick", "3600", "--max-attempts", "1",
                                    extra_env={"FAKE_COPILOT_EMPTY": "1"})
        self.assertEqual(code, 3, err)
        code, out, err = self.run_cli(*self.fake_args, "--tick", "3600", "--max-attempts", "2")
        self.assertEqual(code, 0, err + out)
        events = [json.loads(line) for line in
                  (self.root / "reports" / "execution" / "journal.jsonl").read_text(encoding="utf-8").splitlines()]
        author = [item["outcome"] for item in events
                  if item["event"] == "task_recorded" and item["agent"] == "author-01-platform"]
        self.assertEqual(author, ["null", "accepted"], "more attempts were granted and the work went on from there")


class QualifyTests(BackendCase):
    def qualify_cli(self, *args: str, extra_env: dict[str, str] | None = None):
        environment = {**os.environ, "PYTHONUTF8": "1", **self.environment(), **(extra_env or {})}
        output = Path(self.temporary.name) / "qualification.json"
        done = subprocess.run([sys.executable, "-S", "-m", "scripts.orchestration", "qualify", "--output", str(output),
                               "--copilot", sys.executable, "--copilot-arg=-S", f"--copilot-arg={FAKE}", *args],
                              cwd=ROOT, capture_output=True, env=environment, timeout=600)
        return done.returncode, done.stdout.decode("utf-8"), done.stderr.decode("utf-8"), output

    def test_it_refuses_to_spend_credits_without_being_told_to(self):
        code, out, err, output = self.qualify_cli("--model", "barato")
        self.assertEqual(code, 2)
        self.assertIn("pass --yes", err)
        self.assertEqual(self.calls(), [])
        self.assertFalse(output.exists())

    def test_a_cli_that_honours_its_flags_is_qualified_and_the_report_says_what_held(self):
        code, out, err, output = self.qualify_cli("--model", "barato", "--yes", "--large-kb", "32")
        self.assertEqual(code, 0, out + err)
        report = json.loads(output.read_text(encoding="utf-8"))
        self.assertTrue(report["qualified"])
        passed = {item["probe"]: item["passed"] for item in report["probes"]}
        self.assertEqual(passed, {"contract": True, "usage": True, "no-write": True, "web": True, "parallel": True,
                                  "large-prompt": True})
        self.assertEqual(report["model"], "barato")
        self.assertEqual(report["inconclusive"], [])
        self.assertIn("QUALIFICADO", out)

    def test_a_cli_that_ignores_the_tool_restriction_is_not_qualified_and_leaves_nothing_behind(self):
        code, out, err, output = self.qualify_cli("--model", "barato", "--yes", extra_env={"FAKE_COPILOT_DISOBEY": "1"})
        self.assertEqual(code, 1, out + err)
        report = json.loads(output.read_text(encoding="utf-8"))
        self.assertFalse(report["qualified"])
        failed = [item for item in report["probes"] if item["passed"] is False]
        self.assertEqual([item["probe"] for item in failed], ["no-write"])
        self.assertIn("CRIOU", failed[0]["detail"])
        self.assertIn("NÃO qualificado", out)

    def test_a_web_tool_that_does_not_work_under_the_restriction_is_not_qualified(self):
        code, out, err, output = self.qualify_cli("--model", "barato", "--yes", extra_env={"FAKE_COPILOT_TITLE": "404"})
        self.assertEqual(code, 1, out + err)
        report = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual([item["probe"] for item in report["probes"] if item["passed"] is False], ["web"])

    def test_a_cli_that_answers_nothing_is_not_qualified(self):
        code, out, err, output = self.qualify_cli("--model", "barato", "--yes", extra_env={"FAKE_COPILOT_EMPTY": "1"})
        self.assertEqual(code, 1, out + err)
        report = json.loads(output.read_text(encoding="utf-8"))
        self.assertFalse(report["qualified"])
        contract = next(item for item in report["probes"] if item["probe"] == "contract")
        self.assertFalse(contract["passed"])
        self.assertIn("empty output", contract["detail"])

    def test_a_usage_record_that_cannot_be_read_is_inconclusive_not_a_failure(self):
        backend = CopilotCli(executable=[sys.executable, "-S", str(FAKE)], environment=self.environment())
        report = qualify.run(backend, model="barato")
        self.assertTrue(report["qualified"])
        self.assertEqual([item["probe"] for item in report["probes"] if item["passed"] is None], ["usage"])
        self.assertEqual(report["inconclusive"], ["usage"])
        self.assertTrue(report["notes"])

    def test_a_different_model_in_the_usage_record_disqualifies_the_backend(self):
        backend = CopilotCli(executable=[sys.executable, "-S", str(FAKE)], usage_dir=Path(self.temporary.name) / "usage",
                             environment=self.environment(FAKE_COPILOT_MODEL="outro"))
        report = qualify.run(backend, model="barato")
        usage = next(item for item in report["probes"] if item["probe"] == "usage")
        self.assertFalse(usage["passed"])
        self.assertIn("outro", usage["detail"])
        self.assertFalse(report["qualified"], "a model that is not the one requested is a silent substitution")


if __name__ == "__main__":
    unittest.main()
