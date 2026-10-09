# Ponte shadow do ator de taxa QKD — 24/09/2026

Incremento Q16 em código-fonte para `qkd.keyrate.actor`: recebe observáveis
simulados decoy-state BB84, valida contexto e tempo, e registra uma recomendação
comparada com manter os parâmetros atuais. Não aplica configurações, não chama
executores nem escreve no journal de comandos. O modelo continua opcional e OFF
por padrão; OFF não lê a entrada nem o ZIP.

API: `evaluate_optional_keyrate(snapshot, model_id=None, archive_path=None)`.
O método `ControllerRuntime.observe_optional_keyrate(...)` delega ao mesmo
avaliador, fora de `cycle()`. O [contrato offline](orion_q_optional_keyrate.md)
continua definindo as 16 features, nove ações e limites numéricos. A ponte não
altera a inferência, a normalização treinada ou a semente PCG64.

## Janela e identidade

Campo `NetworkStateSnapshot.telemetry.optional_keyrate_window`, esquema
`orion.sandbox-keyrate-window.v1`. `KeyRateShadowWindow.from_dict()` valida e
congela o JSON; `to_dict()` devolve uma cópia independente. Campos extras são
recusados. Snapshot completo limitado a 128 KiB; entrada offline a 64 KiB.

| Campo | Contrato |
|---|---|
| `schema`, `evidence` | Esquema acima e `simulated`; proveniência também simulada, integridade declarada verificada, flag OOD falsa. |
| `source_id`, `node_id` | Fonte da proveniência e nó do snapshot; o nó deve ser o transmissor do enlace. |
| `campaign_id`, `clock_id`, `stream_id` | Coincidem com `metadata.campaign_id`, `clock_id`, `keyrate_stream_id`. |
| `origin_timestamp_utc` | Origem dos segundos relativos do replay; coincide com `metadata.keyrate_origin_timestamp_utc`. |
| `decision_timestamp_utc` | Mesmo instante do timestamp do snapshot, igual ou posterior à origem. |
| `seed` | Inteiro uint32 explícito para PCG64; não há substituição por seleção gulosa. |
| `observation_mode` | Exatamente `calibration_bootstrap` ou `previous_period`. |
| `units` | Mapa exato: intensity=`mean_photons_per_pulse`; probability/gain/error/yield=`fraction`; clock=`Hz`; key_rate=`bit/s`; time=`s`; distance=`km`. |
| `context` | Enlace, parâmetros atuais e calibração descritos abaixo. |
| `previous` | Período anterior explícito, ou `null` apenas no início por calibração. |

Todas as datas exigem fuso horário. Não há conversão de WDM, SO(3), alocação,
roteamento ou fidelidade genérica em observáveis decoy-state. Os hashes vinculam
conteúdo declarado; não autenticam o produtor nem certificam o domínio.

## Contexto

`context` exige exatamente:

- `link_id`, `transmitter_id`, `receiver_id`: identificadores não vazios;
  endpoints distintos, transmissor igual a `node_id`;
- `protocol`, `link_span_km`: `decoy_state_bb84`, extensão declarada entre 25 e
  80 km, inclusive;
- `settings_version`: inteiro de 0 a 2⁶³−1, igual a `snapshot.state_version`;
- `settings_timestamp_utc`, `settings`: início declarado de vigência e os cinco
  campos do contrato offline (`signal_intensity`, `decoy_intensity`,
  `vacuum_probability`, `signal_probability`, `clock_rate_hz`);
- `calibration_id`, `calibration_timestamp_utc`, `calibration_valid_until_utc`:
  identidade e intervalo explícito de validade da calibração;
- `calibration`: os cinco campos offline (`calibration_gain`,
  `calibration_rate_hz`, `dark_count_probability`, `misalignment_error`,
  `dead_time_s`). O tempo morto admitido pelo export permanece 50 ns.

O SHA-256 do JSON canônico de `context` (UTF-8, chaves ordenadas, sem espaços)
coincide com `metadata.keyrate_context_sha256`. Configuração e calibração devem
estar disponíveis até a decisão; calibração deve expirar **depois** dela.
A validade é declarada pelo produtor, não inferida dos pesos. Os campos legados
`key_rate_hz` e `calibration_rate_hz` representam taxa de chave em bit/s neste
contrato; `clock_rate_hz` representa frequência de pulsos em Hz.

## Causalidade e início por calibração

Em `calibration_bootstrap`, `previous` deve ser `null` e a decisão deve coincidir
com a origem do relógio. O registro identifica essa origem das features. Não há
conversão silenciosa de observáveis ausentes ou inválidos em início por calibração.

Em `previous_period`, `previous` exige exatamente `start_timestamp_utc`,
`end_timestamp_utc`, `settings_sha256`, `observables`. O início é igual ou posterior
à origem, o fim é posterior ao início e não posterior à decisão. A idade entre
fim e decisão deve ser de 0 a 1 s, inclusive. Configuração e calibração devem ter
sido disponibilizadas até o início do período. `settings_sha256` é o hash canônico
dos mesmos `context.settings` usados na referência de manter parâmetros.

