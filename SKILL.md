---
name: document-swarm
description: "Use when the user asks for a substantial document (playbook, whitepaper, report, RFC, policy, technical guide, comparison) OR a slide presentation/deck (PPTX), or to evolve an existing swarm document or deck. The skill first asks framing questions, then creates declarative agents with an explicit model choice for each and runs evidence-based improvement cycles until every evaluated topic reaches at least A. In document mode it runs author + reviewer + coordinator + rubber-duck agents. In presentation mode it also runs slide authors, a single deck builder (which compiles the .pptx via pptxgenjs and renders slide images), content reviewers and design reviewers that grade the rendered slides. Outputs go under an explicit target, DOCSWARM_ROOT, or the clone's swarms folder. Do not use for short text such as a paragraph or email."
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

Use no **Modo Apresentação (PPTX)** quando o pedido for uma **apresentação/deck de
slides** em vez de um documento corrido, com frases como:

- "crie/monte uma apresentação sobre <tema>"
- "quero um deck/PowerPoint/pptx sobre <tema>"
- "monte um swarm de slides sobre <tema>"
- "preciso de uma apresentação completa sobre <tema>"
- "transforme <este conteúdo/swarm> em uma apresentação"

Nesse caso, siga a seção **"Modo Apresentação (PPTX) 🎞️"** (abaixo): o motor é o
mesmo (agentes declarativos, modelo por agente, régua D-…A+, rubber duck, portão ≥ A),
mas os autores produzem **specs de slide**, um **Deck Builder** compila o `.pptx` e há
uma camada extra de **revisão de design** sobre as imagens renderizadas dos slides.

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
2. **Modelo explícito por agente** — ao enumerar os agentes necessários, escolha
   e registre o **melhor modelo para cada agente** (não um padrão único cego),
   incluindo a justificativa no frontmatter e no plano de agentes.
3. **Human-in-the-loop no início** — sempre faça as perguntas de enquadramento
   ANTES de gerar qualquer agente. Não presuma escopo, público ou profundidade.
4. **Evidência obrigatória** — **todo** agente (autores e revisores) deve
   consultar **no mínimo 5 fontes online funcionais e verificadas** (documentação
   oficial, blogs de referência, artigos acadêmicos, normas). Toda fonte citada
   precisa ter URL **verificado como acessível** (HTTP 200 via `web_fetch`) na
   data de uso. Fonte quebrada ou inventada é falha grave.
5. **Qualidade com régua** — revisores dão nota de **D-** até **A+** por tópico,
   com sugestão de melhoria acionável. O ciclo se repete até **todos** os tópicos
   avaliados ficarem **≥ A** (A ou A+; **A- não passa**).
6. **Rubber duck transversal** — um agente rubber duck revisa o trabalho de
   TODOS os agentes (coordenador, autores, revisores) a cada ciclo, caçando
   falhas de lógica, vieses, lacunas e contradições que escaparam.
7. **Tudo rastreável** — cada ciclo produz relatórios versionados em `/reports`.

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
│  ├─ agent-models.md            # matriz: agente, modelo escolhido e motivo
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

## Seleção de modelo por agente

Ao identificar perfis na Fase 2 ou novos agentes na Fase E, defina também o
modelo ideal de cada agente. Use somente modelos disponíveis na ferramenta `task`
da sessão atual; se um modelo preferido não estiver disponível, escolha o
equivalente mais próximo e registre a substituição.

Para cada agente, registre:

- `model`: modelo escolhido.
- `reasoning_effort`: esforço de raciocínio quando suportado pelo modelo
  escolhido; omita quando não suportado.
- `context_tier`: `default` ou `long_context`, conforme a quantidade de material
  que o agente precisa ler.
- `model_rationale`: uma frase curta explicando por que aquele modelo é o melhor
  para a missão do agente.

Critérios de escolha:

| Necessidade do agente | Preferência de modelo |
|---|---|
| Coordenação, trade-offs difíceis, síntese de muitos achados | modelo forte de raciocínio; use esforço alto quando suportado |
| Autor com pesquisa extensa, documento longo ou muitas fontes | modelo com boa escrita e `long_context` quando suportado |
| Autor técnico/regulatório/arquitetural de alto risco | modelo de maior precisão e raciocínio disponível |
| Revisor de precisão, segurança, governança ou fontes | modelo crítico, preferencialmente de família diferente dos autores principais |
| Revisor de clareza, narrativa, didática ou UX do documento | modelo forte em linguagem e estrutura editorial |
| Rubber duck transversal | modelo mais crítico disponível, idealmente diferente do coordenador, com esforço alto |
| **(Deck)** Autor de slides | modelo forte em síntese e narrativa visual; enxuga e destila conteúdo denso em mensagens de slide |
| **(Deck)** Deck Builder (pptxgenjs) | modelo forte em **código** e disciplina de design; gera/depura o `deck.js` e mantém paleta/motivo/tipografia consistentes; esforço alto |
| **(Deck)** Revisor de design | modelo **multimodal** (precisa "ver" as imagens dos slides com a ferramenta `view`); preferencialmente de família diferente do Deck Builder |

