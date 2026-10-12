# OrionQ 5.63.0 — controlador portável, rede mista e terminal ao vivo

Data: 2026-10-11. Escopo: qualificação computacional dos contratos de controle,
aquisição e apresentação. A bancada Swabian não está conectada e o SDK nativo
não foi executado. N01 é explicitamente emulado. Não se atribui TRL, integração
física, chaves certificadas ou desempenho de FPGA a estes resultados.

## Alterações entregues

O serviço mantém roteamento, reservas, configuração, persistência e autorização
fora do desktop. A API HTTPS exige TLS 1.3/mTLS, identidade provisionada e
permissões por função/recurso. O perfil sem interface instala `orion-core` e
`orion-operator`, sem Qt, login interativo Windows ou DPAPI. O perfil desktop
existente continua disponível; a interface não controla o relógio do serviço.

O operador terminal acrescenta oito visões: resumo, enlaces, nós, satélites,
reservas, auditoria, dados estruturados e controlador misto. Gráficos compactos
usam as séries publicadas pelo backend; lacunas e dados obsoletos permanecem
explícitos. Housekeeping e Science são consultáveis nos detalhes do satélite.
Teclado, seleção, rolagem, pausa e redimensionamento não aguardam o GET de rede.
Um worker mantém uma única requisição/resultado pendente. Pausar a tela não
pausa o processamento científico. O terminal não renderiza o globo 3D.

Os contratos neutros de observação preservam sessões, configuração, canais,
sequência e tempos inteiros em ps. Fonte e adaptador são auditados, mas não
entram na política de roteamento. A etiqueta `physical` de uma fonte
autenticada, por si só, não atesta equipamento ou calibração. O adaptador
físico continua exigindo configuração verificada, autoridade e relógio válidos.

O pipeline comum normaliza eventos, valida o bloco, atravessa a ABI PS/PL e o
DMA emulado, calcula a correlação, registra o conteúdo e publica um recibo.
CPU e PL emulada usam implementações distintas e comparam métricas. O formato
é um payload da aplicação; não implementa descritores AMD, driver AXI, RTL,
interrupções físicas ou acionamento de portas. Atuação física permanece bloqueada.

Contratos e execução:

- [Serviço Linux e identidade](../product/orionq_linux_service.md).
- [Operação e monitor terminal](../product/orionq_terminal_operator.md).
- [Rede mista e matriz dos componentes](../product/orionq_mixed_controller.md).
- [ABI, propriedade de buffers e processamento PS/PL](../architecture/orionq_pspl_contract.md).

## Evidências Windows e SeQUeNCe nativo

| Verificação | Evidência | Resultado e limite |
| --- | --- | --- |
| Contratos, controlador, API e artefato instalado | [JUnit](../../outputs/mixed_controller_20261011/core-fixed-qualified.xml) | 41 testes aprovados; inclui exclusão entre processos, reinício, expiração, autorização por nó e configuração verificada |
| PS/PL, processamento, captura e observações | [JUnit](../../outputs/mixed_network_20261010/processing-capture-win2.xml) | 81 testes aprovados; 200 blocos contínuos conservaram eventos e pares de coincidência; DMA/PL emulados |
| Normalização e execução SeQUeNCe | [JUnit](../../outputs/mixed_nodes_adapter_20261011/adapter-final.xml) | 32 testes aprovados; conservação, reprodução, perda óptica e caso de quantização com fibra de 1000,011 m |
| Operador e monitor | [JUnit inicial](../../outputs/terminal_operator_20261010/terminal-live-mixed-final.xml), [detalhes](../../outputs/terminal_operator_20261010/details-output-final.xml), [cancelamento](../../outputs/terminal_operator_20261010/drain-final.xml) | 50 casos distintos aprovados no Windows; o caso adicional PTY requer Linux. Rechecagens não são somadas como novos casos |
| Interface Qt remota | [JUnit final](../../outputs/background_service_20261011/renewal/remote-qt-win.xml) | 15 testes aprovados no Windows em 158,48 s sobre a renovação final; conexão TLS real, navegação/login, snapshots, alterações autorizadas, revogação e encerramento dos workers locais |
| Onze nós, processamento e API mTLS | [Campanha v2](../../outputs/mixed_controller_20261011/campaign-pipeline-v2/report.json) | Nove critérios aprovados, 1970 observações, 11 comparações CPU/PL emulada e métricas calculadas publicadas pelo serviço |

O SeQUeNCe foi executado sem importações OrionQ ou shims, na revisão
`3133c66d80a898c6c3cc9af9eec3fa94fdbe2233`. Dez nós, N02–N11, formam um
anel com canais clássicos e quânticos separados. Photon, QuantumChannel,
recepção e Detector são executados pelo simulador; recibos clássicos retornam
pela ClassicalChannel. Não há protocolo de entrelaçamento ou QKD nesse ensaio.

