# OrionQ — operação da rede mista pelo terminal

Exclusividade entre processos, vínculos de hardware e recuperação de commit
com auditoria pendente seguem o [contrato de concorrência](orionq_controller_concurrency.md).

Este guia descreve o controlador independente da interface gráfica, as fontes
de observações e o contrato de processamento PS/PL. O cenário contém N01 como
ponto de aquisição preparado para uma integração Swabian e N02–N11 como um
anel de sinais nativos SeQUeNCe. **Na evidência disponível, N01 é emulado:** não
há Time Tagger conectado e o SDK Swabian não foi executado. Atuação física
permanece bloqueada e modelos ML são opcionais.

Uma reserva do controlador separa capacidade de controle para uma rota.
Ela não representa a criação, entrega ou certificação de um par entrelaçado ou
de uma chave. O recebimento de cliques e mensagens clássicas é observável, mas
não basta para comprovar esses resultados quânticos.

## Processos, contratos e fronteiras

```text
N01: aquisição emulada ou instrumento autorizado e qualificado
       -> Acquisition/CaptureBatch -> normalização -> registros identificados
N02–N11: processo SeQUeNCe independente
       -> QuantumChannel/Detector + ClassicalChannel -> callbacks preservados
       -> normalização -> registros identificados

blocos de timestamps -> NodeProcessingPipeline -> DMA emulado -> worker
       -> correlação verificada contra referência CPU -> registro/recibo
registros e resultados admitidos -> API HTTPS/mTLS -> MixedController/SQLite
       -> política por capacidades, relógio, canais e recursos -> reserva/recibo
operador/auditor por terminal -> mesma API e autorização do backend
```

O normalizador preserva a origem em um envelope de auditoria. A política de
rota recebe IDs, capacidades e estados observados; não recebe marca do
instrumento, origem física/simulada, identidade Windows ou credenciais.
A substituição de uma fonte não concede capacidades nem transforma seus dados
em medições físicas. O hash permite vincular a observação ao registro bruto;
isoladamente, ele não autentica o equipamento ou a autoria.

| Contrato | Responsabilidade | Implementação |
|---|---|---|
| `orion.mixed-controller.configuration.v1` | Nós, capacidades, sessões, relógios, enlaces e idade máxima das observações | `control_plane/mixed_contracts.py` |
| `orion.controller.observation.v1` | Entrada comum, com sequência e tempo inteiro em ps | `ControllerObservation` |
| Envelope `{observation, provenance}` | `source_id`, `adapter_id`, `origin` e hash do registro bruto | `lab/mixed_sources.py` |
| `TimestampBlock`, ABI `orion.lab.pspl.v1` | Bloco versionado de eventos, configuração, sessão, relógio e qualidade | `lab/pspl.py` |
| `orion.lab.node-processing-receipt.v1` | Métricas produzidas pelo bloco e qualificação do transporte/worker | `lab/processing_node.py` |
| `orion.mixed-controller.state.v1` | Estado persistido, reservas e contagem das origens | `MixedController.status()` |

O contrato de observações admite `session_started`, `link_state`, `resources`,
`timetags`, `correlation`, `heartbeat` e `fault`. Uma sessão inicia na sequência
zero, com hash de configuração e verificação explícita. A sequência seguinte
deve ser consecutiva e o horizonte temporal não pode regredir. Timestamps
continuam inteiros em ps; precisão de representação não é precisão medida.
Um mesmo nome de domínio de relógio em fixtures também não comprova sincronismo
entre instrumentos reais.

`timetags` leva `{events: [{timestamp_ps, channel}], lost_events}`. São no máximo
4.096 eventos por observação e 256 registros por requisição de ingestão, ainda
sujeitos ao limite HTTP em bytes. Os canais são inteiros não nulos. Perdas são
informação válida, mas bloqueiam o estado nominal. Bases, preparação e bits
não são inferidos desses timestamps.