Evite usar o mesmo modelo para todos sem justificativa. A diversidade entre
autores, revisores e rubber duck reduz cegueira coletiva; quando repetir um
modelo, explique que ele é a melhor opção para aquele papel específico.

Grave a decisão em `reports\agent-models.md` antes de despachar agentes:

| Agente | Papel | Modelo | Effort | Contexto | Justificativa |
|---|---|---|---|---|---|
| author-01-... | ... | ... | ... | ... | ... |

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
  Template de Autor (numerando na sequência: `author-07`, `author-08`, ...) e
  escolha o melhor modelo para cada um usando a seção **Seleção de modelo por
  agente**.
- Da mesma forma, avalie se é preciso uma **nova dimensão de revisão** (ex.:
  **Revisor de Diagramas & Visualização** para validar sintaxe Mermaid e fidelidade
  arquitetural). Se sim, gere `reviewer-06`, etc., com o Template de Revisor e
  selecione o modelo ideal dessa dimensão de revisão.
- Registre a decisão (quais agentes novos e por quê) em
  `reports\evo-<MM>-plan.md` e atualize `reports\agent-models.md` com agentes
  novos, modelo, esforço/contexto e justificativa. Se nenhum agente novo for
  necessário, diga isso explicitamente e justifique.
- Se a evolução mudar substancialmente o papel de um agente existente, reavalie
  também o modelo dele e registre a alteração em `reports\agent-models.md`.

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
   Compliance, Redator Técnico). Para CADA perfil, escolha o melhor modelo,
   registre a justificativa em `reports\agent-models.md`, e gere um `.md` em
   `agents\authors\` usando o **Template de Autor**.
2. **Revisores:** identifique de **3 a 5 perfis de revisão** que cubram dimensões
   distintas de qualidade. Sugestão de base (ajuste ao tema):
   - **Precisão técnica** (fatos, exatidão, ausência de erros).
   - **Estrutura & clareza** (organização, fluência, didática).
   - **Completude & escopo** (nada faltando, nada fora do escopo).
   - **Aderência ao público** (nível, tom, utilidade prática).
   - **Fontes & evidências** (≥5 fontes funcionais, citação correta, atualidade).
   Para CADA perfil, escolha o melhor modelo, registre a justificativa em
   `reports\agent-models.md`, e gere um `.md` em `agents\reviewers\` usando o
   **Template de Revisor**.
3. **Coordenador:** escolha o modelo de coordenação, registre em
   `reports\agent-models.md` e gere `agents\coordinator.md` usando o **Template
   de Coordenador**.
4. **Rubber duck:** escolha um modelo crítico/transversal (preferencialmente
   diverso do coordenador), registre em `reports\agent-models.md` e gere
   `agents\rubber-duck.md` usando o **Template de Rubber Duck**.

### Fase 3 — Loop de produção (você atua como Coordenador)

Você, executando a skill, **é o coordenador**. Siga o que `coordinator.md`
declara. Para cada ciclo `N` (começando em 1):

1. **Despachar autores.** Para cada autor, lance um subagente `task`
   (`general-purpose`) usando `model`, `reasoning_effort` e `context_tier` do
   frontmatter do agente, e passando o conteúdo do `.md` do autor como prompt, mais:
   o `brief.md`, a seção/tópicos sob responsabilidade dele e — a partir do ciclo
   2 — as **sugestões de melhoria** dos revisores para os tópicos dele. Cada
   autor escreve/atualiza sua parte direto no `output\` e registra suas fontes.
   Agentes independentes podem rodar em paralelo (background).
2. **Montar o documento.** Consolide as contribuições em `output\<doc>.md` (TOC,
   seções na ordem certa, fontes unificadas em `sources\sources-index.md`).
   Registre `reports\cycle-0N-authors.md`.
3. **Despachar revisores.** Para cada revisor, lance um subagente `task` usando
   os parâmetros de modelo do frontmatter e passando o `.md` do revisor + o
   documento atual. Cada revisor devolve, para **cada tópico de importância** do
   `brief`, uma **nota D- a A+** + justificativa + sugestão acionável. Consolide
   em `reports\cycle-0N-review.md` com uma matriz tópico × revisor e a **nota
   mínima por tópico** (a que vale para o portão).
4. **Despachar rubber duck.** Lance o subagente rubber duck usando os parâmetros
   de modelo do frontmatter para revisar o trabalho de coordenador + autores +
   revisores do ciclo (consistência das notas, fontes realmente verificadas,
   lacunas, vieses). Salve
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
   ≥ A), nº de fontes verificadas, perfis/modelos usados e principais decisões.
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
model: <modelo escolhido>
reasoning_effort: <se suportado pelo modelo; ex.: high>
context_tier: <default|long_context>
model_rationale: "<por que este modelo é o melhor para este autor>"
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
model: <modelo escolhido>
reasoning_effort: <se suportado pelo modelo; ex.: high>
context_tier: <default|long_context>
model_rationale: "<por que este modelo é o melhor para este revisor>"
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
model: <modelo escolhido>
reasoning_effort: <se suportado pelo modelo; ex.: high>
context_tier: <default|long_context>
model_rationale: "<por que este modelo é o melhor para coordenação>"
swarm: <swarm_id>
gate: "A"
max_cycles: <n, padrão 5>
---

# Coordenador do Swarm

## Missão
Orquestrar autores, revisores e o rubber duck para entregar o documento do
`brief.md` com **todos os tópicos de importância ≥ A**, no menor nº de ciclos.

## Loop (por ciclo N)
1. **Autores:** despache cada autor (subagente `general-purpose`) usando o
   `model`, `reasoning_effort` e `context_tier` do frontmatter dele, passando o
   `.md` do autor + `brief.md` + tópicos dele + (ciclo ≥ 2) as sugestões dos
   revisores. Agentes independentes em paralelo. Eles escrevem em `output\`.
2. **Consolidar:** monte `output\<doc>.md` (índice, ordem, fontes unificadas) e
   registre `reports\cycle-0N-authors.md`.
3. **Revisores:** despache cada revisor usando os parâmetros de modelo do
   frontmatter dele com o documento atual. Colete notas D-…A+ por tópico +
   sugestões. Consolide `reports\cycle-0N-review.md` com a matriz tópico ×
   revisor e a **nota mínima por tópico**.
4. **Rubber duck:** despache o rubber duck usando os parâmetros de modelo do
   frontmatter dele sobre o trabalho de todos. Salve
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
model: <modelo escolhido>
reasoning_effort: <se suportado pelo modelo; ex.: high>
context_tier: <default|long_context>
model_rationale: "<por que este modelo é o melhor para auditoria transversal>"
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

---

# Modo Apresentação (PPTX) 🎞️

Variante da skill que entrega uma **apresentação `.pptx`** (via skill `pptx`) em vez de
um documento `.md`. **O motor é o mesmo** — agentes declarativos, modelo explícito por
agente, human-in-the-loop, evidência obrigatória, régua **D- … A+**, rubber duck
transversal, rastreabilidade e **portão ≥ A**. O que muda:

- Os **autores produzem specs de slide** (não prosa corrida).
- Um **Deck Builder** único detém o sistema visual e **compila o `.pptx`** com pptxgenjs.
- Há uma camada extra de **revisão de design** sobre as **imagens renderizadas** dos slides.
- O **rubber duck roda em 3 checkpoints** por ciclo (pós-autores, pós-conteúdo, pós-design).

## Princípios adicionais (além dos 7)

8. **Conteúdo e forma são avaliados separadamente.** Revisores de **conteúdo** julgam a
   mensagem (por tópico); revisores de **design** julgam o visual (por slide, olhando a
   imagem renderizada). Um slide correto e feio não passa; um slide bonito e vazio também não.
9. **Um só dono do sistema visual.** O **Deck Builder** escolhe **uma** paleta, **um**
   motivo e **uma** dupla de fontes e os aplica em todos os slides (coesão > variedade).
   Segue o guia de design da skill `pptx` (`~/.copilot/skills/pptx/SKILL.md`).
10. **Design se avalia na imagem, não no código.** O julgamento de design é feito sobre os
    `.jpg` renderizados (render nativo da `pptx`: `soffice → PDF → pdftoppm`), com
    subagentes de **olhos frescos** usando a ferramenta `view` — nunca "confiando no code".
11. **Speaker notes obrigatórias** por slide no `.pptx` final.

## Pré-requisitos de ferramenta (toolchain)

Antes do primeiro build, resolva o caminho da skill `pptx` e confirme as dependências;
instale o que faltar. Descubra `<PPTX>` pelo alvo real do symlink da skill (análogo ao
`<OUTPUT_ROOT>`):

- Windows: `$PPTX = (Get-Item -Force "$env:USERPROFILE\.copilot\skills\pptx").Target`
- Linux/macOS: `PPTX=$(readlink -f "$HOME/.copilot/skills/pptx")`

Dependências (ver `pptx/SKILL.md` → *Dependencies*):

- `pptxgenjs` (npm, criação do `.pptx`) · **LibreOffice** (`soffice`) · **Poppler**
  (`pdftoppm`) · `Pillow` (grade de miniaturas) · `markitdown[pptx]` (QA de texto).

Checagem rápida (Windows): `Get-Command node,soffice,pdftoppm`. Instalação, se faltar:

- **pptxgenjs:** `npm i -g pptxgenjs` (ou local na pasta `output\build`).
- **Pillow / markitdown:** `pip install Pillow "markitdown[pptx]"`.
- **Windows:** `winget install TheDocumentFoundation.LibreOffice` e
  `winget install oschwartz10612.Poppler` (garanta o `pdftoppm` no `PATH`).
- **Debian/Ubuntu:** `apt-get install libreoffice poppler-utils`.
- **macOS:** `brew install --cask libreoffice && brew install poppler`.

**Leia `<PPTX>\pptxgenjs.md` antes de gerar o `deck.js`.** Se alguma ferramenta de render
faltar e não puder ser instalada, **avise o usuário** — sem render não há revisão de
design (não improvise com Playwright: ele é para páginas web, não para `.pptx`).

## Estrutura de saída (deck)

Use o id `<YYYY-MM-DD>-DECK-<XX>` (o `-DECK-` distingue de swarms de documento; `XX`
sequencial por dia). `<OUTPUT_ROOT>` é resolvido igual ao modo documento.

```text
<OUTPUT_ROOT>\<YYYY-MM-DD>-DECK-<XX>\
├─ brief.md                          # tema + respostas + tópicos + identidade visual + público/ocasião
├─ agents\
│  ├─ coordinator.md
│  ├─ rubber-duck.md
│  ├─ deck-builder.md                # dono do sistema visual + build + render
│  ├─ slide-authors\
│  │  ├─ author-01-<slug>.md          # um por bloco de slides/seção
│  │  └─ author-02-<slug>.md
│  ├─ content-reviewers\
│  │  ├─ reviewer-01-<slug>.md        # dimensões de conteúdo (nota por tópico)
│  │  └─ reviewer-02-<slug>.md
│  └─ design-reviewers\
│     ├─ design-01-<slug>.md          # dimensões de design (nota por slide) — multimodal
│     └─ design-02-<slug>.md
├─ reports\
│  ├─ agent-models.md
│  ├─ cycle-01-authors.md             # specs entregues por autor
│  ├─ cycle-01-build.md               # log do build (pptxgenjs), erros, nº de slides
│  ├─ cycle-01-content-review.md      # matriz tópico × revisor + nota mínima
│  ├─ cycle-01-design-review.md       # matriz slide × revisor de design + nota + dimensões do deck
│  ├─ cycle-01-rubberduck.md          # 3 blocos: pós-autores / pós-conteúdo / pós-design
│  └─ final-report.md
├─ sources\
│  └─ sources-index.md
└─ output\
   ├─ slides\                         # spec declarativo por autor
   │  ├─ 01-<slug>.md
   │  └─ 02-<slug>.md
   ├─ build\
   │  └─ deck.js                      # script pptxgenjs gerado pelo Deck Builder
   ├─ renders\
   │  └─ cycle-01\slide-01.jpg …      # imagens renderizadas do ciclo (+ thumbnails.jpg)
   └─ deck.pptx                       # a entrega
