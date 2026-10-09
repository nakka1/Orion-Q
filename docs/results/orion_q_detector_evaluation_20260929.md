# Detector de ataques QKD sem *pickle* — exportação segura e campanha v1

Data: 29/09/2026. Modelo `qkd.detector.boosting`, modo `offline_monitoring`.
**O detector do autor, que o ORION-Q mantinha bloqueado por vir só como *pickle*,
agora roda na plataforma a partir de uma exportação provada idêntica, sem que o
arquivo do autor seja desserializado. Antes de pontuar, a réplica reproduziu byte a
byte as sete sessões de exemplo exportadas (entrada, gabarito, saída e resumo) e,
com erro 0,0, as 240 métricas por classe de `scores.csv`, as tabelas do divisor de
feixe e dos casos difíceis, a matriz de confusão e as duas calibrações de
`settings.json`. Em dois conjuntos novos de sessões (288 episódios cujos fluxos
aleatórios nenhum treino ou teste do autor usou), o detector implantado marcou 84% e
89% dos períodos com evento, contra 7% e 14% do alarme de QBER (110 e 111
vitórias em 118 e 119 episódios; p de Holm 5×10⁻²⁴), com F1 macro 0,86 e 0,89 e 3,1
e 4,0 alarmes falsos por hora.** O ganho é desigual: *intercept-and-resend* é a
classe fraca (45% e 68% dos períodos, 7 derrotas por conjunto). Nenhuma política
foi ativada e nenhuma ação física foi executada.

## Exportação segura

O pacote fixado (`modelo_qkd_ml.zip`) traz o detector só como
`modelos/detector_boosting.pkl`. `tools/export_detector_safe.py` o reconstrói com o
pipeline do próprio autor — `generate_qkd_session_dataset` com a semente 0 e o ponto
de `task5.py`, releitura do CSV (como fez a execução do autor), atributos, divisão por
episódio, `fit_boosted` com semente 0 e calibração do limiar de decisão a 0,5% de
alarme falso — num processo à parte, serializa o resultado como o `export_models.py`
serializou, e compara os bytes com o membro do ZIP: **idênticos** (SHA-256
`900d4d16…`). O membro nunca é aberto como objeto. Só então as 2.400 árvores (400
rodadas × 6 classes, 96.858 nós) são gravadas como *arrays* numéricos, sem objetos, em
`src/quantum_autopilot/model_intake/derived/qkd_detector_boosting_v1.npz` (SHA-256
`5a7442e9…`, fixado em `model_intake/detector.py`), e um percurso em NumPy é conferido
contra o scikit-learn nas 26.460 linhas de teste: diferença máxima 0,0. A prova de
bytes exige o ambiente do autor (Python 3.11, NumPy 2.4.4, pandas 3.0.2,
scikit-learn 1.8.0), registrado no manifesto; a leitura do NPZ não exige nada disso.

`SafeDetector` carrega o NPZ com `allow_pickle=False`, confere hash, tipos e ligações
das árvores, e se vincula ao pacote revisado conferindo o SHA-256 do membro *pickle*.
`SafeAttackDetector` expõe a interface `AttackDetector` do autor, de modo que as
próprias funções de pontuação do autor (`evaluate_detector`, `SustainedDetector`)
rodam sobre ele.

## Portões de reprodução

| Portão | Referência | Resultado |
|---|---|---|
| Sessões exportadas (7 classes) | `exemplos/detector_*_{entrada,gabarito,saida}.csv`, `detector_resumo.json` | conjunto semente 0 regenerado: entradas e gabaritos idênticos byte a byte; saídas idênticas byte a byte (`%.6g`); resumos idênticos |
| Tabela de métricas | `runs/detector/scores.csv` (SHA-256 `61d0966a…`) | 4 detectores × 6 classes × 10 colunas = 240 valores, erro máximo 0,0 |
| Divisor de feixe | `beam_splitting.csv` | 12 valores, erro 0,0 |
| Casos difíceis | `hardness.csv` (degrau, rampa, intermitente, com e sem excursão térmica) | 45 valores, erro 0,0 |
| Confusão do *boosting* | `confusion.csv` | idêntica byte a byte |
| Calibrações | `settings.json` | σ do limiar de QBER = 97,15752200912328; limiar de decisão = 0,9997181057361344; histerese 3 |

