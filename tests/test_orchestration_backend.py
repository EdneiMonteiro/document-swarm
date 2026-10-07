from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from scripts.checks.common import InputError
from scripts.orchestration import driver, qualify
from scripts.orchestration.backend import CopilotCli, mcp_flags, models_from_usage, parse_mcp_list
from scripts.orchestration.contracts import Answer
from scripts.orchestration.engine import Engine
from scripts.orchestration.store import Journal
from tests.test_orchestration_engine import EngineCase, Scripted, build_swarm

# No test may reach a model: a broken guard under test must fail the test, not spend credits.
os.environ["DOCSWARM_NO_REAL_CLI"] = "1"

ROOT = Path(__file__).resolve().parents[1]
FAKE = Path(__file__).with_name("fake_copilot.py")
# What `copilot mcp list` printed on the machine where the startup cost was measured.
MCP_LISTING = (
    "User servers:\n  playwright (local)\n  azure (local)\n  microsoft-learn (http)\n  filesystem (local)\n"
    "  sequential-thinking (local)\n  workiq (local)\n\n"
    "Plugin servers:\n  msx (local)\n  action360 (local)\n  earnings (local)\n  finhub (local)\n  customer360 (local)\n\n"
    "Builtin servers:\n  computer-use (local)\n  github-mcp-server (http)\n")
MCP_NAMES = ["playwright", "azure", "microsoft-learn", "filesystem", "sequential-thinking", "workiq",
             "msx", "action360", "earnings", "finhub", "customer360"]


