# Q04 — central local de comandos: primeiro incremento

Data: 21/09/2026. Escopo: operações de inventário do sandbox pela console, não hardware.

## Uso e estados

Em **ACCESS & AUDIT → COMMAND CENTER**, consulte os últimos 50 comandos admitidos de cadastro de nós e criação/alteração de enlaces. Selecione uma linha para ler o ID completo, parâmetros, eventos UTC e hash. O painel usa snapshots atualizados pelo provider em segundo plano; o REFRESH da tela de auditoria permite solicitar nova leitura. Não existe outro menu lateral.

Fluxo: `REQUESTED → RUNNING → CONFIRMED | UNKNOWN`, ou `REQUESTED → REJECTED` quando a capacidade é revogada antes de invocar o backend. Não são estados derivados de mensagens do ledger. A admissão ocorre depois da validação do contrato, dentro da tarefa aceita pelo provider, não no instante do clique. Se o provider estiver ocupado, não cria uma fila ou registro fictício. Solicitações malformadas ou sem capacidade inicial são recusadas antes de entrar no diário.

- **REQUESTED:** solicitação validada persistida; ainda não comprova invocação.
- **RUNNING:** marco persistido imediatamente antes da chamada. Após reinício, significa estado não resolvido; não prova que haja processo vivo.
- **CONFIRMED:** o serviço retornou persistência simulada e a releitura da topologia corresponde à resposta completa e à versão esperada + 1; o hash dessa topologia acompanha o evento. Uma mudança posterior não é revertida nem impede consultar a confirmação histórica.
- **UNKNOWN:** erro na chamada ou confirmação. Pode haver alteração já gravada; conferir a topologia antes de qualquer nova solicitação. Até erros que poderiam indicar recusa são conservadoramente classificados como desconhecidos depois da invocação.
- **REJECTED:** capacidade revogada antes da chamada do serviço.

Não há retry, cancelamento, rollback, reconciliação automática nem reexecução pelo painel. Falha de gravação do estado inicial impede a chamada. Falha ao gravar o estado final pode deixar RUNNING apesar de efeito persistido; isso não é sucesso confirmado. Falha posterior na atualização visual não apaga uma confirmação já persistida. Eventos intermediários podem não aparecer durante uma operação breve: o provider publica um snapshot depois de concluir a tarefa; o histórico preserva todos os marcos.

## Persistência e limites

Tabela `console_commands` no diário local existente, criada somente no primeiro comando válido. Cada registro contém seu histórico limitado de até três estados e hash do JSON canônico. Transições são serializadas por transação SQLite e estados terminais não podem ser alterados pela API. Isso não é uma trilha criptograficamente inviolável nem assinatura de autoria.

Consulta por conexão somente leitura, sem criar tabela/banco ou inicializar esquema. Ausência da tabela significa diário anterior a Q04; banco ausente, corrompido ou registro inválido gera indisponibilidade explícita. Exibição limitada a 50 registros recentes, sem paginação/exportação específica nesta etapa. Troca para fonte sem capacidade limpa detalhes e desabilita o botão. Registros persistidos sobrevivem à reinicialização; não se inferem resultados a partir de campos exibidos.

Cobertos somente `add_simulated_node` e `configure_simulated_link` pelo provider da console. Não cobre chamadas diretas ao serviço/API, campanhas, IBM, replay, purificação, swapping, comandos físicos nem seleção de política. Campanhas mantêm seu diário próprio. Não são comandos de equipamento: endereços de inventário continuam metadados.

Q04 permanece parcial: faltam ampliar os verbos/recibos suportados, prazos e acompanhamento geral. Esta fronteira prepara a futura ativação de políticas Q05, incluindo os dois modelos indicados pelo usuário, mas não os carrega nem atribui desempenho a eles.

## Reconciliação por evidência — API local e CLI (26/09/2026)

Para `REQUESTED`, `RUNNING` e `UNKNOWN`, a nova consulta compara o recibo selecionado
com a topologia atual em **uma única transação SQLite somente leitura**. Nenhum
estado original é alterado. Use o ID e `record_sha256` exibidos nos detalhes do
COMMAND CENTER; se o recibo mudou desde a seleção, atualize a seleção antes de
consultar novamente. A interface também oferece esta consulta no incremento descrito abaixo.

