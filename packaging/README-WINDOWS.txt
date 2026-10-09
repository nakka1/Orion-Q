OrionQ - Console nativa Windows

O executavel e a pasta portatil agora se chamam OrionQ.exe e OrionQ.
A versao nos detalhes do executavel corresponde ao pyproject.toml do build.
Os dados continuam em %LOCALAPPDATA%\ORION-Q, sem migracao ou reset.
A identidade Windows e o bloqueio de instancia sao compartilhados com as
versoes anteriores. O instalador reutiliza a pasta da instalacao existente
e substitui o executavel antigo dentro dessa pasta. Cria novos atalhos OrionQ;
atalhos legados sao preservados e podem precisar de atualizacao manual.
Releases portateis anteriores permanecem preservados e separados.
As ferramentas aceitam leitura de pacotes legados, mas o empacotador atual
recusa reempacota-los com o nome ou a versao do novo produto.

IDENTIDADE WINDOWS: a nova logo da interface aparece no executavel, na janela
e na barra de tarefas. A interface permite uma instancia por maquina;
abrir novamente solicita a ativacao da janela existente, sem criar outra.
Feche as versoes antigas e substitua atalhos que ainda apontem para elas.
Workers, gateways e diagnosticos isolados continuam independentes.
Leia docs/product/orion_q_desktop_identity.md para comportamento e limites.

DESEMPENHO: geometrias GEO reutilizadas, matriz sob demanda e selecao em
worker com autorizacao e auditoria. Rodape COMPUTE informa passos mais
lentos que o periodo desejado; o tooltip mostra filas e frames agregados.
Eventos cientificos e registros brutos permanecem no backend. Consulte
docs/product/orion_q_performance.md e docs/results/orion_q_performance_20261007.md.
Diagnostico reproduzivel: --performance-test <diretorio-novo>
--performance-duration 20 --performance-soak 5 --performance-size 3440x1440.

SEGURANCA: identidade Windows e permissoes verificadas no backend.
API exige autenticacao inclusive em loopback. Auditoria HMAC e cofre DPAPI.
Para administrar identidades explicitamente, use --security-admin --root
<diretorio-de-seguranca> --report <arquivo.json> status/grant/revoke.
O diretorio nativo e <state-root>/.security. O laboratorio QKD exige as
identidades SAE/KME documentadas em docs/product/orion_q_security.md.
Nao ha provisionamento automatico de privilegios nem chaves certificadas.

GEO: o globo permite aproximacao ate zoom 5,8 antes de mudar para mapa-mundi.
Partindo do zoom 1, nove passos normais de scroll ainda ficam no globo;
o decimo abre o mapa, preservando a regiao focalizada.

