# ORION-Q — diário operacional de tentativas

14/09/2026. Incremento de Q14 para a console nativa. Registra a execução do **software**, não amostras ou resultados quânticos. O motor físico e a estatística das campanhas permanecem inalterados.

## Utilização

Na fonte **Existing service**, abra **TOPOLOGY → TEMPORAL CAMPAIGNS → ATTEMPTS → REFRESH**. Em janelas pequenas, as setas da barra de abas permitem alcançar ATTEMPTS sem diminuir a fonte.

A tabela mostra data UTC da solicitação, ID da campanha, estado e ID único da tentativa. Selecione uma linha para consultar/copiar o JSON completo, incluindo hash da solicitação, versão esperada da topologia, código de saída observado, indicação de cancelamento e eventual hash de recibo. **OLDER/NEWER** navegam páginas de até 20 tentativas.

O JSON da tentativa é metadado operacional, não o recibo científico. Selecione uma linha e use **EXPORT ATTEMPT** para salvar essa tentativa. O botão geral **EXPORT JSON** continua exportando somente o recibo científico confirmado que estiver carregado; não exporta a linha selecionada em ATTEMPTS. Para localizar tentativas, use prefixo do ID e estado operacional; para consultar resultados, use **SAVED CAMPAIGNS → OPEN / VERIFY**.

## Filtros operacionais — atualização de 14–15/09/2026

Em **ATTEMPTS**, preencha **Campaign ID starts with...**, escolha o estado e pressione **Enter** no campo de texto ou **REFRESH**. Prefixo vazio e **ALL STATES**, sem período preenchido, consultam todas as tentativas. Prefixo e estado são combinados por AND; a busca é literal e diferencia maiúsculas/minúsculas. `%`, `_` e aspas não são operadores nem SQL executável. O prefixo aceita até 128 caracteres sem controles; não há busca por trecho intermediário ou expressão regular. O filtro por datas foi acrescentado em 19/09, conforme a seção seguinte.

Editar um filtro não executa consultas. Enquanto houver alterações não aplicadas, a nota **FILTERS NOT APPLIED** identifica que as linhas ainda pertencem à consulta anterior e os botões **OLDER**, **NEWER** e **EXPORT ATTEMPT** ficam desabilitados. Aplicar filtros volta à primeira página e limpa a seleção; resultado vazio remove os cursores anteriores. Restaurar exatamente os filtros aplicados permite usar novamente a página atual. Exportar continua exigindo releitura e o hash selecionado, inclusive depois de filtrar.

Cada página retorna até 20 correspondências, ordenadas pela sequência de inserção decrescente. OLDER/NEWER preservam os filtros aplicados; o backend recusa trocá-los durante navegação sem um REFRESH. O cursor evita incluir novas inserções acima da posição já percorrida ao avançar para páginas antigas, mas não congela o banco: tentativas OPEN podem ser concluídas e deixar de corresponder entre leituras. Portanto, voltar ou atualizar pode mostrar outra composição. OPEN não é prova de processo ativo e filtrar falhas não transforma tentativas em amostras científicas.

Os predicados são parametrizados e executados sobre os metadados JSON no SQLite existente; não há migração ou índice novo. Somente os registros retornados são conferidos por hash antes da exibição. Uma busca filtrada **não é uma auditoria integral do banco**: corrupção de um registro JSON bem formado que não corresponde à consulta pode ficar fora dela. JSON malformado não é descartado silenciosamente pelo predicado; quando alcançado na página, provoca recusa. A busca pode varrer muitos registros em um diário grande: a página limita o material retornado, não garante tempo constante da consulta.

A leitura ocorre fora da thread gráfica. Troca de fonte limpa filtros aplicados, rascunhos e seleção; respostas tardias não são anexadas à nova fonte. Consultar não altera os registros, recibos científicos ou a topologia, não reconcilia OPEN e não inicia processos de campanha. Não inclui contagens globais por estado ou pesquisa por UUID. A exportação em lote da página foi acrescentada em 19–20/09, conforme a seção abaixo.