Uma mensagem recebida pelo `ClassicalChannel` nativo pode comprovar recepção
clássica naquele modelo. O estado quântico `up` exige evidências associadas de
emissão, recepção e callback do detector, com IDs e ordem causal consistentes.
Uma mensagem que afirma ter enviado um fóton não basta. Mesmo com essa prova,
`up` significa entrega fotônica observada; não significa entrelaçamento ou
segurança QKD. O adaptador de aquisição N01 não inventa `link_state` nem
`resources` a partir de cliques.

## Configuração e matriz dos onze componentes

A configuração congelada da primeira campanha está em
[`configuration.json`](../../outputs/mixed_controller_20261011/campaign-first/configuration.json).
Os onze nós registram `classical_control`, `time_tagging` e `correlation`,
capacidade máxima 4, sessões `mixed-session-Nxx` e relógio de fixture
`fixture-aligned-clock`. Não registram `entanglement_memory` ou `bsm` nessa
configuração. Memórias vazias existentes no simulador não habilitam
automaticamente essas operações no controlador.

Cada enlace tem dois identificadores distintos: para `L02-03`, por exemplo,
`classical-L02-03` e `quantum-L02-03`. Essa mesma regra identifica os dois
canais de **cada** enlace da tabela. Os dez enlaces do anel são nativos;
`L01-02` é a ligação cadastrada para a futura ponte de bancada e está sem
evidência suficiente de operação.

Na tabela, “slots” é o recurso reportado para admissão de controle, “tags” é
a contagem preservada no snapshot e “não registrado” se refere ao resultado
de correlação da rodada `campaign-first`, não à ausência do contrato.
Todos esses nós aparecem com `status=ready` no snapshot daquela rodada;
esse estado da sessão não garante que existam rotas ou recursos disponíveis.

| Componente | Fonte na campanha | Enlaces associados, cada um com canal clássico + quântico | Slots reportados / máximo | Tags recebidas | Participação e correlação registrada |
|---|---|---|---|---:|---|
| N01 | `EmulatedPhotonAdapter`; `origin=simulated` | `L01-02` | 0 / 4 | 267 | Entrada de aquisição; demanda até N11 bloqueada; correlação não registrada |
| N02 | Callbacks SeQUeNCe nativos; `origin=simulated` | `L01-02`, `L02-03`, `L11-02` | 4 / 4 | 101 | Reserva N02→N03 e destino N11→N02; correlação não registrada |
| N03 | Callbacks SeQUeNCe nativos; `origin=simulated` | `L02-03`, `L03-04` | 4 / 4 | 90 | Reserva N03→N04 e destino N02→N03; correlação não registrada |
| N04 | Callbacks SeQUeNCe nativos; `origin=simulated` | `L03-04`, `L04-05` | 4 / 4 | 96 | Reserva N04→N05 e destino N03→N04; correlação não registrada |
| N05 | Callbacks SeQUeNCe nativos; `origin=simulated` | `L04-05`, `L05-06` | 4 / 4 | 102 | Reserva N05→N06 e destino N04→N05; correlação não registrada |
| N06 | Callbacks SeQUeNCe nativos; `origin=simulated` | `L05-06`, `L06-07` | 4 / 4 | 107 | Reserva N06→N07 e destino N05→N06; correlação não registrada |
| N07 | Callbacks SeQUeNCe nativos; `origin=simulated` | `L06-07`, `L07-08` | 4 / 4 | 103 | Reserva N07→N08 e destino N06→N07; correlação não registrada |
| N08 | Callbacks SeQUeNCe nativos; `origin=simulated` | `L07-08`, `L08-09` | 4 / 4 | 95 | Reserva N08→N09 e destino N07→N08; correlação não registrada |
| N09 | Callbacks SeQUeNCe nativos; `origin=simulated` | `L08-09`, `L09-10` | 4 / 4 | 92 | Reserva N09→N10 e destino N08→N09; correlação não registrada |
| N10 | Callbacks SeQUeNCe nativos; `origin=simulated` | `L09-10`, `L10-11` | 4 / 4 | 94 | Reserva N10→N11 e destino N09→N10; correlação não registrada |
| N11 | Callbacks SeQUeNCe nativos; `origin=simulated` | `L10-11`, `L11-02` | 4 / 4 | 93 | Reserva N11→N02 e destino N10→N11; correlação não registrada |

