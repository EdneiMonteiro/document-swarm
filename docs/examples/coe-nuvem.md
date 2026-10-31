# Exemplo fictício — Playbook de CoE de Nuvem (criação + evolução)

Cenário **inteiramente fictício** para demonstrar o uso da skill
`document-swarm` em dois pedidos ao Copilot CLI. Os prompts foram escritos
para este exemplo; não reproduzem solicitações de um usuário ou cliente.
O cenário não relata uma execução, notas obtidas ou resultados medidos.

Uma organização hipotética deseja criar um playbook de **Cloud Center of
Excellence (CoE)**, ou Centro de Excelência em Nuvem, agnóstico de provedor.
Depois, deseja acrescentar um assessment de maturidade e diagramas de
arquitetura, preservando a coerência do documento.

> `<swarm_id>` representa o identificador retornado por uma execução local.
> Substitua esse marcador ao executar os comandos. Entregas em
> `<clone>\swarms\<YYYY-MM-DD>-SWARM-<XX>\` permanecem locais e são ignoradas
> pelo Git.

## Pedido 1 — Criar o playbook

**Prompt fictício:**

> Crie um playbook de Centro de Excelência em Nuvem para uma organização
> hipotética. Cubra governança, plataforma, segurança, gestão financeira,
> operação e capacitação, sem depender de um provedor específico.

### Enquadramento

A skill confirma objetivo, público, extensão, frameworks, idioma e limites
antes de registrar o brief. Para este cenário, as escolhas poderiam ser:

| Campo | Escolha ilustrativa |
|---|---|
| Objetivo | Orientar a criação e a operação de um CoE. |
| Público | Liderança executiva e times técnicos. |
| Escopo | Papéis, decisões, processos, critérios e modelos reutilizáveis. |
| Referências | Frameworks públicos de adoção de nuvem, FinOps, segurança e operação. |
| Idioma | Português brasileiro, com termos técnicos definidos no primeiro uso. |
| Régua | Nota mínima A em todos os tópicos, sem achado crítico. |
| Limite | Teto de ciclos definido no brief antes dos despachos. |

### Composição ilustrativa do enxame

Os agentes são definidos pelos tópicos e pelas necessidades do público:

- **Autores:** estratégia, governança e plataforma, segurança, FinOps,
  operação e capacitação.
- **Revisores:** precisão técnica, clareza, completude, aderência ao público
  e evidência das fontes.
- **Papéis transversais:** coordenador e rubber duck.

Essa composição é uma possibilidade, não uma quantidade obrigatória de
agentes. O coordenador evita especialidades redundantes e registra os
contratos de cada papel.

### Produção e aprovação

Os autores pesquisam e escrevem suas seções. O coordenador consolida o
documento e executa os checks aplicáveis antes da revisão. Os revisores
avaliam os tópicos, e o rubber duck audita a coerência da entrega e das
avaliações.

| Situação hipotética | Consequência |
|---|---|
| Um tópico recebe A-, abaixo da régua A deste cenário. | Corrigir o tópico e submetê-lo novamente à revisão. |
| Todos os tópicos alcançam a régua, mas existe achado crítico. | A entrega permanece bloqueada. |
| Revisões, evidências e checks satisfazem o contrato. | A candidata pode ser aprovada pelo gate. |
| O limite de ciclos é atingido sem aprovação. | Escalar ao usuário, sem declarar o documento aprovado. |

Não se presume um número de ciclos, fontes ou páginas. Esses resultados
devem ser lidos dos artefatos de uma execução efetiva.

## Pedido 2 — Evoluir o playbook

**Prompt fictício:**

> Evolua o playbook de Centro de Excelência em Nuvem do swarm `<swarm_id>`.
> Acrescente diagramas de arquitetura e um modelo de assessment de
> maturidade. Avalie primeiro as especialidades necessárias e mantenha os
> critérios, termos e referências coerentes em toda a entrega.

O identificador existente e o pedido de extensão acionam o
**Modo Evolução (Fase E)**, em vez de criar um documento independente.

### Fluxo ilustrativo da evolução

1. **Diagnosticar a entrega existente:** ler o brief, o documento,
   os relatórios e os agentes.
2. **Avaliar especialidades adicionais:** verificar se os papéis existentes
   cobrem arquitetura visual e assessment; adicionar agentes somente quando
   houver lacuna.
3. **Atualizar o brief:** registrar novos tópicos, entregáveis e critérios,
   sem relaxar a régua anterior.
4. **Reativar o enxame:** integrar as mudanças às seções existentes, evitando
   um apêndice desconectado.
5. **Consolidar e revisar:** avaliar os tópicos antigos e novos, os diagramas,
   as tabelas e a consistência das referências.
6. **Registrar o resultado:** produzir evidências e relatório final a partir
   dos artefatos, sem presumir aprovação.

### Entregáveis possíveis

- Diagramas de papéis, decisões, plataforma e operação.
- Modelo de maturidade com níveis nomeados e critérios observáveis.
- Template de assessment preenchível e scorecard.
- Orientações de uso, limitações e condições para adoção.

A validação deve verificar os diagramas renderizados, os cálculos do
scorecard, as fontes consultadas e a cobertura dos critérios. Este exemplo
não afirma que essas verificações foram executadas.

## Perfil editorial e acompanhamento

Registre `editorial_profile: principal-cloud-solution-architect` no brief
quando esse perfil atender ao público. A liderança precisa de síntese de
impacto, riscos e decisões; os times técnicos precisam de mecanismos,
restrições e condições de operação.

Cada autor preserva sua especialidade e recebe um contrato editorial
autocontido. Revisores avaliam clareza, alternativas e trade-offs sem forçar
equivalências entre provedores. Veja o
[perfil editorial da skill](../../SKILL.md#21-perfil-editorial-técnico).

Com a extensão instalada e `monitor: true`, o painel acompanha os agentes
e os ciclos depois da criação do brief. O coordenador registra cada despacho
antes da chamada real à ferramenta de agentes. O monitor observa os
artefatos; não executa agentes nem concede aprovação. Consulte o
[guia do monitor](../monitor.md).

O revisor editorial designado lê a entrega completa, incluindo títulos,
legendas e conteúdo acrescentado na evolução. Os arquivos de entrega e seus
hashes vinculam a avaliação à candidata revisada. A aprovação da edição
anterior não aprova automaticamente a nova. Veja o
[contrato de revisão editorial](../editorial-review.md).

## Lições do cenário

| Princípio | Aplicação |
|---|---|
| Enquadrar antes de escrever. | Fixar objetivo, público e critérios no brief. |
| Evoluir sem diluir a régua. | Reavaliar tópicos antigos e novos. |
| Adicionar capacidade, não redundância. | Criar agentes apenas para lacunas reais. |
| Integrar a entrega inteira. | Manter terminologia e critérios uniformes. |
| Verificar o resultado, não a intenção. | Usar artefatos e evidências da execução efetiva. |