```

## Fases (deck)

### Fase 0 (deck) — Perguntas de enquadramento
Faça as perguntas gerais da Fase 0 e **acrescente** as específicas de apresentação:

- **Ocasião/formato**: pitch, executivo, técnico, treinamento, comercial…
- **Nº de slides alvo** e **tempo de apresentação**.
- **Identidade visual**: paleta/cores da marca, logo, fontes, template `.pptx` a respeitar
  (se houver), tom visual (sóbrio, ousado, minimalista…).
- **Formatos de dado**: precisa de gráficos, tabelas, diagramas, imagens?
- **Restrições de marca/template** e o que **não** pode aparecer.

Registre tudo no `brief.md`, incluindo os **tópicos de importância** (cobertura de
conteúdo) e a **identidade visual** definida.

### Fase 1 (deck) — Setup
`deck_id = <YYYY-MM-DD>-DECK-<XX>`; crie a árvore acima; escreva `brief.md`.

### Fase 2 (deck) — Gerar agentes declarativos
Escolha o melhor modelo de cada agente (seção **Seleção de modelo por agente**, incluindo
as linhas **(Deck)**) e gere:

1. **3–6 Autores de slides** (`agents\slide-authors\`) — Template de Autor de Slides.
2. **3–5 Revisores de conteúdo** (`agents\content-reviewers\`) — Template de Revisor de
   Conteúdo (slides). Dimensões: precisão técnica, clareza da mensagem/**arco narrativo**,
   completude vs. escopo, aderência ao público/ocasião, fontes & evidências.
3. **2–3 Revisores de design** (`agents\design-reviewers\`, **modelo multimodal**) —
   Template de Revisor de Design.
4. **Deck Builder** (`agents\deck-builder.md`) — Template de Deck Builder.
5. **Coordenador** e **Rubber Duck** (mesmos templates do modo documento).

Registre a matriz completa em `reports\agent-models.md`.

### Fase 3 (deck) — Loop de produção (você é o Coordenador)
Por ciclo `N`:

1. **Autores.** Despache cada autor (subagente `task`, parâmetros do frontmatter) com o
   `.md` dele + `brief.md` + seus slides + (ciclo ≥ 2) as sugestões dos revisores. Cada um
   escreve/atualiza seu spec em `output\slides\` e registra fontes (≥5 verificadas).
2. **Rubber duck (pós-autores).** Audita os specs: afirmação sem fonte, contradição entre
   slides, fuga de escopo, arco narrativo, densidade/nº de slides. → bloco em
   `reports\cycle-0N-rubberduck.md`.
3. **Deck Builder — build.** Consolida os specs, aplica o sistema visual e gera
   `output\build\deck.js` (pptxgenjs); compila `output\deck.pptx`; roda o QA de texto
   (`markitdown`). Registra `reports\cycle-0N-build.md`.
4. **Deck Builder — render.** Converte para imagens em `output\renders\cycle-0N\`
   (`soffice → PDF → pdftoppm`; opcional grade `thumbnail.py`).
5. **Revisores de conteúdo.** Cada um avalia os specs + o texto renderizado e dá **nota
   D-…A+ por tópico de importância** + sugestão acionável. Consolide
   `reports\cycle-0N-content-review.md` (matriz tópico × revisor + nota mínima por tópico).
6. **Rubber duck (pós-conteúdo).** Audita a **calibração** dos revisores de conteúdo (nota
   vs. evidência; dimensão faltando). → segundo bloco no rubberduck do ciclo.
7. **Revisores de design.** Cada um **abre as imagens** de `output\renders\cycle-0N\` com a
   ferramenta `view` e dá **nota D-…A+ por slide** + nota nas **dimensões do deck** (coesão
   de paleta, motivo, tipografia, ritmo/consistência) + correção acionável. Consolide
   `reports\cycle-0N-design-review.md` (matriz slide × revisor + nota mínima por slide).
8. **Rubber duck (pós-design).** **Reabre os `.jpg`** e audita os achados de design
   (overflow/colisão ignorados? nota inflada? conteúdo e design coerentes entre si?). →
   terceiro bloco no rubberduck do ciclo.
9. **Portão.** Aprova se **todos os tópicos ≥ A** (conteúdo) **E** **todo slide ≥ A**
   (design) **E** **todas as dimensões do deck ≥ A** **E** sem achado **Crítico** do rubber
   duck. Senão, `N+1` e volte ao passo 1 alimentando **só** os tópicos/slides < A e os
   achados do rubber duck aos autores e/ou ao Deck Builder.
10. **Trava.** Ao bater `max_cycles` sem aprovar tudo, pare, escreva o estado no
    `final-report.md` e **escale ao usuário** — nunca entregue < A em silêncio.

### Fase 4 (deck) — Entrega
1. Finalize `output\deck.pptx` (com **speaker notes**) e a render final do último ciclo.
2. Escreva `reports\final-report.md`: nº de ciclos, matriz final de conteúdo (tópicos ≥ A)
   e de design (slides ≥ A), nº de fontes verificadas, perfis/modelos usados.
3. Responda ao usuário com os caminhos + resumo curto (não descreva slide a slide).

### Modo evolução (deck)
Para **evoluir um deck já entregue** (adicionar slides, atualizar dados, re-estilizar),
siga a lógica da **Fase E** do modo documento: localize o `<deck_id>`, diagnostique
(`brief.md`, specs, `final-report.md`), **avalie se faltam agentes** (ex.: um autor novo
para uma seção nova; um revisor de design para uma dimensão nova), **reative o enxame
inteiro** (autores existentes + novos + Deck Builder) para manter paleta/motivo/tom
uniformes, recompile e re-renderize, e aplique o portão **≥ A para todos os tópicos e
slides, novos e antigos** (a evolução não pode rebaixar nada aprovado).

## Comandos de referência (build & render)

Rode a partir da pasta do deck; `<PPTX>` é o caminho resolvido da skill `pptx`.

```powershell
# 1) Build: o deck.js (pptxgenjs) deve gravar em output\deck.pptx
node output\build\deck.js

