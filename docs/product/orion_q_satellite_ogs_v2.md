# Satélite / OGS v2 — operação e contratos

Implementação 5.41. O adaptador recebe registros clássicos de uma estação óptica;
não recebe fótons pela rede TCP e não controla RF, telescópios ou carga útil.

## Execução

```powershell
python -m pip install -e ".[console,satellite,dev]"
python -m pytest -q tests/test_satellite_pipeline_v2.py tests/test_console_satellite_telemetry.py
python tools/validate_satellite_pipeline.py --output-dir outputs/satellite-validation-novo --seed 42
```

No executável: GEO/Topology → PROTOCOLOS → selecione `RIO-SATELLITE` →
**SATÉLITE · HOUSEKEEPING / SCIENCE / ENTRADA OGS**. As tabelas mostram unidades,
origem, idade, perdas, APT, TDC e motivos de aborto. A evidência capturada permanece
estável durante a atualização da rede. Também é possível abrir a inspeção na
janela de um node conectado ao satélite.

Para recepção externa, autentique a identidade local/institucional e use uma sessão
autorizada para o serviço `ogs_gateway`. Um administrador com `security.manage`
pode clicar **PROVISIONAR SESSÃO OGS**: a sessão de uma hora permanece no cofre e
a interface recebe apenas sua referência, por padrão `vault:orion-ogs-session`.
Provisionar e iniciar são permissões distintas; a disponibilidade de um botão não
substitui a autorização aplicada novamente pelo backend.

Informe somente `vault:<referência de sessão autorizada>` no formulário e clique
**ABRIR RECEPÇÃO OGS**. `env:` e tokens digitados são rejeitados antes da emissão
do comando. A verificação sintática da referência na interface não comprova sua
existência, validade ou permissão: o serviço resolve o cofre e verifica a sessão.
O listener escuta `127.0.0.1:8765`; encerrar a entrada ou a aplicação fecha o socket.
Uma estação remota exige uma implantação autenticada/TLS adequada; o listener
local não deve ser exposto diretamente. Tokens não entram nos cadastros, tabelas
ou evidências capturadas.

O gateway consulta `/ogs/v2/status`, com o mesmo bearer, enviando
`{"query":"status"}`. A resposta `orion.ogs-status.v2` informa o relógio operacional
e as janelas pendentes com sessão, início/fim, prazo e IDs dos dois canais. Ela não
exige IDs internos de comando nem autoriza disparar hardware. Um gateway que já
recebe dados continuamente deve bufferizar, calibrar e normalizar as observações
dessas janelas; o mapeamento UTC/rede continua sendo responsabilidade do integrador.
Janelas concluídas/expiradas saem da consulta; capturas consecutivas usam sequências
crescentes por stream.

Em Comunicadores, o modo **OGS WebSocket · somente recepção** exige tecnologia
satelital, `ogs_read_only_v2`, interface `websocket_ogs_v2`, endereço local e
referência `vault:` de uma sessão autorizada. Cadastre os três componentes com origem compatível antes de
ativar uma conexão. O listener precisa estar ativo; ausência de dados gera timeout.
Cada segmento mantém seus IDs clássico e quântico. RF, beacon e payload podem
compartilhar telescópio conforme a missão, conservando streams e funções distintos.

## Transporte

| Caminho WebSocket | Conteúdo | Origem física prevista |
|---|---|---|
| `/ogs/v2/housekeeping` | Vetor ECEF, velocidade, época, validade, emissão e origem | Gateway RF/laser clássico após decodificação do ICD |
| `/ogs/v2/tracking` | Lock APT, tempo observado e intervalos operacionais por OGS | Controlador beacon/telescópio |
| `/ogs/v2/tdc` | Tags inteiros, IDs de detector, relógio, correções e perdas | TDC após detectores de fóton único |

