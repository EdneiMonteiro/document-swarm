---
name: document-swarm
description: "Use this skill whenever the user asks to implement, create, or write a substantial document about any topic (whitepaper, technical guide, report, RFC, policy, comparison), OR to evolve/expand/update an EXISTING swarm document (add diagrams, sections, maturity models, templates, etc.). Triggers include: 'implemente um documento sobre <tema>', 'crie/escreva um documento sobre <tema>', 'monte um swarm para escrever sobre <tema>', 'preciso de um documento completo sobre <tema>', 'quero um whitepaper/relatório/guia técnico sobre <tema>', 'evolua o documento/playbook <id>', 'adicione <X> ao swarm <id>', 'atualize/expanda o documento existente'. The skill asks framing questions, identifies author profiles and turns each into a declarative .md agent, creates a coordinator and reviewer agents plus a rubber-duck agent, then runs author→review cycles grading each topic D- to A+ until all topics reach at least A. For existing documents it runs in EVOLUTION mode: assess whether new authors/reviewers are needed, generate them, then the coordinator re-activates ALL agents (existing + new) to keep the whole deliverable uniform. Output goes to the resolved output root (an explicit user-provided target, the DOCSWARM_ROOT env var, or by default a 'swarms' subfolder inside the document-swarm clone). Do NOT use for short text like a single paragraph or an email."
---

# Document Swarm Skill

> 🐝 **Swarm de Documentação** — orquestra um enxame de agentes declarativos
> (autores + revisores + coordenador + rubber duck) para produzir um documento
> de alta qualidade sobre qualquer tema, em ciclos de melhoria iterativa até que
> **todos os tópicos avaliados atinjam nota mínima A**.

## Trigger

Use esta skill sempre que o usuário pedir algo como:

- "implemente um documento sobre <tema>"
- "crie/escreva um documento sobre <tema>"
- "monte um swarm para escrever sobre <tema>"
- "preciso de um documento completo sobre <tema>"
- "swarm de documentação sobre <tema>"
- "quero um whitepaper/relatório/guia técnico sobre <tema>"

Use também no **modo evolução** (documento já existente em um swarm), quando o
usuário pedir algo como:

- "evolua o documento/playbook <swarm_id>"
- "adicione <diagramas / seção / capítulo / modelo / template> ao swarm <id>"
- "atualize / expanda / revise o documento existente em <id>"
- "incorpore <X> à entrega já produzida"

Nesse caso, NÃO crie um swarm novo do zero: trabalhe sobre o swarm existente
seguindo a **Fase E — Modo Evolução** (abaixo).

Não dispare para pedidos triviais de texto curto (um parágrafo, um e-mail). O
swarm é para entregas substanciais que se beneficiam de múltiplas especialidades
e revisão iterativa.

## Princípios

1. **Agentes declarativos** — cada perfil (autor, revisor, coordenador, rubber
   duck) é materializado como um arquivo `.md` autocontido. O documento `.md` é a
   especificação do agente; a execução é feita despachando esse `.md` como prompt
   de um subagente (ferramenta `task`, tipo `general-purpose`).
2. **Human-in-the-loop no início** — sempre faça as perguntas de enquadramento
   ANTES de gerar qualquer agente. Não presuma escopo, público ou profundidade.
3. **Evidência obrigatória** — **todo** agente (autores e revisores) deve
   consultar **no mínimo 5 fontes online funcionais e verificadas** (documentação
   oficial, blogs de referência, artigos acadêmicos, normas). Toda fonte citada
   precisa ter URL **verificado como acessível** (HTTP 200 via `web_fetch`) na
   data de uso. Fonte quebrada ou inventada é falha grave.
4. **Qualidade com régua** — revisores dão nota de **D-** até **A+** por tópico,
   com sugestão de melhoria acionável. O ciclo se repete até **todos** os tópicos
   avaliados ficarem **≥ A** (A ou A+; **A- não passa**).
5. **Rubber duck transversal** — um agente rubber duck revisa o trabalho de
   TODOS os agentes (coordenador, autores, revisores) a cada ciclo, caçando
   falhas de lógica, vieses, lacunas e contradições que escaparam.
