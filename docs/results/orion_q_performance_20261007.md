# ORION-Q 5.51 — diagnóstico, otimizações e qualificação de desempenho

Consolidado em 08/10/2026, após ensaios de 07–08/10. As correções reduzem substancialmente as pausas no Mapa, no Globo e nas trocas de visualização. A versão compilada foi exercitada diretamente com a topologia completa, navegação, telemetria e simulação. Permanecem pausas ocasionais, custo de primeira abertura de detalhes/Keys, passos científicos mais lentos que um segundo e limites de qualificação descritos abaixo. Não se declara ausência universal de travamentos ou vazamentos.

As alterações existentes, cadastros do operador, resultados científicos e versões anteriores foram preservados. Os modelos continuam opcionais e não houve atuação física. O ZIP completo e seu recibo de integridade ficam na pasta da versão `5.51.0-native-20261008-015715`; os hashes do pacote são publicados separadamente para evitar uma referência circular no próprio arquivo.

## Condições e significado das medidas

Os ensaios utilizaram Windows 11 10.0.26200, Ryzen 7 5700X (8 núcleos/16 threads), 32 GiB de RAM e RTX 3060 de 12 GiB, driver 616.64. A janela tinha 3440×1440 pixels e DPR 1, com Python 3.12.14, PySide6 6.11.2, NumPy 2.5.2 e SciPy 1.18.1. A GPU era compartilhada com outros aplicativos e o compositor: sua utilização não foi atribuída ao ORION-Q.

O cenário `brazil-institutions-v1`, semente `20261004`, tinha 71 nodes, 131 enlaces, nove satélites, 36 sessões tecnológicas, 76 demandas globais e quatro demandas legadas. Simulação e telemetria estavam ativas; o detector opcional estava em SAFE e os demais modelos opcionais desabilitados. Não houve atuação física. Mapa, Globo, navegação entre abas e trocas entre renderizadores foram exercitados durante 20 segundos por fase, seguidos de uso contínuo.

A referência usa os módulos ORION preservados do PYZ 5.50, com seus assets e as dependências registradas no ensaio. Ela não representa a execução integral do antigo `.exe`. Plano de ações, topologia inicial e tamanho da janela foram iguais. Na comparação normal, os nove critérios de configuração coincidiram e a comparação isolada DPAPI alterou apenas `security/vault.py`; os outros patches estão identificados nos recibos de cada execução. Igual tempo de execução não significa igual quantidade de ticks científicos.

O heartbeat de 10 ms mede atrasos do event loop. `paintEvent` mede a duração de execução/submissão do desenho no lado CPU, incluindo possíveis esperas; não mede apresentação de frames pela GPU, FPS ou vsync. Callback, início da pintura após a ação e apresentação completa dos campos são medidas distintas. Profiling, tracemalloc e censo Qt são diagnósticos separados dos ensaios normais. CPU de 100% corresponde a um núcleo lógico; não foi demonstrada redução material da carga total de CPU. Os processos do operador 5956 e 25772 foram observados como carga ambiente e preservados.

## Comparação normal de fonte — uso contínuo de cinco minutos

Valores em milissegundos, antes → depois. Cada p95 descreve sua execução registrada; não é uma garantia para outros computadores ou uma análise de significância entre repetições.

| Cenário | Heartbeat p95 | Heartbeat máximo | Pintura p95 | Callback p95 |
| --- | ---: | ---: | ---: | ---: |
| Mapa | 1625.3 → 53.9 | 2755.5 → 480.5 | 188.2 → 46.6 | 4522.2 → 512.7 |
| Globo | 1910.9 → 63.4 | 2705.9 → 301.1 | 181.0 → 49.6 | 2735.1 → 256.7 |
| Abas | 39.1 → 27.5 | 320.3 → 586.7 | 93.2 → 39.8 | 254.2 → 559.8 |
| Mapa / Globo | 72.0 → 33.9 | 329.1 → 153.4 | 116.7 → 50.8 | 335.7 → 137.3 |
| Uso contínuo, 5 min | 138.6 → 46.8 | 2736.0 → 545.7 | 133.1 → 47.5 | 3841.8 → 561.3 |

O pico de RSS da árvore de processos caiu de 1410.0 para 827.0 MiB. No uso contínuo, as lacunas de event loop ≥250 ms passaram de 149 para 64: pausas ainda existem. As 190 ações planejadas e 90 leituras completas foram concluídas sem divergências de conteúdo, erros ou estouro do prazo de cinco segundos. A auditoria tinha 8.745 registros e checkpoints válidos, sem registros pendentes.

