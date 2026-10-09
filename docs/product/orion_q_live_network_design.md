# ORION-Q · Rede ao vivo, GEO de Salvador e modo de demonstração — projeto

Atualização implementada (2026-10-05): o console agora usa o cenário nacional
`brazil-institutions-v1`, documentado no [guia de demonstração](orion_q_demo_guide.md).
São sete locais institucionais, mapa offline do IBGE, foco por campus e oito
enlaces hipotéticos com comprimentos geodésicos. O switch confirmado de ML
controla a permissão dos cinco papéis de controle, com persistência, readback,
recibo e fallback; o detector permanece diagnóstico. O novo journal é
`<state-root>/demo/brazil-institutions-v1/sandbox_journal.sqlite`.
As referências metropolitanas abaixo descrevem o projeto anterior, ainda
disponível pela API. Nenhuma conexão ou equipamento real é descoberto ou acionado.

Estado: **implementado** (2026-10-05). Este documento é o contrato entre os módulos; as seções "Desvios"
registram onde a implementação decidiu algo além do texto original, e a seção 4 a verificação final.
Guia do operador: [`orion_q_demo_guide.md`](orion_q_demo_guide.md).
Referência visual: protótipo "ORION-Q Workspace (opção 5)" (`Main.dc.html`).

## 0. Regras que não mudam

1. **Nenhuma atuação física.** Tudo roda em `simulation`; todo registro carrega
   `physical_action_performed: False`. Nenhum socket é aberto.
2. **Toda saída exibida vem do backend.** A interface não calcula física, não decide rota,
   não anima operação que não foi executada. A única animação permitida é a rolagem de
   amostras que o backend já entregou (gráfico de onda).
3. **A única simulação é a geração dos dados de entrada**, feita pelo motor do backend
   (`SimulatedInputSource`). Depois disso, o fluxo é o mesmo da operação normal.
4. **Honestidade de domínio.** Quando um modelo roda fora do domínio em que foi validado
   (ex.: agente de roteamento fora da cadeia de 4 nós), o registro da decisão diz
   `in_contract: False` e a interface mostra isso. Modelo sem pesos verificados aparece como
   `unavailable` e o controlador de referência (baseline) assume, identificado pelo nome.
5. Pacotes vendorizados (`src/qnet_network`, `src/qnet_qkd`, `external/`) **não são editados**.
6. Compatível com Python 3.10+. Sem dependências novas além das já declaradas.

## 1. Modelo de topologia (persistido, `SimulatedTopologyStore`)

Sem campos novos nos dataclasses (`SimulatedNodeState`, `SimulatedLink`): registros antigos
continuam válidos. Dados novos entram em `SimulatedNodeState.telemetry`:

```python
telemetry["location"] = {              # ausente ⇒ node SEM localização (fica na fila)
    "latitude": float,                 # graus decimais, -90..90
    "longitude": float,                # graus decimais, -180..180
    "place": str,                      # rótulo livre, ≤ 80 caracteres
    "source": "operator_declared",
    "set_at_utc": iso8601,
}
telemetry["inventory"]["kind"] = "core"   # opcional; papel visual (backend segue endpoint|repeater)
telemetry["console_control"] = {       # já existe; chaves novas:
    ...,
    "isolated": bool,
    "isolation_disabled_links": [link_id, ...],   # o que o isolamento desligou (para restaurar)
    "calibration": {                   # último relatório de recalibração completa
        "calibration_id": str, "completed_at_utc": iso, "duration_s": float,
        "links": {link_id: {"qber_before": f, "qber_after": f, "sync_residual_ps_before": f,
                             "sync_residual_ps_after": f, "signal_intensity": f,
                             "decoy_intensity": f, "key_rate_hz_after": f, "status": str}},
        "memory_reset": True, "report_sha256": hex,
    },
}
```

**Distância.** `control_plane/geodesy.py` fornece `geodesic_km(lat1, lon1, lat2, lon2)`
(Vincenty WGS-84 com queda para haversine). Quando os dois extremos de uma fibra têm
localização, `SimulatedLink.distance_km` **é** a distância geodésica (recalculada na mesma
transação em que a localização muda). Se um extremo não tem localização, vale o valor
configurado pelo operador no editor de fibras.