6. **Tudo rastreável** — cada ciclo produz relatórios versionados em `/reports`.

## Estrutura de saída

### Local de saída (output root)

A skill **não** tem um caminho fixo de saída. Antes de criar qualquer pasta,
resolva o **`<OUTPUT_ROOT>`** nesta ordem de prioridade:

1. **Destino explícito do usuário** — se o pedido indicar onde gravar (uma pasta,
   um drive, ou até um repositório separado para o produto de saída), use-o.
2. **Variável de ambiente `DOCSWARM_ROOT`** — se definida, `<OUTPUT_ROOT> = $env:DOCSWARM_ROOT`.
3. **Default = `<clone>\swarms`** — onde `<clone>` é a pasta onde este repositório
   `document-swarm` foi clonado. Descubra `<clone>` resolvendo o **alvo real do
   symlink** da skill:
   - Windows (PowerShell): `(Get-Item -Force "$env:USERPROFILE\.copilot\skills\document-swarm").Target`
   - Linux/macOS: `readlink -f "$HOME/.copilot/skills/document-swarm"`
   - Então `<OUTPUT_ROOT> = <clone>\swarms`.

Confirme o `<OUTPUT_ROOT>` resolvido com o usuário se houver ambiguidade. Crie o
diretório se não existir. Todo o restante desta skill usa `<OUTPUT_ROOT>` como
raiz das entregas. Cada pedido ganha uma subpasta:

```text
<OUTPUT_ROOT>\<YYYY-MM-DD>-SWARM-<XX>\
├─ brief.md                      # tema + respostas das perguntas + tópicos avaliados
├─ agents\
│  ├─ coordinator.md             # agente coordenador
│  ├─ rubber-duck.md             # agente rubber duck (revisor transversal)
│  ├─ authors\
│  │  ├─ author-01-<slug>.md     # um por perfil de autor
│  │  └─ author-02-<slug>.md
│  └─ reviewers\
│     ├─ reviewer-01-<slug>.md   # um por perfil de revisor
│     └─ reviewer-02-<slug>.md
├─ reports\
│  ├─ cycle-01-authors.md        # o que cada autor entregou no ciclo
│  ├─ cycle-01-review.md         # notas D- a A+ por tópico + sugestões
│  ├─ cycle-01-rubberduck.md     # achados do rubber duck
│  ├─ cycle-02-review.md         # ...
│  └─ final-report.md            # resumo do swarm: ciclos, notas finais, fontes
├─ sources\
│  └─ sources-index.md           # consolidado de todas as fontes verificadas
└─ output\
   └─ <documento-final>.md       # a entrega
```

`<XX>` é sequencial **por dia** (`01`, `02`, ...). Antes de criar, liste
`<OUTPUT_ROOT>` e escolha o próximo número livre para a data de hoje.

## Régua de notas

Da pior para a melhor:

```text
D-  D  D+   C-  C  C+   B-  B  B+   A-  A  A+
```

- **Portão de aprovação:** cada tópico avaliado precisa de **A** ou **A+**.
- **A- NÃO passa** — exige mais um ciclo de melhoria naquele tópico.
- Cada nota vem **sempre** acompanhada de: (a) justificativa curta, (b) sugestão
  de melhoria **acionável** (o que mudar, não só "melhore").

## Fluxo de execução

### Fase 0 — Perguntas de enquadramento (obrigatória)

Antes de qualquer coisa, use a ferramenta `ask_user` para coletar o essencial.
Adapte as perguntas ao tema, mas cubra no mínimo:

- **Objetivo** do documento (decidir, ensinar, vender, especificar, auditar...).
- **Público-alvo** e nível de senioridade.
- **Formato/tipo** (whitepaper, guia técnico, relatório executivo, tutorial,
  RFC, artigo, política, comparativo...).
