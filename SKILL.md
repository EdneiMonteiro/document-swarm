---
name: document-swarm
skill_version: "2.0.0"
description: "Use when the user asks for a substantial document (playbook, whitepaper, report, RFC, policy, technical guide, comparison) or a slide presentation/deck (PPTX), or wants to evolve an existing swarm deliverable. The skill frames the request, creates declarative specialist agents with session-validated model provenance, runs evidence-based improvement cycles, executes deterministic source/table/quality gates, and stops only when every evaluated topic reaches at least A or the work is explicitly escalated. Do not use for short text such as a paragraph or email."
---

# Document Swarm Skill

> Swarm de documentação para produzir documentos e apresentações substanciais com
> agentes declarativos, evidência rastreável, revisão iterativa e portões
> determinísticos. A régua é `D- ... A+`; **A- não aprova**.

## 1. Quando usar

Use esta skill para:

- criar whitepaper, playbook, relatório, RFC, política, guia técnico, tutorial ou
  comparativo substancial;
- criar apresentação/deck PowerPoint;
- evoluir uma entrega já produzida por um swarm;
- trabalhos que se beneficiam de autores especializados, revisores independentes
  e mais de um ciclo de melhoria.

Não use para e-mail, parágrafo, resposta curta ou edição trivial.

### Modo documento

Triggers típicos:

- "implemente um documento sobre `<tema>`";
- "crie um playbook/whitepaper/relatório sobre `<tema>`";
- "monte um swarm para escrever sobre `<tema>`".

### Modo apresentação

Triggers típicos:

- "crie uma apresentação/deck/pptx sobre `<tema>`";
- "transforme este conteúdo em uma apresentação";
- "monte um swarm de slides".

### Modo evolução

Triggers típicos:

- "evolua o documento/deck `<id>`";
- "adicione diagramas/capítulos/slides ao swarm existente";
- "atualize, expanda ou revise a entrega já produzida".

No modo evolução, trabalhe sobre a pasta existente. Não crie um swarm novo.

## 2. Contrato de qualidade

1. **Human-in-the-loop no início.** Enquadre objetivo, público, formato, escopo,
   restrições, critérios de sucesso e limite de ciclos antes de gerar agentes.
2. **Agentes declarativos.** Cada autor, revisor, coordenador, rubber duck e,
   quando aplicável, deck builder é um arquivo Markdown autocontido.
3. **Modelo com proveniência.** O modelo declarado por agente deve existir na
   lista atual da ferramenta `task`; substituições ficam registradas.
4. **Evidência proporcional ao papel.** Autores e revisores de fatos pesquisam e
   verificam fontes. Revisores de forma não executam pesquisa ritual.
5. **Determinismo onde é mecânico.** URLs, contas de tabelas, matriz de notas,
   teto de ciclos e fatos do relatório final são computados por scripts.
6. **Julgamento onde é semântico.** Autores, revisores e rubber duck continuam
   responsáveis por correção, relevância, clareza, coerência e sustentação das
   afirmações.
7. **Portão duro.** Todo tópico precisa de `A` ou `A+`; no deck, todo slide e toda
   dimensão visual também. Achado crítico do rubber duck veta a entrega.
8. **Sem regressão.** Evoluções reavaliam tópicos antigos e novos.
9. **Rastreabilidade.** Cada ciclo preserva relatórios humanos e artefatos
   estruturados.
10. **Escala explícita.** Ao atingir `max_cycles` sem aprovação, pare e informe o
    usuário; nunca entregue algo abaixo da barra em silêncio.

## 3. Convenções de caminho e versão

