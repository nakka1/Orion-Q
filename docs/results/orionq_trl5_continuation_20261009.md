# OrionQ — continuação da qualificação SOFT-DT, 09/10/2026

Esta rodada continua o [ensaio inicial](orionq_trl5_initial_campaign_20261009.md)
e o [protocolo de qualificação](../product/orionq_trl5_qualification.md).
O escopo permanece software e gêmeo digital. Não atribui TRL 5 à rede física,
à geração de chaves QKD ou a equipamentos. Os critérios de aceitação continuam
iguais; modelos opcionais, configurações existentes e bloqueio físico foram
preservados. A distribuição 5.60.1 existente não foi recompilada nesta rodada.

## Diagnóstico e correções

| Componente | Causa/evidência | Alteração |
| --- | --- | --- |
| Persistência do controle | No run5, `journal.append` consumia 186,89 dos 195,65 ms/ciclo. O profiler isolado localizou o custo principalmente no commit SQLite | Conexão ociosa mantém o pager WAL inicializado em versões/discos elegíveis; operações continuam em conexões próprias, com `synchronous=FULL` verificado |
| Fechamento de detalhes | Teste Qt reproduziu sinal `closed` de uma janela antiga removendo a nova janela com o mesmo ID | Registry confere a identidade do emissor antes de aceitar o sinal; fechamento explícito por ID continua disponível |
| Construção dos cards | Cards eram montados e estilizados antes de receber seu parent final | Construção já no parent; esta mudança isolada não apresentou ganho consistente nas duas medições curtas |
| Fila visual | CIMATEC/CBPF exigiram 36–39 turnos; intervalos medianos próximos de 21 ms contribuíam para atraso acumulado | Comparação de cadência com limite de trabalho preservado; frequência científica não é alterada |
| Instrumentação | O erro original não registrava motivo/instância do fechamento | Diagnóstico opt-in de ciclo de vida e etapas de apresentação, sem credenciais e com contador de registros descartados |
| Admissão anônima | Regressão reproduziu aceitação de um contexto explicitamente anônimo na fila; o worker recusava depois | Dispatch assíncrono recusa antes de enfileirar, sem ler o cofre na thread Qt; identidade capturada legítima e revalidação posterior permanecem |
| Evidências | As novas regressões de recuperação também devem pertencer ao manifesto | Congelamento passa a incluir os testes Python e as ferramentas TRL5, com detecção de alteração/adição posterior |

O teste de sinal tardio falhou antes da correção e sua evidência foi preservada
em `outputs/trl5_continuation_20261009/details-stale-close-before.xml`.
Os diagnósticos curtos não reproduziram o desaparecimento da janela do run5:
não se atribui a ele a mesma causa apenas porque o defeito de identidade existe.

## Persistência: comparação de protótipos

[Profiler](../../tools/profile_trl5_persistence.py): mesmo ciclo simulado de
produção, bases próprias novas e conexões instrumentadas. Não acessa o workspace
do operador. SQLite 3.53.1, Windows e disco local fixo, conforme os registros.
O custo do profiler/cProfile está incluído; 15 amostras não qualificam p99.

| Protótipo no run4 | Média do ciclo | p95 | Média de append | Média do commit |
| --- | ---: | ---: | ---: | ---: |
| DELETE, padrão | 203,17 ms | 236,89 ms | 191,45 ms | 189,75 ms |
| WAL com pager mantido aberto | 73,65 ms | 87,74 ms | 57,26 ms | 56,21 ms |

Redução da média: aproximadamente 63,75%. O WAL permaneceu com 440.872 bytes
depois de todos os fechamentos dos writers, sem transação aberta no keeper.
O último fechamento custou 122,90 ms; o backup online custou 159,18 ms.
Todos os 18 recibos, hashes e contagens foram verificados, além de um replay de
request_id; o backup reproduziu os registros confirmados. Esses custos são expostos, não absorvidos
silenciosamente pela média dos ciclos.

