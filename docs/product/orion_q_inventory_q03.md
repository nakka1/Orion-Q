# ORION-Q — Q03: primeiro incremento do inventário persistente

Data: 12/09/2026. Alteração de código sobre a base v5.38.0. O executável distribuído anteriormente não foi reconstruído.

Atualização posterior no mesmo dia: a configuração de enlaces foi acrescentada no [segundo incremento de Q03](orion_q_links_q03.md). Este documento descreve o escopo do primeiro incremento.

## Entrega e uso

Na entrada de serviço local, abra **NODES**, informe ID, função ENDPOINT ou REPEATER, endereço IP e porta, e pressione **ADD TO SIMULATOR**.

O comando adiciona um nó isolado à topologia padrão `sandbox-line-3`. A tela mostra PENDING enquanto aguarda leitura do inventário persistido. Somente a confirmação por leitura produz CONFIRMED, limpa o formulário e apresenta o registro PERSISTED. O nó reaparece após reabrir o serviço com o mesmo banco SQLite e também integra o snapshot usado pelo grafo.

IP e porta são exclusivamente metadados: nenhum socket é aberto, protocolo de equipamento é executado ou credencial é enviada. Token, serial, bancos de memória e função CORE ficam desabilitados neste modo. O protocolo exibido é SIMULATOR; não há negociação QNET implementada.

A entrada artificial conserva o cadastro temporário local anterior, explicitamente SHADOW. A entrada medida continua somente leitura e não recebe a capacidade de edição. Nenhuma rota HTTP de escrita foi adicionada.

## Contrato e consistência

`AddSimulatedNode` aceita exatamente seis campos: `node_id`, `role`, `expected_version`, `address`, `port` e `topology_id`.

- ID: 1–64 caracteres ASCII, começando por letra ou dígito; aceita também ponto, hífen e sublinhado.
- Função: endpoint ou repeater; nó começa IDLE, sem pares armazenados.
- Endereço: IP literal, não URL ou nome de host; porta inteira de 1 a 65535.
- Versão: inteiro não negativo, obtido do campo tipado do snapshot, nunca de um rótulo formatado.
- Topologia: apenas o workspace padrão neste incremento; limite total de 32 nós.
- Campos extras, inclusive credenciais, são recusados.

Leitura, verificação da versão, inclusão do nó, incremento da versão e gravação do evento `simulated_node_added` ocorrem na mesma transação SQLite `BEGIN IMMEDIATE`. Escritores concorrentes que partem da mesma versão não sobrescrevem silenciosamente um ao outro. Duplicidade, versão antiga e limite excedido não modificam o estado. Falha na gravação do evento também desfaz a inclusão.

O evento registra a transição de versão, o nó, a função e a ausência de atuação física. IP e porta permanecem nos metadados do inventário; não são credenciais nem prova de conectividade.

Se a operação falhar, a interface informa UNCONFIRMED e orienta atualizar o inventário antes de repetir. Isso não afirma que houve rollback em falhas posteriores ao commit, como uma falha de leitura. A troca de fonte limpa o formulário e a solicitação pendente, sem reenviá-la.

## Preservação da topologia

O botão de cenário demonstrativo do serviço agora executa a demonstração em uma topologia separada, com identificador único `console-demo-*`. A inicialização do workspace padrão preserva seu conteúdo existente. Assim, executar a demonstração não apaga os nós cadastrados nem altera a versão do workspace editado.

Consequência: os efeitos desse cenário não são aplicados aos novos nós do inventário. O cenário ainda é o exemplo de linha existente. As execuções isoladas permanecem armazenadas; seleção e limpeza de projetos exigem uma etapa própria.

## Interface e verificação

A composição e a paleta da tela existente foram mantidas. Em janela estreita, a tabela tem largura mínima por coluna e rolagem horizontal; os valores têm tooltip completo, sem reduzir a fonte.

