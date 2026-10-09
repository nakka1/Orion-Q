# Recepção satelital — quadros de missão e engenharia RAW

Revisão: 2026-10-08. O ORION-Q recebe quadros já demodulados, conserva bytes e
proveniência e aplica um decoder registrado por missão. Arquivos públicos,
gravações KISS, um demodulador externo e uma simulação usam o mesmo decoder.
Os valores EPS, COM e OBC permanecem RAW quando unidades, escalas ou relógios
não estão qualificados. Receber e decodificar um quadro não comprova recepção RF
local, autoria do satélite ou operação de uma carga útil quântica.

## Escolher a entrada

| Entrada | Origem do envelope | O que o resultado demonstra |
|---|---|---|
| `satnogs` | `measurement_file` | Download HTTPS de bytes publicados por uma estação, com observação, horários e hashes |
| `import-jsonl` | Origem declarada no arquivo, coincidente com a outbox | Importação de envelopes de missão identificados |
| `kiss-file` | `measurement_file` | Leitura de uma gravação delimitada em KISS, com horário de captura fornecido |
| `kiss-tcp` | Origem explicitamente escolhida | Recebimento de quadros de um processo local que emite KISS |
| Simulação | `simulated` | Exercício do mesmo transporte e decoder com entrada simulada identificada |

Escolha `physical` para `kiss-tcp` somente quando a origem dos quadros for uma
cadeia de recepção real identificada. Um servidor de teste ou replay mantém sua
origem correspondente. O socket não determina a origem e o software não atesta
antena, SDR, demodulação, FEC, calibração ou relógio da estação.

O pipeline óptico continua separado: `/ogs/v2/housekeeping`, `/ogs/v2/tracking`
e `/ogs/v2/tdc` conservam seus contratos e endpoints. Telemetria CSP não fornece
automaticamente vetores ECEF, lock APT, tags de detectores, coincidências, QBER
ou chaves. Consulte [Satélite / OGS v2](orion_q_satellite_ogs_v2.md) e
[efemérides públicas](orion_q_public_ephemeris.md) para essas trilhas.

## Decoder Suomi100 e perfis explícitos

O registry contém `mission_id=suomi100`, NORAD `43804`, decoder
`suomi100-stdlib`, versão `2`. Outras missões exigem registro de outro decoder;
nome desconhecido, decoder desativado e perfil indisponível são estados
explícitos. `decode_frame(mission_id, frame: bytes, *, profile)` não adivinha um
perfil a partir do comprimento nem modifica os bytes recebidos.

Ambos os perfis admitem somente Beacon0 (`frame[4] == 0`) e o cabeçalho CSP
`82a78001`. Os intervalos abaixo são semiabertos: `[4,88)` inclui os bytes 4 a 87.

| Perfil | Tamanho exato | Verificações CRC32C |
|---|---|---|
| `csp1-beacon0-crc32c-v1` | 92 bytes | Payload `[4,88)`; checksum big-endian em `[88,92)` |
| `satnogs-beacon0-dual-crc32c-v1` | 96 bytes | Payload `[4,88)` em `[88,92)` **e** quadro `[0,92)` em `[92,96)` |

O primeiro descreve o perfil CSP1.x com CRC do payload e verificação separada
do cabeçalho. É o padrão das ações KISS. O segundo é o perfil observado nos dois
arquivos públicos examinados; é o padrão das demais ações. A interpretação das
duas camadas de checksum do arquivo de 96 bytes não foi estabelecida. O produto
não o declara canônico CSP1.x e não remove quatro bytes para fazê-lo caber em
outro perfil. Um prefixo de 88 bytes sem CRC é recusado mesmo que seus campos
sejam parseáveis. `serve` usa o padrão de arquivo público; informe `--profile`
explicitamente ao receber quadros KISS de 92 bytes.

CRC32C detecta certas alterações de bytes; não autentica o satélite nem protege
contra um emissor que recompute o checksum. SHA-256 identifica os bytes ou o
artefato indicado pela proveniência, sem atribuir autoria. Um ACK `decoded`
significa execução do decoder; o resultado Suomi100 mantém
`qualification.status=decoded_not_qualified`, `units.status=raw_unscaled`,
`integrity.authenticated=false`, `clock.qualified=false` e HK/OGS/QKD/hardware
não validados.