Extraia a pasta inteira e execute OrionQ.exe.
KEY MANAGEMENT > CONFIGURAR BANCADA valida parametros e revisao antes de
executar. Aquisicao emulada, implementacao de referencia e replay ORION podem
ser selecionados no mesmo servico. Replay exige aquisicao registrada completa;
nao e replay nativo Swabian. SDK, licenca e FileWriter originais ainda faltam.
Uma falha de configuracao Swabian bloqueia aquisicao ate releitura verificada
e nova calibracao. A API local nao conecta hardware.
QKD + APLICACOES usa duas KMEs e duas aplicacoes em processos separados,
SQLite separado por KME e HTTPS/mTLS. Entrega incerta invalida o material;
nenhuma resposta perdida autoriza recolocar registros no buffer.
As chaves sao de teste, reproduziveis e sem certificacao de seguranca.
--lab-test pasta-nova verifica captura, replay, recuperacao e entrega local
no executavel. Leia docs/product/orion_q_laboratory.md,
orion_lab_acquisition_contracts.md e orion_q_lab_key_delivery.md.
O adaptador publico OMM/SGP4 integra previsoes orbitais ao contrato do backend;
nao recebe housekeeping ou deteccoes quanticas de um satelite real.
Leia docs/product/orion_q_public_ephemeris.md.
No modo institucional, PROTOCOLS > Demandas / Componentes mostra a auditoria
e os recibos de comunicacao. As 20 instituicoes sao endpoints combinaveis.
Quatro GEO simulados e quatro ISL explicitos passam por geometria e perdas.
O globo destaca os satelites e apresenta o solo de forma discreta.
Leia docs/product/orion_q_global_network.md e orion_q_inter_satellite_contract.md.
As sondas globais sao classicas; servicos quanticos sao limitados aos drivers
admitidos. Nao ha relay quantico orbital nem chaves certificadas.
A pasta _internal deve permanecer ao lado do executavel.
Comunicadores cadastra sete tipos fisicos, funcoes, tecnologias e dois canais.
GEO destaca somente Endpoint/Core. Selecionar satelite abre Terra e orbita.
Leia docs/product/orion_q_communicators_geo.md para contratos e limites.
Orbitas aceitam modelos hipoteticos, TLE/SGP4 ou vetores ECEF com cobertura.
Nao ha driver de hardware satelital qualificado nem atuacao fisica.
GEO tem maior tolerancia no scroll Mapa/Globo, legenda completa de simbolos
e linhas, e janela satelital com telemetria, pontes e canais do backend.
Linha satelital pontilhada indica cadastro; linha continua, emissao recebida.
A demonstracao inclui uma ponte MEO UFRJ-BSC com terminais hipoteticos.
Leia docs/product/orion_q_satellite_bridges.md para parametros e limites.
Esse teste BBM92 nao entrega chaves certificadas ou transporte de aplicacoes.
Graficos de conexao preservam as amostras recebidas ate sairem da tela.
A janela inicia em formato restaurado; use as bordas para redimensionar.
O zoom em monitores ultrawide usa limites estaveis, sem alternar as barras
de rolagem durante a abertura. A moldura nativa continua removida.
--startup-test report.json verifica a abertura normal e os controles de layout
em um diario novo e isolado. --startup-size 3440x1440 usa tamanho logico
explicito nesse diagnostico. --demo testa a mesma abertura com fonte simulada.
Configure simulated links solicita canais classico e quantico associados.
Eles sao ports locais de observacoes e registros opticos estatisticos;
nao ha transmissao fisica de qubits ou atuacao em equipamentos.
O terminal aceita input measured "C:\dados\acquisitions.json" para replay
bruto orion.channel-capture.v1, input simulated 19 quiet ou input none.
Leia docs/orion_q_paired_channels.md para o formato e as limitacoes.
Atualizacao 06/10/2026: arraste a divisoria acima de JOURNAL / TERMINAL
para ajustar sua altura. O botao X fecha o terminal; TERMINAL na barra
inferior ou Ctrl+J abre novamente. Mapas, graficos e tabelas usam o espaco
disponivel; historico e texto digitado sao preservados durante a sessao.
Na divisoria, as setas ajustam a altura; Shift aumenta o passo e Home
ou clique duplo restaura o tamanho inicial.
Incremento de 02/10/2026: FPGA > POLARISATION LOOP > COMPARE CONTROLLERS
compara 36 perfis simulados revisados e exporta JSON/Markdown/manifesto.
--diagnose-runtime mede ciclos simulados em diario novo, sem estado do operador.
Leia docs/orion_q_roadmap_usage.md para os comandos e limites.
O aceite em um segundo computador permanece pendente.
Esta distribuicao inclui o worker de campanhas temporais (14/09/2026).
TOPOLOGY > TEMPORAL CAMPAIGNS > LOAD MANIFEST abre a campanha.
Exemplo: examples/temporal_path_campaign_demo.json (workspace versao zero).
Se o workspace foi editado, ajuste explicitamente IDs/versao em uma copia;
nao resete dados existentes apenas para rodar o exemplo.
Revise os parametros, confirme a revisao e selecione RUN CAMPAIGN.
O mesmo executavel roda o worker sem outra interface ou Python instalado.
Progresso nao confirma persistencia: aguarde CONFIRMED. CANCEL solicita
cancelamento; um recibo ja concluido nao e desfeito. EXPORT JSON exige
um arquivo novo. Copie a pasta inteira ao mover o programa.
Leia docs/orion_q_temporal_campaign_ui.md e docs/orion_q_frozen_temporal_worker.md.
O historico esta em TEMPORAL CAMPAIGNS > SAVED CAMPAIGNS > REFRESH.
NEWER/OLDER navegam paginas de 20 registros. Use
o campo acima da tabela para pesquisar pelo inicio do ID (diferencia
maiusculas/minusculas). Enter ou REFRESH aplica; campo vazio busca todos.
Com uma linha selecionada, use
OPEN / VERIFY para conferir os hashes e abrir a comparacao salva.
EXPORT JSON salva o recibo verificado. Abrir historico nao executa ensaios
nem restaura a topologia antiga; RUN exige carregar e revisar um manifesto.
Somente campanhas concluidas com recibo aparecem. Consulte
docs/orion_q_temporal_history.md para limites e evidencias da atualizacao.
ATTEMPTS > REFRESH consulta tentativas de execucao, inclusive falhas e
cancelamentos, em um diario separado dos resultados cientificos.
OPEN significa ausencia de conclusao registrada, nao processo ativo.
Selecione uma tentativa e use EXPORT ATTEMPT para salvar seu JSON operacional.
O registro e relido e verificado antes de exportar. Se mudou desde a selecao,
atualize a lista. Arquivos existentes nao sao sobrescritos.
Leia docs/orion_q_campaign_attempts.md. EXPORT JSON permanece exclusivo
do recibo cientifico carregado, separado de EXPORT ATTEMPT.