`observables` contém os oito campos offline: `signal_gain`, `decoy_gain`,
`vacuum_gain`, `signal_error`, `decoy_error`, `single_photon_yield_lower`,
`single_photon_error_upper`, `key_rate_hz`. Ganhos, erros e limites são frações;
taxa anterior é finita e não negativa. A ponte não calcula esses estimadores a
partir de contagens brutas nem verifica uma prova de segurança.

O limite de idade, o início na origem e a exigência de mesmos parâmetros são
regras conservadoras deste adaptador de replay, não limites aprendidos, prazos
físicos ou comprovação de frescor no relógio de parede. Períodos obtidos sob outra
configuração são recusados neste contrato; não há remapeamento implícito.

## Resultado e uso

O registro inclui snapshot/hash, contexto/hash, entrada/hash, identidade do ZIP,
versão NumPy, origem dos observáveis, semente, features, logits, probabilidades,
índices amostrado/guloso, nove prévias limitadas e referência de manter parâmetros.
A janela e o registro são imutáveis. As configurações do snapshot permanecem iguais.

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
python -m quantum_autopilot.control_plane.optional_keyrate `
  --input keyrate-snapshot.json --model qkd.keyrate.actor `
  --qkd-zip C:\Users\alexa\Downloads\modelo_qkd_ml.zip `
  --output keyrate-shadow-new.json
```

Sem `--model`, exporta OFF sem ler a entrada. Códigos de saída: 0 para OFF ou
avaliação, 2 para recusa/indisponibilidade. Exportação exige destino novo. O ZIP
externo é opcional e verificado pelo leitor existente, sem executar seus scripts
ou carregar o detector pickle. [Resultados e reprodução](../results/orion_q_keyrate_shadow_20260924.md).

Diferenças de parâmetros não demonstram diferenças de desempenho. Probabilidades
da política não são probabilidades de sucesso; não há previsão da próxima taxa,
chaves geradas, certificado de chave finita, fidelidade de Bell ou ganho operacional.
O [produtor artificial e a sessão gráfica shadow](../results/orion_q_keyrate_shadow_ui_20260926.md)
foram concluídos em fonte em 26/09, conforme a seção seguinte. Q05 permanece
pendente; nenhum executável foi reconstruído ou dispositivo acionado.

## Sessão gráfica shadow — 26/09

Em EDGE CONTROL, selecione `qkd.keyrate.actor`, confira o ZIP por VERIFY LOCAL ZIP
e abra QKD KEY RATE · LIVE ARTIFICIAL SHADOW. A janela inicia OFF. START SHADOW
exige um snapshot artificial válido em `Snapshot.optional_keyrate_state`; não usa
telemetria medida ou campos de outras pontes como substitutos. Receber um
snapshot valida a entrada para habilitar START, sem carregar pesos ou avaliar o ator.

`SyntheticKeyRateStream` publica uma fixture decoy-state de 50 km com parâmetros
fixos. Cada publicação consecutiva avança 1 s virtual, independentemente da
pintura da tela. O primeiro frame é `calibration_bootstrap`; os seguintes contêm
observáveis artificiais do intervalo desde a publicação anterior. Há uma taxa
anterior zero a cada seis intervalos para exercitar o contrato numérico. Nenhuma
recomendação altera parâmetros, observáveis, relógio ou chave disponível.

Perda de fonte, salto/regressão de tick, mudança de campanha, adaptador, semente
ou nó inicia um novo segmento. A origem passa a ser o instante dessa publicação,
com bootstrap explicitamente identificado, sem preencher períodos perdidos.
A calibração da fixture tem validade de um dia virtual; seu vencimento também
inicia um novo segmento. Isso não representa recalibração de equipamento.
A versão dos parâmetros permanece constante enquanto os parâmetros não mudam;
não é um contador de observações.

A sessão e o catálogo medem frescor pelo fim do período observado (pela decisão
no bootstrap). Duplicata, payload alterado no mesmo instante ou nova decisão sobre
o mesmo período não renova frescor. Ausência de período novo por 3,5 s interrompe
a avaliação; retomada exige entrada nova e START. Alterações de enlace, parâmetros,
versão, calibração, relógio ou fonte invalidam a sessão e exigem reabertura pelo
catálogo. O hash de contexto é conferido novamente ao abrir o payload em cache.

Há um único worker, sem fila de frames. Novos frames descartam resultados antigos;
STOP, encerramento, perda de contexto e entradas inválidas impedem uso tardio.
EXPORT FRAME captura o resultado antes do seletor: um período novo válido não
substitui a seleção; perda de contexto cancela a exportação. Destino existente
é preservado. A janela mostra origem das observações, semente, ação amostrada,
ação gulosa e parâmetros recomendados versus manter os atuais. O JSON preserva
as nove prévias e toda a proveniência.

Os limites científicos da ponte continuam aplicáveis. Este produtor não modela
resultados das ações e não mede vantagem operacional. As dez configurações
admitidas agora têm sessões shadow artificiais em fonte; o catálogo continua
opcional, e o detector e Q05 permanecem pendentes.