**Disponibilidade efetiva de uma fibra** = `link.active` e nenhum extremo isolado e nenhum
extremo em recalibração e sem alarme de ataque retido. `effective_graph(topology, live)`
no motor é a única fonte dessa regra.

### Verbos novos (todos rastreados em `console_commands`, um incremento de versão cada)

| Verbo | Contrato (`control_plane/inventory.py`) | Efeito |
|---|---|---|
| `add_network_node` | `AddNetworkNode(node_id, role, address, port, latitude, longitude, place, expected_version, topology_id)` — lat/lon ambos `None` ou ambos válidos | cria node, com ou sem localização |
| `set_node_location` | `SetNodeLocation(node_id, latitude, longitude, place, expected_version, topology_id)` — `None/None` devolve o node à fila | posiciona; recalcula `distance_km` das fibras incidentes; preserva dados e conexões |
| `set_node_isolation` | `SetNodeIsolation(node_id, isolated, expected_version, topology_id)` | isola (desliga fibras incidentes ativas e guarda a lista) ou restaura exatamente as que o isolamento desligou |
| `set_link_active` | `SetLinkActive(link_id, active, expected_version, topology_id)` | ativa/desativa uma fibra; não mexe em isolamento |
| `recalibrate_node` | `RecalibrateNode(node_id, expected_version, topology_id)` | recalibração completa executada pelo motor (§2.4); grava o relatório |

Os verbos antigos (`add_simulated_node`, `configure_simulated_link`,
`apply_simulated_node_action`) continuam funcionando.

Eventos de topologia novos: `network_node_added`, `node_location_set`, `node_isolated`,
`node_restored`, `link_activated`, `link_deactivated`, `node_recalibrated`.

### Desvios (implementação da §1, 2026-10-04)

Pontos em que a implementação precisou decidir algo que a tabela acima não dizia, ou foi além dela.
Detalhes e limites em [`orion_q_inventory_q03.md`](orion_q_inventory_q03.md), seção "Verbos da rede ao vivo".

1. **`place` sem coordenadas é recusado.** Em `AddNetworkNode` e `SetNodeLocation`, quando
   `latitude`/`longitude` são `None`, `place` precisa ser `""` — não existe onde guardar um rótulo
   sem coordenadas, e descartá-lo em silêncio esconderia o dado. `place` aceita só texto imprimível
   (sem quebras de linha/controle), ≤ 80 caracteres. Inteiros em `latitude`/`longitude` são aceitos e
   normalizados para `float`; `bool` é recusado.
2. **`configure_simulated_link` também respeita a regra da distância.** Entre dois nodes localizados
   o valor gravado é o geodésico e o `distance_km` do pedido é ignorado (o evento registra
   `requested_distance_km` e `distance_source: "geodesic_from_node_locations"`). Sem isso, uma fibra
   criada entre dois nodes já posicionados ficaria com o número digitado até a próxima mudança de
   localização. A interface não precisa (nem deve) calcular a distância. A reconciliação compara com
   o valor geodésico nesse caso.
3. **Distância gravada com resolução de milímetro** (`round(km, 6)`), para o valor persistido ser
   estável; `geodesic_km` em si não arredonda.
4. **Remover a localização não devolve a distância antiga.** A fibra mantém o último valor (o
   geodésico) até o operador editá-la; o valor digitado antes do posicionamento não é guardado.
5. **Restauração com vizinho ainda isolado.** Uma fibra registrada em `isolation_disabled_links`
   cujo outro extremo continua isolado não é religada: o registro passa para o vizinho
   (`deferred_links: [{link_id, held_by}]` no evento `node_restored`) e a fibra volta quando ele for
   restaurado — exatamente uma vez, em qualquer ordem de restauração.
6. **Religação manual pelo editor de fibras encerra o isolamento sem restaurar.** O comportamento
   antigo foi mantido (`configure_simulated_link` com `active=True` limpa `isolated` dos extremos);
   nesse caso `isolation_disabled_links` do node é esvaziada e as demais fibras ficam desligadas até
   serem ligadas uma a uma. `set_link_active(active=True)` nunca faz isso: é recusado enquanto um
   extremo estiver isolado.
