---
name: document-swarm
skill_version: "3.0.0"
description: "Use when the user asks for a substantial document (playbook, whitepaper, report, RFC, policy, technical guide, comparison) or wants to evolve an existing document produced by a swarm. The skill frames the request, creates declarative specialist agents with session-validated model provenance, runs evidence-based improvement cycles, executes deterministic source/table/quality gates, and stops only when every evaluated topic reaches at least A or the work is explicitly escalated. Do not use for presentations, slide decks, PPTX, or short text such as a paragraph or email."
---

# Document Swarm Skill

> Swarm de documentação para produzir documentos substanciais com
> agentes declarativos, evidência rastreável, revisão iterativa e portões
> determinísticos. A régua é `D- ... A+`; **A- não aprova**.

## 1. Quando usar

Use esta skill para:

- criar whitepaper, playbook, relatório, RFC, política, guia técnico, tutorial ou
  comparativo substancial;
- evoluir um documento já produzido por um swarm;
- trabalhos que se beneficiam de autores especializados, revisores independentes
  e mais de um ciclo de melhoria.

Não use para e-mail, parágrafo, resposta curta ou edição trivial.
Criação ou evolução de apresentações, slides, decks e arquivos PPTX está fora
do escopo desta skill.

### Modo documento

Triggers típicos:

- "implemente um documento sobre `<tema>`";
- "crie um playbook/whitepaper/relatório sobre `<tema>`";
- "monte um swarm para escrever sobre `<tema>`".

### Modo evolução

Triggers típicos:

- "evolua o documento `<id>`";
- "adicione diagramas/capítulos ao swarm existente";
- "atualize, expanda ou revise a entrega já produzida".

No modo evolução, trabalhe sobre a pasta existente. Não crie um swarm novo.

## 2. Contrato de qualidade

1. **Human-in-the-loop no início.** Enquadre objetivo, público, formato, escopo,
   restrições, critérios de sucesso e limite de ciclos antes de gerar agentes.
2. **Agentes declarativos.** Cada autor, revisor, coordenador e rubber duck é um
   arquivo Markdown autocontido.
3. **Modelo com proveniência.** O modelo declarado por agente deve existir na
   lista atual da ferramenta `task`; substituições ficam registradas.
4. **Evidência proporcional ao papel.** Autores e revisores de fatos pesquisam e
   verificam fontes. Revisores de forma não executam pesquisa ritual.
5. **Determinismo onde é mecânico.** URLs, contas de tabelas, matriz de notas,
   teto de ciclos e fatos do relatório final são computados por scripts.
6. **Julgamento onde é semântico.** Autores, revisores e rubber duck continuam
   responsáveis por correção, relevância, clareza, coerência e sustentação das
   afirmações.
7. **Portão duro.** Todo tópico precisa de `A` ou `A+`. Achado crítico do rubber
   duck veta a entrega.
8. **Sem regressão.** Evoluções reavaliam tópicos antigos e novos.
9. **Rastreabilidade.** Cada ciclo preserva relatórios humanos e artefatos
   estruturados.
10. **Escala explícita.** Ao atingir `max_cycles` sem aprovação, pare e informe o
    usuário; nunca entregue algo abaixo da barra em silêncio.
11. **Diretriz editorial compartilhada.** Registre o perfil editorial e o público
    no brief. Autores o aplicam, revisores o avaliam dentro de suas dimensões e o
    coordenador mantém a uniformidade. Não crie um agente apenas para humanizar
    texto.

### 2.1. Perfil editorial técnico

Para documentos de arquitetura de nuvem, o perfil padrão é
`principal-cloud-solution-architect`, salvo orientação diferente no enquadramento.
Adote o nível de análise, discernimento e comunicação esperado de um Principal
Cloud Solution Architect de Microsoft, AWS ou Google Cloud. A referência é a
senioridade técnica, não vínculo profissional, voz institucional ou preferência
comercial por um provedor.

