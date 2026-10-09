# Validação de chaves, pontas independentes e observações — 07/10/2026

**Resultado:** 49 testes aprovados, 14 verificações de falhas de entrega aprovadas e 11 verificações da campanha dedicada aprovadas. Execução local Windows sem equipamento, sem atuação física, sem ML obrigatório e com `certified_secret_bits=0`.

Evidências finais:

- [JUnit: 49 testes](../../outputs/lab_hardening_20261007/keys/tests-final-v5.xml), 75,82 segundos.
- [Manifesto com hashes de fontes, ambiente e nove regimes](../../outputs/lab_hardening_20261007/keys/validation-final-v3/manifest.json).
- [Recibo da entrega independente e da injeção de falhas](../../outputs/lab_hardening_20261007/keys/validation-final-v3/independent-delivery/qkd_report.json).
- [Registros de entrada reproduzíveis](../../outputs/lab_hardening_20261007/keys/validation-final-v3/independent-delivery/observations.json): **dados de teste**, contendo preparação/análise, nunca registros secretos para uso operacional.
- [Script de reprodução](../../outputs/lab_hardening_20261007/keys/run_key_validation.py) e [contrato técnico](../product/orion_q_lab_key_delivery.md).

## Casos de uso e resultados

| Caso | Injeção/entrada | Resultado observado | Evidência |
|---|---|---|---|
| Solicitação inválida | Número booleano, zero, acima de 64, NaN, tamanho incompatível ou campo desconhecido | Lote inteiro permanece disponível; nenhuma reserva parcial | JUnit, seis casos |
| Reserva abandonada | Reabertura do SQLite após reserva sem emissão | Estado invalidado, quantidade disponível reduzida, token repetido rejeitado | JUnit |
| Validação da KME parceira | Um identificador inexistente no lote | Nenhuma reserva parcial na parceira | JUnit |
| Autorização atrasada | Mensagem após invalidação | Não revive chaves invalidadas | JUnit |
| Consumo antes da resposta | Emissão confirmada em SQLite | Material local removido antes do HTTP; segunda emissão rejeitada | JUnit |
| Uso não autorizado na ponta Bob | Pedido antes da reserva/autorização Alice | Rejeitado sem consumo | JUnit |
| Consumo de lote inconsistente | Identificador inválido misturado aos válidos | Rejeição atômica; lote válido continua autorizado | JUnit |
| Provisionamento duplicado | Reimportação dos mesmos identificadores | Rejeitado, incluindo registros já reservados ou usados | JUnit |
| Credencial de envelopamento errada | Outra chave local AES-GCM | Inicialização rejeitada antes de KME pronta | JUnit |
| Concorrência de conexões | Dez pedidos de três chaves; 24 chaves | Oito lotes completos; dois recusados; nenhuma duplicação | JUnit |
| Concorrência entre processos | Quatro processos acessando SQLite com 24 chaves | Exatamente 24 identificadores distintos reservados | JUnit |
| Término durante transação | Processo encerrado antes do commit, sem emissão | Rollback SQLite; a chave ainda não emitida permanece disponível | JUnit |
| Término após commit | Processo encerrado antes da resposta | Recuperação invalida a emissão incerta e persiste reconciliação | JUnit |
| Dois KMEs e duas aplicações | Quatro processos; dois bancos; comunicações mTLS | Uma chave correspondente permite mensagem AES-GCM; adulteração rejeitada | Recibo independente |
| Certificados/associação inválidos | SAE válida na KME errada; identidade KME errada; certificado expirado/ausente | Operações recusadas antes de usar material | Recibo independente |
| Resposta perdida antes de reserva | Conexão encerrada na fronteira inicial | Nenhuma chave consumida | `fault_campaign.before_reservation` |
| Resposta perdida após reserva local | Conexão encerrada após commit da reserva | Reserva invalidada; token não recupera material | `after_local_reservation` |
| Resposta perdida após reserva parceira | Conexão encerrada após a reserva nas duas KMEs | Ambas invalidadas; nenhuma devolução ao pool | `after_peer_reservation` |
| Resposta perdida após commit local | Alice já consumiu, HTTP ainda não respondeu | Emissão incerta invalidada e reconciliada | `after_local_commit` |
| Resposta perdida após commit parceiro | Bob autorizado, HTTP ainda não respondeu | Copias não consumidas invalidadas; token repetido recusado | `after_peer_commit` |
| Perda após cabeçalhos | HTTP 200 enviado sem corpo completo | Falha de transporte; material não reapresentado | `after_response_headers` |
| Perda durante corpo | Metade da resposta enviada | Resposta incompleta; emissão invalidada sem reutilização | `mid_response` |
| Término real de KME | Processo Alice encerrado na fronteira de reserva parceira | Novo processo recupera SQLite e reconcilia Bob | `process_killed_reservation_recovered_without_reuse` |
| Particionamento real | Processo Bob encerrado | Falha 503, reserva queimada e invalidação persistida | `partition_burns_reservation_and_persists_invalidation` |
| Nova demanda durante partição | Outra solicitação enquanto há invalidação pendente | Bloqueada antes de reservar nova chave | `new_delivery_blocked_until_peer_reconciliation` |
| Recuperação de uma ponta | Processo Bob reiniciado com seu banco próprio | Fila reconciliada; sem repor chaves usadas | `independent_peer_restart_reconciles` |
| Atraso | Oitenta milissegundos no caminho de entrega | Atribuição única preservada; atraso observado ≥70 ms | `injected_delay_preserves_assignment` |
| Resposta Bob perdida | Bob consome e encerra o socket antes do corpo | Pedido repetido rejeitado; material não reapresentado | `slave_response_loss_never_reissues_consumed_key` |
| Erro local inesperado | Falha emulada após reserva parceira | Aquisição bloqueada, status publica zero disponível | `unexpected_local_failure_blocks_acquisition` |
| Reprodução do bloco QKD | Mesmo arquivo de observações e configuração | Sessão e material de teste reproduzidos exatamente | JUnit e manifesto |
| Bits/bases ausentes | Timestamps sem registros de preparação/análise | Contrato recusado; nenhum bit inferido | JUnit |
| Relógio e tempo de voo | Clock destravado, incerteza excessiva, detecções fora de janela | Aborto explícito e zero material | JUnit |
| Perda/ruído | Eficiência zero ou erro 0,3–0,5 em várias sementes | Aborto explícito e nenhuma KME provisionada | JUnit e nove regimes no manifesto |
| Estimador independente | Cinco sementes com erro zero e contagem conhecida | Sifting, amostra X, desvio sem reposição e orçamento conferidos | JUnit |
| Toeplitz independente | Multiplicação de matriz inteira em quatro dimensões | Mesmos bits que o algoritmo de produção | JUnit |
| Reconciliação/vazamento | Quatro strings com erros conhecidos | Erros corrigidos conferem; paridades/respostas contabilizadas | JUnit |
| Custo de autenticação | Dedução adicional de 1.024 bits | Orçamento reduz exatamente em 1.024 bits | JUnit |

