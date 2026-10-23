# Apresentações

A versão 3.4 acrescenta um tipo de entrega opcional: uma fonte estruturada
produz um pacote HTML navegável e dois arquivos PowerPoint. O modo documento
continua intacto e não ganha nenhuma dependência obrigatória.

Os três formatos vêm do mesmo conteúdo e do mesmo plano de composição:

```text
DeckSpec + tema + ativos
        -> LayoutPlan
        -> HTML offline -> quadros capturados -> deck-faithful.pptx
        -> deck-editable.pptx
        -> inspeções dos arquivos salvos
        -> revisão editorial, factual, decisória, visual e de edição
        -> gate -> aceite -> publicação
```

O motor não escreve nem reformula conteúdo e não atribui notas. Um arquivo que
abre sem erro ainda pode ter redação inadequada ou composição ilegível.

## Dependências opcionais

Os checks de `scripts/checks` continuam usando apenas a biblioteca padrão.
Para compor e inspecionar apresentações:

```powershell
python -m venv .venv-presentations
.\.venv-presentations\Scripts\python.exe -m pip install -r requirements-presentations.txt
$env:PLAYWRIGHT_BROWSERS_PATH = "$PWD\.venv-presentations\browsers"
.\.venv-presentations\Scripts\python.exe -m playwright install chromium
```

Em Linux e macOS o motor compõe HTML e os dois PPTX, mas o perfil homologado
exige Windows com PowerPoint instalado; sem ele, a qualificação fica
`not_evaluated` e o portão não aprova a entrega.

A preparação é explícita: nenhum pedido de documento ou apresentação instala
pacotes sozinho, e o Office nunca é instalado pela ferramenta.

## Interface

```powershell
$py = ".\.venv-presentations\Scripts\python.exe"

& $py -m scripts.presentations preflight --swarm <swarm> --profile windows-powerpoint-v1 `
      --isolation <swarm>\reports\isolation-evidence.json

& $py -m scripts.presentations build --swarm <swarm> --deck <swarm>\source\deck.json `
      --destination output\presentation-cycle-01 --profile windows-powerpoint-v1 --cycle 1

& $py -m scripts.presentations inspect --destination <swarm>\output\presentation-cycle-01

& $py -m scripts.presentations publish --swarm <swarm> --cycle 1 --destination <pasta nova>
```

| Operação | Efeito |
|---|---|
| `preflight` | Executa os testes de implementação e qualifica o ambiente numa apresentação sintética, nunca na candidata. Grava `reports/implementation*.json` e `reports/profile*.json`. |
| `build` | Compõe os três formatos num destino **novo**, inspeciona os arquivos salvos e grava inventário, manifesto, inspeções e o texto editorial do ciclo. |
| `inspect` | Reexamina uma pasta existente sem alterá-la; recompõe o plano a partir da fonte e recusa um plano derivado de outro deck. |
| `publish` | Registra o aceite depois de um gate aprovado e copia exatamente o conjunto aceito para um destino novo. |

Códigos de saída: **0** quando tudo que foi executado passou, **1** quando uma
verificação executada falhou e **2** para erro de entrada, dependência ou
composição. O destino de `build` e de `publish` precisa ser inédito: nenhuma
entrega avaliada é sobrescrita ou mesclada.

## Fonte: DeckSpec

O modelo é JSON estrito, validado pelo schema
[`schemas/presentation/deck.schema.json`](../schemas/presentation/deck.schema.json).
Chaves duplicadas, números não finitos, propriedades desconhecidas, IDs repetidos
e referências inexistentes são recusados. Nada no deck é executado.

Identificadores autorais usam letras ASCII minúsculas, dígitos, hífen e
sublinhado, começam por letra e têm até 64 caracteres. O prefixo `sys:` é
reservado às páginas e ações geradas.

