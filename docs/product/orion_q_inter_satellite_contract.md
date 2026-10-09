# Contrato de enlaces entre satélites e referência GEO

Revisão: 2026-10-07. O suporte é arquitetural e simulado/reproduzível por arquivo;
não existe adaptador físico de missão validado nem atuação de terminais.

## Operação implementada

`satellite_isl` registra `InterSatelliteAdapter` no mesmo `TechnologyRegistry` dos
demais protocolos. O percurso de uma sessão é exatamente `[satélite A, satélite B]`,
com um enlace explicitamente cadastrado e um par obrigatório de canais clássico e
quântico. A mera presença de dois dispositivos não estabelece um enlace.

O transmissor dispõe de fonte de pares emaranhados e mede um fóton localmente;
o parceiro atravessa o enlace óptico até o receptor. Ambos fornecem os carimbos
de tempo e os resultados de bases/detectores. `BBM92` é processado pelo mesmo
processador de time tags, sifting, estimativa finita, reconciliação por paridade,
verificação e orçamento de amplificação usado pelos enlaces satélite–solo.

Os pares medidos foram consumidos: `measurement_resources.stored_pairs = 0`.
Este driver não declara memória quântica orbital, armazenamento sem detecção,
BSM orbital, encaminhamento transparente nem chave certificada. Consequentemente,
uma cadeia solo–satélite–satélite–solo não constitui por si só uma sessão quântica
de ponta a ponta. Sua composição exige recursos e operações adicionais compatíveis.

## Envelope e observações

`ProtocolConfig` usa `technologies = ["satellite_isl"]`, dois endpoints, um `link_id`,
`satellite_orbits` com uma órbita por ID e `device_parameters` com parâmetros de
terminal por ID. `DeviceCommand.operation` é `isl_block`; o comando contém os IDs
dos dois canais, o instante de emissão, prazo, tentativa e duração do bloco.

A fonte entrega o envelope comum `Acquisition`. O campo `raw.isl_schema` é
`orion.inter-satellite-observation.v1`, contendo:

| Grupo | Conteúdo e validação |
| --- | --- |
| `housekeeping` | Dois estados por ID, ECEF em km, velocidade em km/s, instante, validade e fonte. Exige órbitas externas à Terra, mesma época e cobertura válida. |
| `tracking` | Dois registros APT com associação ao dispositivo, lock explícito e intervalos observados de disponibilidade. |
| `tdc` | Dois registros de UTC inteiro mais fração em ps, relógio calibrado, incerteza, time tags relativos inteiros, detector/base e correção de voo por tag. |
| `intervals` | Integração óptica por intervalos de até 250 ms, emissões, transmissão e causas de bloqueio. |
| `parameter_provenance` | Origem e hipóteses dos dispositivos, equações simplificadas e referências. |

Cada lote TDC aceita no máximo 100.000 eventos candidatos. Exceder o orçamento ou
declarar `lost_tags > 0` aborta o bloco, sem truncamento silencioso. O cenário deve
reduzir duração/taxa de fonte quando necessário. Tags descartados por dead time
do detector são contabilizados separadamente; representam uma perda modelada.

Resultados derivados como `qber`, `coincidences`, `final_fidelity`, chaves ou taxas
finais são recusados nas observações. O fluxo completo é aquisição → validação de
binding/época → sincronização → protocolo → envio clássico → recebimento clássico
→ métricas → recibo durável → interface. Não há métrica operacional publicada
antes da chegada do recibo clássico. Tentativas rejeitadas requerem novos comandos
e novas observações; nenhum recurso consumido é restaurado.

`MeasurementFileSource` reproduz o mesmo fluxo usando envelopes vinculados a
comandos. Um arquivo capturado da simulação continua identificado como simulado
em sua proveniência, mesmo quando a origem de leitura é `measurement_file`.
O WebSocket OGS existente não passa a aceitar ISL automaticamente: um gateway de
missão deverá normalizar os dois terminais neste contrato antes da integração.

## Geometria e orçamento físico

`geometry_at(parameters, endpoints, network_time_s)` retorna os estados dos dois
satélites e a geometria. `inter_satellite_geometry` testa o segmento de visada
contra o elipsoide WGS84, expandido pela margem de limbo configurada. Um raio que
atravessa a Terra ou tangencia a margem atmosférica não é um enlace de vácuo viável.

