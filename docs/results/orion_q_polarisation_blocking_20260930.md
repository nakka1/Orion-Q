# Blocagem de chave no laço do ORION-Q — campanha v1

Data: 30/09/2026. Alvo `polarisation.compensation.v1`, modo `simulation`.
**O controlador de blocagem do autor, liquidando a chave do laço supervisionado do
ORION-Q, entrega 10,9 vezes (ponto calibrado) e 7,9 vezes (desenvolvimento) o melhor
temporizador no enlace compensado por persistência, em 20 de 20 sementes novas
(p de Holm 2×10⁻⁶), e fica a 94–95% do seu oráculo. Com os compensadores
aprendidos, em que o melhor bloco é a execução inteira, ele chega a 99,8–100,0% do
melhor temporizador, não inferior com margem de 1% nos quatro casos (p de Holm
≤ 5×10⁻⁵).** No enlace do alfa-beta (QBER perto de 4%), fica 1,7–2,0% abaixo do
bloco único, em 19 e 16 das 20 sementes: o preço de decidir sobre estimativas. Antes
disso, o laço do ORION-Q regenerou as tabelas de blocagem do próprio autor. Nenhuma
política foi ativada e nenhuma ação física foi executada.

## O que entra

O controlador é `qnet_network.physics.blocks.adaptive_blocks` com `pooling=True`, o
"adaptativo agrupado" do autor: vê só os 10% de bits que o protocolo publica para
estimar parâmetros e, a cada segmento de 0,1 s, compara encerrar o bloco, seguir com
ele ou pular o segmento; não tem constante de ajuste. Ele liquida a QBER por amostra
do laço do ORION-Q (`run_loop(..., return_qber=True)`) — a réplica revisada, o
supervisor e a reserva alfa-beta das campanhas em laço fechado —, e é comparado com
os temporizadores de 0,1 a 20 s, com o controlador sem agrupamento e com os dois
oráculos de retrospectiva do autor, todos com a mesma cota de chave finita do autor.

## Portões

| Portão | Resultado |
|---|---|
| `runs/realism/adaptive_blocks.csv` do autor: 12 sementes de teste do ponto de desenvolvimento, persistência, alfa-beta 0,05/0,005, Ridge e MLP, pelo laço do ORION-Q e pelas funções de blocagem do autor | 432 de 432 linhas, erro relativo 6,1×10⁻¹⁶ |
| `adaptive_blocks_summary.csv` | 36 linhas, 4,6×10⁻¹⁶ |
| `runs/realism/blocking_stress.csv` e resumo: MLP do autor (reconstruído dos *arrays* exportados) sob vibração de 0,3 a 1,2 rad, pelo `run_closed_loop` do autor | 24 e 4 linhas, erro 0 |
| QBER média de cada compensador nas sementes de avaliação contra as campanhas em laço fechado v1 | idêntica (2,8×10⁻¹⁷ e 0) |

## Protocolo congelado

`configs/optional_models/polarisation_blocking_v1.json`, SHA-256
`8c645c01c1408773ab89c2852d485dd014d989bcfffb66bafc202ab56c4b2844`. As 20 sementes
das campanhas em laço fechado (1000–1019), 20 s, monitoramento semente + 6000,
blocagem semente + 8000; alfa-beta com os parâmetros selecionados por aquelas
campanhas. Melhor temporizador: a duração de maior chave média nas próprias sementes
de avaliação (retrospectiva, a favor do temporizador). Primárias: persistência,
controlador acima do melhor temporizador (unilateral, Holm sobre os dois pontos);
compensadores aprendidos, controlador ≥ 0,99 × melhor temporizador (unilateral, Holm
sobre os quatro). Teste exato de inversão de sinais pareado sobre as 20 sementes.

## Resultados (chave média, bit/s, 20 sementes)

| Ponto | Compensador | QBER | Melhor temporizador | Controlador | Razão (IC 95%) | Do oráculo agrupado |
|---|---|---:|---:|---:|---:|---:|
| calibrado | persistência | 0,122 | 393 (0,1 s) | **4.294** | **10,9** (9,7–12,5) | 94,2% |
| | Ridge | 0,013 | 66.067 (20 s) | 65.933 | 0,998 (0,996–0,999) | 99,7% |
| | MLP | 0,013 | 65.668 (20 s) | 65.511 | 0,998 (0,995–1,000) | 99,7% |
| | alfa-beta | 0,044 | 35.840 (20 s) | 35.113 | 0,980 (0,973–0,986) | 97,3% |
| desenvolvimento | persistência | 0,128 | 599 (0,1 s) | **4.714** | **7,9** (7,1–8,9) | 94,5% |
| | Ridge | 0,008 | 72.742 (20 s) | 72.698 | 0,999 (0,997–1,002) | 99,7% |
| | MLP | 0,007 | 73.249 (20 s) | 73.268 | 1,000 (0,998–1,002) | 99,8% |
| | alfa-beta | 0,036 | 42.456 (20 s) | 41.748 | 0,983 (0,976–0,991) | 97,5% |

No enlace da persistência, temporizadores de 5 s ou mais não produzem chave nos dois
pontos (a QBER média, de 12% a 13%, fica acima do limiar de 11%), e com o de 0,1 s
mais de 90% dos blocos não rendem chave; o controlador sem agrupamento entrega 976 e
1.264 bit/s, e o agrupamento, que pula os segmentos ruins, multiplica isso por 3,7 a
4,4. O supervisor não registrou falhas.

**Leitura.** O resultado do autor — o controlador é a única forma de tirar chave de
um enlace mal compensado e custa pouco num enlace bom — vale no laço supervisionado
do ORION-Q e nos dois pontos. O custo num enlace bom é pequeno com os
compensadores aprendidos (até 0,25%) e de cerca de 2% com o alfa-beta: onde a QBER é moderada, o
ruído das estimativas faz o controlador fechar ou pular segmentos que valiam a pena.

## Como reproduzir

```
python tools/run_polarisation_blocking.py --qnet-zip F:\qnet_network.zip --output outputs\polarisation-blocking-v1 --workers 3
```

Resumo: [`orion_q_polarisation_blocking_20260930_summary.json`](orion_q_polarisation_blocking_20260930_summary.json).
Na nuvem, 20 minutos com três processos em duas CPUs.

## Limites

- Contabilidade de chave com a cota de chave finita do autor: a sinalização para as
  duas pontas concordarem sobre o bloco não é modelada, e não há prova de segurança
  para blocos não contíguos (o agrupamento).
- Atuador ideal; a planta do autor nos seus dois pontos de operação.
