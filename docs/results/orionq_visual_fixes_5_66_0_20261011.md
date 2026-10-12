# OrionQ 5.66.0 — correções de leitura e viewport, corrected-r2

Data: 11 de outubro de 2026. Os cortes da auditoria 5.65 foram corrigidos nos
painéis flutuantes e no diálogo HOUSEKEEPING / SCIENCE. A revisão corrected-r2
acrescenta a correção da posição após troca de tema encontrada no gate Windows.
O código Qt nativo Windows e os widgets Linux offscreen foram verificados;
os binários e os gates completos de release possuem relatórios separados.

| Caso reproduzido | Causa | Correção |
|---|---|---|
| Título CIMATEC oculto com terminal aberto em 1280 × 720 | O painel usava o tamanho completo do widget dentro do QScrollArea. O foco e o movimento posterior do conteúdo deslocavam o título para fora do viewport. | Interseção visível dos ancestrais controla tamanho e arraste; foco no botão de fechar e timer único ajustam a posição após scroll automático. |
| Épocas e vetores elididos em HOUSEKEEPING 640 × 480 | ResizeToContents consumia largura; Valor e Origem ficavam estreitos e sem quebra. | Colunas proporcionais com mínimos, ElideNone e QTextLayout com quebra; altura considera todos os caracteres recebidos. |
| Vetores presentes no modelo mas invisíveis após scroll | O scroll externo genérico estendia o viewport da tabela abaixo da região visível. | Especialização local mantém cabeçalho, tabela e FECHAR visíveis. OGS e Missão/RF têm scroll próprio. AdaptiveDialog genérico permaneceu igual. |
| Posição alterada após trocar tema | Restore era executado enquanto ancestrais ocultos tinham geometria provisória: bounds 632 × 251 limitavam o ponto correto (422, 35) a (84, 0). O layout final 1380 × 555 não recuperava o valor perdido. | Somente restore oculto conserva a posição solicitada; o timer existente a aplica uma vez após Show/layout. Close/shutdown removem pendências; estado salvo preserva o ponto. O clamp inferior inclui o deslocamento de 1 pixel da barra. |
| Teste de recalibração lia relatório incompleto | Demo.done confirma o backend, mas não a apresentação Qt assíncrona. A leitura ocorria com ready=false, três itens pendentes e aviso de publicação anterior. | Apenas a fixture de teste passou a aguardar Demo.settle antes ler o relatório. Todas as verificações de fases, contador, hash e valores exatos por enlace foram preservadas. Nenhum resultado foi fabricado ou fila forçada. |
| `Â` nos metadados cartográficos | Dupla codificação de separadores/copyright. | Apenas os metadados dos dois JSONs foram corrigidos; geometria permaneceu idêntica. |

O delegate guarda no máximo 512 layouts, reutiliza QTableWidgetItems e conserva
o scroll. Apenas linhas alteradas recalculam altura; mudança de largura agenda
uma atualização completa coalescida. Eventos, valores e cadência do backend,
fonts, logos, dados científicos e bloqueio físico não foram alterados por esses
ajustes de layout.

## Resultados finais

| Verificação | Resultado | Escopo |
|---|---:|---|
| Novas regressões de restore oculto/posição inválida/close-reopen | 3 aprovadas, 0 puladas/falhas, 0,88 s | Qt Windows; ponto válido preservado, ponto inválido limitado ao viewport real e pendências removidas. |
| Teste oficial de tema intacto e entrada autenticada | 2 aprovados, 20,95 s | A posição permaneceu (422, 35) antes/depois de settle e após reconstrução do tema. |
| Corpus Windows final | 136 aprovados, 0 pulados/falhas, 95,63 s no console | Nove módulos de layout/lifecycle/atualização mais os 22 casos de topologia com serviço real. |
| Componentes Linux final | 114 aprovados únicos, 0 pulados/falhas, 35,387 s no JUnit | Ubuntu-24.04/WSL2, UID 10001, Python 3.12.3, Qt/PySide6 6.12.0; offscreen 800 × 800 lógico, DPR 1. |
| Interface nativa Windows | 4/4 aprovados, 41,14 s | Entrada normal autenticada, 76 nodes, fonte simulada, janela solicitada 1280 × 720, CIMATEC/CBPF/GEO-AMERICAS-SIM e HOUSEKEEPING 640 × 480. |
| Interface nativa Windows 200% | 4/4 aprovados, 42,75 s | Monitor U34G4C 3440 × 1440 físico, QScreen 1720 × 720 lógico e área disponível 1720 × 696, DPR 2. |