Os testes específicos em `tests/test_console_inventory.py` cobrem validação, limites, persistência após reabertura, concorrência entre instâncias, rollback por falha injetada no evento, formulário pendente/confirmado, conflito de versão, troca para fonte somente leitura, responsividade e preservação do workspace durante a demonstração.

Evidências da regressão ficam em `outputs/software_validation_inventory_q03/`, incluindo relatórios JUnit e capturas nativas da tela. São testes de software e do simulador, não medições de hardware nem nova evidência da hipótese preditiva WDM–desempenho quântico.

## O que ainda não foi entregue

Q03 está **parcialmente implementado**. Ainda faltam projetos nomeados, duplicação/importação/exportação de configuração, editor de enlaces e posições, remoção segura de nós persistentes e aplicação de redes personalizadas às campanhas. Não há serviço quântico fim a fim criado pelo ato de cadastrar um nó.

Próximo incremento recomendado: edição versionada de enlaces simulados, com validação de endpoints, parâmetros e conectividade, seguida da execução explicitamente vinculada à topologia escolhida. Q04 deverá generalizar o acompanhamento de comandos além da confirmação simples usada aqui.

## Verbos da rede ao vivo

Data: 04/10/2026. Acréscimo ao inventário para a rede ao vivo e a visão GEO; o contrato entre os módulos está em [`orion_q_live_network_design.md`](orion_q_live_network_design.md), §1. Os três verbos anteriores (`add_simulated_node`, `configure_simulated_link`, `apply_simulated_node_action`) continuam funcionando. Nenhuma rota HTTP foi adicionada e nenhum equipamento é acionado: tudo é configuração do simulador, com `physical_action_performed: False`.

Os dataclasses `SimulatedNodeState` e `SimulatedLink` não ganharam campos. Os dados novos ficam em `telemetry["location"]` e em `telemetry["console_control"]` do nó, de modo que topologias gravadas antes deste incremento continuam carregando, com os mesmos bytes e o mesmo hash, e continuam reconciliáveis.

### Contratos

Todos em `control_plane/inventory.py`, com o mesmo rigor dos anteriores: conjunto exato de campos (campo a mais ou a menos é recusado, inclusive `topology_id` e coordenadas nulas, que precisam ser enviados), IDs ASCII de 1–64 caracteres, `expected_version` inteiro não negativo e apenas o workspace padrão `sandbox-line-3`.

| Verbo | Contrato e campos | Evento |
|---|---|---|
| `add_network_node` | `AddNetworkNode`: `node_id`, `role`, `address`, `port`, `latitude`, `longitude`, `place`, `expected_version`, `topology_id` | `network_node_added` |
| `set_node_location` | `SetNodeLocation`: `node_id`, `latitude`, `longitude`, `place`, `expected_version`, `topology_id` | `node_location_set` |
| `set_node_isolation` | `SetNodeIsolation`: `node_id`, `isolated`, `expected_version`, `topology_id` | `node_isolated` / `node_restored` |
| `set_link_active` | `SetLinkActive`: `link_id`, `active`, `expected_version`, `topology_id` | `link_activated` / `link_deactivated` |
| `recalibrate_node` | `RecalibrateNode`: `node_id`, `expected_version`, `topology_id` | `node_recalibrated` |

- Localização: `latitude` e `longitude` são ambos `null` (nó sem localização, fica na fila) ou ambos números finitos em graus decimais, −90..90 e −180..180. Inteiros são aceitos e gravados como `float`; `bool` e texto são recusados.
- `place`: rótulo livre de até 80 caracteres imprimíveis (aceita acentos; não aceita quebra de linha). Pode ser vazio. Sem coordenadas, **precisa** ser vazio: não há onde guardar um rótulo sem posição.
- Função: endpoint ou repeater, como antes. O papel visual CORE não é gravado por estes verbos.
- A localização é uma declaração do operador (`source: "operator_declared"`), com o instante em `set_at_utc`. Nada é geocodificado, resolvido ou medido.

