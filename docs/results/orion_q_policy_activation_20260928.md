# Q05 — ativação local em simulação (28/09/2026)

Implementada a ativação de limiar da política existente por API local, com nova
confirmação, validação da aprovação selecionada, auditoria persistente e leitura
atual da política. Somente fixtures isoladas foram ativadas neste incremento.
[Contrato](../product/orion_q_policy_activation_q05.md) ·
[Decisão arquitetural](../architecture/adr_q05_001_process_bound_activation.md).

## Implementação e limites

A ativação usa bloqueios de sessão/ciclo e `BEGIN IMMEDIATE` no SQLite existente.
Relê política, supervisor e topologia pela mesma conexão e exige o contexto aprovado
inalterado e prazo original vigente. JSON importado não substitui a aprovação da
sessão nem a confirmação específica de ativação.

A política candidata preserva custos e tempos aprovados, trocando apenas o limiar.
A aprovação completa, contexto, configurações anterior/nova, supervisor, UUID da
instância, revisão persistente e hashes ficam no mesmo registro transacional.
Não há decisão ou ciclo executado pela ativação, carregamento de pesos ou ação física.
Os recibos anteriores de ciclos, eventos de segurança e topologia são preservados.

Falhas anteriores ao commit restauram o objeto anterior e desfazem a transação.
Falha ao tentar commit, retorno incerto ou prazo ultrapassado durante commit
restaura a memória e bloqueia novos ciclos/ativações com UNRESOLVED. O registro
pode ter sido persistido; não se conclui que restaurar memória reverteu SQLite.
Não há retry ou liberação automática desse bloqueio.

COMMITTED é evidência histórica, não prova de atividade atual. A leitura ACTIVE
confere recibo, índices/hash, parâmetros, supervisor e objetos da instância.
Cada ciclo posterior verifica essa correspondência antes de decidir, permite apenas
SIMULATION e registra ID/revisão/hash de ativação nos detalhes. Divergência ou
indisponibilidade resulta em UNVERIFIED e impede execução. O replay idempotente
continua retornando seu recibo original, sem atribuição retroativa de política.

Reinício não reaplica a última ativação: nova instância informa NO_LOCAL_ACTIVATION
e segue sua inicialização habitual. Instâncias que compartilham o diário mantêm
identidades independentes. Não existe atomicidade durável entre memória e SQLite;
essa limitação é tratada pela identidade da instância, observação atual e bloqueio
conservador, conforme ADR. A recuperação/rollback explícitos ainda não existem.

## Validação local

- **244 testes aprovados**, sem falhas, erros ou skips, em **293.37 s**,
  no XML `outputs/policy_activation_20260928/regression-final.xml`.
- **51 testes novos** cobrem confirmação/campos estritos, seleção/contexto/prazo,
  clonagem de custos/tempos, ausência de execução/modelos, aprovação consumida,
  auditoria, exportação exclusiva, reinício, instâncias independentes, revisões
  sucessivas, JSON corrompido/não finito/maior que 256 KiB, concorrência com ciclo,
  escritor de topologia bloqueado e falhas antes/depois do commit.
- A primeira rodada passou com 46 casos. Após ampliar os casos de entrada inválida,
  o ID automático de um payload grande ultrapassou 32.767 caracteres em
  PYTEST_CURRENT_TEST no Windows, causando erros de setup/teardown. IDs explícitos
  curtos corrigiram o ambiente de testes; a regressão inteira foi repetida.
  O XML com esses erros permanece em `regression-initial.xml`.
- Regressão inclui preparação/revisão, aprovação, sandbox, barreira física, serviço
  QKD e interface nativa de aprovação. Ruff aprovado em seis arquivos Python;
  workflow YAML e sintaxe PowerShell validados. Build/workflow incluem a nova suíte.

`tools/validate_policy_activation.py` cria novo simulador e produz quatro evidências
em `outputs/policy_activation_20260928/diagnostic/`: transição de limiar 0.65 para
0.7, leitura ACTIVE, divergência UNVERIFIED e reinício NO_LOCAL_ACTIVATION. O
rótulo automated diagnostic fixture não representa aprovação humana real. Nenhum
ciclo é executado pelo diagnóstico. A alteração direta que provoca divergência
é configuração do teste, não um caminho de ativação suportado.

O teste de integração de ciclo executa somente o simulador isolado e verifica o
vínculo ao recibo de ativação. Não há ganho científico inferido do limiar de exemplo.
A nova API não foi aplicada ao runtime de operação, não houve campanha científica,
CI remota ou reconstrução do executável Windows.

## Preservação e roadmap

A auditoria `outputs/policy_activation_20260928/verification.json` compara os **582
arquivos existentes** no início, incluindo alterações anteriores não commitadas,
e confere os 240 relatórios SO(3), 15 fontes congeladas, summary, protocolo e XML
histórico dos 500 testes. `implementation.diff` registra somente este incremento.

Próximo no [roadmap](../product/roadmap.md): recuperação/rollback auditáveis e,
depois, integração nativa da ativação/readback. Preparação/revisão/aprovação seguem
com interface, mas a ativação nova é somente API local. Q05 permanece parcial;
modelos continuam opcionais/OFF e atuação física permanece bloqueada.
