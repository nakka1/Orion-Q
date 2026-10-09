# ORION-Q — Q03: configuração persistente de enlaces

Data: 12/09/2026. Segundo incremento de Q03, após o cadastro de nós. Código-fonte atualizado; o executável anterior não foi reconstruído.

Atualização posterior: foi implementado um [swapping condicional sobre caminho configurado](orion_q_workspace_path.md). Este documento mantém o escopo do editor, sem atribuir a ele execução automática de serviços.

## Como usar

1. Selecione a entrada do serviço local e abra **TOPOLOGY → CONFIGURE SIMULATED LINKS**.
2. Escolha **NEW LINK** ou selecione um enlace existente.
3. Na criação, forneça ID e dois nós já existentes. Informe comprimento de fibra em km, fidelidade nominal como fração e o indicador de habilitação da configuração.
4. Pressione **SAVE CONFIGURATION**. PENDING indica solicitação sem confirmação; CONFIRMED exige leitura posterior da configuração persistida.
5. Para editar novamente, selecione o enlace. Atualizações mantêm ID e endpoints fixos. Após conflito de versão, **RELOAD FORM** descarta o formulário e carrega a versão mais recente disponível no snapshot.

O editor só aparece na entrada local que declara essa capacidade. As entradas artificial e medida não recebem permissão para escrever nesse inventário. Trocar de fonte fecha e limpa o editor, sem reenviar comandos. Fechar a janela não cancela uma gravação já aceita.

## Contrato

`ConfigureSimulatedLink` exige exatamente `mode`, `link_id`, `left_node_id`, `right_node_id`, `distance_km`, `nominal_pair_fidelity`, `active`, `expected_version` e `topology_id`.

- Modos separados `create` e `update`: um ID existente nunca é sobrescrito por uma criação.
- IDs ASCII limitados a 64 caracteres; endpoints diferentes e existentes no inventário/blueprint padrão.
- Comprimento numérico finito entre 0 e 10000 km; fidelidade nominal finita entre 0 e 1; booleanos não são aceitos como números.
- `active` deve ser booleano; não é uma medida de disponibilidade física.
- Apenas o workspace padrão `sandbox-line-3`, com no máximo 256 enlaces. São limites operacionais do editor, não limites físicos validados.
- Enlaces paralelos, inclusive com endpoints invertidos, são recusados. Este editor trabalha com um grafo simples não dirigido; não modela canais WDM paralelos.
- Atualizar endpoints, remover enlaces, salvar configuração idêntica ou alterar versão antiga é recusado.
- Campos extras, credenciais e alegações de medição não são aceitos. O parser também recusa chaves JSON duplicadas.

## Consistência e auditoria

A operação usa `BEGIN IMMEDIATE` no mesmo store SQLite dos nós. Verificação de versão, configuração, incremento de versão e evento são uma transação única. Gravações concorrentes de nós e enlaces compartilham esse controle; uma versão antiga falha sem sobrescrever a nova.

Os eventos `simulated_link_created` e `simulated_link_updated` registram a configuração anterior e posterior e a ausência de atuação física. Resumos das mudanças aparecem em EVENTS. Falha ao gravar o evento desfaz a alteração de topologia.

A versão do formulário fica fixa durante a edição: atualizações periódicas não substituem silenciosamente a base. UNCONFIRMED não afirma rollback caso a gravação tenha ocorrido, mas a confirmação tenha falhado. Não há repetição automática.

O snapshot transporta ID, comprimento, fidelidade nominal e habilitação em campos tipados. O editor usa esses números originais para carregar e confirmar valores, nunca o texto arredondado da tabela. A confirmação também é processada se o usuário navegar para outra tela durante a execução assíncrona.

## Significado científico e limites

Cadastrar um enlace cria **configuração simulada**, não emaranhamento. A fidelidade nominal é uma hipótese de entrada, não medição, previsão do modelo WDM ou evidência da tese. QBER/SKR não são inventados nem copiados de outra fonte. Alterar comprimento/fidelidade não recalcula automaticamente pares já armazenados.

O grafo mostra conexões configuradas. A apresentação adota posições lógicas circulares quando há mais nós/enlaces que a linha padrão, para evitar sobreposição de caminhos colineares; não fornece coordenadas geográficas. A matriz explicita que seus valores são parâmetros nominais de enlaces diretos, sem inferir fidelidade multissalto.

O painel de métricas da topologia tem rolagem própria em janelas pequenas, preservando o tamanho da fonte e o conteúdo dos cartões.

Habilitar/desabilitar um enlace altera o indicador e a apresentação; não executa falha física ou recuperação. Redes desconectadas são permitidas para configuração: não há nesta entrega teste de viabilidade de um serviço entre dois usuários.

O cenário demonstrativo continua isolado e não executa automaticamente a rede editada. O replay WDM permanece no seu caminho de avaliação anterior. Nenhum driver, conexão de equipamento ou rota HTTP de escrita foi adicionado.

## Verificação

`tests/test_console_link_editor.py` cobre contratos inválidos, duplicidades, endpoints desconhecidos, atualizações sem mudanças, persistência após reabertura, valores sem arredondamento, concorrência entre nós/enlaces, rollback por falha de evento, limite de enlaces, confirmação na interface, versão fixa, fonte somente leitura, bloqueio por ocupação e fluxo assíncrono da janela principal.

Relatórios JUnit e capturas nativas ficam em `outputs/software_validation_links_q03/`. Essa validação demonstra comportamento do software; não valida hardware ou a relação preditiva WDM–fidelidade quântica.

## Próximo incremento

Q03 continua parcial: faltam projetos nomeados, importação/exportação, remoção segura e edição de posições. Para transformar a configuração em experimento, o próximo passo recomendado é validar uma solicitação origem–destino contra uma versão congelada da topologia e executar um cenário explicitamente vinculado a ela, começando por um caminho/protocolo suportado. Isso deve declarar suas hipóteses e recusar recursos ainda não implementados.
