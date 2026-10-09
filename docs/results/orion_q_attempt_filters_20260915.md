# Filtros de tentativas operacionais — 15/09/2026

Implementação iniciada em 14/09, com regressão e build em 15/09. A validação final da distribuição foi retomada em 19/09/2026. A pasta de distribuição conserva o identificador de início `5.38.0-attempt-filters-20260914`.

A aba ATTEMPTS agora combina prefixo literal do ID de campanha e estado operacional. Enter/REFRESH aplica a consulta e volta à primeira página. Alterações não aplicadas desabilitam navegação/exportação e recebem aviso explícito. OLDER/NEWER preservam os filtros; a troca de fonte limpa filtros, seleção e respostas tardias. A exportação individual continua exigindo releitura e hash selecionado.

## Validação

- Regressão ampliada: **261 testes aprovados em 179,63 s**, `outputs/software_validation_attempt_filters/accepted-tests.xml`.
- Rodada focada: **107 testes aprovados em 41,96 s**, `outputs/software_validation_attempt_filters/tests.xml`. Não somar a uma regressão ampliada que inclua os mesmos casos.
- `tests/test_attempt_filters.py` cobre filtros inválidos, prefixos literais sem curingas/injeção SQL, combinação AND, paginação com inserção concorrente, corrupção correspondente, limites de auditoria filtrada, aplicação explícita, consulta vazia, mudança de estado, troca de fonte durante leitura, mudança de filtro no seletor de exportação e layout.
- Capturas `filters-640.png` e `filters-1080.png` inspecionadas com tema/fontes reais na mesma pasta. Lista e JSON usam rolagem em janelas pequenas.
- Ruff aprovado; sintaxe PowerShell e YAML do workflow verificadas. Não foi executado workflow remoto nem alterada política de execução do Windows.
- Na retomada de 19/09, paridade estrutural confirmada entre o código-fonte atual e os 72 módulos `quantum_autopilot` presentes no PYZ, com otimização 1 e normalização somente dos nomes de arquivo. Nenhuma divergência encontrada; o build de 15/09 estava concluído.

## Verificação da distribuição em 19/09/2026

O roteiro `outputs/software_validation_attempt_filters/frozen-worker/report.json` aprovou **17 verificações**, incluindo campanhas pelo EXE, replay, cancelamento, histórico, exportação, verificação de arquivo externo e filtros operacionais. O controlador desse roteiro usa Python fonte e despacha o EXE real como worker: não representa uma interação manual com a GUI empacotada.

SHA256 do executável: `85e5decc27028080a67c53fcca94b0ce85bb297d89f7ab7a2b380b49f6d1155a`.

Autoteste separado do executável aprovado em **19 verificações**; estresse sintético separado aprovado em **20 rodadas**, ambos com saída 0. Evidências em `release/windows/5.38.0-attempt-filters-20260914/self-test.json` e `stress-validation/`, usando estado isolado.

## Artefato portátil

ZIP: `release/windows/5.38.0-attempt-filters-20260914/ORION-Q-5.38.0-native-windows-x64.zip`, **283190462 bytes**, **3833 arquivos**, CRC integral verificado.

SHA256 do ZIP: `7497f3cd5f64ec26d3e9bd4ea484b998f97fba592f848ac2aa0fa227b373df32`.

Extrair toda a pasta antes de abrir ORION-Q.exe; manter `_internal`. Arquivos exportados devem ficar fora da pasta do programa. A distribuição acadêmica não é assinada digitalmente.

Diagnóstico nativo final aprovado em **51,56 s**, código 0, em 19/09: integridade dos 3832 arquivos declarados (excluindo o próprio manifesto), autoteste e 20 rodadas sintéticas. Relatório: `native-acceptance/acceptance-report.json`, na raiz da distribuição. `automatic_passed: true`; `acceptance_status: manual_review_required` e `independent_machine_verified: false`, pois a execução ocorreu neste computador de desenvolvimento.

## Limites

A consulta não constitui auditoria integral: somente registros retornados são verificados. JSON malformado não é omitido silenciosamente pelo filtro; um registro bem formado fora dos critérios pode não ser examinado. Consulta parametrizada sobre o JSON existente, sem migração ou índice adicional. Até 20 correspondências por página na UI, mas a busca pode varrer muitos registros; não há garantia de latência constante.

A paginação não congela os estados. Uma tentativa OPEN concluída entre leituras pode deixar o conjunto filtrado. OPEN nunca comprova processo ativo. Falhas operacionais não são amostras, zeros de fidelidade ou timeouts científicos. Não há contagens globais, busca por período/UUID, exportação em lote ou reconciliação automática nesta etapa.

O motor científico e os modelos WDM não foram alterados. Este incremento melhora a consulta operacional, sem acrescentar validação experimental ou capacidade de atuação em hardware. Teste manual em outra máquina Windows permanece pendente. Nenhum commit criado; distribuições anteriores preservadas. [Guia](../product/orion_q_campaign_attempts.md).
