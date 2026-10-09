# ORION-Q 5.42 — GEO: implementação e validação

Data: 2026-10-06. Resultado funcional automático: aprovado.

287 testes únicos de código passaram. O executável congelado aprovou 38 checks gerais e 11 checks específicos da GEO. A aceitação automática local no Windows repetiu o self-test offscreen e 20 ciclos de stress. Um self-test adicional com QPA Windows aprovou 38 checks gerais e os 11 checks GEO usando a renderização nativa.

## Casos de uso

| Caso | Validação | Resultado |
|---|---|---|
| Todos os satélites e órbitas | Cinco estados orbitais do backend no ensaio de cadastro; seleção mantém as demais representações. | Aprovado |
| Hover, seleção e detalhes | Ícones de 32 pixels lógicos, área de 48×48 e identificação; sinal de detalhes emitido uma vez por clique. | Aprovado |
| Projeções coincidentes | Alças separadas com linhas de referência; coordenadas físicas preservadas (fixture geométrica explícita). | Aprovado |
| Botão Mapa/Globo | Indica modo atual/destino; dados e contexto da câmera preservados. | Aprovado |
| Scroll e histerese | Entrada em 44 milhões de metros projetados; retorno em zoom 2,05/24 milhões; movimento inverso pequeno não oscila. | Aprovado |
| Região focal | Rotação e zoom fora do centro conduzem a região correta ao mapa; focar campus mantém teste de clique alinhado. | Aprovado |
| Trocas consecutivas | 100 trocas completas em código; 60 no self-test e 60 na aceitação nativa. Máximo de uma instância viva. | Aprovado |
| Pedidos rápidos | 201 pedidos em código e 101 por diagnóstico convergem para a última visualização, com uma ativação. | Aprovado |
| Liberação dos recursos | QObjects anteriores inválidos; timers desligados e caches limpos; bitmap de transição termina em 350 ms. | Aprovado |
| Dados durante a troca | Snapshot atualizado enquanto o renderer é retirado; nodes, links, registro de comunicadores, camada, seleção e demanda retidos. | Aprovado |
| Efemérides ausentes | Componente permanece no seletor; nenhuma posição ou transmissão é inventada. | Aprovado |
| Relógio e animação | Clock publicado persiste entre renderizadores; motivo tracejado avança, sem extrapolar o marcador. | Aprovado |
| Resoluções | 640×480, 1280×900 e 3440×1440, além da matriz de adaptação e inicialização Windows em quatro escalas DPI. | Aprovado |
| Tema e localização em edição | Câmeras/seleção restauradas e cadastro de localização atravessa a troca; demais regressões de tema aprovadas. | Aprovado |
| Comunicadores | Botão imediatamente abaixo de Nodes, ícone de transceptor, tooltip, estados consistentes e atalho 5; navegação/paleta alinhadas. | Aprovado |

## Alterações e evidências

`GeoMap` agora é um controlador com `GeoSceneState` independente das instâncias gráficas. Mantém apenas `MapRenderer` ou `OrbitGlobe`, interrompe e destrói o anterior antes de ativar o seguinte. Inspector e janelas de detalhes continuam na Topology. Nenhum renderer consulta ou assina fontes; o fluxo de dados do backend permanece o existente.

O globo mostra todos os satélites e órbitas publicados, usa alças de seleção constantes em tela e distingue ocultação pela Terra. Paths e raster terrestre são reutilizados enquanto câmera/geometria não mudam. A animação altera somente o motivo tracejado; métricas, posição e transmissões permanecem observações do backend.

A primeira regressão detectou seis falhas de clique de campus antes da primeira exibição: o QWidget filho conservava o tamanho inicial enquanto a fachada já tinha sido redimensionada. A atualização de geometria foi corrigida e um teste específico adicionado. Duas expectativas antigas de ordem de navegação foram atualizadas para a posição solicitada de Comunicadores. Os XMLs iniciais ficam preservados; o resumo usa o resultado mais recente de cada caso, sem contar repetições.