- Todos os caminhos lógicos desta skill usam `/`.
- No Windows, comandos PowerShell podem usar `\` quando necessário.
- A versão desta skill é o campo `skill_version` do frontmatter.
- Todo `brief.md` e `reports/final-report.md` deve registrar a versão usada.
- Qualquer mudança de comportamento em `SKILL.md` exige entrada em
  `CHANGELOG.md`.

## 4. Resolver a raiz da skill e a saída

Resolva `<DOCSWARM>` pelo alvo real da skill instalada:

```bash
# Linux/macOS
DOCSWARM=$(readlink -f "$HOME/.copilot/skills/document-swarm")
```

```powershell
# Windows
$DOCSWARM = (Get-Item -Force "$env:USERPROFILE\.copilot\skills\document-swarm").Target
```

Resolva `<OUTPUT_ROOT>` nesta ordem:

1. destino explícito do usuário;
2. variável `DOCSWARM_ROOT`;
3. `<DOCSWARM>/swarms`.

Se houver ambiguidade real, confirme com o usuário. O identificador é sequencial
por data:

- documento: `<YYYY-MM-DD>-SWARM-<XX>`;
- apresentação: `<YYYY-MM-DD>-DECK-<XX>`.

## 5. Estrutura de saída

### Documento

```text
<OUTPUT_ROOT>/<swarm_id>/
├─ brief.md
├─ agents/
│  ├─ coordinator.md
│  ├─ rubber-duck.md
│  ├─ authors/
│  └─ reviewers/
├─ reports/
│  ├─ agent-models.md
│  ├─ cycle-0N-authors.md
│  ├─ cycle-0N-review.md
│  ├─ cycle-0N-review.yaml
│  ├─ cycle-0N-rubberduck.md
│  ├─ cycle-0N-tables-check.json
│  └─ final-report.md
├─ sources/
│  ├─ sources-index.md
│  └─ sources-check.json
└─ output/
   ├─ sections/
   └─ <documento-final>.md
```

### Apresentação

```text
<OUTPUT_ROOT>/<deck_id>/
├─ brief.md
├─ agents/
│  ├─ coordinator.md
│  ├─ rubber-duck.md
│  ├─ deck-builder.md
│  ├─ slide-authors/
│  ├─ content-reviewers/
│  └─ design-reviewers/
├─ reports/
│  ├─ agent-models.md
│  ├─ cycle-0N-authors.md
│  ├─ cycle-0N-build.md
│  ├─ cycle-0N-content-review.md
│  ├─ cycle-0N-design-review.md
│  ├─ cycle-0N-review.yaml
│  ├─ cycle-0N-rubberduck.md
│  ├─ cycle-0N-tables-check.json
│  └─ final-report.md
├─ sources/
│  ├─ sources-index.md
│  └─ sources-check.json
└─ output/
   ├─ slides/
   ├─ build/deck.js
   ├─ renders/cycle-0N/slide-*.jpg
   └─ deck.pptx
```

## 6. Régua e matriz computável

Escala canônica:

```text
D-  D  D+  C-  C  C+  B-  B  B+  A-  A  A+
```

- `A` e `A+`: aprovam;
- `A-` ou menos: bloqueiam;
- toda nota exige justificativa e correção acionável;
- a nota mínima entre revisores é a nota efetiva do tópico/slide/dimensão.

Além do relatório Markdown, cada ciclo deve gerar
`reports/cycle-0N-review.yaml`:

```yaml
schema_version: 1
skill_version: "2.0.0"
mode: document
cycle: 2
max_cycles: 5
topics:
  - topico: "T01 — Enquadramento"
    nota_minima: A
    revisor_da_minima: reviewer-02-clarity
    bloqueia: false
rubberduck:
  critico: false
  achados: []
```

No deck, acrescente:

```yaml
slides:
  - slide: "01"
    nota_minima: A
    revisor_da_minima: design-01-layout
    bloqueia: false
deck_dimensions:
  - dimension: "Coesão de paleta"
    nota_minima: A
    revisor_da_minima: design-02-system
    bloqueia: false
