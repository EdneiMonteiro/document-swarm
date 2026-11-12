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


class GoldfishEvidenceTests(EvidenceTests):
    BACKSTAGE_SECRET = "SECRET_BACKSTAGE_THESIS_NEVER_EXPOSED_TO_CLARITY"

    @unittest.skipUnless(
        (ROOT / "swarms/2026-10-29-SWARM-02/sources/corpus/F16.txt").is_file(),
        "local gitignored swarm corpus absent",
    )
    def test_annotated_initial_d_conflict_checks_both_occurrences_and_source(self):
        super().test_annotated_initial_d_conflict_checks_both_occurrences_and_source()

    def write_brief(self, maximum=None):
        self.write(
            "brief.md",
            '---\nswarm_id: fixture\nskill_version: "4.1.0"\nmode: document\n'
            + (f"max_cycles: {maximum}\n" if maximum is not None else "")
            + 'audience: "Logistics supervisors without prior project context"\n'
            "evidence_contract: evidence-v1\nquality_contract: editorial-v1\n"
            "comprehension_contract: goldfish-v1\n"
            "factual_reviewer: reviewer-01-facts\neditorial_reviewer: reviewer-02-clarity\n"
            "evidence_auditor: rubber-duck\nevidence_reviewers:\n"
            "  - reviewer-01-facts\n  - reviewer-02-clarity\n"
            "topics:\n  T01: Summit delivery route\n"
            "deliverables:\n  - output/document.md\n---\n"
            f"# Fixture\n\n{self.BACKSTAGE_SECRET}\n",
        )

    def reviewer(self, frozen, inventory, name, *, findings=None, conclusion="supported"):
        report = super().reviewer(frozen, inventory, name, findings=findings, conclusion=conclusion)
        if name == self.reviewers[1]:
            report["editorial"]["blind_paraphrase"] = {
                "reader_context": frozen["reader_context"],
                "central_thesis": "A logistics driver ascends to the summit facility to complete a delivery and then descends along the return leg.",
                "how_parts_connect": "The heading establishes the route scope while the body states both the uphill outbound movement and the downhill return.",
                "unconnected_sections": [],
            }
        return report

    def audited(self, frozen, *, findings=None, critical=False, matches_brief=True):
        key = frozen["candidate_id"]
        report = {
            "schema_version": 1,
            "cycle": frozen["review_cycle"],
            "reviewer": self.auditor,
            "evidence_contract": "evidence-v1",
            "candidate_id": key,
            "scope": "full_document",
            "justification": "Independently inspected full candidate, reviewer records, and blind paraphrase against brief.md.",
            "evidence_findings": findings or [],
            "critical": critical,
            "inventory_complete": True,
            "reviewed_reviewers": self.reviewers,
            "review_artifacts": [
                evidence.descriptor(self.root, f"reports/cycle-{frozen['review_cycle']:02d}-{r}.json")
                for r in self.reviewers
            ],
            "paraphrase_check": {
                "reviewer": "reviewer-02-clarity",
                "brief_sha256": evidence.descriptor(self.root, "brief.md")["sha256"],
                "matches_brief": matches_brief,
                "justification": "Compared reviewer-02-clarity blind paraphrase against brief.md route intent.",
            },
        }
        return self.record(frozen, report)

    def test_goldfish_reader_context_isolates_backstage_brief_and_enforces_paraphrase_gate(self):
        frozen, inventory = self.frozen(1)
        rc_path = self.root / frozen["reader_context"]["path"]
        rc_raw = rc_path.read_text(encoding="utf-8")
        self.assertNotIn(self.BACKSTAGE_SECRET, rc_raw)
        rc_obj = json.loads(rc_raw)
        self.assertEqual(rc_obj["comprehension_contract"], "goldfish-v1")
        self.assertIn("brief.md", rc_obj["forbidden_paths"])
        self.assertEqual(rc_obj["topics"], [{"id": "T01", "ordinal": 1, "label": "Summit delivery route"}])

        self.invent(frozen, inventory)
        disp_facts = evidence.register(self.root, "dispatch", frozen["candidate_id"], reviewer="reviewer-01-facts")
        disp_clarity = evidence.register(self.root, "dispatch", frozen["candidate_id"], reviewer="reviewer-02-clarity")
        self.assertEqual(disp_facts["isolation"], "full_brief")
        self.assertEqual(disp_clarity["isolation"], "goldfish-v1")
        self.assertTrue(disp_clarity["fresh_task_required"])
        self.assertEqual(disp_clarity["reader_context"], frozen["reader_context"])

        facts_rep = self.reviewer(frozen, inventory, "reviewer-01-facts")
        evidence.register(self.root, "review", frozen["candidate_id"], self.json("reports/cycle-01-reviewer-01-facts.json", facts_rep))

        # Tampered reader_context hash or verbatim copy in clarity review is rejected on registration.
        bad_clarity = self.reviewer(frozen, inventory, "reviewer-02-clarity")
        bad_clarity["editorial"]["blind_paraphrase"]["reader_context"] = {"path": frozen["reader_context"]["path"], "sha256": "0" * 64}
        with self.assertRaisesRegex(InputError, "exact frozen reader_context"):
            evidence.register(self.root, "review", frozen["candidate_id"], self.json("reports/bad-rc.json", bad_clarity))

        verbatim_clarity = self.reviewer(frozen, inventory, "reviewer-02-clarity")
        verbatim_clarity["editorial"]["blind_paraphrase"]["central_thesis"] = (
            "# Route The driver goes up to deliver and returns down."
        )
        with self.assertRaisesRegex(InputError, "own words, not copied verbatim"):
            evidence.register(self.root, "review", frozen["candidate_id"], self.json("reports/bad-verbatim.json", verbatim_clarity))

        good_clarity = self.reviewer(frozen, inventory, "reviewer-02-clarity")
        evidence.register(self.root, "review", frozen["candidate_id"], self.json("reports/cycle-01-reviewer-02-clarity.json", good_clarity))

        # Before rubber-duck runs, consolidation stays audit_pending and blocks gate.
        pending_review = cycle_artifacts.consolidate(self.root, 1)
        self.assertEqual(pending_review["comprehension_contract"], "goldfish-v1")
        self.assertEqual(gate.evaluate_current(pending_review, self.root)["outcome"], "rejected")

        # Auditor without critical=True when matches_brief=False is rejected.
        evidence.register(self.root, "dispatch", frozen["candidate_id"], reviewer=self.auditor)
        bad_audit = {
            "schema_version": 1, "cycle": 1, "reviewer": self.auditor, "evidence_contract": "evidence-v1",
            "candidate_id": frozen["candidate_id"], "scope": "full_document",
            "justification": "Blind paraphrase misunderstood the route.", "evidence_findings": [],
            "critical": False, "inventory_complete": True, "reviewed_reviewers": self.reviewers,
            "review_artifacts": [
                evidence.descriptor(self.root, f"reports/cycle-01-{r}.json") for r in self.reviewers
            ],
            "paraphrase_check": {
                "reviewer": "reviewer-02-clarity",
                "brief_sha256": evidence.descriptor(self.root, "brief.md")["sha256"],
                "matches_brief": False,
                "justification": "Clarity reviewer described an unrelated route.",
            },
        }
        with self.assertRaisesRegex(InputError, "critical=true"):
            evidence.register(self.root, "review", frozen["candidate_id"], self.json("reports/bad-audit.json", bad_audit))

        # Valid matching audit passes end-to-end and survives explicit recovery artifact tracking.
        good_audit = copy.deepcopy(bad_audit)
        good_audit["justification"] = "Blind paraphrase matches brief.md intent."
        good_audit["paraphrase_check"]["matches_brief"] = True
        good_audit["paraphrase_check"]["justification"] = "Paraphrase accurately captures summit ascent and descent."
        evidence.register(self.root, "review", frozen["candidate_id"], self.json("reports/cycle-01-rubber-duck.json", good_audit))
        final_review = cycle_artifacts.consolidate(self.root, 1)
        decision = gate.evaluate_current(final_review, self.root)
        self.assertEqual(decision["outcome"], "approved", f"measured={decision}")

    def test_goldfish_paraphrase_mismatch_blocks_delivery_in_ledger(self):
        frozen, inventory = self.frozen(1)
        self.invent(frozen, inventory)
        for name in self.reviewers:
            self.record(frozen, self.reviewer(frozen, inventory, name))
        mismatch_finding = self.finding(fid="paraphrase-drift")
        self.audited(frozen, findings=[mismatch_finding], critical=True, matches_brief=False)
        review = cycle_artifacts.consolidate(self.root, 1)
        decision = gate.evaluate_current(review, self.root)
        self.assertEqual(decision["outcome"], "rejected")
        self.assertIn("blind_paraphrase_mismatch", {b["name"] for b in decision["blocked"]})


class ContestEscalateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / "fixture"
        self.root.mkdir()
        for folder in ("agents", "reports", "sources", "output"):
            (self.root / folder).mkdir()
        self.reviewers = ["reviewer-01-facts", "reviewer-02-clarity", "reviewer-03-decision"]
        self.auditor = "rubber-duck"
        self.write(
            "brief.md",
            '---\nswarm_id: fixture\nskill_version: "4.1.0"\nmode: document\nmax_cycles: 5\n'
            'audience: "Platform engineers without prior project context"\n'
            "evidence_contract: evidence-v1\nquality_contract: editorial-v1\n"
            "comprehension_contract: goldfish-v1\n"
            "factual_reviewer: reviewer-01-facts\neditorial_reviewer: reviewer-02-clarity\n"
            "evidence_auditor: rubber-duck\nevidence_reviewers:\n"
            "  - reviewer-01-facts\n  - reviewer-02-clarity\n  - reviewer-03-decision\n"
            "topics:\n  T01: Summit delivery route\n"
            "deliverables:\n  - output/document.md\n---\n# Fixture\n",
        )
        for name in [*self.reviewers, self.auditor]:
            kind = "rubber-duck" if name == self.auditor else "reviewer"
            self.write(
                f"agents/{name}.md",
                f"---\nname: {name}\nkind: {kind}\nrole: Review\nmodel: auto\nswarm: fixture\n"
                "model_status: fixture\nmodel_rationale: annotated test double\ncontext_tier: default\n---\n",
            )
        self.write("sources/F16.txt", "The driver delivers to the hotel at the summit, driving up and back down.\n")
        self.json("sources/sources-check.json", {"counts": {"ok": 1, "redirect": 0, "warn": 0, "fail": 0}, "results": []})
        self.json("reports/cycle-01-tables-check.json", {"failures": 0})
        evidence.initialize(self.root)

    write = EvidenceTests.write
    json = EvidenceTests.json
    frozen = EvidenceTests.frozen
    invent = EvidenceTests.invent
    record = EvidenceTests.record
    finding = EvidenceTests.finding

    def make_reviewer(self, frozen, inventory, name, *, grade="A", action="", findings=None, conclusion="supported"):
        report = {
            "schema_version": 1,
            "cycle": frozen["review_cycle"],
            "reviewer": name,
            "evidence_contract": "evidence-v1",
            "candidate_id": frozen["candidate_id"],
            "scope": "full_document",
            "justification": "Inspected full frozen candidate.",
            "evidence_findings": findings or [],
            "topics": [{"topic": "T01", "grade": grade, "justification": "Assessed T01.", "action": action}],
        }
        if name == "reviewer-01-facts":
            report.update({
                "inventory_complete": True,
                "block_checks": [{"id": b["id"], "accepted": True, "justification": "Checked."} for b in inventory["blocks"]],
                "assessments": [{
                    "claim": "route",
                    "conclusion": conclusion,
                    "justification": "Checked against F16.",
                    "consistency": "Consistent.",
                    "occurrences": [o["id"] for o in inventory["claims"][0]["occurrences"]],
                    "evidence": [{
                        "source": "F16",
                        "start": 1,
                        "end": 1,
                        "quote": (self.root / "sources/F16.txt").read_text().strip(),
                    }],
                }],
            })
        elif name == "reviewer-02-clarity":
            report["editorial"] = {
                "schema_version": 1,
                "cycle": frozen["review_cycle"],
                "reviewer": name,
                "scope": "full_document",
                "text": frozen["text"],
                "artifacts": frozen["artifacts"],
                "surfaces": [{
                    "surface": surface,
                    "grade": "A",
                    "location": "whole document",
                    "quote": "# Route",
                    "justification": "Clear wording.",
                    "action": "",
                } for surface in gate.EDITORIAL_SURFACES],
                "findings": [],
                "blind_paraphrase": {
                    "reader_context": frozen["reader_context"],
                    "central_thesis": "A logistics driver ascends to the summit facility for delivery and descends afterward.",
                    "how_parts_connect": "The heading identifies the route while the body links outbound uphill travel with the return leg.",
                    "unconnected_sections": [],
                },
            }
        return report

    def make_audit(self, frozen, *, findings=None, critical=False, contest_rulings=None, escalation_rulings=None):
        report = {
            "schema_version": 1,
            "cycle": frozen["review_cycle"],
            "reviewer": self.auditor,
            "evidence_contract": "evidence-v1",
            "candidate_id": frozen["candidate_id"],
            "scope": "full_document",
            "justification": "Audited all reviewers and rulings.",
            "evidence_findings": findings or [],
            "critical": critical,
            "inventory_complete": True,
            "reviewed_reviewers": self.reviewers,
            "review_artifacts": [
                evidence.descriptor(self.root, f"reports/cycle-{frozen['review_cycle']:02d}-{r}.json")
                for r in self.reviewers
            ],
            "paraphrase_check": {
                "reviewer": "reviewer-02-clarity",
                "brief_sha256": evidence.descriptor(self.root, "brief.md")["sha256"],
                "matches_brief": True,
                "justification": "Blind paraphrase matches the summit route intent in brief.md.",
            },
        }
        if contest_rulings is not None:
            report["contest_rulings"] = contest_rulings
        if escalation_rulings is not None:
            report["escalation_rulings"] = escalation_rulings
        return report

    def test_contested_rejects_intentional_excuse_clarity_gaps_and_upholds_checkable_evidence(self):
        frozen, inventory = self.frozen(1)
        self.invent(frozen, inventory)
        self.record(frozen, self.make_reviewer(frozen, inventory, "reviewer-01-facts"))
        clarity_finding = self.finding("clarity-gap")
        self.record(frozen, self.make_reviewer(frozen, inventory, "reviewer-02-clarity", findings=[clarity_finding]))
        decision_finding = self.finding("false-return-critique")
        self.record(
            frozen,
            self.make_reviewer(
                frozen,
                inventory,
                "reviewer-03-decision",
                grade="B+",
                action="Specify return direction.",
                findings=[decision_finding],
            ),
        )
        valid_citations = [
            {"target": "candidate", "start": 3, "end": 3, "quote": "The driver goes up to deliver and returns down."},
            {"target": "F16", "start": 1, "end": 1, "quote": "The driver delivers to the hotel at the summit, driving up and back down."},
        ]
        audit = self.make_audit(
            frozen,
            contest_rulings=[{
                "finding": "false-return-critique",
                "target_reviewer": "reviewer-03-decision",
                "verdict": "upheld",
                "justification": "Candidate line 3 and F16 line 1 already state the downhill return explicitly.",
                "citations": valid_citations,
                "overturned_topics": ["T01"],
            }],
        )
        self.record(frozen, audit)

        # 1. Comprehension / clarity findings from editorial_reviewer can NEVER be contested.
        with self.assertRaisesRegex(InputError, "editorial_reviewer cannot be contested"):
            evidence.register(
                self.root,
                "resolution",
                frozen["candidate_id"],
                self.json("reports/contest-clarity.json", {
                    "candidate_id": frozen["candidate_id"],
                    "finding": "clarity-gap",
                    "status": "contested",
                    "reviewer": self.auditor,
                    "rationale": "Line 3 states the return leg clearly in the delivered text.",
                    "citations": valid_citations,
                }),
            )

        # 2. Author-intent excuses ("foi intencional" / "this was intentional") are rejected.
        with self.assertRaisesRegex(InputError, "author intent"):
            evidence.register(
                self.root,
                "resolution",
                frozen["candidate_id"],
                self.json("reports/contest-intent.json", {
                    "candidate_id": frozen["candidate_id"],
                    "finding": "false-return-critique",
                    "status": "contested",
                    "reviewer": self.auditor,
                    "rationale": "Isso foi intencional por escolha dos autores no desenho original.",
                    "citations": valid_citations,
                }),
            )

        # 3. Misquoted citation span or non-auditor actor is rejected.
        with self.assertRaisesRegex(InputError, "independent auditor"):
            evidence.register(
                self.root,
                "resolution",
                frozen["candidate_id"],
                self.json("reports/contest-wrong-actor.json", {
                    "candidate_id": frozen["candidate_id"],
                    "finding": "false-return-critique",
                    "status": "contested",
                    "reviewer": "reviewer-03-decision",
                    "rationale": "Candidate line 3 already states the downhill return explicitly.",
                    "citations": valid_citations,
                }),
            )
        with self.assertRaisesRegex(InputError, "differs from candidate text"):
            evidence.register(
                self.root,
                "resolution",
                frozen["candidate_id"],
                self.json("reports/contest-bad-quote.json", {
                    "candidate_id": frozen["candidate_id"],
                    "finding": "false-return-critique",
                    "status": "contested",
                    "reviewer": self.auditor,
                    "rationale": "Candidate line 3 already states the downhill return explicitly.",
                    "citations": [{"target": "candidate", "start": 3, "end": 3, "quote": "Invented sentence not on line 3."}],
                }),
            )

        # Close clarity-gap normally in cycle 2 while upholding contest on reviewer-03-decision's B+ without mutating its JSON.
        evidence.close_cycle(self.root, self.json("reports/cycle-01-review.yaml", cycle_artifacts.consolidate(self.root, 1)))
        c2, inv2 = self.frozen(2, composition="second")
        self.invent(c2, inv2)
        self.record(c2, self.make_reviewer(c2, inv2, "reviewer-01-facts"))
        self.record(c2, self.make_reviewer(c2, inv2, "reviewer-02-clarity"))
        orig_decision_path = self.root / "reports/cycle-02-reviewer-03-decision.json"
        self.record(
            c2,
            self.make_reviewer(
                c2,
                inv2,
                "reviewer-03-decision",
                grade="B+",
                action="Specify return direction.",
                findings=[decision_finding],
            ),
        )
        orig_decision_bytes = orig_decision_path.read_bytes()
        self.record(
            c2,
            self.make_audit(
                c2,
                contest_rulings=[{
                    "finding": "false-return-critique",
                    "target_reviewer": "reviewer-03-decision",
                    "verdict": "upheld",
                    "justification": "Candidate line 3 and F16 line 1 already state the downhill return explicitly.",
                    "citations": valid_citations,
                    "overturned_topics": ["T01"],
                }],
            ),
        )
        evidence.register(
            self.root,
            "resolution",
            c2["candidate_id"],
            self.json("reports/res-clarity-fixed.json", {
                "candidate_id": c2["candidate_id"],
                "finding": "clarity-gap",
                "reviewer": "reviewer-02-clarity",
                "correction": "Clarified phrasing.",
                "reassessment": "Re-read full text.",
                "family_inspection": "Checked all headings and paragraphs.",
                "occurrences": ["block-1", "block-3", "route-3"],
                "previous_occurrences": ["route-3"],
            }),
        )
        evidence.register(
            self.root,
            "resolution",
            c2["candidate_id"],
            self.json("reports/res-decision-contested.json", {
                "candidate_id": c2["candidate_id"],
                "finding": "false-return-critique",
                "status": "contested",
                "reviewer": self.auditor,
                "rationale": "Candidate line 3 and corpus F16 line 1 already specify the downhill return leg.",
                "citations": valid_citations,
            }),
        )
        self.assertEqual(orig_decision_bytes, orig_decision_path.read_bytes(), "original reviewer JSON must remain untouched")
        review_c2 = cycle_artifacts.consolidate(self.root, 2)
        self.assertEqual(review_c2["topics"][0]["nota_minima"], "A")
        self.assertEqual(review_c2["topics"][0]["contested_override"][0]["overturned_reviewer"], "reviewer-03-decision")
        decision_c2 = gate.evaluate_current(review_c2, self.root)
        self.assertEqual(decision_c2["outcome"], "approved", f"measured={decision_c2}")
        report_md = final_report.render(self.root)
        self.assertIn("`false-return-critique`: `contested`", report_md)

    def test_escalate_tracks_pending_user_prevents_stagnation_and_forbids_factual_waiver(self):
        frozen, inventory = self.frozen(1)
        self.invent(frozen, inventory)
        factual_finding = self.finding("unsupported-fact")
        self.record(
            frozen,
            self.make_reviewer(frozen, inventory, "reviewer-01-facts", grade="B+", action="Fix source.", findings=[factual_finding], conclusion="contradicted"),
        )
        self.record(frozen, self.make_reviewer(frozen, inventory, "reviewer-02-clarity"))
        biz_finding = self.finding("biz-convention")
        self.record(
            frozen,
            self.make_reviewer(frozen, inventory, "reviewer-03-decision", grade="B+", action="Add internal SLA.", findings=[biz_finding]),
        )
        self.record(
            frozen,
            self.make_audit(
                frozen,
                escalation_rulings=[{
                    "finding": "biz-convention",
                    "target_reviewer": "reviewer-03-decision",
                    "verdict": "accepted_out_of_band",
                    "justification": "Internal dispatch convention is confidential and outside public deliverables.",
                    "overturned_topics": ["T01"],
                }],
            ),
        )

        # 1. Cannot waive an unsupported factual claim via escalate.
        with self.assertRaisesRegex(InputError, "cannot waive unsupported factual claims"):
            evidence.register(
                self.root,
                "resolution",
                frozen["candidate_id"],
                self.json("reports/bad-factual-waive.json", {
                    "candidate_id": frozen["candidate_id"],
                    "finding": "unsupported-fact",
                    "status": "escalate",
                    "decision_state": "decided",
                    "effect": "waive_finding",
                    "reviewer": self.auditor,
                    "missing_decision": "Tries to waive contradicted fact.",
                    "ask_user_question": "Ignore source F16?",
                    "user_decision": "Ignore.",
                }),
            )

        # 2. Pending escalation blocks approval and exposes pending_escalations in state().
        evidence.register(
            self.root,
            "resolution",
            frozen["candidate_id"],
            self.json("reports/esc-pending.json", {
                "candidate_id": frozen["candidate_id"],
                "finding": "biz-convention",
                "status": "escalate",
                "decision_state": "pending_user",
                "reviewer": "coordinator",
                "missing_decision": "Internal confidential dispatch SLA not in repository.",
                "ask_user_question": "Should the confidential dispatch SLA remain omitted from the public guide?",
            }),
        )
        self.assertEqual(evidence.state(self.root)["pending_escalations"], ["biz-convention"])
        rev_c1 = cycle_artifacts.consolidate(self.root, 1)
        dec_c1 = gate.evaluate_current(rev_c1, self.root)
        self.assertEqual(dec_c1["outcome"], "rejected")
        self.assertIn("escalate_pending:biz-convention", {b["name"] for b in dec_c1["blocked"]})

        # 3. Recording user decision (update_document) clears pending_escalations and counts as cycle progress.
        evidence.register(
            self.root,
            "resolution",
            frozen["candidate_id"],
            self.json("reports/esc-decided-update.json", {
                "candidate_id": frozen["candidate_id"],
                "finding": "biz-convention",
                "status": "escalate",
                "decision_state": "decided",
                "effect": "update_document",
                "reviewer": "coordinator",
                "missing_decision": "Internal confidential dispatch SLA not in repository.",
                "ask_user_question": "Should we state a public boundary note instead?",
                "user_decision": "Keep confidential numbers out; state public boundary in cycle 2.",
            }),
        )
        self.assertEqual(evidence.state(self.root)["pending_escalations"], [])
        closed_c1 = evidence.close_cycle(self.root, "reports/cycle-01-review.yaml")
        self.assertEqual(closed_c1["gains"].get("decided_escalations"), 1)

        # 4. In cycle 2, factual issue is fixed and out-of-band convention is waived via ask_user + auditor ruling.
        c2, inv2 = self.frozen(2, composition="second")
        self.invent(c2, inv2)
        self.record(c2, self.make_reviewer(c2, inv2, "reviewer-01-facts"))
        self.record(c2, self.make_reviewer(c2, inv2, "reviewer-02-clarity"))
        self.record(
            c2,
            self.make_reviewer(c2, inv2, "reviewer-03-decision", grade="B+", action="Add internal SLA.", findings=[biz_finding]),
        )
        self.record(
            c2,
            self.make_audit(
                c2,
                escalation_rulings=[{
                    "finding": "biz-convention",
                    "target_reviewer": "reviewer-03-decision",
                    "verdict": "accepted_out_of_band",
                    "justification": "Confirmed with user via ask_user that internal SLA stays out-of-band.",
                    "overturned_topics": ["T01"],
                }],
            ),
        )
        evidence.register(
            self.root,
            "resolution",
            c2["candidate_id"],
            self.json("reports/fix-fact-c2.json", {
                "candidate_id": c2["candidate_id"],
                "finding": "unsupported-fact",
                "reviewer": "reviewer-01-facts",
                "correction": "Aligned with F16.",
                "reassessment": "Verified line 1 of F16.",
                "family_inspection": "Checked all route occurrences.",
                "occurrences": ["block-1", "block-3", "route-3"],
                "previous_occurrences": ["route-3"],
            }),
        )
        evidence.register(
            self.root,
            "resolution",
            c2["candidate_id"],
            self.json("reports/waive-biz-c2.json", {
                "candidate_id": c2["candidate_id"],
                "finding": "biz-convention",
                "status": "escalate",
                "decision_state": "decided",
                "effect": "waive_finding",
                "reviewer": self.auditor,
                "missing_decision": "Confidential internal SLA stays out-of-band.",
                "ask_user_question": "Keep confidential SLA out of public document?",
                "user_decision": "Yes, keep confidential SLA out of public document.",
            }),
        )
        rev_c2 = cycle_artifacts.consolidate(self.root, 2)
        dec_c2 = gate.evaluate_current(rev_c2, self.root)
        self.assertEqual(dec_c2["outcome"], "approved", f"measured={dec_c2}")
        report_md = final_report.render(self.root)
        self.assertIn("`biz-convention`: `escalate` (decided/waive_finding)", report_md)


if __name__ == "__main__":
    unittest.main()