Testes: `tests/test_attempt_filters.py`. Evidências: `outputs/software_validation_attempt_filters/`. Distribuição: `release/windows/5.38.0-attempt-filters-20260914/`. Validação em máquina Windows independente continua pendente.

Regressão ampliada deste incremento: **261 testes aprovados em 179,63 s** (`accepted-tests.xml`). A rodada focada aprovou 107 testes em 41,96 s (`tests.xml`), sobrepostos à regressão; não somar contagens. Capturas 640×500 e 1080×740 inspecionadas com tema/fontes reais.

## Período UTC e rolagem — atualização de 19/09/2026

Use **UTC FROM ≥** para a primeira data incluída e **UTC BEFORE <** para a data final excluída. Formato: `YYYY-MM-DD`. Por exemplo, FROM `2026-09-19` e BEFORE `2026-09-20` selecionam solicitações de todo o dia 19 em UTC, incluindo `23:59:59.999999`, mas não a meia-noite do dia 20. Os campos da UI são limitados a dez caracteres; não são campos de horário. Datas impossíveis, limites iguais ou intervalo invertido são recusados. Um campo vazio remove apenas aquele limite; ambos vazios removem o período.

A data consultada é **requested_at_utc**, registrada antes do despacho. Não é `finished_at_utc`, data do recibo ou data local. Prefixo, estado e datas são combinados por AND e aplicados juntos por Enter/REFRESH. Rascunhos de data também bloqueiam navegação e exportação. Uma recusa de intervalo mantém a última consulta aplicada e seu conteúdo, com aviso de que o rascunho não foi aplicado; nunca executa uma busca sem os limites recusados.

A consulta compara o prefixo de data ISO UTC produzido pelo escritor do diário, sem converter timestamps para ponto flutuante. Isso evita arredondar o último microssegundo do dia para o dia seguinte. Registros retornados têm o timestamp de solicitação validado como UTC explícito (`Z` ou `+00:00`), com segundos e até seis casas decimais. Um registro retornado inválido recusa a página. Registros fora da consulta não são uma auditoria integral; dados legados modificados externamente não são convertidos nem corrigidos automaticamente.

OLDER/NEWER mantêm os quatro filtros, e o backend recusa mudá-los durante a navegação sem REFRESH. Trocar fonte limpa datas aplicadas e rascunhos; resultados tardios são descartados. O período não congela a base e não muda as regras de leitura/exportação existentes. Não há migração de banco, novo índice, agregação estatística, conversão automática de fuso ou reexecução científica.

Em janelas pequenas, role verticalmente **dentro da aba ATTEMPTS** para chegar ao JSON e aos botões. Lista e detalhes têm alturas mínimas para não se sobreporem. A navegação geral e os controles científicos continuam fora dessa rolagem.

Evidências: `outputs/software_validation_attempt_period/`; testes em `tests/test_attempt_period.py` e nas regressões existentes. Distribuição: `release/windows/5.38.0-attempt-period-20260919/`. Verificação manual em outro computador Windows permanece pendente.

Regressão final: **288 testes aprovados em 169,97 s** (`accepted-tests.xml`). Os 134 testes da primeira rodada e os quatro testes específicos de layout se sobrepõem à regressão; não somar. A inspeção visual identificou sobreposição em janela pequena que os primeiros testes não capturavam; a rolagem foi corrigida, os testes reforçados e as capturas finais de topo/rodapé inspecionadas.

## Exportação da página em ZIP — atualização de 19–20/09/2026

1. Em **ATTEMPTS**, aplique os filtros com Enter/REFRESH e navegue até a página desejada.
2. Role até **EXPORT PAGE ZIP · CURRENT PAGE ONLY**. Não é necessário selecionar uma linha: o lote inclui todos os registros da página atual, até 20 na UI, inclusive os que exigem rolagem para aparecer.
3. Escolha um arquivo ZIP novo, fora da pasta do programa, e aguarde **PAGE EXPORTED**. A mensagem informa quantidade, destino e hash do manifesto. As outras páginas da busca **não** são exportadas.