| Evento nativo | Quantidade |
| --- | ---: |
| Envios quânticos | 1280 |
| Recepções quânticas | 1222 |
| Cliques do detector, incluindo ruído modelado | 973 |
| Recibos clássicos recebidos | 964 |
| Observações de recursos | 10 |

Windows e Linux produziram os mesmos bytes do registro nativo, SHA-256
`d1fc8027a8a613a5b829f44e130f0ad9d8f34a59828ed66e7a774c16ede6f109`.
As contagens de cliques e recibos não são tomadas como contagens de pares.
A [comparação e execução Linux](../../outputs/mixed_nodes_adapter_20261011/linux-sequence-native-v2/cross-platform-comparison.json)
preserva versões, dependências, semente e hashes. O checkout original
`C:\SeQUeNCe` permaneceu intacto.

N01 conserva 267 tags geradas pelo adaptador emulado. A campanha não inventa
slots ou recibos clássicos para esse nó: a rota N01→N11 continua bloqueada.
Os dez nós nativos obtêm e liberam reservas de controle compatíveis. Essas
reservas não estabelecem entrelaçamento; pedidos quânticos sem memória/BSM
continuam bloqueados e `quantum_result` permanece nulo.

## Falhas reproduzidas e corrigidas

| Falha | Correção e comprovação |
| --- | --- |
| Heartbeats mantinham uma reserva apesar de recursos/canais antigos | Validade de recurso e canal independente; reserva ativa invalidada ao expirar qualquer dependência |
| Tipos JSON inválidos em enums causavam TypeError | Validação de tipo antes de aplicar ou persistir; API retorna rejeição de schema |
| `fault.reason=ready` podia transformar um diagnóstico em autorização | Estado operacional fixo `fault`, motivo separado e impossibilidade de ressuscitar a sessão |
| Quantização do clique podia aparentar regressão no instante de outro callback nativo | Horizonte causal monotônico separado de `callback_at_ps`; timestamp quantizado e instante do callback original preservados |

A [revisão independente](../../outputs/mixed_nodes_adapter_20261011/independent-review.json)
guarda reproduções antes/depois e hashes revisados. Os primeiros corpora
também conservam erros de fixture/importação, expectativa de HTTP e ACL de
temporários; somente os corpora finais são apresentados como aprovados.

## Qualificação Linux e terminal integrado

A [qualificação do pacote instalado](../../outputs/portable_core_linux_20261011/report.json)
passou **327 testes, sem falhas, erros ou skips**, em 45,88 s de pytest.
Executou no Ubuntu 24.04.5 LTS/WSL2, kernel
`6.18.33.2-microsoft-standard-WSL2`, Python 3.12.3, x86-64, 16 CPUs lógicas,
UID/GID 10001, diretório privado 0700 e filesystem nativo `ext2/ext3`.
As [versões instaladas](../../outputs/portable_core_linux_20261011/dependency-freeze.log)
e [contagens por arquivo](../../outputs/portable_core_linux_20261011/test-counts-by-file.csv)
estão preservadas. O JUnit inclui o PTY Linux real e as execuções nativas
SeQUeNCe; os 327 casos não são somados aos corpora Windows como casos únicos.

O [ensaio CLI separado](../../outputs/portable_core_linux_20261011/cli-smoke.json)
comprova o relógio próprio do serviço: tick 2→3 depois de desconectar o
cliente, TLS 1.3/mTLS, SIGTERM com saída 0 em 0,251 s e ausência dos segredos
de fixture nos logs. O [artefato instalado](../../outputs/portable_core_linux_20261011/installed-core-report.json)
executa a física/rede sem GUI, ML ou autoridade Windows. Não houve Docker.

A [integridade após execução](../../outputs/portable_core_linux_20261011/after-staging-integrity.json)
conserva 485/489 hashes do staging e valida quatro alterações esperadas de
metadados `egg-info` produzidas pela instalação. Os 488 arquivos do checkout,
12 recursos e 249 fontes SeQUeNCe de origem/cópia verificados ficaram
idênticos. Não se apresenta `egg-info` gerado pelo build como alteração de
algoritmo científico.

A [campanha mista Linux](../../outputs/portable_core_linux_20261011/mixed-campaign-linux/report.json)
executou o Python instalado e o repositório staged: nove critérios passaram,
com onze comparações CPU/PL emulada, dez reservas de controle e o mesmo
bloqueio N01/entrelaçamento. Certificados, chaves, cofre e estado privado não
foram publicados. A tentativa inicial de wrapper com argumento incorreto
foi recusada pelo parser antes de executar; o comando correto foi registrado.

