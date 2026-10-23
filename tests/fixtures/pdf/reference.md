:::cover-art

# Capacidade e operação de uma plataforma

## Referência visual para composição de documentos

Este documento sintético contém prosa, tabelas, fórmulas e figuras para
verificar a composição. Os textos e os números pertencem à fixture de
desenvolvimento; não descrevem uma infraestrutura de cliente.

Uma composição legível preserva palavras, operadores e condições. As duas
versões visuais usam esta mesma fonte sem criar títulos, legendas ou recomendações.

:::pagebreak

# Sumário

:::toc

# 1. Critérios e condições do exemplo

O relatório compara capacidade, demanda e reserva operacional em um cenário
inteiramente sintético. A comparação serve para exercitar a paginação e a
leitura de condições que precisam permanecer explícitas durante a composição.
O texto justificado deve ocupar a largura do bloco nas linhas intermediárias,
sem esticar a última linha do parágrafo. A recomendação do exemplo não depende
da cor de uma legenda nem de uma palavra removida durante a impressão.

O leitor encontra as definições antes da aplicação. Capacidade útil é o recurso
disponível depois das reservas declaradas no próprio exemplo. Demanda é o
volume indicado para a comparação. A função usada no cálculo aparece como
texto autoral, com seus operadores preservados. Números, unidades e símbolos
não podem ser substituídos silenciosamente para caber na página.

## Condições de adoção

- A comparação usa a mesma unidade para demanda e capacidade.
- A reserva operacional permanece explícita.
- Uma nova medição exige reavaliar o cenário.

Consulte a [documentação do Python](https://docs.python.org/3/) para a sintaxe
do código literal apresentado. O link é parte da fonte, não conteúdo criado
pelo compositor.

:::figure comparison
{
  "kind": "bars",
  "title": "Capacidade e demanda do cenário sintético",
  "caption": "Valores em unidades sintéticas. A figura compara a mesma base e não representa dados de produção.",
  "labels": ["Capacidade", "Demanda", "Reserva"],
  "values": [100, 65, 20],
  "display_values": ["100", "65", "20"],
  "unit": "Unidades sintéticas"
}
:::

# 2. Fórmula e código literal

Os operadores abaixo precisam continuar distintos: maior que, menor que,
maior ou igual, menor ou igual e diferente. A expressão usa a notação fornecida
na fonte; o motor não resolve a equação nem escolhe seu resultado.

:::formula capacity
{
  "expression": "N ≥ ⌈D / C⌉ + R\nC > 0 ∧ D ≥ 0 ∧ R ≥ 0",
  "caption": "N é a quantidade calculada; D, a demanda; C, a capacidade por unidade; R, a reserva. A aplicação depende das condições explícitas."
}
:::

```python
def capacity(demand, useful, reserve):
    if useful <= 0 or demand < 0:
        raise ValueError("capacidade inválida")
    return demand / useful + reserve  # preserve + / <= <

assert capacity(20, 10, 1) != 0
text = "<tag> & ação, medição, revisão"
```

Um trecho literal como `left <= right and right != 0` não deve perder sinais
por ser confundido com marcação HTML. As palavras **atenção**, *condição* e
**revisão** preservam seus acentos e estilos.

:::figure workflow
{
  "kind": "flow",
  "title": "Sequência de validação do exemplo",
  "caption": "Os rótulos e a ordem pertencem à fonte. As setas mostram a sequência declarada.",
  "labels": ["Preparar dados", "Comparar condições", "Validar resultado", "Registrar decisão"]
}
:::

# 3. Inventário tabular

A tabela reúne descrições longas para exercitar quebra de linha dentro das
células. Quando a tabela continua em outra página, o cabeçalho deve reaparecer
sem substituir, omitir ou duplicar linhas de dados.

| Componente | Condição | Resultado esperado |
|---|---|---|
| Serviço de entrada | Dados disponíveis na mesma janela de observação. | Comparação com unidades e condições explícitas. |
| Processamento | Reserva operacional incluída no cenário sintético. | Capacidade útil separada da capacidade nominal. |
| Persistência | Ausência de escrita em ambientes de cliente. | Fixture reproduzível com dados inteiramente locais. |

:::figure layers
{
  "kind": "layers",
  "title": "Elementos do cenário sintético",
  "caption": "A figura representa composição conceitual. Nenhum nome ou dado de cliente é utilizado.",
  "labels": ["Plataforma", "Grupo de execução", "Unidade de trabalho", "Processo"]
}
:::

# 4. Condições para concluir

A inspeção mecânica verifica o PDF gerado, e não apenas os parâmetros pedidos.
Textos, fórmulas, tabelas e figuras precisam aparecer dentro da área de leitura.
Essa inspeção não atribui nota editorial: a redação e a adequação ao público
continuam sob responsabilidade dos revisores.
