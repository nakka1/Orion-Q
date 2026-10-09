# ORION-Q — painéis flutuantes, leitura completa e atualização — 08/10/2026

A correção torna os campos longos legíveis e distribui a apresentação dos painéis entre turnos do Qt. A comparação normal em fonte registrou melhora de heartbeat e pintura do stage nas fases mapa, globo, abas e alternâncias. A fase de continuidade ainda ficou mais lenta que o baseline, e a memória residente aumentou. Portanto, as evidências mostram ganhos mistos; não demonstram eliminação de todas as pausas.

O produto continua em Python/PySide6. O candidato isolado C++ por C-ABI foi avaliado sem ganho suficiente para justificar migração; a avaliação de um renderer C++/Qt completo depende de SDK compatível e não integra esta correção. O EXE 5.52.0 concluiu os gates funcionais e a coleta GUI de três minutos de continuidade, com 60 readbacks completos iguais e sem violação do prazo de apresentação. O estado final de integridade e hashes do pacote é registrado em `qualification.json`, conforme a seção final.

## Configuração e origem das evidências

Foram executadas três coletas normais pelo mesmo harness, sem cProfile, hooks de métodos ou substituição global de `QApplication.notify`. As evidências são [before-normal](../../outputs/floating_panels_20261008/before-normal/report.json), [after-normal](../../outputs/floating_panels_20261008/after-normal/report.json) e [after-final-normal](../../outputs/floating_panels_20261008/after-final-normal/report.json). Aqui, “final” identifica o diretório da coleta; refinamentos posteriores têm hashes próprios e receberam medição separada no EXE, apresentada na seção final.

| Item | Configuração registrada |
| --- | --- |
| Sistema | Windows 11, build 10.0.26200 |
| CPU | AMD Ryzen 7 5700X, 8 núcleos físicos / 16 processadores lógicos |
| RAM visível pelo sistema | 34.280.902.656 bytes, aproximadamente 31,93 GiB |
| GPU | NVIDIA GeForce RTX 3060, 12 GiB, driver 616.64; counters compartilhados do dispositivo, sem atribuição ao ORION-Q |
| Python | 3.12.14, MSC v.1944, AMD64 |
| Qt/PySide6 | 6.11.2; QPA Windows nas coletas normais |
| Dependências | NumPy 2.5.2, SciPy 1.18.1, psutil 7.2.2, torch 2.13.0, cryptography 46.0.7 |
| Janela | Cliente 3440 × 1440 pixels lógicos; DPR 1,0 |
| Fonte de dados | Demonstração simulada; `provider_step_period_s=1.0` |
| Workload | `actual-qt-actions-v3`; ações em widgets reais do Qt |
| Duração solicitada | 20 s em cada fase mapa/globo/abas/alternâncias, mais 60 s de continuidade; 140 s planejados, além da inicialização |
| Heartbeat / recursos | Timer de 10 ms; amostragem externa de recursos a cada 0,5 s |
| Intervalo de threads Python | 0,005 s, sem alteração experimental |
| Baseline | Módulos compilados do PYZ preservado da release 5.51.0, usando as mesmas dependências da `.audit-winvenv`; `frozen=false` |
| Depois | Código fonte corrente em `app.run`, também `frozen=false` |

As três coletas terminaram com `exit_code=0`, `measurement_completed=true`, `source_unchanged=true` e nenhuma entrada em `errors`. Houve 30 seleções, 30 verificações de conteúdo completo e 10 navegações em cada coleta, sem divergências funcionais registradas; as 30 verificações de deadline passaram. Esses checks não substituem a inspeção de legibilidade: o baseline conservava strings completas no modelo, mesmo quando as mostrava cortadas.

O hardware foi registrado em [hardware.json](../../outputs/floating_panels_20261008/hardware.json); contexto da GPU está em [gpu-context.jsonl](../../outputs/floating_panels_20261008/after-final-normal/gpu-context.jsonl), com `attributed_to_orion_q=false`. Não é medição isolada da utilização de GPU deste aplicativo.

