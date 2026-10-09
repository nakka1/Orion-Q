# ORION-Q — Varredura técnica e backlog de funcionalidades

Data: 10/09/2026. Base inspecionada: repositório local v5.38.0, incluindo modificações presentes no diretório de trabalho. Este documento é uma proposta; não atesta implementação, interoperabilidade ou operação física das funcionalidades abaixo.

**Atualização de escopo em 07/10/2026:** a integração IBM Quantum e o Q25 foram
cancelados. Seus módulos e roteiros ficam no [arquivo histórico](../../archives/retired_ibm_quantum_20261007/ibm_quantum_source.zip),
sem disponibilidade no produto atual. Qiskit/Aer e os protocolos simulados locais
continuam no escopo; Q26 não depende de acesso a QPU IBM.

## 1. Recomendação executiva

Evoluir o ORION-Q para uma **bancada local de experimentação e supervisão de redes quânticas**: criar cenários, solicitar serviços entre nós, comparar estratégias de controle, reproduzir incidentes e exportar evidências verificáveis. Posteriormente, conectar equipamentos específicos por adaptadores.

O maior ganho imediato não está em acrescentar indicadores à tela. Está em completar o fluxo:

**Solicitação de serviço → admissão e reserva → execução → entrega ou falha → avaliação e evidência.**

Esse produto pode ser útil sem laboratório: treinamento, avaliação de algoritmos, teste de integração e planejamento exploratório. Não deve ser apresentado como controlador universal de equipamentos, instrumento de certificação ou validação experimental da hipótese WDM→fidelidade.