Cada verbo é uma única transação SQLite `BEGIN IMMEDIATE`: leitura, verificação de versão, alteração, **um** incremento de `topology_version` e **um** evento em `topology_events`. O payload de todo evento traz `scope: "simulator_only"`, `physical_action_performed: False` e o antes/depois (`before`/`after`). Versão antiga, alvo inexistente e operação sem efeito são recusados com `ValueError` e não alteram nem a topologia nem a trilha de eventos; falha na gravação do evento desfaz a alteração inteira.

### Localização e distância

`control_plane/geodesy.py` (somente biblioteca padrão) fornece `geodesic_km(lat1, lon1, lat2, lon2)`: fórmula inversa de Vincenty no elipsoide WGS-84, com queda para haversine (esfera de raio médio) quando a iteração não converge, o que só acontece para pontos quase antipodais.

Quando **os dois** extremos de uma fibra têm localização, `distance_km` é a distância geodésica, gravada com resolução de milímetro. Isso vale em dois momentos:

- em `set_node_location`, na mesma transação: toda fibra com os dois extremos localizados é recalculada e listada em `recomputed_links` (`link_id`, `distance_km_before`, `distance_km_after`);
- em `configure_simulated_link`: entre dois nós localizados o valor digitado é ignorado e o evento registra `requested_distance_km` e `distance_source: "geodesic_from_node_locations"`. A interface não calcula distância.

Se um extremo não tem localização, vale o valor configurado pelo operador no editor de fibras.

`set_node_location` altera apenas `telemetry["location"]` do nó. Memória, pares armazenados, metadados de inventário, estado de isolamento, calibração e todas as fibras (extremos, estado ativo, fidelidade nominal) são preservados. Enviar `null`/`null` devolve o nó à fila; repetir a mesma localização ou limpar um nó sem localização é recusado.

Limite: ao remover a localização, a fibra **mantém o último comprimento** (o geodésico). O valor que o operador havia digitado antes do posicionamento não é guardado; se for o caso, ele precisa ser informado de novo no editor de fibras.

### Isolamento e restauração

`set_node_isolation` com `isolated: true`:

- desliga as fibras incidentes que estão **ativas naquele momento** e guarda seus IDs em `console_control.isolation_disabled_links`;
- marca `console_control.isolated = true` e zera a memória do nó (IDLE, sem pares), como a ação antiga;
- o evento `node_isolated` lista `disabled_links`.

Com `isolated: false`, a restauração religa **somente o que o isolamento desligou**: cada fibra registrada que ainda existe e ainda está desligada. A lista é esvaziada e `isolated` volta a `false`. O evento `node_restored` traz `restored_links`, `deferred_links` e `skipped_links`. A memória descartada não volta.

Casos que foram decididos explicitamente:

- **Fibra desligada pelo operador antes do isolamento** não entra na lista e continua desligada depois da restauração.
- **Dois vizinhos isolados.** Se o outro extremo de uma fibra registrada também está isolado, ela não é religada: o registro passa para a lista do vizinho (`deferred_links: [{link_id, held_by}]`) e a fibra volta quando ele for restaurado. Em qualquer ordem de restauração a fibra compartilhada é religada exatamente uma vez, e nunca enquanto um dos extremos estiver isolado.
- **Fibra registrada que deixou de existir ou já está ligada** é ignorada e aparece em `skipped_links`.
- **Isolamento feito pelo verbo antigo** (`apply_simulated_node_action` com `isolate`) agora também grava a lista, então pode ser desfeito por `set_node_isolation`.
- **Isolamento gravado antes deste incremento** não tem lista: restaurar limpa a marca e não religa nada; as fibras são ligadas uma a uma com `set_link_active`.
- **Religação manual pelo editor de fibras** (`configure_simulated_link` com `active: true` em fibra de nó isolado) mantém o comportamento antigo: encerra o isolamento dos extremos sem restaurar as demais fibras. A lista do nó é esvaziada.
- Isolar um nó já isolado, ou restaurar um nó que não está isolado, é recusado.

### Ativar e desativar uma fibra