7. **`recalibrate_node` e a versão.** O incremento de versão acontece quando o motor chama
   `record_node_calibration(request, report)`, ao fim da recalibração. Se a topologia mudar entre o
   pedido e a gravação, a gravação falha com conflito de versão (nada é gravado) e o comando não pode
   ser CONFIRMED; o motor precisa tratar isso como resultado da recalibração.
8. **Relatório de calibração**: objeto JSON não vazio, finito, chaves string, ≤ 16 KiB em JSON
   canônico; o store acrescenta `report_sha256` (se o motor já enviar um, ele precisa coincidir).
   O store não exige as chaves listadas acima — o formato é responsabilidade do motor.
9. `telemetry["inventory"]["kind"]` não é gravado por nenhum verbo desta seção (o conjunto de campos
   de `AddNetworkNode` é o da tabela).

## 2. Motor de rede (`src/quantum_autopilot/network_engine/`)

Pacote novo, sem Qt. Depende de numpy/pandas/networkx e do código do autor em `qnet_*`.

```
network_engine/
  __init__.py        # API pública
  state.py           # dataclasses congelados do LiveState (JSON-serializáveis)
  sources.py         # InputSource, SimulatedInputSource, IdleInputSource, (MeasuredInputSource)
  models.py          # ModelRegistry: localiza/verifica arquivos, instancia controladores
  links.py           # processamento por fibra: polarização, sincronismo, QKD, detector
  routing.py         # grafo efetivo, demandas, realocação (agente + supervisor + baseline)
  calibration.py     # procedimento de recalibração completa
  engine.py          # NetworkEngine: step(), comandos, journal de decisões
  presets.py         # topologia de referência "Salvador metro" (demo)
```

### 2.1 Fonte de entrada

```python
class InputSource(Protocol):
    kind: str            # "simulated" | "measured" | "none"
    label: str
    def frame(self, tick: int, topology: SimulatedTopology) -> InputFrame: ...
    def inject(self, event: InputEvent) -> None: ...     # só a fonte simulada aceita
```

`InputFrame` traz, por fibra, **observáveis brutos** de 1 s de rede: registro de sessão
decoy-BB84 (as 17 colunas públicas de `detector_session`), rotações de polarização medidas e
covariáveis (temperatura, dT/dt, vibração) em cadência de 2 ms, offset de relógio medido e
temperatura. `SimulatedInputSource(seed, scenario)` gera isso com as plantas do autor
(`polarisation_plant`, `ClockModel`, `qnet_qkd.qkd.session`/`physics.decoy`,
`qnet_qkd.qkd.attacks`), parametrizadas pelo **comprimento real da fibra**. O cenário da
demonstração tem uma linha do tempo de eventos de entrada (deriva térmica, evento de perda,
ataque intercept-resend / PNS / blinding em uma fibra) e aceita injeções do operador
(`inject_input_event`). `IdleInputSource` não entrega observáveis: a operação normal sem
alimentação mostra só configuração e resultados de roteamento, sem séries.

### 2.2 Processamento (idêntico para qualquer fonte)

A cada `step()` (1 s de tempo de rede), para cada fibra efetiva:

1. **Compensação de polarização** — controlador ativo (`qnet.compensation.*` se o ZIP `qnet`
   foi verificado e o checkpoint foi admitido; senão `baseline.alpha_beta`) sob
   `PolarisationSupervisor`; resultado: resíduo, QBER de polarização, traço de estabilidade.
2. **Sincronismo** — `qkd.sync.ridge.1s.h2s` (ZIP `qkd`) ou `AlphaBetaTracker`; resultado:
   resíduo de tempo, eficiência de janela.
3. **Taxa de chave** — `qkd.keyrate.actor` (ZIP `qkd`) ou controlador estático; ajusta
   intensidades/clock; resultado: SKR, ajustes do transmissor.
4. **Detector de ataques** — `SafeDetector()` (pesos no repositório), features causais;
   resultado: classe, probabilidade, alarme sustentado. Alarme ⇒ fibra `compromised`:
   chave do período descartada, fibra sai do grafo efetivo, realocação disparada.
