"""Command line for the deterministic execution package.

Run from the skill root: ``python -m scripts.orchestration <command> ...``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from scripts.checks.common import InputError
from scripts.orchestration import metrics


def command_metrics(args: argparse.Namespace) -> int:
    results = metrics.legacy(args.swarm, host_power=args.host_power)
    if not results:
        print("ERROR: no monitor journal found under reports/progress", file=sys.stderr)
        return 2
    if args.execution:
        results = [item for item in results if item["execution_id"].startswith(args.execution)]
        if not results:
            print(f"ERROR: no execution starts with {args.execution}", file=sys.stderr)
            return 2
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print("\n\n".join(metrics.render(item) for item in results))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m scripts.orchestration", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    measure = commands.add_parser("metrics", help="decompose where an execution spent its wall-clock time")
    measure.add_argument("swarm", type=Path)
    measure.add_argument("--execution", help="execution id prefix; default is every recorded execution")
    measure.add_argument("--json", action="store_true")
    measure.add_argument("--host-power", action="store_true",
                         help="Windows only: separate time the host was suspended from time the flow was idle")
    measure.set_defaults(handler=command_metrics)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.handler(args)
    except (OSError, UnicodeError, InputError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
