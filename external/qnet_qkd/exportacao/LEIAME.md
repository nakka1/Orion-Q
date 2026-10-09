# Modelos de ML para QKD com *decoy states* — inferência

Os três modelos do artigo *"Aprendizado de máquina sobre observáveis públicos de
QKD"* (A. O. S. Santos), prontos para uso fora do repositório `qnet_autopilot`:

| modelo | o que faz | arquivo |
|---|---|---|
| **Detector de ataques** | classifica cada período de um enlace em `normal`, `thermal_drift`, `loss_event`, `intercept_resend`, `pns` ou `detector_blinding` | `modelos/detector_boosting.pkl` + `detector_config.json` |
| **Agente de taxa de chave** | escolhe, a cada período, um de nove ajustes em μ, ν, clock e fração de pulsos de sinal | `modelos/agente_taxa_chave.npz` + `agente_config.json` |
| **Preditor de sincronismo** | prevê o atraso de chegada do pulso 2 s adiante, para posicionar a janela do detector | `modelos/sincronizador_ridge.json` |

`inferencia.py` depende só de **numpy, pandas e scikit-learn** — não importa o
pacote original. O construtor de atributos, a rede do agente, o vetor de
observação e os limites de hardware foram portados com os mesmos nomes e a mesma
aritmética, e o script que gerou este pacote (`scripts/export_models.py` no
repositório) roda cada exemplo **também** no pacote original e aborta se houver
qualquer divergência. Os números em `exemplos/` são, portanto, os do pacote.

## Instalar e rodar

```bash
pip install -r requirements.txt
python exemplo.py
```

`exemplo.py` carrega os três modelos, roda sobre os exemplos, imprime o resultado e
confere contra as saídas gravadas. O `scikit-learn` está fixado em **1.8.0**
porque o detector é um objeto serializado com `pickle`, que só carrega com
garantia na mesma versão em que foi salvo.

## 1. Detector de ataques

```python
import pandas as pd
from inferencia import AttackDetector

detector = AttackDetector.load("modelos")
saida = detector.classify_session(pd.read_csv("exemplos/detector_pns_entrada.csv"))
```

**Entrada:** o registro de um enlace, uma linha por período de acerto (1 s), com
as colunas abaixo. Os **60 primeiros períodos são o comissionamento**: todo
atributo é medido em relação à mediana deles, e eles não recebem classificação.
Portanto a sessão precisa ter mais de 60 linhas, e o comissionamento precisa ser
um trecho sem evento.

| coluna | unidade | o que é |
|---|---|---|
| `period`, `time_s` | —, s | índice e instante do período |
| `signal_gain`, `decoy_gain`, `vacuum_gain` | fração | ganho $Q$ em cada intensidade |
| `signal_error`, `decoy_error`, `vacuum_error` | fração | taxa de erro $E$ em cada intensidade |
| `single_photon_yield_lower` | fração | cota inferior $Y_1^L$ |
| `single_photon_error_upper` | fração | cota superior $e_1^U$ |
| `key_rate_hz` | bit/s | taxa de chave provável do período |
| `temperature_c` | °C | termômetro no cabo |
| `expected_gain` | fração | ganho de sinal previsto pela calibração de perda |

**Saída:** uma linha por período a partir do 60º.

| coluna | o que é |
|---|---|
| `argmax` | classe mais provável, sem limiar |
| `attack_probability` | $1 - P(\text{normal})$ |
| `per_period` | classe após o limiar calibrado (P(ataque) ≥ 0,9997, só suprime) |
| `alarm` | **o que o operador vê**: `per_period` só depois de 3 períodos seguidos iguais |
| `p_<classe>` | probabilidade de cada classe |

**Os exemplos** (sessões retidas do teste; `*_gabarito.csv` traz a resposta):

| classe verdadeira | vão | perfil | início | primeiro alarme | atraso | classificado como | períodos alarmados após o início |
|---|---|---|---|---|---|---|---|
| normal | 20 km | — | — | 593 s | — | **pns (alarme falso)** | 4 de 540 |
| thermal_drift | 20 km | degrau | 335,9 s | 338 s | 2,1 s | thermal_drift | 93% |
| loss_event | 50 km | degrau | 460,2 s | 463 s | 2,8 s | loss_event | 99% |
| intercept_resend | 70 km | degrau | 504,9 s | 507 s | 2,1 s | intercept_resend | 98% |
| pns | 35 km | rampa | 228,0 s | 231 s | 3,0 s | pns | 99% |
| detector_blinding | 20 km | intermitente | 530,1 s | 533 s | 2,9 s | detector_blinding | 62% |
| beam_splitting | 35 km | rampa | 190,3 s | — | — | normal | 0% |

O alarme falso na sessão normal foi mantido de propósito: é a taxa de ~7 alarmes
por hora que o artigo reporta, vista de perto. A divisão de feixe ser chamada de
`normal` é o resultado correto — esse ataque não deixa assinatura física, e o
artigo o usa como controle negativo.

## 2. Agente de taxa de chave