Handshake: `Authorization: Bearer <token>`. Mensagens JSON, limite de 8 MiB,
origem browser bloqueada, no máximo quatro conexões, fila WebSocket limitada e
heartbeat. ACK `accepted` ou `duplicate` contém hash e sequência. `rejected`
contém código: `wrong_source`, `wrong_channel`, `replay`, `stale_observation`,
`backpressure`, `missing_stream`, `unsynchronized` ou `tdc_overflow`, conforme o caso.
O diário OGS limita registros aceitos a 512 MiB; o armazenamento comprimido de
aquisições limita-se a 2 GiB por padrão (`raw_store_limit_bytes`, configurável).
`storage_full` interrompe novos dados preservando o histórico; exporte/arquive e
use um novo diário. Dados antigos não são apagados automaticamente para abrir espaço.

Campos exatos do envelope: `schema`, `session_id`, `source_id`, `origin`,
`sequence`, `start_s`, `end_s`, `quantum_channel_ids`, `classical_channel_ids`,
`kind`, `data`. Schema `orion.ogs-packet.v2`; origem explícita `physical`,
`simulated` ou `measurement_file`. A identidade esperada do listener é
`ogs_gateway`. `kind` coincide com o caminho. A sequência cresce independentemente
em cada stream da sessão e sobrevive à reconexão. Os três streams usam o mesmo
intervalo do relógio operacional e os mesmos canais. Não é necessário conhecer
o ID interno de comando para enviar o bloco.

Exemplo de cliente: os arquivos `*.ogs-packets.jsonl` gerados pelo validador
contêm os três contratos completos, vinculados à topologia usada no teste.
Um gateway real deve convertê-los a partir de suas próprias observações.

```python
from websockets.sync.client import connect
import json

def send_ogs_packet(packet, authorized_credential_provider):
    # Integração do gateway: o provedor autorizado entrega o bearer em memória.
    # Não é uma API fictícia do ORION-Q nem uma variável de ambiente.
    token = authorized_credential_provider()
    url = f"ws://127.0.0.1:8765/ogs/v2/{packet['kind']}"
    with connect(url, additional_headers={"Authorization": "Bearer " + token}) as connection:
        connection.send(json.dumps(packet, allow_nan=False))
        return json.loads(connection.recv())
```

O provedor de credenciais desse exemplo é uma dependência a implementar na
implantação autorizada da estação. Ele não deve registrar o bearer, entregá-lo a
processos sem permissão ou copiar o cofre privado para o gateway. A interface do
ORION-Q trabalha somente com a referência; autenticação e transporte continuam
responsabilidades explícitas do serviço e do gateway.

## Missão / RF — engenharia clássica independente

A inspeção possui a aba **Missão / RF · engenharia RAW** e uma entrada própria
**Entrada Missão / RF · somente recepção**. A janela flutuante do satélite mostra
a mesma seção quando o snapshot contém `satellite_mission_ingress`. Nenhum timer
de aquisição é criado na interface: as tabelas reutilizam os itens existentes e
só alteram os campos cujo conteúdo mudou. A captura é um retrato estável, separado
das atualizações seguintes.

Essa entrada recebe quadros de missão já demodulados/extraídos pela estação.
A implementação do decoder não qualifica uma antena, receptor RF ou demodulador.
EPS, COM e OBC são apresentados com os nomes e valores **RAW** fornecidos pelo
decodificador. Sem ICD, escalas e calibração, `batt_v`, `temp` e `cur` não passam a
ser volts, graus Celsius ou amperes. CRC verificado não autentica a origem do
satélite. O hash identifica bytes e não comprova autoria.

O relógio de recepção UTC é mostrado separado dos timestamps a bordo. A
interpretação Unix preservada pelo decoder não qualifica a época ou o sincronismo
da missão: o relógio não qualificado continua em quarentena. Quadros aceitos pelo
transporte podem permanecer `decoded_not_qualified` para uso operacional; o total
aceito não é uma declaração de dados físicos calibrados. Essa entrada não
alimenta automaticamente vetores ECEF, tracking/APT, contagens TDC, QBER ou chaves.
O pipeline OGS exige suas próprias observações e validações.

| Campo do formulário | Regra / significado |
|---|---|
| Identificador da fonte | Obrigatório; padrão `mission_gateway`; coincide com a identidade provisionada e os envelopes |
| Origem | Obrigatória: `physical`, `measurement_file` ou `simulated`; origem do dado, não do socket |
| IDs dos satélites | Obrigatórios; lista separada por vírgulas de comunicadores cadastrados; IDs repetidos são deduplicados |
| Referência da sessão | Obrigatória; padrão `vault:orion-mission-session`; o backend valida autoridade e revogação |
| Porta local | Inteiro entre 1024 e 65535; padrão 8766, distinto da entrada OGS |
| Provisionar sessão | Ação administrativa independente, `security.manage`; TTL de uma hora; nenhum token exibido |