No modo servico, a console opera uma topologia quantica SIMULADA com tres nos.
RUN SIMULATED SCENARIO executa o cenario de resiliencia em uma topologia
de demonstracao isolada; o workspace editado nao e reinicializado.
REPLAY WDM MODEL reproduz o modelo congelado em shadow.
DIAGNOSTIC SHADOW usa entradas fixas explicitamente identificadas.
EXPORT JSON salva evidencias sem assinatura digital.
A hipotese de predicao temporal WDM -> fidelidade real continua aberta.

O estado fica em %LOCALAPPDATA%\ORION-Q.
Os diarios antigos em NISQPredictiveTwin nao sao migrados nem removidos.
F11 alterna tela cheia; Esc retorna. Ctrl++ / Ctrl+- ajustam o zoom;
Ctrl+0 restaura o ajuste automatico. Janelas pequenas usam rolagem.
F6 ou DATA SOURCE troca a entrada na MESMA janela, mantendo as telas.
Entradas: demonstracao artificial, servico existente e telemetria medida JSON.
Trocar a entrada limpa valores anteriores e mantem diarios separados.
Perfis: reference (8 nos/12 links), load (32 nos/256 links/500 registros),
faults (falhas e recuperacao). O menu permite pausar, perder dados,
recuperar e testar o controlador simulado em diario exclusivo.
Nenhum resultado artificial e evidencia experimental.
O layout institucional v5.38 usa a navegacao lateral e a tipografia Chakra
Petch do novo modelo. GRAPH, GEO e MATRIX compartilham a mesma topologia.
GEO exige coordenadas declaradas; MATRIX mostra somente enlaces diretos e
nunca calcula fidelidade multi-hop. Perfil e detalhes de no abrem por clique.
NODES permite cadastro de nos simulados no servico local. EDGE CONTROL mostra somente
agregados do diario; nao ha telemetria por shot nem programacao de FPGA.
O backend deste pacote nao permite atuacao fisica. As integracoes IBM Quantum
foram descontinuadas; Qiskit/Aer continua disponivel para simulacao local, sem
contas, consultas ou execucao em servicos IBM.
A interface web anterior continua disponivel no codigo-fonte; este pacote
inclui a console nativa. Nao e necessario instalar Python ou Node.

