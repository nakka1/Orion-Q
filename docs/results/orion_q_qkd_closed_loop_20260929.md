# Ator de taxa de chave e sincronizador em laço fechado — campanha QKD v1

Data: 29/09/2026. Alvos `qkd.keyrate.v1` e `qkd.sync.v1`, modo `simulation`.
**Os dois modelos QKD agora operam em laço fechado sobre as plantas do próprio
autor, e o ORION-Q reproduz, pela sua réplica, as duas tabelas publicadas (15 de
15 valores da taxa de chave, com média e desvio; 24 de 24 do sincronismo) e o
episódio exportado de 40 períodos bit a bit.** Em sementes novas, o ator supera o
comissionamento estático em 13,6–20,8% de 25 a 65 km (p de Holm ≤ 4×10⁻⁴) e não
tem vantagem significativa a 80 km (+8,2%, p = 0,10); o sincronizador supera o
filtro alfa-beta nas oito configurações, em 20 de 20 execuções, com margem que vai
de 5,1% (janela estreita, detector ruidoso) a praticamente zero (janela de 1 ns).
Nenhuma política foi ativada e nenhuma ação física foi executada.

## O que mudou

As campanhas shadow de 24–26/09 avaliavam o ator sobre observáveis de um produtor
artificial do ORION-Q e o sincronizador em janelas avulsas. Aqui a planta é o
código que gerou os pesos:

- **Planta de taxa de chave:** `qnet_qkd.qkd.keyrate.KeyRateEnv` — o pacote do autor
  incorporado ao repositório (`src/qnet_qkd`, fonte que produziu o
  `modelo_qkd_ml.zip` fixado) — no ponto de referência do autor
  (`scripts/task5.py`: 50 km nominais, 1 dB de perda extra, acoplamento 0,85,
  detector 0,7, 10 Hz de escuro, desalinhamento 1,5%, tempo morto 50 ns,
  pós-pulso 1%, períodos de 1 s, 240 por episódio). O ORION-Q transcreve essas
  constantes e o teste confere com o script do autor.
- **Laço de taxa** (`control_plane/keyrate_loop.py`): a cada período o ORION-Q lê só o
  que o receptor publica (ganhos e erros do período anterior, com as cotas de
  isca) e a calibração de comissionamento — nunca a perda ou o desalinhamento
  ocultos —, pede à réplica revisada (`KeyRateReplayModel`, T = 0,25, gerador
  explícito) um dos nove ajustes limitados e o transmissor executa o índice. O
  laço confere, bit a bit, que os parâmetros que a planta adotou são os que o
  ORION-Q propôs; se não forem, a execução para. Uma proposta recusada pelo
  contrato vira *manter*; após 8 recusas o ator é desligado e o transmissor
  mantém os últimos parâmetros verificados.
- **Laço de sincronismo** (`control_plane/sync_loop.py`): `ClockModel` do autor
  (≈ 40 ps/km/K), estimativa do atraso a cada 1 s com 2000 fótons, porta movida
  com 2 s de latência (`run_sync_loop`). A réplica Ridge revisada é o preditor;
  como ela recusa menos de oito amostras, as sete primeiras atualizações usam a
  regra de aquecimento do próprio autor (o alfa-beta de `LearnedSync`), registrada
  em cada execução. A pontuação é a taxa de chave que o resíduo deixa, calculada
  como na avaliação do autor.
- **Contratos:** o ator só roda com o conjunto de ações, a caixa de parâmetros e o
  tempo morto exportados, e num vão de 25–80 km (90 km é recusado); o
  sincronizador só roda com período de 1 s, horizonte de 2 s, 2000 fótons e a
  50 km, o vão em que foi treinado.

## Protocolo congelado

`configs/optional_models/qkd_closed_loop_v1.json`, SHA-256
`43d8337ff6ce15b9ee0ad1e48775426ba42688cfc3324b532563127876d46235`. Arquivo exportado
fixado: `modelo_qkd_ml.zip`, SHA-256 `94352f15…` (execução oficial, sem troca de pin).

