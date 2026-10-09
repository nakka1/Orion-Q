# ORION-Q · Rede ao vivo, GEO de Salvador e modo de demonstração — entrega

Data: 2026-10-05 · Base de comparação: commit `6577713` · Versão do pacote: 5.38.0 (não alterada)
Verificado em Linux, Python 3.13, Qt offscreen. **Nenhum executável Windows foi reconstruído.**

> Nota sobre a numeração: recebi literalmente apenas o texto do requisito 7. Os itens 1–6 abaixo seguem os
> temas do contrato `docs/product/orion_q_live_network_design.md`; se a numeração original for outra, o conteúdo
> é o mesmo e só a ordem muda.

## Resumo por requisito

### 1. Topologia abre no mapa real de Salvador (GEO), em escala
- **Arquivos**: `console/geodata.py`, `console/geo/salvador_v1.json` (+ `SOURCES.md`), `console/widgets/geo_map.py`,
  `console/widgets/figures.py`, `console/screens/topology.py`, `control_plane/geodesy.py`.
- **O que faz**: mapa vetorial offline (terra, município, vias, rótulos, atribuição), uma escala para os dois
  eixos, barra de escala e norte; nós nas coordenadas declaradas; fibra = geodésica. Rótulos dos nós trocam de
  lado para não se cobrirem. Faixa de cabeçalho em uma linha a 1440 px.
- **Provas**: `tests/test_console_geodata.py` (24), `tests/test_console_topology_live.py::test_demo_opens_on_geo_after_login`,
  `tests/test_console_screens.py::test_map_labels_move_instead_of_covering_a_neighbour`, self-test
  (`geo_is_the_default_view`, `geo_shows_the_located_nodes`, `salvador_basemap_bundled`).

### 2. Matrix e Graph mostram os mesmos nós e fibras
- **Arquivos**: `console/widgets/network_views.py` (um modelo para as três visões).
- **Provas**: `test_console_topology_live.py`, `test_functional_topology.py` (23), self-test `matrix_ids`,
  `geo_and_queue_ids`, `matrix_lists_every_node_and_fibre`.

### 3. Nós sem localização ficam na fila; a criação pede a localização (opcional)
- **Arquivos**: `control_plane/inventory.py` e `topology.py` (verbos `add_network_node`, `set_node_location`),
  `console/widgets/unplaced_queue.py`, `location_dialog.py`, `location_fields.py`, `console/screens/node.py`.
- **Provas**: `tests/test_network_inventory.py` (207), `test_console_topology_live.py::test_set_location_moves_a_queued_node_onto_the_map_with_its_links`,
  `topology_validation.py` (30 verificações no self-test), roteiro ponta a ponta (passo `add_nodes_queue_and_map`).

### 4. Clique no nó abre janela flutuante com todos os dados e o gráfico de onda por conexão
- **Arquivos**: `console/widgets/node_window.py`, `wave_chart.py`.
- **O que faz**: desenha só amostras entregues pelo backend; entre entregas apenas rola as já recebidas.
- **Provas**: `test_console_topology_live.py`, self-test `node_window_wave_is_backend_samples`, roteiro
  (`click_node_floating_window_wave`: toda amostra desenhada pertence a `LinkLive.wave`).

### 5. Operações reais com realocação: desligar fibra, isolar, recalibrar; modelos em ação
- **Arquivos**: `network_engine/` (`engine.py`, `links.py`, `routing.py`, `calibration.py`, `allocation.py`,
  `models.py`, `sources.py`, `state.py`, `presets.py`), `qkd/dashboard_api.py`, `console/service_provider.py`,
  `console/live_commands.py`, `console/widgets/inspector.py`, `demo_panel.py`, `live_qkd.py`,
  `console/screens/telemetry.py`, `keys.py`, `fpga.py`, `audit.py`.
- **Provas**: `tests/test_network_engine_*.py` (110 em 7 arquivos), `tests/test_console_live_provider.py` (19),
  `tests/test_console_screens_live.py` (27), `tests/test_control_plane_topology.py` (18).

### 6. Modo de demonstração escolhido no login; tema e moldura do protótipo
- **Arquivos**: `console/theme.py`, `shell.py`, `screens/boot.py`, `widgets/chrome.py`, `journal.py`, `palette.py`,
  `console/app.py`, `desktop.py` (`--demo`, `--qnet-archive`, `--qkd-archive`).
- **Provas**: `tests/test_console_shell.py` (10), `tests/test_desktop_app.py` (6), `tests/test_orion_console.py` (14).

### 7. "A única simulação permitida é a geração dos dados de entrada…"
Auditoria independente de `src/quantum_autopilot/console/**` e do motor; tabela completa em
`docs/product/orion_q_network_engine.md` §16.

