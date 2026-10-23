from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
import re
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

from scripts.checks import final_report, gate, progress, update_memory, verify_sources, verify_tables
from scripts.checks.common import InputError, parse_yaml
from tests.test_checks import SourceHandler

FIXTURES = Path(__file__).parent / "fixtures" / "editorial"


class EditorialTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "editorial-swarm"
        for folder in ("agents", "reports", "output", "sources"):
            (self.root / folder).mkdir(parents=True)
        (self.root / "brief.md").write_text(
            "---\nswarm_id: editorial-swarm\nskill_version: \"3.2.0\"\nmode: document\n"
            "max_cycles: 3\nquality_contract: editorial-v1\neditorial_reviewer: reviewer-01-clarity\n"
            "deliverables:\n  - output/document.md\n---\n# Editorial fixture\n", encoding="utf-8",
        )
        (self.root / "agents" / "reviewer.md").write_text(
            "---\nname: reviewer-01-clarity\nkind: reviewer\nrole: Clareza editorial\n"
            "evidence_class: form\nmodel: auto\nswarm: editorial-swarm\n---\n"
            "# Review the complete wording, independently of layout.\n", encoding="utf-8",
        )

    def descriptor(self, path):
        return {"path": path.relative_to(self.root).as_posix(), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}

    def cycle(self, number=1, *, generic=False):
        text = (FIXTURES / ("generic.md" if generic else "specific.md")).read_text(encoding="utf-8")
        document = self.root / "output" / "document.md"
        frozen = self.root / "reports" / f"cycle-{number:02d}-editorial-text.txt"
        document.write_text(text, encoding="utf-8")
        frozen.write_text(text, encoding="utf-8")
        quotes = (
            ["Dimensionar melhor. Operar com critério.", "A decisão em uma página",
             "Não confundir candidato com destino.", "Cores e rótulos acompanham o leitor",
             "Decidir com clareza. Operar com confiança."]
            if generic else
            ["Dimensionamento dos node pools do AKS", "O estudo compara capacidade alocável",
             "Os requests dos pods são comparados à capacidade alocável.",
             "Capacidade alocável e soma dos requests", "Não altere produção sem essa validação."]
        )
        surfaces = [{
            "surface": surface, "grade": "B+" if generic else "A",
            "location": f"section {index + 1}", "quote": quote,
            "justification": "Generic wording omits its technical object." if generic else "The wording identifies the object and preserves the relevant condition.",
            "action": "Replace the rhetorical wording with the specific object and condition." if generic else "",
        } for index, (surface, quote) in enumerate(zip(gate.EDITORIAL_SURFACES, quotes))]
        editorial = {
            "schema_version": 1, "reviewer": "reviewer-01-clarity", "cycle": number,
            "scope": "full_document", "text": self.descriptor(frozen),
            "artifacts": [self.descriptor(document)], "surfaces": surfaces, "findings": [],
        }
        data = {
            "schema_version": 1, "skill_version": "3.2.0", "quality_contract": "editorial-v1",
            "cycle": number, "max_cycles": 3,
            "topics": [{"topico": "T01", "nota_minima": "A", "revisor_da_minima": "reviewer-01-clarity", "bloqueia": False}],
            "rubberduck": {"critico": False, "achados": []}, "editorial": editorial,
        }
        self.save(data)
        return data

    def save(self, data, *, reviewer=True):
        path = self.root / "reports" / f"cycle-{data['cycle']:02d}-review.yaml"
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        if reviewer and "editorial" in data:
            report = {
                "schema_version": 1, "cycle": data["cycle"], "reviewer": "reviewer-01-clarity",
                "topics": [{"topic": "T01", "grade": "A", "justification": "Technical coverage preserved.", "action": ""}],
                "editorial": data["editorial"],
            }
            (path.parent / f"cycle-{data['cycle']:02d}-reviewer-01-clarity.json").write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
        return path

    def run_gate(self, data):
        path = self.save(data)
        output = path.with_name(f"cycle-{data['cycle']:02d}-gate.json")
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = gate.main([str(path), "--output", str(output)])
        return code, json.loads(output.read_text(encoding="utf-8"))

    def test_generic_copy_blocks_even_when_topics_and_layout_are_a(self):
        data = self.cycle(generic=True)
        data["visual_grade"] = "A+"
        result = gate.evaluate_current(data, self.root)
        self.assertEqual(result["outcome"], "rejected")
        self.assertEqual({item["name"] for item in result["blocked"]}, set(gate.EDITORIAL_SURFACES))
        self.assertTrue(all(item["kind"] == "editorial" for item in result["blocked"]))
        self.assertEqual(self.run_gate(data)[0], 1)
        data["max_cycles"] = 1
        self.assertEqual(self.run_gate(data)[0], 2)

    def test_specific_copy_passes_without_banning_legitimate_negative_sentences(self):
        data = self.cycle()
        self.assertEqual(gate.evaluate_current(data, self.root)["outcome"], "approved")
        self.assertEqual(self.run_gate(data)[0], 0)
        self.assertIn("Não altere produção", data["editorial"]["surfaces"][-1]["quote"])

    def test_partial_inherited_missing_and_layout_only_reviews_are_invalid(self):
        original = self.cycle()
        cases = []
        missing = copy.deepcopy(original)
        del missing["editorial"]
        cases.append(missing)
        for scope in ("targeted", "layout", "inherited", ""):
            data = copy.deepcopy(original)
            data["editorial"]["scope"] = scope
            cases.append(data)
        inherited = copy.deepcopy(original)
        inherited["editorial"]["cycle"] = 0
        cases.append(inherited)
        partial = copy.deepcopy(original)
        partial["editorial"]["surfaces"].pop()
        cases.append(partial)
        for data in cases:
            with self.subTest(editorial=data.get("editorial")):
                with self.assertRaises(InputError):
                    gate.evaluate_current(data, self.root)

    def test_new_brief_prevents_legacy_review_downgrade(self):
        data = self.cycle()
        del data["editorial"]
        del data["quality_contract"]
        data["skill_version"] = "3.1.0"
        self.assertEqual(gate.evaluate(data)["outcome"], "approved")
        self.assertEqual(self.run_gate(data)[0], 3)

    def test_blocking_finding_cannot_be_hidden_by_high_grades(self):
        data = self.cycle()
        data["editorial"]["findings"] = [{
            "severity": "blocking", "location": "title", "quote": "Dimensionamento",
            "reason": "Fixture explicitly records an unresolved editorial defect.",
            "action": "Resolve it and re-review.",
        }]
        self.save(data)
        result = gate.evaluate_current(data, self.root)
        self.assertEqual(result["outcome"], "rejected")
        self.assertEqual(result["blocked"][0]["kind"], "editorial")

    def test_current_text_deliverables_and_reviewer_json_are_bound(self):
        for change in ("text", "delivery", "quote", "individual", "reviewer", "missing-output"):
            data = self.cycle()
            with self.subTest(change=change):
                if change == "text":
                    (self.root / data["editorial"]["text"]["path"]).write_text("Changed wording", encoding="utf-8")
                elif change == "delivery":
                    (self.root / "output" / "document.md").write_text("Added a slogan after review", encoding="utf-8")
                elif change == "quote":
                    data["editorial"]["surfaces"][0]["quote"] = "A quote not present anywhere."
                    self.save(data)
                elif change == "individual":
                    self.save({**data, "editorial": {**data["editorial"], "scope": "targeted"}})
                elif change == "reviewer":
                    data["editorial"]["reviewer"] = "reviewer-02-other"
                else:
                    brief = self.root / "brief.md"
                    brief.write_text(brief.read_text(encoding="utf-8").replace("  - output/document.md", "  - output/document.md\n  - output/document.pdf"), encoding="utf-8")
                with self.assertRaises((InputError, OSError)):
                    gate.evaluate_current(data, self.root)

    def test_invalid_surface_shapes_and_missing_justifications_fail_closed(self):
        original = self.cycle()
        for change in ("duplicate", "object-id", "empty-quote", "empty-reason", "not-applicable-body", "bool-schema"):
            data = copy.deepcopy(original)
            with self.subTest(change=change):
                item = data["editorial"]["surfaces"][0]
                if change == "duplicate":
                    data["editorial"]["surfaces"].append(copy.deepcopy(item))
                elif change == "object-id":
                    item["surface"] = {}
                elif change == "empty-quote":
                    item["quote"] = " "
                elif change == "empty-reason":
                    item["justification"] = ""
                elif change == "not-applicable-body":
                    item.pop("grade")
                    item["not_applicable"] = "Already approved."
                else:
                    data["editorial"]["schema_version"] = True
                with self.assertRaises(InputError):
                    gate.evaluate(data)

    def test_absent_captions_need_a_reason_but_no_invented_grade(self):
        data = self.cycle()
        document = self.root / "output" / "document.md"
        frozen = self.root / data["editorial"]["text"]["path"]
        body = document.read_text(encoding="utf-8").replace(
            "Figura 1. Capacidade alocável e soma dos requests, medidas na mesma janela.\n", "",
        )
        document.write_text(body, encoding="utf-8")
        frozen.write_text(body, encoding="utf-8")
        data["editorial"]["text"] = self.descriptor(frozen)
        data["editorial"]["artifacts"] = [self.descriptor(document)]
        data["editorial"]["surfaces"][3] = {"surface": "captions", "not_applicable": "This variant has no figures or tables."}
        self.save(data)
        self.assertEqual(gate.evaluate_current(data, self.root)["outcome"], "approved")

    def test_artifact_paths_cannot_escape_the_swarm(self):
        for name in ("../outside.md", "/tmp/outside.md", r"C:\outside.md", r"output\..\..\outside.md", "output/null\0.md"):
            data = self.cycle()
            data["editorial"]["text"]["path"] = name
            with self.subTest(name=name), self.assertRaises(InputError):
                gate.evaluate_current(data, self.root)

    def test_boolean_cycles_are_not_integer_cycle_evidence(self):
        for field in ("cycle", "max_cycles"):
            data = self.cycle()
            data[field] = True
            with self.subTest(field=field), self.assertRaises(InputError):
                gate.evaluate(data)

    def test_changed_delivery_invalidates_monitor_report_and_memory(self):
        data = self.cycle()
        self.assertEqual(self.run_gate(data)[0], 0)
        (self.root / "reports" / "cycle-01-tables-check.json").write_text('{"failures":0}', encoding="utf-8")
        (self.root / "sources" / "sources-check.json").write_text('{"counts":{"ok":1,"redirect":0,"warn":0,"fail":0}}', encoding="utf-8")
        self.assertIn("Final editorial review", final_report.render(self.root))
        self.assertTrue(update_memory.approved(self.root))
        self.assertEqual(progress.snapshot(self.root)["cycles"][0]["gate"]["status"], "verified")
        (self.root / "output" / "document.md").write_text("Different final wording", encoding="utf-8")
        cycle = progress.snapshot(self.root)["cycles"][0]
        self.assertEqual(cycle["gate"]["status"], "stale")
        self.assertFalse(cycle["consistent"])
        self.assertFalse(update_memory.approved(self.root))
        with self.assertRaises(InputError):
            final_report.render(self.root)

    def test_two_cycle_fixture_runs_the_document_pipeline(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), SourceHandler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            lines = ["| ID | Title | Type | URL |", "| --- | --- | --- | --- |"]
            lines += [f"| F{number:02d} | Source {number} | Test | http://127.0.0.1:{server.server_port}/ok-{number} |" for number in range(1, 6)]
            index = self.root / "sources" / "sources-index.md"
            index.write_text("\n".join(lines), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                first = self.cycle(generic=True)
                self.assertEqual(self.run_gate(first)[0], 1)
                second = self.cycle(2)
                self.assertEqual(verify_sources.main([str(index), "--force", "--timeout", "2"]), 0)
                self.assertEqual(verify_tables.main([
                    str(self.root / "output" / "document.md"),
                    "--output", str(self.root / "reports" / "cycle-02-tables-check.json"),
                ]), 0)
                self.assertEqual(self.run_gate(second)[0], 0)
                self.assertEqual(final_report.main([str(self.root)]), 0)
                self.assertEqual(update_memory.main([str(self.root)]), 0)
            text = (self.root / "reports" / "final-report.md").read_text(encoding="utf-8")
            self.assertIn("Final editorial review", text)
            self.assertIn("**rejected**", text)
            self.assertIn("**approved**", text)
            self.assertTrue(json.loads((self.root / "reports" / "memory-proposal.json").read_text(encoding="utf-8"))["approved"])
            cycles = progress.snapshot(self.root)["cycles"]
            self.assertEqual([item["gate"]["outcome"] for item in cycles], ["rejected", "approved"])
        finally:
            server.shutdown()
            worker.join()
            server.server_close()

    def calibration_case(self, case, variant):
        data = self.cycle()
        document = self.root / "output" / "document.md"
        frozen = self.root / data["editorial"]["text"]["path"]
        body = document.read_text(encoding="utf-8")
        if case["surface"] == "titles":
            body = body.replace("# Dimensionamento dos node pools do AKS", f"# {case[variant]}", 1)
        elif case["surface"] == "openings":
            body = body.replace(
                "O estudo compara capacidade alocável, requests dos pods e condições de manutenção.",
                case[variant], 1,
            )
        elif case["surface"] == "captions":
            body = body.replace(
                "Figura 1. Capacidade alocável e soma dos requests, medidas na mesma janela.",
                f"Figura 1. {case[variant]}", 1,
            )
        elif case["surface"] == "conclusions":
            body = body.split("## Condições para adoção", 1)[0] + f"## Condições para adoção\n\n{case[variant]}\n"
        else:
            body += "\n\n" + ("> " if case["surface"] == "cards" else "") + case[variant] + "\n"
        document.write_text(body, encoding="utf-8")
        frozen.write_text(body, encoding="utf-8")
        data["editorial"]["text"] = self.descriptor(frozen)
        data["editorial"]["artifacts"] = [self.descriptor(document)]
        surface = "body" if case["surface"] == "cards" else case["surface"]
        item = next(item for item in data["editorial"]["surfaces"] if item["surface"] == surface)
        item.update({
            "grade": case[f"{variant}_grade"], "location": f"Calibration: {case['id']}",
            "quote": case[variant],
            "justification": " ".join(case[f"{variant}_analysis"].values()),
            "action": case["after"] if gate.GRADE_INDEX[case[f"{variant}_grade"]] < gate.GRADE_INDEX["A"] else "",
        })
        self.save(data)
        return data, item

    def test_language_calibration_is_explicitly_annotated_across_all_surfaces(self):
        reference = json.loads((FIXTURES / "language-calibration.json").read_text(encoding="utf-8"))
        self.assertIn("anotados", reference["purpose"])
        self.assertEqual(set(reference["required_lenses"]), {"language", "referents", "tone", "standalone"})
        self.assertEqual({case["surface"] for case in reference["cases"]}, set(reference["required_surfaces"]))
        for case in reference["cases"]:
            with self.subTest(case=case["id"]):
                self.assertTrue(case["technical_assessment"])
                self.assertTrue(case["preserved_meaning"])
                for variant in ("before", "after"):
                    self.assertEqual(set(case[f"{variant}_analysis"]), set(reference["required_lenses"]))
                    self.assertTrue(all(reason.strip() for reason in case[f"{variant}_analysis"].values()))
                    self.assertIn(case[f"{variant}_grade"], gate.SCALE)
        self.assertFalse(reference["audit_protocol"]["silent_grade_replacement"])
        self.assertEqual(reference["audit_protocol"]["action"], "return_to_responsible_reviewer")
        self.assertEqual(reference["audit_protocol"]["acceptance_while_unresolved"], "blocked")
        self.assertIn("editorial_quality", reference["audit_protocol"]["deterministic_checks_do_not_prove"])

    def test_annotated_wording_grades_are_independent_of_valid_architectural_topics(self):
        reference = json.loads((FIXTURES / "language-calibration.json").read_text(encoding="utf-8"))
        for case in reference["cases"]:
            for variant in ("before", "after"):
                with self.subTest(case=case["id"], variant=variant):
                    data, item = self.calibration_case(case, variant)
                    self.assertEqual(data["topics"][0]["nota_minima"], "A")
                    result = gate.evaluate_current(data, self.root)
                    expected = "approved" if gate.GRADE_INDEX[item["grade"]] >= gate.GRADE_INDEX["A"] else "rejected"
                    self.assertEqual(result["outcome"], expected)
                    self.assertNotEqual(item["justification"], case["insufficient_justification"])
        original = reference["cases"][0]
        self.assertEqual(original["before"], "AKS compartilhado com dependências segregadas. Não impor outro AKS só por isso.")
        self.assertEqual(original["before_grade"], "B+")
        for condition in ("plano de controle", "administração", "manutenção"):
            self.assertIn(condition, original["after"])

    def test_architectural_only_justification_needs_a_semantic_audit_not_keyword_detection(self):
        reference = json.loads((FIXTURES / "language-calibration.json").read_text(encoding="utf-8"))
        case = reference["cases"][0]
        data, item = self.calibration_case(case, "before")
        item["grade"] = "A"
        item["justification"] = case["insufficient_justification"]
        self.save(data)
        # Valid structure and hashes cannot establish the soundness of this judgment.
        self.assertEqual(gate.evaluate_current(data, self.root)["outcome"], "approved")
        original_item = copy.deepcopy(item)
        data["rubberduck"] = {"critico": True, "achados": [{
            "reviewer": "reviewer-01-clarity",
            "action": reference["audit_protocol"]["action"],
            "reason": reference["audit_cases"][1]["challenge"],
        }]}
        self.assertEqual(gate.evaluate_current(data, self.root)["outcome"], "rejected")
        self.assertEqual(item, original_item, "The auditor must not silently replace the reviewer's grade")

    def test_declaration_templates_preserve_the_guidance_stamp(self):
        skill = (Path(__file__).resolve().parents[1] / "SKILL.md").read_text(encoding="utf-8")
        headers = re.findall(r"```markdown\s*\n---\n(.*?)\n---(?:\n|$)", skill, re.S)
        declarations = [parse_yaml(header) for header in headers]
        self.assertCountEqual([item["kind"] for item in declarations], ["author", "reviewer", "coordinator", "rubber-duck"])
        self.assertTrue(all(item["editorial_guidance_version"] == "3.2.2" for item in declarations))

    def test_nomenclature_calibration_keeps_internal_ids_and_uses_annotated_judgments(self):
        reference = json.loads((FIXTURES / "nomenclature-calibration.json").read_text(encoding="utf-8"))
        for case in reference["cases"]:
            adapted = {
                **case,
                "before_analysis": {"nomenclature": case["before_reason"]},
                "after_analysis": {"nomenclature": case["after_reason"]},
            }
            for variant in ("before", "after"):
                with self.subTest(case=case["id"], variant=variant):
                    data, item = self.calibration_case(adapted, variant)
                    self.assertEqual(data["topics"][0]["topico"], "T01")
                    result = gate.evaluate_current(data, self.root)
                    expected = "approved" if gate.GRADE_INDEX[item["grade"]] >= gate.GRADE_INDEX["A"] else "rejected"
                    self.assertEqual(result["outcome"], expected)