Os PIDs do operador 5956 e 25772 foram apenas observados. O launcher registra `user_owned_processes_modified=false`; o watchdog limita sua eventual interrupção ao novo PID diagnóstico. Os parâmetros de protocolos não foram modificados e `physical_action_performed=false` nas três coletas. Configurações e comandos completos permanecem nos respectivos `configuration.json` e `launcher.json`.

## Coletas normais: resultados mistos

Em cada célula abaixo, a ordem é **baseline / primeira tentativa / coleta final normal**. Heartbeat e pintura estão em ms; CPU é a média da árvore de processos em porcentagem; RSS é o p95 da árvore em MiB.

| Fase | Heartbeat p95 | Stage paint p95 | CPU média | RSS p95 |
| --- | ---: | ---: | ---: | ---: |
| Mapa | 129,65 / 123,72 / 106,19 | 50,47 / 50,29 / 49,09 | 111,57 / 119,79 / 118,13 | 756,52 / 753,01 / 782,95 |
| Globo | 117,92 / 115,83 / 103,23 | 74,35 / 64,99 / 65,94 | 118,80 / 120,93 / 119,67 | 741,39 / 748,30 / 781,11 |
| Abas | 53,30 / 57,67 / 49,31 | 74,61 / 79,81 / 61,92 | 92,14 / 95,07 / 93,69 | 756,01 / 768,54 / 801,25 |
| Alternâncias | 63,69 / 75,42 / 62,67 | 70,12 / 80,31 / 69,43 | 105,09 / 101,60 / 102,61 | 764,89 / 777,19 / 809,99 |
| Continuidade de 60 s | 66,35 / 83,85 / 78,26 | 58,12 / 74,82 / 65,88 | 106,68 / 110,57 / 109,99 | 774,35 / 787,84 / 820,19 |

A primeira tentativa regrediu heartbeat e pintura nas fases abas, alternâncias e continuidade. Nas abas, chegou a um intervalo máximo de heartbeat de 1.126,20 ms. O refinamento seguinte reduziu esse máximo para 282,04 ms, abaixo dos 383,43 ms do baseline, mas ainda ocorreram intervalos longos em outras fases.

| Fase | Heartbeat máximo: antes / tentativa / final (ms) | Intervalos > 250 ms: antes / tentativa / final |
| --- | ---: | ---: |
| Mapa | 698,19 / 646,80 / 565,54 | 6 / 9 / 8 |
| Globo | 426,31 / 376,78 / 414,81 | 2 / 2 / 3 |
| Abas | 383,43 / 1.126,20 / 282,04 | 3 / 3 / 1 |
| Alternâncias | 387,33 / 267,93 / 201,82 | 4 / 1 / 0 |
| Continuidade | 463,97 / 507,33 / 504,55 | 14 / 11 / 15 |

A coleta final normal recuperou as regressões da primeira tentativa em abas e alternâncias. Na continuidade, heartbeat p95 passou de 66,35 para 78,26 ms e stage paint p95 de 58,12 para 65,88 ms. O RSS p95 subiu em todas as fases frente ao baseline, inclusive de 774,35 para 820,19 MiB na continuidade; CPU média subiu em quatro das cinco fases. Esses custos permanecem registrados, sem atribuição causal isolada a um widget.

CPU da árvore pode ultrapassar 100% porque soma utilização de processos e núcleos; não é a porcentagem de ocupação do desktop inteiro. RSS da árvore soma o processo UI e seus processos filhos. As séries brutas estão em `heartbeat.csv`, `paints.csv` e `resources.jsonl` de cada coleta.

## Apresentação cooperativa e custo do quadro completo

`NodeWindow` conserva o timer de apresentação e recebe observações completas. No modo incremental, publica diferenças de seções e conexões em turnos cooperativos; mantém identidade de widgets, geração da publicação, origem e status de conteúdo pendente. Criações caras continuam limitadas por turno. A revisão local posterior agrupa até quatro atualizações pequenas de conexões existentes, ou encerra o turno ao atingir o orçamento nominal de 6 ms, e retoma pelo timer de 20 ms. Uma operação Qt individual não pode ser interrompida no meio pelo orçamento; seu máximo continua observável.

