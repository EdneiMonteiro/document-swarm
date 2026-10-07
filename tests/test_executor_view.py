from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from scripts.checks import executor_view, health
from scripts.checks.common import InputError
from tests.test_orchestration_engine import EngineCase, Scripted

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = datetime(2026, 10, 7, 3, 0, 0, tzinfo=timezone.utc)
A, B = "a" * 64, "b" * 64


def stamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"


def tid(cycle: int, stage: str, agent: str, round_number: int = 0) -> str:
    return f"c{cycle:02d}.r{round_number}.{stage}.{agent}"


class Journal:
    """A journal with exact times, written the way the executor writes one."""

    def __init__(self, origin: datetime = ORIGIN) -> None:
        self.origin = origin
        self.entries: list[dict] = []

    def add(self, offset: float, event: str, **fields) -> "Journal":
        self.entries.append({"seq": len(self.entries) + 1, "at": stamp(self.origin + timedelta(seconds=offset)),
                             "event": event, **fields})
        return self

    def issue(self, seconds, agent, stage="authors", *, cycle=1, attempt=1, round_number=0, identity=A):
        fields = {"inputs_sha256": identity} if identity else {}
        return self.add(seconds, "task_issued", task_id=tid(cycle, stage, agent, round_number), stage=stage,
                        kind=stage, agent=agent, cycle=cycle, round=round_number, attempt=attempt, **fields)

    def start(self, seconds, agent, stage="authors", *, cycle=1, attempt=1, round_number=0, identity=None):
        fields = {"inputs_sha256": identity} if identity else {}
        return self.add(seconds, "task_started", task_id=tid(cycle, stage, agent, round_number), attempt=attempt,
                        agent=agent, **fields)

    def record(self, seconds, agent, stage="authors", *, cycle=1, attempt=1, round_number=0, identity=None,
               outcome="accepted", errors=None, runtime=None, took=60.0):
        fields = {"inputs_sha256": identity} if identity else {}
        return self.add(seconds, "task_recorded", task_id=tid(cycle, stage, agent, round_number), stage=stage,
                        kind=stage, agent=agent, cycle=cycle, round=round_number, attempt=attempt, outcome=outcome,
                        errors=errors or [], seconds=took, runtime=runtime or {}, **fields)

    def write(self, root: Path) -> Path:
        path = root / "reports" / "execution" / "journal.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(item, ensure_ascii=False) + "\n" for item in self.entries), encoding="utf-8")
        return path


class ProjectionCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "swarm"
        self.root.mkdir()

    def project(self, journal: Journal | None = None, **kwargs):
        if journal is not None:
            journal.write(self.root)
        return executor_view.project(self.root, **kwargs)

    def of(self, result, kind: str) -> list[dict]:
        return [event for event in result["events"] if event["type"] == kind]


