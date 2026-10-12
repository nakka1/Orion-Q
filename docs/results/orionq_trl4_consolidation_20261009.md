# OrionQ — consolidação de evidências TRL 4, 09/10/2026

**Estado: revisão 2 concluída — pacote funcional `complete_for_review`.** A
terceira rodada aprovou os 1.728 casos previstos e as 20 funções declaradas;
as tentativas anteriores ficam preservadas. Não há atribuição automática de
TRL nem aprovação operacional. O elemento é **SOFT-ALPHA**, o protótipo integrado
em fontes Windows, com dados simulados/fixtures identificados e atuação física
bloqueada. O executável 5.60.1 anterior não foi recompilado nesta rodada.

## Elemento e protocolo prospectivo

O [protocolo](../product/orionq_trl4_qualification.md), o
[desenho](../product/orionq_trl4_design.md) e o
[manual](../product/orionq_trl4_operator_manual.md) delimitam arquitetura, domínio,
operações, erros e capacidades não qualificadas. A
[matriz executável](../../configs/qualification/trl4_software_v1.json) relaciona
20 funções a implementação, documentação, testes, campanhas e limites.

Na revisão 1, foram coletados antes da execução **1.701 casos em 72 módulos**, incluindo todas
as parametrizações: 569 core, 463 integração e 669 Qt. Um módulo compartilhado
entre requisitos é executado uma vez. Testes do avaliador e fixtures adversariais
integram esse universo, sem serem apresentados como resultados da física.

As duas primeiras tentativas possuem a mesma identidade de fontes e critérios:

| Elemento congelado | Identidade |
| --- | --- |
| Protocolo SOFT-ALPHA-TRL4-v1/revisão 1 | SHA-256 `b876bc8d89ddf82b35b65a04600613213d0ae1f08aa0514dce88168d3c717e83` |
| Manifesto de 1.058 arquivos | SHA-256 `5fe2475b7af466eed754b1429cc02251506b4601624112eb03ef15d7072cbef4` |
| Plano nativo completo, conhecido e prospectivo | SHA-256 `a994c0c5c34b26ca6597946ed9e91218779e7f57e3a1b69c763e7fe9da9c9398` |
| Semente dos ensaios analíticos/pipeline | `2026100900`, reprodução conhecida, não holdout |

Hashes conferem integridade de bytes; não autenticam autoria humana nem
comprovam exatidão científica por si só. A classificação final exige revisão
humana identificada do elemento e das limitações.

O ambiente congelado utiliza Windows 11 build 26200, Python 3.12.14,
PySide6 6.11.2, SQLite 3.53.1, NumPy 2.5.2, SciPy 1.18.1, pytest 9.1.1 e
cryptography 46.0.7. O perfil usa Qt offscreen para regressões e Qt Windows para
os dois ensaios nativos. SDKs e presença de instrumentos não são inferidos.
O computador de referência tem Ryzen 7 5700X, 16 CPUs lógicas, 31,93 GiB de RAM
e RTX 3060 de 12.288 MiB, driver 616.64. A interface de desempenho usou
3440×1440, DPR 1, no monitor U34G4C; o ensaio de fluxos usou 1440×900.

## Preparação e tentativas preservadas

O desenvolvimento do avaliador passou em 92 regressões adversariais. A
ferramenta nativa passou em 141 testes de preparação e quatro verificações
focadas após ajustes do driver; esses números não devem ser somados à campanha
como testes científicos independentes. Ruff aprovou os quatro arquivos novos.

O [piloto preparatório](../../outputs/trl4_console_preparation_20261009/native-pilot1/report.json)
teve 11 casos aprovados e cinco falhas do driver. As expectativas incorretas
tratavam a matriz como um conjunto de enlaces individuais, deixavam GEO oculto
após uma falha e chamavam `KVList.texts()` em vez de `values()`. Foram corrigidas
na ferramenta, preservando o piloto, sem atribuir essas falhas ao produto.

A [primeira rodada congelada](../../outputs/trl4_consolidation_20261009/run1/assessment.json)
teve **16/16 fluxos nativos aprovados**, em 107,094 s, com login real da sessão
Windows. A validação material conferiu PNGs, exportações, cadastros e recibos
persistidos. O processo do grupo de testes terminou/interrompeu antes de gerar
JUnit; a causa não foi qualificada. A ausência de XML e das etapas restantes
resultou corretamente em pacote **incompleto**, com fontes inalteradas.
Testes parcialmente executados não foram contados como aprovados.