Mudanças visuais idênticas evitam reaplicar conteúdo. As gerações de seções/conexões e `presentation_status()` impedem tratar o primeiro feedback como apresentação completa. O rodapé identifica publicação anterior/pendências enquanto há trabalho, e a apresentação completa só é declarada quando as seções atuais estão preenchidas. Não há execução de instrumentos ou protocolos nesses widgets.

O ensaio [node-before](../../outputs/floating_panels_20261008/node-before/result.json) e suas três revisões posteriores usa um painel sintético com 24 conexões, 20 publicações e cProfile. Ele mede callback e espera até o conteúdo integral estar pronto; compara os textos com uma janela de referência síncrona em cada publicação.

| Revisão local | Callback p95 (ms) | Quadro completo pronto p95 (ms) | Readbacks |
| --- | ---: | ---: | --- |
| Antes | 10,817 | 10,893 | Todos iguais à referência |
| Primeira tentativa | 2,446 | 1.269,986 | Todos iguais à referência |
| Ajuste intermediário | 2,839 | 795,954 | Todos iguais à referência |
| [node-after-final](../../outputs/floating_panels_20261008/node-after-final/result.json) | 2,807 | 229,405 | Todos iguais à referência |

O callback final caiu cerca de 74% frente ao baseline, mas o quadro completo passou a demorar aproximadamente 229 ms no p95, contra 11 ms antes. Isso é um custo real da apresentação cooperativa e dos campos completos legíveis; o baseline também truncava conteúdo visual. O ensaio não isola quanto desse custo vem de cada alteração. A primeira tentativa, com mais de um segundo até o quadro completo, foi refinada antes da qualificação do pacote.

O heartbeat desse ensaio local inclui construir/destruir a janela síncrona de referência e tem profiler ativo. Portanto, não foi misturado com o heartbeat da aplicação normal. O ensaio verifica uma janela incremental por vez, embora crie a referência durante a comparação.

No painel satelital, preparar o estado continua síncrono e não cria um timer independente; `NodeWindow` controla a apresentação por seção. Seções iguais evitam `set_rows` redundante; estações existentes só mudam de posição quando a ordem muda, e removidas saem imediatamente do layout. Origem, revisão e tema participam dos checks de atualização, com retry após falha de apresentação.

## Layout: campos completos e rolagem do pai

`MetricTable` usava uma linha fixa, elisão à direita e até 65% da largura para a chave, deixando 35% para o valor. A correção usa `QTextLayout` com quebra por palavra ou caractere, respeita novas linhas e não elide. Chaves ocupam até 45% e valores ao menos 55% da largura. Cada linha acomoda chave e valor completos; a tabela conserva a altura integral para a rolagem do documento pai, sem criar um widget por célula.

| Fixture MetricTable | Células cortadas antes / depois | Altura antes / depois |
| --- | ---: | ---: |
| Host 350 px, viewport 326 px | 9 de 10 / 0 | 140 / 412 px |
| Host 520 px, viewport 496 px | 6 de 10 / 0 | 140 / 284 px |
| Host 780 px, viewport 756 px | 5 de 10 / 0 | 140 / 236 px |

Os layouts usados para medir e pintar compartilham cache limitado a 1.024 entradas, com texto/fonte/largura/alinhamento na chave. Texto inalterado e mudança somente de tom não recalculam alturas; um valor alterado mede somente sua linha. Mudar largura ou fonte refaz as medidas necessárias. Seleção, Ctrl+C, acessibilidade, unidades e identidades de registros foram preservados. Evidências: [metric_before.json](../../outputs/floating_panels_20261008/layout/metric_before.json), [metric_after.json](../../outputs/floating_panels_20261008/layout/metric_after.json) e [comparação visual](../../outputs/floating_panels_20261008/layout/metric_table_fix.md).