class DispatchTests(ProjectionCase):
    def test_a_swarm_without_an_executor_journal_has_nothing_to_project(self):
        self.assertEqual(executor_view.project(self.root), {"schema_version": 1, "present": False})

    def test_an_issue_a_start_and_a_record_are_one_dispatch_with_its_states_in_order(self):
        journal = Journal().add(0, "run_started", plan_sha256="p", agents=2)
        journal.issue(1, "author-01").start(2, "author-01").record(62, "author-01", took=541, runtime={"models_seen": "gpt-5.5,x", "seconds": 58.44})
        result = self.project(journal)
        [dispatch] = self.of(result, "dispatch")
        self.assertEqual(dispatch["data"]["id"], "x2", "named by the journal entry that issued it, so it never changes")
        self.assertEqual((dispatch["data"]["agent_id"], dispatch["data"]["cycle"], dispatch["data"]["attempt"],
                          dispatch["data"]["stage"], dispatch["data"]["label"]),
                         ("author-01", 1, 1, "authors", "c01.r0.authors.author-01.a1"))
        running, done = self.of(result, "runtime")
        self.assertEqual((running["data"]["dispatch_id"], running["data"]["status"]), ("x2", "running"))
        self.assertEqual((done["data"]["status"], done["data"]["outcome"], done["data"]["seconds"], done["data"]["observed_model"]),
                         ("completed", "accepted", 58.4, "gpt-5.5"))
        self.assertLess(running["at"], done["at"])
        self.assertEqual((running["data"]["started_at"], done["data"]["ended_at"]), (running["at"], done["at"]),
                         "the times a panel measures a duration with are the journal's own")
        self.assertEqual(result["summary"]["counts"], {"issued": 1, "accepted": 1, "rejected": 0, "null": 0, "repairs": 0})
        self.assertEqual(result["summary"]["running"], [])

    def test_a_refused_attempt_fails_with_the_reason_and_the_next_attempt_is_another_dispatch(self):
        journal = Journal().add(0, "run_started")
        journal.issue(1, "author-01").start(2, "author-01")
        journal.record(30, "author-01", outcome="rejected", errors=["topic T09 is not in the brief", "second"])
        journal.issue(31, "author-01", attempt=2).start(32, "author-01", attempt=2)
        journal.record(60, "author-01", attempt=2, outcome="null", errors=["the agent returned no result"],
                       runtime={"error": "timeout", "stderr": "SEGREDO que nao deve sair"})
        result = self.project(journal)
        first, second = self.of(result, "dispatch")
        self.assertEqual((first["data"]["attempt"], second["data"]["attempt"]), (1, 2))
        ends = [event["data"] for event in self.of(result, "runtime") if event["data"]["status"] in ("completed", "failed")]
        self.assertEqual([(item["status"], item["outcome"]) for item in ends], [("failed", "rejected"), ("failed", "null")])
        self.assertEqual(ends[0]["error"], "topic T09 is not in the brief", "the first reason, not every one")
        self.assertEqual((ends[1]["error"], ends[1]["cause"]), ("the agent returned no result", "timeout"))
        self.assertNotIn("SEGREDO", json.dumps(result), "what the CLI wrote to stderr never leaves the executor's record")
        self.assertEqual(result["summary"]["counts"]["rejected"], 1)
        self.assertEqual(result["summary"]["counts"]["null"], 1)

    def test_what_the_process_measured_is_shown_only_when_it_is_a_duration(self):
        journal = Journal().add(0, "run_started")
        cases = ((12.34, 12.3), (0, 0.0), (10**7 - 1, 9999999.0), (-5, None), (10**7, None), (True, None), ("12", None), ([1], None))
        for index, (seconds, _) in enumerate(cases):
            agent = f"author-{index:02d}"
            journal.issue(10 * index + 1, agent).start(10 * index + 2, agent).record(10 * index + 8, agent, took=541, runtime={"seconds": seconds})
        ends = [event["data"].get("seconds") for event in self.of(self.project(journal), "runtime") if event["data"]["status"] == "completed"]
        self.assertEqual(ends, [expected for _, expected in cases])

    def test_a_refusal_that_has_a_cause_and_no_reason_shows_the_cause_once(self):
        journal = Journal().add(0, "run_started").issue(1, "author-01").start(2, "author-01")
        journal.record(30, "author-01", outcome="null", runtime={"error": "timeout"})
        [end] = [event["data"] for event in self.of(self.project(journal), "runtime") if event["data"]["status"] == "failed"]
        self.assertEqual((end["error"], "cause" in end), ("timeout", False))

    def test_work_issued_and_never_recorded_is_listed_as_queued_or_running(self):
        journal = Journal().add(0, "run_started")
        journal.issue(1, "author-01").issue(1, "author-02").start(2, "author-01")
        result = self.project(journal)
        self.assertEqual([(row["agent"], row["state"], row["stage"], row["cycle"]) for row in result["summary"]["running"]],
                         [("author-02", "queued", "authors", 1), ("author-01", "running", "authors", 1)])

    def test_a_start_after_a_stop_is_the_same_dispatch_started_again(self):
        # The run stopped with the agent running and the same command ran it again hours later.
        journal = Journal().add(0, "run_started").issue(1, "author-01").start(2, "author-01")
        journal.start(14400, "author-01").record(14500, "author-01", took=100)
        result = self.project(journal)
        self.assertEqual(len(self.of(result, "dispatch")), 1)
        starts = [event["data"]["started_at"] for event in self.of(result, "runtime") if event["data"]["status"] == "running"]
        self.assertEqual(len(starts), 2)
        self.assertLess(starts[0], starts[1], "the last start is the run that finished")

    def test_the_same_task_asked_again_for_other_inputs_pairs_each_record_with_its_own_ask(self):
        journal = Journal().add(0, "run_started")
        journal.issue(1, "rubber-duck", "rubber-duck", identity=A).start(2, "rubber-duck", "rubber-duck", identity=A)
        journal.record(60, "rubber-duck", "rubber-duck", identity=A)
        journal.issue(500, "rubber-duck", "rubber-duck", identity=B).start(501, "rubber-duck", "rubber-duck", identity=B)
        journal.record(590, "rubber-duck", "rubber-duck", identity=B, outcome="rejected", errors=["x"])
        result = self.project(journal)
        first, second = self.of(result, "dispatch")
        self.assertNotEqual(first["data"]["id"], second["data"]["id"])
        ends = {event["data"]["dispatch_id"]: event["data"]["status"] for event in self.of(result, "runtime")
                if event["data"]["status"] in ("completed", "failed")}
        self.assertEqual(ends, {first["data"]["id"]: "completed", second["data"]["id"]: "failed"})

    def test_an_ask_that_is_replaced_by_one_for_other_inputs_before_it_ended_is_cancelled(self):
        journal = Journal().add(0, "run_started")
        journal.issue(1, "author-01", identity=A).start(2, "author-01", identity=A)
        journal.issue(900, "author-01", identity=B).start(901, "author-01", identity=B).record(960, "author-01", identity=B)
        result = self.project(journal)
        first, second = self.of(result, "dispatch")
        cancelled = [event for event in self.of(result, "runtime") if event["data"]["status"] == "cancelled"]
        self.assertEqual([(event["data"]["dispatch_id"], event["data"]["outcome"]) for event in cancelled],
                         [(first["data"]["id"], "superseded")])
        self.assertEqual(result["summary"]["running"], [], "the replaced ask is not still running")
        self.assertEqual(result["summary"]["counts"]["accepted"], 1)

    def test_a_journal_from_before_start_and_record_carried_the_identity_still_pairs_in_order(self):
        journal = Journal().add(0, "run_started")
        journal.issue(1, "coordinator", "narrative", identity=A).start(2, "coordinator", "narrative").record(40, "coordinator", "narrative")
        journal.issue(300, "coordinator", "narrative", identity=B).start(301, "coordinator", "narrative")
        journal.record(380, "coordinator", "narrative")
        result = self.project(journal)
        ends = [event["data"] for event in self.of(result, "runtime") if event["data"]["status"] == "completed"]
        self.assertEqual(len(ends), 2)
        self.assertEqual(len({item["dispatch_id"] for item in ends}), 2, "each record ends the ask it follows")
        self.assertEqual(len(self.of(result, "dispatch")), 2, "a start and a record that carry no identity belong to the ask before them")
        self.assertFalse([event for event in self.of(result, "runtime") if event["data"]["status"] == "cancelled"])
        self.assertEqual(result["summary"]["running"], [])

    def test_a_start_or_a_record_for_other_inputs_than_the_open_ask_is_a_new_ask_and_replaces_it(self):
        journal = Journal().add(0, "run_started")
        journal.issue(1, "author-01", identity=A).start(2, "author-01", identity=B).record(60, "author-01", identity=B)
        result = self.project(journal)
        first, second = self.of(result, "dispatch")
        self.assertEqual((first["data"]["identity"], second["data"]["identity"]), (A[:12], B[:12]))
        states = [(event["data"]["dispatch_id"], event["data"]["status"]) for event in self.of(result, "runtime")]
        self.assertEqual(states, [(first["data"]["id"], "cancelled"), (second["data"]["id"], "running"), (second["data"]["id"], "completed")])
        self.assertEqual(result["summary"]["running"], [])

    def test_an_ask_that_carried_no_identity_pairs_with_a_start_and_a_record_that_do(self):
        journal = Journal().add(0, "run_started")
        journal.issue(1, "author-01", identity=None).start(2, "author-01", identity=A).record(60, "author-01", identity=A)
        result = self.project(journal)
        self.assertEqual(len(self.of(result, "dispatch")), 1)
        self.assertEqual([event["data"]["status"] for event in self.of(result, "runtime")], ["running", "completed"])

    def test_a_start_or_a_record_with_no_issue_becomes_a_dispatch_of_its_own(self):
        # An executor that did not journal an issue for a task asked again left records like these.
        journal = Journal().add(0, "run_started")
        journal.start(5, "author-01").record(60, "author-01")
        journal.record(90, "author-02", cycle=2, outcome="rejected", errors=["x"])
        result = self.project(journal)
        dispatches = self.of(result, "dispatch")
        self.assertEqual([(item["data"]["agent_id"], item["data"]["cycle"], item["data"]["stage"]) for item in dispatches],
                         [("author-01", 1, "authors"), ("author-02", 2, "authors")])
        self.assertEqual(result["summary"]["counts"], {"issued": 2, "accepted": 1, "rejected": 1, "null": 0, "repairs": 0})
        self.assertEqual(result["summary"]["running"], [])

    def test_a_task_whose_id_cannot_be_read_is_ignored_rather_than_guessed(self):
        journal = Journal().add(0, "run_started")
        journal.add(5, "task_started", task_id="not a task id", attempt=1, agent="author-01")
        journal.add(6, "task_recorded", task_id="c00.r0.authors.author-01", attempt=1, agent="author-01", outcome="accepted")
        journal.add(7, "task_recorded", task_id=tid(1, "authors", "x"), attempt=0, agent="x", outcome="accepted")
        self.assertEqual(self.of(self.project(journal), "dispatch"), [])
        # The longest ids the engine can write are read; one character more is not an id of this executor.
        edge = tid(1, "a" * 40, "x" * 100)
        journal.add(8, "task_recorded", task_id=edge, attempt=1, agent="x", outcome="accepted")
        self.assertEqual([item["data"]["stage"] for item in self.of(self.project(journal), "dispatch")], ["a" * 40])
        for too_long in (tid(1, "a" * 41, "x"), tid(1, "authors", "x" * 101)):
            journal.add(9, "task_recorded", task_id=too_long, attempt=1, agent="x", outcome="accepted")
        self.assertEqual(len(self.of(self.project(journal), "dispatch")), 1)


