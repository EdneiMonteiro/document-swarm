from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from scripts.checks import final_report, gate, lint_agents, update_memory, verify_sources, verify_tables
from scripts.checks.common import parse_yaml

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
WORK = ROOT / "tests" / ".work"


class WorkTest(unittest.TestCase):
    def setUp(self):
        self.work = WORK / self.id().rsplit(".", 1)[-1]
        shutil.rmtree(self.work, ignore_errors=True)
        self.work.mkdir(parents=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(WORK, ignore_errors=True)


class SourceHandler(BaseHTTPRequestHandler):
    calls = 0
    guard = threading.Lock()

    def log_message(self, *_args):
        pass

    def count(self):
        # The checker asks for several URLs at once, so an unguarded += would lose counts.
        with type(self).guard:
            type(self).calls += 1

    def do_HEAD(self):
        self.count()
        if self.path == "/get":
            self.send_response(405)
        elif self.path == "/warn":
            self.send_response(403)
        elif self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/ok")
        elif self.path == "/missing":
            self.send_response(404)
        else:
            self.send_response(200)
        self.end_headers()

    def do_GET(self):
        self.count()
        if self.path == "/warn":
            self.send_response(403)
        elif self.path == "/missing":
            self.send_response(404)
        else:
            self.send_response(200)
        self.end_headers()
        if self.path not in {"/warn", "/missing"}:
            self.wfile.write(b"ok")


class VerifySourcesTests(WorkTest):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), SourceHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.thread.join()
        cls.server.server_close()
        super().tearDownClass()

    def test_cache_and_force_and_statuses(self):
        base = f"http://127.0.0.1:{self.server.server_port}"
        index = self.work / "sources-index.md"
        index.write_text(f"{base}/ok {base}/ok {base}/get {base}/warn {base}/redirect {base}/missing", encoding="utf-8")
        output = self.work / "sources-check.json"
        SourceHandler.calls = 0
        first = verify_sources.verify(index, output, timeout=2)
        first_calls = SourceHandler.calls
        self.assertEqual(len(first["results"]), 5)
        self.assertEqual(first["counts"], {"ok": 2, "redirect": 1, "warn": 1, "fail": 1})
        self.assertGreater(first_calls, 4)  # /get retries with GET
        self.assertEqual(next(item for item in first["results"] if item["url"].endswith("/warn"))["method"], "GET")
        self.assertEqual(next(item for item in first["results"] if item["url"].endswith("/missing"))["method"], "GET")
        second = verify_sources.verify(index, output, timeout=2)
        self.assertEqual(SourceHandler.calls, first_calls + 2)  # failed HEAD+GET checks are never cached
        self.assertFalse(next(item for item in second["results"] if item["status"] == "fail")["cached"])
        verify_sources.verify(index, output, force=True, timeout=2)
        self.assertGreater(SourceHandler.calls, first_calls)

    def test_broken_cli_returns_nonzero_and_bad_options_are_rejected(self):
        base = f"http://127.0.0.1:{self.server.server_port}"
        index = self.work / "sources-index.md"
        index.write_text(f"{base}/missing", encoding="utf-8")
        self.assertEqual(verify_sources.main([str(index), "--timeout", "2"]), 1)
        self.assertEqual(verify_sources.main([str(self.work / "absent.md")]), 2)
        with self.assertRaises(ValueError):
            verify_sources.verify(index, self.work / "out.json", cache_days=-1)
        with self.assertRaises(ValueError):
            verify_sources.verify(index, self.work / "out.json", timeout=0)
        empty = self.work / "empty.md"
        empty.write_text("# No sources\n", encoding="utf-8")
        self.assertEqual(verify_sources.main([str(empty)]), 2)