Há uma regressão observada em Keys: o primeiro callback passou de 145.7 para 559.8 ms, com início da pintura em 575.3 ms. Esse acesso explica a piora da cauda na fase Abas, apesar da melhora da mediana e da pintura. Não foi demonstrado que o DPAPI causou essa variação. Nas janelas de detalhes, apresentação completa continua custando mais que o primeiro retorno do callback: p95 de 2.570 ms no Mapa, 1.781 ms no Globo e 2.496 ms no uso contínuo; máximo observado de 2.577 ms.

Fontes: [comparação independente de cinco minutos](E:/Codex/2026-08-27/e/work/NISQ-Predictive-Twin/outputs/performance_20261007/geo/dpapi-five-minute-independent-review-v1.json) e [comparação do ensaio normal](E:/Codex/2026-08-27/e/work/NISQ-Predictive-Twin/outputs/performance_20261007/ui/dpapi-final-soak-comparison.md). Os hashes das entradas constam dos JSONs citados e da revisão independente final.

## Gargalos tratados e escopo das mudanças

| Componente | Causa localizada / mudança adotada | Preservação e limite |
| --- | --- | --- |
| GEO | Projeção e geometria refeitas em pinturas; caches por câmera/contexto, NumPy float64, rasters limitados e chave de transmissões corrigida. | Posições e estados continuam vindo do backend; nove satélites/orbitas permanecem representados. Apenas Mapa ou Globo está instanciado/ativo. |
| Abas, topologia e Inspector | Renderizações e construção de controles ocultos; atualização visual somente da vista ativa, com metadata compartilhada atual. | Seleção e dados permanecem disponíveis; o cache de células da matriz é limitado a 2.048. |
| Detalhes dos nodes | Primeira abertura construía centenas de linhas/cards em um callback. A apresentação desktop cria as novas linhas, seções, estações e conexões em turnos cooperativos. | Um timer por janela, um contexto mais recente; loading e erro explícitos. Uma chamada Qt individual não é preemptível, portanto 50 ms por turno não é garantia. |
| Tabelas satelitais | Muitas instâncias QWidget/QLabel e layouts; Qt `QTableView`/`QAbstractTableModel` somente para tabelas grandes. | Todas as seções, unidades, textos completos, copy, tooltips e acessibilidade permanecem; pequenas listas terrestres foram mantidas. |
| Backend → interface | Conversão/serialização repetidas e publicação concorrente; conversão simplificada, dados apresentados agrupados e ordenados por contexto recebido. | Apenas apresentação pode coalescer contexto; eventos, aquisição, persistência e recibos científicos não são descartados. SDK, autorização e pipelines não são contornados. |
| Cofre DPAPI | Classe `Blob` criada por chamada era retida pelo cache global de ponteiros de ctypes. Tipo único e bindings DLL reutilizados. | Buffers continuam por operação; sem cache de segredos ou de autorizações, sem mudança do perfil CurrentUser ou dos critérios de segurança. |

`field_texts()` serve como metadata e não comprova que os controles foram apresentados. A aceitação de uma janela exige `ready`, ausência de erro/pending, links e seções completos e textos efetivos de todos os widgets da mesma fotografia recebida, com origem, tick e tempo de rede correspondentes. Fechamento, ocultação, remoção ou troca de origem cancelam a apresentação pendente. O método público síncrono foi mantido para callers compatíveis.

A escolha tecnológica foi seletiva. O protótipo Windows das 14 seções/211 linhas sintéticas de satélite mediu 245→109 ms ocioso e 354→155 ms sob worker sintético para criar, popular, mostrar e pintar, com 654→273 widgets. Isso não é um ganho equivalente da aplicação inteira; no cenário real GEO-ATLANTIC foram conferidos 207 campos. Tabelas pequenas regrediram no protótipo nativo e foram rejeitadas. Também foram rejeitados o intervalo de scheduling de 1 ms e quatro threads BLAS: não demonstraram melhora suficiente da interação, embora a segunda opção reduzisse memória privada. Pools de 16 foram preservados. Não foi necessária uma nova linguagem, ABI ou dependência de compilação; NumPy, Qt e zlib já fornecem os trechos nativos utilizados. Migração para processos continua proposta, pois o ganho líquido incluindo IPC não foi comprovado.

