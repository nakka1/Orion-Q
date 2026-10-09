# Q05 — rollback aprovado e recuperação explícita

API local de simulação, implementada em 28/09/2026. Complementa a
[ativação vinculada à instância](orion_q_policy_activation_q05.md). O painel nativo
ACTIVE POLICY também oferece esses fluxos; não há novas rotas HTTP. Modelos continuam opcionais/OFF;
nenhum fluxo emite comandos, executa ciclos ou concede autoridade física.

## Rollback

`AssuranceDashboardService.prepare_policy_rollback(activation_id, expected_sha256)`
exige a ativação atualmente verificada nesta instância. Lê a configuração anterior
registrada e prepara uma proposta com esse limiar exato. A preparação não altera
a política. Mudanças em outros parâmetros continuam fora do contrato.

A proposta segue o fluxo existente: `review_policy_change`, `approve_policy_change`
e `activate_policy_change`, cada qual com sua seleção/hash e confirmação exigida.
A ativação relê a referência de rollback e o destino dentro da transação. O recibo
contém `transition_kind=rollback` e `rollback_of` com ID/hash da origem e hash do
destino. Não se reutiliza a aprovação antiga. Prazo, contexto, custos, tempos,
supervisor, topologia e instância seguem as mesmas verificações de ativação.

Registros históricos não são apagados ou reescritos. O rollback cria uma nova
transição; não desfaz recibos, ciclos já executados ou consumo de recursos. Parar
uma fonte não provoca rollback automático. Falha de commit também pode ocorrer no
rollback e mantém o bloqueio conservador de ciclos.

## Recuperação de commit incerto

Quando uma tentativa de commit falha ou excede o prazo, o runtime restaura o objeto
anterior e conserva um `fault_id` com recibo da tentativa, identidades dos objetos e
caminho do diário. `policy_activation_status()` retorna UNRESOLVED. Novos ciclos e
ativações ficam bloqueados, mesmo que exista uma linha persistida da tentativa.

`policy_recovery_context()` inspeciona a instância existente, sem inicializar banco
ou runtime, sem trocar política e sem gravar no diário. Confere objetos, parâmetros,
supervisor, referência anterior, topologia e eventual registro da tentativa falha.

| Observação | Significado |
|---|---|
| `present_and_verified` | O registro da tentativa está presente e seus hashes/índices foram verificados |
| `absent_at_observation` | Não foi encontrado no instante observado; não prova que o commit nunca ocorreu |

Apenas a última observação local pode ser usada. Seu prazo monotônico de 300 segundos
começa antes da leitura; uma leitura que consome o prazo não produz seleção válida.
Nova observação, parada/invalidação da fonte ou reinício invalida a seleção anterior.
JSON exportado não substitui esse estado em memória.

`recover_policy_change(payload)` aceita **somente** estes campos:

| Campo | Valor exigido |
|---|---|
| `mode` | `simulation` |
| `fault_id` | UUID canônico da falha observada |
| `expected_recovery_sha256` | SHA-256 da última observação selecionada |
| `operator_label` | Identificação declarada, imprimível, de 1 a 80 caracteres, sem espaços externos |
| `confirmation` | `KEEP_RESTORED_SIMULATION_POLICY` |

O rótulo não autentica o operador. A confirmação autoriza manter a política já
restaurada, sem repetir a candidata falha. A tentativa válida consome a seleção;
campos inválidos ou referência errada não a consomem. Sob bloqueio do ciclo e
`BEGIN IMMEDIATE`, todo contexto é relido e comparado, incluindo a observação da
linha falha. Qualquer divergência exige nova inspeção.

O checkpoint `orion.policy_recovery.v1` guarda pedido explícito, observação,
recibo completo da tentativa falha, política retida, supervisor, identidade da
instância e hashes. Usa o diário `policy_activations`, com revisão sequencial e
identificador único `recovery:<fault_id>` na coluna histórica `approval_id`.
Este identificador evita duplicação; não representa aprovação de uma proposta.

Somente após commit e nova verificação de prazo o bloqueio é liberado. A referência
do objeto da política permanece idêntica. O serviço invalida propostas/aprovações
anteriores. ACTIVE passa a significar que a política retida corresponde ao novo
checkpoint; o ciclo seguinte precisa verificar essa correspondência normalmente.

Se a recuperação falhar antes do commit, o bloqueio permanece. Se o commit da
recuperação também for incerto, uma nova falha identifica esse checkpoint como
nova tentativa não resolvida. É necessário inspecionar e confirmar novamente.
Não há retry automático. Recibos encadeados preservam evidências; o limite de
256 KiB é mantido e, se excedido, a gravação é recusada com ciclos ainda bloqueados.

