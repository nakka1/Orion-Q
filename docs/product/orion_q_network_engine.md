# ORION-Q · Motor de rede

Atualização (2026-10-05): o console abre a demonstração institucional
`brazil-institutions-v1`, com sete locais e oito fibras hipotéticas. O journal
anterior de Salvador e o preset da API permanecem disponíveis. O switch de ML
inicia OFF em um workspace novo, requer confirmação e revisão vigente e grava
recibo com hash. OFF mantém baselines e detector; ON admite somente os contratos
compatíveis no novo cenário. Veja o [guia de demonstração](orion_q_demo_guide.md).

O comprimento de cada fibra nova é o limite inferior geodésico WGS84 entre os
locais; não é um levantamento de cabo. Estimativas positivas de chave com QBER
acima do limite `qber_down` não abastecem o estoque. A estimativa rejeitada fica
na proveniência. Equipamentos e enlaces são referências hipotéticas e não
representam instalações reais dessas instituições.

Estado: **implementado** (2026-10-04) — `src/quantum_autopilot/network_engine/`: processamento por fibra
(`state.py`, `sources.py`, `models.py`, `links.py`, seções 1–7) e rede (`routing.py`, `allocation.py`,
`calibration.py`, `engine.py`, `presets.py`, seções 8–15), mais a API de serviço em
`qkd/dashboard_api.py`. Cobre a §2 do [projeto da rede ao vivo](orion_q_live_network_design.md).

Regra do produto: **a única simulação é a geração dos dados de entrada**. Tudo o que é exibido sai do
processamento real (controladores, detector, cálculo de chave) aplicado a essa entrada; o processamento não
sabe se o quadro foi simulado ou medido e nunca lê a verdade oculta da planta (`LinkInput.reference`; há
teste). Nenhuma atuação física: todo registro carrega `physical_action_performed: False`.

## 1. O que é simulado (só a entrada)

`SimulatedInputSource(seed, scenario)` gera, por fibra ativa e por tick (1 s de rede), **observáveis brutos**
com as plantas do autor, no comprimento real da fibra (nada é ajustado para caber em um domínio):

| Observável | Planta do autor | Cadência |
|---|---|---|
| Rotações de polarização medidas + covariáveis (T, dT/dt, vibração) | `control_plane.polarisation_plant` (fibra aérea, ponto `calibrado`), medição com 400 fótons/base | 2 ms (500 por tick) |
| Offset de relógio medido + temperatura do cabo | `qnet_qkd.qkd.timing.ClockModel` (deriva térmica ~40 ps/km/K), histograma de 2000 fótons | 1 s |
| Período da sessão decoy-BB84: as 17 colunas públicas | condições de `keyrate_loop.LINK`, transmissor comissionado para o comprimento (`keyrate_loop.make_link`), desalinhamento do modelo de deriva da sessão do autor, receptor da planta de taxa de chave (porta, tempo morto, afterpulsing), ataques de `qnet_qkd.qkd.attacks`, estatística finita (`sample_observables`) | 1 s |

- **Determinismo**: cada fluxo aleatório é semeado por `sha256(seed, link_id, epoch_tick, finalidade)`. Uma
  fibra produz os mesmos quadros com qualquer outro conjunto de fibras e em qualquer ordem. O primeiro
  segmento de cada fluxo é idêntico, bit a bit, à função do autor com o mesmo gerador (testes).
- **Sessões = episódios do autor.** O modelo de desalinhamento da sessão e a linha de base do detector foram
  estudados em episódios de 600 períodos. Deixado correr, o QBER da sessão vagueia até 5–20 % em 15–30 min
  (fora do que o autor validou). Por isso, a cada 600 períodos a sessão da fibra é **comissionada de novo**
  (novo `epoch_tick`, alinhamento inicial, nova linha de base do detector); a temperatura do cabo continua, e
  a troca espera um ataque/perda em curso terminar. O primeiro episódio de cada fibra é encurtado por um
  valor próprio (hash) para que as fibras não fiquem todas em linha de base no mesmo minuto.
  `session_periods=None` mantém uma sessão única sem limite.
- **Acoplamentos** (termos do próprio autor): um evento térmico eleva a única temperatura do cabo, que move o
  desalinhamento da sessão, o atraso do relógio e — pelo termo dT/dt da planta — a rotação monitorada; e o
  ajuste do transmissor decidido pelo controlador de taxa de chave volta para a planta (`actuate`).
  As três plantas **não** são acopladas entre si além disso: o resíduo de polarização e a eficiência de
  porta não entram no erro da sessão.
- `IdleInputSource`: não entrega nada; `inject` é recusado.

### Linha do tempo da demonstração (`scenario="demo"`, repete a cada 345 s)

As fibras são ordenadas por `sha256(seed, link_id)`; o evento *i* vai para a fibra de posto `i mod n`
(funciona com qualquer topologia com ≥ 1 fibra). Os primeiros 65 s cobrem a linha de base das sessões.

| t (s) | Evento de entrada | Posto | Intensidade | Duração |
|---|---|---|---|---|
| 65 | `thermal_drift` | 0 | excursão de 12 K, subida de 60 s | 60 s |
| 135 | `loss_event` | 1 | 3 dB de perda extra | 30 s |
| 185 | `intercept_resend` | 2 | 24,4 % dos pulsos interceptados | 40 s |
| 245 | `pns` | 3 | 56 % dos fótons únicos bloqueados | 35 s |
| 295 | `detector_blinding` | 4 | cliques forçados a 1,38× o ganho | 30 s |

Um evento agendado que cairia na linha de base de uma sessão espera ela terminar. O operador injeta qualquer
um deles (e `beam_splitting`) em uma fibra com `inject(InputEvent(kind, link_id, strength, duration_s))`;
`strength ∈ (0, 1]` percorre a faixa de severidade do gerador de dados do autor.

