# Q05 — interface de preparação e revisão (27–28/09/2026)

Entregue em fonte o painel **EDGE CONTROL → POLICY REVIEW**, com carregamento
explícito da referência, proposta de limiar, revisão contextual e exportação JSON
capturada. Q05 permanece parcial: aprovação humana, ativação transacional,
confirmação da política ativa e rollback auditável ainda não estão implementados.
[Contrato e uso](../product/orion_q_policy_preparation_q05.md).

## Comportamento

O painel usa o runtime local já inicializado e a validação da API de preparação.
Exibe limiar de referência/proposto, identidade da proposta, topologia, horário e
estado da revisão. O recibo completo fica numa segunda aba. Não preenche um limiar
recomendado, inicializa runtime/inventário ou carrega modelos. O runtime WDM separado
não é selecionado. Os seletores ML/determinístico continuam sem ativação disponível.

Cada painel tem sessão própria; suas propostas não substituem as da API do serviço.
Captura e exportação usam tarefas em segundo plano, uma por vez, sem fila. Edição,
fonte/runtime diferentes, mudança observada da topologia, expiração e fechamento
invalidam a seleção conforme o contrato. Cancelamento não espera uma leitura SQLite
já iniciada; resultados tardios são descartados. O prazo local monotônico começa
antes de submeter a preparação, de forma conservadora em relação ao prazo interno.

CURRENT é compatibilidade no instante da revisão, não aprovação nem garantia atual.
EXPORT REVIEW grava o recibo exibido sem releitura e sem sobrescrita, inclusive
STALE. Mudanças durante o seletor de arquivo impedem o envio; uma gravação já aceita
pode terminar após fechar o painel, sem restaurar sua seleção. Nenhum recibo
exportado autoriza ativação. Não há comandos, decisão, ciclo ou atuação física.

## Validação local

- **235 testes aprovados**, sem falhas, erros ou skips, em 1447.87 s:
  `outputs/policy_review_ui_20260927/regression-final.xml`.
- **38 testes da nova interface** cobrem operações explícitas fora da thread gráfica,
  SQLite/referência preservados, rascunhos inválidos, expiração, edição, fonte/runtime,
  capacidade, resultados tardios, ausência de fila, fechamento/reabertura, topologia,
  revisão STALE, erro de captura, isolamento da API, seleção/rolagem preservadas,
  seletor de arquivo, exportação capturada e recusa de sobrescrita.
- A regressão inclui a API Q05, serviço QKD, provider, capacidades/fontes/telas,
  reconciliação gráfica Q04, sandbox e barreira física.
- A rodada inicial expôs uma falha no consumo do resultado assíncrono: o sinal de
  limpeza anterior ao envio marcava a geração como já processada. A correção só
  consome gerações com resultado/erro disponível. A repetição passou com 31 testes;
  sete casos adicionais foram incluídos na regressão final. O XML da rodada inicial
  com falhas foi preservado para rastreabilidade.
- Ruff aprovado nos oito arquivos Python novos/alterados; workflow YAML e sintaxe
  PowerShell validados. A suíte nativa foi incluída no workflow/build Windows.
- Capturas offscreen inspecionadas em **640 × 500** e **1000 × 700**, com referência,
  proposta, revisão e controles legíveis. Evidências em `final-native/`, incluindo
  `review.json`. Não equivalem a aceitação numa máquina independente.

Não houve CI remota nem reconstrução do executável Windows. A implementação está
em fonte; o pacote anterior permanece preservado. O valor 0.7 dos testes é apenas
entrada de diagnóstico, sem calibração ou alegação de ganho científico.

## Preservação e próximos passos

`outputs/policy_review_ui_20260927/verification.json` compara os 570 arquivos
existentes no início, incluindo alterações/não rastreados anteriores, com os
arquivos finais. Também verifica os 240 relatórios SO(3), 15 fontes congeladas,
summary, protocolo e XML histórico dos 500 testes. O diff deste incremento fica
em `implementation.diff`; a campanha científica não foi reexecutada nem reinterpretada.

O [roadmap](../product/roadmap.md) passa à aprovação explícita vinculada à proposta
e ao contexto revisado, seguida de ativação transacional em simulação, readback e
rollback auditável. Modelos opcionais seguem OFF; nenhuma autoridade física foi adicionada.