Os frames publicados em 2026-10-08 às 13:54:20 e 13:55:20 UTC na observação
SatNOGS `15146588` têm timestamps internos interpretados pelo layout como
2033-10-06 às 19:21:00 e 19:22:00 UTC. Essa diferença permanece registrada;
o decoder não corrige a época nem usa o timestamp interno como recepção. Valores
como `batt_v=7285`, `temp=106` e `rssi=-117` permanecem valores brutos: não são
promovidos a volts, graus Celsius ou dBm sem especificação e calibração.

## Contrato de missão

O ingresso usa WebSocket local autenticado:

| Caminho | Conteúdo |
|---|---|
| `/satellite/v1/frames` | Envelope `orion.satellite-frame.v1` |
| `/satellite/v1/status` | Consultas `status`, `frames.latest` e `frames.query` |

Campos obrigatórios do envelope: `schema`, `source_id`, `origin`, `satellite_id`,
`mission_id`, `sequence`, `reception_id`, `received_at_utc`, `station`,
`frame_hex`, `decoder_profile` e `provenance`. `station` contém `id` e `name`,
com latitude e longitude opcionais em conjunto. `frame_hex` contém bytes
contíguos em hexadecimal, sem espaços. Campos extras, credenciais e resultados
QKD derivados são recusados.

`satellite_id` é o ID cadastrado no app, não uma inferência de NORAD. O binding
explícito associa esse ID a missão, NORAD e perfil. `provenance.provider` é
obrigatório; a proveniência pode conter URL pública, `retrieved_at_utc`, hashes
`sha256`, `observation_sha256` ou `capture_sha256`, `observation_id`,
`norad_cat_id`, `timestamp_origin`, `captured_at_utc` e `kiss_port`. Um hash do
arquivo completo não deve ser confundido com o hash do frame contido nele.
O ingresso calcula seus próprios hashes de frame e envelope.

`received_at_utc` descreve a recepção informada pela fonte;
`retrieved_at_utc` descreve a obtenção do artefato e `ingested_at_utc` sua entrada
no diário. Os timestamps a bordo ficam em `decode.clock`. UTC explícito é
obrigatório, e recepção/proveniência no futuro além da tolerância configurada
(300 s por padrão) é recusada. `measurement_file` e recepções com idade superior
a 300 s são marcadas como replay histórico; um arquivo recente continua arquivo.

No KISS, o comando exato `0x09` armazena um uint64 big-endian de UNIX em
milissegundos. O parser o associa uma vez ao próximo frame de dados do porto 0.
Falha, controle incompatível ou dados de outro porto descartam a associação.
`captured_at_utc` fornecido permanece separado e preservado. Sem extensão nem
horário fornecido, `timestamp_origin=local_ingress_clock` identifica quando os
bytes entraram no leitor, sem declarar horário RF. A origem
`kiss_timestamp_extension` também não qualifica sincronismo físico.

## Preparar o console nativo

Extraia a distribuição nova em sua própria pasta e conserve releases anteriores.
Mantenha dados, outboxes e relatórios fora da pasta do programa. Os exemplos usam
um diretório dedicado autorizado para esta operação; não reinicialize o estado
de uma implantação existente para contornar sua política.

```powershell
$taskData = 'C:\ORION-Q-data\satellite-reception-01'
.\ORION-Q.exe --ui native --state-root $taskData
```

Repita a definição de `$taskData` em cada terminal usado nos exemplos seguintes.

Em **COMUNICADORES**, cadastre um satélite com `mission` e capacidade
`classical_telemetry`. O trecho abaixo descreve um satélite apenas de telemetria;
complete IDs, função e órbita válida no cadastro. A órbita precisa ter fonte e
cobertura identificadas; não preencha uma órbita hipotética como medição.

```json
{
  "physical_type": "satellite",
  "technologies": [],
  "protocols": ["CSP1"],
  "capabilities": ["classical_telemetry"],
  "mission": {
    "mission_id": "suomi100",
    "norad_catalog_id": 43804,
    "decoder_profile": "csp1-beacon0-crc32c-v1"
  },
  "optical": {},
  "configuration": {},
  "orbit": {"optical_payload": "none_classical_telemetry_only"},
  "integration": {
    "mode": "measurement_stream",
    "adapter": "mission_gateway",
    "interface": "websocket_mission_v1",
    "service_address": "ws://127.0.0.1:8766/satellite/v1/",
    "credential_ref": "vault:orion-mission-session",
    "data_origin": "physical"
  }
}
```

