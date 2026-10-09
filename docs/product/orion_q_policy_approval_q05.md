# Q05 — declaração local de aprovação

Data: 28/09/2026. API Python local em simulação; sem ativação de política.

## Escopo

Após [preparar e revisar](orion_q_policy_preparation_q05.md) uma proposta, o serviço
pode registrar uma declaração explícita de aprovação para o limiar proposto.
A solicitação vincula UUID/hash da proposta e hash da **última revisão CURRENT**
retida na mesma sessão. Recibos importados, revisões anteriores e propostas de
outro runtime/processo não são aceitos.

O rótulo de operador é declarado pelo chamador. Não há autenticação de identidade,
assinatura ou prova de presença humana. A API registra a declaração recebida;
não infere aprovação de CURRENT, de uma exportação ou do resultado de testes.
O painel POLICY REVIEW também oferece a confirmação explícita descrita abaixo.
Não há rota HTTP nova.

## Uso na instância local já inicializada

Depois de obter `proposal` e `review` pela API da mesma instância:

```python
from quantum_autopilot.control_plane.policy_approval import APPROVAL_CONFIRMATION

# Somente após a decisão explícita do operador sobre esta revisão.
approval = service.approve_policy_change({
    "mode": "simulation",
    "proposal_id": proposal["record"]["proposal_id"],
    "expected_proposal_sha256": proposal["record_sha256"],
    "expected_review_sha256": review["record_sha256"],
    "operator_label": "operador-local",
    "confirmation": APPROVAL_CONFIRMATION,
})
service.export_policy_approval(
    approval["record"]["approval_id"], approval["record_sha256"],
    "policy-approval-new.json",
)
```

`confirmation` exige exatamente `APPROVE_SIMULATION_PROPOSAL_ONLY`. Modo físico,
booleanos como confirmação, campos adicionais ou ausentes, hashes inválidos e
rótulos vazios, com controles ou mais de 80 caracteres são recusados. O rótulo
não pode ter espaços externos. Não enviar credenciais ou dados pessoais sensíveis.
Não há aprovação genérica, de modelo opcional ou de alterações futuras.

## Confirmação nativa

Após REVIEW produzir CURRENT, **APPROVE PROPOSAL** abre uma janela modal com o
limiar observado/proposto, UUID da proposta/revisão, horários, hashes e topologia.
Abrir a janela não registra aprovação. O rótulo do operador começa vazio e a caixa
“I approve this proposal for simulation only” começa desmarcada.

Informe um rótulo válido, marque a declaração e escolha **RECORD APPROVAL**.
A chamada usa exatamente os hashes mostrados, relê o contexto em segundo plano e
registra apenas se ainda válido. CANCEL ou fechar a confirmação mantém a revisão
inalterada; não há confirmação automática. Campos inválidos impedem o envio.
Nova revisão exige nova confirmação, com rótulo vazio e caixa desmarcada.

Troca/parada de fonte, perda de capacidade, troca de runtime, edição, expiração
ou fechamento do painel descarta a seleção e fecha a confirmação. Mudança de
política sem troca do runtime é detectada pela releitura do backend. Resultados
tardios são descartados; uma leitura já iniciada termina sem bloquear a interface.
As operações continuam serializadas, sem fila. A capacidade `approve_policy_proposal`
é distinta da revisão e indisponível em fontes sintéticas/somente leitura.

Após sucesso, o painel mostra APPROVAL RECORDED, rótulo declarado, horário e ID;
a aba CAPTURED RECEIPT passa a mostrar o JSON completo da aprovação. A política de
referência continua ativa. **EXPORT APPROVAL** grava exatamente esse recibo, sem
releitura ou sobrescrita. Troca de seleção/fonte ou expiração durante o seletor de
arquivo impede o envio. Uma gravação já aceita pode terminar após fechar o painel,
sem restaurar a seleção. Exportar a revisão capturada pela interface continua
possível e não produz nova revisão, diferentemente de `export_policy_review` da API.