| Achado | Correção | Prova |
|---|---|---|
| Linha de limiar da Telemetria lia `LinkPolicy()` (padrão do módulo), não o motor em execução | `LiveState.policy` → `LiveView.policy`; a tela lê do estado | `test_network_engine_engine.py::test_live_state_publishes_the_thresholds_the_running_engine_applies`, `test_console_screens_live.py::test_telemetry_plots_the_backend_history_point_for_point` |
| `MockProvider` (`--provider mock`) aparecia como "SERVICE" e `RING 04 · MEASURED` | declara `is_synthetic`, `input_kind="synthetic"`, `UI FIXTURE` | `test_console_screens.py::test_mock_fixture_declares_itself_and_cannot_pass_for_a_data_source` |
| Clique em nó deixava o provedor ocupado (tela piscava) | seleção respondida com os registros já lidos | `test_console_polling.py::test_selection_during_background_read_is_immediate_and_never_busy` |
| Recalibração adiantava o tempo de rede (5 ticks em ~1,5 s) | `tick_interval_s` = período do stepper | `test_network_engine_calibration.py::test_blocking_recalibration_can_keep_the_network_cadence` |
| Motivo de demanda bloqueada com `{'deadline': 3}` cru | `routing.outcome_counts` (backend) | `test_network_engine_routing.py::test_blocked_reason_is_written_for_an_operator_and_keeps_every_count` |
| Taxa da onda (20 Hz) e janela do histórico fixas na interface | lidas de `LiveState.policy` | self-test `thresholds_published_by_the_engine` |

Sem violação encontrada: nenhum caminho do login "Demonstração" até `SyntheticProvider`/`MockProvider`; nenhuma
mudança otimista de estado; nenhum temporizador anima operação; nenhuma conta de rota ou física na interface;
`LinkInput.reference` (verdade oculta) não é lida fora de `sources.py`; nenhum ramo por tipo de fonte em
`step`/`_reallocate`/`_publish`/`_refresh`
(`test_same_processing_objects_for_simulated_and_idle_sources`).

Restam, declarados: agregados **rotulados** na faixa da Topologia ("median of N fibres", "sum of N fibres");
para fontes **sem motor** (fixtures, arquivo medido) a matriz e a tela Nodes ainda colorem por faixas fixas.

## Verificação

| Grupo | Resultado |
|---|---|
| Console, motor, plano de controle (79 arquivos) | **2289 aprovados, 0 falhas**, 3 ignorados |
| Restante de `tests/` (157 arquivos) | 1946 aprovados, 14 falhas + 1 erro de coleta pré-existentes, 8 ignorados |
| `orion-q --self-test` | **25/25** (inclui 17 da demonstração, 76 responsivas, 30 + 12 funcionais) |
| `--stress-test` (20 rodadas, semente 1337) | aprovado |
| `--acceptance-test` | o ponto de entrada roda; fora de um pacote congelado termina `failed` por falta de `SHA256SUMS.txt`, como esperado |
| Roteiro ponta a ponta (`/home/claude/shots/e2e_demo.py`) | **aprovado**: 316 s, 14 passos, 1 tick/s (151 ticks em 149 s), aplicação de snapshot p50 5 ms / p95 41 ms / máx. 573 ms (troca de fonte), RSS 830 → 1004 MB (+0,2 MB em 149 s de uso contínuo), 0 exceções, 0 avisos do Qt, 0 threads ao fechar |
| Compatibilidade 3.10 | `ruff --target-version py310 --select E9,F,UP036` limpo no console e no motor; `vermin`: mínimo 3.10 no pacote inteiro |

### Falhas pré-existentes (idênticas na base `6577713`, executada em cópia exportada)
- `tests/test_vendored_qnet.py` (2): `compat/_gmpy2_shim.py` editado depois de vendorizado.
- `tests/test_laboneq_emulation.py` (5), `tests/test_roadmap_frozen_commands.py` (1): sondas de processo.
- `tests/test_polarisation_loop.py` (2): valores de referência diferem na 6ª casa.
- `tests/test_multiple_comparisons.py` (2), `tests/test_causal_analysis.py` (coleta): falta `statsmodels`.
- `tests/test_telemetry_interface.py` (2): falta `pyarrow`/`fastparquet`.
- `tests/test_attempt_file.py::test_read_only_ui_keeps_scientific_state_and_clears_failed_preview`: falhou uma vez
  com dois lotes de testes em paralelo; passa sozinho (3/3). Corrida de tempo do próprio teste, arquivo não tocado.