Se já havia uma ativação válida, o checkpoint preserva seu destino anterior para
rollback posterior. Se a política retida era apenas o baseline inicial, não há
ativação anterior disponível para rollback; uma nova alteração exige preparação
normal. Reinício cria outra identidade e não reaplica ativação/checkpoint histórico.

## Interface nativa

Em EDGE CONTROL → POLICY REVIEW → ACTIVE POLICY:

1. READ ACTIVE POLICY consulta o estado atual em segundo plano.
2. Com ACTIVE e destino anterior disponível, PREPARE ROLLBACK captura ID/hash do
   registro observado e prepara o limiar anterior. O campo fica somente leitura;
   LOAD BASELINE permite abandonar a proposta e iniciar uma alteração normal.
   REVIEW, APPROVE PROPOSAL e ACTIVATE continuam etapas explícitas e separadas.
   As confirmações identificam o rollback e sua origem.
3. Com UNRESOLVED, INSPECT RECOVERY observa a tentativa incerta e mostra política
   retida, falha, presença/ausência do registro, horário e hash. Nada é recuperado
   pela inspeção. CONFIRM RECOVERY exige rótulo declarado e caixa desmarcada por
   padrão; KEEP POLICY AND RECOVER solicita o checkpoint.
4. Após commit, o painel mostra recibo histórico; READ ACTIVE POLICY confirma o
   estado observado. Se a recuperação também falhar, nova inspeção e confirmação
   são exigidas. Não existe repetição automática.

Capacidades separadas `prepare_policy_rollback` e `recover_simulation_policy` são
anunciadas pelo serviço local com runtime existente. Fontes sintéticas/medidas não
recebem esses controles. A seleção de recuperação fica vinculada ao binding do
painel: outra inspeção a substitui, e um painel antigo não pode recuperar com a
revisão nova nem removê-la ao fechar. A comparação e a remoção usam um bloqueio
curto de seleção, nunca mantido durante leitura SQLite ou commit. READ ACTIVE POLICY descarta a inspeção de
recuperação local; EXPORT CAPTURED STATE pode preservar seu recibo sem sobrescrita.

O prazo local começa antes do envio ao worker e é conservador em relação ao prazo
do backend. Fonte/runtime/capacidade, edição, expiração, topologia observada ou
fechamento invalidam a confirmação. Uma inspeção que termina depois da invalidação
remove somente a seleção que ela própria criou. O binding fixa o runtime também
para operações que não criam sessão de preparação.

O callback interno de recuperação verifica a fonte após obter o bloqueio do ciclo
e imediatamente antes do commit, preservando as validações de seleção/contexto/prazo.
Cancelar antes dessa fronteira mantém o bloqueio de ciclos. Depois que commit
começa, fechar ou trocar fonte não promete desfazê-lo: o resultado pode persistir,
e a interface exige nova leitura para observá-lo.

Recuperação concluída invalida aprovações anteriores, inclusive de outros painéis.
O painel responsável reconhece apenas a mudança de geração anunciada por sua
própria operação, conservando o recibo sem aceitar mudanças de fonte/runtime ou
invalidações posteriores. A política permanece o mesmo objeto restaurado.

[Validação nativa](../results/orion_q_policy_recovery_ui_20260928.md).

## Diagnóstico e limites

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
python tools/validate_policy_recovery.py --output-dir outputs/q05-recovery-diagnostic-new
```

O diretório deve ser novo. O diagnóstico cria um simulador isolado, usa declarações
identificadas como `automated diagnostic fixture`, ativa e reverte um limiar de
exemplo e injeta perda da resposta de commit apenas nesse processo. Verifica a
recuperação mantendo o objeto anterior. Não executa ciclos nem representa aprovação
humana ou ganho científico.

Alterações arbitrárias fora dos bloqueios e autenticação/assinatura de operador
continuam fora do contrato. Esta API não recupera automaticamente uma configuração
modificada externamente nem promete atomicidade durável entre memória e SQLite.
Ativação e leitura atual já têm [interface nativa](orion_q_policy_activation_q05.md).
Rollback/recuperação também têm interface e testes integrados do fluxo de limiar.
A [explicação estruturada histórica](orion_q_policy_decisions_q05.md) também está
integrada. Próximo: critérios de seleção/compatibilidade de outros controladores;
Q05 permanece parcial e modelos seguem OFF.
O executável Windows não foi reconstruído.

[Decisão](../architecture/adr_q05_002_explicit_recovery.md) ·
[Validação](../results/orion_q_policy_recovery_20260928.md) · [Roadmap](roadmap.md).
