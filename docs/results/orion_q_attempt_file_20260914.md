# Verificação de JSON operacional externo — 14/09/2026

Implementada a aba **VERIFY FILE**, com leitura assíncrona, limite de 32 KiB, validação estrita e comparação do hash canônico. A função `verify_attempt_file(path)` não abre um SQLite, não importa registros e não executa campanhas. O motor científico não foi alterado.

## Evidências do código e da interface

- Regressão ampliada: **241 testes aprovados em 140,89 s** (`outputs/software_validation_attempt_file/accepted-tests.xml`).
- Rodada focada: 71 aprovados em 11,23 s (`recheck-tests.xml`); não somar aos 241, pois há sobreposição.
- Na primeira rodada, o identificador automático do caso de 32 KiB ultrapassou o limite de variável de ambiente do Windows no pytest. Foi corrigida a identificação dos testes, não relaxada a validação de arquivos. O relatório inicial permanece em `tests.xml`.
- Cobertura: estados operacionais, tipos/flags, UTC válido, hashes, UTF-8/BOM, JSON truncado/duplicado/não finito, profundidade excessiva, limite exato e excesso de bytes, arquivo ausente, hash recalculado sem autenticação, preservação de recibo/banco, seletor cancelado, leitura concorrente recusada, cancelamento, perda de capacidade e troca de fonte.
- Capturas `verify-640.png` e `verify-1080.png` inspecionadas na pasta de evidências, com tema/fontes reais. Texto externo usa QPlainTextEdit somente leitura; não é renderizado como HTML.
- Ruff aprovado nos módulos/testes/ferramentas alterados. Sintaxe PowerShell e YAML do workflow verificadas; workflow remoto não disparado. Nenhuma política de execução do Windows foi desativada.

## Verificação do executável

O build Windows foi concluído em aproximadamente 253 s. O roteiro `outputs/software_validation_attempt_file/frozen-worker/report.json` aprovou **16 verificações**: despachou campanhas pelo EXE real, conferiu resultados, replay, cancelamento, histórico, exportação e leitura do arquivo externo. O controlador desse roteiro é Python fonte; a última verificação da aba externa não deve ser apresentada como uma interação manual com a GUI congelada.

Paridade estrutural de objetos de código confirmada entre fonte e PYZ para `campaign_runner`, `campaign_job` e `attempt_file` (otimização 1; normalização dos nomes de arquivos). Uma tentativa de inspecionar o PYZ enquanto ele ainda estava sendo gerado foi inconclusiva; a conferência aprovada foi realizada depois do build completo.

SHA256 do executável: `a3104fac188b193493ea54edd0c8ee782563993c4814411db7cb13343f51e1ec`.

Autoteste separado do executável aprovado em **19 verificações** e estresse sintético separado aprovado em **20 rodadas**, com saídas 0 e estado isolado na raiz da distribuição `release/windows/5.38.0-attempt-file-20260914/`.

## Distribuição portátil

ZIP: `release/windows/5.38.0-attempt-file-20260914/ORION-Q-5.38.0-native-windows-x64.zip`, **283185059 bytes**, **3833 arquivos**, CRC integral verificado.

SHA256 do ZIP: `adc1dc3f6394a19dc2abe4378e431b9aec0f919bcbe7ab3686c7fbd21d38fa23`.

Extrair a pasta inteira antes de abrir ORION-Q.exe; `_internal` é necessário. Não salvar exportações dentro da pasta do programa, pois o diagnóstico confere arquivos extras e hashes. A edição é acadêmica, não assinada digitalmente.

Diagnóstico nativo final aprovado em **43,34 s**, código 0: integridade dos 3832 arquivos declarados (manifesto exclui a si próprio), autoteste e 20 rodadas sintéticas aprovados. Relatório `native-acceptance/acceptance-report.json` na raiz desta distribuição: `automatic_passed: true`, `acceptance_status: manual_review_required`, `independent_machine_verified: false`. A execução foi neste computador de desenvolvimento, não em máquina independente.

## Escopo e limites

SCHEMA + RECORD HASH VERIFIED significa consistência da cópia lida. Não autentica operador, autor ou equipamento; não confronta o arquivo com o diário atual; não valida o recibo eventualmente referido por hash; não comprova ciência ou atuação física. Uma edição com hash recalculado pode passar. A UI apresenta explicitamente NOT AUTHENTICATED, NOT A SCIENTIFIC RECEIPT e NOT IMPORTED.

OPEN não prova processo vivo. Datas são UTC válidas, mas o verificador não impõe ordem cronológica nem calcula duração: relógios podem ser corrigidos. A versão é inteira não negativa, IDs ASCII de até 64 caracteres e detalhe de até 2000 caracteres. Não há timeout de I/O para caminhos remotos/dispositivos; preferir arquivos locais disponíveis. A UI aguarda operações pendentes no fechamento e descarta resultados de fonte anterior.

O incremento melhora rastreabilidade e inspeção do produto, sem acrescentar evidência experimental sobre WDM e desempenho quântico. Permanecem pendentes reconciliação explícita de OPEN, cobertura dos runners CLI, filtros operacionais, exportação em lote e verificação manual em máquina Windows independente. Nenhum commit foi criado e distribuições anteriores foram preservadas. [Guia de uso](../product/orion_q_campaign_attempts.md).
