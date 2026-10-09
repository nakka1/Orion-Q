# ORION-Q — interface adaptada ao monitor, 5 de outubro de 2026

Estado: fonte e executável aprovados na validação automática local.

## Mudanças

A aplicação calcula o tamanho restaurado pela área útil do monitor do cursor e
abre maximizada. O ajuste acompanha mudanças de monitor, DPI e área útil.
Os painéis e grupos de ações se reorganizam conforme a largura disponível,
preservando as instâncias dos controles e seus campos. A tela inicial, o
carregamento e os diálogos permitem rolagem; a paleta calcula seu tamanho pelo
espaço disponível. A imagem em cache do mapa acompanha a densidade de pixels
do monitor e o zoom da apresentação.

Foram corrigidas três falhas encontradas durante esta atualização: arredondar
a cena para pixels inteiros criava barras de rolagem externas em telas largas;
um segundo ajuste após abrir um diálogo desfazia o redimensionamento manual;
o observador de monitor mantinha uma referência à janela fechada. A cena agora
preserva suas dimensões fracionárias, o ajuste inicial é feito uma vez e a
referência ao proprietário é fraca.

A sonda nativa revelou que solicitar maximização no primeiro comando de
exibição podia manter o tamanho restaurado. A abertura cria a janela antes de
solicitar a maximização. Esse comportamento é compatível com a regra do Windows
para a primeira chamada de exibição, que pode seguir os parâmetros do processo
que iniciou o programa. [Referência: Microsoft ShowWindow](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-showwindow).

## Casos de uso e evidências

Todas as referências relativas abaixo partem da raiz do repositório.

| Caso de uso | Cenário verificado | Resultado | Evidência |
| --- | --- | --- | --- |
| Abrir no monitor escolhido | Tamanho restaurado e moldura dentro da área útil | Aprovado | `tests/test_console_display_adaptation.py` |
| Abrir maximizado ou em tela cheia | Dois modos de abertura | Aprovado | `outputs/display_adaptation_20261005_v1/show-final.xml` |
| Usar o monitor conectado no Windows | Monitor 3440×1440; área útil 3440×1392; janela nativa 3440×1369 | Aprovado | `outputs/display_adaptation_20261005_v1/native-window.json` |
| Redimensionar a interface | Dez tamanhos: 960×540, 1280×720, 1280×800, 1366×768, 1920×1080, 2560×1440, 3440×1440, 3840×2160, 5120×2160 e 1024×768 | Aprovado | Matriz `responsive_ui` do self-test do executável |
| Usar telas compactas, grandes e temas distintos | Login e seis páginas em 960×540, 1280×720 e 3840×2160, nos temas claro e escuro | Aprovado | `test_all_pages_and_palette_keep_controls_inside_their_containers` |
| Ver a tela de carregamento | Três tamanhos e dois temas | Aprovado | `outputs/display_adaptation_20261005_v1/loading-final.xml` |
| Aplicar escala fracionária | Qt a 100%, 125%, 150% e 200%, em processos separados | Aprovado | `test_fractional_dpi_uses_native_qt_pixels_in_separate_processes` |
| Mudar de monitor e de área útil | Origens positivas e negativas; mudança de DPI; campo ainda não enviado preservado | Aprovado | `test_monitor_and_dpi_changes_preserve_controls_and_input` |
| Mover uma janela restaurada para monitor menor | Áreas úteis 1280×680 e 640×360; moldura dentro do monitor; rascunho de node preservado; rolagem nas áreas compactas | Aprovado | `test_restored_console_fits_a_smaller_monitor_without_losing_drafts` |
| Redimensionar cards | Três, duas e uma coluna; mesmos campos e ações | Aprovado | `test_card_reflow_preserves_widgets_and_their_actions` |
| Usar grupos de ações em espaço estreito | Controles empilhados dentro de seus limites | Aprovado | `test_action_row_stacks_instead_of_clipping_controls` |
| Ajustar o zoom | Escala uniforme; retorno ao ajuste automático; cache do mapa na densidade da vista | Aprovado | `test_stage_fills_monitor_without_distortion_and_map_density_tracks_zoom` |
| Abrir e filtrar comandos | Paleta contida no espaço de trabalho e operação pelo teclado | Aprovado | Testes da paleta e matriz do executável |
| Abrir diálogos e configurações | Conteúdo rolável, controles acessíveis e janelas nativas | Aprovado | `secondary_dialogs` e configuração dos enlaces no self-test |
| Fechar janelas secundárias | Observador não mantém a janela fechada viva | Aprovado | `test_display_observer_does_not_retain_closed_windows` |
| Operar nodes, conexões e políticas | Comandos, recusas, confirmação, isolamento, retorno e preservação de contexto | Aprovado | Regressão dos testes `test_console_*` |
| Usar modelos opcionais | Pacotes originais e modo sem pacotes | Aprovado | Validação do executável com os ZIPs originais |

