# OrionQ — controle nativo no SeQUeNCe e resiliência

Data: 12/10/2026. Código OrionQ 5.66.1 em `E:/OrionQ/project`; SeQUeNCe 1.2.0, commit `3133c66d80a898c6c3cc9af9eec3fa94fdbe2233`. Resultado **APROVADO no escopo de interoperabilidade de observações fotônicas e execução de controle clássico**. Não se atribui novo TRL a este ensaio.

## O que efetivamente foi executado

Um processo Python independente do simulador criou dez nós N02–N11 em anel, com `QuantumChannel`, `Detector`, `ClassicalChannel` e bancos nativos de quatro memórias desocupadas. Não importou `quantum_autopilot`, não usou shims e verificou o clone limpo e a revisão fixada. N01 utilizou aquisição emulada identificada, pois não há Swabian conectado.

As transmissões fotônicas, recepções, cliques e mensagens clássicas vieram dos callbacks nativos. O normalizador preservou origem, sessão, sequência, relógio em ps e evidência de entrega. Os onze nós passaram pelo mesmo `NodeProcessingPipeline`, com correlação CPU e PL emulada equivalentes e recibos persistidos. A API real usou TLS 1.3/mTLS, identidades de serviço, permissões e auditoria autenticada; não dependeu da sessão gráfica Windows.

No ensaio final, o processo nativo **PID 529** manteve a mesma timeline, os mesmos objetos Node e o RNG entre a aquisição inicial e cinco etapas subsequentes. O controlador escolheu e reservou rotas sem despachar por marca da fonte; o harness consultou a reserva pela API, verificou status/hash/TTL e devolveu o recibo à entrada limitada do simulador. Cada salto foi executado por `ClassicalChannel`, e a chegada retornou ao backend pelos mesmos contratos de observações.

| Etapa | Operação e evidência | Resultado |
| --- | --- | --- |
| Rota inicial | N02 → N03 → N04 → N05 → N06; quatro callbacks de recepção e um de entrega | Recebida em 13.781.169.840 ps da timeline |
| Falha de N05 | Comando identifica indisponibilidade do dispositivo simulado; callback nativo `fault` ingressa na API | Reserva anterior `invalidated_observation`; pedido com N05 bloqueado |
| Desvio | N02 → N11 → N10 → N09 → N08 → N07 → N06; seis recepções e uma entrega | Recebida em 13.841.169.842 ps; reserva liberada |
| Tentativa com sessão antiga | Registro negativo identificado tenta verificar novamente a mesma sessão | Não ressuscita a fonte nem autoriza a rota |
| Recuperação | Reinício explícito das fontes simuladas, novos callbacks de recursos, ambos os canais e fotodetecção; nova sessão configurada | Estado reinicia como `no_input`; apenas a ingestão fresca restaura disponibilidade |
| Rota recuperada | N05 → N06; uma recepção e uma entrega | Recebida em 28.065.830.265 ps; reserva liberada |

Os três comandos percorreram onze saltos efetivos. Os tempos são **relógio causal do simulador**, não latência de API nem tempo de parede. As perdas ópticas e os cliques não foram convertidos em prova de entrelaçamento.

O ensaio final aprovou nove verificações do bootstrap/controlador e quatorze de resiliência/execução nativa. Registrou **82 chamadas concluídas**, **3.916 observações aceitas e 3.916 IDs únicos**. As métricas finais dos onze nós correspondem aos recibos CPU persistidos da recuperação. A mutação tentada pelo auditor recebeu HTTP 403, e a auditoria autenticada foi validada. Os processos e o servidor próprios foram encerrados.

## Perdas e separação dos canais

Todos os casos usam semente 2026101101, 128 pulsos por enlace e dez enlaces de 1.000 m. Cadência óptica: 100.000.000 ps; atraso clássico: 10.000.000 ps. São hipóteses explícitas da fixture, não caracterização experimental.

| Cenário nativo | Pulsos enviados | Fótons recebidos | Cliques de sinal | Dark counts | Mensagens clássicas recebidas |
| --- | ---: | ---: | ---: | ---: | ---: |
| Nominal: 0,2 dB/km; eficiência 0,75; dark 100 Hz | 1.280 | 1.222 | 944 | 29 | 964 |
| Perda de 20 dB/km; demais parâmetros nominais | 1.280 | 9 | 7 | 33 | 27 |
| Eficiência zero e dark zero; perda nominal | 1.280 | 1.216 | 0 | 0 | 20 |

O caso sem detecção conserva as vinte mensagens iniciais de comunicação clássica. Ausência de cliques não inventa um estado quântico operacional. Os dark counts são identificados e não possuem a evidência fotônica de entrega. A mesma semente com parâmetros nominais em duas execuções independentes produziu os mesmos 5.413 eventos e SHA-256 `d1fc8027a8a613a5b829f44e130f0ad9d8f34a59828ed66e7a774c16ede6f109`.

