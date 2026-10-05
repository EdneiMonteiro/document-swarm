from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.checks.common import InputError
from scripts.checks.gate import evaluate_current
from scripts.checks.pdf_contract import verify_pdf_inspections


class PdfContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bundle = self.root / "output" / "pdf"
        (self.bundle / "previews").mkdir(parents=True)
        self.source = self.root / "output" / "source.md"
        self.source.write_text("# Synthetic contract fixture\n", encoding="utf-8")
        (self.bundle / "document.pdf").write_bytes(b"%PDF-contract-fixture")
        (self.bundle / "layout.json").write_text("{}", encoding="utf-8")
        (self.bundle / "editorial-text.txt").write_text("Synthetic contract fixture", encoding="utf-8")
        (self.bundle / "previews" / "page-001.png").write_bytes(b"synthetic-preview-contract")
        self.manifest = {
            "schema_version": 1, "engine": "docswarm-pdf", "engine_version": "1.0",
            "profile": "textbook", "language": "pt-BR",
            "source": {"sha256": self.ref(self.source)["sha256"]},
            "pdf": self.ref(self.bundle / "document.pdf", self.bundle),
            "layout": self.ref(self.bundle / "layout.json", self.bundle),
        }
        self.write(self.bundle / "manifest.json", self.manifest)
        self.report = {
            "schema_version": 1, "inspector": "docswarm-pdf", "status": "pass",
            "editorial_approval": "not_evaluated", "errors": [], "page_count": 1,
            "pages": [{"page": 1}], "previews": [{**self.ref(self.bundle / "previews" / "page-001.png", self.bundle), "page": 1}],
            "source_sha256": self.ref(self.source)["sha256"],
            "pdf_sha256": self.ref(self.bundle / "document.pdf")["sha256"],
            "layout_sha256": self.ref(self.bundle / "layout.json")["sha256"],
            "manifest_sha256": self.ref(self.bundle / "manifest.json")["sha256"],
            "editorial_text": self.ref(self.bundle / "editorial-text.txt", self.bundle),
        }
        self.brief = {"skill_version": "3.3.0", "deliverables": ["output/source.md", "output/pdf/document.pdf"]}
        self.refresh()

    def ref(self, file, base=None):
        return {"path": file.relative_to(base or self.root).as_posix(), "sha256": hashlib.sha256(file.read_bytes()).hexdigest()}

    def write(self, file, data):
        file.write_text(json.dumps(data), encoding="utf-8")

    def refresh(self):
        self.write(self.bundle / "inspection.json", self.report)
        self.review = {"pdf_inspections": [{
            "source": self.ref(self.source), "pdf": self.ref(self.bundle / "document.pdf"),
            "manifest": self.ref(self.bundle / "manifest.json"),
            "inspection": self.ref(self.bundle / "inspection.json"),
        }]}

    def test_passed_report_is_hash_bound_without_pdf_dependencies(self):
        self.assertEqual(verify_pdf_inspections(self.review, self.root, self.brief), [])
        (self.bundle / "document.pdf").write_bytes(b"%PDF-modified")
        with self.assertRaisesRegex(InputError, "stale"):
            verify_pdf_inspections(self.review, self.root, self.brief)

    def test_mechanical_failure_blocks_without_awarding_editorial_grades(self):
        self.report["status"] = "fail"
        self.report["errors"] = [{"code": "missing_text", "count": 1}]
        self.refresh()
        blockers = verify_pdf_inspections(self.review, self.root, self.brief)
        self.assertEqual(blockers[0]["kind"], "pdf")
        self.assertEqual(blockers[0]["grade"], "")
        self.report["status"] = "pass"
        self.refresh()
        with self.assertRaisesRegex(InputError, "contradicts"):
            verify_pdf_inspections(self.review, self.root, self.brief)

    def test_missing_new_reports_fail_but_legacy_documents_remain_supported(self):
        with self.assertRaises(InputError):
            verify_pdf_inspections({}, self.root, self.brief)
        legacy = {**self.brief, "skill_version": "3.2.2"}
        self.assertEqual(verify_pdf_inspections({}, self.root, legacy), [])
        self.assertEqual(verify_pdf_inspections({}, self.root, {"skill_version": "3.3.0", "deliverables": ["output/source.md"]}), [])

    def test_altered_preview_or_false_editorial_success_is_rejected(self):
        self.report["editorial_approval"] = "approved"
        self.refresh()
        with self.assertRaisesRegex(InputError, "must not claim"):
            verify_pdf_inspections(self.review, self.root, self.brief)
        self.report["editorial_approval"] = "not_evaluated"
        self.refresh()
        (self.bundle / "previews" / "page-001.png").write_bytes(b"changed")
        with self.assertRaisesRegex(InputError, "stale"):
            verify_pdf_inspections(self.review, self.root, self.brief)

    def test_changed_source_missing_pages_and_wrong_pdf_set_are_rejected(self):
        self.source.write_text("Changed source", encoding="utf-8")
        with self.assertRaises(InputError):
            verify_pdf_inspections(self.review, self.root, self.brief)
        self.source.write_text("# Synthetic contract fixture\n", encoding="utf-8")
        self.report["previews"] = []
        self.refresh()
        with self.assertRaises(InputError):
            verify_pdf_inspections(self.review, self.root, self.brief)
        with self.assertRaises(InputError):
            verify_pdf_inspections({"pdf_inspections": []}, self.root, self.brief)

    def test_invalid_manifest_shapes_produce_input_errors(self):
        original = json.dumps(self.manifest)
        for field, value in (
            ("schema_version", True), ("source", []), ("pdf", {}), ("layout", None),
            ("profile", []), ("engine_version", "unknown"),
        ):
            with self.subTest(field=field):
                self.manifest = json.loads(original)
                self.manifest[field] = value
                self.write(self.bundle / "manifest.json", self.manifest)
                self.report["manifest_sha256"] = self.ref(self.bundle / "manifest.json")["sha256"]
                self.refresh()
                with self.assertRaises(InputError):
                    verify_pdf_inspections(self.review, self.root, self.brief)

    def test_one_preview_cannot_stand_in_for_multiple_pages(self):
        self.report["page_count"] = 2
        self.report["pages"].append({"page": 2})
        self.report["previews"].append({**self.report["previews"][0], "page": 2})
        self.refresh()
        with self.assertRaisesRegex(InputError, "distinct owned page images"):
            verify_pdf_inspections(self.review, self.root, self.brief)

    def test_invalid_inspection_shapes_fail_explicitly(self):
        original = json.dumps(self.report)
        for field, value in (("schema_version", True), ("status", []), ("errors", [None]), ("page_count", 0)):
            with self.subTest(field=field):
                self.report = json.loads(original)
                self.report[field] = value
                self.refresh()
                with self.assertRaises(InputError):
                    verify_pdf_inspections(self.review, self.root, self.brief)

    def test_new_review_cannot_bypass_pdf_contract_with_an_old_brief(self):
        legacy = {**self.brief, "skill_version": "3.2.2"}
        with self.assertRaises(InputError):
            verify_pdf_inspections({"skill_version": "3.3.0"}, self.root, legacy)

    def test_recorded_pdf_contract_cannot_be_checked_without_its_swarm(self):
        review = {
            "cycle": 1, "max_cycles": 3, "pdf_inspections": [],
            "topics": [{"topico": "T01", "nota_minima": "A", "revisor_da_minima": "reviewer-01", "bloqueia": False}],
            "rubberduck": {"critico": False, "achados": []},
        }
        with self.assertRaisesRegex(InputError, "swarm brief and artifacts"):
            evaluate_current(review, None)
