from __future__ import annotations

import hashlib
import importlib.util
import contextlib
import copy
import io
import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from scripts.checks.common import InputError

HAS_PDF = all(importlib.util.find_spec(module) is not None for module in ("reportlab", "markdown_it", "pypdf", "pypdfium2", "PIL"))
ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(HAS_PDF, "Install optional requirements-pdf.txt to run PDF rendering tests")
class PdfEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from scripts.pdf.engine import render
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        cls.source = cls.root / "reference.md"
        text = (ROOT / "tests" / "fixtures" / "pdf" / "reference.md").read_text(encoding="utf-8")
        long_prose = "\n\n".join(
            f"Parágrafo sintético {number}. " +
            "O cálculo mantém a mesma unidade e explicita as condições de validade. "
            "A revisão precisa verificar o texto efetivamente entregue ao leitor. " * 5
            for number in range(1, 9)
        )
        text = text.replace("## Condições de adoção", long_prose + "\n\n## Condições de adoção")
        extra_rows = "\n".join(
            f"| Linha {number:03d} | Condição específica da linha {number:03d}, preservada na passagem de página. | Resultado {number:03d} com identificação literal. |"
            for number in range(1, 76)
        )
        text = text.replace("\n\n:::figure layers", "\n" + extra_rows + "\n\n:::figure layers")
        text = text.replace("A inspeção mecânica verifica", "Consulte a [primeira seção](#1-critérios-e-condições-do-exemplo).\n\nA inspeção mecânica verifica")
        cls.source.write_text(text, encoding="utf-8")
        cls.bundles = {}
        for profile in ("textbook", "technical-report"):
            destination = cls.root / profile
            result = render(cls.source, destination, profile, "pt-BR")
            if result["status"] != "pass":
                raise AssertionError(f"Valid {profile} fixture failed: {result['errors']}")
            cls.bundles[profile] = destination

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def setUp(self):
        self.work = Path(tempfile.mkdtemp(dir=self.root))
        self.addCleanup(shutil.rmtree, self.work)

    def bundle(self):
        result = self.work / "mutant"
        shutil.copytree(self.bundles["textbook"], result)
        return result

    def inspect(self, bundle):
        from scripts.pdf.engine import inspect
        return inspect(self.source, bundle)

    def mutate(self, bundle, callback):
        from pypdf import PdfReader, PdfWriter
        reader = PdfReader(bundle / "document.pdf")
        writer = PdfWriter()
        writer.clone_document_from_reader(reader)
        callback(writer)
        with (bundle / "mutant.pdf").open("wb") as stream:
            writer.write(stream)
        (bundle / "mutant.pdf").replace(bundle / "document.pdf")

    def test_profiles_match_source_and_produce_real_multipage_tables_and_navigation(self):
        from pypdf import PdfReader
        for name, bundle in self.bundles.items():
            with self.subTest(profile=name):
                report = json.loads((bundle / "inspection.json").read_text(encoding="utf-8"))
                manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
                self.assertEqual(report["status"], "pass")
                self.assertEqual(report["editorial_approval"], "not_evaluated")
                self.assertGreater(report["page_count"], 7)
                self.assertEqual(report["source_sha256"], hashlib.sha256(self.source.read_bytes()).hexdigest())
                self.assertEqual(report["pdf_sha256"], hashlib.sha256((bundle / "document.pdf").read_bytes()).hexdigest())
                self.assertEqual(report["manifest_sha256"], hashlib.sha256((bundle / "manifest.json").read_bytes()).hexdigest())
                self.assertGreater(report["justification"]["eligible_lines"], 15)
                self.assertGreaterEqual(report["justification"]["ratio"], .9)
                self.assertEqual(len(report["outline"]), 4)
                self.assertIn("https://docs.python.org/3/", report["links"])
                self.assertEqual(PdfReader(bundle / "document.pdf").trailer["/Root"]["/Lang"], "pt-BR")
                layout = json.loads((bundle / "layout.json").read_text(encoding="utf-8"))
                table_pages = {item["page"] for item in layout["placements"] if item["kind"] == "table"}
                self.assertGreater(len(table_pages), 2)
                for page in table_pages:
                    header = [item for item in layout["placements"] if item["page"] == page and "-r0-c" in item["id"]]
                    self.assertEqual(len(header), 3)
                self.assertEqual(len(report["previews"]), report["page_count"])
                for image in report["previews"]:
                    self.assertGreater(image["width"], 800)
                    self.assertGreater(image["height"], 1200)
                    self.assertEqual(hashlib.sha256((bundle / image["path"]).read_bytes()).hexdigest(), image["sha256"])
                self.assertEqual(manifest["profile"], name)

    def test_missing_text_is_detected_independently_of_the_hash_failure(self):
        from pypdf.generic import ContentStream, TextStringObject
        bundle = self.bundle()
        def change(writer):
            changed = False
            for page in writer.pages:
                content = ContentStream(page.get_contents(), writer)
                for operands, operator in content.operations:
                    if operator == b"Tj" and "Par" in str(operands[0]) and "sint" in str(operands[0]):
                        operands[0] = TextStringObject("")
                        changed = True
                        break
                page.replace_contents(content)
                if changed:
                    break
            self.assertTrue(changed, "The mutation must actually remove a source text run")
        self.mutate(bundle, change)
        report = self.inspect(bundle)
        self.assertEqual(report["status"], "fail")
        self.assertTrue(report["missing_text"])
        self.assertIn("missing_text", {error["code"] for error in report["errors"]})

    def test_operator_mutation_is_detected_in_literal_code(self):
        from pypdf.generic import ContentStream, TextStringObject
        bundle = self.bundle()
        def change(writer):
            changed = False
            for page in writer.pages:
                content = ContentStream(page.get_contents(), writer)
                for operands, operator in content.operations:
                    if operator == b"Tj" and "return demand / useful" in str(operands[0]):
                        operands[0] = TextStringObject(str(operands[0]).replace(" / ", " * ", 1))
                        changed = True
                page.replace_contents(content)
            self.assertTrue(changed)
        self.mutate(bundle, change)
        report = self.inspect(bundle)
        self.assertIn("changed_operators", {item["code"] for item in report["errors"]})
        self.assertTrue(any(item["kind"] == "code" for item in report["changed_operators"]))

    def test_formula_operator_loss_is_detected_in_the_actual_pdf(self):
        from pypdf.generic import ContentStream, TextStringObject
        bundle = self.bundle()
        def change(writer):
            changed = False
            for page in writer.pages:
                content = ContentStream(page.get_contents(), writer)
                for operands, operator in content.operations:
                    if operator == b"Tj" and " + R" in str(operands[0]):
                        operands[0] = TextStringObject(str(operands[0]).replace(" + R", " R"))
                        changed = True
                page.replace_contents(content)
            self.assertTrue(changed, "The mutant must remove the formula's plus operator")
        self.mutate(bundle, change)
        report = self.inspect(bundle)
        self.assertTrue(any(item["kind"] == "formula" for item in report["changed_operators"]))
        self.assertIn("changed_operators", {item["code"] for item in report["errors"]})

    def test_cut_page_is_not_mistaken_for_a_shorter_valid_document(self):
        bundle = self.bundle()
        self.mutate(bundle, lambda writer: writer.remove_page(len(writer.pages) - 1))
        report = self.inspect(bundle)
        self.assertIn("missing_layout_page", {item["code"] for item in report["errors"]})
        self.assertTrue(report["missing_text"])
        self.assertEqual(len(list((bundle / "previews").glob("*.png"))), report["page_count"])

    def test_table_cell_loss_is_detected_on_a_continuation_page(self):
        from pypdf.generic import ContentStream, TextStringObject
        bundle = self.bundle()
        def change(writer):
            changed = False
            for page in writer.pages:
                content = ContentStream(page.get_contents(), writer)
                for operands, operator in content.operations:
                    if operator == b"Tj" and "Resultado 042" in str(operands[0]):
                        operands[0] = TextStringObject("")
                        changed = True
                page.replace_contents(content)
            self.assertTrue(changed, "The actual continued table cell must be erased")
        self.mutate(bundle, change)
        report = self.inspect(bundle)
        self.assertTrue(any(item["kind"] == "table-cell" and "Resultado 042" in item["expected"]
                            for item in report["missing_text"]))

    def test_removed_word_spacing_is_detected_from_rendered_lines(self):
        from pypdf.generic import ContentStream, NumberObject
        bundle = self.bundle()
        def change(writer):
            changed = 0
            for page in writer.pages:
                content = ContentStream(page.get_contents(), writer)
                for operands, operator in content.operations:
                    if operator == b"Tw" and operands[0] > 0:
                        operands[0] = NumberObject(0)
                        changed += 1
                page.replace_contents(content)
            self.assertGreater(changed, 15)
        self.mutate(bundle, change)
        report = self.inspect(bundle)
        self.assertIn("nonjustified_prose", {item["code"] for item in report["errors"]})
        self.assertLess(report["justification"]["ratio"], .9)

    def test_invisible_text_on_white_is_detected_even_though_it_extracts(self):
        from reportlab.pdfgen import canvas
        from reportlab.lib.pagesizes import A4
        from pypdf import PdfReader
        bundle = self.bundle()
        overlay = io.BytesIO()
        c = canvas.Canvas(overlay, pagesize=A4)
        c.setFillColorRGB(1, 1, 1)
        c.drawString(70, 90, "Invisible glyph control")
        c.save()
        overlay.seek(0)
        self.mutate(bundle, lambda writer: writer.pages[-1].merge_page(PdfReader(overlay).pages[0]))
        self.assertIn("Invisible glyph control", PdfReader(bundle / "document.pdf").pages[-1].extract_text())
        report = self.inspect(bundle)
        self.assertIn("invisible_text", {item["code"] for item in report["errors"]})
        self.assertTrue(report["invisible_text"])

    def test_out_of_margin_text_is_detected_from_actual_pdf_geometry(self):
        from reportlab.pdfgen import canvas
        from reportlab.lib.pagesizes import A4
        from pypdf import PdfReader
        bundle = self.bundle()
        overlay = io.BytesIO()
        c = canvas.Canvas(overlay, pagesize=A4)
        c.drawString(5, 90, "Outside allowed margin")
        c.save()
        overlay.seek(0)
        self.mutate(bundle, lambda writer: writer.pages[-1].merge_page(PdfReader(overlay).pages[0]))
        report = self.inspect(bundle)
        self.assertIn("out_of_bounds", {item["code"] for item in report["errors"]})
        self.assertTrue(report["out_of_bounds"])

    def test_erased_bar_is_detected_from_pixels_not_existing_vector_objects(self):
        from reportlab.pdfgen import canvas
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.colors import HexColor
        from pypdf import PdfReader
        bundle = self.bundle()
        layout = json.loads((bundle / "layout.json").read_text(encoding="utf-8"))
        bar = next(item for item in layout["placements"] if item["kind"] == "bar")
        x0, y0, x1, y1 = bar["bbox"]
        overlay = io.BytesIO()
        c = canvas.Canvas(overlay, pagesize=A4)
        c.setFillColor(HexColor("#f7fafc"))
        c.rect(x0 - 2, A4[1] - y1 - 2, x1 - x0 + 4, y1 - y0 + 4, stroke=0, fill=1)
        c.save()
        overlay.seek(0)
        self.mutate(bundle, lambda writer: writer.pages[bar["page"] - 1].merge_page(PdfReader(overlay).pages[0]))
        report = self.inspect(bundle)
        self.assertTrue(any(item["id"] == bar["id"] for item in report["figure_failures"]))
        self.assertIn("missing_figure_ink", {item["code"] for item in report["errors"]})

    def test_literal_code_entities_and_math_are_preserved_without_windows_fonts(self):
        text = (self.bundles["textbook"] / "editorial-text.txt").read_text(encoding="utf-8")
        for literal in ("<= 0", "!= 0", "<tag>", "& ação", "N ≥", "⌈D / C⌉", "∧"):
            self.assertIn(literal, text)
        manifest = json.loads((ROOT / "scripts" / "pdf" / "fonts" / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(len([item for item in manifest["files"] if item["file"].endswith(".ttf")]), 8)
        for item in manifest["files"]:
            font = ROOT / "scripts" / "pdf" / "fonts" / item["file"]
            self.assertEqual(hashlib.sha256(font.read_bytes()).hexdigest(), item["sha256"])
            if item["file"].endswith("OFL.txt"):
                self.assertIn("SIL OPEN FONT LICENSE Version 1.1", font.read_text(encoding="utf-8"))

    def test_unsafe_or_unsupported_sources_fail_without_replacing_existing_outputs(self):
        from scripts.pdf.engine import render
        from scripts.pdf.source import read_source
        for body in ("# Title\n\n![Remote](https://example.test/image.png)",
                     "# Title\n\n:::figure unknown\n{}\n:::",
                     '# Title\n\n:::figure invalid\n{"kind": []}\n:::',
                     "# Title\n\n<!-- check: -->",
                     "# Title\n\n<!-- Internal author instruction -->",
                     "# Title\n\nVisible text <!-- internal aside -->.",
                     "# Title\n\n<div>Unsupported HTML</div>",
                     "# Title\n\n:::not-supported",
                     "# Title\n\n[Broken](#absent)"):
            source = self.work / "bad.md"
            source.write_text(body, encoding="utf-8")
            with self.subTest(body=body), self.assertRaises(InputError):
                read_source(source)
        with self.assertRaises(InputError):
            render(self.source, self.bundles["textbook"])
        source = self.work / "wide.md"
        source.write_text("# Wide\n\n```\n" + "x" * 250 + "\n```\n", encoding="utf-8")
        with self.assertRaises(InputError):
            render(source, self.work / "wide")
        self.assertFalse((self.work / "wide").exists())

    def test_bundle_can_move_and_inspect_on_paths_with_spaces_and_accents(self):
        from scripts.pdf.engine import inspect
        bundle = self.work / "saída portátil"
        shutil.copytree(self.bundles["textbook"], bundle)
        source = self.work / "fonte relocada.md"
        shutil.copy2(self.source, source)
        report = inspect(source, bundle, "textbook", "pt-BR")
        self.assertEqual(report["status"], "pass")
        with self.assertRaises(InputError):
            inspect(source, bundle, "technical-report", "pt-BR")
        self.assertNotIn(str(self.root), (bundle / "manifest.json").read_text(encoding="utf-8"))

    def test_table_check_annotations_remain_metadata_not_customer_text(self):
        from scripts.pdf.engine import render
        from scripts.pdf.source import read_source
        source = self.work / "annotated.md"
        source.write_text(
            '# Inventory\n\n<!-- check: sum column="Value" target=3 -->\n'
            '| Value |\n|---:|\n| 1 |\n| 2 |\n', encoding="utf-8",
        )
        document = read_source(source)
        self.assertFalse(any("<!--" in item["text"] for item in document.expected.values()))
        result = render(source, self.work / "annotated")
        self.assertEqual(result["status"], "pass")
        self.assertNotIn("check:", (self.work / "annotated" / "editorial-text.txt").read_text(encoding="utf-8"))

    def test_inspection_does_not_overwrite_hardlinked_inputs_or_publish_partial_results(self):
        from scripts.pdf.engine import inspect
        bundle = self.bundle()
        original_source = self.source.read_bytes()
        text = bundle / "editorial-text.txt"
        text.unlink()
        os.link(self.source, text)
        preview = bundle / "previews" / "page-001.png"
        preview.unlink()
        os.link(self.source, preview)
        result = inspect(self.source, bundle)
        self.assertEqual(result["status"], "pass")
        self.assertEqual(self.source.read_bytes(), original_source)
        self.assertFalse(os.path.samefile(text, self.source))
        before = {path: path.read_bytes() for path in (text, preview, bundle / "inspection.json")}
        def interrupt(document, pdf, layout, destination):
            destination.mkdir()
            (destination / "page-001.png").write_bytes(b"incomplete")
            raise InputError("deliberate interrupted inspection")
        with patch("scripts.pdf.engine.inspect_pdf", side_effect=interrupt), self.assertRaises(InputError):
            inspect(self.source, bundle)
        self.assertEqual({path: path.read_bytes() for path in before}, before)
        self.assertFalse(list(bundle.glob(".docswarm-inspect-*")))

    def test_invalid_bundle_metadata_is_rejected_before_touching_outputs(self):
        from scripts.pdf.engine import inspect
        bundle = self.bundle()
        manifest_path = bundle / "manifest.json"
        original = manifest_path.read_text(encoding="utf-8")
        report_bytes = (bundle / "inspection.json").read_bytes()
        for field, value in (("layout", {}), ("source", None), ("schema_version", True)):
            manifest = json.loads(original)
            manifest[field] = value
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.subTest(field=field), self.assertRaises(InputError):
                inspect(self.source, bundle)
        manifest = json.loads(original)
        (bundle / "layout.json").write_text("[]", encoding="utf-8")
        manifest["layout"]["sha256"] = hashlib.sha256((bundle / "layout.json").read_bytes()).hexdigest()
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaises(InputError):
            inspect(self.source, bundle)
        self.assertEqual((bundle / "inspection.json").read_bytes(), report_bytes)

    def test_actual_pdf_bundle_binds_gate_report_monitor_and_memory(self):
        from scripts.checks import final_report, gate, progress, update_memory
        swarm = self.work / "swarm"
        for name in ("reports", "agents", "output", "sources"):
            (swarm / name).mkdir(parents=True)
        source = swarm / "output" / "reference.md"
        shutil.copy2(self.source, source)
        bundle = swarm / "output" / "pdf"
        shutil.copytree(self.bundles["textbook"], bundle)
        def ref(path):
            return {"path": path.relative_to(swarm).as_posix(), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        (swarm / "brief.md").write_text(
            '---\nswarm_id: pdf-fixture\nskill_version: "3.3.0"\nmode: document\nmax_cycles: 3\n'
            'quality_contract: editorial-v1\neditorial_reviewer: reviewer-01-clarity\npdf_engine: reportlab-v1\n'
            'deliverables:\n  - output/reference.md\n  - output/pdf/document.pdf\n---\n'
            '# Synthetic integration fixture; editorial judgments below are annotated test data.\n',
            encoding="utf-8",
        )
        (swarm / "agents" / "reviewer.md").write_text(
            '---\nname: reviewer-01-clarity\nkind: reviewer\nrole: Clareza\n'
            'evidence_class: form\nmodel: auto\nswarm: pdf-fixture\n---\n# Annotated fixture\n',
            encoding="utf-8",
        )
        quotes = [
            "Capacidade e operação de uma plataforma", "O relatório compara capacidade, demanda",
            "A reserva operacional permanece explícita.", "Valores em unidades sintéticas.",
            "Essa inspeção não atribui nota editorial",
        ]
        data = {
            "schema_version": 1, "skill_version": "3.3.0", "cycle": 1, "max_cycles": 3,
            "topics": [{"topico": "T01", "nota_minima": "A", "revisor_da_minima": "reviewer-01-clarity", "bloqueia": False}],
            "rubberduck": {"critico": False, "achados": []},
            "editorial": {
                "schema_version": 1, "cycle": 1, "reviewer": "reviewer-01-clarity", "scope": "full_document",
                "text": ref(bundle / "editorial-text.txt"),
                "artifacts": [ref(source), ref(bundle / "document.pdf")],
                "surfaces": [{
                    "surface": surface, "grade": "A", "location": surface, "quote": quote,
                    "justification": "Annotated fixture judgment: explicit object and conditions; not inferred from mechanical inspection.",
                    "action": "",
                } for surface, quote in zip(gate.EDITORIAL_SURFACES, quotes)],
                "findings": [],
            },
            "pdf_inspections": [{
                "source": ref(source), "pdf": ref(bundle / "document.pdf"),
                "manifest": ref(bundle / "manifest.json"), "inspection": ref(bundle / "inspection.json"),
            }],
        }
        review = swarm / "reports" / "cycle-01-review.yaml"
        individual = review.with_name("cycle-01-reviewer-01-clarity.json")
        def save(value):
            review.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
            individual.write_text(json.dumps({
                "schema_version": 1, "cycle": 1, "reviewer": "reviewer-01-clarity",
                "topics": [{"topic": "T01", "grade": "A", "justification": "Annotated fixture", "action": ""}],
                "editorial": value["editorial"],
            }, ensure_ascii=False), encoding="utf-8")
        save(data)
        (swarm / "reports" / "cycle-01-tables-check.json").write_text('{"failures":0}', encoding="utf-8")
        (swarm / "sources" / "sources-check.json").write_text('{"counts":{"ok":1,"redirect":0,"warn":0,"fail":0}}', encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(gate.main([str(review), "--output", str(review.with_name("cycle-01-gate.json"))]), 0)
        self.assertIn("PDF mechanical inspections", final_report.render(swarm))
        self.assertTrue(update_memory.approved(swarm))
        self.assertEqual(progress.snapshot(swarm)["cycles"][0]["gate"]["status"], "verified")
        for path in (source, bundle / "document.pdf", bundle / "previews" / "page-001.png",
                     bundle / "manifest.json", bundle / "inspection.json", bundle / "layout.json"):
            original = path.read_bytes()
            with self.subTest(artifact=path.name):
                try:
                    path.write_bytes(original + b" ")
                    with self.assertRaises(InputError):
                        gate.evaluate_current(data, swarm)
                    with self.assertRaises(InputError):
                        final_report.render(swarm)
                    self.assertFalse(update_memory.approved(swarm))
                    self.assertEqual(progress.snapshot(swarm)["cycles"][0]["gate"]["status"], "stale")
                finally:
                    path.write_bytes(original)
        missing = copy.deepcopy(data)
        missing.pop("pdf_inspections")
        with self.assertRaises(InputError):
            gate.evaluate_current(missing, swarm)
        below = copy.deepcopy(data)
        below["editorial"]["surfaces"][0].update(grade="B+", action="Resolve the annotated editorial issue.")
        save(below)
        decision = gate.evaluate_current(below, swarm)
        self.assertEqual(decision["outcome"], "rejected")
        self.assertTrue(all(item["kind"] == "editorial" for item in decision["blocked"]))
