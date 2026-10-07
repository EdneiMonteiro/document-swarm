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
from scripts.checks.gate import APPROVAL_GRADES
from scripts.orchestration import metrics
from scripts.orchestration.engine import Engine, Options


def emit(value: Any, *, pretty: bool = False) -> None:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, **({"indent": 2} if pretty else {"separators": (",", ":")}))
    sys.stdout.buffer.write((text + "\n").encode("utf-8"))
    sys.stdout.buffer.flush()


def command_metrics(args: argparse.Namespace) -> int:
    results = metrics.legacy(args.swarm, host_power=args.host_power) + metrics.executor(args.swarm, host_power=args.host_power)
    if not results:
        print("ERROR: no monitor journal under reports/progress and no executor journal under reports/execution",
              file=sys.stderr)
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


def declared_models(args: argparse.Namespace) -> list[str] | None:
    """The models this session offers, or None when the caller did not say."""
    return [item.strip() for item in args.models.split(",") if item.strip()] if args.models else None


def command_init(args: argparse.Namespace) -> int:
    models = declared_models(args)
    engine = Engine(args.swarm, Options(max_attempts=args.max_attempts, max_repairs=args.max_repairs,
                                        max_cycles=args.max_cycles, approval_grade=args.approval_grade))
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


def backend_for(args: argparse.Namespace, usage_dir: Path | None) -> Any:
    from scripts.orchestration.backend import CopilotCli

    if args.copilot_arg and not args.copilot:
        raise InputError("--copilot-arg only makes sense together with --copilot")
    executable = [args.copilot, *args.copilot_arg] if args.copilot else None
    return CopilotCli(executable=executable, timeout=args.timeout, usage_dir=usage_dir,
                      prune_mcp=not args.keep_mcp_servers)


def command_run(args: argparse.Namespace) -> int:
    from scripts.orchestration import driver

    models = declared_models(args)
    options = Options(max_attempts=args.max_attempts, max_repairs=args.max_repairs, max_cycles=args.max_cycles,
                      approval_grade=args.approval_grade)
    swarm = args.swarm.resolve(strict=True)
    backend = backend_for(args, swarm / "reports" / "execution" / "usage")
    if args.plan_only:
        engine = Engine(swarm, options)
        engine.init(models=models)
        directive = engine.next()
        tasks = [{"label": item["label"], "agent": item["agent"], "kind": item["kind"], "model": item["model"],
                  "reasoning_effort": item["reasoning_effort"], "context_tier": item["context_tier"], "tools": item["tools"],
                  "prompt_bytes": len(item["prompt"].encode("utf-8")),
                  "command": backend.command(item, swarm / "reports" / "execution" / "usage" / f"{item['label']}.json",
                                             backend.mcp_servers())}
                 for item in directive.get("tasks", [])]
        emit({"status": directive["status"], "stage": directive.get("stage"), "tasks": tasks, "spends_credits": False},
             pretty=args.pretty)
        return 0
    out = (lambda text: print(text, file=sys.stderr, flush=True)) if args.json else (lambda text: print(text, flush=True))
    try:
        directive = driver.execute(swarm, backend, options=options, models=models, parallel=args.parallel,
                                   tick=args.tick, beat=min(15.0, args.tick), out=out, backend_name=backend.name)
    except KeyboardInterrupt:
        print("ERROR: interrupted; run the same command again to resume where it stopped", file=sys.stderr)
        return 130
    if args.json:
        emit(directive, pretty=args.pretty)
    if directive["status"] == "done":
        return 0 if directive["outcome"] == "approved" else 1
    if not args.json:
        print(f"{directive['status']}: {directive.get('detail') or directive.get('kind')}", file=sys.stderr)
    return 3