## 2. O que é processamento real (`links.py`)

Por fibra e por tick, na ordem do projeto:

1. **Compensação de polarização** — a cada 2 ms o controlador ativo propõe; o supervisor de
   `polarisation_loop` aceita, limita ou recusa; o comando vale 40 ms depois. O laço do supervisor foi
   desenrolado entre ticks com o estado preservado; um teste compara os comandos com
   `PolarisationSupervisor.run` (iguais, inclusive com falhas e troca para a reserva). Publica-se o resíduo
   **monitorado** `C(t)·R_medido(t)` — o único que um receptor conhece —, que inclui o ruído do monitor
   (≈ 0,15 % de QBER; verificado contra a verdade em teste).
2. **Sincronismo** — uma estimativa de offset por segundo; previsão 2 s à frente; resíduo = offset medido −
   posição da porta em vigor; eficiência por `gate_efficiency`.
3. **Taxa de chave** — limites decoy e taxa secreta recalculados das colunas públicas
   (`single_photon_bounds`, `decoy_key_rate_hz`; igual à planta, bit a bit); o controlador decide o ajuste
   do próximo período dentro da caixa revisada; o período seguinte confere que o transmissor adotou a
   proposta. A chave do período vai para o estoque, ou é **descartada** enquanto o alarme está retido (e os
   dois períodos que formaram o alarme são recolhidos).
4. **Detector de ataques** — as 17 features do autor, causais, calculadas de forma incremental (medianas da
   linha de base fixadas aos 60 períodos + janela de 31 períodos). Teste prova igualdade **bit a bit** com a
   referência causal em lote (`control_plane/detector_causal.py`) e chamadas idênticas às do `SafeDetector`.
   `FastSafeDetector` percorre as 2400 árvores juntas com a soma na ordem ajustada: probabilidades idênticas
   bit a bit, ~0,7 ms por fibra em vez de ~80 ms. Alarme de referência: limiar de QBER na calibração do autor.
5. **Estimativas** — QBER total = taxa de erro de sinal do período; `F = 1 − 3·QBER/2` (cortada em
   [0,25; 1]); `wave` = 20 amostras/s de `1 − 2·QBER` do resíduo de polarização monitorado (últimos 12 s);
   histórico 1 Hz (300 s).

**Estabilidade** (indicador de exibição, não é grandeza de segurança) — produto de três termos sobre os
últimos 30 períodos da sessão:

```
s_qber = clip(1 − (média(QBER) + 2·desvio(QBER)) / 0,11, 0, 1)     0,11 = limiar BB84
s_sync = clip(média(eficiência de porta) / eficiência com offset zero, 0, 1)
s_pol  = clip(média(1 − 2·QBER de polarização monitorado), 0, 1)
stability = s_qber · s_sync · s_pol
```

Valor típico de uma fibra sã: ≈ 0,72–0,75 (o termo de polarização da planta aérea fica em ≈ 0,91). Cai quando
o QBER sobe ou oscila, quando a porta perde o pulso e quando o compensador deixa mais resíduo.

**Status**: `compromised` enquanto o alarme de ataque está retido (levantado por 3 chamadas de ataque
iguais e consecutivas; liberado após 30 períodos sem chamada sustentada — a janela de features do detector);
`down` se o período não rende chave ou QBER ≥ 11 %; `degraded` (classe sustentada não-ataque, alarme de
limiar de QBER, controlador em reserva/falha, porta < 90 % do ideal, taxa < metade da calibração, QBER de
polarização ≥ 11 %); senão `up`. `disabled | isolated | calibrating` são impostos por quem chama e nada é
publicado sob eles (métricas `None`, onda vazia); em `calibrating` os laços continuam rodando internamente
(é o próprio procedimento) e `LinkBank.measurement()` dá o antes/depois. Sem entrada: `no_input`, sem
decisões. Um alarme retido sobrevive a uma troca de sessão automática; só o detector ou uma recalibração
(`reset_link`) o libera.

**Decisões**: alinhamento inicial, aquisição de porta, proposta de ajuste do transmissor (ação ≠ manter),
entrada/saída de reserva, propostas recusadas ou limitadas, alarme levantado/liberado — um registro por tick
(não por passo de 2 ms), com os números reais, `inputs_sha256`, `in_contract`, `fallback_used` e hash próprio.

## 3. Registro de modelos (`models.py`)

Pesos aprendidos de compensação, sincronismo, taxa de chave, roteamento e alocação só existem nos dois ZIPs
revisados (`qnet`, `qkd`), fixados por SHA-256. Vêm de `set_archive(kind, path)` ou de `ORION_QNET_ARCHIVE` /
`ORION_QKD_ARCHIVE` e só são adotados depois de `verify_model_archive` + `ReviewedArchive`. Sem ZIP verificado:
estado `unavailable` e a referência nomeada assume. Com ZIP mas contrato recusado: `fallback`, com o motivo.

| Papel | Modelo | Contrato | Referência |
|---|---|---|---|
| compensação | `qnet.compensation.<ponto>.ridge` | `check_checkpoint` (planta declarada = planta em uso) | `baseline.alpha_beta` (0,01; 0) |
| sincronismo | `qkd.sync.ridge.1s.h2s` | cadência 1 s, horizonte 2 s, **vão de 50 km exato** | `AlphaBetaTracker` |
| taxa de chave | `qkd.keyrate.actor` | vão **25–80 km**, caixa de ações revisada | `baseline.static` (mantém o comissionado) |
| detector | `qkd.detector.boosting` (export seguro no repositório) | sempre disponível; treinado em 20–70 km | limiar de QBER (σ = 97,16) |
| roteamento | `qnet.routing.nominal` | roda em qualquer grafo, mas só está **em contrato** na cadeia revisada de 4 nós (seção 8) | `baseline.greedy_fidelity` |
| alocação | `qnet.allocator.ridge` | planta fixa de 11 vãos, 5 s, BB84 de fóton único: **não roda** nesta topologia (seção 10) | `baseline.quantum_first` |

