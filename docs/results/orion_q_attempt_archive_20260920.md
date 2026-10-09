# Verificação de ZIP operacional — 20/09/2026

## Entrega

**TEMPORAL CAMPAIGNS → VERIFY FILE → CHOOSE / VERIFY PAGE ZIP** confere o lote exportado por EXPORT PAGE ZIP sem extração, importação ou acesso ao diário. Uma falha em qualquer membro recusa o lote inteiro. Somente após verificar todos os registros, exibe o manifesto em texto simples. O botão de JSON individual permanece disponível.

Leitura limitada a 2 MiB + 1 byte antes de interpretar o ZIP, 1–50 tentativas e um manifesto, até 32 KiB descompactados por membro. Verifica CRC, JSON estrito, esquema, hashes, IDs e correspondência com os filtros. Recusa nomes inesperados, caminhos/pastas, membros duplicados, extras ou ausentes, tipos especiais, cifragem e compressão diferente de STORED/DEFLATED. O recibo científico carregado, o diário e a topologia não são alterados.

Troca de fonte no seletor exige nova escolha. Durante a leitura, troca de fonte, perda de capacidade ou cancelamento descarta o resultado tardio; fechar a aplicação aguarda a tarefa pendente. Nova verificação limpa o sucesso anterior, inclusive se o próximo arquivo for recusado.

## Evidências de código e interface

- **388 testes aprovados em 268,63 s**: `outputs/software_validation_attempt_archive/accepted-tests.xml`.
- Rodada focada: **158 aprovados em 36,54 s**, `recheck-tests.xml`. Sobrepõe-se à regressão; não somar contagens. O novo arquivo `tests/test_attempt_archive.py` contém 70 casos.
- A primeira rodada teve 69 aprovações e dois erros de preparação/encerramento do caso de arquivo grande: o identificador automático do pytest ultrapassava um limite do Windows. O teste recebeu nome curto; rodada focada e regressão repetidas com sucesso. Não era uma rejeição indevida do verificador.
- Casos incluem limites de 1/50 registros, formatos aceitos, corrupção de CRC, cifragem declarada, compressão recusada, excesso de bytes/membros, JSON inválido/recursivo, chaves duplicadas, nomes suspeitos, symlinks/tipos especiais, alterações re-hashadas incompatíveis e correspondência com filtros. Testes impedem explicitamente acesso ao banco e chamadas de extração.
- Preservação de entrada, diário, solicitação e recibo científico; recusa limpa, seletor cancelado, mudança de fonte e descarte de resultados tardios; encerramento bloqueado enquanto a leitura está ativa.
- Capturas `archive-640.png` e `archive-1080.png` inspecionadas com tema/fontes reais. Ambos os botões acessíveis e resultado com rolagem em texto simples.
- Ruff aprovado; sintaxe PowerShell e YAML do workflow verificadas. Novos testes adicionados aos gates de build. Workflow remoto não disparado; política de execução do Windows preservada.

## Executável Windows

Build concluído em aproximadamente **374 s**, na pasta `release/windows/5.38.0-attempt-archive-20260920/`. Paridade estrutural verificada entre os **74 módulos** `quantum_autopilot` do PYZ e a fonte (otimização 1, nomes de arquivo normalizados), sem divergências. Avisos de empacotamento sobre import opcional de SciPy, referência Linux de libgomp, cache de fontes sem permissão e APIs de Torch obsoletas não impediram a conclusão; não foram alteradas permissões do sistema.

**21 verificações aprovadas** no roteiro `outputs/software_validation_attempt_archive/frozen-worker/report.json`, incluindo exportação e verificação direta do ZIP. O controlador desse roteiro usa Python fonte, enquanto campanhas são executadas pelo EXE real sem Python externo no PATH; não é um teste manual da nova tela congelada. Autoteste separado do EXE: **19 verificações aprovadas**. Estresse separado: **20 rodadas aprovadas**, com estado isolado e códigos de saída 0.

SHA256 do executável: `c2d31d5dacd0ad9e13114f3dd563a16ea29085db04fa430cd94e1713d02a2b37`.

## Distribuição validada

ZIP portátil: `release/windows/5.38.0-attempt-archive-20260920/ORION-Q-5.38.0-native-windows-x64.zip`, **283205167 bytes**, **3833 arquivos**, CRC integral verificado. Esse ZIP é o aplicativo, não um lote operacional.

SHA256 do ZIP: `405f6199444dc0b81a5c15eb77c256d282e7404bca448dabbcffd96633b42a9a`.

Extrair a pasta inteira, preservando `_internal`, e abrir ORION-Q.exe. Não requer Python externo. Salvar exportações fora da pasta do programa. Distribuição acadêmica não assinada digitalmente.

Diagnóstico nativo final aprovado em **51,95 s**, código 0: integridade dos 3832 arquivos declarados (o manifesto não inclui a si próprio), autoteste e 20 rodadas sintéticas. Relatório `native-acceptance/acceptance-report.json` na raiz da distribuição: `automatic_passed: true`, `acceptance_status: manual_review_required`, `independent_machine_verified: false`. Executado neste computador de desenvolvimento; não substitui o aceite manual em outro Windows.

## Limites

CRC/hashes representam consistência, não assinatura ou autenticação de autoria. Um terceiro pode editar registros e recalcular os hashes. A verificação não compara com o diário atual, não prova completude da busca original, não autentica todos os bytes/metadados do contêiner e não abre arquivos mencionados nos registros. OPEN não prova processo ativo. Limite de bytes não é timeout de I/O; preferir arquivos locais disponíveis.

Este incremento é de integridade operacional do produto, não um novo resultado científico. Motor quântico e modelos WDM inalterados; nenhuma medição física adicionada. Não há reconciliação/importação de tentativas, assinatura digital ou exportação de toda a busca. Aceite manual em outro computador Windows e validação experimental continuam pendentes. Distribuições anteriores preservadas; nenhum commit criado.

[Guia operacional](../product/orion_q_campaign_attempts.md).
