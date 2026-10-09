# ORION-Q — rolagem dos leitores e editores de texto — 2026-10-05

O usuário relatou conteúdo visual congelado ao rolar o CAPTURED RECEIPT do CONTEXT INSPECTION, recuperado ao selecionar texto, e comportamento semelhante em outros leitores. A revisão encontrou 25 instâncias que ainda não usavam o tratamento comum de repintura; a correção anterior cobria somente cinco campos. A mudança atual cobre as 30 declarações de campos e suas variantes herdadas, preservando os dados e as alterações recebidas.

## Correção

O componente compartilhado pinta explicitamente o fundo do viewport e pede repintura completa após cada deslocamento, horizontal ou vertical. Isso evita depender dos pixels reaproveitados pela rolagem de um contêiner transparente. O fundo é conferido por pixel nos temas claro e escuro. Atualizações de evidências que não mudam o texto preservam posição de leitura, cursor, seleção e revisão do documento. Entradas JSON continuam editáveis, com desfazer e refazer; as regras de cancelamento e invalidação de entradas permanecem ativas.

Os painéis de contexto, revisão de política, aprovação, ativação, recuperação, auditoria, histórico de comandos, campanhas, catálogo e todas as sessões opcionais passaram a usar o mesmo componente. Nenhum cálculo científico, referência, modelo, mapa ou critério de aprovação foi alterado. Modelos permanecem opcionais e não há atuação física.

## Casos de uso

Cada campo abaixo foi exercitado em ambos os temas: uma pequena rolagem para baixo de três linhas, repintura completa antes de selecionar texto, Page Down, barra até o fim, rolagem para cima, rolagem horizontal de linhas longas, fundo correto, seleção preservada ao repetir evidências ou edição com desfazer/refazer. O corpus de pintura é sintético e rotulado; os painéis são as classes reais do programa. Nesses casos os watchdogs de fonte/expiração são suspensos apenas nas instâncias descartáveis que exibem o corpus, para não interpretar texto de teste como um recibo válido. A validação do recibo real, abaixo, mantém o watchdog ativo.

| Caso | Janela / variante | Campos exercitados | Claro e escuro |
| --- | --- | --- | --- |
| 1 | PolicyContextInspection | receipt (leitura), details (leitura), input (edição) | APROVADO |
| 2 | PolicyReview | active_details (leitura), receipt (leitura), comparison (leitura) | APROVADO |
| 3 | PolicyDecisionAudit | receipt (leitura), details (leitura) | APROVADO |
| 4 | CommandHistory | output (leitura), detail (leitura) | APROVADO |
| 5 | ModelCatalog | detail (leitura) | APROVADO |
| 6 | CampaignRunner | attempt_file_view (leitura), attempt_detail (leitura), provenance (leitura), manifest (leitura) | APROVADO |
| 7 | PathRunner | results (leitura) | APROVADO |
| 8 | ControllerComparison | output (leitura) | APROVADO |
| 9 | PolarisationControl | output (leitura) | APROVADO |
| 10 | PolicyApprovalConfirmation | details (leitura) | APROVADO |
| 11 | PolicyActivationConfirmation | details (leitura) | APROVADO |
| 12 | PolicyRecoveryConfirmation | details (leitura) | APROVADO |
| 13 | PolarisationApprovalConfirmation | text-0 (leitura) | APROVADO |
| 14 | PolarisationActivationConfirmation | text-0 (leitura) | APROVADO |
| 15 | CampaignManifestEditor | text (edição) | APROVADO |
| 16 | qkd.detector.boosting-offline | output (leitura), editor (edição) | APROVADO |
| 17 | qkd.sync.ridge.1s.h2s-offline | output (leitura), editor (edição) | APROVADO |
| 18 | qkd.sync.ridge.1s.h2s-shadow | output (leitura) | APROVADO |
| 19 | qkd.keyrate.actor-offline | output (leitura), editor (edição) | APROVADO |
| 20 | qkd.keyrate.actor-shadow | output (leitura) | APROVADO |
| 21 | qnet.routing.nominal-offline | output (leitura), editor (edição) | APROVADO |
| 22 | qnet.routing.nominal-shadow | output (leitura) | APROVADO |
| 23 | qnet.routing.prazo_longo-offline | output (leitura), editor (edição) | APROVADO |
| 24 | qnet.routing.prazo_longo-shadow | output (leitura) | APROVADO |
| 25 | qnet.allocator.persistence-offline | output (leitura), editor (edição) | APROVADO |
| 26 | qnet.allocator.persistence-shadow | output (leitura) | APROVADO |
| 27 | qnet.allocator.ridge-offline | output (leitura), editor (edição) | APROVADO |
| 28 | qnet.allocator.ridge-shadow | output (leitura) | APROVADO |
| 29 | qnet.compensation.calibrado.ridge-offline | output (leitura), editor (edição) | APROVADO |
| 30 | qnet.compensation.calibrado.ridge-shadow | output (leitura) | APROVADO |
| 31 | qnet.compensation.calibrado.mlp-offline | output (leitura), editor (edição) | APROVADO |
| 32 | qnet.compensation.calibrado.mlp-shadow | output (leitura) | APROVADO |
| 33 | qnet.compensation.desenvolvimento.ridge-offline | output (leitura), editor (edição) | APROVADO |
| 34 | qnet.compensation.desenvolvimento.ridge-shadow | output (leitura) | APROVADO |
| 35 | qnet.compensation.desenvolvimento.mlp-offline | output (leitura), editor (edição) | APROVADO |
| 36 | qnet.compensation.desenvolvimento.mlp-shadow | output (leitura) | APROVADO |