A [segunda rodada](../../outputs/trl4_consolidation_20261009/run2/freeze.json)
usa diretórios novos e o mesmo congelamento. Um processo diagnóstico próprio
registra início, PID, comando, término e código de saída por etapa. O driver de
orquestração está nos artefatos da rodada, separado das fontes do produto.

## Resultados negativos da segunda rodada

Os resultados da segunda rodada são completos e negativos. Não foram
reaproveitados como aprovação das fontes corrigidas da terceira rodada.

| Evidência | Universo previsto | Resultado da segunda rodada |
| --- | --- | --- |
| Componentes | 569 casos | 569 aprovados, 1.108,63 s |
| Integração/segurança/recuperação | 463 casos | 462 aprovados e 1 falha, 650,45 s |
| Qt/console e ferramentas | 669 casos | 664 aprovados e 5 falhas, 2.273,05 s |
| Fluxos nativos | 16 casos com critérios e artefatos | 16 aprovados, 118 s |
| GEO/topologia completa | 122 ações, 57 readbacks, 3 min de soak | Plano completo; soak observado 180,034 s |
| Referências reduzidas | 35 verificações em cada uma de duas repetições | 35/35 nas duas repetições |
| Pipeline causal | 49 verificações em cada uma de duas repetições | 49/49 nas duas repetições, fingerprints iguais |
| Matriz funcional | 20 funções | 7 pass, 13 fail; pacote incompleto |

A falha de integração é
`test_attempt_export.py::test_export_requires_export_capability_in_addition_to_history`:
a atualização explícita da janela usa a capacidade do último quadro e mantém
habilitado o botão após revogação de `export_record`. O método operacional
revalida a permissão antes da exportação; esta falha não comprova exportação
indevida. A tentativa permanece preservada e não pode ser promovida a pacote
completo. Não se altera o critério nem se remove o caso para obter aprovação.

O JUnit bruto contém 1.695 aprovações e seis falhas, sem skips/erros. O avaliador
admite apenas grupos integralmente válidos: `validated_passed_testcase_count`
é 569, e não 1.695. Requisitos que dependem de um grupo reprovado recebem fail;
isso não identifica uma falha distinta em cada função. A
[avaliação integral](../../outputs/trl4_consolidation_20261009/run2/assessment.json)
confirma `source_unchanged=true`, `passed=false` e `trl4_assigned=false`.

## Correções e nova revisão prospectiva

| Caso reprovado em run2 | Causa confirmada | Correção e preservação do critério |
| --- | --- | --- |
| `test_export_requires_export_capability_in_addition_to_history` | Atualização explícita usava capacidade visual antiga | Consulta explícita renova autoridade; recusa invalida controles. Pintura, filtros, seleção e busy usam apresentação sem IO; exportação revalida no backend |
| `test_same_window_hot_swap_clears_hidden_screens_and_ignores_old_signals` | Conclusão do loading sempre abria Topology | Preserva somente a aba; mantém limpeza de dados, autenticação e prontidão |
| `test_edge_feed_updates_after_a_real_fibre_change` | Esperado e exibido vinham de ticks diferentes | Compara o mesmo frame recebido pelo sinal real; conserva recusa, reroute e recuperação |
| `test_every_action_button_names_its_operation` | Botão de laboratório sem metadado e gating genérico anulava disponibilidade | Operação identificada; permissão intersectada com configuração/recursos. Modal inválido é recusado mesmo após restauração do contexto |
| `test_real_backend_keeps_publishing_while_terminal_is_hidden` | Contagem fixa anterior à expansão e expectativa incompleta de geometria orbital | Compara todos os IDs e enlaces com o backend; distingue associação cadastrada de emissão validada |
| `test_pair_reset_and_fresh_request_are_reflected_in_the_interface` | Fixture tentava entrar sem autenticação | Exercita recusa antes do login e login real pela sessão Windows; conserva entrega, invalidação, recuperação e consumo |

Foram acrescentados 27 casos: três de exportação, dois de preparação assíncrona,
14 de laboratório/modal e oito de admissão da revisão do avaliador. Os cenários,
20 funções, 72 módulos, semente, plano nativo e tolerâncias permanecem. A revisão
2 coleta **1.728 casos: 569 core, 474 integração e 685 Qt**, antes do congelamento
de [run3](../../outputs/trl4_consolidation_20261009/run3/freeze.json).

