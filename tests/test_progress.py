from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.checks import gate, progress
from scripts.checks.common import InputError


class ProgressTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "fixture"
        for name in ("reports", "sources", "agents", "output"):
            (self.root / name).mkdir(parents=True)
        (self.root / "brief.md").write_text(
            '---\nswarm_id: fixture\nskill_version: "3.1.0"\nmode: document\n'
            'max_cycles: 3\ndemo: true\n---\n# Fixture\n', encoding="utf-8",
        )
        (self.root / "agents" / "author.md").write_text(
            "---\nname: author-01\nkind: author\nrole: Evidence\nmodel: auto\nswarm: fixture\n---\n",
            encoding="utf-8",
        )

    def review(self, grade="A", *, cycle=1, maximum=3, critical=False):
        data = {
            "cycle": cycle, "max_cycles": maximum,
            "topics": [{"topico": "T01", "title": "Evidence", "nota_minima": grade,
                        "revisor_da_minima": "reviewer-01", "bloqueia": False}],
            "rubberduck": {"critico": critical, "achados": []},
        }
        path = self.root / "reports" / f"cycle-{cycle:02d}-review.yaml"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def recorded_gate(self, path):
        output = path.with_name(path.name.replace("review.yaml", "gate.json"))
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = gate.main([str(path), "--output", str(output)])
        return code, json.loads(output.read_text(encoding="utf-8"))

    def individual(self, reviewer, grade, *, cycle=1):
        data = {"schema_version": 1, "cycle": cycle, "reviewer": reviewer, "topics": [
            {"topic": "T01", "grade": grade, "justification": "Evidence is bounded.", "action": ""},
        ]}
        path = self.root / "reports" / f"cycle-{cycle:02d}-{reviewer}.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_new_swarm_is_pending_not_approved_and_reader_is_read_only(self):
        before = sorted(str(path.relative_to(self.root)) for path in self.root.rglob("*"))
        data = progress.snapshot(self.root)
        self.assertEqual(data["cycles"], [])
        self.assertEqual(data["sources"]["status"], "pending")
        self.assertEqual(data["agents"][0]["declared_model"], "auto")
        self.assertTrue(data["demo"])
        self.assertEqual(data["title"], "Fixture")
        self.assertEqual(before, sorted(str(path.relative_to(self.root)) for path in self.root.rglob("*")))

    def test_calculated_grade_does_not_claim_gate_was_executed(self):
        self.review()
        cycle = progress.snapshot(self.root)["cycles"][0]
        self.assertEqual(cycle["gate"]["status"], "not_recorded")
        self.assertEqual(cycle["individual_reviews"], "not_recorded")
        self.assertEqual(cycle["topics"][0]["grade"], "A")

    def test_record_preserves_exit_codes_and_is_bound_to_exact_bytes(self):
        for grade, critical, maximum, expected in (
            ("A", False, 3, 0), ("A-", False, 3, 1),
            ("B+", False, 1, 2), ("A+", True, 3, 1),
        ):
            with self.subTest(grade=grade, critical=critical, maximum=maximum):
                path = self.review(grade, critical=critical, maximum=maximum)
                code, record = self.recorded_gate(path)
                self.assertEqual(code, expected)
                self.assertEqual(record["exit_code"], expected)
                self.assertEqual(record["review_sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
                current = progress.snapshot(self.root)["cycles"][0]["gate"]
                self.assertEqual(current["status"], "verified")
                self.assertEqual(current["exit_code"], expected)
                path.write_bytes(path.read_bytes() + b"\n")
                stale = progress.snapshot(self.root)["cycles"][0]
                self.assertEqual(stale["gate"]["status"], "stale")
                self.assertFalse(stale["consistent"])

    def test_invalid_input_records_failure_and_output_cannot_replace_input(self):
        path = self.review()
        original = path.read_bytes()
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(gate.main([str(path), "--output", str(path)]), 3)
        self.assertEqual(path.read_bytes(), original)
        path.write_text("{broken", encoding="utf-8")
        code, record = self.recorded_gate(path)
        self.assertEqual(code, 3)
        self.assertIsNone(record["result"])
        self.assertTrue(record["error"])
        data = progress.snapshot(self.root)
        self.assertFalse(data["cycles"][0]["consistent"])
        self.assertNotEqual(data["cycles"][0]["gate"]["status"], "verified")

    def test_minimum_is_not_average_and_disagreement_is_visible(self):
        self.individual("reviewer-01", "B+")
        self.individual("reviewer-02", "A+")
        path = self.review("A")
        self.recorded_gate(path)
        cycle = progress.snapshot(self.root)["cycles"][0]
        self.assertFalse(cycle["consistent"])
        self.assertIn("individual minimum", " ".join(cycle["issues"]))
        path = self.review("B+")
        self.recorded_gate(path)
        cycle = progress.snapshot(self.root)["cycles"][0]
        self.assertTrue(cycle["consistent"])
        self.assertEqual(cycle["gate"]["outcome"], "rejected")
        self.assertEqual({row["grade"] for row in cycle["reviews"]}, {"A+", "B+"})

    def test_cycles_do_not_inherit_old_grades_and_sort_numerically(self):
        self.recorded_gate(self.review("A", cycle=2))
        self.individual("reviewer-01", "B+", cycle=10)
        data = progress.snapshot(self.root)
        self.assertEqual([item["cycle"] for item in data["cycles"]], [2, 10])
        self.assertEqual(data["cycles"][1]["topics"], [])
        self.assertEqual(data["cycles"][1]["gate"]["status"], "not_recorded")

    def test_malformed_review_and_source_counts_are_not_silently_accepted(self):
        self.individual("reviewer-01", "A")
        duplicate = self.root / "reports" / "cycle-01-reviewer-duplicate.json"
        duplicate.write_bytes((self.root / "reports" / "cycle-01-reviewer-01.json").read_bytes())
        (self.root / "sources" / "sources-check.json").write_text('{"counts":{"ok":true}}', encoding="utf-8")
        data = progress.snapshot(self.root)
        self.assertFalse(data["cycles"][0]["consistent"])
        self.assertEqual(data["sources"]["status"], "invalid")
        self.assertTrue(data["warnings"])

    def test_symlink_escape_is_not_read_or_published(self):
        outside = Path(self.temp.name) / "outside.md"
        outside.write_text("private content", encoding="utf-8")
        link = self.root / "output" / "escape.md"
        try:
            link.symlink_to(outside)
        except OSError:
            self.skipTest("symbolic links are unavailable")
        data = progress.snapshot(self.root)
        self.assertTrue(any("escapes swarm" in warning for warning in data["warnings"]))
        self.assertNotIn("output/escape.md", {item["path"] for item in data["artifacts"]})
        self.assertNotIn("private content", json.dumps(data))

    def test_oversized_input_is_rejected(self):
        path = self.root / "brief.md"
        path.write_bytes(b"x" * (progress.MAX_FILE_BYTES + 1))
        with self.assertRaises(InputError):
            progress.snapshot(self.root)

    def test_machine_json_survives_legacy_windows_output_encoding(self):
        title = "Demonstração · execução · revisão · ação 🚀"
        brief = self.root / "brief.md"
        brief.write_text(brief.read_text(encoding="utf-8").replace("# Fixture", f"# {title}"), encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(Path(progress.__file__)), str(self.root)],
            env={**os.environ, "PYTHONIOENCODING": "cp1252"}, capture_output=True, timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout.decode("ascii"))
        self.assertEqual(data["title"], title)

    def test_review_changed_during_projection_cannot_remain_approved(self):
        review = self.review("A")
        self.recorded_gate(review)
        old_hash = hashlib.sha256(review.read_bytes()).hexdigest()
        source = self.root / "sources" / "sources-check.json"
        source.write_text('{"counts":{"ok":1,"fail":0}}', encoding="utf-8")
        original = progress.bounded_file
        changed = False

        def mutate(root, path):
            nonlocal changed
            raw = original(root, path)
            if path == source and not changed:
                changed = True
                self.review("B+")
            return raw

        with patch.object(progress, "bounded_file", side_effect=mutate):
            data = progress.snapshot(self.root)
        cycle = data["cycles"][0]
        self.assertFalse(cycle["consistent"])
        self.assertEqual(cycle["gate"]["status"], "stale")
        artifact = next(item for item in data["artifacts"] if item["name"] == review.name)
        self.assertEqual(artifact["sha256"], old_hash)
        self.assertTrue(any("changed during observation" in warning for warning in data["warnings"]))
