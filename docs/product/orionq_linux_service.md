# OrionQ — núcleo headless em Linux e contêiner

Data: 2026-10-10. Perfil: serviço autenticado, simulação e aquisição somente
leitura; atuação física bloqueada. Esta entrega separa o artefato desktop do
serviço. Não qualifica OBCs, RTOS, FPGA ou uma missão satelital.

## Artefatos e fronteiras

| Artefato | Função | Dependências |
|---|---|---|
| Desktop fonte 5.62, modo remoto | Cliente de apresentação de um serviço separado | Qt, TLS 1.3 e certificado próprio; sem cofre/journal do núcleo |
| Executável desktop 5.61 existente / modo local legado | Interface nativa e workspace local | Windows/Qt; perfil local pode usar DPAPI |
| `packaging/core` | API/serviço sem GUI | Python 3.12 em Linux, NumPy/SciPy, Qiskit/Aer, mTLS e cofre portável |
| Adaptador externo | Tradução de dados e protocolos de equipamento | SDK/driver e contrato correspondente; qualificação própria |

O processo de serviço autentica uma identidade explicitamente provisionada,
independente do usuário logado no Windows e do UID do contêiner. Roteamento,
autorização, KME e auditoria devem receber essa autoridade no backend. A sessão
gráfica não é uma credencial do serviço. Segredos montados são referências
externas; certificados e chaves privadas não são incorporados à imagem.

O script `packaging/core/prepare_source.py` cria uma árvore nova para construir
um wheel com o mesmo nome e versão de distribuição que o produto, identificada
por `core-artifact.json`. Exclui o pacote Qt e o launcher desktop; mantém os
módulos científicos. O staging não reescreve o pyproject do checkout. O perfil omite ML
e seus pacotes pesados; a execução de modelos requer um perfil adicional
explicitamente configurado e validado.

## Configuração

O contrato estrito é `orion.core-service.v1`, implementado em
`quantum_autopilot.service.config`. O exemplo sem valores secretos está em
`packaging/core/core.example.json`. As chaves desconhecidas são rejeitadas.

| Campo | Uso |
|---|---|
| `repository_root` | Recursos imutáveis públicos: `/opt/orionq` na imagem |
| `state_root` | Estado durável local: `/var/lib/orionq` |
| `identity.subject` / `credential_file` | Identidade do serviço e arquivo de credencial opaca |
| `provisioning_identity` | Administrador offline; remover da configuração normal |
| `vault.key_id` / `key_file` | Identificador da chave de proteção e referência a arquivo com 32 bytes brutos |
| `vault.previous_keys` | Referências a chaves anteriores para recuperação/rotação autorizada |
| `http` | Bind, porta, allowlist, quotas, concorrência e deadlines |
| `tls.root` | Registro de certificados, CA pública e folhas provisionadas |
| `tls.server_leaf` | Descritor da folha do serviço; contém referências, não a senha da chave |

Credenciais de serviço e de provisionamento usam arquivos privados com 32 a
4096 bytes brutos. No Linux, use proprietário UID 10001 e modo 0400 para as
montagens de runtime; o provedor também admite propriedade root e modos
privados, desde que o processo consiga ler o arquivo. Symlinks e arquivos
acessíveis a grupo/outros são rejeitados. Não use nomes/senhas do easter egg, valores de ambiente ou
UIDs como credenciais. A allowlist HTTP não substitui o SAN do certificado; o
hostname utilizado pelo cliente deve constar no certificado provisionado. O
bind `0.0.0.0` representa interfaces locais, não é o nome do servidor no cliente.

## Construção

Na raiz do repositório, em um host com Docker e contêineres Linux:

```bash
docker build -f packaging/core/Dockerfile -t orionq-core:local .
docker run --rm orionq-core:local python -m quantum_autopilot.service.cli --help
```

