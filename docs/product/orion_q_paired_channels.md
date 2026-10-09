# Canais associados e replay de aquisições

Implementação local de 06/10/2026. Cada enlace novo tem um par de canais com IDs
distintos, associado ao ID do enlace e aos mesmos endpoints. A configuração pode
ser conferida e editada em **TOPOLOGY → Configure simulated links**.

| Canal | Parâmetros | Uso implementado |
|---|---|---|
| Clássico | ID, `local-envelope-v1`, limite de bytes, idade máxima em segundos, habilitação | Observações do receptor e mensagens de controle da planta local |
| Quântico | ID, receptor (endpoint B), `bb84-statistical-record-v1`, habilitação | Registros estatísticos de atividade óptica, provenientes da planta ou de uma aquisição gravada |

**Limite técnico:** o canal quântico implementado transporta registros ópticos
estatísticos em memória. Não transporta qubits, estados quânticos físicos nem
simula estados individuais dos fótons. Não há driver de equipamento, socket,
autenticação de dispositivo ou confirmação de transmissão física. O replay de
medições também não realiza uma nova transmissão quântica. O suporte físico e o
transporte de informação quântica completa continuam pendentes.

O fluxo comum é `InputSource.frame → ChannelIntake → QuantumPort → ClassicalPort
→ validação das observações → LinkBank → alocação/roteamento → publicação`.
O produtor simulado gera somente a aquisição bruta com as plantas já existentes;
não preenche o estado dos nodes. O produtor de arquivos fornece aquisições
gravadas. Ambos usam os mesmos ports, associação, limites, verificação de
sequência e processamento. A fonte e o escopo estatístico permanecem identificados.

O port quântico recebe ID de aquisição, IDs dos dois canais, transmissor,
receptor, quantidade de pulsos emitidos e ganhos observados. O port clássico
recebe as rotações monitoradas, covariáveis, aquisição de relógio e as 17 colunas
da sessão decoy-BB84. Fidelidade, estado do enlace e série do gráfico são
calculados pelo backend a partir dessa entrada recebida. Fidelidade estimada a
partir do QBER permanece uma estimativa de Werner, não uma medição tomográfica.
Os hashes dos dois payloads e as etapas executadas aparecem em
`LiveState.links[].provenance.channels`; a janela do node identifica os canais e
a última aceitação ou recusa.

A ausência de um canal, sua desativação, uma associação incompatível, payload
excessivo, aquisição duplicada/fora de ordem ou observação inválida impede a
entrega ao `LinkBank`. O backend não substitui a aquisição recusada por valores
nominais de comunicação. Esses enlaces ficam fora do grafo efetivo de operação.
Um serviço sem fonte pode continuar planejando sobre configurações nominais:
esse planejamento não atesta uma conexão nem uma transmissão.

Inventários antigos sem o campo `channels` continuam legíveis, sem alterar a
serialização canônica ou os hashes antigos. Eles permanecem sem canais
configurados até uma edição explícita. Blueprints novos e chamadas antigas de
criação recebem defaults **locais e estatísticos**, publicados na configuração;
nenhum endpoint de equipamento é deduzido. A tela solicita os dois IDs e mostra
a associação antes de salvar.

## Arquivo bruto e seleção da fonte

No terminal do programa, use `input measured "C:\dados\acquisitions.json"` para
um arquivo de aquisições declaradas como medidas, `input simulated 19 quiet` para
o simulador ou `input none` para remover a fonte. A seleção passa pelo executor do
serviço; aguarde a leitura de volta do backend. A leitura de arquivos é somente
replay; comandos não modificam as aquisições históricas.

O envelope é JSON UTF-8, de até 64 MiB, sem campos repetidos:

```text
schema: "orion.channel-capture.v1"
source_id: identificador da fonte
kind: "measured" ou "simulated" (origem declarada, não autenticada)
frames: lista de 1..3600 objetos {tick, links}
links: lista de {observation, quantum_record}, no máximo 256 por quadro
```

`observation` contém `link_id`, `a`, `b`, `distance_km`, `epoch_tick`, `period`,
`polarisation`, `sync`, `qkd` e `domain_notes`, com os campos dos respectivos
contratos de `network_engine.sources`. As três séries de polarização são arrays
JSON com formas `(n,)`, `(n,3,3)` e `(n,3)`; as matrizes devem pertencer a SO(3).
`qkd` inclui a configuração de comissionamento e as 17 colunas de
`detector_session.COLUMNS`. Não são aceitos snapshots de nodes ou só resultados
de fidelidade/QBER como substitutos de uma aquisição completa.

`quantum_record` exige `acquisition_id`, `channel_id`, `associated_classical_id`,
`transmitter_id`, `receiver_id`, `emitted_pulses`, `signal_gain`, `decoy_gain`,
`vacuum_gain`, `scope` e `physical_transmission: false`. O escopo é
`recorded_measurement` para origem declarada medida ou `statistical_simulation`
para origem simulada. Os IDs, pulsos e ganhos devem coincidir com o enlace e sua
aquisição clássica. Essa coincidência verifica consistência; não certifica a
origem física do arquivo. Nenhum dado é gerado para preencher campos ausentes.

Os ticks originais são alinhados ao primeiro tick de leitura. Lacunas e o fim do
arquivo entregam ausência de dados; o último quadro não é repetido. O hash do
arquivo e a limitação de replay constam na identificação da fonte.

O formato anterior `orion.telemetry.v1`, disponível no leitor de evidências da
tela de entrada, continua sendo uma visualização de métricas declaradas e
somente leitura. Ele não fornece aquisições completas, não participa deste fluxo
de canais e não atesta comunicação quântica. Para o fluxo do motor, é necessário
o novo envelope bruto acima. Modelos continuam opcionais e a atuação física
continua bloqueada.

Na demonstração institucional, a atualização dos canais ausentes se aplica uma
única vez aos enlaces originais identificados pelo preset. O evento de topologia
guarda o estado anterior. Canais já configurados, enlaces desativados e objetos
criados pelo operador são preservados. Os novos trechos com repetidores usam
aquisições independentes e os mesmos seis estágios de recepção; o estado do node
não é preenchido diretamente. O teto de 150 km por trecho é uma escolha desta
demonstração, avaliada pela planta óptica existente.

A admissão de chave exige referência de comissionamento positiva e QBER abaixo
do limiar publicado. Uma amostra com zero erros por acaso não autoriza chave
quando o comissionamento já indica taxa nula. O valor bruto estimado recusado e
seu motivo permanecem na proveniência. Essa regra também vale para arquivos de
aquisições e não é uma prova de segurança finita nem uma certificação de hardware.
Enlaces medidos como `down` saem do grafo operacional, sem apagar sua aquisição
clássica nem sua conexão configurada no mapa.
