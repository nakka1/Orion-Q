# Q04 — reconciliação por evidência local (26/09/2026)

Entregue API local e CLI para observar comandos de inventário não resolvidos,
com exportação de evidência independente. O estado do comando permanece intacto;
nenhum resultado é promovido a CONFIRMED. [Uso e contrato](../product/orion_q_commands_q04.md).

## Implementação e limites

`reconcile_command` recebe diário existente, UUID e hash do recibo selecionado.
Lê comando e topologia na mesma transação SQLite em modo somente leitura, valida
recibo, esquema/índices, limites e JSON estrito; recusa recibo alterado ou resolvido.
A exportação exclusiva incorpora os dois snapshots, hashes, instante UTC,
campos esperados/observados e relação entre versões. Não modifica o diário.

A coincidência dos campos não atribui causalidade ao comando, mesmo na versão
esperada + 1. Ausência ou divergência não prova falha. Todos os relatórios mantêm
`command_outcome=UNRESOLVED`. Hashes são verificações locais, não assinaturas.
O horário identifica a observação da fotografia transacional, não uma medição
física nem uma garantia de que o inventário permaneça igual após a leitura.

A consulta suporta cadastro de nó e criação/alteração de enlace. A CLI exige ação
explícita e arquivo novo. Não há integração gráfica da consulta neste incremento,
reexecução, retry automático, mudança de política, carregamento de modelos ou
atuação física. Q04 continua parcial e Q05 continua pendente.

## Validação local

- **243 testes aprovados**, sem falhas, erros ou skips, em 230.85 s
  conforme `outputs/command_reconciliation_20260926/regression-final.xml`.
- **48 testes novos:** estados não resolvidos, topologia/alvo ausente, coincidência,
  divergência, versões anteriores/posteriores, criação/alteração de enlace,
  recibo alterado/removido/resolvido, corrupção, limites, exportação e conexão
  que recusa escrita. A regressão inclui ciclo de comandos, inventário, editor
  de enlaces, leitura de campanhas, topologia, sandbox, provider, capacidades
  e barreira física.
- Concorrência real em SQLite/WAL: uma escrita feita entre as duas leituras não
  mistura fotografias. A próxima consulta exige hash atualizado e vê o commit.
- Ruff aprovado nos três arquivos Python novos.
- A primeira rodada teve 45 aprovações e dois erros de preparação de testes que
  tentavam alterar extremos de um enlace, operação já proibida pelo inventário.
  Os cenários foram corrigidos sem relaxar a regra; o XML inicial foi preservado.

O diagnóstico `outputs/command_reconciliation_20260926/diagnostic/` exporta três
fotografias de um inventário temporário controlado: topologia ausente, campos
coincidentes e divergência posterior. Nas três, os bytes do SQLite e o recibo
original ficaram inalterados pela consulta/exportação, com zero comandos emitidos.
Esses casos são verificações de software em simulação, não execuções científicas
adicionais nem medições de desempenho dos modelos.

## Preservação e próximo passo

`outputs/command_reconciliation_20260926/verification.json` registra hashes,
arquivos alterados/adicionados e a preservação da campanha SO(3): 240 relatórios,
15 fontes congeladas, summary, protocolo e XML histórico dos 500 testes.
A auditoria compara o estado anterior deste incremento, incluindo arquivos já
modificados ou ainda não rastreados pelo Git. Nenhum executável Windows foi
reconstruído e nenhuma CI remota foi executada.

Próximo incremento: consulta explícita no COMMAND CENTER, executada em segundo
plano, com invalidação por fonte/seleção e exportação da fotografia capturada.
Depois, preparação auditável de Q05 em simulação. Modelos permanecem opcionais,
OFF por padrão e sem atuação física.
