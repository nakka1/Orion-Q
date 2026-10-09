# ORION-Q — observações de protocolos nos detalhes dos pontos — 08/10/2026

A janela de pontos como IBM-WATSON consultava exclusivamente `LiveLink.wave`, a série de polarização dos enlaces legados, mesmo quando o enlace era gerenciado por TF-QKD, repetidores ou satélites. Esses protocolos publicam observações e resultados em `LiveView.technologies.sessions` e `LiveLink.provenance.protocol_metrics`; não publicam uma onda de polarização a 20 Hz. O aviso genérico “No samples received” confundia contratos diferentes.

A versão 5.53 apresenta essas observações em uma tabela própria, acima dos detalhes das conexões. O gráfico continua exibindo somente a série de polarização publicada pelo backend. Quando todas as conexões são gerenciadas por protocolos, esse gráfico e sua animação ficam ocultos. Em pontos mistos, a tabela e o gráfico coexistem com escopos explícitos.

## Diagnóstico reproduzível

O diagnóstico [audit_demo_samples.py](../../outputs/demo_samples_20261008/audit_demo_samples.py) consultou, em modo somente leitura, o journal encerrado da qualificação 5.52 e seus contratos de configuração. Não alterou estado de operador ou resultados. [audit_result.json](../../outputs/demo_samples_20261008/audit_result.json) registra os detalhes por ponto e por sessão. O `net-model.json` daquela coleta foi capturado no início, a 2 segundos; seus estados iniciais não representam os resultados ao final do journal.

| Evidência daquela execução | Resultado |
| --- | --- |
| Componentes / enlaces | 71 pontos, 131 enlaces |
| Contratos dos enlaces | 35 legados e 96 gerenciados por protocolos |
| Pontos somente com protocolos | 41: 32 terrestres e 9 satélites |
| Pontos com os dois tipos | 30 |
| Sessões com aquisição, recebimento clássico e cálculo registrados | 36 de 36 |
| IBM-IQC-TF | 123 sequências adquiridas e recebidas |
| US-REPEATER-EXPERIMENT | 861 sequências adquiridas e recebidas, somando os segmentos da sessão |
| Sessão GEO associada ao IBM-WATSON | 30 sequências adquiridas e recebidas |

O último bloco TF registrado tentou 5.000.000 preparações Z e observou zero cliques Z. O último bloco de geração examinado no repetidor teve 125.000 tentativas e zero heralds. O último registro GEO examinado continha 2.398 e 2.535 cliques TDC nos seus dois braços. Cliques individuais, recebimento clássico e tentativas não comprovam por si só coincidências úteis, entrega de pares, disponibilidade atual ou chave segura. Esses resultados não foram modificados para preencher a interface.

## Apresentação e escopos

`node_protocol_observations()` apenas formata a publicação recebida. A tabela distingue estado e causa da sessão, tecnologias e extremos, origem, número de observações e instante do último recebimento clássico. O número de observações e as taxas agregadas pertencem à **sessão**. Os estados e impedimentos dos enlaces incidentes e os heralds de segmentos locais são identificados separadamente. QBER e fidelidade conjuntos de uma ponte satelital continuam identificados como métricas conjuntas, sem atribuição aos seus braços individuais.

A presença de um dicionário `metrics`, de taxas iniciais iguais a zero ou do estado `operational` não substitui evidência de aquisição. As linhas científicas só são apresentadas após publicação de `last_observed_at_s`. O instante `0.0` é aceito; campos não publicados aparecem como indisponíveis, sem valores substitutos. Um segmento sem heralds não recebe fidelidade estimada na tabela. Um resultado histórico permanece acompanhado de seu instante e do estado atual, inclusive quando o enlace foi bloqueado ou desabilitado.

A seção usa `MetricTable` e participa da mesma fila cooperativa, geração de apresentação e verificação de conteúdo completo das demais seções. Publicações intermediárias são coalescidas para a mais recente. Alterações de fonte ou de tipo invalidam a apresentação anterior; painéis ocultos continuam sem mutações Qt a cada tick. Na apresentação mista, hover ou clique de um enlace tecnológico não transforma seu resultado em série de polarização e não apaga o destaque do gráfico legado. As legendas dos enlaces legados mantêm a mesma cor e estilo das curvas; protocolos não recebem uma amostra de legenda de onda. Uma seção vazia não altera a ordem inicial de materialização das seções existentes.

Quando um enlace legado realmente não fornece sua série, o texto nesta janela é **Nenhuma amostra de polarização recebida**, sob o título **Visibilidade de polarização · enlaces legados**. Isso distingue a ausência desta medição das observações protocolares que aparecem na tabela.