O trecho não é um cadastro completo nem declaração de hardware validado. Para
arquivos públicos, use outro ID, por exemplo `SUOMI100-PUBLIC`, o perfil de
96 bytes e integração `measurement_file` com adaptador `measurement_file`,
endereço do arquivo identificado e `data_origin=measured`. Para RF em fluxo,
use `SUOMI100-RF` e o perfil de 92 bytes. `mission_id`, NORAD e perfil do envelope
devem coincidir com o cadastro. Um satélite óptico existente pode ter metadados
de missão sem perder seus campos ópticos; não acrescente uma carga útil quântica
ao cadastro RF para liberar o recebimento.

Na inspeção satelital, abra **Missão / RF · engenharia RAW** e
**Entrada Missão / RF · somente recepção**. Informe a fonte, origem, IDs
cadastrados, porta e `vault:orion-mission-session`. O receptor exige origem
coerente com a integração: `measurement_file` com arquivo, `simulated` com
simulação e `physical` com `physical` ou `measurement_stream`.

Um administrador autorizado usa **PROVISIONAR SESSÃO DE MISSÃO**; provisionar
exige `security.manage` e não inicia o receptor. O botão emite uma sessão de
uma hora para a fonte indicada e entrega somente sua referência no cofre.
Depois, o operador autorizado usa **ABRIR RECEPÇÃO DE MISSÃO**. Confirme
`listener_active=true` e `transport_state=listening` no estado publicado.
`state=decoded` refere-se ao último quadro, não ao socket.

**ENCERRAR RECEPÇÃO DE MISSÃO** fecha o listener; parar o demodulador externo é
uma ação separada. Trocar fonte, origem, perfil ou sessão exige encerramento e
nova configuração autorizada. Reabrir o console recupera o histórico e deixa
o listener parado; reabertura é manual. Expirar ou revogar a sessão bloqueia
novas mensagens, sem apagar frames ou transformar o histórico em aquisição nova.

## CLI e dependências

No portátil, use `ORION-Q.exe --satellite-gateway`. A execução equivalente em
um ambiente do projeto já provisionado é:

```powershell
python -m quantum_autopilot.quantum_protocols.satellite_gateway_cli --help
```

O gateway usa Python 3.10 ou superior e as dependências de segurança do projeto;
WebSocket exige
`websockets` do extra `satellite`. Decoder Suomi100 e parser KISS usam apenas
stdlib: não exigem Construct, SDK Qt ou GNU Radio no processo do gateway.
PySide6 é necessário ao console em execução a partir dos fontes. Efemérides
SGP4 são uma dependência distinta. O executável inclui suas dependências; instalar
um SDR/demodulador ou conectar hardware não faz parte desses comandos.

As opções gerais `--outbox`, `--source-id`, `--origin`, `--satellite-id`,
`--profile` e `--report` vêm **antes** da ação. `--outbox` é obrigatório para
aquisição/importação/entrega. Para usar Python nos exemplos seguintes, substitua
apenas `.\ORION-Q.exe --satellite-gateway` pelo módulo acima.

### Receptor standalone

Use um receptor por porta. O exemplo abaixo é uma alternativa ao listener do
console, em estado próprio e com origem de arquivo. Execute em um terminal e
use outro para aquisição/entrega. `serve` requer autoridade administrativa,
provisiona uma sessão no cofre local e publica somente a referência.

```powershell
.\ORION-Q.exe --satellite-gateway --source-id mission_public `
  --origin measurement_file --satellite-id SUOMI100-PUBLIC `
  --profile satnogs-beacon0-dual-crc32c-v1 `
  --report "$taskData\receiver-public.json" serve `
  --state-root "$taskData\standalone-public" --port 8766 --duration 300 `
  --mission-id suomi100 --norad 43804
```

`--duration` limita o período de serviço (máximo 86400 s); `Ctrl+C` solicita
encerramento. O relatório final registra `stopped` e o estado do diário.
Os limites de software não são garantia de tempo real nem um comando de parada
de hardware externo. Para o listener nativo, o cofre é
`<state-root>\.security`; para o exemplo standalone, é
`$taskData\standalone-public\.security`.

### Arquivo público SatNOGS

O exemplo usa a observação histórica identificada na avaliação. Download atual
de seu arquivo não passa a ser recepção local atual. Disponibilidade externa e
conteúdo publicado podem mudar; falha HTTPS não gera telemetria substituta.

```powershell
.\ORION-Q.exe --satellite-gateway --outbox "$taskData\public-outbox.sqlite" `
  --source-id mission_public --origin measurement_file --satellite-id SUOMI100-PUBLIC `
  --profile satnogs-beacon0-dual-crc32c-v1 --report "$taskData\satnogs.json" `
  satnogs --observation 15146588 --norad 43804 --timeout 5 --polls 1
```