O gancho `register_test_archive` aceita arquivos sem hash **só** em um registro criado com
`allow_unverified=True` (testes); em produção é recusado.

## 4. Comportamento real observado (sem ajuste de resultado)

Medido com os controladores de referência (os ZIPs não estão nesta máquina) e o detector do repositório.

- **Canal honesto**: 10 km → QBER ≈ 2,2 %, chave ≈ 4,1 Mbit/s; 50 km → ≈ 2,0 Mbit/s; 70 km → ≈ 0,84 Mbit/s.
  Resíduo de polarização monitorado ≈ 450–550 mrad (QBER de polarização ≈ 4–5 %: a vibração de 60 Hz da
  planta aérea com 40 ms de latência não é seguida pelo alfa-beta); resíduo de sincronismo de poucos ps.
- **Intercept-resend**: QBER 2,2 % → 6,7–9,4 % (intensidade 0,6–1,0); alarme no 3º período atacado (o mínimo
  possível com histerese de 3) em todos os vãos testados (3–90 km). Com 7,6 % dos pulsos interceptados
  (intensidade 0,2) o alarme falha em parte das fibras. Cerca de 13 períodos depois do fim do ataque o
  detector volta a chamar `intercept_resend` por ~16 períodos (os períodos atacados saindo da janela de
  tendência) — por isso a liberação exige 30 períodos limpos.
- **PNS**: QBER inalterado (o limiar de QBER não vê nada); alarme no 3º período em vãos ≥ 20 km. Em fibras
  metropolitanas (3–15 km) é frequentemente classificado como `loss_event` (sem alarme) ou com atraso.
- **Detector blinding**: **não detectado até 50 km**; detectado a 70–90 km. Causa medida: o receptor da
  planta de taxa de chave tem afterpulsing, que eleva o rendimento de vácuo de ~1e-8 para ~6e-5; a assinatura
  do ataque (vazamento no vácuo) passa de ×10⁴ nas sessões de treino para ×1,7 (10 km), ×3,5 (50 km),
  ×13 (90 km). A taxa de chave aparente sobe um pouco durante o ataque.
- **Beam splitting**: sem assinatura observável, por construção (nenhum detector o vê).
- **Evento de perda**: classificado como `loss_event` em todos os vãos; fibra `degraded`, chave mantida.
- **Deriva térmica** (12 K em 60 s): classificada como `thermal_drift` enquanto a temperatura está fora da
  linha de base (inclusive no relaxamento). Em um vão de 34 km o atraso muda ~270 ps/s: o alfa-beta (2 s de
  latência) perde a porta por alguns segundos no início e no fim da rampa (eficiência → 0) e depois segue.
  Houve um falso `intercept_resend` durante deriva térmica forte em uma fibra de 5 km.
- **Falsos alarmes em rede quieta** (duas sementes, 10 fibras × 1500 s cada): 12 e 2 alarmes de ataque falsos
  — ≈ 2,9 e ≈ 0,5 por fibra-hora; ≈ 11 % e ≈ 1 % do tempo-fibra em `compromised` —, quase todos
  `intercept_resend`, na parte final dos episódios, quando a deriva natural do modelo do autor leva o QBER a
  4–8 %. É compatível com a taxa do próprio detector em domínio (≈ 3–4 por hora sã com limiar + histerese,
  `docs/results/orion_q_detector_*`). O limiar de QBER quase nunca dispara. A mesma deriva natural deixou
  uma fibra de 35 km sem chave (`down`) por 84 s no fim de um episódio. **Consequência para a rede**: fibras
  saem do grafo efetivo por alarmes falsos com essa frequência; é o comportamento real do detector revisado.

## 5. Domínio e contrato (o que cada registro declara)

- **Detector: `in_contract = False` em toda fibra com entrada simulada.** Motivos registrados por fibra: vão
  fora de 20–70 km (quase toda fibra metropolitana), transmissor no ponto comissionado em vez de
  (μ 0,5; ν 0,1; 1e9 pulsos), e receptor com tempo morto/afterpulsing ausente das sessões de treino. Se o
  ator de taxa de chave mudar o transmissor depois da linha de base, o registro também marca fora de contrato.
- **Ator de taxa de chave**: só roda em 25–80 km; fora disso `baseline.static` (o modelo recusa o vão).
- **Sincronizador ridge**: só em 50 km exatos (contrato existente); nos demais, alfa-beta.
- **Compensador**: a planta de polarização do autor não tem parâmetro de comprimento — o mesmo ponto de
  operação serve a toda fibra.
- Quadros que não sejam `simulated` são entregues aos modelos com seu domínio; os modelos revisados só
  admitem `simulated_offline` e a referência assume.
- Decisões de uma referência (baseline) levam `in_contract = True` e `fallback_used = True`.

## 6. Desempenho (máquina de 2 núcleos, controladores de referência)

| Medida | Valor |
|---|---|
| Tick completo de 10 fibras (entrada + processamento) | média **≈ 183 ms**, p95 ≈ 207 ms, máx. ≈ 300 ms (alvo < 400 ms) |
| Geração de entrada, 10 fibras | ≈ 42 ms |
| Processamento, 10 fibras | ≈ 141 ms (≈ 13 ms por fibra só nos 500 passos de polarização) |
| Detector, por fibra por tick (features + inferência em lote) | ≈ 0,8 ms (alvo ≤ 15 ms; a referência em lote custa ≈ 115 ms) |
| Importar o pacote | não importa PySide6 (teste) |

Teste de regressão: mediana do processamento de 10 fibras < 1,5 s.

## 7. Limites conhecidos