Medições isoladas adicionais sustentam as escolhas de integração:

| Trecho / alternativa | Antes → depois / custo observado | Decisão |
| --- | --- | --- |
| Projeção GEO float64 Python → NumPy nativo | 22,25 → 7,75 ms, 2,87×; erro máximo 2,27×10⁻¹³ pixels | Adotada, sem mudar os dados orbitais |
| Estado completo para SDK | 70,90 → 28,59 ms no corpus controlado | Conversão simplificada; resultado completo continua default |
| Compressão zlib nível 6 → 1 | 587,87 → 117,82 ms, 4,99×; disco 22,35 → 24,19 MB (+8,25%) | Adotada, formato/CRC/durabilidade/limites preservados |
| Estado entre processos | 43,55 ms; pickle 1,25 MB / JSON 2,73 MB | Não adotada sem ganho líquido incluindo IPC |
| BLAS 16 → 4, três pares frios | Memória privada −66%; pintura Mapa p95 +23,4%, início de pintura após dispatch +25,2% | Rejeitada para priorizar interação; 16 threads preservadas |

Esses ensaios não são ganhos equivalentes de toda a aplicação. Os três pares BLAS conservaram 27.882 eventos/120 blocos/186 auditorias no corpus científico, mas o passo aquecido não acelerou materialmente. Nenhuma alteração de GIL, GC em background ou poda de caches de segurança foi adotada. Os perfis e custos constam dos relatórios das alternativas.

Evidências: [protótipo Qt](E:/Codex/2026-08-27/e/work/NISQ-Predictive-Twin/outputs/performance_20261007/geo/kv-native-result.md), [GEO e caches](E:/Codex/2026-08-27/e/work/NISQ-Predictive-Twin/outputs/performance_20261007/geo/implementation-notes.md) e [alternativas e custos de integração](E:/Codex/2026-08-27/e/work/NISQ-Predictive-Twin/outputs/performance_20261007/backend/native-alternatives-docfinal-candidate.md).

## Memória — diagnósticos anteriores de vinte minutos, com histórico da coleta em RAM

Este par compara fonte otimizada antes/depois da correção DPAPI, não o antigo 5.50. Valores de RSS abaixo usam medianas de bins de 30 segundos; inclinações robustas usam Theil–Sen dos bins elegíveis.

| Indicador da UI | Antes da correção DPAPI | Depois |
| --- | ---: | ---: |
| Primeiro → último bin RSS (MiB) | 693.34 → 1077.39 | 662.85 → 683.42 |
| Variação (MiB) | +384.05 | +20.57 |
| Inclinação total / segunda metade (MiB/min) | 20.81 / 20.95 | 1.48 / 2.42 |
| Critério formal de plateau da UI | Não | Não |

A árvore inteira cresceu 22.20 MiB. O observador mantinha aproximadamente 40.5 MiB em seus próprios containers de amostras; esse valor lógico **não foi subtraído** de RSS nem da memória privada. A UI não satisfez o critério formal de plateau: amplitude de 33,86 MiB na segunda metade, contra limite de 33,14 MiB, embora a inclinação estivesse abaixo do limite. Isso não permite afirmar ausência de crescimento contínuo ou de vazamentos em uso ilimitado.

Os 40 censos não registraram erro: renderizadores instanciados/ativos =1, timers 27→27 (até sete ativos), widgets 3.470→3.517 (máximo 3.832), uma janela de node registrada e fila de apresentação até sete itens. Foram observadas mortes de wrappers e diminuições de contagens durante as trocas. Os widgets sem parent foram 3→2; suas classes/causas não foram registradas e não podem ser inferidas desses totais. Um censo custou até 107 ms, por isso seus tempos não qualificam a latência normal.

O sampler auxiliar identificou o processo Qt real após 24 s, enquanto a cobertura anterior começou após 271 s. A ausência de amostras iniciais não foi reconstruída. Memória privada comprometida, estimativas de rasters e containers não são RSS nem parcelas diretamente somáveis. O perfil tracemalloc localizou o retentor DPAPI, mas sua própria coleta pesada induziu timeouts: não foi utilizado como ensaio funcional aprovado.