- **Profundidade e extensão** alvo (páginas/seções aproximadas).
- **Idioma** da entrega (PT-BR por padrão).
- **Escopo incluído e excluído** (o que NÃO abordar).
- **Restrições** (tom, normas, compliance, confidencialidade, tecnologias).
- **Fontes preferenciais ou proibidas** (ex.: só documentação oficial).
- **Critérios de sucesso** do usuário (como ele vai julgar "ficou bom").
- **Prazo/urgência** e **nº máximo de ciclos** aceitável (padrão 5).

Se o usuário não responder algo, registre um padrão sensato no `brief.md` e siga.

> **Atalho no modo evolução:** se o documento já existe (Fase E), não repita a
> Fase 0 inteira. Pergunte apenas o que for novo para a evolução pedida (ex.:
> "quer diagramas em quais seções?", "quantos níveis de maturidade?"). O escopo
> original já está no `brief.md`.

### Fase E — Modo Evolução (documento já existente) ⭐

Use esta fase quando o pedido é **evoluir/expandir/atualizar um documento já
produzido** por um swarm anterior (em vez de criar do zero). O princípio central
é: **estender sem regredir** e **manter a entrega inteira uniforme**.

**E.0 — Localizar e diagnosticar o swarm existente (PRIMEIRO PASSO OBRIGATÓRIO).**
1. Identifique a pasta do swarm (`<OUTPUT_ROOT>\<swarm_id>`). Leia o
   `brief.md`, o documento em `output\`, o `reports\final-report.md` (ou a última
   `cycle-*-review.md`) e liste os agentes existentes em `agents\`.
2. Entenda o **pedido de evolução** (o que adicionar/mudar) e mapeie em quais
   **tópicos de importância** ele incide e se ele cria **novos tópicos**.

**E.1 — Avaliar se faltam agentes (autores e revisores).** Este é o passo que
decide a composição do enxame para a evolução:
- Para cada capacidade exigida pela evolução, verifique se já existe um autor com
  esse perfil. Se a evolução exige uma especialidade nova (ex.: "diagramas de
  arquitetura" → **Arquiteto/Diagramador**; "modelo de maturidade + assessment" →
  **Especialista em Maturidade & Assessment**), **gere novos autores** com o
  Template de Autor (numerando na sequência: `author-07`, `author-08`, ...).
- Da mesma forma, avalie se é preciso uma **nova dimensão de revisão** (ex.:
  **Revisor de Diagramas & Visualização** para validar sintaxe Mermaid e fidelidade
  arquitetural). Se sim, gere `reviewer-06`, etc., com o Template de Revisor.
- Registre a decisão (quais agentes novos e por quê) em
  `reports\evo-<MM>-plan.md`. Se nenhum agente novo for necessário, diga isso
  explicitamente e justifique.

**E.2 — Atualizar o `brief.md`.** Acrescente uma seção "Evolução `<EVO-XX>`" com:
a data, o pedido, os novos tópicos (se houver, ex.: `T13 Diagramas`,
`T14 Maturidade & Assessment`), os novos agentes e os artefatos adicionais a gerar.
Não apague o histórico anterior.

**E.3 — Coordenador ativa TODOS os agentes (uniformidade).** A regra de ouro da
evolução: o coordenador **reativa o enxame inteiro** — autores **existentes E
novos** — não só os novos. Cada autor revisita a sua seção para **incorporar de
forma coerente** o que a evolução introduz (ex.: cada autor adiciona o diagrama
Mermaid pertinente à sua seção; todos passam a referenciar o novo modelo de
maturidade com a mesma terminologia). Isso evita uma "ilha" nova desconectada do
resto e garante tom, vocabulário e referências cruzadas uniformes em toda a
entrega.
- Despache os autores em paralelo (cada um edita seu `output\sections\NN-*.md`),
  com instruções: (a) o que a evolução pede na seção dele; (b) **não regredir** o
  conteúdo já aprovado (≥ A); (c) manter rastreabilidade de fontes (≥5
  verificadas); (d) usar a terminologia canônica definida pelos novos autores.
- Os autores novos produzem suas seções/artefatos novos (ex.: galeria de
  diagramas, modelo de maturidade detalhado, template de assessment preenchível).

**E.4 — Consolidar e revisar (loop normal).** Reconsolide o documento (incluindo
os novos tópicos na ordem certa) e siga o **loop da Fase 3 a partir do passo 3**:
despache **todos** os revisores (existentes + novos) sobre o documento evoluído;
some o rubber duck; aplique o portão **≥ A para todos os tópicos, novos e
antigos** (a evolução não pode ter rebaixado nenhum tópico). Numere os relatórios
como `cycle-0N-*` continuando a sequência do swarm, e/ou prefixe com `evo-<XX>`.

**E.5 — Entrega.** Igual à Fase 4, mas o `final-report.md` ganha uma seção de
evolução (o que mudou, agentes adicionados, novos tópicos e suas notas).

> **Resumo da Fase E:** localizar → diagnosticar → **avaliar se faltam agentes** →
> gerar os que faltam → **coordenador reativa o enxame inteiro** → consolidar →
> revisar todos os tópicos (portão ≥ A) → entregar. Nunca trate a evolução como um
> apêndice isolado: ela atravessa o documento todo.

### Fase 1 — Setup da pasta

> **Modo evolução:** se você está trabalhando sobre um swarm existente (Fase E),
> **pule a Fase 1** — a pasta e o `brief.md` já existem; você apenas o estende.

1. Compute `swarm_id = <YYYY-MM-DD>-SWARM-<XX>` (próximo `XX` livre do dia).
2. Crie a árvore de pastas descrita acima.
3. Escreva `brief.md` com: tema, respostas das perguntas, objetivo, público,
   formato, escopo, restrições e a **lista de "tópicos de importância"** que
   serão avaliados (estes são os itens que o portão A exige). Derive os tópicos
   do tema + objetivo (ex.: para um guia de arquitetura: "Visão geral",
   "Componentes", "Segurança", "Custos", "Trade-offs", "Referências"...).

### Fase 2 — Identificar perfis e gerar agentes declarativos

1. **Autores:** a partir do tema, identifique de **3 a 6 perfis** complementares
   que, juntos, cobrem o assunto com profundidade (ex.: para "Zero Trust no
   Azure": Arquiteto de Identidade, Engenheiro de Rede, Especialista em
   Compliance, Redator Técnico). Para CADA perfil, gere um `.md` em
   `agents\authors\` usando o **Template de Autor**.
2. **Revisores:** identifique de **3 a 5 perfis de revisão** que cubram dimensões
   distintas de qualidade. Sugestão de base (ajuste ao tema):
   - **Precisão técnica** (fatos, exatidão, ausência de erros).
   - **Estrutura & clareza** (organização, fluência, didática).
   - **Completude & escopo** (nada faltando, nada fora do escopo).
   - **Aderência ao público** (nível, tom, utilidade prática).
   - **Fontes & evidências** (≥5 fontes funcionais, citação correta, atualidade).
   Para CADA perfil, gere um `.md` em `agents\reviewers\` usando o **Template de
   Revisor**.
3. **Coordenador:** gere `agents\coordinator.md` usando o **Template de
   Coordenador**.
4. **Rubber duck:** gere `agents\rubber-duck.md` usando o **Template de Rubber
   Duck**.

### Fase 3 — Loop de produção (você atua como Coordenador)

Você, executando a skill, **é o coordenador**. Siga o que `coordinator.md`
declara. Para cada ciclo `N` (começando em 1):

1. **Despachar autores.** Para cada autor, lance um subagente `task`
   (`general-purpose`), passando o conteúdo do `.md` do autor como prompt, mais:
   o `brief.md`, a seção/tópicos sob responsabilidade dele e — a partir do ciclo
   2 — as **sugestões de melhoria** dos revisores para os tópicos dele. Cada
   autor escreve/atualiza sua parte direto no `output\` e registra suas fontes.
   Agentes independentes podem rodar em paralelo (background).
2. **Montar o documento.** Consolide as contribuições em `output\<doc>.md` (TOC,
   seções na ordem certa, fontes unificadas em `sources\sources-index.md`).
   Registre `reports\cycle-0N-authors.md`.
3. **Despachar revisores.** Para cada revisor, lance um subagente `task` passando
   o `.md` do revisor + o documento atual. Cada revisor devolve, para **cada
   tópico de importância** do `brief`, uma **nota D- a A+** + justificativa +
   sugestão acionável. Consolide em `reports\cycle-0N-review.md` com uma matriz
   tópico × revisor e a **nota mínima por tópico** (a que vale para o portão).
4. **Despachar rubber duck.** Lance o subagente rubber duck para revisar o
   trabalho de coordenador + autores + revisores do ciclo (consistência das
   notas, fontes realmente verificadas, lacunas, vieses). Salve
   `reports\cycle-0N-rubberduck.md`. Achados críticos do rubber duck viram
   melhorias obrigatórias no próximo ciclo, mesmo em tópicos já com A.
5. **Avaliar o portão.** Se **todos** os tópicos estão **≥ A** E o rubber duck
   não levantou achado crítico → **sucesso**, vá para a Fase 4. Caso contrário,
   incremente `N` e volte ao passo 1, passando aos autores apenas os tópicos
   abaixo de A (e os achados do rubber duck).
6. **Trava de segurança.** Se atingir o **nº máximo de ciclos** sem aprovar tudo,
   pare, escreva o estado atual no `final-report.md` e **escale ao usuário** com
   os tópicos teimosos e por quê — não entregue silenciosamente algo abaixo de A.

### Fase 4 — Entrega

1. Finalize `output\<documento-final>.md` (limpo, com índice e bibliografia).
2. Escreva `reports\final-report.md`: nº de ciclos, matriz final de notas (todas
   ≥ A), nº de fontes verificadas, perfis usados e principais decisões.
3. Responda ao usuário com o caminho do swarm, do documento e um resumo curto
   (não cole o documento inteiro no chat salvo pedido).

## Regra de fontes (vale para TODO agente)

- Mínimo **5 fontes online distintas e funcionais** por agente (autores e
  revisores). Priorize: **documentação oficial** > **normas/padrões** > **artigos
  acadêmicos** > **blogs de referência reconhecidos**. Evite conteúdo gerado por
  IA sem autoria e fóruns não confiáveis.
- **Verifique cada URL** com `web_fetch` (e/ou `web_search` para localizar):
  registre o status (ex.: `HTTP 200 em 2026-06-29`). **Nunca** cite uma URL sem
  abri-la. Se cair, troque por outra fonte funcional.
- Cada autor registra suas fontes ao final da sua seção e no
  `sources\sources-index.md` (URL, tipo, título, autor/org, data de acesso).
- Revisores **conferem** as fontes: contagem, funcionamento e se sustentam de
  fato as afirmações. Fonte fraca/irrelevante derruba a nota do tópico
  "Fontes & evidências".

---

## Template de Autor (`agents\authors\author-XX-<slug>.md`)

```markdown
---
name: author-<XX>-<slug>
kind: author
role: <Perfil, ex.: Arquiteto de Identidade>
model: claude-sonnet-4.6
swarm: <swarm_id>
sources_min: 5
---