O contexto de build tem uma allowlist própria. Apenas fontes e recursos
públicos necessários são transferidos; `outputs` entra com um único CSV público
verificado pela grade científica. Estado, cofres, credenciais, arquivos PEM,
chaves e outros outputs não entram no contexto. A imagem final executa UID/GID
10001, sem Qt, PyTorch ou scikit-learn. O build verifica dependências com
`pip check` e a ausência da GUI.

O tag de Python e as faixas em `requirements.txt` permitem preparar o perfil;
não são um lock de release. Para uma qualificação reproduzível, escolha um
digest revisado para `PYTHON_IMAGE`, registre o digest da imagem resultante e a
saída de `python -m pip freeze`, examine as dependências e execute os ensaios
no host alvo. Não atribua a uma imagem recém-resolvida resultados de outra.

## Provisionamento separado da inicialização

Prepare três diretórios separados no host: configuração, segredos do serviço e
estado durável. Os diretórios/arquivos montados devem ser privados e acessíveis
ao UID 10001 do contêiner. Não altere automaticamente a propriedade de arquivos
de usuário durante a inicialização.

Exemplo de organização:

```text
/srv/orionq/config/core.json
/srv/orionq/config/server-leaf.json
/srv/orionq/secrets/orion-vault       # 32 bytes, UID 10001, modo 0400
/srv/orionq/secrets/orion-service     # credencial opaca, UID 10001, modo 0400
/srv/orionq/state/                   # UID/GID 10001, modo 0700
/srv/orionq-admin/orion-admin         # UID 10001, modo 0400; não montar no serviço
```

Copie o exemplo de configuração e gere as três credenciais fora do controle de
versão, usando um gerador criptográfico ou o serviço de segredos institucional.
Não as imprima, não as inclua em argumentos da CLI e não as substitua por
valores demonstrativos. O comando administrativo usa o arquivo da identidade
provisionadora; a configuração fica gravável apenas nessa etapa offline:

```bash
docker run --rm --read-only --tmpfs /tmp \
  --mount type=bind,src=/srv/orionq/config,dst=/etc/orionq \
  --mount type=bind,src=/srv/orionq/state,dst=/var/lib/orionq \
  --mount type=bind,src=/srv/orionq/secrets,dst=/run/secrets,readonly \
  --mount type=bind,src=/srv/orionq-admin/orion-admin,dst=/run/secrets/orion-admin,readonly \
  orionq-core:local \
  python -m quantum_autopilot.service.cli --config /etc/orionq/core.json \
  provision --server-names orion-core localhost 127.0.0.1
```

Provisionamento repetido não cria silenciosamente outro administrador. Em um
ambiente institucional, a configuração inicial e a distribuição das
credenciais são ações administrativas controladas. Remova
`provisioning_identity` da configuração de execução e mantenha a configuração
administrativa fora da montagem de runtime.

Clientes recebem identidades, papéis, escopos e certificados próprios por
provisionamento administrativo. O comando disponível para registrar uma folha
de cliente é:

```bash
python -m quantum_autopilot.service.cli --config /etc/orionq/core-admin.json \
  grant-certificate orion-auditor --roles auditor \
  --scopes 'workspace:/var/lib/orionq' 'workspace:/var/lib/orionq/*' --services dashboard \
  --kind service --output /etc/orionq/auditor-leaf.json
```

Execute-o somente no contexto offline que possui a identidade administrativa
e os recursos de emissão. O descritor da folha não contém a senha privada; a
distribuição de chaves e credenciais aos clientes deve usar o mecanismo seguro
do ambiente. Não compartilhe o cofre inteiro do núcleo como credencial de um
cliente. Permissões recebidas em JSON ou claims de função não concedem papéis.

Retenha a chave privada da autoridade emissora e sua capacidade de emissão
fora da montagem normal de serviço. O runtime carrega a CA pública, a folha
criptografada do serviço e o registro de autorização; não precisa abrir a
chave privada da CA. Preserve os checkpoints de revogação/consumo durante a
transferência: não faça uma cópia de backup que restaure material consumido.

