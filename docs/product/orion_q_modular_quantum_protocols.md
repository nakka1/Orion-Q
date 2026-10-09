# ORION-Q — tecnologias quânticas modulares

Pesquisa e implementação: 6 de outubro de 2026. Fontes consultadas nessa data. Escopo: simulador operacional, aquisição por arquivos de medições e ponto de extensão de aquisição somente para leitura. Nenhuma conexão física com as instituições, nenhum executor físico e nenhuma chave certificada são declarados.

## Estado examinado e decisão de arquitetura

O projeto recebido já possuía inventário persistente, canais clássico/quântico associados, intake de medições estatísticas BB84, controladores de referência e modelos opcionais, journal, roteamento, gestão de pares, mapa nacional e repetidores hipotéticos. Um enlace com repetidores geográficos não representava, por si, uma cadeia de troca de entrelaçamento: geração, BSM, memória, heralding e consumo de pares precisavam de contratos próprios. As telas existentes não constituíam evidência de suporte a TF-QKD ou satélites.

O pacote `quantum_protocols` acrescenta registro de adaptadores de processamento e aquisição, configurações de sessão, comandos de aquisição, observações tipadas, recursos de pares, relógio causal e recibos persistentes. `NetworkEngine` executa essas sessões no mesmo worker da rede e publica suas métricas em `LiveState.technologies`. Os enlaces antigos continuam no intake BB84. Os novos enlaces usam `quantum-operation-record-v1`, com o mesmo contrato obrigatório de associação dos dois canais.

Os recursos novos não são admitidos artificialmente no roteador ou no estoque BB84 antigos. Eles têm IDs, linhagem, capacidade, consumo e entrega próprios. Essa separação impede confundir um orçamento condicional de privacidade com chaves disponíveis. A interface lê o snapshot publicado pelo backend; não calcula resultados científicos.

## Contratos e extensão

Implementação: `src/quantum_autopilot/quantum_protocols/{contracts,registry,runtime,sources,protocols,physics,topology,satellite}.py`.

| Contrato | Conteúdo obrigatório e responsabilidade |
|---|---|
| `ProtocolConfig` / `orion.quantum-technology.v1` | ID da sessão, tecnologias, extremos, caminho sem ciclos, um enlace por segmento, parâmetros, semente, condição, habilitação e fontes. |
| `DeviceCommand` | ID correlacionado, operação, nós, IDs de ambos os canais, instante de emissão, deadline, tentativa e argumentos. Não autoriza atuação física. |
| `Acquisition` | ID do comando/sessão/operação, fonte e origem, sequência, instante observado, offset de relógio, IDs dos canais e observáveis brutos. Campos de resultados finais são rejeitados. |
| `AcquisitionSource.acquire` | Retorna observação bruta correspondente ao comando ou `None`, indicando espera. Não preenche o estado de um nó. |
| `ProtocolAdapter.validate/process` | Valida capacidades, parâmetros e topologia; transforma observações em estimativas e efeitos de protocolo. Substituição registrada executa efetivamente o novo `process`. |
| `PairResource` | ID, extremos, fidelidade estimada, criação/disponibilidade, pais, purificação e frame lógico de Pauli composto. Sem cópias recuperáveis depois do consumo. |
| `orion.technology-event.v1` | Etapa, instante causal, comando, origem, payload, hash e hash anterior. SQLite `technology_events`. |
| `orion.technologies-live.v1` | Configurações, estado, motivo, contadores, métricas, ocupação/idade das memórias, últimos eventos, registry, origem e relógio. |

Fluxo aplicável às fontes implementadas:

```mermaid
flowchart LR
  S[Simulador / arquivo / fila de leitura] --> A[Aquisição bruta]
  A --> V[Contrato, origem, sequência, associação]
  V --> T[Sincronização e deadline]
  T --> Q[Observação de transmissão pelo canal quântico]
  Q --> P[Adaptador de protocolo e recursos]
  P --> C[Mensagem no canal clássico]
  C --> R[Recebimento após propagação e jitter]
  R --> M[Métricas e recursos utilizáveis]
  M --> J[Recibos SQLite com hashes]
  J --> U[Snapshot da interface]
```