# Autor: <Perfil>

## Persona
Você é um(a) **<perfil>** sênior. <1-2 frases sobre experiência e ponto de vista
que esse perfil traz para o tema.>

## Missão
Produzir, com excelência, as seções/tópicos sob sua responsabilidade do documento
descrito em `brief.md`, no nível e tom definidos para o público-alvo.

## Tópicos sob sua responsabilidade
- <tópico A>
- <tópico B>

## Como trabalhar
1. Leia `brief.md` e o estado atual de `output\` (se existir).
2. Pesquise: consulte **≥ 5 fontes online funcionais** (documentação oficial,
   normas, artigos acadêmicos, blogs de referência). **Verifique cada URL**
   (HTTP 200) antes de citar.
3. Escreva conteúdo correto, específico e acionável — sem encher linguiça, sem
   inventar fatos. Onde faltar dado, diga o que falta em vez de fabular.
4. Se houver **sugestões de melhoria** de revisores (ciclos ≥ 2), trate cada uma
   explicitamente e eleve a qualidade do tópico apontado.
5. Salve sua contribuição na(s) seção(ões) certa(s) de `output\<doc>.md` e liste
   suas fontes no formato abaixo, também atualizando `sources\sources-index.md`.

## Formato das fontes (obrigatório, ≥ 5)
| # | Título | Tipo | URL | Verificado |
|---|--------|------|-----|------------|
| 1 | ...    | oficial/norma/acadêmico/blog | https://... | HTTP 200 em <data> |

## Padrão de qualidade
- Precisão factual acima de tudo; afirmações sustentadas por fonte.
- Específico > genérico; exemplos concretos quando ajudarem.
- Coerente com os demais autores (sem contradição/duplicação).
- Linguagem clara no idioma e tom do `brief`.
```

