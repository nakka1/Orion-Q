# OrionQ 5.62 — separação do desktop e núcleo portável

Data: 2026-10-10. Escopo: fontes do serviço, cliente remoto e perfil de
implantação. A distribuição Windows 5.61 e seus resultados anteriores foram
preservados. Esta rodada não recompila o executável, não admite chaves emuladas
para uso operacional e não habilita atuação física.

## Resultado arquitetural

O perfil operacional headless usa uma identidade de serviço provisionada,
revogável e autenticada por credencial protegida, com cofre AES-GCM cuja chave
vem de um provedor externo. Não consulta a sessão Windows, SID/UID como
autoridade, nem DPAPI. O desktop remoto é um cliente HTTPS/mTLS: não cria motor,
runtime de segurança, cofre ou journal local. O processo do núcleo mantém o
relógio, a topologia, o processamento e a persistência quando a GUI desconecta.

| Área | Implementação nesta versão | Verificação aplicável |
|---|---|---|
| Identidade | `identity_profile=service`; provisionamento offline; sessão limitada e revogação | OS/DPAPI proibidos por sentinelas; ausência de credencial bloqueia; revogação antes da mutação |
| Segredos | Provedor explícito, chave AES-256 externa, envelopes autenticados/versionados e referências de rotação | Adulteração, configuração, permissões, corridas, exceções sem conteúdo secreto e escrita concorrente |
| Roteamento/controle | API e serviços existentes com autorização por função/recurso; núcleo autônomo | TLS real, pipeline de fontes existente, leitura sem avanço, operador permitido e auditor recusado |
| PKI | TLS 1.3 obrigatório; certificado/identidade/serviço/revogação verificados por requisição | Recusa anônima, papel falsificado e revogação em conexão persistente; runtime não lê a chave privada da CA |
| KME | Configuração de spawn carrega referências públicas ao perfil portável | Contrato Windows e ensaio Linux aprovado com duas KMEs/estados próprios, mTLS, consumo, reinício e revogação SAE |
| Desktop remoto | Workers limitados, última publicação marcada stale em falha, sem fallback local e sem retry de mutação incerta | Cliques reais, recibos duráveis, RUN PATH, exportação local, reconexão e GUI sem estado de serviço |
| GEO remoto | Snapshots do mesmo backend; seleção e dados independentes do renderizador | Todos os satélites/órbitas e seis trocas Mapa/Globo sem consultas extras ou renderização duplicada |
| Relógio | Uma thread por serviço; autorização fresca; handoff de recalibração; parada limitada | Continuidade sem observador, sobrecarga explícita, nenhuma fila de catch-up e bloqueio após revogação |
| Artefato headless | Wheel sem GUI/launcher/ML; Docker Linux, UID 10001 e montagens separadas | Construção/instalação real do wheel fora do checkout; execução do contêiner requer evidência própria |

O modo local antigo permanece uma composição distinta para preservar os
workspaces. O uso de DPAPI nesse modo não participa da identidade ou proteção
do serviço remoto. O novo cofre não reinterpreta silenciosamente um envelope
DPAPI: transferir o estado protegido de uma instalação existente exige
procedimento administrativo de migração, preservando checkpoints e consumo.

## Falhas reproduzidas e corrigidas

1. Um campo `node_id` decorativo podia estreitar o recurso de autorização de
   uma configuração global. A resolução agora considera a operação efetiva:
   somente verbos que operam naquele node/enlace recebem esse escopo. As
   configurações globais continuam exigindo permissão de workspace.
2. `export_technology_events` aceitava um destino de escrita no servidor. O
   perfil de serviço e a rota HTTP recusam esse verbo; a exportação remota é
   um download autorizado, auditado e com hash, salvo pelo desktop.
3. O primeiro cliente acrescentava `request_id` a contratos de inventário
   que não o aceitam. A correlação da GUI foi separada do payload estrito.
   Isolate/Restore agora persistem versões e recibos `CONFIRMED`.
4. A leitura periódica podia perder o pedido de refresh recebido durante uma
   consulta. O cliente mantém uma consulta e apenas um pedido subsequente
   coalescido; a fila não cresce com os ticks.
5. O núcleo não tinha cadência independente da GUI. `serve` agora possui
   relógio próprio e entrega exclusiva à recalibração. Em sobrecarga, o tempo
   científico contado avança com as etapas realmente processadas, sem
   preencher valores finais nem omitir etapas para recuperar o tempo de parede.
6. O encerramento aguardava o relógio, mas podia fechar journals/recepção com
   um handler HTTP ainda em uso. O servidor contabiliza handlers antes de
   criar suas threads, interrompe novas admissões e drena os ativos dentro
   do mesmo prazo de parada. O perfil core usa threads não daemon; timeout
   não é tratado como autorização para fechar recursos ou matar uma gravação.