O snapshot registra recepção clássica `up` nos dois extremos de cada enlace
do anel. A disponibilidade quântica pode ser `up` em um extremo e `unknown`
no outro, conforme as observações locais mais recentes; ela não é preenchida
simetricamente por conveniência. `L01-02` não tem observação operacional de
ponte e N01 não tem disponibilidade de recurso reportada.

Limites validados pela configuração: 2–256 nós, 1–1.024 enlaces, capacidade
1–65.536 por nó e `freshness_s` de 1–3.600 segundos. A campanha usa 600 segundos
para ingestão de evidência gravada. Esse valor não qualifica uma cadência de
telemetria operacional. IDs de canais não podem se repetir entre canais
clássicos e quânticos, e todos os extremos devem estar registrados.

## Reservas, bloqueios e recuperação

`mixed-demand` admite `classical_control`, `correlation` ou `entanglement`.
Cada solicitação inclui `request_id`, `source`, `target`, `operation` e
`ttl_s` inteiro de 1–3.600 segundos. A rota exige sessões verificadas,
observações recentes de recursos e recepção clássica `up` dos dois extremos.
Para correlação, os extremos precisam das capacidades correspondentes e de
domínios de relógio compatíveis. Para entrelaçamento, a admissão exige canal
quântico observado, memórias e BSM nos intermediários.

Uma decisão `reserved` retém um slot de controle em cada nó da rota e conserva
o recibo em SQLite. Ela não agenda sozinha a geração de pares, não ocupa uma
memória física, não executa um BSM e não entrega dados quânticos. A integração
futura com um protocolo executável precisa vincular seus próprios recibos de
consumo e resultado à reserva. Atualmente o recibo preserva
`quantum_result=null`, `physical_action_performed=false` e o estado informa
`certified_key_bits=0`.

Recursos indisponíveis, canais ausentes/desatualizados, perdas, falhas,
sequências interrompidas e relógios regressivos bloqueiam novas demandas ou
invalidam reservas, com causas explícitas. `heartbeat` não renova a idade dos
recursos ou dos canais. O TTL é tempo de recepção do serviço, separado do
relógio de eventos ópticos; consulta e demanda reconciliam expiração.

`request_id` vincula a solicitação durável: reapresentar exatamente a mesma
demanda recupera seu recibo e não aloca outra reserva; conteúdo diferente com
o mesmo ID é rejeitado. Registros repetidos com o mesmo `event_id` e conteúdo
são duplicatas; conflitos são rejeitados. Isso não autoriza retries cegos de
outras mutações. Após resposta perdida, consulte estado e recibos primeiro.

Há uma autoridade de processo por SQLite local. Reinício invalida reservas e
requer reverificação dos nós; encerramento invalida reservas remanescentes.
Uma configuração alterada exige novas sessões de aquisição para os nós que
já possuem eventos. Não se recomenda réplicas sobre SQLite compartilhado em
filesystem de rede. O runbook do serviço descreve drenagem e encerramento
limitados, incluindo a preservação dos recursos ainda em uso.

## Identidades e API HTTPS/mTLS

O serviço Linux usa credencial de processo explicitamente provisionada e
cofre AES-GCM com referência a segredo privado montado. Não autentica uma
sessão desktop por Windows/DPAPI. Siga o
[runbook de serviço Linux](orionq_linux_service.md) para montagem, PKI,
provisionamento offline, revogação e proteção do estado. O cliente recebe CA,
certificado e chave próprios; não recebe o cofre ou a credencial do core.

As conexões usam TLS 1.3, verificação de CA e hostname e certificado de cliente
autorizado para `dashboard`. O backend verifica ação, recurso, sessão,
revogação e prazo da chamada; certificados ou campos de função recebidos em
um payload não concedem autorização. API anônima, fallback local após erro e
conteúdo secreto em argumentos da CLI não fazem parte desse fluxo.

