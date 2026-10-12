# OrionQ — núcleo no boot, independente do login

O núcleo pode executar como processo de serviço sem uma sessão gráfica ou
login de operador. O login/certificado do operador identifica e audita suas
ações, e o backend continua verificando suas permissões. Autenticação e RBAC
não deixam de existir quando a interface é fechada: o núcleo tem identidade
de serviço própria, e cada cliente mantém identidade separada.

Esta entrega fornece uma unidade Linux concreta e revisável em
[`orionq-core.service`](../../packaging/core/systemd/orionq-core.service), alvo
Ubuntu 24.04/systemd 255. Ela **não foi instalada, habilitada nem iniciada** no
host desta entrega. Credenciais, certificados e autoridade emissora não são
criados durante o boot. Os demais detalhes de configuração, PKI e cliente
estão no [guia Linux](orionq_linux_service.md) e no
[guia da rede mista](orionq_mixed_controller.md).

## Preparação administrativa, uma vez por implantação

Instale o wheel headless e as dependências qualificadas em um venv imutável
`/opt/orionq/venv`; coloque os recursos públicos em `/opt/orionq`. A unidade usa
`python -I`: não depende de `PYTHONPATH`, ambiente de login ou diretório do
desenvolvedor. Não copie o executável Qt como se fosse o serviço Linux.

Prepare um usuário/grupo de sistema `orionq`, sem shell de login, e diretório
de estado privado `/var/lib/orionq` no filesystem local. A unidade requer essa
conta existente; não a provisiona. `StateDirectory=orionq` gerencia o diretório
e sua propriedade para o usuário do serviço: use somente o estado dedicado
do OrionQ, nunca um diretório compartilhado com arquivos de terceiros.

Separe os arquivos de configuração legíveis pelo serviço dos segredos fonte
lidos pelo gerenciador systemd. Organização de referência:

| Caminho | Conteúdo e acesso |
|---|---|
| `/opt/orionq/` | Código/recursos revisados, graváveis apenas pelo administrador |
| `/etc/orionq/core.json` | Configuração sem segredo, legível por `orionq`, administrada fora do runtime |
| `/etc/orionq/server-leaf.json` | Descritor de folha provisionada; referências, sem senha da chave |
| `/etc/orionq/secrets/orion-service` | Credencial opaca já provisionada; root:root, 0400; não armazenar no Git |
| `/etc/orionq/secrets/orion-vault` | Chave AES-GCM de 32 bytes brutos; root:root, 0400 |
| `/var/lib/orionq/` | Estado/journal/registro de autorização privados, pertencentes a `orionq`, 0700 |
| `/run/credentials/orionq-core.service/` | Cópias privadas entregues por `LoadCredential`; gerenciadas por systemd |

Gere/distribua segredos pelo mecanismo administrativo institucional e execute
o provisionamento offline do guia Linux **antes** de iniciar a unidade.
Na configuração administrativa temporária, as referências devem apontar aos
segredos disponíveis naquele processo administrativo; `/run/credentials/...`
existe durante a execução da unidade. Depois do provisionamento, distribua o
estado, a CA pública, o certificado do serviço e o descritor correspondentes.
Retenha a capacidade de emissão e a chave privada da CA fora da montagem normal
de runtime. Não monte a credencial do administrador no serviço.

O arquivo de runtime `/etc/orionq/core.json` pode usar este contrato completo:

```json
{
  "schema": "orion.core-service.v1",
  "repository_root": "/opt/orionq",
  "state_root": "/var/lib/orionq",
  "identity": {
    "subject": "orion-core",
    "credential_file": "/run/credentials/orionq-core.service/orion-service"
  },
  "vault": {
    "key_id": "master-v1",
    "key_file": "/run/credentials/orionq-core.service/orion-vault",
    "previous_keys": {}
  },
  "http": {
    "host": "127.0.0.1",
    "port": 8765,
    "allowed_hosts": ["127.0.0.1", "localhost", "orion-core"],
    "allowed_origins": [],
    "requests_per_minute": 120,
    "max_concurrency": 8,
    "body_timeout_seconds": 5,
    "operation_timeout_seconds": 30
  },
  "tls": {
    "root": "/var/lib/orionq/certificates",
    "server_leaf": "/etc/orionq/server-leaf.json"
  },
  "clock": {
    "enabled": true,
    "period_s": 1.0,
    "stop_timeout_s": 10.0
  }
}
```

O bind inicial é loopback. Para outros hosts, revise bind, firewall, DNS/SAN e
allowlists explicitamente, mantendo TLS 1.3/mTLS e RBAC. Alterar o bind não
admite acesso anônimo. Os IDs e bytes dos segredos precisam corresponder aos
provisionados: a unidade não gera outra chave diante de um arquivo ausente.
Para rotação, adicione as referências de chaves anteriores e suas diretivas
`LoadCredential` correspondentes, sem apagar o estado de consumo/revogação.