```

O rubber duck deve conferir a consistência entre `.md` e `.yaml`. O portão usa o
arquivo estruturado, não uma interpretação livre da prosa.

## 7. Evidência e cache de fontes

### Exigência por papel

| Papel | `sources_min` | Regra |
|---|---:|---|
| Autor / autor de slides | 5 | Fontes online distintas, funcionais e relevantes. |
| Revisor de fatos: precisão, fontes, compliance, segurança, governança | 5 | Pesquisa própria e conferência das fontes dos autores. |
| Revisor de forma: estrutura, clareza, narrativa, aderência ao público | 0 | Fonte apenas quando contestar um fato. |
| Rubber duck | 0 | Fonte quando contestar um fato; não há coleta ritual. |
| Revisor de design | 0 | Avalia imagens renderizadas; não pesquisa fontes. |

Priorize documentação oficial, normas, artigos acadêmicos e referências
reconhecidas. Nunca invente URL ou trate acessibilidade como prova de que a fonte
sustenta a afirmação.

### Cache

1. Consulte `sources/sources-check.json` e `<DOCSWARM>/memory/sources.json`.
2. Verificação com menos de 7 dias pode ser reutilizada e citada como cache.
3. URL nova ou expirada deve ser aberta pelo agente e verificada pelo script.
4. Antes da entrega final, rode a verificação com `--force`.
5. `403` e `429` são `warn`, não aprovação automática: o coordenador confirma
   por ferramenta de conteúdo e registra a ressalva.
6. Fonte `fail` deve ser removida ou substituída antes dos revisores.

## 8. Tabelas auditáveis

Autores devem marcar tabelas cuja aritmética bloqueia a confiabilidade.

### Total ponderado

```markdown
<!-- check: weighted weights=20,25,8,12,10,10,8,4,3 -->
| Papel | Alternativa | C1 | C2 | C3 | C4 | C5 | C6 | C7 | C8 | C9 | Total | Score |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Hot serving | ADX | 5 | 5 | 3 | 4 | 4 | 4 | 3 | 4 | 4 | 429 | 86 |
```

O alias `pesos=` também é aceito. O divisor/escala padrão é `5`; use os
parâmetros documentados em `verify_tables.py --help` quando a escala mudar.

### Soma/percentuais

Use `<!-- check: sum column="Percentual" target=100 -->` (ou `percent`) para
somar uma coluna. O nome pode ser substituído por índice de coluna baseado em 1.

Tabelas marcadas que não fecham bloqueiam o ciclo. Heurísticas em tabelas não
marcadas são informativas e nunca substituem uma marcação explícita.

## 9. Proveniência de modelos

Antes de criar ou despachar agentes:

1. Leia a lista de valores aceitos pelo parâmetro `model` da ferramenta `task`
   na sessão atual. Não copie nomes de swarms antigos.
2. Escolha o modelo conforme missão, risco, volume de contexto e necessidade
   multimodal.
3. Confirme cada modelo da matriz contra essa lista.
4. Se o preferido não existir, escolha o equivalente mais próximo e registre a
   substituição.
5. Não confunda `agent_type` customizado com nome de modelo.

Formato obrigatório de `reports/agent-models.md`:

| Agente | Papel | Modelo | Effort | Contexto | Status | Substituído de | Justificativa |
|---|---|---|---|---|---|---|---|
| author-01 | ... | ... | high | long_context | disponível confirmado | — | ... |

Use diversidade entre autores, revisores e rubber duck quando ela reduzir
cegueira coletiva. Depois de gerar os agentes, rode `lint_agents.py`.

## 10. Memória entre swarms

Antes de definir agentes e fontes, leia:

- `<DOCSWARM>/memory/index.md`;
- `<DOCSWARM>/memory/sources.json`.

Reutilize apenas o que for relevante:

- perfil reutilizado/adaptado recebe `derived_from` no frontmatter;
- fonte herdada é apenas uma semente e continua sujeita ao cache/checagem;
- observação de calibração deve alterar a persona ou a revisão, não virar fato do
  documento.

No modo evolução, a pasta original é a primeira fonte de memória.

Ao final, execute `update_memory.py` sem `--apply`. Revise a proposta criada em
`reports/`; só então aplique com a confirmação explícita exigida pelo script.
Fontes `warn`, `fail` ou desconhecidas não entram na memória aplicada; perfis só
entram se o portão estiver aprovado.

## 11. Fluxo — modo documento

### Fase 0 — Enquadramento obrigatório

Use `ask_user` antes de gerar agentes. Cubra:

- objetivo;
- público e senioridade;
- tipo/formato;
- profundidade/extensão;
- idioma;
- escopo incluído e excluído;
- restrições, confidencialidade e compliance;
- fontes preferenciais/proibidas;
- critérios de sucesso;
- prazo e `max_cycles` (padrão 5).

Se o usuário omitir algo ou recusar o formulário, registre um padrão sensato.
No modo evolução, pergunte somente o que mudou.

### Fase 1 — Setup e brief

1. Resolva `<OUTPUT_ROOT>` e o próximo `swarm_id`.
2. Crie a árvore.
3. Escreva `brief.md` com frontmatter mínimo:

```yaml
---
swarm_id: <swarm_id>
skill_version: "2.0.0"
mode: document
max_cycles: 5
---
```

4. Registre enquadramento, tópicos de importância e critérios de sucesso.

### Fase 2 — Memória, agentes e modelos

1. Consulte a memória e registre o que será reutilizado.
2. Defina de 3 a 6 autores complementares.
3. Defina de 3 a 5 revisores em dimensões distintas; classifique cada dimensão
   como `fact` ou `form` para calcular `sources_min`.
4. Gere coordenador e rubber duck.
5. Escolha e valide modelos contra a sessão.
6. Grave `reports/agent-models.md` com `Status` e `Substituído de`.
7. Gere os arquivos declarativos.
8. Rode:

```bash
python3 "<DOCSWARM>/scripts/checks/lint_agents.py" agents --strict
```

Corrija qualquer erro antes do primeiro despacho.

### Fase 3 — Loop determinístico por ciclo

Você é o coordenador. Para cada ciclo `N`:

1. **Autores.** Despache autores independentes em paralelo, usando exatamente
   `model`, `reasoning_effort` e `context_tier` confirmados. No ciclo seguinte,
   envie somente tópicos bloqueados e achados obrigatórios.
2. **Consolidação.** Monte `output/<doc>.md`, atualize
   `sources/sources-index.md` e grave `reports/cycle-0N-authors.md`.
3. **Checagem de fontes.**

   ```bash
   python3 "<DOCSWARM>/scripts/checks/verify_sources.py" sources/sources-index.md --output sources/sources-check.json
   ```

   Remova/substitua `fail`; documente `warn`.
4. **Checagem de tabelas.**

   ```bash
   python3 "<DOCSWARM>/scripts/checks/verify_tables.py" output/<doc>.md --output reports/cycle-0N-tables-check.json
   ```

   Corrija qualquer falha marcada antes dos revisores.
5. **Revisores.** Colete nota e ação por tópico e consolide
   `reports/cycle-0N-review.md`.
6. **Matriz estruturada inicial.** Grave `reports/cycle-0N-review.yaml` com as
   notas mínimas.
7. **Rubber duck.** Audite autores, revisores, coordenador, resultados dos
   scripts e consistência `.md` ↔ `.yaml`; grave
   `reports/cycle-0N-rubberduck.md`.
8. **Atualize o YAML.** Registre `rubberduck.critico` e os achados.
9. **Portão por exit code.**

   ```bash
   python3 "<DOCSWARM>/scripts/checks/gate.py" reports/cycle-0N-review.yaml
   ```

   - exit `0`: aprovado; vá à Fase 4;
   - exit `1`: reprovado; execute `N+1`;
   - exit `2`: `max_cycles` atingido; execute
     `python3 "<DOCSWARM>/scripts/checks/final_report.py" . --force`, complete a
     narrativa dos bloqueios e escale ao usuário com esse relatório; não aplique
     memória;
   - exit `3`: artefato inválido; corrija o YAML, não prossiga.

### Fase 4 — Entrega e memória

1. Force a rechecagem final das fontes:

   ```bash
   python3 "<DOCSWARM>/scripts/checks/verify_sources.py" sources/sources-index.md --output sources/sources-check.json --force
   ```

2. Gere os fatos do relatório:

   ```bash
   python3 "<DOCSWARM>/scripts/checks/final_report.py" . --force
   ```

3. Complete apenas a seção narrativa do relatório, sem alterar os fatos
   derivados.
4. Gere a proposta de memória:

   ```bash
   python3 "<DOCSWARM>/scripts/checks/update_memory.py" .
   ```

5. Revise a proposta; aplique somente pelo fluxo explícito do script.
6. Entregue os caminhos e um resumo curto.

## 12. Modo evolução

### E.0 — Diagnóstico

Leia `brief.md`, documento/deck atual, último relatório/review estruturado,
agentes e artefatos de checks. Identifique tópicos afetados e novos tópicos.

### E.1 — Composição

- avalie se faltam autores ou dimensões de revisão;
- adicione apenas especialidades realmente novas;
- reavalie modelos se a missão mudou;
- registre em `reports/evo-<XX>-plan.md`;
- consulte memória, usando o swarm original como fonte primária.

### E.2 — Histórico

Acrescente ao `brief.md` uma seção de evolução com data, versão atual da skill,
pedido, tópicos, agentes e artefatos novos. Não apague histórico.

### E.3 — Uniformidade

Reative **todos** os autores existentes e novos. Cada um revisita sua seção para
incorporar terminologia, referências cruzadas e impactos da evolução sem
regredir conteúdo já aprovado.

### E.4 — Revisão

Continue a numeração de ciclos, rode todos os checks e reative todos os
revisores. O YAML e o portão incluem tópicos antigos e novos.

### E.5 — Entrega

Gere novamente o relatório derivado e registre o delta da evolução na narrativa.

## 13. Templates — documento

### Autor

```markdown
---
name: author-<XX>-<slug>
kind: author
role: <perfil>
model: <modelo confirmado>
reasoning_effort: <quando suportado>
context_tier: <default|long_context>
model_rationale: "<justificativa>"
model_status: "disponível confirmado"
swarm: <swarm_id>
sources_min: 5
derived_from: <origem reutilizada ou vazio>
---

