# ORION-Q 5.39 — validação das tecnologias modulares

6 de outubro de 2026, Windows, Python 3.12, PySide6 6.11.2. A implementação acrescenta cinco famílias operacionais, 12 sessões, 14 referências institucionais/rede e 36 segmentos; a demonstração atual contém 66 nós e 71 enlaces. A UNIJORGE e seu enlace incidente foram retirados da demonstração atual; as demais referências e conexões anteriores permanecem. O IME militar foi acrescentado e o IME-USP permanece.

Os resultados são de aquisições simuladas e replay sintético declarado, com integração ao backend. Não comprovam conexão com instituições, transmissão física de qubits, drivers de instrumentos ou segurança de chaves. Nenhuma atuação física foi habilitada; os modelos continuam opcionais.

Os [contratos, pesquisa e hipóteses](../product/orion_q_modular_quantum_protocols.md), a [matriz com os 36 segmentos](orion_q_modular_connection_matrix_20261006.md) e as [localizações e fontes](orion_q_modular_locations_20261006.md) descrevem a entrega. Artefatos reproduzíveis: `outputs/modular_quantum_20261006_v1/`.

## Casos de uso verificados

APROVADO indica a satisfação do critério declarado para a implementação de simulação. Indisponibilidade física prevista constitui um resultado válido; não é convertida em sucesso.

