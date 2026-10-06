from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from scripts.checks.common import InputError
from scripts.orchestration import __main__ as cli
from scripts.orchestration import metrics
from tests.test_orchestration_engine import EngineCase

ORIGIN = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def at(seconds: float) -> str:
    return (ORIGIN + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")


class Journal:
    """Build a monitor journal with exact timings."""

    def __init__(self) -> None:
        self.events: list[dict] = []

    def add(self, seconds: float, kind: str, **data) -> "Journal":
        self.events.append({"id": f"e{len(self.events)}", "at": at(seconds), "type": kind, "data": data})
        return self

    def dispatch(self, key: str, seconds: float, agent: str = "author-01", role: str = "author", cycle: int = 1):
        return (self.add(seconds, "dispatch", id=key, agent_id=agent, agent_kind=role, cycle=cycle)
                .add(seconds + 2, "binding", dispatch_id=key, bound_at=at(seconds + 2)))

    def run(self, key: str, start: float, end: float, *, closes: str = "idle", started_at: float | None = None):
        self.add(start, "runtime", dispatch_id=key, status="running",
                 started_at=at(start if started_at is None else started_at))
        return self.add(end, "runtime", dispatch_id=key, status=closes)


class DecompositionTests(unittest.TestCase):
    def test_wall_clock_splits_into_agent_time_and_nobody_time(self):
        journal = Journal().add(0, "phase", phase="authors", cycle=1)
        journal.dispatch("a", 0).run("a", 10, 110)
        journal.add(1000, "phase", phase="reviews", cycle=1)
        result = metrics.execution(journal.events)
        self.assertEqual(result["wall_seconds"], 1000)
        self.assertEqual(result["agent_running_union_seconds"], 100)
        self.assertEqual(result["nobody_running_seconds"], 900)
        self.assertEqual(result["nobody_running_share"], 0.9)

    def test_parallel_agents_are_a_union_and_a_sum(self):
        journal = Journal()
        journal.dispatch("a", 0, "author-01").run("a", 10, 110)
        journal.dispatch("b", 0, "author-02").run("b", 10, 110)
        result = metrics.execution(journal.events)
        self.assertEqual(result["agent_running_union_seconds"], 100)
        self.assertEqual(result["agent_running_summed_seconds"], 200)
        self.assertEqual(result["parallelism"], 2.0)

    def test_a_repeated_started_at_does_not_count_the_same_interval_twice(self):
        # The runtime repeats the dispatch's first start on every later turn.  Reopening
        # with it once turned 18 minutes of work into hours of "agent time".
        journal = Journal().dispatch("a", 0, "reviewer-01", "reviewer")
        for turn_start, turn_end in ((10, 70), (300, 330), (600, 660)):
            journal.run("a", turn_start, turn_end, started_at=10)
        result = metrics.execution(journal.events)
        self.assertEqual(result["agent_running_summed_seconds"], 60 + 30 + 60)
        self.assertEqual(result["agent_seconds_by_role"]["reviewer"]["total"], 150)

    def test_a_terminal_state_is_final(self):
        journal = Journal().dispatch("a", 0).run("a", 10, 60, closes="completed")
        journal.add(5000, "runtime", dispatch_id="a", status="running")
        journal.add(9000, "runtime", dispatch_id="a", status="idle")
        result = metrics.execution(journal.events)
        self.assertEqual(result["agent_running_summed_seconds"], 50)

    def test_an_agent_never_seen_stopping_ends_at_its_last_observation(self):
        journal = Journal().dispatch("a", 0, "reviewer-09", "reviewer", cycle=2)
        journal.add(10, "runtime", dispatch_id="a", status="running", started_at=at(10))
        journal.add(40, "runtime", dispatch_id="a", status="running")
        journal.add(100000, "session", status="waiting")
        result = metrics.execution(journal.events)
        self.assertEqual(result["agent_running_union_seconds"], 30)
        self.assertEqual(result["nobody_running_seconds"], 100000 - 30)
        [missing] = result["dispatches_without_an_end"]
        self.assertEqual((missing["agent"], missing["cycle"]), ("reviewer-09", 2))

    def test_a_long_gap_is_split_between_running_and_idle_time(self):
        journal = Journal().dispatch("a", 0).run("a", 0, 1000)
        journal.add(1200, "session", status="idle")
        journal.add(5000, "session", status="idle")
        result = metrics.execution(journal.events)
        # The first silence starts at the binding event (t=2), not at the dispatch.
        first, second = sorted(result["largest_gaps"], key=lambda item: item["from"])
        self.assertEqual((first["seconds"], first["agent_running_seconds"]), (998, 998))
        self.assertEqual(first["nobody_running_seconds"], 0)
        self.assertEqual(second["nobody_running_seconds"], 3800)

    def test_a_gap_lists_who_was_last_seen_running(self):
        journal = Journal().dispatch("a", 0, "reviewer-07", "reviewer")
        journal.add(10, "runtime", dispatch_id="a", status="running", started_at=at(10))
        journal.add(20, "session", status="processing")
        journal.add(4000, "session", status="idle")
        [gap] = metrics.execution(journal.events)["largest_gaps"]
        self.assertEqual(gap["last_seen_running"], ["reviewer-07"])

    def test_phase_totals_attribute_idle_time_to_the_phase_it_happened_in(self):
        journal = Journal().add(0, "phase", phase="rubber-duck", cycle=3)
        journal.dispatch("a", 0, "rubber-duck", "rubber-duck").run("a", 5, 105)
        journal.add(20000, "session", status="idle")
        row = metrics.execution(journal.events)["phase_totals"]["rubber-duck"]
        self.assertEqual(row["agent_running_seconds"], 100)
        self.assertEqual(row["nobody_running_seconds"], 19900)

    def test_an_empty_journal_is_refused(self):
        with self.assertRaises(InputError):
            metrics.execution([])
        with self.assertRaises(InputError):
            metrics.execution([{"type": "phase", "data": {}}])


class HostPowerTests(unittest.TestCase):
    def journal(self) -> Journal:
        journal = Journal().dispatch("a", 0).run("a", 0, 100)
        return journal.add(10000, "session", status="idle")

    def test_suspended_time_is_separated_from_an_awake_idle_flow(self):
        origin = ORIGIN.timestamp()
        result = metrics.execution(self.journal().events, sleeps=[(origin + 1000, origin + 4000)])
        self.assertEqual(result["host_asleep_seconds"], 3000)
        self.assertEqual(result["awake_nobody_running_seconds"], 10000 - 100 - 3000)

    def test_sleep_while_an_agent_was_recorded_running_is_not_subtracted_twice(self):
        origin = ORIGIN.timestamp()
        result = metrics.execution(self.journal().events, sleeps=[(origin + 50, origin + 500)])
        self.assertEqual(result["host_asleep_seconds"], 400)

    def test_sleep_outside_the_execution_is_ignored(self):
        origin = ORIGIN.timestamp()
        result = metrics.execution(self.journal().events, sleeps=[(origin - 9000, origin - 100)])
        self.assertEqual(result["host_asleep_seconds"], 0)

    def test_unknown_power_history_is_not_reported_as_zero(self):
        result = metrics.execution(self.journal().events)
        self.assertIsNone(result["host_asleep_seconds"])
        self.assertIsNone(result["awake_nobody_running_seconds"])
        self.assertNotIn("máquina dormindo", metrics.render(result))

    def test_a_failing_power_query_is_unknown_not_empty(self):
        with mock.patch.object(metrics.sys, "platform", "win32"), \
                mock.patch.object(metrics.subprocess, "run", side_effect=OSError("no shell")):
            self.assertIsNone(metrics.host_sleep(0, 10))
        failed = mock.Mock(returncode=1, stdout="", stderr="boom")
        with mock.patch.object(metrics.sys, "platform", "win32"), \
                mock.patch.object(metrics.subprocess, "run", return_value=failed):
            self.assertIsNone(metrics.host_sleep(0, 10))

    def test_a_single_recorded_suspension_is_read_as_a_list(self):
        done = mock.Mock(returncode=0, stdout='{"sleep":100,"wake":200}')
        with mock.patch.object(metrics.sys, "platform", "win32"), \
                mock.patch.object(metrics.subprocess, "run", return_value=done):
            self.assertEqual(metrics.host_sleep(50, 500), [(100.0, 200.0)])

    def test_other_platforms_report_unknown(self):
        with mock.patch.object(metrics.sys, "platform", "linux"):
            self.assertIsNone(metrics.host_sleep(0, 10))


class ExecutorJournal:
    """Build an executor journal with exact timings."""

    def __init__(self) -> None:
        self.events: list[dict] = []

    def add(self, moment: float, event: str, **fields) -> "ExecutorJournal":
        self.events.append({"seq": len(self.events) + 1, "at": at(moment), "event": event, **fields})
        return self

    def issue(self, seconds: float, task: str, agent: str, *, stage: str = "authors", kind: str = "author",
              attempt: int = 1, cycle: int = 1, round: int = 0):
        return self.add(seconds, "task_issued", task_id=task, agent=agent, stage=stage, kind=kind, attempt=attempt,
                        cycle=cycle, round=round)

    def record(self, seconds: float, task: str, agent: str, *, attempt: int = 1, outcome: str = "accepted"):
        return self.add(seconds, "task_recorded", task_id=task, agent=agent, attempt=attempt, outcome=outcome)

    def script(self, seconds: float, name: str, took: float, cycle: int = 1):
        return self.add(seconds, "script_finished", script=name, seconds=took, cycle=cycle, round=0, exit_code=0)


def sample_run() -> ExecutorJournal:
    journal = ExecutorJournal().add(0, "run_started")
    journal.issue(0, "t1", "author-01").issue(0, "t2", "author-02")
    journal.record(100, "t1", "author-01").record(120, "t2", "author-02")
    journal.script(130, "sources", 10).script(130, "tables", 4)
    journal.issue(200, "r1", "reviewer-01", stage="reviewers", kind="reviewer").record(300, "r1", "reviewer-01")
    journal.add(310, "gate_run", cycle=1, exit_code=0, seconds=5)
    journal.add(400, "run_finished", outcome="approved", cycle=1)
    return journal


class ExecutorTests(unittest.TestCase):
    def measure(self, journal: ExecutorJournal, **kwargs):
        events, mechanical = metrics.executor_events(journal.events)
        return metrics.execution(events, execution_id="executor", mechanical=mechanical, **kwargs)

    def test_agent_code_and_idle_time_add_up_to_the_clock(self):
        result = self.measure(sample_run())
        self.assertEqual(result["wall_seconds"], 400)
        self.assertEqual(result["agent_running_union_seconds"], 220)
        self.assertEqual(result["mechanical_running_union_seconds"], 15, "sources and tables overlapped; code counts once")
        self.assertEqual(result["idle_seconds"], 165)
        self.assertEqual(result["idle_share"], 0.412)
        self.assertEqual(result["parallelism"], round(320 / 220, 2))
        self.assertEqual(result["dispatches"], 3)

    def test_a_retry_is_a_second_dispatch_of_the_same_task(self):
        journal = ExecutorJournal().add(0, "run_started")
        journal.issue(0, "t1", "author-01").record(50, "t1", "author-01", outcome="null")
        journal.issue(60, "t1", "author-01", attempt=2).record(160, "t1", "author-01", attempt=2)
        result = self.measure(journal)
        self.assertEqual((result["dispatches"], result["agent_running_union_seconds"]), (2, 150))
        self.assertEqual(result["dispatches_without_an_end"], [])

    def test_work_issued_and_never_recorded_is_listed_without_an_end(self):
        journal = ExecutorJournal().add(0, "run_started")
        journal.issue(10, "t1", "author-01").script(500, "sources", 3)
        result = self.measure(journal)
        [lost] = result["dispatches_without_an_end"]
        self.assertEqual(lost["agent"], "author-01")
        self.assertEqual(result["agent_running_union_seconds"], 0, "an agent nobody saw stop is not credited with hours")

    def test_repairs_and_withdrawn_verdicts_are_recoveries(self):
        journal = ExecutorJournal().add(0, "run_started").add(5, "repair_started", cycle=1, round=1)
        journal.add(9, "verdict_withdrawn", cycle=1)
        self.assertEqual(self.measure(journal)["recoveries"], ["repair", "verdict_withdrawn"])

    def test_every_stage_and_script_is_a_phase_with_its_own_idle_time(self):
        result = self.measure(sample_run())
        self.assertEqual(set(result["phase_totals"]),
                         {"setup", "authors", "sources", "tables", "reviews", "gate"})
        for row in result["phase_totals"].values():
            self.assertIn("idle_seconds", row)
            self.assertLessEqual(row["idle_seconds"], row["seconds"])
        self.assertEqual(result["phase_totals"]["reviews"]["idle_seconds"], 5 + 0, "the quiet before the gate belongs to reviews")

    def test_any_other_journal_entry_still_extends_the_clock(self):
        journal = ExecutorJournal().add(0, "run_started").add(900, "matrix_written", cycle=1)
        self.assertEqual(self.measure(journal)["wall_seconds"], 900)

    def test_an_entry_without_a_valid_time_is_ignored_not_fatal(self):
        journal = sample_run()
        journal.events.append({"seq": 99, "at": "not a time", "event": "task_issued", "task_id": "x"})
        journal.events.append({"seq": 100, "at": "not a time", "event": "script_finished", "script": "sources", "seconds": 3})
        journal.events.append({"seq": 101, "event": "gate_run", "seconds": 2})
        self.assertEqual(self.measure(journal)["dispatches"], 3)

    def test_a_machine_that_slept_is_told_apart_from_a_flow_that_was_idle(self):
        origin = ORIGIN.timestamp()
        result = self.measure(sample_run(), sleeps=[(origin + 310, origin + 390)])
        self.assertEqual(result["host_asleep_seconds"], 80)
        self.assertEqual(result["awake_idle_seconds"], 85)

    def test_sleeping_while_code_was_running_is_not_counted_as_sleeping_through_an_idle_flow(self):
        origin = ORIGIN.timestamp()
        result = self.measure(sample_run(), sleeps=[(origin + 125, origin + 135)])
        self.assertEqual(result["host_asleep_seconds"], 5, "only 130 to 135 was idle; 125 to 130 was the checks running")

    def test_without_mechanical_work_given_the_legacy_figures_are_unchanged(self):
        journal = Journal().add(0, "phase", phase="authors", cycle=1)
        journal.dispatch("a", 0).run("a", 10, 110).add(1000, "phase", phase="reviews", cycle=1)
        result = metrics.execution(journal.events)
        for key in ("idle_seconds", "idle_share", "mechanical_running_union_seconds", "awake_idle_seconds"):
            self.assertNotIn(key, result)
        self.assertNotIn("idle_seconds", result["phase_totals"]["authors"])

    def test_no_journal_means_nothing_to_measure(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(metrics.executor(Path(folder)), [])


class RealRunMetricsTests(EngineCase):
    def test_a_real_run_is_measured_with_agents_code_and_idle_time(self):
        self.finish()
        [result] = metrics.executor(self.root)
        self.assertEqual(result["dispatches"], 7)
        self.assertGreater(result["mechanical_running_union_seconds"], 0, "the checks and the gate ran as code")
        self.assertLessEqual(result["idle_seconds"], result["wall_seconds"])
        self.assertEqual(result["recoveries"], [])
        self.assertTrue({"authors", "reviews", "rubber-duck", "gate", "delivery"} <= set(result["phase_totals"]))
        self.assertEqual(result["dispatches_without_an_end"], [])

    def test_the_command_prints_the_executor_figures_and_selects_by_id(self):
        self.finish()
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["metrics", str(self.root)])
        self.assertEqual(code, 0, err.getvalue())
        for text in ("Agente ou código", "Ocioso", "do journal do executor", "Por fase (relógio, ocioso)"):
            self.assertIn(text, out.getvalue())
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(cli.main(["metrics", str(self.root), "--json", "--execution", "executor"]), 0)
        [result] = json.loads(out.getvalue())
        self.assertEqual(result["execution_id"], "executor")


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.swarm = Path(self.temporary.name) / "swarm"
        folder = self.swarm / "reports" / "progress" / "aaaa1111-exec"
        folder.mkdir(parents=True)
        journal = Journal().add(0, "phase", phase="authors", cycle=1)
        journal.dispatch("a", 0).run("a", 10, 110).add(2000, "session", status="idle")
        (folder / "events.jsonl").write_text("\n".join(json.dumps(item) for item in journal.events), encoding="utf-8")

    def invoke(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(args))
        return code, out.getvalue(), err.getvalue()

    def test_the_command_prints_the_decomposition(self):
        code, out, _ = self.invoke("metrics", str(self.swarm))
        self.assertEqual(code, 0)
        self.assertIn("Ninguém rodando", out)
        self.assertIn("limite superior", out)

    def test_json_output_is_machine_readable(self):
        code, out, _ = self.invoke("metrics", str(self.swarm), "--json")
        self.assertEqual(code, 0)
        [result] = json.loads(out)
        self.assertEqual(result["agent_running_union_seconds"], 100)

    def test_an_unknown_execution_is_an_error(self):
        code, _, err = self.invoke("metrics", str(self.swarm), "--execution", "zzzz")
        self.assertEqual(code, 2)
        self.assertIn("no execution starts with", err)

    def test_a_swarm_without_a_journal_is_an_error(self):
        empty = Path(self.temporary.name) / "empty"
        empty.mkdir()
        code, _, err = self.invoke("metrics", str(empty))
        self.assertEqual(code, 2)
        self.assertIn("no monitor journal", err)

    def test_a_corrupted_journal_is_reported_not_skipped(self):
        journal = next(self.swarm.rglob("events.jsonl"))
        journal.write_text('{"at": "2026-01-01T00:00:00Z"}\n{ not json', encoding="utf-8")
        code, _, err = self.invoke("metrics", str(self.swarm))
        self.assertEqual(code, 2)
        self.assertIn("not valid JSON", err)


if __name__ == "__main__":
    unittest.main()
