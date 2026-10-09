# ORION-Q — execução condicional sobre caminho configurado

Data: 12/09/2026. Incremento após o editor de enlaces Q03. Código-fonte atualizado; o executável distribuído anteriormente não foi reconstruído.

Atualização em 13/09/2026: o diálogo também oferece um [modo temporal com aquisição probabilística e memória T1/T2](orion_q_temporal_path.md). O protocolo condicional descrito neste documento continua disponível, sem mudar suas hipóteses.

## O que passou a funcionar

Em **TOPOLOGY → RUN CONFIGURED PATH**, o usuário escolhe explicitamente origem, repetidor e destino, define uma fidelidade-alvo como fração e executa um swapping condicional. O backend lê os dois enlaces do caminho na versão indicada e reutiliza `SimulatedQuantumExecutor`, `SafetySupervisor` e o núcleo de matrizes densidade `WernerStateSwapping` já existentes no repositório.

Não é o preset demonstrativo `resilience_demo`, não há seleção automática de rota e não se muda a política do controlador WDM. É um protocolo fixo `werner_single_swap_v1`, com exatamente um repetidor.

## Uso e confirmação

1. Cadastre nós/enlaces no workspace local ou use a linha padrão.
2. Abra **RUN CONFIGURED PATH**. A versão do formulário fica fixa; atualizações periódicas não a substituem silenciosamente.
3. Escolha três nós distintos no formato endpoint → repetidor → endpoint e informe o alvo entre 0 e 1.
4. **RUN FROZEN PATH** envia uma solicitação identificada. PENDING não é sucesso; CONFIRMED exige leitura do resultado persistido e verificado.
5. **NEW REQUEST / RELOAD FORM** descarta o formulário anterior e cria outro identificador com a versão atual disponível no snapshot.

Repetir o mesmo ID com a mesma solicitação recupera o resultado original sem recalcular, mesmo que a topologia tenha sido editada depois. Reutilizar o ID com outros parâmetros é recusado. Uma nova solicitação com versão antiga é recusada. Fechar o diálogo não cancela uma operação aceita. Trocar a fonte fecha e limpa o formulário; entradas medidas/artificiais não recebem essa capacidade.

O diálogo identifica o resultado anterior como PREVIOUS RESULT, incluindo a versão efetivamente usada, em vez de atribuí-lo a uma nova solicitação ou à topologia atual. A confirmação também é recebida ao navegar para outra tela durante a execução assíncrona.

## Hipóteses físicas do cálculo

- Dois pares independentes de Werner **já disponíveis**, um para cada enlace selecionado. As fidelidades vêm dos parâmetros nominais, não de medições.
- BSM e correções de Pauli ideais. O núcleo calcula a fidelidade média ponderada pelos resultados da medição.
- Idade de memória zero. Pares são instanciados em um estado isolado; a memória existente no workspace não é reutilizada, consumida ou sobrescrita.
- Não há geração probabilística, perda, decoerência temporal, latência, vazão ou disponibilidade física neste protocolo.
- Comprimentos são registrados, mas **não afetam o resultado**: não se introduziu uma conversão arbitrária distância→ruído.
- Não há purificação, encadeamento de vários repetidores, previsão WDM ou atuação física.

Como referência de teste do núcleo existente, o caso de Werner ideal obedece a `F_out = F1*F2 + (1-F1)*(1-F2)/3`. A suíte compara a simulação de matrizes densidade com essa expressão, admitindo erro numérico absoluto de `1e-7` no teste. Esse valor não é uma incerteza física calibrada.

O campo `conditional_target_met` compara o resultado numérico não arredondado com o alvo (`>=`), sem tolerância adicional de decisão. Valores extremamente próximos ao alvo estão sujeitos ao arredondamento numérico do simulador; não constituem garantia física. Mesmo um alvo satisfeito não significa entrega de emaranhamento a equipamentos. Um alvo baixo também não é certificado de emaranhamento útil.

O contrato legado do executor exige `predicted_fidelity`. Neste caminho, o campo recebe um placeholder nominal explicitamente marcado `nominal_input_placeholder_not_model_prediction`; a ação fixa SWAP não é uma decisão preditiva. Incerteza e idade de telemetria zero são hipóteses do estado sintético, não propriedades instrumentais.

## Validações e persistência

`WorkspacePathRequest` aceita exatamente ID da execução, origem, repetidor, destino, versão, alvo, topologia padrão e protocolo suportado. IDs são ASCII limitados a 64 caracteres; booleanos, números não finitos, campos extras e overrides físicos/modelos são recusados.

Cada salto deve ter exatamente um enlace habilitado entre seus endpoints. Nós inexistentes, papéis incorretos, enlaces ausentes/desabilitados e canais paralelos ambíguos são recusados, sem desvio automático para outra rota. Outras partes desconectadas da rede não impedem um caminho válido.

Leitura da versão, cópia da topologia, cálculo local limitado e gravação do resultado usam uma transação SQLite `BEGIN IMMEDIATE`. Escritores concorrentes não alteram a configuração durante esse cálculo. Isso mantém a operação atômica, mas serializa gravações enquanto o cálculo está em execução; não é arquitetura para alto throughput. Falhas antes do commit não deixam uma execução parcialmente persistida.

O workspace permanece inalterado. Se ainda não houver topologia persistida, o blueprint padrão de versão zero é copiado para o registro, com `source_was_persisted=false`, sem criar uma falsa versão salva no workspace.

A tabela `workspace_path_runs`, separada dos ciclos anteriores, guarda solicitação, topologia completa congelada, hashes, parâmetros dos dois enlaces, snapshot sintético, intenção, avaliação de segurança, resultado do executor, hipóteses, versões NumPy/Qiskit e versão do protocolo. Não incrementa o contador de ciclos do controlador legado. Hashes são verificados na leitura, mas **não são assinaturas nem autenticação contra adulteração deliberada com recálculo dos hashes**.

Após reiniciar o serviço, o resultado mais recente pode ser consultado pelo diálogo. A exportação JSON existente da console agora inclui o registro completo em `workspace_path`, mantendo-o separado da topologia corrente. Não foi adicionada uma rota HTTP de escrita.

## Evidência de software e próximos passos

`tests/test_workspace_path.py` cobre contratos, fórmula de referência, memória preservada, versões antigas, idempotência, gravações concorrentes, falhas transacionais, integridade, caminhos personalizados, exportação e interface síncrona/assíncrona. Relatórios JUnit e capturas nativas ficam em `outputs/software_validation_workspace_path/`.

Esta entrega conecta configuração a um cálculo quântico rastreável. **Não traz nova evidência de que WDM real prediz fidelidade quântica.** Também não fecha Q03 inteiro nem implementa o motor temporal Q08 ou um serviço de rede completo.

Próximo passo recomendado: modelar explicitamente disponibilidade de pares e tempo/idade de memória antes de alegar entrega, taxa, recuperação ou comparar políticas preditivas. Projetos nomeados, remoção segura e importação de configurações seguem pendentes.