5. **Estimativas por fibra** — QBER total, fidelidade Werner `F = 1 − 3·QBER/2`, SKR,
   `stability ∈ [0,1]` e a onda `wave` (20 amostras/s de visibilidade `1 − 2·QBER`).

Depois, na rede:

6. **Roteamento / realocação** — para cada demanda (par fim-a-fim), se a rota atual não está
   no grafo efetivo (fibra desativada, node isolado/recalibrando, alarme) ou não existe:
   reconstrói `EnvironmentConfig` do autor com o grafo efetivo (comprimentos reais), roda
   episódios com o agente (`qnet.routing.*`, ZIP `qnet`) supervisionado, com
   `GreedyFidelityPolicy` como reserva; rota = caminho do par entregue. Sem caminho no grafo
   ⇒ `status="no_route"` com o motivo. Cada realocação gera um `Decision`.
7. **Alocação híbrida** — `qnet.allocator.*` decide admissão de tráfego clássico por fibra
   (protege a chave); `QuantumFirst` é a reserva.

Tudo o que um controlador decide vira `Decision` e é gravado na tabela `network_decisions`
do journal (hash por registro, `physical_action_performed: False`).

### 2.3 `LiveState` (em `state.py`)

```python
LiveState(schema="orion.network-live.v1", tick, at_utc, network_time_s, topology_version,
          source: SourceInfo(kind, label, seed, scenario, note),
          links: tuple[LinkLive, ...], nodes: tuple[NodeLive, ...],
          demands: tuple[DemandLive, ...], decisions: tuple[Decision, ...],   # últimas 200
          models: tuple[ModelStatus, ...], events: tuple[NetEvent, ...])

LinkLive(link_id, a, b, distance_km, status,           # up|degraded|down|disabled|isolated|calibrating|compromised|no_input
         qber, fidelity, skr_bps, sifted_rate_hz, sync_residual_ps, gate_efficiency,
         polarisation_residual_mrad, stability, wave: tuple[float, ...],      # 20 Hz, últimos 12 s
         history: LinkHistory(t_s, qber, fidelity, skr_bps),                 # 1 Hz, últimos 300 s
         detector: DetectorLive(label, attack_probability, alarm, baseline_alarm),
         qkd: QkdLive(protocol, signal_intensity, decoy_intensity, clock_rate_hz, signal_gain,
                      decoy_gain, key_pool_bits, discarded_bits, last_action),
         controllers: dict[str, str], classical_admission, provenance)

NodeLive(node_id, role, kind, status,                  # up|degraded|down|isolated|calibrating
         located: bool, latitude, longitude, place, links: tuple[str, ...],
         memory_state, pairs, temperature_c, calibration: CalibrationLive | None,
         calibration_count, routes_through: tuple[str, ...], address, port)

DemandLive(demand_id, a, b, status,                    # routed|rerouted|no_route|blocked|idle
           route: tuple[str, ...], route_links, fidelity, success_rate, policy_id,
           in_contract: bool, fallback_used: bool, reason, changed_at_tick)

Decision(decision_id, tick, at_utc, kind,              # reroute|compensation|sync|keyrate|detector|allocation|calibration|no_route
         controller_id, model_state, subject, summary, inputs_sha256, outcome,
         in_contract, fallback_used, record_sha256)

ModelStatus(model_id, role, state,                     # active|fallback|unavailable
            reason, archive_sha256, in_contract, inferences, faults, last_latency_ms)
```

Valores ausentes são `None` (a interface mostra "—"); nunca se preenche um número inventado.

### 2.4 Recalibração completa (`calibration.py`)

`recalibrate_node` executa, para o node e cada fibra incidente ativa: (a) node entra em
`calibrating` e sai do grafo efetivo — as demandas que passavam por ele são realocadas;
(b) realinhamento de polarização (reinicia o controlador, mede o alinhamento inicial);
(c) reaquisição de sincronismo (reinicia o rastreador, mede o offset); (d) comissionamento
decoy-BB84 para o comprimento da fibra (busca dos ajustes, como em `keyrate_loop.make_link`);
(e) reinício da memória do repetidor; (f) grava o relatório antes/depois em
`console_control.calibration`, incrementa `calibration_count`, devolve o node ao grafo e
realoca de novo. Dura alguns ticks de rede; o estado intermediário aparece no `LiveState`.