class FlowTests(ProjectionCase):
    def cycle(self, journal: Journal, cycle: int, start: float, *, authors=("author-01", "author-02"),
              reviewers=("reviewer-01", "reviewer-02")) -> float:
        moment = start
        for agent in authors:
            journal.issue(moment, agent, "authors", cycle=cycle).start(moment + 1, agent, "authors", cycle=cycle)
        for agent in authors:
            moment += 60
            journal.record(moment, agent, "authors", cycle=cycle)
        journal.issue(moment + 1, "coordinator", "consolidation", cycle=cycle).start(moment + 2, "coordinator", "consolidation", cycle=cycle)
        journal.record(moment + 30, "coordinator", "consolidation", cycle=cycle)
        journal.add(moment + 35, "script_finished", script="sources", exit_code=0, seconds=3.0, cycle=cycle, round=0)
        journal.add(moment + 35, "script_finished", script="tables", exit_code=0, seconds=1.0, cycle=cycle, round=0)
        journal.add(moment + 35, "script_finished", script="nomenclature", exit_code=0, seconds=1.0, cycle=cycle, round=0)
        for agent in reviewers:
            journal.issue(moment + 40, agent, "reviewers", cycle=cycle).start(moment + 41, agent, "reviewers", cycle=cycle)
            journal.record(moment + 90, agent, "reviewers", cycle=cycle)
        journal.add(moment + 95, "matrix_written", cycle=cycle, duck_recorded=False)
        journal.issue(moment + 96, "rubber-duck", "rubber-duck", cycle=cycle).start(moment + 97, "rubber-duck", "rubber-duck", cycle=cycle)
        journal.record(moment + 150, "rubber-duck", "rubber-duck", cycle=cycle)
        journal.add(moment + 151, "matrix_written", cycle=cycle, duck_recorded=True)
        journal.add(moment + 152, "gate_run", cycle=cycle, exit_code=1, seconds=0.4)
        return moment + 200

    def test_phases_follow_the_stages_and_the_scripts_and_never_repeat_or_go_back(self):
        journal = Journal().add(0, "run_started")
        end = self.cycle(journal, 1, 10)
        self.cycle(journal, 2, end)
        phases = [(event["data"]["cycle"], event["data"]["phase"]) for event in self.of(self.project(journal), "phase")]
        one = [(1, name) for name in ("authors", "consolidation", "sources", "tables", "reviews", "rubber-duck", "gate")]
        self.assertEqual(phases, [(0, "setup")] + one + [(2, name) for _, name in one])

    def test_the_artifacts_that_pass_between_roles_are_handoffs_once_each(self):
        journal = Journal().add(0, "run_started")
        self.cycle(journal, 1, 10)
        edges = [(e["data"]["from"], e["data"]["to"], e["data"]["label"], e["data"]["cycle"])
                 for e in self.of(self.project(journal), "handoff")]
        self.assertEqual(edges, [
            ("author-01", "coordinator", "seções", 1), ("author-02", "coordinator", "seções", 1),
            ("coordinator", "reviewer-01", "documento consolidado", 1), ("coordinator", "reviewer-02", "documento consolidado", 1),
            ("reviewer-01", "rubber-duck", "avaliações", 1), ("reviewer-02", "rubber-duck", "avaliações", 1)])
        self.assertEqual(len(edges), len(set(edges)))

    def test_what_the_reviewers_found_goes_back_to_the_authors_of_the_next_cycle_only(self):
        journal = Journal().add(0, "run_started")
        end = self.cycle(journal, 1, 10)
        journal.issue(end, "author-02", "authors", cycle=2)
        journal.issue(end + 1, "author-02", "authors", cycle=2, attempt=2)
        journal.issue(end + 2, "author-01", "authors", cycle=2, round_number=1)
        back = [(e["data"]["from"], e["data"]["to"], e["data"]["label"]) for e in self.of(self.project(journal), "handoff")
                if e["data"]["cycle"] == 2]
        self.assertEqual(back, [("reviewer-01", "author-02", "achados do ciclo 1"), ("reviewer-02", "author-02", "achados do ciclo 1")],
                         "only the author asked again, once; one asked only in a repair round of the cycle was given no findings")

    def test_the_audit_and_the_narrative_hand_over_to_the_coordinator(self):
        journal = Journal().add(0, "run_started")
        end = self.cycle(journal, 1, 10)
        journal.issue(end, "coordinator", "narrative", cycle=1)
        last = [e["data"] for e in self.of(self.project(journal), "handoff")][-1]
        self.assertEqual((last["from"], last["to"], last["label"]), ("rubber-duck", "coordinator", "auditoria e veredito"))

    def test_only_what_was_accepted_is_handed_over(self):
        journal = Journal().add(0, "run_started")
        journal.issue(1, "author-01").start(2, "author-01").record(30, "author-01")
        journal.issue(1, "author-02").start(2, "author-02").record(31, "author-02", outcome="rejected", errors=["x"])
        journal.issue(40, "coordinator", "consolidation")
        edges = [(e["data"]["from"], e["data"]["to"]) for e in self.of(self.project(journal), "handoff")]
        self.assertEqual(edges, [("author-01", "coordinator")])

    def test_an_agent_that_holds_two_roles_does_not_hand_over_to_itself(self):
        journal = Journal().add(0, "run_started")
        journal.issue(1, "same", "authors").start(2, "same", "authors").record(30, "same", "authors")
        journal.issue(31, "same", "consolidation").start(32, "same", "consolidation")
        self.assertEqual(self.of(self.project(journal), "handoff"), [])

    def test_the_other_things_the_executor_did_are_notes_for_the_history(self):
        journal = Journal().add(0, "run_started").add(1, "plan_refreshed", plan_sha256="p", agents=1)
        journal.add(2, "max_cycles_changed", previous=3, current=5, brief=3)
        journal.add(3, "approval_grade_changed", previous="A", current="A-")
        journal.add(4, "plan_recovered", reason="x", approval_grade="A")
        journal.add(5, "script_finished", script="sources", exit_code=1, seconds=2.5, cycle=1, round=0)
        journal.add(6, "script_finished", script="tables", exit_code=7, seconds=0.1, cycle=1, round=0)
        journal.add(6, "script_finished", script="nomenclature", exit_code=0, seconds=-1, cycle=1, round=0)
        journal.add(6, "script_finished", script="nomenclature", exit_code=0, seconds=True, cycle=1, round=0)
        journal.add(7, "repair_started", cycle=1, round=1, items=2)
        journal.add(7, "repair_started", cycle=1, round=2, items=0)
        journal.add(8, "gate_run", cycle=1, exit_code=2, seconds=0.2)
        journal.add(9, "verdict_withdrawn", cycle=1, recorded="approved", review_sha256="r")
        journal.add(10, "delivery_step", step="sources", ok=False, exit_code=1, seconds=3.0)
        journal.add(11, "delivery_step", step="report", ok=True, exit_code=0, seconds=0.5)
        journal.add(12, "task_stale", task_id=tid(1, "authors", "author-01"), attempt=1)
        journal.add(13, "matrix_written", cycle=1, duck_recorded=True)
        labels = [e["data"]["label"] for e in self.of(self.project(journal), "executor")]
        self.assertEqual(labels, [
            "Executor iniciado", "Plano atualizado", "Teto de ciclos alterado: 3 → 5", "Nota de aprovação alterada: A → A-",
            "Plano recomeçado por quem declarou a política (A)",
            "Verificação de fontes: com achados (2 s)", "Verificação de tabelas: falhou (saída 7) (0 s)",
            "Verificação de nomenclatura: sem achados", "Verificação de nomenclatura: sem achados",
            "Rodada de reparo 1: 2 pendência(s) mecânica(s)", "Rodada de reparo 2: 0 pendência(s) mecânica(s)",
            "Gate executado: escalar: teto de ciclos atingido",
            "Veredito retirado (approved): não se reproduz a partir das notas dos revisores",
            "Entrega: rechecagem final das fontes falhou", "Entrega: relatório final concluída",
            f"Resultado de {tid(1, 'authors', 'author-01')} recusado: a tarefa mudou desde a emissão",
            "Matriz da rodada gravada com a auditoria"])
        phases = [(e["data"]["cycle"], e["data"]["phase"]) for e in self.of(self.project(journal), "phase")]
        self.assertEqual(phases, [(0, "setup"), (1, "sources"), (1, "tables"), (1, "gate"), (1, "delivery")],
                         "the delivery steps belong to the cycle the run had reached")

    def test_the_end_of_the_run_is_announced_only_when_nothing_follows_it(self):
        for outcome, status in (("approved", "completed"), ("escalated", "escalated")):
            with self.subTest(outcome=outcome):
                journal = Journal().add(0, "run_started").add(5, "run_finished", outcome=outcome, cycle=2, warnings=0)
                [finish] = self.of(self.project(journal), "finish")
                self.assertEqual((finish["data"], finish["cycle"], finish["seq"]), ({"status": status}, 2, 2))
        journal = Journal().add(0, "run_started").add(5, "run_finished", outcome="escalated", cycle=1, warnings=0)
        journal.add(60, "plan_refreshed", plan_sha256="p", agents=1)
        self.assertEqual(self.of(self.project(journal), "finish"), [], "the ceiling was raised and the run goes on")
        journal = Journal().add(0, "run_started").add(5, "run_finished", outcome="something", cycle=1, warnings=0)
        self.assertEqual(self.of(self.project(journal), "finish"), [])

    def test_a_cycle_never_moves_backwards(self):
        journal = Journal().add(0, "run_started")
        journal.issue(1, "author-01", cycle=3)
        journal.add(2, "gate_run", cycle=1, exit_code=1, seconds=0.1)
        journal.add(3, "script_finished", script="sources", exit_code=0, seconds=1, cycle=2, round=0)
        phases = [(event["data"]["cycle"], event["data"]["phase"]) for event in self.of(self.project(journal), "phase")]
        self.assertEqual(phases, [(0, "setup"), (3, "authors")])


