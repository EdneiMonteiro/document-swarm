"""Read-only projection of the deterministic executor's journal for the visual monitor.

The executor already writes what a panel needs to follow it: a journal of what it asked for, started and accepted, and
the heartbeat of the driver that runs it.  This module turns them into the few kinds of event the monitor understands,
so that the panel shows the agents at work without the executor knowing the panel exists and without any channel from
the executor to the extension: the monitor stays a reader.

Every event is derived from the journal alone, in order, so the same journal always gives the same events with the same
ids.  The caller keeps a cursor (the sequence number of the last journal entry it applied, and the epoch of the journal
it belongs to) and asks again from there; a call with a cursor that does not belong to this journal says so instead of
guessing.  Nothing here writes, and nothing a prompt or a result held is passed on: only names, states, times, counts and
the short reason a result was refused.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.checks.common import InputError
from scripts.checks.health import DEFAULT_THRESHOLD, classify_executor, executor_state, read_executor_journal

SCHEMA_VERSION = 1
# One call hands over at most this many journal entries' worth of events; the caller asks again from the cursor it got.
MAX_ENTRIES = 1500
PHASE_OF_STAGE = {"authors": "authors", "consolidation": "consolidation", "reviewers": "reviews",
                  "rubber-duck": "rubber-duck", "narrative": "delivery"}
PHASE_OF_SCRIPT = {"sources": "sources", "tables": "tables", "nomenclature": "tables"}
SCRIPT_NAMES = {"sources": "fontes", "tables": "tabelas", "nomenclature": "nomenclatura"}
STEP_NAMES = {"sources": "rechecagem final das fontes", "report": "relatório final", "narrative": "narrativa",
              "memory": "proposta de memória"}
GATE_RESULTS = {0: "aprovado", 1: "reprovado", 2: "escalar: teto de ciclos atingido", 3: "inválido"}
OUTCOMES = {"approved": "completed", "escalated": "escalated"}
STAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?Z$")
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
TASK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")
TASK_ID = re.compile(r"^c(\d{1,4})\.r(\d{1,4})\.([a-z-]{1,40})\.([A-Za-z0-9][A-Za-z0-9._-]{0,99})$")


def number(value: Any) -> int | None:
    return value if type(value) is int and 0 <= value < 10**9 else None


def label(value: Any, limit: int = 300) -> str:
    return " ".join(str(value).split())[:limit] if isinstance(value, (str, int, float)) else ""


def seconds_text(value: Any) -> str:
    return f"{float(value):.0f} s" if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0 else ""


def keyed(value: Any) -> Any:
    """A journal field that is looked up in a table: a text or an integer, never a bool, a list or an object.

    The journal is read back from disk, where a field the engine wrote as a word may hold anything, and an unhashable
    value would end the projection of every other entry with a traceback.
    """
    return value if isinstance(value, str) or type(value) is int else None


class Projector:
    """Fold the journal into monitor events, one journal entry at a time, in the order the executor wrote them."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self.per_seq: dict[int, int] = {}
        self.cycle = 0
        self.phase_name = ""
        self.last_at = ""
        self.open: dict[tuple[str, int], dict[str, Any]] = {}
        self.accepted: dict[tuple[int, str], list[str]] = {}
        self.edges: set[tuple[int, str, str, str]] = set()
        self.counts = {"issued": 0, "accepted": 0, "rejected": 0, "null": 0, "repairs": 0}
        self.finish: dict[str, Any] | None = None
        self.max_seq = 0
        self.epoch = ""

    # -- emitting -----------------------------------------------------------
    def emit(self, seq: int, at: str, kind: str, data: dict[str, Any], cycle: int | None = None) -> None:
        count = self.per_seq.get(seq, 0)
        self.per_seq[seq] = count + 1
        self.events.append({"seq": seq, "n": count, "at": at, "type": kind,
                            "cycle": self.cycle if cycle is None else cycle, "data": data})

    def note(self, seq: int, at: str, kind: str, text: str, cycle: int | None = None) -> None:
        self.emit(seq, at, "executor", {"kind": kind, "label": text}, cycle)

    def phase(self, seq: int, at: str, phase: str, cycle: int) -> None:
        # The cycle of a panel only moves forward, and a phase already shown is not announced again.
        if cycle < self.cycle or (cycle == self.cycle and phase == self.phase_name):
            return
        self.cycle, self.phase_name = cycle, phase
        self.emit(seq, at, "phase", {"phase": phase, "cycle": cycle}, cycle)

    def edge(self, seq: int, at: str, source: str, target: str, text: str, cycle: int) -> None:
        key = (cycle, source, target, text)
        if source != target and key not in self.edges:
            self.edges.add(key)
            self.emit(seq, at, "handoff", {"from": source, "to": target, "label": text, "cycle": cycle}, cycle)

    # -- the journal --------------------------------------------------------
    def feed(self, seq: int, item: dict[str, Any]) -> None:
        kind = item.get("event")
        stamp = item.get("at")
        at = stamp if isinstance(stamp, str) and STAMP.match(stamp) else self.last_at
        self.last_at = at
        self.max_seq = seq
        if not self.epoch and at:
            self.epoch = at
        cycle = number(item.get("cycle"))
        handler = getattr(self, f"on_{kind}", None) if isinstance(kind, str) and NAME.match(kind) else None
        if handler is not None:
            handler(seq, at, item, cycle)

    def on_run_started(self, seq: int, at: str, item: dict[str, Any], cycle: int | None) -> None:
        self.phase(seq, at, "setup", 0)
        self.note(seq, at, "run", "Executor iniciado")

    def on_plan_refreshed(self, seq: int, at: str, item: dict[str, Any], cycle: int | None) -> None:
        self.note(seq, at, "run", "Plano atualizado")

    def on_plan_recovered(self, seq: int, at: str, item: dict[str, Any], cycle: int | None) -> None:
        self.note(seq, at, "policy", f"Plano recomeçado por quem declarou a política ({label(item.get('approval_grade'), 4)})")

    def on_max_cycles_changed(self, seq: int, at: str, item: dict[str, Any], cycle: int | None) -> None:
        self.note(seq, at, "policy", f"Teto de ciclos alterado: {label(item.get('previous'), 6)} → {label(item.get('current'), 6)}")

    def on_approval_grade_changed(self, seq: int, at: str, item: dict[str, Any], cycle: int | None) -> None:
        self.note(seq, at, "policy",
                  f"Nota de aprovação alterada: {label(item.get('previous'), 4)} → {label(item.get('current'), 4)}")

    def on_task_issued(self, seq: int, at: str, item: dict[str, Any], cycle: int | None) -> None:
        task, attempt, round_number = item.get("task_id"), number(item.get("attempt")), number(item.get("round")) or 0
        agent, stage = item.get("agent"), item.get("stage")
        if not (isinstance(task, str) and TASK.match(task) and attempt and cycle and isinstance(agent, str)
                and NAME.match(agent) and isinstance(stage, str) and NAME.match(stage)):
            return
        identity = item.get("inputs_sha256") if isinstance(item.get("inputs_sha256"), str) else None
        self.open_dispatch(seq, at, task, attempt, identity, agent, stage, cycle, round_number)

    def open_dispatch(self, seq: int, at: str, task: str, attempt: int, identity: str | None, agent: str, stage: str,
                      cycle: int, round_number: int) -> dict[str, Any]:
        # A task and attempt have one live ask.  A later ask for the same task and attempt replaces the earlier one, whose
        # result the engine would refuse as stale, so the earlier one no longer leads to anything.
        older = self.open.get((task, attempt))
        if older is not None:
            self.emit(seq, at, "runtime", {"dispatch_id": older["id"], "status": "cancelled", "outcome": "superseded",
                                           "ended_at": at}, older["cycle"])
        dispatch = {"id": f"x{seq}", "task": task, "attempt": attempt, "identity": identity, "agent": agent,
                    "stage": stage, "cycle": cycle, "round": round_number, "issued": at, "started": None}
        self.open[(task, attempt)] = dispatch
        self.counts["issued"] += 1
        phase = PHASE_OF_STAGE.get(stage)
        if phase:
            self.phase(seq, at, phase, cycle)
        self.emit(seq, at, "dispatch", {"id": dispatch["id"], "agent_id": agent, "cycle": cycle, "round": round_number,
                                        "stage": stage, "attempt": attempt, "label": f"{task}.a{attempt}",
                                        "executor_task": task, "identity": (identity or "")[:12] or None}, cycle)
        self.handoffs(seq, at, agent, stage, cycle, round_number)
        return dispatch

    def adopt(self, seq: int, at: str, item: dict[str, Any]) -> dict[str, Any] | None:
        """A task that started or was recorded with no issue before it becomes a dispatch of its own.

        An engine that did not journal an issue for a task asked again under other inputs left records like that, and
        nothing the executor recorded should be missing from the panel.  The identity of the task names its cycle,
        round, stage and agent, so none of them has to be guessed.
        """
        task, attempt = item.get("task_id"), number(item.get("attempt"))
        match = TASK_ID.match(task) if isinstance(task, str) else None
        if match is None or not attempt:
            return None
        identity = item.get("inputs_sha256") if isinstance(item.get("inputs_sha256"), str) else None
        if int(match.group(1)) < 1:
            return None
        return self.open_dispatch(seq, at, task, attempt, identity, match.group(4), match.group(3), int(match.group(1)),
                                  int(match.group(2)))

    def handoffs(self, seq: int, at: str, agent: str, stage: str, cycle: int, round_number: int) -> None:
        """The artifacts that pass between roles, as the executor's stages pass them."""
        done = self.accepted
        if stage == "consolidation":
            for author in done.get((cycle, "authors"), []):
                self.edge(seq, at, author, agent, "seções", cycle)
        elif stage == "reviewers":
            for coordinator in done.get((cycle, "consolidation"), []):
                self.edge(seq, at, coordinator, agent, "documento consolidado", cycle)
        elif stage == "rubber-duck":
            for reviewer in done.get((cycle, "reviewers"), []):
                self.edge(seq, at, reviewer, agent, "avaliações", cycle)
        elif stage == "narrative":
            for auditor in done.get((cycle, "rubber-duck"), []):
                self.edge(seq, at, auditor, agent, "auditoria e veredito", cycle)
        elif stage == "authors" and cycle > 1 and round_number == 0:
            for reviewer in done.get((cycle - 1, "reviewers"), []):
                self.edge(seq, at, reviewer, agent, f"achados do ciclo {cycle - 1}", cycle)

    def pick(self, item: dict[str, Any]) -> dict[str, Any] | None:
        """The live ask a start or a record belongs to; a start or a record for other inputs belongs to none of them."""
        task, attempt = item.get("task_id"), number(item.get("attempt"))
        dispatch = self.open.get((task, attempt)) if isinstance(task, str) else None
        identity = item.get("inputs_sha256") if isinstance(item.get("inputs_sha256"), str) else None
        if dispatch is None or (identity and dispatch["identity"] and identity != dispatch["identity"]):
            return None
        return dispatch

    def on_task_started(self, seq: int, at: str, item: dict[str, Any], cycle: int | None) -> None:
        dispatch = self.pick(item) or self.adopt(seq, at, item)
        if dispatch is None:
            return
        dispatch["started"] = at
        self.emit(seq, at, "runtime", {"dispatch_id": dispatch["id"], "status": "running", "started_at": at},
                  dispatch["cycle"])

    def on_task_recorded(self, seq: int, at: str, item: dict[str, Any], cycle: int | None) -> None:
        dispatch = self.pick(item) or self.adopt(seq, at, item)
        if dispatch is None:
            return
        outcome = item.get("outcome") if item.get("outcome") in ("accepted", "rejected", "null") else "rejected"
        del self.open[(dispatch["task"], dispatch["attempt"])]
        self.counts["accepted" if outcome == "accepted" else outcome] += 1
        if outcome == "accepted":
            self.accepted.setdefault((dispatch["cycle"], dispatch["stage"]), []).append(dispatch["agent"])
        runtime = item.get("runtime") if isinstance(item.get("runtime"), dict) else {}
        errors = item.get("errors") if isinstance(item.get("errors"), list) else []
        data: dict[str, Any] = {"dispatch_id": dispatch["id"], "status": "completed" if outcome == "accepted" else "failed",
                                "outcome": outcome, "ended_at": at}
        # The seconds of the process itself.  The record's own `seconds` run from the issue and take in the wait for a
        # slot and any stop of the executor, so a task issued before a pause would be shown as long as the pause.
        took = runtime.get("seconds")
        if isinstance(took, (int, float)) and not isinstance(took, bool) and 0 <= took < 10**7:
            data["seconds"] = round(float(took), 1)
        model = label(runtime.get("models_seen"), 100).split(",")[0].strip()
        if model:
            data["observed_model"] = model
        reason = label(errors[0], 280) if errors else ""
        cause = label(runtime.get("error"), 60)
        if reason or cause:
            data["error"] = reason or cause
        if cause and reason:
            data["cause"] = cause
        self.emit(seq, at, "runtime", data, dispatch["cycle"])

    def on_script_finished(self, seq: int, at: str, item: dict[str, Any], cycle: int | None) -> None:
        script = keyed(item.get("script"))
        code = keyed(item.get("exit_code"))
        if cycle is not None and script in PHASE_OF_SCRIPT:
            self.phase(seq, at, PHASE_OF_SCRIPT[script], cycle)
        verdict = "sem achados" if code == 0 else "com achados" if code == 1 else f"falhou (saída {label(code, 4)})"
        took = seconds_text(item.get("seconds"))
        self.note(seq, at, "script", f"Verificação de {SCRIPT_NAMES.get(script, label(script, 30))}: {verdict}"
                  + (f" ({took})" if took else ""), cycle)

    def on_matrix_written(self, seq: int, at: str, item: dict[str, Any], cycle: int | None) -> None:
        self.note(seq, at, "matrix", "Matriz da rodada gravada "
                  + ("com a auditoria" if item.get("duck_recorded") is True else "sem a auditoria registrada"), cycle)

    def on_repair_started(self, seq: int, at: str, item: dict[str, Any], cycle: int | None) -> None:
        self.counts["repairs"] += 1
        found = number(item.get("items"))
        self.note(seq, at, "repair", f"Rodada de reparo {number(item.get('round')) or '?'}: "
                  f"{found if found is not None else '?'} pendência(s) mecânica(s)", cycle)

    def on_gate_run(self, seq: int, at: str, item: dict[str, Any], cycle: int | None) -> None:
        if cycle is not None:
            self.phase(seq, at, "gate", cycle)
        code = keyed(item.get("exit_code"))
        self.note(seq, at, "gate", f"Gate executado: {GATE_RESULTS.get(code, 'saída ' + label(code, 4))}", cycle)

    def on_verdict_withdrawn(self, seq: int, at: str, item: dict[str, Any], cycle: int | None) -> None:
        self.note(seq, at, "verdict", f"Veredito retirado ({label(item.get('recorded'), 20)}): "
                  "não se reproduz a partir das notas dos revisores", cycle)

    def on_delivery_step(self, seq: int, at: str, item: dict[str, Any], cycle: int | None) -> None:
        self.phase(seq, at, "delivery", self.cycle)
        step = keyed(item.get("step"))
        step = STEP_NAMES.get(step, label(step, 30))
        self.note(seq, at, "delivery", f"Entrega: {step} " + ("concluída" if item.get("ok") is True else "falhou"))

    def on_task_stale(self, seq: int, at: str, item: dict[str, Any], cycle: int | None) -> None:
        self.note(seq, at, "refusal", f"Resultado de {label(item.get('task_id'), 120)} recusado: a tarefa mudou desde a emissão")

    def on_run_finished(self, seq: int, at: str, item: dict[str, Any], cycle: int | None) -> None:
        outcome = keyed(item.get("outcome"))
        text = {"approved": "aprovado", "escalated": "escalado"}.get(outcome, label(outcome, 20))
        self.note(seq, at, "run", f"Execução encerrada: {text}" + (f" no ciclo {cycle}" if cycle else ""), cycle)
        self.finish = {"seq": seq, "at": at, "status": OUTCOMES.get(outcome), "cycle": cycle}