# Autor: <Perfil>

## Persona
<Especialidade, experiência e ponto de vista.>

## Missão
Produzir os tópicos atribuídos no nível, tom e escopo do brief.

## Tópicos
- <tópico>

## Como trabalhar
1. Leia brief, memória relevante, estado atual e feedback do ciclo anterior.
2. Reuse fontes ainda válidas do cache; pesquise e verifique fontes novas.
3. Escreva conteúdo preciso, específico e acionável.
4. Marque tabelas auditáveis com `<!-- check: ... -->`.
5. Atualize sua seção e o índice de fontes sem sobrescrever trabalho alheio.

## Fontes
| ID | Título | Tipo | URL | Verificado |
|---|---|---|---|---|
| Fxx | ... | oficial/norma/acadêmico/blog | https://... | HTTP 200 em <data> |
```

### Revisor

```markdown
---
name: reviewer-<XX>-<slug>
kind: reviewer
role: <dimensão>
evidence_class: <fact|form>
model: <modelo confirmado>
reasoning_effort: <quando suportado>
context_tier: <default|long_context>
model_rationale: "<justificativa>"
model_status: "disponível confirmado"
swarm: <swarm_id>
sources_min: <5 para fact; 0 para form>
scale: "D- D D+ C- C C+ B- B B+ A- A A+"
gate: "A"
---

