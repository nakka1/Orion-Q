# Exportação da página operacional em ZIP — 20/09/2026

Implementação e build iniciados em 19/09; validação final da distribuição concluída na retomada de 20/09. A pasta conserva o identificador `5.38.0-attempt-batch-20260919`.

## Entrega

**EXPORT PAGE ZIP · CURRENT PAGE ONLY** reúne as tentativas da página atual de ATTEMPTS (até 20 na UI), sem exigir seleção de uma linha. Inclui um JSON individual por tentativa e um manifesto com filtros, membros, quantidade, horário de exportação e hash canônico. Não exporta toda a busca, não agrega observações científicas e não inclui recibos científicos.

Todos os IDs/hashes são relidos em um único SELECT; cada membro passa pela validação estrita de esquema/hash e pela conferência dos filtros. Mudança, remoção ou corrupção recusa o lote inteiro antes de criar o arquivo. A serialização precede a abertura exclusiva do destino. O backend limita a seleção a 50 registros; a página da UI continua limitada a 20.

Fonte, página e filtros são conferidos novamente após o seletor de destino. Exportação já aceita pode concluir após troca de fonte, sem atribuir seu sucesso à nova entrada. Trabalho assíncrono e fechamento aguardando a operação, sem execução de campanha ou escrita no diário.

## Evidências

- **318 testes aprovados em 210,48 s**: `outputs/software_validation_attempt_batch/accepted-tests.xml`.
- Rodada focada: 162 aprovados em 57,43 s (`tests.xml`). Rodada complementar com falha de gravação, seletor cancelado e layout: três aprovados em 6,04 s (`extra-tests.xml`). Não somar casos repetidos à regressão.
- Testes cobrem mudança/remoção/corrupção de qualquer membro, estado que avança após leitura, SELECT único, limite de 50, filtros inconsistentes, identidade/caminho inválido, duplicados, recusa de sobrescrita, página de 20 entre 23 registros, preservação de manifesto científico carregado, capacidade de exportação, troca de fonte/página/filtros, falha de I/O e acesso ao botão por rolagem.
- Capturas `batch-640.png` e `batch-1080.png` inspecionadas com tema/fontes reais, incluindo rolagem até o botão. JSON vazio nas capturas corresponde a nenhuma linha selecionada: exportar a página não depende dessa seleção.
- Ruff aprovado, sintaxe PowerShell e YAML do workflow verificadas. Workflow remoto não disparado; política de execução do Windows preservada.
- Build concluído em aproximadamente 291 s. Paridade estrutural confirmada entre fonte e os **73 módulos** `quantum_autopilot` do PYZ (otimização 1; normalização de nomes de arquivos), sem divergências.
- **20 verificações aprovadas** no roteiro `frozen-worker/report.json`, incluindo exportação da página, CRC do ZIP, leitura dos membros e hash do manifesto. O controlador desse roteiro usa Python fonte; campanhas são executadas pelo EXE real. Não é teste manual da GUI congelada.
- Autoteste separado do executável: **19 verificações aprovadas**. Estresse separado: **20 rodadas aprovadas**. Saídas 0 e estado isolado na raiz da distribuição.

SHA256 do executável: `8509e64411e27d24ed3e69c1b11885633d17f9a62d5b8001656688f35eaae16d`.

## Pacote do aplicativo

ZIP portátil: `release/windows/5.38.0-attempt-batch-20260919/ORION-Q-5.38.0-native-windows-x64.zip`, **283199245 bytes**, **3833 arquivos**, CRC integral verificado. Este é o pacote do aplicativo, não um lote operacional.

SHA256 do ZIP portátil: `0b1d3c76706078d38d2c9006b04098cca3141dc9dc61d692e73b21a807f9c8e0`.

Extrair a pasta inteira e manter `_internal` ao abrir ORION-Q.exe. Exportações devem ficar fora da pasta do programa. Distribuição acadêmica não assinada digitalmente.

Diagnóstico nativo final aprovado em **45,78 s**, código 0: integridade dos 3832 arquivos declarados (excluindo o manifesto), autoteste e 20 rodadas sintéticas. Relatório `native-acceptance/acceptance-report.json` na raiz da distribuição: `automatic_passed: true`, `acceptance_status: manual_review_required`, `independent_machine_verified: false`. Execução neste computador de desenvolvimento, não em máquina independente.

## Limites

O arquivo representa o snapshot de leitura, não o estado mais recente para sempre. `page_only: true` evita apresentá-lo como o conjunto integral da consulta. Hashes verificam consistência, não autenticam autoria nem validam ciência. OPEN não prova processo ativo. Detalhes podem conter caminhos locais; revisar antes de compartilhar.

Falha de I/O ou queda pode deixar ZIP parcial; não há promessa de gravação atômica. Uma nova tentativa exige outro nome e não apaga a cópia parcial. VERIFY FILE continua verificando o JSON individual extraído, não o ZIP nem `manifest.json`. Não há importação/reconciliação em lote, exportação de todas as páginas ou assinatura digital.

Motor científico e modelos WDM inalterados. Teste manual em outro computador Windows e validação experimental continuam pendentes. Distribuições anteriores preservadas; nenhum commit criado. [Guia operacional](../product/orion_q_campaign_attempts.md).