def project(root: Path, *, after: int = 0, epoch: str | None = None, limit: int = MAX_ENTRIES,
            threshold: int = DEFAULT_THRESHOLD) -> dict[str, Any]:
    """The events of the executor's journal after ``after``, with who is running and how healthy the run is."""
    root = root.resolve(strict=True)
    journal = root / "reports" / "execution" / "journal.jsonl"
    if not journal.is_file():
        return {"schema_version": SCHEMA_VERSION, "present": False}
    resolved = journal.resolve(strict=True)
    if root not in resolved.parents:
        raise InputError("the executor journal escapes the swarm")
    entries, skipped = read_executor_journal(resolved)
    projector = Projector()
    last = 0
    for index, item in enumerate(entries, 1):
        # The cursor is a sequence number, so whatever the file says each entry gets one past the one before it, or more.
        last = max(number(item.get("seq")) or index, last + 1)
        projector.feed(last, item)
    final = entries[-1] if entries else None
    if projector.finish and final is not None and final.get("event") == "run_finished" and projector.finish["status"]:
        data = {"status": projector.finish["status"]}
        projector.emit(projector.finish["seq"], projector.finish["at"], "finish", data, projector.finish["cycle"])
    reset = bool(epoch and projector.epoch and epoch != projector.epoch) or after > projector.max_seq
    fresh = [] if reset else [event for event in projector.events if event["seq"] > after]
    more = False
    cursor = projector.max_seq
    if len(fresh) > limit:
        boundary = fresh[limit - 1]["seq"]
        while limit < len(fresh) and fresh[limit]["seq"] == boundary:
            limit += 1
        more = limit < len(fresh)
        fresh = fresh[:limit]
        cursor = boundary if more else projector.max_seq
    elif reset:
        cursor = after
    executor = executor_state(root)
    health = ({"state": "unobserved", "reason": "o journal do executor não pôde ser lido"} if executor is None else
              dict(zip(("state", "reason"), classify_executor(executor, threshold))))
    health["threshold_seconds"] = threshold
    driver = executor["driver"] if executor else None
    running = [{"agent": item["agent"], "label": f"{item['task']}.a{item['attempt']}", "stage": item["stage"],
                "cycle": item["cycle"], "state": "running" if item["started"] else "queued",
                "since": item["started"] or item["issued"]}
               for item in projector.open.values()]
    return {
        "schema_version": SCHEMA_VERSION, "present": True, "epoch": projector.epoch, "cursor": cursor,
        "reset": reset, "more": more, "skipped_lines": skipped, "events": fresh,
        "summary": {
            "finished": bool(executor and executor["finished"]), "outcome": executor["outcome"] if executor else None,
            "cycle": projector.cycle, "counts": projector.counts, "running": sorted(running, key=lambda row: row["since"]),
            "driver": None if not driver else {
                key: driver.get(key) for key in ("state", "stage", "cycle", "detail", "backend", "pid", "age_seconds")},
        },
        "health": health,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Project the executor's journal and heartbeat for the local progress "
                                                 "monitor; never modifies the swarm.")
    parser.add_argument("swarm", type=Path)
    parser.add_argument("--after", type=int, default=0, help="sequence number of the last journal entry already applied")
    parser.add_argument("--epoch", default=None, help="epoch of the journal the cursor belongs to")
    parser.add_argument("--limit", type=int, default=MAX_ENTRIES)
    parser.add_argument("--threshold", type=int, default=DEFAULT_THRESHOLD)
    args = parser.parse_args(argv)
    if args.after < 0 or args.limit < 1 or args.threshold < 30:
        print("ERROR: --after must not be negative, --limit at least 1 and --threshold at least 30", file=sys.stderr)
        return 2
    try:
        result = project(args.swarm, after=args.after, epoch=args.epoch, limit=args.limit, threshold=args.threshold)
    except (OSError, UnicodeError, InputError) as exc:
        print(f"ERROR: cannot project the executor journal: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