A [campanha suplementar do terminal](../../outputs/portable_core_linux_20261011/terminal-live-linux/report.json)
passou em 38,07 s com fonte simulada, semente 17 e topologia completa de
76 nós, 136 enlaces e 11 satélites. Seis quadros consultados sem `step`
receberam tick 2→3, histórico dos enlaces 70→105 amostras e séries de
protocolos 714→968 amostras, em 261 séries. As
[capturas textuais](../../outputs/portable_core_linux_20261011/terminal-live-linux/report.txt)
são projeções limitadas das viewports; não alegam leitura integral de cada
campo de satélite.

| Medida/critério | Resultado |
| --- | --- |
| Layout textual, 57 renderizações | p95 1,759 ms |
| GET autenticado, seis consultas | p95 1178,07 ms |
| Pausa da apresentação | Quadro retido; núcleo avançou tick 4→6 |
| Backpressure | 100 pedidos adicionais recusados; máximo uma leitura em execução |
| Certificado do operador revogado | HTTP 401 e monitor explicitamente STALE |
| Cliente anônimo | Acesso recusado |
| Troca de operador sem reinício | Novo operador recebeu tick 8 após tick 6, com identidade própria |
| Auditoria | 214 registros válidos, distinguindo serviço e operadores |
| Parada do processo | SIGTERM, saída 0 em 0,397 s |
| Conteúdo publicado | Sem os segredos de fixture nos logs/capturas |

Renderização rápida não implica processamento científico a cada segundo.
O período nominal é 1 s, mas consultas e passos podem atrasar; o núcleo
preserva ordem e resultados, sem gerar amostras intermediárias para a tela.
Essa campanha curta não fecha G05, não mede GPU nem demonstra estabilidade
prolongada. O [manifesto suplementar](../../outputs/portable_core_linux_20261011/supplement-publication-manifest.json)
registra o runner adicional e os imports reais do wheel instalado, separados
do freeze original.

## Execução sem usuário conectado

O processo operacional é `orion-core serve`, com identidade própria de
serviço e relógio independente. Login, logout, encerramento da GUI ou saída
do monitor não iniciam nem encerram esse processo. A sessão humana autoriza
as consultas e alterações e fornece a identidade auditada da ação; não é a
credencial do relógio ou da aquisição. Revogar um operador deve interromper
seu acesso, preservando a autoridade separada do serviço. Revogar a própria
identidade do serviço deve bloquear o processamento de forma segura.

O core habilita renovação automática somente da sua identidade de processo,
usando o binding e a credencial protegida próprios. A sessão e a geração da
concessão original são revalidadas antes e depois da emissão. Revogação,
mudança de permissões, expiração da identidade ou falha da credencial deixam
o processo bloqueado até autenticação explícita. Contextos humanos ou anônimos
não entram nesse caminho. O relógio mantém a sessão durante cadência longa e
pausas exclusivas, sem acrescentar ticks científicos. Autoridade de equipamento
continua presa à sessão verificada original; não é substituída nem estendida
silenciosamente por uma renovação do processo.

Na GUI remota, voltar ao login altera o estado de apresentação; fechar a
janela encerra polling/executores locais, sem endpoint de desligamento do
core. O modo local histórico de demonstração permanece uma execução
embutida no desktop e não equivale a esse serviço autônomo. Para operação
contínua, GUI e terminal devem conectar ao núcleo independente. A instalação
em segundo plano e as regressões adicionais estão documentadas no
[guia de serviço autônomo](../product/orionq_background_service.md).

O [suplemento de continuidade e unidade Linux](../../outputs/background_service_20261011/supplement-linux.json)
passou **12 testes adicionais, sem skips**, incluindo ausência de conexão
humana, saída/revogação de Alice e chegada de Bob. Alterações de configuração
foram auditadas como `operator-alice`/`operator-bob`; os passos automáticos
foram auditados exclusivamente como `orion-core`. O teste também passou no
[Windows](../../outputs/background_service_20261011/sessions-win-qualified.xml).

A [unidade systemd](../../packaging/core/systemd/orionq-core.service) prepara
partida em `multi-user.target`, conta sem privilégios, credenciais externas,
precheck, estado durável e parada/restart limitados. O parser systemd 255
aceitou a gramática com substituição de executável somente para o teste.
Verificar a unidade original reportou o executável `/opt/orionq/venv/bin/python`
ausente: esse destino de implantação não foi instalado. Nenhum serviço foi
instalado, habilitado ou iniciado no host, e boot real ainda precisa ser
qualificado. A validade da identidade de serviço e sua rotação são dependências
operacionais explícitas; perder autoridade bloqueia processamento sem usar
a sessão de um operador como substituta.

## Qualificação final da execução contínua