```python
import numpy as np
from inferencia import KeyRateAgent, PeriodObservables, TransmitterSettings, keyrate_observation, ACTIONS

agente = KeyRateAgent.load("modelos")
obs = keyrate_observation(periodo_anterior, parametros_atuais,
                          calibration_gain=..., calibration_rate_hz=...,
                          dark_count_probability=..., misalignment_error=...,
                          previous_rate_hz=periodo_anterior.key_rate_hz)
acao = agente.act(obs, np.random.default_rng(0))        # amostra a T = 0,25
novos = agente.apply(parametros_atuais, acao)           # respeita os limites de hardware
```

**Entrada:** os observáveis publicados no período anterior (`PeriodObservables`),
os parâmetros atuais do transmissor (`TransmitterSettings`) e quatro números do
comissionamento do enlace. `keyrate_observation` monta com eles o vetor de 16
números em que a rede foi treinada; a ordem está em `agente_config.json`.

**Saída:** probabilidades das nove ações, a ação escolhida e os novos
parâmetros, já limitados à caixa de hardware (μ ∈ [0,05; 1], ν ∈ [0,01; 0,4],
ν ≤ 0,6 μ, clock ∈ [0,1; 5] GHz, fração de sinal ∈ [0,40; 0,85]).

**O exemplo** `agente_episodio_50km.json` traz 40 períodos de um episódio de teste
a 50 km: para cada um, a entrada, o vetor de observação, as probabilidades, a
ação gulosa, a ação amostrada e os bits de chave que o simulador produziu. No
artigo, o agente foi avaliado **amostrando a temperatura 0,25**, que é o padrão
de `act`; passe `temperature=0` para a escolha gulosa.

## 3. Preditor de sincronismo

```python
from inferencia import SyncPredictor
sync = SyncPredictor.load("modelos")
previsto_s = sync.predict(ultimos_8_offsets_s, ultimas_8_temperaturas_c)
```

**Entrada:** as 8 últimas estimativas de atraso (s), uma por segundo, e a
temperatura do cabo nos mesmos instantes. **Saída:** o atraso previsto 2 s depois
da última estimativa. Nos primeiros 7 segundos de uma sessão não há janela
completa; no pacote original esse intervalo é coberto por um filtro alfa-beta,
que não faz parte deste modelo.

**O exemplo** `sincronizador_saida.csv` traz 593 previsões sobre uma trajetória de
teste de 50 km, com o atraso verdadeiro 2 s depois e o erro.

## Limites que você precisa conhecer

- **Todos os modelos foram treinados em simulação.** O detector nunca viu dado
  de um enlace real atacado — esse dado não existe publicamente. A validação com
  a rede de Cambridge (Seção 10 do artigo) confirma a física e a premissa do
  detector, não o detector em si. Use-o como ponto de partida para retreinar com
  dados do seu enlace.
- **O preditor de sincronismo não supera repetir a última medida.** Refazendo o
  laço fechado do artigo (50 km, janela de 500 ps, 12 trajetórias de 1 h de
  teste) com essa alternativa trivial incluída: eficiência média 0,9638 com o
  modelo contra 0,9637 repetindo a última medida; resíduo médio 45,21 ps contra
  45,27 ps. O modelo explica 0,14% da variância do que prevê. Neste regime o
  atraso deriva muito menos em 2 s do que o ruído de uma estimativa, e o que
  importa é reestimar a cada segundo, não prever. O modelo está incluído porque é
  o do artigo; na prática, a última medida faz o mesmo.
- **O 14º atributo do agente repete o 8º.** A documentação do pacote descreve o
  atributo 13 (índice a partir de 0) como a variação da taxa desde o período
  anterior, mas o ambiente guarda a taxa do próprio período observado, então os
  dois são sempre iguais e a observação não carrega memória. A política foi
  treinada assim e é reproduzida assim — passe `previous_rate_hz =
  periodo_anterior.key_rate_hz`.
- **A calibração do limiar depende do período de acerto.** O limiar e a histerese
  foram calibrados a 1 s. A 5 s o artigo reporta precisão muito maior, mas com
  outro limiar; não reutilize este a outra taxa de decisão.

## FPGA

`modelos/fpga/` contém os artefatos verificados em hardware simulado:

- `detector_5rodadas_14b/` — o detector podado a 5 rodadas, em ponto fixo de 14
  bits: vetor de nós (`nodes.hex`), tabela de árvores, linha de base e o
  cabeçalho de elaboração. Cossimulado com memória síncrona sem nenhuma
  divergência de escore. **Atenção:** esta é a versão podada (F1 0,822 contra
  0,830), sem o limiar calibrado; é a que cabe em 9,4 kB de BRAM.
- `sincronizador_12b/` — ROM de coeficientes em 12 bits.
- `rtl/` — o Verilog dos dois módulos, os *testbenches* e os *tops* de síntese.

Para regenerar ou cossimular, use os scripts `cosimulate_*.py` e `synthesise.py`
do repositório.

## Arquivos

```
inferencia.py        código de inferência (numpy, pandas, scikit-learn)
exemplo.py           roda e confere os exemplos
requirements.txt
modelos/             pesos, configurações e artefatos de FPGA
exemplos/            entradas, gabaritos e saídas
```
