# Segurança da aquisição e do protocolo clássico

Atualização: 2026-10-07. Esta camada verifica autoridade de software e autentica
mensagens. Não qualifica instrumentos, fontes quânticas nem interlocks físicos.

## Contrato comum de equipamentos

`create_photon_adapter(adapter_id, options, security=runtime, principal=identity,
authority=authority)` constrói `AuthorizedPhotonAdapter`. Todas as operações de
controle e aquisição passam por `SecurityRuntime.authorize` com recursos
`device:<serial>` e ações `adapters.configure`, `adapters.acquire`, `adapters.read`.
O consumidor não recebe o objeto nativo ou a referência ao SDK.

No uso local, a identidade do processo é vinculada à sessão revogável
`runtime.device_session_identity()`. A revogação não renova automaticamente a
sessão. Um serviço institucional fornece a identidade autenticada explicitamente.
Um contexto anônimo não obtém a identidade local por fallback.

Para aquisição física é obrigatória uma série identificada e um endpoint. A
autoridade mantém um lock do sistema operacional por fabricante/série, de forma
que USB e TCP do mesmo instrumento não possam ser controlados simultaneamente.
Um registro SQLite durável associa endpoint, identidade, sessão, token interno,
geração de fencing e prazo de validade. A morte do processo libera o lock; a
próxima conexão incrementa a geração e exige nova configuração e calibração.
O vencimento da concessão bloqueia o titular, mas não permite que outro processo
roube o instrumento enquanto o primeiro ainda possui o lock.

Todos os processos de uma implantação devem usar o mesmo diretório de
`EquipmentAuthority`, provisionado pelo administrador e protegido por ACL. Esse
diretório não é um parâmetro aceito da API. Autoridades em diretórios independentes
não coordenam entre si; contas com permissão de alterar os arquivos ou executar
diretamente o SDK continuam fora do isolamento desta camada cooperativa. A
implantação física deve separar contas de aquisição e controle e restringir o
acesso direto ao equipamento.

Requisitos antes de adquirir:

| Requisito | Verificação | Bloqueio/recuperação |
|---|---|---|
| Identidade e sessão | Permissão e validade em cada operação | Revogação ou mudança de sessão exige reconexão |
| Exclusividade | Lock OS, token e geração durável | Processo concorrente recusado |
| Configuração | Validação completa antes dos setters e readback de todos os canais | Falha parcial invalida a configuração; somente leitura/verificação explícita pode recuperá-la |
| Calibração | Resultado nativo e validade temporal configurada (padrão conservador: 300 s) | Calibração expirada bloqueia aquisição |
| Sincronização | Referência externa habilitada e travada | Relógio interno livre não comprova sincronismo da experiência |
| Captura contínua | Autoridade, sessão, readback e relógio antes de cada drenagem | Interrupção preserva blocos já comprometidos e marca captura como falha |

O diagnóstico `autoCalibration` do Time Tagger não comprova calibração óptica,
jitter, eficiência ou sincronismo entre fontes e analisadores. Os 300 s são um
prazo de política ajustável após caracterização, não uma validade científica
universal. A precisão e a tolerância temporal da experiência ainda devem ser
qualificadas na bancada. Falhas de aplicação, desconexão e readback divergente
revogam os requisitos de aquisição; não se mantém a configuração antiga como
se tivesse sido aplicada ao instrumento.

O adaptador Swabian de baixo nível recusa configuração, calibração e aquisição
física sem o guard instalado pelo wrapper autorizado. Os doubles de testes usam
um argumento de construtor Python `test_only_transport=True`, ausente das opções
da API/registry. Esse caminho bloqueia um SDK nativo instalado e grava origem
`simulated`, modo `emulation` e `test_only_transport` explícitos. Não é autorização
para atuar em equipamento ou promover uma fixture a medição física.

## Mensagens clássicas autenticadas

`AuthenticatedClassicalChannel` usa uma referência de chave separada no cofre,
sem provisionamento implícito a partir dos bits QKD. O envelope
`orion.security.qkd-classical.v1` contém identificadores da sessão, emissor e
receptor, sequência, operação, hash vinculando preparação/análise/relógio,
hash do envelope anterior, payload e referência da chave. Todos esses campos
participam do HMAC-SHA256. O receptor valida MAC, identidade, sessão, operação
esperada e payload efetivo antes de avançar atomicamente a sequência em SQLite.

A validação recusa adulteração, reflexão de sentido, sessão diferente,
repetição, lacunas e mensagens fora de ordem. O reinício preserva o estado de
replay. Uma perda entre envio e recebimento requer reenvio do mesmo envelope
original ou aborto da sessão; não se ignora a lacuna. As transações guardam apenas
sequência, referência e hashes, sem bits brutos, material de chave ou segredo MAC.

O BB84 observado autentica o compromisso de observação, seleção de bases,
divulgação da amostra X, permutações e cada paridade efetivamente usada na
reconciliação, tags de verificação e semente pública de amplificação. A validação
usa os mesmos registros que o estimador; não há resultados finais injetados.
`exchange` é o transporte local de ensaio: outro transporte pode enviar os mesmos
envelopes entre processos/serviços usando `send` e `receive`.

