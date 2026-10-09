# Segurança comum do ORION-Q

Versão de implementação: 5.50.0. Revisão: 2026-10-07. Os mecanismos abaixo são
controles de software testáveis localmente. Não constituem certificação QKD,
qualificação de interlocks ou autorização para atuação física.

## Identidade e autorização

`SecurityRuntime` é a fronteira comum. O console nativo usa o SID do token do
processo Windows; não confunde a seleção de fonte com autenticação. Os serviços
validam a identidade, a sessão, a função e o recurso em cada chamada. Console,
serviço principal, laboratório e adaptadores compartilham a mesma instância.
Workers preservam a identidade recebida; não herdam silenciosamente privilégios
administrativos do processo servidor. A revogação impede novas operações,
inclusive após uma tarefa já ter sido colocada na fila.

| Função | Operações admitidas, dentro dos recursos cadastrados |
|---|---|
| `administrator` | Administração explícita de identidades, certificados, recuperação e cofre |
| `operator` | Configuração, aquisição autorizada, experimentos, operação e leitura |
| `researcher` | Leitura, simulações e exportação de evidências; sem controle de equipamento |
| `auditor` | Leitura e inspeção; sem alterar configuração ou consumir chaves |
| `consumer` | Consumo/estado de chaves e uso das referências de credenciais permitidas |

As permissões são definidas no backend. Tokens, JSON, componentes Qt e claims de
funções fornecidos pelo cliente não podem criar privilégios. Escopos aceitam
identificadores exatos ou somente um wildcard final, como
`workspace:E:/ORION/state/*`, `device:SERIAL123` e `keys:kme-alice`. Uma função sem
escopo correspondente é recusada. A administração usa recursos `security:*`;
consultar a própria identidade usa `security:identity`.

No primeiro uso local, o proprietário autenticado pelo OS inicializa a política
do seu diretório. Não use diretórios novos para evitar a política de uma
implantação existente. Serviços provisionados devem usar `bootstrap=False` e
contas próprias. Código Python arbitrário executado pela conta do serviço é
confiável nesta fronteira; não se trata de uma sandbox para plugins hostis.

Uso institucional: `OIDCConfig` configura issuer HTTPS, audience da API, JWKS
confiável, algoritmos RSA/EC e limites temporais. O verificador aceita tokens de
acesso assinados do perfil `at+jwt`, não ID tokens usados como bearer. Valida
assinatura, emissor, destinatário, validade e claims obrigatórios. Identidades
`oidc:<issuer>:<sub>` precisam de cadastro administrativo próprio. A obtenção de
tokens, login Authorization Code/PKCE, rotação/distribuição de JWKS e conexão a
um provedor institucional real permanecem responsabilidades da implantação;
nenhum IdP externo foi conectado nesta validação.

## API e ingestão

Loopback não autentica um cliente. Rotas `/api/` exigem bearer opaco emitido pelo
processo autenticado, token institucional válido ou certificado cliente
provisionado. A API não oferece emissão anônima de sessão. Cookies e credenciais
na URL não são usados. Com bearer explícito e sem cookie de sessão, não há
credencial ambiente para CSRF; a allowlist de Origin continua obrigatória quando
esse cabeçalho estiver presente. Os arquivos públicos da aplicação não expõem
dados operacionais.

`HTTPPolicy` valida Host/porta, Origin, cabeçalhos duplicados e tamanho. Rejeita
transfer encoding, formatos diferentes de JSON, campos duplicados, NaN/Infinity,
objetos de profundidade excessiva e schemas incompatíveis. Os contratos dos
adaptadores validam unidades, canais, intervalos e configuração experimental.
Registros recusados vão à quarentena com razão, tamanho e SHA-256, sem corpo,
credenciais ou material de chave. A quarentena tem retenção limitada.

O servidor limita workers e requisições por identidade e endereço. Há deadline
para leitura e admissão de operações e verificação nos serviços chamados. Uma
operação síncrona que já começou não é artificialmente cancelada nem apresentada
como revertida: em caso de falha de resposta, consulte recibos e estados duráveis.
As quotas são locais ao processo; uma implantação distribuída deve fornecer
controle de admissão compartilhado na sua fronteira institucional.

Transporte operacional externo exige perfil operacional, allowlist explícita,
TLS 1.3, certificado cliente obrigatório e autorização de certificado a cada
requisição. `create_server(..., ssl_context=..., peer_authorizer=...)` recebe essas
dependências. Certificados de bancada e exemplos loopback não substituem uma CA
institucional. Consulte [PKI e chaves](orion_q_security_pki_keys.md).

