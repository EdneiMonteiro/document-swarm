from __future__ import annotations

import json
import os
import re
import tempfile
import time
import unittest
from pathlib import Path

from scripts.checks import final_report
from scripts.checks.common import InputError
from scripts.checks.health import classify, compose, render
from scripts.checks.resume import project, verify
from scripts.checks.resume import MONITOR_PHASES

REVIEWERS = ("reviewer-01-facts", "reviewer-02-clarity")
AUTHORS = ("author-01-platform", "author-02-operations")


class Swarm:
    """Build a synthetic swarm and stop it at any point of the cycle contract."""

    ORDER = ("agents", "authors", "consolidation", "editorial", "sources", "tables",
             "nomenclature", "reviews", "matrix", "rubberduck", "gate", "final", "memory")

    def __init__(self, root: Path, *, cycle: int = 1, max_cycles: int = 3,
                 skill_version: str = "3.1.0", deliverables: tuple[str, ...] = ()) -> None:
        self.root = root
        self.cycle = cycle
        self.max_cycles = max_cycles
        self.deliverables = deliverables
        self.tag = f"cycle-{cycle:02d}"
        for folder in ("agents", "reports", "output", "sources"):
            (root / folder).mkdir(parents=True, exist_ok=True)
        declared = "".join(f"  - {item}\n" for item in deliverables)
        declared = f"deliverables:\n{declared}" if declared else ""
        (root / "brief.md").write_text(
            f'---\nswarm_id: watchdog-fixture\nskill_version: "{skill_version}"\nmode: document\n'
            f"max_cycles: {max_cycles}\n{declared}---\n# Fixture do vigia\n", encoding="utf-8")

    def write(self, relative: str, text: str) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def agent(self, name: str, kind: str) -> None:
        self.write(f"agents/{name}.md",
                   f"---\nname: {name}\nkind: {kind}\nrole: Papel\nmodel: auto\n"
                   f"swarm: watchdog-fixture\n---\n# {name}\n")

    def upto(self, stage: str) -> Swarm:
        """Populate every artifact that precedes *stage* and stop there."""
        if stage not in self.ORDER:
            raise AssertionError(f"unknown stage {stage}")
        limit = self.ORDER.index(stage)

        def reached(name: str) -> bool:
            return self.ORDER.index(name) < limit

        if reached("agents"):
            for name in AUTHORS:
                self.agent(name, "author")
            for name in REVIEWERS:
                self.agent(name, "reviewer")
            self.agent("coordinator", "coordinator")
            self.agent("rubber-duck", "rubber-duck")
        if reached("authors"):
            self.write(f"reports/{self.tag}-authors.md", "# Rodada de autores\n")
        if reached("consolidation"):
            if self.deliverables:
                for name in self.deliverables:
                    self.write(name, f"entrega sintetica de {name}\n")
            else:
                self.write("output/documento.md", "# Documento\n\nConteúdo sintético.\n")
        if reached("editorial"):
            self.write(f"reports/{self.tag}-editorial-text.txt", "Documento\nConteudo sintetico.\n")
        if reached("sources"):
            self.write("sources/sources-check.json",
                       json.dumps({"counts": {"ok": 5, "redirect": 0, "warn": 0, "fail": 0}}))
        if reached("tables"):
            self.write(f"reports/{self.tag}-tables-check.json", json.dumps({"failures": 0}))
        if reached("nomenclature"):
            self.write(f"reports/{self.tag}-nomenclature.json", json.dumps({"candidates": []}))
        if reached("reviews"):
            for name in REVIEWERS:
                self.write(f"reports/{self.tag}-{name}.json", json.dumps({
                    "schema_version": 1, "cycle": self.cycle, "reviewer": name,
                    "topics": [{"topic": "T01", "grade": "A", "justification": "Fixture", "action": ""}]}))
        if reached("matrix"):
            self.review()
        if reached("rubberduck"):
            self.write(f"reports/{self.tag}-rubberduck.md", "# Auditoria\n")
        if reached("gate"):
            self.gate()
        if reached("final"):
            self.write("reports/final-report.md", "# Final report\n")
        if reached("memory"):
            self.write("reports/memory-proposal.json", json.dumps({"approved": True}))
        return self

    def review(self, *, grade: str = "A", max_cycles: int | None = None) -> Path:
        for name in REVIEWERS:
            path = self.root / "reports" / f"{self.tag}-{name}.json"
            if path.is_file():
                self.write(f"reports/{self.tag}-{name}.json", json.dumps({
                    "schema_version": 1, "cycle": self.cycle, "reviewer": name,
                    "topics": [{"topic": "T01", "grade": grade, "justification": "Fixture",
                                "action": "" if grade == "A" else "Corrigir o tópico."}]}))
        return self.write(f"reports/{self.tag}-review.yaml", json.dumps({
            "schema_version": 1, "skill_version": "3.1.0", "cycle": self.cycle,
            "max_cycles": max_cycles or self.max_cycles,
            "topics": [{"topico": "T01", "nota_minima": grade,
                        "revisor_da_minima": REVIEWERS[0], "bloqueia": False}],
            "rubberduck": {"critico": False, "achados": []}}))

    def gate(self) -> None:
        import contextlib
        import io

        from scripts.checks import gate

        review = self.root / "reports" / f"{self.tag}-review.yaml"
        output = self.root / "reports" / f"{self.tag}-gate.json"
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            gate.main([str(review), "--output", str(output)])


class ResumeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def swarm(self, stage: str, name: str | None = None, **kwargs) -> Swarm:
        target = self.root / (name or stage)
        target.mkdir()
        return Swarm(target, **kwargs).upto(stage)

    def test_each_stall_point_projects_its_own_next_step(self):
        expected = {
            "agents": ("agents", "create", "agents"),
            "authors": ("authors", "dispatch", AUTHORS[0]),
            "consolidation": ("consolidation", "compose", "output"),
            "editorial": ("sources", "run", "verify_sources.py"),
            "sources": ("sources", "run", "verify_sources.py"),
            "tables": ("tables", "run", "verify_tables.py"),
            "nomenclature": ("reviews", "run", "inspect_nomenclature.py"),
            "reviews": ("reviews", "dispatch", REVIEWERS[0]),
            "matrix": ("reviews", "compose", "review.yaml"),
            "rubberduck": ("rubber-duck", "dispatch", "rubber-duck"),
            "gate": ("gate", "run", "gate.py"),
            "final": ("delivery", "run", "final_report.py"),
            "memory": ("delivery", "run", "update_memory.py"),
        }
        for stage, (phase, action, target) in expected.items():
            with self.subTest(stage=stage):
                result = project(self.swarm(stage).root)
                self.assertEqual(result["phase"], phase)
                self.assertFalse(result["complete"])
                self.assertEqual(result["next"][0]["action"], action)
                self.assertEqual(result["next"][0]["target"], target)
                self.assertTrue(result["next"][0]["reason"])

    def test_a_complete_delivery_reports_no_pending_step(self):
        swarm = self.swarm("memory")
        swarm.write("reports/memory-proposal.json", json.dumps({"approved": True}))
        result = project(swarm.root)
        self.assertTrue(result["complete"])
        self.assertEqual(result["next"], [])
        self.assertEqual(result["phase"], "delivery")

    def test_every_pending_reviewer_is_listed_not_only_the_first(self):
        swarm = self.swarm("reviews")
        result = project(swarm.root)
        self.assertEqual([item["target"] for item in result["next"]], list(REVIEWERS))

    def test_a_rejected_cycle_moves_to_the_next_one_without_approving_anything(self):
        swarm = self.swarm("gate")
        swarm.review(grade="B+", max_cycles=5)
        swarm.gate()
        result = project(swarm.root)
        self.assertEqual(result["phase"], "authors")
        self.assertEqual(result["cycle"], swarm.cycle + 1)
        self.assertEqual({item["cycle"] for item in result["next"]}, {swarm.cycle + 1})
        self.assertFalse(result["complete"])

    def test_the_cycle_ceiling_blocks_instead_of_proposing_another_round(self):
        target = self.root / "ceiling"
        target.mkdir()
        swarm = Swarm(target, cycle=2, max_cycles=2).upto("gate")
        swarm.review(grade="B+", max_cycles=5)
        swarm.gate()
        result = project(swarm.root)
        self.assertEqual(result["next"], [])
        self.assertEqual([item["kind"] for item in result["blocked"]], ["max_cycles"])

    def test_an_escalated_gate_is_reported_and_never_resumed(self):
        target = self.root / "escalated"
        target.mkdir()
        swarm = Swarm(target, cycle=3, max_cycles=3).upto("gate")
        swarm.review(grade="B+")
        swarm.gate()
        result = project(swarm.root)
        self.assertEqual(result["next"], [])
        self.assertIn("escalation", [item["kind"] for item in result["blocked"]])

    def test_the_projection_is_refused_when_its_evidence_changed(self):
        swarm = self.swarm("reviews")
        result = project(swarm.root)
        self.assertEqual(verify(swarm.root, result), [])
        swarm.write(f"reports/{swarm.tag}-authors.md", "# Rodada alterada\n")
        self.assertIn(f"reports/{swarm.tag}-authors.md", verify(swarm.root, result))

    def test_an_artifact_that_should_be_absent_also_invalidates_the_projection(self):
        swarm = self.swarm("rubberduck")
        result = project(swarm.root)
        self.assertEqual(verify(swarm.root, result), [])
        swarm.write(f"reports/{swarm.tag}-rubberduck.md", "# Auditoria\n")
        self.assertIn(f"reports/{swarm.tag}-rubberduck.md", verify(swarm.root, result))

    def test_a_record_without_evidence_is_rejected(self):
        swarm = self.swarm("authors")
        with self.assertRaises(InputError):
            verify(swarm.root, {"schema_version": 1})

    def test_the_editorial_text_is_required_only_under_the_editorial_contract(self):
        legacy = self.swarm("editorial", name="legacy")
        self.assertEqual(project(legacy.root)["phase"], "sources")
        target = self.root / "editorial-contract"
        target.mkdir()
        modern = Swarm(target, skill_version="3.5.0").upto("editorial")
        result = project(modern.root)
        self.assertEqual(result["phase"], "consolidation")
        self.assertEqual(result["next"][0]["target"], "editorial-text")

    def test_a_legacy_unpadded_cycle_prefix_is_not_treated_as_missing_work(self):
        swarm = self.swarm("reviews", name="legacy-prefix")
        for path in sorted((swarm.root / "reports").glob("cycle-01-*")):
            path.rename(path.with_name(path.name.replace("cycle-01-", "cycle-1-", 1)))
        result = project(swarm.root)
        self.assertEqual(result["phase"], "reviews")
        self.assertEqual({item["target"] for item in result["next"]}, set(REVIEWERS))
        self.assertTrue(any(item["path"].startswith("reports/cycle-1-") for item in result["evidence"]["present"]))

    def test_a_presentation_swarm_with_no_markdown_is_not_asked_to_compose_forever(self):
        # Regression: the projection looked for output/*.md.  A presentation has none, so the
        # watchdog projected "compose" on every tick and a real run recomposed the deck three times.
        deck = ("output/presentation-cycle-01/index.html", "output/presentation-cycle-01/deck-editable.pptx")
        swarm = self.swarm("editorial", name="presentation", deliverables=deck)
        result = project(swarm.root)
        self.assertEqual(list(swarm.root.joinpath("output").rglob("*.md")), [])
        self.assertNotEqual(result["phase"], "consolidation")
        self.assertNotIn("output", {item["target"] for item in result["next"]})
        self.assertEqual(result["phase"], "sources")

    def test_a_missing_declared_deliverable_is_named_not_reported_generically(self):
        deck = ("output/presentation-cycle-01/index.html", "output/presentation-cycle-01/deck-editable.pptx")
        swarm = self.swarm("consolidation", name="missing", deliverables=deck)
        result = project(swarm.root)
        self.assertEqual(result["phase"], "consolidation")
        self.assertEqual({item["target"] for item in result["next"]}, set(deck))
        swarm.write(deck[0], "so uma das entregas\n")
        remaining = project(swarm.root)
        self.assertEqual({item["target"] for item in remaining["next"]}, {deck[1]})

    def test_the_declared_deliverables_are_hash_bound_evidence(self):
        deck = ("output/presentation-cycle-01/index.html",)
        swarm = self.swarm("editorial", name="bound", deliverables=deck)
        result = project(swarm.root)
        self.assertIn(deck[0], {item["path"] for item in result["evidence"]["present"]})
        swarm.write(deck[0], "a entrega mudou depois da projecao\n")
        self.assertIn(deck[0], verify(swarm.root, result))

    def test_a_deliverable_outside_the_swarm_is_refused(self):
        swarm = self.swarm("editorial", name="escape", deliverables=("../fora.pptx",))
        with self.assertRaises(InputError):
            project(swarm.root)

    def test_legacy_markdown_swarms_without_a_declared_delivery_still_work(self):
        swarm = self.swarm("editorial", name="legacy-doc")
        self.assertEqual(project(swarm.root)["phase"], "sources")
        bare = self.root / "no-document"
        bare.mkdir()
        empty = Swarm(bare).upto("consolidation")
        result = project(empty.root)
        self.assertEqual((result["phase"], result["next"][0]["target"]), ("consolidation", "output"))

    def test_the_projection_never_writes_into_the_swarm(self):
        swarm = self.swarm("reviews")
        before = {path: path.stat().st_mtime_ns for path in swarm.root.rglob("*") if path.is_file()}
        project(swarm.root)
        after = {path: path.stat().st_mtime_ns for path in swarm.root.rglob("*") if path.is_file()}
        self.assertEqual(before, after)


class HealthTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def swarm(self, stage: str, name: str = "swarm") -> Swarm:
        target = self.root / name
        target.mkdir()
        return Swarm(target).upto(stage)

    def monitor(self, swarm: Swarm, **fields) -> None:
        swarm.write("reports/progress/exec-01/health.json", json.dumps({
            "schema_version": 1, "execution_id": "exec-01", "state": "active",
            "session_activity": {"status": "processing", "stale": False},
            "inactive_seconds": 5, "dispatches": {"running": 1, "completed": 2},
            **fields}))

    def test_a_healthy_execution_is_never_reported_as_stalled(self):
        swarm = self.swarm("reviews")
        self.monitor(swarm)
        record = compose(swarm.root)
        self.assertEqual(record["state"], "active")
        self.assertIn("processando", record["reason"])
        self.assertIn("em andamento", render(record))

    def test_a_silent_session_beyond_the_threshold_is_stalled(self):
        swarm = self.swarm("reviews")
        self.monitor(swarm, session_activity={"status": "waiting", "stale": False}, inactive_seconds=600,
                     dispatches={"running": 0, "completed": 2})
        record = compose(swarm.root, threshold=180)
        self.assertEqual(record["state"], "stalled")
        self.assertEqual(record["next"][0]["action"], "dispatch")
        self.assertIn("parado", render(record))

    def test_a_wedged_loop_reporting_processing_is_still_stalled(self):
        swarm = self.swarm("reviews")
        self.monitor(swarm, session_activity={"status": "processing", "stale": False},
                     inactive_seconds=300, dispatches={"running": 0, "completed": 2})
        record = compose(swarm.root, threshold=180)
        self.assertEqual(record["state"], "stalled")
        self.assertIn("sem sinal", record["reason"])

    def test_a_running_agent_earns_grace_but_not_forever(self):
        swarm = self.swarm("reviews")
        self.monitor(swarm, session_activity={"status": "processing", "stale": False},
                     inactive_seconds=400, dispatches={"running": 2})
        self.assertEqual(compose(swarm.root, threshold=180)["state"], "active")
        self.monitor(swarm, session_activity={"status": "processing", "stale": False},
                     inactive_seconds=1000, dispatches={"running": 2})
        record = compose(swarm.root, threshold=180)
        self.assertEqual(record["state"], "stalled")
        self.assertIn("triplo", record["reason"])

    def test_a_health_file_the_extension_stopped_refreshing_is_discarded(self):
        swarm = self.swarm("reviews")
        self.monitor(swarm, session_activity={"status": "processing", "stale": False}, inactive_seconds=5)
        old = time.time() - 3600
        for path in swarm.root.rglob("*"):
            if path.is_file():
                os.utime(path, (old, old))
        record = compose(swarm.root, threshold=180)
        self.assertEqual(record["state"], "stalled")
        self.assertIn("parou de publicar", record["reason"])
        self.assertEqual(record["execution_id"], "exec-01")

    def test_a_dispatch_within_the_threshold_is_waiting_not_stalled(self):
        swarm = self.swarm("reviews")
        self.monitor(swarm, session_activity={"status": "waiting", "stale": False}, inactive_seconds=30)
        self.assertEqual(compose(swarm.root, threshold=180)["state"], "waiting")

    def test_lost_observation_is_never_presented_as_active(self):
        swarm = self.swarm("reviews")
        self.monitor(swarm, state="unobserved",
                     session_activity={"status": "processing", "stale": True})
        record = compose(swarm.root)
        self.assertEqual(record["state"], "unobserved")
        self.assertIn("não observado", render(record))

    def test_without_the_extension_the_session_line_says_so(self):
        swarm = self.swarm("reviews")
        record = compose(swarm.root)
        self.assertFalse(record["session"]["observed"])
        self.assertIn("não observada", render(record))
        self.assertIsNotNone(record["artifacts"]["age_seconds"])

    def test_a_finished_delivery_is_closed_and_proposes_nothing(self):
        swarm = self.swarm("memory")
        swarm.write("reports/memory-proposal.json", json.dumps({"approved": True}))
        record = compose(swarm.root)
        self.assertEqual(record["state"], "closed")
        self.assertIn("entrega está completa", render(record))

    def test_an_escalated_execution_is_closed_for_the_watchdog(self):
        target = self.root / "escalated"
        target.mkdir()
        swarm = Swarm(target, cycle=3, max_cycles=3).upto("gate")
        swarm.review(grade="B+")
        swarm.gate()
        record = compose(swarm.root)
        self.assertEqual(record["state"], "closed")
        self.assertIn("escalada", record["reason"])

    def test_invalid_artifacts_are_reported_instead_of_crashing(self):
        target = self.root / "broken"
        target.mkdir()
        (target / "brief.md").write_text("sem frontmatter\n", encoding="utf-8")
        record = compose(target)
        self.assertEqual(record["state"], "invalid")
        self.assertIn("Diagnóstico", render(record))

    def test_the_threshold_is_bounded(self):
        swarm = self.swarm("reviews")
        for value in (10, 90000):
            with self.subTest(threshold=value), self.assertRaises(InputError):
                compose(swarm.root, threshold=value)

    def test_the_monitor_own_files_do_not_mask_a_stalled_swarm(self):
        swarm = self.swarm("reviews")
        self.monitor(swarm, session_activity={"status": "waiting", "stale": False}, inactive_seconds=600,
                     dispatches={"running": 0})
        record = compose(swarm.root, threshold=180)
        self.assertEqual(record["state"], "stalled")
        self.assertNotIn("health.json", record["artifacts"]["newest"] or "")

    def test_classification_requires_a_measurement_for_every_verdict(self):
        resume = {"complete": False, "blocked": [], "next": [], "cycle": 1, "phase": "reviews"}
        state, reason = classify(resume, None, None, 180)
        self.assertEqual(state, "unobserved")
        self.assertTrue(reason)
        state, _ = classify(resume, None, 600.0, 180)
        self.assertEqual(state, "stalled")
        state, _ = classify(resume, None, 10.0, 180)
        self.assertEqual(state, "waiting")