## Contabilidade do cenário padrão

Semente pública `20261007`, 80.000 pulsos ideais, perda de 3 dB, eficiência 0,7 e erro de 0,01. Foram observadas **28.209 detecções**, **14.058 registros após sifting**, **3.514 amostras X**, 36 erros na amostra, QBER de amostra ≈**1,0245%** e limite condicional de fase ≈**8,0221%**. A reconciliação comunicou 1.648 bits contabilizados como vazamento; o orçamento condicional foi 2.467 bits. O arredondamento a blocos de 256 bits admitiu **2.304 bits de teste, nove chaves**.

O volume foi calculado dos registros e do orçamento; o ensaio não força dez chaves nem relaxa a análise para satisfazer uma quantidade. No final, Alice contabilizou nove emissões, disponibilidade zero; Bob contabilizou uma chave consumida e oito autorizadas. As cópias Bob não consumidas pertencem às emissões já retiradas da seleção Alice, não a um pool compartilhado. A PKI e as chaves locais de envelopamento foram removidas ao fim do ensaio; os bytes de teste persistidos ficaram irrecuperáveis.

Repetir registros e sementes públicas reproduz os mesmos bytes de teste em ensaios diferentes. A evidência de ausência de reatribuição refere-se ao ciclo de vida de registros e identificadores no ledger persistente de cada ensaio, não à unicidade criptográfica entre fixtures reproduzíveis.

As taxas são condicionais ao modelo e à duração de aquisição, **não taxas seguras certificadas**. A origem quântica segue centralmente emulada. Medições físicas, prova completa do dispositivo, autenticação do canal QKD, RNG qualificado, cofre/PKI de produção e interoperabilidade com KMEs comerciais continuam externos. [ETSI 014](https://www.etsi.org/deliver/etsi_gs/QKD/001_099/014/01.01.01_60/gs_qkd014v010101p.pdf) especifica a entrega HTTPS; a política de reserva e as extensões de recuperação aqui são contratos próprios. A Eq. 76 de [Tomamichel e Leverrier](https://arxiv.org/pdf/1506.08458) é a referência da amostragem, sob hipóteses identificadas; ela não certifica por si só esta implementação.
