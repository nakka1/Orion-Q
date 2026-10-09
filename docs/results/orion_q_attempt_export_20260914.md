# Exportação operacional de tentativas — 14/09/2026

A aba ATTEMPTS agora oferece EXPORT ATTEMPT: exportação individual de metadados operacionais, separada de EXPORT JSON, que continua destinado ao recibo científico. O motor de simulação, os modelos preditivos e os métodos estatísticos não foram alterados.

## Comportamento entregue

- Exige seleção válida e capacidades de histórico e exportação da fonte.
- Relê e verifica o registro SQLite, exigindo o hash da versão selecionada. Alteração, remoção ou corrupção recusam a exportação antes de criar o arquivo.
- Produz um JSON com `record` e `record_sha256`, esquema `orion.campaign_attempt.v1`. OPEN é exportável sem afirmar que o processo está ativo.
- Serializa antes de criar o destino e usa criação exclusiva: arquivos existentes não são sobrescritos. A cópia é um snapshot da leitura, não acompanha alterações posteriores.
- Executa leitura/escrita fora da thread gráfica. Troca de fonte enquanto o seletor está aberto exige nova seleção; uma exportação já aceita pode terminar sem atribuir a confirmação à nova fonte.
- Não executa campanhas, não modifica recibos e não transforma falhas operacionais em resultados quânticos.

## Evidências locais

- **183 testes aprovados em 138,37 s**: `outputs/software_validation_attempt_export/accepted-tests.xml`. A execução inicial de 28 testes está em `tests.xml`; não somar casos repetidos.
- Capturas com tema/fontes reais em 640×500 e 1080×740 inspecionadas: `export-640.png` e `export-1080.png` na mesma pasta de evidências.
- Ruff aprovado nos módulos alterados, testes e ferramentas pertinentes; sintaxe PowerShell do build e YAML do workflow verificadas. Workflow remoto não executado.
- **15 verificações aprovadas**: `outputs/software_validation_attempt_export/frozen-worker/report.json`. O controlador externo desse roteiro usa Python fonte e despacha o EXE real como worker; a exportação operacional nesse roteiro não constitui teste manual da GUI congelada.
- Autoteste separado do executável aprovado em **19 verificações**; teste de estresse separado aprovado em **20 rodadas**.
- Paridade estrutural de objetos de código entre fonte e PYZ confirmada para `campaign_runner`, `campaign_job` e `campaign_attempts`, com otimização 1 e normalização apenas dos nomes de arquivos.

## Artefato

Pasta: `release/windows/5.38.0-attempt-export-20260914/`.

ZIP: `ORION-Q-5.38.0-native-windows-x64.zip`, **283178185 bytes**, **3833 arquivos**, CRC integral verificado.

SHA256 do ZIP: `cd5836a6f54677e8a3407c3b1407e8174ca95e8804cb5e801c9cdc54ab09e0c9`.

SHA256 do executável: `de4a8e736a6ce646976701506a7c099d75d1e5a69e253db3ae85c538afc0bcc9`.

Distribuições anteriores preservadas. Nenhum commit foi criado nesta etapa.

Diagnóstico nativo final aprovado em **40,94 s**, código 0: integridade dos 3832 arquivos declarados (o manifesto não inclui a si próprio), autoteste e estresse sintético aprovados. Relatório: `native-acceptance/acceptance-report.json`, na raiz desta distribuição. `automatic_passed: true`, mas `acceptance_status: manual_review_required` e `independent_machine_verified: false`: a execução ocorreu neste computador de desenvolvimento, não em máquina independente.

## Limites e continuidade

Hashes locais verificam consistência, não autenticam operador/dispositivo. Detalhes podem conter caminhos locais: revisar antes de compartilhar. Uma falha de escrita ou queda pode deixar arquivo parcial; não há garantia de gravação atômica. Não há exportação em lote, reimportação pela UI, captura de runners CLI ou reconciliação automática de OPEN.

A melhoria é de rastreabilidade do produto, não uma nova descoberta científica nem evidência de controle físico. Validação manual em Windows independente permanece pendente. Próximo incremento funcional possível: verificação independente de um JSON operacional exportado, com esquema estrito e limites de tamanho, sem importá-lo como recibo científico ou alterar o diário. [Guia operacional](../product/orion_q_campaign_attempts.md).
