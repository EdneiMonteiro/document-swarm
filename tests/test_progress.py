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

    def review(self, grade="A", *, cycle=1, maximum=3, critical=False, approval_grade=None):
        data = {
            "cycle": cycle, "max_cycles": maximum,
            "topics": [{"topico": "T01", "title": "Evidence", "nota_minima": grade,
                        "revisor_da_minima": "reviewer-01", "bloqueia": False}],
            "rubberduck": {"critico": critical, "achados": []},
        }
        if approval_grade is not None:
            data["approval_grade"] = approval_grade
        path = self.root / "reports" / f"cycle-{cycle:02d}-review.yaml"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def brief(self, extra=""):
        (self.root / "brief.md").write_text(
            '---\nswarm_id: fixture\nskill_version: "3.1.0"\nmode: document\n'
            f'max_cycles: 3\ndemo: true\n{extra}---\n# Fixture\n', encoding="utf-8",
        )

    def plan(self, content):
        path = self.root / "reports" / "execution" / "plan.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content if isinstance(content, str) else json.dumps(content), encoding="utf-8")
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

    def test_a_cycle_carries_the_grade_its_own_review_was_judged_under(self):
        # The panel colours every grade against this; a fixed A would show a gate-approved A- as a failure.
        for declared, grade, bar, outcome in (
            (None, "A-", "A", "rejected"),
            ("A", "A-", "A", "rejected"),
            ("A-", "A-", "A-", "approved"),
            ("A-", "A", "A-", "approved"),
            ("A-", "B+", "A-", "rejected"),
        ):
            with self.subTest(declared=declared, grade=grade):
                self.brief("approval_grade: A-\n" if declared == "A-" else "")
                self.recorded_gate(self.review(grade, approval_grade=declared))
                cycle = progress.snapshot(self.root)["cycles"][0]
                self.assertEqual(cycle["approval_grade"], bar)
                self.assertEqual(cycle["gate"]["status"], "verified")
                self.assertEqual(cycle["gate"]["outcome"], outcome)

    def test_a_review_that_declares_no_grade_stays_under_a_in_a_swarm_that_relaxed_it(self):
        # What the gate applied is what the review says; the swarm's setting is only a default for a cycle with no review.
        self.brief("approval_grade: A-\n")
        self.recorded_gate(self.review("A-"))
        data = progress.snapshot(self.root)
        self.assertEqual(data["approval_grade"], "A-")
        self.assertEqual(data["cycles"][0]["approval_grade"], "A")
        self.assertEqual(data["cycles"][0]["gate"]["outcome"], "rejected")
        (self.root / "reports" / "cycle-01-review.yaml").unlink()
        (self.root / "reports" / "cycle-01-gate.json").unlink()
        self.individual("reviewer-01", "A-")
        self.assertEqual(progress.snapshot(self.root)["cycles"][0]["approval_grade"], "A-")

    def test_the_swarm_grade_is_resolved_in_the_order_the_executor_resolves_it(self):
        for brief, option, record, expected in (
            (None, None, None, "A"),
            ("A-", None, None, "A-"),
            (None, None, "A-", "A-"),
            ("A", None, "A-", "A"),
            ("A", "A-", "A", "A-"),
            ("A-", "A", "A-", "A"),
            ("B+", "A+", 7, "A"),
            ("B+", "A+", "A-", "A-"),
        ):
            with self.subTest(brief=brief, option=option, record=record):
                self.brief(f"approval_grade: {brief}\n" if brief else "")
                (self.root / "reports" / "execution" / "plan.json").unlink(missing_ok=True)
                if option is not None or record is not None:
                    self.plan({"approval_grade": record, "options": {"approval_grade": option}})
                data = progress.snapshot(self.root)
                self.assertEqual(data["approval_grade"], expected)
                self.assertEqual(data["warnings"], [], "a swarm with no plan, or a valid one, has nothing to warn about")

    def test_the_ceiling_is_the_one_a_person_set_with_the_option_not_the_briefs(self):
        for options, expected in (
            ({"max_cycles": 6}, 6),
            ({"max_cycles": 1}, 1),
            ({"max_cycles": None}, 3),
            ({}, 3),
            ({"max_cycles": 0}, 3),
            ({"max_cycles": -2}, 3),
            ({"max_cycles": True}, 3),
            ({"max_cycles": "6"}, 3),
            ({"max_cycles": 2.5}, 3),
        ):
            with self.subTest(options=options):
                # The plan's own record of the ceiling (99) is not what a person asked for and must not be shown.
                self.plan({"max_cycles": 99, "options": options})
                self.assertEqual(progress.snapshot(self.root)["max_cycles"], expected)
        self.plan({"max_cycles": 99, "options": "not an object"})
        self.assertEqual(progress.snapshot(self.root)["max_cycles"], 3)

    def test_a_review_cannot_declare_a_lower_bar_than_the_swarm_authorizes(self):
        # (what the brief says, what a person gave the executor, what the review declares, the grade of its topic) -> exit code
        for brief, option, declared, grade, expected in (
            (None, None, "A-", "A", 3),      # nothing authorizes A-: the review chose it by itself
            ("A", None, "A-", "A", 3),
            ("A-", None, "A-", "A", 0),      # the brief authorizes it
            ("A-", "A", "A-", "A", 3),       # a person tightened it with the executor's option
            (None, "A-", "A-", "A", 0),      # a person relaxed it with the option
            ("A", "A-", "A-", "A-", 0),
            ("A-", None, "A", "A-", 1),      # a stricter declaration is only stricter
            ("A-", None, None, "A", 0),      # and no declaration is the original A
        ):
            with self.subTest(brief=brief, option=option, declared=declared):
                self.brief(f"approval_grade: {brief}\n" if brief else "")
                (self.root / "reports" / "execution" / "plan.json").unlink(missing_ok=True)
                if option is not None:
                    self.plan({"approval_grade": option, "options": {"approval_grade": option}})
                code, record = self.recorded_gate(self.review(grade, approval_grade=declared))
                self.assertEqual(code, expected, record)
                cycle = progress.snapshot(self.root)["cycles"][0]
                if expected == 3:
                    self.assertIsNone(record["result"])
                    self.assertIn("cannot lower the bar", record["error"])
                    self.assertFalse(cycle["consistent"])
                    self.assertIn("cannot lower the bar", cycle["gate"]["error"], "the monitor says why")
                else:
                    self.assertEqual(cycle["gate"]["status"], "verified")

    def test_the_plan_that_authorizes_a_bar_is_read_only_inside_the_swarm_and_bounded(self):
        authorizing = {"options": {"approval_grade": "A-"}, "approval_grade": "A-"}
        outside = Path(self.temp.name) / "outside-plan.json"
        outside.write_text(json.dumps(authorizing), encoding="utf-8")
        plan_file = self.root / "reports" / "execution" / "plan.json"
        plan_file.parent.mkdir(parents=True)
        try:
            plan_file.symlink_to(outside)
        except OSError:
            self.skipTest("symbolic links are unavailable")
        code, record = self.recorded_gate(self.review("A", approval_grade="A-"))
        self.assertEqual(code, 3, "a plan outside the swarm authorizes nothing")
        plan_file.unlink()
        plan_file.write_text(json.dumps({**authorizing, "pad": "x" * gate.MAX_PLAN_BYTES}), encoding="utf-8")
        code, _ = self.recorded_gate(self.review("A", approval_grade="A-"))
        self.assertEqual(code, 3, "nor does one too large to be a plan, though it is valid JSON")
        plan_file.write_text("[1, 2]", encoding="utf-8")
        self.brief("approval_grade: A-\n")
        code, _ = self.recorded_gate(self.review("A", approval_grade="A-"))
        self.assertEqual(code, 0, "a plan that is not an object is no plan, and the brief still authorizes")

    def test_the_gate_stands_down_only_for_an_executor_swarm_that_never_had_a_plan(self):
        execution = self.root / "reports" / "execution"
        journal = execution / "journal.jsonl"
        declares_by_itself = lambda: self.recorded_gate(self.review("A", approval_grade="A-"))[0]
        self.assertEqual(declares_by_itself(), 3, "the coordinator flow: no executor, no default the gate could not know")
        execution.mkdir(parents=True)
        journal.write_text("", encoding="utf-8")
        self.assertEqual(declares_by_itself(), 3, "an empty journal is not an executor that ran")
        journal.write_text('{"seq": 1, "event": "task_issued"}\n', encoding="utf-8")
        self.assertEqual(declares_by_itself(), 0, "the executor's own default is not the gate's to know")
        self.plan({"approval_grade": "A", "options": {"approval_grade": "A"}})
        self.assertEqual(declares_by_itself(), 3, "with a plan the swarm authorizes a grade, and the declaration is compared with it")
        self.plan({"approval_grade": "A-", "options": {"approval_grade": "A-"}})
        self.assertEqual(declares_by_itself(), 0)

    def test_the_gate_and_the_reader_authorize_the_same_grade(self):
        for brief, plan, expected in (({}, {}, "A"), ({"approval_grade": "A-"}, {}, "A-"),
                                      ({}, {"approval_grade": "A-"}, "A-"), ({"approval_grade": "A"}, {"approval_grade": "A-"}, "A"),
                                      ({"approval_grade": "A"}, {"options": {"approval_grade": "A-"}}, "A-"),
                                      ({"approval_grade": "B+"}, {"options": {"approval_grade": 7}, "approval_grade": None}, "A")):
            with self.subTest(brief=brief, plan=plan):
                self.assertEqual(gate.authorized_grade(brief, plan), expected)
                self.brief(f"approval_grade: {brief['approval_grade']}\n" if "approval_grade" in brief else "")
                (self.root / "reports" / "execution" / "plan.json").unlink(missing_ok=True)
                if plan:
                    self.plan(plan)
                self.assertEqual(progress.snapshot(self.root)["approval_grade"], expected)

    def test_reading_the_plan_modifies_nothing(self):
        self.plan({"max_cycles": 3, "approval_grade": "A-", "options": {"max_cycles": 5, "approval_grade": "A-"}})
        self.recorded_gate(self.review("A-", approval_grade="A-"))

        def tree():
            return {str(path.relative_to(self.root)): path.read_bytes()
                    for path in sorted(self.root.rglob("*")) if path.is_file()}

        before = tree()
        data = progress.snapshot(self.root)
        self.assertEqual((data["max_cycles"], data["approval_grade"]), (5, "A-"))
        self.assertEqual(before, tree())

    def test_an_unreadable_plan_is_a_warning_and_the_briefs_values_stand(self):
        self.brief("approval_grade: A-\n")
        path = self.plan("{}")
        for raw in (b"{broken", b"[1, 2]", b'"text"', b"null", b"\xff\xfe\x00", b"x" * (progress.MAX_FILE_BYTES + 1)):
            with self.subTest(raw=raw[:12]):
                path.write_bytes(raw)
                data = progress.snapshot(self.root)
                self.assertEqual((data["max_cycles"], data["approval_grade"]), (3, "A-"))
                self.assertTrue(any(warning.startswith("plan.json:") for warning in data["warnings"]), data["warnings"])
        path.write_text("{}", encoding="utf-8")
        self.assertEqual(progress.snapshot(self.root)["warnings"], [], "an empty plan is a plan, not a problem")

    def test_a_plan_that_escapes_the_swarm_is_not_read(self):
        outside = Path(self.temp.name) / "outside-plan.json"
        outside.write_text(json.dumps({"options": {"max_cycles": 9, "approval_grade": "A-"}}), encoding="utf-8")
        link = self.root / "reports" / "execution" / "plan.json"
        link.parent.mkdir(parents=True)
        try:
            link.symlink_to(outside)
        except OSError:
            self.skipTest("symbolic links are unavailable")
        data = progress.snapshot(self.root)
        self.assertEqual((data["max_cycles"], data["approval_grade"]), (3, "A"))
        self.assertTrue(any("escapes swarm" in warning for warning in data["warnings"]), data["warnings"])