O prazo gráfico original não é renovado pela confirmação ou aprovação. Após seu
fim, a interface exige nova preparação; uma exportação histórica pela API tem os
limites distintos descritos abaixo. A sessão gráfica é própria e não aprova as
propostas da API do serviço. [Validação nativa](../results/orion_q_policy_approval_ui_20260928.md).

## Registro e invalidação

A aprovação relê a política/supervisor e a topologia com o mesmo mecanismo da
revisão, sob bloqueio da sessão. Exige contexto idêntico ao revisado e proposta
não expirada. O prazo monotônico original de 300 segundos não é renovado; é
verificado após a captura e novamente antes de guardar o recibo. Uma leitura
que atravesse o prazo resulta em recusa. Revisões têm UUID próprio, inclusive
quando o relógio civil e o contexto coincidem.

O recibo `orion.policy_approval.v1` inclui UUID, horário UTC, solicitação explícita,
revisão selecionada e revisão de confirmação completas, com hashes. `RECORDED`
significa que a declaração foi registrada naquele instante; não significa que a
aprovação permanece válida ou foi usada. `operator_approval_recorded=true` vale
somente para esse recibo. Recibos de contexto, proposta e revisão mantêm esse
campo falso porque não registram uma decisão do operador.

A submissão válida consome a revisão selecionada e gera a observação de
confirmação. Duplicatas não criam novos recibos. Para uma nova declaração, faça
uma nova revisão explícita. Solicitações com campos/hashes inválidos preservam a
seleção; contexto obsoleto ou indisponível torna a proposta STALE de forma definitiva.

Nova proposta aceita, nova revisão (inclusive `export_policy_review`) ou invalidação
da sessão remove a aprovação da seleção local. A mesma sessão mantém no máximo
um recibo de aprovação, em memória. Não existe tabela persistente de aprovações,
recuperação após reinício ou revogação auditável persistente neste incremento.

## Exportação e limites

`export_policy_approval` grava **o recibo histórico capturado**, sem reler o
contexto ou aprovar novamente. Exige ID/hash do recibo ainda selecionado e arquivo
novo; nunca sobrescreve. Pode exportar evidência após o prazo ou após uma mudança
não observada do contexto. Isso preserva a declaração passada, sem renovar sua
validade. Nova revisão invalida essa seleção, exigindo nova aprovação antes de
exportar um novo recibo. Objetos retornados são cópias independentes.

Todos os recibos de aprovação mantêm `activation_authority=false`,
`activation_available=false`, `policy_activated=false`,
`physical_action_performed=false` e zero comandos. Não são tokens aceitos por
um executor. A política de referência, o runtime WDM separado e o SQLite
permanecem intactos; não há ciclo, decisão ou carregamento de pesos.
A captura não é uma transação de ativação entre memória e SQLite; mudanças
posteriores e alterações transitórias não observadas continuam possíveis.

Para um diagnóstico automatizado em **novo simulador isolado**:

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
python tools/validate_policy_approval.py --output-dir outputs/q05-approval-diagnostic-new
```

O rótulo `automated diagnostic fixture` identifica uma aprovação de teste,
sem representar uma decisão humana real. O diagnóstico modifica explicitamente
sua própria política entre observações para verificar recusa; isso não é efeito
da aprovação nem campanha científica.

A [API local de ativação](orion_q_policy_activation_q05.md) agora exige nova confirmação
e aprovação ainda selecionada na sessão, relê contexto/prazo, persiste auditoria
e permite observar a política ativa. JSON histórico sozinho continua sem autoridade.
Os campos `activation_available=false` e `activation_authority=false` dos recibos
de aprovação descrevem a ausência de autoridade de ativação no próprio recibo;
não substituem o contrato dessa solicitação separada. Ativação/readback já têm
interface nativa, assim como [recuperação/rollback](orion_q_policy_recovery_q05.md).
Q05 permanece parcial além do fluxo de limiar existente; modelos
opcionais seguem OFF e não há autoridade física.
[Validação local](../results/orion_q_policy_approval_20260928.md).
