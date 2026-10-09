# Q05 — observação contextual em simulação

Implementado em 29/09/2026, na API Python local. A observação compara uma entrada
simulada explícita com a política, supervisor e topologia persistida do runtime
existente. Não decide, prepara proposta, avalia uma ação ou altera a política.

## Uso e estados

```python
context = service.policy_compatibility_context()
observation = service.observe_policy_compatibility({
    "mode": "simulation",
    "candidate_id": "repeater.utility.v1",
    "expected_context_sha256": context["record"]["context_sha256"],
    "snapshot": state.to_dict(),  # NetworkStateSnapshot completo e explícito
})
```

A sessão é separada da preparação/aprovação Q05. Exige runtime já inicializado e
uma topologia `sandbox-line-3` persistida, sem criar banco, tabela ou estado. A
referência inclui identidade da sessão/runtime, configuração observada da política
e supervisor, revisão de identidade em memória e versão/hash da topologia.

A entrada tem todos os campos de `NetworkStateSnapshot`, no máximo 64 KiB de JSON,
sem NaN/Inf. Proveniência exige origem explícita, esquema suportado, metadados e
hashes em objetos; não recebe valores padrão silenciosos. Deve declarar simulação,
a topologia observada, `bell_fidelity_control`, `simulated_future_bell_fidelity` e
`physical_calibration=false`. A origem é fornecida pelo chamador e **não é autenticada**.
A flag de integridade declarada não prova aquisição ou integridade física.

A comparação verifica nó repetidor existente, versão do estado, memória, fidelidades
e idades dos pares e preservação da telemetria persistida. Entradas adicionais
continuam declarações do chamador; a previsão não é calibrada nem reconstruída.
Incerteza/OOD e idade são comparadas aos limites observados do supervisor. A idade
considera tempo transcorrido desde o timestamp mais idade de telemetria declarada;
horários futuros ou divergentes entre snapshot e proveniência são bloqueados.

- `OBSERVED_SUPPORTED_INPUT`: a entrada atende às verificações para o controlador
  existente, no instante da observação. Não afirma autorização, ganho científico,
  correspondência com uma política historicamente ativa ou viabilidade de cada ação.
- `BLOCKED`: inclui motivos estruturados para divergência de entrada, origem/domínio,
  idade, incerteza, OOD, integridade não declarada ou ativação ainda não resolvida.
- As onze opções adicionais continuam `BLOCKED` por ausência de tradução de domínio,
  ações e ciclo Q05. `target_input_checks_passed=true` apenas indica que a entrada
  atende ao alvo existente; não valida o domínio do componente candidato.
- Solicitação incompleta, esquema não suportado, hash incorreto, contexto ausente,
  expirado, alterado ou ilegível são recusados com erro, sem produzir sucesso parcial.

`operator_interlock=false` não impede observar a forma/compatibilidade da entrada;
a observação não chama o supervisor para autorizar uma ação. Os recibos carregam
`per_action_safety_assessed=false`, `selection_available=false`,
`preparation_available=false`, `activation_available=false`, zero decisões/comandos
e `physical_action_performed=false`. Identidade de pesos não é observada.

## Concorrência e validade

O contexto expira em 300 s de relógio monotônico, contados antes da inspeção. Uma
nova inspeção explícita permite nova observação. Troca de fonte, substituição de
runtime e invalidação do serviço descartam a sessão; recuperação Q05 também a
invalida. Mudança observada de objeto/configuração ou topologia recusa a referência.
Solicitação malformada não substitui o contexto válido nem uma proposta preparada.

A captura usa bloqueio de ciclo e conexão SQLite somente leitura, com snapshot de
leitura. Uma segunda captura detecta alterações de topologia confirmadas enquanto
a primeira leitura estava aberta. A idade da entrada é calculada após as leituras.
Isso não cria uma transação distribuída entre memória e SQLite nem uma reserva do
estado: alterações após a observação exigem nova verificação. Mudanças transitórias
não observadas não são provadas ausentes.

