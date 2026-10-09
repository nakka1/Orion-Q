# ORION-Q — terminal ajustável, fechamento e reabertura — 06/10/2026

O terminal agora divide o espaço com as seis telas através de uma divisória vertical ajustável.
Arraste a borda superior para alterar a altura. `×` fecha o painel inteiro; **Terminal** na barra
inferior ou **Ctrl+J** abre novamente. Mapas, gráficos e tabelas usam o espaço liberado e voltam
a se adaptar quando o painel reaparece. A largura acompanha a janela.

O fechamento mantém os widgets, rascunho, histórico de comandos, registros e feedback.
O cabeçalho ainda permite recolher/expandir. A altura preferida é mantida ao navegar, mudar
tema, fechar/reabrir e diminuir/aumentar a janela durante a sessão. A janela compacta limita
o painel para preservar uma área útil da tela e o prompt. A navegação lateral rola quando
necessário. Setas na divisória ajustam a altura; Shift aumenta o passo; Home ou clique duplo
restaura a altura inicial. Os controles incluem ajuda ao passar o mouse.

## Casos de uso

Os 24 casos abaixo passaram em `offscreen` e no plugin **Qt Windows**, com
`QT_SCALE_FACTOR=1`, `1.5` e `2`. Os eventos de ponteiro de redimensionamento percorrem o
viewport, a transformação do canvas e o proxy até a divisória. São eventos Qt enviados
à própria aplicação; entrada física do mouse no desktop não foi validada.

| Caso | Ação | Resultado verificado | Estado |
| --- | --- | --- | --- |
| 01 | Fechar/reabrir em Topology | O mapa ocupa o espaço liberado; reabrir devolve o tamanho do terminal. | Aprovado |
| 02 | Fechar/reabrir em Fidelity & decoherence | Gráficos e cartões seguem a área disponível sem recriar a tela. | Aprovado |
| 03 | Fechar/reabrir em Key management | Painéis, tabelas e rolagem usam a área disponível. | Aprovado |
| 04 | Fechar/reabrir em Nodes | Conteúdo e seleção continuam na mesma tela. | Aprovado |
| 05 | Fechar/reabrir em Edge control | Painéis e controles seguem o novo espaço; programação física permanece bloqueada. | Aprovado |
| 06 | Fechar/reabrir em Access & audit | Tabelas e leitura permanecem acessíveis. | Aprovado |
| 07 | Arrastar em 1024 × 600 | A altura muda em ambas as direções, mantendo prompt e área mínima da tela. | Aprovado |
| 08 | Arrastar em 1280 × 820 | A divisão acompanha a posição enviada pelo viewport. | Aprovado |
| 09 | Arrastar em 1920 × 1080 | O arraste respeita a escala do canvas. | Aprovado |
| 10 | Arrastar em 3440 × 1440 | O conteúdo ocupa a largura disponível e se adapta à nova altura. | Aprovado |
| 11 | Arrastar com zoom manual 1,4× | Coordenadas do viewport são traduzidas sem distorcer o painel. | Aprovado |
| 12 | Fechar/reabrir três vezes com conteúdo | Rascunho, histórico, registros, feedback e posição de leitura são preservados. | Aprovado |
| 13 | Ctrl+J no prompt e na barra inferior | Fecha/reabre; depois Enter continua executando um comando de navegação. | Aprovado |
| 14 | Redimensionar por teclado e restaurar | Setas, Shift, Home e clique duplo funcionam; controles têm ajuda e nome acessível. | Aprovado |
| 15 | Recolher, expandir e mudar tamanho da janela | Mantém a altura preferida; a janela pequena limita a altura visível. A divisória recolhida fica inativa. | Aprovado |
| 16 | Trocar tema com terminal aberto | Tamanho, rascunho e histórico são restaurados. | Aprovado |
| 17 | Trocar tema com terminal recolhido | Estado recolhido e tamanho preferido são mantidos. | Aprovado |
| 18 | Trocar tema com terminal fechado | Continua fechado; botão Terminal continua disponível. | Aprovado |
| 19 | Terminal grande e navegação lateral | A navegação passa a rolar e mantém Sign out acessível. | Aprovado |
| 20 | Trocar tema lendo um registro antigo | A leitura volta à mesma posição, sem saltar ao começo. | Aprovado |
| 21 | Fechar/reabrir durante operação ocupada | Não cancela nem interrompe o provedor. | Aprovado |
| 22 | Demonstração institucional com backend real | Quatro ticks simulados continuam com o terminal fechado; estados e registros são relidos, com ML de controle desativado. | Aprovado |
| 23 | Rotina incorporada ao autoteste do executável | A rotina valida 27 condições na interface com eventos Qt; não envia operações ao backend. | Aprovado |
| 24 | Texto estreito na divisória e em painéis | Mudanças repetidas entre 72 e 73 pixels mantêm conteúdo completo, dica de ferramenta e medidas estáveis. | Aprovado |

## Evidências

- **350 casos únicos aprovados; 473 execuções aprovadas**,
  distribuídos em 21 XMLs. As repetições nas três escalas estão incluídas nas execuções.
- As regressões abrangem janela sem moldura, arraste das bordas, monitores/DPI, temas,
  navegação, mapas, leitores de texto, tabelas, gráficos em tempo real, composição dos nodes
  e ciclo de vida dos widgets. Não se trata de uma nova execução de toda a suíte científica.