O adaptador público implementado é Suomi100/NORAD 43804. Ele confere a identidade
na resposta da observação, vínculo dos arquivos, janela temporal e limites de
resposta. Faz somente GETs HTTPS aos endpoints allowlisted do SatNOGS Network
e seu armazenamento, sem credenciais externas ou uploads. Redirecionamentos
são recusados. Polling explícito admite 1–60 consultas, intervalo de 30–3600 s;
isso é um limite do cliente, não uma promessa de rate limit do provedor.

Para envelopes já identificados, importe um JSON por linha. A outbox aloca sua
própria sequência; horários, origem e bytes não são refeitos pelo importador.

```powershell
.\ORION-Q.exe --satellite-gateway --outbox "$taskData\import-outbox.sqlite" `
  --source-id mission_import --origin measurement_file --satellite-id SUOMI100-PUBLIC `
  --report "$taskData\import.json" import-jsonl '.\capturas\mission-envelopes.jsonl'
```

Nesse arquivo, `source_id` e `origin` precisam coincidir com a outbox; o ID do
satélite e o perfil efetivos vêm de cada envelope. `--satellite-id` e `--profile`
não reescrevem uma linha importada. Configure o receptor para `mission_import`
antes de entregar essa outbox.

### Gravação KISS

Configure previamente o demodulador externo para a missão e o formato real da
estação. gr-satellites pode emitir arquivo com `--kiss_out` ou servidor TCP com
`--kiss_server`; sua demodulação e FEC ficam fora do ORION-Q. Uma gravação RF/IQ
precisa primeiro produzir quadros KISS. Reproduzir uma gravação com timestamps
da execução não qualifica o horário original da observação.

```powershell
.\ORION-Q.exe --satellite-gateway --outbox "$taskData\replay-outbox.sqlite" `
  --source-id mission_replay --origin measurement_file --satellite-id SUOMI100-REPLAY `
  --profile csp1-beacon0-crc32c-v1 --report "$taskData\kiss-file.json" `
  kiss-file '.\capturas\suomi100.kiss' --captured-at '2026-10-08T13:55:20Z' `
  --station-id ESTACAO-01 --station-name 'Estação identificada da gravação' `
  --mission-id suomi100 --norad 43804 --max-frames 100 --timeout 30 --max-bytes 1048576
```

Substitua arquivo, estação e horário pelos valores documentados da sua captura.
`--captured-at` é obrigatório e permanece na proveniência, mesmo quando `0x09`
fornece o horário do frame. O hash do arquivo identifica a captura. A leitura
CLI usa uma cópia temporária limitada e identificada por hash, preservando
o original; repetir o mesmo arquivo com metadados diferentes não autoriza
substituir sua evidência.
Cadastre `SUOMI100-REPLAY` como arquivo, missão Suomi100 e perfil de 92 bytes;
o receptor precisa usar fonte `mission_replay` e esse mesmo binding.

### Fluxo TCP de demodulador local

Prepare um receptor nativo para `mission_rf`/`physical`/`SUOMI100-RF`/perfil de
92 bytes ou use o standalone com esse binding explícito:

```powershell
.\ORION-Q.exe --satellite-gateway --source-id mission_rf --origin physical `
  --satellite-id SUOMI100-RF --profile csp1-beacon0-crc32c-v1 `
  --report "$taskData\receiver-rf.json" serve `
  --state-root "$taskData\standalone-rf" --port 8766 --duration 300 `
  --mission-id suomi100 --norad 43804
```

Em outro terminal, conecte ao servidor KISS local já configurado. Use um novo
`capture-id` a cada sessão de aquisição; a entrega posterior reutiliza a outbox,
sem reconectar à RF para tentar reconstruir frames já recebidos.

```powershell
.\ORION-Q.exe --satellite-gateway --outbox "$taskData\rf-outbox.sqlite" `
  --source-id mission_rf --origin physical --satellite-id SUOMI100-RF `
  --profile csp1-beacon0-crc32c-v1 --report "$taskData\kiss-tcp.json" `
  kiss-tcp --host 127.0.0.1 --port 8100 --capture-id rf-20261008-sessao-01 `
  --station-id ESTACAO-01 --station-name 'Estação RF identificada' `
  --mission-id suomi100 --norad 43804 --max-frames 100 --timeout 30 --max-bytes 1048576
```

