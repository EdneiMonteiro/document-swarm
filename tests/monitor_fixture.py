"""Explicitly synthetic, isolated fixture for monitor integration checks."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
from pathlib import Path

from scripts.checks import final_report, gate

AGENTS = [
    ("coordinator", "coordinator", "Coordenação e consolidação"),
    ("author-01", "author", "Evidência e dimensionamento"),
    ("author-02", "author", "Operação e confiabilidade"),
    ("author-03", "author", "Alternativas e custos"),
    ("reviewer-01", "reviewer", "Precisão técnica"),
    ("reviewer-02", "reviewer", "Qualidade da decisão"),
    ("reviewer-03", "reviewer", "Clareza e público"),
    ("rubber-duck", "rubber-duck", "Auditoria independente"),
]
TOPICS = [("T01", "Evidência e premissas"), ("T02", "Critérios e alternativas"), ("T03", "Operação e limites")]


def write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def check(root: Path) -> None:
    marker = root / ".monitor-fixture.json"
    if not marker.is_file() or json.loads(marker.read_text(encoding="utf-8")) != {"fixture": 1, "root": str(root)}:
        raise ValueError("Refusing to modify a directory not created by this fixture")


def initialize(root: Path) -> None:
    if root.exists() and any(root.iterdir()):
        raise ValueError("Fixture destination must be new or empty")
    for name in ("agents", "reports", "sources", "output"):
        (root / name).mkdir(parents=True, exist_ok=True)
    write(root / ".monitor-fixture.json", {"fixture": 1, "root": str(root)})
    (root / "brief.md").write_text(
        f'---\nswarm_id: {root.name}\nskill_version: "3.1.0"\nmode: document\n'
        'max_cycles: 3\nmonitor: true\ndemo: true\n---\n'
        '# Demonstração do monitor\n\n'
        'Dados sintéticos para testar a interface, não uma entrega de produção.\n'
        'A tarefa de integração usa o runtime real; as avaliações são fixtures.\n',
        encoding="utf-8",
    )


def agents(root: Path) -> None:
    check(root)
    for name, kind, role in AGENTS:
        (root / "agents" / f"{name}.md").write_text(
            f"---\nname: {name}\nkind: {kind}\nrole: {role}\nmodel: auto\n"
            f"swarm: {root.name}\nmodel_status: fixture\nmodel_rationale: Integration fixture\ncontext_tier: default\n---\n"
            "# Agente de fixture\n\nApenas instrumentação do monitor; não produz avaliação real.\n",
            encoding="utf-8",
        )


def cycle(root: Path, number: int) -> None:
    check(root)
    if number not in (1, 2):
        raise ValueError("Only fixture cycles 1 and 2 are defined")
    grades = [["B+", "A-", "A"], ["A", "A", "A+"], ["A+", "A", "A"]] if number == 1 else [
        ["A", "A", "A+"], ["A+", "A", "A"], ["A", "A+", "A"],
    ]
    for reviewer, values in zip(("reviewer-01", "reviewer-02", "reviewer-03"), grades):
        write(root / "reports" / f"cycle-{number:02d}-{reviewer}.json", {
            "schema_version": 1, "cycle": number, "reviewer": reviewer,
            "topics": [{"topic": topic, "grade": grade, "justification": "Avaliação sintética para comprovar o placar.",
                        "action": "Revisar a premissa da fixture." if grade in ("B+", "A-") else ""}
                       for (topic, _), grade in zip(TOPICS, values)],
        })
    topics = []
    for index, (topic, title) in enumerate(TOPICS):
        reviewer_index = min(range(3), key=lambda reviewer: gate.GRADE_INDEX[grades[reviewer][index]])
        topics.append({"topico": topic, "title": title, "nota_minima": grades[reviewer_index][index],
                       "revisor_da_minima": f"reviewer-{reviewer_index + 1:02d}", "bloqueia": False})
    review = root / "reports" / f"cycle-{number:02d}-review.yaml"
    write(review, {"schema_version": 1, "skill_version": "3.1.0", "mode": "document", "cycle": number,
                   "max_cycles": 3, "topics": topics, "rubberduck": {"critico": False, "achados": []}})
    write(root / "sources" / "sources-check.json", {"fixture": True, "counts": {"ok": 0, "redirect": 0, "warn": 0, "fail": 0}})
    write(root / "reports" / f"cycle-{number:02d}-tables-check.json", {"fixture": True, "failures": 0})
    (root / "output" / "fixture.md").write_text(
        "# Documento sintético\n\nNão representa uma entrega avaliada por especialistas.\n\n"
        "<script>window.monitorFixtureInjection = true</script>\n", encoding="utf-8",
    )
    with contextlib.redirect_stdout(io.StringIO()):
        code = gate.main([str(review), "--output", str(root / "reports" / f"cycle-{number:02d}-gate.json")])
        if number == 2:
            if code != 0 or final_report.main([str(root), "--force"]) != 0:
                raise RuntimeError("Fixture final gate/report failed")
        elif code != 1:
            raise RuntimeError("Fixture first cycle must be rejected")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["init", "agents", "cycle1", "cycle2"])
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    if args.operation == "init":
        initialize(root)
    elif args.operation == "agents":
        agents(root)
    else:
        cycle(root, int(args.operation[-1]))
    print(f"Fixture {args.operation}: {root}")


if __name__ == "__main__":
    main()