## Execução e encerramento

O comando `serve` mantém um relógio próprio do backend, independente da GUI.
O campo opcional `clock` aceita exatamente `enabled` (booleano), `period_s`
(0,001–3.600 segundos) e `stop_timeout_s` (0,001–60 segundos). O padrão é
habilitado, período de 1 segundo e prazo de parada de 10 segundos. A fonte
inicial continua `none`: iniciar o serviço não gera medições simuladas.

Um tick processado corresponde a um segundo contado da rede. Configurar um
período menor acelera essa contagem nominal, sem representar tempo físico de
aquisição. Sob sobrecarga, o relógio registra atraso e deadlines perdidos;
não inventa ticks para recuperar tempo nem acumula uma fila de tarefas.
`health.processing_clock` informa cadência, etapas, duração, backlog de
protocolo, origem e estado. Leituras remotas não avançam a rede. Um operador
pode pedir uma etapa adicional pela API; para campanhas com avanço manual
exclusivo, configure `clock.enabled=false`.

Recalibração toma o relógio de forma exclusiva e limitada enquanto executa
suas fases existentes. Revogação ou expiração da identidade bloqueiam o
próximo passo, sem herdar a identidade de um cliente HTTP. O encerramento
interrompe novas admissões e aguarda o worker, a rotina em curso e os handlers
HTTP dentro de um único prazo antes de fechar recepção e journal. Se o prazo
for excedido, informa falha e conserva os recursos em uso; as threads do core
não são daemon. A recalibração revalida autorização e deadline após a espera.

Defina apenas referências a diretórios, sem conteúdo secreto:

```bash
export ORIONQ_CONFIGURATION_DIRECTORY=/srv/orionq/config
export ORIONQ_SECRET_DIRECTORY=/srv/orionq/secrets
export ORIONQ_STATE_DIRECTORY=/srv/orionq/state
docker compose -f packaging/core/compose.yaml up --build
```

O Compose publica a porta no loopback do host por padrão. Uma publicação em
outra interface é uma decisão explícita de implantação, requer DNS/SAN,
firewall e a mesma autorização mTLS. A API não vira anônima ao usar contêiner.
Estado permanece no diretório do host; configuração e segredos ficam somente
leitura; rootfs é somente leitura e capabilities são removidas. Não execute
réplicas compartilhando SQLite em um filesystem de rede. Este perfil contém
uma instância por estado durável local; alta disponibilidade distribuída
requer um projeto e ensaios adicionais.

```bash
docker compose -f packaging/core/compose.yaml stop --timeout 30
```

O processo recebe SIGTERM diretamente e deve encerrar o servidor e liberar os
recursos do journal. `status` e `check` da CLI verificam o perfil sem abrir uma
janela gráfica. O healthcheck Docker é somente liveness TCP: socket aberto não
comprova acesso, protocolo ou prontidão científica. Para readiness, consulte
`/api/v1/health` via TLS 1.3 com certificado de cliente autorizado e validação
de hostname; HTTP anônimo e certificado revogado devem falhar.

## Cliente e rotas implementadas

`quantum_autopilot.service.client.CoreClient` recebe uma origem HTTPS e um
`ssl.SSLContext` com certificado de cliente próprio, CA confiável, verificação
de hostname e TLS 1.3 estrito. Esse contexto é preparado pela implantação,
usando os arquivos e referências de segredos do cliente. O cliente não abre o
journal do serviço, não lê seu cofre e não usa fallback local quando a rede
falha. Não distribua a chave mestra ou a credencial de processo do núcleo à GUI.