O cliente KISS conecta somente a IP loopback literal ou `localhost` mapeado a
127.0.0.1, sem DNS, comandos de transmissão ou reconexão automática. Recompõe
escapes `C0`/`DB` e frames fragmentados entre chunks. Frames malformados,
acima do limite de tamanho ou incompletos são descartados e contados em
quarentena limitada.
O relatório distingue `eof`, `deadline`, `max_frames`, `byte_limit`,
`cancelled`, `closed`, `file_error`, `connect_error` e `socket_error`.
`frames_queued=0` ou `state=queued` não promete amostras válidas; examine
`capture`, contadores e o processamento do receptor.

### Entregar ao receptor autenticado

Para a outbox pública e o receptor standalone acima:

```powershell
.\ORION-Q.exe --satellite-gateway --outbox "$taskData\public-outbox.sqlite" `
  --source-id mission_public --origin measurement_file --satellite-id SUOMI100-PUBLIC `
  --report "$taskData\delivery-public.json" deliver `
  --security-root "$taskData\standalone-public\.security" `
  --credential-ref vault:orion-mission-session `
  --url ws://127.0.0.1:8766/satellite/v1/frames
```

Para RF, use a outbox/fonte/origem RF e o cofre do receptor correspondente:
`$taskData\standalone-rf\.security`, ou `$taskData\.security` no console nativo.
Para acompanhar a outbox enquanto `kiss-tcp` está em execução, mantenha produtor
e entregador em processos separados, apontando para o mesmo SQLite:

```powershell
.\ORION-Q.exe --satellite-gateway --outbox "$taskData\rf-outbox.sqlite" `
  --source-id mission_rf --origin physical --satellite-id SUOMI100-RF `
  --report "$taskData\delivery-rf.json" deliver `
  --security-root "$taskData\standalone-rf\.security" `
  --credential-ref vault:orion-mission-session `
  --url ws://127.0.0.1:8766/satellite/v1/frames `
  --follow-seconds 60 --batch-size 32 --interval 1