class VerifyTablesTests(WorkTest):
    def test_reproduces_eight_documented_bad_rows(self):
        report = verify_tables.check_file(FIXTURES / "tables" / "scoring-bad.md")
        mismatches = [item for check in report["checks"] for item in check.get("mismatches", [])]
        self.assertEqual(report["failures"], 2)
        self.assertEqual(len(mismatches), 8)
        self.assertEqual({item["declared_total"] for item in mismatches},
                         {"445", "415", "310", "386", "450", "405", "440", "389"})
        self.assertIn("Hot serving — ADX", {item["label"] for item in mismatches})

    def test_corrected_table_and_percent_rule_pass(self):
        report = verify_tables.check_file(FIXTURES / "tables" / "scoring-corrected.md")
        self.assertEqual(report["failures"], 0)
        percent = self.work / "percent.md"
        percent.write_text(
            "<!-- check: percent column=Percent -->\n| Category | Percent |\n| --- | ---: |\n| A | 40 |\n| B | 60 |\n| Total | 100 |\n",
            encoding="utf-8",
        )
        self.assertEqual(verify_tables.check_file(percent)["failures"], 0)
        quoted = self.work / "quoted-column.md"
        quoted.write_text(
            '<!-- check: sum column="Allocation %" target=100 -->\n'
            "| Category | Allocation % |\n| --- | ---: |\n| A | 40 |\n| B | 60 |\n",
            encoding="utf-8",
        )
        self.assertEqual(verify_tables.check_file(quoted)["failures"], 0)
        bad_sum = self.work / "bad-sum.md"
        bad_sum.write_text(
            '<!-- check: sum column="Allocation %" target=100 -->\n'
            "| Category | Allocation % |\n| --- | ---: |\n| A | 40 |\n| B | 50 |\n",
            encoding="utf-8",
        )
        mismatch = verify_tables.check_file(bad_sum)["checks"][0]["mismatches"][0]
        self.assertEqual(mismatch["calculated_total"], "90")
        self.assertEqual(mismatch["expected_total"], "100")

    def test_marker_does_not_leak_unknown_rules_fail_and_multiple_inputs_aggregate(self):
        document = self.work / "tables.md"
        document.write_text(
            "<!-- check: weighted weights=50, 50 -->\nIntervening prose.\n"
            "| Item | A | B | Total |\n| --- | ---: | ---: | ---: |\n| X | 1 | 1 | 1 |\n\n"
            "<!-- check: mystery -->\n| Item | Value |\n| --- | ---: |\n| X | 1 |\n",
            encoding="utf-8",
        )
        report = verify_tables.check_file(document)
        self.assertEqual(report["failures"], 2)
        self.assertEqual(report["checks"][0]["error"], "check marker is not followed by a Markdown table")
        self.assertEqual(report["checks"][-1]["rule"], "mystery")
        (self.work / "second.md").write_text(
            "<!-- check: weighted weights=50, 50 -->\n| Item | A | B | Total |\n"
            "| --- | ---: | ---: | ---: |\n| Y | 1 | 1 | 100 |\n", encoding="utf-8"
        )
        aggregate = self.work / "aggregate.json"
        self.assertEqual(verify_tables.main([str(self.work), "--output", str(aggregate)]), 1)
        self.assertIn("documents", json.loads(aggregate.read_text(encoding="utf-8")))
        empty_dir = self.work / "empty"
        empty_dir.mkdir()
        self.assertEqual(verify_tables.main([str(empty_dir)]), 2)

    def test_marked_non_numeric_cells_fail_instead_of_being_skipped(self):
        document = self.work / "invalid-cells.md"
        document.write_text(
            "<!-- check: weighted weights=50,50 -->\n"
            "| Item | A | B | Total | Score |\n"
            "| --- | ---: | ---: | ---: | ---: |\n"
            "| Broken | n/a | 1 | 50 | 50 |\n",
            encoding="utf-8",
        )
        report = verify_tables.check_file(document)
        self.assertEqual(report["failures"], 1)
        self.assertIn("not numeric", report["checks"][0]["mismatches"][0]["error"])


def review(grade="A", *, cycle=1, maximum=3, critical=False, slides=None, dimensions=None, approval=None):
    result = {
        "cycle": cycle, "max_cycles": maximum,
        "topics": [{"topico": "Evidence", "nota_minima": grade, "revisor_da_minima": "R", "bloqueia": False}],
        "rubberduck": {"critico": critical, "achados": []},
    }
    if approval is not None:
        result["approval_grade"] = approval
    if slides is not None:
        result["slides"] = slides
    if dimensions is not None:
        result["deck_dimensions"] = dimensions
    return result


