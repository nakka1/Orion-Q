# ORION-Q 5.56.0 — publicação ao vivo, gráficos, evidência e FPGA

Qualificação de 08/10/2026. As correções tornam o título da telemetria legível, dão às tabelas uma área de pintura própria, permitem exportar para um arquivo escolhido e apresentam os observáveis recebidos de cada protocolo. A tela FPGA passa a mostrar inventário, configuração, conexão e recibos produzidos pelo backend. Os resultados de desempenho abaixo descrevem a versão completa, com ganhos e custos medidos separadamente.

Candidato: [5.56.0-native-20261008-164813](<../../release/windows/5.56.0-native-20261008-164813>). SHA-256 do EXE 5.56.0: `32c1a55cf049c687474f3ade163cc7bef1f5ef6c6e48df1b1d5f75142aa03fde`. O benchmark anterior utilizou o EXE 5.55.0 com SHA-256 `1b7485352c82bbf9e081207d8fb9b065cd571d020e68334c5e273b5ab5dc898c`.

## Pedidos, causas, correções e evidências

| Pedido | Causa constatada | Comportamento resultante | Provas e limites |
|---|---|---|---|
| Fluidez e consumo de CPU/RAM | Leituras completas e consultas autenticadas repetidas no Qt disputavam tempo com apresentação; cópia recursiva de observações brutas tinha custo adicional. | Descritores leves/publicações ao vivo em cadência separada de relatórios; autorização atual no worker; cópia independente preserva valores e tipos. | Comparação frozen por fase abaixo e microbenchmark de cópia. Pausas do backend continuam registradas; não há garantia de FPS. |
| Scroll Housekeeping/Science | Viewports genéricos transparentes dependiam do reaproveitamento de pixels e da pintura do contêiner. O defeito por hover não foi reproduzido na fonte corrente. | Viewport opaco, fundo pintado e update integral após scroll; sem modificar valores, unidades, seleção ou origem. | 42 casos Qt Windows e capturas HWND sem hover iguais ao repaint explícito. Isso valida a correção defensiva, sem inventar um antes visual defeituoso. |
| Título Fidelity by link cortado | Título dividia a linha com todos os seletores: apenas 70–295 px entre larguras 1300–1820; a 1300, quatro linhas. | Título ocupa toda a largura do gráfico; seletores ficam na linha seguinte. | Sete larguras de stage, pontos preservados e capturas antes/depois. |
| EXPORT EVIDENCE inerte | O clique disparava exportação automática com retorno pouco visível; CommandHistory usava permissão de leitura para seu export local. A migração deixou chamadores antigos nos painéis de política e no diagnóstico de scroll, bloqueando o candidato intermediário. | Keys, Telemetry e Audit escolhem arquivo JSON novo; backend escreve exclusivamente e informa o caminho. Todos os leitores de evidência capturada passam o exportador autorizado da fonte ao worker e exigem export_record. | Clique real no EXE, JSON/hash, cancelamento, contexto alterado, arquivo existente, permissão e export assíncrono; novos grupos revalidam todos os chamadores migrados. Hash local não é assinatura. |
| Gráfico de US-GOOG-QA/TF-QKD e onda CIMATEC | GOOGLE-QAI/US-GOOG-QA usa US-SATELLITE no cenário corrente; sua observação não pertence ao monitor legado de polarização. Protocolos modulares tinham tabelas, sem linha temporal própria. | Séries por sessão, tecnologia, métrica, unidade e domínio temporal; CIMATEC conserva 1 − 2·QBER. TF-QKD mostra seus próprios observáveis, sem fabricar onda ou fidelidade Bell. | UI real do EXE, timestamps/pontos/proveniência e zero bits certificados. Não se reclassifica Google como TF-QKD pelo nome da instituição. |
| Edge decision / FPGA devices | O painel anterior não oferecia o inventário e o controle completo dos adaptadores com feedback persistido. | Registro/configuração/conexão com revisão, modelo de clock/pipeline/timeout e recibos após processamento na cadeia existente. | Controles reais no EXE, configuração persistida, observação recebida e hash. Software não é FPGA caracterizada; contrato físico permanece indisponível. |