| Item | Taxa de chave | Sincronismo |
|---|---|---|
| Sementes | 950000–950019 (20); autor: treino 0–9999, validação 700000+, teste 900000–900047 | 600–619 (20); autor: treino 0–7, teste 500–511 |
| Condições | vãos 25, 35, 50, 65, 80 km; 240 períodos de 1 s | 50 km; 1 h; janelas 200, 350, 500, 1000 ps; escuro 10 Hz e 10⁶ Hz |
| Modelo | ator 16-64-64-9, T = 0,25, `default_rng(semente)` (convenção do exemplo exportado) | Ridge de 9 entradas, janela de 8 s, horizonte de 2 s |
| Baseline | comissionamento estático; oráculo do autor só como referência | sem correção, recalibração a cada 60 s, alfa-beta |
| Hipótese | ator > estático, por vão | Ridge > alfa-beta, por janela e escuro |
| Teste | sign-flip pareado exato, unilateral; Holm sobre 5 | idem; Holm sobre 8 |

## Portões de reprodução (antes de pontuar)

1. **Episódio exportado.** O exemplo `exemplos/agente_episodio_50km.json` foi gerado
   com `env.reset(rng=900000)` e amostragem por um `default_rng(900000)` separado
   (`scripts/export_models.py`). Com os dois, o laço regenera os 40 períodos com
   ações amostradas, parâmetros adotados e bits de chave **idênticos** (zero
   divergências); observações até 5×10⁻¹⁰ e probabilidades até 5×10⁻⁷ do arquivo,
   que arredonda em 9 e 6 casas. A avaliação offline anterior registrava
   `sampled_action_parity_checked = false`; essa lacuna está fechada.
2. **Tabela publicada da taxa de chave** (tab:taxa do TCC; Tabela 7 do manuscrito
   QKD): 24 episódios de teste por vão, gerador de amostragem como na avaliação do
   autor (`KeyRatePolicy(rng=0)` recriado por vão; só o fluxo aleatório é usado).
   Os 15 pares média/desvio coincidem nas três casas publicadas: por exemplo,
   25 km — estático 3,180 (0,451), agente 3,782 (0,506), oráculo 3,817 (0,584);
   80 km — 0,489 (0,072), 0,532 (0,113), 0,713 (0,107).
3. **Tabela publicada do sincronismo** (tab:sinc): 12 execuções de 1 h por
   configuração, quatro sincronizadores. As 24 entradas coincidem nas três casas,
   incluindo a do Ridge (1,508; 1,971; 2,007; 0,998; 1,099; 0,253). Observação: o
   script do autor reajusta o Ridge a cada avaliação com ruído de medida sem
   semente (`build_sync_dataset(rng=None)`), então os coeficientes exportados
   diferem dos da tabela em até 0,013; mesmo assim os números publicados se
   reproduzem na precisão publicada.

Sem os três portões a campanha não pontua.

## Resultados — taxa de chave (20 sementes novas por vão)

| Vão | Estático | Ator | Oráculo | Ator/estático | IC 95% | Vitórias | p de Holm | Lacuna fechada |
|---|---:|---:|---:|---:|---|---:|---:|---:|
| 25 km | 3,245 | **3,761** | 3,895 | 1,159 | 1,092–1,222 | 18/20 | 3,9×10⁻⁴ | 79% |
| 35 km | 2,670 | **3,033** | 3,116 | 1,136 | 1,081–1,197 | 18/20 | 1,3×10⁻⁴ | 81% |
| 50 km | 1,855 | **2,134** | 2,208 | 1,150 | 1,096–1,215 | 18/20 | 8,6×10⁻⁵ | 79% |
| 65 km | 0,989 | **1,195** | 1,265 | 1,208 | 1,121–1,302 | 17/20 | 3,9×10⁻⁴ | 75% |
| 80 km | 0,488 | 0,528 | 0,681 | 1,082 | 0,959–1,201 | 15/20 | 0,10 | 21% |

Taxas em Mbit/s (taxa provável por período, contagens finitas). "Lacuna fechada" é
(ator − estático)/(oráculo − estático). O oráculo lê o canal verdadeiro e não é
implantável.

O resultado confirma o do manuscrito com sementes novas e mostra onde ele para:
até 65 km o ator fecha três quartos da distância ao oráculo; a 80 km a vantagem
média (+8,2%) não é distinguível de zero com 20 episódios — o manuscrito já
registrava que ali o ator fecha só 19% da lacuna. O comportamento aprendido é o
descrito pelo autor: nos vãos curtos o ator leva a isca ao piso (ν ≈ 0,011 no fim
do episódio, contra 0,1 do comissionamento) e sobe a fração de pulsos de sinal; a
80 km eleva a isca a ν ≈ 0,27. Em 24.000 períodos do ator, as 24.000 propostas
foram conferidas contra a planta, o contrato não recusou nenhuma e houve 1 período
sem chave (0 no estático).