| Chamada de terminal | Rota | Permissão |
|---|---|---|
| `mixed-status` | `GET /api/v1/mixed-controller/status` | `network.read` no workspace |
| `mixed-configure --input ...` | `POST /api/v1/mixed-controller/configure` | `network.configure` no workspace |
| `mixed-ingest --input ...` | `POST /api/v1/mixed-controller/observations` | `ingestion.write`, conferida também para cada nó reportado |
| `mixed-demand --input ...` | `POST /api/v1/mixed-controller/demands` | `network.operate` no workspace |
| `mixed-release --input ...` | `POST /api/v1/mixed-controller/release` | `network.operate` no workspace |

Use identidades distintas para core, operador, auditor e fontes. O operador
recebe o workspace autorizado; o auditor consulta sem permissão de mutação.
Uma fonte restrita a N01 pode receber papel `operator` e escopo exato
`workspace:/var/lib/orionq/nodes/N01`. Uma requisição somente desse nó é
autorizada no escopo do nó; misturar nós exige autorização adicional no
workspace e em cada nó. Esse escopo não permite reservar ou reconfigurar a
rede inteira. O RBAC atual não tem papel exclusivo de ingestão: outras ações
de operador sobre o mesmo recurso podem ser permitidas. Não descreva esse
papel como uma ACL de ação única; avalie os privilégios da implantação.

## Execução sem interface gráfica

### Core e operador

Com `core.json`, credenciais e certificados já provisionados conforme o
runbook, execute no host Linux com o perfil headless instalado:

```bash
python -m quantum_autopilot.service.cli --config /etc/orionq/core.json check
python -m quantum_autopilot.service.cli --config /etc/orionq/core.json serve
```

O segundo comando permanece em execução; não requer login gráfico. Para
campanhas com avanço manual exclusivo, configure `clock.enabled=false` no
contrato do serviço. O relógio de rede não substitui a Timeline SeQUeNCe nem
as marcas de tempo do instrumento.

Em outro terminal, use a identidade **do cliente**. Exemplo PowerShell no
checkout com Python e dependências do produto instalados:

```powershell
$clientArgs = @('--url', 'https://orion-core:8765',
  '--ca', 'C:\OrionClient\ca.pem',
  '--cert', 'C:\OrionClient\operator.pem',
  '--key', 'C:\OrionClient\operator.key')
python -m quantum_autopilot.service.operator_cli @clientArgs health
python -m quantum_autopilot.service.operator_cli @clientArgs mixed-status
python -m quantum_autopilot.service.operator_cli @clientArgs watch --view mixed --frames 30
```

O hostname precisa estar no SAN do certificado do servidor e na allowlist
HTTP. No Linux, o mesmo cliente usa, por exemplo:

```bash
python -m quantum_autopilot.service.operator_cli \
  --url https://orion-core:8765 --ca /etc/orion-client/ca.pem \
  --cert /etc/orion-client/operator.pem --key /run/secrets/operator-key \
  watch --view mixed --interval-s 1 --frames 30
```

`watch` é leitura e admite vistas `overview`, `links`, `nodes`, `satellites`,
`reservations`, `audit`, `raw` e `mixed`, além de `--select`. Sem TTY, informe
`--frames` finito. Ele não executa protocolos nem avança a simulação por ler
a tela. O cliente não abre journal/cofre do core e não inicia um substituto
local quando a rede falha.

### Configuração, ingestão e demanda

Em um estado dedicado à campanha, a configuração integral pode ser enviada
diretamente do artefato público:

```powershell
python -m quantum_autopilot.service.operator_cli @clientArgs mixed-configure `
  --input outputs\mixed_controller_20261011\campaign-first\configuration.json
