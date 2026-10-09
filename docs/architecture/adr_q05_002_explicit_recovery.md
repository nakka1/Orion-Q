# ADR-Q05-002: recuperação explícita mantendo a política restaurada

**Status:** Accepted — implementado por API local de simulação.
**Date:** 2026-09-28.
**Deciders:** manutenção do ORION-Q, no escopo do roadmap de simulação.

## Context

O [ADR-Q05-001](adr_q05_001_process_bound_activation.md) conserva a referência da
política anterior e bloqueia ciclos quando o commit SQLite tem resultado incerto.
Uma linha persistida comprova evidência histórica, mas não a política atual em
memória; a ausência de linha não prova que o commit nunca ocorreu. Era necessário
permitir resolução explícita e rollback sem inferir autoridade dos registros.

## Decision

Rollback será uma nova proposta fixada à configuração anterior registrada da
ativação local atual. Segue revisão, aprovação e ativação existentes. O destino e
a origem são revalidados na transação; somente alteração de limiar é suportada.

Recuperação observará a tentativa falha, a política restaurada, seus objetos,
supervisor, topologia e eventual ativação anterior. A última observação tem prazo
monotônico de 300 segundos. Confirmação específica, UUID da falha, hash da observação
e identificação declarada são exigidos, sem promessa de autenticação humana.

A transação relê o contexto e grava um checkpoint que mantém a política restaurada.
O checkpoint inclui a tentativa falha completa, inclusive quando seu registro não
está presente. A liberação do bloqueio ocorre somente após commit e conferência do
prazo. Uma falha de commit na própria recuperação cria nova incerteza e exige nova
observação/declaração. Não há retry automático ou reaplicação após reinício.

## Options Considered

| Opção | Benefício | Limitação |
|---|---|---|
| Reaplicar a candidata falha | Retoma a intenção anterior | Reutiliza autoridade antiga apesar de resultado incerto e exige novo contrato de decisão |
| Limpar o bloqueio sem registro | Pouco código | Perde vínculo entre tentativa, política retida e resolução |
| Manter política restaurada e auditar declaração explícita | Preserva objetos, evidência e decisão atual | Requer inspeção/confirmação; indisponibilidade ou divergência mantém bloqueio |

Foi escolhida a terceira opção. A restauração do objeto já é a decisão conservadora
do caminho de falha; a recuperação confirma essa configuração, sem repetir a troca.
A revisão SQLite continua sendo sequência de auditoria, sem política global entre
instâncias. Não se declara atomicidade durável entre memória e banco.

## Consequences

- Checkpoints compartilham verificação/exportação e proteção de ciclos com ativações.
- ACTIVE pode apontar para checkpoint da política retida, mesmo sem troca do objeto.
- O destino de rollback anterior é preservado quando existe ativação válida anterior.
- Recibos encadeados estão limitados a 256 KiB; excesso mantém bloqueio e não apaga história.
- Não há atuação física, modelos ativos, rotas HTTP ou restauração de autoridade após reinício.
- Interface nativa e regressão do fluxo de limiar foram entregues; seleção de outros controladores e aceite Windows independente continuam pendentes.

## Action Items

1. [x] Rollback por proposta/revisão/aprovação e recuperação com checkpoint explícito.
2. [x] Testes de falhas antes/depois do commit, concorrência, expiração e reinício.
3. [x] Integração nativa da ativação/readback/rollback/recuperação; [validação](../results/orion_q_policy_recovery_ui_20260928.md).
4. [ ] Continuidade entre processos, somente se o produto vier a exigir esse contrato.

[Contrato](../product/orion_q_policy_recovery_q05.md) ·
[Validação](../results/orion_q_policy_recovery_20260928.md).