class GateTests(unittest.TestCase):
    def test_approved(self):
        self.assertEqual(gate.evaluate(review())["outcome"], "approved")

    def test_a_minus_rejected(self):
        result = gate.evaluate(review("A-"))
        self.assertEqual(result["outcome"], "rejected")
        self.assertEqual(result["blocked"][0]["name"], "Evidence")

    def test_a_review_may_declare_that_a_minus_approves_and_the_result_says_so(self):
        for grade in ("A-", "A", "A+"):
            with self.subTest(grade=grade):
                result = gate.evaluate(review(grade, approval="A-"))
                self.assertEqual((result["outcome"], result["approval_grade"]), ("approved", "A-"))
        below = gate.evaluate(review("B+", approval="A-"))
        self.assertEqual((below["outcome"], below["approval_grade"], below["blocked"][0]["grade"]),
                         ("rejected", "A-", "B+"), "a relaxed bar is A-, not anything lower")
        self.assertEqual(gate.evaluate(review("B+", cycle=3, maximum=3, approval="A-"))["outcome"], "escalate")

    def test_without_a_declaration_or_with_a_the_bar_is_a_and_the_result_keeps_its_old_shape(self):
        for approval in (None, "A", " a "):
            with self.subTest(approval=approval):
                result = gate.evaluate(review("A-", approval=approval))
                self.assertEqual(result["outcome"], "rejected")
                self.assertNotIn("approval_grade", result, "a result recorded before the field existed must still reproduce")

    def test_a_topic_flagged_as_blocking_blocks_whatever_the_bar(self):
        data = review("A", approval="A-")
        data["topics"][0]["bloqueia"] = True
        self.assertEqual(gate.evaluate(data)["outcome"], "rejected")

    def test_a_bar_that_is_not_a_minus_or_a_is_refused(self):
        for bad in ("B+", "A+", "Z", "", None, 3, True):
            with self.subTest(bad=bad):
                data = review("A")
                data["approval_grade"] = bad
                with self.assertRaisesRegex(gate.InputError, "invalid grade|approval_grade must be one of"):
                    gate.evaluate(data)

    def editorial(self, grade, action="Reescrever."):
        return {"editorial": {
            "schema_version": 1, "scope": "full_document", "cycle": 1, "reviewer": "reviewer-02-clarity",
            "text": {"path": "reports/texto.txt", "sha256": "0" * 64},
            "artifacts": [{"path": "output/documento.md", "sha256": "1" * 64}],
            "surfaces": [{"surface": surface, "grade": grade, "location": "seção 1", "quote": "trecho",
                          "justification": "porque sim", "action": action} for surface in gate.EDITORIAL_SURFACES],
            "findings": []}}

    def test_the_editorial_surfaces_are_judged_against_the_declared_bar_too(self):
        a_minus = {**review("A", approval="A-"), **self.editorial("A-")}
        self.assertEqual(gate.evaluate(a_minus, require_editorial=True)["outcome"], "approved")
        original = {**review("A"), **self.editorial("A-")}
        result = gate.evaluate(original, require_editorial=True)
        self.assertEqual((result["outcome"], len(result["blocked"])), ("rejected", 5))
        too_low = {**review("A", approval="A-"), **self.editorial("B+")}
        self.assertEqual(len(gate.evaluate(too_low, require_editorial=True)["blocked"]), 5)

    def test_a_reviewer_must_say_what_would_make_an_a_minus_an_a_whatever_the_bar(self):
        silent = {**review("A", approval="A-"), **self.editorial("A-", action="")}
        with self.assertRaisesRegex(gate.InputError, "non-empty action"):
            gate.evaluate(silent, require_editorial=True)
        self.assertEqual(gate.evaluate({**review("A", approval="A-"), **self.editorial("A", action="")},
                                       require_editorial=True)["outcome"], "approved")

    def test_critical_rejected_and_max_escalates(self):
        self.assertEqual(gate.evaluate(review(critical=True))["outcome"], "rejected")
        self.assertEqual(gate.evaluate(review("B+", cycle=3, maximum=3))["outcome"], "escalate")

    def test_a_matrix_cannot_drop_the_veto_by_a_flag_while_a_finding_is_critical(self):
        # The flag and the findings state one fact.  An executor writes both; a hand-edited or damaged matrix
        # that says "not critical" beside a critical finding must not approve.
        def with_findings(findings, critical=False):
            data = review(critical=critical)
            data["rubberduck"]["achados"] = findings
            return data

        for severity in ("critical", "Critical", " CRITICAL ", "critico", "crítico"):
            with self.subTest(severity=severity):
                with self.assertRaisesRegex(gate.InputError, "the veto cannot be dropped"):
                    gate.evaluate(with_findings([{"severity": severity, "target": "t", "evidence": "e", "correction": "c"}]))
        with self.assertRaisesRegex(gate.InputError, "the veto cannot be dropped"):
            gate.evaluate(with_findings([{"severidade": "critico"}]))
        # What stays valid: a vetoing flag with its finding, findings that are not critical, and the plain
        # strings the coordinator flow wrote, which carry no severity to contradict.
        self.assertEqual(gate.evaluate(with_findings([{"severity": "critical"}], critical=True))["outcome"], "rejected")
        self.assertEqual(gate.evaluate(with_findings([{"severity": "important"}, {"severity": "minor"}]))["outcome"], "approved")
        self.assertEqual(gate.evaluate(with_findings(["texto livre", {"target": "sem severidade"}]))["outcome"], "approved")

    def test_legacy_deck_grades_still_block(self):
        self.assertEqual(gate.evaluate(review(slides=[{"slide": "1", "nota_minima": "B+"}]))["outcome"], "rejected")
        self.assertEqual(gate.evaluate(review(dimensions=[{"dimension": "contrast", "grade": "A-"}]))["outcome"], "rejected")
        self.assertEqual(gate.evaluate(review(slides={"Cover": {"grade": "A", "blocks": True}}))["blocked"][0]["name"], "Cover")

    def test_portuguese_yaml_and_invalid(self):
        path = WORK / "canonical-review.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "ciclo: 1\nmax_ciclos: 1\ntopicos:\n  - topico: X\n    nota_minima: A+\n"
            "    revisor_da_minima: R\n    bloqueia: false\nrubberduck:\n  critico: false\n  achados: []\n",
            encoding="utf-8",
        )
        parsed = gate.evaluate(gate.load_data(path))
        self.assertEqual(parsed["outcome"], "approved")
        with self.assertRaises(Exception):
            gate.evaluate(review("Z"))


