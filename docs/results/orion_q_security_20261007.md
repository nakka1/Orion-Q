# Aceitação da segurança comum — 2026-10-07

Implementação 5.50.0, Windows, com fontes de dados simuladas identificadas e
atuação física bloqueada. Os testes abaixo validam controles de software;
não certificam segurança QKD experimental ou uma implantação institucional.

| Controle | Correção e evidência local | Limite de qualificação |
|---|---|---|
| Identidade e autorização | SID Windows, sessão revogável, OIDC `at+jwt` verificado, funções/escopos backend; acesso anônimo, claims falsos e escopos indevidos recusados | Nenhum IdP institucional conectado; login PKCE não implementado no console |
| Interface e serviços | Mesmo runtime no console, API, laboratório e workers; revogação revalidada após fila; 160 casos distintos da interface aprovados após repetições direcionadas | Testes de interface em Qt offscreen; executável tem aceitação própria |
| PKI/TLS | Certificados provisionados, renovados e revogados; SAN/serial/serviço vinculados; TLS 1.3 e revogação na mesma conexão exercitados; 52 casos distintos de PKI/chaves | CA de bancada, sem interoperabilidade institucional certificada |
| Segredos e auditoria | DPAPI, escrita atômica, MAC por registro, cadeia/checkpoint; canários de segredo ausentes; resultado/recibo vinculado por digest autenticado | Cofre CurrentUser não isola processos do mesmo SID; rotação não recifra ledgers antigos automaticamente |
| Ciclo de vida | Reserva irreversível, consumo antes da resposta, invalidação da entrega incerta; restauração de SQLite não reativa material | Rollback total de cofre, journal e banco exige âncora externa |
| Protocolo clássico | 837 mensagens autenticadas no ensaio; adulteração, replay, troca de sessão e rollback recusados; bases/estimação/reconciliação/verificação/PA vinculadas à aquisição | HMAC computacional; não estabelece sozinho segurança QKD composável; zero bits certificados |
| Equipamentos | Autoridade por série/endpoint entre processos, configuração/readback, calibração e sincronismo; falha parcial bloqueia aquisição | Interlocks, SDK/licença/replay nativo e medições físicas pendentes de bancada |
| API/OGS | Host/Origin/JSON/quotas/deadlines, quarentena sem payload, handshake TLS limitado; OGS verifica identidade/permissão em cada mensagem, 50 testes gateway/pipeline aprovados | OGS WebSocket em loopback; transporte remoto e dados de passagem reais não qualificados |
| Isolamento/manutenção | Dois KMEs e duas aplicações em processos separados; scanner de segredos no CI, hashes de fontes/dependências no bundle | Quotas por processo; contas distintas, fronteira institucional e auditoria completa de dependências exigem implantação |

## Evidências preservadas

Consolidação: **601 casos distintos aprovados, zero falhas/erros finais e zero
skips**, deduplicados por `classname` + `name`.
`outputs/security_20261007/source-validation-summary.json` registra 705
observações de casos, seus hashes e a proveniência das repetições. O resultado é
das suítes selecionadas; não representa execução de todo o corpus do repositório.
O scanner examinou 1250 arquivos selecionados, sem encontrar literais de segredos.

