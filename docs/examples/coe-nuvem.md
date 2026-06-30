# Estudo de caso — Playbook de CoE de Nuvem (criação + evolução)

Exemplo **real e ponta a ponta** de uso da skill `document-swarm`, em **dois
pedidos** ao Copilot CLI:

1. **Criação do zero** de um playbook de _Cloud Center of Excellence (CoE)_
   agnóstico de provedor.
2. **Evolução** do mesmo documento, adicionando **assessment de maturidade** e
   **diagramas de arquitetura** — sem recriar nada, usando o **Modo Evolução
   (Fase E)** da skill.

> Swarm de referência: `2026-06-29-SWARM-01`. A pasta de saída de um swarm fica
> em `<clone>\swarms\<YYYY-MM-DD>-SWARM-<XX>\` e **não vai para o Git** (está no
> `.gitignore`). Este documento resume o que aconteceu lá dentro.

---

## Pedido 1 — Criar o playbook (do zero)

**Prompt do usuário:**

> _"implemente um playbook sobre CoE de Nuvem (agnóstico de nuvem)."_

### O que a skill fez

1. **Fase 0 — Perguntas de enquadramento** (via `ask_user`): objetivo, público,
   extensão, frameworks de base, idioma, escopo e máximo de ciclos.
   Respostas registradas no `brief.md`:
   - **Objetivo:** referência abrangente para montar, operar e evoluir um CoE.
   - **Público:** misto — liderança executiva + times técnicos.
   - **Extensão:** abrangente (50+ páginas equivalentes).
   - **Frameworks de base:** CAF, FinOps Framework, Well-Architected, NIST, SRE.
   - **Idioma:** PT-BR com termos técnicos em EN.
   - **Máximo de ciclos:** 8.
2. **Fases 1–2 — Setup + geração dos agentes declarativos.** Foram derivados
   **12 tópicos de importância** (T1…T12) e gerado o enxame:
   - **6 autores:** Estrategista de Nuvem · Governança & Plataforma ·
     Segurança & Compliance · FinOps · SRE & Operações · Enablement & Redator.
   - **5 revisores:** Precisão técnica · Estrutura & clareza ·
     Completude & agnosticismo · Aderência ao público · Fontes & evidências.
   - **2 transversais:** coordenador + rubber duck.
3. **Fase 3 — Loop de produção (autor → revisão → rubber duck).** Partiu de um
   rascunho majoritariamente **abaixo do portão** e subiu até **todos os 12
   tópicos ≥ A**:

   | Tópico | C1 | C2 | C3 | | Tópico | C1 | C2 | C3 |
   |--------|:--:|:--:|:--:|---|--------|:--:|:--:|:--:|
   | T1  | A  | A- | **A** | | T7  | C- | A  | **A** |
   | T2  | A- | A  | **A** | | T8  | C- | A  | **A** |
   | T3  | D+ | A  | **A** | | T9  | D+ | A- | **A** |
   | T4  | D+ | A  | **A** | | T10 | D+ | A  | **A** |
   | T5  | D+ | A  | **A** | | T11 | A- | A- | **A** |
   | T6  | D+ | A- | **A** | | T12 | D+ | A- | **A** |

4. **Fase 4 — Entrega.** Rubber duck: **0 críticos**. Documento entregue com
   **68 fontes verificadas (HTTP 200)** e rastreabilidade inline `[Fx]`.

**Resultado:** playbook de CoE concluído em **3 ciclos**, 12 tópicos em A.

---

## Pedido 2 — Evoluir (assessment + diagramas)

**Prompt do usuário:**

> _"Evolua o playbook de coe de nuvem (2026-06-29-SWARM-01): Adicione diagramas
> de arquitetura (Mermaid), defina níveis de maturidade e modelo/template de
> assessment. Artefatos adicionais podem e devem ser gerados._
> _Quem deve executar é o skill documents-swarm. (...) O primeiro passo dele é
> avaliar se precisa de mais agentes (autores e revisores). E o coordenador
> ativar todos, os atuais e existentes, para garantir a uniformidade das
> atualizações por toda a entrega."_

> 💡 Este pedido é o gatilho do **Modo Evolução (Fase E)**: cita um `swarm_id`
> existente e pede para **estender** a entrega, não criar outra do zero.

### O que a skill fez (Fase E)

1. **E.0 — Localizar e diagnosticar** o swarm `2026-06-29-SWARM-01`: leu
   `brief.md`, o documento em `output/`, o `final-report.md` e listou os agentes.
2. **E.1 — Avaliar se faltam agentes** (o "primeiro passo" pedido). A evolução
   exigia especialidades novas, então **3 agentes foram adicionados** (registro
   em `reports/evo-01-plan.md`):
   - **author-07 — Arquiteto & Diagramador** → novo **T13 Diagramas (galeria)**.
   - **author-08 — Maturidade & Assessment** → novo **T14 Maturidade & Assessment**.
   - **reviewer-06 — Diagramas & Visualização** → valida sintaxe Mermaid e
     fidelidade arquitetural.
3. **E.2 — Atualizar o `brief.md`** com a seção `EVO-01` (pedido, novos tópicos
   T13/T14, novos agentes, artefatos a gerar).
4. **E.3 — Coordenador reativa o enxame INTEIRO** (a regra de ouro da evolução):
   os **8 autores** (6 existentes + 2 novos) foram reativados. Cada autor
   embutiu o diagrama Mermaid pertinente à sua seção e alinhou a terminologia às
   **9 dimensões canônicas** de maturidade — evitando uma "ilha" desconectada e
   garantindo tom/vocabulário/referências uniformes em toda a entrega.
5. **E.4 — Consolidar e revisar** com o portão **≥ A para todos os tópicos,
   novos e antigos** (a evolução não pode rebaixar nada já aprovado):

   | | T1…T12 | T13 | T14 |
   |---|:--:|:--:|:--:|
   | **Mínimo** | A | **A** | **A** |

   Revisão agora com **6 dimensões** (as 5 originais + R06 Diagramas).
6. **E.5 — Entrega.** `final-report.md` ganhou a seção de evolução.

### Artefatos adicionais gerados

- **16 diagramas Mermaid** (`.mmd`) + **32 blocos embutidos** no documento
  (visão de contexto do CoE, mapa de papéis, topologia de landing zones,
  fluxo de incidente/SRE, RACI de waiver, jornada de adoção 90 dias,
  KPI→dimensão→decisão, roadmap, ciclo de vida de artefato, zero-trust, etc.).
- **Modelo de maturidade N1→N5** × **9 dimensões ponderadas** (rubrica por nível,
  fórmula de score, anti-gaming).
- **Template de assessment preenchível** (`coe-maturity-assessment.md`) +
  **scorecard CSV** (`coe-maturity-assessment-scorecard.csv`).

### Validação técnica da evolução

- 32 blocos embutidos + 16 `.mmd` isolados **renderizados** com
  `@mermaid-js/mermaid-cli` → **0 falhas**.
- Fontes ampliadas de **68 → 72 URLs** distintas verificadas (HTTP 200).
- Rubber duck (EVO-01): nenhum tópico antigo rebaixado; portão comprovado.

---

## Lições deste exemplo

| Lição | Onde aparece |
|---|---|
| **Comece pelo enquadramento.** As perguntas da Fase 0 evitam retrabalho de escopo. | `brief.md` |
| **A régua não dilui.** Tópicos saíram de D+/C- e só entregaram em A. | matriz C1→C3 |
| **Evoluir ≠ recriar.** Modo Evolução estende o mesmo swarm sem regredir. | Fase E |
| **Primeiro avalie os agentes.** Capacidade nova → autor/revisor novo. | `evo-01-plan.md` |
| **Uniformidade vem do coordenador reativar TODOS.** Não trate a novidade como apêndice. | E.3 |
| **Evidência é obrigatória e verificada.** 72 fontes com HTTP 200. | `sources/sources-index.md` |

---

## Como reproduzir

```text
# 1) Criar do zero
"implemente um playbook sobre CoE de Nuvem (agnóstico de nuvem)."

# 2) Evoluir (assessment + diagramas), citando o swarm_id retornado no passo 1
"Evolua o playbook de CoE de nuvem (<swarm_id>): adicione diagramas de
 arquitetura (Mermaid), defina níveis de maturidade e um modelo/template de
 assessment. Primeiro avalie se faltam agentes; depois reative todos para
 manter a entrega uniforme."
```

> A skill faz o resto: perguntas de enquadramento, geração dos agentes, ciclos
> de autor→revisão→rubber duck e o portão **A** para todos os tópicos.