Os tempos ocorreram durante compilações concorrentes e descrevem execução de
testes, não latência de interação ou benchmark. As campanhas não demonstram
eliminação universal de pausas; desempenho prolongado e executáveis são
qualificados separadamente.

Cada campanha nativa realizou nove readbacks completos em topo/fundo/topo dos
três painéis, verificou três títulos inteiros e épocas/vetores em células
inteiramente visíveis. Scroll causou Paint sem hover, seleção de texto ou
repaint manual. A tabela inteira fica acima do FECHAR. Capturas também foram
inspecionadas em pixels; leitura do modelo sozinha não estabelece legibilidade.

No Windows normal, QScreen é 3440 × 1440, área disponível 3440 × 1392 e DPR 1.
A 200%, a janela solicitada de 1280 × 720 lógicos excede a altura disponível de
696; essa campanha qualifica os títulos, os readbacks e o diálogo interno de
640 × 480, sem afirmar legibilidade da barra inferior fora do monitor. O excesso foi
pedido pelo próprio harness: Driver.attach executa window.resize(*size) depois
da preparação normal da janela. O launcher utiliza 90% da área disponível e
fit_to_screen; o mínimo de ConsoleWindow é 320 × 240, compatível com a altura de
696. DisplayObserver não desfaz no próximo turno um resize explícito do operador.
Esta execução não qualifica a barra fora do monitor nem identifica um impedimento
de tamanho mínimo ou uma falha da geometria inicial normal.
O Linux offscreen verifica comportamento e geometria; não comprova interação
Wayland/WSLg nem resolução física de um monitor.

## Tentativas preservadas e diagnóstico

O gate oficial inicial encontrou a perda real de posição após tema. A prova
antes/depois registra a posição estável antes de restore, bounds provisórios e
posição final correta. O teste oficial de tema não foi relaxado.

Uma execução conjunta teve 134 aprovados e duas falhas: leitura da recalibração
antes da materialização e, como consequência do teste interrompido, N-11 ficou
aberto no caso seguinte. Um diagnóstico separado reproduziu a mesma corrida nos
valores QBER: backend complete, ready=false, calibration_links ainda enfileirado
e aviso de atualização pendente. Após Demo.settle real, ready=true e fila vazia.
A fixture agora espera esse contrato já existente e o corpus final aprovou todos
os valores exatos. Esse diagnóstico não mede desempenho.

O novo teste com posição fora dos limites revelou corte inferior de 1 pixel da
barra; o clamp foi corrigido e a falha permaneceu registrada. As fontes 5.65
falharam nos 12 casos iniciais como esperado. Uma tentativa offscreen a 200%
falhou em 7/15 por limitar o QScreen padrão 800 × 800 a 400 × 400 lógicos; não
qualifica o monitor físico. Resultados antigos 125%/150%, 81/111 casos e nativos
anteriores ficam preservados como evidência anterior à correção de restore,
substituída para aceitação final por corrected-r2.

## Evidências e reprodução

- [Resumo final corrected-r2 com hashes](../../outputs/release-5.66.0-20261011/visual/summary-corrected-r2.json).
- [Corpus Windows final](../../outputs/release-5.66.0-20261011/visual/windows-visual-topology-corrected-r2.xml).
- [Diagnóstico da restauração antes/depois](../../outputs/release-5.66.0-20261011/visual/theme-restore-diagnostic.json).
- [Diagnóstico de apresentação da recalibração](../../outputs/release-5.66.0-20261011/visual/calibration-readback-diagnostic.json).
- [Relatório nativo normal](../../outputs/release-5.66.0-20261011/visual/native-viewport-corrected-r2/report.json).
- [Título CIMATEC](../../outputs/release-5.66.0-20261011/visual/native-viewport-corrected-r2/node-CIMATEC-top-restored.png).
- [Vetores completos a 200%](../../outputs/release-5.66.0-20261011/visual/native-scale-200-corrected-r2/housekeeping-GEO-AMERICAS-SIM-row-4.png).
- [JUnit Linux final](../../outputs/release-5.66.0-20261011/linux/visual-regression-final.xml).
- [Resumo Linux final](../../outputs/release-5.66.0-20261011/linux/visual-regression-final-summary.json).
- [Vetores Linux após scroll](../../outputs/release-5.66.0-20261011/linux/visual-captures-final/housekeeping-dark-640-updated.png).
- [Metadados com geometria igual](../../outputs/release-5.66.0-20261011/visual/geodata-metadata-repair.json).