## Template de Revisor (`agents\reviewers\reviewer-XX-<slug>.md`)

```markdown
---
name: reviewer-<XX>-<slug>
kind: reviewer
role: <Dimensão, ex.: Precisão técnica>
model: claude-sonnet-4.6
swarm: <swarm_id>
sources_min: 5
scale: "D- D D+ C- C C+ B- B B+ A- A A+"
gate: "A"
---

# Revisor: <Dimensão>

## Persona
Você é um(a) revisor(a) exigente focado(a) em **<dimensão>**. Alto sinal, zero
ruído: só aponta o que importa, mas não deixa passar nada relevante na sua área.

## Missão
Avaliar o documento atual (`output\<doc>.md`) sob a ótica de **<dimensão>**, dando
para **cada tópico de importância** listado no `brief.md` uma nota na régua
`D- … A+` com justificativa e **sugestão de melhoria acionável**.

## Como avaliar
1. Leia `brief.md` (tópicos de importância e critérios de sucesso) e o documento.
2. Para sustentar seu julgamento, consulte **≥ 5 fontes online funcionais** e
   **verifique os URLs** (HTTP 200). Confira se as fontes citadas pelos autores
   existem, funcionam e sustentam as afirmações.
3. Seja calibrado: **A/A+** = pronto para publicar nesta dimensão; **B** = bom mas
   com lacunas; **C** = sério retrabalho; **D** = inadequado. **A- não é
   aprovação** — indique exatamente o que falta para virar A.

## Saída (obrigatória)
Para cada tópico:
| Tópico | Nota | Justificativa | Sugestão de melhoria (acionável) |
|--------|------|---------------|----------------------------------|
| <t>    | B+   | ...           | "Adicione ... / corrija ... / cite ..." |

Encerre com: nota mínima geral, tópicos que **bloqueiam** o portão (< A) e suas
próprias fontes (tabela ≥ 5, com verificação).
```

