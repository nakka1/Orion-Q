# Q05 — explicação estruturada de ciclos simulados

Consulta somente leitura do diário local e painel EDGE CONTROL → DECISION AUDIT,
implementados em 28/09/2026. Mostram intenção da política, avaliação independente de
segurança e resultado do executor conforme registrados. Não executam ciclos,
recalculam decisões, ativam modelos ou afirmam atividade atual da política.
A tela principal EDGE CONTROL continua agregada; este painel consulta ciclos do
simulador, sem telemetria de chegadas individuais de fótons ou operações físicas.

## Consulta e integridade

`AssuranceDashboardService.policy_decision_history(limit=20)` retorna até 50 ciclos
simulados, em ordem decrescente de sequência, com ID de solicitação/ciclo, hash,
horário, intenção e ação efetiva. Exige inteiro entre 1 e 50; booleanos são recusados.
A janela gráfica usa os últimos 20 e não afirma representar o histórico completo.

`explain_policy_decision(request_id, expected_sha256=None)` abre um ciclo específico.
O painel envia também o hash capturado na listagem, recusando uma seleção alterada.
O ID deve ser imprimível e ter 1 a 200 caracteres. O JSON é limitado a 256 KiB e
recusa duplicação de campos, números não finitos e overflow numérico.

As leituras usam SQLite existente em modo `ro` e `query_only`, com uma única
transação de leitura para ciclo e política. Não inicializam runtime, banco ou tabelas.
Verificam hash canônico do recibo, índices, hash da requisição original, esquema e
coerência entre intenção, avaliação de segurança e ação informada pelo executor.
Recibos corrompidos são recusados; a janela não os omite silenciosamente.
Somente `simulation` é exposto: shadow, assurance e physical ficam fora deste painel.

A explicação `orion.policy_decision_explanation.v1` conserva o recibo completo e
seus campos estruturados: `intended_action`, `authorized`, `safety_violations`,
`effective_action`, `execution` e `recorded_policy_details`. As violações mantêm
código, restrição, valor requerido/observado e detalhe originais. Utilidades e
metadados da previsão são mostrados somente quando registrados, sem justificativa
inventada ou reavaliação com a política atual.

Ação efetiva do supervisor e resultado do executor são fatos separados. Um HALT
pode ser uma ação executada no simulador; a consulta não transforma veto em ação
física nem infere sucesso operacional da intenção. `performed_in_simulator`,
status e detalhes são os valores originais do executor.

## Vínculo histórico à política

Novos ciclos que já possuem referência de ativação passam a registrar também
`activation_record_sha256` em `intent.policy_details.policy_activation`. O campo
ancora o recibo completo de ativação, rollback ou checkpoint de recuperação.
Recibos anteriores não são reescritos. Replay idempotente conserva o recibo e o
vínculo originais, mesmo depois de nova ativação ou rollback.

| Estado de evidência | Significado |
|---|---|
| `VERIFIED_RECORDED_HASH` | Hash do recibo de política registrado no ciclo e referência de ID/revisão/runtime/configuração correspondem ao registro histórico |
| `LEGACY_REFERENCE_MATCH` | ID/revisão/runtime/configuração correspondem, mas o ciclo antigo não registrou o hash do recibo completo; não há a mesma força de vínculo |
| `NOT_RECORDED` | O ciclo não possui referência de ativação; não se substitui pela configuração atual |
| `UNAVAILABLE` | O recibo de política referenciado está ausente |
| `UNVERIFIED` | Não foi possível verificar o registro ou sua correspondência; nenhuma configuração é apresentada como verificada |

Quando apenas o vínculo de política falha, os fatos do ciclo cujo hash foi validado
continuam disponíveis, com a limitação explícita. Quando o próprio ciclo falha,
a consulta inteira é recusada. Hashes são verificações locais de integridade, não
assinaturas ou autenticação humana. Uma observação histórica nunca substitui READ
ACTIVE POLICY do [painel de ativação](orion_q_policy_activation_q05.md).

## Interface e exportação

1. Abra EDGE CONTROL → DECISION AUDIT e selecione READ LATEST 20.
2. Escolha o ciclo; EXPLAIN SELECTED consulta ID/hash explicitamente em segundo plano.
3. STRUCTURED EXPLANATION separa intenção, segurança, executor e vínculo da política.
   CAPTURED RECEIPT mostra o JSON completo verificado.
4. EXPORT CAPTURED grava o recibo selecionado em arquivo novo, sem sobrescrita.

O provedor local anuncia `read_policy_decisions` como operação de visualização.
Fontes sintéticas/medidas não recebem acesso ao diário local por essa capacidade.
Um banco ausente resulta em erro, sem inicialização implícita. Não é necessário
criar um novo runtime para consultar histórico existente após reinício.

Há somente uma leitura e uma exportação aceitas por vez, sem fila. Mudança de fonte,
seleção, caminho do diário, parada ou fechamento limpa a seleção e descarta retorno
atrasado. A exportação captura o JSON antes do diálogo de arquivo e revalida a
seleção antes de enviar a escrita; não relê ou executa o ciclo. Escrita já aceita
pode terminar após fechamento, sem repopular o painel.

## Limites e próximos passos

O fluxo de limiar da política existente ganha explicação estruturada e auditável,
mas isso não habilita `set_decision_source`, modelos opcionais ou atuação física.
Q05 permanece parcial para seleção/compatibilidade de outros controladores/modelos.
O próximo passo é explicitar esses critérios de compatibilidade antes de permitir
qualquer seleção, mantendo modelos opcionais/OFF. Aceite Windows independente e
reconstrução do executável também continuam pendentes.

[Validação local](../results/orion_q_policy_decisions_20260928.md) · [Roadmap](roadmap.md).
