"""Verify unique http(s) URLs from a ``sources-index.md`` file.

Successful, redirect and warning responses are reused for seven days by
default.  ``--force`` always makes a network request.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
from html.parser import HTMLParser
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

# Allow ``python3 /repo/scripts/checks/verify_sources.py`` from any cwd.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.checks.common import write_json

URL_RE = re.compile(r"https?://[^\s<>)\]\"']+")
ROW_SOURCE_RE = re.compile(
    r"^\s*\|\s*(F\d{2,3})\s*\|[^|]*\|[^|]*\|\s*(https?://[^\s|<>\"']+)\s*\|",
    re.MULTILINE,
)
GOOD_CACHE = {"ok", "redirect", "warn"}
MAX_CORPUS_BYTES = 2 * 1024 * 1024
MAX_CORPUS_CHARS = 120_000


class _ReadableHTMLParser(HTMLParser):
    """Extract readable plain text from HTML without external dependencies."""

    _SKIP_TAGS = {"script", "style", "noscript", "svg", "template"}
    _BLOCK_TAGS = {
        "p", "div", "section", "article", "main", "aside", "header", "footer",
        "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr", "td", "th", "br", "hr",
        "blockquote", "pre", "dt", "dd",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        low = tag.lower()
        if low in self._SKIP_TAGS:
            self._skip_depth += 1
        elif self._skip_depth == 0 and low in self._BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        low = tag.lower()
        if low in self._SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
        elif self._skip_depth == 0 and low in self._BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0 and data:
            self._parts.append(data)

    def get_text(self) -> str:
        raw = "".join(self._parts)
        lines = [re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in raw.splitlines()]
        compact = "\n".join(line for line in lines if line)
        return compact[:MAX_CORPUS_CHARS]


def html_to_text(raw_bytes: bytes, content_type: str = "") -> str:
    """Convert raw HTTP response bytes into bounded UTF-8 plain text."""
    charset = "utf-8"
    match = re.search(r"charset=([A-Za-z0-9._-]+)", content_type or "", re.IGNORECASE)
    if match:
        charset = match.group(1)
    try:
        decoded = raw_bytes.decode(charset, errors="replace")
    except LookupError:
        decoded = raw_bytes.decode("utf-8", errors="replace")
    if "<html" in decoded[:2000].lower() or "<!doctype html" in decoded[:2000].lower() or "text/html" in (content_type or "").lower():
        parser = _ReadableHTMLParser()
        parser.feed(decoded)
        parser.close()
        return parser.get_text()
    return decoded[:MAX_CORPUS_CHARS].strip()


def extract_source_ids(text: str) -> dict[str, str]:
    """Map URL -> declared source ID (e.g. F01) from markdown table rows."""
    mapping: dict[str, str] = {}
    for sid, url in ROW_SOURCE_RE.findall(text):
        clean_url = url.rstrip(".,;:!?")
        if clean_url not in mapping:
            mapping[clean_url] = sid
    return mapping


def fetch_corpus_text(url: str, timeout: float, user_agent: str) -> tuple[str | None, str | None]:
    """Fetch bounded GET body and extract readable text for local corpus caching."""
    headers = {
        "User-Agent": user_agent if "Mozilla" in user_agent else f"Mozilla/5.0 ({user_agent})",
        "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.8",
    }
    try:
        req = urllib.request.Request(url, headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            ctype = str(resp.headers.get("Content-Type", ""))
            raw = resp.read(MAX_CORPUS_BYTES)
        return html_to_text(raw, ctype), None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        return None, str(exc)


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
           corpus_dir: Path | None = None) -> dict[str, Any]:
    """Run checks and return the stable report dictionary."""
    if cache_days < 0:
        raise ValueError("cache_days must be nonnegative")
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    raw_index = index.read_text(encoding="utf-8")
    urls = extract_urls(raw_index)
    if not urls:
        raise ValueError("no http(s) URLs found")
    source_ids = extract_source_ids(raw_index)
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
    if corpus_dir is not None:
        corpus_dir.mkdir(parents=True, exist_ok=True)
    now = dt.datetime.now(dt.timezone.utc)
    results = []
    for idx, url in enumerate(urls, 1):
        cached = prior.get(url)
        if cached and not force and cache_fresh(cached, cache_days, now):
            item = {key: value for key, value in cached.items() if key != "cached"}
            item["cached"] = True
        else:
            item = {"url": url, **request_url(url, timeout, user_agent), "checked_at": checked_now(), "cached": False}
        sid = source_ids.get(url) or f"S{idx:02d}"
        if url in source_ids:
            item["source_id"] = sid
        if corpus_dir is not None and item.get("status") == "ok":
            corpus_file = corpus_dir / f"{sid}.txt"
            if force or not corpus_file.is_file():
                text_body, fetch_err = fetch_corpus_text(url, timeout, user_agent)
                if text_body is not None:
                    header = f"# Source {sid}\nURL: {url}\nChecked: {item['checked_at']}\n\n"
                    corpus_file.write_text(header + text_body + "\n", encoding="utf-8")
                elif fetch_err:
                    warnings.append(f"corpus extraction failed for {sid} ({url}): {fetch_err}")
            if corpus_file.is_file():
                content = corpus_file.read_text(encoding="utf-8")
                body_only = content.split("\n\n", 1)[1] if "\n\n" in content else content
                item["corpus_path"] = corpus_file.name
                item["text_chars"] = len(body_only.strip())
                item["corpus_sha256"] = hashlib.sha256(content.encode("utf-8")).hexdigest()
                if item["text_chars"] < 300:
                    warnings.append(
                        f"thin source content ({item['text_chars']} chars) for {sid} ({url}); "
                        "verify that the page contains the cited facts"
                    )
        results.append(item)
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
    parser.add_argument("--corpus-dir", type=Path, help="directory to cache extracted plain-text source snapshots (e.g. sources/corpus)")
    args = parser.parse_args(argv)
    if args.cache_days < 0:
        parser.error("--cache-days must be nonnegative")
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    output = args.output or args.index.with_name("sources-check.json")
    try:
        report = verify(args.index, output, force=args.force, cache_days=args.cache_days,
                        timeout=args.timeout, user_agent=args.user_agent, corpus_dir=args.corpus_dir)
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
