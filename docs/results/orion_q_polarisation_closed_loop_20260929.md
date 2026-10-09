# Compensação de polarização em laço fechado supervisionado — campanha v1

Data: 29/09/2026. Alvo de controle `polarisation.compensation.v1`, modo `simulation`.
**Os dois compensadores aprendidos do ponto calibrado vencem os três baselines em 20
de 20 sementes novas; o Ridge fica 3,33 vezes abaixo do filtro alfa-beta
ressintonizado (IC 95% 3,24–3,42), com p de Holm 5,7×10⁻⁶ em todas as seis
comparações.** Nenhuma política Q05 foi ativada e nenhuma ação física foi executada.
O mesmo protocolo, repetido no **ponto de desenvolvimento** (40 Hz) com os
checkpoints daquele ponto, dá razão 4,75 (Ridge) e 5,00 (MLP) contra o alfa-beta,
também em 20 de 20 sementes (seção "Segundo ponto de operação").

## O que mudou em relação às campanhas shadow

As campanhas de 23/09 avaliavam previsões de rotação sobre um produtor artificial
sem ruído de fótons e com vibração de ~0,003 rad, longe do domínio de treino. Aqui
o ORION-Q fecha o laço sobre a planta em que os pesos foram treinados:

- **Planta** (`control_plane/polarisation_plant.py`): transcrição do modelo de fibra
  do qnet_autopilot (deriva térmica Ornstein-Uhlenbeck, acoplamento a dT/dt,
  vibração por soma de tons com harmônicos, saltos de Poisson), do monitoramento com
  dois estados de sonda e 400 fótons por base (estimativa binomial reprojetada em
  SO(3)) e da QBER BB84 da rotação residual. A mesma semente produz a mesma
  trajetória que o pacote do autor, bit a bit; o CI confere com fixtures gravadas.
  Ponto calibrado: tom de 60 Hz com harmônicos em 120 e 180 Hz, deriva 0,02 rad/s.
- **Laço** (`control_plane/polarisation_loop.py`): a cada 2 ms o controlador propõe
  a rotação da fibra 40 ms à frente; o supervisor aceita, limita ou recusa a
  correção; o comando aprovado entra em vigor 40 ms depois. Atuador ideal.
- **Supervisor independente**: recusa proposta não finita ou fora de SO(3) e mantém
  o comando anterior; limita a variação geodésica entre comandos consecutivos a
  π/2 por período; após 8 recusas seguidas, degrada até o fim da execução para o
  alfa-beta, que roda em espera quente. Reativar o modelo exige nova ativação.
- **Contrato**: um checkpoint só roda se os próprios metadados declararem esta
  planta (fibra, período, horizonte, amostragem e fótons de monitoramento).

## Protocolo congelado

`configs/optional_models/polarisation_closed_loop_v1.json`, SHA-256 do arquivo
`0b7b12b586e78839d2385af4acbbf2908eb1807ddd8812c2340a030eba79d9f4` (bytes LF).

| Item | Valor |
|---|---|
| Sementes de avaliação | 1000–1019 (20), nunca usadas pelo autor (treino 0–11, validação 100–103, teste 500–511) |
| Execução | 20 s, amostragem 1 ms, controle 2 ms, latência 40 ms, 400 fótons/base |
| Aprendidos | `qnet.compensation.calibrado.ridge`, `qnet.compensation.calibrado.mlp` |
| Baselines | sem compensação, persistência, alfa-beta ressintonizado |
| Ressintonia | grade 11 × 8 (α 0,005–1; β 0–0,1), sementes 100–103, 10 s, critério QBER média |
| Desfecho primário | QBER média por semente |
| Teste | sign-flip pareado exato, unilateral; Holm sobre 6 comparações; α = 0,05 |
| Intervalos | bootstrap sobre sementes, 10.000 reamostragens, semente 20261022 |

A grade do alfa-beta foi estendida para baixo em relação à do autor
(α ≥ 0,05), porque ali o ótimo caía na borda. Com a grade estendida o ótimo é
α = 0,01, β = 0 (QBER de ajuste 0,04459), e o baseline ficou mais forte que o do
manuscrito (0,0441 contra 0,0461 nas sementes 500–505). O ótimo é plano abaixo de
α = 0,01 (α = 0,005 dá 0,04460). Com β = 0 o filtro vira uma persistência
suavizada: com 40 ms de atraso, 2,4 períodos do tom de 60 Hz, estimar velocidade
não ajuda.

## Verificações antes de pontuar

1. **Contrato.** Os dois checkpoints calibrados foram admitidos. Os dois de
   desenvolvimento foram recusados nesta planta
   (`checkpoint_regime_differs_from_plant`, `declared_training_fibre_differs_from_plant`);
   transplantados, eles dariam QBER 0,26–0,27, quatro vezes pior que não compensar.