O formulário emite `configure_technology_source` com `kind="mission_websocket"`,
`source_id`, `origin`, `satellite_ids`, `credential_ref` e `port`.
**ENCERRAR RECEPÇÃO DE MISSÃO** emite `kind="stop_mission"` pelo mesmo serviço.
Os comandos são intenções: apenas o estado publicado pelo backend indica que o
listener está ativo. Um descriptor `stopped` permite reabrir; `listening` bloqueia
nova abertura e permite encerramento ao usuário autorizado.

`listener_active` é a autoridade para esses controles, com `transport_state`
mostrando `listening` ou `stopped`. O campo `state` descreve o processamento do
último quadro, como `decoded`, `quarantined` ou `unavailable`; ele não indica se
o socket está aberto. Antes do primeiro quadro, um listener ativo já pode ser
encerrado; após fechar, um histórico `decoded` não impede reabertura. O replay
histórico e a proveniência da estação ficam explícitos na seção de recepção.

O gateway local recebe os quadros em
`ws://127.0.0.1:8766/satellite/v1/frames`; a consulta fica em
`/satellite/v1/status`, com a mesma autenticação. O envelope
`orion.satellite-frame.v1` identifica `source_id`, `origin`, `satellite_id`,
`mission_id`, `sequence`, `reception_id`, `received_at_utc`, `station` com `id` e
`name`, `frame_hex`, `decoder_profile` e a proveniência de obtenção. O perfil
Suomi100 observado é `satnogs-beacon0-dual-crc32c-v1`; ele não afirma que qualquer
quadro da missão é canônico ou fisicamente calibrado. A proveniência pode registrar
provider, URL, instante de obtenção, SHA-256 e referências da observação/NORAD,
conforme o contrato do serviço.

O cliente é `python -m quantum_autopilot.quantum_protocols.satellite_gateway_cli`
ou, no executável, `ORION-Q.exe --satellite-gateway`. A referência
`vault:orion-mission-session` resolve a sessão autorizada; o token não é argumento
da linha de comando. O procedimento de operação e os contratos completos estão
em [Recepção satelital](orion_q_satellite_reception.md).

Os dois botões de provisionamento emitem `provision_satellite_session` com
`source_id`, `session_reference` sem o prefixo `vault:` e `ttl_seconds=3600`.
Seu retorno contém a referência do cofre, nunca o bearer. A sessão e seus escopos
são concedidos pelo serviço de segurança; o cliente não pode autorizá-los por
alteração dos campos do formulário.

Contrato de apresentação independente do renderizador GEO:

```json
{
  "source_id": "mission_gateway",
  "origin": "measurement_file",
  "state": "decoded",
  "transport_state": "listening",
  "listener_active": true,
  "last_received_at_utc": "2026-10-08T12:00:00+00:00",
  "accepted": 1,
  "quarantined": 0,
  "latest": {
    "satellite_id": "SAT-ID-CADASTRADO",
    "mission_id": "suomi100",
    "station": "estacao-de-replay-identificada",
    "received_at_utc": "2026-10-08T12:00:00+00:00",
    "origin": "measurement_file",
    "frame_sha256": "sha256-dos-bytes",
    "status": "decoded_not_qualified",
    "historical_replay": true,
    "provenance": {"provider": "arquivo de medicao identificado"},
    "decode": {
      "decoder_id": "suomi100-stdlib",
      "decoder_version": "versao-publicada-pelo-registro",
      "header": {},
      "payload": {"eps": {}, "com": {}, "obc": {}},
      "integrity": {"verified": true, "authenticated": false},
      "clock": {"qualified": false},
      "qualification": {"status": "decoded_not_qualified"},
      "units": {"qualified": false, "status": "raw_unscaled"}
    }
  }
}
```