A reprodução isolada de 6.000 chamadas DPAPI retinha 6.000 classes depois de GC, com +34,19 MiB RSS antes. Após a correção, permaneceu um tipo Blob, com +0,71 MiB RSS; 11 testes específicos passaram, incluindo ciphertext legado e concorrência. A evidência confirma esse retentor, não atribui a ele cada MiB da aplicação.

O diagnóstico completou 640 ações planejadas e 315 leituras integrais, sem erros, divergências ou deadlines excedidos; 28.441 registros/checkpoints de auditoria estavam válidos. Fontes: [análise independente de retenção](E:/Codex/2026-08-27/e/work/NISQ-Predictive-Twin/outputs/performance_20261007/geo/dpapi-twenty-minute-retention-v1.json), [limites do censo e memória privada](E:/Codex/2026-08-27/e/work/NISQ-Predictive-Twin/outputs/performance_20261007/geo/dpapi-twenty-minute-independent-review-v1.md) e [reprodução/correção DPAPI](E:/Codex/2026-08-27/e/work/NISQ-Predictive-Twin/outputs/performance_20261007/backend/dpapi-cache-implementation.md).

## Causalidade e validação de apresentação

Na qualificação científica controlada de 12 passos pós-DPAPI, 27.882 eventos e 120 blocos brutos preservaram métricas, ordem, timestamps, aquisição, comunicação clássica, recursos e hashes; os 186 registros/checkpoints de auditoria eram válidos. O passo aquecido variou de 3.055,59 para 3.047,68 ms, sem ganho material atribuído ao DPAPI. Não se declara igualdade byte a byte de todo o JSON de estado, nem integração física validada. Fonte: [corpus científico controlado](E:/Codex/2026-08-27/e/work/NISQ-Predictive-Twin/outputs/performance_20261007/backend/post-dpapi-science-results.md).

Os validadores de ponte/globo passaram a esperar a apresentação integral recebida, em vez de consultar controles ainda incompletos. O novo lote teve **19 testes aprovados e 13 esperas reais completas** de 122.5 a 384.3 ms, dentro de cinco segundos incluindo leitura. Todos os nove satélites foram inspecionados; origem simulada, contexto exato, seções, conexões, ciência e estações foram conferidos. Casos adversariais cobriram ready prematuro, fotografia errada, check falso, erro e deadline. Esses resultados qualificam os validators em fonte; não reclassificam um `.exe` já rejeitado. Evidências: [XML dos 19 testes](E:/Codex/2026-08-27/e/work/NISQ-Predictive-Twin/outputs/performance_20261007/geo/satellite-validation-readiness-v1/regression.xml) e [13 esperas e checks completos](E:/Codex/2026-08-27/e/work/NISQ-Predictive-Twin/outputs/performance_20261007/geo/satellite-validation-readiness-v1/readiness-summary.json).


## Executável de produção — carga normal de cinco minutos

O processo da UI foi o próprio `ORION-Q.exe` (PID 24296), `frozen=true`, origem `frozen`, sem fallback para código fonte. O cenário final manteve 71 nodes, 131 enlaces, nove satélites, 36 sessões e 76 demandas globais, com origem simulada. Os nove critérios de comparabilidade passaram: plano completo e fases regulares, escopo de pintura, leitura dos detalhes, dimensões reais da janela/viewport e monitor. A referência continua sendo os módulos 5.50 extraídos do PYZ em um ambiente registrado; **esta tabela não é um par de dois executáveis integrais**. O par de fonte acima e a execução do binário são evidências distintas.

| Cenário | Heartbeat p95 (ms) | Heartbeat máximo (ms) | Pintura p95 (ms) | Callback p95 (ms) |
| --- | ---: | ---: | ---: | ---: |
| Mapa | 1625.3 → 53.2 | 2755.5 → 506.9 | 188.2 → 33.4 | 4522.2 → 492.2 |
| Globo | 1910.9 → 68.5 | 2705.9 → 376.3 | 181.0 → 50.5 | 2735.1 → 271.6 |
| Abas | 39.1 → 26.5 | 320.3 → 354.7 | 93.2 → 40.4 | 254.2 → 336.7 |
| Mapa / Globo | 72.0 → 34.0 | 329.1 → 159.7 | 116.7 → 48.5 | 335.7 → 163.5 |
| Uso contínuo, 5 min | 138.6 → 46.1 | 2736.0 → 528.5 | 133.1 → 45.9 | 3841.8 → 551.3 |

