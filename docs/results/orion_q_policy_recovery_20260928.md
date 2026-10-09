# Q05 — rollback aprovado e recuperação explícita (28/09/2026)

Implementados rollback e recuperação de commit incerto por API local, mantendo o
contrato de simulação e a identidade da instância. Somente novos simuladores isolados
foram usados. [Contrato](../product/orion_q_policy_recovery_q05.md) ·
[Decisão](../architecture/adr_q05_002_explicit_recovery.md).

## Comportamento entregue

Rollback prepara o limiar da configuração anterior registrada, preserva custos e
tempos e exige nova revisão/aprovação/ativação. A transação revalida origem e destino
e registra uma nova transição, sem reescrever histórico ou reutilizar aprovação.

Recuperação observa a tentativa falha, sua eventual linha persistida, política
restaurada, objetos, supervisor, topologia e referência anterior. A última observação
local tem prazo monotônico de 300 segundos, contado antes da leitura. Confirmação
específica, hash, UUID da falha e identificação declarada são obrigatórios.

Sob bloqueio do ciclo e transação SQLite, a recuperação compara novamente o contexto
e grava checkpoint com a tentativa falha completa. Mantém o mesmo objeto da política
restaurada e só libera ciclos após commit e conferência do prazo. A ausência de linha
é uma observação, sem concluir que o commit nunca ocorreu. Falha no próprio checkpoint
mantém o bloqueio e, em caso de commit incerto, exige nova observação da nova falha.

Readback/exportação e proteção de ciclos aceitam esse checkpoint. ACTIVE indica
correspondência atual à política retida. Reinício não reaplica recibos históricos.
Recuperação preserva o destino de rollback anterior quando há uma ativação válida
anterior. Sem ativação anterior, a política inicial não oferece destino de rollback.

## Validação

- **290 testes aprovados**, sem falhas, erros ou skips, em **373.65 s**.
  XML: `outputs/policy_recovery_20260928/regression-final.xml`.
- **46 testes novos** cobrem rollback exato, aprovação nova, seleção/contexto/prazo,
  entradas inválidas, runtime ausente, commit antes/depois da persistência, recuperação
  que também falha, expiração durante leitura/commit, diário corrompido, concorrência,
  reinício, falha de rollback e preservação do destino anterior.
- A primeira rodada teve 38 testes aprovados. A cobertura foi ampliada antes da
  regressão final; seu XML está preservado em `targeted.xml`.
- A regressão inclui ativação, preparação/revisão, aprovação, sandbox, barreira física,
  serviço QKD e interface nativa de aprovação. Ruff passou nos seis arquivos Python
  envolvidos. YAML do workflow e sintaxe PowerShell passaram; ambos incluem a suíte nova.

`tools/validate_policy_recovery.py` produziu quatro evidências em
`outputs/policy_recovery_20260928/diagnostic/`: proposta de rollback sem troca,
rollback persistido, observação de commit cuja resposta foi perdida e recuperação
com retenção do mesmo objeto de política. O limiar de exemplo retorna a 0.65.
Nenhum ciclo foi executado pelo diagnóstico. Os testes de integração que executam
ciclos usam somente simuladores isolados. As declarações automated diagnostic fixture
não representam aprovação humana real.

## Preservação e limites

A auditoria `outputs/policy_recovery_20260928/verification.json` compara **588 arquivos
preexistentes**, incluindo alterações anteriores não commitadas. Preserva **240
relatórios SO(3)**, **15 fontes congeladas**, summary/protocolo e XML histórico dos
**500 testes**. O diff deste incremento está em `implementation.diff`.

Não foi feita campanha científica ou inferência de melhora por escolha de limiar.
Não houve ativação/recuperação no runtime de operação, CI remota ou reconstrução do
executável Windows. Modelos continuam opcionais/OFF e não há atuação física.

Q05 permanece parcial. O próximo passo no [roadmap](../product/roadmap.md) é integrar
ativação, leitura atual, rollback aprovado e recuperação explícita à interface
nativa, com validação do fluxo completo. Q16 e seus aceites pendentes permanecem
inalterados.
