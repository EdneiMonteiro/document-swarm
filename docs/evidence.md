# Evidência factual e integridade da aprovação

A versão 4.0 adota `evidence-v1` para novas execuções e evoluções. O contrato
acrescenta evidência por afirmação, cobertura integral declarada, pareceres
originais imutáveis e auditoria independente vinculada à candidata. Não
substitui `editorial-v1`, os contratos de PDF/apresentação ou julgamento humano.

## Abertura e política

Crie o brief e as declarações dos agentes existentes antes de registrar:

```yaml
skill_version: "4.0.0"
quality_contract: editorial-v1
evidence_contract: evidence-v1
max_cycles: 10
factual_reviewer: reviewer-01-facts
editorial_reviewer: reviewer-02-clarity
evidence_auditor: rubber-duck
evidence_reviewers:
  - reviewer-01-facts
  - reviewer-02-clarity
deliverables:
  - output/document.md
```

```bash
python3 scripts/checks/evidence.py init <swarm>
```

SQLite fica em `reports/evidence/evidence.sqlite`, com foreign keys,
transações, versão de esquema, eventos append-only encadeados por hash e
relações entre candidatas, arquivos, fontes, afirmações, ocorrências e
avaliações. `reports/evidence-policy.json` registra a abertura. A ausência ou
alteração da política, do banco ou dos pareceres bloqueia aprovação; editar o
brief não relaxa o contrato. `events.json` é exportação derivada, nunca uma
segunda autoridade de aprovação.

O teto padrão é 10; qualquer teto explícito prevalece. Não há mínimo de ciclos,
nem prêmio por chegar ao último. O teto refere-se ao número do ciclo, incluindo
a numeração histórica ao evoluir um swarm. A primeira nova revisão segue o
maior ciclo histórico, sem reescrever registros anteriores. Se não houver
espaço no teto, registre extensão autorizada pelo usuário.

Responsáveis e formatos ficam fixados. Uma recomposição pode mudar os caminhos
das entregas no brief, preservando os formatos; a candidata antiga deixa de
corresponder à entrega atual. Alterar declaração de revisor invalida a política
registrada; não troque silenciosamente suas instruções durante a revisão.

## Candidata congelada

Depois de compor, inspecionar e verificar fontes/tabelas, grave um spec:

```json
{
  "review_cycle": 1,
  "composition_id": "composition-01",
  "source": "output/document.md",
  "text": "reports/cycle-01-editorial-text.txt",
  "sources": [
    {"id": "F16", "path": "sources/corpus/versions/F16/<sha256>.txt"}
  ],
  "bundles": ["reports/cycle-01-tables-check.json"]
}
```

```bash
python3 scripts/checks/evidence.py freeze <swarm> \
  --input reports/candidate-spec.json
```

Os caminhos são relativos ao swarm; caminhos externos, drives Windows,
travessias e symlinks que saem da pasta são recusados. O resultado devolve
`candidate_id`, SHA-256 do manifesto com fonte, texto, entregas, corpus e
bundles. PDFs incluem automaticamente todos os arquivos de sua pasta,
manifesto, inspeção, layout, texto e prévias. Apresentações incluem
automaticamente a pasta da entrega, inputs, manifesto, inspeções e os registros
de qualificação da implementação e do perfil; use `bundles` para outros arquivos
examinados. A consolidação usa as avaliações de apresentação dos pareceres
originais registrados, inclusive em caminhos próprios de cada composição.
O gate continua verificando o contrato de apresentação.

O texto revisado deve corresponder à fonte e entrega Markdown, ao texto
inspecionado do PDF ou à projeção textual do layout composto da apresentação.
Congelar dois arquivos com hashes válidos não basta se suas palavras diferirem.
A projeção textual complementa, nunca substitui, a inspeção dos arquivos
efetivamente entregues e o julgamento factual/editorial dos revisores.

Use nomes de composição novos e destinos novos para renders. Não selecione
texto pelo mtime, nem remonte snapshots de uma candidata já congelada.
Uma segunda composição não gasta outro ciclo de revisão, mas exige registros
próprios e `consolidate --candidate <id>` explícito quando houver ambiguidade.
Preserve respostas em `reports/evidence/responses/<candidate_id>/` nesses casos,
sem substituir o JSON de uma composição anterior.

`verify_sources.py --corpus-dir` mantém `Fxx.txt` como referência legada e
acrescenta snapshots em `versions/Fxx/<sha256>.txt`. Use o snapshot versionado,
não o alias, na candidata. Uma atualização com novo hash no `sources-check.json`
invalida dependentes, mesmo que o snapshot anterior ainda exista. Falha no novo
GET não certifica o corpus antigo como extração nova. HTTP ok não prova suporte.

