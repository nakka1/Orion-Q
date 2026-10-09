# ORION-Q 5.48 — aproximação do globo

Data: 07/10/2026. Alteração visual na GEO; protocolos, dados do backend, modelos opcionais e bloqueio de atuação física permanecem preservados.

O globo mudava para mapa-múndi ao atingir zoom 2,85, com faixa de câmera limitada a 4. A transição agora exige **5,8**, e o renderer admite até **6** tanto no scroll quanto na restauração da câmera. Isso permite aproximadamente o dobro da ampliação linear antes da troca.

| Caso | Resultado verificado |
| --- | --- |
| Aproximação partindo de zoom 1 | Nove passos de scroll de 120 unidades mantêm o globo; zoom 5,1598 no nono passo |
| Décimo passo | Abre o mapa na região sob o cursor, com largura projetada de 20 milhões de metros |
| Um afastamento após a troca | Continua no mapa, com largura de 24 milhões de metros; sem alternância imediata |
| Mapa → Globo | Limite anterior de 60 milhões de metros preservado |
| Botão Mapa/Globo | Alternância manual continua disponível, preservando seleção e contexto |
| Aproximação 5,5 na janela nativa | Globo ativo, nove satélites acessíveis, controles visíveis |
| Ícones até zoom 6 | Área de clique e tamanho mínimo em pixels preservados nas resoluções testadas |
| Trocas consecutivas | Um renderer ativo; liberação de timers, rasters e paths do anterior |
| Câmera fora do centro | Região focalizada pelo cursor preservada ao voltar ao mapa |

Os passos descritos correspondem a eventos Qt de scroll de 120 unidades, fator 1,2 por passo, partindo de zoom 1. Trackpads, posição inicial e deltas maiores podem produzir outra quantidade de gestos; o critério operacional é zoom 5,8.

## Evidências de fonte

- **40/40 testes aprovados**: `outputs/globe_zoom_20261007/regression.xml`, incluindo GEO, interação, resoluções, foco e apresentação discreta da infraestrutura terrestre.
- Janela nativa Windows, com snapshot do serviço institucional emulado após cinco ticks: **71 componentes, 131 enlaces e nove satélites**. Não foi usada fixture gráfica para esse ensaio.
- Harness GEO: **11/11**; complemento de aproximação: **11/11**, em `native-source-geo/summary.json` e `native-source-geo/harness/report.json`.
- Captura inspecionada: `native-source-geo/globe-zoom-5_5.png`.
- Diagnóstico global atualizado: **15/15** em Qt nativo Windows, backend institucional emulado após oito ticks, em `native-global-globe-r2/report.json` e `summary.json`.
- Whitespace aprovado. Ruff `F,E4,E9`, excluindo `F401`, aprovado; quatro imports sem uso já existentes nesses arquivos foram preservados.

A construção do executável e seus diagnósticos congelados são registrados separadamente em `outputs/globe_zoom_20261007/`. Os artefatos anteriores permanecem preservados.

O diagnóstico GEO dentro do primeiro build aprovou **11/11 condições** na plataforma Qt `windows`, incluindo `globe_retained_after_nine_scroll_steps=true` em `communicators-geo/geo-modes/report.json`. O diagnóstico global ainda esperava seis passos e reprovou a nova tolerância; essa expectativa também foi atualizada para nove passos no globo e troca no décimo. O primeiro build permanece preservado com `DO-NOT-DISTRIBUTE.txt`. A fonte final altera quatro arquivos de runtime: controlador GEO, renderer e os dois diagnósticos de visualização. A entrega só é liberada após os gates congelados e a integridade do novo pacote.

Este incremento valida comportamento de interface com dados identificados do backend simulado. Não qualifica mouse físico, outro computador, enlaces reais ou segurança QKD. Consulte [a operação da GEO](../product/orion_q_geo_modes.md) para os controles e limites.

## Entrega validada

Distribuição: `release/windows/5.48.0-native-20261007-140936/ORION-Q-5.48.0-native-windows-x64.zip`. Extraia a pasta inteira e abra `ORION-Q/ORION-Q.exe`, mantendo `_internal` junto ao executável.

| Verificação do executável final | Resultado |
| --- | --- |
| Self-test de telas e contratos | 39 condições aprovadas |
| GEO / globo global | 11/11 e 15/15; ambos confirmam permanência após nove passos |
| Bancada completa | 12/12 aprovadas, incluindo processos independentes e recuperação |
| Pipeline satelital | 21 condições aprovadas |
| Rede global, roadmap, campanhas temporais e modelos opcionais | Diagnósticos aprovados, vinculados ao executável final |
| Stress | 20 rodadas aprovadas |
| Inicialização e redimensionamentos | Seis cenários aprovados, escalas 100%, 125%, 150% e 200% |
| ZIP | CRC aprovado, 3.948 arquivos, 298.982.219 bytes |
| Integridade após empacotamento | Manifesto aprovado pelo executável; membros `.exe` e `SHA256SUMS.txt` dentro do ZIP conferidos com os arquivos aprovados |
| Preservação | Executável e ZIP da versão 5.47 mantêm seus hashes originais |

Os diagnósticos congelados, self-test e stress foram executados uma vez no executável final. Depois do empacotamento, `--acceptance-test --integrity-only` verificou o manifesto, sem repetir self-test/stress nem declarar `automatic_passed=true` nesse relatório de integridade. O helper de build inicialmente perdeu a variável `$exe` ao sair do bloco de validação; a etapa foi retomada com caminho explícito, sem mudar binários ou repetir os testes aprovados. A falha permanece em `BUILD-HELPER-FAILURE-PRESERVED.txt` e nos logs.

Evidência consolidada: `outputs/globe_zoom_20261007/delivery-evidence.json`; valida fonte congelada, quatro arquivos alterados, JUnit de 40 casos, gates, ZIP e preservação. Nenhuma alteração de runtime ocorreu após o snapshot do build final.

Executável SHA-256: `f39b9f530ef3c8d44c17e89f0e0e60a8883511af78d9a1d4f55213ed38a0b582`.

ZIP SHA-256: `5c63e444ec2badb343c368f17d95146608d957c82a78473b08f34a8bb16739b3`.