A GPU compartilhada teve medianas por fase de 29–34% de utilização, 3473–3474 MiB usados e 23,99–24,39 W. Os arquivos registram as amostras do dispositivo; esses valores incluem compositor e aplicativos do operador, sem atribuição exclusiva ao ORION-Q e sem inferência de ganho de GPU.

No uso contínuo, RSS máximo da árvore: **1410,0 → 737,8 MiB**; CPU mediana: **103,6 → 105,7% de um núcleo**, sem ganho material de CPU; lacunas ≥250 ms: **149 → 64**. As 190 ações, dez abas e 90 leituras completas passaram sem divergências, erros ou deadlines excedidos. Foram observadas 90 solicitações de modo/86 mudanças efetivas, incluindo mudanças provocadas pela seleção; 85 alternâncias estavam no plano. Todos os satélites foram acessados. Renderizadores instanciados/ativos: um, em todas as fases amostradas; fila de seleção até um job, zero rejeições, zero jobs pendentes no recibo final.

Apresentação completa p95: Mapa 2445,8 ms, Globo 1633,8 ms e uso contínuo 2544,0 ms. Um feedback de loading ou o fim da callback não significa que todos os controles já estão prontos. No uso contínuo, o início de pintura após callback teve p95 de 747,3 ms. As dez ações da fase Abas são poucas para caracterizar uma cauda estatística; seu p95 de callback passou de 254,2 para 336,7 ms. A regressão de primeira abertura de Keys observada no ensaio em fonte permanece registrada acima. As pausas não desapareceram.

O rodapé registra overruns: 100 passos excederam o período desejado de um segundo, e o último levou 4450,5 ms. O relógio científico permanece causal, com backlog de protocolo zero e 190 eventos futuros pendentes; isso não significa que todos os eventos terminaram nem que a simulação acompanha relógio real. Os 9059 registros de auditoria tinham checkpoints válidos e zero registros não checkpointados. Coalescência: 123 polls e 44 frames de seleção visuais; nenhum evento científico foi descartado por esse mecanismo.

Evidências: [comparação normal do binário](E:/Codex/2026-08-27/e/work/NISQ-Predictive-Twin/outputs/performance_20261007/ui/frozen-final-soak-comparison.json), [relatório do próprio EXE](E:/Codex/2026-08-27/e/work/NISQ-Predictive-Twin/outputs/performance_20261007/ui/after-frozen-final-soak/report.json) e [revisão independente final](E:/Codex/2026-08-27/e/work/NISQ-Predictive-Twin/outputs/performance_20261007/geo/final-performance-independent-review-v1.md).

## Diagnóstico final de vinte minutos — observador limitado

Esta execução em fonte é um diagnóstico separado, com censo Qt a cada 30 s; seus tempos não substituem a medição normal. Apenas as três séries do observador foram transmitidas para arquivos JSONL, com fila de 512 entradas mais uma em escrita, buffer de 8 KiB e histórico recente de enqueue limitado. Ações, snapshots, eventos, recibos, proteção contra replay e consumo continuam preservados. A interferência e a mudança do armazenamento do observador impedem atribuir toda a diferença de RSS à aplicação.

| Medida no soak | Primeiro → último bin de 30 s | Tendência robusta total / segunda metade | Critério formal de plateau |
| --- | ---: | ---: | --- |
| RSS UI (MiB) | 697.59 → 690.25 | 0.40 / 0.38 MiB/min | Passou nesta janela |
| Memória privada UI (MiB) | 3541.13 → 3564.33 | 1.13 / 0.58 MiB/min | Passou nesta janela |

RSS: −7,34 MiB; amplitude dos bins na segunda metade 24,34 MiB, limite previamente definido 34,88 MiB. Memória privada: **+23,19 MiB**, e não redução; amplitude 23,99 MiB, limite relativo 177,06 MiB. O critério é descritivo de curto prazo (5% ou 32 MiB para amplitude; 1%/min ou 6 MiB/min para inclinação), não ausência de vazamentos. RSS, private commit e memória lógica dos containers não são somados nem subtraídos. O auxiliar de cinco segundos mediu o PID Qt verdadeiro 39360: 263 amostras, 240 no soak, início 4,24 s; o início foi anterior às fases temporizadas (coverage_started_late=false); os primeiros segundos não foram reconstruídos. O markdown descritivo antigo conserva uma frase genérica de cobertura tardia, que não prevalece sobre os timestamps do JSON. O sampler interno que seguiu o shim foi preservado e excluído da memória privada da UI. Dados privados anteriores amostrados a dez segundos não qualificam a mesma cobertura dos bins.

