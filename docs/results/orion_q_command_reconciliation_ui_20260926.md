# Q04 — consulta nativa de reconciliação (26–27/09/2026)

Implementação e testes em 26/09; consolidação e auditoria final em 27/09/2026.

O COMMAND CENTER agora oferece CHECK CURRENT STATE e EXPORT EVIDENCE para
comandos de inventário não resolvidos. Consulta e gravação usam tarefas em segundo
plano, com uma operação por vez e sem fila. [Uso](../product/orion_q_commands_q04.md).

## Comportamento entregue

A consulta usa o diário da fonte de serviço ativa, UUID e hash do recibo selecionado.
A API Q04 anterior faz a leitura transacional consistente e valida os dados.
ORIGINAL RECEIPT mantém o recibo; OBSERVED EVIDENCE mostra a fotografia JSON.
O resultado continua UNRESOLVED mesmo quando os campos coincidem. Abrir o painel
ou selecionar um registro não executa consulta. CONFIRMED e REJECTED não habilitam
CHECK CURRENT STATE.

Mudança de fonte, capacidade ou seleção, alteração/remoção do recibo selecionado,
limpeza da seleção e fechamento descartam a evidência e resultados atrasados.
Atualizar outro comando preserva a seleção pelo UUID. Atualizar a topologia não
reescreve uma observação histórica: uma nova leitura exige nova ação explícita.

A exportação captura o texto exibido antes de abrir a escolha de destino, usa
criação exclusiva e não relê SQLite. Alterações de contexto durante a escolha
cancelam a gravação. Uma exportação já aceita e iniciada pode concluir seu arquivo
capturado; seu retorno é descartado se o contexto tiver mudado. O fechamento da
janela principal também fecha/invalida o painel. Nenhum comando é reenviado.

## Validação

- **299 testes aprovados**, zero falhas, erros ou skips; 597.22 s
  no XML final `outputs/command_reconciliation_ui_20260926/regression-final.xml`.
- **22 testes novos** de interface: consulta explícita fora da thread gráfica,
  preservação do SQLite/recibo, estados terminais, seleção alterada/removida,
  recibo alterado no banco, invalidação por fonte/capacidade/fechamento,
  ausência de fila, exportação capturada fora da thread gráfica, recusa de
  sobrescrita, mudanças durante o diálogo de arquivo e conclusão tardia de uma
  gravação já aceita. Integração da janela principal também verificada.
- Regressão ampliada: API de reconciliação, ciclo de comandos, inventário,
  enlaces, provider, capacidades, fontes, telas, integração da console,
  topologia, sandbox e barreira física. Ruff aprovado em quatro arquivos Python.
- Primeira rodada: 46 testes aprovados, preservados em `targeted-initial.xml`.
- Rechecagem final: 22 testes gráficos aprovados em `gui-final.xml`, após ajustar
  a coleta para ambientes sem PySide6. Essa contagem se sobrepõe à suíte ampliada;
  não deve ser somada a ela. A dependência gráfica continua opcional.
- Capturas nativas 640×500 e 1080×740 revisadas, com controles e JSON legíveis,
  em `final-native/`. O JSON exportável do cenário está em `final-native/evidence.json`.
- Workflow YAML e script PowerShell de build validados; ambos incluem as suítes
  de reconciliação. Não houve execução remota da CI nem reconstrução de EXE.

A evidência visual e o JSON usam um inventário temporário controlado pelos testes.
São verificações de software em simulação, não novas execuções científicas ou
medições de melhoria dos modelos. As consultas/exportações não alteraram o diário.

## Preservação e roadmap

A auditoria `outputs/command_reconciliation_ui_20260926/verification.json` compara
os arquivos anteriores ao incremento, incluindo alterações já existentes fora de
commits. Ela verifica também os 240 relatórios SO(3), 15 fontes congeladas,
summary, protocolo e XML histórico dos 500 testes.

Q04 permanece parcial fora dos verbos de inventário e desta observação sem inferir
causalidade. O próximo incremento é a preparação/revisão auditável Q05 em simulação,
com proposta vinculada à fonte, política e versões, baseline explícito e invalidação
de proposta desatualizada. Ativação, readback e rollback seguem pendentes.
Modelos continuam opcionais, OFF por padrão, sem atuação física.
