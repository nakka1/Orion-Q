# Segurança de certificados e ciclo de vida de chaves do ORION-Q

Implementação verificada em 7 de outubro de 2026. Os controles abaixo são
controles de software, testáveis sem equipamentos quânticos. Eles não qualificam
uma fonte QKD, um módulo criptográfico, interlocks ou uma operação física.

## Identidade, certificados e TLS

`security/pki.py` contém `OperationalPKI`, `CertificatePolicy` e o perfil TLS.
O administrador provisiona identidades no `SecurityRuntime` antes de emitir
certificados. A emissão vincula SAN URI `urn:orion:identity:<identidade>`, número
de série, impressão SHA-256, finalidade SAE/KME/serviço e serviços permitidos.
Registros do catálogo têm MAC; alterar permissões diretamente no SQLite não
cria uma autorização válida. A CA usa ECDSA P-256; a validade de folhas é de
1–90 dias, com 30 dias por padrão. Essa escolha é um perfil de implementação,
não um prazo universal derivado de norma.

Provisionar, renovar e revogar exige `security.manage` por administrador.
A renovação persiste a substituta antes de revogar o certificado anterior.
`server_context()` e `client_context()` exigem `credentials.use` no recurso
`certificate:<identidade>`. As chaves PEM privadas são cifradas; as senhas ficam
no `SecretVault` e não são devolvidas no cadastro nem incluídas nos recibos.
No Windows, o perfil operacional usa DPAPI CurrentUser. Uma conta de serviço
distinta precisa de provisionamento próprio ou de um cofre externo autorizado;
copiar o arquivo de outro usuário não constitui esse provisionamento.

O cofre oferece referências versionadas para rotação e recuperação autorizada.
Quando o runtime é fornecido, os wrapping keys dos KMEs emulados são obtidos do
cofre, separados da origem QKD. A rotação de referência não recifra
automaticamente um ledger AES-GCM existente: re-encriptação/migração coordenada
entre processos ainda não está implementada ou qualificada para operação.
Ledgers desta bancada permanecem exclusivamente TEST; essa limitação não pode
ser contornada marcando material emulado como operacional.

TLS tem mínimo e máximo **TLS 1.3**, certificados mútuos e compressão desativada.
Tickets de servidor são desabilitados neste perfil; não existe entrega com
0-RTT. A decisão de aplicação consulta novamente revogação e vínculos em
**cada requisição**, inclusive na mesma conexão TLS estabelecida. Validar a CA
no handshake não concede permissão para consumir uma chave nem protege, por
si só, uma conexão já aberta contra revogação posterior.

O emissor local atual inclui SAN `localhost` e `127.0.0.1` para servidores.
Uso institucional com DNS/endpoints externos exige certificados emitidos com
SAN apropriado ou contexto fornecido por uma PKI institucional, além da
configuração de Host e da autorização do serviço na API. Não há integração
qualificada com HSM, OCSP ou CA de terceiros nesta entrega. A validade e a
revogação do emissor local são controladas pelo catálogo autenticado.

## Chaves e recuperação

`lab/persistent_keys.py` preserva AES-GCM, transações SQLite `BEGIN IMMEDIATE`,
`synchronous=FULL`, bloqueio de proprietário entre processos e consumo antes
da resposta HTTP. Uma reserva é irreversível. Os estados distinguem disponível,
reservado, autorizado na ponta remota, emitido, consumido, resposta enviada sem
confirmação, invalidado e expirado. Uma escrita no socket não prova recebimento
pela aplicação. Repetir o token dá apenas estado; não retorna material.

`security/key_lifecycle.py` acrescenta `ConsumptionJournal`: MAC HMAC-SHA-256,
encadeamento, sequência e checkpoint protegido. Reserva, emissão, consumo,
expiração e invalidação são gravados e sincronizados **antes** de disponibilizar
material ou mudar o SQLite. Uma queda entre journal e SQLite conserva a decisão
mais restritiva: os IDs são queimados na reconciliação. Não se afirma transação
distribuída entre os dois arquivos. A autorização remota e a confirmação de
escrita HTTP são mantidas no SQLite; a reserva/emissão já registrada no journal
é suficiente para impedir que elas devolvam material ao estado disponível.

