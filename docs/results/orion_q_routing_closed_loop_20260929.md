# Agente de roteamento em laço fechado — campanha v1

Data: 29/09/2026. Alvo `routing.repeater.v1`, modo `simulation`.
**O ORION-Q regenera, pela sua réplica, as 2.091 decisões dos 48 episódios
exportados (zero divergências) e as duas tabelas retidas do pacote (erro ≤ 6×10⁻¹⁷).
Em 1.000 episódios novos por variante, o agente com prazo longo empata com a
melhor heurística (45,1% contra 45,0% de sucesso), mas o agente do prazo nominal
fica abaixo dela (20,4% contra 23,4%; p de Holm 0,010), o que o empate descrito
no manuscrito não mostrava com 400 episódios.** Nenhuma política foi ativada e
nenhuma ação física foi executada.

## O laço

- **Planta:** `qnet_network.env.QuantumNetworkEnv`, o ambiente analítico do autor
  (cadeia de 4 nós com vãos de 20 km, 6 memórias por nó, *heralding* cobrado a 4,1
  travessias do vão, valor que o autor mediu contra o SeQUeNCe), com a configuração
  que o `model/policy.json` de cada variante declara. As constantes são as de
  `scripts/task.py`; o teste confere.
- **Variantes:** `nominal` (prazo de 40–90 ms, 0,01 por tentativa, amostragem com
  T = 1) e `prazo_longo` (prazo ×4, 0,0025 por tentativa, guloso).
- **Laço** (`control_plane/routing_loop.py`): a cada passo o ORION-Q entrega à
  réplica revisada (`RoutingReplayModel`) as 11 características de estado e as 23 de
  cada ação legal, e o ambiente executa a ação no índice devolvido. Se a réplica
  recusar um passo, a heurística gulosa de fidelidade assume como reserva quente;
  após 3 recusas, termina o episódio.
- **Contrato:** topologia, memórias, eficiências, custos, orçamento de geração e
  distribuição das requisições têm de coincidir com o que a exportação declara.

## Portões de reprodução

| Portão | O que foi regenerado | Resultado |
|---|---|---|
| Episódios exportados, nominal | 24 episódios, 549 decisões, gerador `default_rng(0)` | 0 divergências de ação e de desfecho |
| Episódios exportados, prazo longo | 24 episódios, 1.542 decisões | 0 divergências |
| Tabela retida, nominal | 400 episódios (sementes 500000–500399), agente e 5 heurísticas; gerador do agente como em `evaluate_phase2` (`ActorCriticPolicy(rng=0)`) | erro 0,0 (agente 0,2175; gulosa 0,24) |
| Tabela retida, prazo longo | 160 episódios (sementes 510000+), agente guloso e 4 heurísticas | erro 6×10⁻¹⁷ (agente 0,45625; gulosa 0,4625) |

## Protocolo congelado

`configs/optional_models/routing_closed_loop_v1.json`, SHA-256
`96095487368bb5ace1bc1f175da8550a3eee72ee52b0bdd3bca0033ee5003c72`. 1.000 episódios
novos por variante (sementes 600000–600999; o autor usou 1–3000 no treino,
900000–900099 na validação, 500000–500399 no teste e 510000+ no estudo de custo),
mesmo gerador de amostragem da exportação. Comparação pareada por episódio, agrupada
em 20 blocos de 50 episódios; teste de sinais exato e **bilateral** (o manuscrito
descreve empate), Holm sobre as quatro comparações primárias (sucesso e retorno,
por variante, contra a heurística gulosa).

## Resultados (1.000 episódios por variante)

| Variante | Política | Sucesso | Retorno bruto | Trocas por episódio |
|---|---|---:|---:|---:|
| nominal | **agente (T = 1)** | 20,4% | −0,416 | 2,53 |
| nominal | gulosa de fidelidade | **23,4%** | **−0,382** | 2,71 |
| nominal | caminho mais curto | 23,4% | −0,382 | 2,71 |
| nominal | limiar 0,90 | 7,2% | −0,635 | 2,50 |
| nominal | não fazer nada | 0% | −0,508 | 0 |
| prazo longo | **agente (guloso)** | **45,1%** | **−0,023** | 8,74 |
| prazo longo | gulosa de fidelidade | 45,0% | −0,025 | 8,75 |
| prazo longo | caminho mais curto | 29,9% | −0,229 | 9,46 |
| prazo longo | limiar 0,90 | 22,6% | −0,434 | 6,50 |
| prazo longo | não fazer nada | 0% | −0,443 | 0 |

| Comparação primária (agente − gulosa) | Diferença média | Blocos (a favor/contra) | p | p de Holm |
|---|---:|---:|---:|---:|
| nominal, sucesso | −0,030 | 3/15 | 0,0024 | **0,0095** |
| nominal, retorno | −0,034 | 5/15 | 0,0055 | **0,016** |
| prazo longo, sucesso | +0,001 | 4/4 | 1,0 | 1,0 |
| prazo longo, retorno | +0,002 | 11/9 | 0,56 | 1,0 |

Contra as outras heurísticas (secundário), o agente vence o limiar 0,90, a política
aleatória e "não fazer nada" nas duas variantes, e vence o caminho mais curto no
prazo longo (45,1% contra 29,9%, 19 de 20 blocos); no prazo nominal perde para o
caminho mais curto, que ali coincide com a gulosa.

**Leitura.** A tabela retida do próprio pacote já mostrava o agente nominal abaixo
da heurística (21,75% contra 24%), e o manuscrito a leu como empate. Com 2,5 vezes
mais episódios e teste pareado, a diferença de 3 pontos é real: no prazo nominal,
amostrar a política com T = 1 custa mais do que ela ganha. No prazo longo, a
política gulosa corrigida (custo por tentativa invariante ao prazo) iguala a melhor
heurística e supera as demais. Isso não muda a conclusão do TCC — o aprendizado não
supera a melhor regra simples no roteamento — mas troca "empata" por "empata no
prazo longo e perde no nominal".

O supervisor não registrou recusas nas 2.000 execuções do agente.

## Como reproduzir

```
python tools/run_routing_closed_loop.py --qnet-zip F:\qnet_network.zip --output outputs\routing-closed-loop-v1
```

Resumo: [`orion_q_routing_closed_loop_20260929_summary.json`](orion_q_routing_closed_loop_20260929_summary.json).
Esta execução usou, na nuvem, um ZIP remontado a partir de `F:\qnet_network` (os
portões confirmam que os pesos são os exportados).

## Limites

- Ambiente analítico do autor, conferido por ele contra o SeQUeNCe; o SeQUeNCe no
  laço foi avaliado à parte em 30/09 ([SeQUeNCe](orion_q_routing_sequence_20260930.md)).
- Uma topologia (cadeia de 4 nós) e um par origem-destino; contenção entre
  requisições simultâneas não foi testada.
- O painel *shadow* de roteamento do console é alimentado pelo mesmo ambiente do
  autor (desde 29/09), mas só registra recomendações; a avaliação do agente é esta.
