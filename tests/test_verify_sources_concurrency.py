from __future__ import annotations

import contextlib
import io
import json
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from scripts.checks import verify_sources


class SlowHandler(BaseHTTPRequestHandler):
    """Answers every HEAD after a pause and remembers how many requests were in flight at once."""

    lock = threading.Lock()
    active = 0
    peak = 0
    calls = 0
    pause = 0.4

    def log_message(self, *_args):
        pass

    def do_HEAD(self):
        cls = type(self)
        with cls.lock:
            cls.active += 1
            cls.calls += 1
            cls.peak = max(cls.peak, cls.active)
        time.sleep(cls.pause)
        with cls.lock:
            cls.active -= 1
        self.send_response(404 if self.path == "/gone" else 200)
        self.end_headers()


class ConcurrencyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), SlowHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.thread.join()
        cls.server.server_close()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        SlowHandler.active = SlowHandler.peak = SlowHandler.calls = 0
        self.index = self.folder / "sources-index.md"
        self.output = self.folder / "sources-check.json"

    def write_index(self, count: int, *, gone: int | None = None) -> list[str]:
        urls = [f"{self.base}/gone" if number == gone else f"{self.base}/page-{number}" for number in range(count)]
        self.index.write_text("\n".join(f"| F{number:02d} | {url} |" for number, url in enumerate(urls)), encoding="utf-8")
        return urls

    def test_urls_are_checked_at_the_same_time_and_the_report_keeps_the_index_order(self):
        urls = self.write_index(8, gone=3)
        started = time.monotonic()
        report = verify_sources.verify(self.index, self.output, timeout=5, workers=8, per_host=8)
        wall = time.monotonic() - started
        self.assertEqual([item["url"] for item in report["results"]], urls)
        self.assertEqual([item["status"] for item in report["results"]], ["fail" if number == 3 else "ok" for number in range(8)])
        self.assertGreater(SlowHandler.peak, 1)
        self.assertLess(wall, 8 * SlowHandler.pause * 0.6, "eight pauses of 0.4 s were not added up")

    def test_one_worker_is_the_sequential_behaviour(self):
        self.write_index(4)
        verify_sources.verify(self.index, self.output, timeout=5, workers=1)
        self.assertEqual(SlowHandler.peak, 1)

    def test_no_host_gets_more_simultaneous_requests_than_the_limit(self):
        self.write_index(8)
        verify_sources.verify(self.index, self.output, timeout=5, workers=8, per_host=2)
        self.assertEqual(SlowHandler.peak, 2, "workers are available, but one host is spared")

    def test_the_number_of_workers_bounds_the_concurrency_too(self):
        self.write_index(8)
        verify_sources.verify(self.index, self.output, timeout=5, workers=3, per_host=8)
        self.assertEqual(SlowHandler.peak, 3)

    def test_cached_entries_cost_no_request_and_keep_their_place(self):
        urls = self.write_index(6)
        first = verify_sources.verify(self.index, self.output, timeout=5, workers=6, per_host=6)
        self.assertEqual(SlowHandler.calls, 6)
        again = verify_sources.verify(self.index, self.output, timeout=5, workers=6, per_host=6)
        self.assertEqual(SlowHandler.calls, 6, "a fresh verdict is reused")
        self.assertEqual([item["url"] for item in again["results"]], urls)
        self.assertTrue(all(item["cached"] for item in again["results"]))
        forced = verify_sources.verify(self.index, self.output, force=True, timeout=5, workers=6, per_host=6)
        self.assertEqual(SlowHandler.calls, 12)
        self.assertFalse(any(item["cached"] for item in forced["results"]))
        self.assertEqual([item["status"] for item in first["results"]], [item["status"] for item in forced["results"]])

    def test_a_mix_of_cached_and_new_urls_puts_each_result_at_its_own_position(self):
        urls = self.write_index(4)
        verify_sources.verify(self.index, self.output, timeout=5)
        self.index.write_text("\n".join(urls[:2] + [f"{self.base}/new-a", f"{self.base}/new-b", urls[3]]), encoding="utf-8")
        before = SlowHandler.calls
        report = verify_sources.verify(self.index, self.output, timeout=5, workers=4, per_host=4)
        self.assertEqual(SlowHandler.calls - before, 2, "only the two new URLs were requested")
        self.assertEqual([item["url"] for item in report["results"]],
                         urls[:2] + [f"{self.base}/new-a", f"{self.base}/new-b", urls[3]])
        self.assertEqual([item["cached"] for item in report["results"]], [True, True, False, False, True])

    def refused(self, **options):
        """Run verify and return what it raised, failing instead of hanging when nothing refuses the options."""
        outcome: list[BaseException] = []

        def attempt():
            try:
                verify_sources.verify(self.index, self.output, **options)
            except BaseException as exc:  # noqa: BLE001 - the caller inspects it
                outcome.append(exc)

        thread = threading.Thread(target=attempt, daemon=True)
        thread.start()
        thread.join(8)
        self.assertFalse(thread.is_alive(), f"{options} must be refused at once, not accepted and left to hang")
        return outcome[0] if outcome else None

    def test_invalid_concurrency_options_are_rejected(self):
        self.write_index(1)
        for options in ({"workers": 0}, {"per_host": 0}, {"workers": -1}):
            with self.subTest(options=options):
                raised = self.refused(**options)
                self.assertIsInstance(raised, ValueError)
                self.assertIn("must be positive", str(raised))
        for flag in ("--workers", "--per-host"):
            with self.subTest(flag=flag), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
                verify_sources.main([str(self.index), flag, "0"])
            self.assertEqual(raised.exception.code, 2)

    def test_the_command_line_accepts_the_options_and_still_exits_one_on_a_dead_source(self):
        self.write_index(3, gone=1)
        with contextlib.redirect_stdout(io.StringIO()):
            code = verify_sources.main([str(self.index), "--timeout", "5", "--workers", "2", "--per-host", "1", "--force"])
        self.assertEqual(code, 1)
        self.assertEqual(SlowHandler.peak, 1)
        report = json.loads(self.output.read_text(encoding="utf-8"))
        self.assertEqual(report["counts"], {"ok": 2, "redirect": 0, "warn": 0, "fail": 1})


if __name__ == "__main__":
    unittest.main()