- `outputs/security_20261007/application-final.xml`: 24 testes de fronteira/autorização/HTTP.
- `outputs/security_20261007/artifact-cli-final2.xml`: vínculo autenticado do recibo e administração local sem segredos, 2 testes.
- `outputs/security_20261007/worker-delivery-final2.xml`: 22 testes de entrega, revogação, polling e recalibração após a correção de fluidez; 15 casos novos na consolidação e 7 repetições.
- `outputs/security_20261007/live-source-after2/report.json`: cinco percursos de gráficos, com pausas máximas de 22–98 ms nesta máquina; limite de aceitação preservado em 250 ms.
- `outputs/security_20261007/startup-regression-final.xml`: 36 casos de primeira entrega, início único, cancelamento e invalidação de contexto; 26 casos adicionais na consolidação.
- `outputs/security_20261007/startup-fix-summary.json`: reprodução da pausa de abertura, perfil Qt e comparação causal. Abertura real em demonstração passou em 100% e 150%, com maiores pausas de 1375 e 1312 ms, sem alterar o limite de 3000 ms.
- `outputs/security-ogs-pipeline-20261007.xml`: 50 testes OGS/pipeline, incluindo 5 novos controles de segurança.
- `outputs/security_20261007/ui-validation-summary.json`: proveniência dos 160 casos de interface; o agrupamento inicial e suas falhas foram preservados, com repetições direcionadas documentadas.
- `outputs/security_protocol_20261007/validation-summary.json`: aquisição, captura, protocolo, replay e exclusividade de equipamento.
- `outputs/security_validation_20261007/pki-full-01.xml` e relatórios finais complementares: PKI/consumo/restauração.
- `outputs/security_validation_20261007/laboratory-common-03/report.json`: 12/12 critérios de laboratório; quatro processos, TLS 1.3, 837 mensagens autenticadas, auditoria válida com 2964 registros/checkpoints completos.
- `outputs/security_validation_20261007/laboratory-http-native-03/integration_report.json`: 5/5 critérios da integração HTTP autenticada e interface nativa; dois recibos terminados, restauração e auditoria válida com 3123 registros/checkpoints.

Os relatórios intermediários que falharam não foram apagados. As contagens de
suítes se sobrepõem e não devem ser somadas como casos únicos. A consolidação
final registra caminhos, hashes e a regra de deduplicação.

## Operação

Leia [segurança comum](../product/orion_q_security.md),
[PKI/chaves](../product/orion_q_security_pki_keys.md),
[aquisição/protocolo](../product/orion_q_security_acquisition.md) e a
[decisão de arquitetura](../architecture/adr_security_001.md).
O ensaio TEST QKD requer cadastro explícito das quatro identidades SAE/KME;
o aplicativo não cria permissões privilegiadas silenciosamente.

## Histórico dos candidatos 5.50.0

O primeiro candidato, `5.50.0-native-20261007-161925`, passou nos testes
isolados de segurança, mas foi **reprovado em 38/39 critérios do self-test**.
Permanece marcado `DO-NOT-DISTRIBUTE`. A publicação de frames fazia consultas
e commits de auditoria síncronos na thread Qt. Os percursos em código-fonte
reproduziram pausas de 333–357 ms. A correção mantém autorização, leitura e
commit durável nos workers; a interface recebe os frames já verificados.
Nenhum registro de autorização foi suprimido e o limite de fluidez não mudou.

O ensaio isolado de segurança do executável passou em **14/14 critérios**:
identidade OS/DPAPI, cadastro e revogação pela CLI compilada, acesso anônimo
`401`, administrador `200`, Host indevido `400`, consumidor sem permissão `403`,
sessão revogada `401`, auditoria válida e ausência de tokens nos logs.
O listener criado pelo ensaio foi encerrado após a validação.

Evidências do segundo candidato: `outputs/security_20261007/frozen-security/run-20261007-170823/report.json`
e `results.xml`. SHA-256 do executável:
`80e2c2e681d3188e807a67eca7000a01af66d8ec8f56aca968a296fedec01aef`.

O segundo candidato também permanece **reprovado para distribuição**:
`startup-validation/demo-100/report.json` registrou pausa de 4157 ms, acima
do limite preservado de 3000 ms, apesar de todas as 15 interações terem passado.
Os quatro percursos de abertura em serviço passaram; o percurso de demonstração
em escala 150% não foi executado depois dessa falha. O diretório mantém
`DO-NOT-DISTRIBUTE`, sem pacote portátil aprovado. As aprovações específicas
abaixo não substituem esse gate de abertura.

Os diagnósticos gerais têm relatórios próprios em
`release/windows/5.50.0-native-20261007-170323`, vinculados ao mesmo binário.
A embalagem e o gate de integridade não foram executados nesse candidato
depois da reprovação na abertura.
Os 601 casos de código-fonte permanecem separados dos critérios congelados;
esta seção não atribui ao binário resultados obtidos somente no código-fonte.