## Template de Coordenador (`agents\coordinator.md`)

```markdown
---
name: coordinator
kind: coordinator
model: claude-sonnet-4.6
swarm: <swarm_id>
gate: "A"
max_cycles: <n, padrão 5>
---

# Coordenador do Swarm

## Missão
Orquestrar autores, revisores e o rubber duck para entregar o documento do
`brief.md` com **todos os tópicos de importância ≥ A**, no menor nº de ciclos.

## Loop (por ciclo N)
1. **Autores:** despache cada autor (subagente `general-purpose`), passando o
   `.md` do autor + `brief.md` + tópicos dele + (ciclo ≥ 2) as sugestões dos
   revisores. Agentes independentes em paralelo. Eles escrevem em `output\`.
2. **Consolidar:** monte `output\<doc>.md` (índice, ordem, fontes unificadas) e
   registre `reports\cycle-0N-authors.md`.
3. **Revisores:** despache cada revisor com o documento atual. Colete notas
   D-…A+ por tópico + sugestões. Consolide `reports\cycle-0N-review.md` com a
   matriz tópico × revisor e a **nota mínima por tópico**.
4. **Rubber duck:** despache o rubber duck sobre o trabalho de todos. Salve
   `reports\cycle-0N-rubberduck.md`. Achados críticos viram melhorias
   obrigatórias.
5. **Portão:** se todo tópico ≥ A e sem achado crítico do rubber duck → entregar.
   Senão, N+1 e volte ao passo 1 com só os tópicos < A + achados do rubber duck.
6. **Trava:** ao bater `max_cycles` sem aprovar tudo, pare e **escale ao
   usuário** (não entregue < A em silêncio).

## Regras
- Nunca dilua a régua para "fechar" o documento. A barra é A.
- Garanta que cada agente cumpriu a regra de ≥ 5 fontes verificadas.
- Mantenha tudo rastreável em `reports\` e `sources\`.

## Modo evolução (documento existente)
- Quando o pedido for **evoluir um documento já entregue**, primeiro **avalie se
  faltam agentes** (autores/revisores) para a nova demanda e gere os que faltarem.
- **Ative TODOS os agentes** — existentes E novos — para que a mudança seja
  incorporada de forma uniforme em toda a entrega (mesma terminologia, referências
  cruzadas, tom). Nunca trate a novidade como apêndice isolado.
- O portão ≥ A vale para **todos** os tópicos, novos e antigos: a evolução não
  pode rebaixar nada já aprovado.
```

