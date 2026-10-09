# Q05 — inspeção contextual nativa (29/09/2026)

Implementado EDGE CONTROL → CONTEXT INSPECTION para a fonte local com runtime já
inicializado. LOAD CONTEXT, candidato de inspeção, snapshot JSON explícito,
OBSERVE INPUT e EXPORT CAPTURED usam o [contrato contextual](../product/orion_q_policy_context_q05.md).
Nenhum resultado autoriza seleção, mudança de política ou ação.

## Fluxo e limites

O painel tem sessão privada, sem substituir contexto criado pela API ou propostas
de POLICY REVIEW. Abrir não consulta nem cria runtime. Entrada/previsão/proveniência/
timestamps não são preenchidos ou renovados automaticamente. JSON é limitado a
64 KiB e recusa duplicatas e números não finitos antes da validação contextual.

A aba CONTEXT / RESULT mostra resultado, candidato, idade efetiva, motivos,
configuração e hash capturados. CAPTURED RECEIPT mostra o JSON completo. O estado
OBSERVED_SUPPORTED_INPUT corresponde somente à entrada para o alvo existente;
BLOCKED conserva seus motivos e pode ser exportado. Origem permanece declarada,
sem autenticação, e nenhuma ação é avaliada individualmente. Modelos ficam OFF.

Consultas/exportações ocorrem em segundo plano, sem fila. Edição durante trabalho,
fonte/runtime/caminho/geração alterados, parada, versão observada de topologia,
expiração ou fechamento descartam resultados. Cancelar apenas sinaliza invalidação,
sem esperar SQLite; retornos tardios não restauram a seleção. O prazo gráfico de
300 s começa antes do worker. Conteúdo de topologia com versão igual é verificado
pela API na observação, sem leitura contínua no timer nativo.

Exportação captura o JSON antes do seletor de arquivo e revalida fonte/seleção/
prazo depois. Grava arquivo novo sem sobrescrever ou refazer a consulta. Uma
exportação já aceita pode terminar após fechar/editar; o resultado tardio é
ignorado e o arquivo conserva a observação histórica aceita.

## Validação

- **259 testes distintos aprovados**, incluindo **24 novos**. Zero falhas,
  erros ou skips nas rodadas concluídas: painel, 24 (`fixed.xml`); integração,
  235 (`integration.xml`).
- Casos novos cobrem sessão privada versus API, leitura em worker, banco preservado,
  bloqueio do candidato, edição, JSON incompleto/duplicado/não finito/grande,
  cancelamento durante leitura por oito mudanças, seis invalidações no diálogo de
  exportação, gravação capturada em worker, arquivo existente, topologia/prazo e
  fontes sem capacidade. A primeira rodada falhou porque o identificador do caso
  grande excedia o limite Windows da variável PYTEST_CURRENT_TEST; IDs curtos
  corrigiram a infraestrutura do teste, sem alterar o comportamento do produto.
- Regressão cobre API contextual/declarativa, POLICY REVIEW, DECISION AUDIT,
  catálogo, capacidades, providers, fontes e telas da console.
- Ruff passou em sete arquivos Python; YAML, sintaxe PowerShell e diff passaram.
  Build/workflow incluem a nova suíte. EXE não reconstruído; CI remota não executada.

A captura em **640×480** foi inspecionada: resultado, ausência de autorização,
idade, contexto e botões ficaram legíveis. Também foi capturada **1000×700**.
`outputs/policy_context_ui_20260929/gui/` contém imagens e recibo verificado da
fixture. O painel não executa ciclos, carrega pesos ou muda políticas; testes
usam apenas bancos e simuladores isolados.

## Preservação e roadmap

A auditoria parte de **638 arquivos preexistentes**, com doze arquivos
modificados neste incremento e quatro novos de sua autoria. Alterações anteriores,
incluindo monografia, foram preservadas. `verification.json` confere 240 relatórios
SO(3), quinze fontes congeladas e hashes de summary/protocolo/XML histórico de
500 testes. `implementation.diff` isola este incremento.

O [roadmap](../product/roadmap.md) agora pede contrato de tradução limitada das
ações dos roteadores para recursos/precondições do repetidor simulado. Ações sem
correspondência devem ser recusadas antes de qualquer seleção. Q05 continua
parcial: não há adaptador opcional integrado ao ciclo de ativação. Modelos opcionais/
OFF, nenhum runtime operacional ativado e nenhuma atuação física.