# Revisor: <Dimensão>

## Missão
Avaliar cada tópico do brief sob a dimensão atribuída.

## Evidência
- `fact`: consulte pelo menos 5 fontes e confira as fontes dos autores.
- `form`: não faça pesquisa ritual; cite fonte apenas ao contestar um fato.

## Saída
| Tópico | Nota | Justificativa | Correção acionável |
|---|---|---|---|
| <tópico> | B+ | ... | "Adicione/corrija/remova..." |

Encerre com a nota mínima, bloqueios e fontes exigidas pela sua classe.
```

### Coordenador

```markdown
---
name: coordinator
kind: coordinator
model: <modelo confirmado>
reasoning_effort: high
context_tier: long_context
model_rationale: "<justificativa>"
model_status: "disponível confirmado"
swarm: <swarm_id>
gate: "A"
max_cycles: 5
---

# Coordenador

Orquestre o fluxo declarado no SKILL.md. Nunca pule `verify_sources.py`,
`verify_tables.py`, a matriz YAML, o rubber duck ou `gate.py`. O exit code do
portão decide o próximo passo.
```

### Rubber duck

```markdown
---
name: rubber-duck
kind: rubber-duck
model: <modelo confirmado e preferencialmente de família diversa>
reasoning_effort: high
context_tier: long_context
model_rationale: "<justificativa>"
model_status: "disponível confirmado"
swarm: <swarm_id>
sources_min: 0
---

# Rubber Duck

## Missão
Auditar autores, revisores e coordenador.

## Checagens obrigatórias
- contradições, omissões, fuga de escopo e notas infladas;
- fonte acessível que não sustenta a afirmação;
- falhas ou avisos dos checks determinísticos;
- aritmética relevante não marcada;
- divergência entre review Markdown e YAML;
- aplicação correta do portão e de `max_cycles`.

