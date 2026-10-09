# Produtores do console a partir das plantas do autor

Data: 29/09/2026. Afeta os cinco painéis *shadow* do console (compensação,
sincronismo, taxa de chave, alocação e roteamento).

## O que mudou

Até aqui os painéis *shadow* eram alimentados por geradores escritos para o
ORION-Q: rotação com vibração de 0,003 rad e sem ruído de fótons, *offset* em
forma de seno, observáveis *decoy-state* de fixture, fila de alocação fixa e um
quadro de roteamento ilustrativo de três nós. Esses geradores mediam o domínio
deles, não os modelos — as campanhas de 23 a 26/09 mostraram o MLP perdendo em
todas as sementes por causa disso.

Agora cada painel recebe observações da planta em que o modelo foi treinado,
com o código do autor incorporado ao repositório
(`src/quantum_autopilot/control_plane/author_feeds.py`):

| Painel | Planta | Quem conduz a planta | Segmento |
|---|---|---|---|
| Compensação | `polarisation_plant` (transcrição bit a bit do qnet_autopilot) nos pontos calibrado e de desenvolvimento; estimativas de rotação com 400 fótons por base, reprojetadas em SO(3), e as covariáveis da planta | nada (a previsão é da fibra) | 30 s de fibra = 937 publicações de 32 ms |
| Sincronismo | `qnet_qkd` `ClockModel` a 50 km; *offset* estimado com 2000 fótons como em `run_sync_loop` | nada (a previsão é do atraso) | 1 h simulada |
| Taxa de chave | `qnet_qkd` `KeyRateEnv` a 50 km | parâmetros de comissionamento mantidos (incumbente) | um episódio de 240 períodos |
| Alocação | `qnet_network` `HybridAllocationEnv`, planta exportada com chave finita | quântico-primeiro (incumbente) | um episódio de 30 min |
| Roteamento | `qnet_network` `QuantumNetworkEnv`, configuração nominal exportada; as 11 + 23 características do próprio ambiente; pares e memórias como recursos instantâneos | heurística gulosa de fidelidade (incumbente); a escolha dela é a referência declarada | episódios consecutivos |

Em todos, a recomendação do modelo é pontuada e registrada, nunca aplicada. Cada
alimentação é função pura de (semente do console, segmento, índice); a semente
da planta é derivada por SHA-256, não pelo `hash` do Python. O `source_id` das
janelas passou de `synthetic-*-generator` para `author-plant-*`.

## O que isso resolve e o que não resolve

- O painel mostra o modelo no domínio dele: a mesma fibra, o mesmo relógio, o
  mesmo enlace, a mesma planta metropolitana e o mesmo ambiente de repetidores
  dos laços fechados (`docs/results/orion_q_*_closed_loop_20260929.md`).
- Não é dado medido: as plantas são simulações do autor, com as hipóteses dele.
- As campanhas *shadow* de 23 a 26/09 continuam registradas como estavam; elas
  usaram os geradores antigos e servem como histórico do problema.

## Testes

`tests/test_author_feeds.py` confere que cada alimentação é a planta do autor
(mesma simulação e mesma medição, bit a bit) e que 80 janelas consecutivas de
roteamento passam no validador de recursos com as características do ambiente.
Os testes dos painéis foram ajustados onde afirmavam propriedades da fixture
antiga (fila fixa, período sem chave, contagem de candidatos).