`latest` é um objeto ou `null`, nunca uma lista de histórico. A inspeção global
pode exibir o último quadro da fonte; a inspeção de um satélite filtra por seu ID
e mostra ausência de observação quando o último quadro pertence a outro. O
snapshot não carrega credenciais nem bytes brutos completos. A evidência capturada
inclui `mission_rf` e `optical_session` separados quando existe um quadro de missão.
Integração Mission/RF e OGS são trilhas distintas, com associação explícita e
qualificação própria antes de qualquer uso conjunto.

Housekeeping exige `frame=ECEF`, `ecef_km`, `velocity_ecef_km_s`, `at_s`,
`valid_until_s` e `source`. Validade padrão máxima: 5 s. ECEF exige conversão
correta do referencial da missão; TEME/J2000 não são aliases de ECEF.
No pacote desse stream, `data` contém o objeto `housekeeping`, `emitted_pairs`
inteiro observado no bloco e `geometry` com dois registros na ordem dos endpoints.
A geometria mínima exige `range_km`, `elevation_deg` e `visible` booleano; quando as
coordenadas das OGS estão cadastradas, o backend recalcula a geometria a partir
do vetor recebido. A contagem de emissão precisa vir da fonte/carga útil ou de
sua observação normalizada, sem substituí-la por uma contagem final de detecções.
Dados de engenharia opcionais `control_downlink`, `bus_voltage_v`,
`payload_temperature_c` e `battery_soc` são apresentados quando recebidos;
ausência permanece sem valor, sem preenchimento pelo backend.

`data` de tracking e TDC é uma lista de dois objetos, na mesma ordem das OGS
endpoints. Cada tracking exige `station_id`, `locked` booleano, `at_s` e
`open_intervals_s`: lista de intervalos `[início,fim]` dentro do bloco, podendo
ser vazia quando o enlace está indisponível. Frescor padrão do APT: 60 s.
`housekeeping_max_age_s`, `tracking_max_age_s` e `sync_tolerance_s` são políticas
configuráveis da sessão, aplicadas às fontes físicas, de arquivo e simuladas.

Cada TDC exige `station_id`, `epoch_utc_s` inteiro, `epoch_fraction_ps` inteiro
em [0,10¹²), `time_ps` inteiros relativos ordenados, `detector` em [0,3],
`clock_reference`, `clock_offset_ps`, `clock_uncertainty_ps`,
`propagation_delay_ps` e `lost_tags`. Mapeamento canônico: 0=Z0, 1=Z1, 2=X0,
3=X1; o gateway mapeia seus canais reais/calibração para esses IDs.
O relógio/PPS precisa ter referência conhecida; TDC relativo não é UTC.
`flight_time_ps` opcional permite correção calibrada por tag, de igual comprimento,
com `delay_reference`. Correção uniforme usa `propagation_delay_ps`.
Não use Unix timestamp em ponto flutuante ps. Cada OGS admite até 100 mil tags
por bloco; overflow aborta, sem truncamento silencioso. A fonte simulada também
limita a 100 mil candidatos por OGS antes do filtro de tempo morto, evitando
alocar blocos enormes antes da recusa; ajuste cadência/taxa ao testar alta carga.
Janelas APT limitam quais tags podem participar de coincidências. A associação é por sessão/tempo/canais;
o gateway precisa mapear o UTC da missão para a época do relógio operacional.

O tracking pode incluir `additional_channel_loss_db` em [0,1000] dB, acompanhado
de `loss_reference` não vazio. Esse valor representa a perda adicional sobre os
parâmetros ópticos cadastrados, com origem medida ou hipótese explicitada. O
processador incorpora a informação ao orçamento de perdas para qualquer origem.
No cenário degradado, a fonte registra os 12 dB adicionais utilizados na geração
dos cliques; o orçamento exibido reflete essa mesma degradação.

O modo atual é observação em shadow do relógio operacional, não agendamento de
hardware em tempo real. Um deployment da missão deve qualificar seu sincronismo,
cadência e ligação UTC/rede; acelerar a demonstração não acelera um satélite.

Arquivos v1 com contagens agregadas permanecem legíveis para compatibilidade com
o histórico. Seus orçamentos assimptóticos não comprovam a recepção nem o
pós-processamento OGS v2. A fonte simulada nova usa tags v2; o listener exige os
três streams v2 e não aceita esses arquivos agregados como substituição.