## Saída
Achados priorizados como Crítico/Importante/Menor, com alvo, evidência e
correção. Um achado Crítico deve aparecer em `rubberduck.critico: true`.
```

## 14. Modo apresentação (PPTX)

O motor de qualidade é o mesmo, com três diferenças:

1. autores produzem specs de slide;
2. um deck builder único compila e mantém o sistema visual;
3. revisores multimodais avaliam imagens renderizadas por slide.

### Toolchain

Resolva a skill `pptx` e leia `SKILL.md` e `pptxgenjs.md` dela antes do build.
Dependências:

- Node.js e `pptxgenjs`;
- LibreOffice (`soffice`);
- Poppler (`pdftoppm`);
- Pillow;
- `markitdown[pptx]`.

Sem render não há revisão de design; informe o bloqueio em vez de improvisar com
Playwright.

No Windows, caminhos de instalação comuns incluem
`C:\Program Files\LibreOffice\program\soffice.exe`.

### Fase 0

Além das perguntas gerais, colete ocasião, tempo, quantidade de slides,
identidade visual, template/logo, gráficos/diagramas necessários e restrições de
marca.

### Fase 1

Crie a estrutura de deck e registre `skill_version`, `mode: deck`, tópicos,
identidade visual e `max_cycles` no brief.

### Fase 2

Gere:

- 3 a 6 autores de slides;
- 3 a 5 revisores de conteúdo, classificados em `fact` ou `form`;
- 2 a 3 revisores de design multimodais;
- 1 deck builder;
- coordenador e rubber duck.

Consulte memória, valide modelos e rode `lint_agents.py`.

### Fase 3 — ciclo

1. Autores atualizam specs em `output/slides/`.
2. Rubber duck audita specs e arco narrativo.
3. Rode `verify_sources.py` e
   `verify_tables.py output/slides/ --output reports/cycle-0N-tables-check.json`
   sobre os specs aplicáveis.
4. Deck builder gera `output/build/deck.js`, compila `output/deck.pptx`, executa
   QA de texto e renderiza `output/renders/cycle-0N/slide-*.jpg`.
5. Revisores de conteúdo dão nota por tópico.
6. Rubber duck audita a calibração de conteúdo.
7. Revisores de design abrem **todas** as imagens com `view` e dão nota por slide
   e por dimensão do deck.
8. Rubber duck reabre os renders e audita os achados visuais.
9. Consolide os relatórios humanos e `cycle-0N-review.yaml` com `topics`,
   `slides`, `deck_dimensions` e o veredito final do rubber duck.
10. Rode `gate.py` e siga o exit code. Em exit `2`, execute
    `python3 "<DOCSWARM>/scripts/checks/final_report.py" . --force` antes de
    escalar; não aplique memória.

### Fase 4

Force a verificação de fontes, finalize o `.pptx` com speaker notes, gere
`final-report.md` por script, complete a narrativa e proponha a memória.

### Comandos de build/render

```bash
node output/build/deck.js
python3 -m markitdown output/deck.pptx
python3 "<PPTX>/scripts/office/soffice.py" --headless --convert-to pdf --outdir output/renders/cycle-0N output/deck.pptx
pdftoppm -jpeg -r 150 output/renders/cycle-0N/deck.pdf output/renders/cycle-0N/slide
```

### Autor de slides

```markdown
---
name: author-<XX>-<slug>
kind: slide-author
role: <perfil>
model: <modelo confirmado>
reasoning_effort: <quando suportado>
context_tier: <default|long_context>
model_rationale: "<justificativa>"
model_status: "disponível confirmado"
swarm: <deck_id>
sources_min: 5
derived_from: <origem ou vazio>
---

# Autor de Slides: <Perfil>

Produza uma ideia por slide, conteúdo enxuto, intenção visual e speaker notes.

### Slide NN — <título>
- **Objetivo:** ...
- **Mensagem-chave:** ...
- **Conteúdo:** ...
- **Visual:** ...
- **Dados:** ...
- **Speaker notes:** ...
- **Fontes:** [Fxx]
```

### Deck builder

```markdown
---
name: deck-builder
kind: deck-builder
model: <modelo confirmado forte em código>
reasoning_effort: high
context_tier: long_context
model_rationale: "<justificativa>"
model_status: "disponível confirmado"
swarm: <deck_id>
---

