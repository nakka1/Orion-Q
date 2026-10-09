# Roteamento com o SeQUeNCe no laço — campanha v1

Data: 30/09/2026. Alvo `routing.repeater.v1`, modo `simulation`.
**Com o SeQUeNCe como planta — fótons que chegam ou não, detectores que clicam,
protocolos que negociam por canais com atraso —, o agente de roteamento fica abaixo
da heurística gulosa de fidelidade nos dois prazos: 19,0% contra 22,9% no nominal
(p de Holm 1×10⁻⁴; 1 bloco a favor, 17 contra) e 45,4% contra 46,7% no prazo
quádruplo (p de Holm 0,004; nenhum bloco a favor, 9 contra, 11 empatados).** No
prazo quádruplo, as duas políticas seguem a mesma trajetória em 892 de 1.000
requisições; nas que diferem, a heurística entrega em 13 e o agente em nenhuma. O
empate que o gêmeo analítico mostrava nesse prazo (45,1% contra 45,0%) não se
mantém. Ao mesmo tempo, o gêmeo, com a cobrança de 4,1 travessias por tentativa que
o autor mediu, prevê o SeQUeNCe: nenhuma das oito taxas de sucesso (quatro
políticas, dois prazos) muda de forma significativa entre os dois simuladores
(diferenças de −1,4 a +1,7 ponto, p de Holm 1,0), e os dois ofereceram as mesmas
ações legais em todos os passos de 8.000 episódios. Nenhuma política foi ativada e
nenhuma ação física foi executada.

## O laço

- **Planta:** o SeQUeNCe 1.2.0 (commit `3133c66d`), montado pelo
  `qnet_network.env.sequence_runner` do autor: roteadores quânticos com memórias,
  nós de medida de Bell com *heralding* simples, canais quânticos com atenuação e
  canais clássicos com atraso, com os parâmetros de `routing_loop.make_config` de
  cada variante — os mesmos que o contrato da exportação admite. Cada primitiva
  (gerar, purificar, trocar, entregar, descartar, esperar) é resolvida pelos
  protocolos do próprio SeQUeNCe; o relógio é o do simulador.
- **Decisão:** a cada passo, o ORION-Q entrega à réplica revisada
  (`ReplayRouter`) as 11 características de estado e as 23 de cada ação legal,
  calculadas pelos codificadores do autor sobre o estado espelhado das memórias do
  SeQUeNCe; a heurística gulosa fica de reserva, como na campanha analítica.
- **Requisições:** as mesmas 1.000 por variante da campanha de roteamento v1
  (sementes 600000–600999); os nós do SeQUeNCe usam a mesma semente.

## Portões

| Portão | Resultado |
|---|---|
| `runs/sequence/comparison.csv` do autor: agente da fase 2 (T = 0,5) e três heurísticas, 40 requisições, no SeQUeNCe e no gêmeo a 1 e 4,1 travessias, com o `scripts/evaluate_sequence.py` do autor e os pesos do arquivo do autor (SHA-256 em `external/VENDORED.json`) | 480 de 480 linhas, 10 colunas: erro relativo 0 |
| `runs/sequence/summary.csv` | 12 linhas: 1,5×10⁻¹⁵ |
| `runs/sequence/round_cost.csv` (tempo de uma tentativa de *heralding* em cada simulador, de 10 a 80 km) | 4 linhas: 1,5×10⁻¹³ |
| Lado analítico: as mesmas 1.000 requisições no gêmeo, contra o resumo publicado da campanha de roteamento v1 | 8 taxas de sucesso idênticas (erro 0) |

O primeiro portão mostra que o SeQUeNCe instalado aqui é o que o autor usou: o
laço de eventos regenera cada episódio do autor bit a bit.

## Protocolo congelado

`configs/optional_models/routing_sequence_v1.json`, SHA-256
`89ef712fbbfc778821ce1627d6aa1ca50271f84e5b31dab87bb3b0096ce21680`. Primária: agente
contra gulosa de fidelidade na taxa de sucesso, por variante, no SeQUeNCe; teste
exato de inversão de sinais pareado sobre 20 médias de blocos de 50 episódios,
bilateral, Holm sobre as duas variantes. Secundária: cada política no SeQUeNCe
contra ela mesma no gêmeo, nas mesmas requisições (Holm sobre as oito).

## Resultados (taxa de sucesso, 1.000 requisições por variante)

| Variante | Política | SeQUeNCe | Gêmeo (4,1) | Fidelidade entregue | Tempo médio |
|---|---|---:|---:|---:|---:|
| Nominal | agente (T = 1) | 19,0% | 20,4% | 0,716 | 59,4 ms |
| | gulosa de fidelidade | **22,9%** | 23,4% | 0,715 | 57,9 ms |
| | caminho mais curto | 22,9% | 23,4% | 0,715 | 57,9 ms |
| | limiar 0,90 | 7,4% | 7,2% | 0,715 | 65,8 ms |
| Prazo ×4 | agente (guloso) | 45,4% | 45,1% | 0,732 | 182,5 ms |
| | gulosa de fidelidade | **46,7%** | 45,0% | 0,734 | 181,3 ms |
| | caminho mais curto | 29,6% | 29,9% | 0,714 | 201,5 ms |
| | limiar 0,90 | 22,5% | 22,6% | 0,722 | 248,8 ms |

No nominal, o agente entrega em 25 requisições em que a gulosa falha e falha em 64
em que ela entrega. A réplica não recusou nenhum passo (nenhuma falha, nenhum
episódio degradado).

**Leitura.** O gêmeo analítico, calibrado pelo autor contra o SeQUeNCe, reproduz o
SeQUeNCe para as quatro políticas; o que o SeQUeNCe acrescenta é resolução: no
prazo quádruplo, as poucas decisões em que o agente se afasta da heurística perdem
sempre. A conclusão da campanha analítica fica mais forte, não mais fraca: o
aprendizado não supera a melhor regra simples no roteamento, em nenhum dos dois
simuladores, e no SeQUeNCe fica um pouco abaixo dela nos dois prazos.

## Como reproduzir

```
set SEQUENCE_SRC=C:\caminho\SeQUeNCe   (checkout do commit 3133c66d)
python tools/run_routing_sequence.py --qnet-zip F:\qnet_network.zip --author-zip "Qnet-Network ML.zip" --output outputs\routing-sequence-v1 --workers 2
```

Resumo: [`orion_q_routing_sequence_20260930_summary.json`](orion_q_routing_sequence_20260930_summary.json).
Na nuvem, 18 minutos com dois processos (30 s de portões do autor).

## Limites

- O SeQUeNCe com os parâmetros de *hardware* do autor e geração com *heralding*
  simples (o único esquema que o formalismo de Bell diagonal representa); é um
  simulador, não uma rede.
- Uma topologia (cadeia de quatro nós) e um par origem-destino.
- O agente foi treinado no gêmeo analítico, não no SeQUeNCe.