Na preparação corretiva, houve uma recusa de acesso ao temporário global e uma
fixture que abriu um seletor sem interação prevista, encerrada somente nos seus
processos próprios. Essas tentativas ficam preservadas. A repetição focada teve
59 aprovações e uma expectativa de geometria ainda incorreta, corrigida e
revalidada em um teste posterior aprovado. O avaliador passou em 100 casos;
Ruff aprovou os 12 arquivos alterados. Essas verificações preparatórias não
substituem a nova campanha integral.

## Resultado final da terceira rodada

O congelamento ocorreu em **2026-10-09T18:19:22.388145Z**. A revisão 2 manteve
o plano e os limites anteriores, coletou os novos casos antes da execução e
executou todas as dez etapas sequencialmente, em estado diagnóstico novo.
Nenhuma evidência anterior foi usada para aprovar as fontes corrigidas.

| Identidade da revisão 2 | SHA-256 |
| --- | --- |
| Protocolo | `d0a7d19189e2cc95d6c16a3ecd9a712e39c620173f389452e268e6c0d8cd147d` |
| Manifesto dos 1.058 arquivos | `75fcd6c70747349d802dcd2dc6f23e1527286721e4ee82fe25daafcd9470c72e` |
| Plano nativo | `a994c0c5c34b26ca6597946ed9e91218779e7f57e3a1b69c763e7fe9da9c9398` |

| Evidência | Resultado final | Artefato |
| --- | --- | --- |
| Componentes | 569/569; 978,473 s | [JUnit core](../../outputs/trl4_consolidation_20261009/run3/group-core.xml) |
| Integração/segurança/recuperação | 474/474; 617,550 s | [JUnit integração](../../outputs/trl4_consolidation_20261009/run3/group-integration.xml) |
| Qt/console e ferramentas | 685/685; 2.543,174 s | [JUnit Qt](../../outputs/trl4_consolidation_20261009/run3/group-qt.xml) |
| Console Windows | 16/16 casos; 112,265 s | [Fluxos nativos](../../outputs/trl4_consolidation_20261009/run3/console/report.json) |
| GEO, navegação e conteúdo | 122 ações, 57 leituras completas, 8 navegações; sem divergências | [Interface nativa](../../outputs/trl4_consolidation_20261009/run3/native-ui/report.json) |
| Oráculos analíticos reduzidos | 35/35 em cada repetição; fingerprints iguais | [Repetição 1](../../outputs/trl4_consolidation_20261009/run3/reference-1/report.json), [2](../../outputs/trl4_consolidation_20261009/run3/reference-2/report.json) |
| Pipeline causal | 49/49 em cada repetição; fingerprints iguais | [Repetição 1](../../outputs/trl4_consolidation_20261009/run3/pipeline-1/report.json), [2](../../outputs/trl4_consolidation_20261009/run3/pipeline-2/report.json) |
| Avaliação do pacote | 20/20 funções; `passed=true`, `source_unchanged=true`, erros vazios | [Assessment](../../outputs/trl4_consolidation_20261009/run3/assessment.json) |

Os 1.728 casos JUnit não tiveram falhas, erros ou skips. Os tempos da tabela
são durações das suítes JUnit; o driver registra separadamente a duração dos
processos, incluindo inicialização e término. As contagens de console, ações e
comparações científicas não foram somadas aos casos pytest como um único total.

### Matriz das funções declaradas

Todas as funções abaixo receberam `functional_status=pass` no domínio
SOFT-ALPHA, com evidências e limites detalhados na matriz executável. A coluna
de limites impede interpretar uma recusa segura como capacidade implementada.

