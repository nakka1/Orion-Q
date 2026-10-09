# Q05 — preparação e revisão local de política

Data: 27/09/2026. Preparação/revisão via API local e interface nativa, somente em simulação.

## Escopo

A API Python local prepara uma alteração do **limiar da política de utilidade do
repetidor existente** (`repeater.utility.v1`). Limiar finito entre 0 e 1; sem
alteração efetiva da política. Propostas sem mudança de limiar são recusadas.
Todos os modelos Q16 permanecem opcionais e OFF: `optional_model_id` deve ser
`None`. Não há seleção ML, carregamento de pesos, decisão, ciclo ou executor.

O vínculo é com `AssuranceDashboardService.controller_runtime`, já inicializado,
e com a topologia padrão persistida `sandbox-line-3`. O runtime separado do
bundle WDM não é selecionado, descrito como ativo ou alterado por esta API.
Preparar/revisar não cria o runtime, a topologia ou tabelas de propostas.
O painel nativo descrito abaixo usa essa mesma captura/validação. Não há rota HTTP ou ativação.

## Uso no serviço local já inicializado

Use a instância de serviço que possui o runtime da simulação, não uma instância
nova criada apenas para reconstruir uma proposta de outro processo:

```python
context = service.policy_preparation_context()
proposal = service.prepare_policy_change({
    "mode": "simulation",
    "policy_id": "repeater.utility.v1",
    "threshold": 0.70,
    "optional_model_id": None,
    "expected_context_sha256": context["record"]["context_sha256"],
})
selection = proposal["record"]["proposal_id"], proposal["record_sha256"]
review = service.review_policy_change(*selection)
service.export_policy_review(*selection, "policy-review-new.json")
```

O limiar `0.70` é um exemplo de configuração, não um valor recomendado ou
calibrado. A referência continua no limiar observado, sem qualquer troca.
A exportação faz **nova revisão** e grava seu resultado em um arquivo novo;
não sobrescreve destinos nem importa uma proposta exportada.