class FinalReportTests(WorkTest):
    def test_generates_approved_fixture_and_refuses_overwrite(self):
        swarm = self.work / "SWARM"
        (swarm / "reports").mkdir(parents=True)
        (swarm / "sources").mkdir()
        (swarm / "brief.md").write_text("---\nskill_version: \"1.2.3\"\n---\n", encoding="utf-8")
        (swarm / "reports" / "cycle-01-review.yaml").write_text(
            "cycle: 1\nmax_cycles: 2\ntopics:\n  - topico: T1\n    nota_minima: A\n    revisor_da_minima: r\n    bloqueia: false\nrubberduck:\n  critico: false\n  achados: []\n",
            encoding="utf-8")
        (swarm / "sources" / "sources-check.json").write_text(json.dumps({"counts": {"ok": 2, "redirect": 0, "warn": 1, "fail": 0}}))
        (swarm / "reports" / "cycle-01-tables-check.json").write_text(json.dumps({"failures": 0}))
        output = swarm / "reports" / "final-report.md"
        self.assertEqual(final_report.main([str(swarm)]), 0)
        text = output.read_text(encoding="utf-8")
        self.assertIn("1.2.3", text)
        self.assertNotIn('"1.2.3"', text)
        self.assertIn("**approved**", text)
        self.assertIn("Final rubber-duck state", text)
        self.assertIn("COORDINATOR", text)
        self.assertNotIn("Final slide grades", text)
        self.assertNotIn("Final deck dimensions", text)
        self.assertEqual(final_report.main([str(swarm)]), 1)

    def test_legacy_presentation_sections_only_appear_when_present(self):
        swarm = self.work / "LEGACY"
        (swarm / "reports").mkdir(parents=True)
        (swarm / "sources").mkdir()
        (swarm / "brief.md").write_text('---\nskill_version: "2.0.0"\n---\n', encoding="utf-8")
        (swarm / "sources" / "sources-check.json").write_text(
            json.dumps({"counts": {"ok": 1, "redirect": 0, "warn": 0, "fail": 0}}), encoding="utf-8",
        )
        (swarm / "reports" / "cycle-01-tables-check.json").write_text('{"failures":0}', encoding="utf-8")
        base = (
            "cycle: 1\nmax_cycles: 1\ntopics:\n  - topico: T1\n    nota_minima: A\n"
            "    revisor_da_minima: r\n    bloqueia: false\n"
            "rubberduck:\n  critico: false\n  achados: []\n"
        )
        for slides, dimensions in ((False, False), (True, False), (False, True), (True, True)):
            with self.subTest(slides=slides, dimensions=dimensions):
                document = base
                if slides:
                    document += 'slides:\n  - slide: "01"\n    nota_minima: A\n'
                if dimensions:
                    document += "deck_dimensions:\n  - dimension: Contrast\n    nota_minima: A\n"
                (swarm / "reports" / "cycle-01-review.yaml").write_text(document, encoding="utf-8")
                text = final_report.render(swarm)
                self.assertEqual("### Final slide grades" in text, slides)
                self.assertEqual("### Final deck dimensions" in text, dimensions)
                if slides:
                    self.assertIn("| 01 | A |", text)
                if dimensions:
                    self.assertIn("| Contrast | A |", text)

    def test_missing_mandatory_artifact_does_not_write_report(self):
        swarm = self.work / "MISSING"
        (swarm / "reports").mkdir(parents=True)
        (swarm / "sources").mkdir()
        (swarm / "brief.md").write_text("---\nskill_version: \"2.0.0\"\n---\n", encoding="utf-8")
        (swarm / "reports" / "cycle-01-review.yaml").write_text(
            "cycle: 1\nmax_cycles: 1\ntopics:\n  - topico: T\n    nota_minima: A\n"
            "    revisor_da_minima: r\n    bloqueia: false\nrubberduck:\n  critico: false\n  achados: []\n",
            encoding="utf-8",
        )
        (swarm / "sources" / "sources-check.json").write_text('{"counts":{"ok":1,"redirect":0,"warn":0,"fail":0}}')
        self.assertEqual(final_report.main([str(swarm)]), 1)
        self.assertFalse((swarm / "reports" / "final-report.md").exists())
        (swarm / "reports" / "cycle-01-tables-check.json").write_text('{"failures":0}')
        (swarm / "reports" / "cycle-01-review.yaml").write_text(
            "cycle: 1\nmax_cycles: 2\ntopics:\n  - topico: T\n    nota_minima: B+\n"
            "    revisor_da_minima: r\n    bloqueia: false\nrubberduck:\n  critico: false\n  achados: []\n",
            encoding="utf-8",
        )
        self.assertEqual(final_report.main([str(swarm)]), 1)
        (swarm / "reports" / "cycle-01-review.yaml").write_text(
            "cycle: 1\nmax_cycles: 1\ntopics:\n  - topico: T\n    nota_minima: A\n"
            "    revisor_da_minima: r\n    bloqueia: false\nrubberduck:\n  critico: false\n  achados: []\n",
            encoding="utf-8",
        )
        (swarm / "sources" / "sources-check.json").write_text(
            '{"counts":{"ok":0,"redirect":0,"warn":0,"fail":1}}',
            encoding="utf-8",
        )
        self.assertEqual(final_report.main([str(swarm)]), 1)

    def test_final_report_uses_latest_table_check_and_allows_escalation_facts(self):
        swarm = self.work / "ESCALATE"
        (swarm / "reports").mkdir(parents=True)
        (swarm / "sources").mkdir()
        (swarm / "brief.md").write_text("---\nskill_version: \"2.0.0\"\n---\n", encoding="utf-8")
        (swarm / "reports" / "cycle-01-review.yaml").write_text(
            "cycle: 1\nmax_cycles: 2\ntopics:\n  - topico: T\n    nota_minima: B+\n"
            "    revisor_da_minima: r\n    bloqueia: true\nrubberduck:\n  critico: false\n  achados: []\n",
            encoding="utf-8",
        )
        (swarm / "reports" / "cycle-02-review.yaml").write_text(
            "cycle: 2\nmax_cycles: 2\ntopics:\n  - topico: T\n    nota_minima: B+\n"
            "    revisor_da_minima: r\n    bloqueia: true\nrubberduck:\n  critico: false\n  achados: []\n",
            encoding="utf-8",
        )
        (swarm / "reports" / "cycle-01-tables-check.json").write_text('{"failures":2}', encoding="utf-8")
        (swarm / "reports" / "cycle-02-tables-check.json").write_text('{"failures":0}', encoding="utf-8")
        (swarm / "sources" / "sources-check.json").write_text(
            '{"counts":{"ok":4,"redirect":0,"warn":0,"fail":1}}',
            encoding="utf-8",
        )
        self.assertEqual(final_report.main([str(swarm)]), 0)
        text = (swarm / "reports" / "final-report.md").read_text(encoding="utf-8")
        self.assertIn("deterministic outcome:** escalate", text)
        self.assertIn("blocking failures remain for escalation", text)
        self.assertIn("latest failures=0", text)
        self.assertIn("Escalation blockers", text)


