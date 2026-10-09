# Associação verificável entre tentativa e recibo — 20/09/2026

## Entrega

**ATTEMPTS → OPEN LINKED RECEIPT · SELECTED ATTEMPT** abre a comparação histórica de um recibo já associado a CONFIRMED, REPLAYED ou RECOVERED. Exige seleção e filtros aplicados. A tela reaproveita a apresentação científica existente, mas identifica UUID, estado e hash da tentativa e hash da solicitação na proveniência. Abrir não reexecuta ensaios; o manifesto anterior deixa de ser executável. EXPORT JSON preserva o recibo original, sem incorporar metadados da tentativa.

O backend relê tentativa e recibo numa única transação SQLite. Verifica a versão selecionada da tentativa, o hash do recibo registrado, a solicitação completa, ID da campanha, topologia e versão esperada. Recusa recibo fora do escopo simulado/sem atuação física. Ausência, alteração ou inconsistência impede a abertura; um ID de campanha igual não substitui a associação registrada. OPEN e falhas sem recibo não recebem resultado por inferência.

## Correção encontrada durante os testes

A primeira implementação reutilizava o inicializador `SimulatedTopologyStore`, que pode executar `PRAGMA optimize` e atualizar estatísticas do SQLite. Os testes de preservação byte a byte detectaram esse efeito. O novo fluxo passou a abrir diretamente uma conexão URI **mode=ro**, com **query_only** e transação explícita. Não inicializa tabelas, otimiza estatísticas ou cria um banco ausente. A correção é restrita a esta nova abertura; não constitui refatoração dos demais leitores legados.

Os testes também exigiram normalizar tuplas/listas pela representação JSON persistida e usar o sinal do botão Qt customizado. Uma instrumentação de conexão inicialmente conflitava com o argumento `uri`; foi corrigida e inclui uma tentativa de escrita explicitamente recusada pelo SQLite. Rodadas exploratórias com essas falhas foram mantidas nas evidências; a aprovação final usa as rodadas posteriores.

## Evidências

- **433 testes aprovados em 282,78 s**, `outputs/software_validation_attempt_receipt/accepted-tests.xml`, incluindo o novo vínculo e a regressão de campanhas, histórico, diário, exportação, verificação externa, capabilities e telas.
- **45 testes específicos aprovados em 17,48 s**, `outputs/software_validation_attempt_receipt/final-focused-tests.xml`. As rodadas anteriores se sobrepõem; não somar contagens.
- Cobertura: CONFIRMED/REPLAYED/RECOVERED; ausência de associação nos demais estados; identidade inválida; tentativa/recibo alterados, ausentes ou corrompidos; metadados incompatíveis mesmo re-hashados; escopo de evidência; banco ausente; caminhos com espaços, Unicode, `%` e `#`; duas consultas em transação somente leitura.
- Teste concorrente em WAL remove o recibo após a primeira consulta. A leitura em curso conserva o snapshot consistente; a seguinte recusa o recibo ausente. Não há promessa de que a cópia permaneça atual após a leitura.
- GUI: seleção, filtros em rascunho, bloqueio de reexecução, preservação do banco, limpeza de resultado anterior, perda de capacidade, cancelamento/troca de fonte e fechamento aguardando trabalho pendente.
- Capturas `link-640.png`, `link-1080.png` e `linked-comparison.png` inspecionadas com tema/fontes reais. Capturas de ATTEMPTS estão roladas até o novo botão; o cabeçalho não visível nessa posição não é truncamento permanente.
- Ruff aprovado; sintaxe PowerShell e YAML verificadas; teste acrescentado aos gates de build. Workflow remoto não disparado e política de execução do Windows preservada.

## Executável Windows

Build concluído em aproximadamente **337 s**, em `release/windows/5.38.0-attempt-receipt-20260920/`. Paridade estrutural entre fonte e os **75 módulos** `quantum_autopilot` do PYZ, com otimização 1 e nomes de arquivo normalizados, sem divergências. Avisos opcionais de SciPy/libgomp, cache de fontes sem permissão e deprecações de Torch não impediram o build; nenhuma política do sistema foi alterada.

**22 verificações aprovadas** em `outputs/software_validation_attempt_receipt/frozen-worker/report.json`, incluindo associação tentativa–recibo. O controlador desse roteiro usa código Python fonte; as campanhas usam o EXE real sem Python externo no PATH. Isso não é um teste manual da nova tela congelada. Autoteste separado do EXE: **19 verificações aprovadas**; estresse separado: **20 rodadas aprovadas**. Códigos de saída 0, estado de teste isolado.

SHA256 do executável: `d03cc030ef3d6d4d9a6f1c6c3819e8e361d4a2637a857d98925bed5e16180389`.

## Pacote portátil

`release/windows/5.38.0-attempt-receipt-20260920/ORION-Q-5.38.0-native-windows-x64.zip`: **283210615 bytes**, **3833 arquivos**, CRC integral verificado. SHA256: `ab61d3693df1e1fc7ff9eca5f5c34cc7fe69de3bb29ae189caf5f3559341b846`.

Extrair a pasta inteira, preservando `_internal`, e abrir ORION-Q.exe. Não requer Python externo. Salvar exportações fora da pasta do programa. Distribuição acadêmica não assinada digitalmente. O ZIP portátil não é o lote operacional aceito em VERIFY FILE.

Diagnóstico nativo final aprovado em **44,61 s**, código 0: integridade dos 3832 arquivos declarados (excluindo o próprio manifesto), autoteste e 20 rodadas sintéticas. Relatório `native-acceptance/acceptance-report.json` na raiz da distribuição: `automatic_passed: true`, `acceptance_status: manual_review_required`, `independent_machine_verified: false`. Executado neste computador de desenvolvimento, sem atestar aceite em máquina independente.

## Limites científicos e operacionais

Associação consistente não autentica autoria, não valida hipóteses físicas e não prova que um despacho produziu novos ensaios. Várias tentativas podem apontar para um mesmo recibo: não são amostras independentes. REPLAYED e RECOVERED não devem ser somados como novas campanhas. O novo vínculo não é inferido de JSON/ZIP externo, não importa dados, não reconcilia OPEN e não altera estados do diário.

Motor quântico, modelos WDM e protocolo estatístico permanecem inalterados. Nenhuma medição física nova ou evidência experimental da hipótese WDM→fidelidade foi adicionada. Aceite manual em computador Windows independente e validação experimental continuam pendentes. Distribuições anteriores preservadas; nenhum commit criado.

[Guia operacional](../product/orion_q_campaign_attempts.md).
