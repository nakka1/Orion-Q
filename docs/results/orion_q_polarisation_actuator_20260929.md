# Compensador de polarização com o atuador limitado do autor — campanha v1

Data: 29/09/2026. Alvo `polarisation.compensation.v1`, modo `simulation`.
**Com o atuador real do modelo do autor entre o supervisor e a fibra, o
compensador aprendido só vence se o dispositivo for rápido: no ponto de
desenvolvimento a partir de 120 rad/s (1,18×, Holm p 0,03) e no ponto calibrado
a partir de 250 rad/s (1,21×); abaixo disso o alfa-beta ressintonizado vence em
todas as sementes (a 30–60 rad/s, 0,77–0,91×), e a 120 rad/s o ponto calibrado
empata.** A partir de ~3.000 rad/s o resultado é o do atuador ideal (4,75× e
3,33×). Antes de pontuar, o laço regenerou a varredura de velocidade do próprio
autor (4 compensadores × 9 dispositivos) com diferença ≤ 3×10⁻¹⁷. Nenhuma
política foi ativada e nenhuma ação física foi executada.

## O que entra no laço

`qnet_network.compensation.actuator.PolarisationActuator`, o modelo do autor:
cascata de três retardadores X-Y-X, decomposição de Euler mais próxima da
posição atual, velocidade finita, 4π de curso por elemento e *reset* sem fim
(3× mais rápido que o rastreamento). O comando aprovado pelo supervisor em vigor
a cada amostra de 1 ms é percorrido pelo dispositivo em ordem, exatamente como
`run_closed_loop` do autor faz; muda só o que chega à fibra. O `LoopRecord` passou
a registrar o dispositivo (velocidade, curso, *resets*, fração em *reset*,
percurso). Com `actuator=None` o laço é o de antes, bit a bit: as campanhas de
atuador ideal foram reexecutadas com o código final e deram os mesmos números.

## Portão

`external/qnet_network/runs/actuator/slew_sweep.csv` (SHA-256 `ca494315…`, do
`Qnet-Network ML.zip`): ponto de desenvolvimento, sementes 500–503, monitoramento
7000–7003, alfa-beta (0,05; 0,005), persistência, Ridge e MLP, atuador ideal e 8
velocidades. O laço do ORION-Q com os *checkpoints* exportados reproduziu as 36
QBER médias com diferença ≤ 2,8×10⁻¹⁷ e os percursos com diferença ≤ 2×10⁻¹².
Isso confirma, de passagem, que os pesos exportados do ponto de desenvolvimento
são os do `main.pkl` usado no manuscrito.

## Protocolo congelado

`configs/optional_models/polarisation_actuator_v1.json`, SHA-256
`ac93fd8c9e51911faa68e1c5f2acd5c928744819c4d7c6b5a5320f1128847433`. Os dois
pontos de operação, cada um com os seus *checkpoints*; atuador ideal e 30, 60,
120, 250, 500, 1.000, 3.000 e 10.000 rad/s; 20 sementes novas (1000–1019), 20 s.
O alfa-beta foi **ressintonizado para cada ponto e cada dispositivo** (grade 8 × 4
nas sementes 100–103; o autor usou uma sintonia só), o que dá ao filtro a chance
de se adaptar a um dispositivo lento. Teste bilateral (a direção pode inverter),
Holm sobre as 36 comparações modelo × alfa-beta.

## Resultados (QBER média, 20 sementes)