Contratos e interpretação: [orion_q_protocol_graphs_fpga.md](<../product/orion_q_protocol_graphs_fpga.md>) e [orion_q_protocol_time_series.md](<../product/orion_q_protocol_time_series.md>). Detalhes de scroll/export: [telemetry-ui-fix.txt](<../../outputs/desktop_identity_20261008/telemetry-ui-fix.txt>).

Antes do título: [heading-before.png](<../../outputs/desktop_identity_20261008/heading-before.png>); depois: [heading-after.png](<../../outputs/desktop_identity_20261008/heading-after.png>). Capturas de scroll: [housekeeping-proxy-scroll-after.png](<../../outputs/desktop_identity_20261008/housekeeping-proxy-scroll-after.png>), [science-scroll-after.png](<../../outputs/desktop_identity_20261008/science-scroll-after.png>) e [records-proxy-scroll-after.png](<../../outputs/desktop_identity_20261008/records-proxy-scroll-after.png>).

## Comparação nativa dos executáveis

Coletas: [5.55.0 baseline-clean](<../../outputs/ui_pipeline_20261008/baseline-clean/report.json>) e [5.56.0 after-frozen](<../../outputs/ui_pipeline_20261008/after-frozen/report.json>). Windows 11 build 10.0.26200; AMD Ryzen 7 5700X (8 núcleos/16 processadores lógicos); 31,93 GiB de RAM; NVIDIA GeForce RTX 3060 (12 GiB); monitor U34G4C, cliente 3440 × 1440, DPR 1,0. A GPU descreve o ambiente; seus counters compartilhados não são atribuídos ao ORION-Q.

A demonstração simulada usa os serviços e widgets reais, estado novo e processos próprios. As coletas normais não usam cProfile, profiling de métodos nem override global de QApplication.notify. As ações são eventos Qt nos widgets; não constituem aquisição nem atuação física.

Antes: Mapa 20 s, Globo 20 s, Abas 20 s, Alternâncias 20 s, Continuidade 180 s; topologia observada com 71 nós, 131 enlaces, 9 satélites e 36 sessões de tecnologia. Workload `actual-qt-actions-v3`; plano SHA-256 `11ddbb0e7fddd8fdf00e88e65c7580a6280c5ab5c67455e8649f2b9f240dbc0c`.

Depois: Mapa 20 s, Globo 20 s, Abas 20 s, Alternâncias 20 s, Continuidade 180 s; topologia observada com 71 nós, 131 enlaces, 9 satélites e 36 sessões de tecnologia. Workload `actual-qt-actions-v3`; plano SHA-256 `11ddbb0e7fddd8fdf00e88e65c7580a6280c5ab5c67455e8649f2b9f240dbc0c`.

O workload e o hash do plano de ações coincidem nas duas coletas.

As mudanças incluem novas funcionalidades e publicação de históricos/FPGA; a comparação mede o custo da versão completa e não isola causalmente cada widget. Não há intervalo de confiança nem qualificação em outra máquina. Uma célula “—” significa ausência de amostras, não zero.

Em todas as tabelas, a ordem é **antes → depois**. Tempos em ms. Heartbeat mede atraso do event loop; stage paint mede o handler CPU do stage, incluindo os filhos observados. Nenhuma das duas medidas é FPS, vsync ou tempo de apresentação pelo compositor.

| Fase | Heartbeat p95 | Heartbeat máximo | Stage paint p95 | Intervalos >250 ms |
|---|---:|---:|---:|---:|
| Mapa | 158,47 → 57,69 | 577,47 → 408,21 | 79,23 → 49,13 | 11 → 4 |
| Globo | 140,92 → 71,16 | 379,41 → 463,02 | 91,79 → 55,93 | 11 → 1 |
| Abas | 52,46 → 43,02 | 395,15 → 610,13 | 50,60 → 45,55 | 1 → 3 |
| Alternâncias | 69,45 → 51,31 | 287,81 → 199,16 | 81,75 → 64,83 | 1 → 0 |
| Continuidade | 97,44 → 64,73 | 783,26 → 853,23 | 77,63 → 50,69 | 71 → 19 |