### 2.5 Serviço (`AssuranceDashboardService`)

```python
service.network_engine                      # NetworkEngine preso ao mesmo journal/topology store
service.configure_network_input(kind, *, seed=None, scenario=None, path=None) -> dict
service.network_live_state() -> dict        # LiveState.to_dict()
service.network_step() -> dict              # avança um tick (chamado pelo provedor em thread própria)
service.network_command(verb, payload) -> dict   # verbos da §1 + request_pair/purify_pair/consume_pair/
                                                 # define_pair/remove_pair/inject_input_event/set_model_archive
```

**Modo de demonstração** = estado próprio (`state_root/demo/sandbox_journal.sqlite`),
topologia de referência `presets.salvador_metro()` e `SimulatedInputSource`. **Operação
normal** = journal do operador, topologia do operador e a fonte escolhida (`none` por padrão).
O código de processamento é o mesmo objeto `NetworkEngine`.

### Desvios (implementação da §2 — roteamento, alocação, recalibração, motor e serviço, 2026-10-04)

Detalhes em [`orion_q_network_engine.md`](orion_q_network_engine.md), seções 8–15.

1. **`effective_graph(topology, link_live, calibrating_nodes)`** recebe os estados das fibras e os nós em
   recalibração em vez de um `live` único. `degraded`, `down` e `no_input` ficam no grafo; só estado do
   operador e alarme de ataque tiram a fibra.
2. **A estimativa de fidelidade por fibra não entra na planta de roteamento.** O ambiente do autor tem uma
   fidelidade bruta única (0,90); a estimativa vai nas arestas e é mostrada como
   `DemandLive.detail.link_estimate_fidelity`.
3. **`in_contract` do roteamento descreve a planta**, também quando a referência decide: fora da cadeia
   revisada de 4 nós é `False` (nos registros por fibra, uma referência leva `in_contract = True`).
4. **Limiar padrão 0,70 e prazo escalado** (`max(65 ms · T_caminho/T_ref, 2 · saltos · 4 ms)`); o protótipo
   usava 0,900, inalcançável com fidelidade bruta 0,90 sem purificação dentro do prazo.
5. **Status `blocked`** = conexo, mas nenhum episódio entregou; `rerouted` vale até o próximo plano periódico
   confirmar a rota. Planos de demandas bloqueadas também geram `Decision` de tipo `no_route`
   (`outcome = "blocked"`), porque `DECISION_KINDS` não tem um tipo próprio.
6. **Supervisão no nível do plano**: se o agente não entrega nenhum episódio, a referência planeja a rota
   (`fallback_used = True`).
7. **Alocador aprendido não roda** (planta fixa de 11 vãos, 5 s, BB84 de fóton único): `QuantumFirst` decide,
   com o mapeamento de "chave devida" declarado; `classical_admission` é um dicionário, `None` sem observáveis.
8. **Fases da recalibração** seguem `CALIBRATION_PHASES` (`queued`, `polarisation`, `sync`, `commissioning`,
   `memory`, `report`, `complete`): "isolar do roteamento" é o `queued` e "verificar + gravar" é o `report`.
   No tick em que termina, as fibras do nó ainda aparecem `calibrating` (voltam a ser publicadas no seguinte),
   mas já estão no grafo efetivo.
9. **Topologia segura durante a recalibração**: os verbos de topologia são recusados enquanto ela roda
   (motor e repasses do serviço); uma recalibração por vez; conflito de versão vindo de fora termina em
   `failed`, sem gravar nada.
10. **`service.recalibrate_node(payload)` é bloqueante** (avança o motor até o relatório ser gravado), para
    caber no ciclo dos comandos rastreados; `engine.command("recalibrate_node", …)` é a forma assíncrona.
11. **`DemandLive` ganhou `min_fidelity`, `pair` e `detail`**; a tabela `network_decisions` ganhou
    `detail_json` (gatilho, rota antiga/nova, números do plano); há `service.network_decisions(limit)`.
