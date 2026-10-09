# ORION-Q — painel nativo de campanhas temporais

Data: 13/09/2026. Incremento de Q14/Q15, **simulado e local**. Reutiliza o [motor multisseed e seu contrato estatístico](orion_q_temporal_campaign.md), sem mudar suas hipóteses físicas, treinar modelos ou coletar dados medidos.

## Abrir e demonstrar

Na raiz do repositório, execute a console pelo ambiente Python com PySide6, Qiskit/Aer e as demais dependências do projeto:

```powershell
python -m quantum_autopilot.desktop --ui native --state-root outputs/campaign-ui-demo
```

Um diretório de estado novo permite usar o manifesto de demonstração, que espera o blueprint de três nós na versão zero. Não resete um workspace existente para fazer o exemplo caber: ajuste explicitamente IDs/versão do manifesto ou use um estado de demonstração separado. No ambiente local, o Python é `.\.audit-winvenv\Scripts\python.exe`.

1. Use a fonte **Existing service**, não o plug de telemetria medida nem a injeção visual sintética.
2. Abra **TOPOLOGY → TEMPORAL CAMPAIGNS**, na área LINKS.
3. Clique **LOAD MANIFEST** e selecione `experiments/configs/temporal_path_campaign_demo.json`, ou seu manifesto completo.
4. Em **MANIFEST REVIEW**, confira a prévia da fonte, enlaces nominais, IDs/versão e o manifesto completo: alvo, prazo, baseline, regimes e todas as sementes. A prévia é identificada como tal; o worker congela a topologia ao iniciar, com nova validação de versão.
5. Marque a confirmação de revisão e clique **RUN CAMPAIGN**.
6. Acompanhe a contagem de ensaios. Ao terminar, a aba **CONFIRMED COMPARISON** abre após leitura e verificação do recibo salvo.
7. Use **EXPORT JSON** para salvar o recibo completo em um arquivo novo. Arquivos existentes são recusados, mesmo após confirmação do seletor de arquivos do sistema.

A prévia do manifesto é somente leitura: não há valores experimentais ocultos ou adaptação automática para outra topologia. Desde 21/09, **EDIT MANIFEST** abre um rascunho editável interno, conforme a seção abaixo. Também é possível editar uma cópia do JSON externamente e carregá-la. Um novo manifesto aplicado invalida a confirmação anterior. Uma tentativa de carregar **arquivo** inválido continua limpando o estado anterior, impedindo executar inadvertidamente o manifesto antigo; o editor isolado tem a semântica de cancelamento/validação descrita abaixo.

O carregamento verifica estrutura, limites, chaves duplicadas e correspondência com a versão exibida. O backend confere novamente a versão persistida, o caminho, os papéis dos nós e os enlaces ativos. Se a configuração mudar depois da revisão, a execução pode ser recusada; não há retry silencioso em outra versão.

## Editor interno — atualização de 21/09/2026

1. Carregue um manifesto completo por **LOAD MANIFEST**, incluindo o exemplo distribuído com o aplicativo.
2. Abra **MANIFEST REVIEW → EDIT MANIFEST · IN-MEMORY DRAFT**. O botão requer fonte com execução permitida, solicitação carregada e nenhuma operação em andamento; um recibo histórico sem solicitação executável não habilita edição.
3. Edite o JSON completo: ID, caminho, versão, alvo, prazo, regimes e sementes permanecem explícitos. Não são preenchidos a partir de medições nem ajustados automaticamente.
4. Clique **VALIDATE / APPLY TO REVIEW**. Aplique somente quando desejar substituir a solicitação em memória. O editor fecha após validação; a prévia atualizada inclui hash e quantidade de ensaios.
5. Confira tudo novamente, marque a revisão e só então use **RUN CAMPAIGN**. Aplicar não envia worker, cria tentativa, salva arquivo ou altera o banco.

