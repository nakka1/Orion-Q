# Q05 — preparação e revisão de política (27/09/2026)

Entregue o primeiro incremento de preparação/revisão Q05 via API Python local.
Propõe somente um novo limiar para a política de utilidade do repetidor existente,
preservando a referência e sem ativação. [Contrato e uso](../product/orion_q_policy_preparation_q05.md).

## Implementação

O contexto é observado no runtime local já inicializado, com seu bloqueio de ciclo,
configuração da política/supervisor e versão/hash da topologia persistida. Uma
verificação posterior recusa alteração detectada da política durante a leitura.
A preparação exige o hash desse contexto, clona a configuração e registra a
diferença de limiar; não decide, executa ciclos ou altera o journal.

O runtime não mantém uma versão persistente de política. Este incremento usa
hashes e uma revisão de observações na sessão, incluindo substituições de objeto;
não a apresenta como versão persistente ou histórico completo. Uma alteração
transitória não observada pode não ser detectada. A revisão não é uma transação
atômica de futura ativação entre memória e banco.

Cada sessão retém uma proposta, com UUID/hash e prazo de 300 segundos monotônicos.
A revisão retorna CURRENT ou STALE. CURRENT é compatibilidade observada, não
aprovação humana, ativação ou garantia futura. Invalidação observada é definitiva
para a proposta. Nova sessão/runtime ou nova preparação exige nova seleção.
O provider invalida a sessão ao parar. Exportação faz nova revisão, produz JSON
histórico com hashes e recusa sobrescrita.

O runtime do bundle WDM permanece separado; esta API não o seleciona nem modifica.
Modelos Q16 continuam OFF: candidatos ML/opcionais são recusados. Não há integração
gráfica, rota HTTP, aprovação, ativação, readback de ativação ou rollback nesta etapa.
`set_decision_source` continua recusando a troca.

## Validação local

- **189 testes aprovados**, sem falhas, erros ou skips, em 305.95 s
  no XML `outputs/policy_preparation_20260927/regression-final.xml`.
- **49 testes novos:** contratos estritos, referência real, ausência de ciclos/
  executor/modelos, estado de SQLite preservado, hashes, exportação exclusiva,
  mudança de política/custos/tempos/supervisor/topologia, substituição por objeto
  equivalente, corrupção/indisponibilidade, sessão/runtime diferentes, expiração
  monotônica, invalidação definitiva, dados retornados independentes, proposta
  substituída, bloqueio do runtime e parada do provider.
- A primeira rodada passou com 48 casos; o caso adicional verifica configuração
  interna booleana que não pode ser tratada como um custo numérico equivalente.
- Regressão inclui o serviço QKD, provider, fontes de entrada, interface Q04,
  sandbox, topologia e barreira física. Ruff aprovado em cinco arquivos Python.
- Workflow YAML e script PowerShell validados e atualizados com a suíte Q05.
  Nenhuma CI remota ou reconstrução de executável foi realizada.

`tools/validate_policy_preparation.py` reproduz três casos num novo inventário:
proposta CURRENT, topologia alterada tornando a proposta STALE e recusa da proposta
após invalidação da sessão. Evidências em `outputs/policy_preparation_20260927/diagnostic/`.
Em todos, preparação/revisão mantiveram o journal intacto e a política de referência
ativa, sem comandos. O diagnóstico altera explicitamente sua própria topologia
entre observações; isso não é uma ação da política ou uma campanha científica.
O limiar de exemplo não foi calibrado, recomendado ou associado a ganho.

## Preservação e roadmap

`outputs/policy_preparation_20260927/verification.json` registra os hashes dos
arquivos alterados e artefatos. A comparação inclui arquivos previamente
modificados/não rastreados pelo Git, além dos 240 relatórios SO(3), 15 fontes
congeladas, summary, protocolo e XML histórico dos 500 testes.

Próximo incremento: preparação/revisão na interface nativa, com referência/proposta
explícitas e invalidação por fonte/seleção. Aprovação, ativação transacional,
confirmação por leitura e rollback auditável permanecem pendentes. Q05 não está
concluída. Modelos continuam opcionais e sem atuação física.