class CursorTests(ProjectionCase):
    def journal(self, calls: int = 6) -> Journal:
        journal = Journal().add(0, "run_started")
        for index in range(calls):
            agent = f"author-{index:02d}"
            journal.issue(10 * index + 1, agent).start(10 * index + 2, agent).record(10 * index + 8, agent)
        return journal

    def test_the_events_after_a_cursor_are_exactly_the_rest(self):
        journal = self.journal()
        full = self.project(journal)
        for after in (0, 1, 5, 9, full["cursor"] - 1, full["cursor"]):
            with self.subTest(after=after):
                rest = self.project(after=after)
                self.assertEqual(rest["events"], [event for event in full["events"] if event["seq"] > after])
                self.assertEqual((rest["cursor"], rest["epoch"], rest["reset"], rest["more"]),
                                 (full["cursor"], full["epoch"], False, False))

    def test_a_cursor_that_does_not_belong_to_this_journal_is_refused_not_applied(self):
        full = self.project(self.journal())
        wrong_epoch = self.project(after=3, epoch="2000-01-01T00:00:00.000Z")
        self.assertEqual((wrong_epoch["reset"], wrong_epoch["events"], wrong_epoch["cursor"]), (True, [], 3),
                         "a refused call hands back the cursor it was given: nothing was consumed")
        ahead = self.project(after=full["cursor"] + 1, epoch=full["epoch"])
        self.assertEqual((ahead["reset"], ahead["events"], ahead["cursor"]), (True, [], full["cursor"] + 1),
                         "a journal that got shorter is not the one that was read")
        same = self.project(after=3, epoch=full["epoch"])
        self.assertFalse(same["reset"])

    def test_a_long_journal_is_handed_over_in_pieces_that_never_split_an_entry(self):
        journal = self.journal(calls=12)
        journal.add(500, "script_finished", script="sources", exit_code=0, seconds=1, cycle=1, round=0)
        journal.add(501, "run_finished", outcome="approved", cycle=1, warnings=0)
        full = self.project(journal)
        for limit in (1, 2, 3, 7, 1000):
            with self.subTest(limit=limit):
                collected, after, calls = [], 0, 0
                while True:
                    piece = self.project(after=after, limit=limit, epoch=full["epoch"])
                    collected += piece["events"]
                    calls += 1
                    self.assertLess(calls, 200)
                    if not piece["more"]:
                        break
                    self.assertGreater(piece["cursor"], after, "every call moves the cursor")
                    after = piece["cursor"]
                self.assertEqual(collected, full["events"])
                self.assertEqual(piece["cursor"], full["cursor"])

    def test_the_epoch_is_the_first_time_of_the_journal_and_does_not_change_as_it_grows(self):
        journal = self.journal(calls=3)
        before = self.project(journal)
        journal.add(5000, "plan_refreshed", plan_sha256="p", agents=1)
        after = self.project(journal)
        self.assertEqual((before["epoch"], after["epoch"]), (stamp(ORIGIN), stamp(ORIGIN)))
        self.assertTrue(self.project(after=before["cursor"], epoch=before["epoch"])["events"], "the grown journal still belongs to the cursor")

    def test_pieces_are_cut_at_the_limit_and_the_last_one_says_nothing_is_left(self):
        journal = Journal()
        for index in range(10):
            journal.add(index, "plan_refreshed", plan_sha256="p", agents=1)
        journal.add(20, "mystery")
        full = self.project(journal)
        self.assertEqual(len(full["events"]), 10)
        pieces, after = [], 0
        for _ in range(10):
            piece = self.project(after=after, limit=3, epoch=full["epoch"])
            pieces.append((len(piece["events"]), piece["more"], piece["cursor"]))
            if not piece["more"]:
                break
            after = piece["cursor"]
        self.assertEqual(pieces, [(3, True, 3), (3, True, 6), (3, True, 9), (1, False, 11)])

    def test_a_piece_that_swallows_the_rest_of_the_events_says_so_and_goes_past_the_silent_entries(self):
        journal = Journal().add(0, "run_started").add(1, "mystery")
        piece = self.project(journal, limit=1)
        self.assertEqual((len(piece["events"]), piece["more"], piece["cursor"]), (2, False, 2),
                         "run_started makes two events; neither is left behind, and the cursor does not stop at the entry that made them")

    def test_a_piece_ends_between_journal_entries_not_in_the_middle_of_one(self):
        journal = Journal().add(0, "run_started").issue(1, "author-01").issue(1, "author-02")
        journal.start(2, "author-01").record(9, "author-01")
        piece = self.project(journal, limit=1)
        seqs = {event["seq"] for event in piece["events"]}
        full = self.project()
        for seq in seqs:
            self.assertEqual([e for e in piece["events"] if e["seq"] == seq], [e for e in full["events"] if e["seq"] == seq],
                             "an entry that makes several events is handed over whole")

    def test_the_same_journal_always_gives_the_same_events(self):
        journal = self.journal()
        self.assertEqual(self.project(journal), self.project())

    def test_sequence_numbers_that_repeat_or_go_back_still_give_a_cursor_that_loses_and_repeats_nothing(self):
        journal = self.journal(calls=4)
        for index in (3, 5, 6):
            journal.entries[index]["seq"] = 2
        full = self.project(journal)
        seqs = [event["seq"] for event in full["events"]]
        self.assertEqual(seqs, sorted(seqs), "the order of the events is the order of the journal")
        self.assertEqual(len({(event["seq"], event["n"]) for event in full["events"]}), len(full["events"]), "no two events share a name")
        ids = [event["data"]["id"] for event in full["events"] if event["type"] == "dispatch"]
        self.assertEqual(len(set(ids)), len(ids), "no two dispatches share an id")
        collected, after = [], 0
        for _ in range(100):
            piece = self.project(after=after, limit=2, epoch=full["epoch"])
            collected += piece["events"]
            if not piece["more"]:
                break
            after = piece["cursor"]
        self.assertEqual(collected, full["events"])

    def test_projecting_modifies_nothing(self):
        journal = self.journal()
        journal.write(self.root)
        (self.root / "reports" / "execution" / "driver.json").write_text("{}", encoding="utf-8")

        def tree():
            return {str(path.relative_to(self.root)): path.read_bytes() for path in sorted(self.root.rglob("*")) if path.is_file()}

        before = tree()
        executor_view.project(self.root, after=2)
        self.assertEqual(before, tree())


