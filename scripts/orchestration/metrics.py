"""Measure where a swarm execution spent its wall-clock time.

The monitor journal records when each dispatched agent was running.  Subtracting
the union of those intervals from the wall clock separates two very different
costs that "the swarm is slow" lumps together: time an agent was working, and time
nobody was (coordinator turns, permission prompts, a closed laptop, a stall).

The figures are stamps recorded by the monitor.  They include tool calls and any
observation delay, so they are an upper bound on model time, never inference time.
"""

from __future__ import annotations

import json
import statistics
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from scripts.checks.common import InputError

TERMINAL = {"completed", "failed", "cancelled"}
IDLE_GAP_SECONDS = 300.0
MAX_JOURNAL_BYTES = 64 * 1024 * 1024
TOP_GAPS = 5


def parse_time(value: Any) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def read_journal(path: Path) -> list[dict[str, Any]]:
    if path.stat().st_size > MAX_JOURNAL_BYTES:
        raise InputError(f"{path.name} exceeds the supported journal size")
    events = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise InputError(f"{path.name}:{number}: not valid JSON") from exc
        if isinstance(item, dict):
            events.append(item)
    return events


def merge(intervals: list[tuple[float, float]]) -> list[tuple[float, float]]:
    merged: list[tuple[float, float]] = []
    for start, end in sorted(intervals):
        if end <= start:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def covered(merged: list[tuple[float, float]], low: float, high: float) -> float:
    """Length of [low, high] that lies inside a merged interval set."""
    total = 0.0
    for start, end in merged:
        total += max(0.0, min(end, high) - max(start, low))
    return total


def summary(values: list[float]) -> dict[str, float | int]:
    if not values:
        return {"n": 0}
    return {"n": len(values), "min": round(min(values), 1), "median": round(statistics.median(values), 1),
            "max": round(max(values), 1), "total": round(sum(values), 1)}


