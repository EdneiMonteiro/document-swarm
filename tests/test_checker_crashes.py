"""A checker that crashes must never read as a checker that found something.

Python ends an uncaught exception with status 1, the status the verifiers use for "findings".  The addresses and
the table markers come from the document, so what they can make a checker do matters: every case here is one that
used to end the process with a traceback and no report, and the engine took that for a clean run.
"""

from __future__ import annotations

import contextlib
import io
import json
import socketserver
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from scripts.checks import gate, verify_sources, verify_tables
from scripts.orchestration.engine import NO_REPORT, Engine
from tests.test_orchestration_engine import EngineCase

ROOT = Path(__file__).resolve().parents[1]


class Garbage(socketserver.BaseRequestHandler):
    """A server that is not an HTTP server: it answers every request with text that is not a status line."""

    def handle(self) -> None:
        self.request.recv(1024)
        self.request.sendall(b"this is not http\r\n\r\n")


def serve(handler: type) -> tuple[str, "socketserver.BaseServer"]:
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), handler) if issubclass(handler, socketserver.BaseRequestHandler) \
        else ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_address[1]}", server


def recorder() -> tuple[type, list[str]]:
    seen: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_HEAD(self):
            seen.append(self.path)
            self.send_response(200)
            self.end_headers()

    return Handler, seen