São 36 janelas/variantes, 56 campos, 112 casos por configuração e 1.120 verificações por execução. Um caso adicional cria um contexto e uma observação pelo serviço real em estado isolado, abre CAPTURED RECEIPT, rola sem selecionar texto, verifica a repintura e o fundo, mantém a leitura durante dois intervalos do watchdog, exporta o JSON e compara seu conteúdo. O diário permanece idêntico em bytes e a inspeção não autoriza seleção ou ativação: sete verificações adicionais aprovadas.

## Validação de origem

641 testes distintos aprovados: 75 casos novos de leitura/edição, 193 de contexto e ciclo de políticas/auditoria/comandos, 299 de sessões opcionais/catálogo/controle e 72 de campanhas/editor/resultados e feedback. Dois testes do detector foram inicialmente ignorados por falta da variável do ZIP; ambos foram repetidos com `ORION_QKD_ARCHIVE` apontando para o ZIP original e aprovados. Rodadas redundantes não entram na contagem. Ruff aprovou os arquivos de código modificados.

- Resultados: `outputs/text_scroll_fixes_20261005_v1/{scroll-pixels,policy-final,models-final,campaign-final,detector-original,context-pixels-final}.xml`.
- Matriz Qt offscreen: 100%, 125%, 150% e 200%; pasta `source-scale-*-paint-final`.
- Qt Windows com opacidade normal: 100% e 150%; pastas `native-scale-1-paint-final` e `native-scale-1.5-paint-final`.
- Diferença desta intervenção: `outputs/text_scroll_fixes_20261005_v1/changes.patch`, com hashes em `change-manifest.json`, calculada contra as 133 cópias recebidas, não contra Git HEAD.

## Limites da verificação

Os testes medem a região de pintura real recebida depois dos eventos de rolagem. Uma captura de widget força pintura, portanto é usada para conferir cores e legibilidade, não como prova isolada de repintura ao rolar. A imagem foi inspecionada e o recibo está legível no tema claro. O congelamento relatado não foi reproduzido de forma idêntica no ensaio inicial; a correção amplia e reforça o tratamento que faltava nesses leitores.