`SimulatedAcquisitionSource` emite contagens, correlações de caracterização e resultados locais. `MeasurementFileSource` lê JSONL do mesmo `Acquisition`; os comandos precisam corresponder à sessão, revisão e sequência da execução. `ReadOnlyQueueSource` permite integrar um coletor externo somente de leitura por `submit`. É uma interface de desenvolvimento, **não um driver de instrumento validado**. A fila é limitada, há limites de arquivo/mensagem, timeout e tentativas de aquisição. Operações que já consumiram estados quânticos não restauram os estados numa tentativa posterior. O canal clássico `local-envelope-v1` usa uma fila causal local com propagação e recibos modelados. Não é um driver TCP/UDP de controle distribuído. O arquivo comprova a passagem pelo intake e processamento de software, não uma transmissão clássica ou quântica física entre instituições. Um coletor físico futuro precisará fornecer observações vinculadas e implementar/validar o transporte e sua autenticação; a fila somente de leitura não satisfaz essas etapas por si só.

### Observáveis por operação

O envelope `Acquisition` usa `schema`, `command_id`, `session_id`, `operation`,
`source_id`, `origin`, `sequence`, `observed_at_s`, `clock_offset_s`,
`quantum_channel_ids`, `classical_channel_ids` e `raw`. IDs e tempos vêm do
comando solicitado; os timestamps são segundos no relógio lógico da sessão, não timestamps Unix/UTC. O coletor deve efetuar essa conversão e declarar seu offset; sequência é positiva e crescente no stream. `origin` é
`simulated`, `measurement_file` ou `physical`; esta última etiqueta não valida
um instrumento. JSON deve ser finito. Um JSONL contém um envelope por linha.

| Operação | Observações em `raw` | Processamento e validação |
|---|---|---|
| `generate` | `attempts`, `heralds`, `correlations.xx/yy/zz` com `equal`/`opposite`, `memory_writes`, `optical_transmission`, `effective_length_km` | Contagens inteiras limitadas; caracterização consome pares; escrita não excede o restante; fidelidade calculada das correlações. |
| `bsm` | `bsm_success` inteiro 0/1; `bell_outcome` 0..3 quando há herald, `null` na falha simulada | Não é uma fidelidade final. O resultado local de Bell, os pares reservados e seus frames determinam o novo recurso. Óptica linear configurada aceita ψ (2/3). |
| `purify` | `alice_outcome`, `bob_outcome` inteiros 0/1 | Paridade clássica, frames e estados de entrada determinam aceitação e rendimento BBPSSW. |
| `readout` | `readout` inteiro 0/1 | Recuperação observada, idade e parâmetros da memória determinam o recurso entregue. |
| `tf_block` | `z_trials`, `z_clicks`, `z_errors`, `x_trials`, `x_clicks`, `x_errors`; `vacuum`, `weak`, `strong`, cada um com `trials`/`clicks` | Contagens delimitadas; chamariz, sifting e orçamentos de pós-processamento. Telemetria opcional de fase e dos dois braços deve ser numérica, finita e válida. |
| `satellite_block` | `emitted_pairs`, `coincidences`, `errors`, duas entradas `geometry`, correlações opcionais | Alcance, elevação e visibilidade válidos; coincidências exigem visibilidade simultânea. Telemetria óptica/Doppler numérica. |

A vinculação de cada comando abrange os canais obrigatórios da sessão. O evento
`quantum_transmission_observed` discrimina os canais do segmento realmente
observado: um segmento na geração elementar, dois braços em TF e dois downlinks
na fonte satelital. A mensagem clássica pode disseminar heralding pelo caminho
modelado completo. IDs de vinculação não significam que todos os segmentos
transportaram fótons em uma geração elementar. Operações locais não emitem esse
evento. `final_key`, `secure_key`, `final_fidelity` e `skr_bps` não são observáveis
admitidos como substitutos de aquisição. Dados incoerentes ficam nos recibos de
rejeição, sem métricas finais ou atualização de sucesso na interface.