# Deck Builder

Seja o único dono da paleta, tipografia, motivo e compilação. Gere um deck
determinístico, com speaker notes, QA de texto e renders por ciclo. Não invente
conteúdo ausente no spec.
```

### Revisor de conteúdo

Use o template de revisor do modo documento com `kind: content-reviewer`.
Dimensões de fatos usam `sources_min: 5`; dimensões de forma usam `0`.

### Revisor de design

```markdown
---
name: design-<XX>-<slug>
kind: design-reviewer
role: <dimensão visual>
model: <modelo multimodal confirmado>
reasoning_effort: high
context_tier: long_context
model_rationale: "<justificativa>"
model_status: "disponível confirmado"
swarm: <deck_id>
sources_min: 0
scale: "D- D D+ C- C C+ B- B B+ A- A A+"
gate: "A"
---

# Revisor de Design

Abra cada `slide-*.jpg` com `view`. Avalie colisão, overflow, contraste,
alinhamento, margens, tipografia, densidade, consistência, ritmo e coesão do
sistema visual.

| Slide | Nota | Problema | Correção acionável |
|---|---|---|---|
| 03 | B | ... | ... |

Encerre com notas das dimensões do deck e bloqueios.
```

## 15. Referência dos scripts determinísticos

Todos usam somente Python stdlib. Consulte `--help` para opções exatas.

| Script | Função | Bloqueia quando |
|---|---|---|
| `verify_sources.py` | testa URLs e mantém cache auditável | há fonte `fail` |
| `verify_tables.py` | recalcula tabelas marcadas | conta marcada não fecha |
| `lint_agents.py` | valida frontmatter e swarm dos agentes | agente está inválido |
| `gate.py` | aplica régua, crítico e `max_cycles` | retorna exit `1`, `2` ou `3` |
| `final_report.py` | deriva fatos do relatório final | artefatos estão ausentes/inválidos |
| `update_memory.py` | propõe e, após aprovação, aplica memória | swarm não aprovado ou fonte inelegível |

## 16. Checklist — documento

- [ ] `brief.md` registra `skill_version`, `mode`, `max_cycles` e tópicos.
- [ ] Memória consultada e reutilizações registradas.
- [ ] Agentes declarativos criados e `lint_agents.py` aprovado.
- [ ] Modelos confirmados na sessão; substituições registradas.
- [ ] Autores e revisores de fatos cumprem sua exigência de fontes.
- [ ] Cada ciclo tem relatórios humanos, check de fontes, check de tabelas e YAML.
- [ ] `gate.py` retornou `0`.
- [ ] Rechecagem final de fontes executada com `--force`.
- [ ] `final-report.md` foi derivado por script e recebeu apenas narrativa humana.
- [ ] Proposta de memória foi gerada e revisada.
- [ ] Documento final tem índice e bibliografia.

## 17. Checklist — apresentação

- [ ] Todos os itens aplicáveis do modo documento.
- [ ] Toolchain de build/render disponível.
- [ ] Specs têm mensagem, visual e speaker notes.
- [ ] `deck.pptx` compila e o QA de texto passa.
- [ ] Todos os slides foram renderizados e abertos por revisores multimodais.
- [ ] Todo tópico, slide e dimensão do deck está em `A` ou `A+`.
- [ ] O `.pptx` final preserva speaker notes.

## 18. Resposta ao usuário

### Documento

```markdown
Swarm concluído: `<OUTPUT_ROOT>/<swarm_id>/`

Documento: `output/<documento>.md`
Relatório: `reports/final-report.md`
Versão da skill: <skill_version>
Ciclos: <N> | Tópicos: <k>/<k> ≥ A | Gate: aprovado
Fontes: <ok> ok, <warn> warn, 0 fail
```

### Apresentação

```markdown
Deck concluído: `<OUTPUT_ROOT>/<deck_id>/`

Apresentação: `output/deck.pptx`
Renders: `output/renders/cycle-<N>/`
Relatório: `reports/final-report.md`
Versão da skill: <skill_version>
Ciclos: <N> | Tópicos ≥ A: <k>/<k> | Slides ≥ A: <m>/<m> | Gate: aprovado
```
