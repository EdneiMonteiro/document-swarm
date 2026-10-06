"""Prompts for the executor, built as pure functions of their inputs.

A prompt may contain nothing that changes between two calls on the same state: no
timestamps, no random identifiers, no ordering that depends on the filesystem.
Resuming after a crash relies on that, because the runtime can then recognise an
agent it already ran and return its recorded result instead of paying for it again.
"""

from __future__ import annotations

import json
from typing import Any

from scripts.orchestration.spec import AgentSpec

CONTRACT_VERSION = "execution-contract-v1"


def block(title: str, body: str) -> str:
    return f"## {title}\n\n{body.strip()}\n"


def topic_lines(topics: dict[str, str]) -> str:
    return "\n".join(f"- {key}: {title}" for key, title in topics.items())


def header(spec: AgentSpec, *, swarm_id: str, cycle: int, round_number: int, task_id: str, attempt: int,
           previous_errors: list[str]) -> str:
    lines = [
        f"# Execução determinística do Document Swarm ({CONTRACT_VERSION})",
        f"Swarm: {swarm_id} · ciclo {cycle} · rodada {round_number} · tarefa {task_id} · tentativa {attempt}",
        "",
        f"Você é o agente `{spec.name}` (papel: {spec.kind}). A sua declaração segue integralmente e define persona, "
        "missão e critérios. O protocolo desta seção tem precedência sobre qualquer instrução da declaração sobre "
        "ONDE ou COMO entregar: você não grava arquivos nem executa comandos, e devolve o resultado como um único "
        "objeto JSON que obedece ao esquema. O executor valida, persiste e registra tudo.",
    ]
    if previous_errors:
        lines += ["", "## Correção exigida: a tentativa anterior foi recusada",
                  "Corrija exatamente estes pontos e mantenha o que já estava correto:"]
        lines += [f"- {item}" for item in previous_errors]
    return "\n".join(lines) + "\n"


def declaration(spec: AgentSpec) -> str:
    return block("Sua declaração", spec.body)


def files_block(files: list[tuple[str, str]]) -> str:
    parts = []
    for path, content in files:
        parts.append(f"### {path}\n\n````\n{content.rstrip()}\n````")
    return "\n\n".join(parts)


