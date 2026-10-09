# Ponte shadow de roteamento — 24/09/2026

Incremento Q16 em código-fonte para `qnet.routing.nominal` e
`qnet.routing.prazo_longo`. Recebe features e candidatos explícitos, verifica um
inventário simulado declarado e compara a decisão com uma referência opcional.
Não consome pares, não reserva memória, não chama executores e não altera o
journal de comandos. OFF é o padrão; nessa condição nem o snapshot nem os pesos
são lidos. A API é `evaluate_optional_routing(...)`, também acessível por
`ControllerRuntime.observe_optional_routing(...)`, fora de `cycle()`.

## Entrada e identidade

`NetworkStateSnapshot.telemetry.optional_routing_window` contém o esquema
`orion.sandbox-routing-window.v1`. `RoutingShadowWindow.from_dict()` valida e
congela a representação JSON; `to_dict()` devolve uma cópia independente.

| Campo | Contrato |
|---|---|
| `schema`, `evidence`, `feature_contract` | Esquema acima, `simulated` e `qnet.routing.state11.action23.v1`. |
| `source_id`, `node_id` | Fonte da proveniência e nó do snapshot; o nó também pertence ao inventário. |
| `campaign_id`, `clock_id`, `stream_id` | Coincidem com `metadata.campaign_id`, `clock_id`, `routing_stream_id`. |
| `origin_timestamp_utc` | Mesmo instante de `metadata.routing_origin_timestamp_utc`; origem dos segundos relativos enviados à sessão offline. |
| `decision_timestamp_utc` | Mesmo instante do timestamp do snapshot. |
| `features_timestamp_utc` | Igual à aquisição dos recursos, entre a origem e a decisão, com idade máxima de 1 s. |
| `seed` | Inteiro de 0 a 4294967295, usado pelo PCG64 da sessão offline. |
| `state_features` | As 11 features nomeadas, finitas, do contrato offline. |
| `candidates` | 1 a 64 alternativas ordenadas, com `action_id`, `action` e 23 features nomeadas. Flags one-hot coerentes; IDs e descrições não duplicados. |
| `reference_action_id` | ID de candidato fornecido, ou `null`. Referência externa; não há avaliação independente de uma heurística. |
| `task` | `endpoints`: dois nós distintos; `minimum_fidelity`: fração em [0,1]. |
| `resources` | Inventário e capacidades instantâneas descritos abaixo. |

As datas exigem fuso horário. A idade de 1 s é uma regra local de admissão deste
adaptador, no relógio do replay, não um prazo físico, certificação de frescor no
relógio de parede ou limite aprendido pelo modelo. A proveniência deve ser
simulada, com integridade declarada verificada e flag OOD falsa. Nenhuma feature
é derivada de WDM, SO(3), alocação ou dos campos genéricos de fidelidade do snapshot.

## Recursos e admissão instantânea

`resources` exige exatamente `ruleset`, `timestamp_utc`, `state_version`,
`enabled_kinds`, `nodes`, `links`, `pairs`. O ruleset é
`orion.routing-instantaneous-resources.v1`. Sua representação JSON canônica
(chaves ordenadas, UTF-8, sem espaços) tem SHA-256 igual a
`metadata.routing_resource_sha256`; `metadata.routing_ruleset` coincide com o
ruleset e a versão coincide com `snapshot.state_version`. O hash identifica o
conteúdo declarado; não autentica o produtor.

- `nodes`: 1–64 registros com `node_id`, `memory_capacity` (0–256),
  `reserved_slots` (0–capacidade), `available`, `can_swap`, `can_purify`.
  Reservas são slots adicionais, além dos ocupados pelos pares inventariados.
- `links`: 0–256 registros com `endpoints` e `available`. Enlaces não dirigidos,
  endpoints existentes e distintos, sem duplicatas invertidas.
- `pairs`: 0–128 registros com `pair_id` (inteiro de 0 a 2⁶³−1), `endpoints`,
  `fidelity` ([0,1]), `created_timestamp_utc`, `expires_timestamp_utc`, `reserved`.
  Criação até a aquisição; expiração posterior à criação. Pares expirados ou
  reservados continuam ocupando um slot em cada endpoint. Ocupação mais reservas
  não pode exceder a capacidade.
- `enabled_kinds`: subconjunto não vazio, sem repetição, dos seis tipos abaixo.
  Flags são booleanos estritos; nomes, campos adicionais e formatos inválidos
  recusam a janela antes de abrir o ZIP.

| Candidato | Condição adicional |
|---|---|
| WAIT | Tipo habilitado; não requer pares ou memória livre. |
| GENERATE | Enlace declarado disponível, dois endpoints disponíveis e pelo menos um slot livre em cada um. |
| PURIFY | Dois pares não reservados e não expirados, mesmos endpoints disponíveis e capacidade declarada de purificação em ambos. |
| SWAP | Dois pares não reservados e não expirados com exatamente um endpoint comum: o nó indicado, disponível e capaz de swap. Endpoints externos disponíveis. |
| DELIVER | Par não reservado, não expirado, endpoints da tarefa disponíveis e fidelidade declarada pelo menos igual ao mínimo. |
| DISCARD | Par existente, não reservado, endpoints disponíveis; pode estar expirado. |

