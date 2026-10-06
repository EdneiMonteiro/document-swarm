"""Command line for the deterministic execution package.

Run from the skill root: ``python -m scripts.orchestration <command> ...``.

``init``, ``next``, ``record`` and ``status`` are the whole interface a backend needs.  The backend asks
``next`` what to do, runs the agent tasks it is given (in parallel when there are several), and hands each
result to ``record``.  Everything else (checks, matrix, gate, delivery, retries) happens inside ``next``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    # Run as a directory (python .../scripts/orchestration), from any working directory.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.checks.common import InputError
from scripts.orchestration import metrics
from scripts.orchestration.engine import Engine, Options


def emit(value: Any, *, pretty: bool = False) -> None:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, **({"indent": 2} if pretty else {"separators": (",", ":")}))
    sys.stdout.buffer.write((text + "\n").encode("utf-8"))
    sys.stdout.buffer.flush()


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


def command_init(args: argparse.Namespace) -> int:
    models = [item.strip() for item in args.models.split(",") if item.strip()] if args.models else None
    engine = Engine(args.swarm, Options(max_attempts=args.max_attempts, max_repairs=args.max_repairs))
    emit(engine.init(models=models), pretty=args.pretty)
    return 0


def command_next(args: argparse.Namespace) -> int:
    emit(Engine(args.swarm).next(), pretty=args.pretty)
    return 0


def command_record(args: argparse.Namespace) -> int:
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise InputError(f"stdin is not valid UTF-8: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise InputError(f"stdin is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict) or not all(key in payload for key in ("task_id", "attempt", "inputs_sha256", "result")):
        raise InputError("stdin must be an object with task_id, attempt, inputs_sha256 and result (null when the agent failed)")
    if not isinstance(payload["task_id"], str) or type(payload["attempt"]) is not int \
            or not isinstance(payload["inputs_sha256"], str):
        raise InputError("task_id and inputs_sha256 must be strings and attempt an integer")
    emit(Engine(args.swarm).record(payload["task_id"], payload["attempt"], payload["inputs_sha256"], payload["result"],
                                   payload.get("runtime")), pretty=args.pretty)
    return 0


def command_status(args: argparse.Namespace) -> int:
    emit(Engine(args.swarm).status(), pretty=args.pretty)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m scripts.orchestration", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)

    def swarm_command(name: str, help_text: str, handler: Any) -> argparse.ArgumentParser:
        command = commands.add_parser(name, help=help_text)
        command.add_argument("swarm", type=Path, help="the swarm folder")
        command.add_argument("--pretty", action="store_true", help="indent the JSON answer for reading")
        command.set_defaults(handler=handler)
        return command

    init = swarm_command("init", "validate the swarm and write the execution plan; refuses what it cannot run", command_init)
    init.add_argument("--models", help="comma-separated models available in this session; a declared model outside "
                                       "the list is refused before any agent is paid for")
    init.add_argument("--max-attempts", type=int, default=Options.max_attempts, help="attempts per agent task")
    init.add_argument("--max-repairs", type=int, default=Options.max_repairs,
                      help="repair rounds per cycle for failing mechanical checks")
    swarm_command("next", "advance as far as code can and print what is needed next, or the outcome", command_next)
    swarm_command("record", "validate and persist one agent result read as JSON from stdin", command_record)
    swarm_command("status", "summarise the run from its journal", command_status)

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
