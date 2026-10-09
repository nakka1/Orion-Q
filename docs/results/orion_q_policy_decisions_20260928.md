# Q05 — explicação estruturada das decisões (28/09/2026)

Implementados API local somente leitura e painel EDGE CONTROL → DECISION AUDIT para
explicar ciclos simulados a partir de recibos verificados. Intenção, avaliação de
segurança e resultado do executor são apresentados separadamente, com vínculo
histórico à política quando registrado. [Contrato](../product/orion_q_policy_decisions_q05.md).

## Implementação

READ LATEST 20 consulta uma janela limitada de ciclos `simulation`. A API aceita
limite de 1 a 50. EXPLAIN SELECTED consulta ID/hash da seleção; verifica hash do
recibo, índices, hash da requisição, esquema e coerência de intenção/segurança/
executor em uma conexão SQLite `ro` com `query_only` e transação de leitura.
Não cria runtime/banco/tabelas e não executa decisões ou ciclos.

A política referenciada é consultada na mesma transação de leitura. Novos ciclos
com ativação incluem `activation_record_sha256`, ancorando o recibo completo da
ativação, rollback ou recuperação. Ciclos antigos mantêm seu conteúdo e recebem
estado explícito de vínculo legado, ausente ou não verificável. O painel nunca
substitui configuração histórica pela atual; replay idempotente não ganha vínculo
retroativo. Consulta após reinício não exige inicializar um runtime.

A explicação conserva o recibo original e apresenta ação pretendida, autorização,
violações (código/restrição/requerido/observado/detalhe), ação efetiva e status,
flag de execução simulada e detalhes do executor. Utilidades/metadados são os
registrados, sem reconstrução de justificativa ou decisão por IA. Veto para HALT
continua distinto da execução de HALT pelo simulador.

Falha no vínculo da política conserva os fatos do ciclo verificado, sem apresentar
configuração como válida. Corrupção do próprio ciclo recusa a consulta inteira.
JSON é limitado a 256 KiB por ciclo e recusa campos duplicados/números não finitos.
Hashes são integridade local, sem assinatura ou autenticação humana.

A interface consulta em segundo plano, sem fila. Troca de fonte, seleção, caminho,
parada ou fechamento limpa o resultado e descarta retornos atrasados. Exportação
captura antes do diálogo de arquivo e revalida a seleção, em arquivo novo, sem
sobrescrita ou releitura. A tela EDGE CONTROL continua agregada; DECISION AUDIT é
restrito a ciclos simulados e não exibe decisões individuais de chegada de fótons.

## Validação

- **354 testes distintos aprovados**, incluindo **44 novos**: 29 de consulta e 15
  de interface. Não houve falhas, erros ou skips.
- Primeira rodada: 29 casos (`targeted.xml`). Interface e fontes: **62 casos**
  (`ui.xml`). Integração: **292 casos** (`integration.xml`), incluindo os 29 novos
  e regressões de ativação, recuperação, preparação/aprovação, sandbox, barreira
  física e serviço QKD. O total distinto desconta a primeira rodada repetida.
- Casos novos cobrem veto, execução registrada, baseline sem referência, hash completo,
  referência legada, reinício, rollback, recuperação, replay idempotente, corrupção de
  índices/JSON/hashes, limite de tamanho, modo não simulado recusado, ausência de
  runtime/banco, escrita concorrente durante o snapshot SQLite, seleção/fonte
  inválidas durante leitura/exportação e preservação do arquivo existente.
- Ruff passou em dez arquivos Python. YAML do workflow, sintaxe PowerShell e diff
  com CRLF preservado passaram. Build/workflow incluem as duas novas suítes.

Captura inspecionada em **640×480**, com intenção SWAP, autorização negada, ação
efetiva HALT e execução SIMULATED claramente separadas. Também há captura em
**1000×700**. `outputs/policy_decisions_20260928/gui/` contém as capturas e o recibo
verificado da fixture. Os ciclos foram gerados somente para testes isolados; a
consulta/painel não criam novos ciclos nem representam medição ou aprovação humana.

## Preservação e roadmap

`outputs/policy_decisions_20260928/verification.json` compara **600 arquivos
preexistentes**, incluindo alterações anteriores não commitadas. Confere os **240
relatórios SO(3)**, **15 fontes congeladas**, summary/protocolo e XML histórico dos
**500 testes**. `implementation.diff` registra o incremento.

Não houve campanha científica, atuação física, ativação no runtime de operação,
CI remota ou reconstrução do executável Windows. Modelos permanecem opcionais/OFF.

O fluxo da política existente agora tem explicação estruturada histórica. Q05 segue
parcial para seleção/compatibilidade de outros controladores/modelos. Próximo no
[roadmap](../product/roadmap.md): explicitar critérios de identidade, domínio de
entrada e ações suportadas antes de permitir seleção. `set_decision_source` continua
bloqueado; resultados SO(3) não elegem automaticamente uma política ou modelo.