def stopped_servers(argv: list[str]) -> list[str]:
    return [argv[index + 1] for index, item in enumerate(argv) if item == "--disable-mcp-server"]


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

    def test_describing_a_command_never_runs_the_cli(self):
        # Not a program that cannot be found, which would hide an attempt to run it: any attempt fails the test.
        backend = CopilotCli(executable=["nao-existe-copilot-xyz"])
        with mock.patch.object(CopilotCli, "list_mcp", side_effect=AssertionError("asked the CLI for its servers")), \
                mock.patch.object(subprocess, "Popen", side_effect=AssertionError("started a process")), \
                mock.patch.object(subprocess, "run", side_effect=AssertionError("ran a process")):
            argv = backend.command(task_for())
            self.assertEqual(backend.command(task_for(), None, None), argv)
        self.assertNotIn("--disable-mcp-server", argv)
        self.assertNotIn("--disable-builtin-mcps", argv)

    def test_the_servers_to_stop_come_after_the_rest_of_the_command_and_leave_the_tools_alone(self):
        argv = self.backend().command(task_for(), None, parse_mcp_list(MCP_LISTING))
        start = argv.index("--available-tools") + 1
        self.assertEqual(argv[start:start + 5], ["view", "glob", "grep", "web_search", "web_fetch"])
        self.assertEqual(stopped_servers(argv), MCP_NAMES)
        self.assertIn("--disable-builtin-mcps", argv)
        self.assertGreater(argv.index("--disable-mcp-server"), argv.index("--allow-all-urls"))

    def test_the_listing_of_the_cli_is_read_by_origin(self):
        found = parse_mcp_list(MCP_LISTING)
        self.assertEqual(found["user"], MCP_NAMES[:6])
        self.assertEqual(found["plugin"], MCP_NAMES[6:])
        self.assertEqual(found["builtin"], ["computer-use", "github-mcp-server"])

    def test_anything_that_is_not_the_listing_is_read_as_no_servers(self):
        for text in ("", "No MCP servers configured.\n", "error: unknown command\n", "  stray (local)\n",
                     "User servers:\nnot an entry\n", "\x00\x00", "User servers:\n  bad name (local)\n",
                     "User servers:\n  " + "x" * 200 + " (local)\n"):
            with self.subTest(text=text[:30]):
                self.assertEqual(parse_mcp_list(text), {})

    def test_a_listing_with_a_line_that_is_not_part_of_it_is_not_acted_on_in_part(self):
        # The builtin servers are stopped by one flag for all of them: read only as far as the odd line, the listing
        # would name computer-use alone, and stopping "the builtin servers" would take github-mcp-server with it.
        broken = ("User servers:\n  playwright (local)\n\nBuiltin servers:\n  computer-use (local)\n"
                  "warning: the listing was cut short\n  github-mcp-server (http)\n")
        self.assertEqual(parse_mcp_list(broken), {})
        for text in (MCP_LISTING + "Note: run `copilot mcp add` to add a server.\n", MCP_LISTING.replace("Builtin servers:", "Builtin servers (2):"),
                     "Builtin servers:\n  computer-use (local)\n  github-mcp-server\n"):
            with self.subTest(text=text[-40:]):
                self.assertEqual(parse_mcp_list(text), {})
        self.assertEqual(parse_mcp_list(MCP_LISTING)["builtin"], ["computer-use", "github-mcp-server"])
        self.assertEqual(parse_mcp_list(MCP_LISTING.replace("\n", "\r\n"))["user"], MCP_NAMES[:6], "the CLI on Windows ends lines with CRLF")
        self.assertEqual(parse_mcp_list("\n\nUser servers:\n  playwright (local)\n\n\n"), {"user": ["playwright"]})
        self.assertEqual(parse_mcp_list("User servers:\n\nPlugin servers:\n  msx (local)\n"), {"plugin": ["msx"]}, "a heading with nothing under it")

    def test_every_server_the_task_has_no_tool_of_is_stopped_builtin_ones_with_their_own_flag(self):
        flags = mcp_flags(["view", "glob"], parse_mcp_list(MCP_LISTING))
        self.assertEqual(stopped_servers(flags), MCP_NAMES)
        self.assertEqual(flags.count("--disable-builtin-mcps"), 1)
        self.assertEqual(mcp_flags(["view"], {}), [], "nothing is known, so nothing is stopped")

    def test_a_server_whose_tool_the_task_lists_is_kept(self):
        servers = parse_mcp_list(MCP_LISTING)
        flags = mcp_flags(["view", "microsoft-learn-microsoft_docs_search", "workiq-ask"], servers)
        stopped = stopped_servers(flags)
        self.assertNotIn("microsoft-learn", stopped)
        self.assertNotIn("workiq", stopped)
        self.assertIn("playwright", stopped)
        self.assertIn("--disable-builtin-mcps", flags)
        self.assertNotIn("--disable-builtin-mcps", mcp_flags(["github-mcp-server-get_file_contents"], servers),
                         "a task that uses a builtin server keeps the builtin ones")

    def test_only_a_tool_of_that_server_keeps_it(self):
        self.assertEqual(mcp_flags(["azurex-foo", "azur"], {"user": ["azure"]}), ["--disable-mcp-server", "azure"])
        self.assertEqual(mcp_flags(["azure"], {"user": ["azure"]}), [], "a tool named like the server keeps it")
        self.assertEqual(mcp_flags(["azure-acr"], {"user": ["azure"]}), [])

    def test_a_name_shared_by_two_origins_is_stopped_once(self):
        self.assertEqual(mcp_flags(["view"], {"user": ["dup"], "plugin": ["dup"]}), ["--disable-mcp-server", "dup"])

    def test_a_cli_that_cannot_even_start_is_not_a_reason_to_stop(self):
        self.assertEqual(CopilotCli(executable=["nao-existe-copilot-xyz"]).mcp_servers(), {})

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
        # The backend makes one scratch folder per agent in the system temp folder.  A deliberate orphan keeps its
        # folder open, so it cannot be removed at once; pointing the temp folder inside the test's own tree keeps
        # that leftover out of the user's real temp folder.
        self.scratch = Path(self.temporary.name) / "tmp"
        self.scratch.mkdir()
        previous, tempfile.tempdir = tempfile.tempdir, str(self.scratch)
        self.addCleanup(setattr, tempfile, "tempdir", previous)

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

    def test_a_task_run_again_keeps_the_usage_record_of_every_run(self):
        # The first real run lost two usage records: a task asked again after its identity changed has the same label,
        # and the second call wrote over the first, the only account of what it cost.
        backend = self.fake()
        task = self.first_task()
        first, second = backend(task), backend(task)
        self.assertEqual(first.runtime["usage_file"], f"{task['label']}.json")
        self.assertEqual(second.runtime["usage_file"], f"{task['label']}.2.json")
        usage = self.root / "reports" / "execution" / "usage"
        self.assertEqual(sorted(path.name for path in usage.glob("*.json")),
                         sorted([first.runtime["usage_file"], second.runtime["usage_file"]]))

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
            deadline = time.monotonic() + 15
            while any(alive(pid) for pid in orphans) and time.monotonic() < deadline:
                time.sleep(0.2)

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