O ZIP contém `manifest.json` e um `attempt-<UUID>.json` por tentativa, na ordem da página. Cada tentativa conserva o esquema `orion.campaign_attempt.v1`, `record` e `record_sha256` da exportação individual. O manifesto usa `orion.campaign_attempt_page.v1` e contém filtros aplicados, horário de exportação UTC, quantidade, nomes dos membros, IDs, hashes dos registros e `page_only: true`. Ele é envolvido por `manifest` e `manifest_sha256`. Hashes cobrem conteúdo canônico, não os bytes formatados do JSON/ZIP; não são assinatura digital nem prova de autoria.

Antes de abrir o destino, todos os IDs/hashes selecionados são validados e relidos por **um único SELECT** no SQLite, garantindo a mesma leitura para o lote. Os registros passam pela validação estrita de esquema/hash da exportação externa e são conferidos contra os filtros declarados. Mudança, remoção, corrupção ou inconsistência de qualquer membro recusa o lote inteiro antes da criação do arquivo; atualize a página para tentar de novo. Todas as serializações também terminam antes de criar o ZIP. IDs duplicados e lotes vazios são recusados; a função de backend aceita até 50 registros, enquanto a UI exporta sua página de até 20.

O lote é um snapshot da leitura. Um estado que mude depois dela não altera o arquivo. O horário do manifesto registra a preparação da exportação, não um timestamp de aquisição quântica nem prova de estado mais recente. Não há junção automática com recibos científicos, auditoria integral da base ou transformação de falhas em amostras.

Fonte, filtros e conteúdo da página são conferidos novamente após o seletor de destino. Filtros não aplicados desabilitam o botão. Uma exportação já aceita pode terminar após troca de fonte; sua confirmação não é anexada à nova fonte. O trabalho fica fora da thread gráfica; fechar a aplicação aguarda a operação. Cancelar o seletor não inicia exportação. Não há cancelamento no meio da escrita.

Arquivos existentes nunca são sobrescritos. Falha de I/O ou queda pode deixar ZIP incompleto: não há promessa de gravação atômica/durabilidade. Uma nova tentativa exige outro nome; o programa não apaga silenciosamente arquivos parciais. Nomes internos são gerados a partir dos UUIDs validados, sem caminhos provenientes de mensagens de erro ou outros campos. Detalhes podem expor caminhos locais: revise antes de compartilhar.

Para conferir o lote diretamente, use **VERIFY FILE → CHOOSE / VERIFY PAGE ZIP**, acrescentado em 20/09 conforme a seção seguinte. O botão **CHOOSE / VERIFY FILE** permanece destinado ao JSON individual de até 32 KiB, não ao manifesto isolado. Não há importação/reconciliação em lote. A cópia operacional não é o pacote instalável do aplicativo nem uma exportação de resultados científicos.

Regressão desta etapa: **318 testes aprovados em 210,48 s**, `outputs/software_validation_attempt_batch/accepted-tests.xml`. As rodadas focadas de 162 testes (57,43 s) e três testes adicionais (6,04 s) se sobrepõem e não devem ser somadas. Capturas 640×500 e 1080×740 inspecionadas. Distribuição: `release/windows/5.38.0-attempt-batch-20260919/`. Teste manual em outra máquina Windows permanece pendente.

## Verificação direta do ZIP — atualização de 20/09/2026

Na fonte **Existing service**, abra **TEMPORAL CAMPAIGNS → VERIFY FILE → CHOOSE / VERIFY PAGE ZIP** e escolha um lote operacional exportado. Não é necessário extrair o arquivo, carregar uma página ou consultar o diário atual. Não selecione o ZIP de instalação do ORION-Q: são formatos e finalidades diferentes. A função independente é `quantum_autopilot.control_plane.attempt_archive.verify_attempt_archive(path)`.