O heartbeat p95 passou de 158,47 → 57,69 ms no mapa, 140,92 → 71,16 ms no globo e 97,44 → 64,73 ms na continuidade. Nessa última fase, intervalos acima de 250 ms passaram de 71 → 19. O stage paint p95 nas abas passou de 50,60 → 45,55 ms. O callback p95 de navegação nas abas passou de 363,35 → 592,92 ms. As diferenças entre métricas e os máximos permanecem explícitos; a melhora não é uniforme em todas as interações.

O primeiro feedback e a conclusão da apresentação são medidos separadamente. “Campos completos” é a verificação de todo o conteúdo esperado; não é somente a abertura de uma janela nem um screenshot parcial.

| Fase | Callback p95 | Ação → paint após dispatch p95 | Ação → campos completos p95 | Campos completos máximo |
|---|---:|---:|---:|---:|
| Mapa | 802,90 → 329,81 | 1307,44 → 457,65 | 4194,15 → 2905,34 | 4194,15 → 2905,34 |
| Globo | 290,07 → 222,59 | 463,06 → 457,20 | 2203,97 → 1666,20 | 2203,97 → 1666,20 |
| Abas | 363,35 → 592,92 | 364,11 → 594,63 | — → — | — → — |
| Alternâncias | 382,46 → 216,41 | 384,43 → 217,23 | — → — | — → — |
| Continuidade | 734,97 → 354,56 | 891,03 → 499,61 | 3133,47 → 2529,12 | 3378,07 → 3385,42 |

CPU da árvore soma a utilização do processo UI e seus filhos: 100% corresponde a um núcleo lógico, e valores acima de 100% são possíveis. RSS é memória residente, não heap Python nem memória privada exclusiva; páginas compartilhadas podem aparecer no working set de mais de um processo. O sampler é um processo independente do Qt.

| Fase | CPU média da árvore (%) | CPU média do host (%) | RSS UI p95 (MiB) | RSS árvore p95 (MiB) | RSS árvore máximo (MiB) |
|---|---:|---:|---:|---:|---:|
| Mapa | 118,27 → 93,72 | 36,44 → 33,54 | 569,70 → 561,67 | 674,35 → 666,14 | 676,86 → 666,14 |
| Globo | 121,41 → 102,72 | 40,94 → 35,54 | 576,40 → 562,09 | 681,05 → 666,56 | 681,10 → 672,60 |
| Abas | 93,00 → 69,37 | 29,24 → 34,95 | 591,78 → 572,88 | 696,41 → 677,32 | 701,08 → 677,32 |
| Alternâncias | 102,44 → 80,83 | 30,23 → 33,79 | 598,45 → 590,20 | 703,10 → 694,65 | 704,85 → 696,36 |
| Continuidade | 104,07 → 91,35 | 41,97 → 35,59 | 624,37 → 630,07 | 729,11 → 734,61 | 738,73 → 738,10 |

Amostras brutas e configuração de cada coleta: `heartbeat.csv`, `paints.csv`, `resources.jsonl`, `gpu-context.jsonl` e `configuration.json` no diretório correspondente ao report. A memória dos coletores é contabilizada separadamente em `collector-accounting.json`.

## Memória observada e aquecimento dos históricos

RSS UI p95 durante continuidade: 624,37 → 630,07 MiB. As amostras retidas do mesmo processo UI nessa fase permitem ver início, fim e pico, sem incluir a memória do worker auxiliar:

| Coleta | Primeira amostra (MiB) | Última amostra (MiB) | Pico observado (MiB) | Janela amostrada (s) | Amostras |
|---|---:|---:|---:|---:|---:|
| Antes | 591,83 | 615,37 | 633,99 | 179,52 | 336 |
| Depois | 582,77 | 631,87 | 633,56 | 179,57 | 338 |

Na versão nova, o RSS UI terminou a janela 49,10 MiB acima da primeira amostra e alcançou 633,56 MiB. O consumo não foi demonstrado estável durante os três minutos. Primeira/última amostra não descrevem uma curva monotônica nem permitem distinguir aquecimento de crescimento persistente.

O histórico de protocolo tem retenção de até 240 pontos por série, 120 s no relógio do protocolo e 2048 séries. O relógio da versão nova chegou a 42,00 s nesta coleta: a janela de 120 s ainda não foi completada. Os limites de retenção no código não são prova de um teto de RSS; seria necessária uma coleta mais longa, após o preenchimento dos históricos, para demonstrar estabilização.