Para verificar o fluxo em um inventário temporário independente:

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
python tools/validate_policy_preparation.py --output-dir outputs/q05-diagnostic-new
```

O diretório deve ser novo. Esse diagnóstico inicializa seu próprio simulador e
altera sua topologia entre observações, para testar a invalidação; não é uma
campanha científica e não altera o inventário da console.

## Interface nativa

Na fonte de serviço local, com o runtime da simulação já inicializado, abra
**EDGE CONTROL → POLICY REVIEW**. Abrir o painel não inicia leitura ou preparação.
A capacidade fica indisponível para fontes sintéticas, somente leitura ou serviço
sem runtime; o painel não inicializa runtime nem inventário.

1. **LOAD BASELINE** observa a política existente e sua topologia persistida.
2. Digite um limiar diferente, finito, entre 0 e 1, usando ponto decimal.
   **PREPARE** valida o contexto e guarda a proposta; não sugere um valor.
3. **REVIEW** relê o contexto e mostra CURRENT ou STALE com horário e motivos.
4. **EXPORT REVIEW** grava o JSON da revisão exibida em um arquivo novo.

A aba BASELINE / PROPOSAL compara os limiares; CAPTURED RECEIPT permite examinar
configurações, hashes e limites completos. CURRENT descreve o instante da revisão,
sem monitoramento contínuo da política. Use REVIEW para uma nova observação.
O painel tem uma sessão própria, separada da proposta criada pela API do serviço.

Todas as operações de captura e arquivo ocorrem em segundo plano, uma por vez,
sem fila. Edição descarta proposta/revisão; edição durante uma operação exige nova
referência. Troca/parada de fonte, substituição do runtime, mudança observada de
versão da topologia, expiração e fechamento descartam a seleção e seus resultados
tardios. O prazo local de 300 s começa antes do envio da preparação ao worker,
portanto pode encerrar antes do prazo interno da proposta. Reabrir exige nova leitura.
O cancelamento gráfico não espera uma leitura SQLite já iniciada terminar.

Ao contrário da exportação da API, **EXPORT REVIEW não faz nova revisão**: grava
exatamente o recibo capturado, inclusive STALE, sem sobrescrever. Mudança de seleção
ou fonte durante o seletor de arquivo impede a gravação. Uma gravação já aceita
pode terminar após o fechamento, preservando o recibo original; o resultado tardio
não restaura a seleção. Arquivo exportado é evidência histórica, sem importação ou
capacidade de autorizar uma troca. Os seletores ML/determinístico seguem bloqueados.

[Validação da interface](../results/orion_q_policy_review_ui_20260927.md).

## Conteúdo e estados

O contexto inclui identidade da sessão, política observada, limiar, custos de
energia/risco, tempos usados pelo controlador de risco, supervisor e versão/hash
da topologia. Os custos continuam estimativas do controlador existente.
A proposta inclui UUID, horários UTC, contexto e hash, configuração de referência,
configuração proposta, seus hashes e diferença explícita de limiar.

O runtime ainda não tem uma versão persistente de política. Por isso,
`policy_observation_revision` conta **mudanças observadas dentro desta sessão**,
incluindo substituição do objeto por outro com parâmetros iguais. Não é uma
versão persistente, assinatura, histórico completo ou prova contra alterações
locais arbitrárias. Mudanças transitórias entre observações podem não ser vistas.

- `PREPARED`: proposta validada e retida como a única seleção atual na memória.
- `CURRENT`: revisão encontrou o mesmo contexto e a proposta ainda não expirou.
  É observação de compatibilidade, não aprovação humana ou autorização.
- `STALE`: revisão observou mudança, indisponibilidade ou expiração. A invalidação
  é definitiva para esta proposta, mesmo se o contexto voltar ao estado anterior.

A proposta expira após **300 segundos de relógio monotônico**, com limite final
excluído. O horário UTC de expiração é descritivo; ajustes do relógio civil não
renovam o prazo. Nova preparação válida substitui a seleção anterior. Uma
solicitação inválida não substitui a seleção. Reinício do processo, nova fonte ou
invalidação da sessão exigem nova preparação; IDs/hashes antigos são recusados.
Parar/trocar o provider de serviço invalida sua sessão de preparação.

## Fronteiras

A leitura usa o bloqueio de ciclo do runtime e uma conexão SQLite somente leitura
para a topologia. Uma verificação posterior recusa alteração detectada da política
durante a captura. Isso pressupõe o runtime local confiável; não cria uma transação
atômica entre memória e SQLite para futura ativação. Mudanças após uma revisão
exigem nova leitura e futuros mecanismos próprios de ativação.

Relatórios carregam `activation_available=false`, `operator_approval_recorded=false`,
`policy_activated=false`, `physical_action_performed=false` e zero comandos.
`set_decision_source` continua recusando troca. Exportações são recibos históricos
com hashes locais, não tokens de autorização. Não há rollback ou reexecução.

Em 28/09, a [API local de aprovação](orion_q_policy_approval_q05.md) passou a
registrar declaração explícita vinculada à última revisão, com nova captura de
contexto e sem renovar o prazo. Isso não muda o significado dos recibos de revisão.
O painel nativo também oferece APPROVE PROPOSAL, confirmação explícita e EXPORT
APPROVAL conforme esse contrato. A [ativação transacional em simulação](orion_q_policy_activation_q05.md)
agora tem API própria com auditoria persistente e leitura atual. Os recibos de
preparação/revisão continuam sem autoridade de ativação. Ativação/readback já têm
interface nativa, assim como [recuperação/rollback](orion_q_policy_recovery_q05.md).
Q05 permanece parcial além do fluxo de limiar da política existente.
[Validação](../results/orion_q_policy_preparation_20260927.md).