O verificador lê no máximo **2 MiB + 1 byte** para detectar excesso antes de interpretar o ZIP. Aceita **1 a 50 tentativas**, mais `manifest.json`, e até **32 KiB descompactados por membro**, inclusive manifesto. Somente membros STORED ou DEFLATED são aceitos. Recusa arquivos cifrados, nomes inesperados, caminhos/pastas, tipos especiais como symlinks, nomes duplicados, membros extras/ausentes, manifesto incompatível, JSON com chaves duplicadas ou valores não finitos, CRC inválido e hashes divergentes. Cada registro passa pela mesma validação estrita usada para o JSON individual e precisa corresponder ao ID/hash do manifesto e aos filtros declarados. Um único erro recusa o lote inteiro; não existe aprovação parcial.

**PAGE ZIP VERIFIED** só aparece depois de todos os membros serem lidos e conferidos. A área de texto simples mostra o manifesto, não concatena os detalhes de até 50 registros. O resultado significa consistência de CRC, esquema, hashes e filtros da cópia lida; **não é autenticação, evidência científica ou importação**. Não verifica autoria, assinatura digital, completude da busca original, atualidade do estado, existência de recibos científicos citados ou vínculo com o diário atual. Editar registros e recalcular todos os hashes pode produzir outro ZIP consistente. Não é uma perícia de todos os bytes do contêiner: metadados, comentários e bytes anexos não são autenticados. OPEN não prova processo ativo.

Não há extração para disco, abertura de caminhos citados no conteúdo, escrita na entrada ou acesso ao SQLite. O recibo científico carregado e a topologia permanecem intactos. A UI exige capacidade de histórico e opera fora da thread gráfica. Nova verificação limpa a confirmação anterior; falha não reapresenta sucesso antigo. Cancelar o seletor não inicia leitura. Troca de fonte enquanto o seletor está aberto exige nova escolha; durante a leitura, descarta o resultado tardio. Fechar a aplicação aguarda a operação pendente. O limite de bytes não garante timeout de I/O: use arquivos locais disponíveis, não dispositivos especiais ou caminhos remotos.

Regressão ampliada: **388 testes aprovados em 268,63 s**, `outputs/software_validation_attempt_archive/accepted-tests.xml`. A rodada focada aprovou 158 em 36,54 s (`recheck-tests.xml`), sobrepostos à regressão; não somar. Os 70 novos casos estão em `tests/test_attempt_archive.py`. Capturas 640×500 e 1080×740 inspecionadas. A validação no ambiente de desenvolvimento não substitui o aceite em computador Windows independente.

## Exportação operacional — atualização de 14/09/2026

1. Em **ATTEMPTS**, clique **REFRESH**, selecione a tentativa e confira seu JSON.
2. Clique **EXPORT ATTEMPT** e escolha um arquivo novo, fora da pasta do programa. O nome sugerido inclui o UUID da tentativa.
3. Aguarde **ATTEMPT EXPORTED**. A nota mostra o caminho e o hash do registro; mensagens longas têm tooltip completo.

Antes de escrever, a operação relê o registro no SQLite, valida sua integridade e exige o mesmo hash selecionado. Se a tentativa tiver sido concluída/alterada ou removida desde a seleção, a exportação é recusada antes de criar o arquivo: atualize e selecione novamente. Troca de fonte ou substituição da linha enquanto o seletor de arquivo está aberto também exige nova seleção.

O arquivo contém exatamente `record` e `record_sha256`; `record.schema` é **orion.campaign_attempt.v1**, e `record.scope` identifica execução de software na console. O hash cobre o registro canônico, não os bytes do JSON formatado. Não é assinatura digital nem autenticação de operador. OPEN pode ser exportado e continua significando ausência de conclusão registrada, não prova de processo ativo.

A cópia representa o estado verificado no instante da leitura. Se a tentativa evoluir depois dessa leitura, o arquivo não muda; não há promessa de refletir o estado mais recente para sempre. O registro SQLite não é modificado pela exportação, e não se executam ensaios nem se altera o recibo científico carregado.