| Verificação no segundo executável | Resultado | Evidência no diretório da versão |
|---|---|---|
| Segurança CLI/API | 14/14 | Relatório isolado citado acima |
| Interface e pipeline completos | 39/39 | `self-test.json` |
| Atualização contínua de cinco percursos | 5/5, pausas máximas de 31–159 ms nesta máquina | `live-updates/report.json` |
| Laboratório, replay e entrega incerta | 12/12 | `laboratory-validation/report.json` |
| Satélite/OGS e limitações físicas simuladas | 21/21 | `satellite-validation/report.json` |
| Rede global e admissão física simulada | 33/33 | `global-validation/report.json` |
| Controlador sob condições nominais, degradadas e falhas | 20 rodadas aprovadas | `stress-validation/` |

O laboratório congelado usou quatro processos independentes, TLS 1.3 e 837
mensagens clássicas autenticadas. Seus 2964 registros de auditoria foram
verificados com checkpoint completo e zero registros pendentes. Oito reservas
foram consumidas, o buffer terminou vazio e a recuperação de entrega incerta
passou. O material permaneceu TEST, com zero bits certificados e sem atuação
física. A latência máxima local de entrega foi 8,99 s; não se trata de um SLA.

Antes da correção de abertura, os 527 arquivos de fonte Python/JSON coincidiam
com o snapshot da segunda compilação.
`outputs/security_20261007/source-provenance-candidate-170323.json` registra a inspeção
estática dos 3913 arquivos do produto e dos módulos PyInstaller descomprimidos,
sem encontrar cofres ou credenciais privadas da aplicação. Certificados públicos
são distinguidos de chaves privadas. Essa inspeção não é uma certificação de
detecção de todo segredo codificado nem prova de equivalência por descompilação.

A segunda correção de abertura teve duas partes. Os resets e callbacks de
campanha passaram a usar as capacidades do frame já autorizado, evitando
reentrada nas consultas do serviço durante a apresentação. A coleta inicial
começa no evento Qt seguinte à primeira pintura concluída do stage; a
autenticação OS e a construção do runtime continuam anteriores à janela.
O primeiro desenho caiu de 4118 para 100 ms no perfil da implementação em
código-fonte. Não há atraso fixo, alteração de limites ou supressão de auditoria.
Os registros de 575 casos anteriores e dos dois candidatos reprovados foram
preservados. A aceitação do terceiro candidato exige sua própria validação.

## Executável aprovado nos controles locais de software

Diretório: `release/windows/5.50.0-native-20261007-175417`. SHA-256:
`14028a46f7587c85452784a0e3ea15fa2fe2eb93c41dc17cca72ac7449268c51`.

A segurança CLI/API do novo executável passou em **14/14 critérios**, incluindo
revogação, permissões indevidas e ausência de tokens nos logs. Evidências:
`outputs/security_20261007/frozen-security/run-20261007-175932/report.json`
e `results.xml`. A auditoria terminou válida com 20 registros, 20 checkpoints
e nenhum registro pendente. O listener isolado foi encerrado.

Todos os gates locais deste terceiro executável passaram. O relatório
`outputs/security_20261007/release-security-summary.json` consolida os resultados,
hashes e arquivos de evidência. Os resultados dos candidatos anteriores não
foram atribuídos a este binário.

| Verificação neste executável | Resultado | Evidência no diretório da versão |
|---|---|---|
| Segurança CLI/API | 14/14 | Relatório isolado citado acima |
| Interface e pipeline completos | 39/39 | `self-test.json` |
| Atualização contínua de cinco percursos | 5/5, pausas máximas de 29–137 ms | `live-updates/report.json` |
| Laboratório, replay e entrega incerta | 12/12 | `laboratory-validation/report.json` |
| Satélite/OGS | 21/21 | `satellite-validation/report.json` |
| Rede global | 33/33 | `global-validation/report.json` |
| Estresse do controlador | 20 rodadas, 140 ciclos e 535 verificações aprovadas | `stress-validation/` |
| Workers e recibos temporais | 25/25 | `campaign-validation/report.json` |
| Comparação do roadmap e runtime isolado | 4/4 | `roadmap-validation/report.json` |
| Modelos opcionais sem pacotes carregados | 11 modelos aprovados, sem ativar política | `optional-models-validation/optional-models-report.json` |
| Abertura em serviço e demonstração | 6/6 percursos | `startup-validation/report.json` |
| Integridade da distribuição | Manifesto, SHA-256 e CRC do ZIP aprovados | `portable-package.json` e `package-integrity-final/acceptance-report.json` |