Evidência: [report.json](../../outputs/orionq_trl5_persistence_profile_20261009_run4/report.json).
As rodadas anteriores foram preservadas: duas tiveram erros de contratos na
ferramenta, corrigidos antes da comparação; a terceira mostrou que um keeper
aberto apenas com PRAGMA não retinha o pager e não era um candidato válido.
PERSIST, TRUNCATE e WAL sem keeper não produziram o ganho buscado nessa rodada.

## Garantias e limites da integração

O commit continua precedendo a confirmação do ciclo. Um cenário continua usando
`BEGIN IMMEDIATE`, a conexão emprestada e um único commit/rollback para ciclos,
topologia, eventos e recibo. Não há NORMAL/OFF, journal em memória nem resposta
antecipada à persistência. Chaves, PKI e auditoria autenticada usam seus próprios
armazenamentos; não foram substituídos.

O keeper somente é ativado quando o helper identifica positivamente um disco
local fixo no Windows e uma versão SQLite com a correção oficial do WAL-reset:
3.51.3 ou posterior, ou os backports 3.50.7/3.44.6. Dentro de uma transação de
cenário, a criação é adiada. Fora dessas condições, a otimização não é ativada e
o modo existente é preservado com FULL. Isso não converte um banco que já esteja
em WAL para DELETE nem qualifica acesso WAL por filesystem de rede.

O keeper não conserva um snapshot/transação de leitura. Checkpoints automáticos
continuam ativos. A liberação é idempotente e não reseta políticas ou registros;
leituras não reabrem o keeper e a próxima escrita pode rearmá-lo. A parada do
provider libera os dois runtimes já inicializados quando seus workers terminaram,
sem acessar properties que criariam serviços novos.

Backup de um workspace WAL deve usar a API de backup SQLite ou uma captura
consistente de todos os arquivos necessários. Copiar apenas o arquivo principal
com writers ativos não constitui backup válido. Não foi criado nesta rodada um
fluxo operacional completo de backup/restauração do workspace.