## Física e hipóteses

| Parâmetro/modelo | Unidade / valor padrão | Interpretação |
|---|---|---|
| Órbita hipotética | 500 km, inclinação 60° nos cenários existentes | Não é TLE de Micius nem garantia de cobertura |
| Emissão | 5×10⁶ pares/s | Fonte Poisson configurável; não taxa final de chave |
| Divergência gaussiana | 10 µrad, meia abertura do raio 1/e² | Não confundir com divergência total |
| Detector | eficiência 0,8; jitter σ=50 ps | Hipótese; não especificação validada de um produto |
| Inserção / perdas extras | 2 dB / 0 dB por braço | Atenuam a detecção; cenário degradado acrescenta 12 dB |
| Background / dark | 500 / 20 contagens/s **por detector** | Quatro detectores por OGS; eventos Poisson |
| Tempo morto | 50 ns por detector | Modelo não paralisável; afterpulse configurável |
| Janela de coincidência | 1 ns total | Matching 1:1; gates ambíguos são descartados |
| Atmosfera | profundidade óptica de zênite 0,2 | Aproximação plano-paralela; nuvens bloqueiam |
| Turbulência / AO | Cn²=0 por padrão; camada constante configurável | Fried/Strehl apenas para SMF; AO reduz variância conforme hipótese, sem ganho garantido |
| Cintilação | σ lognormal=0 por padrão | Quando ativa, altera detecção, não valores exibidos prontos |
| APT / Doppler | atraso e limites de ajuste configuráveis | Filtro penaliza residual; PAA usa 2v_transversal/c para beacon recebido→emissão |
| Polarização | rotação óptica parametrizada, erro residual 0,01 rad | Fonte simulada identificada; não orientação universal deduzida apenas da órbita |
| Integração óptica | passos ≤250 ms; limiares de elevação e janelas resolvidos | Orçamento óptico constante por intervalo; dinâmica reduzida identificada |
| Propagação | tempo de voo contínuo por tag, interpolação Hermite da distância/velocidade radial | Correção calibrada separada; erro de distância configurável altera coincidências |

O perfil de cada dispositivo prevalece sobre parâmetros compartilhados; altere
os campos do componente para mudar um receptor específico. O cenário degradado
também aumenta jitter e erro óptico. Nós distantes sem visibilidade simultânea
continuam indisponíveis. Distância geográfica não substitui distância oblíqua.

A correção do tempo de voo usa a distância evoluindo durante o bloco; não arredonda
o atraso a um valor fixo de 250 ms. `propagation_compensation` permite testar sua
ausência e `range_estimation_error_m` injeta incerteza gaussiana de calibração.
O valor ideal zero é uma hipótese da demonstração, não precisão comprovada de
uma missão. Tags físicos exigem calibração de relógio, distância e atraso
instrumental fornecida pela OGS; a simulação não qualifica sincronização subns.

Em Órbita, `sgp4_tle` exige duas linhas com checksum válido e TLE dentro da política
de idade (padrão 3 dias, configurável). A transformação TEME→ECEF usa GMST,
UT1=UTC e movimento polar zero. Para rastreamento preciso, forneça vetores ECEF da
missão/EOP. `state_vectors` exige amostras ordenadas com posição/velocidade e
tempo da rede; não extrapola fora da cobertura. O mapa mostra indisponibilidade
quando falta cobertura. Kepler/reference continuam identificados como hipóteses.

Vetores ECEF recebidos e órbitas SGP4/state_vectors são convertidos para latitude
geodética e altitude elipsoidal WGS84 (a=6.378.137 m; 1/f=298,257223563). A posição
recebida alimenta o GEO pelo mesmo fluxo para fontes físicas e simuladas, dentro
de sua validade. Modelos circulares/Kepler legados conservam a aproximação
geocêntrica de raio médio 6.371 km, identificada em `geographic_reference`.
O propagador cadastrado ainda precisa ter parâmetros e cobertura válidos; a
recepção OGS não substitui a configuração orbital exigida pela topologia.

## Pós-processamento e limites de segurança

