# ADR-SEC-001 — Identidade e autorização comuns no backend

Data: 2026-10-07. Estado: implementado em software; qualificação operacional e experimental pendente.

## Problema e decisão

A escolha de fonte na tela de entrada não identifica o usuário. A interface nativa, HTTP,
serviços, adaptadores e gerenciamento de chaves precisam utilizar uma autoridade comum.
O núcleo `quantum_autopilot.security` autentica o usuário local pelo SID obtido do token
Windows; o fallback Unix usa o UID do processo e se identifica como perfil local limitado.
Variáveis como `USERNAME` e funções recebidas em JSON não concedem autoridade.

O primeiro provisionamento vincula o proprietário ao SID/UID real e armazena essa política
no cofre. Abrir uma política existente com outro usuário não cria outro administrador.
Os papéis são `administrator`, `operator`, `researcher`, `auditor` e `consumer`. A autorização
combina papel, operação e escopo do recurso. Uma operação desconhecida é negada para
papéis sem administração. A gestão de identidades e do cofre é exclusiva do administrador.
Toda autorização relê concessões, expiração e revogação; o objeto de identidade anterior
não mantém permissões que foram retiradas.

Autorizações aprovadas e recusadas permanecem registradas. `audit.batch()` permite juntar
leituras de um refresh da interface: preserva cada registro individual, sua ordem e seu HMAC,
e persiste todos antes do retorno do contexto, com um único fsync/checkpoint. Esse contexto
não engloba entrega de chaves ou efeitos em equipamentos. O cache de verificação só vale
enquanto identidade do arquivo, tamanho, timestamps e checkpoint protegido permanecerem
iguais; `verify()` sempre refaz a verificação completa.

HTTP estabelece sempre um contexto explícito: identidade autenticada ou anônimo. Anônimo
não recebe o privilégio da conta que executa o servidor. Chamadas internas sem contexto usam
a identidade real do sistema operacional. O código carregado sob a conta proprietária é
parte da base de confiança; Python não constitui uma sandbox contra código local hostil.
Serviços no mesmo processo compartilham a mesma instância de `SecurityRuntime`.

## Contrato para integrações

```python
security = SecurityRuntime(workspace / "security", profile="local")
security.authorize("network.configure", "network:global")
token = security.issue_local_session()  # somente processo do usuário autenticado
with security.identity_scope(security.session(token)):
    service_operation()
with security.identity_scope(None):
    service_operation()  # AuthenticationError
```

`grant_identity(subject, roles, scopes, source=...)`, `revoke_identity(subject)` e
`issue_service_session(subject)` exigem administração. `issue_local_session()` não é um
endpoint HTTP de login. Os tokens são opacos, expiram, ficam associados à concessão vigente
e são invalidados após reinício; não são persistidos em texto. `device_session_identity()`
vincula o controle nativo do equipamento a uma sessão revogável e não a renova silenciosamente.

`authorize(action, resource, principal=None)` é a fronteira obrigatória do serviço. O contexto
é isolado com `ContextVar` e restaurado mesmo após exceções. `snapshot()` retorna identidade,
papéis, escopos e estado do perfil, sem credenciais. Adaptações que abrem um novo thread devem
propagar deliberadamente a identidade validada, nunca dados de papel recebidos do cliente.

## Identidade institucional

`OIDCConfig` exige emissor HTTPS, audiência própria da API e JWKS fornecido por configuração
confiável. `authenticate_oidc()` verifica assinatura RSA/EC, allowlist RS256/ES256, tipo
`at+jwt`, emissor, audiência, sujeito, expiração, emissão, `client_id`, `jti` e `nbf` quando
presente. Rejeita `none`, HMAC, chaves/URLs do cabeçalho, IDs de chave ambíguos e tokens
adulterados. As permissões vêm de concessões locais, nunca do claim `roles`.