2. **Reprodução.** Nas sementes 500–505 o laço supervisionado reproduziu a QBER
   retida que cada checkpoint declara nos seus metadados: Ridge 0,0132653151857408
   contra 0,0132653151857407; MLP 0,0136375117637344 contra 0,0136375117637343
   (diferença ~10⁻¹⁶; tolerância 10⁻⁹). Sem essa reprodução a campanha para.

## Resultados (20 sementes, 20 s cada)

| Controlador | QBER média | IC 95% | p95 | Disponibilidade | Chave (kbit/s) | Resíduo (mrad) |
|---|---:|---|---:|---:|---:|---:|
| Ridge (calibrado) | **0,01308** | 0,01277–0,01340 | 0,0383 | 99,86% | **80,1** | 253 |
| MLP (calibrado) | 0,01342 | 0,01311–0,01376 | 0,0393 | 99,85% | 79,7 | 257 |
| Alfa-beta ressintonizado | 0,04355 | 0,04290–0,04425 | 0,1137 | 94,38% | 50,1 | 481 |
| Sem compensação | 0,08357 | 0,06602–0,10584 | 0,2120 | 75,10% | 34,9 | 659 |
| Persistência | 0,12167 | 0,11937–0,12414 | 0,3022 | 54,55% | 19,4 | 835 |

Disponibilidade é a fração do tempo com QBER < 11%. Chave é a taxa assintótica
BB84 com taxa peneirada de 100 kbit/s e eficiência de correção 1,16.

| Comparação | Razão de QBER | IC 95% | Vitórias | p | p de Holm |
|---|---:|---|---:|---:|---:|
| Ridge vs. alfa-beta | 3,33 | 3,24–3,42 | 20/20 | 9,5×10⁻⁷ | 5,7×10⁻⁶ |
| Ridge vs. sem compensação | 6,39 | 5,03–8,00 | 20/20 | 9,5×10⁻⁷ | 5,7×10⁻⁶ |
| Ridge vs. persistência | 9,30 | 9,04–9,58 | 20/20 | 9,5×10⁻⁷ | 5,7×10⁻⁶ |
| MLP vs. alfa-beta | 3,24 | 3,16–3,33 | 20/20 | 9,5×10⁻⁷ | 5,7×10⁻⁶ |
| MLP vs. sem compensação | 6,23 | 4,93–7,77 | 20/20 | 9,5×10⁻⁷ | 5,7×10⁻⁶ |
| MLP vs. persistência | 9,06 | 8,80–9,32 | 20/20 | 9,5×10⁻⁷ | 5,7×10⁻⁶ |

Com 20 sementes e vitória em todas, 2⁻²⁰ é o menor p que o teste exato consegue
dar. A razão contra o alfa-beta (3,33) fica próxima da do manuscrito (3,47, com a
grade original), mesmo com um baseline mais forte e sementes novas.

**O MLP empata com o Ridge.** Nas campanhas shadow ele perdia em todas as
sementes; aqui, na planta de treino, fica 2,6% acima do Ridge, como no
manuscrito (0,0136 contra 0,0133). O fracasso anterior era efeito do domínio do
produtor artificial, não da arquitetura.

## Segundo ponto de operação (desenvolvimento)

Mesmo desenho, mesmo código, protocolo
`configs/optional_models/polarisation_closed_loop_dev_v1.json` (SHA-256
`419029f0e6cc43296405e1ecb8eec03e78bf11224ae3077928bbd71e34c901e2`), que difere do
v1 só no ponto de operação (tom de 40 Hz sem harmônicos, deriva 0,05 rad/s), nos
checkpoints avaliados e nas sementes de reprodução (as oito retidas que os
metadados desse ponto declaram, 500–507). Resumo:
[`orion_q_polarisation_closed_loop_dev_20260929_summary.json`](orion_q_polarisation_closed_loop_dev_20260929_summary.json).

- **Contrato:** admitiu os dois checkpoints de desenvolvimento e recusou os dois
  calibrados nesta planta, com os mesmos dois motivos, no sentido inverso.
- **Reprodução:** Ridge 0,007738638194211411 contra 0,0077386381942114 declarado;
  MLP 0,007356499121608856 contra 0,0073564991216088 (diferença ≤ 6×10⁻¹⁷).
- **Ressintonia:** ótimo interior à grade, α = 0,03, β = 0,005 (QBER de ajuste
  0,03751). A 40 Hz, sem harmônicos, estimar velocidade ajuda um pouco.