O pós-processamento central local agrupa um bloco em `atomic_processing`: todas
as mensagens continuam autenticadas e ordenadas, mas a transação SQLite é
comprometida uma vez antes de liberar o resultado. Uma interrupção reverte o
bloco inteiro, sem retornar material ou admitir chaves. Observações físicas e
simuladas usam a mesma fronteira de processamento local. Não é permitido colocar
transporte externo, comando de equipamento ou entrega de chaves nesse contexto;
as chamadas externas `send`/`receive` conservam commits por mensagem. A auditoria
agrupada conserva todos os eventos e sela o checkpoint antes do retorno.

Na validação Windows com checkpoint contra restauração, o bloco padrão de 80.000 pulsos completou em 10,688 s:
837 mensagens autenticadas, 214.272 bits de tags, 2.048 bits TEST, zero bits
certificados e 2.515 registros auditados com checkpoint verificado. A medição é
local, não um limite de desempenho para instrumentação. Evidência:
`outputs/security_protocol_20261007/benchmark-default-4/default_qkd_benchmark.json`.

No cofre operacional disponível, os estados de sequência e cadeia têm um
checkpoint protegido independente do SQLite. Restaurar apenas uma cópia antiga
do journal ou interromper a gravação entre checkpoint e commit deixa a sessão
em quarentena. A recuperação exige um novo identificador; não se reduz o contador
para voltar a aceitar mensagens antigas. Fixtures com cofre somente de leitura
não afirmam essa proteção (`backup_rollback_protected=False`). Uma restauração
integral do cofre e do journal para o mesmo estado antigo exige uma âncora externa
de recuperação/implantação; este controle local não fornece contador físico
antirrollback resistente a um administrador do sistema operacional.

O relatório separa os 256 bits de reserva da chave MAC por sessão, o número de
mensagens autenticadas, os bits de tags transmitidos e o consumo de chave QKD
para autenticação (zero neste modelo HMAC com chave separada reutilizável).
O estimador cobra pelo menos 256 bits como reserva conservadora do orçamento,
além de uma reserva maior explicitamente configurada. Isso não transforma
tráfego de tags em consumo de OTP. HMAC oferece autenticação computacional;
não fornece por si só autenticação informação-teórica ou um limite de erro
componível para QKD. Os campos correspondentes continuam explicitamente falsos.

Fixtures sem assinatura podem ser importadas e processadas como evidência TEST.
`purpose="operational"` recusa tanto material simulado quanto medições físicas
ainda sem qualificação experimental. A central conhece ambas as cadeias de
bits; fontes, detectores, aleatoriedade e prova de segurança continuam pendentes.
Nenhuma nova chave é declarada certificada por esses controles de software.

## Validação reproduzível

```powershell
$env:PYTHONPATH = 'src'
.audit-winvenv\Scripts\python.exe -m pytest tests\test_security_protocol_equipment.py tests\test_lab_photon.py tests\test_lab_swabian_hardening.py tests\test_lab_observed_qkd.py tests\test_lab_observations_capture.py -q
```

Os testes incluem adulteração em nove campos, replay após reinício, reflexão,
sequência indevida, disputa simultânea pelo mesmo envelope, contador e orçamento
de autenticação, bloqueio operacional de fixtures, autoridade entre processos,
queda do processo proprietário, incremento do fencing, exclusividade mesmo
com lease expirada, configuração incerta, sessão alterada, calibração vencida,
ausência de sincronismo, chamada física direta sem autoridade e sessão revogada.
Os doubles são identificados; nenhum instrumento foi qualificado nesses testes.

Evidências em `outputs/security_protocol_20261007`: 127 casos distintos aprovados
na união de `regression-final.xml`, `capture-final.xml` e `security-tests.xml`
(29 casos de segurança específicos). A demonstração autenticada de fótons
validou nominal, replay, substituição, sete falhas e recuperação, com 186
registros auditados e checkpoint íntegro; os três adaptadores declaram
`security_enforced=True`. O resumo com hashes dos fontes e das evidências está
em `validation-summary.json`.

## Fontes e limites

Consulta em 2026-10-07:

- [ETSI GS QKD 016 V2.1.1, janeiro de 2024](https://www.etsi.org/deliver/etsi_gs/QKD/001_099/016/02.01.01_60/gs_QKD016v020101p.pdf): o canal clássico autenticado identifica o emissor e protege as mensagens contra modificação. A implementação não implica certificação pelo perfil.
- [NIST SP 800-38B, apêndice C](https://nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.800-38b.pdf): a verificação de um MAC sozinha não impede replay; informações de sequência/sessão precisam fazer parte do protocolo autenticado. Essa regra motivou o estado durável, independentemente da escolha de HMAC neste código.
- [IETF RFC 2104, fevereiro de 1997](https://www.rfc-editor.org/rfc/rfc2104.html): construção de HMAC com chave secreta. O uso de SHA-256 neste projeto é autenticação computacional, sem promover o protocolo a prova QKD informação-teórica.
- [Swabian Time Tagger, API oficial](https://www.swabianinstruments.com/static/documentation/TimeTagger/api/TimeTaggerLibrary.html): contratos de configuração, readback, referência e aquisição. Disponibilidade da API não qualifica o aparelho e a experiência óptica.

Não foram adicionados interlocks físicos, validação de fabricante, autenticação
informação-teórica, prova composta de QKD ou autorização para atuação física.
