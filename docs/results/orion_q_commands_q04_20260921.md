# Q04 — primeira central de comandos de inventário

Entrega em 21/09/2026. Implementação limitada ao cadastro de nós e configuração de enlaces pelo provider nativo. Guia: [Q04 — comandos](../product/orion_q_commands_q04.md).

## Mudanças

- Diário SQLite de solicitações admitidas, IDs UUID, eventos UTC, estados e hashes; transições terminais protegidas por transação.
- Confirmação por resposta do serviço e releitura da topologia/versão. Exceção após a invocação vira UNKNOWN, sem afirmar rollback e sem retry automático. Falha de gravação antes da invocação impede o efeito; depois pode deixar estado não resolvido.
- COMMAND CENTER em ACCESS & AUDIT: últimos 50 comandos, tabela e detalhe integral selecionável, sem comandos de reexecução. Fonte sem capacidade limpa o painel. Consulta somente leitura, sem inicializar banco/tabela.
- Roadmap consolidado com Q04 parcial e Q05 pendente; [plano dos dois modelos](../product/orion_q_two_models_integration_plan.md) prioriza os artefatos QKD e Quantum Network declarados pelo autor, ainda não recebidos/avaliados. Nenhum modelo novo carregado ou treinado; nenhum resultado dos artigos presumido.

## Evidências

- Aceite final: **652 casos únicos aprovados** em três grupos disjuntos: inventário 86, console 68 e campanhas 498. IDs de todos os casos comparados com a primeira regressão de 652: nenhum removido, acrescentado à contagem ou duplicado. Grupo campanhas: **498 aprovados em 355,61 s**, `accepted-campaign-tests.xml`.
- Rodada focada inicial: **109 testes aprovados em 15,39 s**, `outputs/software_validation_commands_q04/tests.xml`.
- Inclui **26 testes novos** de transições, concorrência, persistência, tampering, falhas de diário e execução/releitura, revogação antes da chamada, fronteira de fonte e painel somente leitura. Os conjuntos de testes se sobrepõem; não somar as rodadas.
- Primeira regressão ampliada: **651 aprovados e 1 falha**, `accepted-tests.xml`. A falha expôs dependência de ordem entre testes do launcher: um teste deixava um marcador global de worker e outro simulava um executável sem esse suporte sem limpar o marcador. Corrigidos o isolamento do teste do launcher e a preparação explícita do cenário sem suporte. Runtime não foi relaxado para passar o teste. A rodada original foi preservada.
- A repetição monolítica foi interrompida por lentidão crescente nos testes visuais Qt; os tempos individuais da primeira rodada chegavam a minutos por layout. A verificação final foi dividida em processos separados para inventário, console e campanhas, sem remover casos. Grupo de inventário: **86 aprovados em 16,58 s**, `accepted-inventory-tests.xml`.
- Grupo console: **68 aprovados em 81,95 s**, `accepted-console-tests.xml`. Gates PowerShell/CI agora separam console legado e campanhas em processos, conservando todos os arquivos de teste que já continham, além do grupo Q04/inventário adicionado. Sintaxes verificadas novamente; CI remoto não executado.
- Capturas 640×500 e 1080×740 inspecionadas após instalação das fontes reais no teste. A primeira captura sem inicialização do tema foi substituída na rodada seguinte; não foi tratada como evidência visual válida. Tabela/detalhe têm rolagem, com avisos legíveis.
- Ruff aprovado nos módulos/testes alterados; sintaxe PowerShell/YAML dos gates validada. Workflow remoto não disparado; política de execução Windows preservada.
- Build PyInstaller concluído em aproximadamente **339 s**. **79 módulos** `quantum_autopilot` conferidos por comparação do código compilado com o fonte, sem divergências.
- Worker do executável: **25 verificações de regressão aprovadas** para campanhas, cancelamento, replay, histórico e salvar/reabrir manifesto. Esse harness usa controlador Python-fonte e filho EXE; não é teste manual congelado da nova central.
- Autoteste nativo: **19 verificações aprovadas**; estresse sintético: **20 rodadas aprovadas**, sem ação física. Testes em estado isolado, sem diário operacional em AppData.

## Distribuição

Pacote: `release/windows/5.38.0-command-center-20260921/ORION-Q-5.38.0-native-windows-x64.zip` — **283.234.884 bytes**, **3.835 arquivos**, CRC verificado. Extraia toda a pasta antes de abrir o executável.

- SHA-256 do ZIP: `e08ddbe205a1516d270036d4c46582f740d407f5dd2c485402c605bcd755fc4f`.
- SHA-256 do EXE: `da0d6d755ab4805d384245340eb7557485232b9876fb6ad8161f2efb31c9b289`.
- Aceitação nativa pós-empacotamento aprovada em **47,15 s**: integridade de 3.834 arquivos de conteúdo, autoteste e 20 rodadas de estresse. Relatório `native-acceptance/acceptance-report.json`: `automatic_passed=true`, `acceptance_status=manual_review_required`, `independent_machine_verified=false`.

## Limites

Q04 não está concluído: sem centralização de todos os verbos, reconciliação, cancelamento, paginação/exportação dedicada ou comandos físicos. REQUESTED/RUNNING após reinício não provam processo vivo. Hashes locais não autenticam autoria nem impedem reescrita externa. Guias e plano ML acompanham a distribuição; nenhum commit criado e distribuições anteriores preservadas. Homologação manual em outro computador Windows permanece pendente.