# 2) QA de texto (placeholders, ordem, typos)
python -m markitdown output\deck.pptx

# 3) Render para imagens (design QA) — soffice + pdftoppm
python "$PPTX\scripts\office\soffice.py" --headless --convert-to pdf --outdir output\renders\cycle-0N output\deck.pptx
pdftoppm -jpeg -r 150 output\renders\cycle-0N\deck.pdf output\renders\cycle-0N\slide

# 4) (Opcional) Grade de miniaturas para visão geral
python "$PPTX\scripts\thumbnail.py" output\deck.pptx output\renders\cycle-0N\thumbnails
```

---

## Template de Autor de Slides (`agents\slide-authors\author-XX-<slug>.md`)

```markdown
---
name: author-<XX>-<slug>
kind: slide-author
role: <Perfil, ex.: Estrategista de Produto>
model: <modelo escolhido>
reasoning_effort: <se suportado; ex.: high>
context_tier: <default|long_context>
model_rationale: "<por que este modelo é o melhor para este autor de slides>"
swarm: <deck_id>
sources_min: 5
---

# Autor de Slides: <Perfil>

## Persona
Você é um(a) **<perfil>** sênior que pensa em **mensagem por slide**, não em parágrafos.

## Missão
Produzir o **spec** dos slides sob sua responsabilidade — conteúdo destilado, correto e
apresentável — para o deck descrito em `brief.md`, no tom e para a ocasião definidos.