Na aba ATTEMPTS, filtre pelo inicio do ID e pelo estado operacional.
UTC FROM inclui a primeira data; UTC BEFORE exclui a data final (YYYY-MM-DD).
O periodo usa a data UTC da solicitacao, nao a conclusao nem o horario local.
Em janela pequena, role dentro de ATTEMPTS para acessar detalhes e botoes.
Enter no campo ou REFRESH aplica os filtros; OLDER/NEWER preservam a consulta.
Alteracoes ainda nao aplicadas bloqueiam navegacao e EXPORT ATTEMPT.
OPEN nao comprova processo ativo. Filtros nao alteram resultados cientificos.
EXPORT PAGE ZIP exporta somente a pagina atual (ate 20 tentativas), com
manifest.json e um JSON por tentativa. Reconfere todos os hashes antes de criar
o ZIP e nao sobrescreve arquivos. Nao exporta todas as paginas da busca.
Para conferir o lote, use VERIFY FILE > CHOOSE / VERIFY PAGE ZIP.
Verifica manifesto, CRC, esquemas, hashes e correspondencia com os filtros,
sem extrair, importar ou acessar o diario. Limites: 2 MiB, 50 tentativas,
32 KiB por membro. Uma falha recusa o lote inteiro. Nao aceita ZIPs genericos.
Exibe o manifesto somente depois de verificar todos os membros.
Integridade nao autentica autoria nem comprova completude da busca original.
Prefira arquivos locais: limite de tamanho nao e timeout de leitura.

Em ATTEMPTS, selecione CONFIRMED, REPLAYED ou RECOVERED e use
OPEN LINKED RECEIPT para abrir o recibo cientifico registrado.
Reconfere tentativa, solicitacao e recibo em uma transacao somente leitura.
Ausencia, alteracao ou vinculo divergente recusa a abertura; nao procura outro
resultado apenas pelo ID. Abre a comparacao historica sem rodar ensaios.
OPEN e falhas sem recibo registrado nao recebem resultados automaticamente.
Recuperacao/replay nao sao novas amostras. Hash nao autentica autoria.
Consultas de campanhas/tentativas e releituras para exportacao usam agora
conexao SQLite explicitamente somente leitura, sem criar/otimizar o diario.
Banco ausente ou esquema incompativel gera recusa, nao reparo automatico.
Execucao autorizada e finalizacao de tentativas conservam a gravacao separada.

Depois de LOAD MANIFEST, use MANIFEST REVIEW > EDIT MANIFEST para editar
o JSON da campanha. A documentacao completa acompanha este pacote.

ACCESS & AUDIT > COMMAND CENTER mostra os ultimos 50 comandos locais de
cadastro de nos/configuracao de enlaces: ID, parametros, estados UTC e hash.
CONFIRMED requer resposta e releitura da topologia. UNKNOWN pode incluir
efeito ja gravado: confira a topologia antes de tentar novamente. Nao ha retry
automatico. REQUESTED/RUNNING apos reinicio nao provam processo em atividade.
Campanhas mantem seu historico separado. Nenhum desses controles opera hardware.
Guia: docs/orion_q_commands_q04.md. Os dois novos modelos do autor (QKD e rede)
aguardam artefatos/artigos para integracao; nao foram carregados neste incremento.

No editor de campanhas, edite
o JSON completo dentro da console. VALIDATE / APPLY TO REVIEW valida e aplica
somente em memoria; nao salva arquivo nem executa ensaios. Limite: 64 KiB.
Revise novamente os parametros e marque a confirmacao antes de RUN CAMPAIGN.
CANCEL DRAFT descarta o rascunho. JSON invalido nao substitui a solicitacao.
Troca de fonte/manifesto exige reabrir o editor. Nada ajusta IDs ou versoes
automaticamente; escolha outro campaign_id para um experimento alterado.
Depois de aplicar o rascunho, SAVE MANIFEST AS > NEW JSON salva a configuracao
em um arquivo novo, sem sobrescrever arquivos ou executar ensaios. Use um
destino local. LOAD MANIFEST reabre com validacao da fonte/versao e nova revisao.
O arquivo nao inclui topologia, resultados ou diario; nao e um pre-registro.
Cancelar a escolha nao salva. Nao ha autosave de rascunho nao aplicado.
Gravacao aceita continua ao trocar fonte ou fechar a janela; aguarde o fim.
Falha de gravacao pode deixar arquivo incompleto; confira o aviso e escolha
outro destino. A solicitacao carregada e os recibos continuam preservados.

Para conferir um JSON operacional exportado, abra TEMPORAL CAMPAIGNS >
VERIFY FILE > CHOOSE / VERIFY FILE na fonte Existing service. Limite: 32 KiB.
Verifica esquema e hash sem importar registros ou alterar o diario.
HASH VERIFIED nao autentica autoria nem valida resultados cientificos.
Essa verificacao de arquivo nao substitui o diagnostico da distribuicao abaixo.