class SourceChecks(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.index = self.folder / "sources-index.md"
        self.output = self.folder / "sources-check.json"

    def serve(self, handler: type) -> str:
        base, server = serve(handler)
        self.addCleanup(lambda: (server.shutdown(), server.server_close()))
        return base

    def check(self, *urls: str) -> list[dict]:
        self.index.write_text("# Fontes\n\n" + "\n".join(f"| F{n:02d} | t | oficial | {url} |" for n, url in enumerate(urls, 1)) + "\n",
                              encoding="utf-8")
        report = verify_sources.verify(self.index, self.output, force=True, timeout=5)
        return report["results"]

    def test_a_server_that_does_not_speak_http_is_a_dead_source_and_the_others_are_still_checked(self):
        handler, _seen = recorder()
        good, bad = self.serve(handler), self.serve(Garbage)
        results = self.check(f"{bad}/x", f"{good}/ok")
        self.assertEqual([item["status"] for item in results], ["fail", "ok"])
        self.assertIn("BadStatusLine", results[0]["error"])

    def test_malformed_addresses_fail_one_source_each_instead_of_ending_the_run(self):
        # urlsplit rejects the first, http.client the second and third; none of them may stop the others.
        handler, _seen = recorder()
        good = self.serve(handler)
        hostile = ("http://[abc/x", "http://localhost:abc/", "http://localhost:99999/", "http://127.0.0.1/a\x7fb",
                   "http://127.0.0.1/a\x00b")
        results = self.check(*hostile, f"{good}/ok")
        self.assertEqual([item["status"] for item in results], ["fail"] * len(hostile) + ["ok"])
        for item in results[:-1]:
            self.assertTrue(item["error"], item)

    def test_the_command_line_reports_a_dead_source_as_a_finding_with_a_report(self):
        bad = self.serve(Garbage)
        self.index.write_text(f"{bad}/x\n", encoding="utf-8")
        done = subprocess.run([sys.executable, "-S", str(ROOT / "scripts" / "checks" / "verify_sources.py"), str(self.index),
                               "--output", str(self.output), "--force"], capture_output=True, text=True, cwd=ROOT, timeout=60)
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertEqual(json.loads(self.output.read_text(encoding="utf-8"))["counts"]["fail"], 1)

    def test_an_address_with_accents_is_requested_in_its_ascii_form_and_is_not_reported_dead(self):
        handler, seen = recorder()
        base = self.serve(handler)
        results = self.check(f"{base}/wiki/Computação?q=ü")
        self.assertEqual(results[0]["status"], "ok", results)
        self.assertEqual(seen, ["/wiki/Computa%C3%A7%C3%A3o?q=%C3%BC"])

    def test_the_ascii_form_leaves_what_is_already_encoded_alone(self):
        self.assertEqual(verify_sources.to_uri("https://pt.wikipedia.org/wiki/Computa%C3%A7%C3%A3o"),
                         "https://pt.wikipedia.org/wiki/Computa%C3%A7%C3%A3o")
        self.assertEqual(verify_sources.to_uri("https://exemplo.test/a b?x=1&y=%20"), "https://exemplo.test/a b?x=1&y=%20",
                         "an ASCII address is returned untouched, whatever it holds")
        self.assertEqual(verify_sources.to_uri("https://pt.wikipedia.org/wiki/Computação"),
                         "https://pt.wikipedia.org/wiki/Computa%C3%A7%C3%A3o")
        self.assertEqual(verify_sources.to_uri("https://pt.wikipedia.org/wiki/Computa%C3%A7ão"),
                         "https://pt.wikipedia.org/wiki/Computa%C3%A7%C3%A3o", "mixed forms are completed, not doubled")
        self.assertEqual(verify_sources.to_uri("https://bücher.example:8443/straße?q=ü#frag"),
                         "https://xn--bcher-kva.example:8443/stra%C3%9Fe?q=%C3%BC")

    def test_a_checker_that_crashes_exits_3_and_never_1(self):
        self.index.write_text("https://exemplo.test/a\n", encoding="utf-8")
        errors = io.StringIO()
        with mock.patch.object(verify_sources, "verify", side_effect=RuntimeError("boom")), contextlib.redirect_stderr(errors):
            code = verify_sources.main([str(self.index), "--output", str(self.output)])
        self.assertEqual(code, 3)
        self.assertIn("RuntimeError: boom", errors.getvalue())
        self.assertFalse(self.output.exists(), "a crash writes no report")


class TableChecks(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)

    def document(self, marker: str, rows: str = "| a | 60 |\n| b | 40 |\n") -> Path:
        path = self.folder / "doc.md"
        path.write_text(f"# T\n\n<!-- check: {marker} -->\n| Item | Percentual |\n|---|---:|\n{rows}", encoding="utf-8")
        return path

    def test_a_marker_the_arithmetic_cannot_carry_is_a_failed_table_not_a_crash(self):
        for marker, fragment in (("sum target=1e999999999", "invalid target"), ("sum target=abc", "invalid target"),
                                 ("sum target=NaN", "invalid target"), ("sum target=sNaN", "invalid target"),
                                 ("sum target=Infinity", "invalid target"), ("sum target=-Infinity", "invalid target"),
                                 ("sum target=1e-999", "invalid target")):
            with self.subTest(marker=marker):
                report = verify_tables.check_file(self.document(marker))
                self.assertEqual(report["failures"], 1)
                self.assertIn(fragment, report["checks"][0]["error"])
        weighted = ("| Alt | C1 | C2 | Total | Score |\n|---|---:|---:|---:|---:|\n| x | 1 | 2 | 3 | 1 |\n")
        for marker, fragment in (("weighted weights=1,2 divisor=NaN", "invalid divisor"),
                                 ("weighted weights=1,2 divisor=Infinity", "invalid divisor"),
                                 ("weighted weights=1,2 divisor=1e999", "invalid divisor"),
                                 ("weighted weights=NaN,1", "invalid weights"), ("weighted weights=1e999,2", "invalid weights"),
                                 ("weighted weights=Infinity,2", "invalid weights")):
            with self.subTest(marker=marker):
                path = self.folder / "w.md"
                path.write_text(f"# T\n\n<!-- check: {marker} -->\n{weighted}", encoding="utf-8")
                report = verify_tables.check_file(path)
                self.assertEqual(report["failures"], 1)
                self.assertIn(fragment, report["checks"][0]["error"])

    def test_a_number_too_large_to_round_is_not_numeric_instead_of_a_crash(self):
        self.assertIsNone(verify_tables.as_number("9" * 40))
        self.assertIsNotNone(verify_tables.as_number("12345678901234567890"))
        report = verify_tables.check_file(self.document("sum target=100", f"| a | {'9' * 30} |\n| b | 40 |\n"))
        self.assertEqual(report["failures"], 1)
        self.assertTrue(any("not numeric" in item.get("error", "") for item in report["checks"][0]["mismatches"]))
        self.assertEqual(verify_tables.display(__import__("decimal").Decimal("1" * 40)), "1" * 40,
                         "a value that cannot be rounded is shown as it is, not raised")

    def test_the_command_line_reports_a_hostile_marker_as_a_finding_with_a_report(self):
        path = self.document("sum target=1e999999999")
        output = self.folder / "tables.json"
        done = subprocess.run([sys.executable, "-S", str(ROOT / "scripts" / "checks" / "verify_tables.py"), str(path),
                               "--output", str(output)], capture_output=True, text=True, cwd=ROOT, timeout=60)
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["failures"], 1)

    def test_a_checker_that_crashes_exits_3_and_never_1(self):
        path = self.document("sum target=100")
        errors = io.StringIO()
        with mock.patch.object(verify_tables, "check_file", side_effect=RuntimeError("boom")), contextlib.redirect_stderr(errors):
            code = verify_tables.main([str(path), "--output", str(self.folder / "t.json")])
        self.assertEqual(code, 3)
        self.assertIn("RuntimeError: boom", errors.getvalue())