O perfil não substitui a especialidade de cada autor nem presume que o público
seja técnico. Em outros domínios, preserve os princípios abaixo e registre no
brief um perfil adequado, sem forçar conteúdo de nuvem.

**Julgamento e precisão**

- Comece pelo problema, pela decisão ou pela conclusão relevante. Conecte
  escolhas técnicas a resultados de negócio e consequências operacionais; um
  catálogo de serviços não substitui uma arquitetura.
- Nas decisões arquiteturais, explicite critérios, alternativas relevantes,
  recomendação, trade-offs e condições que mudariam a escolha. Em vez de apenas
  dizer "depende", identifique os fatores e como alteram a decisão.
- Considere segurança, confiabilidade, desempenho, custo, governança e operação
  conforme o assunto. Não repita todos os pilares como checklist em cada seção.
- Diferencie fatos verificados, premissas, estimativas e recomendações. Não
  invente experiências pessoais, números, benchmarks, referências ou capacidades
  de produtos. Use métricas como latência, SLO, RTO e RPO quando ajudarem a decidir,
  sem criar valores para aparentar precisão.
- Preços, limites, regiões, disponibilidade e SLAs exigem fontes oficiais atuais.
  Quando não puder verificar, declare a limitação; nunca apresente uma suposição
  como fato confirmado.
- Respeite diferenças de comportamento, responsabilidade e modelo comercial dos
  provedores. Não force equivalências entre serviços nem comparações multicloud
  fora do escopo.

**Escrita e público**

- Escreva de forma direta, natural, respeitosa e segura, sem arrogância,
  intimidade artificial ou didatismo excessivo. Varie o ritmo sem fabricar
  fragmentos ou imperfeições de raciocínio.
- Prefira verbos concretos e mecanismos a adjetivos. Se algo é "seguro",
  "resiliente" ou "escalável", explique o que sustenta a afirmação e seus limites.
- Evite clichês, superlativos, linguagem promocional, conectivos repetitivos,
  introduções como "no cenário atual" e conclusões que apenas repetem o texto.
  Não use travessões longos no texto autoral; preserve citações literais e código.
- Explique siglas conforme o público, sem infantilizar especialistas. Para
  executivos, priorize impacto, riscos, investimento e decisão; para técnicos,
  mecanismos, restrições, integração, modos de falha, operação e validação.
  Para público misto, ofereça síntese decisória e aprofundamento técnico.
- Use seções, listas, tabelas e exemplos quando facilitarem a compreensão.
  Naturalidade não significa proibir estrutura nem impor a mesma estrutura a
  todos os tópicos.

**Estrutura dos documentos**

Documentos devem desenvolver argumentos com encadeamento claro e extensão
proporcional à necessidade do leitor. Preserve as condições indispensáveis para
interpretar recomendações, mesmo em resumos executivos. Sugira diagramas para
explicar relações, fluxos, dependências ou limites da arquitetura, não como
decoração.

Se faltar informação que mude materialmente a recomendação, peça esclarecimento;
para lacunas menores, avance com premissas explícitas. Na revisão, elimine frases
sem informação e confira se as conclusões decorrem das evidências. Priorize
correção técnica, utilidade para decisão e clareza, nessa ordem.

### 2.2. Aplicação por papel

Ao gerar ou adaptar agentes, materialize em cada arquivo o perfil, o público e
as regras pertinentes à sua missão. Não deixe apenas uma referência ao prompt
compartilhado: os arquivos precisam continuar autocontidos. Não copie instruções
de autoria para quem só revisa.

| Papel existente | Responsabilidade editorial |
|---|---|
| Autor | Produzir e corrigir o conteúdo segundo o perfil, preservando sua especialidade e seu escopo. |
| Revisor de clareza e público (`form`) | Avaliar naturalidade profissional, precisão da linguagem, organização, densidade, jargão, redundância e adequação ao leitor. |
| Revisor de completude decisória (`form`) | Avaliar critérios, alternativas, trade-offs, riscos, limites e condições da recomendação, quando aplicáveis. |
| Revisor de fatos (`fact`) | Verificar sustentação das afirmações, atualidade das capacidades e distinção entre fatos, premissas e estimativas. |
| Coordenador | Uniformizar voz, terminologia e profundidade na consolidação, sem inventar fatos nem decidir silenciosamente divergências técnicas. |
| Rubber duck | Auditar se clareza aparente ou fluência mascaram lacunas, contradições ou notas infladas. |