Um único candidato inválido recusa **toda a janela**. Não se filtram, completam,
reordenam ou substituem candidatos. Alternativas podem compartilhar pares;
somente uma decisão é observada e nenhuma é executada. Expiração exatamente no
instante da decisão impede uso, exceto DISCARD. A ponte não simula a duração ou
a conclusão das operações, ancillas, ruído, decaimento, reservas distribuídas,
emissão completa de ações legais ou realimentação.

## Seleção, registro e limites científicos

Depois da admissão, o avaliador offline existente recebe as mesmas features,
candidatos, ordem, semente e referência. Nominal usa seleção estocástica T=1;
prazo longo usa seleção gulosa T=0. O registro preserva entrada, hashes do
snapshot/recursos/ZIP, versão NumPy, semente, logits, probabilidades, retorno do
crítico e decisão. Exportação abre um arquivo novo e recusa sobrescrita.

As features são declaradas pelo produtor: a ponte verifica formato e finitude,
mas **não reconstrói nem certifica sua normalização ou correspondência com os
recursos**. As regras são um contrato conservador do ORION, não reprodução da
legalidade completa do simulador do artigo. Fidelidade do inventário é um valor
simulado declarado. Probabilidade da política não é probabilidade de sucesso;
retorno do crítico não é recompensa medida. Concordância com a referência não
mede desempenho, ganho operacional ou resultado contrafactual.

## Uso

Na raiz registrada em [local_workspace.md](local_workspace.md), com `src` no
`PYTHONPATH`, é possível reproduzir uma observação explícita:

```powershell
python -m quantum_autopilot.control_plane.optional_routing `
  --input routing-snapshot.json --model qnet.routing.nominal `
  --qnet-zip F:\qnet_network.zip --output routing-shadow-new.json
```

Sem `--model`, registra OFF sem ler a entrada. Códigos de saída: 0 para OFF ou
avaliação; 2 para indisponibilidade ou recusa. O ZIP externo continua opcional,
fixado pela admissão existente e lido sem executar scripts do pacote.

O diagnóstico `tools/validate_routing_shadow.py` cria entradas artificiais
identificadas como tal e compara quatro avaliações com a sessão offline.
[Resultados e reprodução](../results/orion_q_routing_shadow_20260924.md).
A [emissão artificial e sessão gráfica shadow](../results/orion_q_routing_shadow_ui_20260924.md)
agora estão implementadas em fonte, conforme a seção seguinte. Nenhum executável
foi reconstruído, Q05 permanece pendente e não há atuação física.

## Sessão gráfica opt-in

No catálogo de EDGE CONTROL, selecione um dos dois roteadores, confira o ZIP com
VERIFY LOCAL ZIP e abra ROUTING · LIVE ARTIFICIAL SHADOW. A janela fica OFF até
START SHADOW. Exige a fonte artificial e seu campo `Snapshot.optional_routing_state`;
telemetria medida ou apenas dados de outras pontes não habilitam START.

`SyntheticRoutingStream` produz uma fixture separada de três nós, três enlaces e
quatro pares. Usa o primeiro nó do dashboard somente como identificador de origem;
não converte a topologia exibida em uma rede física. Cada publicação avança 1 s
virtual. Um ciclo de seis fases declara, independentemente das recomendações:
inventário livre (duas fases), enlace indisponível, par reservado, memória cheia
e par expirado. O produtor fornece respectivamente 6, 6, 5, 5, 5 e 4 alternativas;
a ponte continua recusando integralmente entradas com candidatos inválidos.

Features são ilustrativas, definidas no produtor, sem reconstrução da normalização
original: flags de ação, latência de demonstração, fidelidades declaradas dos pares
e um pequeno conjunto de features de estado; demais valores são zero. WAIT é uma
referência declarada, não uma heurística avaliada. A semente PCG64 de cada frame é
determinística em função da semente do produtor e do tick. Não há consumo de
recomendações, resultado de ações, desempenho medido ou realimentação.

Cada janela mantém um único worker sem fila de frames. Resultado de trabalho
anterior perde validade quando chega um novo frame. STOP, encerramento, troca de
fonte/pacote/contexto, entrada inválida ou ausência de nova entrada por 3,5 s
cancelam o uso do resultado. Duplicatas não renovam frescor; timestamps e versões
dos recursos precisam avançar. Mudança de tarefa ou topologia estrutural também
invalida o pacote da sessão, exigindo reabertura explícita pelo catálogo. O
inventário pode variar em disponibilidade, reservas e pares dentro do mesmo fluxo.

O catálogo mede frescor do roteamento separadamente e reconfere o payload antes
de abrir a janela. EXPORT FRAME captura o resultado antes do seletor de arquivo:
um frame posterior válido não substitui o capturado; perda de contexto cancela a
exportação. O arquivo destino precisa ser novo. Limites de API e evidência
científica descritos acima continuam aplicáveis.