7. A autorização de recalibração era verificada antes da espera pelo relógio.
   Uma revogação ou deadline vencido durante a espera agora é revalidado
   imediatamente após o handoff e antes de iniciar a calibração.

Os recibos de inventário registram `REQUESTED`, `RUNNING` e confirmação por
readback da topologia persistida. Falha após aplicação vira `UNKNOWN`, não
sucesso nem rollback presumido. O cliente recebe esse resultado como incerto,
não repete automaticamente a operação e permite consultar o recibo.

## Condições e evidências

Host inicial: Windows 11 Pro 10.0.26200, AMD Ryzen 7 5700X. Python e dependências
das execuções ficam registrados nos artefatos. Os testes portáveis Windows
desabilitam explicitamente identidade do SO/DPAPI ou instalam o wheel isolado
com sentinelas de importação. Isso demonstra independência dessas funções;
não transforma o host Windows em uma execução Linux.

As execuções e falhas anteriores estão preservadas em
[`outputs/portable_core_20261010`](../../outputs/portable_core_20261010),
[`outputs/portable_remote_20261010`](../../outputs/portable_remote_20261010),
[`outputs/portable_identity_20261010`](../../outputs/portable_identity_20261010)
e [`outputs/service_portability_20261010`](../../outputs/service_portability_20261010).
Os corpora sobrepostos não devem ser somados como casos únicos.

A matriz final e o manifesto de hashes identificam os corpora utilizados para
o estado entregue. Uma falha inicial de recibo por campo não permitido e as
reproduções de autorização/exportação permanecem disponíveis para comparação.
Skips de testes POSIX em Windows não contam como aprovação; uma execução
Linux deve registrar seus resultados próprios. Os testes de serviço e wheel
Windows precedem a correção final de drenagem; a campanha Linux e os corpora
pós-drenagem abaixo executam essas fontes finais.

| Corpus Windows final | Resultado | Artefato |
|---|---|---|
| Serviço, relógio e contrato KME | 67 aprovados; 2 skips POSIX; 181,22 s | [JUnit final9](../../outputs/portable_core_20261010/final-service-tests-win9.xml) |
| Artefato headless atual | 14 aprovados; 45,81 s | [JUnit do wheel](../../outputs/portable_core_20261010/artifact-final-tests.xml) |
| Cofre portável | 46 aprovados; 4 skips POSIX | [JUnit cofre](../../outputs/portable_core_20261010/vault-tests-win10.xml) |
| CLIques reais, RUN PATH e exportação | 2 aprovados; 45,31 s | [JUnit de ações](../../outputs/portable_remote_20261010/window-actions-regression.xml) |
| Refresh/backpressure | 2 aprovados; 30,31 s | [JUnit freshness](../../outputs/portable_remote_20261010/freshness-regression.xml) |
| GEO global remoto | 1 aprovado; 36,75 s | [JUnit GEO](../../outputs/portable_remote_20261010/global-geo-final.xml) |
| Console remoto, identidade e regressões locais | 96 aprovados; 1 skip FIFO Windows; 350,67 s | [JUnit conjunto](../../outputs/portable_remote_20261010/final-validation.xml) |
| GUI com o núcleo após correção de drenagem | 3 aprovados; 35,16 s | [JUnit pós-drenagem](../../outputs/portable_remote_20261010/post-drain-final.xml) |
| Drenagem HTTP e admissão após espera | 12 aprovados; 7,37 s | [JUnit de concorrência](../../outputs/portable_core_20261010/http-drain-win2.xml) |
| Segurança, core e controle de protocolo/equipamento após a correção final | 115 aprovados; 90,25 s | [JUnit de segurança](../../outputs/portable_core_20261010/security-final-tests.xml) |

O wheel final carregou **112 módulos exclusivamente do artefato instalado**,
sem imports GUI/ML/ABI Windows. O teste executou rede, swapping e purificação,
com tolerância numérica declarada de 10⁻⁷ para a referência de swapping, além
de snapshots completo e leve coerentes que não avançam o motor. A diferença
de fidelidade entre purificação ideal configurada e o ruído padrão permaneceu
observável; os testes não removeram o ruído para garantir sucesso.

## Implantação e dependências

Consulte o [runbook de serviço](../product/orionq_linux_service.md), a
[decisão de arquitetura](../architecture/adr_security_002_portable_core.md),
`packaging/core/core.example.json`, Dockerfile/Compose e o gate
`.github/workflows/core-linux.yml`. O workflow adicionado não foi executado
automaticamente nesta máquina.

O usuário autorizou instalar WSL para reproduzir os testes Linux. O pacote e
os recursos Windows foram instalados com elevação administrativa aprovada.
Ubuntu 24.04.5 LTS iniciou com kernel
`6.18.33.2-microsoft-standard-WSL2` e Python 3.12.3. O componente opcional do
Windows marcou reinício pendente, mas o WSL 2 iniciou e executou Linux sem
reiniciar a máquina. O [estado da instalação](../../outputs/service_portability_20261010/wsl/install-state.json)
descreve a etapa anterior à inicialização Linux; as evidências da execução são
separadas. Docker não foi instalado nesta rodada; executar Python sob WSL não
comprova o contêiner Docker.