- As três plantas são independentes (como o autor as validou): atacar a sessão não muda a onda de
  polarização; perder a porta não reduz a chave da sessão. São combinadas só em `stability` e `status`.
- Chave de bloco finito (`bb84_finite_key_length`) não é aplicada; o estoque soma a taxa assintótica decoy do
  período com estatística finita de contagem.
- Durante os 60 períodos de linha de base de cada sessão o detector não opina (campos `None`); a chave desses
  períodos é contada, salvo alarme retido de sessão anterior.
- Uma recalibração no meio de um ataque põe o ataque dentro da nova linha de base: o detector deixa de vê-lo
  (limitação do método de linha de base própria da sessão).

## 8. Roteamento e realocação (`routing.py`)

**Regra de disponibilidade** (um único lugar: `block_reasons`; `effective_graph` e os sinalizadores do banco de
fibras saem dela): a fibra está no grafo efetivo quando `link.active` **e** nenhum extremo está isolado **e**
nenhum extremo está em recalibração **e** o status ao vivo não é `compromised`. Fibras `degraded`, `down` e
`no_input` **continuam** no grafo (a regra do projeto só tira fibra por estado do operador ou alarme de
ataque). Sem observáveis (`no_input`), a fibra é usável e leva a `nominal_pair_fidelity` configurada.
As arestas levam `length` (km), `link_id`, `fidelity` (estimativa atual ou nominal) e `fidelity_source`.

**Demandas** (`Demand(demand_id, a, b, min_fidelity)`): tabela `network_demands` do mesmo arquivo do journal,
por topologia; extremos precisam existir e ser diferentes; no máximo 16. Padrão de `min_fidelity`: **0,70**.

**Plano** (`Router.plan(demand, graph, seed=…) -> RoutePlan`): episódios reais do ambiente do autor
(`QuantumNetworkEnv`) na componente conexa da origem, com os comprimentos reais.

| Parâmetro da planta | Valor | Origem |
|---|---|---|
| memória | coerência 1,0 s; fidelidade bruta 0,90; razão de corte 0,1; 6 posições por nó | `routing_loop.make_config` (benchmark do autor), sem mudança |
| portas / medição; acoplamento; detector | 0,999 / 0,999; 0,7; 0,85 | idem |
| anúncio (heralding) | 4,1 travessias do vão por tentativa | idem (medido contra o SeQUeNCe) |
| `generate_budget_s`, `wait_slot_s`, `max_steps`, pares por fibra | 4 ms, 2 ms, 120, 3 | idem |
| custo por tentativa | 0,01 (variante `nominal`) | idem |
| limiar de fidelidade | o da demanda (0,70 por padrão) | 0,70 é a menor das três opções do autor (0,70/0,74/0,78): três saltos a 0,90 dão ≈ 0,73, e com 0,74 a heurística não entrega em rotas de 3 saltos (medido: 0 de 8 episódios em P3 e P4) |
| prazo | `max(65 ms · T_caminho / T_ref, 2 · saltos · 4 ms)` | 65 ms = ponto médio da faixa do autor (40–90 ms); `T = Σ tempo de ida-e-volta / p_sucesso` no caminho mais curto, `T_ref` o mesmo na cadeia revisada. Em vãos metropolitanos vale o piso (dois orçamentos de geração por salto): 16 ms para 2 saltos, 24 ms para 3 |
| episódios | quota fixa de 24 por avaliação de política; fallback de plano pode acrescentar mais 24 | trabalho declarado (`RouterConfig`), independente de carga/relógio; 120 ms é apenas alvo indicativo, não deadline físico |

Desde 12/10/2026, a quota integra o hash de entradas. `planning_ms` mede o custo real e não escolhe quantos episódios entrarão nas estatísticas; as sementes e os prazos físicos continuam explícitos. Ver [qualificação de concorrência e roteamento](../results/orionq_concurrency_and_routing_20261012.md).

O ambiente do autor tem **uma** fidelidade bruta para todo par anunciado. A estimativa por fibra
(`1 − 3·QBER/2` da sessão QKD) é outra grandeza e **não entra na planta**; ela vai nas arestas e aparece ao
lado do plano como `detail.link_estimate_fidelity` (cadeia de Werner com `bell_diagonal.swap` do autor, sem
decaimento de memória) — é a parte do painel de demanda que responde aos dados a cada segundo.

**Rota = caminho do par entregue.** O ambiente não informa de quais fibras o par entregue foi feito;
`run_episode` reconstrói a linhagem a partir do histórico de ações (GENERATE cria `(u, v)`; SWAP em `n` junta
os caminhos das duas entradas; PURIFY mantém o caminho do par conservado). A rota do plano é o caminho mais
frequente entre os episódios que entregaram (`route_share` diz a fração), com taxa de sucesso, fidelidade média
entregue, passos e latência médios.

**Controlador**: com ZIP `qnet` verificado, o agente (`ReplayRouter`: réplica revisada + `GreedyFidelityPolicy`
assumindo o passo recusado e o episódio após 3 recusas); sem ZIP, `baseline.greedy_fidelity`, e o registro diz
`model_state = unavailable`. Supervisão no nível do plano: se o agente não entrega nenhum episódio em grafo
conexo, a referência recebe o mesmo pedido antes de a demanda ser dada como bloqueada (`fallback_used = True`,
motivo no registro).

**`in_contract`** só é `True` quando a planta **é** a revisada (cadeia de 4 nós com vãos de 20 km, pedido
0→3, limiar e prazo dentro das faixas do autor, e `check_contract` do export quando há ZIP). Na topologia de
Salvador é sempre `False`, quem quer que esteja decidindo — a heurística também só foi avaliada naquela cadeia.