A fonte usa fótons declarados de 810 nm com uma atenuação abstrata de 0,2 dB/km. Essa combinação não foi qualificada como espectro físico de fibra; não sustenta estimativas universais de alcance ou desempenho experimental. Alterar parâmetros também altera o consumo de RNG, portanto não se assume identidade individual de cada fóton entre os cenários distintos.

## Ensaios adicionais e limitações encontradas

| Verificação | Resultado | Evidência em `outputs/defect-corrections-20261012/sequence-control/` |
| --- | --- | --- |
| Contrato de execução clássico no Windows: adulteração, expiração, repetição, reserva liberada, atuação física, operação quântica, enlace incompatível, nó offline e campos duplicados | 10 aprovados | `native-contract-windows.xml` |
| Mesmos testes no Linux | 10 aprovados | `native-contract-linux.xml` |
| Novo orçamento determinístico de roteamento e cache de comissionamento no Linux | 7 aprovados | `linux-work-cache-tests.xml` |
| Resiliência com capturas independentes, sem executor vivo | 9 verificações base + 11 resiliência aprovadas | `resilience/controller/report.json` e `resilience/resilience.json` |
| Controle clássico em malha fechada no processo nativo persistente | 9 verificações base + 14 resiliência/execução aprovadas | `closed-loop-native-r2/controller/report.json` e `closed-loop-native-r2/resilience.json` |
| Conferência de hashes, callbacks causais, IDs únicos e métricas recuperadas | Aprovada | `qualification.json` |

O primeiro carregamento frio falhou no guard de 30 s do `git status` nativo antes da simulação, sobre DrvFS/HDD. Foi aquecida a inspeção somente leitura com limite de 120 s e `GIT_OPTIONAL_LOCKS=0`; depois o runner original passou com seu guard de 30 s preservado. `core.autocrlf=true` e `safe.directory` foram aplicados apenas ao processo. Não houve alteração do clone nem configuração global Git. O aviso do QuTiP sobre matplotlib ausente não afeta os ensaios sem renderização.

A primeira tentativa do executor vivo foi corretamente rejeitada na última ingestão porque o harness vinculava o callback recuperado à sessão inicial. Foi corrigido exclusivamente o harness para passar a configuração da sessão nova. Essa tentativa é **FAIL**, permanece preservada em `closed-loop-native/attempt-status.json` e não entra nos aprovados. A repetição completa em `closed-loop-native-r2` passou.

## Reprodução

No ambiente Linux já preparado, mantenha o `PYTHONPATH` do controlador apontando para a fonte atual. O processo independente SeQUeNCe retira esse `PYTHONPATH` automaticamente e registra seus hashes. Use um diretório de saída novo a cada ensaio; não reutilize recibos antigos.

```bash
PYTHONPATH=/mnt/e/OrionQ/project/src \
/var/tmp/orionq-linux-release-5.66.1-9z8rpqe8/venv/bin/python \
  tools/run_sequence_control_resilience_campaign.py \
  --closed-loop-native \
  --sequence-src /mnt/e/OrionQ/dependencies/SeQUeNCe \
  --sequence-python /var/tmp/orionq-sequence-mixed-20261011/venv/bin/python \
  --repository /mnt/e/OrionQ/project \
  --output outputs/defect-corrections-20261012/sequence-control/reproducao-nova
```

Os modos anteriores continuam disponíveis: `run_mixed_sequence_nodes.py` sem `--interactive` gera capturas independentes; `run_mixed_controller_campaign.py --native ... --output ...` executa o bootstrap/API; o novo harness aceita `--native ... --recovery-native ...` para resiliência registrada. `--interactive` é um executor de fixture local delimitado; não expõe um serviço de equipamento público.

## Escopo que permanece pendente

**As reservas do MixedController são slots lógicos centralizados de controle e capacidade observada.** Não são endereços físicos de memórias orbitais, nem reservas de protocolo de entrelaçamento no `MemoryArray` nativo. A execução demonstrada é clássica; arbitragens multi-host, controle de hardware, temporização física e prevenção de deadlock distribuído exigem ensaios próprios.

A pipe local é uma interface confiável de fixture. O hash de recibo verifica integridade, não autentica por si só; a autoridade vem do harness conectado ao serviço mTLS. Ela não é declarada transporte de comando físico qualificado. A recuperação utilizada é reinício explicitamente simulado com uma nova sessão, não recuperação elétrica comprovada.

N01 continua emulado, sem SDK Swabian executado e sem bridge clássica verificada ao anel; sua rota permanece bloqueada. Não se geram chaves operacionais, pares finais, QBER ou fidelidade de Bell a partir de timestamps isolados. O relógio comum em ps é assumido pela fixture, sem qualificação de sincronismo de bancada. O caso de uma semente e dez nós não substitui a validação científica multi-semente ou a demonstração da topologia global completa.