Os limites de desempenho foram preservados: 250 ms nos percursos de atualização
contínua e 3000 ms na abertura. Os seis percursos nativos de abertura tiveram
pausas máximas de 296–890 ms e apresentaram o primeiro snapshot do backend em
7,52–9,74 s nesta máquina. O início inclui a autenticação e a preparação do
runtime. Não é um SLA para outros computadores. O redimensionamento automatizado
passou, mas o arraste físico das bordas por mouse e outra instalação Windows
não foram qualificados neste ensaio.

| Percurso de abertura | Maior pausa | Primeiro snapshot após mostrar a janela |
|---|---:|---:|
| Serviço, escala 100% | 313 ms | 8,22 s |
| Serviço, escala 125% | 297 ms | 7,56 s |
| Serviço, escala 150% | 296 ms | 7,69 s |
| Serviço, escala 200% | 312 ms | 7,52 s |
| Demonstração, escala 100% | 890 ms | 9,73 s |
| Demonstração, escala 150% | 687 ms | 9,70 s |

O laboratório deste executável utilizou o runtime comum e identidades TEST
explicitamente provisionadas em diretório vazio isolado. Dois KMEs e duas
aplicações executaram em quatro processos independentes, com TLS 1.3 e 837
mensagens clássicas autenticadas. A auditoria terminou válida com 2964 registros,
2964 checkpoints e nenhum registro pendente. As oito reservas foram consumidas,
o buffer terminou vazio e o caso de entrega incerta passou. A maior latência
local de entrega foi 9,27 s. Não foi produzido material QKD certificado:
zero bits certificados, atuação física bloqueada, hardware e replay Swabian
nativo não qualificados.

A proveniência própria foi verificada em
`outputs/security_20261007/source-provenance-final.json`: 527 fontes correspondem
ao snapshot; nenhuma credencial privada válida ou cofre foi identificado no
payload. Quatro marcadores de chave nas bibliotecas foram classificados pelo
parser como constantes, com os resultados de inspeção registrados separadamente.

O ZIP contém o executável, bibliotecas, instruções e este relatório. A revisão
final do relatório foi empacotada com o mesmo executável; a primeira embalagem
e seus manifestos foram preservados em `packaging-before-final-report/`.
O SHA-256 do ZIP consta em `portable-package.json` e no arquivo `.zip.sha256`;
não é inserido no próprio ZIP para evitar uma referência circular. A aceitação
de integridade verifica somente arquivos e não substitui os testes funcionais
listados acima nem a qualificação manual ou institucional.

Continuam pendentes a conexão com um IdP institucional e seu fluxo de login,
PKI interoperável com a instituição, contas de serviço separadas, uma âncora
externa contra restauração conjunta de todos os estados e rotação/recifração
operacional dos ledgers existentes. OGS externa, interlocks, calibração,
sincronismo e segurança QKD experimental dependem de qualificação na bancada.
A autenticação HMAC implementada é computacional; não estabelece um orçamento
de segurança composável nem autenticação de informação teórica. Os mecanismos
implementados não são apresentados como certificação dessas propriedades.

### Errata do recibo legado de laboratório

O array `limits` do `qkd/qkd_report.json` ainda contém a descrição de wrapping
keys efêmeras e de ausência de cofre/PKI. Essa descrição aplica-se à execução
legada sem `security_runtime`; não descreve o ensaio comum desta versão. Neste
ensaio, `independent_pair` obtém as wrapping keys do `SecretVault` persistente,
protegido por DPAPI no Windows, fora dos bancos de chaves. As evidências
`os_secret_vault` e `outside_key_database` do mesmo recibo registram esse modo.
Também existe PKI provisionada para o ensaio. Nenhum desses mecanismos foi
qualificado como cofre ou PKI institucional de produção. Preservamos o recibo
e seu hash original, com esta correção explícita da interpretação; não se deve
inferir que encerrar o processo destrói as wrapping keys persistidas.
