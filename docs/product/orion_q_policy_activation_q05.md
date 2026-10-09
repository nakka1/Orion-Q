# Q05 — ativação auditável em simulação

Data: 28/09/2026. API Python local; apenas a política de utilidade do repetidor.

## O que muda

Uma nova solicitação explícita pode ativar o limiar de uma proposta já aprovada
na **mesma sessão e instância de runtime**. Os demais parâmetros aprovados são
preservados. A ativação não decide ações, executa ciclos, aciona hardware ou abre
pesos opcionais. O runtime separado do bundle WDM não é selecionado.

Aprovar continua sem ativar. O JSON de aprovação, mesmo válido, não é token
importável: é preciso a aprovação ainda selecionada em memória, contexto inalterado,
prazo monotônico vigente e confirmação específica de ativação. Ainda não há botão
nativo ou rota HTTP de ativação. A [decisão arquitetural](../architecture/adr_q05_001_process_bound_activation.md)
explica a fronteira entre memória e SQLite.

## Uso local explícito

Na mesma instância usada para [preparar, revisar e aprovar](orion_q_policy_approval_q05.md):

```python
from quantum_autopilot.control_plane.policy_activation import ACTIVATION_CONFIRMATION

activation = service.activate_policy_change({
    "mode": "simulation",
    "approval_id": approval["record"]["approval_id"],
    "expected_approval_sha256": approval["record_sha256"],
    "confirmation": ACTIVATION_CONFIRMATION,
})
readback = service.policy_activation_status()
service.export_policy_activation(
    activation["record"]["activation_id"], activation["record_sha256"],
    "policy-activation-new.json",
)
```

A confirmação literal é `ACTIVATE_APPROVED_SIMULATION_POLICY`. Campos adicionais,
modo físico/shadow, hashes diferentes, outra sessão, aprovação substituída, contexto
alterado ou prazo vencido são recusados. Preparação/revisão/aprovação não inicializam
runtime/topologia; ativação também exige esses recursos existentes.

O prazo original de 300 segundos não é renovado. A ativação consome a seleção quando
começa a tentativa válida, inclusive em falha; repetir exige nova preparação e
aprovação. Solicitações estruturalmente inválidas ou com seleção errada não a consomem.
A política ativada não expira ao fim do prazo da proposta: o prazo limita a transição,
não a duração dos ciclos posteriores. Fonte parada não causa rollback implícito.

## Transação e evidência persistente

O bloqueio do ciclo impede execução enquanto SQLite mantém `BEGIN IMMEDIATE`.
A captura usa a mesma conexão da transação e relê política, supervisor e topologia.
A política candidata é construída com os custos/tempos da configuração aprovada e
um limiar novo; o objeto anterior permanece intacto.

A tabela `policy_activations` é criada na tentativa de ativação ou recuperação
explícita, dentro da transação. Guarda aprovação completa, contexto, configurações anterior/nova,
supervisor, identidade da instância, UUID da ativação, revisão sequencial e hashes.
Não modifica os recibos de ciclos anteriores, eventos de segurança ou topologia.
A leitura do recibo é estrita, limitada a 256 KiB, verifica hashes e índices; a
exportação é histórica e recusa sobrescrita.

COMMITTED descreve uma transição persistida, não atividade atual. A API de leitura
retorna um recibo com um destes estados:

| Estado | Significado |
|---|---|
| ACTIVE | Política/supervisor em memória correspondem ao registro e aos objetos da instância, no instante da leitura |
| NO_LOCAL_ACTIVATION | Esta instância não realizou ativação; registros de outras instâncias continuam históricos |
| UNVERIFIED | Há marca local de ativação, mas política, objetos, caminho do diário, recibo ou índices divergem/estão indisponíveis |
| UNRESOLVED | Falha/expiração durante tentativa de commit deixou resultado incerto; política anterior restaurada e ciclos bloqueados |

Falhas antes do commit desfazem a transação e restauram a referência exata da
política anterior. Se o commit puder ter ocorrido, restaurar a memória não permite
concluir que SQLite voltou ao estado anterior. O runtime conserva UNRESOLVED e
recusa novos ciclos/ativações. Não há retry ou liberação automática desse bloqueio.
A aprovação válida não é apresentada como prova de que um commit falho não aconteceu.

Depois de ativar, cada ciclo confere o registro e a política atual antes de decidir;
UNVERIFIED/UNRESOLVED impede a execução. Só SIMULATION é aceito, e o recibo do ciclo
inclui ID, revisão e hash da política ativada. Replay idempotente continua devolvendo
o recibo original, sem atribuir retrospectivamente a ele uma nova política. Novos
ciclos incluem também `activation_record_sha256`, usado pela [consulta estruturada
de decisões](orion_q_policy_decisions_q05.md); registros antigos permanecem intactos.