São verificados alcance de ambos os terminais, apontamento habilitado, campo de
visada do receptor, comprimento de onda compatível e o encoding suportado
`polarization_BBM92`. A velocidade transversal utilizada em point-ahead é inercial:
a rotação terrestre é restaurada sobre os vetores relativos em ECEF. A convenção
beacon recebido → apontamento futuro utiliza `2 v_transversal / c`.
`point_ahead_compensation` precisa estar habilitada ou o desvio calculado entra
no erro de apontamento/FoV; `point_ahead_calibration_error_urad` permite testar
compensação imperfeita. A compensação altera as detecções, não apenas a legenda.

O raio do feixe no receptor é `w ≈ θ L`; a captura de um feixe gaussiano por uma
abertura de raio `r` é `1 − exp(−2 r²/w²)`. A divergência efetiva nunca fica abaixo
do piso gaussiano ideal `λ/(π D_transmissor/2)`. O fator de apontamento é
`exp(−2 σ_apontamento²/θ²)`, com atraso de tracking, perdas ópticas/inserção e
filtragem Doppler aplicados separadamente. Eficiência do detector, ruído,
dark counts, dead time e jitter são aplicados na geração de eventos.

Não se aplica uma atmosfera fictícia a um ISL de vácuo. Eclipse/energia, exclusão
solar, atitude completa, perturbações orbitais e calibração de missão permanecem
dependências externas explícitas. Os parâmetros de telescópios grandes ou jitter
baixo da demonstração são hipóteses de engenharia, não capacidades comprovadas
das instituições nem de um satélite real.

## Métricas para backend e interface

Após o recebimento clássico, a sessão fornece `satellite_housekeeping[ID]`,
`isl_geometry`, `tdc_summary`, `emitted_pairs`, `coincidences`, `pair_rate_hz`, QBER,
etapas executadas/abortadas, fonte e origem da observação. `isl_geometry` inclui
visibilidade, obstrução terrestre, alcance, tempo de voo, margem do limbo,
apontamento, Doppler, orçamento em dB e `block_reasons`.

Os aliases `emitted_photons` e `detected_photons` referem-se, respectivamente, aos
parceiros produzidos para o braço remoto e aos cliques do receptor remoto; os
cliques incluem ruído e não equivalem a coincidências comprovadas. A incerteza
máxima dos relógios aparece como `sync_jitter_ps`. `certified_secret_bits` permanece
zero. Nenhuma métrica de detecção é um recurso quântico armazenado ou uma evidência
de integração física.

## Referência geoestacionária

`geo_reference_orbit(longitude, ...)` cria exclusivamente uma órbita simulada
`geostationary_reference`, equatorial, circular e síncrona. O raio é calculado como
`(μ/ω_Terra²)^(1/3)` usando as constantes do próprio propagador. A posição atual é
fixa em ECEF, com velocidade ECEF zero; a amostragem da órbita com orientação da
Terra congelada produz o anel completo para a animação do tracejado.

A altitude conhecida de aproximadamente 35.786 km é medida sobre o equador;
ORION conserva o modelo legado de raio médio de 6.371 km nas referências
hipotéticas, resultando em aproximadamente 35.793 km nesse referencial. Ambos os
referenciais são explicitados; o raio orbital e o período sideral são coerentes.

O novo modelo não altera TLE/SGP4, vetores medidos, referências LEO/MEO ou elementos
Kepler existentes. Também não simula manutenção orbital ou garante condições
operacionais contínuas: disponibilidade óptica, recursos, pointing e sincronização
continuam sendo avaliados. Variações reais exigem dados orbitais correspondentes.

## Fontes primárias e maturidade

Todas consultadas em 2026-10-07. As equações do modelo são aproximações declaradas;
nenhuma taxa ou alcance foi tratado como garantia experimental universal.