class HostileJournalTests(ProjectionCase):
    def test_unreadable_lines_are_skipped_and_counted_and_hide_nothing_around_them(self):
        journal = Journal().add(0, "run_started").issue(1, "author-01", identity=A).start(2, "author-01", identity=A)
        path = journal.write(self.root)
        lines = path.read_text(encoding="utf-8").splitlines()
        lines.insert(1, '{"seq": 99, "event": "task_rec')
        lines.append("[1, 2]")
        lines.append('{"seq": 7, "event": "gate_run", "at": "2026-10-07T03:01:00.000Z", "cycle": 1, "exit_code": 0, "note": "a\u2028b"}')
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        result = executor_view.project(self.root)
        self.assertEqual(result["skipped_lines"], 2)
        [gate] = [e for e in self.of(result, "executor") if "Gate executado: aprovado" in e["data"]["label"]]
        self.assertEqual(gate["seq"], 7, "an entry is named by the sequence number the executor wrote, not by its place in the file")
        self.assertEqual(len(self.of(result, "dispatch")), 1)
        self.assertEqual(len(self.of(result, "runtime")), 1)
        self.assertTrue(any("Gate executado: aprovado" in e["data"]["label"] for e in self.of(result, "executor")),
                        "an entry whose text holds a Unicode line separator is still one entry")

    def test_fields_that_are_not_what_the_executor_writes_never_reach_the_events(self):
        journal = Journal().add(0, "run_started")
        journal.add(1, "task_issued", task_id="c01.r0.authors.x", stage="authors", agent="../etc/passwd", cycle=1, round=0, attempt=1)
        journal.add(2, "task_issued", task_id="c01.r0.authors.x", stage="a b", agent="x", cycle=1, round=0, attempt=1)
        journal.add(3, "task_issued", task_id="c01.r0.authors.x", stage="authors", agent="x" * 200, cycle=1, round=0, attempt=1)
        journal.add(4, "task_issued", task_id="c01.r0.authors.x", stage="authors", agent="x", cycle=True, round=0, attempt=1)
        journal.add(5, "task_issued", task_id="c01.r0.authors.x", stage="authors", agent="x", cycle=1, round=0, attempt=-3)
        journal.add(6, "task_issued", task_id=["c01"], stage="authors", agent="x", cycle=1, round=0, attempt=1)
        journal.add(7, "__class__", cycle=1)
        journal.add(7, "task_issued", task_id="not a task id", stage="authors", agent="x", cycle=1, round=0, attempt=1)
        journal.add(7, "task_issued", task_id="t" * 201, stage="authors", agent="x", cycle=1, round=0, attempt=1)
        journal.add(8, "task_issued", task_id="c01.r0.authors.x", stage="authors", agent="x", cycle=1, round=0, attempt=1,
                    prompt_sha256="p", inputs_sha256=A)
        journal.entries[-1]["prompt"] = "TEXTO DO PROMPT"
        result = self.project(journal)
        [dispatch] = self.of(result, "dispatch")
        self.assertEqual(dispatch["data"]["agent_id"], "x")
        self.assertNotIn("TEXTO DO PROMPT", json.dumps(result))
        self.assertNotIn(A, json.dumps(result), "only a short prefix of the identity is shown")
        self.assertEqual(dispatch["data"]["identity"], A[:12])

    def test_a_field_that_is_a_list_or_an_object_where_the_executor_writes_a_word_does_not_stop_the_projection(self):
        journal = Journal().add(0, "run_started")
        journal.add(1, "script_finished", script=["sources"], exit_code={"a": 1}, cycle=1, round=0, seconds=[1])
        journal.add(2, "script_finished", script={"a": 1}, exit_code=[0], cycle=[1], round=0, seconds={"x": 1})
        journal.add(3, "script_finished", script="sources", exit_code=0, round=0)
        journal.add(4, "gate_run", cycle=1, exit_code=[0], seconds={"x": 1})
        journal.add(5, "gate_run", cycle=1, exit_code=True)
        journal.add(6, "gate_run", exit_code=0)
        journal.add(7, "delivery_step", step=["report"], ok=[True])
        journal.add(8, "delivery_step", step={"a": 1}, ok="yes")
        journal.add(9, "task_issued", task_id="c01.r0.authors.x", stage=["authors"], agent={"a": 1}, cycle=[1], round=[0], attempt=[1])
        journal.add(10, "task_started", task_id=["c01"], attempt={"a": 1}, inputs_sha256=["x"])
        journal.add(11, "task_recorded", task_id={"a": 1}, attempt=[1], outcome=["accepted"], runtime=[1], errors={"a": 1})
        journal.add(12, "plan_recovered", approval_grade=["A"])
        journal.add(13, "max_cycles_changed", previous=[1], current={"a": 2})
        journal.add(14, "repair_started", round=[1], items=[2])
        journal.add(15, "verdict_withdrawn", recorded=[1])
        journal.add(16, "task_stale", task_id={"a": 1})
        journal.add(17, "matrix_written", duck_recorded=["yes"])
        hostile = tid(1, "authors", "hostile")
        journal.add(18, "task_issued", task_id=hostile, stage="authors", agent="hostile", cycle=1, round=0, attempt=1, inputs_sha256=["x"])
        journal.add(19, "task_recorded", task_id=hostile, attempt=1, outcome=["accepted"], runtime=[1], errors={"a": 1},
                    inputs_sha256=["x"], seconds=[1])
        journal.issue(20, "author-01").start(21, "author-01")
        secret = tid(1, "authors", "secretive")
        journal.add(22, "task_issued", task_id=secret, stage="authors", agent="secretive", cycle=1, round=0, attempt=1)
        journal.add(23, "task_recorded", task_id=secret, attempt=1, outcome="rejected", errors=[{"detail": "SEGREDO1"}],
                    runtime={"error": {"detail": "SEGREDO2"}, "models_seen": ["SEGREDO3"], "seconds": {"x": "SEGREDO4"}})
        result = self.project(journal)
        self.assertNotIn("SEGREDO", json.dumps(result), "an object or a list where a word was expected is dropped, not printed")
        odd, real, _ = self.of(result, "dispatch")
        self.assertEqual((odd["data"]["agent_id"], odd["data"]["identity"], real["data"]["agent_id"]), ("hostile", None, "author-01"),
                         "the entries that can be read are projected around the ones that cannot")
        [ended] = [e["data"] for e in self.of(result, "runtime") if e["data"]["dispatch_id"] == odd["data"]["id"]]
        self.assertEqual((ended["status"], ended["outcome"], "error" in ended, "seconds" in ended), ("failed", "rejected", False, False))
        self.assertEqual(result["summary"]["counts"], {"issued": 3, "accepted": 0, "rejected": 2, "null": 0, "repairs": 1})
        self.assertEqual([item["agent"] for item in result["summary"]["running"]], ["author-01"])
        notes = {kind: [e["data"]["label"] for e in self.of(result, "executor") if e["data"]["kind"] == kind]
                 for kind in ("gate", "delivery", "script")}
        self.assertEqual(sorted(notes["gate"]), ["Gate executado: aprovado", "Gate executado: saída ", "Gate executado: saída "],
                         "a list or a bool is not the exit code 0; only the entry that holds a 0 says so")
        self.assertFalse(any("concluída" in text for text in notes["delivery"]), "[True] and 'yes' are not the truth")
        self.assertEqual(len(notes["script"]), 3)
        phases = self.of(result, "phase")
        self.assertTrue(phases and all(type(e["data"]["cycle"]) is int for e in phases), "an entry with no cycle moves no phase")
        journal.add(22, "run_finished", outcome=["approved"], cycle=[3])
        ended = self.project(journal)
        self.assertEqual(self.of(ended, "finish"), [], "an outcome that is not a word is not an ending the panel can show")

    def test_a_line_the_parser_refuses_for_a_reason_other_than_syntax_is_skipped_like_a_torn_one(self):
        journal = Journal().add(0, "run_started").issue(1, "author-01").start(2, "author-01")
        path = journal.write(self.root)
        lines = path.read_text(encoding="utf-8").splitlines()
        lines.insert(1, "1" * 5000)
        lines.insert(3, "[" * 100000)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        result = executor_view.project(self.root)
        self.assertEqual(result["skipped_lines"], 2)
        self.assertEqual(len(self.of(result, "dispatch")), 1, "what is around them is still read")
        self.assertEqual(health.executor_state(self.root)["pending_agents"], ["author-01"], "and so is it by the health command")

    def test_a_time_that_is_not_a_time_is_replaced_by_the_last_good_one(self):
        journal = Journal().add(0, "run_started")
        journal.entries.append({"seq": 2, "at": "ontem", "event": "plan_refreshed"})
        journal.entries.append({"seq": 3, "event": "plan_refreshed"})
        result = self.project(journal)
        self.assertEqual({event["at"] for event in result["events"]}, {stamp(ORIGIN)})

    def test_long_reasons_are_cut_and_whitespace_is_collapsed(self):
        journal = Journal().add(0, "run_started").issue(1, "author-01").start(2, "author-01")
        journal.record(3, "author-01", outcome="rejected", errors=["linha um\n\n   linha dois " + "x" * 1000])
        [end] = [e for e in self.of(self.project(journal), "runtime") if e["data"]["status"] == "failed"]
        self.assertTrue(end["data"]["error"].startswith("linha um linha dois x"))
        self.assertLessEqual(len(end["data"]["error"]), 280)

    def test_a_journal_that_links_outside_the_swarm_is_refused(self):
        outside = Path(self.temporary.name) / "outside.jsonl"
        outside.write_text("", encoding="utf-8")
        link = self.root / "reports" / "execution" / "journal.jsonl"
        link.parent.mkdir(parents=True)
        try:
            link.symlink_to(outside)
        except OSError:
            self.skipTest("symbolic links are unavailable")
        with self.assertRaisesRegex(InputError, "escapes the swarm"):
            executor_view.project(self.root)

    def test_a_journal_larger_than_the_supported_size_is_refused(self):
        Journal().add(0, "run_started").write(self.root)
        with mock.patch.object(health, "MAX_JOURNAL_BYTES", 10), self.assertRaisesRegex(InputError, "supported size"):
            executor_view.project(self.root)

    def test_an_empty_journal_is_present_with_nothing_to_apply(self):
        (self.root / "reports" / "execution").mkdir(parents=True)
        (self.root / "reports" / "execution" / "journal.jsonl").write_text("", encoding="utf-8")
        result = executor_view.project(self.root)
        self.assertEqual((result["present"], result["events"], result["cursor"], result["epoch"]), (True, [], 0, ""))


