"""Compose the health table of a running swarm from measurements, never guesses.

Three independent observations feed the table: the artifacts on disk, the
projection of the next deterministic step and, when the monitor extension is
running, its view of the session.  A missing observation is printed as not
observed; it is never replaced by a plausible value.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.checks.common import InputError, parse_strict_json
from scripts.checks.progress import snapshot
from scripts.checks.resume import project

DEFAULT_THRESHOLD = 180
STATES = ("active", "waiting", "stalled", "closed", "unobserved", "invalid")
LABELS = {
    "active": "em andamento", "waiting": "aguardando", "stalled": "parado",
    "closed": "encerrado", "unobserved": "não observado", "invalid": "artefatos inválidos",
}
SELF_WRITTEN = re.compile(r"^(health|resume|snapshot|owner)\.json$|^events\.jsonl$|^torn-|\.tmp$")
MAX_SCANNED = 20000


def elapsed(seconds: float | None) -> str:
    if seconds is None:
        return "não observado"
    seconds = max(0.0, seconds)
    if seconds < 90:
        return f"há {seconds:.0f} s"
    if seconds < 5400:
        return f"há {seconds / 60:.0f} min"
    return f"há {seconds / 3600:.1f} h"


def newest_artifact(root: Path) -> tuple[float | None, str | None]:
    """Return the age and name of the most recent artifact the swarm itself wrote."""
    newest: tuple[float, Path] | None = None
    scanned = 0
    for path in root.rglob("*"):
        scanned += 1
        if scanned > MAX_SCANNED:
            raise InputError("the swarm directory exceeds the supported file count")
        if not path.is_file() or path.is_symlink() or SELF_WRITTEN.search(path.name):
            continue
        try:
            stamp = path.stat().st_mtime
        except OSError:
            continue
        if newest is None or stamp > newest[0]:
            newest = (stamp, path)
    if newest is None:
        return None, None
    return max(0.0, time.time() - newest[0]), newest[1].relative_to(root).as_posix()


def monitor_health(root: Path) -> dict[str, Any] | None:
    """Read the most recent health file published by the monitor extension."""
    candidates = sorted((root / "reports" / "progress").glob("*/health.json"))
    best: tuple[float, dict[str, Any]] | None = None
    for path in candidates:
        try:
            if path.stat().st_size > 1024 * 1024:
                continue
            data = parse_strict_json(path.read_text(encoding="utf-8"), name=path.name)
        except (OSError, UnicodeError, InputError):
            continue
        if not isinstance(data, dict) or data.get("schema_version") != 1:
            continue
        stamp = path.stat().st_mtime
        if best is None or stamp > best[0]:
            best = (stamp, {**data, "file_age_seconds": max(0.0, time.time() - stamp)})
    return best[1] if best else None


def current(monitor: dict[str, Any] | None, threshold: int) -> tuple[dict[str, Any] | None, str]:
    """Discard a health file the extension stopped refreshing; say so explicitly."""
    if monitor is None:
        return None, ""
    if monitor.get("file_age_seconds", 0.0) > max(90.0, threshold / 2):
        return None, "; a extensão parou de publicar saúde"
    return monitor, ""


def classify(resume: dict[str, Any], monitor: dict[str, Any] | None,
             artifact_age: float | None, threshold: int, lost: str = "") -> tuple[str, str]:
    """Decide the health state and say which observation supports it.

    The age of the newest observation decides; the session label only explains.
    A wedged agent loop keeps reporting ``processing`` forever, so trusting the
    label over the age is exactly how a stall stays invisible.
    """
    if resume.get("complete"):
        return "closed", "o ciclo aprovado tem todos os artefatos de entrega"
    if any(item["kind"] in ("escalation", "max_cycles") for item in resume.get("blocked", [])):
        return "closed", "a execução está escalada ao usuário; o vigia não decide por ele"
    if monitor is None:
        if artifact_age is None:
            return "unobserved", f"não há extensão de monitoramento nem artefatos datados{lost}"
        if artifact_age > threshold:
            return "stalled", f"nenhum artefato novo {elapsed(artifact_age)}, sem observação da sessão{lost}"
        return "waiting", f"último artefato {elapsed(artifact_age)}, sem observação da sessão{lost}"
    session = monitor.get("session_activity") or {}
    inactive = monitor.get("inactive_seconds")
    running = (monitor.get("dispatches") or {}).get("running") or 0
    if monitor.get("state") == "unobserved" or (session.get("observed_at") and session.get("stale") is True):
        return "unobserved", "a extensão perdeu a observação; o estado anterior está desatualizado"
    if isinstance(inactive, (int, float)):
        if inactive > threshold * 3:
            return "stalled", f"nada observado {elapsed(float(inactive))}, além do triplo do limiar"
        if inactive > threshold and not running:
            return "stalled", f"a sessão está sem sinal {elapsed(float(inactive))} e nenhum agente executa"
    if session.get("status") == "processing":
        return "active", "a sessão está processando dentro do limiar"
    if artifact_age is not None and artifact_age > threshold and not running:
        return "stalled", f"nenhum artefato novo {elapsed(artifact_age)} e nenhum despacho em curso"
    return "waiting", "há trabalho em curso dentro do limiar"


def compose(swarm: Path, threshold: int = DEFAULT_THRESHOLD) -> dict[str, Any]:
    """Return the measured health record of one swarm."""
    root = swarm.resolve(strict=True)
    if threshold < 30 or threshold > 86400:
        raise InputError("the inactivity threshold must be between 30 and 86400 seconds")
    try:
        data = snapshot(root)
        resume = project(root)
    except (OSError, UnicodeError, InputError, json.JSONDecodeError) as exc:
        return {"schema_version": 1, "swarm": root.name, "state": "invalid",
                "reason": str(exc), "threshold_seconds": threshold,
                "next": [], "blocked": [{"kind": "artifact", "detail": str(exc)}]}
    artifact_age, artifact_name = newest_artifact(root)
    published = monitor_health(root)
    monitor, lost = current(published, threshold)
    state, reason = classify(resume, monitor, artifact_age, threshold, lost)
    cycle = resume["cycle"]
    recorded = {item["cycle"]: item for item in data["cycles"]}.get(cycle, {})
    return {
        "schema_version": 1,
        "swarm": data["swarm_id"],
        "title": data["title"],
        "execution_id": (published or {}).get("execution_id"),
        "state": state,
        "reason": reason,
        "threshold_seconds": threshold,
        "cycle": cycle,
        "max_cycles": data["max_cycles"],
        "phase": resume["phase"],
        "session": {
            "observed": monitor is not None,
            "status": ((monitor or {}).get("session_activity") or {}).get("status"),
            "inactive_seconds": (monitor or {}).get("inactive_seconds"),
        },
        "artifacts": {"newest": artifact_name, "age_seconds": artifact_age},
        "dispatches": (monitor or {}).get("dispatches"),
        "checks": {
            "sources": data["sources"]["status"],
            "tables": recorded.get("tables", {}).get("status", "pending"),
            "gate": recorded.get("gate", {}).get("status", "not_recorded"),
            "gate_outcome": recorded.get("gate", {}).get("outcome"),
        },
        "next": resume["next"],
        "blocked": resume["blocked"],
        "complete": resume["complete"],
    }


def render(record: dict[str, Any]) -> str:
    """Render the terminal table from the record, with no invented values."""
    state = record["state"]
    head = f"Document Swarm · {record.get('swarm', 'desconhecido')}"
    if record.get("execution_id"):
        head += f" · execução {str(record['execution_id'])[:8]}"
    lines = [f"{head}  [{LABELS.get(state, state)}]", ""]
    if state == "invalid":
        lines.append(f"{'Diagnóstico':<18}{record.get('reason', 'artefatos inválidos')}")
        return "\n".join(lines)
    session = record["session"]
    if not session["observed"]:
        session_text = "não observada (extensão do monitor ausente)"
    elif session["inactive_seconds"] is None:
        session_text = f"{session['status'] or 'desconhecida'}, sem carimbo de inatividade"
    else:
        session_text = f"{session['status'] or 'desconhecida'}, sem sinal {elapsed(session['inactive_seconds'])}"
    dispatches = record.get("dispatches") or {}
    agents_text = ("não observados" if not dispatches else
                   " · ".join(f"{count} {name}" for name, count in sorted(dispatches.items()) if count))
    checks = record["checks"]
    gate_text = checks["gate"] if not checks.get("gate_outcome") else f"{checks['gate']} ({checks['gate_outcome']})"
    rows = [
        ("Fase/Ciclo", f"{record['phase']} · ciclo {record['cycle']} de {record['max_cycles']}"),
        ("Sessão", f"{session_text} (limiar {record['threshold_seconds']} s)"),
        ("Agentes", agents_text or "nenhum em curso"),
        ("Artefatos", f"{record['artifacts']['newest'] or 'nenhum'} {elapsed(record['artifacts']['age_seconds'])}"),
        ("Checks do ciclo", f"fontes {checks['sources']} · tabelas {checks['tables']} · gate {gate_text}"),
        ("Diagnóstico", record["reason"]),
    ]
    actions = record["next"]
    if record["complete"]:
        rows.append(("Ação", "nenhuma: a entrega está completa"))
    elif record["blocked"] and not actions:
        rows.append(("Ação", "nenhuma: " + record["blocked"][0]["detail"]))
    elif actions:
        targets = ", ".join(str(item["target"]) for item in actions[:4])
        more = f" (+{len(actions) - 4})" if len(actions) > 4 else ""
        rows.append(("Próximo passo", f"{actions[0]['action']} {targets}{more} — {actions[0]['reason']}"))
    else:
        rows.append(("Próximo passo", "nenhum passo determinístico pendente"))
    for label, value in rows:
        lines.append(f"{label:<18}{value}")
    if record["blocked"]:
        lines.append("")
        for item in record["blocked"][:5]:
            lines.append(f"  ! {item['detail']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Report the measured health of a swarm; never modifies it.")
    parser.add_argument("swarm", type=Path)
    parser.add_argument("--threshold", type=int, default=DEFAULT_THRESHOLD,
                        help="seconds without progress before the swarm is reported as stalled")
    parser.add_argument("--json", action="store_true", help="emit the record instead of the table")
    args = parser.parse_args(argv)
    try:
        record = compose(args.swarm, args.threshold)
    except (OSError, UnicodeError, InputError) as exc:
        print(f"ERROR: cannot read the swarm health: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(record, ensure_ascii=True, sort_keys=True) if args.json else render(record))
    return 1 if record["state"] in ("stalled", "invalid") else 0


if __name__ == "__main__":
    raise SystemExit(main())