As 640 ações e 315 leituras integrais, incluindo todos os satélites, passaram com zero erros, divergências ou violações de cinco segundos. Houve 315 solicitações de modo/311 mudanças efetivas; 310 alternâncias estavam planejadas. Fonte e configuração permaneceram imutáveis. Auditoria: 28751 registros/checkpoints válidos, zero pendentes. Último passo científico 4634,1 ms; zero rejeições de seleção/backlog de protocolo, 170 eventos futuros pendentes.

Foram 40 censos no soak/44 no total, sem erro ou perda: renderer instanciado/ativo sempre um; widgets 3469→3576 (máximo 3910); widgets sem parent 2→5 (máximo 12); timers 27→29 (máximo 29), até oito ativos, zero sem parent. Uma janela de node registrada; fila de apresentação 0→6 (máximo 26). As diferenças finais incluem trabalho de apresentação observado em andamento; os totais não provam por si só nem vazamento nem liberação completa. O JSON registra classes globais e mortes de wrappers, mas não atribui os widgets sem parent a classes específicas. Uma chamada nativa Qt não é preemptível: no EXE, um turno de apresentação chegou a 259,9 ms, acima do orçamento cooperativo; não há garantia rígida de 12/50 ms.

O censo custou até 43,19 ms e obteve só uma leitura detalhada não bloqueante do backend; 39 foram puladas. Esses intervalos não são considerados iguais a zero. Os conjuntos de antirreplay/consumo global cresceram e não foram podados; recursos de apresentação têm limites, mas não foi demonstrado limite de memória de todos os históricos científicos. Não se certifica estabilidade por horas/dias, GPU por aplicação ou ausência de vazamentos em todos os componentes.

Coleta: 80541 heartbeats, 47321 pinturas e 2486 contadores, total **130348**, integralmente verificados por hash/bytes/sequência/conservação. Fila do observador: pico 10/512, final zero, sem overflow/I/O/perdas; drenagem após fases 0,343 ms. Containers restantes da instrumentação: 2.39 MiB lógicos, não RSS. A tentativa v1 falhou antes da UI com FileExistsError e permanece preservada. A leitura de metadados zerados em arquivos abertos no v2 foi um falso alarme; o histórico comprova coleta desde o início e nenhum processo foi interrompido.

Fontes: [análise final de memória/objetos](E:/Codex/2026-08-27/e/work/NISQ-Predictive-Twin/outputs/performance_20261007/ui/bounded-observer-20min-v2-analysis.json), [coleta completa e verificada](E:/Codex/2026-08-27/e/work/NISQ-Predictive-Twin/outputs/performance_20261007/ui/bounded-observer-20min-v2/stream-integrity-verification.json) e [qualificação do observador](E:/Codex/2026-08-27/e/work/NISQ-Predictive-Twin/outputs/performance_20261007/geo/streamed-observer-qualification.md).

Os journals finais também registram o volume operacional da carga:

| Execução | Eventos de tecnologia | Eventos de operação global | Aquisições satelitais persistidas | Decisões |
| --- | ---: | ---: | ---: | ---: |
| EXE, cinco minutos | 226567 | 23694 | 1000 | 124 |
| Fonte, diagnóstico de vinte minutos | 731360 | 75727 | 3260 | 274 |

São contagens do SQLite final, distintas das amostras do observador, sem supor que a referência e a versão nova completaram o mesmo número de ticks em tempo de parede igual. A equivalência científica de eventos/timestamps foi conferida no corpus controlado de passos iguais.

## Inicialização completa e gates do novo executável

O monitor externo executou ambos os EXEs completos. Antes/depois: processo **42,761 → 42,645 s**, sem ganho material; primeiro snapshot após show **9,719 → 13,563 s**, regressão de 3,844 s; heartbeat p95 **47 → 32 ms**, máximo **891 → 860 ms**. No novo: cinco redimensionamentos, 15 ações, 12 snapshots, todos concluídos; RSS máximo da árvore 818,0 MiB. Tempo total inclui a jornada e sua espera de 16 s após o snapshot, não apenas carregamento. O benchmark normal tem outro procedimento de startup e não substitui essa medida.

