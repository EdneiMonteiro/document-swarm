"""Run a swarm to its outcome with no coordinator model.

``execute`` composes the engine with a backend and gives a person something to look at: a table every
minute and a heartbeat file the health command reads.  Nothing here decides anything about the document.
The engine says what is needed, the backend runs the agents, and the engine records what came back.
"""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from scripts.orchestration.engine import Engine, Options
from scripts.orchestration.store import atomic_json

HEARTBEAT = "reports/execution/driver.json"
STATES = {"running": "em andamento", "waiting": "aguardando", "done": "encerrado", "blocked": "bloqueado",
          "failed": "falhou", "interrupted": "interrompido"}


def stamp(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def elapsed(seconds: float) -> str:
    seconds = max(0.0, seconds)
    if seconds < 90:
        return f"{seconds:.0f} s"
    if seconds < 5400:
        return f"{seconds / 60:.0f} min"
    return f"{seconds / 3600:.1f} h"


class Monitor:
    """The live view of one run: who is running, a heartbeat on disk and a table on the terminal."""

    def __init__(self, swarm: Path, *, backend: str, parallel: int, tick: float = 60.0, beat: float = 15.0,
                 out: Callable[[str], None] = print, clock: Callable[[], float] = time.time) -> None:
        self.swarm = swarm
        self.backend = backend
        self.parallel = parallel
        self.tick, self.beat = tick, beat
        self.out = out
        self.clock = clock
        self.guard = threading.Lock()
        self.write_guard = threading.Lock()
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.started = clock()
        self.state = "waiting"
        self.stage = ""
        self.cycle: int | None = None
        self.running: dict[str, dict[str, Any]] = {}
        self.last: str = ""
        self.last_at = clock()
        self.detail = ""
        self.closed = False

    # -- events from the engine ---------------------------------------------
    def event(self, name: str, data: dict[str, Any]) -> None:
        with self.guard:
            if self.closed:
                return  # a worker that began after the run ended must not write over its final state
            now = self.clock()
            if name == "directive":
                directive = data["directive"]
                status = directive["status"]
                self.cycle = directive.get("cycle", self.cycle)
                self.stage = directive.get("stage") or directive.get("kind") or self.stage
                if status == "agents":
                    self.state = "running"
                elif status == "done":
                    self.state, self.detail = "done", f"{directive.get('outcome')} no ciclo {directive.get('cycle')}"
                elif status == "blocked":
                    self.state, self.detail = "blocked", str(directive.get("detail") or directive.get("kind") or "")
                else:
                    self.state, self.detail = "failed", str(directive.get("detail") or directive.get("kind") or "")
            elif name == "started":
                task = data["task"]
                self.running[task["label"]] = {"agent": task["agent"], "since": now, "model": task.get("model")}
            elif name == "finished":
                task, outcome = data["task"], data["outcome"]
                self.running.pop(task["label"], None)
                verdict = "aceito" if outcome.get("accepted") else ("recusado, nova tentativa" if outcome.get("retry") else "recusado")
                self.last, self.last_at = f"{task['agent']} {verdict}", now
            elif name == "failed":
                task = data["task"]
                self.running.pop(task["label"], None)
                self.last, self.last_at = f"{task['agent']}: o resultado não pôde ser registrado ({data.get('error', '')})", now
        self.write_heartbeat()

    # -- heartbeat and table -------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        with self.guard:
            now = self.clock()
            return {
                "schema_version": 1, "pid": os.getpid(), "backend": self.backend, "parallel": self.parallel,
                "state": self.state, "stage": self.stage, "cycle": self.cycle, "detail": self.detail,
                "started_at": stamp(self.started), "updated_at": stamp(now),
                "running": [{"task": label, "agent": item["agent"], "since": stamp(item["since"]),
                             "model": item["model"]} for label, item in sorted(self.running.items())],
                "last": self.last, "last_at": stamp(self.last_at),
            }

    def write_heartbeat(self) -> None:
        # The snapshot is taken inside the write lock: otherwise a thread that took its snapshot earlier could write
        # it after a later state (the final one) had been written, and the file would end up saying "running".
        with self.write_guard:
            try:
                atomic_json(self.swarm / HEARTBEAT, self.snapshot())
            except OSError:
                pass  # a locked or missing file must not stop a paid run; the next beat tries again

    def table(self) -> str:
        counts = {"accepted": 0, "rejected": 0, "null_results": 0, "repairs": 0}
        try:
            status = Engine(self.swarm).status()
            counts = {key: status[key] for key in counts}
            swarm_id, ceiling = status["swarm"], status.get("max_cycles")
        except Exception:  # the table is a convenience; a half-written journal must not break it
            swarm_id, ceiling = self.swarm.name, None
        with self.guard:
            now = self.clock()
            running = [f"{item['agent']} {elapsed(now - item['since'])}" for _, item in sorted(self.running.items())]
            rows = [
                ("Etapa", f"ciclo {self.cycle if self.cycle is not None else '?'}"
                          f"{f' de {ceiling}' if ceiling else ''} · {self.stage or 'iniciando'}"),
                ("Agentes", f"{' · '.join(running)}  ({len(running)} de {self.parallel} simultâneos)" if running
                 else "nenhum em execução agora"),
                ("Resultados", f"{counts['accepted']} aceitos · {counts['rejected']} recusados · "
                               f"{counts['null_results']} sem resposta · {counts['repairs']} reparos"),
                ("Último registro", f"há {elapsed(now - self.last_at)} · {self.last}" if self.last else "nenhum ainda"),
            ]
            if self.detail:
                rows.append(("Situação", self.detail))
            head = (f"Document Swarm · {swarm_id} · executor determinístico  [{STATES.get(self.state, self.state)}]"
                    f"  {datetime.fromtimestamp(now).strftime('%H:%M:%S')}")
        return "\n".join([head, ""] + [f"{label:<16}{value}" for label, value in rows])

    # -- the ticker ----------------------------------------------------------
    def start(self) -> None:
        self.write_heartbeat()
        self.thread = threading.Thread(target=self.loop, name="docswarm-monitor", daemon=True)
        self.thread.start()

    def loop(self) -> None:
        last_table = self.clock()
        while not self.stop_event.wait(self.beat):
            self.write_heartbeat()
            if self.clock() - last_table >= self.tick:
                last_table = self.clock()
                try:
                    self.out(self.table())
                except Exception:  # a closed terminal must not end the run
                    pass

    def finish(self, state: str | None = None, detail: str = "") -> None:
        with self.guard:
            self.closed = True
            if state:
                self.state = state
            if detail:
                self.detail = detail
            self.running.clear()
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=5)
        self.write_heartbeat()
        try:
            self.out(self.table())
        except Exception:
            pass


def execute(swarm: Path, backend: Callable[[dict[str, Any]], Any], *, options: Options | None = None,
            models: list[str] | None = None, parallel: int = 4, tick: float = 60.0, beat: float = 15.0,
            out: Callable[[str], None] = print, backend_name: str = "backend") -> dict[str, Any]:
    """Run ``swarm`` until the engine reports an outcome or needs a person."""
    engine = Engine(swarm, options or Options())
    # The run lock comes first.  A second run that is refused must not have written its own heartbeat over the one
    # the first run keeps, which is what ``health`` reads to tell a live run from a dead one.
    with engine.hold_run():
        monitor = Monitor(engine.root, backend=backend_name, parallel=parallel, tick=tick, beat=beat, out=out)
        monitor.start()
        try:
            directive = engine.drive(backend, parallel=parallel, models=models, on_event=monitor.event)
        except KeyboardInterrupt:
            # The engine has already stopped the agents that were running: that is where they are known.
            monitor.finish("interrupted", "interrompido pelo usuário; execute o mesmo comando para retomar de onde parou")
            raise
        except BaseException as exc:
            monitor.finish("failed", f"{type(exc).__name__}: {exc}")
            raise
        monitor.finish()
        return directive