Leitura e escrita ocorrem fora da thread gráfica. É necessário ter tanto capacidade de histórico quanto de exportação local. Uma exportação já aceita pode terminar no destino escolhido após troca de fonte ou fechamento da janela; sua confirmação não é anexada à nova fonte. Fechar o seletor sem escolher um arquivo não inicia exportação. Não há cancelamento de escrita no meio do arquivo.

Arquivos existentes são recusados por criação exclusiva, inclusive se o seletor do sistema permitir confirmar substituição. A serialização acontece antes de abrir o destino. Falha de escrita ou queda do sistema ainda pode deixar um arquivo incompleto; não há promessa de gravação atômica/durabilidade contra falta de energia. Nesse caso, preserve o diário e use outro nome para tentar novamente. Os detalhes podem conter caminhos locais e mensagens de erro; revise-os antes de compartilhar.

Para lotes da página atual, use EXPORT PAGE ZIP conforme a seção acima. Não há exportação automática de todas as páginas, reimportação pela UI, assinatura digital ou transformação desse JSON em evidência quântica.

## Verificação de arquivo externo — atualização de 14/09/2026

Na mesma janela **TEMPORAL CAMPAIGNS**, abra **VERIFY FILE → CHOOSE / VERIFY FILE** e escolha um JSON operacional exportado. A aba exige a capacidade de histórico da fonte atual, mas não precisa carregar uma página nem acessar seu SQLite. A função independente `quantum_autopilot.control_plane.attempt_file.verify_attempt_file(path)` também pode ser usada no código, sem instanciar um banco ou uma interface.

O resultado **SCHEMA + RECORD HASH VERIFIED** significa apenas que a cópia lida passou pelas regras do esquema operacional e pelo hash canônico. A UI explicita **NOT AUTHENTICATED · NOT A SCIENTIFIC RECEIPT · NOT IMPORTED**. Não comprova autoria, vínculo com o diário atual, existência de um recibo mencionado, execução física, validade científica ou estado atual de um processo. Alguém que editar o conteúdo e recalcular seu hash pode produzir um arquivo consistente. OPEN continua sem prova de liveness.

A leitura tem limite de **32 KiB** (no máximo 32769 bytes lidos para detectar excesso), aceita UTF-8 com ou sem BOM e recusa JSON truncado, campos duplicados em qualquer nível, números não finitos, excesso de tamanho, campos desconhecidos, tipos inválidos, esquema diferente e hash divergente. Timestamps devem ser datas válidas com segundos e UTC explícito (`Z` ou `+00:00`), com até seis casas decimais. Não há inferência de duração nem imposição de ordem cronológica, pois relógios do sistema podem ser corrigidos entre início e fim. IDs seguem o formato ASCII de até 64 caracteres; detalhe tem até 2000 caracteres.

Estados terminais exigem timestamp final e código de saída inteiro. OPEN exige ausência desses valores e de cancelamento terminal. Somente CONFIRMED/REPLAYED/RECOVERED aceitam hash de recibo. A verificação não interpreta o código de saída como prova independente de sucesso e não abre arquivos ou endereços citados no JSON. Todo conteúdo exibido usa texto simples, não HTML executável.

A operação é assíncrona e somente leitura: não escreve o arquivo escolhido, não importa registros, não altera seleção/recibo científico e não inicia worker. Escolher outro arquivo limpa o resultado anterior antes da leitura; falhas não deixam uma confirmação antiga visível. Cancelar o seletor não inicia trabalho. Trocar a fonte no seletor exige nova escolha; trocar a fonte durante a leitura descarta o resultado tardio. Fechar a aplicação aguarda a operação pendente pelo mesmo mecanismo do diário. O limite de bytes não é um timeout de I/O: prefira arquivos locais disponíveis, não caminhos remotos ou dispositivos especiais.

Testes desta atualização: `tests/test_attempt_file.py`; evidências em `outputs/software_validation_attempt_file/`. Distribuição em `release/windows/5.38.0-attempt-file-20260914/`. Verificação manual em computador Windows independente continua pendente.

