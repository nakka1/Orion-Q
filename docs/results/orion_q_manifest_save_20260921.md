# Salvamento explícito de configuração de campanha — 21/09/2026

## Entrega

**MANIFEST REVIEW → SAVE MANIFEST AS · NEW JSON** completa o ciclo carregar → editar → aplicar → salvar → reabrir. Salva a solicitação tipada já aplicada, não o rascunho em edição nem a prévia textual da fonte. O parser compartilhado verifica novamente o contrato UTF-8 de até 64 KiB antes da escrita.

A escolha de destino captura identidade da solicitação e contexto da fonte. Após o seletor, o adaptador recusa troca de manifesto/fonte, operação ativa e perda de capacidade. Criação exclusiva protege arquivos existentes, inclusive o original e destinos surgidos durante a escolha. Não cria diretórios. Cancelar a escolha não modifica o estado.

A escrita é executada no pool de fundo existente. Não há worker científico, nova tentativa, escrita SQLite ou atuação física. Durante a gravação, operações de campanha ficam bloqueadas; o loop gráfico continua disponível. Uma gravação aceita continua após troca da fonte, mas sua conclusão não preenche a mensagem da fonte nova. A identidade da solicitação, a revisão marcada e os resultados anteriores são preservados. Reabrir o JSON continua exigindo revisão e validação normal da topologia/versão.

## Verificação

- Regressão ampliada final: **498 testes aprovados em 411,87 s**, `outputs/software_validation_manifest_save/accepted-final-tests.xml`. A rodada anterior (`accepted-tests.xml`) registrou 497 aprovados e a falha do teste de clique descrita abaixo; a suíte completa foi repetida após a correção e terminou sem falhas. Conjuntos se sobrepõem: não somar contagens.
- Primeira rodada focada: **98 testes aprovados em 76,46 s**, `outputs/software_validation_manifest_save/tests.xml`; rodada anterior à adição dos dois últimos testes. Não somar conjuntos sobrepostos.
- Rodada específica final: **43 testes aprovados em 31,72 s**, `corrected-tests.xml`, incluindo 16 casos acrescentados nesta entrega. Um teste inicial chamava incorretamente `.click()` no componente customizado `Btn`; corrigido para `QTest.mouseClick` sem alterar o runtime. A falha e as rodadas anteriores foram preservadas nos relatórios, não ocultadas.
- Casos novos cobrem roundtrip, escolha cancelada, bloqueios após o seletor, preservação do original/diário/recibo/revisão, concorrência de destino, pasta/inexistência de diretório, falha parcial injetada e resposta tardia após troca de fonte.
- Capturas `manifest-save-640.png` e `manifest-save-1080.png` inspecionadas: botões acima da prévia rolável, sem sobreposição, com tema/fontes existentes. Não alterados menus laterais nem GRAPH/GEO/MATRIX.
- Ruff aprovado nos arquivos alterados. A suíte já está incluída nos gates Windows; workflow remoto não disparado.
- Build PyInstaller concluído em aproximadamente **294 s**; os **77 módulos** da aplicação conferem com o código-fonte compilado, sem divergências.
- Harness com worker empacotado: **25 verificações aprovadas**, incluindo editar → salvar → reabrir → despachar, paridade numérica, replay, cancelamento e preservação do diário nas leituras. O controlador do harness usa Python-fonte; o processo filho usa o executável Windows, sem Python externo. Isso não é homologação manual da GUI congelada em outra máquina.
- Autoteste nativo: **19 verificações aprovadas**. Estresse sintético: **20 rodadas aprovadas**, escopo de injeção de falhas de software, sem atuação física.

## Distribuição

Pacote `release/windows/5.38.0-manifest-save-20260921/ORION-Q-5.38.0-native-windows-x64.zip`: **283.221.301 bytes**, **3.833 arquivos**, CRC verificado. Extraia a pasta inteira antes de executar `ORION-Q.exe`; dependências e guias acompanham o aplicativo. Distribuições anteriores preservadas, sem commit automático.

- SHA-256 do ZIP: `c7b99ad5a8d1e817302356a5bffb7d045ff08055e6f56ffeb3c4c060166990ab`.
- SHA-256 do executável: `819e5fe21ce1099802dfca303b8bf54485c9aa1abaf76bd16fe61760270bebb8`.
- Aceite nativo pós-empacotamento aprovado em **55,04 s**, cobrindo integridade de 3.832 arquivos de conteúdo, autoteste e 20 rodadas de estresse. Relatório `native-acceptance/acceptance-report.json`: `automatic_passed=true`, `acceptance_status=manual_review_required`, `independent_machine_verified=false`.

## Limites

JSON de configuração não é recibo científico, backup da topologia, pacote de reprodução completo, assinatura ou pré-registro. Não gera IDs/sementes, adapta versões ou muda física/estatística. Configuração que já ficou incompatível com a topologia pode ser guardada; salvar não atesta executabilidade futura. O hash exibido identifica a solicitação estruturada e não os bytes do arquivo.

Sem autosave de rascunho, sincronização remota ou promessa de durabilidade sob queda de energia. Erro de gravação pode deixar arquivo incompleto; o programa sinaliza falha e não apaga esse arquivo automaticamente. Nenhum diário operacional em AppData foi usado nos testes. O aceite em outro computador Windows continua pendente. Este incremento não acrescenta dados medidos nem evidência WDM→fidelidade.

[Guia de uso e limites](../product/orion_q_temporal_campaign_ui.md).
