# Revisão editorial da entrega

Desde a versão 3.2, a redação recebe avaliação explícita no gate. O revisor de
clareza continua no swarm; não há um agente adicional de humanização.
As regras de comportamento estão no [SKILL.md](../SKILL.md#21-perfil-editorial-técnico).

## O que precisa ser avaliado

O revisor lê toda a redação da edição atual: capa, títulos, subtítulos,
aberturas, corpo, chamadas, rótulos, legendas e conclusões. Ele identifica
slogans, promessas vagas, títulos intercambiáveis, metatexto dispensável,
antíteses de efeito e encerramentos genéricos.

Uma distinção técnica necessária deve ser preservada e explicada. Uma negativa
como uma proibição operacional fundamentada não é defeito por conter "não".
Índices, legendas, taxonomias e notação matemática continuam disponíveis quando
têm função concreta. O teste é a utilidade e a especificidade da redação.

| Exemplo de problema | Direção de correção |
|---|---|
| Dimensionar melhor. Operar com critério. | Identificar o sistema e a decisão de dimensionamento. |
| A decisão em uma página | Nomear a recomendação e as condições para adotá-la. |
| Não confundir candidato com destino | Explicitar as validações que faltam antes da migração. |
| Três camadas de evidência | Nomear os dados de produção, os testes e as premissas realmente usados. |
| Cores e rótulos acompanham o leitor | Remover a frase; fornecer a legenda concreta onde necessária. |

Esses exemplos não são uma blacklist. Trocar algumas palavras e preservar a
mesma construção vazia continua sendo um problema editorial.

## Como fundamentar a nota

Uma afirmação pode ser arquiteturalmente válida e inadequada ao público pela
forma como foi escrita. Desde a orientação 3.2.1, a justificativa editorial deve
examinar estes aspectos conforme sua relevância no trecho:

| Aspecto | Pergunta de revisão |
|---|---|
| Linguagem | O vocabulário e a construção explicam a alternativa ou reproduzem uma conversa/instrução interna? |
| Referentes | O leitor identifica o que expressões como "isso" ou "esse caso" designam? |
| Tom | O registro é adequado ao público, sem coloquialismo gratuito ou frase de efeito? |
| Autonomia | O trecho explicita as condições e limites pertinentes sem depender da conversa de autoria? |

Exemplo inadequado ao perfil técnico-corporativo:

> AKS compartilhado com dependências segregadas. Não impor outro AKS só por isso.

"Preserva uma alternativa sem exigir outro cluster" explica mérito decisório.
Uma justificativa editorial precisa examinar a ordem "Não impor", que soa como
instrução ao redator, e o referente "só por isso", que deixa implícita a condição
de adoção. A intenção arquitetural pode ser válida; essa redação fica abaixo de A.

Versão de referência:

> AKS compartilhado com dependências segregadas. Aplicável quando a segregação das dependências é necessária, mas o compartilhamento do plano de controle, da administração e da manutenção do cluster permanece aceitável.

Ela nomeia os elementos que continuam compartilhados e a condição para aceitar
esse compartilhamento. A alternativa é preservada, sem afirmar que um cliente
específico já atende aos critérios. Condições novas devem ser confirmadas nas
evidências ou encaminhadas ao autor, nunca acrescentadas como fatos presumidos.

Uma ocorrência deve levar à inspeção da família em títulos, aberturas, corpo,
cards/chamadas, legendas e conclusões. Registre as outras ocorrências encontradas
e os contextos que justifiquem expressões semelhantes. Não substitua análise
contextual por uma lista de palavras proibidas.

## Auditoria das justificativas

O rubber duck compara a nota com o trecho e a justificativa. Aprovações
fundamentadas somente em correção técnica, completude decisória, legibilidade
visual ou aprovação histórica devem voltar ao revisor responsável.

A contestação identifica o revisor, cita o trecho, explica o fundamento
insuficiente e pede a análise de linguagem que falta. O revisor reavalia sua
própria nota; o auditor e o coordenador não a substituem silenciosamente.
O aceite fica bloqueado enquanto a insuficiência material não for resolvida.

Isso vale também quando a frase está adequada, mas a justificativa apresentada
não demonstra essa adequação: o revisor precisa fundamentar sua decisão.
Os hashes corretos continuam sendo fatos de integridade, sem validar o argumento
usado para atribuir A.

## Siglas e códigos na entrega

Desde a orientação 3.2.2, o padrão é usar nomes descritivos. Códigos como T1,
S1, W1 e E1 não possuem um significado universal que possa ser adivinhado pelo
leitor ou pelo coordenador.

- **Controle interno:** mantenha IDs como `T01` no YAML e nos relatórios do swarm.
  O texto público usa o título descritivo, sem renomear chaves internas.
- **Convenção local:** mantenha um código somente quando facilitar referências;
  explique sua finalidade e seu significado no primeiro uso. Use um mapa único
  no documento-fonte para evitar dois sentidos para a mesma abreviação.
- **Abreviação dispensável:** prefira o nome completo, como "Semana 1", quando
  esse for o significado definido pelo autor. Não deduza o significado de W1
  ou de qualquer outro código apenas pela letra.
- **Sigla técnica:** expanda no primeiro uso ou em nota imediatamente próxima.
  Alegações de notação padronizada exigem referência normativa ou técnica.
- **Figura/card/tabela:** dê título ou legenda local que permita interpretação
  isolada. Um glossário distante não resolve um rótulo opaco dentro do elemento.
- **Identificador oficial/código literal:** preserve a identidade exata necessária
  ao assunto e explique sua função no contexto, sem inventar expansões de SKUs,
  campos de API ou nomes de produto.

Citações bibliográficas podem seguir numeração convencional com referências
correspondentes. Elas não devem ser confundidas com códigos de cenários,
categorias ou etapas. Glossário é complementar às definições no ponto de leitura.

O revisor de clareza examina primeiro uso, significado, consistência e autonomia.
Insuficiência material bloqueia A na superfície afetada. O auditor confronta
a justificativa e não aceita apenas "é comum", "é técnico" ou "está no glossário".
O renderer usa a nomenclatura do conteúdo-fonte; não cria rótulos novos.

### Inspeção lexical de apoio

Depois de consolidar o texto integral, execute:

```powershell
python .\scripts\checks\inspect_nomenclature.py <swarm>\reports\cycle-01-editorial-text.txt --output <swarm>\reports\cycle-01-nomenclature.json
```

O script lê Markdown ou texto UTF-8, registra o hash do input, agrupa candidatos
lexicais e informa linhas, colunas e contexto. O relatório pode ser consultado
na aba Detalhes do monitor. Não modifica o documento.

Ele não distingue automaticamente sigla, palavra em maiúsculas ou código local,
não deduz definições e não decide se o primeiro uso está suficientemente
explicado. Em Markdown, exclui frontmatter, blocos cercados de código, comentários
HTML e destinos de links; rótulos em código inline continuam sendo candidatos.
Imagens, PDFs binários e siglas de capitalização mista não são interpretados.

O estado é sempre `inspection_only`, com aprovação editorial `not_evaluated`,
inclusive com zero candidatos. O código de saída 0 informa apenas que a
inspeção foi produzida; 2 informa erro de leitura/configuração. A leitura
contextual pelos revisores permanece obrigatória. Refaça a inspeção se alterar
o texto e não a apresente como prova de cobertura completa.

## Preparação dos artefatos

O brief informa:

```yaml
skill_version: "3.5.0"
quality_contract: editorial-v1
editorial_reviewer: reviewer-03-clarity
pdf_engine: reportlab-v1
deliverables:
  - output/documento.md
  - output/pdf-cycle-01/document.pdf
```

Liste somente os formatos realmente entregues. O revisor deve existir na
composição e avaliar forma, sem substituir a revisão factual.
Se não houver PDF, omita `pdf_engine` e a entrega PDF. Com PDF, use o
[motor e contrato de inspeção](./pdf.md) antes dos revisores; o consolidado
inclui `pdf_inspections`. Esse registro comprova a inspeção mecânica corrente,
não a adequação editorial das palavras ou a qualidade visual das páginas.

Numa apresentação, o brief usa `artifact_type: presentation` e lista os três
formatos entregues. A revisão editorial continua sendo esta, sobre o texto
integral de `cycle-NN-editorial-text.txt`, e é independente das dimensões
`legibility`, `interaction` e `editability` descritas em
[apresentações](./presentations.md).

Conclua a redação no documento-fonte antes da composição. Títulos e legendas
não devem ser criados como uma segunda redação dentro do gerador. Caso a
renderização acrescente texto autoral, leve esse texto de volta à revisão.
Um PDF precisa ser lido como entrega, com seu texto integral e sua renderização;
o código gerador ou a ausência de clipping não bastam para aprovar sua redação.

Em uma evolução visual, preservar conteúdo significa preservar fatos, lógica,
escopo, condições e ressalvas. A redação inadequada pode e deve ser corrigida.
A versão alterada retorna à revisão integral e à vinculação dos artefatos;
a aprovação anterior não é reaproveitada automaticamente.

Preserve o texto efetivamente examinado em UTF-8, por exemplo
`reports/cycle-01-editorial-text.txt`. Registre SHA-256 desse texto e de cada
arquivo final. Os hashes devem ser calculados sobre os bytes reais.

## Registro do revisor

O arquivo `reports/cycle-01-reviewer-03-clarity.json` mantém seus campos de
notas por tópico e acrescenta `editorial`. O coordenador copia o bloco, sem
alterá-lo, para `reports/cycle-01-review.yaml`.

Exemplo de forma do bloco; substitua os caminhos, hashes e avaliações pelos
registros reais. Os trechos abaixo são ilustrativos, não notas de uma entrega:

```json
{
  "schema_version": 1,
  "cycle": 1,
  "reviewer": "reviewer-03-clarity",
  "scope": "full_document",
  "text": {
    "path": "reports/cycle-01-editorial-text.txt",
    "sha256": "<sha256 real de 64 caracteres>"
  },
  "artifacts": [
    {"path": "output/documento.md", "sha256": "<sha256 real>"},
    {"path": "output/pdf-cycle-01/document.pdf", "sha256": "<sha256 real>"}
  ],
  "surfaces": [
    {
      "surface": "titles",
      "grade": "A",
      "location": "Título principal",
      "quote": "Dimensionamento dos node pools do AKS",
      "justification": "O título identifica o objeto e a análise realizada.",
      "action": ""
    },
    {
      "surface": "openings",
      "grade": "A",
      "location": "Abertura",
      "quote": "O estudo compara capacidade alocável",
      "justification": "A abertura apresenta o objeto da comparação.",
      "action": ""
    },
    {
      "surface": "body",
      "grade": "A",
      "location": "Dados utilizados",
      "quote": "Os requests dos pods são comparados à capacidade alocável.",
      "justification": "A relação técnica é expressa diretamente.",
      "action": ""
    },
    {
      "surface": "captions",
      "not_applicable": "Esta entrega não contém figuras, tabelas ou legendas."
    },
    {
      "surface": "conclusions",
      "grade": "A",
      "location": "Condições para adoção",
      "quote": "Não altere produção sem essa validação.",
      "justification": "A negativa preserva uma condição operacional concreta.",
      "action": ""
    }
  ],
  "findings": []
}
```

Todas as superfícies aplicáveis precisam de A ou A+. Apenas `captions` ausente
pode ser justificada sem nota. Cada trecho deve existir no texto examinado;
espaços e quebras de linha são normalizados para a conferência.

Achados não resolvidos também entram em `findings`:

```json
{
  "severity": "blocking",
  "location": "Capa",
  "quote": "Dimensionar melhor. Operar com critério.",
  "reason": "O slogan não identifica o objeto ou a decisão e serve para assuntos distintos.",
  "action": "Substituir por título específico e procurar a mesma construção nas demais seções."
}
```

Um achado `blocking` impede aprovação mesmo que alguém tenha deixado notas A
nas superfícies. Melhorias genuinamente menores usam `severity: minor`.
A nota editorial deve refletir redação; cor, fonte, margem e alinhamento são
assuntos da avaliação visual.

## O que o gate confere

O gate exige, nas novas execuções:

- revisão integral do ciclo atual, em vez de `targeted`, `layout` ou herdada;
- cobertura das cinco superfícies e justificativas/trechos presentes;
- revisor igual ao designado no brief;
- bloco editorial igual ao JSON individual desse revisor;
- lista de artefatos igual à lista de entregas do brief;
- hashes correspondentes aos arquivos reais e trechos presentes no texto;
- notas suficientes e ausência de achados bloqueantes.

O comando permanece:

```powershell
python .\scripts\checks\gate.py <swarm>\reports\cycle-01-review.yaml --output <swarm>\reports\cycle-01-gate.json
```

Os códigos continuam sendo 0 (aprovado), 1 (reprovado), 2 (escalado por limite
de ciclos) e 3 (registro inválido). Alterar um arquivo depois da revisão
invalida sua associação com a avaliação; atualizar apenas o hash sem reler o
artefato viola o contrato.

Os scripts não determinam se a escrita é humana nem julgam a qualidade semântica
das justificativas. Eles tornam verificáveis a presença, o escopo declarado,
a proveniência documental e a identidade dos artefatos. Cabe ao revisor aplicar
a rubrica e ao rubber duck confrontar os trechos com a fundamentação das notas.

## Histórico e evolução

Revisões antigas permanecem legíveis segundo seu contrato original. Em uma
nova evolução, atualize versão, responsável editorial e entregas no brief e
avalie a nova edição integralmente. “Abertura já aprovada” não fundamenta a
nota da edição atual.

Relatório final, monitor e elegibilidade de memória usam a mesma validação
para a revisão corrente. A modificação de um PDF aprovado não continua sendo
apresentada como uma entrega corrente validada. Os relatórios históricos não
são reescritos para simular uma revisão que nunca aconteceu.

## Declarações e calibração

Neste repositório, o coordenador gera as declarações a partir dos templates do
`SKILL.md`. A geração e a reutilização devem materializar a orientação pertinente
ao papel, incluindo os quatro aspectos no revisor de clareza e o protocolo de
contestação no rubber duck. O metadado `editorial_guidance_version` identifica
a versão da orientação; não comprova a qualidade de uma avaliação.

Agentes existentes precisam de atualização explícita de seus arquivos. Alterar
o template ou o metadado sozinho não altera o corpo de uma declaração antiga,
nem o contexto já carregado por uma tarefa em execução. Preserve sua missão,
modelos, escopo e fronteiras de escrita e faça o agente reler o contrato ao retomar.

Os [casos anotados de calibração](../tests/fixtures/editorial/language-calibration.json)
incluem o trecho denunciado, versões corrigidas, justificativas por aspecto,
outras ocorrências da mesma família em todas as superfícies e uma negativa
legítima que permanece aceita. Também incluem fundamentos técnicos, decisórios,
visuais e históricos que não sustentam A editorial isoladamente.

As notas de referência desses casos são julgamentos anotados. Os testes verificam
coerência dos dados e comportamento do gate diante desses registros. Um caso de
controle mantém estrutura e hashes válidos com uma justificativa semanticamente
insuficiente, mostrando que cabe ao auditor contestá-la. Não é apresentado como
detecção automática de linguagem ou prova computacional de boa redação.

Os [casos de nomenclatura](../tests/fixtures/editorial/nomenclature-calibration.json)
acrescentam códigos indefinidos, definição apenas no fim, significado conflitante,
figura sem legenda autônoma, abreviação dispensável, padrão alegado sem fonte e
sigla técnica sem expansão. Um código local corretamente definido permanece
aceito. São exemplos sintéticos anotados, sem atribuir significados à captura
do usuário ou alterar IDs da matriz interna.