Cubra clareza/aderência ao público e completude decisória com os revisores de
forma existentes, sem adicionar um editor ou uma etapa separada. Eles não editam
o documento: devolvem trechos problemáticos e correções acionáveis aos autores.
A pesquisa continua proporcional à classe de evidência.

A aderência editorial integra as notas por tópico nas dimensões existentes.
Não é um selo separado nem uma
substituição do portão. `A` exige atendimento ao perfil e ao público na dimensão
avaliada, sem lacunas materiais; estilo agradável não compensa recomendação sem
sustentação. Critérios que não se aplicam ao tópico devem ser justificados, não
preenchidos artificialmente. Scripts não atribuem notas de estilo ou maturidade
arquitetural.

Mudar os templates não atualiza agentes já gerados. Quando solicitado, ajuste o
brief e as declarações existentes e registre a versão da nova diretriz. Um pedido
apenas de atualização de instruções não dispara reescrita da entrega: preserve
outputs, relatórios, versões e notas históricas, sem alegar aprovação pelo novo
perfil. Para aplicar a diretriz à entrega, execute o modo evolução.

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
por data: `<YYYY-MM-DD>-SWARM-<XX>`.

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

## 6. Régua e matriz computável

Escala canônica:

```text
D-  D  D+  C-  C  C+  B-  B  B+  A-  A  A+
```

- `A` e `A+`: aprovam;
- `A-` ou menos: bloqueiam;
- toda nota exige justificativa e correção acionável;
- a nota mínima entre revisores é a nota efetiva do tópico.

Além do relatório Markdown, cada ciclo deve gerar
`reports/cycle-0N-review.yaml`:

```yaml
schema_version: 1
skill_version: "3.0.0"
mode: document
cycle: 2
max_cycles: 5
topics:
  - topico: "T01: Enquadramento"
    nota_minima: A
    revisor_da_minima: reviewer-02-clarity
    bloqueia: false
rubberduck:
  critico: false
  achados: []
```

O rubber duck deve conferir a consistência entre `.md` e `.yaml`. O portão usa o
arquivo estruturado, não uma interpretação livre da prosa.

## 7. Evidência e cache de fontes

### Exigência por papel

