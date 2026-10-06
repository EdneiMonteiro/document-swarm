# Suporte

## Como obter ajuda

Este projeto utiliza GitHub Issues para:

- Bugs
- Dúvidas
- Sugestões

Antes de abrir uma issue:

1. Verifique se já existe uma semelhante
2. Leia README e DISCLAIMER

## O que incluir

- Descrição clara
- Passos para reproduzir
- Erro esperado vs ocorrido
- Logs / prints
- Versões utilizadas

## Nível de suporte

Para o monitor visual, inclua também:

- versão do Copilot CLI, Node.js e Python;
- canvas ou navegador utilizado;
- fase/ciclo afetados e se o problema ocorre em uma fixture sintética;
- diagnóstico da extensão `document-swarm-monitor` e mensagem de erro relevante.

Não publique URLs do monitor contendo credenciais temporárias, prompts, tokens,
dados de clientes ou um dump completo da sessão. Prefira mensagens mínimas e
capturas sem informações sensíveis. O [guia do monitor](./docs/monitor.md#diagnóstico)
descreve falhas conhecidas e a continuidade pelo terminal.

Para composição ou inspeção PDF, inclua perfil/idioma, versões do Python e das
dependências opcionais, código de erro de `inspection.json` e uma fonte sintética
mínima. O relatório pode conter trechos integrais do documento; não o publique
sem remover dados confidenciais. Consulte os [limites e comandos PDF](./docs/pdf.md).
Uma inspeção mecânica aprovada não atesta qualidade editorial ou correção técnica.

Para apresentações, informe também o perfil usado, as versões de Python,
python-pptx, Playwright e Chromium, a versão e o build do PowerPoint, o estado
de cada verificação em `cycle-NN-presentation-inspections.json` e um deck
sintético mínimo. Não envie decks, notas do apresentador ou pacotes com conteúdo
de cliente. Os [limites e comandos](./docs/presentations.md) descrevem o que o
perfil cobre e o que permanece `not_evaluated`.

Para o executor determinístico, informe a versão do Copilot CLI e do Python, o
comando e o código de saída de `run`, a saída de `python scripts/orchestration
status <swarm>`, o relatório de `qualify` quando existir e o estado em
`reports/execution/driver.json`. Os arquivos de `reports/execution/results/` e
`documents/` guardam o texto produzido pelos agentes; o `journal.jsonl` guarda só
metadados, mas as mensagens de erro podem citar trechos. Não publique nada disso sem
remover o conteúdo do cliente, e use um swarm sintético mínimo para reproduzir. O
[guia do executor](./docs/executor.md) lista as proteções, os códigos de saída e os
limites conhecidos.

## Limites de suporte

Este projeto:

- Não possui SLA
- Não garante resposta
- Não garante correções
- Não garante evolução

## Suporte oficial

Este repositório **não substitui suporte oficial da Microsoft**.

Para ambientes produtivos, utilize suporte oficial.