Identidade do EXE: SHA-256 `ed5f111e60404991e26c9ed3abfdc24f88b25b13c63ed2969a3d1faecc602d01`. Freeze/gates verificaram 529 arquivos de fonte, 82 assets e dez arquivos de build, sem diferenças. As verificações finais de origem e pacote são publicadas na pasta da versão. Não houve teste em máquina independente ou arraste físico da borda pelo mouse; redimensionamentos foram eventos programáticos no Qt nativo Windows.

| Validação compilada | Resultado | Escopo/limite |
| --- | --- | --- |
| Self-test da interface | 39/39 PASS | Fluxos completos e recibos da UI; separado de latência |
| Campanha temporal | 25/25 PASS | Worker real do EXE, inicialização das DLLs antes do listener |
| Roadmap | 4/4 PASS | 20/20 ciclos excederam 10 ms; p95 357,72 ms, máximo 382,96 ms; p99 inadequadamente amostrado |
| Laboratório FULL | 12/12 PASS | Emulação/replay ORION, quatro processos independentes; hardware/Swabian nativo não qualificados |
| Satélite | 21/21 PASS | Software/OGS hipotética de 6 m; mission-ready e hardware false |
| Global FULL | 33/33 PASS | Terminais GEO/ISL hipotéticos e probes clássicos; sem relay quântico global operacional |
| Stress | 20 rounds, 140 ciclos, 535/535 checks PASS | Controlador sintético; latência 350,32–1067,73 ms, não tempo de frame |
| Startup/DPI | 6/6 casos PASS | service 100/125/150/200%, demo 100/150%; quatro/cinco resizes por caso |
| Segurança comum | 14/14 PASS | Identidade Windows, DPAPI, API anônima/Host/permissões/revogação/auditoria/logs |
| Opcionais sem pacotes / ZIPs originais | 11/11 modelos em cada modo PASS | SAFE/admissão/inferência local, políticas não ativadas e atuação física false |

Os 14 checks de segurança do EXE não são um ensaio integral de PKI/hardware: revogação de sessão/identidade é distinta de revogação de certificado. Os 126 testes de segurança de componentes e as campanhas laboratoriais exercitam os controles complementares documentados. Nenhum resultado certifica geração experimental de chaves ou interlocks físicos; `certified_secret_bits=0`.

O importador QKD usou o ZIP original SHA-256 `94352f15b6dcb132a3c03559b9d94b28f42b6e9adb8b2cdaab18f46720a55a79`; Qnet original `6b8563209f54a2d566f1a4229b140b75fda93555085ff2bf6bfcad8f07abd938`. Nenhum pacote foi refeito para contornar hash. Isso é smoke de admissão/inferência, não nova campanha científica dos modelos.

A qualificação de fonte contabiliza **1199 casos distintos mais recentes aprovados, em 44 lotes**, sem failures/errors/skips nos resultados efetivos. Não é um full-suite único fresco; falhas históricas e candidatas rejeitadas permanecem guardadas. Os validators conservam os critérios originais: 21 testes de apresentação, 19 da ponte/globo, 14 de regressão funcional e a jornada original de 42 checks (30 service/12 synthetic). A correção sintética conserva ambos os canais; clocks ausentes continuam None, sem observações inventadas. A varredura heurística selecionada de 1271 arquivos públicos não encontrou segredos; não equivale a uma auditoria de todo o histórico Git. Lint crítico dos arquivos alterados e diff-check passaram; lint de estilo legado não foi reformatado globalmente.

As candidatas `003500` (timeout nativo) e `010926` (self-test recusado) mantêm `DO-NOT-DISTRIBUTE`. Não foram reclassificadas. A correção do worker foi reproduzida com stdin aberto: leitor antes de DLLs travava; bibliotecas inicializadas primeiro completaram os quatro ensaios e a campanha do EXE, preservando protocolo/cancelamento/semente/paridade. Não altera o import comum do console.