O contrato é compartilhado com o carregador de arquivos: até **64 KiB em UTF-8**, campos exatos, sem chaves duplicadas/NaN/Infinity, limites de regimes/sementes e orçamento do motor. Codificação inválida e JSON excessivamente aninhado são recusados. A versão/topologia deve corresponder à fonte atual; caminho, papéis, enlaces e versão persistida continuam sendo conferidos pelo backend ao executar. A validação da prévia não congela a topologia nem garante que a execução futura será aceita.

O rascunho é isolado. **CANCEL DRAFT**, Esc ou fechar descarta apenas as edições; JSON inválido permanece no editor com motivo de recusa e não substitui a solicitação carregada. Aplicação válida limpa a comparação anterior e desmarca a revisão, inclusive ao aplicar texto equivalente. O arquivo originalmente carregado e os recibos já persistidos não são modificados. Não há salvamento automático, recuperação de rascunho após fechar o aplicativo ou editor por campos nesta etapa. Depois de aplicar, use **SAVE MANIFEST AS** para guardar a configuração, conforme abaixo.

Trocar a fonte ou carregar outra solicitação enquanto o editor está aberto invalida sua aplicação; reabra após conferir a entrada. Perda da capacidade de execução ou operação em andamento também bloqueia aplicar. A checagem se repete no adaptador, não depende apenas do botão habilitado. O trabalho permanece local e simulado, sem acesso a equipamento.

Escolha explicitamente um **novo campaign_id** ao mudar um experimento concluído. O editor não decide se o ID já existe: a proteção de idempotência do backend continua recusando mesmo ID com parâmetros diferentes e identificando replay para a mesma solicitação. Não duplicar resultados como amostras independentes. Comparações após exploração de vários rascunhos continuam exploratórias; a interface não estabelece pré-registro nem novidade científica.

Testes: `tests/test_campaign_manifest_editor.py`; evidências em `outputs/software_validation_manifest_editor/`. Regressão desta atualização: **482 testes aprovados em 369,11 s**, incluindo 27 casos novos; rodada focada: 84 aprovados em 77,06 s (conjuntos sobrepostos, não somar). Capturas 640×500 e 1080×740 verificadas com tema/fontes reais. Validação manual em outro computador Windows permanece pendente.

## Salvar configuração — atualização de 21/09/2026

Após carregar ou aplicar uma edição, **MANIFEST REVIEW → SAVE MANIFEST AS · NEW JSON** permite escolher um arquivo novo. O conteúdo é o contrato completo da solicitação aplicada, em UTF-8, revalidado pelo mesmo parser de 64 KiB; não é o texto da prévia nem um rascunho ainda não aplicado. Use um destino local. **LOAD MANIFEST** reabre esse arquivo com a validação normal de fonte/topologia/versão e nova confirmação de revisão.

O diálogo de arquivo não concede autorização: fonte/contexto, identidade da solicitação, capacidade e ausência de operação ativa são conferidos após a escolha. Cancelar não faz nada. Uma gravação aceita ocorre fora da thread gráfica e bloqueia outras operações de campanha até finalizar; não inicia worker, cria tentativa ou escreve no diário. Arquivos existentes (inclusive o original) são recusados por criação exclusiva, mesmo que tenham surgido após a escolha. Não cria diretórios automaticamente.

Salvar não altera a solicitação, sua confirmação de revisão nem resultados já confirmados. Não requer marcar a revisão e não a marca por conta própria. Um histórico sem solicitação executável não habilita o botão. Trocar fonte após a aceitação não cancela a gravação já autorizada, mas o resultado tardio não é exibido como pertencente à nova fonte. Fechar o diálogo não cancela a gravação; o fechamento da aplicação aguarda sua conclusão.

**MANIFEST SAVED** confirma conclusão local da escrita; o hash exibido é da solicitação estruturada, não assinatura de autoria nem hash dos bytes do arquivo. Não promete durabilidade contra queda de energia. Falha de escrita/fechamento é sinalizada e pode deixar arquivo incompleto, que não é removido automaticamente; escolha outro destino. Não há backup da topologia, diário, modelos ou resultados no JSON, nem adaptação de IDs/versões ao reabrir. Configuração salva não é pré-registro científico e não torna resultados exploratórios confirmatórios.