Os registros do autor vêm do `Qnet-QKD ML.zip` e foram incorporados em
`external/qnet_qkd/runs/` (`tools/vendor_qnet.py --qkd-runs`), com hash por arquivo
em `external/VENDORED.json`.

## Protocolo congelado

`configs/optional_models/detector_evaluation_v1.json`, SHA-256
`22475035b093df34362bd3e0ca182f91302dbf089be3db2fe726bf5e88cc4ed2`. Dois conjuntos
novos com o gerador e o ponto do autor, 24 episódios por classe, sementes-base 1000 e
2000. O gerador dá ao episódio *i* do conjunto *s* o fluxo aleatório *s + i*: as
execuções do autor usaram 0–167, e uma semente-base 1 repetiria os episódios de treino
deslocados de um (uma primeira versão deste protocolo caiu nisso e foi descartada
antes de qualquer registro). Os fluxos de avaliação são 1000–1167 e 2000–2167. O
limiar de QBER mantém a calibração do autor, como um alarme implantado manteria.
Hipótese primária, por conjunto: nos episódios com evento (classes treináveis), o
detector implantado (*boosting* + limiar + histerese) marca como ataque uma fração
maior dos períodos com evento que o limiar de QBER; teste do sinal exato, bilateral,
Holm sobre os dois conjuntos. O resto é descritivo, com *bootstrap* por episódio
(2.000 reamostras).

## Resultados

| Conjunto | Detector | F1 macro (IC 95%) | Alarmes falsos/h (IC 95%) | Detecção | Precisão a 1% |
|---|---|---:|---:|---:|---:|
| 1000 | limiar de QBER | 0,120 | 0,8 (0,2–1,8) | 0,075 | 0,20 |
| 1000 | *boosting* | 0,886 (0,850–0,915) | 11,5 (7,2–16,2) | 0,907 | 0,17 |
| 1000 | + limiar | 0,867 (0,829–0,898) | 5,3 (2,2–8,9) | 0,859 | 0,26 |
| 1000 | **+ limiar + histerese** | **0,857 (0,819–0,889)** | **3,1 (1,4–5,0)** | 0,834 | 0,28 |
| 2000 | limiar de QBER | 0,124 | 2,1 (0,4–4,2) | 0,128 | 0,13 |
| 2000 | *boosting* | 0,908 (0,875–0,933) | 9,2 (5,2–14,1) | 0,949 | 0,15 |
| 2000 | + limiar | 0,901 (0,868–0,926) | 7,5 (3,8–11,9) | 0,921 | 0,18 |
| 2000 | **+ limiar + histerese** | **0,895 (0,861–0,921)** | **4,0 (2,0–6,2)** | 0,894 | 0,19 |
| autor (teste, semente 0) | + limiar + histerese | 0,818 | 6,8 | — | 0,22 |

144 episódios pontuados por conjunto (100.800 períodos gerados). Detecção = fração dos
períodos com evento marcados como qualquer ataque.

| Hipótese primária | Conjunto 1000 | Conjunto 2000 |
|---|---:|---:|
| Fração marcada, detector implantado | 0,837 | 0,891 |
| Fração marcada, limiar de QBER | 0,075 | 0,139 |
| Diferença (IC 95%) | +0,76 (0,69–0,83) | +0,75 (0,68–0,82) |
| Vitórias / derrotas | 110 / 8 | 111 / 8 |
| p de Holm | 5,1×10⁻²⁴ | 5,1×10⁻²⁴ |

Por classe (fração marcada, implantado contra limiar): cegamento 0,89/0,04 e
0,92/0,12; perda 0,99/0,01 e 0,99/0,07; PNS 0,88/0,04 e 0,94/0,03; deriva térmica
0,97/0,04 e 0,93/0,09; *intercept-and-resend* 0,45/0,24 e 0,68/0,39 (15 e 16
vitórias, 7 derrotas). O atraso mediano até o alarme sustentado fica entre 2,4 e 3,1 s
nas classes fortes e chega a 21 s no *intercept-and-resend* do conjunto 1000.