def execution(events: list[dict[str, Any]], *, execution_id: str = "",
              sleeps: list[tuple[float, float]] | None = None) -> dict[str, Any]:
    """Decompose one monitor journal into agent time, idle time and retrabalho.

    ``sleeps`` are the intervals the host was suspended, when known.  They split
    "nobody was running" into a machine that was off and a flow that was awake
    but not advancing, which are different problems with different remedies.
    """
    stamped = [(parse_time(item.get("at")), item) for item in events]
    stamped = [(at, item) for at, item in stamped if at is not None]
    if not stamped:
        raise InputError("the journal has no timestamped events")
    stamped.sort(key=lambda pair: pair[0])
    first, last = stamped[0][0], stamped[-1][0]

    dispatches: dict[str, dict[str, Any]] = {}
    phases: list[tuple[float, str, Any]] = []
    recoveries: list[str] = []
    for at, item in stamped:
        data = item.get("data") or {}
        kind = item.get("type")
        if kind == "dispatch" and isinstance(data.get("id"), str):
            dispatches[data["id"]] = {
                "role": data.get("agent_kind") or "unknown", "agent": data.get("agent_id"),
                "cycle": data.get("cycle"), "registered": at, "bound": None,
                "spans": [], "open": None, "model": None, "done": False, "last_seen": at,
                "final_status": "queued",
            }
        elif kind == "binding" and data.get("dispatch_id") in dispatches:
            record = dispatches[data["dispatch_id"]]
            record["bound"] = record["bound"] or parse_time(data.get("bound_at")) or at
            record["last_seen"] = at
        elif kind == "runtime" and data.get("dispatch_id") in dispatches:
            record = dispatches[data["dispatch_id"]]
            status = data.get("status")
            # A terminal state is final: later raw events describe the task, not new work.
            if record["done"]:
                continue
            if data.get("observed_model"):
                record["model"] = data["observed_model"]
            if status:
                record["final_status"] = status
                record["last_seen"] = at
            if status == "running" and record["open"] is None:
                # The runtime repeats the dispatch's first start on every later turn; only
                # the first opening may use it, or the same interval is counted again.
                reported = parse_time(data.get("started_at")) if not record["spans"] else None
                record["open"] = reported or at
            elif status in TERMINAL | {"idle"} and record["open"] is not None:
                record["spans"].append((record["open"], max(at, record["open"])))
                record["open"] = None
            if status in TERMINAL:
                record["done"] = True
        elif kind == "phase" and isinstance(data.get("phase"), str):
            phases.append((at, data["phase"], data.get("cycle")))
        elif kind == "recovery":
            recoveries.append(str(data.get("rule")))

    unmeasured = 0
    unresolved: list[dict[str, Any]] = []
    for record in dispatches.values():
        if record["open"] is not None:
            # Nobody saw this agent stop.  Claiming it ran until the journal ended would
            # invent hours, so the interval ends at the last observation of this dispatch.
            record["spans"].append((record["open"], max(record["last_seen"], record["open"])))
            record["open"] = None
            unresolved.append({"agent": record["agent"], "cycle": record["cycle"], "role": record["role"],
                               "last_seen": datetime.fromtimestamp(record["last_seen"]).astimezone().isoformat(timespec="seconds"),
                               "last_seen_epoch": record["last_seen"]})
        if not record["spans"]:
            unmeasured += 1

    all_spans = [span for record in dispatches.values() for span in record["spans"]]
    union = merge(all_spans)
    wall = last - first
    agent_union = covered(union, first, last)
    agent_sum = sum(end - start for start, end in all_spans)

    by_role: dict[str, list[float]] = {}
    queue, launch = [], []
    for record in dispatches.values():
        if record["spans"]:
            by_role.setdefault(record["role"], []).append(sum(end - start for start, end in record["spans"]))
        if record["bound"] is not None:
            queue.append(record["bound"] - record["registered"])
            if record["spans"]:
                launch.append(record["spans"][0][0] - record["bound"])

    ticks = [at for at, _ in stamped]
    gaps = []
    for earlier, later in zip(ticks, ticks[1:]):
        length = later - earlier
        if length < IDLE_GAP_SECONDS:
            continue
        running = covered(union, earlier, later)
        pending = sorted({str(item["agent"]) for item in unresolved if item["last_seen_epoch"] <= earlier})
        gaps.append({"from": datetime.fromtimestamp(earlier).astimezone().isoformat(timespec="seconds"),
                     "seconds": round(length, 1), "agent_running_seconds": round(running, 1),
                     "nobody_running_seconds": round(length - running, 1),
                     "last_seen_running": pending})
    gaps.sort(key=lambda item: item["nobody_running_seconds"], reverse=True)
    quiet = sum(item["nobody_running_seconds"] for item in gaps)

    spans = []
    ordered = sorted(phases)
    for index, (start, name, cycle) in enumerate(ordered):
        end = ordered[index + 1][0] if index + 1 < len(ordered) else last
        length = max(0.0, end - start)
        inside = covered(union, start, end)
        spans.append({"phase": name, "cycle": cycle, "seconds": round(length, 1),
                      "agent_running_seconds": round(inside, 1),
                      "nobody_running_seconds": round(length - inside, 1)})
    by_phase: dict[str, dict[str, float]] = {}
    for item in spans:
        row = by_phase.setdefault(item["phase"], {"seconds": 0.0, "agent_running_seconds": 0.0, "nobody_running_seconds": 0.0})
        for key in row:
            row[key] = round(row[key] + item[key], 1)

    nobody = wall - agent_union
    asleep_idle = None
    if sleeps is not None:
        suspended = merge([(max(a, first), min(b, last)) for a, b in sleeps])
        overlap = sum(max(0.0, min(s_end, u_end) - max(s_start, u_start))
                      for s_start, s_end in suspended for u_start, u_end in union)
        asleep_idle = max(0.0, sum(end - start for start, end in suspended) - overlap)

    return {
        "execution_id": execution_id,
        "wall_seconds": round(wall, 1),
        "agent_running_union_seconds": round(agent_union, 1),
        "agent_running_summed_seconds": round(agent_sum, 1),
        "nobody_running_seconds": round(nobody, 1),
        "nobody_running_share": round(nobody / wall, 3) if wall else 0.0,
        "host_asleep_seconds": None if asleep_idle is None else round(asleep_idle, 1),
        "awake_nobody_running_seconds": None if asleep_idle is None else round(max(0.0, nobody - asleep_idle), 1),
        "in_large_gaps_seconds": round(quiet, 1),
        "parallelism": round(agent_sum / agent_union, 2) if agent_union else 0.0,
        "dispatches": len(dispatches),
        "unmeasured_dispatches": unmeasured,
        "dispatches_without_an_end": [{key: item[key] for key in ("agent", "cycle", "role", "last_seen")}
                                      for item in unresolved],
        "cycles": sorted({item["cycle"] for item in dispatches.values() if isinstance(item["cycle"], int)}),
        "agent_seconds_by_role": {role: summary(values) for role, values in sorted(by_role.items())},
        "registered_to_binding_seconds": summary(queue),
        "binding_to_running_seconds": summary(launch),
        "largest_gaps": gaps[:TOP_GAPS],
        "phase_totals": by_phase,
        "recoveries": sorted(recoveries),
    }