| Fonte | Data | Evidência e limite de aplicação |
| --- | --- | --- |
| [ESA — Types of orbits](https://www.esa.int/Enabling_Support/Space_Transportation/Types_of_orbits) | 2020-03-30 | Definição GEO: equador, período sideral e posição fixa em relação ao solo. Não valida terminais quânticos. |
| [ESA — European system speeds data flow with 50 000 links](https://www.esa.int/Applications/Connectivity_and_Secure_Communications/European_system_speeds_data_flow_with_50_000_links) | 2021-06-24 | Enlaces laser EDRS operacionais entre satélites e retransmissão clássica. Essa maturidade não implica QKD/entrelaçamento orbital. |
| [Dabiri et al. — Physical Limits of Entanglement-Based Quantum Key Distribution over Long-Distance Satellite Links](https://arxiv.org/abs/2506.20798) | 2025-06-25, v1 | Estudo teórico de perdas, apontamento, FoV e ruído em QKD entre satélites. Não é experimento de uma constelação GEO. |
| [Tomamichel & Leverrier — A largely self-contained and complete security proof for QKD](https://arxiv.org/abs/1506.08458) | 2015, versão publicada 2017 | Referência de amostragem finita usada pelo processador comum. A demonstração permanece condicionada às hipóteses de dispositivos/autenticação, sem certificação de missão. |

## Verificação

O cenário global novo registra quatro referências GEO simuladas, em −100°, −20°,
+20° e +120°, preservando as referências LEO/MEO anteriores. Os 11 percursos
satélite–solo e os quatro ISL são associações explícitas, não um grafo completo
gerado apenas pela presença dos dispositivos.

| Perfil de demonstração | Parâmetros escolhidos e hipótese |
| --- | --- |
| Downlinks GEO | 5 milhões de pares/s; 850 nm; 2 µrad de divergência; jitter de tracking 0,3 µrad; telescópio terrestre de 4 m; eficiência óptica 0,5 e detecção 0,8; inserção de 2 dB; profundidade óptica de zênite 0,2; elevação mínima 20°. |
| ISL GEO | 20.000 pares/s; detector local com eficiência 0,8; transmissor de 2 m e receptor de 4 m; divergência 0,4 µrad; jitter 0,05 µrad; tracking com atraso 1 ms; FoV 20 µrad; margem do limbo 80 km; alcance máximo configurado 90.000 km; compensação de point-ahead habilitada. |
| Detectores/tempo | Jitter 50 ps; background 1 contagem/s e dark counts 5 contagens/s por detector; dead time 50 ns no ISL; blocos 2 s e prazo 4 s. Background baixo representa condição filtrada favorável, não qualquer condição diurna. |

Esses são parâmetros deliberadamente exigentes para observar operações sem
garantir taxas positivas em todos os blocos. Telescópios de 4 m, baixa divergência
e estabilidade submicroradiana em órbita não foram validados como equipamentos
instalados. A taxa menor de fonte no ISL limita o volume de eventos do detector
local, preservando os limites do contrato; não adiciona sucesso final artificial.

A revisão independente da configuração realmente cadastrada, com sementes padrão
e 30 segundos virtuais, produziu 14 aquisições recebidas por sessão. Cada um dos
11 downlinks teve coincidências em alguma amostra; os máximos foram 1–3 por bloco
de 2 segundos. Isso é atividade experimental simulada muito esparsa, sem garantia
de sifting positivo ou orçamento de chave em cada bloco.

| ISL registrado | Distância recebida | Perda óptica | Maior número observado de coincidências em 2 s |
| --- | ---: | ---: | ---: |
| Américas → Atlântico | 54.205 km | 28,17 dB | 47 |
| Atlântico → Europa | 28.842 km | 22,79 dB | 150 |
| Europa → Ásia | 64.599 km | 29,69 dB | 40 |
| Ásia → Américas | 79.243 km | 31,45 dB | 24 |

As quatro longitudes permaneceram fixas durante uma semana virtual; todos os
campos `certified_secret_bits` ficaram em zero. A evidência está em
`outputs/global_network_20261007_v1/isl-geo-review/report.json`, com recibos e
aquisições brutas exportados. A contagem de snapshots positivos no relatório
não representa blocos adicionais: cada bloco recebido pode aparecer em dois
snapshots consecutivos.

`tests/test_satellite_isl_geo.py` cobre posição GEO fixa e anel animável, preservação
das outras órbitas, LOS/obstrução, alcance/FoV/apontamento, piso de difração,
compatibilidade, detecção nominal, perdas, jitter, compensação de voo, entrada
inválida, limites TDC, causalidade clássica, consumo, arquivo pelo pipeline comum,
retries e bloqueio da camada física associada. Os resultados ficam nos XMLs
`outputs/geo_global_network_20261007_isltest*.xml`.