```

`--follow-seconds` admite 0–3600 s (0: uma rodada), `--batch-size` 1–256
(padrão 32), e `--interval` 0,1–60 s (padrão 1). O envio à rede fica no processo
entregador; falha de transporte conserva as mesmas pendências para nova
tentativa. A captura continua sujeita à quota e ao seu próprio limite de tempo.
O relatório registra `transport_failures`, `identity_blocked`, `pending` e
`last_error_type`. HTTP 401/403 bloqueia a drenagem, sem renovar o token;
`auto_session_renewal=false`. Examine a sessão e peça provisionamento autorizado
antes de repetir a entrega. Um limite de acompanhamento não garante drenagem
completa de todas as pendências.

O emissor local precisa de acesso autorizado ao cofre pela mesma conta Windows
ou por um provisionamento próprio da implantação. Tokens não são argumentos,
variáveis de ambiente ou campos de cadastro. Não copie o cofre privado para
outra estação.

O transporte revalida identidade, escopo e revogação a cada mensagem. A referência
`vault:` e um listener loopback não substituem autenticação. Revogar
`service:satellite:<source-id>` pela administração bloqueia novas mensagens;
encerrar o listener é uma ação separada. Origem browser é recusada. Uma estação
remota requer fronteira/proxy TLS autenticado e provisionamento da implantação;
o listener e o cliente descritos aqui permanecem locais. Consulte
[segurança comum](orion_q_security.md) e [PKI](orion_q_security_pki_keys.md).

## Recibos, quotas e recuperação

| Estado do frame | Significado |
|---|---|
| `received` | Envelope/bytes persistidos; processamento pendente |
| `decoded` | Decoder executado e resultado persistido; verificar qualificação separada |
| `quarantined` | Evidência preservada, sem resultado admitido: formato/CRC, identidade, limite ou falha de decode |
| `unavailable` | Decoder, perfil ou dependência indisponível; evidência preservada |
| ACK `duplicate` | Repetição idêntica de uma recepção já persistida |
| ACK `rejected` | Mensagem não admitida: contrato, fonte, binding, sequência, autenticação ou quota |

`acknowledged` na outbox também pode corresponder a `quarantined` ou
`unavailable`: é um recibo durável de transporte, não dado qualificado.
`deliver` envia até 32 pendências por rodada por padrão; `--batch-size` altera
esse limite e `--follow-seconds` permite acompanhar novas pendências. Se ainda
houver pendências, repita a entrega com a mesma outbox após examinar os recibos.

| Limite padrão | Valor / comportamento |
|---|---|
| Outbox | 16 MiB lógicos; envelope de até 32 KiB; nenhuma exclusão automática |
| Ingresso | Frame de até 64 KiB; mensagem de até 512 KiB |
| Pendências de decode | 32 frames / 8 MiB |
| Diário de missão | 64 MiB lógicos / 100 mil registros; preserva o histórico ao atingir quota |
| Resultado do decoder | Até 256 KiB; projeção de apresentação limitada, evidência completa no diário |
| Consulta | Até 128 registros, resposta limitada; paginação por `after_id` |
| KISS CLI | 1000 frames / 30 s / 16 MiB de entrada, alteráveis explicitamente |
| Parser KISS | Frame de 4 KiB, 32 eventos de quarentena; chunks de até 64 KiB |

As quotas lógicas não prometem o mesmo tamanho físico do SQLite ou do diretório.
O limite de tempo da leitura KISS é de até 3600 s e o de bytes até 256 MiB.
Esses limites são de software; não qualificam entrega em tempo real ou parada
instantânea de processos externos.

A sequência cresce por **fonte**, inclusive entre satélites diferentes. A
outbox persiste o envelope antes de enviar. Timeout ou perda de ACK deixa a
mesma linha pendente: repita `deliver`, conservando `reception_id`, sequência,
origem, horários e bytes. O hash do ACK precisa corresponder ao envelope enviado.
Conflito de recepção ou sequência é recusado, preservando o primeiro registro.

Após queda, pendências `received` são recuperadas pelo diário. Frames
`quarantined`/`unavailable` exigem revisão e retry explícito de decode, pela API
`MissionFrameStore.retry_decode(journal_id)`, sem criar uma recepção nova.
Não há ação CLI de retry-decode anunciada neste guia. Registro de decoder ou
alteração do cadastro não converte automaticamente evidência antiga em dado
físico qualificado.

Ao atingir quota, preserve/exporte a evidência e planeje um novo diário/outbox
com um novo ID de fonte e reconfiguração explícita do binding/receptor.
Reiniciar a sequência em uma outbox vazia com a mesma fonte de um receptor já
utilizado causa conflito; não apague ou reutilize automaticamente os diários.
Reabrir o listener com o mesmo estado recupera o histórico; restaurar um backup
antigo não é autorização para substituir bytes ou recibos atuais.

## Fontes e licenças

- [KISS, Chepponis/Karn](https://www.ka9q.net/papers/kiss.html): delimitadores, escapes e controle/porto. KISS não adiciona CRC próprio.
- [gr-satellites 5.9, KISS output](https://gr-satellites.readthedocs.io/en/v5.9.0/command_line.html#kiss-output): extensão `0x09`, UNIX ms uint64BE e saídas para integração com demodulador externo.
- [Layout Suomi100, commit cb5d0fc](https://github.com/daniestevez/gr-satellites/blob/cb5d0fca22ed439e9ce029516b755491a57c5764/python/telemetry/suomi100.py): fatos do layout de Beacon0; não estabelece escalas físicas de todos os campos.
- [libcsp CRC32, commit 583c9eb](https://github.com/libcsp/libcsp/blob/583c9ebc23b9193c2c6800fdff6a182e13df03e7/src/csp_crc32.c): cobertura do payload no perfil anterior a CSP2.1.
- [SatNOGS Network API](https://docs.satnogs.org/projects/satnogs-network/en/stable/api.html) e [observação 15146588](https://network.satnogs.org/api/observations/15146588/): publicação/proveniência dos frames; serviço público não autentica a carga útil satelital para esta integração.

As duas fixtures públicas em `tests/fixtures/satellite_public` conservam bytes,
horários, URLs e SHA-256 em `provenance.json`, com atribuição SatNOGS Network /
Libre Space Foundation, estação CIA_S4 (5112), licença
[CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/) e `LICENSE.md`.
Os decoders e o parser do produto foram escritos independentemente em stdlib;
nenhum código GPL de gr-satellites foi incorporado. Referências e ferramentas de
pesquisa permanecem separadas, com suas próprias licenças e proveniência.