```

Não reutilize essas sessões de fixture em um workspace operacional. Para
ingestão, prepare pelo adaptador autorizado arquivos JSON com
`{"records":[...]}`, respeitando sequência e limites. O artefato
`normalized-observations.jsonl` contém uma linha por registro; **não** é um
payload JSON de requisição. Agrupe no máximo 256 registros, sem ordenar ou
reparar dados inválidos, e mantenha cada lote dentro do limite em bytes do
serviço. Uma fonte com escopo de nó envia lotes somente desse nó.

```powershell
python -m quantum_autopilot.service.operator_cli @clientArgs mixed-ingest --input n01-records.json
python -m quantum_autopilot.service.operator_cli @clientArgs mixed-status
python -m quantum_autopilot.service.operator_cli @clientArgs mixed-demand --input demand.json
python -m quantum_autopilot.service.operator_cli @clientArgs mixed-release --input release.json
```

Exemplo de `demand.json` para uma tentativa identificada:

```json
{"request_id":"operator-n02-n03-001","source":"N02","target":"N03","operation":"classical_control","ttl_s":60}
```

O backend decide `reserved` ou `blocked` com base nas observações atuais. Para
`release.json`, use `{"request_id":"operator-n02-n03-001"}`. O operador pode
usar `--output caminho-novo.json` antes do subcomando para conservar a resposta
no cliente; o arquivo é criado exclusivamente, sem sobrescrever evidências.
Não envie um caminho de exportação para escrever arquivos do servidor.

Códigos de saída do operador: 0 para resposta recebida e gravada; 2 para
entrada/identidade TLS inválida; 3 para rejeição ou indisponibilidade; 4 para
mutação com resultado incerto; 5 para resposta recebida mas saída local
indisponível. Os códigos 4 e 5 não justificam repetir automaticamente uma
mutação. Inspecione o recibo e o estado com uma chamada de leitura.

### Repetir a fonte nativa e a integração autenticada

O runner nativo exige processo independente, ambiente SeQUeNCe e checkout
verificado no commit `3133c66d80a898c6c3cc9af9eec3fa94fdbe2233`.
Use um diretório novo em cada execução. Exemplo Linux, com os caminhos do
ambiente preparado substituídos pelos caminhos locais:

```bash
/var/tmp/orionq-sequence-mixed-20261011/venv/bin/python \
  tools/run_mixed_sequence_nodes.py \
  --sequence-src /var/tmp/orionq-sequence-mixed-20261011/checkout \
  --output /var/tmp/orionq-native-new \
  --seed 2026101101 --pulses 128 --length-m 1000 \
  --attenuation-db-km 0.2 --detector-efficiency 0.75 --dark-count-hz 100
```

No ambiente do produto, separado do processo nativo:

```powershell
python tools\run_mixed_controller_campaign.py `
  --native outputs\mixed_nodes_adapter_20261011\linux-sequence-native\native-final `
  --output outputs\mixed_controller_20261011\nova-rodada