def host_sleep(since: float, until: float) -> list[tuple[float, float]] | None:
    """Read the suspend intervals Windows recorded, or None when they cannot be read.

    Only Windows keeps this record.  A failure is reported as None rather than as
    "the host never slept": an unknown must not be presented as zero.
    """
    if sys.platform != "win32":
        return None
    script = (
        "$start=[DateTimeOffset]::FromUnixTimeSeconds([int64]%d).UtcDateTime;"
        "$rows=@(Get-WinEvent -FilterHashtable @{LogName='System';"
        "ProviderName='Microsoft-Windows-Power-Troubleshooter';Id=1;StartTime=$start} -ErrorAction SilentlyContinue"
        "|ForEach-Object{$d=@{};foreach($n in ([xml]$_.ToXml()).Event.EventData.Data){$d[$n.Name]=$n.'#text'};"
        "[pscustomobject]@{sleep=[DateTimeOffset]::Parse($d['SleepTime']).ToUnixTimeSeconds();"
        "wake=[DateTimeOffset]::Parse($d['WakeTime']).ToUnixTimeSeconds()}});"
        "ConvertTo-Json -InputObject $rows -Compress"
    ) % int(since - 86400)
    try:
        done = subprocess.run(["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
                              capture_output=True, text=True, timeout=60)
        if done.returncode != 0:
            return None
        text = done.stdout.strip()
        data = json.loads(text) if text else []
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return None
    if isinstance(data, dict):
        data = [data]
    found = []
    for item in data:
        if isinstance(item, dict) and isinstance(item.get("sleep"), int) and isinstance(item.get("wake"), int):
            if item["wake"] >= since and item["sleep"] <= until:
                found.append((float(item["sleep"]), float(item["wake"])))
    return found


def legacy(swarm: Path, *, host_power: bool = False) -> list[dict[str, Any]]:
    """Analyse every monitor journal under a swarm, newest last."""
    root = swarm.resolve(strict=True)
    results = []
    for journal in sorted((root / "reports" / "progress").glob("*/events.jsonl")):
        events = read_journal(journal)
        if not events:
            continue
        sleeps = None
        if host_power:
            times = [parse_time(item.get("at")) for item in events]
            times = [value for value in times if value is not None]
            if times:
                sleeps = host_sleep(min(times), max(times))
        results.append(execution(events, execution_id=journal.parent.name, sleeps=sleeps))
    return results


def clock(seconds: float) -> str:
    seconds = max(0.0, seconds)
    if seconds < 90:
        return f"{seconds:.0f} s"
    if seconds < 5400:
        return f"{seconds / 60:.1f} min"
    return f"{seconds / 3600:.1f} h"


def render(result: dict[str, Any]) -> str:
    lines = [f"Execução {str(result['execution_id'])[:8]}", ""]
    rows = [
        ("Relógio", clock(result["wall_seconds"])),
        ("Agente rodando", f"{clock(result['agent_running_union_seconds'])} "
                           f"(soma {clock(result['agent_running_summed_seconds'])}, paralelismo {result['parallelism']}x)"),
        ("Ninguém rodando", f"{clock(result['nobody_running_seconds'])} "
                            f"({result['nobody_running_share'] * 100:.0f}% do relógio; "
                            f"{clock(result['in_large_gaps_seconds'])} em lacunas de {int(IDLE_GAP_SECONDS)} s ou mais)"),
    ]
    if result.get("host_asleep_seconds") is not None:
        rows.append(("  máquina dormindo", clock(result["host_asleep_seconds"])))
        rows.append(("  acordada, parada", f"{clock(result['awake_nobody_running_seconds'])}  "
                                           "(o fluxo não avançava ou esperava alguém)"))
    rows.append(("Despachos", f"{result['dispatches']} em ciclos {result['cycles']}; "
                              f"{result['unmeasured_dispatches']} sem intervalo medido"))
    if result["recoveries"]:
        rows.append(("Recuperações", ", ".join(result["recoveries"])))
    if result["dispatches_without_an_end"]:
        rows.append(("Sem desfecho", f"{len(result['dispatches_without_an_end'])} despacho(s) vistos rodando "
                                     "sem nunca ficarem ociosos ou concluírem"))
    for label, value in rows:
        lines.append(f"{label:<18}{value}")
    lines += ["", "Por papel (agente rodando)"]
    for role, stats in result["agent_seconds_by_role"].items():
        lines.append(f"  {role:<14} n={stats['n']:<3} mediana {clock(stats['median'])}, "
                     f"máximo {clock(stats['max'])}, total {clock(stats['total'])}")
    lines += ["", "Por fase (relógio, ninguém rodando)"]
    for phase, row in sorted(result["phase_totals"].items(), key=lambda pair: -pair[1]["seconds"]):
        lines.append(f"  {phase:<14} {clock(row['seconds']):>9}   ninguém rodando {clock(row['nobody_running_seconds'])}")
    if result["largest_gaps"]:
        lines += ["", "Maiores lacunas sem nenhum agente rodando"]
        for gap in result["largest_gaps"]:
            note = f"  (último visto rodando: {', '.join(gap['last_seen_running'])})" if gap["last_seen_running"] else ""
            lines.append(f"  {gap['from']}  {clock(gap['nobody_running_seconds'])}{note}")
    lines += ["", "Os carimbos vêm do monitor: incluem ferramentas e atraso de observação, "
                  "então são um limite superior do tempo de modelo, nunca o tempo de inferência."]
    return "\n".join(lines)
