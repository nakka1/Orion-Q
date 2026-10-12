# Contrato PS/PL de aquisição e correlação do OrionQ

**Estado:** ABI OQPL implementada e validada em emulação. A fonte 5.64 acrescenta [gateway Linux externo](orionq_pl_transport.md), [perfil PetaLinux](../product/orionq_petalinux.md) e lógica RTL de referência; driver AXI DMA e bancada continuam pendentes.  
**Data:** 2026-10-10. **ABI:** `orion.lab.pspl.v1`, versão binária 1.0.  
**Implementação:** `src/quantum_autopilot/lab/pspl.py`.  
**Verificação:** `tests/test_lab_pspl.py`.

## Contexto e decisão

O núcleo deve receber eventos de um instrumento físico ou de um simulador sem mudar o algoritmo de controle conforme a origem. O PS (processador/Linux) mantém validação, sessão, configuração, autorização, persistência e protocolos. A fronteira PL (lógica programável) permite delegar correlação preservando o contrato dos registros e seus relógios. O kernel QCOR e as portas rápidas têm RTL de referência; sua integração física ainda exige uma plataforma qualificada.

Foi implementada uma fronteira pequena, independente de Qt, Windows, credenciais e endereços físicos: um formato binário versionado, anel DMA emulado com propriedade explícita e correlação determinística delegável. A origem física/simulada continua registrada para rastreabilidade e admissão de material, mas não escolhe outro algoritmo de correlação. Esse módulo não autoriza aquisição de um instrumento: a autorização e a configuração verificada continuam sendo responsabilidades do serviço/adaptador existente.

Não há acesso a `/dev/mem`, mapa de registradores, ioctl, bitstream, interrupção real ou medição de desempenho de FPGA. A decisão preserva Python no controle e os contratos atuais. Na 5.64, Verilog/SystemVerilog implementam e testam a lógica de referência delegável; a equivalência funcional não demonstra aceleração em placa. `CorrelationWorker` mantém a comparação independente antes de admitir uma implementação substituta. Consulte a [operação PS/PL](../product/orionq_pspl_linux.md).

## Referências verificadas e limites

Consultadas em 2026-10-10, prioritariamente fontes dos fabricantes e do kernel:

| Fonte | Versão/data documental | Uso e limite |
| --- | --- | --- |
| [AMD AXI DMA PG021 — Core Overview](https://docs.amd.com/r/en-US/pg021_axi_dma/Core-Overview) | 7.1, 2025-06-24 | AXI4-Lite para controle; MM2S e S2MM para transferência entre memória e AXI4-Stream. O contrato OrionQ não implementa esses barramentos. |
| [PG021 — Descriptor Management](https://docs.amd.com/r/en-US/pg021_axi_dma/Descriptor-Management) | 7.1, 2025-06-24 | Software gerencia descritores e reutiliza buffers após conclusão. Propriedade e conclusão precisam ser verificadas antes de reutilização. |
| [PG021 — Field Descriptions](https://docs.amd.com/r/en-US/pg021_axi_dma/Field-Descriptions) | 7.1, 2025-06-24 | Comprimento máximo depende de `LengthWidth`: `2^LengthWidth−1` bytes. DRE, largura, alinhamento e Micro DMA dependem da configuração do IP; nenhum valor universal foi assumido. |
| [PG021 — Cyclic DMA Mode](https://docs.amd.com/r/en-US/pg021_axi_dma/Cyclic-DMA-Mode) | 7.1, 2025-06-24 | Modo cíclico revisita descritores até parar/resetar. O anel emulado usa entrega controlada por propriedade; não declara reproduzir ciclo automático do IP. |
| [AMD PetaLinux UG1144 — Configuring and Building](https://docs.amd.com/r/2025.2-English/ug1144-petalinux-tools-reference-guide/Configuring-and-Building) | 2025.2, 2025-11-20 | Fluxo de plataforma de hardware, exportação e construção do Linux. Uma imagem OrionQ deve usar XSA/BSP, device tree e driver compatíveis com a placa escolhida. |
| [Linux — Dynamic DMA mapping guide](https://docs.kernel.org/core-api/dma-api-howto.html) | documentação corrente consultada; sem release fixada nesta URL | Alocação/mapeamento DMA, direção, sincronização de caches, tratamento de falhas e barreiras. O kernel/driver alvo ainda precisa ser fixado e qualificado. |

Memória coerente não elimina barreiras de ordenação. Um driver real deverá utilizar a API DMA do kernel, sincronizar regiões conforme a direção e não tratar ponteiros virtuais como endereços DMA. As marcas de fence deste módulo representam essas dependências logicamente; não executam cache flush nem barreiras ARM. [Guia DMA do Linux](https://docs.kernel.org/core-api/dma-api-howto.html).

## ABI de dados 1.0

O envelope abaixo é **o payload OrionQ**, não um buffer descriptor AMD. Todos os inteiros binários são little-endian, sem conversão intermediária para `float`. O timestamp é um inteiro em ps do domínio de relógio indicado; sua unidade não prova resolução, jitter, precisão ou sincronismo físicos de 1 ps.

| Offset no cabeçalho | Tipo | Campo |
| --- | --- | --- |
| 0 | 4 bytes | Magic `OQPL` |
| 4, 6 | uint16, uint16 | ABI major=1, minor=0; versão desconhecida é rejeitada |
| 8, 12 | uint32, uint32 | Comprimento do cabeçalho=64, evento=16 bytes |
| 16, 20 | uint32, uint32 | Comprimento de metadados UTF-8, quantidade de eventos |
| 24 | uint64 | Sequência do bloco na sessão |
| 32, 40 | int64, int64 | Início e duração positivos/não negativos do intervalo, em ps |
| 48, 56 | uint64, uint64 | Tick do relógio e incerteza de alinhamento, em ps |

Após o cabeçalho: JSON UTF-8 canônico dos metadados, registros de eventos e SHA-256 binário de 32 bytes do conteúdo anterior. O hash detecta corrupção; não autentica produtor nem substitui mTLS/MAC, identidade ou auditoria do sistema. Comprimentos incompatíveis, versão inválida, campos adicionais/duplicados e adulteração são rejeitados antes de entregar eventos.

Cada evento contém `timestamp_ps:int64`, `channel:int32` e `flags:uint32`, respectivamente nos offsets 0, 8 e 12. Canais com sinal permitem identificar bordas conforme o adaptador. Canal zero é reservado a marcadores de erro/perda, nunca a cliques. Flags: bit0 perda/overflow, bit1 erro. Eventos ficam ordenados e dentro de `[start_ps,start_ps+duration_ps)`.

Metadados obrigatórios: `session_id`, `clock_domain`, `configuration_id`, `origin`, `source_id`, `adapter_id`, `clock_locked`, `lost_events`, `loss_count_exact`, `quality_flags`. Campos de proveniência não são características de decisão. Bases, preparação, análise e bits **não** são inferidos dos timestamps; QKD exige o contrato de observações e os registros clássicos correspondentes.

Limites da aplicação: até 65.536 eventos/bloco, metadados até 4.096 bytes, wire até 1.052.768 bytes/bloco, orçamento do anel até 64 MiB em representação wire, slots entre 2 e 1.024 respeitando esse orçamento. Esse orçamento não é uma promessa de RSS: objetos Python e cópias têm overhead. Parâmetros elétricos, DMA masks, capacidade real de DDR e largura do IP precisam de negociação do driver de hardware; limites do software não os qualificam.

`TimestampBlock.from_capture(batch, session_id=...)` preserva eventos, sequência, origem, configuração e sinalizações do contrato `CaptureBatch`. Perdas positivas continuam sendo registros serializáveis para diagnóstico. Saturação com perda não quantificada também é conservada em `quality_flags`. Nenhum desses blocos incompletos recebe correlação aceita ou entra como operação nominal no anel.

## Propriedade, estados e recuperação

`EmulatedDMARing` usa operações protegidas por lock e slots fixos. O ciclo é:

1. `configure(DMAConfig(...), session_id=..., clock_domain=..., configuration_id=...)` valida tudo antes da configuração emulada.
2. `start()` exige estado `ready` verificado.
3. `submit(block, now_ps=...)` valida sessão, relógio, configuração, sequência, janela, qualidade e capacidade. Cria token com geração crescente, registra fence de entrega e transfere propriedade à PL.
4. `complete(token, now_ps=..., transferred_bytes=..., fence_visible=True)` publica somente a conclusão válida. Transferência parcial ou fence ausente leva a `uncertain`.
5. `acquire(now_ps=...)` entrega lease PS após fence de aquisição. Conclusão fora de ordem espera a sequência anterior.
6. `release(lease)` devolve a propriedade. Tokens antigos e liberação duplicada bloqueiam o anel.

`now_ps` é um relógio **virtual de agendamento DMA**, monotônico, injetado pelo executor; não precisa ser o relógio óptico do registro. `descriptor_deadline_ps` é testável sem dormir e não representa deadline físico garantido. A igualdade com o deadline já é atraso. Em hardware, relógio de supervisão, medição de latência, interrupções e recuperação deverão ser mapeados explicitamente.

`cancel()` interrompe novas admissões e mantém transferências anteriores draináveis. `drain()` entrega somente blocos concluídos; não termina uma transferência por conta própria. Overrun não sobrescreve o buffer antigo: registra perda conhecida dos eventos novos rejeitados e bloqueia novas operações. Desconexão, timeout, perda/erro ou relógio destravado levam a `uncertain`; `reconnect()` sozinho não restaura `ready`.

Na recuperação: drenar completados verificáveis, devolver leases PS, descartar explicitamente os dados não verificáveis com `discard_unverified()` e fazer `verify_configuration(readback, new_session_id=...)` com readback correspondente, conexão restabelecida e anel quiescente. Esse reset é emulado e nunca afirma reset físico. A sessão nova deve ser identificada pelo coordenador; recibos/jornal são necessários para deduplicação durável entre processos/restarts. O anel em memória não substitui esse journal. Há contadores de perdas/incerteza, estados por slot e histórico limitado às últimas 256 fences.

## Correlação e comandos

`cpu_correlation(block, CorrelationRequest(...))` conta **todos os pares**, inclusive empates, entre canais distintos. Usa `delta=t_b−t_a−delay_ps`, janela inclusiva `[-radius,+radius]` e bin por divisão inteira. O último bin inclui o extremo positivo. Até 65.537 bins e 10 milhões de pares por chamada; exceder orçamento rejeita o resultado parcial. Não há coincidências entre blocos nesta ABI; dividir uma aquisição pode omitir pares na fronteira. Um worker de streaming futuro precisa carregar histórico validado ou publicar guard intervals explicitamente antes de alegar equivalência de uma aquisição contínua.

`CorrelationWorker.correlate(block_wire, request)` é a interface substituível. `execute_correlation` compara integralmente a saída delegada com a referência CPU. Isso inclui custo CPU/serialização e serve à qualificação, não comprova aceleração. O resultado contém hash do bloco, sessão, sequência, domínio e parâmetros; não calcula fidelidade, acidentais, bases, chaves ou segurança QKD a partir de timestamps isolados.

`PortCommand(...).execute(mode="prepare"|"simulate")` valida e produz recibo de preparação. Operações: `route` exige `target_port`; `attenuation` exige `attenuation_db` entre 0 e 120; `phase` exige `phase_rad` entre −2π e +2π; `trigger` exige `period_ps` e `width_ps` inteiros positivos com largura menor/igual ao período. São limites conservadores da aplicação para preparação, não faixas operacionais de uma placa. `mode="hardware"` permanece bloqueado. Não há função que arme portas, lasers ou atuadores físicos neste módulo.

## Validação reproduzível e integração restante

### Processamento por nó

`src/quantum_autopilot/lab/processing_node.py` conecta os contratos acima. `ProcessingNodeConfig` identifica nó, sessão, relógio e configuração e escolhe explicitamente um worker do `ProcessingWorkerRegistry`. A fonte não escolhe worker. Há `cpu-reference` e `emulated-pl-v1`; o segundo implementa uma janela deslizante independente em Python, com as mesmas regras matemáticas. Ambos passam por comparação com a referência CPU. Nenhum worker disponível é uma FPGA física.

```python
from quantum_autopilot.lab.processing_node import ProcessingNodeConfig, NodeProcessingPipeline
from quantum_autopilot.lab.pspl import CorrelationRequest, TimestampBlock

block = TimestampBlock.from_capture(capture_batch, session_id="capture-session-1")
config = ProcessingNodeConfig(
    node_id="N01", session_id=block.session_id, clock_domain=block.clock_domain,
    configuration_id=block.configuration_id,
    correlation=CorrelationRequest(1, 2, bin_width_ps=50, radius_ps=2000),
    worker_id="emulated-pl-v1",
)
processor = NodeProcessingPipeline(config, on_record=record_sink)
receipt = processor.process(block, now_ps=0, completion_ps=1)
```

`capture_batch` e `record_sink` são fornecidos pelo serviço de aquisição/persistência. Não há criação de dados ópticos no processador. O método passa por `to_wire/from_wire → submit → complete → acquire → execute_correlation → callback opcional → release → recibo`. Uma callback SDK simulada usa `SwabianSDKAdapter(test_only_transport=True)` e `NativeStreamingSource` nos testes; esse modo bloqueia um SDK instalado e nunca qualifica a unidade física.

O recibo informa origem/adaptador, SHA-256 do bloco binário, domínio e características declaradas do relógio, worker, DMA emulada, comparação com CPU e contadores/histograma obtidos dos eventos. `metrics.observations` é qualificado como `detector-time-tags`: não representa preparações/pulsos BB84. A janela e as bordas da correlação são explícitas. Elas não substituem implicitamente a janela semiaberta de `lab.photon.analyze`; estimadores que precisem daquele critério devem negociá-lo ou converter sua definição antes da comparação.

A callback recebe um envelope `orion.lab.node-processing-record.v1` com `block_wire_hex`, seu hash e um `prepared_receipt` ainda não aceito. Ela pode conservar o registro bruto antes da publicação. Retornar da callback é `callback_acknowledged`; não comprova `fsync`, transação SQLite, assinatura ou persistência durável. A implementação do sink e a reconciliação de recibos continuam pertencendo ao serviço/journal. Sem callback, o recibo declara `not_requested`. Uma falha da callback deixa resultado incerto e não retorna sucesso; exceções brutas do SDK/sink não entram no recibo.

Um bloco ativo por nó impede filas ilimitadas. Concorrência recebe `node_processing_busy`, `retryable=True` e a instrução de conservar/repetir o mesmo bloco ainda não aceito. Não se pode descartar esse bloco e avançar silenciosamente a sequência. Cancelamento/desconexão podem ser sinalizados enquanto o worker/sink executa; a saída tardia é rejeitada. Eles não interrompem código nativo ou Python à força: antes de fechar recursos é necessário aguardar `snapshot().active=False`, usando o supervisor do serviço. Recuperação exige ausência de operação ativa, readback e sessão nova.

`tests/test_lab_processing_node.py` cobre o fluxo completo emulado e normalização por callbacks do contrato SDK nativo, substituição CPU/PL emulada, comparação independente em três sementes, origem sem bifurcação de algoritmo, persistência falha, prazo virtual, cancelamento/desconexão durante processamento, backpressure/retry, blocos vazios e recusa de duplicação. Não mede benefício de offload e não instala um driver real.

Executar no Python do núcleo, sem GUI:

```text
python -m pytest tests/test_lab_pspl.py tests/test_lab_processing_node.py -q
```

Os testes conservam uma aquisição no journal, passam pelo wire/anel e calculam correlação; comparam histogramas com enumeração independente em três sementes; verificam limites, dados acima de `2^53` ps, atraso, empates, perda/saturação, concorrência, cancelamento e readback. Uma execução prolongada de 400 blocos com consumidores concorrentes verifica ausência de duplicação e estruturas de anel/histórico limitadas. Isso não é teste de memória do driver de kernel, frequência de FPGA nem throughput de TDC.

Para qualificação física ainda faltam placa/XSA/bitstream identificados, driver DMAengine e device tree, revisão de buffers/fences/cache em ARM, IOMMU/permissões, isolamento de portas, leitura real de configurações e reset, IRQs, disciplina de relógio, medição de latência/perdas, calibração elétrica e óptica, comparação PL/CPU com dados da unidade e testes de desconexão. A interface permite desenvolver esses adaptadores sem reescrever o núcleo; não declara que eles já existem.