class HealthTests(EngineCase):
    """The comparison with the health command needs a whole swarm, which the engine's test case builds."""

    def project(self, journal: Journal | None = None, **kwargs):
        if journal is not None:
            journal.write(self.root)
        return executor_view.project(self.root, **kwargs)

    def beat(self, state="running", age=5.0, running=None):
        path = self.root / "reports" / "execution" / "driver.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "schema_version": 1, "pid": 4242, "backend": "copilot-cli", "parallel": 3, "state": state, "stage": "authors",
            "cycle": 1, "detail": "", "updated_at": stamp(datetime.now(timezone.utc) - timedelta(seconds=age)),
            "running": running or []}), encoding="utf-8")

    def journal(self, age: float) -> Journal:
        return Journal(datetime.now(timezone.utc) - timedelta(seconds=age)).add(0, "run_started").issue(1, "author-01")

    def test_the_health_is_the_one_the_health_command_reports(self):
        cases = (("running", 5, "active"), ("running", 300, "stalled"), ("interrupted", 5, "stalled"),
                 ("blocked", 5, "stalled"), ("done", 5, "closed"))
        for state, age, expected in cases:
            with self.subTest(state=state, age=age):
                journal = self.journal(400)
                journal.write(self.root)
                self.beat(state, age)
                result = executor_view.project(self.root)
                self.assertEqual(result["health"]["state"], expected)
                self.assertEqual(result["health"]["state"], health.compose(self.root)["state"])
                self.assertEqual(result["health"]["reason"], health.compose(self.root)["reason"])

    def test_without_a_heartbeat_the_journal_decides(self):
        self.assertEqual(self.project(self.journal(5))["health"]["state"], "waiting")
        self.assertEqual(self.project(self.journal(900))["health"]["state"], "stalled")

    def test_a_finished_run_is_closed_and_says_how_it_ended(self):
        journal = self.journal(5).add(6, "run_finished", outcome="escalated", cycle=1, warnings=0)
        result = self.project(journal)
        self.assertEqual((result["summary"]["finished"], result["summary"]["outcome"], result["health"]["state"]),
                         (True, "escalated", "closed"))

    def test_the_driver_is_reported_without_its_running_list_or_anything_else_it_wrote(self):
        self.journal(5).write(self.root)
        self.beat("running", 5, running=[{"task": "t", "agent": "a", "since": "x", "model": "m"}])
        path = self.root / "reports" / "execution" / "driver.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["last"] = "SEGREDO"
        path.write_text(json.dumps(data), encoding="utf-8")
        driver = executor_view.project(self.root)["summary"]["driver"]
        self.assertEqual(sorted(driver), ["age_seconds", "backend", "cycle", "detail", "pid", "stage", "state"])
        self.assertEqual((driver["state"], driver["backend"], driver["pid"]), ("running", "copilot-cli", 4242))


