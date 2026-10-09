# Compensador em ponto fixo no laço do ORION-Q — campanha v1

Data: 30/09/2026. Alvo `polarisation.compensation.v1`, modo `simulation`.
**Com 16 bits e 8 estágios de CORDIC — a configuração do RTL do autor —, o
compensador em ponto fixo fica a menos de 1% do modelo em ponto flutuante no ponto
calibrado (Ridge 1,001×; MLP 0,999×) e a até 4% no de desenvolvimento (Ridge 1,010×;
MLP 1,038×), não inferior com margem de 5% nos quatro casos (p de Holm ≤ 0,046), e
continua vencendo o alfa-beta em 20 de 20 sementes novas.** A 14 bits o custo sobe
para 14% a 21% (o MLP de desenvolvimento, 0,2%); a 12 bits o laço colapsa (8 a 23
vezes a QBER) e perde para o alfa-beta em todas as sementes. Antes disso, a réplica
regenerou a varredura de largura de palavra do próprio autor, linha a linha. Nenhuma
política foi ativada e nenhuma ação física foi executada.

## O que entra no laço

O porte do autor (`qnet_network.deploy`): formatos Q por tensor, calibrados sobre
traços do próprio laço fechado; uma única rodada de arredondamento e saturação por
camada; logaritmo e exponencial de SO(3) por CORDIC. Os compensadores são
reconstruídos a partir dos *arrays* do pacote revisado — `LearnedCompensator` do
autor em volta dos coeficientes do Ridge ou das camadas do MLP, sem *pickle* — e
passam pelo `build_quantised` do autor. No laço do ORION-Q, a predição em ponto fixo é
uma rotação só até a resolução da palavra, e o supervisor recusa o que está fora de
SO(3); por isso ela é projetada na rotação mais próxima (SVD), como faria o *driver*
de um atuador. O deslocamento máximo registrado foi 8×10⁻⁵ (Frobenius) a 16 bits e
1,3×10⁻³ a 12 bits; o supervisor não registrou falhas em nenhuma execução.

## Portões

| Portão | Resultado |
|---|---|
| Varredura do autor (`runs/quantisation/width.csv`: ponto de desenvolvimento, TEST_SEEDS[:6], calibração em TRAIN_SEEDS[:3]; ponto flutuante e 8 a 24 bits com CORDIC 16) e a linha de 16 bits com CORDIC 8 de `cordic.csv`, Ridge e MLP | 20 de 20 linhas, 10 colunas cada (QBER média, mediana e p95, disponibilidade, taxa de chave, resíduo, erro de previsão, erro-padrão, saturações): erro relativo máximo 3,2×10⁻¹⁶ |
| Compensador do autor reconstruído contra a réplica do ORION-Q, no laço do ORION-Q, 20 sementes × 2 pontos × 2 modelos | diferença 0,0 |
| QBER média do ponto flutuante contra as campanhas em laço fechado | idêntica (0,013079…, 0,013423…, 0,007613…, 0,007221…) |

O primeiro portão também confirma que os *arrays* exportados do ponto de
desenvolvimento são os pesos do `main.pkl` que o autor quantizou.

Uma primeira execução parou no portão com 19 de 20 linhas: a tolerância era absoluta
(10⁻¹²) em todas as colunas, e a taxa de chave, na casa de 10⁵ Hz, diferiu no último
bit. A tolerância passou a relativa antes de qualquer avaliação.

## Protocolo congelado

`configs/optional_models/polarisation_quantisation_v1.json`, SHA-256
`3764bf96d0c582ee713dc2cd121c9ccf0a55d84861e4b535cacd5e3f6654da58`. Calibração dos
formatos nas sementes de ajuste (100–103), nunca nas avaliadas; avaliação nas 20
sementes das campanhas em laço fechado (1000–1019), 20 s, supervisor e reserva
alfa-beta dessas campanhas. Configurações: 16 bits com CORDIC 8 e 16; 14 e 12 bits
com CORDIC 16. Hipótese primária (16 bits, CORDIC 8): QBER média por semente não
maior que 1,05 vez a do ponto flutuante; permutação de sinais exata, unilateral,
Holm sobre os quatro casos.

## Resultados (QBER em ponto fixo / ponto flutuante, 20 sementes)

| Ponto | Modelo | 16 b, CORDIC 8 | 16 b, CORDIC 16 | 14 b | 12 b | Ponto flutuante |
|---|---|---:|---:|---:|---:|---:|
| calibrado | Ridge | **1,001** (0,995–1,007) | 1,000 | 1,136 | 9,02 | 0,01308 |
| calibrado | MLP | **0,999** (0,994–1,005) | 0,999 | 1,143 | 8,12 | 0,01342 |
| desenvolvimento | Ridge | **1,010** (0,994–1,028) | 1,009 | 1,208 | 19,3 | 0,00761 |
| desenvolvimento | MLP | **1,038** (1,025–1,051) | 1,037 | 1,002 | 23,0 | 0,00722 |

Entre parênteses, IC 95% por *bootstrap* sobre sementes. p de Holm da não
inferioridade: 3,8×10⁻⁶ (calibrado), 6,1×10⁻⁴ (Ridge de desenvolvimento) e 0,046 (MLP
de desenvolvimento). O alfa-beta dessas campanhas fica em 0,0436 (calibrado) e 0,0361
(desenvolvimento): todas as configurações de 14 e 16 bits vencem em 20 de 20 sementes;
as de 12 bits perdem em 20 de 20.

**Leitura.** A conclusão do autor — 16 bits bastam, e o limite é um degrau — vale nos
dois pontos e em sementes novas, e vale para a configuração que o RTL implementa
(CORDIC 8, que custa cerca de 0,1% em relação a 16 estágios). O degrau fica entre 12 e
14 bits; a 14 bits o custo varia de 0,2% a 21% conforme o modelo e o ponto (o autor
viu 5% e 28% no desenvolvimento, com 6 sementes). O MLP do ponto de desenvolvimento é
o caso mais apertado a 16 bits: 3,8% acima do ponto flutuante, dentro da margem, com o
intervalo inteiro acima de 1.

## Como reproduzir

```
python tools/run_polarisation_quantisation.py --qnet-zip F:\qnet_network.zip --output outputs\polarisation-quantisation-v1 --workers 2
```

Resumo: [`orion_q_polarisation_quantisation_20260930_summary.json`](orion_q_polarisation_quantisation_20260930_summary.json).
Na nuvem, ~10 min de portão e ~45 min de avaliação com dois processos, com o ZIP
remontado das outras campanhas.

## Limites

- Atuador ideal; o modelo em ponto fixo é a referência bit a bit do autor, cujo RTL
  foi conferido à parte ([RTL](orion_q_author_rtl_20260930.md)), não o *hardware*
  sintetizado em placa.
- Formatos calibrados em quatro sementes de ajuste de cada ponto.
- A projeção em SO(3) é do ORION-Q; o laço do autor aplica a matriz sem projetar.