Recibos: [campanha temporal](E:/Codex/2026-08-27/e/work/NISQ-Predictive-Twin/release/windows/5.51.0-native-20261008-015715/campaign-validation/report.json), [self-test](E:/Codex/2026-08-27/e/work/NISQ-Predictive-Twin/release/windows/5.51.0-native-20261008-015715/self-test.json), [laboratório](E:/Codex/2026-08-27/e/work/NISQ-Predictive-Twin/release/windows/5.51.0-native-20261008-015715/laboratory-validation/report.json), [satélite](E:/Codex/2026-08-27/e/work/NISQ-Predictive-Twin/release/windows/5.51.0-native-20261008-015715/satellite-validation/report.json), [global FULL](E:/Codex/2026-08-27/e/work/NISQ-Predictive-Twin/release/windows/5.51.0-native-20261008-015715/global-validation/report.json), [startup completo](E:/Codex/2026-08-27/e/work/NISQ-Predictive-Twin/outputs/performance_20261007/ui/frozen-after-startup-1/monitor.json), [segurança compilada](E:/Codex/2026-08-27/e/work/NISQ-Predictive-Twin/outputs/security_20261007/frozen-security/run-20261008-025445/report.json), [ZIPs originais](E:/Codex/2026-08-27/e/work/NISQ-Predictive-Twin/outputs/performance_20261007/frozen/optional-original-v1/external_packages/optional-models-report.json), [1199 casos de fonte](E:/Codex/2026-08-27/e/work/NISQ-Predictive-Twin/outputs/performance_20261007/root-tests/source-qualification-progress.json) e [diagnóstico do worker nativo](E:/Codex/2026-08-27/e/work/NISQ-Predictive-Twin/outputs/performance_20261007/backend/temporal-worker-startup-diagnosis.md).

## Reprodução e limites restantes

Usar diretórios novos, dependências/hardware/configuração registrados, seed 20261004, mesma topologia e janela real, sem benchmark/build concorrente. Os estados de teste são isolados do operador. Preservar todos os registros, fases, hashes e erros; não aumentar os deadlines ou ajustar retrospectivamente os critérios de plateau.

```powershell
$env:PYTHONPATH = 'src'
$env:QT_QPA_PLATFORM = 'windows'
.audit-winvenv\Scripts\python.exe tools\benchmark_console_performance.py `
  --output outputs\performance-new\source-soak `
  --duration 20 --soak 5 --size 3440x1440
.audit-winvenv\Scripts\python.exe tools\benchmark_console_performance.py `
  --executable release\windows\5.51.0-native-20261008-015715\ORION-Q\ORION-Q.exe `
  --output outputs\performance-new\frozen-soak `
  --duration 20 --soak 5 --size 3440x1440
```

Profiling deve ocorrer separadamente (`--local-profile`/`--profile`), conservando sua interferência; não substituir a medição normal. O diagnóstico limitado está em `outputs/performance_20261007/geo/benchmark_streamed_observer_diagnostic.py`. Para reproduzir, copiar/configurar o launcher externo com caminhos de saída novos; ele usa a mesma carga com soak 20, censo de 30 s, amostrador auxiliar de cinco segundos e verificador integral. O procedimento e os hashes estão em `launch_bounded_observer.ps1`, `resource-diagnostic.json` e na revisão independente. A taxa de emissão científica e a precisão temporal não são aceleradas pelo observador.

Plano normal SHA-256 `d8090f01b00cd16a77bcdd05dd43500a311098ad4aad700f503853136eb1c895`; o plano de soak 20 tem outro hash porque contém mais ações. Topologia inicial SHA-256 `749b3c87bfc4e4aad84dca02573177e2b3b863c91f10b3adac606fcd9a655824`. Qt events foram exercitados na janela Windows; não houve injeção física de mouse. Os dados de GPU são contexto compartilhado, e não consumo exclusivo ou frame timing por aplicação.

Ficam limites concretos: pausas ≥250 ms e apresentação completa de até alguns segundos; primeira abertura de Keys/startup com regressões observadas; passos científicos de 3–4,6 s; nenhuma garantia de 60 FPS/10 ms; memória privada ainda crescente nesta janela; histórias/replay/consumo não podados; qualificação de horas/dias e outra máquina pendente; hardware, calibração, sincronismo e segurança QKD experimental pendentes.

A revisão automática rejeitou duas ações específicas: a proposta de alterar a transação central de persistência/recuperação global, por risco de perda de dados, e a execução do protótipo gráfico de paleta, por exigir autorização humana específica. As perguntas correspondentes estão pendentes; ambas ficaram sem aplicação/execução. A proposta de durabilidade e seus riscos reproduzidos continuam preservados em `backend/global-durability-proposed.md` e `.patch`; não foram contornados nem mascarados como resolvidos. Isso não impede as correções autorizadas e os ensaios concluídos acima, mas impede declarar recuperação global por queda completamente qualificada.