`WrappingLabel` usa texto simples integral, inclusive tags HTML literais e identificadores sem espaços. Mede a altura ao mudar texto/fonte/largura e conserva até oito larguras em cache, com uma fonte já resolvida para o layout. O `sizePolicy` não ativa height-for-width global. `KVRow` aplica essa apresentação por opt-in; o ajuste de altura/posição do pai é enfileirado para ocorrer após o passe de layout, com invalidação/reativação quando necessário. Os painéis de detalhes usam uma coluna para dar espaço aos campos.

A validação encontrou e corrigiu três problemas: Ctrl+C sem a importação de `QApplication`, altura do pai menor que os filhos e posições calculadas com a altura anterior após alternar larguras. Medir a altura do label sozinho não teria detectado os dois últimos; os testes verificam também contenção no pai e geometria estável.

| Fixture WrappingLabel/KVRow, fonte 200% | Campos cortados antes / depois | Alturas finais dos quatro campos |
| --- | ---: | --- |
| Host 180 px, dark e light | 4 de 4 / 0 | 607 / 1.027 / 432 / 397 px |
| Host 350 px, dark e light | 4 de 4 / 0 | 292 / 502 / 222 / 222 px |
| Host 518 px, dark e light | 4 de 4 / 0 | 187 / 327 / 187 / 117 px |

Essas alturas longas usam a rolagem do pai e conservam os dados recebidos. A suíte faz 800 resizes e 200 publicações nas combinações de tema/fonte, verifica larguras 180/350/518 px, fontes 125%/200%, cache limitado, fonte/alinhamento alterados após mostrar, texto/cópia integral e seis ciclos de eventos sem oscilação de geometria. Evidências: [before](../../outputs/floating_panels_20261008/wrapping/wrapping_before.json), [after](../../outputs/floating_panels_20261008/wrapping/wrapping_after.json), [validação](../../outputs/floating_panels_20261008/wrapping/wrapping_validation.md) e [documento completo em imagem](../../outputs/floating_panels_20261008/wrapping/wrapping_after_dark_518_document.png).

Os headings satelitais também impunham largura mínima excessiva. A fixture isolada de 211 campos tentou 518 px, mas cresceu para 867 px; depois permaneceu em 518 px. A medição abaixo caracteriza a correção conjunta, inclusive a apresentação diferente do `MetricTable`, sem isolar uma única alteração.

| Fixture SatelliteLivePanel, 12 publicações offscreen | Antes | Depois |
| --- | ---: | ---: |
| Largura efetiva | 867 px | 518 px |
| MinimumSizeHint de headings de seção | 701 / 749 / 867 / 796 px | 95 px em cada seção |
| MinimumSizeHint de headings das quatro estações | 662 px cada | 95 px cada |
| Campos completos | 211 | 211 |
| `set_state` p95 | 1,487 ms | 2,791 ms |
| `processEvents` p95 | 64,723 ms | 44,403 ms |

Fontes: [baseline satelital](../../outputs/floating_panels_20261008/satellite/baseline.json), [after satelital](../../outputs/floating_panels_20261008/satellite/after.json) e [escopo/testes](../../outputs/floating_panels_20261008/satellite/findings.md). A redução de `processEvents` local não é um número de latência do aplicativo completo.

## Verificação e rastreabilidade

| Execução registrada | Casos / falhas / errors / skips |
| --- | ---: |
| [gui-tests-final.xml](../../outputs/floating_panels_20261008/gui-tests-final.xml) | 220 / 0 / 0 / 0 |
| [final-target.xml](../../outputs/floating_panels_20261008/final-target.xml) | 76 / 0 / 0 / 0 |
| [final-layout.xml](../../outputs/floating_panels_20261008/final-layout.xml), após os últimos refinamentos | 180 / 0 / 0 / 0 |
| [floating-tick-readiness.xml](../../outputs/floating_panels_20261008/review/floating-tick-readiness.xml), checks complementares | 8 / 0 / 0 / 0 |
| [layout-tests5.xml](../../outputs/floating_panels_20261008/layout-tests5.xml) | 29 / 0 / 0 / 0 |
| MetricTable, execução local documentada | 18 / 0 / 0 / 0 |
| WrappingLabel/KVRow, execução local documentada | 10 / 0 / 0 / 0 |