## Unidade e ciclo de vida

`ExecStartPre` chama `check` com a mesma conta, sandbox e credenciais do
processo principal. Falta de segredo, autenticação inválida, configuração
incompatível ou TLS inválido impede a execução de `serve`. `Type=exec` confirma
a execução do binário; não declara readiness da API, protocolo ou hardware.
Use `/api/v1/health` por mTLS autorizado para verificar a aplicação.

`ExecStart` executa `serve` em foreground para que systemd acompanhe o PID.
O relógio é do backend e não depende de clientes conectados. A fonte inicial
permanece `none`: iniciar no boot não fabrica medições, não habilita modelos
ML e não ativa hardware. Uma fonte/demanda deve ser configurada por uma
identidade autorizada e produzir observações por seu contrato. A ausência de
tráfego deve ser distinguida de falha do processo. Para campanhas com avanço
manual exclusivo, configure `clock.enabled=false`.

`Restart=on-failure` tenta recuperar falhas de processo, com intervalo de
10 segundos e no máximo três partidas por janela de 300 segundos. O código
2 de bloqueio controlado do processo principal impede restart automático;
uma falha do preflight pode consumir as tentativas limitadas. Os logs e o
estado ficam disponíveis para investigação, sem criar autoridade nova.
O administrador deve corrigir a causa e, quando apropriado, usar
`reset-failed`/`restart` explicitamente. Reinício não comprova recuperação de
um instrumento ou de uma entrega incerta; reservas e aquisição precisam de
reconciliação/verificação conforme seus contratos.

SIGTERM pede a drenagem existente do core. O orçamento interno configurado
é de 10 segundos; `TimeoutStopSec=90s` é a margem externa do supervisor.
Se o processo continuar preso, systemd pode terminar o grupo por SIGKILL.
Essa terminação forçada não é encerramento gracioso nem prova de flush: o
próximo início deve preservar entrega incerta e reverificação conservadora.
Nunca apague SQLite, recibos, checkpoints ou chaves para forçar uma partida.

A unidade não recebe capabilities, usa `NoNewPrivileges`, rootfs protegido,
home inacessível, temporários privados e core dumps desabilitados.
`PrivateDevices=yes` é intencional no perfil atual: acesso direto a USB/SDR/FPGA
não está qualificado por esta unidade. Uma implantação de bancada precisa
de política explícita de dispositivos/driver e testes próprios, ou de gateway
autenticado separado. Não remova globalmente os isolamentos para contornar
uma dependência não investigada.

A sessão autenticada do processo CLI tem duração limitada de até 24 horas.
O runtime do core habilita sua renovação automática com a credencial própria
do serviço, sem usar a sessão de um operador. A renovação revalida a identidade,
a geração das permissões, a sessão original e a credencial protegida. Revogação,
mudança de autorização, expiração da identidade ou erro de leitura bloqueiam
o processamento; não concedem outra autoridade automaticamente. Depois desse
bloqueio, a recuperação exige autenticação explícita e configuração revisada.

Essa renovação pertence somente ao processo. Sessões humanas, certificados e
autoridade de equipamentos não são renovados por ela. Uma aquisição continua
vinculada à sessão verificada original e precisa de reconexão/verificação
explícitas quando essa autoridade expirar. Planeje a rotação de credenciais e
certificados com preservação do estado durável. Um relógio `blocked` pode
coexistir com socket aberto; liveness não equivale a readiness de operação.