A versão 5.56 também cadastra três dispositivos FPGA software no cenário institucional e processa seus recibos, além de publicar os históricos de protocolos. Essa carga nova faz parte da comparação da versão completa; as duas coletas não isolam seu custo nem permitem atribuir a ela toda a variação de memória ou dos passos científicos.

## Passos científicos e limites restantes

| Indicador final do backend | Antes | Depois |
|---|---:|---:|
| Ticks científicos observados | 52 | 42 |
| Tempo de parede da última contagem (s) | 300,02 | 309,08 |
| Relógio científico final (s) | 52,00 | 42,00 |
| Último passo observado (ms) | 7606,65 | 6903,56 |
| Passos acima do período solicitado | 52,00 | 42,00 |
| Backlog publicado do protocolo (s) | 0,00 | 0,00 |
| Hops clássicos pendentes | 0,00 | 0,00 |

Período científico solicitado: 1,00 → 1,00 s. Último passo em baseline-clean: 7,61 s. Foram observados 52 ticks em 300,02 s de parede antes e 42 ticks em 309,08 s depois. O último passo novo levou 6,90 s. A coleta curta anterior registrou último passo de 4,43 s ([report.json](<../../outputs/ui_pipeline_20261008/baseline-frozen/report.json>)); não integra as tabelas da comparação principal. Essas medições anteriores documentam pausas do backend da ordem de 4–7 s. Os valores são amostras de passos e não percentis. O último passo não é a duração média dos ticks; a menor contagem de ticks da versão nova não demonstra backend mais rápido. Uma interface mais responsiva não acelera automaticamente o relógio científico nem demonstra tempo real físico. Os overruns e o último passo da versão nova ficam explícitos acima; não há alegação de 60 FPS, eliminação de pausas ou redução global de CPU sem apoio nas métricas.

## Microbenchmark de cópia de observações

[acquisition-copy-benchmark-v2.json](<../../outputs/ui_pipeline_20261008/acquisition-copy-benchmark-v2.json>): 200,000 eventos artificiais, 7 repetições; mediana **21,78 → 9,88 ms**, razão 2,21× nesse trecho. Valores e tipos de contêineres são iguais e a cópia tem propriedade independente. Esse ganho local não é um speedup do backend inteiro, não reduz o volume de eventos e não qualifica captura física.

## Aceitação funcional no EXE e testes de fonte

[ui-pipeline-report.json](<../../release/windows/5.56.0-native-20261008-164813/ui-pipeline-validation/ui-pipeline-report.json>) vinculado ao SHA-256 do candidato, com Qt Windows e workspace isolado.

| Caso no executável | Resultado |
|---|---|
| Topologia real do demo e fonte simulada identificada | APROVADO |
| US-GOOG-QA/GOOGLE-QAI: observável recebido, unidade e proveniência | APROVADO |
| TF-QKD: taxa condicional; zero bits certificados; nenhuma onda/Bell inventada | APROVADO |
| CIMATEC: observação legada de polarização preservada | APROVADO |
| FPGA: conexão/configuração persistidas e recibos do processamento | APROVADO |
| QKEYS: clique real grava evidência no caminho escolhido | APROVADO |

Capturas são da superfície HWND real; o diagnóstico não usa um widget duplicado como evidência de pintura.
- [cimatec-legacy-polarisation](<../../release/windows/5.56.0-native-20261008-164813/ui-pipeline-validation/cimatec-legacy-polarisation.png>)
- [fpga-control-and-receipts](<../../release/windows/5.56.0-native-20261008-164813/ui-pipeline-validation/fpga-control-and-receipts.png>)
- [google-protocol-timeline](<../../release/windows/5.56.0-native-20261008-164813/ui-pipeline-validation/google-protocol-timeline.png>)
- [qkeys-chosen-evidence-export](<../../release/windows/5.56.0-native-20261008-164813/ui-pipeline-validation/qkeys-chosen-evidence-export.png>)
- [tf-qkd-certified-zero](<../../release/windows/5.56.0-native-20261008-164813/ui-pipeline-validation/tf-qkd-certified-zero.png>)
- [tf-qkd-received-timeline](<../../release/windows/5.56.0-native-20261008-164813/ui-pipeline-validation/tf-qkd-received-timeline.png>)