| Papel | `sources_min` | Regra |
|---|---:|---|
| Autor | 5 | Fontes online distintas, funcionais e relevantes. |
| Revisor de fatos: precisão, fontes, compliance, segurança, governança | 5 | Pesquisa própria e conferência das fontes dos autores. |
| Revisor de forma: estrutura, clareza, narrativa, aderência ao público | 0 | Fonte apenas quando contestar um fato. |
| Rubber duck | 0 | Fonte quando contestar um fato; não há coleta ritual. |

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
- perfil herdado preserva a especialidade, mas recebe o contrato editorial do
  brief atual; não herde tom, público ou critérios de revisão sem conferir;
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
- perfil editorial, propondo `principal-cloud-solution-architect` para
  arquitetura de nuvem e ajustando a linguagem ao público;
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
skill_version: "3.0.0"
mode: document
max_cycles: 5
editorial_profile: <perfil definido no enquadramento>
---
```

4. Registre enquadramento, público e familiaridade técnica, perfil editorial,
   tópicos de importância e critérios de sucesso. Inclua clareza e utilidade
   decisória na avaliação; registre adaptações e restrições do perfil.

### Fase 2 — Memória, agentes e modelos

1. Consulte a memória e registre o que será reutilizado.
2. Defina de 3 a 6 autores complementares.
3. Defina de 3 a 5 revisores em dimensões distintas; classifique cada dimensão
   como `fact` ou `form` para calcular `sources_min`. Cubra precisão factual,
   clareza/aderência ao público e completude decisória nos papéis existentes.
4. Gere coordenador e rubber duck.
5. Escolha e valide modelos contra a sessão.
6. Grave `reports/agent-models.md` com `Status` e `Substituído de`.
7. Gere os arquivos declarativos com o contrato editorial materializado por
   papel conforme o brief e a seção 2.2.
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
   `sources/sources-index.md` e grave `reports/cycle-0N-authors.md`. Uniformize
   voz, terminologia e profundidade antes da revisão; divergências de conteúdo
   voltam aos autores, não são resolvidas apenas por edição de estilo.
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
   `reports/cycle-0N-review.md`, incluindo a aderência editorial nas dimensões
   atribuídas. Correções voltam aos autores e são reavaliadas; não faça uma
   reescrita de conteúdo depois do portão sem nova revisão.
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

Leia `brief.md`, documento atual, último relatório/review estruturado,
agentes e artefatos de checks. Identifique tópicos afetados e novos tópicos.

### E.1 — Composição

- avalie se faltam autores ou dimensões de revisão;
- adicione apenas especialidades realmente novas;
- mudanças de perfil editorial adaptam os agentes existentes, sem criar um
  agente de humanização; confronte perfis herdados com o brief atualizado;
- reavalie modelos se a missão mudou;
- registre em `reports/evo-<XX>-plan.md`;
- consulte memória, usando o swarm original como fonte primária.

### E.2 — Histórico

Acrescente ao `brief.md` uma seção de evolução com data, versão atual da skill,
pedido, tópicos, agentes e artefatos novos. Não apague histórico.
Ao iniciar a nova execução, atualize `skill_version` no frontmatter do brief
para a versão usada, sem alterar versões dos relatórios de ciclos anteriores.
Registre mudanças de perfil editorial e materialize-as nas declarações.

### E.3 — Uniformidade

Reative **todos** os autores existentes e novos. Cada um revisita sua seção para
incorporar o perfil editorial, terminologia, referências cruzadas e impactos da
evolução sem regredir conteúdo já aprovado.

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
<Especialidade, ponto de vista, perfil editorial e público concretos do brief.>

## Missão
Produzir os tópicos atribuídos no nível, tom e escopo do brief.

## Contrato editorial
- Aplique o perfil declarado sem abandonar sua especialidade. Escreva de forma
  direta e natural, segura sem arrogância, ajustando jargão e profundidade ao
  público. Não fabrique experiências pessoais ou imperfeições de raciocínio.
- Comece pelo problema ou conclusão relevante. Nas decisões arquiteturais,
  conecte resultados de negócio e operação a critérios, alternativas,
  recomendação, trade-offs e condições que mudariam a escolha. Explique de que
  depende, sem impor esse roteiro a trechos que não contêm uma decisão.
- Separe fatos, premissas, estimativas e recomendações. Não invente valores,
  capacidades ou referências; verifique informações voláteis em fontes oficiais
  e explicite o que não foi possível confirmar. Não force equivalências multicloud.
- Explique mecanismos e limites em vez de acumular adjetivos. Evite clichês,
  linguagem promocional, conectivos repetidos e travessões longos no texto
  autoral. Preserve citações e código.
- Use seções, listas, tabelas, exemplos e métricas apenas quando úteis. Para
  executivos, priorize impacto, risco, investimento e decisão; para técnicos,
  mecanismos, restrições, falhas, operação e validação.
- Peça esclarecimento para lacunas que mudem a recomendação; explicite premissas
  para as demais. Revise conclusões contra as evidências e elimine redundâncias.
  Priorize correção técnica, utilidade decisória e clareza.

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
Avaliar cada tópico do brief sob a dimensão atribuída, considerando
<perfil editorial e público concretos>. Não editar o documento;
devolver os achados aos autores.

## Critérios da dimensão
<Materialize somente a rubrica pertinente à missão, sem deixar este marcador:
clareza/público avalia linguagem natural profissional, organização, jargão,
densidade, redundância e ausência de promoção; completude decisória avalia
critérios, alternativas, recomendação, trade-offs, limites e condições de mudança
quando pertinentes; fatos verifica sustentação, atualidade e distinção entre
fatos, premissas e estimativas.>

Exija evidência no trecho para justificar a nota e uma correção
acionável. `A` exige atendimento à dimensão sem lacunas materiais; não premie
apenas fluência nem exija estruturas desnecessárias. Justifique critérios não
aplicáveis. As notas alimentam a matriz por tópico e o portão existente.

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

Materialize o perfil editorial e o público do brief nos contratos de cada
agente. Garanta cobertura de clareza e completude decisória pelos revisores
existentes. Na consolidação, uniformize voz, terminologia e profundidade sem
apagar ressalvas ou inventar fatos. Devolva divergências técnicas aos autores;
revisores não reescrevem conteúdo.
Submeta alterações de conteúdo à revisão, não a uma edição posterior ao portão.

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
- aderência ao perfil e ao público do brief: estilo ou fluência não podem
  mascarar falta de evidência, trade-offs omitidos ou ressalvas removidas;
- fonte acessível que não sustenta a afirmação;
- falhas ou avisos dos checks determinísticos;
- aritmética relevante não marcada;
- divergência entre review Markdown e YAML;
- aplicação correta do portão e de `max_cycles`.

## Saída
Achados priorizados como Crítico/Importante/Menor, com alvo, evidência e
correção. Um achado Crítico deve aparecer em `rubberduck.critico: true`.
```

