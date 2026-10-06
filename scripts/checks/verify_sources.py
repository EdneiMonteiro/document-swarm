"""Verify unique http(s) URLs from a ``sources-index.md`` file.

Successful, redirect and warning responses are reused for seven days by
default.  ``--force`` always makes a network request.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

# Allow ``python3 /repo/scripts/checks/verify_sources.py`` from any cwd.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.checks.common import write_json

URL_RE = re.compile(r"https?://[^\s<>)\]\"']+")
GOOD_CACHE = {"ok", "redirect", "warn"}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Return redirect responses instead of following them."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        return None


def extract_urls(text: str) -> list[str]:
    """Extract de-duplicated URLs while preserving first occurrence."""
    found: list[str] = []
    for value in URL_RE.findall(text):
        value = value.rstrip(".,;:!?")
        if value not in found:
            found.append(value)
    return found


def checked_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def cache_fresh(entry: dict[str, Any], days: int, now: dt.datetime) -> bool:
    if entry.get("status") not in GOOD_CACHE:
        return False
    try:
        checked = dt.datetime.fromisoformat(str(entry["checked_at"]).replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError):
        return False
    return checked.tzinfo is not None and now - checked <= dt.timedelta(days=days)


def request_url(url: str, timeout: float, user_agent: str) -> dict[str, Any]:
    """HEAD a URL, using GET when HEAD is unsupported or inconclusive."""
    opener = urllib.request.build_opener(NoRedirect())
    headers = {"User-Agent": user_agent}
    method = "HEAD"
    try:
        response = opener.open(urllib.request.Request(url, headers=headers, method=method), timeout=timeout)
        code = response.getcode()
        response.close()
    except urllib.error.HTTPError as exc:
        code = exc.code
        exc.close()
        if code in {400, 403, 404, 405, 501}:
            method = "GET"
            try:
                response = opener.open(
                    urllib.request.Request(url, headers=headers, method=method), timeout=timeout
                )
                code = response.getcode()
                response.close()
            except urllib.error.HTTPError as get_exc:
                code = get_exc.code
                get_exc.close()
            except (urllib.error.URLError, TimeoutError, OSError, ValueError) as get_exc:
                return {"status": "fail", "method": method, "error": str(get_exc)}
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        return {"status": "fail", "method": method, "error": str(exc)}
    status = "ok" if 200 <= code < 300 else "redirect" if 300 <= code < 400 else "warn" if code in {403, 429} else "fail"
    return {"status": status, "http_status": code, "method": method}


def verify(index: Path, output: Path, *, force: bool = False, cache_days: int = 7,
           timeout: float = 10, user_agent: str = "document-swarm-source-check/1.0",
           workers: int = 8, per_host: int = 3) -> dict[str, Any]:
    """Run checks and return the stable report dictionary.

    URLs that need a request are checked concurrently, at most ``workers`` at once and at most ``per_host``
    for any one host, so a list with many pages of one site does not hammer it.  The report keeps the order
    of the index, and ``workers=1`` is the sequential behaviour.
    """
    if cache_days < 0:
        raise ValueError("cache_days must be nonnegative")
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    if workers < 1 or per_host < 1:
        raise ValueError("workers and per_host must be positive")
    urls = extract_urls(index.read_text(encoding="utf-8"))
    if not urls:
        raise ValueError("no http(s) URLs found")
    prior: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    if output.exists():
        try:
            saved = json.loads(output.read_text(encoding="utf-8"))
            results = saved.get("results") if isinstance(saved, dict) else None
            if not isinstance(results, list):
                raise ValueError("cache has no results list")
            prior = {
                item["url"]: item
                for item in results
                if isinstance(item, dict) and isinstance(item.get("url"), str)
            }
        except (json.JSONDecodeError, OSError, TypeError, ValueError) as exc:
            warnings.append(f"ignored invalid cache {output}: {exc}")
    now = dt.datetime.now(dt.timezone.utc)
    results = []
    pending: list[int] = []
    for position, url in enumerate(urls):
        cached = prior.get(url)
        if cached and not force and cache_fresh(cached, cache_days, now):
            item = {key: value for key, value in cached.items() if key != "cached"}
            item["cached"] = True
            results.append(item)
        else:
            results.append({})
            pending.append(position)
    gates = {urllib.parse.urlsplit(urls[position]).hostname or "": threading.BoundedSemaphore(per_host)
             for position in pending}

    def check(position: int) -> dict[str, Any]:
        url = urls[position]
        with gates[urllib.parse.urlsplit(url).hostname or ""]:
            return {"url": url, **request_url(url, timeout, user_agent), "checked_at": checked_now(), "cached": False}

    if pending:
        with ThreadPoolExecutor(max_workers=min(workers, len(pending))) as pool:
            for position, item in zip(pending, pool.map(check, pending)):
                results[position] = item
    counts = {name: sum(item["status"] == name for item in results) for name in ("ok", "redirect", "warn", "fail")}
    report = {
        "schema_version": 1,
        "index": str(index),
        "checked_at": checked_now(),
        "cache_days": cache_days,
        "results": results,
        "counts": counts,
        "warnings": warnings,
    }
    write_json(output, report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check URLs in sources-index.md and write an audit JSON report.")
    parser.add_argument("index", type=Path, help="sources-index.md to inspect")
    parser.add_argument("--output", type=Path, help="audit JSON path (default: beside the index)")
    parser.add_argument("--force", action="store_true", help="bypass successful/warning cache")
    parser.add_argument("--cache-days", type=int, default=7, help="valid cache duration (default: 7)")
    parser.add_argument("--timeout", type=float, default=10, help="per-request timeout in seconds")
    parser.add_argument("--user-agent", default="document-swarm-source-check/1.0", help="HTTP User-Agent")
    parser.add_argument("--workers", type=int, default=8, help="URLs checked at the same time (1 is sequential)")
    parser.add_argument("--per-host", type=int, default=3, help="most simultaneous requests to one host")
    args = parser.parse_args(argv)
    if args.cache_days < 0:
        parser.error("--cache-days must be nonnegative")
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    if args.workers < 1 or args.per_host < 1:
        parser.error("--workers and --per-host must be positive")
    output = args.output or args.index.with_name("sources-check.json")
    try:
        report = verify(args.index, output, force=args.force, cache_days=args.cache_days,
                        timeout=args.timeout, user_agent=args.user_agent, workers=args.workers, per_host=args.per_host)
    except (OSError, ValueError) as exc:
        print(f"ERROR: cannot read/check {args.index}: {exc}", file=sys.stderr)
        return 2
    for item in report["results"]:
        detail = item.get("http_status", item.get("error", ""))
        cached = " (cache)" if item.get("cached") else ""
        print(f"{item['status'].upper():8} {detail!s:5} {item['url']}{cached}")
    for warning in report["warnings"]:
        print(f"WARN     {warning}", file=sys.stderr)
    print(f"Wrote {output}: " + ", ".join(f"{key}={value}" for key, value in report["counts"].items()))
    return 1 if report["counts"]["fail"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