Testes do editor/salvamento: **43 aprovados**, incluindo 16 casos novos de persistência da configuração, em `outputs/software_validation_manifest_save/corrected-tests.xml`. Capturas em 640×500 e 1080×740 inspecionadas. O fluxo salvar/reabrir também foi exercitado antes de enviar a solicitação ao worker Windows empacotado; isso não substitui aceite manual em outro computador.

## Comparação e layout

A tabela principal exibe, por regime, alvo atingido/total, probabilidade com Wilson pontual de 95%, timeouts, fidelidade média **condicionada ao swapping** com seu n, e média de pares gerados por ensaio. A segunda tabela exibe diferença em pontos percentuais contra o baseline declarado, p ajustado por Holm e decisão bilateral a 5%.

Significância não é sinônimo de ganho: a diferença pode ser negativa. Ausência de rejeição não demonstra equivalência. Os intervalos não incluem incerteza do modelo físico. Timeouts ficam no denominador da métrica primária e não viram fidelidade zero.

O rodapé permite selecionar/copiar ID, versão congelada, data UTC e hashes de fonte/registro. O JSON contém também contabilidade, tempos, matrizes, trilhas e todas as limitações; não foram duplicados todos esses detalhes no placar.

O painel reutiliza fontes, cores e tabelas da console. A área de resultados possui rolagem vertical própria; tabelas têm rolagem horizontal quando necessária, sem reduzir automaticamente a fonte para encaixar. Botões e progresso ficam fora dessa área. Mensagens longas têm tooltip completo. Não há novos menus laterais nem mudança nos modos GRAPH/GEO/MATRIX.

## Processo, confirmação e cancelamento

`CampaignJob` é o adaptador entre o painel e o domínio. O processo filho é o mesmo Python da aplicação, executando `quantum_autopilot.control_plane.temporal_campaign_worker`. O banco é obtido do serviço ativo; o manifesto não pode escolher um banco alternativo, destino remoto ou dispositivo. A comunicação usa pipes locais de stdin/stdout, sem HTTP, portas ou conexões com equipamentos.

O processo recebe a solicitação carregada e validada em memória, não relê um arquivo que possa ter mudado após a revisão. Eventos pequenos de progresso mostram somente ensaios concluídos. **100% de ensaios não é confirmação de persistência.** Após o processo terminar, uma tarefa de leitura verifica os hashes e a correspondência da solicitação inteira, incluindo sementes/parâmetros, no SQLite.

Uma execução de mesmo ID/manifesto pode recuperar o registro anterior: isso é identificado como **IDEMPOTENT REPLAY**, sem novos ensaios. Recibo com mesmo ID e parâmetros diferentes não confirma a solicitação atual. Falha no processo pode permitir recuperar um recibo anterior compatível, mas isso não é anunciado como um novo cálculo.

- **CANCEL** envia um pedido cooperativo, verificado antes de cada ensaio e imediatamente antes da inserção final. Não interrompe a operação interna do Aer no meio de um ensaio.
- Se o worker não encerrar em até dez segundos após o pedido, a aplicação solicita sua parada forçada. Continua lendo o banco para decidir o que efetivamente foi salvo; não presume rollback de um commit concluído.
- Se o processo reconhece cancelamento e não há recibo, o painel mostra **CANCELLED**. Se há recibo completo compatível, mostra **CONFIRMED**, mesmo que o pedido de cancelamento tenha chegado tarde. Parada anormal sem recibo fica **UNCONFIRMED**, não timeout quântico nem resultado estatístico.
- Esc/fechar apenas o diálogo o oculta, sem cancelar a campanha. A tarefa pertence à janela principal e continua ao navegar entre telas.
- Fechar a janela principal solicita cancelamento e mantém o loop gráfico ativo até o encerramento/readback. Não destrói deliberadamente um QProcess ainda em execução.
- Trocar a fonte limpa prévia, revisão e resultados e solicita cancelamento. Eventos tardios do trabalho anterior não são anexados à fonte nova. Um recibo eventualmente concluído permanece no banco de origem.

