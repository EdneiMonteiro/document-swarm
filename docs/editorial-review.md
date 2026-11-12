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

## Completude decisória e falsos compromissos (`technical-report` / `textbook`)

Enquanto o revisor de clareza responde por `editorial-v1`, o revisor de
completude decisória (`reviewer-*-decision`) avalia critérios, alternativas,
trade-offs, aceite e reversão nos modos `technical-report` e `textbook` (nunca
em `magazine`, `book` ou `almanac`). Para evitar que autores aceitem acoplamentos
evitáveis como leis imutáveis — e, ao mesmo tempo, impedir que revisores forcem
complexidade desnecessária —, essa rubrica segue quatro regras:

1. **Passo 0 obrigatório (trava anti-*over-engineering*):** antes de sugerir
   qualquer transformação, verificar se o limite citado é um invariante
   teórico/físico, SLA externo, teto de custo/prazo ou escolha deliberada de
   simplicidade operacional registrada no `brief.md`. Quando a arquitetura já
   estiver equilibrada, registrar `"Nenhum falso compromisso detectado"` na
   justificativa e manter a avaliação livre de exigências especulativas (filas,
   sidecars, CQRS ou microsserviços sem contradição real).
2. **Padrão resolutivo concreto:** quando houver falso dilema real (como tratar
   "maior vazão exige abrir mão de durabilidade" como inevitável), apontar o
   acoplamento e indicar o padrão arquitetural estabelecido que o resolve
   (segmentação, extração/sidecar, ação preliminar/cache, *cushioning*/backpressure,
   inversão, intermediário/fila durável/outbox, *self-service* ou workers efêmeros).
3. **Passivos operacionais secundários:** toda sugestão de padrão deve declarar
   os novos custos operacionais introduzidos (hops extras, consistência eventual,
   idempotência no consumidor, manutenção de fallback ou memória ociosa).
4. **Severidade calibrada:** acoplamentos periféricos geram observações
   consultivas (`severity: minor`, sem derrubar nota abaixo de `A`); apenas um
   falso compromisso central que invalide a recomendação arquitetural ou oculte
   risco operacional grave bloqueia o tópico (`<= A-`, `severity: blocking`).

O rubber duck audita as duas pontas: contesta recomendações aprovadas sobre um
falso dilema central não examinado e devolve ao `reviewer-*-decision` notas
rebaixadas por *over-engineering* especulativo que ignore o Passo 0, sem
substituir silenciosamente a nota do revisor. Os casos anotados de referência
estão em
[`tests/fixtures/editorial/false-compromise-calibration.json`](../tests/fixtures/editorial/false-compromise-calibration.json).

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
skill_version: "4.1.0"
quality_contract: editorial-v1
comprehension_contract: goldfish-v1
evidence_contract: evidence-v1
audience: "Arquitetos e engenheiros de plataforma responsáveis por operar AKS em produção"
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

O JSON original mantém notas por tópico e acrescenta `editorial`, já vinculado
aos hashes examinados. Na 4.0, registre-o com `evidence.py review` conforme o
[contrato factual](./evidence.md). `cycle_artifacts.py consolidate --candidate`
calcula os mínimos e copia o bloco sem alterar notas, trechos ou hashes, nem
sobrescrever o original. Sem JSON independente do auditor, o estado continua
`audit_pending`. Parecer herdado não certifica a candidata atual.

O diff orienta a revisão, mas não substitui a leitura integral. Uma descoberta
fundamentada pode reabrir nota A em texto inalterado; inspecione a família
inteira. O formato `regression` legado também aceita
`newly_discovered_defect`, com trecho, evidência e motivo.

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
  "findings": [],
  "blind_paraphrase": {
    "reader_context": {
      "path": "reports/evidence/reader-context/composition-01-c01.json",
      "sha256": "<sha256 real do reader-context congelado>"
    },
    "central_thesis": "O relatório compara os requests reais dos pods com a capacidade alocável dos node pools do AKS para recomendar redimensionamento seguro condicionado à validação prévia.",
    "how_parts_connect": "A abertura define o recorte de capacidade alocável, o corpo demonstra a relação entre requests e reservas dos nós, e a conclusão vincula qualquer mudança em produção a uma etapa explícita de validação.",
    "unconnected_sections": []
  }
}
```

Todas as superfícies aplicáveis precisam de A ou A+. Apenas `captions` ausente
pode ser justificada sem nota. Cada trecho deve existir no texto examinado;
espaços e quebras de linha são normalizados para a conferência.

### Isolamento Goldfish e Paráfrase às Cegas (`goldfish-v1`)

A partir da versão `4.1.0` (`comprehension_contract: goldfish-v1`), o
`editorial_reviewer` (`reviewer-*-clarity`) opera sob contexto zero dos
bastidores:

- **Sem acesso ao `brief.md`:** `brief.md` completo é entregue somente aos
  revisores de fatos/decisão e ao `rubber-duck`. O `editorial_reviewer` é
  despachado sempre por uma chamada nova de `task` (nunca `write_agent`),
  recebendo apenas `reports/evidence/reader-context/<composition_id>-c<NN>.json`
  (público-alvo conciso de uma linha, perfil editorial, mapa de IDs de tópicos e
  hashes das entregas) e a candidata congelada;
- **Paráfrase em palavras próprias (`blind_paraphrase`):** `central_thesis` e
  `how_parts_connect` (30 a 2.500 caracteres cada, distintos entre si) não
  podem copiar trechos literais da candidata (`gate.py` e `evidence.py` recusam
  cópia verbatim com `exit 3`). Se uma seção exigir conhecimento oculto do
  briefing para se conectar às demais, ela deve constar em
  `unconnected_sections` acompanhada de achado `blocking`;
- **Confronto pelo `rubber-duck` (`paraphrase_check`):** o auditor independente
  compara `blind_paraphrase` com o `brief.md` real (`brief_sha256`). Quando
  `matches_brief` é `false`, o auditor registra `critical: true` e um achado
  `blocking`, vetando a entrega no gate.

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
nas superfícies. Melhorias genuinamente menores usam `severity: minor` e não
podem reduzir a nota de uma superfície ou tópico para abaixo de `A`: quando
todos os achados de `findings` forem `minor`, atribuir `A-` ou menos é inválido
(`exit 3`). Da mesma forma, no ciclo `N > 1`, rebaixar um tópico ou superfície
que já tinha `A`/`A+` do mesmo revisor no ciclo `N-1` (sem contestação do
rubber duck) exige o bloco `regression` com `cause`
(`text_changed_regression` ou `factual_or_cross_table_contradiction`), `quote`,
`previous_quote` (ou `conflicting_quote`) e `evidence`. Reabrir texto inalterado
por preferência estilística ou terminológica é recusado pelo gate.
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
