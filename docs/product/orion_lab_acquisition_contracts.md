# Aquisição da bancada: configuração, observações e captura

Implementação e consulta das referências: **2026-10-07**. Toda a validação abaixo utiliza interfaces sintéticas e arquivos de fixture identificados. Não houve comandos enviados a equipamentos.

## Configuração Swabian

`SwabianSDKAdapter` separa a configuração experimental (`PhotonConfig`) das capacidades consultadas do instrumento (`InstrumentCapabilities`). Antes do primeiro setter, verifica todos os canais, faixas de trigger, canais de referência, unidades e limites inteiros. A documentação usa volts e timestamps int64 em picosegundos; não se adivinham códigos de eventos. [Biblioteca oficial](https://www.swabianinstruments.com/static/documentation/TimeTagger/api/TimeTaggerLibrary.html).

A aplicação nativa não é uma transação de hardware. Qualquer erro durante setters, sincronização ou leitura posterior limpa a configuração considerada válida e muda o estado para `configuration_unverified`. A aquisição fica bloqueada. Não há rollback presumido nem conservação de um `ready` com valores antigos.

`reverify_configuration(expected_configuration=None)` sincroniza e lê novamente **todos** os triggers, atrasos e estado do relógio. A correspondência usa tolerância de 1 nV nos triggers e igualdade inteira nos atrasos; uma quantização incompatível precisa ser reconhecida em uma configuração explícita. A recuperação exige correspondência completa e retorna a `configured`, seguido de nova calibração. `getReferenceClockState` permite verificar canal, período e lock; o relógio externo é desabilitado quando a configuração seleciona o relógio interno. [Métodos oficiais](https://www.swabianinstruments.com/static/documentation/TimeTagger/api/TimeTagger.html).

Conexão, configuração, calibração, aquisição e desconexão não podem executar concorrentemente no mesmo objeto. Uma alteração externa detectada antes da aquisição também retira a autorização de aquisição. Isso não substitui controle exclusivo e sincronismo comprovado em uma bancada real.

## Seleção de adaptadores

O serviço escolhe o adaptador por `adapter_id` por meio de `create_photon_adapter`. `emulated` e `emulated-reference` usam o mesmo modelo identificado, em configurações independentes. **A substituição entre implementações distintas** é demonstrada por `EmulatedPhotonAdapter` e `ReplayPhotonAdapter`: ambos seguem `connect → validate → configure → calibrate → acquire → store → analyze` em `run_registered_acquisition`.

O serviço resolve o replay por recibo local validado; `manifest_path` é um argumento interno, não uma API para caminhos arbitrários. O replay não aceita alterar a configuração original, origem, timestamps, relógio ou identidade experimental. Seu identificador de execução fica separado em `replay_run_id`. O registro nativo não habilita automaticamente hardware e não usa fallback simulado.

## Observações que alimentam BB84

`ObservationBlock` registra origem/modo, experimento, dispositivo, protocolo, configuração, intervalo de captura e relógio. Cada `PulseObservation` contém `pulse_id`, timestamp de preparação, base/bit preparado, base de análise, detecção, bit analisado e timestamp detectado. Pulsos não detectados não contêm bits de saída.

Bases e bits vêm de registros de preparação/análise. **Timestamps isolados não são convertidos em bits ou bases.** `bind_acquisition_observations` exige identificação dos dois registros e mapa explícito da fiação dos detectores; verifica correspondência de todos os clicks e conserva o SHA256 da aquisição bruta. Um registro SPDC de coincidências não qualifica, por si só, uma experiência BB84 com fonte de fóton único. A adequação e caracterização da fonte continuam necessárias.

O transporte do bloco pode conservar ticks relativos superiores a 2^53 e época UTC com timezone explícito quando esta foi fornecida. O vínculo de registros físicos exige uma referência de relógio medida explícita, incluindo incerteza, no mesmo domínio da aquisição; não presume alinhamento perfeito a partir dos timestamps. Um relógio não alinhado não autoriza sifting. O formato contém registros de teste sensíveis: a demonstração usa seed pública, não aleatoriedade secreta ou armazenamento certificado.

## Captura contínua

`run_continuous_capture` utiliza `start → drain → stop` para fontes sintéticas e `NativeStreamingSource`. Cada bloco passa por validação e é gravado em SQLite junto com sequência, SHA256 e hash anterior. A mesma sequência com o mesmo conteúdo é idempotente; conteúdo diferente, sequência omitida ou outro proprietário são recusados. O cancelamento para a fonte e drena o último buffer disponível. Há timeout explícito.

A biblioteca documenta `TimeTagStream.getData()` como drenagem que esvazia o buffer. Saturação do buffer local e overflow no fluxo são condições diferentes. `FileReader.getData()` fornece o mesmo tipo de buffer. [Fluxos e arquivos oficiais](https://www.swabianinstruments.com/static/documentation/TimeTagger/api/measurements/time_tag_streaming.html).

O produtor sintético tem taxa conhecida em **tempo lógico**: abaixo da capacidade conserva eventos; acima dela contabiliza perdas exatas. O adaptador físico não inventa a quantidade produzida ou descartada pelo buffer: conserva `MissedEvents` publicados e indica contagem desconhecida quando não há evidência suficiente. Essa campanha não mede throughput real do SDK.

Na recuperação, um writer vivo não é tomado por outro processo. O encerramento real do writer permite marcar `interrupted`; blocos confirmados permanecem, uma transação não confirmada é revertida, e perdas de dados em trânsito ficam explicitamente desconhecidas. Um capture_id interrompido não é reutilizado. Uma nova aquisição exige novo identificador: não se declara recuperação de buffers físicos perdidos.

## Importação FileWriter

`import_filewriter_record` recebe a lista completa dos arquivos, SHA256 de cada arquivo, contexto original, hash da configuração nativa e mapa documentado dos tipos de evento. Um cabeçalho sem blocos ou uma sequência incompleta é recusado. A leitura preserva a configuração nativa opaca e valida sua vinculação ao contexto fornecido, canais, ordenação, unidades e intervalo. A configuração óptica normalizada é **metadado original fornecido**, não inferida de um schema proprietário desconhecido; sua interpretação precisa ser validada para a versão da instalação.

O importador chama o **FileReader oficial**, quando disponível. Os testes injetam fixtures de métodos e arrays; não decodificam `.ttbin` nativos. `createTimeTaggerVirtual` e replay licenciado continuam dependendo de SDK, licença aplicável e arquivos FileWriter completos. A existência de um cabeçalho experimental não demonstra disponibilidade dos blocos de clicks.

## Reprodução

```powershell
$env:PYTHONPATH = 'src'
.audit-winvenv\Scripts\python.exe -m pytest tests\test_lab_swabian_hardening.py tests\test_lab_observations_capture.py tests\test_lab_swabian_import_registry.py tests\test_lab_photon.py -q --basetemp=outputs\lab_hardening_20261007\reproduction_tmp
```

As regressões incluem falha do segundo canal após modificação do primeiro, todas as faixas verificadas antes de qualquer setter, readback divergente, falhas de atraso/relógio/sync, concorrência, cancelamento nativo com buffer pendente, duas implementações de adaptadores, corrupção de replay, taxas conhecidas e encerramento de dois writers reais — inclusive durante uma transação SQLite.

Permanecem pendentes SDK/licença/replay nativo completo e validação física de espectro, eficiência, jitter, níveis elétricos, impedância, sincronismo, calibração e caracterização experimental de entrelaçamento.