| Dispositivo | Desenv.: alfa-beta | Ridge | MLP | Razão (Ridge) | Calibr.: alfa-beta | Ridge | MLP | Razão (Ridge) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 30 rad/s | **0,0342** | 0,0443 | 0,0440 | 0,77 | **0,0435** | 0,0477 | 0,0480 | 0,91 |
| 60 rad/s | **0,0308** | 0,0393 | 0,0392 | 0,78 | **0,0435** | 0,0477 | 0,0480 | 0,91 |
| 120 rad/s | 0,0360 | **0,0304** | 0,0299 | 1,18 | 0,0435 | 0,0449 | 0,0452 | 0,97 (empate) |
| 250 rad/s | 0,0369 | **0,0201** | 0,0198 | 1,83 | 0,0436 | **0,0361** | 0,0368 | 1,21 |
| 500 rad/s | 0,0368 | **0,0143** | 0,0143 | 2,57 | 0,0436 | **0,0265** | 0,0268 | 1,64 |
| 1.000 rad/s | 0,0368 | **0,0110** | 0,0108 | 3,35 | 0,0436 | **0,0193** | 0,0196 | 2,26 |
| 3.000 rad/s | 0,0361 | **0,0076** | 0,0072 | 4,75 | 0,0436 | **0,0131** | 0,0134 | 3,33 |
| 10.000 rad/s | 0,0361 | **0,0076** | 0,0072 | 4,75 | 0,0436 | **0,0131** | 0,0134 | 3,33 |
| ideal | 0,0361 | 0,0076 | 0,0072 | 4,75 | 0,0436 | 0,0131 | 0,0134 | 3,33 |

Razão = QBER do alfa-beta / QBER do Ridge. Veredito do teste (Holm, α = 0,05):
alfa-beta melhor a 30 e 60 rad/s nos dois pontos (0 de 20 sementes para o
modelo); modelo melhor a partir de 120 rad/s no desenvolvimento (16/20, p de Holm
0,030; MLP 15/20, 0,024) e a partir de 250 rad/s no calibrado (18–19/20, p de Holm
6,9×10⁻⁵); sem diferença significativa a 120 rad/s no calibrado (5/20, p 0,07).

**Mecanismo.** Um dispositivo lento filtra o comando como um passa-baixas. O
preditor pede o movimento necessário para seguir a vibração — no ponto calibrado,
~15.600 rad em 20 s a 10.000 rad/s, contra ~640 do alfa-beta —, e um atuador que
não chega lá entrega uma correção atrasada e parcial, pior que a do filtro, que
quase não se move. O ponto calibrado (60 Hz com harmônicos em 120 e 180 Hz) exige
um dispositivo mais rápido que o de desenvolvimento (40 Hz), como o cálculo de
2πfA prevê. Os *resets* disparam muito com o preditor (até ~870 por minuto) mas
ocupam ≤ 3% do tempo e não mudam a ordem, como o autor registrou.

**Comparação com o manuscrito.** O autor mediu no ponto de desenvolvimento, com o
alfa-beta fixo e 4 sementes, empate a 120 rad/s e vantagem estabelecida a 250.
Com 20 sementes novas e o filtro ressintonizado por dispositivo, a vantagem já é
significativa a 120 rad/s no desenvolvimento; no calibrado, que o autor não
mediu, o cruzamento fica entre 120 e 250 rad/s. A conclusão prática é a mesma do
manuscrito, agora nos dois pontos: o compensador aprendido exige um controlador
de polarização piezoelétrico rápido ou de niobato de lítio (centenas de rad/s ou
mais); com um retardador motorizado, o filtro clássico é a escolha certa.

O supervisor limitou 27 comandos em 1.440 execuções e não registrou falhas.

## Como reproduzir

```
python tools/run_polarisation_actuator.py --qnet-zip F:\qnet_network.zip --output outputs\polarisation-actuator-v1 --workers 4
```

Resumo: [`orion_q_polarisation_actuator_20260929_summary.json`](orion_q_polarisation_actuator_20260929_summary.json).
Na nuvem levou ~2,5 h com dois processos e a máquina compartilhada.

## Limites

- O dispositivo é o modelo simulado do autor, não um controlador caracterizado.
- Os pesos foram treinados com atuador ideal; não foram retreinados por
  dispositivo (um preditor que pede menos movimento poderia ganhar abaixo do
  cruzamento).
- Execuções de 20 s; efeitos de curso que exigem deriva acumulada longa ficam
  fora (o autor mediu à parte que o *reset* quase não pesa num dispositivo
  rápido).