Regressão ampliada aprovada: **241 testes em 140,89 s** (`accepted-tests.xml`). A rodada focada aprovou 71 testes em 11,23 s (`recheck-tests.xml`), que se sobrepõem à regressão e não devem ser somados. A primeira execução teve erro no nome gerado para o caso de arquivo grande: excedia o limite de variável de ambiente do Windows usado pelo pytest. Os casos receberam nomes curtos e foram executados novamente. Capturas 640×500 e 1080×740 foram inspecionadas com tema/fontes reais.

## Abrir o recibo vinculado — atualização de 20/09/2026

Em **ATTEMPTS**, aplique os filtros, selecione uma tentativa **CONFIRMED**, **REPLAYED** ou **RECOVERED** e role até **OPEN LINKED RECEIPT · SELECTED ATTEMPT**. O botão exige capacidade de histórico, uma seleção e filtros aplicados; não exige autoridade para executar campanhas ou exportar. Tentativas sem hash de recibo registrado não habilitam a abertura. JSON/ZIP externo verificado não é importado nem ganha vínculo com o diário atual.

A abertura relê a tentativa e o recibo em **uma transação SQLite somente leitura**, usando o banco da fonte selecionada. A função `quantum_autopilot.control_plane.attempt_receipt.read_attempt_receipt(database_path, attempt_id, expected_sha256)` usa URI `mode=ro` e `query_only`, sem instanciar o store que inicializa esquemas e otimiza estatísticas. Não cria banco/tabelas nem executa manutenção. Hash selecionado divergente ou registro ausente/corrompido recusa a abertura. Não substitui silenciosamente a versão selecionada por outra: atualize a página.

Além da integridade individual, exige hash do recibo registrado, hash da solicitação completa, ID de campanha, topologia e versão esperada compatíveis. O recibo precisa declarar evidência simulada, nenhuma ação física e workspace não modificado. Mesmo havendo uma campanha com o mesmo ID, **OPEN, CANCELLED, FAILED_TO_START, FAILED e UNCONFIRMED não recebem um resultado por inferência**. Esta função não é reconciliação de processos e não muda estados do diário.

Após sucesso, abre **CONFIRMED COMPARISON**, reutilizando a apresentação histórica e a exportação científica existentes. A proveniência identifica UUID, estado e hash da tentativa e hash da solicitação. A solicitação anterior deixa de ser executável: para rodar outra campanha é necessário carregar/revisar um manifesto. Falhas limpam a comparação anterior, evitando apresentar um resultado antigo como se fosse a abertura recusada. Troca de fonte, cancelamento ou perda da capacidade durante a leitura impede anexar o resultado tardio; fechamento aguarda a operação.

**ASSOCIATION VERIFIED** significa apenas associação consistente na cópia lida, não autoria autenticada nem prova de que aquele despacho calculou novos ensaios. REPLAYED e RECOVERED podem apontar para o mesmo recibo de uma execução anterior; não contar tentativas como amostras científicas independentes. Os hashes não validam as hipóteses físicas nem resultados experimentais. A transação preserva uma visão coerente durante a leitura, não garante que o banco permanecerá igual depois. `EXPORT JSON` continua exportando o recibo científico original, sem inserir os metadados da tentativa nele.

Regressão ampliada: **433 testes aprovados em 282,78 s**, `outputs/software_validation_attempt_receipt/accepted-tests.xml`. Os 45 testes específicos de `tests/test_attempt_receipt.py` também passaram em 17,48 s (`final-focused-tests.xml`), sobrepostos à regressão; não somar. Capturas 640×500 e 1080×740 e comparação vinculada inspecionadas. A camada científica/modelos WDM permanece inalterada. Aceite manual em outro computador Windows continua pendente.

## Consultas sem inicialização do banco — atualização de 21/09/2026