O perfil de tokens de acesso segue a [RFC 9068](https://www.rfc-editor.org/rfc/rfc9068).
Um ID token OIDC não é aceito como credencial da API. O fluxo interativo de autorização com
PKCE, `state` e `nonce`, o provedor institucional, sua descoberta e a atualização controlada
das JWKS dependem de configuração externa; não são declarados integrados ou qualificados.
As verificações de identidade foram confrontadas com a [especificação oficial OpenID Connect
Core](https://openid.net/specs/openid-connect-core-1_0.html), consultada em 2026-10-07.

## Cofre, rotação e recuperação

No Windows, `SecretVault` usa DPAPI `CurrentUser`, com UI desabilitada e sem o sinalizador
`LOCAL_MACHINE`. A proteção está vinculada ao usuário e à máquina, conforme a
[documentação Microsoft](https://learn.microsoft.com/en-us/windows/win32/seccrypto/example-c-program-using-cryptprotectdata),
consultada em 2026-10-07. DPAPI não protege contra código já executado sob a mesma conta e
não substitui HSM, isolamento por conta ou autorização de recuperação institucional.

Escritas são atômicas, sincronizadas em disco e serializadas entre threads/processos.
O fallback Unix de permissões 0600 é explicitamente local; o perfil `operational` o rejeita.
`rotate_secret()` cria uma referência versionada e preserva versões anteriores necessárias
para recuperar dados cifrados; não recifra bases automaticamente. `recover_secret()` é uma
ação administrativa explícita e auditada. Não há exportação silenciosa de segredos nem
recuperação automática baseada apenas na existência de backup. A política institucional de
retenção, período criptográfico e autorização de recuperação deve ser qualificada conforme
as recomendações de [NIST SP 800-57 Parte 1, revisão 5](https://csrc.nist.gov/pubs/sp/800/57/pt1/r5/final).

## Auditoria e limites de restauração

`AuthenticatedAudit` grava identidade, operação, recurso, resultado e metadados permitidos.
Corpos de requisição, exceções livres, tokens, senhas, chaves e credenciais são descartados.
HMAC-SHA256 autentica cada registro e encadeia o MAC anterior. O último contador/MAC fica
no cofre protegido. `verify()` detecta adulteração, reordenação e restauração do log anterior
ao checkpoint. Uma queda após append durável e antes do checkpoint permite reconciliar a
cauda válida sem duplicar registros; uma cauda incompleta bloqueia a operação.

Não se declara resistência universal a rollback se um administrador restaurar simultaneamente
o log, o cofre e todos os checkpoints. Esse caso requer uma âncora monotônica independente,
serviço externo ou procedimento de restauração que preserve os checkpoints atuais e invalide
material potencialmente entregue. HMAC autentica o detentor da chave, não fornece assinatura
pública atribuível a uma pessoa nem proteção contra comprometimento da conta proprietária.

Interlocks físicos, calibração, proteção elétrica/óptica e segurança experimental de material
QKD permanecem dependentes de bancada. Autenticação de software não torna material emulado
operacional nem confere qualificação física aos adaptadores.

## Ingestão OGS

O gateway WebSocket da estação terrestre recebe o mesmo `SecurityRuntime` do serviço.
Uma referência `vault:` identifica uma sessão opaca de serviço ou um token de acesso
institucional validado; nunca um papel fornecido pelo pacote. O handshake e cada mensagem
revalidam a identidade, a revogação e a permissão `ingestion.write` no recurso
`source:ogs_gateway/<canal>`; consultas de estado exigem `sources.read`. O registro autenticado
do hash da mensagem precede seu processamento e a persistência das observações. JSON ambíguo,
profundo ou não finito é rejeitado sem copiar corpos ou exceções livres para respostas/logs.

Sem a camada comum, o modo legado `env:` é permitido exclusivamente em fixtures simuladas;
origem física é recusada antes da abertura do listener. O transporte atual permanece restrito
a loopback e não constitui qualificação de TLS remoto, estação terrestre real ou dados de
passagem externos. As fixtures físicas testam contratos de ingestão, com dados artificiais
identificados e sem validar um adaptador físico.

## Entrega Qt e durabilidade

Os callbacks de atualização contínua da interface não consultam o serviço nem fazem
`fsync`: o worker prepara o snapshot, revalida a identidade e as permissões, lê as
capacidades e conclui o batch autenticado antes de completar o Future. O thread Qt
apenas encaminha esse frame às telas. As capacidades recebidas são declarações para
apresentação; não autorizam operações. APIs públicas do provider, ações de equipamentos,
entrega de chaves e os serviços mantêm suas verificações por chamada.

Durante um comando que ocupa o worker serial, um worker separado de leitura coleta
o progresso do backend e conclui os mesmos registros duráveis. Há no máximo uma coleta
pendente; seu resultado fica vinculado ao Future do comando e é descartado após sua
conclusão ou a parada da fonte. Essa mudança não altera a retenção da auditoria, o
tick de produção nem o limite de 250 ms do validador de fluidez. O batch continua
restrito a leituras: efeitos físicos e entrega de material exigem seus próprios registros
duráveis antes da ação.

A regressão foi reproduzida nas fontes e no pacote inicial, que permaneceu marcado
`DO-NOT-DISTRIBUTE` após 38/39 checks. A evidência de causa, métricas antes/depois e
22 regressões pertinentes está em `outputs/security_20261007/worker-delivery-summary.json`.
Aprovação das fontes não substitui os gates do executável reconstruído. Ações síncronas
do SDK e watchdogs de painéis visíveis ainda podem realizar IO de autorização; não se
declara eliminação universal de pausas nem garantia de tempo real.

### Pintura inicial e callbacks reentrantes

O segundo candidato também permaneceu `DO-NOT-DISTRIBUTE`: seu startup demo em
3440×1440 excedeu o limite original de 3 s de heartbeat, embora as 15 interações
funcionais fossem aprovadas. A reprodução nas fontes registrou 4,312 s. O perfil
identificou consultas de capacidades na GUI disparadas pelo painel de campanhas
e por `job.clear()` durante a invalidação de fonte dos painéis de inspeção. A
apresentação da campanha passou a consumir o frame com auditoria já persistida pelo worker;
os resets bloqueiam apenas a emissão reentrante de `changed`, mantendo gerações,
cancelamento, descarte de resultados antigos e os checks das ações reais.

Após essa correção, a primeira pintura do viewport `ResponsiveStage` ainda
demorava 4,118 s, antes do primeiro snapshot. Um experimento externo que adiou
somente o início da coleta reduziu a mesma pintura para 105 ms, sem remover
autenticação, registros ou etapas do backend. O perfil de GC não explicou a
pausa. O contraste indica contenção entre a primeira rasterização/layout Qt,
que entra em callbacks Python, e o início simultâneo dos workers.

A aplicação agora conecta um sinal de primeira pintura antes de `show()`,
emite-o uma única vez após `paintEvent` terminar e agenda `provider.start()`
para o evento Qt seguinte. Não há espera fixa nem fallback que oculte uma
falha de pintura. A identidade do SO e o `SecurityRuntime` são estabelecidos
antes da abertura; a aquisição continua usando o mesmo pipeline e autorização.
Uma janela fechada antes do callback não inicia a fonte; pinturas seguintes
não duplicam sua inicialização.

O startup real das fontes passou em 100% e 150% de escala: maiores gaps de
1,375 s e 1,312 s, primeiros snapshots em 9,547 s e 9,938 s, 15/15 interações,
zero erros e limites intactos. A primeira pintura a 100% caiu para 100 ms.
As 36 regressões direcionadas incluem auditoria durável antes da entrega,
revogação, reentrância, início único, fechamento precoce, cancelamento e
invalidação de contextos. Evidências: `outputs/security_20261007/startup-fix-summary.json`.
Esses resultados não tornam aprovado um executável anterior nem substituem
os gates da distribuição reconstruída. A primeira entrada em telas com muitos
widgets ainda pode levar mais tempo que uma atualização contínua; não se
declara resposta uniforme abaixo de 250 ms para todo carregamento inicial.

## Validação reproduzível

`tests/test_security_core.py` cobre anônimo explícito, cinco funções/escopos, ausência de
autoridade do payload, revogação/expiração, SID independente de variáveis de ambiente,
sessões opacas, assinaturas RSA/EC, adulteração JWT, tipo de token, emissor/audiência/tempo,
DPAPI alterado, rotação/recovery privilegiadas, logs sem segredos, checkpoint após queda,
backup antigo e concorrência de registros. Nenhum teste depende de hardware ou provedor OIDC.

```powershell
$env:PYTHONPATH = "src"
.audit-winvenv\Scripts\python.exe -m pytest tests/test_security_core.py -q `
  --basetemp outputs/security-core-clean-test --junitxml outputs/security-core-clean-test.xml
```
