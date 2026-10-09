# Efemérides públicas e limites de integração satelital

Em 07/10/2026, o ORION-Q recebeu um adaptador assíncrono de OMM JSON do CelesTrak. Ele valida identidade, época, parâmetros, unidades, referencial TEME, UTC e teoria SGP4; conserva resposta original, URL e hashes, propaga a órbita e publica vetores ECEF com cobertura explícita no contrato existente `state_vectors`. Assim, dados públicos alimentam os mesmos cálculos de posição/geometria do backend. Não são housekeeping, detecções TDC, lock de rastreamento ou evidência de enlace quântico.

## Recursos públicos examinados

| Recurso | Uso e comprovação local | Limite |
| --- | --- | --- |
| [CelesTrak OMM/GP](https://celestrak.org/NORAD/documentation/gp-data-formats.php) | Resposta pública ISS/ZARYA, NORAD 25544, recebida por HTTPS; validada e propagada com SGP4 | Elementos ajustados/predições, sem payload ou telemetria bruta |
| Consulta NORAD 41731, Micius/QESS | Resposta HTTP 404 registrada em `public_fetch_probe.json` | Esta consulta não forneceu OMM atual; não prova ausência de missão ou serviço |
| [NASA ILRS CPF](https://ilrs.gsfc.nasa.gov/data_and_products/formats/cpf.html) | Formato oficial de predições identificado como possível fonte futura | Parser CPF não implementado; predição de ranging não é aquisição QKD |
| [SatQuMA / Zenodo 8101679](https://zenodo.org/records/8101679) | Arquivo público de referência v1.1.0 baixado, tamanho/hashes/listagem preservados | Saídas de simulação; não dados experimentais de satélite nem calibração do BB84 local |

A [política CelesTrak](https://celestrak.org/usage-policy.php) orienta consulta no máximo a cada atualização de duas horas e suspensão após erro. O adaptador usa um único worker, compartilha o future de consultas simultâneas, timeout de rede e resposta limitada a 256 KiB. Intervalo mínimo de duas horas, cache atômico com hash e validade padrão de três dias evitam polling em cada tick. Erro não gera fallback fictício: requer revisão/acknowledgement explícito e respeito ao intervalo antes de nova tentativa. Esse cache não é um coordenador distribuído entre instalações independentes.

## Execução

```powershell
.audit-winvenv\Scripts\python.exe tools\validate_public_satellite.py --catalog-id 25544 --output outputs\public_iss_validation --latitude -22.954 --longitude -43.167 --altitude-m 20
```

O comando inclui `src` por caminho absoluto e não exige ajuste manual de `PYTHONPATH`. Precisa de `sgp4`, `numpy` e rede HTTPS para a primeira consulta. Um cache válido dispensa novo download. `report.json`, `orbit.json` e `predicted_pass_samples.json` distinguem predição orbital de payload físico. O limiar geométrico de 10° não comprova alinhamento, condições atmosféricas ou fonte de fótons.

O serviço Python é `PublicEphemerisSource(catalog_id, cache_root)`: `refresh()` assíncrono, `snapshot()`, `state(utc)`, `sample_orbit(utc, duration_s, step_s, stations)` e `close()`. Depois de validar o cadastro do comunicador, o objeto orbital retornado pode ser usado no seu campo `orbit`, tipo `state_vectors`. A origem e os hashes devem acompanhar o cadastro/recibo. Não há alteração automática de cadastros ou ativação de enlaces ao baixar elementos; o CLI exporta a configuração para revisão. SDK, endpoints autorizados e credenciais de uma OGS continuam sendo dependências separadas.

SGP4 produz posição/velocidade TEME. A conversão existente usa GMST, UT1 aproximado por UTC e movimento polar nulo; latitude/longitude usam WGS84. Essas aproximações estão identificadas e não qualificam apontamento de precisão ou sincronismo em picossegundos. A cobertura de `state_vectors` é limitada aos instantes amostrados; fora dela a posição é recusada, sem extrapolação ou troca para órbita hipotética.

## Evidência de 07/10/2026

A resposta ISS tem época `2026-10-06T23:34:32.785824`, 420 bytes e SHA-256 `0ec5e976dc9142ec277b23a04689ce80bfd22d80e9c281d42a2b1f6352f83dbe`. O primeiro probe HTTPS foi reaproveitado no cache para evitar novo download dentro de duas horas; `retrieved_at_utc` registra a importação no cache, com vínculo ao probe. Foram propagadas 181 amostras em 90 minutos e calculada a geometria para a estação de referência no Rio. Nenhuma amostra ultrapassou 10° nessa janela; não se fabricou uma passagem para apresentar conexão.

As fixtures verificam aquisição assíncrona, uma consulta compartilhada, cache, campos inválidos, erro/staleness, identidade, cobertura e adulteração, além da concordância SGP4 com os vetores no backend. A ISS é apenas referência de dinâmica orbital e não é cadastrada como satélite quântico operacional.

O que continua exigindo fonte externa autorizada: engenharia/housekeeping, apontamento medido, clock e calibração OGS, configuração óptica medida, logs de preparação/análise e detecções TDC. Os canais clássicos de engenharia, beacon/APT e payload óptico continuam separados. Um OMM público não fornece esses dados.
