# ORION-Q — primeira entrega do plano: capacidades das fontes

Data: 10/09/2026. Incremento de código sobre a base v5.38.0; não é um novo executável distribuído.

## Implementado

- Contrato local imutável `orion.capabilities.v1`, com identidade/versão do adaptador, modo, saúde, operações e limites declarados.
- Fontes sem declaração são bloqueadas por padrão. Metadados do payload não concedem operações: o plug usa a declaração do adaptador local.
- Fontes artificial, JSON medido e serviço local publicam capacidades pelo mesmo contrato `Snapshot`.
- Interface usa IDs de operação, não textos de botões ou presença de métricas, para habilitar comandos.
- Fronteiras de dispatch também verificam capacidades; o serviço revalida antes de executar o comando agendado.
- Consulta das capacidades no menu **Data Source → Input capabilities / execution scope** e tooltip da fonte.
- Troca de entrada mantém a proteção por geração e limpa o estado visual anterior. Plug parado recusa comandos; plug ocupado só permite cancelamento quando suportado.
- Fim de processamento não reativa indiscriminadamente botões. Flash, isolamento físico e seleção de política não implementada permanecem bloqueados.

## Operações honestamente disponíveis

| Fonte | Capacidades | Limites da declaração |
|---|---|---|
| Artificial | Controle do stream, injeção de ausência de dados, cenário isolado do controlador, exportação, anotações e staging local de nós | Os limites de 32 nós/256 enlaces são de apresentação, não capacidade de uma rede física ou de um simulador geral |
| JSON medido | Recarregar, exportar e selecionar registros válidos | Somente leitura; entrada ausente/inválida retira seleção de registros; validade temporal continua verificada pelo adaptador |
| Serviço local | Operações simuladas conforme saúde e disponibilidade declaradas pelo backend; replay apenas com modelo configurado, válido e replay disponível | Não controla equipamento; leitura de métricas não prova política ativa |
| Adaptador não declarado/parado | Nenhum comando | Deve implementar e publicar capacidades para integrar-se ao console |

### Atualização de 04/10/2026 — rede ao vivo e modo de demonstração

A tabela acima passa a ter duas linhas para o serviço local e a fonte artificial deixa de ser a demonstração:

| Fonte | Adaptador / modo | Operações acrescentadas | Observação |
|---|---|---|---|
| Serviço local (operação normal) | `assurance-local-service` · `simulation` | `read_network_live`, `add_network_node`, `set_node_location`, `set_node_isolation`, `set_link_active`, `recalibrate_node`, `define_pair`, `remove_pair`, `request_pair`, `purify_pair`, `consume_pair`, `set_model_archive` | Fonte de entrada do motor: nenhuma, salvo configuração. Mostra configuração e roteamento; campos medidos ficam ausentes ("—"). Rótulo do operador: `LOCAL SANDBOX`. |
| Demonstração | `assurance-demo-service` · `simulation` | as mesmas, mais `inject_input_event` | "simulated input · real pipeline": mesmo serviço, mesmo motor e mesmo provedor; só a origem dos dados de entrada muda (`SimulatedInputSource` do backend) e o estado fica em `<state-root>/demo/`. Rótulo do operador: `DEMONSTRATION`; `Snapshot.input_kind = "demo"`. |
| Artificial (`--synthetic`) | `synthetic-stream` · `synthetic` | nenhuma | Acessório de desenvolvimento para estressar a interface; os valores são gerados no lado da interface. Não é a demonstração e não aparece na tela de login nem na paleta. |

- Os cinco verbos de topologia passam pelo ciclo rastreado `REQUESTED → RUNNING → CONFIRMED | UNKNOWN` com releitura da topologia persistida; `expected_version` e `topology_id` são preenchidos a partir do snapshot quando o pedido não os traz.
- Os verbos do motor são executados por `service.network_command` e o efeito é lido nos eventos e no estado publicados pelo próprio motor.
- `inject_input_event` só é declarado na demonstração e é recusado nas duas fronteiras (capacidades e provedor) fora dela: ele altera a ENTRADA simulada de uma fibra, nunca um resultado.
- `configure_network_input` existe no backend, mas não é declarado por nenhum adaptador do console: a fonte de entrada é escolhida pela seleção de fonte na tela de login.
- Recusas do backend chegam à interface pelo sinal de erro existente, com a mensagem do backend.
- O modo continua `simulation` nos dois casos: nenhuma operação física é declarada, e todo registro carrega `physical_action_performed: False`.

O staging de nós continua sendo uma edição local temporária, explicitamente separada de provisionamento. A seleção ML/determinístico foi bloqueada até Q05: antes, ela registrava uma escolha sem alterar a política. A fonte artificial também não anuncia mais replay de modelo sem implementação.

## Verificação

Testes adicionais cobrem declarações inválidas, recusa de operações físicas, adaptador não declarado, tentativa de conceder capacidades no payload, exportação medida, falta de modelo, indisponibilidade do serviço, troca de fonte, plug ocupado/parado e bloqueio de botões após atualização.

Resultados da execução local são registrados em `outputs/software_validation_capabilities_q01/`. A validação de renderização é offscreen: não é ensaio em hardware nem aceite visual humano. Nenhum comando físico ou job IBM é necessário.

Resultado final desta entrega:

- 96 testes aprovados na rodada conjunta de capacidades, fontes, telas, integração e fronteira física, sem falhas ou erros (`accepted-tests.xml`). Não é a suíte completa do repositório.
- Autoteste da aplicação em código-fonte aprovado: 19 verificações principais, incluindo uma matriz com 68 verificações de responsividade e entrada (`self-test.json`).
- Captura de Edge Control revisada: seleção de política e flash indisponíveis, composição visual preservada.
- O executável anteriormente distribuído não foi recompilado; as verificações desta entrega não são testes de um novo binário congelado.

## Fronteira e continuidade

Esta declaração é uma lista de capacidades do adaptador confiável local, não autenticação, autorização de usuário, certificação de dispositivo ou interlock físico. Também não resolve o contrato numérico/temporal completo de Q02.

Próximas entregas do [backlog](orion_q_functional_backlog_20260910.md):

1. Q02 mínimo: observações numéricas, unidades e identidade temporal para integrações seguintes.
2. Q03: projeto/topologia persistentes, ligados ao backend.
3. Q04: comandos rastreáveis com estado confirmado e recuperação.
4. Q05: escolha e leitura da política efetivamente ativa.

O documento de varredura é mantido como retrato da base anterior; esta nota registra a evolução sem reescrever os achados históricos. Não foram modificados modelos, dados científicos, resultados confirmatórios ou proteções físicas.