def command_qualify(args: argparse.Namespace) -> int:
    from scripts.orchestration import qualify

    if not args.yes:
        raise InputError("qualify makes real model calls and spends AI credits (about six minimal prompts); "
                         "pass --yes to proceed")
    output = args.output.resolve()
    backend = backend_for(args, output.parent / f"{output.stem}.usage")
    print(f"Qualificando o copilot CLI com o modelo {args.model} (chamadas mínimas, gasto real)...", flush=True)
    report = qualify.run(backend, model=args.model, large_kb=args.large_kb, log=lambda text: print(text, flush=True))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    verdict = "QUALIFICADO" if report["qualified"] else "NÃO qualificado"
    print(f"\n{verdict}: {output}", flush=True)
    for note in report["notes"]:
        print(f"  nota: {note}", flush=True)
    return 0 if report["qualified"] else 1


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
    init.add_argument("--max-cycles", type=int, default=None,
                      help="replace the brief's cycle ceiling; kept in the plan until another is given (raise it here, "
                           "not in the brief: editing the brief makes the current cycle be paid for again)")
    init.add_argument("--approval-grade", choices=APPROVAL_GRADES, default=None,
                      help="the grade every topic and editorial surface must reach; kept in the plan until another is "
                           "given. Without it a swarm keeps the grade it runs under, or takes the skill's current policy "
                           "(A-) when it is new")
    swarm_command("next", "advance as far as code can and print what is needed next, or the outcome", command_next)
    swarm_command("record", "validate and persist one agent result read as JSON from stdin", command_record)
    swarm_command("status", "summarise the run from its journal", command_status)

    def backend_options(command: argparse.ArgumentParser) -> None:
        command.add_argument("--copilot", help="path to the copilot executable (default: the one on PATH)")
        command.add_argument("--copilot-arg", action="append", default=[],
                             help="an argument placed right after --copilot, repeatable (for a wrapper or a test double)")
        command.add_argument("--timeout", type=float, default=3600.0, help="seconds one agent may run before it is stopped")
        command.add_argument("--keep-mcp-servers", action="store_true",
                             help="start every MCP server the CLI is configured with for each agent, as the CLI does "
                                  "by default; without this only the servers whose tools the task lists are started")

    run = swarm_command("run", "run the whole swarm with no coordinator model; each agent is one copilot process", command_run)
    backend_options(run)
    run.add_argument("--models", help="comma-separated models available in this session (see init)")
    run.add_argument("--parallel", type=int, default=4, help="agents running at the same time")
    run.add_argument("--max-attempts", type=int, default=Options.max_attempts, help="attempts per agent task")
    run.add_argument("--max-repairs", type=int, default=Options.max_repairs,
                     help="repair rounds per cycle for failing mechanical checks")
    run.add_argument("--max-cycles", type=int, default=None,
                     help="replace the brief's cycle ceiling; kept in the plan until another is given (raise it here, "
                          "not in the brief: editing the brief makes the current cycle be paid for again)")
    run.add_argument("--approval-grade", choices=APPROVAL_GRADES, default=None,
                     help="the grade every topic and editorial surface must reach; kept in the plan until another is "
                          "given. Without it a swarm keeps the grade it runs under, or takes the skill's current policy "
                          "(A-) when it is new")
    run.add_argument("--tick", type=float, default=60.0, help="seconds between the status tables")
    run.add_argument("--json", action="store_true", help="print the final answer as JSON on stdout, tables on stderr")
    run.add_argument("--plan-only", action="store_true",
                     help="validate, write the plan and show the first agents and their exact commands; runs nothing "
                          "and spends nothing")

    qualify = commands.add_parser("qualify", help="check the copilot CLI backend with a few minimal real calls (spends credits)")
    backend_options(qualify)
    qualify.add_argument("--model", required=True, help="the model for the probes; use the cheapest one available")
    qualify.add_argument("--yes", action="store_true", help="confirm that real calls will be made")
    qualify.add_argument("--large-kb", type=int, default=0, help="also send a prompt of about this many KB through stdin")
    qualify.add_argument("--output", type=Path, default=Path("copilot-cli-qualification.json"),
                         help="where to write the report")
    qualify.set_defaults(handler=command_qualify)

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