class LintAgentsTests(WorkTest):
    def test_duplicate_frontmatter_fields_cannot_silently_override_contracts(self):
        for field in ("name", "model", "editorial_guidance_version"):
            with self.subTest(field=field):
                text = f"---\n{field}: first\n{field}: second\n---\n"
                with self.assertRaisesRegex(ValueError, f"duplicate frontmatter field: {field}"):
                    lint_agents.frontmatter_text(text)

    def test_missing_model_and_wrong_swarm(self):
        agents = self.work / "SWARM" / "agents"
        agents.mkdir(parents=True)
        missing = agents / "missing.md"
        missing.write_text("---\nname: x\nkind: author\nswarm: SWARM\n---\n", encoding="utf-8")
        wrong = agents / "wrong.md"
        wrong.write_text("---\nname: x\nkind: author\nmodel: model\nswarm: ELSE\n---\n", encoding="utf-8")
        errors = lint_agents.lint([missing, wrong])
        self.assertTrue(any("model" in error for error in errors))
        self.assertTrue(any("swarm must be 'SWARM'" in error for error in errors))

    def test_crlf_and_direct_cli_from_non_repo_cwd(self):
        agents = self.work / "SWARM" / "agents"
        agents.mkdir(parents=True)
        good = agents / "good.md"
        good.write_bytes(
            b"---\r\nname: x\r\nkind: author\r\nmodel: model\r\nswarm: SWARM\r\n"
            b"model_status: confirmed\r\nmodel_rationale: useful\r\ncontext_tier: default\r\n---\r\n"
        )
        self.assertEqual(lint_agents.main([str(good)]), 0)
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "checks" / "lint_agents.py"), str(good)],
            cwd=self.work, text=True, capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