| Método do cliente | Rota | Comportamento |
|---|---|---|
| `health()` | `GET /api/v1/health` | Estado do serviço e bloqueio de atuação física |
| `topology()` | `GET /api/v1/control-plane/topology` | Configuração de topologia autorizada |
| `network_state(metadata_only=False)` | `GET /api/v1/network/state` | Último estado; leitura não avança o relógio |
| `network_step(metadata_only=False)` | `POST /api/v1/network/step` | Avança uma etapa do backend; exige permissão de escrita |
| `network_command(verb, payload)` | `POST /api/v1/network/commands` | Comando tipado, validado e autorizado pelo backend |
| `control_cycle(request)` | `POST /api/v1/control-plane/cycles` | Executa um ciclo com os mesmos controles do serviço |
| `laboratory()` | `GET /api/v1/laboratory` | Estado e capacidades da aquisição |
| `configure_laboratory(request)` | `POST /api/v1/laboratory/configuration` | Valida e persiste a configuração experimental |
| `console_snapshot(include_static=True)` | `GET /api/v1/console/snapshot` | Envelope coerente com identidade do cliente; não avança o motor |
| `console_command(verb, payload)` | `POST /api/v1/console/commands` | Lista explícita de operações; repete a autorização no serviço de destino |
| `console_evidence(scope)` | `POST /api/v1/console/evidence` | Download autorizado, auditado e com hash; não aceita destino de escrita no servidor |

Por exemplo, um cliente operador pode enviar `configure_network_input` com
`{"kind":"simulated","seed":17,"scenario":"quiet"}` e então solicitar
`network_step()`. Os observáveis são calculados pelo backend; o cliente não
apresenta um resultado substituto. Um auditor pode consultar o estado, mas a
mesma tentativa de avanço é recusada. Respostas perdidas em operações de
escrita produzem resultado incerto; não há retry automático que repita consumo
de recursos. Recibos duráveis devem resolver a incerteza.

## Desktop como cliente remoto

Com os arquivos públicos e privados do **cliente** já provisionados pelo
administrador, execute a versão fonte:

```powershell
python -m quantum_autopilot.console.app --provider remote `
  --core-url https://orion-core:8765 `
  --core-ca C:\OrionClient\ca.pem `
  --core-cert C:\OrionClient\operator.pem `
  --core-key C:\OrionClient\operator.key