| ID | Função e evidência aceita | Limite da conclusão |
| --- | --- | --- |
| F01 | Ingresso, canais associados, proveniência e replay | Origem simulada/fixture identificada |
| F02 | Cinco tecnologias, oráculos e consumo de recursos | Modelos reduzidos; sem hardware |
| F03 | Comunicadores, cadastro, recusa e topologia | Compatibilidade de software |
| F04 | Demandas, rotas e recursos compatíveis | Sem prova de rede quântica global física |
| F05 | Órbita, geometria e disponibilidade | Sem calibração óptica de missão |
| F06 | Gateway e decodificação de fixtures satelitais | Sem recepção RF nesta campanha |
| F07 | Adaptadores, configuração, buffers e recuperação | Sem resposta de instrumento qualificada |
| F08 | Observações BB84, relógio e pós-processamento | Hipóteses emuladas, segurança experimental pendente |
| F09 | Reserva, entrega incerta, consumo e recuperação | Sem chaves experimentais admitidas |
| F10 | Identidade e autorização, incluindo entrada Windows | OIDC institucional não implantado |
| F11 | PKI, cofre, MAC, revogação e autoridade | Âncoras externas/PKI operacional pendentes |
| F12 | Persistência, crash, reinício e WAL | Não equivale a restauração operacional integral |
| F13 | Modos, política, reversão e recusa física | Atuação física bloqueada |
| F14 | Modelos opcionais, cancelamento e ON/OFF | Sem prova de benefício científico geral |
| F15 | FPGA emulado, configuração e recibos | FPGA físico não qualificado |
| F16 | Publicação, scroll, seleção e conteúdo recebido | Critérios de fluidez ainda não cumpridos |
| F17 | GEO, seleção e renderizador exclusivo | Soak curto; sem estabilidade prolongada aprovada |
| F18 | Exportação e integridade material dos artefatos | Hash não autentica autoria humana |
| F19 | Bootstrap, autenticação e ciclo de vida Windows | Execução das fontes; EXE anterior não avaliado |
| F20 | 35 comparações analíticas e reprodução | Sem calibração experimental ou prova completa TF-QKD |

### Casos de uso exercitados no console nativo

A ferramenta usa a entrada real `app.run`, autenticação pela sessão Windows e
eventos de widgets Qt. Não usa `--skip-auth`, identidade injetada, credenciais
fornecidas pelo driver ou resultados prontos. O seletor de arquivo Qt foi
exercitado de fato; o seletor Win32 e injeção de mouse/teclado pelo sistema
operacional não fazem parte desta evidência. Todos os 16 casos foram aprovados.

| Caso | Fluxo e comprovação |
| --- | --- |
| `native_entry` | Login pela sessão Windows, identidade verificada pelo backend e ticks recebidos |
| `seven_screens` | Navegação pelas sete telas e conteúdo correspondente |
| `topology_views` | GEO, matriz e grafo associados à topologia recebida |
| `ground_details` | Seleção terrestre e leitura completa dos detalhes |
| `satellite_details` | Seleção orbital e detalhes recebidos |
| `geo_lifecycle` | Trocas consecutivas Mapa/Globo e exclusividade do renderizador |
| `communicator_dynamic` | Campos pertinentes ao tipo físico selecionado |
| `communicator_terrestrial` | Cadastro terrestre e leitura persistida |
| `communicator_satellite` | Cadastro orbital e leitura persistida |
| `communicator_refusal` | Configuração inválida recusada sem cadastro indevido |
| `fpga_emulated` | Controle emulado e recibo do fluxo comum |
| `key_audit_readback` | Consulta de gerenciamento de chaves e auditoria |
| `evidence_export` | Destino selecionado e conferência material dos arquivos exportados |
| `ml_cancel_confirm_restore` | Cancelamento, confirmação ON/OFF e estado final OFF do controle automático |
| `physical_boundary` | Bloqueio de atuação física conservado |
| `cleanup` | Provider/worker encerrados, timers parados e 7 renderizadores criados/7 destruídos |

Ativação de política, transporte de missão ao vivo, emissão KME de bancada e
cada diálogo de modelo opcional não são comprovados por esses 16 fluxos; sua
cobertura componente/integrada e suas dependências permanecem identificadas.
Os PNGs são conferidos quanto à estrutura/integridade, sem inspeção semântica
automática de todos os pixels.

### Evidência científica e causal

As duas repetições usam a semente conhecida `2026100900`, com 35 s simulados
por cenário do pipeline. Exercitam três sessões — RIO-ENTANGLEMENT, RIO-TF e
RIO-SATELLITE — contendo troca de entrelaçamento, memória, purificação, TF-QKD
e satélite/Doppler. É um ensaio causal delimitado, distinto da topologia completa
do benchmark; não comprova comunicação quântica global.

| Condição | Comandos concluídos | Recibos | Efeito observado |
| --- | ---: | ---: | --- |
| Nominal | 741 | 9.343 | Um par simulado entregue, fidelidade 0,913069948 |
| Degradação | 743 | 9.342 | Nenhum par entregue, três entregas recusadas |
| Indisponibilidade | 0 | 116 | 51 eventos de erro, ausência de aquisição e três sessões indisponíveis |
| Recuperação | 520 | 6.620 | 17 eventos de erro acumulados; três sessões operacionais ao final, sem nova entrega final |

