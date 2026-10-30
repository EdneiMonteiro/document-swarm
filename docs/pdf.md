# PDFs profissionais

O motor opcional da versão 3.3 compõe documentos Markdown com ReportLab/Platypus
e inspeciona o PDF gerado. Ele oferece composição fluida e arte vetorial original,
sem caminhos fixos, contagens predeterminadas de capítulos ou dependência de
fontes do Windows.

O motor não cria nem reformula conteúdo. Títulos, legendas, unidades, rótulos,
valores apresentados e recomendações pertencem à fonte. A aprovação editorial
continua separada da inspeção mecânica.

## Dependências opcionais e instalação

Os checks de fontes, tabelas, agentes, nomenclatura e gate continuam usando
somente a biblioteca padrão do Python. Para PDF:

```powershell
python -m venv .venv-pdf
.\.venv-pdf\Scripts\python.exe -m pip install -r requirements-pdf.txt
pwsh .\scripts\install.ps1 -WithPdf
```

Em Linux/macOS:

```bash
python3 -m venv .venv-pdf
.venv-pdf/bin/python -m pip install -r requirements-pdf.txt
./scripts/install.sh --with-pdf
```

O instalador registra a extensão pessoal, sem instalar pacotes automaticamente.
`--without-monitor`/`-WithoutMonitor` continua independente. Omitir a opção de
PDF não remove uma instalação PDF anterior.

A ferramenta procura primeiro `.venv-pdf` do clone e depois um Python que
contenha as dependências. Ela não transfere conteúdo para serviços externos.
Se a extensão não estiver disponível na sessão, use os mesmos comandos pelo terminal.

## Perfis visuais

| Perfil | Composição |
|---|---|
| `textbook` | Livro didático A4: capa com título serifado, prosa justificada de 11,25 pt, capítulos, subtítulos azul/petróleo e espaçamento de leitura longa. |
| `technical-report` | Relatório A4 com a mesma identidade visual, corpo de 10,8 pt e hierarquia mais compacta. |
| `magazine` | Matéria / reportagem especial ilustrada A4: paleta editorial quente (carmim/terracota `#a83820`, azul petróleo `#1b5e7a`, fundo creme `#faf6ee`), títulos em `DSSerifBold`, fluxo contínuo entre seções (sem quebras de página forçadas a cada `#`), *pull-quotes*, boxes de bastidores, fotos e remoção automática de colchetes `[Fxx]` inline. |
| `book` | Livro ilustrado **A5 (`148 × 210 mm`)**: pensado para impressão em **papel Pólen** (`#fdfbf5`), corpo integralmente composto na família serifada `IBM Plex Serif` (`DSSerif`, `DSSerifBold`, `DSSerifItalic`, `DSSerifBoldItalic`), **Primeira Capa isolada** na página 1 (`:::cover-art` + título + subtítulo, sem parágrafos de corpo misturados na capa), **Contracapa dedicada (`:::back-cover`)** na última página (sinopse, destaques/crítica e colofão), capítulos iniciando em nova página e prosa limpa sem colchetes `[Fxx]` inline. |
| `almanac` | Almanaque / compêndio visual **A5 (`148 × 210 mm`)**: paleta azul-marinho/ouro/turquesa (`#b87314` / `#0f766e`), prosa em `IBM Plex Serif` combinada com tabelas e fichas catalográficas (`:::factbox`) em `Carlito`, Primeira Capa isolada, Contracapa (`:::back-cover`), quadros *"Você Sabia?"* (`:::callout`) e prosa limpa sem colchetes `[Fxx]` inline. |

Ambos preservam a paleta, a arte original, tabelas com cabeçalho azul, linhas
alternadas e diagramas vetoriais. Fontes portáveis alteram métricas e quebras
de linha em relação à antiga composição com fontes proprietárias; a referência
é a identidade visual, não uma promessa de PDF pixel-idêntico.

As fontes Carlito, IBM Plex Serif, IBM Plex Mono e Noto Sans Math são distribuídas
sem modificação sob SIL Open Font License 1.1. Licenças, copyrights, URLs de
origem fixadas e hashes estão em `scripts/pdf/fonts`. Não há download de fontes
durante a renderização. O Git preserva os bytes originais das fontes e licenças,
e o motor confere seus hashes antes de compor. O PDF incorpora os glifos usados.

## Interface

