# ORION-Q — janela sem moldura — 05/10/2026

A janela principal passa a usar `Qt.FramelessWindowHint`, removendo a barra de
título e as bordas nativas do Windows. Uma faixa de 34 pixels lógicos contém a
área de arraste e os botões minimizar, maximizar/restaurar e fechar. Os ícones
são vetoriais, têm nomes acessíveis, descrições ao passar o mouse e indicação de
foco pelo teclado. A faixa permanece disponível no carregamento, login e nas
seis páginas do workspace.

A faixa fica fora do canvas com zoom, usando `QMainWindow.setMenuWidget`.
Zoom, reconstrução de tema e ticks do backend não recriam os controles. A troca
de tema atualiza as cores da faixa existente. O DPI continua sendo aplicado pelo
Qt uma única vez; o dimensionamento da interface e as correções anteriores de
rolagem, desenho de gráficos e processamento em segundo plano foram preservados.

Arrastar a faixa usa o recurso de movimentação do sistema. Oito zonas transparentes
nas bordas e cantos oferecem redimensionamento; elas desaparecem quando a janela
está maximizada ou em tela cheia. O clique duplo na faixa maximiza/restaura.
Ao arrastar uma janela maximizada, o tamanho anterior é recuperado sob o ponteiro.
Os limites mínimo e máximo são respeitados, inclusive quando a plataforma precisa
usar o fallback de geometria. As operações de movimentação e redimensionamento
seguem as APIs documentadas de [QWindow](https://doc.qt.io/qt-6/qwindow.html#startSystemMove).

Fechar pelo botão ou Alt+F4 chama o fluxo existente da janela. Uma campanha ativa
continua sendo cancelada e verificada antes do encerramento. A admissão de modelos,
os modelos opcionais, os recibos e o bloqueio de atuação física não foram alterados.
Os diálogos secundários mantêm suas regras anteriores de janela e modalidade.

| Caso de uso | Verificação |
|---|---|
| Inicialização e login | Controles disponíveis antes de entrar no workspace |
| Navegar entre as seis páginas | Faixa independente da página e do canvas |
| Minimizar | Estado minimizado e restauração preservados |
| Maximizar e restaurar | Ícone/descrição atualizados; geometria anterior recuperada |
| Maximizar no Windows | Janela permanece na área útil do monitor |
| Arrastar a faixa | Delegação ao QWindow; fallback acompanha a posição do ponteiro |
| Arrastar uma janela maximizada | Restaura o tamanho anterior sob o ponteiro |
| Clique duplo na faixa | Maximiza e restaura; clique nos botões não inicia arraste |
| Redimensionar pelos quatro lados e quatro cantos | Aresta oposta permanece ancorada no fallback |
| Redimensionamento extremo | Limites mínimo/máximo; origem negativa de monitor |
| Janela compacta, 320 × 240 | Três botões visíveis e acessíveis |
| Zoom do workspace | Altura dos controles permanece em 34 pixels lógicos |
| Temas claro e escuro | Cores atualizadas sem trocar os controles |
| Escalas 100%, 150% e 200% | Pixels acompanham o DPI, sem aplicar zoom duplo |
| Tela cheia | Controles disponíveis e restauração pelo botão |
| Fechar e recusar fechamento | closeEvent respeitado; janela só fecha quando permitido |
| Teclado e acessibilidade | Enter, Espaço, Alt+F4, nomes e tooltips |
| Atualizações ao vivo | Dados exatos e pixels novos nos testes existentes do executável |

O helper Computer Use falhou durante a inicialização com `helper_unknown_error`.
Não houve envio de mouse ou teclado ao desktop do operador. A validação utiliza
eventos dos próprios widgets Qt e janelas nativas próprias, sem aceitar foco ou
entrada real. O teste do fallback força o retorno negativo das operações de sistema;
a delegação ao handle real é verificada separadamente nos testes de fonte.
Uma sessão com mouse físico e outro computador não é declarada como concluída.

## Entrega e evidências

Regressão desta entrega: **612 aprovados, 0 pulados, zero erros e zero falhas**, em 25 módulos executados em processos separados. Inclui os 35 casos novos de janela, a adaptação de monitor, as telas, comandos, paleta, fontes de dados, leitores de texto, sobreposição de nodes e ondas. A regressão de 3162 aprovados da entrega anterior é histórica e não foi somada a esta contagem.

| Plataforma Qt / escala solicitada | DPR | Verificações | Resultado |
|---|---:|---:|---|
| Windows / 100% | 1.00 | 45 | APROVADO |
| Windows / 150% | 1.50 | 45 | APROVADO |
| Windows / 200% | 2.00 | 45 | APROVADO |

Essas escalas usam `QT_SCALE_FACTOR` nesta máquina; não representam três monitores físicos independentes. Os relatórios discriminam eventos Qt, fallback de geometria e ausência de injeção de entrada no desktop.

| Verificação do executável | Resultado |
|---|---|
| Self-test completo | APROVADO; 30 verificações principais |
| Controles de janela no executável | APROVADO; 44 verificações |
| Leitura e rolagem | APROVADO; 1120 verificações |
| Atualizações ao vivo | APROVADO; dados e pixels atualizados |
| Roadmap e runtime isolado | APROVADO nos critérios funcionais |
| Modelos com e sem os ZIPs originais | APROVADO |
| Pacotes opcionais canônicos | APROVADO; 11 modelos |
| Campanha temporal, cancelamento e recibos | APROVADO |
| Stress, 20 rodadas, seed 1337 | APROVADO |
| Aceitação automática do pacote | APROVADO |
| ZIP portátil | CRC e SHA-256 verificados |

O runtime isolado tem 20 amostras e não atende ao orçamento experimental de 10 ms. O resultado funcional não certifica p99, latência de hardware ou atuação física. A aceitação manual permanece pendente.

Dos 531 arquivos recebidos e copiados antes desta intervenção, **528 permaneceram idênticos**, três receberam somente o delta desta entrega e nenhum foi removido. Os arquivos novos são os dois módulos de janela/validação, os testes e este relatório. As versões anteriores do executável e o estado do operador foram preservados.

Evidências: [tabela de casos individuais](E:/Codex/2026-08-27/e/work/NISQ-Predictive-Twin/outputs/window_chrome_20261005_v1/casos-automatizados.md), [manifesto de alterações](E:/Codex/2026-08-27/e/work/NISQ-Predictive-Twin/outputs/window_chrome_20261005_v1/change-manifest.json) e [patch relativo ao estado recebido](E:/Codex/2026-08-27/e/work/NISQ-Predictive-Twin/outputs/window_chrome_20261005_v1/changes.patch).

Entrega: [ORION-Q.exe](E:/Codex/2026-08-27/e/work/NISQ-Predictive-Twin/release/windows/5.38.0-native-20261005-194736/ORION-Q/ORION-Q.exe) e [ZIP portátil](E:/Codex/2026-08-27/e/work/NISQ-Predictive-Twin/release/windows/5.38.0-native-20261005-194736/ORION-Q-5.38.0-native-windows-x64.zip). Extraia o pacote inteiro; a pasta `_internal` é necessária.

SHA-256 do executável: `54e9f784186d7b4caddcd7619c21e35b9de476e3f313cd0a6951a28b464874cc`.

SHA-256 do ZIP: `f4d775ab7c0e3af1f20389bb66f5b2c1ca2b278eef5910d428aede3910f47b77`.