**Leitura.** A afirmação central do autor se mantém em dados novos: o alarme de QBER
vê um de cada dez períodos de ataque, o detector vê de 84% a 89%, e o limiar de
decisão com a histerese custa cerca de dois segundos de atraso e divide os alarmes
falsos por hora por 2,3 a 3,7 em relação ao *boosting* puro. Os números absolutos são melhores que os da tabela do autor (F1 0,86–0,89
contra 0,82; 3–4 alarmes/h contra 6,8), e o valor do autor fica fora do intervalo do
F1 nos dois conjuntos: o teste do autor tinha 7 episódios por classe, e a diferença
está no tamanho da amostra, não num erro — os portões reproduziram aquela tabela
exatamente. A classe *intercept-and-resend* é a que menos se beneficia, porque o
ataque eleva a QBER, justamente o que o limiar vê.

**Divisor de feixe (diagnóstico posterior, fora do protocolo).** O detector chamou de
"normal" só 57% e 54% dos períodos de divisor de feixe, contra 91% no teste do autor.
Separando pelos episódios com excursão térmica simultânea (o gerador pode sortear uma
para cada ataque): sem excursão, 98% e 95% "normal" (96% no teste do autor); com
excursão, 83% e 86% viram "deriva térmica", a resposta física certa para a temperatura
que mudou. Os conjuntos novos têm 9 episódios de divisor de feixe com excursão entre
24; o teste do autor tinha 1 entre 7. O ataque continua invisível, como o autor afirma.

## Produto

O detector deixou de ser "bloqueado" no catálogo: `open_optional_model` devolve o
`SafeDetector`, e a sessão offline (`DetectorSession`, contrato
`orion.optional-detector-input.v1`) classifica um registro de sessão simulado com as
17 colunas públicas dos exemplos do autor, contra o alarme de QBER na calibração do
autor. Ver [catálogo](../product/orion_q_optional_models.md).

## Como reproduzir

```
python tools/export_detector_safe.py --qkd-zip modelo_qkd_ml.zip      # ambiente do autor; só para refazer a prova
python tools/run_detector_evaluation.py --qkd-zip modelo_qkd_ml.zip --output outputs\detector-evaluation-v1
```

Resumo: [`orion_q_detector_evaluation_20260929_summary.json`](orion_q_detector_evaluation_20260929_summary.json).
Cerca de 4 minutos na nuvem.

## Portabilidade (01/10)

Na execução oficial no Windows, as entradas dos sete exemplos exportados diferiram no
último bit da temperatura simulada (3,6×10⁻¹⁵, biblioteca matemática da plataforma),
com gabaritos, saídas, resumos e todas as tabelas do autor idênticos. O portão passou a
comparar as entradas valor a valor com a tolerância de 10⁻¹² do protocolo, relativa ao
valor acima de 1 (a taxa de chave de entrada passa de 10⁶ Hz); gabaritos,
saídas e resumos continuam exigindo igualdade de bytes, e no Linux as entradas seguem
idênticas (erro 0). A campanha foi repetida na nuvem com o portão portável: os mesmos
resultados, com o campo novo `input_max_relative_error` em cada exemplo
([validação no Windows](orion_q_windows_validation_20261001.md)).

## Limites

- Sessões simuladas pelo gerador do autor; não há registros de campo rotulados com
  ataques em nenhuma fonte pública.
- Um evento por episódio, nos vãos e na configuração de transmissor de treino.
- O detector é um monitor: os alarmes são pontuados, nunca acionam nada.
- O detector em FPGA (5 rodadas, 14 bits) do pacote é outro modelo, podado e
  quantizado; não é substituto deste.
- As variáveis do autor usam uma diferença central na taxa de temperatura (um período
  à frente). Com as variáveis que um monitor em tempo real calcularia, o detector
  implantado perde 0,15 e 0,21 ponto dos períodos com evento
  ([campanha de 30/09](orion_q_detector_causal_20260930.md)).