class DocumentScopeTests(unittest.TestCase):
    def test_templates_and_review_modes_are_document_only(self):
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        headers = re.findall(r"```markdown\s*\n---\n(.*?)\n---(?:\n|$)", skill, re.S)
        kinds = [parse_yaml(header)["kind"] for header in headers]
        self.assertCountEqual(kinds, ["author", "reviewer", "coordinator", "rubber-duck"])
        modes = re.findall(r"(?m)^mode:[ \t]+([a-z-]+)[ \t]*$", skill)
        self.assertEqual(set(modes), {"document"})
        self.assertNotIn("<deck_id>", skill)


class InstallTests(WorkTest):
    def test_bash_rejects_removed_and_unknown_options_without_installing(self):
        bash = shutil.which("bash")
        if sys.platform == "win32":
            git = shutil.which("git")
            candidate = Path(git).resolve().parents[1] / "bin" / "bash.exe" if git else None
            bash = str(candidate) if candidate and candidate.is_file() else None
        if not bash:
            self.skipTest("Bash is unavailable")
        script = (ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")
        home = self.work / "home"
        env = {**os.environ, "HOME": str(home), "USERPROFILE": str(home), "BASH_ENV": ""}
        syntax = subprocess.run(
            [bash, "-n"], input=script, cwd=self.work, env=env,
            text=True, encoding="utf-8", capture_output=True, timeout=15,
        )
        self.assertEqual(syntax.returncode, 0, syntax.stderr)
        for option in ("--with-presentation", "--unknown"):
            with self.subTest(option=option):
                result = subprocess.run(
                    [bash, "-s", "--", option], input=script, cwd=self.work, env=env,
                    text=True, encoding="utf-8", capture_output=True, timeout=15,
                )
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn(option, result.stderr)
                self.assertEqual(result.stdout, "")
                self.assertFalse(home.exists())

    def test_powershell_rejects_removed_and_unknown_options_without_installing(self):
        pwsh = shutil.which("pwsh")
        if not pwsh:
            self.skipTest("PowerShell is unavailable")
        scripts = self.work / "repo" / "scripts"
        scripts.mkdir(parents=True)
        script = scripts / "install.ps1"
        shutil.copy2(ROOT / "scripts" / "install.ps1", script)
        home = self.work / "home"
        env = {**os.environ, "USERPROFILE": str(home), "HOME": str(home)}
        for option in ("-WithPresentation", "-UnsupportedOption"):
            with self.subTest(option=option):
                result = subprocess.run(
                    [pwsh, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(script), option],
                    cwd=self.work, env=env, text=True, encoding="utf-8", capture_output=True, timeout=15,
                )
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn(option.lstrip("-"), result.stderr)
                self.assertNotIn("SKILL.md", result.stderr)
                self.assertEqual(result.stdout, "")
                # PowerShell may create its own startup cache under USERPROFILE.
                self.assertFalse((home / ".copilot").exists())


class MemoryTests(WorkTest):
    def make_swarm(self, approved=False, name="SWARM"):
        swarm = self.work / name
        (swarm / "reports").mkdir(parents=True)
        (swarm / "sources").mkdir()
        (swarm / "agents").mkdir()
        (swarm / "sources" / "sources-index.md").write_text(
            "| ID | Título | Tipo | URL | Tópicos cobertos | Status HTTP | Data de acesso |\n"
            "| --- | --- | --- | --- | --- | --- | --- |\n"
            "| F01 | Good | Official | https://good.example | T01, T02 | HTTP 200 | 2026-01-01 |\n"
            "| F02 | Warn | Official | https://warn.example | T01 | HTTP 403 | 2026-01-01 |\n", encoding="utf-8")
        (swarm / "sources" / "sources-check.json").write_text(json.dumps({"results": [
            {"url": "https://good.example", "status": "ok", "checked_at": "2026-01-01T00:00:00+00:00"},
            {"url": "https://warn.example", "status": "warn", "checked_at": "2026-01-01T00:00:00+00:00"},
        ]}), encoding="utf-8")
        (swarm / "agents" / "author.md").write_text(
            "---\nname: author\nkind: author\nmodel: model-x\nrole: facts\ncontext_tier: long_context\n"
            "reasoning_effort: high\nmodel_rationale: evidence work\nderived_from: older-agent\n"
            "editorial_guidance_version: \"3.2.1\"\n---\n\n# Mission\nUseful standalone profile.\n",
            encoding="utf-8")
        if approved:
            (swarm / "reports" / "cycle-01-review.yaml").write_text(
                "cycle: 1\nmax_cycles: 1\ntopics:\n  - topico: T\n    nota_minima: A\n    revisor_da_minima: R\n    bloqueia: false\nrubberduck:\n  critico: false\n  achados: []\n", encoding="utf-8")
        return swarm

    def test_warn_and_unapproved_profiles_are_excluded_on_apply(self):
        swarm = self.make_swarm(approved=False)
        candidate = update_memory.proposal(swarm)
        self.assertEqual(candidate["profiles"], [])
        memory = self.work / "memory"
        self.assertEqual(update_memory.main([str(swarm), "--memory-dir", str(memory), "--apply", "--approve"]), 0)
        store = json.loads((memory / "sources.json").read_text(encoding="utf-8"))
        self.assertEqual([item["id"] for item in store["sources"]], ["F01"])
        self.assertEqual(store["profiles"], [])

    def test_approved_profile_is_standalone_and_reference_has_61_candidates(self):
        candidate = update_memory.proposal(self.make_swarm(approved=True))
        self.assertEqual(len(candidate["profiles"]), 1)
        self.assertIn("Useful standalone profile", candidate["profiles"][0]["summary"])
        self.assertEqual(candidate["profiles"][0]["derived_from"], "older-agent")
        self.assertEqual(candidate["profiles"][0]["editorial_guidance_version"], "3.2.1")
        reference = update_memory.proposal(FIXTURES / "reference-swarm")
        self.assertTrue(reference["approved"])
        self.assertEqual(len(reference["sources"]), 61)
        self.assertEqual(len(reference["profiles"]), 1)

    def test_legacy_web_fetch_and_negative_approval_and_global_default(self):
        swarm = self.make_swarm(approved=False)
        index = swarm / "sources" / "sources-index.md"
        index.write_text(
            "| ID | Título | Tipo | URL | Tópicos cobertos | Status HTTP | Data de acesso |\n"
            "| --- | --- | --- | --- | --- | --- | --- |\n"
            "| F01 | Fetch | Docs | https://fetch.example | T01 | Microsoft Docs Fetch | 2026-01-01 |\n"
            "| F02 | Warn | Docs | https://warn.example | T02 | HTTP 429 | 2026-01-01 |\n",
            encoding="utf-8",
        )
        (swarm / "sources" / "sources-check.json").unlink()
        candidate = update_memory.proposal(swarm)
        self.assertEqual([item["status"] for item in candidate["sources"]], ["ok", "warn"])
        (swarm / "reports" / "final-report.md").write_text("**Não aprovado**\n", encoding="utf-8")
        self.assertFalse(update_memory.approved(swarm))

        approved = self.make_swarm(approved=True, name="APPROVED")
        with patch.object(update_memory, "REPOSITORY_ROOT", self.work):
            self.assertEqual(update_memory.main([str(approved), "--apply", "--approve", "--calibration-note", "Calibrated"]), 0)
        # The patched root is self.work, so the default is self.work/memory.
        store = json.loads((self.work / "memory" / "sources.json").read_text(encoding="utf-8"))
        self.assertEqual(store["schema_version"], 1)
        self.assertEqual(store["calibration"][0]["note"], "Calibrated")

    def test_invalid_sources_check_does_not_silently_fall_back(self):
        swarm = self.make_swarm(approved=True)
        (swarm / "sources" / "sources-check.json").write_text("{broken", encoding="utf-8")
        self.assertEqual(update_memory.main([str(swarm)]), 1)
        self.assertFalse((swarm / "reports" / "memory-proposal.json").exists())


class DirectExecutionTests(WorkTest):
    def test_all_cli_help_work_from_arbitrary_cwd(self):
        outside = self.work / "arbitrary-swarm"
        outside.mkdir()
        for name in ("verify_sources.py", "verify_tables.py", "gate.py", "final_report.py", "lint_agents.py", "update_memory.py", "inspect_nomenclature.py"):
            result = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "checks" / name), "--help"],
                cwd=outside, text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 0, f"{name}: {result.stderr}")


