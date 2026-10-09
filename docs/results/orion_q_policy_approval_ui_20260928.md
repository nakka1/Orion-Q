# Q05 — confirmação nativa de aprovação (28/09/2026)

Entregues **APPROVE PROPOSAL**, confirmação explícita e **EXPORT APPROVAL** no
painel POLICY REVIEW. A proposta/revisão capturada é apresentada antes da declaração
e revalidada em segundo plano. Nenhuma política foi ativada.
[Contrato e uso](../product/orion_q_policy_approval_q05.md).

## Comportamento

APPROVE PROPOSAL só fica disponível com revisão CURRENT e capacidade local de
aprovação. A janela modal mostra limiares de referência/proposto, proposta/revisão,
horários, hashes e topologia. O rótulo do operador começa vazio e a caixa de
aprovação começa desmarcada; ambos são obrigatórios para RECORD APPROVAL.
Cancelar não registra aprovação nem altera a revisão. Não há aprovação automática.

A solicitação usa os hashes capturados, relê contexto e prazo pela API já validada
e mantém a referência. O rótulo é uma declaração local, sem autenticação de
identidade. A capacidade de aprovação é distinta da revisão; fontes sintéticas
ou somente leitura não a oferecem. A sessão gráfica continua separada da API.

Fonte/runtime/capacidade diferentes, edição, prazo expirado ou fechamento invalidam
a seleção e fecham a confirmação. Mudanças de política sem troca de runtime são
recusadas na releitura do backend. As operações continuam serializadas, sem fila;
cancelar não espera a leitura SQLite terminar, e resultados tardios são descartados.
Uma nova revisão remove o recibo selecionado e exige confirmação nova.

Após sucesso, o painel exibe APPROVAL RECORDED, operador declarado, ID e horário;
o recibo completo fica na aba CAPTURED RECEIPT. EXPORT APPROVAL grava a evidência
capturada, sem releitura ou sobrescrita. Mudanças durante o seletor de arquivo
impedem o envio. Uma gravação já aceita pode concluir após fechar, sem restaurar
a seleção. O prazo original não é renovado. Recibo histórico não autoriza ativação.

## Validação local

- **165 testes distintos aprovados**, sem falhas, erros ou skips.
- `outputs/policy_approval_ui_20260928/native.xml`: **65 testes**, incluindo **27
  novos** de aprovação nativa e 38 de preparação/revisão, em 130.56 s.
- `outputs/policy_approval_ui_20260928/integration.xml`: **100 testes**
  de aprovação backend, provider, capacidades, telas e barreira física, em
  92.93 s. Nenhum caso duplicado entre os XMLs.
- Confirmação obrigatória, cancelamento, rótulos inválidos, revisão STALE, mudança
  de política/fonte/capacidade, prazo, resultados tardios, ausência de fila,
  exportação capturada/exclusiva e banco/política de referência preservados.
- Um aborto Qt foi reproduzido ao processar eventos após o fechamento do diálogo.
  Manter a QApplication por sessão não resolveu. Desconectar os callbacks antes de
  `deleteLater()` corrigiu o descarte; teste adicional repete abertura/cancelamento
  e verifica a destruição Qt sem restaurar seleção. O log inicial foi preservado
  em `qt-diagnostic.log`. A suíte final usa o ciclo de vida original da aplicação.
- Ruff aprovado nos seis arquivos Python; YAML e sintaxe PowerShell validados.
  A suíte foi incluída no workflow e no build Windows.
- Capturas offscreen inspecionadas: confirmação em **600 × 480**, painel em
  **640 × 500** e **1000 × 700**. Controles e estado ficam visíveis; detalhes completos
  permanecem roláveis no tamanho mínimo. Recibo de teste e imagens em `final-native/`.

Todos os recibos de teste mantêm ativação/autoridade física indisponíveis. As
confirmações automatizadas são fixtures, não decisões humanas reais. Não houve
aprovação de runtime de operação, CI remota ou reconstrução de executável Windows.

## Preservação e roadmap

A auditoria `outputs/policy_approval_ui_20260928/verification.json` compara os **579
arquivos existentes** no início, incluindo mudanças anteriores não commitadas,
e verifica os 240 relatórios SO(3), 15 fontes congeladas, summary, protocolo e XML
histórico dos 500 testes. `implementation.diff` contém somente este incremento.
As evidências científicas não foram reexecutadas nem reinterpretadas.

O [roadmap](../product/roadmap.md) passa à ativação transacional em simulação com
revalidação própria, auditoria persistente da aprovação/ativação, readback da
política ativa e rollback auditável. Q05 permanece parcial. Modelos opcionais
seguem OFF e nenhuma autoridade física foi adicionada.