| Grupo congelado | Resultado | Duração (s) |
|---|---|---:|
| ui-pipeline | APROVADO | 71,39 |
| desktop-behavior | APROVADO | 14,44 |
| satellite-mission | APROVADO | 14,92 |
| satellite-optical | APROVADO | 17,50 |
| optional-models | APROVADO | 0,66 |
| roadmap | APROVADO | 6,41 |
| campaign-worker | APROVADO | 29,34 |
| self-test | APROVADO | 618,34 |
| laboratory | APROVADO | 112,80 |
| global-network | APROVADO | 172,08 |
| stress | APROVADO | 126,45 |
| startup | APROVADO | 212,41 |

O agregado final de fonte usa release-source-final-v2.xml, incluindo os casos de identidade do worker; o JUnit final de gráficos/ondas e os novos grupos de exportação de política são mostrados separadamente. As execuções abaixo não são somadas, pois há sobreposição de casos. Relatórios históricos com falhas não são usados para qualificar o candidato.

| Execução | Aprovados | Ignorados | Tempo somado dos casos (s) |
|---|---:|---:|---:|
| [release-source-final-v2.xml](<../../outputs/ui_pipeline_20261008/release-source-final-v2.xml>) | 68 | 0 | 61,87 |
| [telemetry-controls-final.xml](<../../outputs/desktop_identity_20261008/telemetry-controls-final.xml>) | 111 | 0 | 315,64 |
| [telemetry-controls-native.xml](<../../outputs/desktop_identity_20261008/telemetry-controls-native.xml>) | 42 | 0 | 8,47 |
| [committed-context-native.xml](<../../outputs/desktop_identity_20261008/committed-context-native.xml>) | 3 | 0 | 0,07 |
| [command-context-final.xml](<../../outputs/desktop_identity_20261008/command-context-final.xml>) | 9 | 0 | 97,06 |
| [local-job-policy-exports-final.xml](<../../outputs/ui_pipeline_20261008/local-job-policy-exports-final.xml>) | 38 | 0 | 91,12 |
| [policy-export-own-final.xml](<../../outputs/ui_pipeline_20261008/policy-export-own-final.xml>) | 19 | 0 | 198,38 |
| [policy-export-permissions-final.xml](<../../outputs/ui_pipeline_20261008/policy-export-permissions-final.xml>) | 10 | 0 | 112,45 |
| [final-wave-protocol.xml](<../../.audit-protocol-series-tests/final-wave-protocol.xml>) | 66 | 0 | 7,19 |

A execução histórica de 111 casos incluiu a exportação real do serviço, tabelas de satélite, reconciliação e renderização naquela revisão. A execução histórica de 42 casos Windows cobriu scroll por wheel/teclas/barra em dark/light, sete larguras, métricas/series/live/replay e chooser/recusa de sobrescrita. Após separar a capacidade comprometida do estado operacional running, três checks de segurança e nove de contexto/export foram reexecutados. Esses resultados preservados não substituem os grupos novos: exportação de contexto, decisões, revisão, aprovação e estado ativo, recusa de sobrescrita, permissão independente de leitura, perda de permissão durante o chooser e identidade/revogação no worker. A evidência histórica de falhas de fixture foi preservada; nenhum requisito científico ou de segurança foi reduzido.

O diagnóstico de fonte [report.json](<../../outputs/ui_pipeline_20261008/context-scroll-source-final/report.json>) passou sete verificações com Qt offscreen: scroll/repaint, preservação da leitura pelo watchdog, exportação via worker/serviço igual à observação capturada, journal científico intacto e nenhuma autorização de seleção. Ele complementa os testes de fonte e não substitui a aceitação Windows no EXE.

## Candidato intermediário rejeitado

O candidato 5.56.0-native-20261008-161754 não foi entregue: a qualificação detectou perda do contexto explícito de identidade no worker e o autoteste terminou com TypeError em um chamador que ainda não passava o exportador autorizado. As condições bloquearam a distribuição mesmo com os outros checks já aprovados. O candidato final foi recompilado depois de conservar copy_context e migrar todos os chamadores; suas medições e gates são vinculados ao novo hash do EXE.