| Caso | Ação / condição | Resultado observado e critério | Evidência | Resultado |
|---|---|---|---|---|
| U01 | Abrir demonstração ampliada | 66 nós, 71 enlaces, 12 sessões; cinco adaptadores registrados | `test_verified_topology_and_explicit_experimental_china` | APROVADO |
| U02 | Retirar UNIJORGE | Sem nó/enlace incidente; sítios e enlaces restantes preservados | Topologia e teste U01 | APROVADO |
| U03 | Distinguir os dois IME | IME-USP e IME militar em referências separadas | Catálogo geográfico e U01 | APROVADO |
| U04 | Representar a rede chinesa | Quatro referências e três macrotrajetos; overlay experimental distinguido dos relays confiáveis documentados | Catálogo, matriz e U01 | APROVADO |
| U05 | Gerar pares elementares | Atenuação, detector, geração probabilística e caracterização destrutiva determinam recursos | `test_all_five_have_operational_events_and_causal_receipts` | APROVADO |
| U06 | Executar troca de entrelaçamento | BSM consome dois recursos; linhagem preservada; entrega entre extremos | U05 e campanha de 100 s | APROVADO |
| U07 | Falhar BSM | Entradas consumidas; ausência de resultado de Bell inventado e sem restauração | Diagnóstico modular, `failed_bsm_consumes_inputs` | APROVADO |
| U08 | Receber resultado de Bell | Frame de Pauli composto, registrado após recebimento clássico; sem correção física | `test_bell_frames_compose_and_purification_compares_correct_parity` e diagnóstico | APROVADO |
| U09 | Purificar pares | BBPSSW com fidelidades distintas; dois pares por tentativa; rendimento ≤ 1/2 | Física de referência e U05 | APROVADO |
| U10 | Armazenar e recuperar | Capacidade, eficiência, idade, decoerência e expiração modificam recursos | U05, diagnóstico e física de referência | APROVADO |
| U11 | Executar SNS-TF reduzido | Contagens Z/X e chamariz alimentam sifting e orçamentos de pós-processamento | U05 e `RIO-TF` | APROVADO |
| U12 | Inspecionar resultado QKD | Orçamento condicional distinguido de chave certificada; saída certificada zero | U05 e recibos das sessões QKD | APROVADO |
| U13 | Simular satélite | Fonte comum, duas estações, coincidências, perdas e velocidade radial | `test_satellite_geometry_availability_and_doppler` | APROVADO |
| U14 | Comparar compensação Doppler | Deslocamento residual modifica transmissão pelo filtro | U13 e física óptica | APROVADO |
| U15 | Solicitar enlace Atlântico LEO | Sem visibilidade simultânea: zero coincidências; indisponibilidade realista | U13 e `ATLANTIC-SATELLITE` | APROVADO |
| U16 | Conferir causalidade | Validação e sincronização precedem processamento; métricas após chegada clássica | U05 e diagnóstico modular | APROVADO |
| U17 | Desabilitar canal quântico | Sem observações utilizáveis ou resultados de comunicação | `test_each_channel_is_obligatory[quantum]` | APROVADO |
| U18 | Desabilitar canal clássico | Sem métricas de comunicação utilizável | `test_each_channel_is_obligatory[classical]` | APROVADO |
| U19 | Trocar simulador por arquivo | Mesmos eventos, contadores e estimativas para observações equivalentes, em TF e entrelaçamento | `test_identical_pipeline_for_file_and_simulator` | APROVADO |
| U20 | Remover fonte | Timeout/tentativas; nenhum QBER ou sucesso final inventado | `test_no_source_has_no_success_or_final_metrics` | APROVADO |
| U21 | Mudar fonte ativa | Descarta recursos e contadores da origem anterior; mantém histórico auditável | `test_source_replacement_drops_previous_source_rates_and_resources` | APROVADO |
| U22 | Degradar, interromper e recuperar | Transmissão reduzida em mais de dez vezes; indisponibilidade e recuperação após novas aquisições | `test_loss_and_recovery_change_results` | APROVADO |
| U23 | Substituir adaptador | Implementação registrada recebe efetivamente `process` | `test_adapter_replacement_executes_and_missing_capability_rejects` | APROVADO |
| U24 | Remover capacidade necessária | Sessão fica incompatível; não é silenciosamente promovida por novo tick | U23 | APROVADO |
| U25 | Injetar resultado final ou parâmetro inválido | Rejeição antes de persistir; inclui valores booleanos usados como números | Contratos e parâmetros inválidos | APROVADO |
| U26 | Editar canal de sessão modular | Associação e metadados preservados; downgrade silencioso para BB84 rejeitado | `test_link_editor_preserves_modular_binding_and_rejects_protocol_downgrade` | APROVADO |
| U27 | Recalibrar nó modular | Recursos e métricas invalidados; novas observações necessárias | `test_memory_reset_does_not_fabricate_observations` | APROVADO |
| U28 | Migrar/reabrir inventário | Migração idempotente; sem sobrescrever edições posteriores; topologia/retirada de demandas auditadas | Migração e regressões institucionais | APROVADO |
| U29 | Verificar recibos | Cadeia de hashes válida; origem e canais identificados; sem atuação física | U05 e diagnóstico modular | APROVADO |
| U30 | Navegar mapa global/campus | Mapa offline, footprint quando verificável; Google identificado como referência aproximada | `test_world_and_verifiable_campus_detail` | APROVADO |
| U31 | Aplicar parâmetros na interface | Comando chega ao backend e readback confirma configuração | `test_backend_readback_and_capture_remain_readable` | APROVADO |
| U32 | Ler evidência durante ticks | Captura e posição do scroll permanecem; rascunho não é sobrescrito | U31 | APROVADO |
| U33 | Redimensionar controles | Verificação Qt em 640×480, 1280×800 e 3440×1440; controles presentes | `test_dialog_responsive_controls` | APROVADO |
| U34 | Confirmar/cancelar ML | Cancelamento não altera estado; ativação/desativação confirmadas no backend e recibos | `native-widget-check/report.json`, 33 verificações | APROVADO |
| U35 | Abreviar identificador longo | Elisão no desenho, conteúdo acessível preservado e sizeHint estável ao redimensionar | `test_long_identifiers_remain_available_in_bounded_labels` | APROVADO |
| U36 | Autoteste das cinco famílias | 100 s simulados, 12 verificações, entrega, falha, memória, QKD, Doppler, causalidade e replay | `native-protocols.xml`, diagnóstico compartilhado com `.exe` | APROVADO no código-fonte e no `.exe` |
| U37 | Inspecionar enlace pelo node | Canais associados visíveis e métricas da família correta; orçamento condicional separado de bits certificados | `test_node_connection_inspection_has_paired_channels_and_protocol_metrics`, `node-inspection.xml` | APROVADO |
| U38 | Receber telemetria malformada | Fase, transmissões, comprimentos e contagens inválidos recusados antes de atingir os formatadores Qt | `test_generation_rejects_invalid_telemetry`, `test_tf_rejects_invalid_telemetry` | APROVADO |
| U39 | Configurar órbita sem acesso à estação | Configuração e geometria recusadas; inclinação não é aumentada automaticamente | Testes orbitais e persistência de configuração | APROVADO |
| U40 | Configurar órbita equatorial/retrógrada válida | Geometria consistente e sem divisão por zero; estação equatorial tem referência de 500 km e 90° | `test_valid_equatorial_and_retrograde_orbits` | APROVADO |