class McpPruningTests(BackendCase):
    def run_all(self, backend: CopilotCli) -> None:
        engine = self.engine()
        engine.init()
        for task in engine.next()["tasks"]:
            backend(task)

    def test_an_agent_starts_without_the_servers_it_has_no_tool_of_and_the_cli_is_asked_once_per_run(self):
        log = Path(self.temporary.name) / "mcp.jsonl"
        self.run_all(self.fake(FAKE_COPILOT_MCP_LIST=MCP_LISTING, FAKE_COPILOT_MCP_LOG=log))
        calls = self.calls()
        self.assertEqual(len(calls), 2, "the listing is not an agent call")
        for call in calls:
            self.assertEqual(stopped_servers(call["argv"]), MCP_NAMES)
            self.assertIn("--disable-builtin-mcps", call["argv"])
        [listing] = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(listing["mcp_list"], ["mcp", "list"])
        self.assertTrue(Path(listing["cwd"]).name.startswith("docswarm-mcp-"), "asked from an empty folder of its own")
        self.assertFalse(Path(listing["cwd"]).exists(), "and that folder is removed")

    def test_a_cli_whose_listing_cannot_be_trusted_is_run_as_before(self):
        for listing in ("fail", "texto que não é a listagem\n"):
            with self.subTest(listing=listing[:12]):
                before = len(self.calls())
                self.fake(FAKE_COPILOT_MCP_LIST=listing)(self.first_task())
                argv = self.calls()[before]["argv"]
                self.assertNotIn("--disable-mcp-server", argv)
                self.assertNotIn("--disable-builtin-mcps", argv)

    def test_pruning_can_be_switched_off_and_then_the_cli_is_not_even_asked(self):
        log = Path(self.temporary.name) / "mcp.jsonl"
        backend = CopilotCli(executable=[sys.executable, "-S", str(FAKE)], prune_mcp=False,
                             environment=self.environment(FAKE_COPILOT_MCP_LIST=MCP_LISTING, FAKE_COPILOT_MCP_LOG=log))
        backend(self.first_task())
        self.assertNotIn("--disable-mcp-server", self.calls()[0]["argv"])
        self.assertFalse(log.exists())

    def test_the_listing_leaves_nothing_registered_as_running(self):
        backend = self.fake(FAKE_COPILOT_MCP_LIST=MCP_LISTING)
        self.assertEqual(backend.list_mcp()["builtin"], ["computer-use", "github-mcp-server"])
        self.assertEqual(backend.running, {})

    def test_a_cancel_that_arrives_while_the_servers_are_being_listed_starts_no_agent(self):
        backend = self.fake()
        listing, release = threading.Event(), threading.Event()

        def slow() -> dict[str, list[str]]:
            listing.set()
            release.wait(30)
            return {}

        backend.list_mcp = slow
        task = self.first_task()
        answers: list[Answer] = []
        worker = threading.Thread(target=lambda: answers.append(backend(task)))
        worker.start()
        self.assertTrue(listing.wait(30), "the worker is inside the listing")
        backend.cancel()
        release.set()
        worker.join(timeout=30)
        self.assertFalse(worker.is_alive())
        self.assertEqual(answers[0].runtime["error"], "cancelled")
        self.assertEqual(self.calls(), [], "no agent ran unwatched, and none was paid for only to be thrown away")

    def test_a_cancel_ends_a_listing_that_is_still_running(self):
        log = Path(self.temporary.name) / "mcp.jsonl"
        backend = self.fake(FAKE_COPILOT_MCP_LIST=MCP_LISTING, FAKE_COPILOT_MCP_LOG=log, FAKE_COPILOT_MCP_SLEEP=60)
        task = self.first_task()
        answers: list[Answer] = []
        worker = threading.Thread(target=lambda: answers.append(backend(task)))
        worker.start()
        deadline = time.monotonic() + 30
        while not log.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertTrue(log.exists(), "the listing started")
        started = time.monotonic()
        backend.cancel()
        worker.join(timeout=30)
        self.assertFalse(worker.is_alive())
        self.assertLess(time.monotonic() - started, 20, "the listing was stopped, not waited for (it sleeps for 60 s)")
        self.assertEqual(answers[0].runtime["error"], "cancelled")
        self.assertEqual(self.calls(), [])

    def test_a_listing_that_overruns_is_ended_with_everything_it_started(self):
        log = Path(self.temporary.name) / "mcp.jsonl"
        backend = self.fake(FAKE_COPILOT_MCP_LIST=MCP_LISTING, FAKE_COPILOT_MCP_LOG=log, FAKE_COPILOT_MCP_SLEEP=60,
                            FAKE_COPILOT_MCP_CHILD=1)
        backend.drain_timeout = 2
        children: list[int] = []
        try:
            with mock.patch("scripts.orchestration.backend.MCP_LIST_TIMEOUT", 3):
                started = time.monotonic()
                found = backend.list_mcp()
                elapsed = time.monotonic() - started
            children = [json.loads(line)["child"] for line in log.read_text(encoding="utf-8").splitlines() if '"child"' in line]
            self.assertEqual(found, {})
            self.assertLess(elapsed, 25, "a child that holds the pipes open must not keep the run waiting (it lives for 40 s)")
            self.assertEqual(len(children), 1)
            deadline = time.monotonic() + 10
            while alive(children[0]) and time.monotonic() < deadline:
                time.sleep(0.2)
            self.assertFalse(alive(children[0]), "what the listing started does not outlive it")
            self.assertEqual(backend.running, {})
        finally:
            for pid in children:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
                elif alive(pid):
                    os.kill(pid, 9)


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

    def test_a_result_that_could_not_be_recorded_leaves_the_running_list_and_says_so(self):
        monitor = self.make()
        monitor.event("started", {"task": {"label": "x", "agent": "author-01", "model": "m"}})
        monitor.event("failed", {"task": {"label": "x", "agent": "author-01"}, "error": "OSError: disk full"})
        beat = self.beat()
        self.assertEqual(beat["running"], [])
        self.assertIn("author-01: o resultado não pôde ser registrado (OSError: disk full)", beat["last"])

    def test_an_event_after_the_run_ended_does_not_write_over_its_final_state(self):
        # With fewer workers than tasks a worker can begin after an interrupt has already been handled.
        monitor = self.make()
        monitor.event("directive", {"directive": {"status": "agents", "cycle": 1, "stage": "authors"}})
        monitor.finish("interrupted", "interrompido pelo usuário")
        monitor.event("started", {"task": {"label": "late", "agent": "author-02", "model": "m"}})
        beat = self.beat()
        self.assertEqual((beat["state"], beat["running"]), ("interrupted", []))

    def test_a_snapshot_taken_earlier_cannot_be_written_after_the_final_one(self):
        # Snapshot and write are one step: a thread that took its snapshot while the run was "running" and is slow
        # to write it must not leave that state on disk after the run has been marked interrupted.
        monitor = self.make()
        monitor.event("directive", {"directive": {"status": "agents", "cycle": 1, "stage": "authors"}})
        entered, release = threading.Event(), threading.Event()
        real, taken = monitor.snapshot, []

        def slow_first_snapshot():
            data = real()
            if not taken:
                taken.append(data)
                entered.set()
                release.wait(10)
            return data

        monitor.snapshot = slow_first_snapshot
        writer = threading.Thread(target=monitor.write_heartbeat)
        writer.start()
        self.assertTrue(entered.wait(10))
        finisher = threading.Thread(target=lambda: monitor.finish("interrupted", "interrompido"))
        finisher.start()
        time.sleep(0.3)
        release.set()
        writer.join(10)
        finisher.join(10)
        self.assertEqual(taken[0]["state"], "running")
        self.assertEqual(self.beat()["state"], "interrupted")

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

    def test_a_run_that_escalated_goes_one_cycle_further_with_a_higher_ceiling_and_pays_only_for_it(self):
        root = build_swarm(Path(self.temporary.name) / "further", max_cycles=1)
        extra = {"FAKE_COPILOT_ROOT": str(root), "FAKE_COPILOT_GRADES": json.dumps({"1|reviewer-01-facts|T02": "B+"})}
        code, out, err = self.run_cli(*self.fake_args, "--tick", "3600", "--json", root=root, extra_env=extra)
        self.assertEqual((code, json.loads(out)["outcome"]), (1, "escalated"), err)
        paid = len(self.calls())
        code, out, err = self.run_cli(*self.fake_args, "--tick", "3600", "--json", "--max-cycles", "2", root=root,
                                      extra_env=extra)
        self.assertEqual(code, 0, err)
        answer = json.loads(out)
        self.assertEqual((answer["outcome"], answer["cycle"]), ("approved", 2))
        self.assertEqual(len(self.calls()) - paid, 6,
                         "cycle 2 only: the author of the blocked topic, the coordinator, two reviewers, the audit and "
                         "the narrative; nothing of cycle 1 is paid for again")

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

    def test_run_passes_the_approval_grade_to_the_engine_and_the_plan_keeps_it(self):
        code, out, err = self.run_cli(*self.fake_args, "--plan-only", "--approval-grade", "A")
        self.assertEqual(code, 0, err)
        plan = json.loads((self.root / "reports" / "execution" / "plan.json").read_text(encoding="utf-8"))
        self.assertEqual((plan["approval_grade"], plan["options"]["approval_grade"]), ("A", "A"))
        code, out, err = self.run_cli(*self.fake_args, "--plan-only")
        self.assertEqual(code, 0, err)
        plan = json.loads((self.root / "reports" / "execution" / "plan.json").read_text(encoding="utf-8"))
        self.assertEqual(plan["approval_grade"], "A", "a later run without the option keeps what was set")

    def test_plan_only_shows_the_servers_that_will_be_stopped_and_keep_mcp_servers_leaves_them_running(self):
        extra = {"FAKE_COPILOT_MCP_LIST": MCP_LISTING}
        code, out, err = self.run_cli(*self.fake_args, "--plan-only", extra_env=extra)
        self.assertEqual(code, 0, err)
        command = json.loads(out)["tasks"][0]["command"]
        self.assertEqual(stopped_servers(command), MCP_NAMES)
        self.assertIn("--disable-builtin-mcps", command)
        code, out, err = self.run_cli(*self.fake_args, "--plan-only", "--keep-mcp-servers", extra_env=extra)
        self.assertEqual(code, 0, err)
        command = json.loads(out)["tasks"][0]["command"]
        self.assertEqual(stopped_servers(command), [])
        self.assertNotIn("--disable-builtin-mcps", command)

    def test_a_declared_model_outside_the_session_list_is_refused_before_any_agent_is_paid_for(self):
        author = self.root / "agents" / "authors" / "author-01-platform.md"
        author.write_text(author.read_text(encoding="utf-8").replace("model: auto", "model: modelo-fantasma"), encoding="utf-8")
        for mode in ((), ("--plan-only",)):
            code, out, err = self.run_cli(*self.fake_args, "--tick", "3600", *mode, "--models", "modelo-real-1,modelo-real-2")
            self.assertEqual(code, 2, (mode, out, err))
            self.assertIn("modelo-fantasma", err)
            self.assertEqual(self.calls(), [], "nothing was started")
        code, out, err = self.run_cli(*self.fake_args, "--plan-only", "--models", " modelo-fantasma , modelo-real-1")
        self.assertEqual(code, 0, err)
        self.assertIn("modelo-fantasma", out, "a listed model is accepted, with the blanks around the names ignored")

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
        # The probes create files and folders in the temp folder; here that is the test's own, so that what they
        # leave behind can be checked exactly.
        where = {"TEMP": str(self.scratch), "TMP": str(self.scratch), "TMPDIR": str(self.scratch)}
        environment = {**os.environ, "PYTHONUTF8": "1", **self.environment(), **where, **(extra_env or {})}
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
        self.assertEqual(passed, {"contract": True, "usage": True, "no-write": True, "confined": True, "web": True,
                                  "parallel": True, "large-prompt": True})
        self.assertEqual(report["model"], "barato")
        self.assertEqual(report["inconclusive"], [])
        self.assertIn("QUALIFICADO", out)
        self.assertEqual(list(self.scratch.iterdir()), [], "the probes leave no file or folder behind")

    def test_a_cli_that_ignores_the_tool_restriction_is_not_qualified_and_leaves_nothing_behind(self):
        code, out, err, output = self.qualify_cli("--model", "barato", "--yes", extra_env={"FAKE_COPILOT_DISOBEY": "1"})
        self.assertEqual(code, 1, out + err)
        report = json.loads(output.read_text(encoding="utf-8"))
        self.assertFalse(report["qualified"])
        failed = [item for item in report["probes"] if item["passed"] is False]
        self.assertEqual([item["probe"] for item in failed], ["no-write"])
        self.assertIn("CRIOU", failed[0]["detail"])
        self.assertIn("NÃO qualificado", out)
        self.assertEqual(list(self.scratch.iterdir()), [], "the file the disobedient agent created is removed")

    def test_a_cli_that_reads_outside_its_working_folder_is_not_qualified_and_leaves_nothing_behind(self):
        code, out, err, output = self.qualify_cli("--model", "barato", "--yes", extra_env={"FAKE_COPILOT_LEAK": "1"})
        self.assertEqual(code, 1, out + err)
        report = json.loads(output.read_text(encoding="utf-8"))
        self.assertFalse(report["qualified"])
        failed = [item for item in report["probes"] if item["passed"] is False]
        self.assertEqual([item["probe"] for item in failed], ["confined"])
        self.assertIn("LEU", failed[0]["detail"])
        self.assertEqual(list(self.scratch.iterdir()), [], "the canary folder is removed even when it was read")

    def test_the_confinement_canary_sits_in_the_system_temp_folder_beside_the_agents_own_folder(self):
        seen: list[tuple[str, str]] = []
        real = CopilotCli.__call__

        def spy(backend, task):
            match = re.search(r"arquivo `([^`]+)`", task["prompt"])
            if match and task["task_id"] == "qualify-confined":
                seen.append((str(Path(match.group(1)).parent.parent), tempfile.gettempdir()))
            return real(backend, task)

        backend = CopilotCli(executable=[sys.executable, "-S", str(FAKE)], environment=self.environment())
        with mock.patch.object(CopilotCli, "__call__", spy):
            qualify.confined(backend, "barato")
        self.assertEqual(len(seen), 1)
        self.assertEqual(os.path.normcase(seen[0][0]), os.path.normcase(seen[0][1]),
                         "inside the system temp folder, where --disallow-temp-dir is what keeps the agent out")

    def test_a_probe_that_gets_no_answer_is_inconclusive_and_a_required_one_blocks_the_qualification(self):
        for name in ("no-write", "confined", "web"):
            with self.subTest(probe=name):
                backend = CopilotCli(executable=[sys.executable, "-S", str(FAKE)],
                                     environment=self.environment(FAKE_COPILOT_SILENT=name))
                report = qualify.run(backend, model="barato")
                state = {item["probe"]: item["passed"] for item in report["probes"]}
                self.assertIsNone(state[name], "no answer is not a pass and not a failure either")
                self.assertIn(name, report["inconclusive"])
                self.assertFalse(report["qualified"], "a required probe that could not be concluded does not qualify")

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