```

A campanha usa um estado temporário privado e certificados de fixture para
exercitar a API, o auditor e o operador. Não provisiona a instituição nem
substitui seu serviço de produção. Ela conserva a aquisição emulada e os
callbacks originais, verifica o hash do arquivo nativo e recusa imports do
produto ou shims na origem independente. O runner atual também conserva os
registros/recibos de processamento por nó; uma execução nova é necessária
para atribuir esses resultados à nova fonte/configuração. Não use um resultado
antigo como prova de código acrescentado depois.

## Processamento de nó e contrato PS/PL

`NodeProcessingPipeline` recebe o mesmo `TimestampBlock` para aquisição,
replay ou simulação. A configuração seleciona o worker registrado
`cpu-reference` ou `emulated-pl-v1`; a marca da fonte não seleciona o algoritmo.
O transporte implementado nesta entrega é um ring **emulado** com slots,
propriedade PS/PL, gerações e fences virtuais. Há submissão, conclusão,
aquisição, correlação, persistência opcional e liberação antes da publicação
do recibo aceito.

A correlação usa todos os pares dentro do intervalo inclusivo
`[-radius_ps,+radius_ps]`, com atraso explícito, bins em ps e orçamento máximo
de pares. O worker delegado é comparado à referência CPU antes da aceitação.
Não há correlação entre blocos nessa versão. A janela inclusiva do PS/PL não
deve ser confundida com outras janelas de análise do produto sem conferir
seus critérios. Correlação temporal não certifica pares entrelaçados, QBER,
bases de preparação ou chave segura.

O ring limita memória de payload e descritores; overrun, perda, sessão
incorreta, desconexão, configuração incerta, fence inválida, cancelamento e
deadline bloqueiam publicação nominal. Recuperação exige quiescência,
readback validado e sessão nova. Cancelamento pode marcar trabalho em curso,
mas não interrompe à força uma função nativa: o supervisor precisa aguardar
o término antes de fechar recursos. O callback de persistência deve conservar
bloco bruto/hash e confirmar seu próprio critério de durabilidade; um simples
retorno da função não prova `fsync`.

Consulte o [contrato PS/PL completo](../architecture/orionq_pspl_contract.md)
para ABI, limites, unidades, estados e fontes AMD/Linux. Não há driver PL
físico, bitstream, IRQ ou acesso `/dev/mem` implementado nesse contrato.
Deadlines do ring são injetados em tempo virtual, sem garantia de tempo real
físico. Os comandos de portas permitem preparação ou simulação e preservam
`physicalActuationLocked=true`. Não se declara aceleração ou ganho de
desempenho apenas porque o worker se chama `emulated-pl-v1`.

## Evidências existentes e seu alcance

Os caminhos abaixo identificam execuções preservadas. Não são um relatório
de qualificação de bancada nem um resultado de versões posteriores do runner.

| Evidência | Estado registrado | Alcance |
|---|---|---|
| [`campaign-first/report.json`](../../outputs/mixed_controller_20261011/campaign-first/report.json) | `PASS`, `fixture_only=true`, TLS `TLSv1.3-mTLS`, 1.959 observações | Onze nós ingeridos, dez reservas de controle nativas, ponte N01 bloqueada, mutação pelo auditor recusada e auditoria autenticada válida |
| Mesmo relatório, `missing_bridge` | `blocked`; `node_one_to_network=blocked_pending_physical_classical_bridge` | N01 não recebe conectividade fabricada por possuir cliques |
| Mesmo relatório, `quantum_admission` | `blocked`, `quantum_result=null` | Capacidades/observações insuficientes para entrelaçamento; nenhum bit certificado |
| [`linux-sequence-native/preparation/report.json`](../../outputs/mixed_nodes_adapter_20261011/linux-sequence-native/preparation/report.json) | `PASS`, UID/GID 10001, Ubuntu 24.04.5 LTS/WSL2, Python 3.12.3 | Preparação em filesystem Linux nativo; 249 hashes de origem; checkout original preservado |
| [`native-final/summary.json`](../../outputs/mixed_nodes_adapter_20261011/linux-sequence-native/native-final/summary.json) | `executed`, `origin=simulated`, SeQUeNCe 1.2.0/commit fixado | 1.280 emissões, 1.222 recepções quânticas, 973 cliques, 964 mensagens clássicas enviadas e recebidas; `entanglement_established=false`, `key_material_generated=false` |
| [`cross-platform-comparison.json`](../../outputs/mixed_nodes_adapter_20261011/linux-sequence-native/cross-platform-comparison.json) | `native-events.jsonl.identical=true` | Hash dos eventos Windows/Linux idêntico nessa semente e configuração; não estabelece equivalência universal dos ambientes |

A execução nativa usa semente 2026101101, 128 pulsos por enlace, 1.000 m,
atenuação 0,2 dB/km, eficiência de detector 0,75, dark counts 100 Hz,
intervalo entre pulsos 100.000.000 ps e atraso clássico 10.000.000 ps. São
parâmetros declarados de fixture, não parâmetros universais de uma rede.
O canal clássico nativo não modela perda nessa campanha; falhas precisam
de injeção separada, explicitamente identificada. O hash dos eventos é
`dfad376826539b6df26b328ef02868c7570efe9331a760b76ad17b3852066128`.

`campaign-first` registra `swabian_hardware_present=false` e
`swabian_sdk_executed=false`; todos os registros dessa rodada têm origem
simulada. O snapshot dessa execução não contém resultado de correlação por
nó. A implementação de processamento acrescentada posteriormente deve ser
avaliada pelos seus próprios recibos e execuções, preservando essa distinção.

## Conectar uma bancada depois de qualificada

Atualização computacional 5.63.0: a
[`campaign-pipeline-v2`](../../outputs/mixed_controller_20261011/campaign-pipeline-v2/report.json)
passou nove critérios com 1.970 observações e onze blocos processados pelo
mesmo pipeline. Métricas CPU e PL emulada coincidiram em todos os nós e os
recibos/hash de bloco foram publicados pela API autenticada. N01 continua
sem ponte clássica observada. A fonte nativa v2 preserva os mesmos contadores
e separa horizonte de observação, timestamp quantizado e instante original
do callback. Windows/Linux produziram bytes idênticos com SHA-256
`d1fc8027a8a613a5b829f44e130f0ad9d8f34a59828ed66e7a774c16ede6f109`.
Consulte o [relatório consolidado desta versão](../results/orionq_mixed_controller_20261011.md)
para a execução Linux e o monitor terminal; as evidências anteriores acima
mantêm seus escopos e hashes históricos.

O critério prospectivo é desempenho de ponta a ponta no ambiente relevante
definido, confrontado com previsões e requisitos de escala documentados.
Esse critério segue a [NASA NPR 7123.1D, Apêndice E](https://nodis3.gsfc.nasa.gov/displayDir.cfm?Internal_ID=N_PR_7123_001D_&page_name=AppendixE).
A evidência de interoperabilidade simulada desta entrega não atribui um nível
de maturidade ao sistema completo. A matriz de verificação deve vincular
requisito, procedimento, ambiente, critério de aprovação e artefato, conforme
as orientações dos [Apêndices D/E do NASA Systems Engineering Handbook](https://www.nasa.gov/reference/system-engineering-handbook-appendix/).

1. Defina a bancada alvo e congele hardware, versões do SDK/firmware,
   configuração óptica/elétrica, taxa esperada e orçamento de incertezas.
   Disponibilize SDK/licença aplicáveis e interfaces autorizadas. Replay
   continua identificado como replay; fixture não é resposta da unidade.
2. Qualifique níveis elétricos, impedância, conectores, detectores, jitter,
   perdas, calibração e sincronismo. Verifique todos os parâmetros antes da
   aplicação e o readback completo depois dela; falha parcial deixa aquisição
   bloqueada até recuperação verificada.
3. Provisione identidade própria da fonte e autoridade exclusiva do instrumento
   por série/endpoint. A normalização física exige adaptador autorizado,
   transporte SDK de hardware real, identidade/configuração atuais, calibração
   válida e `ClockReference` medido, alinhado e travado. Não mude apenas
   `origin` de um JSON para torná-lo físico.
4. Implemente e qualifique a ponte clássica N01–N02 com recibos de envio,
   recebimento, timeout e recuperação. Registre disponibilidade de recursos
   por fonte verificável; não derive slots ou link ativo de um histograma.
   A interface IP transporta dados de controle/observações: não transporta
   os fótons do enlace óptico nem os injeta fisicamente no simulador.
5. Execute o mesmo bloco de aquisição→validação→normalização→processamento→
   persistência→ingestão→admissão, incluindo desconexão, overflow, reinício,
   relógio destravado, resposta perdida e revogação. Confronte as contagens e
   correlações com registros independentes, usando critérios e tolerâncias
   congelados antes do ensaio.
6. Se a implantação usar PS/PL real, desenvolva driver Linux/DMA apropriado,
   hardware/bitstream e sincronização de cache/fences conforme a plataforma.
   Valide ownership, cancelamento, recuperação, perdas e tempos medidos nessa
   plataforma antes de declarar equivalência ou aceleração.
7. Para operações de entrelaçamento ou QKD, acrescente o protocolo e recursos
   físicos correspondentes, bases/preparação/análise, autenticação clássica,
   análise de segurança e recibos de consumo/resultado. Mantenha material
   emulado fora da admissão operacional de chaves. Interlocks e atuação física
   exigem qualificação própria e continuam bloqueados nesta entrega.

Esses passos preservam o controlador comum e substituem a origem por uma
fonte admitida, sem inventar conectividade na fronteira entre bancada e
simulador. A integração híbrida de dados não demonstra, por si só, uma rede
quântica física de onze nós.