O gateway OGS recebe registros já decodificados de housekeeping, tracking e TDC,
com separação dos canais preservada. Com o runtime comum, exige credencial
`vault:<referência>` vinculada a uma identidade provisionada. Revalida sessão,
revogação e permissões em cada mensagem: `ingestion.write` para entrada e
`sources.read` para consulta, com recurso `source:ogs_gateway/<tipo>`. O listener
permanece WebSocket em loopback; sua disponibilidade não comprova integração
RF/CCSDS, transporte remoto seguro ou calibração de uma OGS real. A alternativa
legada `env:` sem runtime só atende fixtures explicitamente simuladas; fontes
físicas e arquivos de medição não podem usar essa alternativa.

## Cofre, auditoria e recuperação

No Windows, o cofre usa DPAPI `CurrentUser`, escrita atômica e lock entre
processos. Chaves privadas de CA e certificados são cifradas; senhas são
referências ao cofre. No Unix, o fallback 0600 é somente local: o perfil
operacional recusa esse fallback. Contas Windows diferentes precisam de cofres
provisionados para suas identidades; processos sob o mesmo SID não ficam
isolados entre si apenas pelo DPAPI.

A auditoria registra identidade, operação, recurso, configuração por hash e
resultado. HMAC, encadeamento, sequência e checkpoint protegido detectam
adulteração e restauração atrás do checkpoint. Logs e erros não recebem payloads
arbitrários ou segredos. Todos os registros de autorização são preservados.
Leituras de um frame podem ser gravadas em lote; cada registro conserva seu MAC
e a gravação termina antes de o frame retornar. Aquisição física e entrega de
chaves não usam esse agrupamento de leituras.

Recibos históricos com somente SHA-256 continuam evidências de integridade;
não passam a comprovar autoria retroativamente. O novo log autenticado registra
as ações autorizadas que produzem ou inspecionam essas evidências.

Reservas, consumo, entrega incerta, invalidação e revogação usam registros
irreversíveis separados do backup do banco. Uma restauração antiga de SQLite é
reconciliada antes de admitir material. Âncoras ausentes, truncadas ou divergentes
bloqueiam recuperação. Restaurar também o cofre e todos os registros protegidos
para uma versão antiga exige uma autoridade externa independente para detectar
rollback total; o software não promete resolver essa impossibilidade sozinho.

Rotação de segredos é explícita e versionada. Restaurar uma referência de segredo
exige autorização de recuperação; não remove estados de consumo. A migração da
chave AES-GCM deve preservar versões históricas e os tombstones do ledger, usando
o contrato documentado de chaves, nunca apenas substituindo arquivos do banco.
Rotacionar o cofre não recifra automaticamente ledgers AES-GCM existentes;
essa migração e seu procedimento de recuperação precisam de qualificação própria.

## Equipamentos e protocolo

Controle físico exige autoridade compartilhada por implantação, série e
endpoint verificados, sessão válida, readback, calibração e sincronismo. Falha
parcial de configuração bloqueia aquisição. A desconexão ou mudança de sessão
revoga os requisitos de controle. Consulte
[aquisição e protocolo](orion_q_security_acquisition.md) para configuração e
condições de qualificação.

Mensagens de bases, amostragem, reconciliação, verificação e amplificação de
privacidade são vinculadas à sessão, sequência e registros de aquisição. MACs
adulterados, sessões trocadas e repetição são recusados antes do processamento.
O modelo atual de autenticação é computacional; não estabelece sozinho um
orçamento composável de segurança QKD. Observações emuladas continuam TEST e
produzem zero bits certificados. Nenhuma execução local admite esse material
para proteger comunicação operacional.

## Administração local e execução

Com `PYTHONPATH=src`, use o mesmo diretório de segurança da implantação:

```powershell
python -m quantum_autopilot.security.cli --root E:\ORION\state\.security status
python -m quantum_autopilot.security.cli --root E:\ORION\state\.security verify-audit
python -m quantum_autopilot.security.cli --root E:\ORION\state\.security grant auditor-1 --roles auditor --scopes "workspace:E:/ORION/state/*" "security:identity"
python -m quantum_autopilot.security.cli --root E:\ORION\state\.security revoke auditor-1
```

No pacote portátil, a mesma administração está disponível sem instalação Python:

```powershell
.\ORION-Q.exe --security-admin --root E:\ORION\state\.security --report E:\ORION\status.json status
```