O backend, as fontes, os adaptadores, os protocolos, a causalidade, os parâmetros físicos, a geração científica e os critérios de admissão de material QKD permanecem preservados. A instituição IBM continua sendo um ponto geográfico simulado; a correção não restaura integrações com contas ou serviços IBM Quantum.

## Uso e validação

Abra a demonstração, entre em GEO ou Topology e selecione IBM-WATSON, cujo identificador de apresentação é `US-IBM-TW`. Consulte **Observações dos protocolos** e os cartões de conexão. A tabela mostra dados do backend e causas dos impedimentos; o painel de satélite mantém a apresentação específica de housekeeping e science.

Os testes focados estão em [test_console_protocol_observations.py](../../tests/test_console_protocol_observations.py). A suíte final [final-regression-r3.xml](../../outputs/demo_samples_20261008/final-regression-r3.xml) passou **318 testes distintos em 205,88 s**, sem falhas, erros ou skips. Inclui os 12 casos focados, 16 testes independentes do verificador e os testes de materialização, atualização por tick, apresentação de satélites, tabelas, campos longos, scroll, composição, inspeção e continuidade das ondas. O estado temporário foi criado em um diretório novo dentro do repositório. As execuções anteriores não são somadas a esse total.

O verificador da coleta de interface também exigia a série de polarização para todos os enlaces. Foi atualizado para conferir a tabela dos protocolos e somente as séries legadas correspondentes, mantendo o limite de apresentação de 5 segundos e a leitura completa das demais seções. [test_console_protocol_validation.py](../../tests/test_console_protocol_validation.py) confere quantidades diretamente nos registros publicados e testa a rejeição de alterações no QBER, contadores, horários, origem, estado, chave certificada, amostras, taxa, relógio e legendas. Uma curva tecnológica inventada também é rejeitada.

| Caso | Evidência final | Resultado |
| --- | --- | --- |
| Sessões TF, repetidor/memória e satélite com recebimento | Tabela por sessão, sem `wave` inventada | Aprovado |
| Configuração sem bloco recebido, com taxas iniciais zero | Aguarda aquisição e recebimento clássico; sem linhas científicas substitutas | Aprovado |
| Bloco recebido com zero heralds | Recebimento explícito, contadores zero preservados, fidelidade do segmento ausente | Aprovado |
| Observação em `0.0 s` | Timestamp zero considerado válido | Aprovado |
| Campos e sessão não publicados | Ausência explícita, sem defaults de resultados | Aprovado |
| Dois enlaces da mesma sessão | Sessão apresentada uma vez, estados locais separados | Aprovado |
| Pontos mistos | Wave exata dos enlaces legados; tabela e legendas dos protocolos separadas | Aprovado |
| Enlace indisponível com observação histórica | Estado, causa e instante permanecem visíveis | Aprovado |
| Alteração da origem dos dados | Conteúdo anterior invalidado; nova origem exibida | Aprovado |
| Atualizações antes de completar a janela | Coalescing e readiness correspondem à publicação atual completa | Aprovado |
| Verificador com dados de protocolo ou gráfico adulterados | Divergências rejeitadas sem relaxar o prazo de apresentação | Aprovado |
| Topologia completa pelo backend e Provider | 71 pontos, 131 enlaces, 36 sessões, 20 instituições nos ticks 3 e 6 | Aprovado |

A execução [validate_full_demo.py](../../outputs/demo_samples_20261008/validate_full_demo.py) usa seis chamadas explícitas ao serviço em estado isolado, sem stepper em background. A repetição final [full_demo_provider_final/report.json](../../outputs/demo_samples_20261008/full_demo_provider_final/report.json) passou **34 verificações em 46,30 s**. Nos dois checkpoints, as contagens de observações das 36 sessões coincidem com os recebimentos clássicos no journal. Os 20 painéis Qt reais apresentam a tabela correspondente, mantêm a wave legada exata e não criam overflow horizontal; seu readiness identifica o tick correto. A renderização não altera o snapshot científico. Onze módulos de UI, Provider e backend conservaram hashes SHA256 iguais no início e no fim da execução.

## Executável 5.53

A nova versão está em `release/windows/5.53.0-native-20261008-101623`. Seu SHA256 é `1174705e528a79484b52280f1ea90b6579d159adf40a14d0de66b6945a602d27`. Os manifests de fonte, recursos e dependências não mudaram durante a compilação ou os testes compilados. Em relação à fonte qualificada da versão 5.52, as alterações de produção são `node_window.py`, `wave_chart.py` e `performance_validation.py`, mais o novo formatador `protocol_observations.py`.

