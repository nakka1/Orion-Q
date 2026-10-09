# ADR-Q05-001: ativação vinculada à instância de simulação

**Status:** Accepted — implementado neste incremento local.
**Date:** 2026-09-28.
**Deciders:** manutenção do ORION-Q, no escopo do roadmap de simulação.

## Context

Q05 já prepara, revisa e registra aprovação explícita de um novo limiar da política
existente do repetidor. A aprovação é mantida em memória e seu JSON exportado é
histórico. O runtime usa uma política em memória; o diário e a topologia usam SQLite.
Não existe transação distribuída que torne memória do processo e SQLite duráveis
como uma única unidade. A presença de pesos opcionais não autoriza ativação física.

## Decision

A ativação exige nova confirmação literal e o ID/hash da aprovação ainda selecionada
na sessão local. Sob bloqueios da sessão e do ciclo, abre SQLite existente em modo
`rw` e `BEGIN IMMEDIATE`. A topologia e a configuração são relidas dentro dessa
fronteira; o hash deve coincidir com a observação aprovada e o prazo original deve
estar vigente. Uma política nova é construída a partir dos parâmetros aprovados,
sem modificar o objeto anterior, sem carregar modelos ou executar decisões/ciclos.

A aprovação inteira, configuração anterior, configuração nova, supervisor, contexto,
UUID da instância de runtime, revisão persistente e hashes são gravados no mesmo
registro SQLite. A referência em memória é trocada antes do commit, enquanto o
bloqueio impede ciclos. Falhas anteriores ao commit restauram a referência exata
e desfazem a transação. Falha ao tentar confirmar o commit, retorno ambíguo ou prazo
ultrapassado durante o commit restaura a referência anterior e deixa um bloqueio
local de resultado não resolvido. Não se tenta executar ou repetir automaticamente.

O resultado persistido é **COMMITTED**, uma observação histórica de uma transição
naquela instância. **ACTIVE** só é emitido por leitura atual que compara registro,
hash, parâmetros, supervisor e identidades dos objetos do runtime. Toda execução
posterior da política ativada exige essa verificação e só admite modo SIMULATION;
o ciclo registra ID/revisão/hash da ativação em seus detalhes. Divergência ou
indisponibilidade do diário bloqueia o ciclo antes da decisão/executor.

Reiniciar cria nova identidade e mantém o comportamento anterior de inicialização
do runtime; registros históricos **não são reaplicados**. O novo runtime informa
NO_LOCAL_ACTIVATION, mesmo se o diário possuir ativações antigas. Diferentes runtimes
sobre o mesmo diário são instâncias independentes; a revisão SQLite é uma sequência
de auditoria, não a indicação de uma política global única.

## Options Considered

| Opção | Vantagem | Custo/limite |
|---|---|---|
| Trocar só a política em memória | Simples | Não preserva auditoria durável nem trata falha de gravação |
| Restaurar automaticamente a última configuração SQLite | Continuidade após reinício | Exige autoridade persistente, recuperação validada e definição de dono do runtime ainda inexistentes |
| Ativação vinculada à instância, com auditoria e leitura atual | Não infere autoridade de JSON/reinício; falhas são observáveis | Continuidade depende de nova preparação/aprovação após reinício; commit incerto bloqueia ciclos |

Foi escolhida a terceira opção. Não se declara atomicidade durável entre memória e
SQLite. Um processo que morre antes do commit perde sua política e deixa SQLite
resolver a transação; se morrer depois, o registro permanece histórico, sem ativação
automática no processo seguinte. COMMITTED isolado nunca comprova atividade atual.

## Consequences

- Preservação do limiar anterior, custos/tempos/supervisor e aprovação permite futura
  recuperação e rollback auditáveis; este incremento não implementa rollback.
- Não há novas rotas HTTP, botão de ativação, modelos opcionais ativos ou autoridade
  física. A API local não autentica identidade humana; conserva a declaração original.
- Código local deve respeitar o bloqueio do runtime. Alterações arbitrárias fora dele
  não são uma autoridade suportada; a leitura atual bloqueia divergências detectadas.
- O bloqueio por commit incerto é intencionalmente conservador e não é liberado por
  nova aprovação. Uma recuperação explícita ainda precisa de contrato próprio.
- Alterações posteriores de topologia não desfazem a política automaticamente; o
  contexto é requisito da transição. Ciclos mantêm a validação de snapshot/supervisor.
- Parar a fonte invalida propostas/aprovações, mas não reverte silenciosamente uma
  ativação já concluída no runtime que continua em memória.

## Action Items

1. [x] API local, transação, auditoria, leitura atual e proteção dos ciclos.
2. [x] Testes de commit incerto, concorrência, reinício e fronteiras físicas.
3. [x] Recuperação/rollback auditáveis por API local; extensão em [ADR-Q05-002](adr_q05_002_explicit_recovery.md).
4. [x] Integração nativa de ativação/readback/rollback/recuperação no escopo de limiar existente; [validação](../results/orion_q_policy_recovery_ui_20260928.md).
5. [ ] Política de continuidade entre processos, caso o produto venha a exigi-la.