A bateria final de fonte reúne **188 casos aprovados**: os 180 de `final-layout.xml` e oito checks complementares disjuntos em `floating-tick-readiness.xml`. As outras execuções da tabela se sobrepõem a essa bateria e não são somadas como casos únicos adicionais. As validações locais de layout usam fixtures sintéticas em Qt offscreen. Elas conservam textos recebidos e não certificam transmissão, qualidade física de canal ou resultado de laboratório.

O plano de ações das três coletas normais tem SHA-256 `41546e5203207d05f31ed41de57031cbf5460cad9b3f9e0a79375d9c71ad0541`. O harness tem SHA-256 `44a2d1d6ecbcdc6abd61037b9f8ad50bdaf1e4513ec54d1978a58315e3e9da47`. O PYZ do baseline tem SHA-256 `0d66a72ab9414fa8a2a50a9eead3edd5f467afefa6d9ab23059c203a6b885010`; seu manifesto preservado tem SHA-256 `2ebea4ea07609715f740b3f9d3e7c2b195540b113a9f9987da06b34746d070d9`. O importer conferiu o magic Python e disponibilizou 310 módulos próprios, conservando os assets correspondentes.

Hashes dos cinco widgets na coleta `after-final-normal`:

| Fonte | SHA-256 |
| --- | --- |
| `inspector.py` | `bc1c109772c42bec6263182afe2fa556918f1a2a68c2292d4ffaa22c82979303` |
| `metric_table.py` | `321cfb138f7eaf03d9032ee475a5afa7cb6830672d7aa16b804e40d29737de2f` |
| `node_window.py` | `b6ef7629f306ac9108d324418e857ffa0b7edd8e68ba2cb865cc466f34c5849d` |
| `prims.py` | `9ca0e6b780345d12eb93b5daad4f917ff7173cab92b93be20834df54f76e7ca1` |
| `satellite_telemetry.py` | `edc55acae47cd7fbab6b859cf6a71f379d7b3d81fc3399563a96b52711f2f688` |

Na primeira tentativa normal, `node_window.py` era `e723901c9397abe27faa0af40a87d3ad78a6bf8772a7b8aa2ea3fbdf6e9ceaa6` e `prims.py` era `e2cd6f2e849bf4ae596b0085b0ca60bd9cf1ef12f2bef3a087b4e58d8393e7d1`; os demais três hashes acima já eram iguais. O ensaio `node-after-final` mediu a revisão posterior de `node_window.py`, hash `a67c4c32f91283738fe30e3127c35f4f3b51787bf1dedf1265caa82a6324b1e4`, com o mesmo `prims.py` da coleta normal. Os refinamentos posteriores à coleta normal ocultam seções core quando uma mudança de modo exige nova apresentação e corrigem a documentação do cache de oito larguras; não alteram cálculos do backend. Os números normais não são atribuídos automaticamente à revisão do novo pacote. Os conjuntos completos e os hashes início/fim permanecem em cada `report.json`.

## Reprodução

Execute no diretório do projeto, usando novos diretórios de output. Os comandos abaixo reproduzem a configuração curta de comparação; não substituem a qualificação final do pacote.

```powershell
$pythonExe = Join-Path $PWD '.audit-winvenv/Scripts/python.exe'
$env:QT_QPA_PLATFORM = 'windows'
& $pythonExe tools/benchmark_console_performance.py `
  --output outputs/floating_panels_20261008/rerun-before-normal --duration 20 --soak 1 `
  --archive-pyz release/windows/5.51.0-native-20261008-015715/build/console/PYZ-00.pyz `
  --archive-assets release/windows/5.51.0-native-20261008-015715/ORION-Q/_internal `
  --archive-manifest release/windows/5.51.0-native-20261008-015715/source-snapshot.json
& $pythonExe tools/benchmark_console_performance.py `
  --output outputs/floating_panels_20261008/rerun-source-normal --duration 20 --soak 1