A primeira campanha Linux, como UID/GID 10001 em filesystem nativo, executou
142 casos: 141 aprovados, uma falha de fixture e zero skips. A fixture criou
um arquivo de chave com modo 0644; o provedor recusou o arquivo corretamente.
Somente o teste foi corrigido para modo 0600, mantendo a validação obrigatória.
O [corpus inicial preservado](../../outputs/service_portability_20261010/wsl/linux-native-v1/tests.xml)
e seu [resumo](../../outputs/service_portability_20261010/wsl/linux-native-v1/outcome-summary.json)
não são apresentados como uma campanha aprovada.

A segunda campanha Linux aprovou **154 testes, sem falhas, erros ou skips**, em
21,77 s reportados pelo pytest (22,36 s para o subprocesso completo). A execução
usou UID/GID 10001, Python 3.12.3, diretório privado 0700 e filesystem nativo,
com instalação nova do wheel 5.62.0. Incluiu permissões POSIX/FIFO, identidade,
cofre, API, relógio, drenagem, artefato e duas KMEs em processos independentes.
Consulte o [relatório Linux](../../outputs/service_portability_20261010/wsl/linux-native-v2/report.json),
o [JUnit](../../outputs/service_portability_20261010/wsl/linux-native-v2/tests.xml)
e as [dependências exatas](../../outputs/service_portability_20261010/wsl/linux-native-v2/dependency-freeze.log).

O [ensaio CLI separado](../../outputs/service_portability_20261010/wsl/linux-native-v2/cli-smoke.json)
usou TLS 1.3/mTLS e relógio de 1 s: o tick avançou de 2 para 3 após a
desconexão do cliente. SIGTERM terminou com código 0 em 0,278 s; não foram
encontrados segredos nos logs. O artefato instalado carregou 112 módulos
próprios, sem GUI/ML/ABI Windows. São ensaios sintéticos de software no mesmo
computador físico; não representam teste Windows-GUI → Linux-core em rede,
latência garantida ou qualificação de duas estações físicas.

A [comparação de integridade](../../outputs/service_portability_20261010/wsl/linux-native-v2/after-staging-integrity.json)
registrou 471 hashes no staging: 467 permaneceram idênticos e quatro arquivos
`egg-info` foram regenerados pelo setuptools ao construir o wheel. Os
metadados gerados foram revalidados para versão, dependências e entrypoint
do perfil core. Não houve mudança de código/teste/recurso durante os ensaios;
os 470 arquivos aplicáveis comparados com o repositório e 12 recursos públicos
coincidem. A comparação bruta também foi preservada. Entre as rodadas v1/v2,
as mudanças de drenagem/admissão e fixtures estão identificadas no relatório,
sem alteração de módulo científico.

O [manifesto consolidado](../../outputs/portable_core_20261010/consolidated-final.json)
registra hashes, contagens e limites de cada corpus, sem somar testes
sobrepostos como casos únicos ou incluir credenciais e estado privado.

O administrador institucional deve provisionar e distribuir credenciais de
cliente por um mecanismo protegido. A proposta de exportação de senha de
certificado foi rejeitada pela revisão automática e não foi implementada.
O cliente Python permite injetar um leitor protegido para uma chave privada
criptografada; a CLI falha sem esse leitor, sem abrir prompt ou usar segredo
do servidor como fallback.

## Limites de aceitação

- O artefato Linux é o plano de integração terrestre. Não se entregou firmware
  RTOS/OBC, bitstream FPGA ou qualificação dos protocolos de uma missão.
- A autenticação OIDC existente pode ser configurada no backend; o novo
  desktop remoto utiliza certificado provisionado, sem novo fluxo de login
  OIDC em navegador nesta rodada.
- Preparação nativa de políticas, campanhas temporais e intake de ZIPs que
  dependem de arquivos/journal locais ficam indisponíveis no cliente remoto.
  Seus modos locais continuam disponíveis; novos endpoints são necessários
  para oferecer esses fluxos remotamente.
- Montagens privadas dependem do host/orquestrador. Não substituem HSM,
  atestação nem âncora externa contra restauração indevida de backups.
- O perfil contém uma instância por SQLite local. Réplicas em filesystem de
  rede, alta disponibilidade e orquestração distribuída não foram qualificadas.
- Separar o processo de GUI reduz o acoplamento, mas não cumpre por si só
  deadlines de tempo real. O gate de desempenho G05 anterior continua aberto;
  esta rodada não declara controle hard real time ou ganhos numéricos de latência.
- Modelos ML continuam opcionais; o perfil headless mínimo não instala seus
  pacotes. O pipeline científico existente foi preservado, sem nova campanha
  de qualificação física ou atribuição de TRL.