**Quando se planeja**: demanda sem plano; rota que saiu do grafo efetivo (o gatilho nomeia o que a tirou:
`fibre X disabled`, `node Y isolated`, `node Y calibrating`, `fibre X compromised (attack alarm held)`); fibra
que voltou (todas as demandas são replanejadas na hora, para a rota poder voltar ao caminho melhor); comando do
operador (seção 12); fim de recalibração; e replanejamento periódico a cada 30 ticks, escalonado entre as
demandas. Cada plano que muda rota ou status gera um `Decision` (`reroute` ou `no_route`) e um evento; a linha
do journal leva em `detail` o gatilho, a rota antiga, a nova e os números do plano.

**Status da demanda**: `routed`; `rerouted` (a rota mudou no último plano e ainda não foi confirmada por um
plano periódico); `no_route`; `blocked`; `idle` (ainda não planejada).

### Sem rota (`no_route`) e bloqueada (`blocked`)

- `no_route`: origem e destino desconexos no grafo efetivo. Nenhum episódio é rodado. O motivo nomeia o que
  bloqueia: `explanation.cut` é o conjunto de fibras configuradas que saem da parte da rede ainda alcançável
  pela origem — todas indisponíveis, e todo caminho até o destino cruza uma delas —, cada uma com seus motivos;
  `shortest_path` e `shortest_path_blockers` dão o caminho mais curto da rede configurada e o que está
  indisponível nele. Se nem a rede configurada liga os dois nós, o motivo diz isso.
- `blocked`: conexos, mas nenhum episódio entregou par com a fidelidade pedida dentro do prazo
  (`success_rate = 0`, com a contagem de episódios e de como terminaram). É o caso real de rotas alternativas
  de 4 ou mais saltos a 0,90 de fidelidade bruta: a heurística purifica, o prazo acaba.

### Operações de par (`request_pair`, `purify_pair`, `consume_pair`)

`request_pair` roda **um** episódio real restrito às fibras da rota atual e guarda o par (fidelidade entregue,
saltos, nós onde houve troca, espera em memória, latência, passos, estado Bell-diagonal). `purify_pair` roda um
segundo episódio para obter a segunda cópia e aplica a forma fechada do autor (`bell_diagonal.dejmps`, o
protocolo e o ruído de porta da planta); o sucesso é sorteado com a probabilidade calculada — se falha, as duas
cópias se perdem. `consume_pair` marca o par como consumido. Nada disso altera a topologia.

## 9. Efeito dos falsos alarmes nas rotas (medido)

Três execuções de 300 s da demonstração (11 fibras, P1–P4, sem comando do operador), controladores de
referência:

| Semente | Alarmes de ataque levantados | Mudanças de rota/status | Tempo-demanda sem entrega (`blocked`/`no_route`) | Tempo-fibra `compromised` |
|---|---|---|---|---|
| 20261004 | 3 | 8 | 13 % | 4,3 % |
| 7 | 2 | 4 | 11 % | 3,1 % |
| 42 | 3 | 3 | 7,6 % | 5,3 % |

A linha do tempo da demonstração só tem ataques a partir de t = 185 s; parte dos alarmes acima veio antes
(t = 63 s, 134 s) — são falsos alarmes do detector revisado, na taxa já descrita na seção 4. Cada alarme tira
a fibra do grafo por ≥ 30 s; as demandas que passam por ela são realocadas no mesmo tick e voltam quando o
alarme é liberado. Quando a alternativa tem 4+ saltos a demanda fica `blocked` (não `rerouted`); `no_route`
apareceu uma vez (N-09 com as duas fibras sob alarme). **As rotas mudam por alarmes falsos**: é o
comportamento real do detector combinado com a regra do projeto, e não foi atenuado.

## 10. Alocação híbrida (`allocation.py`)

O alocador aprendido revisado (`AllocationReplayModel`) **não é executado** nesta topologia. O caminho de
código dele não é por fibra: é uma planta fixa de onze vãos nomeados (corredor São Paulo–Rio) com perfis
(comprimento, taxa peneirada, contagens por janela, potência clássica) vindos do `plant.json` do export; exige
esses nomes nessa ordem, período de 5 s com 30 s de latência e o protocolo `single_photon_bb84` — recusa
sessões decoy por contrato. Remapear vãos seria inventar uma planta. O papel aparece como `unavailable` (sem
ZIP) ou `fallback` (com ZIP), com o motivo "plant differs from the reviewed 11-link plant", e quem decide é a
referência do autor, `QuantumFirst`, pelo próprio `act`.

`QuantumFirst` silencia o canal clássico onde há "trabalho quântico pendente" (`backlog_bits > 0`). O motor
não tem livro de demandas QKD; a observação é montada do que o processamento mediu, com este mapeamento
declarado: `backlog_bits` = chave que a fibra deve no período — falta da chave secreta do período em relação
à calibração do comissionamento, quando a fibra está `degraded`, `down` ou `compromised`, mais a chave
descartada sob alarme; zero em fibra `up`. Resultado em `LinkLive.classical_admission`:
`{level: 0.0|1.0, controller_id, model_state, backlog_bits, reason, in_contract: False, fallback_used: True}`.
Fibra sem observáveis (ou `disabled | isolated | calibrating`): `None`, nenhuma decisão. Um `Decision`
(`allocation`) é gravado quando a admissão de uma fibra muda.

## 11. Recalibração completa de nó (`calibration.py`)

Procedimento de vários ticks conduzido pelo motor, um nó por vez. Fases (`CalibrationLive.phase`):