## Campanhas e interpretação

A campanha nominal mais recente, `campaign-delivery-final/`, usa seed 42 e 100 segundos simulados. `RIO-ENTANGLEMENT` gerou 2.152.503 pares elementares antes das perdas de armazenamento e consumo, executou 344 BSM com 67 sucessos, aceitou 856 de 966 tentativas de purificação e entregou dois pares entre os extremos. Caracterização consumiu 476.148 pares, e houve 229 expirações de memória. Esses contadores têm significados diferentes: pares gerados não são pares entregues ou capacidade disponível.

No último bloco, `RIO-TF` estimou 4.110 bit/s condicionais e `ONTARIO-TF` 3.588 bit/s condicionais. Todas as sessões conservam `certified_secret_bits=0`. O satélite do Rio registrou 45 coincidências no bloco final de 2 s; Doppler variou com a órbita. O teste orbital confirma que a estação sob o satélite na referência inicial fica abaixo do horizonte após meia órbita, evitando uma disponibilidade contínua fictícia. Os casos US e Atlântico terminaram indisponíveis. Sessões terrestres longas também podem processar aquisições sem entregar pares: estado da execução do protocolo não garante serviço entre extremos.

As campanhas de degradação e recuperação de 40 s estão preservadas em `campaign-degraded/` e `campaign-recovery/`. Têm durações diferentes da nominal; totais brutos não constituem comparação estatística controlada. O teste U22 compara a alteração de transmissão e os estados sob configuração explícita. Recuperação exige novas observações e inclui erros de indisponibilidade previstos, retries e recibos.

## Distribuição Windows e regressões

A consolidação dos XMLs até o congelamento apresenta 3.282 casos distintos aprovados, nenhum caso com falha pendente e um teste não executado: compilação/emulação real de LabOne Q 26.7.0, cujo ambiente isolado não está instalado. Os três testes do detector inicialmente pulados passaram ao informar o ZIP original por `ORION_QKD_ARCHIVE`.

A suíte modular de interface passou em 30 casos antes do endurecimento da aquisição; o grupo final de protocolos, contratos, órbitas e diagnóstico passou em 44 casos, incluindo 20 casos de validação de observações/configuração. Os seis casos de interface modular também passaram. O diagnóstico compartilhado com o executável passou nas 12 verificações no código-fonte. As regressões específicas de layout/composição das janelas dos nodes passaram em 24 casos, e a integração da inspeção modular e enlaces passou em 32 casos. Esses grupos se sobrepõem; os números não são somados como casos distintos.

Distribuição final: `release/windows/5.39.0-native-20261006-134257`. O executável foi lido diretamente como arquivo PyInstaller para confirmar a presença da observação por segmento, da validação de telemetria e da recusa de inclinação orbital incompatível. `modular-build-provenance.json` registra fingerprints do código e do bytecode desta distribuição.