Com `SecurityRuntime`, o journal fica por padrão em
`<security.root>/key-recovery`, e o checkpoint fica no cofre. A autoridade usa
`<security.root>/pki-recovery` para revogação. Ambos são domínios de recuperação
independentes dos backups dos bancos de chaves/certificados. Sem runtime, os
ensaios antigos usam checkpoint local identificado como **test_local_file**;
isso não é o perfil operacional.

Ao restaurar somente o banco SQLite, registros de material anteriormente
reservado, emitido ou consumido tornam-se invalidáveis/invalidado e jamais
reaparecem no buffer. IDs removidos do banco por um backup antigo continuam
impedidos de ser reprovisionados pelo journal. Apagar, truncar ou modificar
journal/checkpoint bloqueia a entrega. Identificadores e tokens necessários à
reconciliação ficam nesse armazenamento protegido; bytes de chave nunca são
incluídos no journal nem nos relatórios de auditoria.

Na atualização de um ledger legado, o consumo já persistido é importado para o
journal autenticado antes de uma nova entrega. Essa migração exige autoridade
de invalidação e preserva os IDs e estados irreversíveis já conhecidos. Ela não
pode reconstruir acontecimentos que não existam em nenhuma evidência preservada.

**Limite técnico:** restaurar conjuntamente banco, journal e checkpoint/cofre
para um mesmo passado elimina a evidência local de acontecimentos posteriores.
Um arquivo local não fornece contador monotônico contra rollback integral da
máquina. A implantação deve preservar journal/cofre fora do domínio restaurado,
com autoridade de recuperação separada; resistência a rollback integral exige
âncora externa independente ou contador confiável qualificado. Essa dependência
não está apresentada como concluída. Recuperar um checkpoint perdido exige
reconciliação administrativa com evidências preservadas; o produto não recria
automaticamente o estado e não reativa material incerto.

## Bancada emulada e permissões mínimas

Material BB84 emulado continua inseguro para uso operacional. O ledger admite
somente `usage="test"`; o pedido HTTP não pode elevar isso. Certificados de
ensaio usam namespace separado `urn:orion:lab:` e uma CA efêmera. Endpoints com
essa configuração não aceitam servir esse material como operação física.
`certified_secret_bits` permanece zero. Fixar a identidade de um serviço não
fixa permissões para sempre: o processo consulta os grants atuais por chamada.

A demonstração integrada deve ter estes principals explicitamente cadastrados
por administrador **antes** de executar. A API/tela não cria esses grants:

| Identidade | Papéis | Recursos necessários | Operações |
|---|---|---|---|
| `kme-alice` | operador, aplicação consumidora | `keys:kme-alice`, `keys:kme-bob` | consumo, status, provisionamento TEST, invalidação e coordenação entre pontas |
| `kme-bob` | operador, aplicação consumidora | `keys:kme-alice`, `keys:kme-bob` | consumo, status, provisionamento TEST, invalidação e coordenação entre pontas |
| `sae-alice` | aplicação consumidora | `keys:kme-alice` | consumo e status da própria ponta |
| `sae-bob` | aplicação consumidora | `keys:kme-bob` | consumo e status da própria ponta |

Os nomes de papéis nos contratos são `operator` e `consumer`. Os helper
`required_kme_identities()` e `check_kme_security(runtime)` expõem as dependências
para disponibilidade e formulários; ausência ou escopo insuficiente bloqueia a
execução antes de criar a pasta da campanha. Configurações pré-existentes nunca
são ampliadas automaticamente. A limpeza conservadora de uma entrega falha usa
a identidade autorizada do KME; uma aplicação não recebe poder de invalidar
livremente chaves de outra aplicação.

O ensaio usa dois processos KME e dois processos SAE independentes, mTLS real,
armazenamentos próprios e chaves de cifração no cofre quando o runtime é
fornecido. Os deadlines são finitos: 15 s por requisição da aplicação e 10 s na
coordenação entre KMEs. Foram aumentados após reprodução de latência de 1,39–
6,09 s sob seis consumidores concorrentes com journal/checkpoint sincronizados;
o limite antigo de 5 s expirava enquanto a operação durável ainda terminava.

## Evidências e execução

`tests/test_security_pki_keys.py` verifica:

| Caso | Evidência exigida |
|---|---|
| Oito combinações de restauração disponível/reservado e emitido/incerto/consumido/invalidado | material previamente exposto ou reservado não volta ao buffer; IDs não são reprovisionáveis |
| Aborto após commit do journal e antes do SQLite | reconciliação conservadora queima material |
| Journal truncado/adulterado ou checkpoint inválido | inicialização e entrega bloqueadas |
| Expiração e restauração posterior | material expirado permanece inutilizável |
| Concorrência entre consumidores | IDs únicos, sem lote parcial nem duplicação |
| TLS 1.3 e revogação com conexão aberta | a segunda chamada na mesma conexão é recusada |
| Renovação e restauração do registro de certificados | certificado antigo continua revogado |
| Serviço/SAN inadequado ou catálogo adulterado | autorização recusada |
| Segredos em PEM e auditoria | PEM cifrado, ausência de senha/material nos registros |
| Dois processos KME com escopos e revogação atualizados | recurso errado é negado sem consumo; grant correto e certificado não revogado permitem a operação TEST |

Primeira execução: **21 casos aprovados**, 87,66 s. Os ensaios preexistentes de
consumo/conservação e o fluxo completo de falhas foram incluídos na execução
conjunta: **48 casos aprovados**, 300,47 s, incluindo a campanha com quedas reais
de processos e perdas de resposta. XML `pki-full-01.xml` e resultados estão em
`outputs/security_validation_20261007`. Execução local:

Após integração dos gates, os sete casos finais de identidade, renovação,
revogação, restauração e migração passaram em 26,14 s; os três casos afetados pela
remoção da autorização duplicada no Handler passaram novamente em 43,67 s.
O aceite de laboratório compartilhando runtime passou **12/12 gates**, incluindo
837 mensagens clássicas autenticadas, TLS 1.3 e 2.964 registros de auditoria
válidos com checkpoint completo (`laboratory-common-03/report.json`). Esse
harness cadastra explicitamente os principals de teste em diretório de aceite
novo e isolado; não modifica o cadastro padrão do usuário. Os gates mantêm
`certified_secret_bits=0` e `hardware_validated=false`.

O teste adicional de HTTP autenticado e botão nativo passou **5/5 verificações**
em `laboratory-http-native-03/integration_report.json`: execução integral,
despacho real do botão, leitura do mesmo journal, reprodução das métricas e
recibos preservados após reinício. A imagem `native_laboratory.png` registra o
controle exercitado. As primeiras tentativas foram preservadas: foi corrigido
o import pesado do dashboard no topo do script, que era reexecutado nos filhos
Windows e consumia o prazo de inicialização/recepção dos SAEs. O import agora
ocorre somente no processo principal; os filhos usam seus adaptadores mínimos.

```powershell
$env:PYTHONPATH = "src"
.audit-winvenv\Scripts\python.exe -m pytest tests\test_security_pki_keys.py tests\test_lab_key_ledger.py tests\test_lab_qkd.py -q --basetemp outputs\security-validation-new --junitxml outputs\security-validation.xml
```

Use uma pasta temporária nova para cada execução; preserve as evidências antigas.
Os testes operacionais da PKI exigem Windows DPAPI ou um serviço de cofre
equivalente implementado. Os testes não exigem fonte de fótons.

## Fontes primárias

Consultadas em 7 de outubro de 2026:

- [NIST SP 800-57 Part 1 Rev. 5, maio de 2020](https://csrc.nist.gov/pubs/sp/800/57/pt1/r5/final): proteção, inventário, estados, recuperação e responsabilidades do material criptográfico. A implementação aplica princípios de gestão; não declara certificação NIST.
- [RFC 8446, agosto de 2018](https://www.rfc-editor.org/rfc/rfc8446.html): TLS 1.3 e suas diferenças de autenticação, resumption e early data. O perfil do ORION-Q proíbe early data para entrega de chaves.
- [RFC 5280, maio de 2008](https://www.rfc-editor.org/rfc/rfc5280.html): certificados X.509, SAN, finalidade e revogação. O catálogo autorizado complementa a verificação de cadeia por TLS.
- [ETSI GS QKD 014 V1.1.1, fevereiro de 2019](https://www.etsi.org/deliver/etsi_gs/QKD/001_099/014/01.01.01_60/gs_qkd014v010101p.pdf): subconjunto REST de entrega SAE/KME. Coordenação entre KMEs, token de entrega, journal e estado incerto são extensões explicitamente locais, não requisitos de interoperabilidade certificados.
