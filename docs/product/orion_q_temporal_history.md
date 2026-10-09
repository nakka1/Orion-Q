# ORION-Q — histórico de campanhas temporais

14/09/2026. Incremento de produto em Q14: consulta de **campanhas simuladas concluídas**, sem novos experimentos ou mudanças no protocolo estatístico.

## Uso

1. Na fonte **Existing service**, abra **TOPOLOGY → TEMPORAL CAMPAIGNS → SAVED CAMPAIGNS**.
2. Clique **REFRESH** para carregar os registros mais recentes. **OLDER/NEWER** navegam páginas de até 20 registros.
3. Selecione uma linha e clique **OPEN / VERIFY**, ou dê duplo clique. O índice mostra sequência, ID e hash, inicialmente como **UNVERIFIED INDEX**.
4. Depois da leitura integral e verificação, **CONFIRMED COMPARISON** apresenta os resultados originais. A mensagem **HISTORICAL** distingue consulta de execução; o manifesto e a versão congelada permanecem disponíveis.
5. **EXPORT JSON** salva o recibo completo em um arquivo novo. Não sobrescreve arquivos existentes.

É possível abrir um resultado de uma versão antiga da topologia. Isso não restaura essa versão, não altera o workspace atual e não inicia um worker. O manifesto histórico é somente leitura e não se torna uma solicitação executável: uma nova campanha exige carregar e revisar outro manifesto explicitamente.

## Busca por início do ID — atualização de 14/09/2026

No campo acima da tabela, informe o início do ID e pressione **Enter** ou **REFRESH**. Exemplo: `memory-` localiza os IDs que começam exatamente com esse texto. A busca diferencia maiúsculas/minúsculas, não remove espaços automaticamente e trata `%`, `_` e aspas literalmente, sem padrões SQL ou expressões regulares. Campo vazio seguido de REFRESH retorna a todos os IDs. Limite: 128 caracteres, sem caracteres de controle.

**OLDER/NEWER** mantêm o filtro aplicado. Uma nova pesquisa reinicia na primeira página. Enquanto o texto editado não for aplicado, navegação e abertura ficam desabilitadas; a nota da tabela identifica a consulta aplicada. Trocar a fonte limpa tanto o filtro aplicado quanto o texto ainda não enviado. Erro de leitura não deixa cursores ou linhas da consulta anterior ativos.

A consulta continua em segundo plano, com até 20 resultados por página, sem desserializar os recibos. Esse limite restringe os resultados retornados, não a quantidade de linhas que o SQLite pode examinar: o filtro literal usa `instr`, e não foi feito benchmark em diários de milhões de registros. O resultado continua como **UNVERIFIED INDEX** até a abertura e verificação integral. Filtrar não é selecionar uma amostra científica nem produzir uma comparação entre campanhas.

## Fronteiras e integridade

- O diário é o SQLite do serviço local ativo. Não há seletor de bancos arbitrários nem importação de recibos externos nesta interface.
- A capacidade `read_temporal_campaign_history` é separada de `run_temporal_campaign`. Um launcher sem suporte de execução pode consultar o histórico do serviço, mas não executar campanhas. Telemetria medida e injeção visual não ganham essas capacidades por declarar rótulos no payload.
- O índice consulta apenas três colunas, sem desserializar todos os JSONs. A paginação usa a sequência descendente, cursor estritamente validado e limite de até 50 no backend; a UI usa 20.
- A inserção de novos registros não desloca o cursor de páginas mais antigas. Não é um snapshot global imutável: **REFRESH** e o retorno a páginas recentes podem mostrar novas inserções; exclusões externas podem reduzir uma página.
- Ao abrir, são verificados os hashes da solicitação, da fonte congelada e do registro, a identidade da campanha e a correspondência com o hash selecionado no índice. Registro alterado ou removido desde a listagem é recusado.
- Hash local não é assinatura digital nem autenticação de origem. Alguém com escrita no banco pode recalcular hashes; não há alegação de inviolabilidade ou atestação física.
- Leituras e exportação ocorrem fora da thread gráfica. Troca de fonte, perda de capacidade e cancelamento invalidam resultados tardios. As tabelas mantêm rolagem em janelas pequenas, sem reduzir a fonte para encaixar.