## 14. Referência dos scripts determinísticos

Todos usam somente Python stdlib. Consulte `--help` para opções exatas.

| Script | Função | Bloqueia quando |
|---|---|---|
| `verify_sources.py` | testa URLs e mantém cache auditável | há fonte `fail` |
| `verify_tables.py` | recalcula tabelas marcadas | conta marcada não fecha |
| `lint_agents.py` | valida frontmatter e swarm dos agentes | agente está inválido |
| `gate.py` | aplica régua, crítico e `max_cycles` | retorna exit `1`, `2` ou `3` |
| `final_report.py` | deriva fatos do relatório final | artefatos estão ausentes/inválidos |
| `update_memory.py` | propõe e, após aprovação, aplica memória | swarm não aprovado ou fonte inelegível |

## 15. Checklist — documento

- [ ] `brief.md` registra `skill_version`, `mode`, `max_cycles` e tópicos.
- [ ] Perfil editorial e público estão registrados no brief e materializados
      nos contratos dos agentes, preservando especialidades e responsabilidades.
- [ ] Memória consultada e reutilizações registradas.
- [ ] Agentes declarativos criados e `lint_agents.py` aprovado.
- [ ] Modelos confirmados na sessão; substituições registradas.
- [ ] Autores e revisores de fatos cumprem sua exigência de fontes.
- [ ] Cada ciclo tem relatórios humanos, check de fontes, check de tabelas e YAML.
- [ ] Clareza/aderência ao público e completude decisória foram avaliadas pelos
      revisores existentes; as notas integram o portão por tópico.
- [ ] `gate.py` retornou `0`.
- [ ] Rechecagem final de fontes executada com `--force`.
- [ ] `final-report.md` foi derivado por script e recebeu apenas narrativa humana.
- [ ] Proposta de memória foi gerada e revisada.
- [ ] Documento final tem índice e bibliografia.

## 16. Resposta ao usuário

```markdown
Swarm concluído: `<OUTPUT_ROOT>/<swarm_id>/`

Documento: `output/<documento>.md`
Relatório: `reports/final-report.md`
Versão da skill: <skill_version>
Ciclos: <N> | Tópicos: <k>/<k> ≥ A | Gate: aprovado
Fontes: <ok> ok, <warn> warn, 0 fail
```
