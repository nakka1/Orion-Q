# Q05 — contrato local de aprovação (28/09/2026)

Implementada a declaração explícita de aprovação via API Python local, vinculada
à proposta e à última revisão CURRENT da sessão. A aprovação relê o contexto,
preserva o prazo monotônico original e produz recibo histórico exportável.
Nenhuma política foi ativada. [Contrato e uso](../product/orion_q_policy_approval_q05.md).

## Entrega

A solicitação exige modo simulação, UUID da proposta, hashes da proposta e revisão,
rótulo declarado do operador e confirmação literal. Não aceita modelos opcionais,
campos adicionais, revisões importadas/substituídas, outra sessão/runtime, contexto
alterado, revisão STALE ou prazo expirado. Nova observação verifica a política,
supervisor e versão/hash da topologia; uma proposta obsoleta continua inválida.

O recibo mantém a revisão selecionada e a revisão de confirmação com hashes.
RECORDED declara o registro naquele instante, sem autenticar identidade ou conceder
autoridade de ativação. A etiqueta de operador é explicitamente declarada pelo
chamador. Testes automatizados não constituem uma aprovação humana real.

A revisão agora inclui UUID próprio e verifica expiração também após a captura,
evita aceitar uma leitura que atravesse o limite de 300 segundos. O registro de
aprovação faz uma última verificação de prazo antes de guardar seu recibo. Não
há renovação por aprovação/exportação. Duplicatas e chamadas concorrentes não
registram duas aprovações sobre a mesma seleção.

Uma sessão guarda um recibo de aprovação em memória; nova proposta aceita, revisão
ou invalidação remove sua seleção. Exportação é uma cópia histórica, sem releitura
ou sobrescrita, inclusive após expiração ou mudança não observada. O arquivo não é
um token de ativação. Objetos retornados não podem modificar o recibo interno.

A confirmação gráfica, armazenamento persistente de aprovação/revogação, ativação
transacional, confirmação da política ativa e rollback permanecem pendentes. O
painel existente continua preparando/revisando, sem novo botão de aprovação.
A captura não oferece transação atômica de futura ativação entre memória e SQLite.

## Validação

- **212 testes distintos aprovados**, sem falhas, erros ou skips.
- `outputs/policy_approval_20260928/targeted.xml`: **98 testes** de preparação e
  aprovação, incluindo **49 novos**, em 146.10 s.
- `outputs/policy_approval_20260928/integration.xml`: **114 testes**
  da interface Q05, serviço QKD, provider, sandbox e barreira física, em
  93.93 s. Não há testes duplicados entre os XMLs.
- Casos novos: confirmação estrita, hashes e campos inválidos, dados destacados,
  revisão/proposta substituída, fontes diferentes, política/supervisor/topologia
  alterados ou indisponíveis, mesmo conteúdo com novo objeto, expiração durante
  captura/registro, concorrência, bloqueio de ciclo, seleção invalidada, exportação
  histórica exclusiva e ausência de inicialização/decisão/executor/modelos.
- Ruff aprovado nos cinco arquivos Python novos/alterados; workflow YAML e sintaxe
  PowerShell validados. Build/workflow Windows incluem a nova suíte.
- Diagnóstico em `outputs/policy_approval_20260928/diagnostic/`: aprovação da fixture,
  recusa de duplicata, contexto alterado e sessão invalidada. SQLite permaneceu
  intacto; nenhuma política foi ativada pela aprovação e nenhum comando foi emitido.

O diagnóstico usa novo simulador, com etiqueta `automated diagnostic fixture`,
e altera explicitamente sua própria política entre observações para testar recusa.
Esse arranjo não representa consentimento humano, efeito da aprovação, calibração
ou ganho científico. Nenhum runtime de operação foi aprovado por esta tarefa.

## Preservação e roadmap

A auditoria `outputs/policy_approval_20260928/verification.json` compara os **574
arquivos existentes** no início, incluindo alterações anteriores não commitadas,
e verifica os 240 relatórios SO(3), 15 fontes congeladas, summary, protocolo e XML
histórico dos 500 testes. `implementation.diff` registra somente este incremento.
Não houve CI remota, nova campanha científica ou reconstrução do executável Windows.

O [roadmap](../product/roadmap.md) passa à confirmação explícita de aprovação no
painel nativo, antes de ativação transacional em simulação, readback e rollback.
Q05 permanece parcial, os modelos continuam opcionais/OFF e não há atuação física.