`set_link_active` muda apenas o campo `active` de uma fibra. Não altera nós, marcas de isolamento nem a lista de restauração.

- **Ativar** é recusado enquanto qualquer extremo estiver isolado; a mensagem pede para restaurar o nó primeiro.
- **Desativar** é sempre permitido. Uma fibra que o operador desativa por conta própria não passa a pertencer a nenhum isolamento.
- Pedir o estado que a fibra já tem é recusado.

Disponibilidade efetiva de uma fibra (ativa, sem extremo isolado ou em recalibração, sem alarme retido) é regra do motor de rede, não deste inventário.

### Recalibração

`recalibrate_node` é executada pelo motor de rede. O inventário só persiste o resultado, em `SimulatedTopologyStore.record_node_calibration(request, report)`:

- `report` precisa ser um objeto JSON não vazio, com chaves de texto e números finitos, de no máximo 16 KiB em JSON canônico. O formato interno é responsabilidade do motor;
- o relatório é gravado em `console_control.calibration`, acrescido de `report_sha256` (hash do relatório canônico). Se o motor já enviar esse campo, ele precisa coincidir;
- `calibration_count` é incrementado, `calibrated_at_utc` é atualizado e a memória do nó é zerada;
- o evento `node_recalibrated` leva `report_sha256`, `previous_report_sha256` e `calibration_count`.

Recalibrar não religa fibra nem desfaz isolamento. O incremento de versão acontece na gravação do relatório: se a topologia mudar entre o pedido e o fim da recalibração, a gravação é recusada por conflito de versão e nada é persistido.

### Acompanhamento e reconciliação

Os cinco verbos estão em `CONTRACTS` (`console_commands.py`) e seguem o mesmo ciclo REQUESTED → RUNNING → CONFIRMED/UNKNOWN. Como cada um incrementa a versão exatamente uma vez, a confirmação continua exigindo `topology_version == expected_version + 1`.

A reconciliação somente leitura (`command_reconciliation.py`) compara:

| Verbo | Campos esperados × observados |
|---|---|
| `add_network_node` | `node_id`, `role`, metadados de inventário e localização declarada |
| `set_node_location` | `node_id` e localização declarada (latitude, longitude, rótulo; o instante não entra) |
| `set_node_isolation` | `node_id` e `isolated` |
| `set_link_active` | `link_id` e `active` |
| `recalibrate_node` | `node_id` e `last_command_sha256` |
| `configure_simulated_link` | a fibra pedida; entre dois nós localizados, com a distância geodésica |

Como antes, o resultado é uma observação do estado atual: nunca prova que o comando foi executado e nunca altera o registro original.

### Serviço e capacidades

`AssuranceDashboardService` ganhou `add_network_node`, `set_node_location`, `set_node_isolation`, `set_link_active` e `record_node_calibration(payload, report)`, com o mesmo retorno dos repasses anteriores (`status: "persisted"`, `physical_action_performed: False`, `topology`).

O catálogo `OPERATIONS` declara os verbos novos da rede ao vivo. Declarar um verbo no catálogo não o concede a nenhuma fonte: a entrada artificial e a entrada medida não receberam nenhuma capacidade nova.

### Verificação e limites

`tests/test_network_inventory.py` cobre a geodésia contra fórmulas independentes (arco de meridiano por integração numérica, fórmula de Lambert e o exemplo publicado de Vincenty), os contratos, cada método do store (caminho normal, conflito de versão, recusa de operação sem efeito, conteúdo dos eventos, rollback por falha injetada, concorrência), o ciclo de comandos, a reconciliação e a leitura de uma topologia gravada pelo código anterior. São testes de software e do simulador, não medições de hardware.

O que continua fora: remoção de nós e de fibras, projetos nomeados, importação/exportação, mais de 32 nós ou 256 fibras, e qualquer workspace além do padrão. A distância geodésica é a distância sobre o elipsoide entre dois pontos declarados; não é o comprimento medido de uma fibra instalada, que segue o traçado real e é sempre maior.