Fontes primárias: [SQLite WAL](https://www.sqlite.org/wal.html) e
[Python SQLite backup](https://docs.python.org/3.12/library/sqlite3.html#sqlite3.Connection.backup),
consultadas em 09/10/2026. FULL mantém a sincronização por commit; as garantias
reais ainda dependem do filesystem, do dispositivo e da implementação de flush.
Os testes de queda de processo não simulam perda de energia.

## Interface: comparação controlada de cadência

Os dois diagnósticos finais curtos usaram a mesma fonte, Windows/Qt nativos,
3440×1440, entrada pela sessão Windows autenticada e alvos CIMATEC→CBPF.
O único parâmetro solicitado diferente foi a cadência visual, 20 ou 8 ms.
O limite de 6 ms de trabalho cooperativo e uma criação de card por turno
continuou o mesmo. Uma operação Qt individual não pode ser interrompida pelo
orçamento; seu custo é registrado, não ocultado.

| Métrica | 20 ms | 8 ms |
| --- | ---: | ---: |
| CIMATEC, conteúdo completo | 1696,56 ms | 1660,41 ms |
| CBPF, conteúdo completo | 3226,91 ms | 1638,40 ms |
| Heartbeat p95 | 88,14 ms | 96,99 ms |
| Heartbeat p99 | 268,17 ms | 146,92 ms |
| Heartbeat máximo | 360,29 ms | 171,99 ms |
| Amostras de heartbeat acima de 250 ms | 3 | 0 |
| Pintura p95 | 24,49 ms | 23,89 ms |

Ambas verificaram todo o conteúdo solicitado, sem fechamento inesperado nem
descarte de registros diagnósticos. CBPF melhorou cerca de 49,2%; CIMATEC, 2,1%.
O p95 piorou levemente enquanto p99/máximo melhoraram. O trabalho dos cards e
o tempo entre turnos também variaram, evidenciando espera/carga/GIL: não se
isola toda a redução como efeito causal da cadência com somente uma rodada por
configuração. O heartbeat teve 204/127 amostras, respectivamente: o p99 é
descritivo e fica abaixo das mil amostras exigidas para qualificação. A proposta
foi adotada para reduzir espera deliberada, mantendo
frequência de cálculo, eventos científicos, ordem e persistência.

[Comparação e limitações](../../outputs/trl5_continuation_20261009/details-ui-comparison.json).
O teste de propriedade/parent não demonstrou ganho geral, e o limite de um
segundo para conteúdo completo ainda foi excedido. Os 160 testes finais de
componentes passaram, com [JUnit](../../outputs/trl5_continuation_20261009/details-tests-final.xml).
O benchmark comum herda a cadência de produção, sem impor 20 ms pelo driver.

A suíte ampliada do backend teve 221 casos aprovados e uma falha de admissão
anônima, preservada em
[integration-tests-v1.xml](../../outputs/trl5_continuation_20261009/integration-tests-v1.xml).
Não foi observado processamento autorizado indevido: a recusa acontecia no
worker, tarde para o contrato de admissão. O teste original falhou também em
isolamento. Após a correção, os 11 testes de entrega/seleção passaram, incluindo
revogação, mudança de escopo, fila cheia, encerramento, auditoria e recusa anônima
sem acesso ao cofre na Qt. Evidência:
[delivery-final.xml](../../outputs/orionq_trl5_anonymous_admission_20261009-delivery-final.xml).

## Resultados finais da rodada

As fontes finais, testes, ferramentas e referências foram congelados em
[freeze.json](../../outputs/trl5_continuation_20261009/run1/freeze.json).
O protocolo mantém o SHA-256
`d0972d5cd3ae513c2868ac97960d5617fda5cebc7d1d3133b8cd993afebb0690`.
As medições foram sequenciais, sem testes ou campanhas impostos em paralelo.
O host continuou sujeito a atividade externa, registrada pelo sampler.

### Runtime integrado

Mesmo ensaio do run5: 50 ciclos medidos, cinco de aquecimento, condição `idle`
(sem carga artificial imposta), orçamento de 10 ms e estado de repetidor simulado.
Não representa toda a rede. A nova medição usa o caminho de produção, sem
subclasse do profiler ou cProfile.

| Métrica | Antes, run5 | Depois, continuação/run1 |
| --- | ---: | ---: |
| Ciclo, média | 195,65 ms | 62,82 ms |
| Ciclo, p95 | 208,97 ms | 67,11 ms |
| Ciclo, máximo | 217,90 ms | 67,26 ms |
| Persistência, média | 186,89 ms | 51,53 ms |
| Erros funcionais | 0 | 0 |
| Ciclos acima de 10 ms | 50/50 | 50/50 |

Redução observada da média: **67,89%**. O perfil confirma WAL, FULL, SQLite
3.53.1, disco local fixo e keeper ativo. Sua liberação custou **84,04 ms**,
medidos fora dos ciclos. Não se oculta esse custo nem se apresenta o p99 de
50 amostras como qualificado. O limite de 10 ms continua reprovado.

Evidência: [diagnostic.json](../../outputs/trl5_continuation_20261009/run1/runtime-baseline/diagnostic.json).

### Interface completa, Qt nativo

Ryzen 7 5700X, 16 CPUs lógicas, 31,93 GiB de RAM; Windows 11 build 26200;
Python 3.12.14 e Qt/PySide 6.11.2. Monitor U34G4C, 3440×1440, DPR 1.
RTX 3060, 12.288 MiB, driver 616.64: suas amostras são contexto compartilhado
da GPU, sem atribuição ao OrionQ. Não houve mudança de linguagem nesta rodada.

Carga: 71 nodes/comunicadores, 131 enlaces, nove satélites, 36 sessões de
tecnologia, quatro demandas locais e 76 globais. Quatro fases de 15 segundos
e continuidade de três minutos. Os dois ensaios usam a mesma revisão de carga
`actual-qt-actions-v3` e o mesmo hash do plano de 122 ações.
A entrada usa a sessão Windows autenticada; não foram fornecidas credenciais
artificiais nem desativada a autenticação. Eventos Qt exercitam os widgets;
não se trata de automação de mouse no sistema operacional.

Ambos solicitaram 3440×1440; a geometria cliente efetiva anterior foi
3440×1392 e a posterior 3440×1440. A diferença de 48 pixels, a carga variável
do host e uma rodada por build limitam a atribuição causal dos resultados.

| Métrica | Antes, run5 | Depois, continuação/run1 |
| --- | ---: | ---: |
| Coleta completa, sem erros | Não, CBPF não aberto na verificação | Sim |
| Ações planejadas completadas | 122/122 | 122/122 |
| Conteúdos completos verificados | 56/57 | 57/57 |
| Detalhes completos, máximo no mapa | 3282,68 ms | 3192,46 ms |
| Detalhes completos, máximo no globo | 1970,64 ms | 1657,79 ms |
| Detalhes completos, máximo na continuidade | 2741,29 ms | 2190,55 ms |
| Heartbeat p95, mapa | 87,57 ms | 84,12 ms |
| Heartbeat p99, mapa | 185,36 ms | 301,35 ms |
| Heartbeat p95, globo | 49,11 ms | 79,56 ms |
| Heartbeat p95, abas | 22,04 ms | 47,09 ms |
| Heartbeat p95, alternância | 32,44 ms | 42,49 ms |
| Heartbeat p95, continuidade | 43,13 ms | 56,61 ms |
| Maior intervalo de heartbeat na continuidade | 924,32 ms | 851,66 ms |
| CPU média do processo UI na continuidade | 74,21% | 70,84% |
| RSS médio do processo UI na continuidade | 684,84 MiB | 682,88 MiB |

Os máximos de detalhes incluem somente conteúdos concluídos. A tentativa
anterior de CBPF expirou após 5112,22 ms e consta separadamente como erro;
não foi transformada em amostra rápida nem descartada da contabilização 56/57.

CPU segue a convenção do psutil: 100% equivale a um núcleo lógico, não à máquina
inteira. Na continuidade, CPU média do host variou de 28,07% para 15,63%.
Os detalhes melhoraram nas três fases, mas a responsividade por heartbeat não
melhorou uniformemente: globo/abas/alternância/continuidade pioraram no p95,
e o mapa excedeu 250 ms no p99 descritivo, com 557 amostras. Heartbeat não é
FPS nem latência de fótons. O maior tempo completo continua acima de um segundo.

As 57 seleções e 54 mudanças efetivas de modo terminaram sem divergências,
assim como as oito navegações de página. Os contadores observaram no máximo
um renderizador ativo ou residente, 55 criações e 54 destruições. A rede avançou
até 51 segundos simulados, com 115.030 eventos de tecnologia persistidos;
não houve atuação física. O passo científico de um segundo simulado foi
preservado, sem alteração dos cálculos ou eventos; a cadência de parede ficou
abaixo de 1 Hz. Os 51 passos ultrapassaram o período de um segundo e o último
levou 4.836,38 ms:
a persistência isolada corrigida não resolve todo o custo da simulação global.

O RSS na segunda metade da continuidade posterior apresentou inclinação
descritiva de −6,41 MiB/min, porém oscilou 5,71% em cerca de 89 segundos.
Isso não comprova estabilidade de 20 minutos ou ausência de vazamento, nem
satisfaz automaticamente o limite de oscilação de 5%. Filtros, seleções e
telemetria foram verificados dentro da carga limitada, não sob toda combinação
possível ou em uma segunda instalação.

Evidências: [report.json](../../outputs/trl5_continuation_20261009/run1/performance-baseline/report.json),
[contadores](../../outputs/trl5_continuation_20261009/run1/performance-baseline/components.csv),
[amostras de recursos](../../outputs/trl5_continuation_20261009/run1/performance-baseline/resources.jsonl)
e [comparação derivada com hashes](../../outputs/trl5_continuation_20261009/run1/comparison.json).

### Regressões da implementação

| Suíte final | Resultado | Evidência |
| --- | ---: | --- |
| Componentes Qt/detalhes/diagnóstico | 160 aprovados | [JUnit](../../outputs/trl5_continuation_20261009/details-tests-final.xml) |
| Backend crítico, transações, quedas de processo, diagnóstico e assessor | 66 aprovados | [JUnit](../../outputs/trl5_continuation_20261009/backend-tests-v1.xml) |
| Armazenamento durável e elegibilidade WAL | 26 aprovados | [JUnit](../../outputs/orionq_trl5_durable_storage_20261009-tests.xml) |
| Ferramenta de profiling | 27 aprovados | [JUnit](../../outputs/orionq_trl5_persistence_profile_20261009-tests-final.xml) |
| Autorização do console/aplicação e ciclo de vida IO | 47 aprovados | [JUnit](../../outputs/trl5_continuation_20261009/security-final.xml) |
| Admissão/entrega assíncrona após correção | 11 aprovados | [JUnit](../../outputs/orionq_trl5_anonymous_admission_20261009-delivery-final.xml) |

Há sobreposição entre suítes: os números não devem ser somados como casos
únicos. A execução ampliada anterior com uma falha foi preservada; após a
correção, foram repetidas as suítes diretamente afetadas. Ruff passou nos
arquivos de persistência, autorização, assessor, profiler e regressões alteradas.
A conferência Qt mantém exceções para E701/E702 preexistentes; não se declara
lint limpo de todo o repositório.

### Continuidade científica

As duas campanhas de referência e as duas de pipeline foram repetidas, após
as medições de desempenho, com a semente congelada `2026100900`. Cada referência
aprovou 35 verificações e cada pipeline aprovou 49. Os fingerprints científicos
recomputados coincidem entre repetições e com o run5 anterior:

| Família | Fingerprint científico SHA-256 | Resultado |
| --- | --- | --- |
| Referências | `cfa77c3043bfdf461f8627e582452cfbd5e858919b9a4e5611c22a3160893f29` | 35/35 em cada repetição |
| Pipeline | `61eb9f55300bc933d1a4cd175eeee9d82cd5d11a5be70ab59d81c13447fc2bc0` | 49/49 em cada repetição |

Cada pipeline entregou um par simulado no cenário nominal. Os cenários,
eventos e resultados normalizados continuaram equivalentes; timestamps de
parede, ambiente e caminhos não pertencem ao fingerprint científico. As cinco
famílias participam de três sessões, com fonte artificial identificada e
contrato de snapshot. Isso não cobre a rede global inteira, todos os caminhos
da API/interface ou calibração experimental. Não houve aquisição física nem
admissão operacional de chaves emuladas.

A comparação adicional aprovou 34 verificações, incluindo conferência dos
953 arquivos congelados, sem mudanças. Evidências:
[scientific-continuity.json](../../outputs/trl5_continuation_20261009/run1/scientific-continuity.json),
[reference-1](../../outputs/trl5_continuation_20261009/run1/reference-1/report.json),
[reference-2](../../outputs/trl5_continuation_20261009/run1/reference-2/report.json),
[pipeline-1](../../outputs/trl5_continuation_20261009/run1/pipeline-1/report.json) e
[pipeline-2](../../outputs/trl5_continuation_20261009/run1/pipeline-2/report.json).

### Avaliação e próximas pendências

O [assessor](../../outputs/trl5_continuation_20261009/run1/assessment.json)
terminou com código 1, correspondente à reprovação pelos critérios originais:
50/50 ciclos acima de 10 ms, heartbeat p99 do mapa acima de 250 ms e conteúdo
completo acima de 1000 ms em mapa, globo e continuidade. Não é erro da ferramenta
nem aprovação parcial de G05.

| Gate | Estado nesta rodada | Pendência |
| --- | --- | --- |
| G01 — escopo/congelamento | `not_demonstrated` | Hashes íntegros; admissão completa de funções críticas/ambiente ainda necessária |
| G02 — referências | `not_demonstrated` | Verificações analíticas parciais; estimador de segurança TF-QKD e calibração de perdas não admitidos integralmente |
| G03 — pipeline | `not_demonstrated` | Três sessões verificadas; todas as fontes/famílias e topologia/API/UI completas ainda necessárias |
| G04 — recuperação | `not_demonstrated` | Quedas e autorização ampliadas; matriz integral de falhas, saturação e ciclo de chaves pendente |
| G05 — desempenho | `fail` | Limites excedidos; cauda, duas resoluções, fonte/executável, três repetições, 100 swaps e 20 minutos ainda necessários |
| G06 — reprodução | `not_demonstrated` | Repetições no mesmo host não substituem segunda instalação isolada |
| G07 — revisão independente | `not_demonstrated` | Especialista externo identificado e conclusão autenticada; revisão interna por agentes não atende |

Prioridades para a próxima rodada: decompor os passos de vários segundos da
simulação global, reduzir operações Qt individuais de montagem/apresentação
que excedem o orçamento cooperativo e verificar estabilidade de memória em
carga prolongada. A exigência de confirmação durável deve ser preservada ao
tratar o custo restante de persistência; não reduzir FULL para obter aprovação.
Depois, executar a cobertura completa de qualificação e organizar reprodução
e admissão externa. **TRL 5 permanece não demonstrado**, com atuação física
bloqueada e modelos opcionais nos estados declarados.

## Como reproduzir esta rodada limitada

Executar a partir da raiz, no ambiente Windows descrito, sem build, profiler,
outra campanha de desempenho ou testes concorrentes. O diretório deve ser novo;
os artefatos existentes não são sobrescritos. Este exemplo reproduz o ensaio
limitado, não a qualificação completa de G05:

```powershell
$env:PYTHONPATH = 'src'
$env:QT_QPA_PLATFORM = 'windows'
$trial = 'outputs/trl5_continuation_20261009/run2'
& .audit-winvenv\Scripts\python.exe tools/assess_trl5_software.py prepare --directory $trial
& .audit-winvenv\Scripts\python.exe tools/benchmark_console_performance.py --output "$trial/performance-baseline" --duration 15 --soak 3 --size 3440x1440
& .audit-winvenv\Scripts\python.exe -m quantum_autopilot.control_plane.runtime_diagnostic --output "$trial/runtime-baseline" --samples 50 --warmup 5 --budget-ms 10 --condition idle
& .audit-winvenv\Scripts\python.exe tools/run_trl5_reference_campaign.py --output-dir "$trial/reference-1" --seed 2026100900
& .audit-winvenv\Scripts\python.exe tools/run_trl5_reference_campaign.py --output-dir "$trial/reference-2" --seed 2026100900
& .audit-winvenv\Scripts\python.exe tools/run_trl5_pipeline_campaign.py --output-dir "$trial/pipeline-1" --seed 2026100900
& .audit-winvenv\Scripts\python.exe tools/run_trl5_pipeline_campaign.py --output-dir "$trial/pipeline-2" --seed 2026100900
& .audit-winvenv\Scripts\python.exe tools/assess_trl5_software.py assess --directory $trial
```

Conferir a conclusão e os resultados de cada comando antes do seguinte; uma
falha deve ser preservada. As fontes desta rodada não foram empacotadas em um
novo executável, e os ensaios anteriores, releases e alterações existentes do
projeto foram mantidos.