No PowerShell, a partir da raiz do repositório e com o ambiente Python do projeto:

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
python tools/reconcile_console_command.py `
  --journal 'CAMINHO_DO_DIARIO_EXISTENTE.sqlite' `
  --command-id 'UUID_DO_COMANDO_SELECIONADO' `
  --command-sha256 'SHA256_DO_RECIBO_SELECIONADO' `
  --output 'reconciliacao-nova.json'
```

Informe o diário de controle usado pela console, não o banco de outra campanha.
O JSON de destino deve ser novo; arquivo existente é recusado. O comando não cria
um diário ausente, não inicializa esquema e não carrega modelos. A exportação é
uma fotografia histórica independente; não atualiza o COMMAND CENTER.

O relatório `orion.command_reconciliation.v1` incorpora recibo original e hash,
topologia completa e hash (ou ausência da linha), instante UTC da observação,
campos esperados/observados e relação entre versões. A comparação verifica:

- **Nós:** ID, papel e metadados de inventário (endereço, porta, origem e escopo).
  Telemetria dinâmica e memória não fazem parte da pós-condição do cadastro.
- **Enlaces:** ID, extremos ordenados, distância, fidelidade nominal, atividade
  e versão do esquema, tanto para criação quanto para alteração.

`matches_current_state`, `differs_current_state`, `target_absent` e
`topology_absent` descrevem somente a fotografia. Mesmo uma coincidência na
versão esperada + 1 mantém `command_outcome=UNRESOLVED` e
`command_effect_proven=false`: não há associação causal persistida entre o
comando e o evento de topologia. Estado diferente/ausente também não prova falha;
outro comando pode ter alterado o inventário posteriormente.

Recibo ausente, alterado, inválido ou já resolvido é recusado. Tabela ausente,
JSON duplicado/não finito, divergência entre índices e payload ou limites excedidos
(32 KiB por recibo; 2 MiB por topologia) produzem erro, não uma conclusão de ausência.
Hashes locais detectam inconsistência, mas não autenticam a origem dos dados.
A consulta pressupõe um diário local confiável e não é um carregador de bancos
SQLite arbitrários. Não autoriza retry, replay, cancelamento, rollback ou Q05.

[Validação deste incremento](../results/orion_q_command_reconciliation_20260926.md).

## Consulta nativa no COMMAND CENTER (26/09/2026)

Em **ACCESS & AUDIT → COMMAND CENTER**, selecione um comando `REQUESTED`,
`RUNNING` ou `UNKNOWN` e clique **CHECK CURRENT STATE**. A consulta usa o diário
da fonte de serviço ativa e o hash do recibo selecionado, em segundo plano.
Não há consulta automática ao selecionar uma linha ou abrir o painel.

A aba **ORIGINAL RECEIPT** conserva o recibo. **OBSERVED EVIDENCE** mostra o JSON
completo da fotografia e os hashes; o estado abaixo continua **UNRESOLVED**,
mesmo quando os campos coincidem. A data é da observação histórica, não uma
indicação de que a topologia continua igual. CONFIRMED e REJECTED ficam disponíveis
para inspeção, mas não habilitam a consulta de reconciliação.

**EXPORT EVIDENCE** grava exatamente a fotografia exibida em um arquivo JSON
novo, também em segundo plano. Não relê a topologia nem sobrescreve arquivos.
Cancelar a janela de destino não grava nada. Uma atualização de topologia ou de
outro comando conserva a fotografia; selecione CHECK CURRENT STATE novamente
para obter uma observação nova.

Trocar fonte, perder a capacidade de leitura, selecionar outro recibo, alterar ou
remover o recibo selecionado, limpar a seleção ou fechar o painel limpa a evidência.
Uma leitura atrasada é descartada. Há uma operação por vez, sem fila. Mudanças
ocorridas durante a escolha do arquivo cancelam a exportação antes da gravação.
Se a gravação já foi aceita e começou, ela pode terminar no destino escolhido com
a fotografia capturada; seu retorno não reaparece na nova fonte/seleção. Reabrir
o painel exige uma nova consulta explícita.

Não há reexecução de comando, confirmação inferida, controle físico, ativação de
modelo ou Q05. [Validação da interface](../results/orion_q_command_reconciliation_ui_20260926.md).