| Fase | Tick | O que acontece |
|---|---|---|
| `queued` | no comando | pedido aceito; o nó sai do grafo efetivo e as demandas que passam por ele são realocadas na hora; mede-se o "antes" de cada fibra incidente ativa (`LinkBank.measurement`) |
| `polarisation` | 1 | controladores, porta e linha de base do detector de cada fibra são reiniciados e a sessão é comissionada de novo (`reset_link` + `recommission`); registra o resíduo após o alinhamento inicial |
| `sync` | 2 | reaquisição da porta: resíduo de tempo e eficiência |
| `commissioning` | 3 | comissionamento decoy-BB84 para o comprimento (`commission(length_km)`), ao lado dos ajustes que a sessão está de fato usando |
| `memory` | 4 | reinício da memória do repetidor (persistido com o relatório) |
| `report` → `complete` | 5 | verificação: medição "depois" (≥ 4 períodos após o reinício), veredito por fibra (`ok` / `degraded` / `no_observables`), gravação pelo `record_node_calibration(request, report)` com o pedido **original**; o nó volta ao grafo e as demandas são realocadas de novo |

Enquanto dura, as fibras incidentes ficam `calibrating` (nada é publicado; os laços rodam por dentro).
Com a fonte ociosa não há observáveis: os campos medidos do relatório são `null`, o status da fibra é
`no_observables` e `observables` é `"unavailable"` — nada é inventado. Nó sem fibra: só o reinício da memória.

**Conflito de versão.** O store incrementa a versão ao gravar o relatório e recusa a gravação se a topologia
mudou depois do pedido. Escolha: **o motor segura as mudanças de topologia enquanto a recalibração roda**
(`set_node_isolation`, `set_link_active`, `set_node_location`, `add_network_node`, outra recalibração e troca
de fonte são recusados com a mensagem "node X is being recalibrated … topology changes are held"), tanto em
`engine.command` quanto nos repasses do serviço. O pedido nunca é reancorado em outra versão: o hash dele é o
que a reconciliação compara. Se a topologia for alterada mesmo assim (store usado diretamente, outro processo),
a gravação falha: o procedimento termina `failed` com esse motivo, **nada é persistido**, o nó volta ao grafo e
as fibras ficam com as sessões reiniciadas. Um pedido com versão antiga é recusado antes de começar.

## 12. Orquestrador (`engine.py`)

`NetworkEngine(store, journal_path, *, source, registry, topology_id, seed)`. `step()` avança um segundo de
rede: topologia → quadro de entrada → banco de fibras → recalibração → alocação → eventos → grafo efetivo →
roteamento → agregação por nó → journal → `LiveState`. `live_state()` devolve o último estado sem avançar e sem
esperar o tick em curso. O tick 0 é o estado de configuração (demandas já planejadas, nada processado).

**Mesmo fluxo para qualquer fonte**: o motor não consulta o tipo da fonte em nenhum ponto do processamento
(teste). Com `IdleInputSource` as fibras ficam `no_input`, o roteamento usa os valores configurados e todo
campo medido é `None`.

**Comandos** (`command(verb, payload) -> dict`; erro ou recusa = `ValueError`, sem efeito):

| Verbo | Campos | Resultado (além de `verb`, `status`, `tick`, `topology_version`, `affected_demands`, `decisions`, `physical_action_performed: False`) |
|---|---|---|
| `set_link_active` | `link_id`, `active` | `status: "persisted"`, `topology`, `trigger` |
| `set_node_isolation` | `node_id`, `isolated` | idem |
| `set_node_location` | `node_id`, `latitude`, `longitude`, `place` | idem |
| `add_network_node` | `node_id`, `role`, `address`, `port`, `latitude`, `longitude`, `place` | idem |
| `recalibrate_node` | `node_id` | `status: "started"`, `calibration` (avança um passo por `step()`) |
| `define_pair` | `demand_id`, `a`, `b`, [`min_fidelity`] | `status: "persisted"`, `demand` |
| `remove_pair` | `demand_id` | `status: "persisted"`, `demand` |
| `request_pair` | `demand_id` | `status: "delivered" \| "failed" \| "no_route" \| "blocked"`, `pair`, [`reason`] |
| `purify_pair` | `demand_id` | `status: "delivered" \| "purification_failed" \| "no_second_copy"`, `pair` |
| `consume_pair` | `demand_id` | `status: "consumed"`, `pair` |
| `inject_input_event` | `kind`, `link_id`, [`strength`, `duration_s`, `profile`] | `status: "accepted"`, `event` (só fonte simulada) |
| `set_model_archive` | `kind`, `path` | `status` = estado do arquivo (`verified`/`rejected`/`absent`), `archive` |
| `configure_network_input` | `kind`, [`seed`, `scenario`, `path`] | `status: "configured"`, `source` |

Os verbos de topologia chamam os métodos do store (sem duplicar a lógica) e replanejam na hora: o efeito de
desligar uma fibra ou isolar um nó está no `live_state()` seguinte, sem esperar o próximo segundo. Nos verbos
de topologia, `expected_version` e `topology_id` podem ser omitidos (o motor usa os atuais); se vierem, valem
as regras do store. Mudanças feitas direto no store são adotadas no tick seguinte.

**Journal de decisões**: tabela `network_decisions` (`sequence`, `decision_id` único, `tick`, `kind`,
`subject`, `record_json`, `record_sha256`, `detail_json`), só de inclusão, com retenção das últimas 50 mil
linhas. `recent_decisions(limit ≤ 500)` relê e confere o hash de cada linha. Todo registro leva
`physical_action_performed: False`.

**Eventos** (`NetEvent`, últimos 200): `attack_alarm_raised/cleared`, `link_down/degraded/recovered`,
`reroute`, `no_route`, `blocked`, `calibration_started/completed/failed`, `input_event_injected`,
`input_event_started/ended` (o que a fonte diz da entrada, `source: "input_source"`), `node_isolated/restored`,
`fibre_enabled/disabled`, `node_added`, `node_location_set`, `pair_delivered/failed/purified/consumed`,
`input_source_changed`.

