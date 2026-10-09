# Q05 — critérios declarados de compatibilidade

Implementado em 28/09/2026; validação e consolidação concluídas em 29/09/2026.

O catálogo nativo e a API local descrevem por que cada componente opcional ainda
não pode substituir `repeater.utility.v1`. A consulta trata identidade, domínio de
entrada, semântica das ações, ciclo de ativação, evidência científica e aprovação.
Não verifica o contexto corrente: seu escopo é `DECLARED_CONTRACT_ONLY`.

## Consultar e exportar

No catálogo **EDGE CONTROL → OPTIONAL MODELS**, selecionar uma linha mostra o
alvo, domínio, saída e motivos de bloqueio no início dos detalhes. Isso continua
sendo inspeção. VERIFY LOCAL ZIP mantém sua verificação de pacote independente;
mesmo um ZIP verificado não altera o resultado de compatibilidade Q05.

A API Python local dispensa runtime, banco e pesos:

```python
catalog = service.policy_compatibility_catalog()
report = service.inspect_policy_compatibility("qnet.routing.nominal")
assert report["record"]["status"] == "BLOCKED"
assert report["record"]["selection_available"] is False
```

Para obter a mesma matriz sem criar um serviço:

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
python tools/inspect_policy_compatibility.py --output compatibility-new.json
python tools/inspect_policy_compatibility.py --candidate qnet.routing.nominal
```

O arquivo de saída precisa ser novo e seu diretório já deve existir. Não há
sobrescrita nem importação do recibo. Sem `--output`, a CLI imprime JSON.
IDs desconhecidos ou malformados são recusados; uma entrada nova no catálogo
sem declaração revisada não recebe compatibilidade por analogia de nome.
Não há rota HTTP, carregamento de modelo ou consulta automática a arquivos.

## Matriz para o alvo atual

O alvo único deste incremento é a política existente em simulação, com entrada
`NetworkStateSnapshot/1.1.0` e ações HALT, WAIT, PURIFY e SWAP. A matriz inclui sua
declaração e as onze opções do catálogo. Ela não cobre o controlador WDM separado
nem afirma avaliar todos os controladores possíveis do repositório.

| Candidato | Entrada/saída declarada | Impedimento para este alvo |
| --- | --- | --- |
| Política existente | Estado simulado do repetidor → intenção de controle | Exige contexto novo; apenas fluxo de limiar já implementado |
| Detector QKD | Classificação de anomalias | Export seguro e contrato tipado pendentes; pickle bloqueado |
| Ator QKD | Domínio decoy-state declarado → alterações de parâmetros | Não produz intenção do repetidor; não há tradução Q05 |
| Sincronizador | Histórico de atraso → previsão de offset | Atraso não é fidelidade Bell nem ação |
| Dois roteadores | Features state11/action23 → pontuação de candidatos | Recursos/semântica sem tradução Q05; inclui GENERATE, DELIVER e DISCARD |
| Quatro compensadores | SO(3) em regime declarado → rotação futura | Previsão sem interface de atuação ou intenção do repetidor |
| Dois alocadores | Planta fixa de onze enlaces → admissão/estimativa de orçamento | Sem mapeamento automático para topologia corrente ou alteração de fila |

Mesmo WAIT/PURIFY/SWAP nos roteadores não bastam: nomes coincidentes não traduzem
recursos, parâmetros, escopo ou precondições de segurança. O alocador de persistência
é uma referência não ML, mas também permanece um componente opcional incompatível
com substituição direta da política atual.

## Estados e critérios

- O controlador existente recebe `CONTEXT_REQUIRED`; isso não confirma que ele
  esteja ativo. Seu ciclo de mudança é `THRESHOLD_ONLY`.
- Todos os componentes opcionais recebem `BLOCKED`, com critérios bloqueadores
  explícitos: domínio de entrada, tradução de ações e ausência de adaptador Q05.
  Até 29/09 o detector também tinha `safe_export` bloqueado; com a exportação segura
  ([detector](../results/orion_q_detector_evaluation_20260929.md)) ele declara o contrato
  `orion.optional-detector-input.v1` e segue `BLOCKED` só pelos critérios comuns.
- Identidade é `NOT_OBSERVED`: esta consulta não confere bytes, instalação ou
  configuração. Um futuro uso deve conferir novamente pacote fixado/variante e
  vincular o objeto exato ao contexto; o resultado de VERIFY LOCAL ZIP é separado.
- Evidência científica é `NOT_ASSESSED`. A declaração não faz ranking, extrapola
  paridade offline nem elege vencedor da campanha SO(3).
- Aprovação é `NOT_REQUESTED`; relatório e hash não autorizam seleção ou ativação.

Os recibos `orion.policy_compatibility.v1` usam o ruleset
`orion.policy-compatibility-rules.v1`, horário de geração e SHA-256 do JSON
canônico. `generated_at_utc` não é horário de observação de runtime. O catálogo
`orion.policy_compatibility_catalog.v1` agrega recibos individuais. Hashes são
integridade local, sem assinatura. Alterar uma cópia retornada não muda consultas
posteriores; não são aceitos indicadores de compatibilidade fornecidos pelo chamador.

## Evolução em 29/09

A [observação contextual local](orion_q_policy_context_q05.md) foi implementada em
sessão separada para comparar uma entrada simulada explícita com runtime e topologia
persistidos. O catálogo declarativo deste documento não passa a observar runtime.
A inspeção contextual também está na interface com sessão privada e exportação
capturada. Próximo: contrato limitado de tradução de ações dos roteadores; os
adaptadores opcionais para seleção continuam ausentes.

## Aceite de seleção ainda pendente

Q05 segue parcial. A observação local já vincula contexto e entrada; seleção de
outros componentes ainda exige tradução de domínio e ações, vínculo de identidade
dos pesos e integração ao ciclo Q05. Entrada incompleta, alteração de contexto e
mapeamento ausente devem impedir preparação de troca. Declaração ou observação do
alvo existente não satisfazem sozinhas esse aceite.

Qualquer futuro adaptador selecionável ainda precisa integrar supervisor independente,
preparação, revisão, confirmação, ativação/readback, rollback e recuperação.
`set_decision_source` permanece bloqueado. Modelos continuam opcionais/OFF e não há
atuação física. O [fluxo de limiar existente](orion_q_policy_preparation_q05.md) e a
[explicação histórica](orion_q_policy_decisions_q05.md) permanecem separados.

[Validação deste incremento](../results/orion_q_policy_compatibility_20260929.md).