```

Ensaios isolados e regressões de legibilidade:

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
& $pythonExe outputs/floating_panels_20261008/measure_node.py `
  --output outputs/floating_panels_20261008/node-rerun --ticks 20
& $pythonExe outputs/floating_panels_20261008/satellite/measure_panel.py `
  outputs/floating_panels_20261008/satellite/rerun.json
& $pythonExe outputs/floating_panels_20261008/layout/metric_layout_evidence.py rerun
& $pythonExe outputs/floating_panels_20261008/wrapping/wrapping_layout_evidence.py rerun
& $pythonExe -m pytest tests/test_console_metric_table.py tests/test_console_wrapping_fields.py `
  tests/test_console_satellite_incremental.py tests/test_console_node_incremental.py -q `
  --basetemp outputs/floating_panels_20261008/pytest-rerun
```

O baseline compilado requer os três arquivos preservados especificados no comando. Repetições após novas mudanças devem ter hashes e outputs próprios; executar o código corrente não reproduz automaticamente uma revisão anterior.

## Limitações e qualificação da release

- A continuidade de 60 s não prova ausência de vazamento; `final_soak_at_least_three_minutes=false` nas três coletas. O RSS maior permanece um custo observado a investigar em janelas mais longas.
- `stage_paint_ms` mede handlers Python existentes de stage/renderers, incluindo filhos no stage; não é FPS nem tempo do compositor. O paint nativo do `NodeWindow` não recebe um handler Python adicional e não foi cronometrado diretamente.
- As ações usam widgets reais do Qt na plataforma Windows, sem injeção de entrada de sistema operacional. O harness mantém um painel flutuante por vez; não qualifica sozinho abrir muitos painéis simultaneamente ou uma sessão longa com mouse físico.
- Uma coleta por revisão, estado assíncrono e cargas concorrentes do desktop não permitem atribuir pequenas diferenças a uma única alteração nem fornecem intervalo de confiança.
- Os resultados locais com cProfile e construção de referência não são agregados às métricas normais. Os ensaios satelitais isolados tiveram largura efetiva diferente antes/depois e apresentação conjunta modificada.
- Permanecem intervalos de heartbeat acima de 250 ms e regressão na fase de continuidade. Não se declara eliminação de todas as pausas, ganho universal ou mudança de linguagem.

### EXE 5.52.0: gates funcionais e coleta GUI concluídos

O executável do build frio foi gerado em `release/windows/5.52.0-native-20261008-082300`, com SHA-256 `da5a478049b4baa677e7b6747899affb1fa91141803b33e4b90ad8a3b5b16252`. A fonte de produção está congelada para esse build. A bateria `final-layout.xml` concluiu 180 casos sem falhas/errors/skips em 109,128 s, e os oito checks complementares passaram em 13,255 s, totalizando os 188 casos disjuntos acima.

Os gates funcionais compilados concluíram com `exit_code=0` e `passed=true`. As contagens abaixo são checks de cada relatório, e não casos únicos de pytest; não são somadas à bateria de fonte.