**`NodeLive`**: papel, tipo visual (`core`/`repeater`/`node`), status (`isolated`, `calibrating`, `down` =
nenhuma fibra usável, `degraded` = alguma fibra degradada/sem chave/sob alarme, `up`), localização, endereço e
porta do inventário, fibras, memória e pares (do `SimulatedNodeState`), temperatura (média da temperatura do
cabo das fibras incidentes, que é um observável de entrada; `None` sem entrada), calibração, contagem de
calibrações e `routes_through`.

## 13. Topologia de referência da demonstração (`presets.py`)

`salvador_metro()`: os 8 nós e as 10 fibras do protótipo aprovado nas coordenadas de exemplo da tabela `GEO`
(todas em terra firme pelo mapa-base; teste), `R-07` e `CORE` repetidores (`CORE` com
`inventory.kind = "core"`), `distance_km` = distância geodésica, **todas as fibras ativas** — os estados fixos
"down/fault" do protótipo eram cenografia e não foram reproduzidos; falha agora só vem de evento de entrada ou
de comando. Mais dois nós sem localização, para a fila inferior: `N-12` (sem fibra) e `N-13` (fibra
`N05-N13` de 6,5 km configurados). Demandas padrão P1–P4 (`N-01↔N-05`, `N-03↔N-05`, `N-01↔N-03`, `N-09↔N-03`).
`ensure_demo_workspace(store)` grava a topologia e as demandas só quando o journal ainda não tem topologia.

## 14. API de serviço (`AssuranceDashboardService`)

| Método | Efeito |
|---|---|
| `network_engine` | motor preso ao journal e ao store do serviço; criado no primeiro uso, com `IdleInputSource` |
| `configure_network_input(kind, *, seed=None, scenario=None, path=None)` | `simulated` (cenários `demo`, `quiet`), `none`; `measured` é recusado (não há fonte medida nesta versão) |
| `network_live_state()` | `LiveState.to_dict()` do último tick; não avança a rede |
| `network_step()` | avança um segundo e devolve o novo estado |
| `network_command(verb, payload)` | os comandos da seção 12 |
| `network_decisions(limit)` | linhas recentes do journal de decisões |
| `recalibrate_node(payload, *, tick_interval_s=0.0)` | **bloqueante**: inicia a recalibração e avança o motor até ela gravar o relatório (5 segundos de rede); `tick_interval_s` é a pausa de relógio entre esses ticks — o console passa o período do seu stepper (1 s), para a rede manter a cadência e cada fase ficar publicada por um período; devolve `{status: "persisted", physical_action_performed: False, topology, calibration}` — o formato que a leitura de confirmação dos comandos rastreados exige (`versão == expected_version + 1`) |

Os repasses `add_network_node`, `set_node_location`, `set_node_isolation` e `set_link_active` agora recusam o
comando enquanto houver recalibração em curso e avisam o motor (se ele existir) para realocar na hora.

**Modo de demonstração**: `demo_service(repository_root, state_root, *, seed, scenario)` constrói **a mesma
classe de serviço** sobre `<state_root>/demo/sandbox_journal.sqlite`, chama `ensure_demo_workspace` e configura
`SimulatedInputSource`. A operação normal usa o journal do operador e a fonte ociosa. O motor é a mesma classe e
o mesmo `step()`; só a fonte e o arquivo de estado mudam.

**Threads**: uma thread chama `network_step()` uma vez por segundo; `network_live_state()` pode ser chamado
de qualquer thread e não espera o tick. `network_command` e `recalibrate_node` usam o mesmo cadeado do tick
(esperam o tick em curso terminar). Durante `recalibrate_node` a trava é tomada por tick, então as fases
aparecem para quem lê o estado. O tempo de rede é contado em ticks, não medido: avançar mais rápido só faz o
tempo de rede andar mais rápido.

## 15. Desempenho e limites da rede (máquina de 2 núcleos, controladores de referência)

| Medida | Valor |
|---|---|
| Tick da demonstração (11 fibras, 4 demandas, sem replanejar) | mediana **≈ 200 ms**, p95 ≈ 250–275 ms |
| Pior tick em 300 s (replanejando demandas bloqueadas) | ≈ 400–500 ms |
| Primeiro tick (comissiona as plantas de entrada das 11 fibras) | ≈ 1,0 s |
| Plano de uma demanda roteável (24 episódios) | ≈ 30–50 ms |
| Plano de uma demanda bloqueada (para no orçamento) | ≈ 120–140 ms |
| `LiveState.to_dict()` | ≈ 6–7 ms |
| Recalibração bloqueante com entrada simulada (5 ticks) | ≈ 1,5 s sem pausa (`tick_interval_s=0`); ≈ 7 s no console (um tick por segundo) |
| Console em execução (`app.exec()`), demonstração: ticks por segundo de relógio | 1,00 (medido em 167 s de uso contínuo: 172 ticks) |
| Aplicar um snapshot na interface | mediana ≈ 5 ms; p95 ≈ 50 ms na Topologia, ≈ 130 ms na Telemetria; ≈ 180 ms quando chegam muitos eventos de uma vez |

Limites e ressalvas:

- **O agente de roteamento está fora de contrato nesta topologia** e, pelos resultados publicados, **não supera
  a heurística** nem na cadeia revisada. Os ZIPs com os pesos não estão nesta máquina: tudo o que foi medido
  aqui usou `baseline.greedy_fidelity`. O caminho com agente foi exercitado só com um arquivo de teste
  (pesos não treinados).
- A heurística segue o caminho de menos quilômetros do grafo efetivo; a rota não depende do QBER das fibras
  enquanto não há alarme. A resposta do roteamento a dados é: alarme de ataque → fibra fora → realocação.
- Rotas alternativas longas ficam `blocked` com o limiar 0,70 e fidelidade bruta 0,90 (planta do autor).
- A planta de roteamento (pares em memória) e a sessão QKD de cada fibra são modelos independentes do autor;
  só o alarme e o estado operacional da fibra ligam um ao outro.
