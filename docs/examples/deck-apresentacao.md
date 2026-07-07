# Exemplo — Apresentação PowerPoint (Modo Apresentação / PPTX) 🎞️

Exemplo **ilustrativo** ponta a ponta do **Modo Apresentação** da skill
`document-swarm`: um pedido de **apresentação** dispara o enxame de slides, que produz um
`.pptx` revisado em **conteúdo** e **design** até que **todos os tópicos e todos os slides
fiquem ≥ A**.

> ⚠️ **Ilustrativo.** Os números (notas, nº de fontes, ciclos) servem para mostrar o
> fluxo — não são a transcrição de uma execução específica.

> Deck de referência: `2026-07-01-DECK-01`. A saída fica em
> `<clone>\swarms\<YYYY-MM-DD>-DECK-<XX>\` e **não vai para o Git** (está no `.gitignore`).

---

## Pedido — Criar a apresentação

**Prompt do usuário:**

> _"crie uma apresentação executiva sobre arquitetura Zero Trust no Azure para um comitê
> de segurança."_

### O que a skill fez

1. **Fase 0 — Perguntas de enquadramento (deck)** (via `ask_user`), incluindo as
   específicas de apresentação. Respostas no `brief.md`:
   - **Objetivo/ocasião:** apresentação executiva de decisão (comitê de segurança).
   - **Público:** liderança técnica sênior (CISO + arquitetos).
   - **Nº de slides / tempo:** ~14 slides, 20 min.
   - **Identidade visual:** paleta sóbria "Midnight Executive" (navy + ice blue),
     tipografia Georgia/Calibri, logo no rodapé.
   - **Dados:** diagrama de pilares Zero Trust + 1 gráfico de custos.
   - **Idioma:** PT-BR; **máx. de ciclos:** 5; **speaker notes:** sim.
2. **Fases 1–2 — Setup + geração dos agentes declarativos.** Derivados **9 tópicos de
   importância** (T1 Visão geral · T2 Identidade · T3 Rede · T4 Dispositivos · T5 Dados ·
   T6 Monitoramento · T7 Custos · T8 Trade-offs · T9 Próximos passos) e gerado o enxame:
   - **4 autores de slides:** Arquiteto de Identidade · Engenheiro de Rede · Especialista
     em Compliance · Estrategista Executivo.
   - **1 Deck Builder:** dono do sistema visual + compilação `pptxgenjs`.
   - **4 revisores de conteúdo:** precisão técnica · clareza & arco narrativo ·
     completude & escopo · aderência ao público.
   - **2 revisores de design (multimodais):** hierarquia & layout · coesão visual &
     contraste.
   - **2 transversais:** coordenador + rubber duck.

   Cada agente recebeu o **modelo** mais adequado (registrado em
   `reports\agent-models.md`): autores em modelo forte de síntese, Deck Builder em modelo
   forte de **código** (pptxgenjs), revisores de design em modelo **multimodal** de família
   diferente do Deck Builder.

3. **Fase 3 — Loop de produção.** Cada ciclo seguiu:
   autores → **rubber duck** → build (`deck.js` → `deck.pptx`) → **render** →
   revisores de conteúdo → **rubber duck** → revisores de design → **rubber duck** → portão.

   **Conteúdo — nota mínima por tópico (por ciclo):**

   | Tópico | C1 | C2 | C3 | | Tópico | C1 | C2 | C3 |
   |--------|:--:|:--:|:--:|---|--------|:--:|:--:|:--:|
   | T1 | A- | A  | **A** | | T6 | C+ | A- | **A** |
   | T2 | B  | A  | **A** | | T7 | C  | B+ | **A** |
   | T3 | B- | A- | **A** | | T8 | B  | A  | **A** |
   | T4 | B+ | A  | **A** | | T9 | A- | A  | **A** |
   | T5 | C+ | A- | **A** | |    |    |    |       |

   **Design — nota mínima por slide (por ciclo), amostra:**

   | Slide | C1 | C2 | C3 | Problema pego na imagem (render) |
   |------|:--:|:--:|:--:|---|
   | 01 (título) | A- | A | **A** | contraste do subtítulo sobre o navy |
   | 05 (rede)   | C+ | B+ | **A** | diagrama estourando a margem direita |
   | 07 (custos) | C  | A- | **A** | rótulos do gráfico ilegíveis a 100% |
   | 12 (trade-offs) | B | A | **A** | 2 colunas desalinhadas |

   **Dimensões do deck:** coesão de paleta, motivo e tipografia chegaram a **A** no C3.

4. **Rubber duck (3 checkpoints/ciclo).**
   - *Pós-autores* (C1): pegou uma afirmação de custo **sem fonte** → devolvida ao autor.
   - *Pós-conteúdo* (C2): flagrou um **A inflado** em T7 (custos) sem número que o
     sustentasse → rebaixado e corrigido.
   - *Pós-design* (C2): reabriu os `.jpg` e confirmou **overflow real** no slide 05 que um
     revisor havia deixado passar → correção obrigatória.

5. **Fase 4 — Entrega.** No **C3**: todos os 9 tópicos ≥ A, todos os 14 slides ≥ A,
   dimensões do deck ≥ A, rubber duck com **0 críticos**. Deck entregue com **speaker
   notes** e **31 fontes verificadas (HTTP 200)**.

**Resultado:** apresentação concluída em **3 ciclos**.

---

## Como a revisão de design funcionou (screenshot nativo da `pptx`)

Nada de Playwright. A cada ciclo o **Deck Builder** compilou e renderizou:

```powershell
# build (o deck.js grava em output\deck.pptx)
node output\build\deck.js

# render para imagens (design QA)
python "$PPTX\scripts\office\soffice.py" --headless --convert-to pdf --outdir output\renders\cycle-03 output\deck.pptx
pdftoppm -jpeg -r 150 output\renders\cycle-03\deck.pdf output\renders\cycle-03\slide
```

Os **revisores de design** (modelos multimodais) abriram cada `output\renders\cycle-03\
slide-*.jpg` com a ferramenta `view` e deram nota **por slide**, com o checklist herdado
da skill `pptx` (sobreposição, overflow, contraste, margens, alinhamento, antipadrões
"cara de IA").

---

## Estrutura final do deck

```text
2026-07-01-DECK-01\
├─ brief.md
├─ agents\
│  ├─ coordinator.md · rubber-duck.md · deck-builder.md
│  ├─ slide-authors\author-01…04-*.md
│  ├─ content-reviewers\reviewer-01…04-*.md
│  └─ design-reviewers\design-01…02-*.md
├─ reports\
│  ├─ agent-models.md
│  ├─ cycle-01..03-{authors,build,content-review,design-review,rubberduck}.md
│  └─ final-report.md
├─ sources\sources-index.md
└─ output\
   ├─ slides\01…04-*.md
   ├─ build\deck.js
   ├─ renders\cycle-01..03\slide-*.jpg
   └─ deck.pptx        ← a entrega (com speaker notes)
```

---

## Lições que o exemplo ilustra

- **Conteúdo e design são portões separados:** um slide pode ter texto correto (conteúdo
  A) e ainda reprovar por overflow/contraste (design < A) — e vice-versa.
- **Design se julga na imagem renderizada**, não no `deck.js`. Olhos frescos +
  multimodal + o render nativo da `pptx` pegam o que o código esconde.
- **O rubber duck nos 3 checkpoints** protege contra nota inflada e achado ignorado.
- **Um só Deck Builder** garante paleta/motivo/tipografia uniformes no deck inteiro.