```powershell
.\.venv-pdf\Scripts\python.exe -m scripts.pdf render `
  --source documento.md --destination output\pdf-cycle-01 `
  --profile textbook --language pt-BR

.\.venv-pdf\Scripts\python.exe -m scripts.pdf inspect `
  --source documento.md --destination output\pdf-cycle-01
```

O destino de `render` deve ser uma pasta nova. Isso preserva arquivos já
avaliados; use outro diretório para uma revisão. A composição acontece numa
pasta temporária própria e o bundle é publicado depois da inspeção. Um bundle
com falhas mecânicas recebe `status: fail` e não está pronto para revisão final.

`inspect` reexamina um bundle existente, atualizando prévias e relatório sem
alterar o PDF nem a fonte. Mudança de fonte ou PDF em relação ao manifesto
original é registrada como falha. Mudança na geometria declarada do layout é
recusada: recomponha a partir da fonte, em vez de adaptar a inspeção ao defeito.

A extensão `docswarm_pdf` oferece as mesmas ações:

```json
{
  "action": "render",
  "source": "<caminho absoluto de documento.md>",
  "destination": "<caminho absoluto de uma pasta nova>",
  "profile": "textbook",
  "language": "pt-BR"
}
```

`inspect` recebe fonte e destino do bundle. Perfil/idioma, quando fornecidos
na inspeção, precisam corresponder aos registrados. Idiomas aceitos:
`pt-BR`, `pt-PT`, `en-US`, `en-GB` e `es-ES`. A opção identifica o documento;
não traduz palavras nem inventa rótulos para o sumário.

Códigos de saída: **0** para inspeção mecânica aprovada, **1** para falhas
detectadas no PDF e **2** para erro de entrada, dependência ou composição.

## Aquisição de fotos e geração de gravuras com FLUX.2-pro (`fetch_media.py`)

Nos modos `magazine`, `book` e `almanac`, o utilitário `scripts/checks/fetch_media.py`
permite tanto baixar fotografias reais verificadas (Wikipedia, Wikimedia Commons
ou URLs diretas) quanto gerar gravuras editoriais e cortes técnicos sob medida
via **FLUX.2-pro** no Azure APIM (lendo a chave diretamente do Windows Credential
Manager sem expor segredos no repositório):

```powershell
# Buscar foto real no Wikimedia Commons
python scripts\checks\fetch_media.py `
  --output-dir swarms\2026-10-29-SWARM-02\sources\media `
  --id takumi-ae86 --commons-search "Toyota Sprinter Trueno AE86 Initial D"

# Gerar gravura editorial ou corte técnico via FLUX.2-pro (estilos: engraving, manga-engraving, technical-cutaway, editorial-illustration, raw)
python scripts\checks\fetch_media.py `
  --output-dir swarms\2026-10-29-SWARM-02\sources\media `
  --id flux-cover-akina --flux-style manga-engraving `
  --flux-prompt "1986 Toyota Sprinter Trueno AE86 drifting down Mount Akina at dawn" `
  --width 1024 --height 768

# Verificar integridade e hashes SHA-256 de todas as mídias do manifesto
python scripts\checks\fetch_media.py `
  --output-dir swarms\2026-10-29-SWARM-02\sources\media --verify
```


## Fonte Markdown

O primeiro título `#` é o título autoral da entrega. Use `:::cover-art` para a
arte original e `:::pagebreak` para concluir a capa. O sumário recebe seu título
da própria fonte:

```markdown
:::cover-art

# Título do documento

## Subtítulo escrito pelo autor

Texto de apresentação.

:::pagebreak

# Sumário

:::toc

# 1. Primeiro capítulo

Prosa **com destaque**, *ênfase* e `código literal`.
```

São suportados parágrafos, títulos, listas, citações, tabelas Markdown, código
cercado e links HTTP(S) ou para títulos locais. Conteúdo não suportado deve ser
convertido explicitamente na fonte; o motor falha em vez de omiti-lo.
Não há execução de código, busca de imagens remotas ou interpretação de HTML livre.
Marcações `<!-- check: weighted|sum|percent ... -->` continuam na fonte para o
check de tabelas, mas não são impressas como texto destinado ao cliente.
Outros comentários ou tags HTML fora de código são recusados explicitamente,
evitando publicar instruções internas do autor.

### Fórmulas

Use Unicode literal, com quebras de linha autorais quando necessárias:

```text
:::formula capacity
{
  "expression": "N ≥ ⌈D / C⌉ + R\nC > 0 ∧ D ≥ 0",
  "caption": "Definições e condições fornecidas pelo autor."
}
:::
```

Blocos cercados `math`/`formula` também aceitam expressões literais. O MVP não é
um processador LaTeX nem um solucionador matemático: não rearranja termos,
calcula valores ou substitui operadores. Glifos indisponíveis produzem erro
explícito, não quadrados ou caracteres aproximados.

### Figuras e Imagens Reais (`flow`, `layers`, `bars`, `image`)

Use um identificador local para o processamento e forneça o conteúdo visível
integralmente. Tipos: `flow`, `layers`, `bars` e `image`.

```text
:::figure comparison
{
  "kind": "bars",
  "title": "Título autoral da comparação",
  "caption": "Condições e limites da figura.",
  "labels": ["Alternativa original", "Alternativa proposta"],
  "values": [100, 70],
  "display_values": ["100", "70"],
  "unit": "Unidade definida na fonte"
}
:::
```

Para **fotos, gravuras históricas e ilustrações reais** (baixadas e verificadas em `sources/media/` com `scripts/checks/fetch_media.py`), use `kind: "image"`:

```text
:::figure foto-ae86
{
  "kind": "image",
  "src": "sources/media/ae86-trueno.jpg",
  "title": "Figura 1 - Toyota Sprinter Trueno GT-APEX (AE86)",
  "credit": "Foto: Wikimedia Commons (CC BY-SA 4.0) [F04]",
  "caption": "Exemplar de três portas hatchback equipado com faróis escamoteáveis e motor 4A-GE.",
  "max_height": 220
}
:::
```

`display_values` evita que o compositor invente arredondamento ou formatação
numérica. `flow` conecta os rótulos na ordem fornecida; `layers` representa
camadas aninhadas. Esses tipos recebem `title`, `caption` e `labels`, sem
campos numéricos. `image` recebe `src` (caminho relativo para `.png`/`.jpg`/`.jpeg`),
`title`, `caption` e opcionalmente `credit` e `max_height` (`40` a `520` pt).

### Destaques Editoriais, Quadros Didáticos e Fichas de Almanaque (`pullquote`, `callout`, `factbox`)

Nos perfis `magazine`, `book` e `almanac` (e quando útil nos demais), use os três blocos estruturados abaixo para criar ritmo visual, explicar conceitos a leigos e organizar fichas de consulta:

```text
:::pullquote virada-historica
{
  "quote": "Ao deslocar o foco da potência bruta para o equilíbrio dinâmico nas descidas de montanha, a obra transformou um compacto acessível de 1983 em ícone mundial.",
  "attribution": "Síntese histórica da recepção cultural [F01]"
}
:::

:::callout conceito-leigo
{
  "kind": "key-concept",
  "title": "Para Entender Melhor: O que é relação peso-potência?",
  "body": "Mesmo com um motor de 130 cavalos, uma carroceria leve de 940 kg permite frear mais tarde e manter velocidade nas curvas sem sobrecarregar os pneus dianteiros."
}
:::

:::factbox ficha-tecnica-ae86
{
  "title": "Toyota AE86 Sprinter Trueno (1983-1987)",
  "items": [
    {"label": "Configuração", "value": "Motor dianteiro longitudinal, tração traseira (FR)"},
    {"label": "Motorização", "value": "1.587 cm3 DOHC 16 válvulas (4A-GE), 130 PS a 6.600 rpm"},
    {"label": "Massa em ordem de marcha", "value": "940 kg (versão GT-APEX 3 portas)"}
  ],
  "caption": "Especificações de catálogo do modelo comercializado no Japão entre 1983 e 1987 [F04]."
}
:::
```

Em `:::callout`, o campo opcional `kind` aceita `intro`, `key-concept`, `curiosity`, `backstage`, `note` ou `info`. Todos os textos de `pullquote`, `callout`, `factbox` e `credit` são inspecionados mecanicamente no PDF (`missing_text`, `out_of_bounds`, `invisible_text`) e incluídos em `editorial-text.txt`.

Tabelas podem continuar em várias páginas, com repetição do cabeçalho. Código
mantém os caracteres literais e usa fonte monoespaçada; linhas longas demais
exigem quebra explícita na fonte. Tabs são exibidos em paradas de quatro espaços.

## Saídas e inspeção

