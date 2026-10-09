# ORION-Q — teste com telemetria satelital pública real, 8 de outubro de 2026

**Conclusão: a recepção operacional de um satélite real ainda não está qualificada de ponta a ponta.** Foi comprovado o download de dois quadros recentes de rádio recebidos por uma estação externa, a interpretação de campos de engenharia por um parser público específico e o comportamento do ingresso autenticado do ORION-Q diante desses registros. O backend exige observações OGS normalizadas; não possui nesse caminho captura RF, demodulação ou um adaptador Suomi100. A prova não recebeu fótons nem dados de uma missão QKD.

O usuário autorizou fontes públicas pela internet. Os ensaios utilizaram estado isolado, sem alteração de produção, configuração do aplicativo ou release. Não houve POST a provedores, submissão de telemetria, comando a equipamento ou geração de chave operacional. O processo do usuário não foi controlado. Os novos scripts e evidências estão em `outputs/satellite_live_probe_20261008/`.

**Recepção real externa e proveniência.** O [registro SatNOGS da observação 15146588](https://network.satnogs.org/api/observations/15146588/) identifica **Suomi 100, NORAD 43804**, estação **CIA_S4 / 5112**, Taiwan, janela `2026-10-08T13:51:12Z–13:59:04Z`, downlink 437,775 MHz, 9600 baud, modo publicado `MSK AX.100 Mode 5`. O [catálogo da missão](https://db.satnogs.org/satellite/43804/) a identifica como operacional em órbita. O YAML do decoder informa `AX100 ASM+Golay`; demodulação e FEC anteriores ao arquivo publicado não foram executados localmente.

| Registro | Recepção publicada UTC / Bahia | Download UTC | Tamanho | Idade no download |
|---|---|---|---:|---:|
| [Quadro 1](E:/Codex/2026-08-27/e/work/NISQ-Predictive-Twin/outputs/satellite_live_probe_20261008/research/suomi100-frame-20261008-135420.bin) | 13:54:20 / 10:54:20 | 14:01:31,576297 | 96 bytes | 431,576 s |
| [Quadro 2](E:/Codex/2026-08-27/e/work/NISQ-Predictive-Twin/outputs/satellite_live_probe_20261008/research/suomi100-frame-20261008-135520.bin) | 13:55:20 / 10:55:20 | 14:01:30,623341 | 96 bytes | 370,623 s |

Esses horários de recepção são declarações do publicador, obtidas nos nomes dos arquivos associados à observação. O horário local de download foi registrado separadamente. São recepções recentes disponibilizadas por terceiros, não um stream RF capturado neste computador. Os dois GETs retornaram HTTP 200; URL, headers, tamanho e SHA-256 foram preservados nos respectivos `.http.json`.

SHA-256 dos bytes originais:

```text
13:54:20  4ae28a9e2a3871c7fe4d2fa7ee70ca253261cd377414f55731cbc7d4515fbc66
13:55:20  464560d47534bebdfe99455aba43aa2c525ec000fbbcc2075136ed473b844d14
```

**Decode executado e limites.** Foi executado sem alterações o [parser Suomi100 do gr-satellites, commit cb5d0fca22ed439e9ce029516b755491a57c5764](https://raw.githubusercontent.com/daniestevez/gr-satellites/cb5d0fca22ed439e9ce029516b755491a57c5764/python/telemetry/suomi100.py), com CSPHeader e UNIXTimestampAdapter. Construct 2.10.70 foi importado diretamente de um wheel arquivado, sem instalação na venv. GNU Radio não foi importado. Esse parser externo não foi integrado ao núcleo nem ao executável do ORION-Q.

| Campo bruto interpretado | Quadro 1 | Quadro 2 | Qualificação |
|---|---|---|---|
| EPS `batt_v` | 7295 | 7285 | Inteiro; escala física não estabelecida |
| EPS `batt_mode` | 3 | 3 | Código bruto |
| COM `temp` | [112, 111] | [106, 104] | Inteiros; não promovidos a °C |
| COM `rssi` | −114 | −117 | Inteiros; não promovidos a dBm |
| EPS/COM/OBC relógio pelo parser UNIX | 2033-10-06 19:21:00 UTC | 2033-10-06 19:22:00 UTC | Incompatível com recepção publicada de 2026 |

A diferença temporal é constante, **220771600 s**. Pode decorrer de formato, versão ou configuração da missão; a causa não foi estabelecida. Não se afirmou defeito do relógio do satélite, não se corrigiu época por suposição e não se usou o horário interno como observação calibrada. Sem ICD e escalas confirmadas, um parse estrutural não qualifica valores físicos.

O parser consome 88 dos 96 bytes e não verifica os oito finais, apesar de `header.crc=true`. Uma checagem independente, separada do parser e da produção, verificou **dois escopos observados de CRC32C** em ambos os arquivos: `[4:88]` contra `[88:92]` e `[0:92]` contra `[92:96]`, com fim exclusivo e armazenamento big endian. Os parâmetros seguem [gr-satellites/crcs.py](https://github.com/daniestevez/gr-satellites/blob/main/python/crcs.py); as coberturas com/sem cabeçalho estão na [implementação libcsp arquivada no commit 583c9ebc23b9193c2c6800fdff6a182e13df03e7](https://raw.githubusercontent.com/libcsp/libcsp/583c9ebc23b9193c2c6800fdff6a182e13df03e7/src/csp_crc32.c). O vetor conhecido `123456789 → e3069283` passou.

Não foi estabelecida a razão das duas camadas de checksum. O arquivo inteiro de 96 bytes não foi declarado um frame CSP1.x canônico: `[4:92]` não confere com o último checksum. O gate é específico dessas amostras Beacon0 e não aceita genericamente toda a missão. CRC detecta alterações acidentais; não autentica o satélite ou a estação. A pesquisa preserva fontes, commits, licenças e resultados em [public_telemetry_findings.md](E:/Codex/2026-08-27/e/work/NISQ-Predictive-Twin/outputs/satellite_live_probe_20261008/research/public_telemetry_findings.md) e [suomi100-decode-report.json](E:/Codex/2026-08-27/e/work/NISQ-Predictive-Twin/outputs/satellite_live_probe_20261008/research/suomi100-decode-report.json).

**Matriz dos casos executados.** Aprovação de uma guarda de rejeição significa bloqueio correto; não significa que o registro incompatível foi integrado.

| Caso de uso | Evidência observada | Resultado / alcance |
|---|---|---|
| Obter telemetria recente de um satélite em órbita | Dois arquivos Suomi100 da observação de hoje, HTTP 200, hashes preservados | Comprovado por publicação externa; sem RF local |
| Interpretar engenharia dos dois quadros | CSP e Beacon0 EPS/COM/OBC, round-trip exato dos 88 bytes interpretados | Decode estrutural externo executado; unidades/clock pendentes |
| Verificar integridade das duas amostras | Dois escopos CRC32C conferem por arquivo | Integridade de checksum observada; sem autenticação de missão |
| Truncar estrutura para 87 bytes | Parser rejeita `SelectError`; gate rejeita | Bloqueio confirmado |
| Alterar discriminador para 255 | Parser rejeita `SelectError`; gate rejeita | Bloqueio confirmado |
| Alterar um bit do campo de engenharia | Parser aceita valor modificado; gate rejeita | Parser sozinho é insuficiente para integridade |
| Alterar um bit do trailer | Parser aceita; gate rejeita | Parser sozinho é insuficiente para integridade |
| Remover os oito bytes finais | Parser aceita 88 bytes; gate rejeita | Consumo parcial explicitamente identificado |
| Acessar status OGS anonimamente | HTTP 401 | Bloqueio confirmado |
| Usar identidade inválida | HTTP 403 | Bloqueio confirmado |
| Abrir conexão com Origin de navegador | HTTP 403 | Bloqueio confirmado |
| Procurar endpoint de RF no ingresso OGS | `/ogs/v2/rf` retorna HTTP 404 | Esse ingresso não é um decoder RF |
| Consultar status com sessão autorizada no cofre | `orion.ogs-status.v2`, origem `measurement_file`, sem janelas | Transporte digital autenticado funcional |
| Enviar cada frame CSP binário ao stream HK | Ambos retornam `invalid_packet` | Formato incompatível corretamente rejeitado |
| Enviar cada JSON do parser diretamente | Ambos retornam `wrong_stream` | JSON da missão não é pacote OGS |
| Acrescentar apenas `kind=housekeeping` ao JSON real | Ambos retornam `invalid_packet` | Não substitui contrato nem observações ausentes |
| Revogar identidade com conexão aberta | Próxima mensagem retorna `authentication_required` | Revogação aplicada por mensagem |
| Conferir persistência após esses envios | 0 pacotes aceitos, 0 pendentes, 0 linhas em `ogs_packets` | Nenhuma telemetria incompatível promovida a aquisição quântica |
| Conferir auditoria e encerramento | Cadeia/MAC válida, 29 registros, credencial ausente, thread encerrada | Guarda e liberação do servidor verificadas |
| Propagar TLE público pela implementação ORION | SGP4/ECEF/geometria executados, TLE recente válido | Previsão orbital; não telemetria HK nem tracking medido |
| Consultar OMM CelesTrak para Suomi100 | Timeout de 10 s, estado `unavailable`, exit 2 | Dependência externa indisponível nessa tentativa; sem retries |
| Testar contratos/processamento/segurança já implementados | 69 testes distintos passaram em 47,50 s, sem skips | Fixtures e loopback de software; sem qualificação física |
| Conferir preservação da versão qualificada | 622 caminhos e hashes EXE/ZIP 5.53 conferem | Produção e artefatos declarados preservados |

Os **12 casos de ingresso** estão discriminados em [backend-public-probe-20261008T141627559189Z.json](E:/Codex/2026-08-27/e/work/NISQ-Predictive-Twin/outputs/satellite_live_probe_20261008/backend-public-probe-20261008T141627559189Z.json). O servidor utilizado é o código de produção `OGSWebSocketServer`, em localhost, com `SecurityRuntime`, identidade própria, escopo `source:satnogs_public_suomi100/*` e sessão protegida por `vault:`. O estado foi criado numa pasta nova. Origem `measurement_file` identifica o ensaio dos arquivos baixados; eles não foram reclassificados como aquisição física local ou simulação.

O contador `store.rejected=2` conta somente recusas que chegaram ao `OGSStore`; as recusas anteriores de parse/stream também estão nas respostas do probe. A auditoria registra `ingestion.receive/accepted` depois da autorização e **antes** da validação do schema. Esse registro significa recebimento autorizado, não admissão da observação. Respostas e journal foram conferidos para evitar interpretar essa semântica como sucesso de aquisição.

**Órbita e disponibilidade.** O TLE publicado na observação tem origem declarada Space-Track.org, época `2026-10-06T16:19:00.213312Z`. Foi passado diretamente ao backend, sem fingir ser um cache CelesTrak. Em `2026-10-08T14:08:34.128522Z`, com referência Salvador `−12,9714°, −38,5014°` e altitude elipsoidal **assumida** de 0 m, o ORION previu alcance **10962,521 km**, elevação **−54,648°** e azimute **14,644°**. O satélite estava abaixo do horizonte dessa referência naquele instante. Isso é compatível com recepção anterior em outra estação e não determina futuras passagens. Aproximações TEME/GMST, UT1=UTC e polar motion zero estão declaradas; não é apontamento óptico qualificado. [Parâmetros e resultado](E:/Codex/2026-08-27/e/work/NISQ-Predictive-Twin/outputs/satellite_live_probe_20261008/suomi100_satnogs_tle_prediction.json).

O teste CelesTrak não produziu OMM válido e não foi repetido. GRBAlpha/NORAD 47959 foi descartado como candidato atual: o [catálogo](https://db.satnogs.org/satellite/47959/) indica reentrada em 2025-06-09. As consultas iniciais e falhas foram preservadas, sem promover dados de outro NORAD ou arquivos históricos a recepção atual. A [política do provedor](https://celestrak.org/usage-policy.php) foi respeitada; não houve polling contínuo.

**Regressões de software.** Foram executados `tests/test_public_satellite_ephemeris.py` (19 casos), `tests/test_satellite_pipeline_v2.py` (45) e `tests/test_security_ogs.py` (5), com Python 3.12.14 da `.audit-winvenv`, `PYTHONPATH=src` e Qt offscreen. [XML](E:/Codex/2026-08-27/e/work/NISQ-Predictive-Twin/outputs/satellite_live_probe_20261008/focused-software.xml) e [log](E:/Codex/2026-08-27/e/work/NISQ-Predictive-Twin/outputs/satellite_live_probe_20261008/focused-software.log) conservam a execução. Os 69 casos não incluem as duas capturas públicas nem os 12 casos adicionais; as contagens não foram somadas como qualificação física.

**Prontidão por camada e pendências concretas.**

| Camada | Estado demonstrado | Condição ainda necessária |
|---|---|---|
| Internet → arquivos de uma estação pública | Funcionou para duas amostras recentes da missão identificada | Fonte autorizada e contratada para operação contínua; autenticação da origem conforme aplicação |
| Bytes → engenharia de Suomi100 | Parser externo funcional com limites identificados | ICD/versão, escalas/unidades, política de CRC/framing completo e relógio qualificado |
| Recepção RF → demodulação/FEC → bytes | Fora deste ensaio e do ingresso OGS | Gateway específico da missão, receptor ou gravação RF identificada e validação própria |
| Engenharia convencional → monitoramento ORION | Não há adaptador da missão nesse caminho | Adaptador e contrato de telemetria convencional sem fabricar campos quânticos |
| Gateway → ingresso OGS canônico | Transporte e validações testados em loopback | Dados normalizados completos, identidade autorizada e implantação TLS qualificada para estações remotas |
| HK/APT/TDC → processamento quântico | Fixtures passaram; material físico não recebido | Captura independente de duas OGS, emissão observada, tracking, TDC, bases e calibração/sincronismo reais |
| Cadastro pela interface | Incompatibilidade identificada | Corrigir referência de credencial e validar caminho completo da UI |
| Chave QKD operacional | Não qualificada; nenhuma chave foi gerada no ensaio público | Segurança experimental, dispositivos e missão qualificados; autorização e admissão correspondentes |

A incompatibilidade de cadastro é concreta: [satellite_telemetry.py](E:/Codex/2026-08-27/e/work/NISQ-Predictive-Twin/src/quantum_autopilot/console/widgets/satellite_telemetry.py:271) e o [guia OGS](E:/Codex/2026-08-27/e/work/NISQ-Predictive-Twin/docs/product/orion_q_satellite_ogs_v2.md) recomendam `env:ORION_OGS_TOKEN`, enquanto o caminho físico do [ingresso atual](E:/Codex/2026-08-27/e/work/NISQ-Predictive-Twin/src/quantum_autopilot/quantum_protocols/satellite_ingress.py:168) exige `vault:<referência de sessão>`. O engine usa origem física e runtime comum. Seguir a instrução exibida não abre esse receptor. Não foi aplicado um contorno nem alterada a origem para aparentar prontidão. A [auditoria do código](E:/Codex/2026-08-27/e/work/NISQ-Predictive-Twin/outputs/satellite_live_probe_20261008/audit.md) descreve a cadeia e as fronteiras.

O contrato quântico exige HK, tracking e TDC associados ao mesmo bloco/sessão/fonte e a dois pares de canais clássico/quântico. Telemetria de bateria de um CubeSat e órbita prevista não suprem locks ópticos, detecções, bases, perdas medidas ou contador de emissão. O receptor correto deve aguardar essas observações. Suomi100 foi usado para testar telemetria clássica real; não foi apresentado como satélite QKD.

**Reprodução sem novos downloads ou hardware.** A partir da raiz do repositório, este comando lê os arquivos preservados e cria outro estado/relatório com timestamp próprio:

```powershell
$env:PYTHONPATH = 'src'
& '.audit-winvenv/Scripts/python.exe' 'outputs/satellite_live_probe_20261008/backend_public_probe.py'
```

Para repetir o decode preservando a prova original, copie `research` para uma pasta nova antes de executar o script, pois ele grava seus JSONs no próprio diretório:

```powershell
$satelliteReplayPath = Join-Path (Get-Location) ('outputs/satellite_decoder_replay_' + (Get-Date -Format 'yyyyMMdd_HHmmssfff'))
if (Test-Path -LiteralPath $satelliteReplayPath) { throw 'Escolha um diretório novo' }
Copy-Item -LiteralPath 'outputs/satellite_live_probe_20261008/research' -Destination $satelliteReplayPath -Recurse
& '.audit-winvenv/Scripts/python.exe' (Join-Path $satelliteReplayPath 'decode_suomi100.py')
```

Para repetir os 69 testes, use `--basetemp` e `--junitxml` em novos caminhos. A propagação orbital depende do UTC da execução e poderá produzir outra geometria; os valores acima pertencem à amostra arquivada. Os bytes do decode e as respostas esperadas de incompatibilidade são reproduzíveis a partir das capturas preservadas.

**Preservação e limites da entrega.** A [verificação por hashes](E:/Codex/2026-08-27/e/work/NISQ-Predictive-Twin/outputs/satellite_live_probe_20261008/preservation.json) confere os 530 fontes, 82 recursos e 10 arquivos de build declarados no snapshot 5.53, além do executável e ZIP qualificados. Não representa um hash integral de todos os dados do operador. Não foi produzido novo `.exe`; os ensaios de ingresso executaram diretamente o backend Python correspondente à fonte preservada, sem acoplar o feed público à GUI do executável.

As fontes e capturas públicas estão arquivadas com proveniência. SatNOGS documenta dados CC BY-SA 4.0; os módulos gr-satellites são GPL-3.0-or-later, Construct e libcsp MIT. Nenhuma dessas dependências foi incorporada à distribuição. Recepção RF local, calibração de engenharia, fluxo público contínuo autenticado e integração de payload quântico permanecem fora da evidência obtida nesta execução.