```

O launcher também aceita `python -m quantum_autopilot.desktop --core-url ...`
com os mesmos argumentos de certificado. O EXE 5.61 já publicado não contém
essa implementação nova; esta entrega altera a fonte 5.62 e não o recompila.
A CLI do cliente espera uma chave privada já preparada e protegida no desktop;
se estiver criptografada sem leitor configurado, a conexão falha sem abrir um
prompt de senha. A composição Python `core_tls_context` admite um leitor
protegido institucional para a senha da chave privada. Não fornece exportação
da senha do certificado do servidor, nem distribui o cofre do núcleo ao cliente.

A tela inicial identifica o peer que o backend admitiu pelo certificado;
não aceita o usuário Windows como autoridade remota. Requisições rodam em
workers limitados. O desktop preserva seleção e os últimos dados, marcados como
desatualizados, quando a comunicação falha; não inicia um serviço local como
fallback. Leituras posteriores podem reconectar, mas uma mutação incerta não
é repetida automaticamente. Fechar a GUI não encerra o serviço separado.

Os painéis de topologia, GEO, telemetria, laboratório e os comandos com endpoints
implementados usam esse caminho. Ferramentas legadas que exigem acesso direto a
SQLite/arquivos locais — preparação de políticas, campanhas temporais e intake
de ZIPs locais — ficam explicitamente indisponíveis no modo remoto. Continuam
disponíveis nos modos locais existentes. Caminhos de aquisição configurados em
uma requisição remota são caminhos **do servidor**, não arquivos do desktop.

O administrador provisiona privilégios por recurso além de workspace, quando
necessários para protocolos, dispositivos ou laboratório. Papéis apresentados
na GUI só orientam botões; o backend sempre decide o acesso. A identidade de
processo usa sessão limitada (até 24 horas no launcher de serviço), renovada
automaticamente pelo core com sua credencial própria e as mesmas permissões
originais. Sessões humanas não participam dessa renovação. Revogação, mudança
do grant, credencial inválida ou expiração da própria identidade bloqueiam o
processamento. A renovação não substitui a autorização verificada dos
equipamentos: eles permanecem vinculados à sessão original até reconexão
explícita. Após bloqueio ou rotação administrativa, reautentique de forma
controlada e preserve o estado durável; não reinicie aquisição implicitamente.

## Validação e limites atuais

`tests/test_core_deployment.py` cobre staging sem alteração do checkout,
wheel real sem GUI/launcher/segredos, metadata/dependências, montagens privadas
e liveness que não envia uma solicitação de aplicação. Um teste instala o wheel
real em diretório isolado, bloqueia imports Qt/console/ML/ABI Windows e executa
identidade de serviço, rede, swapping e purificação com um provedor de segredos
explicitamente sintético. Verifica que os módulos vêm do wheel instalado.
No Windows, o plugin opcional `dateutil.tz.win` recebe o mesmo `ImportError` que
teria sem a ABI Windows; isso não emula a execução Linux nem uma sessão de SO.
A campanha Linux em
`.github/workflows/core-linux.yml` constrói o artefato e a imagem, verifica
imports, executa os ensaios de serviço e de KMEs independentes e preserva
resultados como artefatos. O ensaio POSIX de dois KMEs passou na campanha WSL
local descrita abaixo; seu skip no Windows não conta como comprovação.
Adicionar o workflow não equivale a executá-lo.

No computador Windows desta entrega, Docker não foi localizado no PATH e
o WSL inicialmente não estava instalado. Por autorização explícita do usuário,
foi instalado WSL 2 com Ubuntu 24.04; uma conta de validação UID/GID 10001
executou 154 testes aprovados, sem falhas ou skips, no filesystem Linux nativo,
incluindo duas KMEs independentes e o serviço CLI separado por mTLS. Consulte os resultados finais no
[relatório desta versão](../results/orionq_portable_core_20261010.md).
Os resultados Windows, Linux e Docker são evidências distintas. Docker não foi
instalado nem executado nesta rodada; não se declara teste do contêiner.

`tools/run_core_linux_validation.py` prepara um novo staging e venv, executa
os testes e o processo CLI/mTLS sem GUI e registra hashes, dependências e
resultados. Execute com Python Linux e uma conta sem privilégios; o runner
conserva estado e segredos em `/var/tmp` privado, não no volume NTFS montado.
Uma execução nova usa um diretório de evidência próprio. Exemplo para este host:

```powershell
wsl --distribution Ubuntu-24.04 --user orionq-validation --exec python3 `
  /mnt/e/Codex/2026-08-27/e/work/NISQ-Predictive-Twin/tools/run_core_linux_validation.py `
  --repository /mnt/e/Codex/2026-08-27/e/work/NISQ-Predictive-Twin `
  --evidence /var/tmp/orionq-linux-evidence-new
```

Somente relatórios públicos devem ser copiados ao repositório; os diretórios
privados de trabalho contêm credenciais de fixture e não são artefatos de entrega.

O hardware precisa de seus contratos e interfaces reais: CCSDS/CSP da missão,
gateway OGS, telemetria de engenharia, tracking e observações TDC não são
intercambiáveis. A integração Linux não converte telemetria RF em fótons nem
certifica QKD ou interlocks. A chave de proteção montada depende da proteção do
host/orquestrador; não é atestação, HSM ou solução universal contra rollback.

As opções de isolamento e montagem seguem a [referência oficial Compose](https://docs.docker.com/reference/compose-file/services/),
consultada em 2026-10-10. O desenho do build segue as [práticas oficiais Docker](https://docs.docker.com/build/building/best-practices/).
TLS usa a [documentação Python ssl](https://docs.python.org/3.12/library/ssl.html).