## Escopo e pendências

Somente recibos completos persistidos aparecem em SAVED CAMPAIGNS. O [diário operacional ATTEMPTS](orion_q_campaign_attempts.md), adicionado em incremento separado, registra tentativas da console e suas observações de falha/cancelamento sem inseri-las nesta tabela científica. Há busca pelo início do ID de recibos; pesquisa por data, igualdade exata ou metadados científicos ainda não está disponível. Não há retomada parcial, fila de campanhas, exclusão pela UI, exportação em lote ou comparação estatística entre campanhas diferentes. Consultar resultados não significa revalidar hipóteses ou confirmar a relação WDM → desempenho quântico real.

## Verificação e distribuição

`tests/test_temporal_history.py` cobre paginação com inserção concorrente, limites, índice não verificado, abertura após mudança de versão, ausência de reexecução, exportação, adulteração, remoção, troca de fonte durante leitura, launcher sem worker e layout em 640×500 e 1080×740.

A primeira regressão deste incremento teve **90 testes aprovados** em 75,74 s, incluindo histórico, painel, backend e capacidades. Evidências: `outputs/software_validation_temporal_history/initial-tests.xml`, `history-640.png` e `history-1080.png`. São verificações de software com fixtures simuladas, não resultados científicos novos.

A regressão adicional de telas, serviço, integração e launcher teve **30 testes aprovados** em 21,10 s (`integration-tests.xml`): 120 testes nesta etapa, sem contar as verificações do executável. Ruff passou nos módulos alterados e a sintaxe PowerShell do build foi validada. As duas capturas de layout foram inspecionadas.

A distribuição deste incremento usa a pasta `release/windows/5.38.0-temporal-history-20260914/`, separada da versão anterior com worker. O relatório `frozen-acceptance/report.json` nas evidências verifica o worker real do executável a partir do controlador em Python; a consulta histórica nesse roteiro usa o controlador fonte sobre o recibo produzido pelo worker. O self-test separado executa a aplicação compilada. Isso não equivale a uma sessão manual completa do histórico em outro computador.

Resultado do executável: **12 verificações de aceitação aprovadas**, **19 verificações de self-test aprovadas** (incluindo a matriz responsiva) e **20 rodadas de stress sintético aprovadas**. SHA256 do executável: `82805825bfd7a166acdc8c71b5a4d2a93dfe4c1bce4df2880f338be028875dc2`. Relatórios de self-test/stress ficam na raiz dessa distribuição; o empacotador não inclui os diários de teste no programa distribuído.

O pacote portátil só é aceito pelo empacotador após validação do worker correspondente ao hash do executável, self-test e stress. Continua sendo um binário acadêmico não assinado; teste em máquina Windows independente permanece pendente. Extraia a pasta completa, preservando `_internal`. A versão anterior não foi sobrescrita.

Os números e hashes acima descrevem o incremento original do histórico. A busca tem testes adicionais em `tests/test_temporal_history_search.py`, evidências em `outputs/software_validation_temporal_search/` e distribuição separada em `release/windows/5.38.0-history-search-20260914/`. O diagnóstico nativo de aceitação permanece disponível nessa distribuição; teste em outro computador segue pendente.

Regressão final da busca: **120 testes aprovados** em 80,25 s (`accepted-tests.xml`), abrangendo histórico, campanhas, interface, diagnóstico e launcher. Não somar aos números anteriores, pois há sobreposição. Ruff, sintaxe do build e YAML do workflow foram verificados. Capturas em 640×500 e 1080×740 foram inspecionadas; a nota do filtro usa texto simples para não interpretar marcações HTML. O roteiro de aceitação do worker também consulta o filtro no controlador-fonte sobre o recibo do processo congelado; isso não equivale a uma interação manual de busca no executável.
