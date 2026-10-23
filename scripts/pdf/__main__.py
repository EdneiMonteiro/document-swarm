"""Run optional PDF rendering/inspection without importing dependencies for help."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from scripts.checks.common import InputError


def main(argv=None):
    parser = argparse.ArgumentParser(description="Render or inspect an authored Markdown PDF bundle.")
    parser.add_argument("action", choices=["render", "inspect"])
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True, help="new bundle directory for render; existing bundle for inspect")
    parser.add_argument("--profile", choices=["textbook", "technical-report"])
    parser.add_argument("--language", choices=["pt-BR", "pt-PT", "en-US", "en-GB", "es-ES"])
    args = parser.parse_args(argv)
    try:
        from scripts.pdf.engine import inspect, render
    except ModuleNotFoundError as exc:
        print(f"PDF dependencies unavailable ({exc.name}). Install the optional requirements-pdf.txt; core document checks do not require them.", file=sys.stderr)
        return 2
    try:
        result = render(args.source, args.destination, args.profile or "textbook", args.language or "pt-BR") if args.action == "render" else inspect(args.source, args.destination, args.profile, args.language)
    except (InputError, OSError, ValueError, RuntimeError) as exc:
        print(f"ERROR: PDF {args.action} failed: {exc}", file=sys.stderr)
        return 2
    if args.action == "inspect":
        result = {
            "destination": str(args.destination), "status": result["status"],
            "inspection": str(args.destination / "inspection.json"), "page_count": result["page_count"],
            "errors": result["errors"], "editorial_approval": "not_evaluated",
            "source_sha256": result["source_sha256"], "pdf_sha256": result["pdf_sha256"],
            "manifest_sha256": result["manifest_sha256"],
        }
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