O primeiro candidato congelado foi preservado em `release/windows/5.42.0-native-20261006-211252`. Seu self-test detectou outra expectativa antiga da navegação e ausência de eventos de hover na janela auxiliar. O diagnóstico foi alinhado à navegação e passou a usar o overload QWindow de QTest, que entrega eventos QPA ao alvo em vez de depender de QCursor::setPos. O teste mantém a passagem pela hierarquia normal de widgets e inclui janelas auxiliares sobrepostas. A distribuição final foi recompilada e passou novamente pelos gates.

Evidência: `outputs/geo_view_20261006_v1/delivery-summary.json` e XMLs. Captura nativa do executável: `release/windows/5.42.0-native-20261006-213158/native-windows-rendering-v2/communicators-geo/geo-modes/all-satellites-globe.png`. Relatórios específicos: `release/windows/5.42.0-native-20261006-213158/communicators-geo/geo-modes/report.json`, `release/windows/5.42.0-native-20261006-213158/native-acceptance/communicators-geo/geo-modes/report.json` e `release/windows/5.42.0-native-20261006-213158/native-windows-rendering-v2/communicators-geo/geo-modes/report.json`.

O primeiro self-test com QPA Windows aprovou todos os 38 checks, mas o wrapper PowerShell recebeu ExitCode nulo. Esse comportamento foi reproduzido com um comando que retorna 0; acessar o handle previamente preservou o código. O ensaio completo foi repetido com subprocess.run, confirmou encerramento com código 0 e ficou em `native-windows-rendering-v2/process-completion.json`. Os relatórios anteriores não foram apagados.

Preservação: 1112 arquivos registrados antes das alterações; 1097 permaneceram idênticos. Os 15 arquivos existentes alterados estão listados no resumo e pertencem à GEO, navegação, testes, documentação e build. Nenhum arquivo de baseline foi removido. Os hashes do EXE/ZIP 5.41 anterior continuam iguais. Protocolos, física, modelos opcionais e bloqueio da atuação física foram preservados.

## Distribuição e execução

- EXE: `release/windows/5.42.0-native-20261006-213158/ORION-Q/ORION-Q.exe`.
- ZIP: `release/windows/5.42.0-native-20261006-213158/ORION-Q-5.42.0-native-windows-x64.zip`.
- SHA-256 EXE: `f6fda15ff933a2433b29415bacd5d3d31aae329ff55c2e8c1148e2e0f34f111b`.
- SHA-256 ZIP: `86197eaa651a13207ae757deb80b32f2230e1cf8674f98446bebaddaab750d03`.
- ZIP: 294742860 bytes, 3928 entradas; CRC verificado e manual/ADR da GEO incluídos.

O ZIP inicial foi retido em `package-before-guide-correction` ao corrigir somente a grafia do caminho de relatório no manual. O runtime compilado permaneceu idêntico. O manifesto final foi novamente validado pelo próprio executável em `final-package-integrity/acceptance-report.json`.

Extraia todo o ZIP e mantenha a pasta `_internal` junto ao EXE. Na GEO, use Mapa → Globo, scroll, seletor ou clique nas alças dos satélites. Para repetir o diagnóstico use um destino novo:

```powershell
ORION-Q.exe --self-test C:\temp\orion-geo-novo\report.json
```

## Limites objetivos

A aceitação automática do Windows aprovou os fluxos; a classificação registrada continua `manual_review_required`. Máquina independente e arraste com mouse físico não foram qualificados. As verificações de recursos comprovam o ciclo de vida dos renderizadores/timers/caches, não uma garantia universal de memória de todo o processo ou de desempenho em qualquer GPU.

Este incremento não modifica as limitações de integração de missão e segurança documentadas em OGS v2 nem qualifica comunicação espacial real. Permanecem os gates externos e as pausas de aquisição/controle já documentados na entrega 5.41. Nenhuma atuação física ocorreu.

Documentação: [operação GEO](../product/orion_q_geo_modes.md), [ADR](../architecture/adr_geo_004_exclusive_renderers.md), [satélite/OGS v2](../product/orion_q_satellite_ogs_v2.md).
