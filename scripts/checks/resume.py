"""Project where a swarm stopped and what its next deterministic step is.

The projection reads artifacts only.  It never writes a document, never assigns
a grade and never decides that a cycle is finished: it reports the first step of
the contract in ``SKILL.md`` that has no artifact yet.  A resumed session can
therefore continue without reconstructing the context by hand.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.checks.common import InputError, write_json_atomic
from scripts.checks.gate import artifact_descriptor, requires_editorial
from scripts.checks.lint_agents import frontmatter_text
from scripts.checks.progress import snapshot

MONITOR_PHASES = ("setup", "agents", "authors", "consolidation", "sources", "tables",
                  "reviews", "rubber-duck", "gate", "delivery")
MAX_CYCLE = 999


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def step(action: str, target: str, reason: str, **extra: Any) -> dict[str, Any]:
    if action not in ("create", "dispatch", "compose", "run", "deliver"):
        raise InputError(f"unsupported resume action {action!r}")
    return {"action": action, "target": target, "reason": reason, **extra}


def cycle_files(reports: Path) -> dict[int, list[Path]]:
    found: dict[int, list[Path]] = {}
    for path in sorted(reports.glob("cycle-*")):
        match = re.match(r"cycle-(\d+)-", path.name)
        if match and path.is_file():
            number = int(match.group(1))
            if 1 <= number <= MAX_CYCLE:
                found.setdefault(number, []).append(path)
    return found


class Projection:
    """Walk the cycle contract and stop at the first step without its artifact."""

    def __init__(self, swarm: Path) -> None:
        self.root = swarm.resolve(strict=True)
        self.data = snapshot(self.root)
        self.brief = frontmatter_text((self.root / "brief.md").read_text(encoding="utf-8"))
        self.reports = self.root / "reports"
        self.present: list[dict[str, str]] = []
        self.absent: list[str] = []
        self.blocked: list[dict[str, str]] = []

    # -- evidence ----------------------------------------------------------
    def exists(self, relative: str) -> bool:
        """Record the artifact the decision depends on, present or absent."""
        path = self.root / relative
        if path.is_file():
            self.present.append({"path": relative, "sha256": digest(path)})
            return True
        self.absent.append(relative)
        return False

    def agents_of(self, kind: str) -> list[str]:
        return [item["id"] for item in self.data["agents"] if item["kind"] == kind]

    def declared_deliverables(self) -> list[str]:
        """The deliveries the brief names, validated like the gate validates them."""
        value = self.brief.get("deliverables")
        if value is None:
            return []
        if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
            raise InputError("brief deliverables must be a list of paths")
        return [artifact_descriptor({"path": item, "sha256": "0" * 64})[0] for item in value]

    # -- phases ------------------------------------------------------------
    def composition(self) -> list[dict[str, Any]] | None:
        authors, reviewers = self.agents_of("author"), self.agents_of("reviewer")
        missing = []
        if not authors:
            missing.append("autores")
        if not reviewers:
            missing.append("revisores")
        if not self.agents_of("coordinator"):
            missing.append("coordenador")
        if not self.agents_of("rubber-duck"):
            missing.append("rubber duck")
        if not missing:
            return None
        return [step("create", "agents",
                     f"a composição não declara {', '.join(missing)}")]

    def active_cycle(self) -> int:
        files = cycle_files(self.reports)
        last = max(files) if files else 1
        recorded = {item["cycle"]: item for item in self.data["cycles"]}
        gate = recorded.get(last, {}).get("gate", {"status": "not_recorded"})
        if gate.get("status") == "verified" and gate.get("outcome") == "rejected":
            return min(last + 1, self.data["max_cycles"] + 1)
        return last

    def tag_for(self, cycle: int) -> str:
        """Use the prefix the swarm actually wrote; older runs are not zero padded."""
        canonical = f"cycle-{cycle:02d}"
        if cycle >= 10 or not self.reports.is_dir():
            return canonical
        if any(self.reports.glob(f"{canonical}-*")):
            return canonical
        return f"cycle-{cycle}" if any(self.reports.glob(f"cycle-{cycle}-*")) else canonical

    def cycle_steps(self, cycle: int) -> tuple[str, list[dict[str, Any]]]:
        tag = self.tag_for(cycle)
        recorded = {item["cycle"]: item for item in self.data["cycles"]}.get(cycle, {})
        authors = self.agents_of("author")
        reviewers = self.agents_of("reviewer")

        if not self.exists(f"reports/{tag}-authors.md"):
            return "authors", [step("dispatch", name, "o ciclo não registrou a rodada de autores", cycle=cycle)
                               for name in authors]
        declared = self.declared_deliverables()
        if declared:
            # The brief names the real delivery.  A presentation has no Markdown to find, and
            # asking for one projected "compose" on every tick of a real run.
            missing = [name for name in declared if not self.exists(name)]
            if missing:
                return "consolidation", [step("compose", name, "a entrega declarada no brief não existe neste ciclo",
                                              cycle=cycle) for name in missing]
        else:
            documents = sorted(path for path in (self.root / "output").rglob("*.md") if path.is_file())
            if not documents:
                return "consolidation", [step("compose", "output", "não há documento consolidado para este ciclo",
                                              cycle=cycle)]
            self.present.append({"path": documents[0].relative_to(self.root).as_posix(),
                                 "sha256": digest(documents[0])})
        # The full-text record only exists under the editorial contract.
        if requires_editorial(self.brief) and not self.exists(f"reports/{tag}-editorial-text.txt"):
            return "consolidation", [step("compose", "editorial-text",
                                          "o texto integral do ciclo não foi preservado para cotejo", cycle=cycle)]
        if self.data["sources"]["status"] in ("pending", "invalid", "stale"):
            return "sources", [step("run", "verify_sources.py",
                                    "a checagem de fontes não está registrada para o estado atual", cycle=cycle)]
        if recorded.get("tables", {}).get("status", "pending") == "pending":
            return "tables", [step("run", "verify_tables.py",
                                   "a checagem de tabelas não foi executada neste ciclo", cycle=cycle)]
        if not self.exists(f"reports/{tag}-nomenclature.json"):
            return "reviews", [step("run", "inspect_nomenclature.py",
                                    "a inspeção de nomenclatura não foi executada neste ciclo", cycle=cycle)]
        delivered = {row["reviewer"] for row in recorded.get("reviews", [])}
        pending = [name for name in reviewers if name not in delivered]
        for name in reviewers:
            self.exists(f"reports/{tag}-{name}.json")
        if pending:
            return "reviews", [step("dispatch", name, "o revisor não entregou sua avaliação estruturada",
                                    cycle=cycle) for name in pending]
        if not (self.exists(f"reports/{tag}-review.yaml") or self.exists(f"reports/{tag}-review.yml")):
            return "reviews", [step("compose", "review.yaml",
                                    "a matriz consolidada do ciclo não foi gravada", cycle=cycle)]
        if not self.exists(f"reports/{tag}-rubberduck.md"):
            return "rubber-duck", [step("dispatch", "rubber-duck",
                                        "a auditoria independente do ciclo não foi registrada", cycle=cycle)]
        gate = recorded.get("gate", {"status": "not_recorded"})
        if gate.get("status") != "verified":
            reason = {"not_recorded": "o portão não foi executado para a revisão atual",
                      "stale": "o resultado do portão não corresponde à revisão atual",
                      "invalid": "o resultado do portão não confere com a revisão"}.get(
                          gate.get("status", "not_recorded"), "o portão precisa ser reexecutado")
            self.exists(f"reports/{tag}-gate.json")
            return "gate", [step("run", "gate.py", reason, cycle=cycle)]
        self.exists(f"reports/{tag}-gate.json")
        if gate["outcome"] == "escalate":
            self.blocked.append({"kind": "escalation",
                                 "detail": "o portão escalou; a decisão é do usuário, não do vigia"})
            return "gate", []
        return self.delivery(cycle)

    def delivery(self, cycle: int) -> tuple[str, list[dict[str, Any]]]:
        if self.data["sources"]["status"] != "ok":
            return "delivery", [step("run", "verify_sources.py --force",
                                     "a rechecagem final de fontes não está registrada", cycle=cycle)]
        if not self.exists("reports/final-report.md"):
            return "delivery", [step("run", "final_report.py", "o relatório final não foi derivado", cycle=cycle)]
        if not self.exists("reports/memory-proposal.json"):
            return "delivery", [step("run", "update_memory.py",
                                     "a proposta de memória não foi gerada", cycle=cycle)]
        return "delivery", [step("deliver", "entrega",
                                 "os artefatos do ciclo aprovado estão completos", cycle=cycle)]

    def run(self) -> dict[str, Any]:
        self.exists("brief.md")
        composition = self.composition()
        if composition is not None:
            phase, steps = "agents", composition
            cycle = 0
        else:
            cycle = self.active_cycle()
            if cycle > self.data["max_cycles"]:
                self.blocked.append({"kind": "max_cycles",
                                     "detail": "o teto de ciclos foi atingido; escale ao usuário"})
                phase, steps = "gate", []
            else:
                phase, steps = self.cycle_steps(cycle)
        for warning in self.data["warnings"]:
            self.blocked.append({"kind": "artifact", "detail": warning})
        for item in self.data["cycles"]:
            for issue in item["issues"]:
                self.blocked.append({"kind": "cycle", "detail": f"ciclo {item['cycle']}: {issue}"})
        complete = bool(steps) and steps[0]["action"] == "deliver"
        if phase not in MONITOR_PHASES:
            raise InputError(f"projected phase {phase!r} is not a phase the monitor accepts")
        return {
            "schema_version": 1,
            "swarm_id": self.data["swarm_id"],
            "skill_version": self.data["skill_version"],
            "max_cycles": self.data["max_cycles"],
            "cycle": cycle,
            "phase": phase,
            "complete": complete,
            "next": [] if complete else steps,
            "blocked": self.blocked,
            "evidence": {"present": self.present, "absent": sorted(set(self.absent))},
        }


def project(swarm: Path) -> dict[str, Any]:
    """Return the next deterministic step without modifying the swarm."""
    return Projection(swarm).run()


def verify(swarm: Path, record: dict[str, Any]) -> list[str]:
    """Report which recorded artifacts no longer match, so a stale plan is refused."""
    root = swarm.resolve(strict=True)
    changed = []
    evidence = record.get("evidence")
    if not isinstance(evidence, dict):
        raise InputError("the resume record carries no evidence")
    for item in evidence.get("present", []):
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            raise InputError("invalid resume evidence entry")
        path = root / item["path"]
        if not path.is_file() or digest(path) != item.get("sha256"):
            changed.append(item["path"])
    for name in evidence.get("absent", []):
        if (root / str(name)).exists():
            changed.append(str(name))
    return sorted(set(changed))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Project the next deterministic step of a swarm; never modifies its artifacts.")
    parser.add_argument("swarm", type=Path)
    parser.add_argument("--output", type=Path, help="default: <swarm>/reports/resume.json")
    parser.add_argument("--check", type=Path, help="verify a recorded projection instead of writing a new one")
    args = parser.parse_args(argv)
    try:
        if args.check:
            record = json.loads(args.check.read_text(encoding="utf-8"))
            changed = verify(args.swarm, record)
            print(json.dumps({"stale": bool(changed), "changed": changed}, ensure_ascii=True, sort_keys=True))
            return 1 if changed else 0
        result = project(args.swarm)
        write_json_atomic(args.output or args.swarm / "reports" / "resume.json", result)
    except (OSError, UnicodeError, InputError, json.JSONDecodeError) as exc:
        print(f"ERROR: cannot project the swarm state: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
