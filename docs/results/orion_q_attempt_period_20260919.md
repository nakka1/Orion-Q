# Período UTC e legibilidade de ATTEMPTS — 19/09/2026

Implementados limites opcionais de data UTC de solicitação: FROM incluído, BEFORE excluído, formato YYYY-MM-DD. Os limites combinam-se por AND com prefixo de campanha e estado e são aplicados por Enter/REFRESH. Navegação, proteção contra exportação sob filtros não aplicados e limpeza na troca de fonte agora abrangem também as datas.

A consulta usa o prefixo ISO UTC gravado pelo diário, sem arredondamento de timestamps em ponto flutuante. Timestamp de solicitação dos registros retornados é validado antes de exibir. Não há migração, nova estatística, reexecução, conversão para horário local ou alteração do motor científico.

## Evidências de desenvolvimento

- Regressão final após a correção visual: **288 testes aprovados em 169,97 s**, `outputs/software_validation_attempt_period/accepted-tests.xml`.
- Primeira rodada focada: **134 testes aprovados em 49,09 s**, `outputs/software_validation_attempt_period/tests.xml`.
- A inspeção da captura inicial em 640×500 revelou sobreposição entre tabela, JSON, nota e botões apesar de os testes geométricos passarem. A aba passou a usar rolagem vertical e alturas mínimas; os testes foram reforçados para verificar separação dos componentes e acesso aos controles dentro do viewport após rolagem.
- Verificação de layout após a correção: **4 testes aprovados em 7,17 s**, `layout-tests.xml`. Não somar testes repetidos à regressão ampliada.
- Capturas finais `period-640-top.png`, `period-640-bottom.png` e `period-1080-top.png` inspecionadas com tema/fontes reais. As capturas iniciais `period-640.png` e `period-1080.png` ficam como evidência anterior, não como aceite do layout final.
- Casos novos cobrem calendário bissexto, intervalo igual/invertido, datas inválidas, limite aberto, último microssegundo antes da meia-noite, separação entre solicitação e conclusão, paginação, rascunhos inválidos, exportação recusada após edição de data e troca de fonte durante consulta.
- Ruff aprovado nos módulos/testes alterados; sintaxe PowerShell e YAML do workflow verificadas. Workflow remoto não disparado; políticas do Windows preservadas.

## Distribuição Windows

Build concluído em aproximadamente **311 s**, na pasta `release/windows/5.38.0-attempt-period-20260919/`. Paridade estrutural confirmada entre os 72 módulos `quantum_autopilot` do código-fonte e o PYZ empacotado, com otimização 1 e normalização somente dos nomes dos arquivos: nenhuma divergência.

O roteiro `outputs/software_validation_attempt_period/frozen-worker/report.json` aprovou **19 verificações**, incluindo consultas por período sobre tentativas de campanhas executadas pelo EXE e exclusão da data-limite. O controlador desse roteiro é Python fonte; não equivale a uma interação manual com a GUI congelada.

SHA256 do executável: `e2200ef134619573188ef52aeff6902f0c640e3701316370337cca274cdf6347`.

Autoteste separado do executável: **19 verificações aprovadas**. Estresse separado: **20 rodadas aprovadas**. Ambos terminaram com código 0 e estado isolado (`self-test.json` e `stress-validation/` na raiz da distribuição).

## Artefato portátil

ZIP: `release/windows/5.38.0-attempt-period-20260919/ORION-Q-5.38.0-native-windows-x64.zip`, **283192541 bytes**, **3833 arquivos**, CRC integral verificado.

SHA256 do ZIP: `ba93c6288cc5cbe32fde6114fae53305b899c13bbfb27804c4f06c43459a2e90`.

Extrair a pasta inteira antes de abrir ORION-Q.exe e preservar `_internal`. Exportar dados para fora da pasta do programa. Distribuição acadêmica não assinada digitalmente.

Diagnóstico nativo final aprovado em **44,36 s**, código 0: integridade dos 3832 arquivos declarados (excluindo o manifesto), autoteste e 20 rodadas sintéticas. Relatório `native-acceptance/acceptance-report.json` na raiz da distribuição: `automatic_passed: true`, `acceptance_status: manual_review_required`, `independent_machine_verified: false`. Execução neste computador de desenvolvimento, não em máquina independente.

## Limitações e continuidade

O período não congela a base e não converte fusos. A UI recebe datas de dez caracteres, não horários. Não consulta por data de conclusão, não reconcilia tentativas OPEN, não conta todos os estados e não exporta lotes. Campos vazios significam limites ausentes. A validação de registros retornados não constitui auditoria integral do banco; metadados modificados externamente e fora da consulta podem não ser examinados. Não há novo índice ou promessa de tempo constante em bases grandes.

Este incremento melhora usabilidade e rastreabilidade, não a evidência experimental sobre WDM e desempenho quântico nem o controle de hardware. Validação manual em máquina Windows independente permanece pendente. Distribuições anteriores preservadas; nenhum commit criado. [Guia de operação](../product/orion_q_campaign_attempts.md).