### Falhas "conhecidas" que deixaram de existir
`tests/test_qkd_dashboard_api.py` (3), `test_console_integration.py::test_qkd_mapping_uses_a_verified_published_cell`,
`test_qkd_operating_point.py` (2), `test_qkd_claim_registry_adapter.py` (1) e a verificação `evidence_valid` do
self-test falhavam só porque `outputs/qkd_coexistence_measured/experimental_cells.csv` não estava no checkout.
O próprio repositório o restaura: **`python tools/prepare_console_resources.py`** (primeiro passo do build do
Windows; copia de `runtime_assets/native/v1`, confere o SHA-256, nunca sobrescreve). Executei esse passo; o
arquivo fica em `outputs/`, que é ignorado pelo git.

## Ressalvas
1. Sem os dois ZIPs do autor, todo papel aprendido exceto o detector roda a referência nomeada.
2. Agente de roteamento fora de contrato nesta topologia; detector fora do domínio de treino nas fibras curtas.
3. Falsos alarmes do detector mudam rotas (≈ 0,5–2,9 por fibra-hora em rede quieta).
4. Desvio com 4+ saltos termina `blocked`; o alocador aprendido não roda (decide `QuantumFirst`).
5. O detector só opina 60 s depois de a sessão da fibra ser comissionada ou recalibrada.
6. `tools/build_windows_console.ps1` e `packaging/console.spec` foram atualizados e **não foram executados**.
7. O teste instável `test_every_screen_accepts_reference_and_wide_frames` não se reproduziu em ~130 execuções;
   a asserção agora nomeia o widget culpado e `FlowLayout` não deixa um item passar da largura da linha.
8. `qkd_example_available` do self-test é verdadeiro por vacuidade quando a grade não carrega (já era assim).
9. `e2e_demo.py` lê `/proc/self/status` (só Linux).

## Arquivos (em relação a `6577713`)

Total: 123 arquivos — 46 novos, 77 modificados, 0 removidos. `git diff --stat 6577713`: 122 arquivos, +31330 / −5039 (mais o guia novo, ainda não rastreado).

### Novos
- `docs/product/orion_q_demo_guide.md`
- `docs/product/orion_q_live_network_design.md`
- `docs/product/orion_q_network_engine.md`
- `src/quantum_autopilot/console/geo/SOURCES.md`
- `src/quantum_autopilot/console/geo/salvador_v1.json`
- `src/quantum_autopilot/console/geodata.py`
- `src/quantum_autopilot/console/live_commands.py`
- `src/quantum_autopilot/console/ui_gc.py`
- `src/quantum_autopilot/console/widgets/cards.py`
- `src/quantum_autopilot/console/widgets/charts.py`
- `src/quantum_autopilot/console/widgets/demo_panel.py`
- `src/quantum_autopilot/console/widgets/geo_map.py`
- `src/quantum_autopilot/console/widgets/inspector.py`
- `src/quantum_autopilot/console/widgets/journal.py`
- `src/quantum_autopilot/console/widgets/live_qkd.py`
- `src/quantum_autopilot/console/widgets/location_dialog.py`
- `src/quantum_autopilot/console/widgets/location_fields.py`
- `src/quantum_autopilot/console/widgets/node_window.py`
- `src/quantum_autopilot/console/widgets/palette.py`
- `src/quantum_autopilot/console/widgets/toast.py`
- `src/quantum_autopilot/console/widgets/unplaced_queue.py`
- `src/quantum_autopilot/console/widgets/wave_chart.py`
- `src/quantum_autopilot/control_plane/geodesy.py`
- `src/quantum_autopilot/network_engine/__init__.py`
- `src/quantum_autopilot/network_engine/allocation.py`
- `src/quantum_autopilot/network_engine/calibration.py`
- `src/quantum_autopilot/network_engine/engine.py`
- `src/quantum_autopilot/network_engine/links.py`
- `src/quantum_autopilot/network_engine/models.py`
- `src/quantum_autopilot/network_engine/presets.py`
- `src/quantum_autopilot/network_engine/routing.py`
- `src/quantum_autopilot/network_engine/sources.py`
- `src/quantum_autopilot/network_engine/state.py`
- `tests/test_console_geodata.py`
- `tests/test_console_live_provider.py`
- `tests/test_console_screens_live.py`
- `tests/test_console_shell.py`
- `tests/test_console_topology_live.py`
- `tests/test_network_engine_calibration.py`
- `tests/test_network_engine_engine.py`
- `tests/test_network_engine_links.py`
- `tests/test_network_engine_models.py`
- `tests/test_network_engine_routing.py`
- `tests/test_network_engine_service.py`
- `tests/test_network_engine_sources.py`
- `tests/test_network_inventory.py`