def closing(schema: dict[str, Any]) -> str:
    rendered = json.dumps(schema, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return block("Saída", "Responda SOMENTE com um único objeto JSON que obedeça ao esquema abaixo. Sem texto antes ou "
                          "depois, sem cercas de código e sem o seu raciocínio.\n\n"
                          f"````json\n{rendered}\n````")


def author(spec: AgentSpec, *, swarm_id: str, cycle: int, round_number: int, task_id: str, attempt: int,
           previous_errors: list[str], brief_body: str, topics: dict[str, str], primary: str, code: str,
           feedback: str, current_files: list[tuple[str, str]], schema: dict[str, Any]) -> str:
    protocol = "\n".join([
        "- `files`: cada arquivo a entregar, com `path` e `content` completo. Pastas permitidas: output/sections/, "
        "output/figures/ e output/assets/. Extensões: .md .svg .json .txt .csv. Reenvie o mesmo `path` para "
        "substituir um arquivo seu; os que você não reenviar permanecem como estão.",
        f"- Não escreva em `{primary}`: é o documento consolidado, produzido pelo coordenador.",
        "- Os arquivos são seus. Não altere arquivos de outros autores.",
        f"- `sources`: fontes online distintas, que você consultou de fato. Cada `id` é `{code}` seguido de dois "
        f"dígitos (por exemplo `{code}01`, `{code}02`): a sua faixa de identificadores, para não colidir com outro "
        f"autor. Mínimo de fontes distintas declarado: {spec.sources_min}.",
        "- Cite as fontes no texto pelo `id`. Separe fatos verificados, premissas, estimativas e recomendações.",
        "- Se faltar informação que mude materialmente a recomendação, registre em `notes` em vez de inventá-la.",
    ])
    parts = [header(spec, swarm_id=swarm_id, cycle=cycle, round_number=round_number, task_id=task_id,
                    attempt=attempt, previous_errors=previous_errors),
             declaration(spec), block("Protocolo de entrega", protocol),
             block("Enquadramento (brief)", brief_body), block("Tópicos do documento", topic_lines(topics))]
    if feedback.strip():
        parts.append(block("Pendências obrigatórias", feedback))
    if current_files:
        parts.append(block("Seus arquivos atuais (revise-os; não recomece do zero)", files_block(current_files)))
    parts.append(closing(schema))
    return "\n".join(parts)


def consolidation(spec: AgentSpec, *, swarm_id: str, cycle: int, round_number: int, task_id: str, attempt: int,
                  previous_errors: list[str], brief_body: str, topics: dict[str, str], sections: list[tuple[str, str]],
                  sources_index: str, feedback: str, previous_document: str, schema: dict[str, Any]) -> str:
    protocol = "\n".join([
        "- `document_markdown`: o documento final completo, em Markdown, com índice e bibliografia que cite os `id` "
        "do índice de fontes. Uniformize voz, terminologia e profundidade.",
        "- Preserve fatos, lógica, escopo e ressalvas. Não invente fatos nem remova condições dos autores.",
        "- `divergences`: conflitos de conteúdo entre autores que precisam voltar a eles. Não os resolva por edição "
        "de estilo. Use lista vazia se não houver.",
    ])
    parts = [header(spec, swarm_id=swarm_id, cycle=cycle, round_number=round_number, task_id=task_id,
                    attempt=attempt, previous_errors=previous_errors),
             declaration(spec), block("Protocolo de entrega", protocol),
             block("Enquadramento (brief)", brief_body), block("Tópicos do documento", topic_lines(topics)),
             block("Seções entregues pelos autores", files_block(sections)),
             block("Índice de fontes", sources_index)]
    if feedback.strip():
        parts.append(block("Pendências obrigatórias deste ciclo", feedback))
    if previous_document.strip():
        parts.append(block("Documento do ciclo anterior (referência de continuidade)", f"````\n{previous_document.rstrip()}\n````"))
    parts.append(closing(schema))
    return "\n".join(parts)


def reviewer(spec: AgentSpec, *, swarm_id: str, cycle: int, round_number: int, task_id: str, attempt: int,
             previous_errors: list[str], brief_body: str, topics: dict[str, str], document: str, primary: str,
             sources_index: str, checks: str, editorial: bool, schema: dict[str, Any]) -> str:
    lines = [
        f"- `topics`: uma entrada para CADA tópico ({', '.join(topics)}), sem omitir nem acrescentar. Cada uma com "
        "`grade` na escala D- a A+, `justification` que cite evidência do trecho e `action` (correção acionável; "
        "obrigatória abaixo de A).",
        "- Você não edita o documento: devolve achados aos autores. A nota mínima entre os revisores decide o tópico.",
    ]
    if spec.evidence_class == "fact":
        lines.append(f"- `sources_consulted`: as fontes que você consultou de fato, com o que verificou em cada uma. "
                     f"Mínimo de fontes distintas declarado: {spec.sources_min}.")
    if editorial:
        lines += [
            "- `editorial`: você é o revisor editorial designado. Leia TODO o documento. Avalie cinco superfícies: "
            "titles, openings, body, captions, conclusions. Cada uma com `grade`, `location`, `quote`, `justification` "
            "e `action`. A `quote` deve ser copiada literalmente do documento: citações que não existam no texto "
            "serão recusadas. Se não houver legendas, use `not_applicable` com o motivo apenas em captions.",
            "- `editorial.findings`: achados com `severity` (blocking ou minor), `location`, `quote`, `reason`, `action`. "
            "Uma lista vazia é válida quando não há achados.",
        ]
    parts = [header(spec, swarm_id=swarm_id, cycle=cycle, round_number=round_number, task_id=task_id,
                    attempt=attempt, previous_errors=previous_errors),
             declaration(spec), block("Protocolo de entrega", "\n".join(lines)),
             block("Enquadramento (brief)", brief_body), block("Tópicos a avaliar", topic_lines(topics)),
             block(f"Documento sob revisão (`{primary}`, texto integral)", f"````\n{document.rstrip()}\n````"),
             block("Índice de fontes", sources_index), block("Resultados das verificações mecânicas", checks),
             closing(schema)]
    return "\n".join(parts)


def rubber_duck(spec: AgentSpec, *, swarm_id: str, cycle: int, round_number: int, task_id: str, attempt: int,
                previous_errors: list[str], brief_body: str, document: str, primary: str, review: dict[str, Any],
                reports: list[dict[str, Any]], checks: str, schema: dict[str, Any]) -> str:
    protocol = "\n".join([
        "- Audite autores, revisores e resultados mecânicos: contradições, omissões, fuga de escopo, notas infladas, "
        "fonte acessível que não sustenta a afirmação, aritmética relevante não marcada, e divergência entre a matriz "
        "consolidada e as avaliações individuais.",
        "- `findings`: cada um com `severity` (critical, important ou minor), `target`, `evidence` e `correction`.",
        "- `critical` é verdadeiro exatamente quando há um achado de severidade critical. Um achado crítico veta a entrega.",
        "- Conteste nota sem sustentação no trecho; não reescreva a nota de ninguém.",
    ])
    parts = [header(spec, swarm_id=swarm_id, cycle=cycle, round_number=round_number, task_id=task_id,
                    attempt=attempt, previous_errors=previous_errors),
             declaration(spec), block("Protocolo de entrega", protocol), block("Enquadramento (brief)", brief_body),
             block(f"Documento (`{primary}`)", f"````\n{document.rstrip()}\n````"),
             block("Matriz consolidada do ciclo", f"````json\n{json.dumps(review, ensure_ascii=False, indent=2, sort_keys=True)}\n````"),
             block("Avaliações individuais", f"````json\n{json.dumps(reports, ensure_ascii=False, indent=2, sort_keys=True)}\n````"),
             block("Resultados das verificações mecânicas", checks), closing(schema)]
    return "\n".join(parts)


def narrative(spec: AgentSpec, *, swarm_id: str, cycle: int, round_number: int, task_id: str, attempt: int,
              previous_errors: list[str], brief_body: str, facts: str, outcome: str, schema: dict[str, Any]) -> str:
    protocol = "\n".join([
        "- `narrative_markdown`: a narrativa humana do relatório final: decisões tomadas, riscos residuais e evidências "
        "que os fatos derivados não representam.",
        "- Não altere nem contradiga os fatos derivados abaixo. Não atribua notas.",
        f"- Desfecho desta execução: **{outcome}**. Se foi escalada, explique os bloqueios que restam.",
    ])
    parts = [header(spec, swarm_id=swarm_id, cycle=cycle, round_number=round_number, task_id=task_id,
                    attempt=attempt, previous_errors=previous_errors),
             declaration(spec), block("Protocolo de entrega", protocol), block("Enquadramento (brief)", brief_body),
             block("Fatos derivados do relatório final", f"````\n{facts.rstrip()}\n````"), closing(schema)]
    return "\n".join(parts)
