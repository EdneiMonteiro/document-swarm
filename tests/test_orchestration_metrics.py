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