Uma nova [execução Linux do wheel instalado](../../outputs/portable_core_linux_20261011/renewal-final/report.json)
incorporou a renovação, a continuidade sem usuários e a unidade de serviço:
**360 testes aprovados, sem falhas, erros ou skips**, em 67,63 s de pytest.
Ubuntu/WSL, Python 3.12.3, UID/GID 10001 e filesystem nativo foram mantidos.
Os três módulos de produção foram importados do `site-packages` do novo wheel,
conforme a [prova de fontes instaladas](../../outputs/portable_core_linux_20261011/renewal-final/renewal-installed-source-proof.json).
A campanha anterior de 327 casos permanece íntegra como histórico e não é
somada à nova campanha como testes distintos.

| Verificação final | Resultado e evidência |
| --- | --- |
| Pacote Linux e contratos completos | [360 casos aprovados](../../outputs/portable_core_linux_20261011/renewal-final/tests.xml), incluindo 38 de identidade, 20 de relógio, dois de continuidade e 11 da unidade |
| Simulação atravessando a validade original sem cliente humano | Fonte configurada pelo backend; sessão do núcleo renovada, ticks continuaram e revogação do núcleo bloqueou a execução |
| Continuidade/unidade no Windows | [12 aprovados, um skip do parser Linux](../../outputs/background_service_20261011/renewal/background-win.xml), em 75,12 s |
| Interface Qt remota no Windows | [15 aprovados](../../outputs/background_service_20261011/renewal/remote-qt-win.xml), em 158,48 s |
| Revisão independente | [19 casos selecionados aprovados](../../outputs/background_service_20261011/renewal/review.json); subconjunto, não somado aos demais totais |
| CLI independente | [TLS 1.3/mTLS, tick 2→3 após desconexão e SIGTERM em 0,289 s](../../outputs/portable_core_linux_20261011/renewal-final/cli-smoke.json); segredos de fixture ausentes nos logs |
| Integridade | [488/492 arquivos de staging idênticos, quatro metadados de instalação revalidados](../../outputs/portable_core_linux_20261011/renewal-final/after-staging-integrity.json); 491 fontes do checkout, 13 recursos e 249 fontes SeQUeNCe de origem/cópia preservados |

A revisão reproduziu uma corrida entre emissão provisória e alteração do
grant: o acesso era bloqueado, mas uma sessão não retornada ficava órfã.
A correção remove sessões provisórias quando validação ou auditoria falham.
A reprodução independente passou de uma sessão órfã para zero, mantendo o
bloqueio e os hashes finais. Sessões de processo expiradas também são
coletadas sem renovar a sessão de equipamentos.

O [agregado Windows de identidade/relógio](../../outputs/process_identity_renewal_20261011/validation-summary.json)
teve 56 aprovados, uma falha de timeout e um skip durante campanhas paralelas.
O caso preexistente de cold-start não alcançou dois ticks no prazo de cinco
segundos. A repetição isolada passou sem alterar esse critério, resultando em
57 casos distintos aprovados e um skip entre os corpora. A causa de desempenho
não foi perfilada; não se apresenta esse agregado como execução monolítica
sem falhas. O Linux final acima aprovou todos os 360 casos numa execução.

Nenhum teste equivale a um ensaio contínuo de 24 horas: a regressão atravessa
uma sessão curta em tempo real e complementa testes de relógio controlado,
concorrência, revogação e limites de sessões. Boot real, renovação de
certificados e aquisição física continuam dependentes de qualificação própria.

## Condições para a bancada e implantação

Permanecem pendentes a Swabian real/SDK, identificação do equipamento,
canais e níveis qualificados, configuração lida de volta, relógios/alinhamento,
medição elétrica/óptica e canal clássico N01↔N02. O processamento de timetags
não fornece preparação, bases ou bits QKD; entrelaçamento requer fonte,
memórias, operações e evidência correspondentes. Origem emulada não pode ser
admitida como material operacional.

Linux x86-64 sob WSL é o alvo desta execução. Contêiner executado, ARM64,
placa PetaLinux, RTOS, barramento/PL física e outro computador não são
qualificados por ela. A API implementada é HTTPS; gRPC permanece uma opção
futura. O cliente CLI utiliza credenciais provisionadas externamente e não
exporta senhas do cofre; chaves privadas cifradas exigem leitor protegido.

As telas gráficas Windows foram preservadas; esta rodada não recompila o
EXE 5.61.0 nem valida Qt em um desktop Linux. O novo monitor sem GUI funciona
independentemente desse pacote. Os modelos ML continuam opcionais.

Uma integração computacional de componentes contribui para evidência TRL 4,
mas não encerra automaticamente a avaliação de maturidade. Ambiente
relevante, desempenho, representatividade e revisão identificada precisam
ser definidos para o subsistema avaliado. [Definições oficiais NASA](https://www.nasa.gov/pdf/458490main_TRL_Definitions.pdf).