class ContractTests(unittest.TestCase):
    """The projection must not drift away from the contracts it depends on."""

    def test_the_projected_phases_are_the_phases_the_monitor_accepts(self):
        source = (Path(__file__).resolve().parents[1] / ".github" / "extensions"
                  / "document-swarm-monitor" / "state.mjs").read_text(encoding="utf-8")
        match = re.search(r"export const PHASES = \[(.*?)\];", source, re.S)
        self.assertIsNotNone(match, "state.mjs no longer declares PHASES")
        published = tuple(re.findall(r'"([^"]+)"', match.group(1)))
        self.assertEqual(MONITOR_PHASES, published[:-1])
        self.assertEqual(published[-1], "done", "only the closing phase may be absent from the projection")

    def test_every_phase_the_projection_returns_is_declared(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        seen = set()
        for stage in Swarm.ORDER:
            target = root / stage
            target.mkdir()
            seen.add(project(Swarm(target).upto(stage).root)["phase"])
        self.assertTrue(seen)
        self.assertTrue(seen <= set(MONITOR_PHASES), f"undeclared phases: {seen - set(MONITOR_PHASES)}")


class RecoveryReportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "swarm"
        self.root.mkdir()
        self.swarm = Swarm(self.root).upto("memory")

    def snapshot(self, recoveries: list[dict]) -> None:
        self.swarm.write("reports/progress/exec-01/snapshot.json", json.dumps({
            "schema_version": 1, "execution_id": "exec-01", "recoveries": recoveries}))

    def test_a_clean_execution_says_none_recorded(self):
        text = final_report.render(self.root)
        self.assertIn("### Watchdog recoveries", text)
        self.assertIn("None recorded.", text)

    def test_every_recovery_appears_in_the_final_report(self):
        self.snapshot([
            {"id": "a", "at": "2026-01-01T00:00:00Z", "rule": "R1", "agent_id": "reviewer-01-facts",
             "cycle": 1, "attempt": 1, "detail": "despacho sem chamada real"},
            {"id": "b", "at": "2026-01-01T00:05:00Z", "rule": "R4", "agent_id": None,
             "cycle": 1, "attempt": 1, "detail": "verify_tables.py não executado"},
        ])
        text = final_report.render(self.root)
        self.assertNotIn("None recorded.", text.split("### Watchdog recoveries")[1])
        self.assertIn("reviewer-01-facts", text)
        self.assertIn("verify_tables.py não executado", text)
        self.assertIn("not an agent", text)
        self.assertIn("never award a grade", text)

    def test_an_unreadable_snapshot_does_not_hide_the_section(self):
        self.swarm.write("reports/progress/exec-02/snapshot.json", "{ isto não é JSON")
        self.snapshot([{"id": "a", "at": "2026-01-01T00:00:00Z", "rule": "R2",
                        "agent_id": "author-01-platform", "cycle": 1, "attempt": 1, "detail": "subagente falhou"}])
        text = final_report.render(self.root)
        self.assertIn("author-01-platform", text)


if __name__ == "__main__":
    unittest.main()