Exemplo de extensão em Python:

```python
registry = default_registry()
registry.register(MyTwinFieldAdapter(), replace=True)
registry.register_source(MyReadOnlyAcquisitionSource())
runtime = ProtocolRuntime(journal_path, topology_id,
                          registry=registry, source=MyReadOnlyAcquisitionSource())
```

Os cinco IDs atuais são `entanglement_swapping`, `tf_qkd`, `purification`, `satellite_doppler`, `quantum_memory`. Implementações substituíveis conservam esses contratos. Adicionar uma sexta família exige versionar a lista de tecnologias e fornecer validação, aquisição e processamento compatíveis. ETSI GS QKD 004/014/015 são referências de interface e gestão, **não implementações certificadas neste módulo**: [004](https://www.etsi.org/deliver/etsi_gs/qkd/001_099/004/01.01.01_60/gs_qkd004v010101p.pdf), [014](https://forge.etsi.org/rep/qkd/gs014-key-deliv/-/tags), [015](https://www.etsi.org/deliver/etsi_gs/QKD/001_099/015/02.01.01_60/gs_qkd015v020101p.pdf).

## Pesquisa e modelos físicos

### Repetidores e troca de entrelaçamento

A fonte gera pares elementares; perdas e detecção determinam heralds. Uma subamostra independente é consumida pela caracterização XX/YY/ZZ. Os pares restantes só entram em memórias após a confirmação clássica. BSM no nó intermediário consome dois pares; sucesso produz um novo recurso entre os extremos, com linhagem dos pais e informação de resultado de Bell. Falha consome ambos e não informa um resultado de Bell inventado. O modelo padrão de óptica linear reconhece os dois resultados ψ; sucesso sem assistência não excede 0,5 antes das perdas. O frame de Pauli é registrado depois da mensagem clássica; a fidelidade resultante usa essa referência lógica, sem executar correção física. Não há transmissão instantânea de informação pela troca de entrelaçamento.

O modelo usa estados Werner e ruído despolarizante efetivo. Para visibilidade `v=(4F−1)/3`, a troca calcula `Fout=(1+3v1v2(1−ε))/4`. A fidelidade é estimada a partir de correlações, não atribuída por instituição. O sucesso nominal de BSM é 0,5 antes de perdas de leitura/detector; esse valor corresponde ao cenário ideal de óptica linear sem assistência, não a qualquer plataforma universal. Latência clássica, espera, eficiência e vida útil da memória limitam a entrega. Referências: [Briegel et al., 1998](https://doi.org/10.1103/PhysRevLett.81.5932), [Dür et al., 1999](https://arxiv.org/abs/quant-ph/9808065).

### Memórias quânticas

Memórias mantêm estados enquanto os heralds e os outros segmentos chegam. O backend contabiliza dois qubits por par distribuído, slots por nó/sessão, escrita, leitura, idade, expiração e consumo. A visibilidade decai exponencialmente com o tempo efetivo de coerência. A recuperação pode falhar; um par não volta ao estoque depois de uma leitura destrutiva.

Não existe uma memória universal com eficiência, largura de banda e coerência simultaneamente máximas. Redes experimentais com diamante foram demonstradas em arquiteturas específicas: [pilha de entrega, 2022](https://www.nature.com/articles/s41534-022-00631-2), [nós de memória em fibra urbana, 2024](https://www.nature.com/articles/s41586-024-07252-z). Eficiência elevada de vapor quente pertence a outra plataforma: [2025](https://www.nature.com/articles/s41534-025-01092-z). Os parâmetros da demonstração são hipóteses de engenharia, sem alegar que uma dessas experiências fornece o dispositivo completo adotado.

### Purificação e destilação

Foi implementada a recorrência BBPSSW para dois estados Werner, inclusive com fidelidades diferentes. Operações locais bilaterais e comparação clássica dos resultados determinam aceitação. Dois pares são consumidos em cada tentativa; no máximo um permanece. Ruído de operação pode reduzir o ganho. O rendimento é sucessos divididos por pares consumidos, portanto não ultrapassa 1/2 por rodada. Purificação não remove a atenuação da distribuição nem garante aumento com operações defeituosas.

BBPSSW é uma forma de purificação recorrente dentro da classe mais ampla de destilação; hashing e DEJMPS não são declarados implementados. Podem substituir o adaptador mediante contratos e validação próprios. Referências: [Bennett et al., 1996](https://arxiv.org/abs/quant-ph/9511027), [Deutsch et al., 1996](https://arxiv.org/abs/quant-ph/9604039), [estados mistos e códigos, 1996](https://journals.aps.org/pra/abstract/10.1103/PhysRevA.54.3824).

### TF-QKD

Foi escolhido um modelo operacional reduzido de SNS-TF: dois emissores independentes preparam escolhas enviar/não enviar no bloco Z; estados coerentes de referência no bloco X interferem em Charlie; provas de vácuo e duas intensidades permitem uma estimativa chamariz. A transmissão de cada braço deriva do comprimento efetivo da fibra, acoplamento, detector e janela temporal. Desbalanceamento dos braços, ruído de fase, polarização e dark counts alteram contagens, QBER e orçamento de privacidade.

TF-QKD é relacionado à família MDI pelo detector central não confiável, mas não é equivalente ao MDI convencional de coincidência de dois fótons. Escalamento favorável com raiz da transmissão em certas arquiteturas não constitui violação de causalidade ou capacidade: a rede e seu corte são diferentes do canal direto sem repetidor. [Proposta TF, 2018](https://www.nature.com/articles/s41586-018-0066-6), [SNS, 2018](https://arxiv.org/abs/1805.09222), [prova de segurança TF, 2019](https://www.nature.com/articles/s41534-019-0175-6), [limite PLOB, 2017](https://arxiv.org/abs/1510.08863).

Sifting, estimação chamariz, contabilização da informação revelada na correção, orçamento de verificação e orçamento de amplificação de privacidade são processados a partir das contagens. **Não são geradas chaves finais.** O erro X observado é utilizado como hipótese substituta, sem uma prova do limite de erro de fase Z de SNS para este simulador. Não há reconciliação de bitstrings, autenticação clássica ou prova componível de tamanho finito implementadas. Por isso `certified_secret_bits=0`; a taxa exibida é uma **estimativa condicional não certificada**, mesmo quando positiva. Registros de longas distâncias dependem de dispositivos e condições particulares: [experimento SNS 300 km, 2019](https://arxiv.org/abs/1902.06268), [TF 833,8 km](https://www.nature.com/articles/s41566-021-00928-2). Não foram convertidos em garantias de alcance.

### Satélite e Doppler

O cenário usa uma fonte de pares num satélite explícito e dois terminais ópticos terrestres hipotéticos. Uma única órbita circular Kepleriana, rotação terrestre e posições das duas estações determinam alcance, elevação e velocidade radial. Só há coincidências quando ambas têm visibilidade e não há bloqueio atmosférico configurado. A inclinação declarada deve alcançar a latitude da estação de referência; configurações incompatíveis são recusadas antes de persistir, sem alterar automaticamente a órbita. Órbitas equatoriais e retrógradas válidas também são aceitas. Apontamento, abertura receptora, divergência Gaussiana, atenuação atmosférica por massa de ar, acoplamento, detectores, ruído e coincidências acidentais alteram a taxa. O tempo de voo e a latência clássica participam do recebimento.

Doppler é calculado da velocidade radial e do comprimento de onda. Compensação reduz o deslocamento residual perante o filtro espectral; o simulador permite comparar habilitação e desabilitação. Ele não atribui aumento obrigatório de fidelidade a uma correção Doppler. Óptica adaptativa pode corrigir frente de onda/turbulência em cenários específicos; não foi implementada nem substituída por um ganho arbitrário. As arquiteturas de uplink, downlink, distribuição de entrelaçamento e relay confiável de chaves têm requisitos e modelos de confiança diferentes.

Referências experimentais: [distribuição de entrelaçamento em 1.200 km, 2017](https://arxiv.org/abs/1707.01339), [QKD satelital, 2017](https://arxiv.org/abs/1707.00542), [transferência de tempo, 2020](https://www.nature.com/articles/s41567-020-0892-y), [sincronização, 2021](https://opg.optica.org/ao/abstract.cfm?uri=ao-60-16-4787). [Revisão de arquiteturas, 2023](https://www.nature.com/articles/s42005-022-01123-7). A proposta de compensação em time-bin não é hardware validado deste projeto: [modelo, 2019](https://arxiv.org/abs/1908.09018).

Limitações: modelo esférico, órbita hipotética, sem TLE/SGP4, efemérides medidas, refração, terreno, meteorologia real ou óptica adaptativa. Um único LEO de 500 km não oferece disponibilidade contínua ou cobertura simultânea intercontinental arbitrária. Os casos US e Atlântico podem permanecer indisponíveis; isso é resultado físico do cenário, não um indicador decorativo. A contabilidade BBM92 do cenário satelital também é assintótica e condicional, sem certificação de chave.

## Parâmetros de demonstração e hipóteses

| Parâmetro nominal | Valor / âmbito | Base e limite |
|---|---|---|
| Atenuação de fibra | 0,2 dB/km | Hipótese de fibra telecom; não caracteriza o cabo da instituição. [Corning](https://www.corning.com/content/dam/corning/media/worldwide/coc/documents/Fiber/product-information-sheets/PI-1424-AEN.pdf). |
| Comprimento efetivo | distância WGS84 × 1,2 | Fator de rota hipotético. Não é traçado ou levantamento de fibra. |
| Perdas de inserção | 2 dB | Hipótese de engenharia; configurável. |
| Detector | eficiência 0,8; probabilidade escura 10⁻⁷/tentativa; jitter 50 ps | Não são uma ficha técnica única. [Detector experimental, 2013](https://www.nature.com/articles/nphoton.2013.13) informa contexto, não valida esta combinação. |
| Fonte de pares | 250 k tentativas/s; emissão 0,1 | Hipótese operacional da demonstração; blocos de 0,5 s, CN de 1 s. |
| Memória | 8 slots/nó/sessão; coerência efetiva de par 1 s; escrita/leitura 0,7 | Hipótese; capacidade entre sessões não compartilhada neste modelo. |
| Operações | BSM nominal 0,5; erro 0,005; purificação 0,01 | Perdas de memória e detector multiplicam o sucesso de BSM. |
| TF | μ=0,4; ν=0,1; enviar=0,08; fase σ=0,05 rad | Modelo SNS reduzido, não parâmetros ótimos ou universais. Ontario e IBM usam 10 MHz. |
| Clássico | propagação efetiva de fibra c/1,468; latência adicional 1 ms; jitter até 10 μs | Simulador local de mensagens, sem conexão a rede IP ou confirmação externa. |
| Satélite | altitude 500 km; inclinação 60°; λ=850 nm; fonte 5 MHz | Satélite hipotético, não órbita da missão Micius. |
| Óptica satelital | abertura 1 m; divergência 10 μrad; pointing 2 μrad; profundidade óptica zenital 0,2 | Hipóteses explícitas, não especificações de um terminal real. |
| Filtro/Doppler | σ=1 GHz; residual compensado 5 MHz | Hipótese para comparação; compensação não é controle físico. |
| Degradação | +12 dB; fase e jitter ×8 | Cenário de estresse definido pelo projeto. |
| Recuperação | 0–10 s nominal; 10–20 s indisponível; 20–30 s nominal; periódico | Reproduzível, não disponibilidade medida. |

A semente nominal é 42; o simulador usa streams estáveis por sessão e configuração. A aceleração é explicitada como segundos simulados por segundo da rede; o processamento mantém timestamps e ordem causal. O worker tem orçamento de eventos e não envelhece recursos além de eventos ainda pendentes. Taxas são estimativas da execução, não promessas de capacidade dos locais.

## Topologia, localização e matriz

A migração é versionada, transacional e executada uma vez. Registra o estado anterior e as demandas antes da remoção. Remove UNIJORGE e seus enlaces incidentes, aposenta sua demanda local e muda a origem da demanda nacional para CIMATEC. Preserva os outros nós, enlaces, isolamento, calibração e permissão ML. Identificadores novos em conflito geram recusa em vez de sobrescrita. O builder nacional histórico conserva a fixture anterior; o workspace e a demonstração atuais usam a migração ampliada.

`quantum_protocols/data/sites_v1.json` contém nomes, endereços, coordenadas, fonte oficial, fonte geográfica, precisão e data. Google usa referência aproximada próxima à via de acesso identificada pela prefeitura, sem footprint inventado; Xanadu usa sede executiva documentada em 2026, sem alegar que ali está o terminal fotônico; IQMP é infraestrutura em desenvolvimento e a posição é referência da parcela South Works. China tem quatro hubs: Pequim, Jinan, Hefei e Xangai; referências municipais/campus não são coordenadas de terminais publicados.

| Sessão / conexão proposta | Componentes e tecnologias | Justificativa e limite |
|---|---|---|
| RIO-ENTANGLEMENT | PUC-Rio → IME militar → CBPF → UFRJ; troca + memória + purificação | Escala metropolitana, segmentos curtos, BSM e LOCC representados; equipamento hipotético. |
| RIO-TF | PUC-Rio / UFRJ → estação central TF explícita | Dois emissores e interferência central; fase e pós-processamento observáveis. |
| ONTARIO-TF | IQC / Xanadu → estação central TF explícita | Escala regional; referência de sede não prova fibra ou equipamento instalado. |
| IBM-IQC-TF | Watson / IQC → estação central TF explícita | Enlace regional mais longo; perdas podem impedir orçamento positivo. |
| US-REPEATER-EXPERIMENT | IQMP → seis estações intermediárias → Watson | Troca + memória + purificação experimental; longa distância pode inviabilizar entrega. |
| CN-BEIJING-JINAN | Pequim → três repetidores hipotéticos → Jinan | Overlay experimental com as três tecnologias de entrelaçamento. |
| CN-JINAN-HEFEI | Jinan → três repetidores hipotéticos → Hefei | Mesmo overlay; perdas e memória limitam recursos. |
| CN-HEFEI-SHANGHAI | Hefei → três repetidores hipotéticos → Xangai | Mesmo overlay; não representa hardware real do backbone. |
| RIO-SATELLITE | CBPF / UFRJ ← satélite explícito | Fonte comum, coincidências, visibilidade, Doppler e métricas derivadas. |
| EUROPE-SATELLITE | BSC / Microsoft Lyngby ← satélite explícito | Cobertura simultânea depende da passagem; pode abrir/fechar janelas. |
| US-SATELLITE | Google Goleta / IQMP ← satélite explícito | Geometria LEO pode impedir coincidência entre estações distantes. |
| ATLANTIC-SATELLITE | UFRJ / BSC ← satélite explícito | Caso de limitação intercontinental com LEO de 500 km; sem sucesso artificial. |

O backbone chinês documentado usa enlaces QKD e **relays confiáveis**: [Chen et al., 2021, rede integrada de 4.600 km](https://www.nature.com/articles/s41586-020-03093-8). As três sessões de entrelaçamento são overlays propostos para simulação, não uma reinterpretação do backbone como rede real de repetidores quânticos. A [matriz detalhada](../results/orion_q_modular_connection_matrix_20261006.md) inclui cada um dos 36 segmentos, distância e hipótese; conexões diretas nacionais longas continuam como exemplos de limitação.

## Operação e leitura da interface

Na demonstração institucional, abra **Topology → PROTOCOLOS**. A tabela acompanha estado, fonte, BSM, rendimento, memória e taxas. A coluna `Pares g/d` usa taxas por segundo: pares elementares heraldados (coincidências recebidas no caso satelital) e pares entregues entre extremos. Não representa o total de fótons emitidos nem chaves disponíveis. Selecione uma sessão para editar parâmetros JSON, habilitação, condição e semente. **APLICAR** envia um comando ao backend; o readback confirma a configuração. **CAPTURAR EVIDÊNCIA** fixa os detalhes para leitura sem alteração a cada tick; **EXPORTAR RECIBOS** exporta o journal. **ARQUIVO JSONL**, **FONTE SIMULADA** e **SEM FONTE** substituem somente o adaptador de aquisição e invalidam recursos anteriores.

O mapa global é offline, com Natural Earth e geodados OSM quando disponíveis. A seleção dos novos locais mostra o footprint cadastrado ou uma referência explicitamente aproximada. Não se inventam edifícios. A projeção do mapa serve à navegação; distâncias do backend usam WGS84. Estações intermediárias são posições hipotéticas, não instalações verificadas ou rotas terrestres levantadas.

Execução reproduzível no Windows:

```powershell
$env:PYTHONPATH = Join-Path (Get-Location) 'src'
.audit-winvenv/Scripts/python.exe tools/run_modular_quantum_demo.py --seconds 100 --seed 42 --output outputs/modular-demo
$env:QT_QPA_PLATFORM = 'offscreen'
.audit-winvenv/Scripts/python.exe -m pytest tests/test_modular_quantum_protocols.py tests/test_console_modular_quantum.py tests/test_modular_runtime_validation.py
```

Para medições, produza registros `Acquisition.to_dict()` compatíveis com os comandos `acquisition_requested` do journal. Alterar apenas `origin`/`source_id` de dados simulados serve a um **teste de replay**, não transforma o arquivo em evidência física. O relatório diferencia essas origens. Não carregue material de chave real neste simulador: a aquisição e os recibos não constituem um serviço seguro de gestão de segredos.

## Dependências e limites restantes

Os terminais fotônicos propostos são externos às plataformas de computação das instituições. Não foi implementada interface com processadores supercondutores, topológicos ou conversão micro-ondas/óptica; essas capacidades não decorrem do cadastro do local. A geração é agregada em blocos, com tempo de voo aplicado ao fechamento da aquisição; não há simulação individual de cada modo de campo ou fóton. A administração da rede ao vivo admite até 256 nós; o editor histórico isolado mantém seu limite anterior.

Os cinco módulos possuem operações observáveis no backend e cenários reproduzíveis. Há integração de arquivos, registro de fontes/adaptadores, comunicação clássica modelada e recursos causais. Não há drivers validados de laser, detector, memória, terminal satelital ou sockets de equipamentos. Continua indisponível atuação física. Memórias de sessões independentes não compartilham um pool físico global; estados Werner e estatísticas agregadas não substituem um simulador de circuito universal. Purificação implementa BBPSSW; outros protocolos demandam adaptadores. QKD calcula orçamentos condicionais, sem geração de chave, reconciliação, autenticação ou prova componível de tamanho finito. Satélites usam efemérides hipotéticas, sem compensador ou rastreador físicos. Conexões inviáveis continuam indisponíveis e não foram excluídas silenciosamente.

O autoteste `ORION-Q.exe --self-test caminho\report.json` executa também o diagnóstico modular de 100 segundos simulados e salva `modular-quantum/report.json` e recibos. Esse diagnóstico usa um estado isolado e replay sintético explícito; não acessa dispositivos ou apresenta arquivos simulados como medições físicas.