Leitura/verificação do recibo e exportação completa também ficam fora da thread gráfica. Uma exportação já aceita não é desfeita por fechar a janela ou trocar a fonte; aguarda-se seu término. Falha ao escrever pode deixar um arquivo incompleto, que não deve ser usado como recibo válido; o registro SQLite não é sobrescrito.

**Atualização de 14/09/2026:** a aba [ATTEMPTS](orion_q_campaign_attempts.md) registra persistentemente as tentativas da console, incluindo observações terminais de falha/cancelamento, separadas dos recibos científicos. Um registro OPEN não atesta que o processo continua vivo. Não há retomada parcial nem fila de várias campanhas simultâneas. Uma campanha por janela é permitida; o backend mantém a proteção de concorrência por ID entre processos/janelas.

## Limites e distribuição

O serviço local declara separadamente `run_temporal_campaign` para execução e `read_temporal_campaign_history` para consulta. O painel pode abrir somente para histórico quando não há suporte de worker. Telemetria medida, demonstração visual sintética e adaptadores sem essas capacidades continuam bloqueados; rótulos visuais não concedem autorização. A execução tem uma fronteira dedicada de processo e não é exposta como novo endpoint HTTP.

**Atualização de 14/09/2026:** o [pacote Windows com worker](orion_q_frozen_temporal_worker.md) foi construído e validado. Ele usa uma entrada interna do próprio executável e pipes Win32, sem Python externo nem segunda interface. Um launcher congelado que não declare esse suporte permanece bloqueado. Versões anteriores do `.exe` não adquirem a função automaticamente; extraia a distribuição nova completa. Não se usa `-m` no executável empacotado.

O avanço é de produto: experimento configurável, responsivo e auditável. Não acrescenta comprovação WDM→desempenho quântico real, dispositivo físico, autenticação instrumental ou novidade científica.

## Verificação

`tests/test_console_temporal_campaign.py` executa processos reais e cobre progresso, responsividade do loop gráfico, cancelamento antes do commit, replay após cancelamento, recibo de solicitação errada, versão desatualizada, troca de fonte, erro de inicialização do processo, exportação sem sobrescrita, navegação/fechamento e layout em 640×500 e 1080×740. A regressão inclui os testes anteriores da console, dos caminhos e da física.

Evidências locais: `outputs/software_validation_temporal_campaign_ui/`. As imagens de teste usam uma fixture explicitamente simulada de quatro regimes; não representam uma nova campanha científica.

Resultados desta entrega: **398 testes passaram** na regressão ampliada (`accepted-tests.xml`, 534,45 s). Após os ajustes finais, **56 testes específicos passaram** (`final-tests.xml`, 70,26 s), além de **2 verificações de layout** (`layout-tests.xml`, 8,11 s). Os conjuntos se sobrepõem; não somar esses números como testes únicos. Ruff passou nos módulos novos e testes. A inspeção visual identificou sobreposição na área de comparação em janela pequena; a área recebeu rolagem própria e foi renderizada novamente, sem reduzir a fonte. A captura de Topology em 960×640 verifica a entrada nova e a área de LINKS; não constitui auditoria completa das legendas antigas do grafo.

O empacotamento do worker foi concluído no [incremento Windows](orion_q_frozen_temporal_worker.md). Em 14/09/2026, foi adicionado o [histórico navegável/exportável por campanha](orion_q_temporal_history.md): abra **SAVED CAMPAIGNS → REFRESH**, navegue com **OLDER/NEWER**, selecione um registro e use **OPEN / VERIFY**. A consulta não reexecuta ensaios nem altera a versão atual. A verificação em máquina independente continua pendente. Nenhum desses pontos altera o protocolo científico; confirmação de hipóteses exige novos cenários/sementes reservados e, quando aplicável, simulação independente ou medições pareadas.