O auxiliar de Computer Use não iniciou: `node_repl kernel exited unexpectedly`, com `windows sandbox failed: helper_unknown_error: setup refresh had errors`, mesmo após reconexão. Assim, não houve inspeção externa por captura da janela nem injeção de mouse do sistema operacional. A verificação nativa utiliza as próprias classes Qt, eventos de roda/teclado e imagens renderizadas dos widgets; não confirma execução em outra máquina ou em todos os drivers de vídeo. A aceitação manual do pacote permanece separada dos testes automáticos.

## Pacote Windows

Novo executável e pacote portátil gerados em `release/windows/5.38.0-native-20261005-140633`. O executável anterior foi preservado.

| Verificação no executável distribuído | Resultado |
| --- | --- |
| Autoteste principal | APROVADO — 28 verificações principais |
| Leitores e editores nos dois temas | APROVADO — 36 janelas, 56 campos, 112 casos, 1.120 verificações |
| CAPTURED RECEIPT real, watchdog e exportação | APROVADO — sete verificações adicionais |
| Resultados, leitura e tooltips da intervenção anterior | APROVADO |
| Topologia funcional, demonstração e adaptação da interface | APROVADO |
| Modelos opcionais, sem pacotes e com ZIPs originais | APROVADO |
| Campanha temporal, cancelamento, histórico e recibos | APROVADO — 25 verificações |
| Estresse do controlador em 20 rodadas | APROVADO |
| Diagnósticos funcionais do roadmap | APROVADO |
| Integridade do ZIP | APROVADO — CRC de 3,892 arquivos e SHA-256 |
| Aceitação automática local | APROVADO — revisão manual ainda pendente |
| Executor físico | INDISPONÍVEL |

O diagnóstico de tempo do roadmap completou os 20 ciclos, mas 20 excederam o orçamento de 10 ms (média 161.238 ms). A amostra não é adequada para certificar p99. Esta correção da interface não estabelece cumprimento de prazo em tempo real. A aceitação continua com `scope=local_software_checks_only`, `manual_review=pending` e `independent_machine_verified=false`.

- [Executável novo](E:/Codex/2026-08-27/e/work/NISQ-Predictive-Twin/release/windows/5.38.0-native-20261005-140633/ORION-Q/ORION-Q.exe) — usar junto da pasta `_internal`.
- [ZIP portátil completo](E:/Codex/2026-08-27/e/work/NISQ-Predictive-Twin/release/windows/5.38.0-native-20261005-140633/ORION-Q-5.38.0-native-windows-x64.zip) — fechar a versão antiga, extrair todo o ZIP e executar `ORION-Q/ORION-Q.exe` da pasta extraída.
- [Autoteste do executável](E:/Codex/2026-08-27/e/work/NISQ-Predictive-Twin/release/windows/5.38.0-native-20261005-140633/self-test.json).
- [Aceitação automática e limites](E:/Codex/2026-08-27/e/work/NISQ-Predictive-Twin/release/windows/5.38.0-native-20261005-140633/native-acceptance/acceptance-report.json).

SHA-256 do executável: `dbd1e84c00c55b5ee063ea9ecac4e9a3a25b289067af257ddd0ecd956480c2ce`. SHA-256 do ZIP: `233c50f525c374fb48ab1513bc5cec283fae09d3ee104470d150b9912da6a1d8`. Tamanho do ZIP: 397,939,254 bytes.

A imagem a seguir é renderizada pelo próprio widget do executável após a roda do mouse, com o recibo real e sem seleção. Ela documenta o fundo e a legibilidade; a prova de repintura está nos eventos de pintura medidos antes da captura.

![Recibo real após rolagem no executável](E:/Codex/2026-08-27/e/work/NISQ-Predictive-Twin/release/windows/5.38.0-native-20261005-140633/text-scrolling/context/captured-receipt-after-wheel.png)