```json
{
  "schema_version": 1,
  "deck_id": "exemplo",
  "language": "pt-BR",
  "title": "Título da apresentação",
  "size_pt": {"width": 960, "height": 540},
  "theme_ref": "neutral-v1",
  "index": {"title": "Sumário"},
  "topics": ["T01"],
  "actions": [
    {"action_id": "abrir-detalhe", "trigger_block_id": "controle",
     "kind": "open_support", "support_id": "detalhe"}
  ],
  "slides": [
    {
      "slide_id": "visao-geral",
      "topic_ids": ["T01"],
      "layout": "title-and-body",
      "title": [{"text": "Visão geral"}],
      "presenter_notes": "Notas publicáveis do apresentador.",
      "blocks": [
        {"block_id": "controle", "type": "control", "layout": {"area": "body"},
         "data": {"action_id": "abrir-detalhe", "label": "Abrir o detalhe"}}
      ]
    }
  ],
  "supports": [
    {"support_id": "detalhe", "layout": "title-and-body",
     "title": [{"text": "Detalhe"}], "presenter_notes": "",
     "blocks": [{"block_id": "texto", "type": "text", "layout": {"area": "body"},
                 "data": {"paragraphs": [{"runs": [{"text": "Conteúdo."}]}]}}]}
  ]
}
```

### Blocos

| `type` | Conteúdo e limite |
|---|---|
| `text` | Parágrafos com trechos em negrito, itálico ou monoespaçado e marcador opcional. |
| `table` | Cabeçalho e linhas retangulares, até 12 colunas; sem células mescladas. |
| `code` | Linhas literais; caracteres e sinais preservados, sem execução. |
| `image` | Ativo PNG ou JPEG declarado, com alternativa textual obrigatória. |
| `shape` | Retângulo, retângulo arredondado ou elipse, com texto opcional. |
| `diagram` | Nós com caixa própria e conectores retos ou ortogonais entre portas declaradas. |
| `control` | Botão que dispara uma ação autoral. Só existe em slides principais. |

`layout` escolhe uma área do layout do tema (`{"area": "body"}`) ou fixa a
geometria (`{"box": {...}}`). Blocos de apoio usam apenas áreas, porque apoios
são paginados. Os três layouts do tema neutro são `title-and-body`,
`two-column` e `full-stage`.

Imagens são colocadas no tamanho intrínseco do arquivo (96 px por polegada),
reduzidas apenas para respeitar a largura da área. O compositor não inventa um
tamanho de exibição.

### Ações e navegação

| `kind` | Alvo | Resolução |
|---|---|---|
| `goto_slide` | `slide_id` existente | Abre esse slide principal. |
| `open_support` | `support_id` existente | Abre a primeira página do apoio materializado para o slide que acionou. |
| `open_external` | `reference_id` com URL `https` | Abre a referência somente por ação do usuário. |

Ações autorais existem apenas nos principais e precisam de um bloco `control`
que as dispare. Um apoio não aciona outro apoio.

Um apoio reutilizado é materializado **por par origem/apoio**, com páginas
numeradas a partir de 1: `sys:support:<slide>:<apoio>:<página>`. O sumário usa
`sys:index:<página>`. A ordem física é: páginas de sumário, principais na ordem
autoral e apoios ordenados por origem, apoio e número.

A sequência normal percorre sumário e principais. Os controles gerados são
`sys:nav:previous`, `sys:nav:next`, `sys:nav:index`, `sys:nav:back` e
`sys:index:entry:<slide>`. Nos extremos, o controle aparece desabilitado e não
tem destino. Dentro de um apoio, anterior e próximo percorrem apenas aquela
materialização, e o retorno leva ao principal que o abriu.

### Tipografia disponível

O tema neutro usa Carlito para texto e IBM Plex Mono para código, distribuídos
sem modificação sob a SIL Open Font License 1.1 e verificados pelo manifesto de
hashes em `scripts/pdf/fonts`. Um caractere sem glifo nessas faces **falha de
forma explícita** em vez de virar um quadrado: por exemplo `⌈`, `⌉`, `∧`, `∨` e
`⇒` não estão cobertos. Reescreva a expressão ou proponha outra face ao tema.

## Saídas

```text
output/presentation-cycle-01/
  deck.json  layout.json  theme.json
  index.html  runtime/  assets/  fonts/
  deck-faithful.pptx  deck-editable.pptx  LEIA-ME.txt
reports/
  cycle-01-inputs.json
  cycle-01-presentation-manifest.json
  cycle-01-presentation-inspections.json
  cycle-01-editorial-text.txt
```