12. **Operações de par** não mexem em `SimulatedNodeState`: o par fica na tabela `network_demands`.
    `purify_pair` usa a forma fechada do ambiente do autor (`bell_diagonal.dejmps`), não o BBPSSW de
    `quantum_twin`, para manter o mesmo ruído de porta da planta.
13. **`MeasuredInputSource` não existe**: `configure_network_input("measured")` é recusado.
14. **Demonstração**: `demo_service(...)` cria outra instância do serviço sobre o journal de demonstração; a
    topologia de referência inclui dois nós sem localização (`N-12`, `N-13`) além dos oito do protótipo.

## 3. Interface (PySide6)

- **Tema**: tokens do protótipo, claro e escuro (`theme.py`), Inter + JetBrains Mono.
- **Shell**: barra superior (marca, trilha, Ctrl K, tema, fonte), trilho de ícones (6 telas +
  sair), terminal/journal recolhível, barra de status. Paleta de comandos.
- **Login** (`boot`): sequência de inicialização, operador local, **seleção da fonte de dados
  com "Demonstração"**, limites de execução, "Open workspace →".
- **Topology**: abre em **GEO** (mapa real de Salvador, projeção local em escala, barra de
  escala). **Matrix** mostra os mesmos nodes/fibras. Fila inferior com os nodes sem
  localização, sem linhas geográficas. As conexões permanecem nos detalhes abertos ao clicar
  no node e nas vistas lógicas. Inspetor (node/fibra), eventos, fibras, pares. Clique no node
  abre a **janela flutuante** com todos os dados e o gráfico de onda por conexão.
- **Nodes**: formulário de criação pede a localização (opcional).
- **Edge control**: decisões dos modelos ao vivo, estado de cada modelo, QKD ao vivo.

A interface só lê `Snapshot` (contrato em `console/contract.py`, campo novo `live`) e só
despacha verbos declarados nas capacidades do adaptador.

### Desvios (implementação da §3 — provedor, contrato `Snapshot.live`, comandos e modo de demonstração, 2026-10-04)

Detalhes em [`native_console.md`](native_console.md) ("Live network view"), [`input_sources.md`](input_sources.md) e
[`orion_q_capabilities_q01.md`](orion_q_capabilities_q01.md).

1. **`Snapshot.live: LiveView | None`** (`console/contract.py`) espelha o `LiveState` com nomes de interface
   (`LiveLink.id`, `LiveNode.id`, `LiveDemand.id`, `LiveDecision.id`, `LiveEvent.id`, `LiveModel.id`; `at` em vez de
   `at_utc`). O mapeamento (`service_provider.live_view_from_state`) só renomeia e reagrupa: `controllers` vira
   uma tupla de `LiveController(role, controller_id, model_state)`, `classical_admission` vira `LiveAdmission`,
   `provenance["domain"]` vira `domain_notes`, `provenance["status_reasons"]` vira `status_reasons`, e o registro
   original fica inteiro em `LiveLink.provenance`. Números continuam números; ausente é `None`.
2. **Campos que o provedor acrescenta sem calcular física**: `LiveLink.configured_active` (o `active` da topologia
   persistida), `LiveLink.effective` (`status` fora de `disabled | isolated | calibrating | compromised` — a regra
   do item 1 dos desvios da §2), `LiveLink.wave_rate_hz = 20.0`, `LiveLink.input` e `display_provenance`
   (`simulated` | `measured` | `assumed`).
3. **`Link.fidelity/qber/skr` com motor**: textos formatados a partir da estimativa do motor (`0.9655`, `2.30 %`,
   `3.864 Mbit/s`) ou "—". A fidelidade nominal configurada não aparece mais nesse campo (continua em
   `Link.nominal_pair_fidelity`). Sem motor (adaptadores antigos) vale o comportamento anterior.
4. **Uma fibra religada ou restaurada mostra o próprio status só no segundo de rede seguinte**: no mesmo tick do
   comando o motor ainda publica `disabled`/`isolated` para ela (já com `configured_active = True`). A interface
   não corrige isso; lê o próximo estado.
5. **Recusa do backend em verbo rastreado termina em `UNKNOWN`**, não em `REJECTED`: o diário de comandos só
   aceita `REJECTED` antes do despacho. A mensagem do backend segue no sinal `error`
   (`Command <id>: outcome UNKNOWN; inspect topology before retrying. <mensagem do backend>`) e a topologia
   não muda de versão.