Os princípios arquiteturais da Internet quântica tratam distribuição de emaranhamento, recursos quânticos e sinalização clássica como componentes relacionados. A RFC 9340 é uma referência informativa da IRTF, não uma norma obrigatória de interoperabilidade. Ela sustenta a direção arquitetural, não prova a eficácia do ORION-Q. [RFC 9340](https://www.rfc-editor.org/rfc/rfc9340.html)

## 2. O que existe e o que ainda é uma lacuna

| Área | Evidência no repositório | Consequência para o backlog |
|---|---|---|
| Interface | Console nativo com Topology, Telemetry, Key Management, Nodes, Edge Control e Access & Audit; GEO/Matrix e fontes intercambiáveis | Preservar a identidade visual; integrar capacidades nos painéis existentes |
| Entrada de dados | Fonte sintética, serviço local e contrato JSON medido, com validação de origem, limites e frescor | Evoluir para observações numéricas, histórico e adaptadores; não recriar o plug |
| Inventário de nós | A inclusão pela tela acrescenta um registro em memória, explicitamente shadow; não registra um dispositivo nem altera a topologia do controlador | Implementar persistência e aplicação da topologia ao backend |
| Escolha de controlador | A seleção ML/determinístico gera registro, mas o próprio código informa que não está vinculada à política do runtime | Conectar seleção, confirmação e leitura da política efetivamente ativa |
| Indicadores de controle | Parte da contagem de ações é extraída de texto do ledger; disponibilidade de modelo é inferida de métricas da tela | Introduzir eventos e capacidades explícitos, sem interpretar rótulos como estado operacional |
| Simulador/controlador | Contratos, avaliação de segurança, ações simuladas, idempotência e topologia persistida; cenário de referência com repetidor e dois pares | Ampliar execução para serviços concorrentes, recursos identificados e evolução temporal |
| Modelos e ciência | Inferência WDM, famílias Edge, múltiplas saídas, calibração, campanhas e comparações já existem em módulos/scripts | Integrar e reutilizar; não duplicar treinamento e avaliação na GUI |
| IBM (histórico) | A integração anteriormente proposta foi removida em 07/10/2026 | Q25 cancelado; não criar painel, diagnóstico ou execução IBM |
| Hardware | O módulo físico é uma estrutura de recusa; não implementa comandos de equipamento | Não ativar controle físico por configuração ou botão |

Rastreabilidade local: [roadmap](roadmap.md), [console nativo](native_console.md), [fontes de entrada](../../src/quantum_autopilot/console/input_sources.py), [provider de serviço](../../src/quantum_autopilot/console/service_provider.py), [cadastro de nós](../../src/quantum_autopilot/console/screens/node.py), [Edge Control](../../src/quantum_autopilot/console/screens/fpga.py), [runtime](../../src/quantum_autopilot/control_plane/runtime.py), [topologia](../../src/quantum_autopilot/control_plane/topology.py), [fronteira física](../../src/quantum_autopilot/control_plane/physical.py), [arquivo histórico IBM](../../archives/retired_ibm_quantum_20261007/ibm_quantum_source.zip).

Esta varredura combina inspeção estática de módulos e documentos de resultados com fontes externas primárias. Não é uma nova certificação de segurança, revisão exaustiva de toda a literatura ou execução de hardware.

## 3. Regras de produto que devem valer para todas as funcionalidades

1. **Mesmo contrato de observação, permissões distintas.** Dados sintéticos, replay e dados medidos alimentam os mesmos componentes. Trocar a entrada não concede autorização de atuação.
2. **Proveniência por observação.** Diferenciar medido, simulado, derivado e previsto. Um painel pode conter várias origens; um selo global é insuficiente.
3. **Tempo explícito.** Separar instante de aquisição, recebimento, intervalo de integração, relógio da simulação e horário de apresentação. Replay não transforma um registro antigo em medição ao vivo.
4. **Fidelidade não é um campo universal.** Registrar estado de referência, definição, método, incerteza e contexto. QBER, SKR e BER não devem preencher fidelidade de Bell ausente.
5. **O backend confirma o estado.** Clique, aceite e execução são eventos diferentes. Falta de resposta pode significar resultado desconhecido, não necessariamente falha sem efeito.
6. **O desktop supervisiona.** Temporização de fótons, portas e proteção física devem ficar no dispositivo/controlador adequado, não no loop gráfico.
7. **Nenhum resultado é promovido por aparência.** Animação de swap, estimativa end-to-end ou resultado de simulador continua identificado como tal.

## 4. Backlog priorizado — 30 funcionalidades

Prioridades: **P0** fecha lacunas da aplicação atual; **P1** entrega o núcleo operacional do sandbox; **P2** amplia pesquisa e uso cotidiano; **P3** depende de integração especializada ou equipamento. Esforço relativo: **M** médio, **G** grande, **GG** muito grande. Não são estimativas de prazo. Dependências são os IDs Qxx.

### P0 — Tornar a interface uma representação fiel do backend

#### Q01 — Registro de capacidades e conexão

- **Funcionamento:** cada adaptador informa identidade, versão, origem, saúde, operações permitidas, parâmetros, limites e modo de execução. A tela mostra desconectado, somente leitura, simulação ou dispositivo autorizado.
- **Aceite:** um adaptador que só publica telemetria jamais habilita PURIFY/SWAP; dados no painel não são usados como prova de modelo carregado. Mudança de fonte invalida seleções e comandos pendentes incompatíveis.
- **Base/esforço:** ampliar os providers existentes; M. Sem dependência de hardware.

#### Q02 — Modelo de observações e qualidade temporal

- **Funcionamento:** manter números, unidades, IDs de canal/dispositivo/campanha, timestamps, intervalo de medição, incerteza e origem no domínio; formatar strings somente na apresentação. Identificar atrasos, lacunas, duplicatas e registros fora de ordem.
- **Aceite:** QBER em fração não pode ser confundido com porcentagem; NaN não vira zero; junções entre canais ou relógios incompatíveis são recusadas. Campos ausentes permanecem ausentes.
- **Base/esforço:** contrato JSON e snapshots existentes; G. Depende de Q01 para negociação de esquema.

#### Q03 — Projetos, inventário e editor de topologia persistentes

- **Terceiro incremento em 12/09/2026:** execução condicional de um caminho endpoint–repetidor–endpoint sobre versão congelada, usando o executor SWAP existente e registro independente. Não inclui tempo, perdas ou serviço físico. [Execução vinculada ao workspace](orion_q_workspace_path.md).
- **Situação em 12/09/2026:** primeiro incremento implementado: inclusão versionada de endpoints/repetidores na topologia padrão, persistência SQLite, confirmação por leitura na tela NODES e cenário demonstrativo isolado para preservar o workspace. Projetos, edição de enlaces e remoção persistente continuam pendentes. Detalhes em [Q03 — inventário](orion_q_inventory_q03.md).
- **Segundo incremento em 12/09/2026:** criação e atualização versionadas de parâmetros de enlaces, formulário em TOPOLOGY, confirmação numérica e histórico antes/depois. A configuração não executa serviços automaticamente. Projetos, remoção, posições e execução vinculada à rede editada continuam pendentes. Detalhes em [Q03 — enlaces](orion_q_links_q03.md).
- **Funcionamento:** criar, salvar, duplicar e reabrir redes; editar nós, enlaces, posições e capacidades; validar a configuração; aplicar uma versão explícita ao simulador. Separar localização geográfica de conexões físicas e emaranhamento disponível.
- **Aceite:** um nó adicionado passa a existir no backend após confirmação e sobrevive à reinicialização. Configuração inválida não substitui a ativa. Topologia de fonte medida continua controlada pela fonte.
- **Base/esforço:** integrar o store de topologia com o cadastro visual; G. Depende de Q01.

#### Q04 — Central de comandos com ciclo de vida

- **Reconciliação em 26/09/2026:** API local e CLI com leitura única de recibo/topologia, recusa de seleção alterada e exportação sem sobrescrita. Evidência separada preserva REQUESTED/RUNNING/UNKNOWN; coincidência não confirma execução. CHECK CURRENT STATE e EXPORT EVIDENCE agora integram o COMMAND CENTER, com tarefas em segundo plano, invalidação por fonte/seleção e exportação capturada. Próximo: preparação/revisão auditável Q05 em simulação. [Escopo e uso](orion_q_commands_q04.md).

- **Primeiro incremento em 21/09/2026:** COMMAND CENTER em Access & Audit acompanha cadastro de nós e configuração de enlaces da console: IDs persistentes, transições, confirmação por releitura e UNKNOWN sem retry após falha. Consulta somente leitura dos últimos 50; sem cancelamento/reconciliação geral. [Escopo e limites](orion_q_commands_q04.md).
- **Funcionamento:** expor solicitado, aceito, executando, concluído, recusado, falhou, cancelado e resultado desconhecido, com IDs correlacionados. Consultar o efeito confirmado, prazo e motivo.
- **Aceite:** timeout não provoca repetição cega; reinício recupera o estado do comando; cancelar uma solicitação não promete desfazer uma operação quântica já consumada. Contagens vêm de eventos tipados, não de palavras em logs.
- **Base/esforço:** reutilizar intents, resultados e journal; G. Depende de Q01–Q02.

#### Q05 — Política realmente ativa e explicação de decisões

- **Inspeção contextual nativa em 29/09/2026:** EDGE CONTROL → CONTEXT INSPECTION recebe snapshot JSON explícito, consulta em sessão privada/segundo plano, apresenta contexto/motivos e exporta recibo capturado. Edição, fonte/runtime/topologia/prazo e fechamento invalidam seleção e retornos tardios. Não preenche previsões/timestamps nem habilita modelos. [Contrato](orion_q_policy_context_q05.md) · [Validação](../results/orion_q_policy_context_ui_20260929.md). Próximo: tradução tipada e limitada das ações dos roteadores, com recursos/precondições explícitos e recusa de ações sem correspondência. Q05 parcial, modelos opcionais/OFF, sem atuação física.

- **Observação contextual em 29/09/2026:** API local em sessão separada vincula entrada simulada explícita a política/supervisor, nó e topologia persistidos. Compara origem declarada, semântica, idade, incerteza e recursos; contexto alterado/expirado é recusado. Não autentica a origem nem avalia uma ação. Candidatos opcionais continuam bloqueados. [Contrato](orion_q_policy_context_q05.md) · [Validação](../results/orion_q_policy_context_20260929.md). Próximo: integrar inspeção contextual à interface com seleção explícita, invalidação e exportação capturada. Q05 parcial; modelos opcionais/OFF, sem atuação física.

- **Compatibilidade declarada em 29/09/2026:** catálogo nativo, API local e CLI expõem identidade, domínio, ações e ciclo de ativação por componente. As onze opções permanecem BLOCKED; a política existente exige contexto. Verificação de ZIP não habilita seleção, e coincidência de nomes de ação não traduz recursos ou semântica. O relatório não observa runtime nem valida telemetria. [Contrato](orion_q_policy_compatibility_q05.md) · [Validação](../results/orion_q_policy_compatibility_20260929.md). Próximo: observação vinculada a contexto simulado concreto, recusando domínio/mapeamento ausente ou contexto alterado. Q05 parcial, modelos opcionais/OFF, sem atuação física.

- **Explicação estruturada em 28/09/2026:** DECISION AUDIT e API somente leitura verificam recibos simulados e apresentam intenção, violações de segurança, ação efetiva e resultado do executor. Novos ciclos ancoram o hash do recibo completo da política; vínculo antigo/ausente/não verificável é explícito. Nenhuma decisão é recalculada e a configuração atual não substitui a histórica. [Contrato](orion_q_policy_decisions_q05.md) · [Validação](../results/orion_q_policy_decisions_20260928.md). Próximo: critérios de compatibilidade para seleção de outros controladores/modelos, mantendo seleção bloqueada até aceite. Q05 parcial, modelos opcionais/OFF, sem atuação física.

- **Rollback e recuperação nativos em 28/09/2026:** ACTIVE POLICY oferece PREPARE ROLLBACK com destino registrado, seguido de nova revisão/aprovação/ativação; INSPECT RECOVERY e confirmação própria mantêm a política restaurada e auditam a resolução. Seleções são vinculadas ao painel, e a recuperação invalida aprovações anteriores. A regressão integrada cobre fluxo completo de limiar, fontes concorrentes e falhas de commit. [Contrato](orion_q_policy_recovery_q05.md) · [Validação](../results/orion_q_policy_recovery_ui_20260928.md). Próximo: explicação estruturada de ação/veto/ação efetiva vinculada à política ativa. Q05 segue parcial para seleção e compatibilidade de outros controladores/modelos; modelos opcionais OFF, sem atuação física.

- **Ativação e readback nativos em 28/09/2026:** POLICY REVIEW oferece ACTIVATE com confirmação separada vinculada à aprovação. ACTIVE POLICY consulta explicitamente o estado e horário, distinguindo COMMITTED de ACTIVE/UNVERIFIED/UNRESOLVED; exporta o recibo capturado sem sobrescrita. Invalidação antes do commit impede a troca; após o início do commit, fechar/trocar fonte não promete desfazê-la. [Contrato](orion_q_policy_activation_q05.md) · [Validação](../results/orion_q_policy_activation_ui_20260928.md). Próximo: rollback e recuperação na interface, seguidos de aceitação do fluxo completo. Q05 parcial, modelos OFF, sem atuação física.

- **Rollback e recuperação locais em 28/09/2026:** rollback prepara o limiar anterior registrado e exige nova revisão/aprovação/ativação. Recuperação exige observação recente e confirmação específica, mantém o objeto restaurado e grava um checkpoint antes de liberar ciclos. Falha durante recuperação mantém bloqueio e exige nova observação. Sem retry ou restauração após reinício. [Contrato](orion_q_policy_recovery_q05.md) · [Validação](../results/orion_q_policy_recovery_20260928.md). Próximo: integrar ativação, readback, rollback e recuperação à interface nativa e validar o fluxo completo. Somente simuladores isolados foram usados; Q05 segue parcial.

- **Ativação local em 28/09/2026:** API de simulação exige aprovação da sessão, nova confirmação, contexto/prazo válidos; transação SQLite registra aprovação e configurações anterior/nova, com leitura atual da política. Ciclos verificam evidência e ficam bloqueados se o resultado do commit for incerto. Reinício não reaplica configuração histórica. [Contrato](orion_q_policy_activation_q05.md) · [Validação](../results/orion_q_policy_activation_20260928.md). Próximo: recuperação/rollback auditáveis e integração nativa da ativação/readback. Somente fixtures isoladas foram ativadas neste incremento; sem modelos opcionais ou atuação física.

- **Confirmação nativa em 28/09/2026:** APPROVE PROPOSAL apresenta referência/proposta e hashes, exige rótulo declarado e caixa inicialmente desmarcada; RECORD APPROVAL relê contexto/prazo em segundo plano. EXPORT APPROVAL guarda o recibo capturado sem sobrescrita. Troca de fonte/seleção, expiração e fechamento invalidam a confirmação. [Uso](orion_q_policy_approval_q05.md) · [Validação](../results/orion_q_policy_approval_ui_20260928.md). Próximo: ativação transacional em simulação com auditoria persistente e confirmação da política ativa, seguida de rollback. Nenhuma política foi ativada.

- **Aprovação local em 28/09/2026:** contrato Python exige confirmação explícita e hashes da proposta/última revisão CURRENT, relê contexto e prazo antes de registrar. Identidade declarada pelo chamador, sem autenticação ou autoridade de ativação. Exportação histórica sem sobrescrita; nova revisão/proposta/sessão invalida a seleção. [Contrato](orion_q_policy_approval_q05.md) · [Validação](../results/orion_q_policy_approval_20260928.md). Próximo: confirmação de aprovação na interface nativa; ativação/readback/rollback permanecem pendentes.

- **Preparação e interface em 27/09/2026:** API local e painel EDGE CONTROL → POLICY REVIEW para proposta explícita de limiar da política existente do repetidor; referência completa, versão/hash de topologia, expiração monotônica e revisão CURRENT/STALE sem aprovação/ativação. Consulta, preparação, revisão e exportação capturada em segundo plano; troca de fonte/runtime, edição, expiração e fechamento invalidam a seleção. Sem modelos ou rotas HTTP. [Contrato](orion_q_policy_preparation_q05.md) · [Validação nativa](../results/orion_q_policy_review_ui_20260927.md). Próximo: aprovação explícita vinculada à revisão; ativação transacional, readback e rollback continuam pendentes.

- **Atualização em 22/09/2026:** recebidos artigos e exports QKD/Quantum Network. Admissão Q16 verifica sincronizador, agente de taxa, roteador, compensador e alocador offline; ativação Q05 continua pendente. Todos devem ser opcionais, OFF por padrão, com baselines preservados. [Plano de integração](orion_q_two_models_integration_plan.md).
- **Funcionamento:** selecionar controlador, validar compatibilidade, preparar a troca e ativar somente após confirmação. Mostrar modelo, hash, parâmetros, política ativa, ação pretendida, veto de segurança e ação efetiva.
- **Aceite:** selecionar ML sem modelo compatível é recusado; a UI reflete a política lida do runtime. Troca e retorno à política anterior são auditáveis. Explicação usa dados estruturados, não texto inventado por IA.
- **Base/esforço:** fechar a lacuna já declarada por `set_decision_source`; G. Depende de Q01 e Q04.

#### Q06 — Gravador e replay de sessões

- **Funcionamento:** registrar observações, configurações e eventos; abrir a sessão, pausar, avançar e buscar um instante usando relógio virtual. Diferenciar reprodução visual de reexecução contrafactual de uma política.
- **Aceite:** replay preserva timestamps originais e não envia comandos físicos. Mesmo pacote e versão reproduzem eventos; divergências são sinalizadas. Histórico longo não precisa permanecer inteiro em RAM.
- **Base/esforço:** ampliar journal e fontes; G. Depende de Q02 e Q04.

#### Q07 — Alarmes operacionais com estado

- **Funcionamento:** regras configuráveis por métrica/contexto, duração mínima, histerese, deduplicação, reconhecimento e resolução. Separar dado ausente de valor ruim e correlacionar alarme com serviço afetado.
- **Aceite:** reconhecer não apaga a falha; desconexão não aparece como QBER zero; oscilação no limiar não gera centenas de incidentes. Limiares de fidelidade não são tratados como universais.
- **Base/esforço:** evoluir o event feed; M. Depende de Q02.

### P1 — Executar uma rede simulada, não apenas ciclos isolados

#### Q08 — Motor de eventos contínuos e relógio virtual

- **Incremento em 13/09/2026:** caminho de dois enlaces com tentativas discretas Bernoulli, semente, relógio virtual, orçamento, retenção e ruído T1/T2. Trilha persistida/exportável; ainda sem fila geral, heralding ou atraso de operação. [Modo temporal](orion_q_temporal_path.md).
- **Funcionamento:** evoluir geração probabilística, perdas, memória, confirmação clássica, operações e atrasos em uma fila de eventos. Rodar em tempo virtual, passo a passo ou velocidade de demonstração.
- **Aceite:** taxa de atualização da tela não muda os resultados; latência clássica e tempo de operação são contabilizados; consumo de CPU e número de eventos têm limites. Modelos físicos e simplificações ficam registrados.
- **Base/esforço:** ampliar o simulador existente; GG. Depende de Q02–Q04. Projetar a fronteira para Q23, sem reescrever um simulador geral completo.

#### Q09 — Inventário de pares e memórias quânticas

- **Incremento em 13/09/2026:** dois slots locais à execução, com chegadas, expiração, consumo e descarte final auditados. Estados envelhecidos preservados no swapping. Não é inventário distribuído ou persistente de pares físicos. [Modo temporal](orion_q_temporal_path.md).
- **Funcionamento:** identificar cada par e suas duas extremidades, posições de memória, proprietário/reserva, instante de criação, modelo de envelhecimento e validade. Acompanhar disponível, reservado, consumido, expirado ou descartado.
- **Aceite:** dois serviços não consomem o mesmo recurso. Swap e purificação consomem os recursos de entrada conforme o protocolo, inclusive em falhas; não há duplicação ou ressurreição no replay. Fidelidade estimada não é tratada como uma medição não destrutiva de cada par.
- **Base/esforço:** ampliar o estado de memória simplificado; G. Depende de Q03 e Q08.

#### Q10 — Solicitação de serviço de emaranhamento

- **Funcionamento:** escolher origem/destino, tipo de entrega suportado, quantidade ou taxa de pares, qualidade-alvo definida, prazo e prioridade. Obter aceitação, inviabilidade fundamentada ou alternativa sugerida.
- **Aceite:** toda solicitação termina ou expira com balanço de entregas, falhas e recursos. Não aceitar promessa de armazenamento em um endpoint sem memória; não confundir chave QKD com par emaranhado.
- **Base/esforço:** novo contrato sobre o controlador; G. Depende de Q04 e Q09. Começar com um único tipo de serviço bem especificado.

#### Q11 — Admissão, reservas e escalonador

- **Funcionamento:** arbitrar serviços concorrentes por memória, enlaces, operações e prazos. Oferecer políticas simples, como FIFO e menor prazo, antes de uma estratégia preditiva.
- **Aceite:** reservas são atômicas; conflito, cancelamento e expiração liberam recursos corretamente; sobrecarga resulta em recusa ou fila limitada. Medir equidade e não ocultar serviços indefinidamente famintos.
- **Base/esforço:** novo módulo; GG. Depende de Q09–Q10.

#### Q12 — Planejador de rotas e recuperação

- **Funcionamento:** comparar caminhos usando capacidades, probabilidades, duração, memória e qualidade estimada sob um modelo declarado. Mostrar motivo da rota e alternativas; recalcular diante de falha ou indisponibilidade.
- **Aceite:** menor distância não é automaticamente melhor rota; fidelidades não são multiplicadas sem modelo que justifique a composição. Uma rota prevista aparece separada de um par end-to-end efetivamente disponibilizado.
- **Base/esforço:** ampliar a topologia e políticas; G. Depende de Q08–Q11.

#### Q13 — Construtor de falhas e carga

- **Funcionamento:** programar perda de enlace, deriva óptica, memória curta, detector indisponível, tráfego excessivo, telemetria atrasada/inválida, falha do adaptador e reinício durante comando.
- **Aceite:** catálogo reproduzível com seed, instante e severidade; relatórios verificam invariantes e recuperação, não somente ausência de crash. Injetor desabilitado para atuação física; produção de dados artificiais usa o mesmo contrato de entrada.
- **Base/esforço:** ampliar a injeção e os testes visuais existentes para consequências operacionais; G. Depende de Q06 e Q08–Q12.

#### Q14 — Gerenciador de campanhas e comparações

- **Persistência da configuração em 21/09/2026:** SAVE MANIFEST AS guarda a solicitação aplicada em novo JSON UTF-8 reabrível por LOAD MANIFEST; sem sobrescrita, worker ou escrita no diário. Gravação em segundo plano, proteção contra troca de seleção/fonte durante a escolha e preservação da revisão/recibos. Não é autosave, backup da topologia ou pré-registro. [Uso e limites](orion_q_temporal_campaign_ui.md).
- **Incrementos em 13–14/09/2026:** [campanha temporal multisseed](orion_q_temporal_campaign.md) no backend/CLI: manifesto estrito, snapshot congelado, sorteios pareados por tick, recibo SQLite completo e JSON. [Painel nativo](orion_q_temporal_campaign_ui.md) com revisão, processo separado, progresso, cancelamento e readback verificado; [worker Windows empacotado e validado](orion_q_frozen_temporal_worker.md). [Histórico paginado de campanhas concluídas](orion_q_temporal_history.md), busca pelo início do ID, abertura verificada e exportação individual implementados. [Diário operacional de tentativas da console](orion_q_campaign_attempts.md) persiste observações de falha/cancelamento separadamente e oferece exportação individual com releitura, hash fixado na seleção e recusa de sobrescrita. Reconciliação de tentativas OPEN, cobertura dos runners CLI, filtros por data/metadados, exportação em lote e validação da distribuição em máquina independente ainda pendentes.
- **Funcionamento:** montar experimentos, congelar configuração, executar em processo separado, acompanhar progresso, cancelar e comparar políticas em cenários equivalentes. Registrar sementes, versões e falhas de execução.
- **Verificação externa em 14/09/2026:** VERIFY FILE confere esquema operacional e hash de JSON de até 32 KiB, sem importar registros ou alterar recibos científicos. Testa conteúdo inválido, troca de fonte e preservação do banco. Não autentica autoria nem compara o arquivo com o diário atual. [Uso e limites](orion_q_campaign_attempts.md).
- **Filtros operacionais em 14/09/2026:** ATTEMPTS combina prefixo literal do ID e estado, com aplicação explícita, paginação preservando filtros, recusa de exportação sob rascunho não aplicado e limpeza ao trocar fonte. Sem reconciliação automática de OPEN, contagens globais ou pesquisa por período. [Uso e limites](orion_q_campaign_attempts.md).
- **Período em 19/09/2026:** filtro pela data UTC de solicitação, com início incluído/fim excluído, combinado com prefixo e estado; datas inválidas recusadas e navegação preservada. Aba com rolagem vertical para evitar sobreposição em janelas pequenas. Não consulta por data de conclusão, não reconcilia OPEN e não agrega resultados científicos. [Uso e limites](orion_q_campaign_attempts.md).
- **Lote da página em 19–20/09/2026:** EXPORT PAGE ZIP exporta até 20 tentativas da página atual, com manifesto/filtros e JSONs individuais. Releitura em um único SELECT, validação estrita e recusa de mudança/remoção antes de criar o destino; sem sobrescrita. Não exporta toda a busca, não inclui recibos científicos, não importa nem reconcilia tentativas. [Uso e limites](orion_q_campaign_attempts.md).
- **Verificação de lote em 20/09/2026:** CHOOSE / VERIFY PAGE ZIP confere manifesto, todos os membros e filtros, sem extração, importação ou acesso ao diário. Limites de 2 MiB por ZIP, 50 tentativas e 32 KiB por membro; recusa conteúdo extra/duplicado, caminhos suspeitos, esquema/hash divergente e CRC inválido. Não autentica autoria nem comprova completude da busca original. [Uso e limites](orion_q_campaign_attempts.md).
- **Vínculo tentativa–recibo em 20/09/2026:** OPEN LINKED RECEIPT abre a comparação histórica a partir de uma tentativa selecionada com associação registrada. Releitura da tentativa e do recibo na mesma transação SQLite somente leitura, verificação de hashes/solicitação/topologia/versão e recusa de mudanças. Não reconcilia OPEN, não infere resultado pelo ID nem transforma replay/recuperação em nova amostra. [Uso e limites](orion_q_campaign_attempts.md).
- **Leitores da console em 21/09/2026:** histórico, recibos, tentativas, exportações operacionais e readback pós-worker usam conexão SQLite explicitamente somente leitura, sem construtor de schema/otimização. Banco ausente não é criado nem reparado. Registro/finalização de tentativas permanecem na trilha de escrita autorizada. [Escopo e limites](orion_q_campaign_attempts.md).
- **Editor de manifesto em 21/09/2026:** MANIFEST REVIEW → EDIT MANIFEST edita o JSON completo em rascunho isolado, com validação compartilhada de 64 KiB e aplicação atômica à revisão. Não salva nem executa; exige nova confirmação. Cancelamento/rascunho inválido preserva a solicitação; troca de fonte ou manifesto impede aplicação tardia. Não gera IDs/sementes nem adapta topologias automaticamente. [Uso](orion_q_temporal_campaign_ui.md).
- **Aceite:** GUI permanece responsiva; orçamento de execução é limitado; comparações usam perturbações exógenas equivalentes. Usar apenas a mesma seed não basta se diferentes ações consumirem o gerador aleatório em ordens diferentes.
- **Base/esforço:** integrar runners existentes; G. Depende de Q05, Q06 e Q13.

#### Q15 — Placar de utilidade e relatório de entrega

- **Incremento em 13/09/2026:** campanha de um caminho relata cumprimento de alvo/prazo incluindo timeouts, Wilson pontual, McNemar pareado + Holm contra baseline e custos/tempos descritivos. Não é taxa de rede, comparação de controladores WDM nem validação experimental. [Contrato e demonstração](orion_q_temporal_campaign.md).
- **Funcionamento:** comparar pares úteis entregues por tempo, cumprimento de prazo, consumo de pares brutos, memória-tempo, latência, recusas, abstinência e utilidade líquida. Exibir também erro preditivo, sem tratá-lo como sucesso operacional.
- **Aceite:** persistência e controle reativo permanecem baselines; oracle é apenas diagnóstico e não opção operacional. Custos normalizados não aparecem como reais, joules ou economia financeira. Relatório inclui resultados negativos e incerteza.
- **Base/esforço:** integrar scorecards e estatística existentes; M/G. Depende de Q14.

### P2 — Pesquisa reprodutível e operação cotidiana

#### Q16 — Catálogo de modelos, calibração e domínio de validade

- **Incrementos em 22/09:** [admissão offline](orion_q_author_model_intake.md) com hashes e paridade de dez configurações (sincronizador, agente de taxa, dois roteadores, quatro compensadores, dois alocadores). [Catálogo/fábrica opcionais](orion_q_optional_models.md) sem carregamento automático; seletor visual/runtime ainda pendentes. Detector bloqueado por pickle.
- **Incrementos em 29–30/09:** detector admitido por [exportação segura](../results/orion_q_detector_evaluation_20260929.md) (sessão offline, campanha em sessões novas); os cinco controladores em laço fechado nas plantas do autor; [Verilog do autor](../results/orion_q_author_rtl_20260930.md) simulado e sintetizado; afirmações num registro verificável.
- **Funcionamento:** listar arquiteturas existentes, cabeças/targets, ordenação de features, janelas, normalização, hashes e evidência de calibração. Mostrar OOD, dados insuficientes e cobertura observada quando houver alvo disponível.
- **Aceite:** modelo incompatível não é carregado; não retreinar silenciosamente; ausência de medição do alvo impede afirmar desempenho corrente. Artefatos inseguros não são desserializados para facilitar importação.
- **Base/esforço:** integração do registro e export seguro existentes; G. Depende de Q02 e Q05.

#### Q17 — Workspace científico WDM→quantum

- **Funcionamento:** executar pela UI as campanhas existentes com alvo e horizonte explícitos, histórico do alvo, WDM, WDM permutado, persistência, divisões temporais purgadas e avaliação entre campanhas/dispositivos quando os dados permitirem.
- **Aceite:** pré-processamento e calibração não veem o teste; hipóteses, famílias de testes e correção de multiplicidade são registradas; resultados exploratórios e confirmatórios são separados. Não chamar associação de causalidade.
- **Base/esforço:** integrar `run_measured_quantum_prediction_campaign.py` e módulos relacionados; G. Depende de Q14–Q16 e Q18 para nova base medida.

#### Q18 — Assistente de importação e aquisição pareada

- **Funcionamento:** mapear colunas CSV/Parquet, unidades, canal, dispositivo, tempo e métricas; mostrar amostra, rejeições e diagnóstico de sincronização antes de importar. Exportar o protocolo/template já existente.
- **Aceite:** tolerância temporal e janela de integração são explícitas; não unir registros só porque têm o mesmo número de linha. Campanha experimental rejeita fidelidade simulada; `KAPPA_DEPOL_FROM_BER` permanece sem calibração experimental na ausência de BER e fidelidade medidos sincronizadamente.
- **Base/esforço:** integrar contratos e templates existentes; M/G. Depende de Q02 e Q06. O assistente não cria a base real que hoje falta.

#### Q19 — Serviços QKD e simulador de consumo de chaves

- **Funcionamento:** planejar demanda, saldo, taxa de reposição, expiração e indisponibilidade de chaves; manter a análise QBER/SKR existente em seu domínio válido. No MVP, usar apenas metadados e material explicitamente fictício.
- **Aceite:** nenhuma chave real é exibida em gráficos, logs ou relatório; saldo simulado não autoriza uso criptográfico. Um adaptador KMS futuro é separado do serviço de emaranhamento e do modelo de Bell.
- **Base/esforço:** ampliar Key Management; G. Depende de Q01, Q04 e Q06; integração externa em Q27.

#### Q20 — Saúde do próprio ORION-Q

- **Funcionamento:** mostrar atraso de ingestão, tamanho de filas, mensagens descartadas, CPU/RAM, latência de inferência e comandos, falhas de adaptador e espaço em disco. Correlacionar por IDs de sessão/serviço.
- **Aceite:** backpressure e retenção são limitados; desconexão é visível; pacote diagnóstico remove segredos. Exportação OpenTelemetry pode ser opcional: seus sinais incluem métricas, traces e logs. [Documentação oficial](https://opentelemetry.io/docs/concepts/signals/)
- **Base/esforço:** instrumentar componentes existentes; M. Depende de Q04 e Q06.

#### Q21 — Pacote de projeto, backup e diagnóstico do executável

- **Funcionamento:** exportar configuração, topologia, referências de dados, políticas e relatórios em um pacote versionado com manifesto. Reabrir offline, migrar esquema e recuperar backup; verificar dependências opcionais.
- **Aceite:** restaurar um pacote em diretório novo reproduz o cenário; extração rejeita caminhos perigosos e tamanhos abusivos; credenciais ficam de fora. Testar conta Windows sem privilégios, escala de tela e indisponibilidade de rede.
- **Base/esforço:** ampliar distribuição e validação existentes; M/G. Depende de Q03 e Q06.

#### Q22 — Sessão, autorização e integridade de auditoria

- **Funcionamento:** distinguir identidade demonstrativa de sessão autenticada. Para uso com serviço compartilhado, estabelecer papéis observador/operador/administrador e conferir permissões no backend. Preservar trilha exportável e retenção.
- **Aceite:** o texto do perfil não concede privilégios; alteração indevida não é aceita por chamadas diretas à API. Hash local não é anunciado como assinatura, proteção contra reescrita total ou armazenamento inviolável; assinatura/âncora externa são melhorias condicionais.
- **Base/esforço:** ampliar sessão e journal; G. Depende de Q01 e Q04. Autenticação real torna-se bloqueante antes de controle remoto/compartilhado.

### P3 — Integrações especializadas, sem comprometer o MVP

#### Q23 — Adaptador SeQUeNCe e verificação cruzada

- **Funcionamento:** traduzir um subconjunto explícito dos cenários para um segundo simulador, executar em ambiente isolado e importar resultados. SeQUeNCe oferece simulação por eventos com componentes de hardware, recursos e protocolos. [Projeto oficial](https://github.com/sequence-toolbox/SeQUeNCe)
- **Aceite:** listar recursos traduzidos e não suportados; comparar distribuições/limites analíticos sob hipóteses equivalentes, não exigir igualdade de trajetórias. Verificar licença, versão e compatibilidade antes de distribuir dependências.
- **Base/esforço:** novo adaptador; G. Depende de Q08–Q15. Prioridade alta dentro de P3 para fortalecer resultados sem laboratório; concordância entre simuladores não é validação física.

#### Q24 — Planejamento óptico e coexistência WDM

- **Funcionamento:** explorar canais, potência, distâncias, perdas, filtros e orçamento óptico. Avaliar GNPy como módulo opcional para a parte clássica: é uma biblioteca de planejamento de redes ópticas baseada no Gaussian Noise Model. [GNPy](https://gnpy.readthedocs.io/en/master/)
- **Aceite:** resultado óptico não preenche fidelidade quântica; ruído de coexistência, detectores e conversão para desempenho quântico exigem modelos adicionais identificados e, para alegações experimentais, calibração. Mostrar faixa de parâmetros, incertezas e simplificações.
- **Base/esforço:** novo adaptador sobre a trilha WDM; G. Depende de Q02 e Q14. Não priorizar uma integração grande sem caso de teste definido.

#### Q25 — Integração IBM cancelada

Cancelado em 07/10/2026 a pedido do autor. Diagnóstico, seleção de backend,
submissão e recuperação IBM deixaram o produto e não constituem pendências
do backlog. A proposta e a implementação anteriores são preservadas no
[arquivo histórico](../../archives/retired_ibm_quantum_20261007/ibm_quantum_source.zip); Qiskit/Aer permanece para simulação local.

#### Q26 — Laboratório de protocolos: BBPSSW e teleportação

- **Funcionamento:** comparar protocolo analítico e circuito em simulação local compatível; acompanhar pós-seleção, probabilidade de sucesso, consumo de pares, medições intermediárias e comunicação clássica modelada.
- **Aceite:** incluir tentativas descartadas no denominador; distinguir fidelidade do par, qualidade do estado teleportado e taxa de sucesso. Circuito executado em um único processador não é apresentado como teleportação entre laboratórios. Circuitos dinâmicos dependem das capacidades efetivamente expostas pelo backend.
- **Base/esforço:** ampliar a simulação local existente; G. Depende de Q09 e Q14. A opção IBM e a dependência de Q25 foram canceladas em 07/10/2026.

#### Q27 — Adaptadores de telemetria e QKD padronizados

- **Funcionamento:** começar com servidor mock e contrato de conformidade; integrar um equipamento/alvo por vez. Considerar gNMI para telemetria clássica compatível; ETSI QKD 014 para entrega de chaves, 020 para interoperabilidade entre KMS, 015/018 para controle/orquestração QKD.
- **Aceite:** negociar versões, TLS/autorização, limites, timestamps e retomada; provar interoperabilidade por testes de um alvo específico. Começar somente leitura. Nomes de protocolo na UI não são prova de driver ou conformidade.
- **Base/esforço:** novos adaptadores; GG. Depende de Q01, Q02, Q04, Q19 e Q22 conforme o alvo. Referências e limites na seção 5.

#### Q28 — Fronteira de execução nos nós: NetQASM/QNodeOS

- **Funcionamento:** estudar um adaptador para enviar programas a runtimes de nós que efetivamente ofereçam essa capacidade; separar orquestração de rede e execução local. O trabalho QNodeOS demonstra execução de aplicações em nós e uma separação entre runtime e dispositivo. [Artigo primário](https://www.nature.com/articles/s41586-025-08704-w)
- **Aceite:** começar por mock/ambiente suportado e demonstrar um programa com retorno estruturado. Não pressupor disponibilidade de hardware, API estável ou driver universal; não tentar substituir esse runtime pelo desktop.
- **Base/esforço:** estudo técnico seguido de adaptador, se viável; GG. Depende de Q01, Q10, Q22 e acesso a um alvo suportado.

#### Q29 — Bancada de sincronização e diagnóstico fotônico

- **Funcionamento:** importar contagens e time tags quando disponíveis, visualizar coincidências, janela de integração, deriva de relógio, perdas e metadados dos instrumentos. Relacionar observações ópticas e quânticas por canal e intervalo validado.
- **Aceite:** precisão e incerteza temporal são declaradas; coincidência não implica fidelidade medida. Tomografia/testemunho e QBER têm procedimentos próprios; processamento pesado ocorre fora da GUI. Relógio Windows/NTP não é anunciado como sincronização de fótons.
- **Base/esforço:** módulo novo; GG. Depende de Q02, Q18 e arquivos de aquisição ou instrumentos compatíveis. Pode iniciar com registros sintéticos claramente rotulados.

#### Q30 — Monitor FPGA e primeiro adaptador físico restrito

- **Funcionamento:** primeiro ler identidade, firmware, estado, contadores e latências reais. Somente depois implementar uma operação específica documentada, com precondições, leitura de confirmação, expiração de autorização e proteção independente do processo gráfico.
- **Aceite:** dispositivo ausente significa métricas indisponíveis; benchmark de CPU não vira latência FPGA. Perda de comunicação, crash ou revogação levam ao estado seguro definido para aquele equipamento; rollback de configuração não promete desfazer consumo quântico. Flash continua fora do MVP.
- **Base/esforço:** novo trabalho de integração e validação em bancada; GG. Depende de Q01, Q04, Q20, Q22 e hardware. A estrutura de recusa atual não deve ser relaxada para simular prontidão física.

## 5. O que aproveitar de fora e o que não confundir

| Referência/ferramenta | Uso proposto | Limite |
|---|---|---|
| PySide6, SQLite, modelos e runners já presentes | Manter a base do produto; dados históricos volumosos podem usar Parquet opcional | Não criar uma segunda interface web ou infraestrutura distribuída por padrão |
| SeQUeNCe | Primeiro candidato a simulação independente, Q23 | Subconjunto traduzido e hipóteses verificadas; não importar um projeto inteiro sem fronteira |
| Qiskit/Aer | Protocolos e circuitos em simulação local, Q26 | Simulação de processador e rede quântica são objetos experimentais distintos; a integração IBM foi cancelada |
| GNPy | Planejamento da camada óptica clássica, Q24 | Não fornece por si só a ponte experimental WDM→fidelidade |
| OpenTelemetry | Diagnóstico opcional do software, Q20 | Não é protocolo de comando quântico |
| gNMI/OpenConfig | Aquisição de equipamentos clássicos que suportem modelos compatíveis, Q27 | Não assume ordem de chegada dos timestamps nem semântica quântica universal |
| ETSI QKD 014 e 020 | Entrega de chaves e interoperabilidade entre KMS, respectivamente | Não são APIs universais de swap/purificação |
| ETSI QKD 015 e 018 | Controle e orquestração SDN de QKD | Compatibilidade exige implementação e teste, não apenas usar o nome |
| QNodeOS/NetQASM | Possível fronteira futura com execução em nós | Exige estudo de disponibilidade e adaptador específico |

gNMI define consultas e assinaturas de telemetria, inclusive notificações que podem chegar fora de ordem temporal. Isso orienta a engenharia da entrada; o mapeamento das métricas continua dependente do equipamento. [Especificação primária OpenConfig](https://github.com/openconfig/reference/blob/master/rpc/gnmi/gnmi-specification.md)

Para QKD, separar as interfaces desde o desenho: [GS QKD 014 v1.1.1 — entrega REST](https://www.etsi.org/deliver/etsi_gs/QKD/001_099/014/01.01.01_60/gs_qkd014v010101p.pdf), [GS QKD 020 v1.1.1, publicada em junho de 2026 — interoperabilidade KMS](https://www.etsi.org/deliver/etsi_gs/QKD/001_099/020/01.01.01_60/gs_QKD020v010101p.pdf), [GS QKD 015 v2.1.1 — controle SDN](https://www.etsi.org/deliver/etsi_gs/QKD/001_099/015/02.01.01_60/gs_QKD015v020101p.pdf) e [GS QKD 018 v1.1.1 — orquestração SDN](https://www.etsi.org/deliver/etsi_gs/QKD/001_099/018/01.01.01_60/gs_qkd018v010101p.pdf). A existência das especificações não certifica a implementação local.

Evitar neste estágio: vários simuladores integrados simultaneamente; dependências de cloud obrigatórias; chatbot autorizando comandos; mais arquiteturas neurais sem hipótese; blockchain para logs; controles físicos genéricos; promessas comerciais de segurança/uptime sem avaliação. Conferir licenças, compatibilidade e política de distribuição antes de incorporar qualquer dependência ao executável.

## 6. Como acomodar as funções na interface aprovada

| Tela existente | Evolução funcional sugerida |
|---|---|
| Topology | Editor validado, seleção de endpoints, solicitações de serviço, rotas e sobreposição de recursos; GEO e Matrix preservados |
| Telemetry | Séries históricas, qualidade temporal, canais, importação e controles de replay |
| Key Management | QKD, disponibilidade/consumo e simulação de demanda; sem exposição de segredos |
| Nodes | Inventário persistente, capacidades, conexões e memória/pares por nó |
| Edge Control | Política efetiva, fila do escalonador, explicação de decisões e cenários de falha; aba de protocolos locais quando disponível |
| Access & Audit | Sessão real ou demonstrativa, comandos, campanhas, comparações, relatórios e exportação |

Usar abas internas, painéis de detalhe e fluxos de confirmação, mantendo cores, fontes e posicionamento estrutural da referência. Não multiplicar menus laterais por biblioteca integrada. Mostrar detalhes de nós e perfil apenas por interação, como solicitado. Uma faixa persistente identifica modo/origem; campos individuais preservam sua proveniência.

## 7. Onde está o possível ganho científico

O resultado registrado v5.26 encontrou **0/3 comparações neurais positivas e significativas contra persistência**, e DualHead teve menor MAE neural, mas utilidade operacional inferior. Isso vale para aquele experimento simulado, não prova que previsão seja sempre inútil. [Resultado local e condições](../results/wdm_policy_remediation_v526.md)

Portanto, a próxima pergunta não deveria ser apenas “qual rede neural prevê melhor?”. Uma hipótese candidata, ainda não descoberta e sem novidade bibliográfica estabelecida, é:

> Sob memória finita, atrasos e custo de aquisição, uma política que decide quando observar, esperar, purificar e alocar recursos entrega mais pares úteis dentro do prazo que baselines periódicos/reativos com o mesmo orçamento de informação?

Antes de criar uma nova cabeça, medir o valor potencial da informação com um oracle diagnóstico. Se nem o oracle melhora a utilidade no cenário escolhido, mudar o preditor dificilmente resolve esse cenário. Se há diferença, investigar frescor, incerteza, custo de sondagem e escalonamento.

Protocolo proposto:

1. Fixar um cenário e mecanismo físico/simulado explícitos; registrar o que é assumido e o que é medido.
2. Pré-definir métrica primária e orçamento igual de observação/recursos. Medir ganho contra persistência, reativo e aquisição periódica; separar oracle.
3. Usar streams exógenos acoplados por evento/canal ou outra técnica documentada para comparação justa entre políticas de ações diferentes.
4. Separar ajuste, calibração e confirmação; não selecionar políticas no teste. Testar duração da memória, atraso, carga e deslocamento de domínio.
5. Reportar tamanho de efeito, intervalos, falhas, custo e multiplicidade; conservar resultados negativos.
6. Validar invariantes e casos analíticos; depois verificar um subconjunto em simulador independente.
7. Somente com dados pareados medidos avaliar a alegação WDM→desempenho quântico real. WDM permutado e histórico do alvo continuam controles necessários.

O ganho do software é tornar esse protocolo executável e auditável. Isso pode sustentar um TCC de produto e experimentação em simulação; não substitui medições físicas nem garante uma descoberta inédita.

## 8. Sequência recomendada e critérios de saída

### Etapa A — Fechar a integração atual

Q01–Q07. Saída: cadastro persistente altera o simulador; política selecionada é a efetivamente ativa; comandos têm confirmação; histórico e alarmes refletem observações tipadas. Nenhum controle visual sugere uma capacidade inexistente.

### Etapa B — Entregar um serviço completo em sandbox

Q08–Q15. Saída: rede com caminhos alternativos executa solicitações concorrentes; recursos são reservados/consumidos; falha reproduzível leva a recuperação ou recusa explicável; relatório compara baselines. Começar com um protocolo e uma topologia pequena antes de generalizar.

### Etapa C — Transformar a execução em bancada de pesquisa

Q16–Q22 e um subconjunto de Q23. Saída: outra instalação reproduz uma campanha a partir do pacote exportado; modelo, dados, política e resultados são rastreáveis; avaliação não mistura evidências. Priorizar Q23 sobre integrações cosméticas se o objetivo imediato for fortalecer a tese.

**Preparação em 14/09/2026:** [diagnóstico nativo de aceitação Windows](orion_q_windows_acceptance.md), com hashes, self-test e stress em estado isolado, mais roteiro manual de campanhas/histórico. A ferramenta facilita executar a aceitação sem ambiente de desenvolvimento; a reprodução em segunda máquina ainda precisa ser realizada e não é atestada pelos testes locais.

### Etapa D — Expandir conforme o acesso disponível

Q24 e Q26–Q30 seletivamente; Q25 foi cancelado em 07/10/2026. Adaptadores começam mock e somente leitura. Cada dispositivo exige um caso de uso, contrato, testes e limites próprios. Não há obrigação de concluir esta etapa para o produto do TCC ser utilizável.

## 9. Primeira demonstração de produto recomendada

1. Abrir um projeto simulado com origem, destino e duas rotas candidatas.
2. Configurar a qualidade-alvo e prazo de um serviço sob hipóteses exibidas.
3. Rodar o baseline reativo e a persistência; acompanhar geração, reserva, consumo e entrega.
4. Injetar uma falha de enlace e envelhecimento de memória pelo painel.
5. Reexecutar o cenário com política candidata congelada, sem presumir que será superior.
6. Comparar entregas úteis, prazo, consumo e abstenções; abrir uma decisão e rastrear seus dados.
7. Exportar o pacote e reproduzi-lo em outra execução do programa.

**Sequência originalmente sugerida em 10/09:** Q01 + Q03 + Q04 + Q05, apoiados na estrutura mínima de Q02. É o menor conjunto que conecta intenção do usuário, estado real do backend e evidência do resultado; depois vêm replay e serviços quânticos completos.

**Sessão gráfica de alocação em 24/09:** entregue o produtor artificial com
histórico causal e a sessão shadow nativa opt-in, START/STOP, watchdog, descarte
de workers atrasados e exportação do frame capturado. Doze avaliações com o
pacote original passaram; a fila permanece fixture exógena, sem realimentação.
[Uso e validação](../results/orion_q_allocation_shadow_ui_20260924.md).
A [ponte shadow de roteamento](orion_q_routing_shadow_bridge.md) também está em
fonte: recursos simulados explícitos, admissão instantânea das seis ações,
recusa integral da janela inválida e seleção reproduzível sem consumo de pares.
[Validação e limites](../results/orion_q_routing_shadow_20260924.md).
A [sessão gráfica shadow de roteamento](../results/orion_q_routing_shadow_ui_20260924.md)
agora também está em fonte, com produtor artificial independente, START, frescor,
invalidação por tarefa/topologia/versão e exportação do frame capturado. Doze
avaliações nativas mantiveram paridade offline, sem consumo de recursos.
A [ponte shadow do ator de taxa QKD](orion_q_keyrate_shadow_bridge.md) também foi
concluída em fonte, com contexto explícito, períodos causais, bootstrap declarado,
semente, nove propostas limitadas e baseline de manter parâmetros. Oito verificações
com os pesos originais e 564 testes selecionados passaram.
[Validação](../results/orion_q_keyrate_shadow_20260924.md).
**Conclusão em 26/09:** o [produtor e a sessão gráfica shadow QKD](../results/orion_q_keyrate_shadow_ui_20260926.md)
também estão em fonte, com START, frescor por período observado, bootstrap explícito,
invalidação e exportação. Seis avaliações nativas com os pesos originais passaram.
As dez configurações admitidas têm interface offline e shadow artificial; Q16
continua parcial. A reconciliação explícita de comandos Q04 não resolvidos já
possui API/CLI somente leitura e integração ao COMMAND CENTER com consulta
explícita e exportação capturada. Q05 já tem API local e interface nativa de
preparação/revisão de limiar em simulação, entregues em 27/09, e contrato local
de declaração de aprovação e confirmação gráfica em 28/09. Ativação transacional
em simulação, auditoria persistente, readback, rollback aprovado e recuperação
explícita já têm API local e interface, com regressão integrada do fluxo de limiar.
A explicação estruturada histórica está integrada ao DECISION AUDIT. Os critérios
de compatibilidade declarada já aparecem na API, CLI e catálogo; seleção permanece
bloqueada. A observação contextual local já compara entrada simulada com runtime
existente e topologia persistida; não fornece tradução de ações para modelos.
A inspeção contextual já está na interface com invalidação e exportação capturada.
Próximo: contrato de tradução limitada de ações dos roteadores para recursos do
repetidor simulado, mantendo seleção bloqueada.
Recibos são preservados e não há retry automático. Todos os modelos
continuam opcionais e sem atuação física.

**Incremento seguinte em 24/09:** entregue a
[ponte shadow de alocação](orion_q_allocation_shadow_bridge.md), com planta,
relógio/fluxo e histórico tipados; comparação persistência/Ridge sem alterar filas,
API somente leitura e CLI de replay. Quatro casos com o pacote original e 407
testes selecionados passaram. Q16 continua parcial; próximo foco: produtor
artificial e sessão nativa shadow opt-in. Q05 e atuação física seguem pendentes.

**Atualização em 24/09:** a [campanha SO(3) v1](../results/orion_q_compensation_campaign_20260923.md)
está concluída: 240 execuções, 24 grupos publicados e seis atendendo aos filtros
descritivos, sem vantagem uniforme. A regressão selecionada registra 500 testes
aprovados. Q16 agora inclui pontes shadow e avaliação temporal de sincronização e
compensação em fonte, mas permanece parcial. Próxima integração: ponte tipada de
outro componente opcional, como roteamento/alocação. Q05 continua pendente,
modelos OFF por padrão e nenhuma atuação física. A [nova localização](local_workspace.md)
foi registrada; o aceite Windows independente e os demais itens abertos continuam
válidos. A posição abaixo mantém o histórico do incremento anterior.

**Posição consolidada em 23/09:** Q01 e a base Q02 estão presentes; Q03 já tem nós/enlaces persistentes e execução limitada de caminho. Q08/Q09 seguem parciais. Q14 avançou até campanhas empacotadas, histórico, filtros, exportações verificadas e edição/salvamento de manifesto. Q04 começou com comandos de inventário rastreáveis; reconciliação explícita e ampliação continuam necessárias. Etapa A não está concluída. Q16 tem admissão offline do sincronizador, agente de taxa, roteador, compensador e alocador, com catálogo/fábrica opcionais OFF por padrão, sem ativação Q05. O catálogo nativo OFF/offline/bloqueado já está implementado em fonte, incluindo verificação explícita de identidade/integridade dos ZIPs em segundo plano, sem instalação inferida. Há sessões gráficas offline distintas para o sincronizador (persistência e referência futura isolada da inferência), o ator QKD (semente PCG64 explícita, nove ações limitadas e baseline de manter parâmetros) e os quatro compensadores (SO(3), aquecimento explícito e erro angular final versus persistência), além dos dois roteadores (features nomeadas, semente explícita e referência de decisão, sem alegação de desempenho), com exportação JSON sem sobrescrita. As duas opções de alocação também têm sessões offline com baseline de persistência sobre o mesmo snapshot, fila comprometida e aquecimento explícito. As dez configurações admitidas possuem sessão gráfica em fonte; Q16 permanece parcial. O build Windows de 23/09 contém as dez sessões e passou na verificação local com/sem os pacotes externos, sem embutir os novos pesos. Próximo foco de integração: ponte tipada para avaliação shadow do sandbox antes de Q05. Permanecem inspeção em máquina independente, export seguro do detector, licenças/distribuição opcional dos pesos e ativação auditável. Os executáveis anteriores não foram alterados. Não inferir ganho operacional de paridade numérica, mudança de parâmetros ou demonstração isolada. O restante de Q10–Q12 e a verificação cruzada Q23 continuam no plano.