```powershell
$env:QT_QPA_PLATFORM='offscreen'
python -m pytest tests/test_console_visual_readability.py tests/test_console_node_window_layout.py tests/test_console_satellite_telemetry.py tests/test_console_node_window_lifecycle.py tests/test_console_satellite_incremental.py tests/test_console_tick_layout_budget.py tests/test_console_wrapping_fields.py tests/test_console_floating_tick.py tests/test_console_inspector_performance.py tests/test_console_topology_live.py -q
```

Os imports Linux apontaram para checkout/src; dependências existentes foram
usadas somente para leitura em uma venv UID 10001. Hashes antes/depois das
fontes/testes e QtCore permaneceram iguais. As 16 capturas e o relatório de cada
campanha nativa foram copiados byte a byte com SHA-256/copy-proof; bancos,
certificados, credenciais, caches e logs privados não foram copiados.

O harness nativo privado deriva de tools/run_trl4_console_campaign.py e está em
work/release-5.66.0/visual-native-campaign.py. Ele seleciona quatro casos, usa
login normal pela sessão Windows e vincula hashes de dois widgets e dois JSONs;
não congela todas as fontes nem integra o pacote público. A campanha oficial
mais ampla e o manifest completo de release fornecem esse escopo. Fontes de
entrada seguem simuladas e identificadas, sem aquisição física, atuação ou
qualificação de hardware.

## Suplemento: espera dos leitores de auditoria no Windows

O segundo gate oficial preservado teve 39 casos aprovados e três falhas no
leitor de comandos. Todas ocorreram no prazo de oito segundos de `query`, antes
das verificações de conteúdo, seleção, scroll ou exportação. O timer Qt permaneceu
ativo, executando polls naturais; o Future ainda estava incompleto e os três
resultados válidos chegaram logo após esse prazo. A reprodução restrita conservou
as três falhas e não alterou produção ou testes.

A espera `processEvents(); QTest.qWait(20)` prejudicava a execução Python do
worker nessa combinação Windows/PySide. As três comparações mantiveram o mesmo
prazo de oito segundos e todos os asserts dos casos, trocando somente o mecanismo
de espera no harness diagnóstico:

| Espera | Casos | Duração do corpus | Fronteira autorizada por consulta |
|---|---:|---:|---:|
| `QTest.qWait(20)` original | 3 falhas | 30,05 s | 7,75–8,06 s |
| `time.sleep(.02)` | 3 aprovados | 3,51 s | 37,7–39,4 ms |
| `QTest.qWait(1)` + `time.sleep(.02)` | 3 aprovados | 3,48 s | 55,9–64,4 ms |
| `QTest.qWait(20)` + `time.sleep(.1)` | 3 aprovados | 4,10 s | Consultar amostras no diagnóstico |

O tempo de CPU da thread nas consultas com sleep ficou entre 15,625 e 31,25 ms,
com quantização do relógio Windows de 15,625 ms. Esses tempos diagnosticam a
inanição causada pela espera do teste; não qualificam latência geral, DPAPI ou
desempenho de hardware. O perfil entre threads incluiu chamadas Qt de espera e
não permite atribuir independentemente esse tempo ao cofre. Nenhum resultado,
poll, autorização ou processamento foi forçado pelo harness.

A única correção adicional foi no helper `wait` de
`tests/test_console_command_reconciliation.py`: `time.sleep(.02)` após
`app.processEvents()`, mantendo o prazo, o assert final e todos os critérios dos
testes. Nenhuma fonte de produção mudou. Após a correção, os **42 casos originais
de auditoria passaram**, sem skips ou falhas, em 43,427 s no JUnit. Essa prova é
suplementar aos 136 casos visuais; não deve ser somada aos resultados oficiais
da release nem às variantes diagnósticas como se fossem casos novos.

- [Diagnóstico e hashes antes/depois](../../outputs/release-5.66.0-20261011/visual/command-wait-diagnostic.json).
- [42 casos originais aprovados](../../outputs/release-5.66.0-20261011/visual/display-audit-final.xml).
- [Resumo suplementar, sem recontagem](../../outputs/release-5.66.0-20261011/visual/summary-corrected-r2-supplemental.json).

Reprodução do corpus aprovado:

```powershell
$env:QT_QPA_PLATFORM='offscreen'
python -m pytest tests/test_console_command_reconciliation.py tests/test_console_policy_decisions.py -q
```

Uma espera semelhante em `test_console_policy_review.py` permanece intacta:
sem falha reproduzida nesse módulo, não houve alteração preventiva.