- A alocação é `QuantumFirst` com o mapeamento de "chave devida" declarado na seção 10; não há tráfego
  clássico simulado nem ruído Raman realimentado na sessão.
- O par guardado por `request_pair` é o resultado do episódio no instante da entrega; ele não envelhece no
  journal.
- `kind: "measured"` não existe; `set_model_archive` vale de imediato para roteamento e alocação, e para os
  controladores por fibra na próxima vez em que a sessão for comissionada.

## 16. Estado publicado: limiares, motivos e auditoria da regra "toda saída vem do backend" (2026-10-05)

**`LiveState.policy`** — todo estado publicado traz os limiares que **este** motor aplica, lidos dos objetos que
os usam (`NetworkEngine.declared_policy()`): `qber_down` e `fidelity_at_qber_down` (o `werner_fidelity` do
próprio motor), `degraded_key_fraction`, `degraded_gate_fraction`, `alarm_clear_periods`,
`stability_window_periods`, `wave_rate_hz`, `wave_window_s`, `history_window_s`, `replan_period_ticks`,
`default_min_fidelity`, `tick_s`. Um motor criado com outro `LinkPolicy` publica os seus valores (teste
`test_live_state_publishes_the_thresholds_the_running_engine_applies`). A interface lê daí a linha "fibra fora"
da Telemetria e a taxa da onda; não importa mais `LinkPolicy` dos módulos do backend.

**Motivo de demanda bloqueada** — o texto é montado no backend e legível:
`N-01 and N-05 are connected but no episode delivered a pair at fidelity >= 0.7 within 24.0 ms (0 of 24 episodes
delivered; episodes ended by: deadline × 21, max steps × 3)` (`routing.outcome_counts`; todas as contagens são
mantidas, só a notação mudou — antes aparecia um `{'deadline': 21, ...}` cru).

**Auditoria (o que foi procurado e o que foi achado)**

| Verificação | Resultado |
|---|---|
| Geração aleatória / senoidal / por tempo usada para exibir algo no caminho serviço ou demonstração | nenhuma. `random`/`math.sin` só existem em `console/providers.py` (`MockProvider`) e `console/synthetic*.py`, que são fixtures de teste rotulados. O `cos/sin` de `service_provider._topology` é o layout lógico da visão GRAPH. |
| Caminho do login "Demonstração" até `SyntheticProvider`/`MockProvider` | não existe: `ConsoleWindow.source_factory("demo")` → `ServiceProvider(demo_service(...))`. **Corrigido**: `MockProvider` (`--provider mock` do módulo `console.app`) não se declarava fixture — o shell o rotulava "SERVICE" e a faixa dizia `RING 04 · MEASURED`; agora declara `is_synthetic`, `input_kind="synthetic"` e `RING 04 · UI FIXTURE`. |
| Números fixos exibidos como dado | **corrigido**: a Telemetria importava `LinkPolicy().qber_down` (padrão do módulo) em vez do valor do motor em execução → `LiveState.policy`. A taxa da onda (20 Hz) e a janela do histórico (300 s) também passaram a vir do estado. |
| Mudança otimista de estado antes da leitura de confirmação | nenhuma: todo verbo mostra `PENDING` e a tela só muda com o snapshot seguinte (`test_console_topology_live`, `topology_validation`, roteiro ponta a ponta: "a fibra ainda aparece habilitada depois do clique"). |
| Temporizadores que animam algo além de (a) rolar amostras já recebidas e (b) sondar | nenhum. `WaveChart` revela a última entrega e para na última amostra; `Toast`, cães de guarda de diálogos e `ui_gc` não desenham dado. |
| Roteamento / física na interface | nenhum. Contas da interface: projeção e geodésica para desenhar (`geodata`, "Straight line on map"), formatação, e os agregados **rotulados** da faixa da Topologia ("median of N fibres", "sum of N fibres"). Para fontes **sem motor** (fixtures, arquivo medido) a matriz ainda colore a fidelidade por faixas fixas (0,93 / 0,90) e a tela Nodes colore pares por 0,90 / 0,65; com motor a cor vem do `status` do backend. |
| Seleção de nó deixava o provedor ocupado | **corrigido** (seção 3 do contrato, desvio 10): tela piscava a cada clique. |
| Recalibração adiantava o tempo de rede (5 ticks em ~1,5 s) | **corrigido**: `tick_interval_s`. |
| Verdade oculta do simulador (`LinkInput.reference`, `true_*`) lida depois da fonte | não: só `sources.py` a escreve; nada em `links.py`, `calibration.py`, `routing.py`, `allocation.py`, `engine.py` ou no console a lê (busca textual + teste existente). |
| Eventos de entrada (`frame.events`) | viram `NetEvent` com `source = "input_source"`: são a declaração da FONTE sobre a entrada ("input intercept_resend on X started"), exibidos como tal na linha do tempo de entrada; nenhum controlador os recebe e o detector não os vê. |
| Ramo por tipo de fonte no processamento | nenhum em `step`, `_reallocate`, `_publish`, `_refresh` (teste por inspeção de código + mesma sequência de chamadas). `links.domain_of(frame.kind)` só traduz o tipo da fonte no **domínio de evidência** entregue aos modelos revisados, que recusam o que não for `simulated_offline` pelo próprio contrato. |
| Registros "shadow · PURIFY/HALT" que aparecem logo ao abrir | são reais: o replay em modo shadow do modelo WDM congelado (`run_wdm_model_replay`, 25 ciclos) executado pelo serviço na primeira montagem do snapshot e gravado no journal. Rotulados `SHADOW REPLAY`. |
| `recalibrate` antigo (ciclo shadow com `F = 0,90`, incerteza `0,01` fixos) | continua existindo para fontes sem motor, rotulado `console-fixed-input-diagnostic`; com motor, `recalibrate <nó>` despacha `recalibrate_node` (recalibração completa). |
