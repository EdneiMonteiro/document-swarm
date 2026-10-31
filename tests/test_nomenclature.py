from __future__ import annotations

import contextlib
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.checks import inspect_nomenclature, progress
from scripts.checks.common import InputError


class NomenclatureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def source(self, text, name="document.md"):
        path = self.root / name
        path.write_text(text, encoding="utf-8")
        return path

    def test_lists_occurrences_without_inventing_meanings_or_approval(self):
        path = self.source("# E1: exemplo\n\nT1 e S1 usam API. W1 ocorre antes de T1.\n")
        report = inspect_nomenclature.inspect_file(path)
        by_token = {item["token"]: item for item in report["candidates"]}
        self.assertEqual(set(by_token), {"E1", "T1", "S1", "API", "W1"})
        self.assertEqual(by_token["E1"]["first_line"], 1)
        self.assertEqual(by_token["T1"]["first_line"], 3)
        self.assertEqual(len(by_token["T1"]["occurrences"]), 2)
        self.assertEqual(by_token["T1"]["kind_hint"], "letter_number")
        self.assertEqual(by_token["API"]["kind_hint"], "uppercase")
        self.assertTrue(all(item["meaning"] is None and item["definition_status"] == "not_assessed" for item in by_token.values()))
        self.assertEqual(report["status"], "inspection_only")
        self.assertEqual(report["editorial_approval"], "not_evaluated")
        self.assertEqual(report["document_sha256"], hashlib.sha256(path.read_bytes()).hexdigest())

    def test_internal_frontmatter_fenced_code_comments_and_urls_are_excluded(self):
        text = (
            "---\ntopic: T01\n---\n"
            "# Exemplo\n"
            "```python\nT2 = 'API'\n```\n"
            "~~~text\nW9\n~~~\n"
            "<!-- S4\nS5 -->\n"
            "[E1](https://example.test/T9/API)\n"
            "https://example.test/T8\n"
            "Texto com `T1` e E2-E3.\n"
        )
        report = inspect_nomenclature.inspect_file(self.source(text))
        by_token = {item["token"]: item for item in report["candidates"]}
        self.assertEqual(set(by_token), {"E1", "T1", "E2", "E3"})
        self.assertEqual(by_token["E1"]["first_line"], 13)
        self.assertEqual(by_token["T1"]["first_line"], 15)

    def test_extracted_text_is_inspected_without_markdown_frontmatter_rules(self):
        report = inspect_nomenclature.inspect_file(self.source("---\nE1\n---\nT1", "extracted.txt"))
        self.assertEqual({item["token"] for item in report["candidates"]}, {"E1", "T1"})

    def test_zero_candidates_does_not_imply_editorial_approval(self):
        report = inspect_nomenclature.inspect_file(self.source("Semana 1: validar o cenário.\n"))
        self.assertEqual(report["candidate_count"], 0)
        self.assertEqual(report["editorial_approval"], "not_evaluated")
        self.assertNotIn("grade", report)

    def test_uppercase_words_are_candidates_not_automatically_acronyms(self):
        report = inspect_nomenclature.inspect_file(self.source("RESUMO\nD8ds_v6\nSLO\n"))
        self.assertEqual({item["token"] for item in report["candidates"]}, {"RESUMO", "SLO"})
        self.assertTrue(all(item["definition_status"] == "not_assessed" for item in report["candidates"]))

    def test_binary_input_limits_and_invalid_utf8_fail_explicitly(self):
        with self.assertRaises(InputError):
            inspect_nomenclature.inspect_file(self.source("%PDF", "document.pdf"))
        path = self.source("T1 T2 T3")
        with patch.object(inspect_nomenclature, "MAX_OCCURRENCES", 2), self.assertRaises(InputError):
            inspect_nomenclature.inspect_file(path)
        with patch.object(inspect_nomenclature, "MAX_BYTES", 2), self.assertRaises(InputError):
            inspect_nomenclature.inspect_file(path)
        path.write_bytes(b"\xff")
        with self.assertRaises(UnicodeError):
            inspect_nomenclature.inspect_file(path)

    def test_report_cannot_replace_input_and_source_bytes_remain_unchanged(self):
        path = self.source("E1 depende de S1.\n")
        original = path.read_bytes()
        output = self.root / "reports" / "cycle-01-nomenclature.json"
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(inspect_nomenclature.main([str(path), "--output", str(path)]), 2)
            self.assertEqual(inspect_nomenclature.main([str(path), "--output", str(output)]), 0)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["candidate_count"], 2)

    def test_bad_input_does_not_write_a_success_shaped_report(self):
        output = self.root / "inspection.json"
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(inspect_nomenclature.main([str(self.root / "missing.md"), "--output", str(output)]), 2)
        self.assertFalse(output.exists())

    def test_calibration_contains_both_opaque_and_defined_local_codes(self):
        path = Path(__file__).parent / "fixtures" / "editorial" / "nomenclature-calibration.json"
        reference = json.loads(path.read_text(encoding="utf-8"))
        cases = {item["id"]: item for item in reference["cases"]}
        self.assertEqual(len(cases), 9)
        self.assertEqual(cases["undefined-local-code"]["before_grade"], "B+")
        self.assertEqual(cases["defined-local-code-is-acceptable"]["before_grade"], "A")
        for key in ("undefined-local-code", "defined-local-code-is-acceptable"):
            report = inspect_nomenclature.inspect_file(self.source(cases[key]["before"]))
            self.assertIn("E1", {item["token"] for item in report["candidates"]})
            self.assertEqual(report["editorial_approval"], "not_evaluated")
        for item in cases.values():
            self.assertTrue(item["before_reason"] and item["after_reason"] and item["preserved_meaning"])
        self.assertEqual({item["surface"] for item in cases.values()}, {"titles", "openings", "body", "cards", "captions", "conclusions"})

    def test_monitor_lists_inspection_without_using_it_as_a_gate(self):
        root = self.root / "swarm"
        (root / "reports").mkdir(parents=True)
        (root / "brief.md").write_text("---\nswarm_id: swarm\nskill_version: \"3.2.2\"\nmax_cycles: 3\n---\n", encoding="utf-8")
        source = root / "reports" / "cycle-01-editorial-text.txt"
        source.write_text("E1 e T1.\n", encoding="utf-8")
        output = root / "reports" / "cycle-01-nomenclature.json"
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(inspect_nomenclature.main([str(source), "--output", str(output)]), 0)
        state = progress.snapshot(root)
        self.assertEqual(state["cycles"], [])
        self.assertIn(output.name, {item["name"] for item in state["artifacts"]})