| Controlador | QBER média | IC 95% | p95 | Disponibilidade | Chave (kbit/s) | Resíduo (mrad) |
|---|---:|---|---:|---:|---:|---:|
| MLP (desenvolvimento) | **0,00722** | 0,00693–0,00750 | 0,0194 | 99,84% | **87,9** | 187 |
| Ridge (desenvolvimento) | 0,00761 | 0,00733–0,00788 | 0,0203 | 99,83% | 87,3 | 193 |
| Alfa-beta ressintonizado | 0,03613 | 0,03536–0,03688 | 0,0942 | 97,17% | 56,0 | 437 |
| Persistência | 0,12814 | 0,12540–0,13061 | 0,3186 | 52,01% | 18,3 | 860 |
| Sem compensação | 0,19574 | 0,14841–0,24762 | 0,4416 | 47,12% | 19,9 | 1066 |

| Comparação | Razão de QBER | IC 95% | Vitórias | p de Holm |
|---|---:|---|---:|---:|
| Ridge vs. alfa-beta | 4,75 | 4,53–4,96 | 20/20 | 5,7×10⁻⁶ |
| MLP vs. alfa-beta | 5,00 | 4,78–5,24 | 20/20 | 5,7×10⁻⁶ |
| Ridge vs. persistência | 16,8 | 16,0–17,6 | 20/20 | 5,7×10⁻⁶ |
| MLP vs. persistência | 17,7 | 16,9–18,6 | 20/20 | 5,7×10⁻⁶ |
| Ridge vs. sem compensação | 25,7 | 19,4–32,8 | 20/20 | 5,7×10⁻⁶ |
| MLP vs. sem compensação | 27,1 | 20,7–34,5 | 20/20 | 5,7×10⁻⁶ |

O manuscrito relata, nas 8 sementes retidas deste ponto, Ridge 0,0077, MLP 0,0074
e alfa-beta 0,0383 (razão 5,2, IC 4,7–5,8). Com sementes novas e o alfa-beta
ressintonizado numa grade mais larga, a razão fica em 4,75–5,00, dentro da faixa
publicada. O supervisor limitou 1 comando do Ridge em 1.000.000 (semente 1018) e
não registrou falhas; com a falha injetada, degradou em t = 5,014 s nas 20
execuções (QBER média 0,0292).

Com os dois pontos, o resultado deixa de depender de um único regime: o contrato
aceita cada checkpoint só na planta que ele declara, e em cada uma o aprendido
vence o melhor clássico em todas as sementes.

## Supervisor

- Execuções limpas: 1.000.000 de comandos, 0 falhas, 2 comandos limitados em
  taxa (semente 1009, um no Ridge e um no MLP, num salto grande da fibra). O
  limite de π/2 fica acima da maior variação real da planta por período
  (1,24 rad na semente 500, fora das de avaliação) e quase nunca atua.
- **Falha injetada** (Ridge, 50 passos com saída NaN a partir de t = 5 s, nas 20
  sementes): o supervisor recusou as 8 primeiras propostas mantendo o último
  comando e degradou para o alfa-beta em t = 5,014 s em todas as execuções.
  QBER média 0,0360: Ridge nos primeiros 5 s, alfa-beta no resto. Nenhuma
  proposta inválida chegou à planta.

## Como reproduzir com o pacote fixado

Esta execução foi feita no ambiente de nuvem com um ZIP remontado a partir de
`F:\qnet_network` extraído (SHA-256 `4b8a18f1…`), com o pin substituído só nesse
processo. O portão de reprodução confirmou que os pesos são os do autor. A
execução oficial usa o ZIP fixado no ORION-Q (`6b856320…`):

```
python tools/run_polarisation_closed_loop.py --qnet-zip F:\qnet_network.zip --output outputs\polarisation-closed-loop-v1
```

O ponto de desenvolvimento usa o mesmo comando com
`--protocol configs/optional_models/polarisation_closed_loop_dev_v1.json`.
Cada campanha levou ~8–9 min na nuvem. Os números por semente devem coincidir com estes até ~10⁻⁹.
O resumo registra o hash dos arquivos de cada modelo (Ridge: `ae91cddd…` json,
`e2d754a2…` npz), e é esse resumo que a
[ativação Q05 do alvo](../product/orion_q_polarisation_activation_q05.md) exige como evidência.

## Limites

- Planta simulada, parametrizada por estatísticas publicadas de fibra aérea; não
  há série temporal pública de polarização em fibra terrestre nessa banda.
- Atuador ideal: a correção aparece exatamente 40 ms depois; curso, velocidade e
  reset do atuador não estão modelados (no ponto calibrado isso não foi medido).
- Dois pontos de operação (calibrado e desenvolvimento), cada um com os seus
  pesos. Os pesos não se transferem; o contrato impede o uso fora do ponto
  declarado, mas não detecta sozinho uma fibra real que mude de tom.
- O tempo de inferência em Python não é latência de hardware; os 40 ms são
  parâmetro da simulação.