| Artefato | Conteúdo |
|---|---|
| `document.pdf` | Documento composto, com fontes incorporadas e navegação. |
| `previews/page-NNN.png` | Rasterização real de cada página, a 108 DPI. |
| `editorial-text.txt` | Texto extraído do PDF para cotejo e revisão. |
| `layout.json` | Regiões efetivamente desenhadas, IDs de blocos, perfil e fontes. |
| `manifest.json` | Hashes da fonte, do PDF e do layout, sem caminhos fixos da máquina. |
| `inspection.json` | Resultado mecânico, páginas, erros, prévias e vínculos de hashes. |

A inspeção usa PDFium para ler glifos e pixels e pypdf para navegação, links e
fontes. Ela verifica:

- preservação dos textos autorais nas regiões correspondentes, inclusive células;
- contagem dos operadores em fórmulas e código, preservando valores Unicode;
- glifos fora da área permitida, texto sem contraste/tinta e transparência;
- tinta e objetos vetoriais das figuras, com controle de cada barra;
- regiões sobrepostas, inventário de páginas, sumário e links;
- alinhamento das linhas elegíveis de prosa justificada e incorporação das fontes.

O relatório também registra limites: normaliza espaços para cotejo textual,
não atribui significado a diagramas e não prova qualidade editorial.
Fundos complexos podem interferir na inspeção de contraste; as prévias continuam
obrigatórias na revisão humana/por agentes. Documentos curtos podem não ter
linhas suficientes para avaliar estatisticamente a justificação.
O MVP limita a fonte a 5 MiB, 10 mil blocos, figuras/tabelas a 12 rótulos/colunas,
o PDF a 100 MiB e a inspeção a 300 páginas. A chamada da extensão tem limite de
três minutos; o comando de terminal permite composições mais demoradas dentro
dos limites de tamanho. Imagens externas, LaTeX completo e PDF/UA não estão
implementados.

## Integração com o gate

Nas novas entregas com PDF, liste o PDF em `brief.deliverables` e registre
`pdf_engine: reportlab-v1`. Antes do gate, o consolidado inclui:

```json
{
  "pdf_inspections": [
    {
      "source": {"path": "output/documento.md", "sha256": "<hash real>"},
      "pdf": {"path": "output/pdf-cycle-01/document.pdf", "sha256": "<hash real>"},
      "manifest": {"path": "output/pdf-cycle-01/manifest.json", "sha256": "<hash real>"},
      "inspection": {"path": "output/pdf-cycle-01/inspection.json", "sha256": "<hash real>"}
    }
  ]
}
```

O gate compara fonte, PDF, layout, manifesto, relatório, texto extraído e todas
as prévias pelos hashes. Falhas mecânicas bloqueiam; ausência ou mudança dos
registros invalida o vínculo. O contrato editorial continua independente,
incluindo o PDF e o texto revisados. Regerar o PDF exige nova validação e revisão
pertinente, não apenas atualizar o campo hash.

## Migração e comprovação

O antigo `render.py` de cada entrega tinha caminhos, fontes e diagramas
específicos. O novo motor centraliza a composição. Migre as palavras para
Markdown e os diagramas para blocos com `kind` explícito; não mantenha prosa
duplicada no código gerador. As verificações são reutilizáveis e não dependem
de nomes específicos nem de contagens fixas de capítulos.

```powershell
python -m unittest discover -s tests -v
.\.venv-pdf\Scripts\python.exe -m unittest tests.test_pdf_engine -v
node --test .\.github\extensions\document-swarm-pdf\tests\tool.test.mjs
```

Sem dependências opcionais, os testes de PDF são identificados como não
executados; os checks principais continuam disponíveis. O aceite do motor exige
executar a suíte opcional. Ela gera os dois perfis com texto longo, tabelas de
várias páginas, código, acentos, links e diagramas e insere defeitos em PDFs:
remoção de texto/página, perda ou alteração de operador, texto branco invisível,
glifos fora da margem, célula omitida, prosa sem justificação e apagamento de uma
barra. Os testes exigem o diagnóstico
correspondente, não apenas a detecção de que o hash mudou.

A integração também altera fonte, PDF, PNG, manifesto, layout e inspeção após
uma aprovação de fixture: gate, relatório, monitor e memória deixam de aceitá-la.
As notas desse teste são anotações explícitas de teste, não avaliações produzidas
pelo motor. Uma nota editorial abaixo de A continua bloqueando um PDF mecanicamente
válido.