**Falha injetada** (50 km, semente 950000, recusas do contrato nos períodos
100–107): o supervisor manteve os parâmetros em cada recusa e desligou o ator no
período 107; o episódio terminou com os últimos parâmetros verificados.

## Resultados — sincronismo (20 execuções novas de 1 h, 50 km)

| Janela | Escuro | Sem correção | Recalib. 60 s | Alfa-beta | Ridge | Ridge/alfa-beta | Vitórias | p de Holm |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| 200 ps | 10 Hz | 0,152 | 1,012 | 1,466 | **1,508** | 1,029 | 20/20 | 7,6×10⁻⁶ |
| 350 ps | 10 Hz | 0,250 | 1,413 | 1,827 | **1,855** | 1,015 | 20/20 | 7,6×10⁻⁶ |
| 500 ps | 10 Hz | 0,336 | 1,649 | 1,958 | **1,971** | 1,006 | 20/20 | 7,6×10⁻⁶ |
| 1000 ps | 10 Hz | 0,571 | 1,935 | 2,007 | 2,007 | 1,00002 | 20/20 | 7,6×10⁻⁶ |
| 200 ps | 10⁶ Hz | 0 | 0,441 | 0,950 | **0,999** | 1,051 | 20/20 | 7,6×10⁻⁶ |
| 350 ps | 10⁶ Hz | 0 | 0,555 | 1,065 | **1,099** | 1,033 | 20/20 | 7,6×10⁻⁶ |
| 500 ps | 10⁶ Hz | 0 | 0,552 | 0,960 | **0,977** | 1,017 | 20/20 | 7,6×10⁻⁶ |
| 1000 ps | 10⁶ Hz | 0 | 0,144 | 0,253 | 0,253 | 1,0003 | 20/20 | 7,6×10⁻⁶ |

Taxas em Mbit/s. O Ridge vence o alfa-beta em todas as execuções, mas a margem
só é relevante onde o autor disse que seria: janela estreita e detector ruidoso
(+5,1% a 200 ps e 10⁶ Hz). Com janela de 1 ns a vantagem é estatisticamente
consistente e praticamente nula (≤ 0,03%). O efeito grande é o do rastreamento em
si — sem correção, a chave fica 72–90% abaixo da do alfa-beta com detector
silencioso e vai a zero com detector ruidoso —, e ele não é de aprendizado de
máquina.

## Como reproduzir

```
python tools/run_qkd_closed_loop.py --qkd-zip modelo_qkd_ml.zip --output outputs\qkd-closed-loop-v1
```

~5,5 min na nuvem. Resumo:
[`orion_q_qkd_closed_loop_20260929_summary.json`](orion_q_qkd_closed_loop_20260929_summary.json)
(os registros por execução, `keyrate-runs.jsonl` e `sync-runs.jsonl`, ficam na pasta
de saída e têm o SHA-256 no resumo). Os testes `tests/test_keyrate_loop.py`,
`tests/test_sync_loop.py` e `tests/test_qkd_campaign.py` conferem as constantes e a
pontuação contra os scripts do autor; o portão do episódio exportado roda quando
`ORION_QKD_ARCHIVE` aponta para o ZIP fixado.

## Portabilidade (01/10)

Na execução oficial no Windows, a calibração do enlace do episódio exportado diferiu no
último bit (3,5×10⁻¹⁶ relativo), com ações, parâmetros e bits de chave dos 40 períodos
idênticos. O portão passou a comparar a calibração com tolerância relativa de 10⁻¹²;
ações, parâmetros e bits de chave continuam exigindo igualdade, e no Linux a calibração
segue idêntica. A campanha foi repetida na nuvem com o portão portável: os mesmos
resultados, com o campo novo `calibration_max_relative_error`
([validação no Windows](orion_q_windows_validation_20261001.md)).

## Limites

- Plantas simuladas do autor (enlace *decoy-state* com deriva de perda e
  desalinhamento; deriva térmica do atraso), não séries medidas de um enlace.
- A taxa é a taxa provável por período sobre contagens finitas, como no
  manuscrito; não há prova de segurança de chave finita.
- O sincronizador só é admitido a 50 km, o vão de treino.
- O oráculo lê o canal verdadeiro; é referência, não baseline implantável.