## Interface nativa

Em EDGE CONTROL → POLICY REVIEW, a aprovação mantém o baseline até a solicitação
separada ACTIVATE. A confirmação mostra limiares anterior/aprovado, ID/hash da
aprovação, operador declarado e topologia. A caixa começa desmarcada. Somente
ACTIVATE IN SIMULATION confirmado envia a solicitação ao worker com a sessão
privada do painel; JSON histórico não pode substituir a aprovação selecionada.

O painel exige capacidades separadas `activate_simulation_policy` e
`read_active_policy`, anunciadas pelo serviço local somente com runtime existente.
Fontes sintéticas/medidas não recebem ativação. O fluxo altera exclusivamente o
limiar da política existente; não seleciona pesos opcionais ou fontes de decisão.

Troca de fonte/runtime/capacidade, edição, expiração e fechamento invalidam a
confirmação e descartam resultados atrasados. A invalidação não espera bloqueios do
worker. O backend verifica a seleção da fonte após adquirir o bloqueio de ciclo e
imediatamente antes de iniciar commit. Cancelamento antes dessa fronteira restaura
memória e desfaz a transação. **Depois que commit começa, fechar ou trocar fonte
não promete cancelamento**: a operação aceita pode concluir ou ficar incerta;
a evidência permanece no diário e deve ser consultada novamente. Não há rollback
implícito ou retry automático. O callback interno de validação da fonte acrescenta
checagens, sem substituir aprovação, prazo ou contexto do contrato local.

Após sucesso, a aprovação é consumida e o painel mostra COMMITTED como recibo
histórico. A aba ACTIVE POLICY oferece READ ACTIVE POLICY, independente de carregar
a referência, para consultar o runtime atual em segundo plano. Mostra estado,
horário da observação, identidade da instância e, quando verificada, política,
limiar, revisão e ID/hash do registro. UNRESOLVED informa ciclos bloqueados e
recuperação explícita por INSPECT RECOVERY e confirmação própria. A observação não é renovada automaticamente: o usuário
solicita nova leitura. Uma nova leitura não consome a aprovação ainda vigente.

EXPORT CAPTURED STATE exporta a última observação selecionada ou, se ainda não houve
leitura, o recibo de commit capturado. Captura ocorre antes do diálogo de arquivo;
revalida fonte/seleção/visibilidade antes do worker e recusa sobrescrita. A exportação
preserva o passado e não consulta novamente a política. Um arquivo já aceito pode
terminar de ser escrito após fechamento, sem restaurar seleção na interface.

[Rollback e recuperação](orion_q_policy_recovery_q05.md) também têm controles nativos
com revisão e confirmação próprias. Nenhum controle desse painel
executa ciclo, aciona hardware ou aplica ativação histórica após reinício.
[Validação nativa](../results/orion_q_policy_activation_ui_20260928.md).

## Diagnóstico reproduzível

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
python tools/validate_policy_activation.py --output-dir outputs/q05-activation-diagnostic-new
```

O diretório precisa ser novo. O diagnóstico cria e ativa apenas sua própria fixture
simulada; usa etiqueta `automated diagnostic fixture`, sem aprovação humana real.
Verifica ACTIVE, divergência UNVERIFIED e reinício NO_LOCAL_ACTIVATION, sem executar
ciclos. A alteração direta usada para causar divergência é preparação do teste,
não um caminho de ativação suportado.

## Reinício, limites e próximos passos

Reinício cria nova identidade e **não restaura** a última ativação do banco. O
runtime segue sua inicialização habitual e informa NO_LOCAL_ACTIVATION. Exportar
um recibo antigo não altera isso. Outros runtimes no mesmo SQLite são independentes;
não há eleição de uma política global pelo maior número de revisão.

A leitura ACTIVE é uma observação, sem assinatura ou autenticação do operador.
Mudanças arbitrárias fora dos bloqueios não são suportadas. A topologia é vinculada
no instante da ativação; mudanças posteriores não desfazem a política, e cada ciclo
continua passando pelas validações existentes de snapshot/supervisor.

Modelos permanecem opcionais/OFF e a barreira física permanece fechada.
[Rollback aprovado e recuperação explícita](orion_q_policy_recovery_q05.md) já têm
API local e interface, assim como ativação/readback. O fluxo de limiar existente está
integrado, com explicação histórica no DECISION AUDIT; Q05 segue parcial para
compatibilidade e seleção de outros controladores. Nenhuma seleção automática usa os resultados científicos SO(3).
[Validação](../results/orion_q_policy_activation_20260928.md).