| Gate do executável final | Resultado | Relatório no diretório da distribuição |
|---|---|---|
| Autoteste geral | APROVADO, 34 verificações principais | `self-test.json` |
| Cinco tecnologias operacionais | APROVADO, 12 verificações e 100 s simulados | `modular-quantum/report.json` + recibos |
| Modelos sem pacotes externos | APROVADO | `optional-models-validation/optional-models-report.json` |
| ZIPs originais revisados | APROVADO, 11 modelos e retenção no backend | `optional-models-original/optional-models-report.json` |
| Comparações e diagnóstico isolado do roadmap | APROVADO funcional | `roadmap-validation/report.json` |
| Worker congelado, recibos, replay/cancelamento e histórico | APROVADO | `campaign-validation/report.json` |
| Estresse de controlador simulado | APROVADO, 20 rodadas | `stress-validation/**/report.json` |
| Abertura nativa e redimensionamento | APROVADO, serviço em 100/125/150/200% e demo em 100/150% de escala | `startup-validation/report.json` |
| ZIP portátil | APROVADO, 3905 arquivos, CRC e SHA-256 | `portable-package.json` |
| Aceitação automática Windows | APROVADO nesta máquina | `native-acceptance/acceptance-report.json` |

SHA-256 do executável: `e005ae4b82d9b572f1ce45c19b743f1fbcb6b9bee573d64270ffd9e9da39a356`.

SHA-256 do ZIP: `a82fdcbcab79412a5028f918720ec79def96adf1c55e8609c3d6324354bfa288`.

O diagnóstico de runtime teve 20 amostras e todas excederam o orçamento experimental de 10 ms (mediana 158,36 ms; p95 172,85 ms). A aprovação funcional não comprova atendimento a esse prazo nem permite conclusão de controle físico em tempo real. A abertura do serviço levou aproximadamente 5,70–5,95 s até o primeiro snapshot, e a demonstração 8,47–10,14 s. Os heartbeats de UI tiveram p95 de 32 ms no serviço e 47 ms na demonstração; houve um pico transitório de 2,937 s na abertura da demo a 100%, sem congelamento persistente. Esses números são medições locais do diagnóstico, não garantias em outros computadores.

XMLs das tentativas anteriores estão preservados. Cinco expectativas antigas falharam nos grupos maiores e foram corrigidas e repetidas com aprovação: elisão de texto, limites/topologia anteriores e inversão de extremos sem atualizar o receptor do canal. A fixture adicional do limite foi corrigida para usar a revisão atual. Relatórios antigos não aprovam o executável final por si: seus fingerprints e os diagnósticos finais identificam os artefatos usados.

A versão anterior `5.38.0-native-20261006-094554` permanece intacta (executável SHA-256 `0e75a83de3f03c2490a3f7337db0e62c222cd0f763dae34ea1c71fcdf3430874`). O manifesto inicial registrou 1.064 arquivos recebidos; nenhum foi apagado. Alterações anteriores não foram descartadas e o journal do operador não foi aberto para testes.

## Dependências e limites objetivos

Drivers e instrumentação físicos não estão disponíveis/validados. A fila de aquisição somente de leitura é um contrato de extensão, sem autenticação de fonte ou executor de equipamento. O transporte clássico desta versão é uma fila causal local; não constitui comunicação TCP/UDP ou enlace físico entre instalações. Arquivos precisam corresponder aos comandos, revisão e tempos da sessão; não há remapeamento automático de medições históricas arbitrárias.

SNS-TF e BBM92 usam estatísticas agregadas e orçamentos assintóticos condicionais. Não implementam reconciliação de bitstrings, autenticação clássica, produção de chave ou prova componível de tamanho finito. Memórias pertencem a cada sessão, sem arbitragem de pool físico global. Purificação implementa BBPSSW; DEJMPS/hashing não são reivindicados.

Órbitas e terminais são hipotéticos, sem TLE/SGP4, meteorologia medida, rastreamento físico ou óptica adaptativa. Coordenadas de instituições não comprovam equipamentos ópticos; intermediários não são rotas de cabo levantadas. A referência Google e os pontos municipais chineses têm aproximações explicitadas. Não há transdutores para processadores de computação quântica dessas instituições.

A interface foi exercitada por eventos Qt e snapshots reais do backend simulado. Isso não é operação manual do mouse no desktop nem aceitação em uma segunda máquina. O roadmap conserva separadas essas dependências e a validação científica de campo.