### Modificados
- `README.md`
- `docs/history.md`
- `docs/product/input_sources.md`
- `docs/product/native_console.md`
- `docs/product/orion_q_capabilities_q01.md`
- `docs/product/orion_q_console.md`
- `docs/product/orion_q_inventory_q03.md`
- `packaging/console.spec`
- `pyproject.toml`
- `src/quantum_autopilot/console/__init__.py`
- `src/quantum_autopilot/console/app.py`
- `src/quantum_autopilot/console/capabilities.py`
- `src/quantum_autopilot/console/contract.py`
- `src/quantum_autopilot/console/providers.py`
- `src/quantum_autopilot/console/responsive_validation.py`
- `src/quantum_autopilot/console/screens/__init__.py`
- `src/quantum_autopilot/console/screens/audit.py`
- `src/quantum_autopilot/console/screens/base.py`
- `src/quantum_autopilot/console/screens/boot.py`
- `src/quantum_autopilot/console/screens/fpga.py`
- `src/quantum_autopilot/console/screens/keys.py`
- `src/quantum_autopilot/console/screens/node.py`
- `src/quantum_autopilot/console/screens/splash.py`
- `src/quantum_autopilot/console/screens/telemetry.py`
- `src/quantum_autopilot/console/screens/topology.py`
- `src/quantum_autopilot/console/service_provider.py`
- `src/quantum_autopilot/console/shell.py`
- `src/quantum_autopilot/console/source_switch.py`
- `src/quantum_autopilot/console/stage.py`
- `src/quantum_autopilot/console/synthetic.py`
- `src/quantum_autopilot/console/theme.py`
- `src/quantum_autopilot/console/topology_validation.py`
- `src/quantum_autopilot/console/validation.py`
- `src/quantum_autopilot/console/widgets/__init__.py`
- `src/quantum_autopilot/console/widgets/adaptive_dialog.py`
- `src/quantum_autopilot/console/widgets/allocation_shadow.py`
- `src/quantum_autopilot/console/widgets/campaign_manifest_editor.py`
- `src/quantum_autopilot/console/widgets/campaign_runner.py`
- `src/quantum_autopilot/console/widgets/chrome.py`
- `src/quantum_autopilot/console/widgets/command_history.py`
- `src/quantum_autopilot/console/widgets/compensation_shadow.py`
- `src/quantum_autopilot/console/widgets/figures.py`
- `src/quantum_autopilot/console/widgets/keyrate_shadow.py`
- `src/quantum_autopilot/console/widgets/link_editor.py`
- `src/quantum_autopilot/console/widgets/model_catalog.py`
- `src/quantum_autopilot/console/widgets/network_views.py`
- `src/quantum_autopilot/console/widgets/path_runner.py`
- `src/quantum_autopilot/console/widgets/polarisation_control.py`
- `src/quantum_autopilot/console/widgets/policy_activation_confirmation.py`
- `src/quantum_autopilot/console/widgets/policy_approval_confirmation.py`
- `src/quantum_autopilot/console/widgets/policy_context.py`
- `src/quantum_autopilot/console/widgets/policy_decisions.py`
- `src/quantum_autopilot/console/widgets/policy_recovery_confirmation.py`
- `src/quantum_autopilot/console/widgets/policy_review.py`
- `src/quantum_autopilot/console/widgets/prims.py`
- `src/quantum_autopilot/console/widgets/records.py`
- `src/quantum_autopilot/console/widgets/routing_shadow.py`
- `src/quantum_autopilot/console/widgets/sync_session.py`
- `src/quantum_autopilot/console/widgets/sync_shadow.py`
- `src/quantum_autopilot/control_plane/command_reconciliation.py`
- `src/quantum_autopilot/control_plane/console_commands.py`
- `src/quantum_autopilot/control_plane/inventory.py`
- `src/quantum_autopilot/control_plane/topology.py`
- `src/quantum_autopilot/desktop.py`
- `src/quantum_autopilot/qkd/dashboard_api.py`
- `tests/conftest.py`
- `tests/test_console_input_sources.py`
- `tests/test_console_integration.py`
- `tests/test_console_inventory.py`
- `tests/test_console_link_editor.py`
- `tests/test_console_observations.py`
- `tests/test_console_polling.py`
- `tests/test_console_screens.py`
- `tests/test_console_temporal_campaign.py`
- `tests/test_functional_topology.py`
- `tests/test_orion_console.py`
- `tools/build_windows_console.ps1`

### Fora do repositório
- `/home/claude/shots/e2e_demo.py`, `/home/claude/shots/e2e/*.png` (38) e `/home/claude/shots/e2e/log.json`