Diagnostico da distribuicao:
Abra VALIDATE-OrionQ.cmd para verificar hashes, self-test e 20 rodadas
de stress em pasta isolada. Nao depende de liberar scripts PowerShell.
Leia docs/orion_q_windows_acceptance.md: a aprovacao automatica nao substitui
os testes manuais em outro computador. Nao desative protecoes do Windows.
Nao salve arquivos adicionais dentro da pasta do programa; exporte para fora.
O nome antigo VALIDATE-ORION-Q.cmd continua como atalho de compatibilidade.
O novo modo tambem aceita: OrionQ.exe --acceptance-test --timeout-seconds 3600 --output-dir pasta-nova
OrionQ.exe --self-test caminho-do-relatorio.json --state-root pasta-de-teste
OrionQ.exe --synthetic load --seed 1337
OrionQ.exe --telemetry-file caminho-do-arquivo.json
OrionQ.exe --stress-test pasta-de-relatorios --stress-rounds 20 --seed 1337

Falhas inesperadas retornam codigo 2 e criam orion-error-*.log na pasta
--state-root ou --stress-test, ou em %TEMP%\ORION-Q-diagnostics.

O plug de dados medidos e somente leitura, com contrato orion.telemetry.v1,
limite de 2 MB/32 nos/256 links e validade padrao de 30 segundos.
Exige timestamp com fuso, fonte, campanha, canal, dispositivo e identificador
da medicao. Nao inventa fidelidade, QBER ou SKR ausentes. O equipamento
precisa de um adaptador que publique o arquivo local; nao ha driver universal.
Proveniencia declarada nao equivale a autenticacao ou atestacao fisica.
Consulte docs/product/input_sources.md no repositorio.

Binario academico nao assinado digitalmente. O instalador preserva o diario
ao desinstalar. Leia docs/product/native_console.md no repositorio.

Modelos opcionais: EDGE CONTROL > OPTIONAL MODEL CATALOG.
Dez configuracoes offline; detector pickle bloqueado. OFF por padrao.
Os ZIPs externos modelo_qkd_ml.zip e qnet_network.zip NAO estao incluidos.
VERIFY LOCAL ZIP > OFFLINE SESSION > DEMO > RUN faz avaliacao simulada,
sem ativar politicas ou enviar comandos. Sem pesos o programa base funciona.
Guia atual: docs/orion_q_optional_models_windows.md.
Diagnostico separado: OrionQ.exe --optional-model-test --output-dir pasta-nova
Para avaliar os pesos, acrescente --qkd-zip arquivo.zip --qnet-zip arquivo.zip.

Tecnologias modulares (06/10/2026): TOPOLOGY > PROTOCOLOS acompanha 12 sessoes
em 66 nos e 71 enlaces. Inclui troca de entrelacamento, memorias, purificacao
BBPSSW, SNS-TF reduzido e satelite/Doppler. UNIJORGE foi retirada da demo;
IME militar foi acrescentado e IME-USP permanece. Instalacoes institucionais
sao referencias geograficas, sem conexao fisica OrionQ.
APLICAR confirma parametros no backend; CAPTURAR EVIDENCIA fixa a leitura;
EXPORTAR RECIBOS salva a cadeia de eventos. FONTE SIMULADA, ARQUIVO JSONL e
SEM FONTE substituem apenas a aquisicao e invalidam recursos anteriores.
Os canais sao distintos e associados; no software a comunicacao classica
utiliza uma fila causal local. Nao e um transporte TCP/UDP validado.
Os orcamentos de QKD sao condicionais: nao sao geradas chaves certificadas.
Longas distancias e falta de visibilidade satelital podem impedir entrega.
--self-test report.json inclui 12 verificacoes operacionais dessas familias
com estado isolado; leia modular-quantum/report.json junto aos recibos.
Pesquisa, contratos, premissas e instrucoes:
docs/product/orion_q_modular_quantum_protocols.md
Matriz de cada novo segmento e localizacoes verificadas:
docs/results/orion_q_modular_connection_matrix_20261006.md
docs/results/orion_q_modular_locations_20261006.md