class EndToEndFixtureTests(WorkTest):
    def test_small_swarm_runs_all_deterministic_gates(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), SourceHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            swarm = self.work / "2026-07-13-SWARM-01"
            agents = swarm / "agents" / "authors"
            reports = swarm / "reports"
            sources = swarm / "sources"
            output = swarm / "output"
            for path in (agents, reports, sources, output):
                path.mkdir(parents=True, exist_ok=True)
            (swarm / "brief.md").write_text(
                "---\nswarm_id: 2026-07-13-SWARM-01\nskill_version: \"2.0.0\"\n"
                "mode: document\nmax_cycles: 2\n---\n",
                encoding="utf-8",
            )
            (agents / "author-01.md").write_text(
                "---\nname: author-01\nkind: author\nmodel: model-x\n"
                "model_status: available confirmed\nmodel_rationale: fixture\n"
                "context_tier: default\nswarm: 2026-07-13-SWARM-01\nsources_min: 5\n---\n"
                "\n# Author\n\nCreate the fixture document.\n",
                encoding="utf-8",
            )
            base = f"http://127.0.0.1:{server.server_port}"
            source_lines = [
                "| ID | Title | Type | URL | Topics | Status HTTP | Access date |",
                "| --- | --- | --- | --- | --- | --- | --- |",
            ]
            for number in range(1, 6):
                source_lines.append(
                    f"| F{number:02d} | Source {number} | Test | {base}/ok-{number} | T01 | Pending | 2026-07-13 |"
                )
            (sources / "sources-index.md").write_text("\n".join(source_lines) + "\n", encoding="utf-8")
            (output / "fixture.md").write_text(
                '<!-- check: sum column="Percent" target=100 -->\n'
                "| Category | Percent |\n| --- | ---: |\n| A | 40 |\n| B | 60 |\n",
                encoding="utf-8",
            )
            review_path = reports / "cycle-01-review.yaml"
            review_path.write_text(
                "schema_version: 1\nskill_version: \"2.0.0\"\nmode: document\n"
                "cycle: 1\nmax_cycles: 2\ntopics:\n"
                "  - topico: T01\n    nota_minima: A\n    revisor_da_minima: reviewer-01\n"
                "    bloqueia: false\nrubberduck:\n  critico: false\n  achados: []\n",
                encoding="utf-8",
            )

            self.assertEqual(lint_agents.main([str(swarm / "agents"), "--strict"]), 0)
            self.assertEqual(verify_sources.main([
                str(sources / "sources-index.md"),
                "--output",
                str(sources / "sources-check.json"),
                "--timeout",
                "2",
            ]), 0)
            self.assertEqual(verify_tables.main([
                str(output / "fixture.md"),
                "--output",
                str(reports / "cycle-01-tables-check.json"),
            ]), 0)
            self.assertEqual(gate.main([str(review_path)]), 0)
            self.assertEqual(final_report.main([str(swarm)]), 0)
            memory_dir = self.work / "memory"
            self.assertEqual(update_memory.main([
                str(swarm),
                "--memory-dir",
                str(memory_dir),
                "--apply",
                "--approve",
            ]), 0)
            store = json.loads((memory_dir / "sources.json").read_text(encoding="utf-8"))
            self.assertEqual(len(store["sources"]), 5)
            self.assertEqual(len(store["profiles"]), 1)
            self.assertIn("deterministic outcome:** approved", (reports / "final-report.md").read_text(encoding="utf-8"))
        finally:
            server.shutdown()
            thread.join()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