| Gate compilado | Evidência / resultado |
| --- | --- |
| [Self-test](../../release/windows/5.52.0-native-20261008-082300/self-test.json) | 39 checks |
| [Roadmap](../../release/windows/5.52.0-native-20261008-082300/roadmap-validation/report.json) | 4 checks |
| [Worker temporal](../../release/windows/5.52.0-native-20261008-082300/campaign-validation/report.json) | 25 checks |
| [Laboratório](../../release/windows/5.52.0-native-20261008-082300/laboratory-validation/report.json) | 12 checks; emulação local completa, sem qualificação de hardware |
| [Satelital](../../release/windows/5.52.0-native-20261008-082300/satellite-validation/report.json) | 21 checks; exercício de software, sem prontidão de missão ou atuação física |
| [Rede global](../../release/windows/5.52.0-native-20261008-082300/global-validation/report.json) | 33 checks; campanha completa congelada |
| [Stress sintético](../../release/windows/5.52.0-native-20261008-082300/stress-validation/synthetic-controller-853c5e2bca4448d3908a279a14a5914a/report.json) | 535 checks, 20 rounds, 140 ciclos; não cancelado |
| [Inicialização](../../release/windows/5.52.0-native-20261008-082300/startup-validation/report.json) | 6 casos nativos: service 100/125/150/200% e demo 100/150% |
| [Modelos opcionais](../../release/windows/5.52.0-native-20261008-082300/optional-models-validation/optional-models-report.json) | 11 modelos no modo `without_packages`, todos descarregados; política de runtime não ativada |

O [snapshot congelado](../../release/windows/5.52.0-native-20261008-082300/source-snapshot.json) tem SHA-256 `ff5e04ffc6c39c7e994ad6a721b79c1b52846efc1dde37dc0f21231b3f74a1bb`. A [verificação após gates](../../release/windows/5.52.0-native-20261008-082300/source-snapshot-verification-gates.json) registra `unchanged=true`, sem diferenças, cobrindo 529 fontes, 82 assets e 10 arquivos de build. A comparação dos mapas de fonte preservados de 5.51.0 e 5.52.0 identifica somente os cinco widgets GUI mencionados acima. Na fonte congelada, `node_window.py` tem hash `a67c4c32f91283738fe30e3127c35f4f3b51787bf1dedf1265caa82a6324b1e4` e `prims.py`, `735fbd6944a7af646cc3df09e80281cb4e5a6a503ae35ab68996fb86eb61396a`; os outros três coincidem com a coleta `after-final-normal`.

| Gate de qualificação | Estado / registro |
| --- | --- |
| Build frio 5.52.0 | Executável gerado; SHA-256 registrado acima |
| Snapshot de fonte e comparação com baseline | Conferidos após gates; verificação final após empacotar registrada em `qualification.json` |
| Self-test e validação funcional do EXE | Aprovados nos relatórios compilados acima |
| Inicialização/layout nativos do EXE | Seis casos aprovados; mouse físico e máquina independente não qualificados |
| Coleta normal do EXE | Concluída em `after-frozen-final`; 20 s por fase e continuidade de 3 min, sem profiler |
| Integridade do pacote e fechamento do ZIP | Estado e hashes de fechamento registrados separadamente em `qualification.json` |

O [relatório da coleta compilada](../../outputs/floating_panels_20261008/after-frozen-final/report.json) registra `frozen=true`, `code_origin=frozen`, `exit_code=0`, `measurement_completed=true`, `source_unchanged=true`, `errors=[]` e `final_soak_at_least_three_minutes=true`. O [launcher](../../outputs/floating_panels_20261008/after-frozen-final/launcher.json) aponta para o executável acima, terminou sem timeout externo e não modificou processos do operador. A [origem binária](../../outputs/floating_panels_20261008/frozen-binary-origin.json) conserva o mesmo caminho e SHA-256 do EXE antes da medição.

São resultados próprios do pacote, separados das três comparações anteriores em fonte. A carga usa 20 s por fase e 180 s de continuidade, 130 ações planejadas e plano SHA-256 `11ddbb0e7fddd8fdf00e88e65c7580a6280c5ab5c67455e8649f2b9f240dbc0c`. Alterar duração, sequência e runtime impede tratar essa coleta como uma comparação de igualdade com o baseline de 60 s. A plataforma foi Windows/Qt 6.11.2, cliente 3440 × 1440, sem cProfile/hooks de métodos; GPU permanece contexto compartilhado. O sampler externo terminou com código zero e 550 amostras de recursos.