Use o diretório `.security` do estado escolhido ao iniciar o aplicativo. Para o
ensaio exclusivamente TEST, o administrador pode provisionar explicitamente:

```powershell
.\ORION-Q.exe --security-admin --root E:\ORION\state\.security grant kme-alice --roles operator consumer --scopes keys:kme-alice keys:kme-bob
.\ORION-Q.exe --security-admin --root E:\ORION\state\.security grant kme-bob --roles operator consumer --scopes keys:kme-alice keys:kme-bob
.\ORION-Q.exe --security-admin --root E:\ORION\state\.security grant sae-alice --roles consumer --scopes keys:kme-alice
.\ORION-Q.exe --security-admin --root E:\ORION\state\.security grant sae-bob --roles consumer --scopes keys:kme-bob
```

As rotas administrativas autenticadas são `POST /api/v1/security/identities` e
`POST /api/v1/security/revocations`. `GET /api/v1/security` consulta a própria
identidade. Os campos de cadastro são `subject`, `roles`, `scopes` e `source`.
Não envie segredos como identificadores ou escopos.
`POST /api/v1/security/sessions` permite ao administrador emitir uma sessão para
uma identidade já cadastrada e armazená-la no cofre; recebe `subject` e
`vault_reference`, limitada ao namespace `api-session:`. Retorna somente a
referência, nunca a credencial. Uma sessão pertence ao processo que a emitiu;
não use uma CLI separada para supor que ela autoriza um servidor já iniciado.

O ensaio QKD exige cadastro explícito de `kme-alice`, `kme-bob`, `sae-alice` e
`sae-bob` com os escopos mínimos de
[PKI e chaves](orion_q_security_pki_keys.md). O laboratório mostra identidades
ausentes/incompatíveis e desabilita os ensaios afetados. Não cria esses privilégios
automaticamente. Os validadores de aceitação provisionam fixtures somente nos
seus diretórios descartáveis de teste, como parte explícita do ensaio.

```powershell
python -m pytest tests/test_security_core.py tests/test_security_console.py tests/test_security_application.py tests/test_security_pki_keys.py tests/test_security_protocol_equipment.py
python tools/check_security_sources.py --output outputs/security/source-scan.json
```

Use `serve-local` na CLI para iniciar uma API local e gravar sua sessão em uma
referência cifrada do cofre. A saída mostra somente porta e nome da referência.
Sessões são revogáveis e expiram; reiniciar o servidor invalida sessões antigas.
O dashboard recebe a sessão local por fragmento removido imediatamente e a mantém
somente em memória, sem imprimir a URL com a credencial.

## Isolamento e manutenção

Separe contas e processos de controle, aquisição e entrega. Proteja por ACL os
cofres, a autoridade comum de equipamentos e os checkpoints. Libere somente
endpoints previstos na configuração e certificados vinculados ao serviço. O
ensaio com quatro processos comprova separação de estado e comunicação local;
não comprova isolamento de contas ou rede de uma instalação real.

O scanner de fontes roda também no CI e relata somente arquivo, linha e categoria.
É uma heurística para literais de credenciais e chaves privadas, não uma inspeção
completa do histórico Git. Os bundles registram hashes e dependências; atualizações
devem repetir a aceitação e comparar esses artefatos. Não foi alegada uma auditoria
de vulnerabilidades completa de todas as dependências transitivas.

## Fontes verificadas

Consulta em 2026-10-07; critérios de implementação e limites:

- [NIST SP 800-57 Part 1 Rev. 5](https://csrc.nist.gov/pubs/sp/800/57/pt1/r5/final), 2020: ciclo de vida e proteção do material de chave.
- [RFC 8446 — TLS 1.3](https://www.rfc-editor.org/rfc/rfc8446), 2018: perfil de transporte solicitado.
- [RFC 9068 — JWT access tokens](https://www.rfc-editor.org/rfc/rfc9068), 2021: distinção entre token de acesso e ID token.
- [OpenID Connect Core](https://openid.net/specs/openid-connect-core-1_0.html): identidade institucional; obtenção de tokens depende do IdP configurado.
- [Microsoft CryptProtectData](https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptprotectdata): vínculo de proteção DPAPI ao usuário e contexto de recuperação.
- [ETSI GS QKD 014 V1.1.1](https://www.etsi.org/deliver/etsi_gs/QKD/001_099/014/01.01.01_60/gs_qkd014v010101p.pdf): subconjunto da interface de entrega exercitado na bancada, sem certificação de interoperabilidade.