Recibos `orion.policy_compatibility_context.v1` e
`orion.policy_compatibility_observation.v1` contêm hashes do contexto e entrada,
declaração do candidato e motivos de recusa. Hashes são integridade local, sem
assinatura; recibos não são importáveis como autorização de preparação ou ativação.

## Diagnóstico reproduzível

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
python tools/validate_policy_context.py --output-dir outputs/q05-context-new
```

O diretório precisa ser novo. O diagnóstico cria apenas seu próprio simulador,
registra uma entrada aceita estruturalmente para o alvo, bloqueio do roteador e
recusa após invalidação da fonte. Confere que as consultas não alteraram o banco.
Não executa ciclos, carrega pesos ou utiliza runtime operacional.

## Interface nativa

Em **EDGE CONTROL → CONTEXT INSPECTION**, a fonte local precisa disponibilizar
runtime já inicializado. Abrir o painel não faz consulta ou inicializa banco.
Fontes sintéticas/somente leitura e serviço sem runtime não recebem a capacidade.

1. **LOAD CONTEXT** captura explicitamente a referência em sessão exclusiva deste
   painel, separada da API e de POLICY REVIEW. Propostas existentes são preservadas.
2. Escolha o candidato para inspeção e cole, na aba **INPUT JSON**, o snapshot
   simulado completo. Não há preenchimento automático de previsões, proveniência,
   horários ou flags; o painel nunca renova timestamps do texto recebido.
3. **OBSERVE INPUT** captura texto/candidato/hash antes do worker, recusa JSON com
   duplicatas/números não finitos ou mais de 64 KiB e usa as verificações desta API.
4. **CONTEXT / RESULT** mostra alvo, resultado, horário, idade efetiva, motivos e
   limites. **CAPTURED RECEIPT** apresenta o JSON completo. Um resultado BLOCKED
   também pode ser exportado para explicar uma recusa.
5. **EXPORT CAPTURED** grava exatamente a observação exibida em um arquivo novo,
   sem sobrescrever, reler contexto, decidir ou repetir a observação.

O seletor de candidato é para inspeção, não para ativação. Mesmo
OBSERVED_SUPPORTED_INPUT deixa seleção/ativação indisponíveis e não autoriza ações.
Nenhum modelo é carregado. O catálogo declarativo continua independente de runtime.

Consultas e gravações ocorrem em segundo plano, uma operação por vez, sem fila.
Edição de texto/candidato descarta a observação; durante um worker, também descarta
a referência e exige LOAD CONTEXT novamente. Mudança/parada de fonte, runtime,
caminho do banco, geração do serviço, versão observada da topologia, expiração ou
fechamento invalidam resultados. Fonte alterada/fechamento também limpam o texto.
O prazo gráfico começa antes de enviar LOAD CONTEXT ao worker, sem prolongar o
prazo backend. Mudança de conteúdo da topologia com mesma versão é conferida pela
API ao observar; não há leitura contínua de SQLite no timer da interface.

Cancelamento não aguarda leitura já iniciada e descarta retornos tardios. Contexto
nunca é reaproveitado após erro de consulta. Durante o diálogo de arquivo, seleção,
fonte e prazo são revalidados; qualquer mudança impede início da gravação. Uma
exportação já aceita pode terminar após fechamento/edição, conservando o recibo
capturado, sem restaurar o resultado no painel.

[Validação da API](../results/orion_q_policy_context_20260929.md) ·
[Validação nativa](../results/orion_q_policy_context_ui_20260929.md).

Próximo: contrato de tradução de ações para um primeiro componente opcional,
começando pelo subconjunto de ações dos roteadores que pode ser representado pelo
repetidor simulado. Recursos e precondições devem ser explícitos, e ações sem
correspondência devem ser recusadas antes de qualquer seleção. Modelos opcionais/OFF,
Q05 parcial, sem atuação física.