- O **novo executável** passou o autoteste completo, incluindo **27 verificações
  do terminal no Qt `offscreen`, janela sem moldura, demonstração institucional,
  leituras, entrega de ticks e isolamento da atuação física.
- Campanha congelada, validação do roadmap, diagnóstico de modelos sem pacotes, integridade
  e aceite automático nativo passaram. Estresse: **20 rodadas,
  140 ciclos e 535 verificações**.
- O mesmo executável passou o diagnóstico com os ZIPs originais `F:\qnet_network.zip`
  e `C:\Users\alexa\Downloads\modelo_qkd_ml.zip`, com PATH restrito. Nenhum arquivo original foi alterado.
- A revisão manual e o aceite em outro computador continuam pendentes. Não foi feito ensaio
  de atuação física ou revalidação de medidas experimentais.

As tentativas iniciais foram preservadas: `pytest-first.xml` contém uma asserção que usava
uma chave incorreta do contrato do backend; o teste foi corrigido para ler
`policy.ml_control_enabled`. `pytest-terminal-final.xml` e as primeiras rodadas nativas
registram a falha de posição do histórico ao mudar tema. A correção estabelece a geometria
dos registros antes de restaurar a barra de rolagem e vincula o callback à vida do painel.
O primeiro executável também encontrou uma recursão de layout ao atualizar enlaces na
topologia: o rótulo “Logical geometry” alternava entre 72 e 73 pixels e o cabeçalho
modificava sua disposição dentro do evento de redimensionamento. Os rastros de fonte e
do pacote foram preservados, incluindo a exceção nativa `0xc00000fd`. O texto agora é
encurtado no desenho, com medidas estáveis, e o cabeçalho aplica as mudanças em um timer
associado ao widget, com proteção contra reentrada. O fluxo completo de nodes/enlaces
foi repetido; seus 23 casos passaram. O primeiro pacote foi preservado e substituído por
um novo build validado; ele não é o executável entregue neste relatório.
Uma rodada posterior também identificou que a tabela de auditoria, antes fixada em
430 pixels, podia ultrapassar o viewport ao aumentar o terminal. Ela agora limita sua
altura pela área disponível. O autoteste completo de fonte foi repetido e passou,
incluindo os limites da tabela. Os relatórios intermediários permanecem preservados.

`pytest-terminal-v5.xml` e `pytest-native-layout-final-*.xml` são os resultados finais,
sem falhas, erros ou testes ignorados. Os XMLs anteriores não entram nos totais aprovados.

## Preservação e entrega

Foram registrados **857 arquivos existentes** antes da edição: todos permanecem;
**846 têm os mesmos bytes**, e 11 receberam incrementos nesta tarefa.
As alterações anteriores, arquivos de referência, backend, física, fontes de modelos e
diários existentes foram preservados. O terminal não pausa a simulação nem envia comandos
ao fechar ou redimensionar. Os modelos permanecem opcionais, sem atuação física.

- [Novo ORION-Q.exe](E:/Codex/2026-08-27/e/work/NISQ-Predictive-Twin/release/windows/5.38.0-native-20261006-020322/ORION-Q/ORION-Q.exe)
- [ZIP portátil](E:/Codex/2026-08-27/e/work/NISQ-Predictive-Twin/release/windows/5.38.0-native-20261006-020322/ORION-Q-5.38.0-native-windows-x64.zip) — extraia inteiro; mantenha `_internal` ao lado do `.exe`.
- [Autoteste do pacote](E:/Codex/2026-08-27/e/work/NISQ-Predictive-Twin/release/windows/5.38.0-native-20261006-020322/self-test.json)
- [Aceite automático](E:/Codex/2026-08-27/e/work/NISQ-Predictive-Twin/release/windows/5.38.0-native-20261006-020322/native-acceptance/acceptance-report.json)
- [Modelos com arquivos originais](E:/Codex/2026-08-27/e/work/NISQ-Predictive-Twin/outputs/terminal_workspace_20261006_v1/frozen-original-models-v2/report.json)
- [Consolidação da validação e preservação](E:/Codex/2026-08-27/e/work/NISQ-Predictive-Twin/outputs/terminal_workspace_20261006_v1/final-validation.json)
- [Diff incremental desta tarefa](E:/Codex/2026-08-27/e/work/NISQ-Predictive-Twin/outputs/terminal_workspace_20261006_v1/session-changes.diff)
- [Captura institucional com terminal ajustado (fonte)](E:/Codex/2026-08-27/e/work/NISQ-Predictive-Twin/outputs/terminal_workspace_20261006_v1/pytest-terminal-v5/test_real_backend_keeps_publis0/live-terminal/terminal-resized.png)
- [Captura institucional com terminal fechado (fonte)](E:/Codex/2026-08-27/e/work/NISQ-Predictive-Twin/outputs/terminal_workspace_20261006_v1/pytest-terminal-v5/test_real_backend_keeps_publis0/live-terminal/terminal-closed.png)

SHA-256 do executável: `0117efc0af926a1f9da2c8038ab2a83a793fed16e30c571fb225ecdadf2f7fa0`.

SHA-256 do ZIP: `b42689a1925c3552ff7b49e61e89d0782b9850b22a29942cb84c859a0d63cdef`. CRC e manifesto verificados.