O adaptador nativo de campanhas utiliza `ReadOnlyCampaignStore` para consultar índices e recibos de SAVED CAMPAIGNS, listar ATTEMPTS, reler a seleção para EXPORT ATTEMPT/EXPORT PAGE ZIP e confirmar o recibo após o encerramento do worker. OPEN LINKED RECEIPT compartilha a mesma conexão protegida, mantendo sua transação com duas consultas. Não há botão novo nesta etapa; são preservados os filtros, hashes, seleções e controles existentes.

O leitor não herda o inicializador do store de escrita: valida um caminho existente e abre SQLite por URI `mode=ro`, com `query_only=ON`. Não executa criação/migração de tabelas, `PRAGMA optimize` ou reparo. Se o arquivo desaparecer ou tiver esquema incompatível, a operação é recusada; não surge um banco vazio no lugar. `mode=ro` continua recusando escrita mesmo se `query_only` for desligado. As exportações gravam exclusivamente os arquivos de destino autorizados, mantendo as regras de não sobrescrita e de conferência do snapshot.

Não se usa `immutable=1`: novas consultas precisam observar commits posteriores e respeitar a coordenação SQLite/WAL. O vínculo tentativa–recibo mantém uma única transação, e a página exportada mantém seu SELECT único. Isso protege a consistência durante a leitura, não congela a base para sempre. O SQLite ainda coordena locks e arquivos auxiliares de WAL conforme seu modo; não há promessa de imutabilidade de toda a pasta. O timeout de bloqueio é de 10 s, não um prazo absoluto para I/O remoto; prefira o diário local da aplicação.

O escopo é o **gerenciador nativo de campanhas**. Inicialização normal do serviço, edição de topologias, execução científica autorizada, registro e finalização de tentativas continuam gravando por suas rotas próprias. As funções de domínio existentes conservam seus argumentos de store para compatibilidade; chamadas externas que instanciam `SimulatedTopologyStore` não recebem essa proteção automaticamente. A consulta não é carregamento arbitrário de bancos não confiáveis nem auditoria de segurança de todo o SQLite.

Testes: `tests/test_campaign_read_store.py`; evidências em `outputs/software_validation_campaign_readonly/`. Incluem preservação do arquivo, recusa efetiva de escrita, banco ausente/incompatível, commits concorrentes, exportações e execução real do worker com gravação da conclusão preservada. Nenhuma mudança no motor quântico, nos modelos WDM ou na interface visual.

Regressão ampliada: **455 testes aprovados em 311,94 s** (`accepted-tests.xml`). A rodada focada teve 85 aprovações em 46,58 s (`tests.xml`), sobrepostas à regressão; não somar. O arquivo novo contém 22 casos. Aceite manual em outro computador Windows permanece pendente.

## Estados e interpretação

| Estado | Significado |
| --- | --- |
| `OPEN` | Solicitação registrada antes do despacho, sem conclusão persistida; não atesta processo vivo nem falha |
| `CONFIRMED` | Processo terminou normalmente e anunciou um novo recibo, confirmado por leitura/verificação |
| `REPLAYED` | Processo confirmou reutilização do recibo de uma solicitação já concluída |
| `RECOVERED` | Há recibo verificado compatível, mas não confirmação de novo cálculo por esta execução |
| `CANCELLED` | Cancelamento observado e nenhum recibo completo compatível confirmado |
| `FAILED_TO_START` | Processo não iniciou e não foi possível recuperar um recibo compatível |
| `FAILED` | Processo retornou código de falha e nenhum recibo foi confirmado |
| `UNCONFIRMED` | Encerramento sem confirmação suficiente, por exemplo parada forçada ou falha de leitura |

O código de saída e os detalhes devem ser lidos junto ao estado. Exemplo: falha ao iniciar com recibo anterior compatível aparece como **RECOVERED**, com código -1; não apaga o recibo nem se apresenta como novo cálculo. A solicitação de cancelamento não desfaz um recibo já concluído.

Nenhum desses estados vira fidelidade zero, QBER, SKR, amostra estatística ou timeout quântico. Contagens de timeouts científicos continuam vindo exclusivamente dos ensaios da campanha confirmada. Não combinar tentativas repetidas de uma campanha como observações independentes.