| Fase do EXE | Heartbeat p95 (ms) | Stage paint p95 (ms) | CPU média da árvore (%) | RSS p95 da árvore (MiB) | Heartbeat máximo (ms) | Intervalos > 250 ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Mapa | 122,82 | 61,21 | 113,80 | 682,55 | 481,09 | 7 |
| Globo | 139,76 | 73,33 | 119,99 | 687,11 | 504,74 | 6 |
| Abas | 29,53 | 47,95 | 88,25 | 696,71 | 366,62 | 2 |
| Alternâncias | 34,07 | 50,28 | 96,58 | 704,59 | 186,86 | 0 |
| Continuidade de 180 s | 93,05 | 73,11 | 111,93 | 724,87 | 673,67 | 56 |

As 60 seleções tiveram 60 readbacks completos; 60 pedidos de modo, 56 mudanças efetivas para 55 planejadas e 10 navegações não produziram divergências. As 60 verificações de apresentação passaram o limite já existente de 5 s, com `latency_deadline_violations=[]`; esse check é conferido explicitamente na qualificação, pois a flag do relatório informa que ele não governa sozinho o exit code da coleta. O frame final registrou 71 nós, 131 links, nove satélites e 36 sessões de tecnologia, com origem simulada e nenhuma atuação física.

O primeiro paint do stage ocorreu em 0,124 s após mostrar; o primeiro snapshot completo em 6,875 s. O p95 desde a ação até o início do paint com todos os campos prontos foi 3.938,61 ms no mapa, 2.368,89 ms no globo e 3.125,76 ms na continuidade. Abas/alternâncias não solicitam readbacks completos nessa métrica e têm contagem zero. Esses tempos distinguem feedback inicial de quadro integral; os vários segundos de apresentação inicial e os intervalos longos de heartbeat continuam limites observados. Três minutos não comprovam ausência de vazamento.

Comando da coleta final, após o build e os gates funcionais:

```powershell
$env:QT_QPA_PLATFORM = 'windows'
& $pythonExe tools/benchmark_console_performance.py `
  --output outputs/floating_panels_20261008/after-frozen-final --duration 20 --soak 3 `
  --executable release/windows/5.52.0-native-20261008-082300/ORION-Q/ORION-Q.exe
```

Essa continuidade de 3 min é maior que os 60 s das comparações de fonte e está apresentada com escopo próprio acima. O `--self-test REPORT_JSON` e o `--startup-test REPORT_JSON` produziram os relatórios aprovados. A aceitação independente em outra máquina e o arraste com mouse físico continuam não verificados.

Na [avaliação isolada de projeção](../../outputs/language_evaluation_20261008/projection/evaluation.md), o custo completo de nove órbitas recebidas, incluindo preparação, cópias e paths Qt, foi 1,8839 ms com NumPy e 1,8630 ms com a ponte C++: margem de 0,0209 ms, aproximadamente 1,1%. Mundo e stress com preparação e paths foram ligeiramente mais lentos em C++. A paridade numérica passou nos casos medidos, mas esses microbenchmarks não medem pintura nem demonstram ganho da UI. A decisão para o candidato é manter Python/PySide6 e não implementar a ponte; renderer C++/Qt completo permanece sem avaliação por ausência do SDK compatível. Nenhum backend de produção foi migrado.

O estado final do fechamento é consultado no [arquivo externo de qualificação](../../release/windows/5.52.0-native-20261008-082300/qualification.json), emitido após empacotar e conferir origem, integridade e hashes. Ele registra SHA-256 do EXE/ZIP, evidências de fonte/gates/coleta e correspondência byte a byte deste relatório com sua cópia no pacote. A verificação de integridade do pacote é `integrity_only`, não uma repetição das campanhas funcionais nem aceitação manual independente. Após a cópia e o fechamento do ZIP, este relatório fica imutável; a conclusão da entrega e o hash do ZIP pertencem ao registro externo, evitando alterar um arquivo já incluído no próprio pacote.