class CommandLineTests(ProjectionCase):
    def run_cli(self, *args: str, env: dict | None = None):
        done = subprocess.run([sys.executable, "-S", str(Path(executor_view.__file__)), *args], capture_output=True,
                              env={**os.environ, **(env or {})}, timeout=60)
        return done.returncode, done.stdout, done.stderr.decode("utf-8", errors="replace")

    def test_the_answer_is_ascii_json_even_with_a_legacy_output_encoding(self):
        journal = Journal().add(0, "run_started").issue(1, "author-01")
        journal.add(2, "verdict_withdrawn", cycle=1, recorded="approved", review_sha256="r")
        journal.write(self.root)
        code, out, err = self.run_cli(str(self.root), env={"PYTHONIOENCODING": "cp1252"})
        self.assertEqual(code, 0, err)
        data = json.loads(out.decode("ascii"))
        self.assertIn("não se reproduz", json.dumps(data, ensure_ascii=False))
        code, out, _ = self.run_cli(str(self.root), "--after", "2", "--epoch", data["epoch"])
        self.assertEqual((code, json.loads(out)["events"][0]["seq"]), (0, 3))

    def test_a_swarm_without_a_journal_prints_that_it_has_none(self):
        code, out, _ = self.run_cli(str(self.root))
        self.assertEqual((code, json.loads(out)), (0, {"schema_version": 1, "present": False}))

    def test_bad_arguments_and_unreadable_input_are_refused_with_a_message(self):
        for args in (("--after", "-1"), ("--limit", "0"), ("--threshold", "5"), ("--threshold", "29")):
            with self.subTest(args=args):
                code, out, err = self.run_cli(str(self.root), *args)
                self.assertEqual((code, out), (2, b""))
                self.assertIn("ERROR", err)
        for args in (("--after", "0"), ("--limit", "1"), ("--threshold", "30")):
            with self.subTest(accepted=args):
                self.assertEqual(self.run_cli(str(self.root), *args)[0], 0, "the smallest value each option takes is accepted")
        code, out, err = self.run_cli(str(self.root / "missing"))
        self.assertNotEqual(code, 0)
        self.assertEqual(out, b"")
        journal = self.root / "reports" / "execution" / "journal.jsonl"
        journal.parent.mkdir(parents=True)
        journal.write_bytes(b'{"event": "run_started"}\n\xff\xfe\n')
        code, out, err = self.run_cli(str(self.root))
        self.assertEqual((code, out), (1, b""), "a journal that is not UTF-8 is an error with a message, not a traceback")
        self.assertIn("cannot project", err)
        self.assertNotIn("Traceback", err)