6. **`configure_network_input` não é declarado por nenhum adaptador do console**: a origem da entrada é escolhida
   pela fonte na tela de login (Demonstração = entrada simulada; Serviço local = sem alimentação).
7. **Cadência**: snapshot a cada 1 s; enquanto um comando ocupa o trabalhador o estado do motor é publicado a
   cada 200 ms, só quando o tick mudou. O thread `console-network-step` fica suspenso durante
   `recalibrate_node`, que dirige os próprios ticks **na mesma cadência** (`tick_interval_s` = período do
   stepper, 1 s no aplicativo): a recalibração leva ~7 s de relógio, cada fase fica publicada por um período e o
   tempo de rede não corre à frente do relógio.
8. **Coleta de lixo só no thread da interface** (`console/ui_gc.py`, e o mesmo em `tests/conftest.py`): uma coleta
   cíclica em thread de trabalho que libera wrappers do PySide6 vira exclusão dupla no Shiboken
   (`runDeletionInMainThread`) e derrubava o processo; era a causa da falha de segmentação conhecida de
   `tests/test_console_policy_context.py`.
9. **`SyntheticProvider` deixou de ser a demonstração**: só `--synthetic` / `--provider synthetic` e os validadores
   internos chegam a ele; `ConsoleWindow.active_source_key()` devolve `"synthetic"` (nenhuma linha do login marcada).
10. **Seleção de nó é estado de interface.** `select_node` é respondido com os registros já lidos (mesmo
    mapeamento `_node` da montagem completa), sem comando no backend e sem estado ocupado: clicar em um nó não
    desabilita as telas. Uma leitura que já estava em curso termina com o nó que o operador escolheu.
11. **Limiares vêm do estado publicado.** `LiveState.policy` (→ `LiveView.policy`) traz os limiares que o motor
    em execução aplica (`qber_down`, `fidelity_at_qber_down`, janelas de onda e histórico, período de replano).
    A linha "fibra fora" da Telemetria lê esse valor; nada é importado de `network_engine.links` pela interface e
    um motor que não publica o limiar não ganha linha.
12. **Faixa de cabeçalho da Topologia em uma linha**: compactação em degraus (ações antigas atrás de `⋯`, depois
    rótulos curtos, depois só contagens com as palavras nos tooltips). A 1440 px o primeiro degrau basta.
13. **Rótulos dos nós no mapa** escolhem o lado (direita, esquerda, abaixo, acima) que não cobre outro nó ou
    rótulo. Só o texto muda de lugar.
14. **Fixtures de interface rotulados.** `MockProvider` e `SyntheticProvider` declaram `is_synthetic`,
    `input_kind="synthetic"` e aparecem como `SYNTHETIC · UI FIXTURE`; só `--provider mock|synthetic` /
    `--synthetic` chegam a eles. Nenhum caminho leva de "Demonstração" a um deles.

## 4. Verificação final (2026-10-05)

- **Regra "toda saída vem do backend"** — auditoria independente do pacote `console/` e do motor; achados e
  correções em [`orion_q_network_engine.md`](orion_q_network_engine.md) §16.
- **Mesmo caminho de código** — `tests/test_network_engine_engine.py::test_same_processing_objects_for_simulated_and_idle_sources`
  (mesmas chamadas, mesmos objetos, nenhum ramo por tipo de fonte) e
  `::test_live_state_publishes_the_thresholds_the_running_engine_applies`.
- **Ponta a ponta pela interface** — `validation.validate_demo_pipeline` (dentro do `--self-test`, também no
  executável congelado) e o roteiro de 5 min 30 s conduzido pelos widgets descrito no guia de demonstração:
  login → Demonstração → GEO → janela do nó com onda → fibra desligada/religada → isolar/restaurar →
  recalibrar (fases e relatório) → nó sem e com localização → ataque intercept-resend → detector → fibra
  comprometida → liberação → tema claro/escuro → Matrix, Keys, Telemetry → Serviço local (sem observáveis) →
  Demonstração. Cada passo é conferido na tela **e** contra `service.network_live_state()`.