| Verificação no novo `.exe` | Resultado |
| --- | --- |
| Autoteste geral | 39 verificações aprovadas |
| Roadmap | 4 verificações aprovadas |
| Campanha temporal e seus recibos | 25 verificações aprovadas |
| Laboratório | 12 verificações aprovadas |
| Satélite / OGS | 21 verificações aprovadas |
| Rede global | 33 verificações aprovadas |
| Estresse do controle | 20 rodadas, 140 ciclos, 535 verificações aprovadas |
| Inicialização | 6 cenários aprovados: serviço em 100%, 125%, 150%, 200%; demonstração em 100% e 150% |
| Modelos opcionais sem pacotes | 11 modelos permanecem descarregados; diagnóstico aprovado |

A coleta [ui-frozen-5-53-r2-20261008/report.json](../../outputs/demo_samples_20261008/ui-frozen-5-53-r2-20261008/report.json) passou na plataforma Qt nativa `windows`, com código compilado e hash do executável iguais no início e no fim. Foram **60 seleções com leitura completa correta**, 60 comandos de visualização, 56 mudanças efetivas de modo (55 previstas no plano e uma transição inicial), 10 navegações entre abas e três minutos finais de operação contínua. Não houve divergência de seleção, modo, navegação ou hit-test, violação do prazo de 5 segundos, timeout externo ou erro da aplicação. A carga manteve 71 pontos, 131 enlaces, nove satélites e 36 sessões. Nenhum processo existente do usuário foi modificado.

O ambiente de execução foi Windows 11, Python 3.12.14 e Qt/PySide6 6.11.2, com janela solicitada e efetiva de 3440×1440 e escala 1,0. [configuration.json](../../outputs/demo_samples_20261008/ui-frozen-5-53-r2-20261008/configuration.json) registra versões e condições completas, incluindo a distribuição instalada no ambiente do lançador; a identidade da versão compilada é comprovada pelo hash do `.exe`. A primeira pintura da cena ocorreu em 122 ms e a primeira publicação do backend em 6,78 s. A coleta durou aproximadamente 286,5 s.

| Fase | Painéis conferidos | Conteúdo completo p95 / máximo | Intervalo de heartbeat p95 / máximo | RSS máximo da UI |
| --- | --- | --- | --- | --- |
| Mapa | 7 | 4.166 / 4.166 ms | 113 / 550 ms | 557,0 MiB |
| Globo | 8 | 2.403 / 2.403 ms | 124 / 323 ms | 561,3 MiB |
| Operação contínua, 3 min | 45 | 3.093 / 4.062 ms | 83 / 740 ms | 625,9 MiB |

Conteúdo completo inclui todas as seções materializadas e sua conferência contra a publicação recebida; não equivale ao primeiro feedback visual. Ainda ocorreram pausas mensuráveis, incluindo um intervalo de heartbeat de 1.365 ms na fase de abas. A mediana de RSS da UI variou de 543,0 MiB no mapa a 609,3 MiB na operação contínua, com carregamento de abas e painéis. Esse ensaio não estabelece ausência de vazamentos ou estabilidade por tempo ilimitado. A pintura medida na cena Qt não é FPS nem latência do compositor. O ensaio abre um painel flutuante por vez.

O [qualificador](../../outputs/demo_samples_20261008/qualify_delivery.py) grava [qualification.json](../../release/windows/5.53.0-native-20261008-101623/qualification.json) somente após conferir essas evidências, a integridade do pacote portátil, os hashes dos artefatos anteriores e a correspondência deste relatório com a cópia no pacote. Sua verificação de integridade não substitui os testes funcionais compilados acima. O ZIP correspondente é `ORION-Q-5.53.0-native-windows-x64.zip`; extraia a pasta completa e execute seu `ORION-Q.exe`.

O candidato anterior `5.53.0-native-20261008-093514` e sua coleta são preservados, marcados como não distribuíveis e não usados como evidência de aprovação, pois ainda continham o verificador que exigia ondas legadas para os protocolos. Os tempos de leitura daquela coleta não constituem uma comparação válida de desempenho. A execução final passou sem alterar os critérios de prazo ou fabricar dados.

## Limites

Não foi criada uma série temporal artificial para protocolos que não publicam `LiveLink.wave`. Não foi alterada a distância de enlaces ou sua física para garantir sucesso. A demonstração continua identificada como simulada, sem atuação física, validação de instrumento conectado ou certificação de segurança QKD.
