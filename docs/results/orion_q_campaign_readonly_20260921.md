# Consultas de campanha explicitamente somente leitura — 21/09/2026

## Entrega e motivação

O incremento anterior identificou que `SimulatedTopologyStore` pode executar manutenção de estatísticas (`PRAGMA optimize`) ao ser instanciado. Uma consulta que use esse inicializador não é necessariamente neutra nos bytes do banco. A abertura de recibo vinculado já contornava o problema; agora a proteção cobre as consultas do gerenciador nativo de campanhas.

`ReadOnlyCampaignStore` oferece apenas a conexão e o lock exigidos pelas funções de consulta existentes, sem herdar inicialização de schema ou métodos de escrita. Abre um arquivo existente por URI SQLite **mode=ro**, ativa **query_only** e fecha a conexão se a configuração falhar. Banco ausente, diretório ou schema incompatível são recusados, não criados/reparados.

Fluxos migrados em `CampaignJob`: lista de campanhas, abertura de recibo histórico, lista de tentativas, releitura para exportação individual, releitura para exportação da página e confirmação do recibo após o encerramento do worker. A abertura tentativa–recibo compartilha o novo leitor, preservando sua transação única. Registro e finalização de tentativas continuam usando o store de escrita autorizado; exportações continuam criando somente seus arquivos de destino, sem sobrescrita.

Não se utiliza `immutable=1`: consultas posteriores devem observar commits posteriores e respeitar SQLite/WAL. A abertura vinculada mantém suas duas consultas na mesma transação; a exportação de página mantém um SELECT único. Essa consistência não promete imutabilidade futura do banco ou de toda a pasta, incluindo arquivos auxiliares de WAL/locks. O timeout de bloqueio de 10 s não equivale a um timeout absoluto de I/O.

## Evidências

- Regressão ampliada: **455 testes aprovados em 311,94 s**, `outputs/software_validation_campaign_readonly/accepted-tests.xml`.
- Rodada focada: **85 testes aprovados em 46,58 s**, `outputs/software_validation_campaign_readonly/tests.xml`. Contém 22 casos novos e regressões do vínculo e histórico; não somar testes sobrepostos a outras rodadas.
- `tests/test_campaign_read_store.py`: constructor sem conexão/inicialização; banco ausente e schema inválido sem reparo; SQLite recusando DML/DDL e alteração de user_version mesmo após desligar query_only; preservação byte a byte do banco durante consultas/exportações; nomes de caminho Unicode/com espaços e caracteres especiais.
- Escritor legítimo em WAL permanece funcional; consultas seguintes veem novos commits. Um banco removido após construir o leitor não é recriado. Falha de configuração fecha a conexão.
- Integração Qt cobre lista/abertura/exportação sem instanciar o store de escrita, recusa de seis fluxos quando o banco da fonte está ausente e execução real de worker preservando registro/finalização enquanto usa readback protegido.
- Ruff aprovado, sintaxe PowerShell e YAML verificadas. Gate local e workflow incluem o novo arquivo de testes; workflow remoto não disparado e política de execução Windows preservada.
- Não há mudança visual. Os testes existentes de telas e fluxos integram a regressão; não foram produzidas novas imagens como se houvesse redesign.

## Executável Windows

Build concluído em aproximadamente **359 s**, em `release/windows/5.38.0-campaign-readonly-20260921/`. Paridade estrutural entre a fonte e os **76 módulos** `quantum_autopilot` do PYZ (otimização 1, nomes de arquivo normalizados), sem divergências. Avisos opcionais de SciPy/libgomp, cache de fontes sem permissão e deprecações do Torch não impediram o build; permissões/políticas do sistema não foram alteradas.

**23 verificações aprovadas** em `outputs/software_validation_campaign_readonly/frozen-worker/report.json`, incluindo preservação byte a byte do diário durante a sequência de consultas/exportações. O controlador desse roteiro usa Python fonte; as campanhas rodam pelo EXE real sem Python externo no PATH. Não representa um teste manual da GUI congelada. Autoteste separado do executável: **19 verificações aprovadas**; estresse separado: **20 rodadas aprovadas**, códigos de saída 0 e estado isolado.

SHA256 do executável: `05f613e5ff3662063a169553b01e5515c303e17c078423c178a5d454514a85bd`.

## Pacote portátil

`release/windows/5.38.0-campaign-readonly-20260921/ORION-Q-5.38.0-native-windows-x64.zip`: **283213155 bytes**, **3833 arquivos**, CRC integral verificado. SHA256: `0fd935c10d3b3c110b98d7c9a69d92ba7792dc2f76ee91ff9e68e20dd275ec60`.

Extrair a pasta inteira, preservando `_internal`, e abrir ORION-Q.exe. Não requer Python externo. Exportações devem ficar fora da pasta do programa. Distribuição acadêmica não assinada digitalmente; o ZIP portátil não é um lote operacional aceito em VERIFY FILE.

Diagnóstico nativo final aprovado em **42,96 s**, código 0: integridade dos 3832 arquivos declarados (o manifesto não inclui a si próprio), autoteste e 20 rodadas sintéticas. Relatório `native-acceptance/acceptance-report.json` na raiz da distribuição: `automatic_passed: true`, `acceptance_status: manual_review_required`, `independent_machine_verified: false`. Executado neste computador de desenvolvimento, não em máquina independente.

## Escopo e limites

Esta proteção cobre o gerenciador nativo de campanhas, não todos os acessos SQLite do produto. Inicialização do serviço, edição de topologia e execução autorizada continuam gravando. As funções de domínio preservam a API de store: consumidores externos que instanciam o store gravável continuam responsáveis por seu uso. O leitor não é uma ferramenta de importação de bancos arbitrários/não confiáveis nem uma auditoria completa de segurança do SQLite.

Modelos WDM, motor quântico e protocolo estatístico permanecem inalterados. Nenhuma medição física ou nova evidência experimental foi adicionada. Aceite manual em computador Windows independente permanece pendente. Distribuições anteriores preservadas, sem commit criado.

[Guia operacional](../product/orion_q_campaign_attempts.md).
