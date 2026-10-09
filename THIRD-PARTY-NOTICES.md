# Avisos de componentes e fontes de terceiros

A licença Apache 2.0 na raiz cobre o código próprio segundo os avisos do
projeto. Ela não substitui licenças dos componentes, dados ou fontes abaixo.
Os arquivos de procedência, atribuições, manifestos e hashes foram preservados.

| Material | Avisos preservados | Condições registradas no projeto |
| --- | --- | --- |
| Pacotes `qnet_network` e `qnet_qkd`, de A. O. S. Santos | `external/README.md`, `external/VENDORED.json`, `external/*/pyproject.toml`, `external/patches/README.md` | Os metadados do autor declaram MIT; cópias e alterações locais mantêm procedência e hashes. |
| Dados Cambridge/Dynes | `external/qnet_qkd/dados_cambridge/LEIAME.md` | CC BY 4.0, DOI 10.17863/CAM.44524; manter atribuição. |
| Fontes Inter, JetBrains Mono, Oswald, Noto Sans JP e Chakra Petch | `src/quantum_autopilot/console/fonts/*-OFL.txt` e `manifest.json` | SIL Open Font License nas famílias correspondentes. |
| Fonte Quantum fornecida pelo usuário | `src/quantum_autopilot/console/fonts/quantum-NOTICE.txt` | Avisos incorporados e referências `100% Free` preservados; não é relicenciada sob OFL ou Apache. |
| Mapas IBGE, OpenStreetMap, DNIT e Natural Earth | `src/quantum_autopilot/console/geo/SOURCES.md` | Atribuições e termos por camada, incluindo ODbL para dados derivados de OSM. |
| Frames públicos Suomi100/SatNOGS | `tests/fixtures/satellite_public/LICENSE.md` e `provenance.json` | CC BY-SA 4.0 para os frames; o decoder próprio não incorpora código GPL. |
| Agregados IrelandQCI/Toshiba | `evidence/tcc/v1.0.0/README.md` e `provenance.json` | CC BY 4.0, DOI 10.5281/zenodo.21132088. |
| Agregados Bristol HCF | Mesmos avisos do pacote de evidências | Non-Commercial Government Licence, DOI 10.5523/bris.61193flfovbn2puknndsr3qm5; uso acadêmico, de pesquisa e não comercial. O README da fonte solicita reavaliar os termos antes de redistribuição ou uso mais amplo. |

As referências científicas e as hipóteses de simulação permanecem em `docs/` e
nos manifestos de dados. A presença de uma instituição no cenário não representa
uma conexão operacional com o OrionQ. SDKs proprietários e pesos externos
opcionais devem ser obtidos separadamente sob os termos aplicáveis.

O som de desligamento de energia foi sintetizado pelo próprio projeto com
`tools/generate_disconnect_sound.py`; nenhum áudio de terceiros foi copiado.