class GateCrash(unittest.TestCase):
    def test_an_internal_failure_of_the_gate_is_invalid_not_a_rejection(self):
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            review = folder / "review.json"
            review.write_text(json.dumps({
                "cycle": 1, "max_cycles": 3, "topics": [{"topico": "T", "nota_minima": "A", "revisor_da_minima": "R", "bloqueia": False}],
                "rubberduck": {"critico": False, "achados": []}}), encoding="utf-8")
            record = folder / "gate.json"
            errors = io.StringIO()
            with mock.patch.object(gate, "evaluate_current", side_effect=RuntimeError("boom")), \
                    contextlib.redirect_stderr(errors), contextlib.redirect_stdout(io.StringIO()):
                code = gate.main([str(review), "--output", str(record)])
            self.assertEqual(code, 3, "Python's default for an uncaught exception, 1, would read as 'rejected'")
            saved = json.loads(record.read_text(encoding="utf-8"))
            self.assertEqual((saved["exit_code"], saved["result"]), (3, None))
            self.assertIn("RuntimeError: boom", saved["error"])


class EngineAndCrashedCheckers(EngineCase):
    def run_checked(self, code: str, *, before: str | None = None) -> dict:
        engine = self.engine()
        engine.init()
        report = self.root / "sources" / "sources-check.json"
        if before is not None:
            report.write_text(before, encoding="utf-8")
        return engine.run_checked("sources", ["-c", code], "sources/sources-check.json")

    def test_a_checker_that_ends_without_rewriting_its_report_is_a_script_error_whatever_its_status(self):
        # The stale report is what an earlier run left; exit 1 is what Python gives an uncaught exception.
        crashed = self.run_checked("import sys; sys.exit(1)", before='{"results": []}')
        self.assertEqual((crashed["exit_code"], crashed["original_exit_code"]), (NO_REPORT, 1))
        self.assertIn("sources-check.json was not written by this run", crashed["stderr"])
        silent = self.run_checked("import sys; sys.exit(0)", before='{"results": []}')
        self.assertEqual((silent["exit_code"], silent["original_exit_code"]), (NO_REPORT, 0), "even a clean exit needs the report")
        absent = self.run_checked("pass")
        self.assertEqual(absent["exit_code"], NO_REPORT)

    def test_a_report_that_is_not_json_is_not_a_report(self):
        result = self.run_checked("import pathlib; pathlib.Path('sources/sources-check.json').write_text('{ not json')",
                                  before='{"results": []}')
        self.assertEqual(result["exit_code"], NO_REPORT)

    def test_a_checker_that_rewrote_its_report_keeps_the_status_it_returned(self):
        write = "import pathlib, sys; pathlib.Path('sources/sources-check.json').write_text('{\"results\": []}'); sys.exit(%d)"
        for status in (0, 1):
            with self.subTest(status=status):
                result = self.run_checked(write % status, before='{"results": []}')
                self.assertEqual(result["exit_code"], status)
                self.assertNotIn("original_exit_code", result)

    def test_a_source_check_that_crashes_stops_the_run_instead_of_reviewing_without_it(self):
        with self.patched(leaves_report=False, sources=(1, "Traceback (most recent call last): BadStatusLine")):
            outcome = self.engine().run(self.agent())
        self.assertEqual((outcome["status"], outcome["kind"], outcome["script"]), ("failed", "script_error", "sources"))
        self.assertIn("was not written by this run", outcome["detail"])

    def test_a_table_check_that_crashes_stops_the_run(self):
        with self.patched(leaves_report=False, tables=(1, "Traceback (most recent call last): InvalidOperation")):
            outcome = self.engine().run(self.agent())
        self.assertEqual((outcome["status"], outcome["kind"], outcome["script"]), ("failed", "script_error", "tables"))
        self.assertIn("was not written by this run", outcome["detail"])

    def test_a_final_recheck_that_crashes_blocks_the_delivery_instead_of_being_taken_for_a_clean_one(self):
        with self.patched(leaves_report=False, verify_sources=(1, "Traceback (most recent call last): BadStatusLine")):
            outcome = self.engine().run(self.agent())
        self.assertEqual((outcome["status"], outcome["kind"], outcome["script"]), ("failed", "script_error", "verify_sources"))
        self.assertFalse((self.root / "reports" / "final-report.md").exists(), "nothing is delivered over a recheck that did not run")

    def test_a_gate_that_crashes_is_run_once_and_reported_not_run_again_until_the_step_limit(self):
        calls: list[str] = []
        original = Engine.run_script

        def crashing_gate(engine, name, command):
            calls.append(name)
            if name == "gate":
                return {"script": name, "exit_code": 1, "seconds": 0.0, "stderr": "Traceback (most recent call last): boom"}
            return original(engine, name, command)

        with mock.patch.object(Engine, "run_script", crashing_gate):
            outcome = self.engine().run(self.agent())
        self.assertEqual((outcome["status"], outcome["kind"]), ("failed", "gate_invalid"))
        self.assertIn("was not written by this run", outcome["detail"], "the crash is told apart from a gate whose record fails to reproduce")
        self.assertEqual(calls.count("gate"), 1)

    def test_a_gate_whose_record_cannot_be_reproduced_is_reported_instead_of_looped_on(self):
        calls: list[str] = []
        original = Engine.run_script

        def wrong_record(engine, name, command):
            result = original(engine, name, command)
            calls.append(name)
            if name == "gate":
                path = engine.root / "reports" / "cycle-01-gate.json"
                record = json.loads(path.read_text(encoding="utf-8"))
                record["result"]["blocked"] = [{"kind": "topic", "name": "T01", "grade": "B", "reviewer": "x"}]
                path.write_text(json.dumps(record), encoding="utf-8")
            return result

        with mock.patch.object(Engine, "run_script", wrong_record):
            outcome = self.engine().run(self.agent())
        self.assertEqual((outcome["status"], outcome["kind"]), ("failed", "gate_invalid"))
        self.assertIn("does not reproduce", outcome["detail"])
        self.assertEqual(calls.count("gate"), 1)


if __name__ == "__main__":
    unittest.main()