## Execuções finais

| Verificação | Resultado | Evidência |
| --- | --- | --- |
| Regressão ampliada da interface e integrações | 960 aprovados; 0 falhas; 0 erros; 0 pulados | `outputs/display_adaptation_20261005_v1/ui-regression-final.xml` |
| Reteste após ajuste da janela restaurada | 77 aprovados; inclui todos os 34 casos novos de exibição e as telas principais | `outputs/display_adaptation_20261005_v1/final-monitor.xml` |
| Casos distintos nas execuções finais acima | 969 aprovados, com os casos novos de carregamento e abertura incluídos no reteste | Consolidação por nome de classe e caso de teste |
| Qt nativo no monitor conectado | Aprovado | `outputs/display_adaptation_20261005_v1/native-window.json` |

| Verificação do executável definitivo | Resultado | Evidência na versão nova |
| --- | --- | --- |
| Self-test | 26 verificações principais aprovadas | `self-test.json` |
| Matriz da interface | 251 verificações aprovadas, incluindo dez tamanhos e barras acessíveis | `self-test.json` → `responsive_ui` |
| Diálogos | 31 vistas; 270 verificações aprovadas nos dois tamanhos | `self-test.json` → `responsive_ui.dialogs` |
| Modelos opcionais | Aprovado sem pacotes e com os ZIPs originais | `optional-models-original-validation/report.json` |
| Comparações e runtime isolado | Comparações de 30, 120 e 250 repetições e 20 ciclos sem falhas de execução | `roadmap-validation/report.json` |
| Campanha no worker | Aprovada; recibo confirmado, paridade, cancelamento e exportação | `campaign-validation/report.json` |
| Estresse do controlador | 20 rodadas aprovadas | `stress-validation/` |
| Integridade e aceitação Windows | Verificação de hashes, CRC do ZIP e aceitação automática local aprovadas | `portable-package.json` e `native-acceptance/acceptance-report.json` |

Versão definitiva: `release/windows/5.38.0-native-20261005-113308`.

- Executável: `ORION-Q/ORION-Q.exe`, junto da pasta `_internal`.
- ZIP: `ORION-Q-5.38.0-native-windows-x64.zip`, 397.902.968 bytes; 3.892 arquivos.
- SHA-256 do executável: `56f505a39df4410c4a46013a86abb174bbf4642b8a48a637368ac2f58e84c941`.
- SHA-256 do ZIP: `6b1b27320748d3336532baae659841ec4a2c122976c42d5e7c4f7409e8c83ae7`.

O ZIP deve ser extraído inteiro. Os hashes foram recalculados após a execução
final e conferidos com o manifesto do pacote; a evidência está em
`outputs/display_adaptation_20261005_v1/final-release-verification.json`.

## Preservação e limites

Os 14 arquivos existentes que receberam edições têm cópias do estado recebido
em `outputs/display_adaptation_20261005_v1/before/`, com hashes em
`before-sha256.json`. `display-only.patch` e `change-manifest.json` registram
apenas esta atualização, incluindo seus arquivos novos. As alterações que já
estavam no projeto e os executáveis anteriores foram preservados.

Os testes de DPI usam processos Qt e as trocas de monitor usam sinais e
geometrias simuladas. A sonda Windows usa a janela da própria aplicação,
transparente, sem foco nem interação com o desktop. Há um único monitor físico
conectado; a passagem real entre monitores distintos ainda requer conferência
humana. Abaixo de 960×480 pixels lógicos, o espaço de trabalho oferece rolagem
externa para conservar a legibilidade. O ajuste manual de tamanho continua
disponível.

O escopo é a apresentação. Os modelos continuam opcionais e não há atuação
física. Os resultados científicos e as limitações de desempenho registrados
no [relatório anterior](orion_q_project_fixes_20261005.md) permanecem aplicáveis.
No diagnóstico desta versão, os 20 ciclos também excederam a meta de 10 ms:
média 152,49 ms e máximo 183,60 ms. A aprovação desse diagnóstico indica execução
sem falhas, com os limites explicitamente registrados.

A aceitação Windows continua marcada `manual_review_required`, com
`automatic_passed=true` e `independent_machine_verified=false`. A distribuição
local está verificada; não foi realizado ensaio em uma segunda máquina.