class ContractTests(unittest.TestCase):
    def test_the_phase_maps_agree_with_the_ones_the_executor_measures_with(self):
        from scripts.orchestration import metrics
        self.assertEqual(executor_view.PHASE_OF_STAGE, metrics.PHASE_OF_STAGE)
        self.assertEqual(executor_view.PHASE_OF_SCRIPT, metrics.PHASE_OF_SCRIPT)

    def test_every_phase_the_projection_announces_is_one_the_monitor_knows(self):
        phases = {"setup", "agents", "authors", "consolidation", "sources", "tables", "reviews", "rubber-duck", "gate",
                  "delivery", "done"}
        used = set(executor_view.PHASE_OF_STAGE.values()) | set(executor_view.PHASE_OF_SCRIPT.values()) | {"setup", "gate", "delivery"}
        self.assertLessEqual(used, phases)

    def test_the_journal_reader_the_health_command_uses_counts_what_it_skips(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "journal.jsonl"
            path.write_text('{"a": 1}\n\nnot json\n[1]\n{"b": 2}\n', encoding="utf-8")
            events, skipped = health.read_executor_journal(path)
        self.assertEqual((events, skipped), ([{"a": 1}, {"b": 2}], 2))

    def test_a_journal_field_of_any_type_is_fit_to_be_a_key_and_a_scalar_stays_what_it_was(self):
        from scripts.checks.common import scalar
        for value in (None, "texto", 0, 3, 2.5, True, False, ""):
            self.assertIs(scalar(value), value)
        for value in (["a"], {"a": [1]}, [[1], {"x": None}], ("t",), {1, 2}.__class__):
            hash(scalar(value))
        self.assertEqual(scalar(["a", 1]), '["a", 1]')
        self.assertLessEqual(len(scalar(["x" * 1000])), 200)
        self.assertEqual(scalar({"b": 1, "a": 2}), scalar({"a": 2, "b": 1}), "the same object is the same key")

    def test_a_value_nested_deeper_than_the_encoder_follows_is_still_a_key_not_an_exception(self):
        from scripts.checks.common import scalar
        deep: list = []
        for _ in range(5000):
            deep = [deep]
        key = scalar(deep)
        self.assertIsInstance(key, str)
        hash(key)
        self.assertIn("too deep", key)
        loop: list = []
        loop.append(loop)
        self.assertIn("too deep", scalar(loop), "a value that contains itself cannot be encoded either, and is still a key")


class RealRunTests(EngineCase):
    def test_a_real_run_is_projected_to_what_the_engine_did(self):
        agent = Scripted(self.root, self.base, grades={(1, "reviewer-01-facts", "T02"): "B+"})
        done = self.engine().run(agent)
        self.assertEqual(done["status"], "done")
        status = self.engine().status()
        result = executor_view.project(self.root)
        events = result["events"]
        dispatches = [event for event in events if event["type"] == "dispatch"]
        ends = [event["data"] for event in events if event["type"] == "runtime" and event["data"]["status"] in ("completed", "failed")]
        self.assertEqual(len(dispatches), status["tasks_issued"])
        self.assertEqual(len(ends), status["tasks_recorded"], "every record the engine made ends a dispatch the panel shows")
        self.assertEqual(sum(1 for item in ends if item["status"] == "completed"), status["accepted"])
        self.assertEqual({item["dispatch_id"] for item in ends}, {event["data"]["id"] for event in dispatches})
        self.assertEqual(result["summary"]["running"], [])
        self.assertEqual((result["summary"]["finished"], result["summary"]["outcome"]), (True, "approved"))
        self.assertEqual([event["type"] for event in events][-2:], ["executor", "finish"])
        declared = {path.stem for path in (self.root / "agents").rglob("*.md")}
        names = {event["data"]["agent_id"] for event in dispatches}
        self.assertLessEqual(names, declared | {"coordinator", "rubber-duck"})
        for edge in (event["data"] for event in events if event["type"] == "handoff"):
            self.assertIn(edge["from"], names)
            self.assertIn(edge["to"], names)
        cycles = [event["data"]["cycle"] for event in events if event["type"] == "phase"]
        self.assertEqual(cycles, sorted(cycles), "the cycle only moves forward")
        self.assertEqual(max(cycles), 2, "the B+ of the first cycle made a second one")

    def test_the_events_of_a_run_in_progress_are_a_prefix_of_the_events_of_the_whole_run(self):
        engine = self.engine()
        engine.init()
        agent = Scripted(self.root, self.base)
        first = engine.next()
        for task in first["tasks"]:
            engine.record(task["task_id"], task["attempt"], task["inputs_sha256"], agent(task))
        early = executor_view.project(self.root)
        self.engine().run(agent)
        late = executor_view.project(self.root)
        self.assertEqual(late["events"][:len(early["events"])], early["events"],
                         "what a panel applied earlier is never contradicted later")
        self.assertEqual(executor_view.project(self.root, after=early["cursor"], epoch=early["epoch"])["events"],
                         late["events"][len(early["events"]):])


if __name__ == "__main__":
    unittest.main()