A medição anterior permanece preservada em [report.json](<../../outputs/ui_pipeline_20261008/after-candidate161754/report.json>) (SHA-256 do EXE `c8e38992858c1b2ab3191764fb61fb6fee9cff854c8beed7eec89d5977410071`). Ela descreve o candidato rejeitado e não qualifica o EXE final nem integra as colunas depois das tabelas acima.

## Distribuição, cadeia de dados e fontes

[Pacote portátil](<../../release/windows/5.56.0-native-20261008-164813/ORION-Q-5.56.0-native-windows-x64.zip>); SHA-256 do ZIP `fd96539276309aaf6f564a910f9b6346b8813d6dfea5e881ac030a25d6143f57`; CRC validado; 3965 arquivos. Metadata: [portable-package.json](<../../release/windows/5.56.0-native-20261008-164813/portable-package.json>).

A cadeia preserva observação bruta → aquisição identificada → envelope/associação dos canais → validação → transmissão e recepção clássicas → processamento dos protocolos → persistência e publicação → interface. A interface não preenche QBER, fidelidade, chaves ou estado de node; lacunas continuam lacunas. Estimativa condicional de chave permanece distinta de material secreto certificado.

Dispositivos FPGA software modelam clock/ciclos/timeout e entregam observações à cadeia existente. Não constituem RTL validado, bitstream, caracterização de timing/temperatura ou atuação física. O adaptador físico declarativo bloqueia aquisição. Exportação exige autorização atual e grava novo arquivo; hashes locais não autenticam autoria nem substituem assinaturas.

Fontes do comportamento e instrumentos:

- [src/quantum_autopilot/console/service_provider.py](<../../src/quantum_autopilot/console/service_provider.py>)
- [src/quantum_autopilot/console/ui_pipeline_validation.py](<../../src/quantum_autopilot/console/ui_pipeline_validation.py>)
- [src/quantum_autopilot/console/widgets/protocol_series.py](<../../src/quantum_autopilot/console/widgets/protocol_series.py>)
- [src/quantum_autopilot/console/widgets/wave_chart.py](<../../src/quantum_autopilot/console/widgets/wave_chart.py>)
- [src/quantum_autopilot/console/widgets/scrollable_table.py](<../../src/quantum_autopilot/console/widgets/scrollable_table.py>)
- [src/quantum_autopilot/console/widgets/evidence_export.py](<../../src/quantum_autopilot/console/widgets/evidence_export.py>)
- [src/quantum_autopilot/console/widgets/command_history.py](<../../src/quantum_autopilot/console/widgets/command_history.py>)
- [src/quantum_autopilot/network_engine/protocol_history.py](<../../src/quantum_autopilot/network_engine/protocol_history.py>)
- [src/quantum_autopilot/control_plane/fpga_devices.py](<../../src/quantum_autopilot/control_plane/fpga_devices.py>)
- [src/quantum_autopilot/console/widgets/fpga_devices.py](<../../src/quantum_autopilot/console/widgets/fpga_devices.py>)
- [src/quantum_autopilot/console/screens/fpga.py](<../../src/quantum_autopilot/console/screens/fpga.py>)
- [tools/benchmark_console_performance.py](<../../tools/benchmark_console_performance.py>)
- [tools/benchmark_acquisition_copy.py](<../../tools/benchmark_acquisition_copy.py>)
- [tests/test_console_telemetry_controls.py](<../../tests/test_console_telemetry_controls.py>)
- [tests/test_protocol_time_series.py](<../../tests/test_protocol_time_series.py>)
- [tests/test_console_protocol_series.py](<../../tests/test_console_protocol_series.py>)
- [tests/test_fpga_devices.py](<../../tests/test_fpga_devices.py>)
- [tests/test_console_policy_export_permissions.py](<../../tests/test_console_policy_export_permissions.py>)
- [tests/test_console_local_job_identity.py](<../../tests/test_console_local_job_identity.py>)

Gerado a partir das evidências existentes em 2026-10-08T20:26:08.117274+00:00. O gerador não executa benchmarks nem acessa o estado do operador.