## Template de Rubber Duck (`agents\rubber-duck.md`)

```markdown
---
name: rubber-duck
kind: rubber-duck
model: claude-sonnet-4.6
swarm: <swarm_id>
---

# Rubber Duck (Revisor Transversal)

## Missão
Revisar o trabalho de **todos** os agentes do ciclo — coordenador, autores e
revisores — caçando o que cada um, sozinho, não enxerga: erros de lógica,
contradições entre seções, vieses, lacunas de escopo, notas mal calibradas e
fontes que não sustentam as afirmações.

## O que checar
- **Autores:** afirmações sem fonte, fatos duvidosos, contradições entre seções,
  duplicação, fuga de escopo, fontes inventadas/quebradas (amostre e verifique
  URLs com HTTP 200).
- **Revisores:** notas coerentes com a evidência? Alguém deu A para um tópico
  ainda fraco? Sugestões são acionáveis? Alguma dimensão importante ficou sem
  cobertura?
- **Coordenador:** o portão (≥ A) está sendo aplicado de verdade? Algum tópico
  passou indevidamente? Os relatórios batem com o documento?

## Saída
- Lista priorizada de achados (Crítico / Importante / Menor), cada um com:
  agente-alvo, evidência, e **correção recomendada**.
- Veredito: o ciclo pode ser aprovado, ou há achado **Crítico** que obriga novo
  ciclo mesmo com todos os tópicos em A?
- Suas próprias fontes quando contestar um fato (≥ 5 quando aplicável, com URL
  verificado).
```

## Checklist antes de entregar

- [ ] Pasta `<OUTPUT_ROOT>\<YYYY-MM-DD>-SWARM-<XX>\` criada com a árvore
      completa (`agents/authors`, `agents/reviewers`, `reports`, `sources`,
      `output`).
- [ ] `brief.md` com perguntas respondidas e tópicos de importância listados.
- [ ] 3–6 agentes de autor + 3–5 agentes de revisor + coordenador + rubber duck,
      todos como `.md` declarativos.
- [ ] Ciclos registrados em `reports\` (authors, review, rubberduck por ciclo).
- [ ] **Todos** os tópicos de importância com nota final **≥ A** (ou escalonado
      ao usuário se bater `max_cycles`).
- [ ] Cada agente cumpriu **≥ 5 fontes online verificadas (HTTP 200)**;
      consolidadas em `sources\sources-index.md`.
- [ ] Documento final em `output\` com índice e bibliografia.
- [ ] `reports\final-report.md` escrito.

## Resposta ao usuário

Depois de concluir, responda com:

```markdown
Swarm concluído: `<OUTPUT_ROOT>\<swarm_id>\`

Documento: `output\<doc>.md`
Ciclos: <N>   |   Tópicos avaliados: <k> (todos ≥ A)
Autores: <lista de perfis>   |   Revisores: <lista de dimensões>
Fontes verificadas: <total>
```