O nominal registrou 112 tentativas BSM/15 sucessos, 335 tentativas de
purificação/304 sucessos, 90 expirações e 984 recusas de capacidade de memória.
Os contadores `pairs_consumed` e `purification_pairs_consumed` têm relações de
inclusão e não devem ser somados como categorias disjuntas. Métricas por janela
não equivalem aos contadores acumulados.

Aquisição, validação, sincronização, protocolo, transmissão e recebimento
clássico, recursos, métricas e persistência estão vinculados aos registros
originais. O replay por arquivo conserva os resultados normalizados e identifica
a origem simulada; não converte a fixture em medição física. Para cada uma das
quatro condições, os fingerprints científicos coincidem entre as duas
repetições; condições distintas conservam resultados e fingerprints distintos.
**Bits secretos
certificados permanecem zero em todas as sessões.** Estimativas condicionais
BBM92/SNS-TF não são chaves operacionais nem prova de segurança completa.

Os comandos exatos, logs, códigos de saída e tempos estão em
[execution-commands.json](../../outputs/trl4_consolidation_20261009/run3/execution-commands.json)
e nos arquivos de início/término de cada etapa. As
[instruções de nova execução](../product/orionq_trl4_qualification.md#execução-reproduzível)
exigem diretório exclusivo e outro congelamento; não sobrescrever `run1`,
`run2` ou `run3`. O
[resumo final verificável](../../outputs/trl4_consolidation_20261009/patch-proposals/run3-evidence-summary.json)
registra a derivação das métricas e hashes dos artefatos consultados.

## Desempenho observado e capacidades não demonstradas

O cenário nativo run2 apresentou 71 nodes, 131 enlaces, nove satélites,
71 comunicadores, 36 sessões tecnológicas e 76 demandas globais, com dados
simulados. Houve no máximo um renderizador ativo, 55 criações/54 destruições
antes do fechamento e nenhuma animação orbital ativa no modo Mapa. As 529
amostras externas de recursos cobrem 279,833 s; o soak efetivo foi 180,034 s.

| Métrica de run2 | Mapa | Globo | Soak |
| --- | ---: | ---: | ---: |
| Heartbeat p99 (ms) | 291,137 | 139,837 | 136,986 |
| Maior espera de conteúdo completo (ms) | 2.543,923 | 1.283,651 | 2.183,987 |

O prazo diagnóstico de 5 s para coletar conteúdo não é uma meta de
responsividade. A última etapa global levou 5.532,670 ms para período de 1 s,
com 50 overruns. Na segunda metade do soak, 169 amostras em 89,418 s tiveram
RSS da interface de 673,391–715,914 MiB e tendência OLS de +6,689 MiB/min.
Isso não comprova vazamento, mas também não demonstra estabilidade prolongada.
CPU de processo usa a convenção psutil: 100% corresponde a uma CPU lógica;
RSS da árvore soma processos, sem equivaler à memória física única.
GPU registrada representa o dispositivo compartilhado, sem atribuição ao OrionQ.

Esses dados permanecem separados da aceitação funcional alpha. Não há
aprovação de FPS, fluidez imperceptível, estabilidade de 20 minutos ou dos
limites de desempenho TRL 5. O
[resumo verificável de run2](../../outputs/trl4_consolidation_20261009/patch-proposals/run2-evidence-summary.json)
registra períodos, amostras, métricas e hashes das evidências brutas.

O run3 repetiu o mesmo perfil e a topologia completa: 71 nodes, 131 enlaces,
nove satélites, 71 comunicadores, 36 sessões e 76 demandas globais. Completou
57 leituras de seleção, 8 navegações e o plano completo de 122 ações. Os registros
incluem 57 transições, 54 mudanças efetivas de modo e 53 mudanças previstas;
callbacks de preparação explicam a diferença, sem divergência do plano.
As fases Mapa/Globo/abas/trocas duraram, respectivamente, 15,029/15,099/15,005/
15,089 s. O soak durou **180,002 s**, sem representar 20 minutos de uso.

| Medição | run2 | run3 |
| --- | ---: | ---: |
| Heartbeat p99 Mapa / Globo / soak (ms) | 291,137 / 139,837 / 136,986 | 131,051 / 96,307 / 75,354 |
| Maior espera de conteúdo Mapa / Globo / soak (ms) | 2.543,923 / 1.283,651 / 2.183,987 | 2.402,131 / 1.092,651 / 1.868,746 |
| Última etapa global / período (ms) | 5.532,670 / 1.000 | 4.608,138 / 1.000 |
| Overruns da etapa global | 50 | 54 |
| CPU média interface / árvore / host no soak (%) | 72,700 / 84,045 / 23,029 | 65,704 / 76,612 / 12,651 |
| RSS médio interface / árvore no soak (MiB) | 686,448 / 799,068 | 671,067 / 783,043 |

São observações de duas rodadas, sem ensaio controlado suficiente para atribuir
a redução de latência/CPU às correções. O passo global e detalhes ainda excedem
1 s; este resultado não aprova fluidez, FPS ou desempenho TRL 5.
O heartbeat mede intervalos do event loop; não é tempo de frame nem latência
de apresentação. O início de pintura completa também é medido separadamente.

O sampler externo run3 registrou 529 amostras em 276,677 s. A segunda metade
do soak contém 171 amostras em 89,068 s, RSS da interface de 642,699–686,793 MiB,
variação de 6,578% da média e tendência OLS de −9,265 MiB/min. A tendência
negativa nesse intervalo não prova ausência de vazamento nem estabilidade
prolongada. O máximo RSS de todo o soak foi 689,418 MiB na interface e
801,371 MiB na árvore; CPU média do host foi 12,651%.

Em 523 leituras de componentes houve no máximo um renderizador ativo/instanciado.
Nenhuma das 273 leituras em Mapa tinha timer orbital ativo. A última leitura
pré-fechamento contabiliza 55 criações/54 destruições, com um renderizador
legitimamente aberto; a limpeza completa é demonstrada separadamente pelo
caso `cleanup` do console. O registro GPU tem 55 amostras de contexto do
dispositivo compartilhado, sem uso atribuível ao OrionQ ou ganho GPU comprovado.

## Revisão humana pendente

O pacote automático está completo para revisão do escopo funcional alpha.
`assessment=pending_external_review`, `human_review=pending`,
`trl4_assigned=false` e `hardware_qualified=false` permanecem explícitos.
A decisão exige revisor identificado com
competência pertinente, data, elemento/domínio examinado, hashes e evidências
consultadas, decisão, justificativa e reservas. Nenhum desses campos foi
preenchido ficticiamente por um agente. Revisões internas de código são controles
de desenvolvimento e não substituem avaliação humana independente.

## Interpretação e limites

Os critérios seguem o domínio alpha descrito pela
[NASA NPR 7123.1D, Appendix E](https://nodis3.gsfc.nasa.gov/displayDir.cfm?Internal_ID=N_PR_7123_001D_&page_name=AppendixE)
e pelo
[ECSS-E-HB-11A, A.4/Table A-1](https://ecss.nl/wp-content/uploads/2017/03/ECSS-E-HB-11A%281March2017%29.pdf),
consultados em 09/10/2026. Esses documentos orientam a avaliação; não conferem
certificação ao projeto.

As 20 funções são as funções críticas declaradas para esse domínio alpha. As
35 verificações analíticas cobrem física reduzida; não são calibração
experimental, prova completa TF-QKD nem previsão analítica de desempenho da
interface. A rodada integral aprovada prepara revisão desse domínio,
sem comprovar a validade científica de todo o digital twin.

Permanecem fora da qualificação: hardware/OGS/RF/TDC/FPGA físico, calibração e
segurança experimental QKD, estimador TF-QKD completo admitido, relay orbital
quântico coerente global, replay Swabian nativo sem SDK/licença/FileWriter,
benefício geral dos modelos e hipótese WDM→Bell medida. Capturas têm integridade
verificada, sem avaliação semântica automática de todos os pixels. Os ensaios
locais de backup/consumo não substituem a restauração operacional integral do
workspace nem âncoras externas anti-rollback.

A [reprovação anterior de desempenho TRL 5](orionq_trl5_continuation_20261009.md)
continua válida: G05 reprovado, outros gates ainda não demonstrados. Esta rodada
não relaxa 10 ms/ciclo, 1 s/detalhe, cauda de latência ou duração de soak de TRL 5.
Os modelos permanecem opcionais; controle automático de rede off não significa
que todos os detectores ML estejam inativos. Material emulado não recebe
admissão operacional e nenhuma atuação física é autorizada.
