# ORION-Q 5.44 — símbolos da topologia

Revisão de 2026-10-06. Validação automática local: aprovada.

Endpoints agora usam uma porta circular de 24 pixels lógicos com duas setas opostas de entrada/saída. Nodes usam hexágono de 14 pixels; Core usa hub de 20 pixels. Repetidores usam marca de sete pixels com alvo de clique mínimo de 24 pixels. A classificação vem de network_role/physical_type publicados pelo backend, com compatibilidade para o metadado kind legado; nomes e prefixos de IDs não determinam ícones.

Fibras são somente linhas. Foram retirados tanto os quadrados no meio quanto seus antigos alvos invisíveis; seleção usa a linha com tolerância de cinco pixels e destaca a própria linha. Não foram removidos repetidores reais: suas pequenas marcas representam componentes do inventário.

## Casos verificados

| Caso | Resultado observado | Status |
|---|---|---|
| Endpoint | Símbolo próprio, maior que nodes/intermediários; rótulo e prioridade no desenho/picking. | Aprovado |
| Node/Core | Símbolos de hub distintos da porta endpoint e da marca de repetidor. | Aprovado |
| Muitos repetidores | Marca pequena no mapa e grafo; nomes no tooltip/inspector; nome/badge contextual no grafo quando selecionado ou no percurso. | Aprovado |
| Fibra normal, degradada, falha e desabilitada | Cor e traçado mantidos; nenhum quadrado sobre a linha. | Aprovado |
| Seleção de fibra | Clique no meio ou fora do meio seleciona o mesmo enlace; ponto afastado da linha não aciona o antigo quadrado. | Aprovado |
| Interação com node | Cadastro, seleção, inspeção, recalibração e isolamento continuam usando o backend. | Aprovado |
| Tema escuro/claro | Primitivas vetoriais usam os tokens de cada tema; seleção/status preservados. | Aprovado |
| Escala e localização | Tamanho lógico estável; geografia e comprimentos não mudam; nodes sem localização seguem sem linhas geográficas. | Aprovado |
| Visões compartilhadas | Mapa, globo, grafo, matriz, fila sem localização e cabeçalho da inspeção usam a mesma classificação. | Aprovado |
| Satélites | Símbolo físico, órbitas, seleção e transmissões publicadas preservados. | Aprovado |
| Cadastro e alternância | Diagnóstico dos sete tipos e 60 trocas Mapa/Globo aprovado no EXE. | Aprovado |
| Pacote Windows | Inicialização em seis casos, 20 rodadas de stress, aceitação automática e integridade do ZIP. | Aprovado |

## Evidências e preservação

85 testes únicos passaram. A execução completa da topologia foi repetida após alinhar a compatibilidade do Core legado e aprovou 22 casos. Uma seleção parcial por -k omitiu o passo de login da fixture compartilhada e encontrou duas falhas de tela ainda não carregada; o XML parcial permanece preservado. A execução completa incluiu a preparação normal e passou; os resultados mais recentes são consolidados sem contar repetições.

O EXE passou por 38 checks gerais com QPA Windows, 20 de Comunicadores, 11 da GEO, 21 do pipeline OGS, 25 de campanha temporal e quatro de roadmap. Seis inicializações em DPI/modos distintos e stress de 20 rodadas/140 ciclos passaram. A aceitação automática local repetiu os diagnósticos e stress a partir do pacote. Isso verifica software e interação Qt, sem qualificar hardware físico ou uma máquina independente.

As prévias comparáveis estão em `outputs/topology_symbols_20261006_v1/before-*` e `after-*`. As folhas `after-symbols-dark.png` e `after-symbols-light.png` foram inspecionadas. São fixtures explícitas de aparência, sem telemetria ou resultados científicos; as capturas do EXE vêm do diagnóstico do backend simulado em `release/windows/5.44.0-native-20261006-232019/communicators-geo/geo-labels.png` e `geo-modes/all-satellites-globe.png`.

O resumo e hashes estão em `outputs/topology_symbols_20261006_v1/delivery-summary.json`; os XMLs em views.xml, classification.xml e topology-final.xml. Diagnósticos congelados: `release/windows/5.44.0-native-20261006-232019/self-test.json` e `native-acceptance/acceptance-report.json`.

Preservação: 1115 arquivos de baseline, 1105 idênticos, dez existentes alterados no escopo de símbolos, interação, documentação e versão; nenhum removido. EXE e ZIP 5.43 anteriores permanecem idênticos. Backend, contratos de comunicação, cálculos científicos e modelos não foram modificados. Modelos continuam opcionais e a atuação física bloqueada.

## Distribuição

- EXE: `release/windows/5.44.0-native-20261006-232019/ORION-Q/ORION-Q.exe`.
- ZIP: `release/windows/5.44.0-native-20261006-232019/ORION-Q-5.44.0-native-windows-x64.zip`.
- SHA-256 EXE: `be0834337aaffefc36d962319c65ac29f168cea21c32ce4f446d7de31f04eed5`.
- SHA-256 ZIP: `5903bf33b32b48ff8500d9fe222080569ccd4be4ccb449f34ebeee2b253e319c`.
- ZIP: 294762768 bytes, 3928 entradas; CRC e manual 5.44 verificados.

Extraia o ZIP completo e mantenha `_internal` junto ao EXE. A aceitação automática passou; o relatório mantém `manual_review_required`, sem avaliação em outra máquina ou mouse físico. Nenhuma atuação física ocorreu.

Operação e legenda: [GEO](../product/orion_q_geo_modes.md).
