# Entrega de chaves e observações no laboratório ORION-Q

Implementação em `lab/persistent_keys.py`, `independent_kme.py`, `observed_qkd.py` e `observations.py`. Escopo: integração local com **material de teste identificado**, sem equipamento, atuação física, cofre qualificado ou certificação de segurança quântica.

## Processos e contratos

O ensaio padrão inicia duas KMEs e duas aplicações SAE em **quatro processos distintos**. Cada KME possui seu próprio SQLite, chave local de envelopamento AES-GCM, estado e conexão HTTPS. As aplicações recebem apenas a cópia fornecida por sua KME. A comunicação SAE–SAE transmite identificador e mensagem AES-GCM por TCP/TLS com certificados mútuos e verificação da identidade do participante.

A origem quântica permanece um gerador **central e explicitamente emulado**. Ele produz registros de preparação/análise; o pós-processamento desses registros provisiona cópias locais correlacionadas. A separação de processos comprova a interface e a contabilização de entrega; não comprova uma fonte QKD distribuída ou equipamento real.

`GET /api/v1/keys/{peer}/status`, `POST .../enc_keys` e `POST .../dec_keys` implementam um subconjunto de [ETSI GS QKD 014 V1.1.1, fevereiro de 2019](https://www.etsi.org/deliver/etsi_gs/QKD/001_099/014/01.01.01_60/gs_qkd014v010101p.pdf), consultado em 07/10/2026. O documento exige HTTPS, autenticação mútua e confirmação da identidade KME/SAE; a troca de identificadores entre aplicações e a gestão interna de chaves são externas ao seu escopo. Não há alegação de conformidade integral: os métodos GET simplificados, multicast e extensões de fabricantes não são implementados.

O protocolo `POST /internal/orion-lab/v1/{reserve|authorize|invalidate}` coordena **apenas estados e identificadores** entre as duas KMEs por mTLS. É um contrato próprio do laboratório, distinto da API ETSI e de um protocolo de distribuição quântica. As chamadas exigem o certificado da KME parceira. Nenhuma chave é enviada por esse protocolo.

## Reserva e entrega incerta

| Cópia | Transição | Regra |
|---|---|---|
| KME Alice | disponível → reservada | Transação `BEGIN IMMEDIATE`; valida toda a solicitação antes da reserva. |
| KME Bob | disponível → reservada | Valida todos os identificadores e vincula a mesma operação. |
| KME Alice | reservada → emitida | Confirma consumo e remove o material envelopado **antes** da resposta HTTP. |
| KME Bob | reservada → autorizada | Permite o acesso da SAE Bob somente após a confirmação Alice. |
| KME Bob | autorizada → consumida | Consome o lote inteiro e remove o material antes da resposta HTTP. |
| Ambas | reservada/emitida/autorizada → invalidada | Falha ou entrega incerta; nunca retorna a disponível. Uma cópia já consumida permanece consumida. |

Uma escrita de socket concluída significa `response_sent_unconfirmed`, não confirmação da aplicação. O estado continua terminal para seleção de material. A reserva é irreversível: falhas podem desperdiçar chaves, mas não permitem sua reutilização.

A extensão **opcional e própria** `X-Orion-Delivery-Token` vincula a solicitação a uma operação. Repetir um token registrado retorna HTTP 409. `GET .../delivery_status` com o mesmo cabeçalho informa estado e quantidade, sem identificadores ou bytes de chave; não permite recuperar o material. Sem token, cada nova requisição é uma nova operação e poderá consumir uma nova chave; o cliente não deve apresentar isso como recuperação da resposta perdida.

Na recuperação do processo, reservas e emissões ainda sem estado de resposta são invalidadas. Invalidações destinadas à outra KME são persistidas em uma fila SQLite. A KME Alice bloqueia novas emissões enquanto não reconciliar essa fila. Mensagens atrasadas não reautorizam um registro invalidado. O lock de proprietário mantido pelo sistema operacional impede dois servidores ativos de assumir o mesmo banco; sua liberação automática após término permite a recuperação.

SQLite usa WAL, `synchronous=FULL` e transações atômicas. Os bancos retêm somente material de teste envelopado e os tombstones dos identificadores usados. A chave local de envelopamento e a PKI do ensaio são efêmeras; ao fim, o material persistido fica irrecuperável. Isso não substitui gerenciamento de credenciais, HSM, proteção contra extração de memória, política de retenção ou provisionamento de produção. Recibos e logs omitem chaves, seus identificadores, certificados privados e tokens.

## Aquisição → protocolo

`ObservationBlock` contém origem/modo, instrumento/adaptador/configuração, relógio, duração, hash da aquisição e registros explícitos de preparação/análise. Cada pulso associa identificador, timestamp, base/bit preparado e base/bit detectado. Pulsos não detectados não fornecem bit de saída. Timestamps isolados não são convertidos em bases ou bits.

`run_observed_bb84(block, config)` recebe o mesmo contrato para emulação, hardware ou replay. Executa validação de proveniência, relógio e janela de voo, descarte temporal, sifting, teste na base X, reconciliação dos candidatos Z, verificação Toeplitz e amplificação de privacidade. Entrada incompleta, relógio destravado, incerteza excessiva, ausência de detecções aceitas, ruído alto ou orçamento insuficiente produz aborto explícito e zero material. A função verifica que o hash do bloco não mudou durante o processamento.

`run_bb84(config, observation_path=...)` substitui apenas a origem por `synthetic_bb84_observations`. O arquivo `observations.json` armazena os registros de **teste**, incluindo bits de entrada necessários à reprodução; não é um recibo público nem deve ser usado para dados secretos. `load_observation_block` verifica sua integridade. Os mesmos registros, configuração e semente do protocolo reproduzem a sessão e o resultado bit a bit.

## Hipóteses e orçamento científico

O gerador modela pulsos de fóton único ideais, perda independente `η = eficiência × 10^(-perda_dB/10)`, bases uniformes e erro binário configurável. Seus PRNGs e sementes são públicos. Não é um modelo de fonte laser de estados coerentes fracos, protocolo decoy ou emissor calibrado.

A repetição da mesma fixture/semente reproduz deliberadamente os mesmos bytes entre ensaios distintos. A prova de não reatribuição cobre o ciclo de vida dos registros de cada ledger, incluindo reinícios; não é uma alegação de unicidade criptográfica dos bytes entre demonstrações públicas. Esse material jamais deve proteger informações reais.

O teste X e os candidatos Z são separados. O limite de amostragem usa a desigualdade sem reposição da Eq. 76 de [Tomamichel e Leverrier, 2017](https://arxiv.org/pdf/1506.08458): `δ = sqrt((n+k)(k+1) ln(1/ε_PE)/(2 n k²))`. O orçamento condicional deduz a entropia binária do limite de fase, os bits comunicados na reconciliação, o tag de verificação, a penalidade de amplificação e um custo de autenticação declarado. A interpretação depende das hipóteses de fonte/detectores, amostragem e autenticação; **não é uma prova composável aplicada ao equipamento**.

O recibo identifica `ε_PE`, a probabilidade condicional do tag (`2^-t`), `ε_PA`, custos e pressupostos. Esses números não são somados e publicados como segurança final certificada. O trabalho de [Tomamichel, Lim, Gisin e Renner, 2012](https://www.nature.com/articles/ncomms1631), consultado em 07/10/2026, mostra por que a análise de tamanho finito depende dos recursos e da especificação completa do protocolo. Neste laboratório permanecem `authentication_proven=false`, `composable_security_claim=false` e `certified_secret_bits=0`.

Os testes comparam Toeplitz a multiplicação inteira de uma matriz independente, usam strings com erros conhecidos para verificar reconciliação/vazamento e repetem regimes em várias sementes. Fidelidade, espectro, eficiência, jitter físico, sincronismo, fontes RNG, imperfeições dos detectores e autenticação do canal quântico clássico ainda exigem dados e avaliação experimental independente.

## Execução

No Windows, com o extra `lab` instalado e sem conectar instrumento:

```powershell
$env:PYTHONPATH='src'
.audit-winvenv\Scripts\python.exe -m quantum_autopilot.lab.cli --experiment qkd --output outputs\lab-qkd-local
.audit-winvenv\Scripts\python.exe -m pytest tests\test_lab_qkd.py tests\test_lab_key_ledger.py tests\test_lab_observed_qkd.py -q --basetemp outputs\lab-qkd-tests
```

O executável usa `multiprocessing.freeze_support()` no launcher antes da interface. O volume de chaves do ensaio é calculado do resultado observado; não há uma promessa de dez chaves. Se o protocolo abortar, `run_qkd_demo` registra `status=protocol_aborted` e não provisiona ou inicia KMEs. `DemoKeyPool`/`run_shared_pool_demo` permanecem como auxiliares legados de testes; não são usados como evidência de pontas independentes.
