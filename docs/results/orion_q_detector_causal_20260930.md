# Detector com variáveis de tempo real — campanha v1

Data: 30/09/2026. Modelo `qkd.detector.boosting`, modo `offline_monitoring`.
**Calculadas como um monitor em tempo real as calcularia — cada período só com o
registro até ele —, as variáveis do detector custam 0,15 e 0,21 ponto percentual dos
períodos de ataque marcados (83,5% contra 83,7%; 88,9% contra 89,1%).** A diferença
é detectável (p de Holm 0,024 e 0,001: o valor causal fica abaixo em 27 e 26
episódios e acima em 12 e 6), mas não muda nenhuma conclusão: o F1 macro do detector
implantado varia 0,001 (0,857 e 0,894), os alarmes por hora continuam entre 3 e 4,2, e
o limiar de QBER, que não usa essas variáveis, não muda. O que fica mais lento é a
deriva térmica: 0,7 a 1 período a mais de atraso, e 0,6 a 0,9 ponto a menos de
períodos marcados. Nenhuma
política foi ativada e nenhuma ação física foi executada.

## Por que esta campanha

O pipeline de variáveis do autor (`qnet_qkd.qkd.detection._episode_features`) mede a
taxa de variação da temperatura com `np.gradient`, uma diferença central: a variável
do período *t* usa a temperatura do período *t + 1*. Duas das 17 variáveis dependem
dela — `temperature_rate` e, por |dT/dt|, `error_temperature_correlation` —; todas as
janelas móveis olham só para trás. O modelo foi treinado e avaliado pelo autor assim,
e a campanha do detector (29/09) manteve o pipeline intacto. Um monitor implantado
não vê o período seguinte, então a pergunta é quanto o desempenho depende dessa
antecipação.

## Método

Para cada período *t* de cada episódio, as variáveis são calculadas pela própria
função do autor sobre o registro até *t* (`frame[:t + 1]`), e fica a última linha —
exatamente o que um monitor calcularia. Modelo, limiar, histerese, conjuntos e
métricas são os da campanha do detector; o modelo não é retreinado.

| Portão | Resultado |
|---|---|
| As 15 variáveis sem antecipação, bit a bit iguais às do autor | 336 de 336 episódios |
| O último período de cada episódio (onde as duas usam diferença unilateral) igual nas 17 | 336 de 336 |
| Conjuntos e métricas com as variáveis do autor iguais aos da campanha do detector | SHA-256 dos CSV e os quatro detectores, idênticos |

Protocolo congelado: `configs/optional_models/detector_causal_v1.json`, SHA-256
`c39e36e2630e71d2771a5c3362929839490a95e6017dbb3376dfec50794f0dba`. Conjuntos 1000 e
2000 (24 episódios por classe, os mesmos da campanha do detector). Primária: por
episódio com evento, fração dos períodos com evento marcados pelo detector implantado,
variáveis causais contra as do autor; teste do sinal exato bilateral, Holm sobre os
dois conjuntos; *bootstrap* de 2.000 reamostragens por episódio.

## Resultados

| Conjunto | Variáveis | Marcados (eventos) | F1 macro | Alarmes/hora | Atraso na deriva térmica |
|---|---|---:|---:|---:|---:|
| 1000 | do autor (com antecipação) | 0,837 | 0,857 | 3,06 | 2,7 s |
| 1000 | causais | 0,835 | 0,857 | 2,97 | 3,7 s |
| 2000 | do autor (com antecipação) | 0,891 | 0,895 | 4,02 | 3,1 s |
| 2000 | causais | 0,889 | 0,894 | 4,18 | 3,8 s |

Diferença média (causal − autor): −0,0015 (IC 95% −0,0041 a 0,0004) e −0,0021
(−0,0036 a −0,0007). Por classe, a deriva térmica perde 0,9 e 0,6 ponto
(96,6% → 95,6%; 93,1% → 92,5%); cegamento, PNS, perda e *intercept-and-resend* mudam
menos de 0,4 ponto. O *gradient boosting* sem limiar nem histerese sofre mais
(16,3 contra 11,5 alarmes por hora no conjunto 1000): a histerese do detector
implantado absorve o ruído extra da diferença unilateral.

**Leitura.** A antecipação existe e é mensurável, mas o resultado do detector não
depende dela: os 84% e 89% da campanha do detector valem para um monitor em tempo
real com 0,2 ponto a menos. Para implantar, o recomendável é retreinar com a
diferença unilateral — o modelo aqui foi treinado com as variáveis antecipadas —, o
que é uma mudança de uma linha no pipeline do autor e fica registrada como trabalho
futuro, não feita aqui para não alterar o modelo recebido.

## Como reproduzir

```
python tools/run_detector_causal.py --qkd-zip modelo_qkd_ml.zip --output outputs\detector-causal-v1 --workers 2
```

Resumo: [`orion_q_detector_causal_20260930_summary.json`](orion_q_detector_causal_20260930_summary.json).
Na nuvem, 41 minutos com dois processos (cerca de 90 mil chamadas da função do autor
por conjunto).

## Limites

- Sessões do gerador do autor; sem dados de campo rotulados.
- O modelo foi treinado com as variáveis antecipadas e não é retreinado; o custo
  medido é o de implantar o modelo recebido como está.
