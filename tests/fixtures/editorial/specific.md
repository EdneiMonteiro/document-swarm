# Dimensionamento dos node pools do AKS

O estudo compara capacidade alocável, requests dos pods e condições de manutenção.

## Dados de produção, laboratório e premissas

Os requests dos pods são comparados à capacidade alocável. As métricas dos nós
incluem aplicações, agentes e componentes do sistema. As premissas de cada
cenário precisam ser registradas antes da comparação.

Figura 1. Capacidade alocável e soma dos requests, medidas na mesma janela.

## Condições para adoção

O cenário proposto segue para teste canário após a validação das restrições das
aplicações e do plano de reversão. Não altere produção sem essa validação.
