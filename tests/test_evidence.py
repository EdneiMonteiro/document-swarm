from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from scripts.checks import cycle_artifacts, evidence, final_report, gate, progress, resume, update_memory
from scripts.checks.common import InputError
from tests import test_editorial

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures/evidence"


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / "fixture"
        self.root.mkdir()
        for folder in ("agents", "reports", "sources", "output"):
            (self.root / folder).mkdir()
        self.reviewers = ["reviewer-01-facts", "reviewer-02-clarity"]
        self.auditor = "rubber-duck"
        self.write_brief(getattr(self, "maximum", None))
        for name in [*self.reviewers, self.auditor]:
            kind = "rubber-duck" if name == self.auditor else "reviewer"
            self.write(f"agents/{name}.md",
                       f"---\nname: {name}\nkind: {kind}\nrole: Review\nmodel: auto\nswarm: fixture\n"
                       "model_status: fixture\nmodel_rationale: annotated test double\ncontext_tier: default\n---\n")
        for name, kind in (("author-01", "author"), ("coordinator", "coordinator")):
            self.write(f"agents/{name}.md",
                       f"---\nname: {name}\nkind: {kind}\nrole: Production\nmodel: auto\nswarm: fixture\n"
                       "model_status: fixture\nmodel_rationale: annotated test double\ncontext_tier: default\n---\n")
        self.write("sources/F16.txt", "The driver delivers to the hotel at the summit, driving up and back down.\n")
        if getattr(self, "historical", False):
            self.json("reports/cycle-05-review.yaml", {
                "skill_version": "3.1.0", "cycle": 5, "max_cycles": 5,
                "topics": [{"topico": "T01", "nota_minima": "A", "revisor_da_minima": "reviewer-01-facts", "bloqueia": False}],
                "rubberduck": {"critico": False, "achados": []},
            })
        evidence.initialize(self.root)

    def write(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
        return path

    def json(self, name, data):
        self.write(name, json.dumps(data, ensure_ascii=True, indent=2))
        return name

    def write_brief(self, maximum=None):
        self.write("brief.md",
                   '---\nswarm_id: fixture\nskill_version: "4.0.0"\nmode: document\n'
                   + (f"max_cycles: {maximum}\n" if maximum is not None else "")
                   + "evidence_contract: evidence-v1\nquality_contract: editorial-v1\n"
                   "factual_reviewer: reviewer-01-facts\neditorial_reviewer: reviewer-02-clarity\n"
                   "evidence_auditor: rubber-duck\nevidence_reviewers:\n"
                   "  - reviewer-01-facts\n  - reviewer-02-clarity\n"
                   "deliverables:\n  - output/document.md\n---\n# Fixture\n")

    def frozen(self, cycle=1, *, text=None, composition="first"):
        text = text or "# Route\n\nThe driver goes up to deliver and returns down.\n"
        self.write("output/document.md", text)
        text_name = f"reports/cycle-{cycle:02d}-editorial-text.txt"
        self.write(text_name, text)
        self.write(f"reports/cycle-{cycle:02d}-source.md", text)
        frozen = evidence.freeze(self.root, {
            "review_cycle": cycle, "composition_id": composition, "source": "output/document.md",
            "text": text_name, "sources": [{"id": "F16", "path": "sources/F16.txt"}],
        })
        key = frozen["candidate_id"]
        lines = text.splitlines()
        positions = [i for i, line in enumerate(lines, 1) if line and not line.startswith("#")]
        inventory = {
            "schema_version": 1, "candidate_id": key,
            "claims": [{"id": "route", "relation": {"subject": "driver", "action": "deliver", "direction": "up"},
                        "occurrences": [{"id": f"route-{i}", "start": i, "end": i, "quote": lines[i - 1]} for i in positions]}],
            "blocks": [{"id": f"block-{i}", "start": i, "end": i, "quote": line,
                        "classification": "non_factual" if line.startswith("#") else "factual",
                        "justification": "Heading." if line.startswith("#") else "Delivery direction.",
                        "claims": [] if line.startswith("#") else ["route"]}
                       for i, line in enumerate(lines, 1) if line],
        }
        return frozen, inventory

    def invent(self, frozen, inventory):
        return evidence.register(self.root, "inventory", frozen["candidate_id"],
                                 self.json(f"reports/inventory-{frozen['candidate_id']}.json", inventory))

    def reviewer(self, frozen, inventory, name, *, findings=None, conclusion="supported"):
        report = {"schema_version": 1, "cycle": frozen["review_cycle"], "reviewer": name,
                  "evidence_contract": "evidence-v1", "candidate_id": frozen["candidate_id"],
                  "scope": "full_document", "justification": "Read all blocks and occurrences.",
                  "evidence_findings": findings or [],
                  "topics": [{"topic": "T01", "grade": "A", "justification": "Whole route inspected.", "action": ""}]}
        if name == self.reviewers[0]:
            report.update({
                "inventory_complete": True,
                "block_checks": [{"id": b["id"], "accepted": True, "justification": "Classification inspected."}
                                 for b in inventory["blocks"]],
                "assessments": [{"claim": "route", "conclusion": conclusion,
                                 "justification": "Outbound and return direction compared with source.",
                                 "consistency": "Every route occurrence compared.",
                                 "occurrences": [o["id"] for o in inventory["claims"][0]["occurrences"]],
                                 "evidence": [{"source": "F16", "start": 1, "end": 1,
                                               "quote": (self.root / "sources/F16.txt").read_text().strip()}]}],
            })
        else:
            report["editorial"] = {
                "schema_version": 1, "cycle": frozen["review_cycle"], "reviewer": name,
                "scope": "full_document", "text": frozen["text"], "artifacts": frozen["artifacts"],
                "surfaces": [{"surface": surface, "grade": "A", "location": "whole document",
                              "quote": "# Route", "justification": "Names the concrete route.", "action": ""}
                             for surface in gate.EDITORIAL_SURFACES],
                "findings": [],
            }
        return report

    def record(self, frozen, report, *, prefix=None):
        name, key = report["reviewer"], frozen["candidate_id"]
        evidence.register(self.root, "dispatch", key, reviewer=name)
        path = f"{prefix or 'reports'}/cycle-{frozen['review_cycle']:02d}-{name}.json"
        evidence.register(self.root, "review", key, self.json(path, report))
        return report

    def audited(self, frozen, *, findings=None, critical=False):
        key = frozen["candidate_id"]
        report = {"schema_version": 1, "cycle": frozen["review_cycle"], "reviewer": self.auditor,
                  "evidence_contract": "evidence-v1", "candidate_id": key, "scope": "full_document",
                  "justification": "Independently inspected full candidate and both reviewer arguments.",
                  "evidence_findings": findings or [], "critical": critical, "inventory_complete": True,
                  "reviewed_reviewers": self.reviewers,
                  "review_artifacts": [evidence.descriptor(self.root, f"reports/cycle-{frozen['review_cycle']:02d}-{r}.json")
                                       for r in self.reviewers]}
        return self.record(frozen, report)

    def ready(self, cycle=1, *, audit=True, findings=None, conclusion="supported"):
        frozen, inventory = self.frozen(cycle)
        self.invent(frozen, inventory)
        for name in self.reviewers:
            self.record(frozen, self.reviewer(frozen, inventory, name, conclusion=conclusion))
        if audit:
            self.audited(frozen, findings=findings)
        review = cycle_artifacts.consolidate(self.root, cycle)
        return frozen, inventory, review

    def finding(self, fid="veto", occurrences=None):
        return {"id": fid, "family": "inverted-direction", "severity": "blocking",
                "reason": "Direction conflicts with source.", "action": "Inspect whole route family.",
                "evidence": "Source says driving up and back down.",
                "family_inspection": "Inspected opening and return.",
                "occurrences": occurrences or ["route-3"]}

    def test_end_to_end_approval_binds_originals_and_read_only_consumers(self):
        frozen, _, review = self.ready()
        self.assertEqual(gate.evaluate(review)["outcome"], "rejected")
        decision = gate.evaluate_current(review, self.root)
        self.assertEqual(decision["outcome"], "approved")
        self.assertEqual(decision["evidence"]["candidate_id"], frozen["candidate_id"])
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(gate.main([str(self.root / "reports/cycle-01-review.yaml"),
                                       "--output", str(self.root / "reports/cycle-01-gate.json")]), 0)
        self.json("sources/sources-check.json", {"counts": {"ok": 1, "fail": 0}})
        self.write("sources/sources-index.md", "| ID | Title | URL |\n| F16 | Route | https://example.org/route |\n")
        self.json("reports/cycle-01-tables-check.json", {"failures": 0})
        before = {p.relative_to(self.root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(progress.snapshot(self.root)["cycles"][0]["gate"]["outcome"], "approved")
        self.assertTrue(update_memory.approved(self.root))
        self.assertIn("Factual evidence", final_report.render(self.root))
        self.assertEqual(resume.project(self.root)["phase"], "authors")
        after = {p.relative_to(self.root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(before, after)

    def test_small_evidence_swarm_persists_gate_report_memory_and_complete_resume(self):
        from scripts.checks import inspect_nomenclature, lint_agents, verify_tables
        _, _, review = self.ready()
        self.write("reports/cycle-01-authors.md", "# Fixture authors\n\nRoute recorded.\n")
        self.write("reports/cycle-01-rubberduck.md", "# Fixture audit\n\nNo unresolved defects.\n")
        self.json("sources/sources-check.json", {
            "counts": {"ok": 1, "fail": 0},
            "results": [{"url": "https://example.org/route", "source_id": "F16", "status": "ok",
                         "corpus_sha256": evidence.descriptor(self.root, "sources/F16.txt")["sha256"]}],
        })
        self.write("sources/sources-index.md", "| ID | Title | URL |\n| F16 | Route | https://example.org/route |\n")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(lint_agents.main([str(self.root / "agents"), "--strict"]), 0)
            self.assertEqual(verify_tables.main([str(self.root / "output/document.md"),
                                                "--output", str(self.root / "reports/cycle-01-tables-check.json")]), 0)
            self.assertEqual(inspect_nomenclature.main([
                str(self.root / "reports/cycle-01-editorial-text.txt"), "--output",
                str(self.root / "reports/cycle-01-nomenclature.json")]), 0)
            self.assertEqual(gate.main([str(self.root / "reports/cycle-01-review.yaml"), "--output",
                                       str(self.root / "reports/cycle-01-gate.json")]), 0)
            evidence.close_cycle(self.root, "reports/cycle-01-review.yaml")
            self.assertEqual(final_report.main([str(self.root)]), 0)
            self.assertEqual(update_memory.main([str(self.root)]), 0)
        gate_record = json.loads((self.root / "reports/cycle-01-gate.json").read_text())
        self.assertEqual(gate_record["result"], gate.evaluate_current(review, self.root))
        self.assertIn("Inventoried claims: 1", (self.root / "reports/final-report.md").read_text())
        self.assertTrue(json.loads((self.root / "reports/memory-proposal.json").read_text())["approved"])
        self.assertTrue(resume.project(self.root)["complete"])

    def test_legitimate_approval_at_six_or_seven_does_not_wait_for_ten(self):
        for target in (6, 7):
            fixture = EvidenceTests()
            fixture.setUp()
            self.addCleanup(fixture.doCleanups)
            for cycle in range(1, target + 1):
                if evidence.state(fixture.root)["diagnosis_required"]:
                    fixture.json(f"reports/diagnosis-{cycle}.json", {
                        "causes": "Same route issue remains",
                        "changed_approach": "Inspect outbound and return together against source"})
                    evidence.control(fixture.root, "diagnosis", f"reports/diagnosis-{cycle}.json")
                frozen, _, review = fixture.ready(cycle, findings=[] if cycle == target else [fixture.finding()])
                if cycle == target:
                    resolution = {"candidate_id": frozen["candidate_id"], "finding": "veto", "reviewer": fixture.auditor,
                                  "correction": "Verified corrected route", "reassessment": "Compared source with all occurrences",
                                  "family_inspection": "Checked all blocks", "previous_occurrences": ["route-3"],
                                  "occurrences": ["block-1", "block-3", "route-3"]}
                    fixture.json("reports/resolution.json", resolution)
                    evidence.register(fixture.root, "resolution", frozen["candidate_id"], "reports/resolution.json")
                    review = cycle_artifacts.consolidate(fixture.root, cycle)
                decision = gate.evaluate_current(review, fixture.root)
                self.assertEqual(decision["outcome"], "approved" if cycle == target else "rejected",
                                 f"target={target}, cycle={cycle}, measured={decision}")
                evidence.close_cycle(fixture.root, f"reports/cycle-{cycle:02d}-review.yaml")
            self.assertEqual(evidence.state(fixture.root)["review_cycles"], target)

    def test_missing_auditor_is_pending_and_reconsolidation_preserves_four_vetoes(self):
        frozen, _, review = self.ready(audit=False)
        self.assertEqual(gate.evaluate_current(review, self.root)["evidence"]["audit"], "audit_pending")
        findings = [self.finding(f"veto-{i}") for i in range(4)]
        self.audited(frozen, findings=findings, critical=True)
        original = (self.root / "reports/cycle-01-rubber-duck.json").read_bytes()
        for _ in range(2):
            review = cycle_artifacts.consolidate(self.root, 1)
            self.assertEqual(len(review["rubberduck"]["achados"]), 4)
            self.assertEqual(gate.evaluate_current(review, self.root)["outcome"], "rejected")
        self.assertEqual(original, (self.root / "reports/cycle-01-rubber-duck.json").read_bytes())

    def test_missing_blocks_and_false_factual_classification_fail_for_the_right_reason(self):
        frozen, inventory = self.frozen()
        missing = copy.deepcopy(inventory)
        missing["blocks"].pop()
        with self.assertRaisesRegex(InputError, "missing lines: \\[3\\]"):
            self.invent(frozen, missing)
        self.invent(frozen, inventory)
        report = self.reviewer(frozen, inventory, self.reviewers[0])
        report["block_checks"][1]["accepted"] = False
        report["block_checks"][1]["justification"] = "The block contains an unlisted direction fact."
        self.record(frozen, report)
        self.record(frozen, self.reviewer(frozen, inventory, self.reviewers[1]))
        self.audited(frozen)
        review = cycle_artifacts.consolidate(self.root, 1)
        self.assertIn("block-3", {b["name"] for b in gate.evaluate_current(review, self.root)["blocked"]})

    def test_relation_subject_direction_and_result_mutations_invalidate_old_candidate(self):
        frozen, _, review = self.ready()
        for word in ("down", "another driver", "failed delivery"):
            with self.subTest(mutation=word):
                original = (self.root / "output/document.md").read_bytes()
                self.write("output/document.md", "# Route\n\n" + word + "\n")
                with self.assertRaisesRegex(InputError, "stale evidence artifact: output/document.md"):
                    gate.evaluate_current(review, self.root)
                (self.root / "output/document.md").write_bytes(original)
        with self.assertRaisesRegex(InputError, "frozen candidate"):
            cycle_artifacts.assemble(self.root, 1)

    def test_a_capture_of_different_wording_cannot_certify_delivered_markdown(self):
        self.write("output/document.md", "# Route\n\nThe driver goes down to deliver.\n")
        self.write("reports/capture.txt", "# Route\n\nThe driver goes up to deliver.\n")
        with self.assertRaisesRegex(InputError, "differs from delivered source wording"):
            evidence.freeze(self.root, {"review_cycle": 1, "composition_id": "unrelated-capture",
                                       "source": "output/document.md", "text": "reports/capture.txt", "sources": []})

    def test_every_original_binding_and_policy_path_rejects_tampering(self):
        _, _, review = self.ready()
        for path in ("reports/cycle-01-reviewer-01-facts.json", "sources/F16.txt",
                     "agents/reviewer-01-facts.md", "reports/cycle-01-editorial-text.txt",
                     "reports/evidence-policy.json"):
            with self.subTest(path=path):
                target = self.root / path
                original = target.read_bytes()
                target.write_bytes(original + b" ")
                if path.endswith("policy.json"):
                    # Whitespace is not policy tampering; its parsed contract is unchanged.
                    target.write_bytes(original.replace(b'"approval_grade": "A"', b'"approval_grade": "B"'))
                with self.assertRaises(InputError):
                    gate.evaluate_current(review, self.root)
                target.write_bytes(original)
        brief = (self.root / "brief.md").read_text()
        self.write("brief.md", brief.replace("evidence_contract: evidence-v1\n", ""))
        with self.assertRaisesRegex(InputError, "cannot be removed"):
            gate.evaluate_current(review, self.root)
        self.assertFalse(update_memory.approved(self.root))

    def test_missing_database_does_not_fall_back_to_old_published_approval(self):
        _, _, review = self.ready()
        (self.root / "reports/evidence/evidence.sqlite").unlink()
        self.write("reports/final-report.md", "**Approved**\n")
        self.assertFalse(update_memory.approved(self.root))
        with self.assertRaisesRegex(InputError, "database is missing"):
            gate.evaluate_current(review, self.root)
        self.assertEqual(progress.snapshot(self.root)["evidence"]["status"], "invalid")

    def test_external_paths_symlinks_transaction_rollback_and_idempotency(self):
        frozen, inventory = self.frozen()
        path = self.json("reports/inventory.json", inventory)
        one = evidence.register(self.root, "inventory", frozen["candidate_id"], path)
        self.assertEqual(one, evidence.register(self.root, "inventory", frozen["candidate_id"], path))
        for path in ("../outside", "/tmp/outside", "C:/outside", "reports/../../outside"):
            with self.subTest(path=path), self.assertRaisesRegex(InputError, "escapes"):
                evidence.inside(self.root, path)
        (self.root / "reports/escape").symlink_to(self.root.parent)
        with self.assertRaisesRegex(InputError, "escapes"):
            evidence.inside(self.root, "reports/escape/file")
        before = evidence.state(self.root)["last_event_sha256"]
        with self.assertRaises(RuntimeError):
            with evidence.connect(self.root, write=True) as db:
                history = evidence.events(db, self.root)
                evidence.append(db, history, "diagnosis", "interrupted", {"why": "fixture"})
                raise RuntimeError("interrupt transaction")
        self.assertEqual(before, evidence.state(self.root)["last_event_sha256"])

    def test_default_ten_explicit_five_and_ceiling_is_not_a_reward(self):
        self.assertEqual(evidence.state(self.root)["max_cycles"], 10)
        self.write_brief(5)
        with self.assertRaisesRegex(InputError, "ceiling differs"):
            self.frozen()
        self.write_brief()
        self.json("reports/unauthorized.json", {"max_cycles": 20})
        with self.assertRaisesRegex(InputError, "user authorization"):
            evidence.control(self.root, "extension", "reports/unauthorized.json")
        self.json("reports/extension.json", {"actor": "user", "decision": "extend", "max_cycles": 12,
                                           "reason": "Need new source", "authorization": "Fixture user explicitly allows 12"})
        evidence.control(self.root, "extension", "reports/extension.json")
        self.write_brief(12)
        self.assertEqual(evidence.state(self.root)["max_cycles"], 12)

    def test_stagnation_requires_recorded_changed_approach_not_more_renders(self):
        for number in range(1, 4):
            _, _, review = self.ready(number, findings=[self.finding()])
            evidence.close_cycle(self.root, f"reports/cycle-{number:02d}-review.yaml")
        self.assertTrue(evidence.state(self.root)["diagnosis_required"])
        with self.assertRaisesRegex(InputError, "diagnosis_required"):
            self.frozen(4)
        self.json("reports/diagnosis.json", {"causes": "Same evidence and unresolved route", "changed_approach": "Consult route record and re-evaluate every occurrence"})
        evidence.control(self.root, "diagnosis", "reports/diagnosis.json")
        self.assertFalse(evidence.state(self.root)["diagnosis_required"])
        self.frozen(4)

    def test_old_veto_survives_new_cycle_until_owner_reassesses_entire_family(self):
        frozen, _, _ = self.ready(findings=[self.finding()])
        evidence.close_cycle(self.root, "reports/cycle-01-review.yaml")
        current, _, review = self.ready(2)
        self.assertEqual(gate.evaluate_current(review, self.root)["evidence"]["open_findings"], 1)
        resolution = {"candidate_id": current["candidate_id"], "finding": "veto", "reviewer": self.auditor,
                      "correction": "Corrected every route location", "reassessment": "Compared all to F16",
                      "family_inspection": "Re-read entire candidate for inverted routes",
                      "occurrences": [], "previous_occurrences": ["route-3"]}
        path = self.json("reports/resolution.json", resolution)
        with self.assertRaisesRegex(InputError, "whole current occurrence"):
            evidence.register(self.root, "resolution", current["candidate_id"], path)
        resolution["occurrences"] = ["block-1", "block-3", "route-3"]
        self.json(path, resolution)
        evidence.register(self.root, "resolution", current["candidate_id"], path)
        self.assertEqual(gate.evaluate_current(review, self.root)["outcome"], "approved")

    def test_consolidation_cannot_change_matrix_or_original_hashes(self):
        _, _, review = self.ready()
        review["topics"][0]["nota_minima"] = "A+"
        with self.assertRaisesRegex(InputError, "original minimum"):
            gate.evaluate_current(review, self.root)
        original = (self.root / "reports/cycle-01-reviewer-02-clarity.json").read_bytes()
        cycle_artifacts.consolidate(self.root, 1)
        self.assertEqual(original, (self.root / "reports/cycle-01-reviewer-02-clarity.json").read_bytes())

    def test_corpus_refresh_invalidates_dependents_even_when_old_snapshot_exists(self):
        _, _, review = self.ready()
        self.json("sources/sources-check.json", {"results": [{"source_id": "F16", "status": "ok", "corpus_sha256": "0" * 64}]})
        with self.assertRaisesRegex(InputError, "source refreshed"):
            gate.evaluate_current(review, self.root)

    def test_explicit_five_approves_early_and_escalates_at_five_without_lowering_a(self):
        fixture = EvidenceTests()
        fixture.maximum = 5
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.assertEqual(evidence.state(fixture.root)["max_cycles"], 5)
        for cycle in range(1, 6):
            if evidence.state(fixture.root)["diagnosis_required"]:
                fixture.json("reports/diagnosis.json", {"causes": "Source insufficient", "changed_approach": "Re-examine source and all route occurrences"})
                evidence.control(fixture.root, "diagnosis", "reports/diagnosis.json")
            _, _, review = fixture.ready(cycle, findings=[fixture.finding()])
            decision = gate.evaluate_current(review, fixture.root)
            self.assertEqual(decision["outcome"], "escalate" if cycle == 5 else "rejected")
            evidence.close_cycle(fixture.root, f"reports/cycle-{cycle:02d}-review.yaml")
        with self.assertRaisesRegex(InputError, "ceiling"):
            fixture.frozen(6)

    def test_multiple_compositions_need_explicit_identity_without_spending_review_cycles(self):
        frozen, inventory, _ = self.ready()
        second, _ = self.frozen(composition="second")
        inventory["candidate_id"] = second["candidate_id"]
        self.invent(second, inventory)
        prefix = f"reports/evidence/responses/{second['candidate_id']}"
        for name in self.reviewers:
            self.record(second, self.reviewer(second, inventory, name), prefix=prefix)
        # No audit yet: both candidates retain their own original responses.
        with self.assertRaisesRegex(InputError, "exact candidate_id"):
            cycle_artifacts.consolidate(self.root, 1)
        result = cycle_artifacts.consolidate(self.root, 1, candidate_id=second["candidate_id"])
        self.assertEqual(result["candidate_id"], second["candidate_id"])
        state = evidence.state(self.root)
        self.assertEqual((state["compositions"], state["review_cycles"]), (2, 0))
        with evidence.connect(self.root) as db:
            history = evidence.events(db, self.root)
        self.assertEqual(evidence.find(history, "review", f"{frozen['candidate_id']}:{self.reviewers[0]}")["record"]["candidate_id"],
                         frozen["candidate_id"])

    def test_explicit_recovery_preserves_history_but_does_not_restore_approval(self):
        _, _, review = self.ready()
        evidence.close_cycle(self.root, "reports/cycle-01-review.yaml")
        self.write("reports/evidence/evidence.sqlite", "corrupt database")
        result = evidence.recover(self.root, "reports/evidence/events.json", "fixture interrupted disk write")
        self.assertFalse(result["approval_restored"])
        with self.assertRaisesRegex(InputError, "new candidate and full re-audit"):
            gate.evaluate_current(review, self.root)
        self.assertFalse(update_memory.approved(self.root))
        self.assertTrue(evidence.state(self.root)["recovery_pending"])
        self.assertEqual(len(list((self.root / "reports/evidence").glob("evidence-before-recovery-*.sqlite"))), 1)
        self.frozen(2, composition="recovered")

    def test_adopting_contract_preserves_legacy_bytes_and_continues_numbering(self):
        fixture = EvidenceTests()
        fixture.historical = True
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        historical = (fixture.root / "reports/cycle-05-review.yaml").read_bytes()
        self.assertEqual(evidence.state(fixture.root)["first_review_cycle"], 6)
        self.assertEqual(resume.project(fixture.root)["cycle"], 6)
        _, _, review = fixture.ready(6)
        self.assertEqual(gate.evaluate_current(review, fixture.root)["outcome"], "approved")
        self.assertEqual((fixture.root / "reports/cycle-05-review.yaml").read_bytes(), historical)
        self.assertEqual(evidence.initialize(fixture.root)["first_review_cycle"], 6)

    def test_annotated_initial_d_conflict_checks_both_occurrences_and_source(self):
        reference = json.loads((FIXTURES / "reference.json").read_text())
        source_line = (ROOT / reference["source"]).read_text().splitlines()[reference["line"] - 1]
        self.assertIn(reference["excerpt"], source_line)
        self.write("sources/F16.txt", source_line + "\n")
        frozen, inventory = self.frozen(text="# Route\n\n" + (FIXTURES / "route.md").read_text())
        self.assertEqual(len(inventory["claims"][0]["occurrences"]), 2)
        self.invent(frozen, inventory)
        finding = self.finding(occurrences=[o["id"] for o in inventory["claims"][0]["occurrences"]])
        self.record(frozen, self.reviewer(frozen, inventory, self.reviewers[0],
                                         findings=[finding], conclusion=reference["expected_conclusion"]))
        self.record(frozen, self.reviewer(frozen, inventory, self.reviewers[1]))
        self.audited(frozen, findings=[finding], critical=True)
        review = cycle_artifacts.consolidate(self.root, 1)
        result = gate.evaluate_current(review, self.root)
        self.assertEqual(result["outcome"], "rejected", f"measured={result}")
        self.assertIn("route", {b["name"] for b in result["blocked"]}, f"measured={result['blocked']}")


class ReopenedEditorialTests(unittest.TestCase):
    setUp = test_editorial.EditorialTests.setUp
    descriptor = test_editorial.EditorialTests.descriptor
    cycle = test_editorial.EditorialTests.cycle
    save = test_editorial.EditorialTests.save
    def test_discovered_defect_can_reopen_a_on_identical_editorial_text(self):
        self.cycle(1)
        review = self.cycle(2)
        item = review["editorial"]["surfaces"][0]
        item.update(grade="B+", action="Specify the missing scope",
                    regression={"cause": "newly_discovered_defect", "quote": item["quote"],
                                "reason": "Prior review overlooked the title scope",
                                "evidence": "Current title omits measured scope"})
        self.save(review)
        result = gate.evaluate_current(review, self.root)
        self.assertEqual(result["outcome"], "rejected", f"measured={result}")


if __name__ == "__main__":
    unittest.main()