## Inventário e cobertura

Autores catalogam afirmações e ocorrências sem inserir IDs na prosa publicada.
O JSON usa `schema_version: 1`, `candidate_id`, `claims` e `blocks`.

```json
{
  "schema_version": 1,
  "candidate_id": "<id>",
  "claims": [{
    "id": "delivery-route",
    "relation": {"subject": "driver", "action": "deliver", "direction": "up"},
    "occurrences": [{
      "id": "opening-route", "start": 3, "end": 3,
      "quote": "The driver goes up to deliver and returns down."
    }]
  }],
  "blocks": [{
    "id": "opening", "start": 3, "end": 3,
    "quote": "The driver goes up to deliver and returns down.",
    "classification": "factual",
    "justification": "Outbound and return direction.",
    "claims": ["delivery-route"]
  }]
}
```

Esse fragmento ilustra o formato, não cobertura integral: adicione blocos para
todos os trechos não vazios da candidata, inclusive títulos. `start` e `end`
são linhas inclusivas, base 1; `quote` é exatamente o trecho UTF-8 dessas linhas.
Blocos não se sobrepõem. Bloco factual exige afirmações com ocorrência local.
Os demais usam `non_factual`, `assumption`, `estimate` ou `interpretation`, com
justificativa; não reclassifique um fato apenas para dispensar suporte.

Relações aceitam somente campos aplicáveis: `subject`, `action`, `object`,
`place`, `time`, `direction`, `conditions`, `result`. Não invente campos
irrelevantes. Datas, nomes, números e relações causais/espaciais centrais exigem
fontes específicas, não uma lista de cinco URLs.

```bash
python3 scripts/checks/evidence.py inventory <swarm> \
  --candidate <id> --input reports/inventory.json
```

O revisor factual verifica a completude contra o documento integral, não só
a seleção do autor. O script detecta linhas omitidas e vínculos quebrados;
o agente julga se uma linha factual foi incorretamente classificada ou se
outra afirmação ficou escondida num bloco aparentemente coberto.

## Pareceres e auditoria

Antes de cada ferramenta real de despacho, registre a identidade:

```bash
python3 scripts/checks/evidence.py dispatch <swarm> \
  --candidate <id> --reviewer reviewer-01-facts
```

Isso registra a passagem, não executa ou observa autonomamente um agente.
Continue usando `task`/`write_agent` e o monitor conforme a skill. Revisores
recebem candidata, tarefa e achados, sem teto/restante para induzir urgência.

Todo parecer tem `schema_version: 1`, `evidence_contract: evidence-v1`,
`candidate_id`, `cycle`, `reviewer`, `scope: full_document`, `justification`,
`evidence_findings` e `topics` com `topic`, `grade`, `justification`, `action`.
Graus válidos: `D- D D+ C- C C+ B- B B+ A- A A+`. Não use `carry_forward` nem
nota herdada para certificar a candidata atual. Revisões parciais intermediárias
podem orientar autoria, mas não satisfazem este registro de aprovação.

O factual acrescenta `inventory_complete: true`, `block_checks` para todos os
blocos (`id`, `accepted`, `justification`) e `assessments` para todas as
afirmações. Cada assessment registra `claim`, `conclusion` (`supported`,
`contradicted`, `uncertain`), `justification`, `consistency`, todas as
`occurrences` e `evidence` com `source`, `start`, `end`, `quote` exatos do corpus.
O julgamento compara sujeito, direção, condições e resultado; um trecho presente
na fonte não prova por si só que sustenta a afirmação.

O editorial acrescenta o bloco `editorial` existente, já com hashes da candidata
examinada. O consolidado não injeta hashes novos num parecer antigo.

```bash
python3 scripts/checks/evidence.py review <swarm> \
  --candidate <id> --input reports/cycle-01-reviewer-01-facts.json
python3 scripts/checks/cycle_artifacts.py consolidate <swarm> \
  --cycle 1 --candidate <id>
```

Sem auditor independente, a candidata continua `audit_pending`. Depois dos
pareceres, registre despacho e resposta do `evidence_auditor`. Seu JSON inclui
os campos comuns, `critical` booleano, `inventory_complete: true`,
`reviewed_reviewers` na ordem do brief e `review_artifacts` com path/sha256
dos originais. Não precisa de `topics`: audita as justificativas, cobertura e
achados, sem substituir as notas dos revisores.