As diretivas seguem os manuais oficiais
[systemd.service v255](https://github.com/systemd/systemd/blob/v255/man/systemd.service.xml)
e [systemd.exec v255](https://github.com/systemd/systemd/blob/v255/man/systemd.exec.xml).
`LoadCredential` entrega arquivos privados ao usuário da unidade; nenhum
segredo é embutido no unit file, em argumento ou variável de ambiente.

## Procedimento de implantação no host Linux

Os comandos abaixo são instruções para uma implantação aprovada e preparada;
não foram executados nesta entrega. Primeiro confira conta, paths, dependências,
SAN, configuração e o estado provisionado. Não inclua o segredo no terminal.

```bash
sudo systemd-analyze verify packaging/core/systemd/orionq-core.service
sudo install -m 0644 packaging/core/systemd/orionq-core.service /etc/systemd/system/orionq-core.service
sudo systemctl daemon-reload
sudo systemctl start orionq-core.service
sudo systemctl status orionq-core.service --no-pager
sudo journalctl -u orionq-core.service --no-pager -n 50
```

O `check` da unidade é a verificação dentro do mesmo contexto de credenciais.
Executá-lo manualmente fora da unidade com os caminhos `/run/credentials/...`
não reproduz esse contexto. Consulte a API com o certificado do operador,
usando `operator_cli health`, `mixed-status` ou `watch`; confirme cadência e
causa de bloqueios em `health.processing_clock` e nos recibos.

Somente após esse ensaio no host alvo, habilite a partida no boot:

```bash
sudo systemctl enable orionq-core.service
```

No ensaio de aceitação, reinicie o host sem login gráfico, consulte o serviço
de outro terminal/cliente e compare identidade, tick e recibos. Feche o cliente
e confirme que o core continua em execução. Teste remoção de credencial,
certificado revogado, fonte indisponível e SIGTERM com demanda em curso num
estado dedicado, sem modificar a produção. Não conclua boot sem login a partir
de um teste que somente iniciou o Python numa sessão interativa.

Para manutenção/rollback, preserve o estado durável e volte somente o artefato
de código/configuração compatível, após verificar migrações e contratos:

```bash
sudo systemctl stop orionq-core.service
sudo systemctl disable orionq-core.service
```

Investigue bloqueios com o responsável pela identidade/PKI ou aquisição
conforme a causa. `reset-failed` apenas libera a contagem de tentativas; não
renova certificados, concede permissões nem reverifica equipamento.

## Windows: execução headless atual e limite de serviço nativo

O núcleo CLI pode ser executado independentemente do Qt em um ambiente Windows
com dependências e um provedor de segredos portável explicitamente qualificado.
O perfil montado exige validação segura de ACLs; a CLI padrão não fornece esse
validador Windows e deve bloquear, sem fallback DPAPI/test/plaintext. Esse
pré-requisito deve ser resolvido por composição institucional antes de usar
credenciais operacionais Windows. O caminho já validado para segredos montados
é Linux, inclusive execução em filesystem Linux nativo no WSL.

Com esse pré-requisito satisfeito, a forma foreground é:

```powershell
python -I -m quantum_autopilot.service.cli --config C:\OrionCore\core.json check
python -I -m quantum_autopilot.service.cli --config C:\OrionCore\core.json serve
```

Os caminhos de `core.json` devem ser absolutos e pertinentes ao servidor;
certificados/segredos precisam estar provisionados e protegidos. Nenhuma GUI
é iniciada por esses comandos. Foreground ainda tem ciclo de vida do processo
do terminal e não comprova partida automática antes do login Windows.

Não foi encontrado adaptador `ServiceMain`/`StartServiceCtrlDispatcher` no
produto. `sc.exe create` sozinho não converte `python -m ... serve` ou o
executável desktop em serviço SCM. A
[documentação Microsoft](https://learn.microsoft.com/en-us/windows/win32/api/winsvc/nf-winsvc-startservicectrldispatchera)
exige comunicação com o dispatcher e tratamento dos estados/controles de
serviço. Um adaptador SCM ou supervisor externo requer implementação,
qualificação de parada/recuperação e instalação administrativa próprias;
esta entrega não fornece um wrapper que simule esse suporte. WSL iniciado
manualmente também não equivale a serviço pré-login do host Windows.

## Verificações desta entrega

`tests/test_core_service_unit.py` confere a unidade, o contrato do exemplo,
referências de credenciais, isolamento e limites de restart/shutdown; rejeita
clock inválido e executa `check`/`serve` com segredo ausente para comprovar
bloqueio antes de abrir listener ou consultar autoridade desktop. Um ensaio
do relógio usa identidades sintéticas explicitamente de teste, sem login,
mantendo a fonte `none`, atuação física bloqueada e auditoria válida.

Quando `systemd-analyze` está disponível em Linux, o teste faz verificação de
sintaxe com executável substituído por `/usr/bin/true` somente para o parser;
não instala, inicia ou habilita a unidade e não atesta os paths de deployment.
Os ensaios de boot real, credenciais carregadas por systemd, readiness mTLS e
falhas sob os isolamentos precisam ser executados no host alvo preparado.

O gate Windows deste arquivo aprovou 10 testes em 5,64 segundos, com um skip
exclusivo do parser Linux. Evidência:
[`unit-win2.xml`](../../outputs/background_service_20261011/unit-win2.xml).
Uma chamada separada no Ubuntu/WSL existente, como usuário sem privilégios,
verificou a unidade com `systemd-analyze 255.4-1ubuntu8.17`: código zero, sem
diagnósticos, usando a mesma substituição de executável. Isso comprova a
sintaxe aceita pelo parser, não uma execução de boot/systemd do OrionQ.
