# Admissão dos alocadores opcionais

22/09/2026. Pacote `qnet_network.zip`, SHA-256
`6b8563209f54a2d566f1a4229b140b75fda93555085ff2bf6bfcad8f07abd938`.
Caminho de inferência, parâmetros da planta e rotina de exemplos inspecionados;
nenhum código do ZIP foi importado/executado e nenhum pickle foi carregado.

## Resultado

| Configuração | Snapshots | Sequências | Passos sequenciais | Divergências de admissão |
|---|---|---|---|---|
| Persistência (sem previsão aprendida) | 10 | sementes 500 e 501 | 720 | 0 |
| Ridge | 10 | sementes 500 e 501 | 720 | 0 |

Foram conferidas deriva, previsão 11 x 30 e admissão por enlace em **1.440
passos sequenciais**, ou **15.840 recomendações de enlace**, mais 20 snapshots.
Desvio máximo observado nos três estágios: **zero**; tolerância declarada 1e-12.
Ridge teve cinco passos de warm-up por sequência (dez no total), preservados
como persistência identificada. Cada sequência cobre 360 observações/30 minutos.

Essas contagens são avaliações sobre observações e filas fornecidas pelo
export, não novos episódios simulados nem experimentos independentes. Não foram
recalculadas vazão clássica, chaves servidas ou vantagem de 2,7x. A persistência
continua explicitamente não aprendida; não se conclui superioridade do Ridge.

## Proteções verificadas

Testes autocontidos verificam a regra de dívida/estoque, ponderação de urgência,
fila mais antiga primeiro, prazo dentro do atraso, orçamento insuficiente,
warm-up/reset, ordem de features do Ridge e recusa sem avanço de estado.
Também verificam a fórmula interna em caso calculável à mão, queda do orçamento
com Raman, identidades de enlaces, protocolo, unidades, metadados e seleção OFF.

O leitor NPZ continua recusando infinito em pesos/arrays comuns. A exceção
explícita é `urgency_s` do stream de avaliação, onde +infinito significa sem
deadline; NaN, -infinito, strings e booleanos são recusados. JSON usa `null`.
Prazo zero/expirado é recusado pelo adaptador, não interpretado como liberação
de toda capacidade. Matrizes de admissão fora dos níveis discretos são recusadas.

## Artefatos e fronteira

276 testes passaram em 11,49 s: 50 novos do alocador e 226 dos incrementos
anteriores/barreiras de controle/evidência/comandos. Ruff passou nos arquivos
da implementação e testes. Não se executou toda a suíte histórica do repositório.
O replay integrado também reverificou os oito modelos anteriormente admitidos.

- `outputs/author_allocation_20260922/replay.json`: replay integrado de dez
  configurações, hashes de código/arquivos e limitações.
- `outputs/author_allocation_20260922/final-tests.xml`: testes e regressões.
- [Guia do alocador opcional](../product/orion_q_optional_allocator.md).

O catálogo tem dez configurações offline, todas OFF por padrão, e detector
bloqueado para pickle. A planta é a do export: não houve mudança na topologia,
fila operacional, estoque real de chaves ou política ativa do ORION-Q.
Seletor nativo, ponte de runtime, avaliação de chave finita e novo `.exe`
permanecem pendentes. O próximo passo do produto é o catálogo nativo sem ativação
implícita, mantendo a distinção entre disponível para avaliação e ativo.
