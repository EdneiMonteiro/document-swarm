from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts.checks import health
from scripts.orchestration import driver
from scripts.orchestration.engine import Engine
from tests.test_orchestration_engine import EngineCase


def stamp(seconds_ago: float) -> str:
    return datetime.fromtimestamp(time.time() - seconds_ago, tz=timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class ExecutorHealthTests(EngineCase):
    def start(self) -> Engine:
        engine = self.engine()
        engine.init()
        engine.next()
        return engine

    def beat(self, *, age: float = 5, state: str = "running", running: int = 2, detail: str = "") -> Path:
        path = self.root / "reports" / "execution" / driver.HEARTBEAT.rsplit("/", 1)[1]
        path.write_text(json.dumps({
            "schema_version": 1, "pid": 1234, "backend": "copilot-cli", "parallel": 4, "state": state, "stage": "authors",
            "cycle": 1, "detail": detail, "updated_at": stamp(age), "started_at": stamp(age + 60),
            "running": [{"task": f"c01.r0.authors.a{index}.a1", "agent": f"author-0{index}", "since": stamp(age + 30),
                         "model": "m"} for index in range(1, running + 1)]}), encoding="utf-8")
        return path

    def age_the_journal(self, seconds: float) -> None:
        path = self.root / "reports" / "execution" / "journal.jsonl"
        lines = []
        for line in path.read_text(encoding="utf-8").splitlines():
            item = json.loads(line)
            item["at"] = stamp(seconds)
            lines.append(json.dumps(item, sort_keys=True))
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def test_a_swarm_run_by_the_coordinator_keeps_its_classification(self):
        record = health.compose(self.root)
        self.assertIsNone(record["executor"])
        self.assertNotIn("Retomar", health.render(record))

    def test_a_driver_that_beats_is_active_and_its_agents_are_named(self):
        self.start()
        self.beat(age=5, running=2)
        record = health.compose(self.root)
        self.assertEqual(record["state"], "active")
        self.assertIn("2 agente(s) em execução", record["reason"])
        table = health.render(record)
        self.assertIn("author-01 · author-02", table)
        # The beat is stamped in whole seconds and rendered a moment later, so the age is a little over the 5 s asked for.
        beat_age = re.search(r"driver running, batimento há (\d+) s", table)
        self.assertIsNotNone(beat_age, table)
        self.assertTrue(5 <= int(beat_age.group(1)) <= 15, f"the table states the age of the beat: {beat_age.group(0)}")
        self.assertNotIn("Retomar", table, "a live run is not invited to be resumed: that would start a second one")
        self.assertIn("nenhuma: o executor está em andamento", table)

    def test_a_stalled_driver_is_told_how_to_resume_and_a_live_one_is_not(self):
        self.start()
        self.beat(age=300, state="running")
        table = health.render(health.compose(self.root))
        self.assertIn(f'Retomar           python "{health.ORCHESTRATION}" run "{self.root.resolve()}"', table)
        self.beat(age=2, state="running")
        self.assertNotIn("Retomar", health.render(health.compose(self.root)))

    def test_a_heartbeat_dated_in_the_future_is_not_trusted_but_a_little_skew_is(self):
        self.start()
        self.beat(age=-1800, state="running")
        record = health.compose(self.root)
        self.assertEqual(record["state"], "stalled", "age zero would make a dead driver look alive")
        self.assertIn("não é confiável", record["reason"])
        self.assertIsNone(record["executor"]["driver"]["age_seconds"])
        self.beat(age=-2, state="running")
        self.assertEqual(health.compose(self.root)["state"], "active", "a couple of seconds ahead is clock skew")

    def test_a_run_continued_after_its_verdict_is_not_closed(self):
        # The ceiling was raised after an escalation and the swarm was run again: the journal goes on past
        # run_finished, and the artifacts of the escalated cycle still say "escalated".
        from tests.test_orchestration_engine import Scripted, build_swarm
        root = build_swarm(Path(self.temporary.name) / "again", max_cycles=1)
        Engine(root).run(Scripted(root, self.base, grades={(1, "reviewer-01-facts", "T02"): "B+"}))
        self.assertEqual(health.compose(root)["state"], "closed")
        engine = Engine(root)
        engine.journal.append("task_issued", task_id="c02.r0.authors.author-01-platform", stage="authors", kind="author",
                              agent="author-01-platform", cycle=2, round=0, attempt=1)
        beat = root / "reports" / "execution" / driver.HEARTBEAT.rsplit("/", 1)[1]
        beat.write_text(json.dumps({"schema_version": 1, "pid": 1, "backend": "x", "state": "running", "stage": "authors",
                                    "cycle": 2, "detail": "", "updated_at": stamp(3), "running": []}), encoding="utf-8")
        record = health.compose(root)
        self.assertEqual(record["state"], "active", record["reason"])
        self.assertFalse(record["executor"]["finished"])

    def test_the_executor_decides_before_the_artifacts_of_a_cycle_that_was_already_judged(self):
        executor = {"finished": False, "outcome": None, "last_event": "task_issued", "last_event_age_seconds": 3.0,
                    "pending_agents": [], "driver": {"state": "running", "age_seconds": 3.0, "running": []}}
        for resume in ({"complete": True, "blocked": []}, {"complete": False, "blocked": [{"kind": "escalation", "detail": "x"}]}):
            with self.subTest(resume=resume):
                self.assertEqual(health.classify(resume, None, None, 180, "", executor)[0], "active")

    def test_an_event_with_a_unicode_line_separator_does_not_hide_the_events_around_it(self):
        # str.splitlines() breaks on U+2028 as well as on newlines, and the journal writes such text unescaped.
        engine = self.start()
        task = engine.next()["tasks"][0]
        engine.journal.append("task_recorded", task_id=task["task_id"], attempt=task["attempt"], agent=task["agent"],
                              outcome="rejected", errors=["topic T09\u2028x is not in the brief"])
        raw = (self.root / "reports" / "execution" / "journal.jsonl").read_text(encoding="utf-8")
        self.assertIn("\u2028", raw, "the journal really holds the separator unescaped")
        self.assertNotIn(task["agent"], health.compose(self.root)["executor"]["pending_agents"],
                         "the recorded attempt is still seen, so the agent is not reported as pending")
        from scripts.orchestration import metrics
        self.assertTrue(metrics.read_journal(self.root / "reports" / "execution" / "journal.jsonl"))

    def test_without_a_driver_the_table_says_there_is_no_coordinator_session_to_observe(self):
        self.start()
        table = health.render(health.compose(self.root))
        self.assertIn("não aplicável: execução pelo executor determinístico, sem coordenador", table)
        self.assertNotIn("extensão do monitor ausente", table)

    def test_a_driver_that_stopped_beating_is_stalled_and_the_same_command_resumes(self):
        self.start()
        self.beat(age=300, state="running")
        record = health.compose(self.root)
        self.assertEqual(record["state"], "stalled")
        self.assertIn("não dá sinal", record["reason"])
        self.assertIn("o mesmo comando retoma", record["reason"])

    def test_a_driver_that_reported_it_needs_a_person_is_stalled_with_the_reason(self):
        self.start()
        self.beat(age=2, state="blocked", detail="um agente não produziu resultado válido")
        record = health.compose(self.root)
        self.assertEqual(record["state"], "stalled")
        self.assertIn("precisa de uma pessoa: um agente não produziu resultado válido", record["reason"])
        self.beat(age=2, state="failed", detail="o script quebrou")
        self.assertIn("o script quebrou", health.compose(self.root)["reason"])

    def test_an_interrupted_driver_says_how_to_continue(self):
        self.start()
        self.beat(age=2, state="interrupted")
        record = health.compose(self.root)
        self.assertEqual(record["state"], "stalled")
        self.assertIn("foi interrompido", record["reason"])

    def test_a_finished_run_is_closed_whatever_the_heartbeat_says(self):
        self.finish()
        self.beat(age=900, state="running")
        record = health.compose(self.root)
        self.assertEqual(record["state"], "closed")
        self.assertNotIn("Retomar", health.render(record))

    def test_the_journal_closes_a_run_even_when_an_artifact_of_the_delivery_was_lost(self):
        self.finish()
        (self.root / "reports" / "memory-proposal.json").unlink()
        self.beat(age=900, state="running")
        record = health.compose(self.root)
        self.assertFalse(record["complete"], "the artifacts alone no longer call it complete")
        self.assertEqual(record["state"], "closed")
        self.assertIn("o executor encerrou a execução: approved", record["reason"])

    def test_an_escalated_run_is_closed_because_a_person_decides(self):
        from tests.test_orchestration_engine import Scripted, build_swarm
        root = build_swarm(Path(self.temporary.name) / "esc", max_cycles=1)
        Engine(root).run(Scripted(root, self.base, grades={(1, "reviewer-01-facts", "T02"): "B+"}))
        self.assertEqual(health.compose(root)["state"], "closed")

    def test_without_a_heartbeat_the_journal_decides(self):
        self.start()
        recent = health.compose(self.root)
        self.assertEqual(recent["state"], "waiting")
        self.assertIn("sem batimento do driver", recent["reason"])
        self.age_the_journal(900)
        old = health.compose(self.root)
        self.assertEqual(old["state"], "stalled")
        self.assertIn("nenhum executor deu sinal", old["reason"])

    def test_issued_work_that_was_never_recorded_is_listed_as_pending(self):
        engine = self.start()
        record = health.compose(self.root)
        self.assertEqual(record["executor"]["pending_agents"], ["author-01-platform", "author-02-operations"])
        self.assertIn("author-01-platform · author-02-operations", health.render(record))
        task = engine.next()["tasks"][0]
        engine.record(task["task_id"], task["attempt"], task["inputs_sha256"], self.agent().author(task))
        self.assertEqual(health.compose(self.root)["executor"]["pending_agents"], ["author-02-operations"],
                         "an agent whose result was recorded is no longer pending")

    def test_one_identitys_answer_does_not_hide_the_pending_work_of_another(self):
        engine = self.start()
        task = engine.next()["tasks"][0]
        other = dict(task_id=task["task_id"], attempt=task["attempt"], agent=task["agent"], outcome="accepted")
        engine.journal.append("task_recorded", inputs_sha256="f" * 64, **other)
        self.assertIn(task["agent"], health.compose(self.root)["executor"]["pending_agents"],
                      "the record answers inputs that nobody issued; the issued ones are still out")
        engine.journal.append("task_recorded", inputs_sha256=task["inputs_sha256"], **other)
        self.assertNotIn(task["agent"], health.compose(self.root)["executor"]["pending_agents"])

    def test_an_answer_from_before_the_journal_carried_the_identity_answers_whatever_was_issued(self):
        engine = self.start()
        task = engine.next()["tasks"][0]
        engine.journal.append("task_recorded", task_id=task["task_id"], attempt=task["attempt"], agent=task["agent"],
                              outcome="accepted")
        self.assertNotIn(task["agent"], health.compose(self.root)["executor"]["pending_agents"])
        issued_before = dict(task_id="c01.r0.authors.legacy", attempt=1, agent="legacy", cycle=1, round=0, stage="authors", kind="author")
        engine.journal.append("task_issued", **issued_before)
        self.assertIn("legacy", health.compose(self.root)["executor"]["pending_agents"])
        engine.journal.append("task_recorded", inputs_sha256="e" * 64, outcome="accepted",
                              **{key: issued_before[key] for key in ("task_id", "attempt", "agent")})
        self.assertNotIn("legacy", health.compose(self.root)["executor"]["pending_agents"],
                         "an issue with no identity is answered by any record of its task and attempt")

    def test_a_corrupt_heartbeat_is_ignored_not_trusted_and_not_fatal(self):
        self.start()
        (self.root / "reports" / "execution" / "driver.json").write_text("{not json", encoding="utf-8")
        record = health.compose(self.root)
        self.assertIsNone(record["executor"]["driver"])
        self.assertEqual(record["state"], "waiting")
        (self.root / "reports" / "execution" / "driver.json").write_text('{"schema_version": 2}', encoding="utf-8")
        self.assertIsNone(health.compose(self.root)["executor"]["driver"], "an unknown schema is not read")

    def test_a_torn_last_journal_line_does_not_hide_the_state(self):
        self.start()
        with open(self.root / "reports" / "execution" / "journal.jsonl", "a", encoding="utf-8") as stream:
            stream.write('{"seq": 99, "event": "task_rec')
        record = health.compose(self.root)
        self.assertEqual(record["state"], "waiting")
        self.assertEqual(record["executor"]["last_event"], "task_issued")

    def test_the_heartbeat_file_is_not_mistaken_for_work_done(self):
        self.start()
        self.beat(age=1)
        record = health.compose(self.root)
        self.assertNotEqual(record["artifacts"]["newest"], "reports/execution/driver.json")

    def test_the_json_record_carries_what_the_table_prints(self):
        self.start()
        self.beat(age=3)
        record = json.loads(json.dumps(health.compose(self.root)))
        self.assertEqual(record["executor"]["driver"]["state"], "running")
        self.assertEqual(record["swarm_path"], str(self.root.resolve()))

    def test_exit_status_flags_a_stalled_executor_for_scripts(self):
        self.start()
        self.beat(age=300)
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(health.main([str(self.root)]), 1)
        self.beat(age=3)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(health.main([str(self.root)]), 0)


if __name__ == "__main__":
    import unittest

    unittest.main()