## Slides sob sua responsabilidade
- <bloco/seção A> (slides NN–NN)

## Como trabalhar
1. Leia `brief.md` (tópicos, público, ocasião, identidade visual) e os specs já existentes.
2. Pesquise: **≥ 5 fontes online funcionais** (oficiais/normas/acadêmicas/blogs de
   referência), **verifique cada URL** (HTTP 200) antes de citar.
3. **Uma ideia por slide.** Título curto; mensagem-chave em uma frase; conteúdo enxuto
   (evite parágrafos e listas gigantes). Descreva a **intenção visual** (o que mostrar),
   não o código. Sempre inclua **speaker notes**.
4. Trate cada **sugestão de revisor** (ciclos ≥ 2) explicitamente.
5. Salve seu spec em `output\slides\<NN>-<slug>.md` e atualize `sources\sources-index.md`.

## Formato do spec (um bloco por slide)
### Slide NN — <título curto>
- **Objetivo:** <o papel do slide na narrativa>
- **Mensagem-chave:** <uma frase>
- **Conteúdo:** <bullets curtos ou texto essencial>
- **Visual:** <imagem/ícone/gráfico/tabela/diagrama + layout sugerido>
- **Dados:** <números/série + fonte, se houver>
- **Speaker notes:** <2–4 frases para quem apresenta>
- **Fontes:** <[#] do sources-index>

## Formato das fontes (obrigatório, ≥ 5)
| # | Título | Tipo | URL | Verificado |
|---|--------|------|-----|------------|
| 1 | ...    | oficial/norma/acadêmico/blog | https://... | HTTP 200 em <data> |

## Padrão de qualidade
- Precisão factual acima de tudo; toda afirmação sustentada por fonte.
- Enxuto e específico; nada de "encher slide". Sem contradição com outros autores.
- Coerente com a terminologia e o público do `brief`.
```

## Template de Deck Builder (`agents\deck-builder.md`)

```markdown
---
name: deck-builder
kind: deck-builder
model: <modelo forte em código/pptxgenjs>
reasoning_effort: <se suportado; ex.: high>
context_tier: <default|long_context>
model_rationale: "<por que este modelo é o melhor para compilar e dar consistência visual>"
swarm: <deck_id>
---

# Deck Builder (Sistema Visual + Compilação)

## Missão
Transformar os specs de `output\slides\` em um `output\deck.pptx` **coeso e apresentável**,
detendo o sistema visual do deck inteiro, e **renderizar** as imagens para a revisão de design.

## Sistema visual (decida uma vez, aplique a tudo)
- Leia o guia de design em `~/.copilot/skills/pptx/SKILL.md` e `~/.copilot/skills/pptx/pptxgenjs.md`.
- Escolha **uma paleta** (uma cor domina 60–70%), **um motivo** repetido e **uma dupla de
  fontes**, respeitando a identidade visual do `brief`.
- Todo slide tem **elemento visual**; contraste forte; margens ≥ 0,5"; **sem linha de
  destaque sob o título** (marca de slide "cara de IA"); nada de slide só-texto.
- Slides de título/encerramento em fundo escuro; conteúdo em fundo claro (ou dark coeso).

## Como trabalhar (por ciclo)
1. Consolide todos os specs e a identidade visual do `brief`.
2. Gere `output\build\deck.js` com **pptxgenjs**, incluindo **speaker notes** por slide, e
   compile `output\deck.pptx` (`node output\build\deck.js`).
3. **QA de texto:** `python -m markitdown output\deck.pptx` — corrija placeholders/typos/ordem.
4. **Render:** converta para `output\renders\cycle-0N\slide-*.jpg` (soffice → PDF → pdftoppm).
5. Aplique as **correções de design** dos revisores (ciclos ≥ 2) e recompile/re-renderize.
6. Registre `reports\cycle-0N-build.md`: nº de slides, paleta/motivo/fontes, erros e correções.

## Regras
- Consistência acima de tudo: mesma paleta/motivo/tipografia em todos os slides.
- Não invente conteúdo nem fontes; se um spec estiver incompleto, **sinalize ao coordenador**.
- Mantenha o `deck.js` legível e determinístico (recompilável a qualquer ciclo).
```

## Template de Revisor de Conteúdo (slides) (`agents\content-reviewers\reviewer-XX-<slug>.md`)

```markdown
---
name: reviewer-<XX>-<slug>
kind: content-reviewer
role: <Dimensão, ex.: Precisão técnica>
model: <modelo escolhido>
reasoning_effort: <se suportado; ex.: high>
context_tier: <default|long_context>
model_rationale: "<por que este modelo é o melhor para este revisor de conteúdo>"
swarm: <deck_id>
sources_min: 5
scale: "D- D D+ C- C C+ B- B B+ A- A A+"
gate: "A"
---

# Revisor de Conteúdo: <Dimensão>

## Missão
Avaliar o **conteúdo** do deck (specs em `output\slides\` + texto renderizado) sob a ótica
de **<dimensão>**, dando **nota D-…A+ por tópico de importância** do `brief.md`.

## Como avaliar
1. Leia `brief.md` (tópicos, público, ocasião, critérios de sucesso) e os specs.
2. Consulte **≥ 5 fontes funcionais** (URLs HTTP 200) e confira se as fontes dos autores
   existem, funcionam e **sustentam** as afirmações dos slides.
3. Julgue também o **arco narrativo** (a sequência conta uma história?) e a densidade
   (mensagem por slide, sem parede de texto). **A- não aprova.**

## Saída (obrigatória)
| Tópico | Nota | Justificativa | Sugestão acionável |
|--------|------|---------------|--------------------|
| <t>    | B+   | ...           | "Reforce ... / corrija ... / cite ..." |

Encerre com a **nota mínima**, os tópicos que **bloqueiam** o portão (< A) e suas próprias
fontes (tabela ≥ 5, verificadas).
```

## Template de Revisor de Design (`agents\design-reviewers\design-XX-<slug>.md`)

```markdown
---
name: design-<XX>-<slug>
kind: design-reviewer
role: <Dimensão de design, ex.: Hierarquia & Layout>
model: <modelo multimodal — precisa "ver" as imagens>
reasoning_effort: <se suportado; ex.: high>
context_tier: <default|long_context>
model_rationale: "<por que este modelo multimodal é o melhor para avaliar os slides>"
swarm: <deck_id>
scale: "D- D D+ C- C C+ B- B B+ A- A A+"
gate: "A"
---

# Revisor de Design: <Dimensão>

## Persona
Olhos frescos, exigente. **Assuma que há problemas** — seu trabalho é achá-los. Se não
achou nada na primeira passada, não olhou com atenção suficiente.

## Missão
Avaliar o **visual** do deck a partir das **imagens renderizadas**
(`output\renders\cycle-0N\slide-*.jpg`), dando **nota D-…A+ por slide** e às **dimensões do
deck**. Você **não** avalia código nem exige fontes.

## Como avaliar
1. **Abra cada `slide-*.jpg` com a ferramenta `view`** (não julgue pelo `deck.js`).
2. Procure, por slide (checklist herdado da skill `pptx`):
   - Elementos sobrepostos (texto sobre forma, linha cortando palavra, blocos empilhados).
   - Texto estourando ou cortado nas bordas/caixas; título que quebrou em 2 linhas e
     desalinhou um enfeite.
   - Rodapé/fonte colidindo com o conteúdo; elementos colados (< 0,3") ou margem < 0,5".
   - Gaps irregulares; colunas desalinhadas; caixas estreitas causando quebra excessiva.
   - **Baixo contraste** (texto/ícone claro sobre fundo claro; escuro sobre escuro).
   - Placeholder esquecido; **linha de destaque sob título** (antipadrão "cara de IA");
     slide **só-texto** sem elemento visual.
3. **Dimensões do deck** (visão do conjunto): coesão de paleta, consistência do motivo,
   tipografia, ritmo/variedade de layout. **A- não aprova.**

## Saída (obrigatória)
| Slide | Nota | Problemas encontrados | Correção acionável |
|-------|------|-----------------------|--------------------|
| 03    | B    | "Título 2 linhas colide com ícone; contraste fraco no rodapé" | "Reduza o título / mova o ícone 0,4"; escureça o rodapé" |

Encerre com: nota das **dimensões do deck**, a **nota mínima por slide**, os slides que
**bloqueiam** o portão (< A) e um veredito geral do visual.
```

## Checklist antes de entregar (modo documento)

- [ ] Pasta `<OUTPUT_ROOT>\<YYYY-MM-DD>-SWARM-<XX>\` criada com a árvore
      completa (`agents/authors`, `agents/reviewers`, `reports`, `sources`,
      `output`).
- [ ] `brief.md` com perguntas respondidas e tópicos de importância listados.
- [ ] 3–6 agentes de autor + 3–5 agentes de revisor + coordenador + rubber duck,
      todos como `.md` declarativos, cada um com modelo escolhido e justificativa.
- [ ] `reports\agent-models.md` registra agente, papel, modelo, esforço/contexto
      e justificativa de escolha.
- [ ] Ciclos registrados em `reports\` (authors, review, rubberduck por ciclo).
- [ ] **Todos** os tópicos de importância com nota final **≥ A** (ou escalonado
      ao usuário se bater `max_cycles`).
- [ ] Cada agente cumpriu **≥ 5 fontes online verificadas (HTTP 200)**;
      consolidadas em `sources\sources-index.md`.
- [ ] Documento final em `output\` com índice e bibliografia.
- [ ] `reports\final-report.md` escrito.

## Resposta ao usuário (modo documento)

Depois de concluir, responda com:

```markdown
Swarm concluído: `<OUTPUT_ROOT>\<swarm_id>\`

Documento: `output\<doc>.md`
Modelos dos agentes: `reports\agent-models.md`
Ciclos: <N>   |   Tópicos avaliados: <k> (todos ≥ A)
Autores: <lista de perfis>   |   Revisores: <lista de dimensões>
Fontes verificadas: <total>
```

## Checklist antes de entregar (modo apresentação)

- [ ] Pasta `<OUTPUT_ROOT>\<YYYY-MM-DD>-DECK-<XX>\` criada com a árvore completa
      (`agents/slide-authors`, `agents/content-reviewers`, `agents/design-reviewers`,
      `agents/deck-builder.md`, `reports`, `sources`, `output/slides`, `output/build`,
      `output/renders`).
- [ ] `brief.md` com perguntas respondidas, tópicos de importância e identidade visual.
- [ ] 3–6 autores de slides + 3–5 revisores de conteúdo + 2–3 revisores de design
      (multimodais) + deck builder + coordenador + rubber duck, todos `.md`, cada um com
      modelo escolhido e justificativa.
- [ ] `reports\agent-models.md` completo.
- [ ] Cada ciclo com `authors`, `build`, `content-review`, `design-review` e `rubberduck`
      (3 checkpoints) registrados em `reports\`.
- [ ] `output\deck.pptx` compilado, **com speaker notes**, e renders do ciclo final em
      `output\renders\`.
- [ ] **Todos** os tópicos ≥ A (conteúdo) **e** **todo slide** ≥ A **e** dimensões do deck
      ≥ A (ou escalonado ao usuário se bater `max_cycles`).
- [ ] Autores e revisores de conteúdo cumpriram **≥ 5 fontes verificadas (HTTP 200)** em
      `sources\sources-index.md` (revisores de design são dispensados dessa regra).
- [ ] `reports\final-report.md` escrito (matriz de conteúdo + matriz de design).

## Resposta ao usuário (modo apresentação)

```markdown
Deck concluído: `<OUTPUT_ROOT>\<deck_id>\`

Apresentação: `output\deck.pptx`   (renders: `output\renders\cycle-<N>\`)
Modelos dos agentes: `reports\agent-models.md`
Ciclos: <N>   |   Tópicos ≥ A: <k>   |   Slides ≥ A: <m>/<total>
Autores: <perfis>   |   Conteúdo: <dimensões>   |   Design: <dimensões>
Fontes verificadas: <total>
```