BBM92 distribui pares entre duas OGS; não é a arquitetura WCP decoy BB84 de
Liao/Jinan-1 nem o TF-QKD com interferência em Charlie. O pipeline combina tags,
seleciona bases, remove X para amostragem, calcula QBER, executa paridades com
look-back, verificação universal e hashing Toeplitz. O orçamento finito inclui
flutuação Serfling, vazamento público observado, tag e margem PA. Blocos pequenos,
erros altos, falta de parâmetros ou verificação malsucedida abortam.

O teste `finite-laboratory` exercita todas as etapas a partir de cliques gerados:
6 s, fonte de 100 mil pares/s, receptores hipotéticos de 6 m e erro óptico 0,001.
Esse perfil serve para obter amostras suficientes dentro do buffer de software;
não descreve uma missão existente e não garante chave. As janelas menores do
perfil padrão podem abortar por falta de amostra. Somente etapas executadas
geram recibos de execução; amplificação omitida registra o motivo de aborto.

O limite é **condicional** à independência/uniformidade das bases, modelo de
detectores, fontes aleatórias e autenticação dos endpoints. Seeds públicos
capturados permitem replay; a fonte simulada usa PRNG reproduzível, não QRNG
qualificado. A implementação central observa os bits e não é um endpoint QKD
distribuído certificado. Não substitui uma prova composável revisada, não fornece
chaves utilizáveis e mantém `certified_secret_bits=0`. Protocolos decoy BB84/CV
exigem adaptadores e provas específicos; não são inferidos de um cadastro BBM92.

Exportar recibos gera um arquivo adicional `.satellite-acquisitions.jsonl` com
aquisições completas, mantidas separadas dos snapshots da GUI. Referências SHA-256
permitem verificar origem e conteúdo; os hashes locais não substituem assinatura
ou âncora externa. Aquisições de demonstração convertidas para testar o transporte
não são evidência de recepção de um satélite real.

## Fontes consultadas em 2026-10-06

- [Yin et al., Science 2017](https://arxiv.org/abs/1707.01339): pares entrelaçados, APT e orçamento experimental de duas pernas; parâmetros de ORION são hipóteses separadas.
- [Liao et al., Nature 2017](https://arxiv.org/abs/1707.00542): WCP decoy BB84; não substitui BBM92.
- [Li et al., Jinan-1, versão 2024](https://arxiv.org/html/2408.10994v1): canais ópticos compartilhando caminho/WDM, com funções clássica/quântica distintas.
- [Han et al., demonstração de point-ahead](https://arxiv.org/abs/1912.06317): convenção beacon recebido/emissão e velocidade transversal.
- [Tomamichel et al., 2012](https://www.nature.com/articles/ncomms1631), [Tomamichel e Leverrier, 2017](https://arxiv.org/abs/1506.08458): amostragem, autenticação, reconciliação e privacidade; sua prova não certifica automaticamente este código.
- [Vallado et al., SGP4](https://celestrak.org/publications/AIAA/2006-6753/), [biblioteca sgp4](https://pypi.org/project/sgp4/): vetor de referência e distinção TEME/ECEF.
- [NGA — WGS84](https://earth-info.nga.mil/index.php?action=wgs84&dir=wgs84): constantes do elipsoide usadas na conversão ECEF/geodética.
- [Swabian Time Tagger](https://www.swabianinstruments.com/static/documentation/TimeTagger/InDepthGuides/RawTimeTagStreamAccess.html): TDC relativo, buffers e perda de tags; nenhum driver desse equipamento foi validado aqui.
- [WebSocket servidor oficial](https://websockets.readthedocs.io/en/stable/reference/sync/server.html): limites, autenticação no handshake, origem e ciclo de vida.
- [Marulanda Acosta et al.](https://arxiv.org/abs/2111.06747): AO, acoplamento e atmosfera; a aproximação Cn² constante não reproduz integralmente o estudo.

Permanecem externos: ICD/RF/TDC da missão, calibração instrumental, EOP e órbita
qualificada, identidade/autenticação distribuída, segurança composável e campanha
medida comparando simulação a capturas reais. A ausência desses dados impede
afirmar prontidão operacional de um satélite real.