## Persistência e isolamento

- Cada envio aceito pela console recebe UUID próprio. Dois envios do mesmo ID de campanha são duas tentativas; podem apontar para o mesmo recibo idempotente.
- O registro inicial é confirmado em SQLite, fora da thread gráfica, antes de chamar o processo. Se essa gravação falhar, o worker não é lançado. Uma falha do próprio banco pode impedir registrar a tentativa; a UI avisa essa limitação.
- A conclusão é gravada após o encerramento e a leitura do recibo. A operação de finalização é transacional e idempotente para os mesmos dados; uma conclusão conflitante não sobrescreve a anterior.
- Se o recibo científico foi confirmado mas o diário operacional não puder ser finalizado, a UI preserva o recibo e mostra **Operational journal NOT finalized**. A tentativa pode permanecer OPEN.
- Em encerramento abrupto, a aplicação não inventa uma conclusão na próxima abertura. OPEN pode significar trabalho em outro processo, falha antes do despacho ou ausência da gravação final. Não há reconciliação automática, heartbeat de processo ou prova de liveness neste incremento.
- `cancel_requested` registra a observação capturada no encerramento, não um log de cada clique. Pedido seguido de queda abrupta pode não estar persistido separadamente. Eventos de progresso por ensaio também não são journalizados aqui.
- Troca de fonte limpa a visualização. A finalização de uma tentativa em andamento continua no banco da fonte original; resultados tardios não são anexados à nova entrada. Leitura de ATTEMPTS usa a capacidade de histórico do serviço local; entradas medidas/sintéticas visuais não recebem essa autoridade.

A tabela `temporal_campaign_attempts` é criada de forma aditiva no mesmo SQLite. Não remove nem altera topologias ou recibos anteriores. Não há preenchimento retroativo: campanhas executadas antes da atualização podem existir sem tentativas registradas. O escopo é **despacho pela console nativa**; CLI e chamadas diretas do motor não são automaticamente registradas. Manifestos inválidos recusados antes do despacho também não são tentativas de execução.

Cada página verifica os hashes dos metadados antes de exibir estados. Corrupção recusa a página, sem reconstruir silenciosamente os dados. Os hashes são locais, não assinaturas digitais: não autenticam operador/dispositivo nem protegem contra quem pode alterar o banco e recalcular hashes.

## Verificação

`tests/test_campaign_attempts.py` cobre persistência após reabertura, IDs distintos, finalização idempotente, recusa de reescrita, limites de estado/recibo, paginação, integridade, execução/replay reais, falha de inicialização, cancelamento antes do despacho, falha do diário, troca de fonte durante registro e layout.

Evidências desta etapa: `outputs/software_validation_campaign_attempts/`. A distribuição fica em `release/windows/5.38.0-campaign-attempts-20260914/`. Os testes são locais com simulações/injeção controlada; verificação manual em outro computador Windows continua pendente.

Regressão ampliada: **170 testes aprovados** em 130,56 s (`accepted-tests.xml`). A verificação isolada de layout também passou (`layout-tests.xml`, 5,43 s); não somar como testes únicos. A primeira captura da fixture não havia instalado o tema/fontes do aplicativo: a fixture foi corrigida e renderizada novamente. As novas capturas usam o tema real. Ruff aprovou os módulos novos/alterados.

Na atualização de exportação individual, `tests/test_attempt_export.py` acrescenta cobertura de seleção, capacidade de exportação, alterações/corrupção/remoção após seleção, troca de fonte no seletor e durante a escrita, recusa de sobrescrita e layout em 640×500 e 1080×740. A regressão ampliada desta atualização aprovou **183 testes em 138,37 s**; são uma nova execução, não 183 testes adicionais aos 170 anteriores. Evidências: `outputs/software_validation_attempt_export/accepted-tests.xml`. Distribuição desta atualização: `release/windows/5.38.0-attempt-export-20260914/`. A validação em máquina Windows independente permanece pendente.
