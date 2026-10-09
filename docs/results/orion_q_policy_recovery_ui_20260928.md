# Q05 — rollback e recuperação nativos (28/09/2026)

ACTIVE POLICY agora oferece preparação de rollback e inspeção/confirmação de
recuperação de commit incerto. O fluxo de limiar da política existente está
integrado ao painel, mantendo confirmação explícita, auditoria e escopo de simulação.
Somente novas fixtures isoladas foram ativadas/recuperadas durante a validação.
[Contrato](../product/orion_q_policy_recovery_q05.md).

## Comportamento entregue

PREPARE ROLLBACK exige observação ACTIVE com destino anterior disponível. Captura
ID/hash da ativação e prepara o limiar registrado, com campo somente leitura.
REVIEW, APPROVE PROPOSAL e ACTIVATE continuam etapas separadas; as confirmações
identificam a origem do rollback. A preparação não troca a política e a aprovação
anterior não é reutilizada. LOAD BASELINE permite abandonar a proposta.

INSPECT RECOVERY exige UNRESOLVED, observa a falha e a política retida e conserva o
bloqueio de ciclos. CONFIRM RECOVERY mostra limiar, falha, presença/ausência do
registro, identidade da instância, horário e hash. Exige rótulo declarado e caixa
inicialmente desmarcada. KEEP POLICY AND RECOVER mantém o objeto restaurado e grava
checkpoint; não repete a candidata falha nem autentica a identidade declarada.

Seleções pertencem ao binding do painel. Outra inspeção substitui a seleção, e
um painel antigo não consegue recuperar usando o recibo novo ou removê-lo ao fechar.
A comparação/limpeza usa um bloqueio curto, liberado antes de qualquer leitura
SQLite ou commit. READ ACTIVE POLICY descarta a inspeção de recuperação local;
EXPORT CAPTURED STATE pode conservar seu recibo em arquivo novo.

O backend acrescenta validação de fonte após o bloqueio do ciclo e antes do commit.
Fonte/runtime/capacidade, edição, expiração e topologia observada invalidam a
confirmação. Resultados atrasados são descartados. Uma inspeção que termina após
fechamento remove apenas sua própria seleção. Após o início do commit, fechamento
não promete cancelamento: o resultado pode persistir e exige nova leitura explícita.

Recuperação concluída invalida aprovações anteriores, inclusive de outros painéis.
O painel responsável reconhece apenas sua própria mudança anunciada de geração,
sem aceitar alterações independentes de fonte/runtime. Se já havia uma ativação
válida, o destino anterior permanece disponível para rollback posterior.
COMMITTED continua histórico; ACTIVE depende de nova leitura verificada.

## Validação local

- **438 testes distintos aprovados**, incluindo **32 novos** de rollback/recuperação
  nativos, sem falhas, erros ou skips nas rodadas registradas.
- Primeira rodada: **27 casos**, em `targeted.xml`. A regressão integrada passou
  com **174 casos gráficos** (`ui-final.xml`) e **263 de backend** (`backend-final.xml`).
- A revisão final reforçou a atomicidade da limpeza de seleção. Após essa mudança,
  **129 casos afetados** foram executados em `concurrency-final.xml`, incluindo todos
  os 32 testes nativos novos, 46 de recuperação e 51 de ativação. O total distinto
  desconta repetições entre rodadas; o caso adicional cobre fechamento de painel
  antigo durante uma nova inspeção bloqueada em leitura.
- Cobertura inclui fluxo nativo de rollback, recuperação de registros presentes/
  ausentes, campos inválidos, confirmação cancelada, fonte/capacidade/runtime,
  expiração, política/topologia alteradas, revisão substituída, concorrência entre
  painéis, falha no próprio commit de recuperação, fechamento antes/depois do início
  do commit, exportação exclusiva e recuperação seguida de rollback nativo.
- Ruff passou em 11 arquivos Python. YAML do workflow e sintaxe PowerShell passaram;
  ambos incluem a suíte nova. O diff foi verificado respeitando CRLF existente.

A confirmação foi inspecionada em **600×500** e o painel em **640×500**, com controles
legíveis e texto longo rolável. Também há captura em **1000×700**. Capturas e recibos
estão em `outputs/policy_recovery_ui_20260928/gui/`. São evidências de teste, sem
aprovação humana real. A interface não executa ciclos; as suítes do backend usam
somente seus próprios simuladores isolados.

## Preservação e próximos passos

A auditoria `outputs/policy_recovery_ui_20260928/verification.json` compara **597
arquivos preexistentes**, incluindo alterações anteriores não commitadas. Confere
**240 relatórios SO(3)**, **15 fontes congeladas**, summary/protocolo e o XML
histórico dos **500 testes**. `implementation.diff` registra o incremento.

Não houve campanha científica, ativação/recuperação no runtime de operação, CI remota
ou reconstrução do executável Windows. Modelos continuam opcionais/OFF, sem atuação
física. Reinício não restaura automaticamente política ou seleção histórica.

Q05 permanece parcial além do fluxo de limiar existente. O próximo passo do
[roadmap](../product/roadmap.md) é vincular a política ativa à explicação estruturada
de ação pretendida, veto e ação efetiva. Seleção/compatibilidade de outros
controladores ou modelos e aceite Windows independente continuam pendentes.