O HTML funciona a partir de `file:`, sem servidor, CDN, service worker ou
`fetch`. A política `Content-Security-Policy` permite somente o runtime e os
recursos locais enumerados; a geometria vai para `runtime/pages.css` justamente
para não precisar de estilos inline. Os apoios abrem em `dialog` modal, com foco
contido, fechamento por Escape e retorno do foco ao botão que os abriu.
O contador usa a sequência do apoio enquanto o diálogo está aberto. Espaço
aciona o controle focado; fora de um controle, avança a apresentação. Home volta
ao sumário com o diálogo fechado, inclusive após Escape; um evento de fechamento
atrasado não desfaz essa navegação.

Os rótulos do sumário usam a tipografia de corpo, correspondente à geometria
planejada, com texto escuro sobre a página clara. O teste de regressão mede
tinta no raster dos rótulos e inclui um controle de texto branco invisível.

`deck-faithful.pptx` tem uma imagem por página mais áreas de clique quase
transparentes sobre ela. `deck-editable.pptx` tem título, texto, tabelas, formas
e conectores nativos; imagens continuam imagens. Nos dois, as páginas de apoio
ficam ocultas na sequência normal e são alcançadas pelos links visíveis.
Os controles dos dois PPTX recebem descrições alternativas. Cada imagem de página
do arquivo fiel também recebe uma descrição com o título e a indicação de usar
o arquivo editável para ler o texto; isso não torna o texto da imagem selecionável.

## Inspeção

A inspeção lê os arquivos salvos, não o mapa emitido pelos exportadores. O HTML é
analisado como markup entregue; os PPTX são abertos como pacotes ZIP com limites
de entradas, expansão e nomes, e percorridos como XML.

| Verificação | O que detecta |
|---|---|
| Conteúdo | Palavra, linha, célula, rótulo, legenda ou nota ausente em qualquer formato. |
| Inventário | Página, arquivo ou objeto ausente ou acrescentado sem declaração. |
| Navegação | Destino errado, controle ausente, extremo habilitado e link externo alterado. |
| Interação | Diálogo modal, contador do apoio, foco contido, Escape, retorno do foco, Espaço no controle focado e Home após fechamento, no navegador real. |
| Estrutura nativa | Texto achatado em imagem, conector sem ancoragem e objeto fora do palco. |
| Pacote | Macro, objeto OLE, relacionamento externo indevido e hyperlink não `https`. |
| Ambiente | Edição que não persiste depois de salvar e reabrir, e divergência visual acima da tolerância. |

A comparação textual ignora espaços, mas preserva operadores, acentos e
distinções Unicode. Decoração marcada com `aria-hidden` não entra no cotejo.

## Qualificação do ambiente

`windows-powerpoint-v1` identifica um contrato de perfil, não todas as versões
de Windows ou Office. O preflight mede e registra sistema, versões de Python,
python-pptx, Playwright, Pillow e pywin32, a revisão do Chromium provisionado,
a versão e o build do PowerPoint e as faces de fonte com seus hashes.

Três verificações compõem o perfil:

- **`component-capability`** abre cópias no PowerPoint, exporta as páginas, altera
  texto, célula e posição de forma, salva, reabre e confere o que persistiu,
  inclusive a ancoragem do conector.
- **`visual-font-calibration`** compara as páginas exportadas pelo PowerPoint com
  os quadros capturados do HTML e registra a diferença medida por página.
- **`interactive-isolation`** depende de evidência do operador. Sem ela o estado é
  `not_evaluated`, e `not_evaluated` nunca aprova.

A Microsoft não recomenda automação do Office em serviço não interativo. Por
isso a composição usa a biblioteca sem Office e a homologação ocorre numa
estação Windows com sessão interativa. A ferramenta não altera políticas, não
fecha um PowerPoint do operador e não instala nada.

### Evidência de isolamento

O arquivo é fornecido pelo operador e descreve a janela em que a estação ficou
sem saída de rede:

```json
{
  "schema_version": 1,
  "responsible": "<quem configurou o controle>",
  "window_start": "2026-10-05T09:00:00Z",
  "window_end": "2026-10-05T10:00:00Z",
  "control": "<descrição do controle de rede externo ao processo>",
  "verified_blocked": true
}
```

Mudança de configuração, transmissão de conteúdo ou reconexão durante o ensaio
invalidam a evidência: o perfil precisa ser restabelecido e o ensaio repetido.

## Contrato do portão

No brief:

```yaml
artifact_type: presentation
quality_contract: editorial-v1
editorial_reviewer: reviewer-01-clarity
presentation:
  schema_version: 1
  capability: presentation-v1
  deck_path: output/presentation-cycle-01/deck.json
  profile: windows-powerpoint-v1
  required_formats:
    - html-offline
    - pptx-faithful
    - pptx-editable
deliverables:
  - output/presentation-cycle-01/index.html
  - output/presentation-cycle-01/deck-faithful.pptx
  - output/presentation-cycle-01/deck-editable.pptx
```

No consolidado do ciclo, o bloco `presentation` referencia inventário,
manifesto e inspeções por caminho e SHA-256, e carrega uma linha por posição
avaliada. O catálogo de dimensões é fechado:

| Dimensão | Unidade obrigatória |
|---|---|
| `factual` | Cada tópico do ciclo. |
| `decision` | Cada tópico do ciclo. |
| `legibility` | Cada página lógica em cada um dos três formatos. |
| `interaction` | Cada página lógica em cada um dos três formatos. |
| `editability` | Cada página de `pptx-editable`. |

Nenhuma dessas dimensões aceita `not_applicable`. A revisão editorial de
`editorial-v1` continua obrigatória e é avaliada separadamente, sobre o texto
integral entregue.

O leitor do portão usa apenas a biblioteca padrão e **reconstrói o domínio
esperado a partir do deck**: páginas, materializações de apoio, grafo de
navegação e posições de revisão. Um manifesto que repita a omissão de um
exportador não reduz o que precisa ser coberto, e o inventário declarado é
comparado com o conteúdo real do diretório.

| Situação | Resultado |
|---|---|
| Tudo verificado e aprovado | `0` |
| Verificação executada falhou, nota abaixo de A ou veto crítico | `1`, ou `2` no teto de ciclos |
| Contrato incompatível, evidência ausente, hash divergente ou check não executado | `3` |

`pending`, `not_evaluated`, `unsupported` e `stale` nunca equivalem a sucesso.
Qualquer alteração em conteúdo, ativo, fonte, layout, arquivo ou revisão
invalida o aceite corrente; o histórico permanece e a nova edição exige novas
exportações, inspeções pertinentes e leitura editorial integral.

## Publicação

O aceite enumera os arquivos públicos das camadas anteriores e só é gravado
depois de um gate aprovado e vinculado aos bytes da revisão. A cópia confere
cada arquivo, usa uma pasta temporária exclusiva no destino e promove o
conjunto inteiro para um nome novo. Destino existente é erro, nunca mesclagem.

Fontes apenas consultáveis não viram arquivo publicado: entram no inventário
como proveniência externa, com identificador e digest, sem caminho pessoal.

## Testes

```powershell
python -m unittest tests.test_presentation_contract -v
.\.venv-presentations\Scripts\python.exe -m unittest tests.test_presentation_engine -v
```

O primeiro conjunto usa apenas a biblioteca padrão e cobre o contrato: registro
completo, capacidade desconhecida, campos legados de slides, cobertura
incompleta, grafo de navegação divergente, apoio ocultado, arquivo não declarado,
caminho inseguro, evidência desatualizada e os códigos do portão.

O segundo compõe a apresentação de referência e insere defeitos deliberados:
texto removido do HTML, controle apagado, política enfraquecida, recurso remoto,
texto nativo apagado, texto acrescentado ao arquivo fiel, apoio visível na
sequência normal, objeto fora do palco, conector desancorado, notas removidas,
relacionamento externo e pacote hostil. Cada defeito exige o diagnóstico
correspondente. A integração ponta a ponta monta um swarm real, passa pelo
portão, altera cada classe de arquivo entregue e confirma que gate, relatório,
monitor e memória deixam de aceitar a entrega.

Sem as dependências opcionais os testes do motor são marcados como não
executados; não confunda isso com aprovação.

## Limites desta versão

Não há PDF de apresentação, editor visual, importação de HTML arbitrário,
animação, áudio, vídeo, macro ou retorno automático de edições do PowerPoint
para a fonte. A exclusão de PDF aqui não altera o
[motor PDF de documentos](./pdf.md).

Aparência homologada no arquivo editável depende da instalação das fontes no
sistema de quem abre o arquivo; o arquivo fiel preserva a aparência por imagens.
As notas do apresentador acompanham os três formatos e ficam acessíveis a quem
recebe a pasta.