Reconsolide após registrar o auditor. O JSON e o manifesto original não são
sobrescritos; mudar uma resposta exige nova candidata. Todos os caminhos de
entrega usam `gate.evaluate_current`; avaliação de notas isolada não aprova
`evidence-v1`.

## Achados e resolução da família

`evidence_findings` registra `id` estável, `family`, `severity` (`blocking` ou
`minor`), `reason`, `action`, `evidence`, `family_inspection` e `occurrences`
enumeradas. Referências podem apontar ocorrências de afirmações ou IDs de blocos
para defeitos não factuais. Uma nota abaixo de A exige achado material.

Uma ocorrência encontrada exige busca da família inteira, inclusive textos
inalterados. Um ID não pode mudar de família. Veto material fica aberto entre
ciclos, ainda que desapareça da lista atual ou a consolidação seja repetida.
Descobrir um defeito antigo pode reabrir A; não exige editar artificialmente o
trecho. Para relatórios legados, a causa `newly_discovered_defect` em `regression`
registra trecho, evidência e motivo da descoberta.

Somente o responsável ou auditor pode registrar resolução. O JSON tem
`candidate_id`, `finding`, `reviewer`, `correction`, `reassessment`,
`family_inspection`, `occurrences` (ocorrências e IDs de blocos do inventário atual) e
`previous_occurrences` (todas as anteriormente afetadas). Reavaliação factual
atual deve sustentar os fatos e aceitar classificações; o resolutor não pode
continuar registrando o mesmo achado ou veto crítico.

```bash
python3 scripts/checks/evidence.py resolution <swarm> \
  --candidate <id> --input reports/resolution.json
```

Renumerar achados, aumentar nota ou mudar só hash/diagramação não é progresso.
Ao encerrar uma rodada, o script mede nova evidência sustentada (relação e
trecho, sem depender de IDs/linhas/timestamps) e resoluções verificadas:

```bash
python3 scripts/checks/gate.py <swarm>/reports/cycle-01-review.yaml \
  --output <swarm>/reports/cycle-01-gate.json
python3 scripts/checks/evidence.py close-cycle <swarm> \
  --input reports/cycle-01-review.yaml
```

Duas rodadas consecutivas sem ganhos exigem `diagnosis_required`. Grave JSON
com `causes` e `changed_approach` e registre `evidence.py diagnosis --input ...`
antes de outra rodada. O diagnóstico não aprova nem estende o teto. Novos
defeitos não impedem reconhecer ganho real de evidência.

Extensão exige JSON com `actor: user`, `decision: extend`, novo `max_cycles`,
`reason` e `authorization` reproduzindo a decisão explícita. Execute
`evidence.py extension --input ...` e então atualize o teto do brief para o
valor registrado. Não apague ciclos nem edite retroativamente a abertura.

## Retomada, recuperação e limites

`state` e `progress.py` são leitores. Monitor e relatório distinguem ciclo,
composição, afirmações, blocos, auditoria, bloqueios e diagnóstico; não despacham
agentes, fecham ciclos ou inferem notas ausentes.

Se o banco foi perdido/corrompido, não reconstrua aprovação a partir de resumo.
Preserve o diagnóstico e use recuperação explícita de um export íntegro:

```bash
python3 scripts/checks/evidence.py recover <swarm> \
  --input reports/evidence/events.json --reason "causa observada"
```

O comando confere cadeia, política e originais, preserva o banco anterior num
arquivo identificado por hash e registra recuperação. A aprovação antiga não
retorna: congele nova candidata e faça revisão integral independente. Esquema
desconhecido requer migração explícita, nunca inicialização silenciosa.

Hashes e registro declarado de despacho não são assinatura criptográfica de
identidade. Um operador privilegiado pode reescrever conjuntamente banco,
política e arquivos; o contrato protege o fluxo normal, não promete resistência
a esse operador. Fonte errada e mau julgamento semântico continuam possíveis.

## Controles

```bash
python3 -m unittest tests.test_evidence tests.test_checks \
  tests.test_editorial tests.test_progress tests.test_watchdog -v
```

A fixture mínima de Initial D conserva a referência F16:141, uma afirmação
invertida e outra correta. Os testes usam julgamentos anotados e provam cobertura,
vínculos, auditoria pendente, quatro vetos, recuperação e resolução de família;
não são um detector lexical de verdade. Uma leitura semântica real precisa
comparar ida ao hotel no alto com retorno descendo, não só encontrar uma palavra.
Os controles contra código antigo devem imprimir o valor observado e falhar
pelo defeito anunciado, nunca por import, arquivo ou pacote ausente.
