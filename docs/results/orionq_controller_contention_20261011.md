# OrionQ 5.64.1 — desempenho do controlador e concorrência

Medições concluídas em 11/10/2026 UTC (10/10/2026 em America/Bahia). O cálculo de rotas é rápido nesta carga, mas a reserva durável sofre contenção. A reserva de slots lógicos no controlador misto passou nos ensaios locais de exclusão; isso não comprova arbitragem de memórias físicas compartilhadas. Foi reproduzido consumo duplicado de um par no caminho legado quando duas instâncias independentes do engine usam o mesmo banco. Esse defeito permanece aberto.

Esta entrega acrescenta diagnóstico e evidências, sem alterar o código de produção. Os 517 arquivos congelados na validação Linux anterior foram comparados por SHA-256 e permaneceram iguais; o pyproject do repositório foi conferido contra seu hash específico, separado do pacote Linux preparado anteriormente. Não houve atuação física, geração certificada de chaves nem nova qualificação de TRL.

## Ambiente e escopo

| Item | Condição |
|---|---|
| Artefato executado nos benchmarks | Wheel Linux instalado, versão 5.64.1; importações isoladas com `python -I` |
| Máquina | AMD Ryzen 7 5700X, 8 núcleos / 16 processadores lógicos |
| Sistema | Ubuntu 24.04.5 LTS em WSL2, kernel 6.18.33.2; usuário sem privilégios, UID/GID 10001 |
| Runtime | Python 3.12.3; SQLite 3.45.1 |
| Topologia | 76 nodes, 136 enlaces; componentes conectados de 72 e 4 nodes |
| Persistência de teste | Bancos privados novos no filesystem Linux nativo `/var/tmp`; journal DELETE, sincronização FULL |
| Hardware quântico | Ausente; nenhum driver físico foi acionado |

Há três operações diferentes nesta análise:

1. **Admissão do catálogo real de 38 sessões:** `GlobalOperationManager.prepare()` após `ProtocolRuntime.sync()`, com `NoAcquisitionSource`, sem avançar o runtime. Mede escalonamento local, estados e gravação do journal, sem aquisição ou reserva de slots físicos.
2. **Roteamento e reserva lógica durável:** `MixedController.demand()`, com observações sintéticas explicitamente identificadas admitidas pelo contrato normal. A fixture atribui capacidade lógica 38 por node, canais disponíveis e capacidades de memória/BSM para exercitar a política. Não é uma afirmação sobre a capacidade física desses equipamentos.
3. **API autenticada:** a mesma reserva lógica através de TLS 1.3 com mTLS e autorização, usando a fixture de serviço portátil. Cliente e servidor estão no mesmo processo, com conexão TLS nova por requisição.

Os pedidos do segundo ensaio usam `operation=entanglement`, mas os recibos contêm `quantum_result=null` e `physical=false`. A política valida capacidades e usa caminho por menor número de saltos; não calcula uma rota ótima por tempo de coerência ou orçamento óptico. Pares de origem/destino foram escolhidos dentro dos componentes conectados, com semente 20261011. Não foi fabricada uma rota entre os dois componentes.

## Tempos medidos

P50 é a mediana; P95 usa o percentil por ordem mais próxima definido nos scripts. Dez lotes não bastam para caracterizar caudas raras ou estabelecer um SLA; nesses lotes, o P95 coincide com o máximo.

| Operação | Amostra | P50 | P95 | O que o tempo inclui |
|---|---:|---:|---:|---|
| Admissão das 38 sessões reais (`prepare`) | 10 lotes | 36,24 ms | 80,45 ms | Admissão e journal após sync; sem aquisição |
| Uma rota, política pura | 3.800 chamadas | 0,503 ms | 0,911 ms | Política e grafo local, sem persistência |
| 38 rotas, política pura | 100 lotes | 20,67 ms | 33,62 ms | 38 chamadas sequenciais à política |
| 38 reservas lógicas duráveis, sequenciais | 10 lotes | 338,01 ms | 419,63 ms | Rotas e transações do controlador misto |
| Uma reserva no caso sequencial | 380 chamadas | 8,74 ms | 12,09 ms | Demanda direta e persistência |
| 38 reservas lógicas duráveis, concorrentes | 10 lotes | 2.879,93 ms | 3.079,59 ms | Janela da primeira entrada à última conclusão |
| Uma reserva sob concorrência de 38 | 380 chamadas | 1.022,72 ms | 2.773,11 ms | Inclui espera pelo escritor SQLite |

No ensaio direto, cada lote retornou 38 reservas, com 38 registros confirmados por leitura posterior e sem exceder as capacidades lógicas. As 38 threads foram liberadas por barreira, mas a diferença real entre a primeira e a última entrada foi de 4,07 a 10,74 ms; não se afirma início simultâneo no mesmo milissegundo para esse benchmark.

A admissão das sessões reais é um ensaio distinto. Nos dez casos, as 38 demandas `SERVICE-*` ficaram em **26 establishing, 8 waiting_resources e 4 unavailable**. As oito aguardam sessões já atribuídas a outras demandas; os quatro enlaces GEO–GEO carecem das capacidades explícitas de terminal óptico ISL/tracking/TDC exigidas pela configuração. O conjunto contém 78 demandas no total. `establishing` não é entrega: foram registrados zero comandos de aquisição, zero observações, zero pares entregues e zero bits secretos certificados.

### API sob rajadas de 38

| Rajada | Recibos `reserved`, confirmados no banco | Erros sem resposta HTTP utilizável | Janela da rajada |
|---|---:|---:|---:|
| 1 | 18/38 | 20 | 2.132,66 ms |
| 2 | 18/38 | 20 | 2.058,83 ms |
| 3 | 22/38 | 16 | 2.580,23 ms |

Resultado: **58/114 reservas confirmadas**, 56 `CoreRequestError` com `http_status=null`, sem retries. Nenhuma reserva adicional sem recibo do cliente foi encontrada nesses três lotes. A integridade autenticada da auditoria foi validada.

A configuração manteve `max_concurrency=8`; apenas a quota de requisições da fixture foi elevada para 10.000/minuto para evitar confusão com o limitador por minuto. A configuração, o custo das conexões novas e a pressão de transporte são relevantes, mas o relatório não atribui cada erro exclusivamente ao semáforo: a causa individual não foi instrumentada até esse nível.

Nas 114 tentativas, incluindo falhas, P50 foi 1.255,56 ms e P95 2.131,03 ms. Considerando apenas as 58 reservas confirmadas, P50 foi 1.436,64 ms e P95 2.339,69 ms (derivados dos registros individuais). Uma latência curta de falha não é uma alocação rápida. **O ensaio da API não passou no critério de aceitar todas as 38 requisições sem erro.**

## CPU, memória e gargalos

| Medida | P50 | P95 | Limite de interpretação |
|---|---:|---:|---|
| CPU de `prepare`, 38 sessões | 16,30 ms | 61,81 ms | Tempo de CPU do processo durante prepare; setup/sync separados |
| CPU de 38 reservas sequenciais | 154,05 ms | 205,97 ms | Demanda direta e persistência |
| CPU de 38 reservas concorrentes | 264,15 ms | 338,81 ms | CPU acumulada de todas as threads do processo |

O lote concorrente usa cerca de 264 ms de CPU para uma mediana de 2,88 s de espera total; saturação de CPU não explica sozinha a demora. O lote sequencial termina em cerca de 338 ms. Isso aponta para contenção/esperas e persistência como prioridades, antes de uma troca de linguagem.

Em um passe separado, para não contaminar os tempos, `tracemalloc` registrou pico de **757.180 bytes (0,722 MiB)** durante 38 chamadas concorrentes. O maior RSS do processo foi **159.220 KiB (155,49 MiB)**, incluindo imports, setup e ensaios anteriores. Esse valor não é o consumo isolado do escalonador, e `tracemalloc` não representa toda a memória nativa de SQLite, stacks ou bibliotecas. Não há neste ensaio uma medição de RSS exclusivo por componente nem um teste de vazamento prolongado.

Um passe de cProfile, separado do benchmark, mediu 38 demandas sequenciais:

| Componente | Chamadas | Tempo observado |
|---|---:|---:|
| Saída/commit de conexão SQLite | 38 | 192,48 ms |
| `json.decoder.raw_decode` | 8.702 | 57,54 ms próprio |
| `route_policy` | 38 | 62,26 ms acumulado |
| `_states` | 76 | 61,06 ms acumulado |
| `demand` | 38 | 401,02 ms acumulado |

Os tempos de cProfile incluem espera de I/O e não são exclusivamente CPU; valores acumulados se sobrepõem e não devem ser somados. O código recompõe estados/grafos e decodifica JSON repetidamente. A serialização do escritor e o commit durável foram os maiores custos observáveis. Reduzir recomputação e organizar a admissão em uma fila com deadlines é uma hipótese de otimização a testar; desativar durabilidade não é uma solução aceitável para consumo de recursos.

## Corridas, atomicidade e recuperação

### Controlador misto: garantias locais verificadas

`MixedController._db()` abre `BEGIN IMMEDIATE`; leitura das reservas, desconto de capacidade, planejamento e inserção ocorrem na mesma transação. A permissão/deadline é reavaliada dentro da transação depois da espera. SQLite permite um escritor por banco, e `BEGIN IMMEDIATE` inicia a transação de escrita antes da leitura usada na decisão ([documentação oficial](https://www.sqlite.org/lang_transaction.html)).

| Caso | Evidência |
|---|---|
| Dois pedidos distintos para capacidade lógica 1 | 16 rodadas diretas: exatamente um `reserved` e um `blocked` |
| Mesmo teste via mTLS | 6 rodadas: exatamente um `reserved` e um `blocked` |
| Proximidade das entradas | Direto: 0,135–0,197 ms; API: 0,175–0,250 ms |
| Mesmo `request_id` concorrente | Mesmo recibo e um registro persistido |
| Segundo registro inválido em um lote de ingestão | Rollback integral do lote |
| Segundo processo tentando controlar o mesmo banco misto | Proprietário concorrente rejeitado |
| Deadline expirando durante espera SQLite | Recusado dentro da admissão; sem commit |
| Duas bases independentes com os mesmos IDs de nodes | Ambas reservaram: não há coordenação global do hardware |

Não há garantia de qual concorrente ganha, nem de justiça ou ausência de starvation. A propriedade do banco usa identidade de instância/PID local; não é um lease distribuído com fencing do equipamento.

Também foi injetada falha na auditoria de conclusão **depois** do commit: a API retornou HTTP 500, mas a reserva continuou `reserved`. Repetir com o mesmo ID recuperou o mesmo recibo. Reserva e registro final de auditoria não formam uma única transação. Após uma resposta incerta, é necessário consultar/repetir com o ID original, sem criar uma nova operação às cegas.

### Caminho legado: consumo duplicado reproduzido

Duas instâncias reais e independentes de `NetworkEngine`, com o mesmo SQLite/topologia/demanda, leram o mesmo par `delivered`. Uma barreira antes da atualização forçou a intercalação; **ambas retornaram `consumed` para o mesmo par**. Como controle, duas threads usando uma única instância produziram um sucesso e `ValueError:no_delivered_pair`.

O `RLock` do engine protege apenas aquela instância. `command('consume_pair')` lê e verifica o par antes de chamar `DemandStore.set_pair()`. A transação desta atualização compara gerações de memória, mas não exige atomicamente o status/revisão esperado do par. Serializar as duas escritas não evita a decisão tomada sobre a mesma leitura antiga.

Referências: [engine.py](../../src/quantum_autopilot/network_engine/engine.py), comando em torno da linha 1406; [routing.py](../../src/quantum_autopilot/network_engine/routing.py), `set_pair` em torno da linha 345. A correção necessária é consumo condicional atômico, com identidade/status/revisão esperado e exatamente um vencedor, associado à autoridade exclusiva do recurso. **Não foi corrigido nesta análise.**

### Memória orbital e deadlock distribuído

A simulação usa `PairResource`, filas de eventos e pools por sessão. BSM consome os pares de entrada; ACKs, timeouts, envelhecimento e limites de capacidade liberam ou invalidam recursos no modelo. O escalonador controla propriedade por sessão. Isso permite testar causalidade e falhas dentro dessa arquitetura centralizada.

Entretanto, as 38 sessões usam bancos hipotéticos dedicados: `shared_hardware_arbitration_implemented=False` consta do inventário. Não há alocação global de IDs de slots de uma memória orbital física entre essas sessões. A reserva do controlador misto não está comprovada como um bloqueio desses slots nos adaptadores.

| Pergunta | Resposta sustentada pela evidência |
|---|---|
| Dois nodes no mesmo milissegundo disputam um slot lógico do mesmo controlador/banco? | Um ganha e outro é bloqueado, nos ensaios descritos |
| A transação lógica local é atômica? | Sim, quanto à reserva dentro desse banco; o resultado HTTP/auditoria pode ser incerto |
| Duas autoridades em bancos diferentes disputam a mesma memória física? | Não há garantia implementada/validada de exclusão global |
| Pode haver deadlock distribuído enquanto pares expiram? | Não foi demonstrada prevenção para hardware distribuído; ausência de deadlock nos testes locais não é prova geral |

O caminho misto decide a rota inteira e persiste a reserva de uma vez, evitando retenção parcial de slots lógicos nesse banco durante a busca. Seu TTL utiliza relógio de parede e a limpeza é acionada por chamadas de demanda/status/release: não é um temporizador autônomo de expiração física. O runtime quântico tem seu próprio tempo lógico e expiração causal.

O registro legado de um par entregue é histórico: a reconciliação cobre reset/isolamento/remoção, mas não comprova a validade atual por tempo de coerência. O limite de 16 demandas daquele caminho legado também não deve ser confundido com o catálogo de 38 sessões do runtime.

Para qualificar o controle de memória compartilhada, faltam IDs físicos globais de slots, autoridade exclusiva verificável nos adaptadores, leases/deadlines coerentes com a vida útil dos pares, fencing contra controladores antigos e uma política de aquisição que evite retenção circular. Falhas, partições e reinícios devem ser testados antes de afirmar ausência de deadlock distribuído.

## Reprodução e evidências

Os comandos abaixo usam a instalação Linux validada nesta máquina e criam novos bancos privados no filesystem Linux. O primeiro executa o benchmark de reservas e API; o segundo mede a admissão das sessões reais. Troque os caminhos de saída ao repetir:

```bash
/var/tmp/orionq-linux-gp354h_2/venv/bin/python -I \
  /mnt/e/Codex/2026-08-27/e/work/NISQ-Predictive-Twin/tools/benchmark_controller_contention.py \
  --repository /mnt/e/Codex/2026-08-27/e/work/NISQ-Predictive-Twin \
  --output /var/tmp/orionq-controller-contention-repeat --trials 10 --api

/var/tmp/orionq-linux-gp354h_2/venv/bin/python -I \
  /mnt/e/Codex/2026-08-27/e/work/NISQ-Predictive-Twin/tools/run_global_scheduler_benchmark.py \
  --report /var/tmp/orionq-global-scheduler-repeat/public/report.json --trials 10
```

Execute como usuário de validação sem privilégios. O primeiro script carrega explicitamente a fixture autorizada de teste do repositório; os serviços do pacote instalado continuam sendo o objeto medido. Os reproduzidores de corrida em `work/` usam explicitamente o fonte, cujos arquivos de produção correspondem aos hashes congelados. Eles não acionam hardware e não devem usar estado operacional existente.

- [Benchmark de reservas e API](../../outputs/controller_contention_20261011/controller-benchmark.json)
- [Admissão das 38 sessões reais](../../outputs/controller_contention_20261011/global-scheduler.json)
- [Ensaios de atomicidade local](../../outputs/controller_contention_20261011/mixed-atomicity.json)
- [Reprodução de consumo duplicado legado](../../outputs/controller_contention_20261011/legacy-pair-race.json)
- [Manifesto de integridade e preservação dos fontes](../../outputs/controller_contention_20261011/delivery-integrity.json)
- [Script de reservas](../../tools/benchmark_controller_contention.py), [script do escalonador](../../tools/run_global_scheduler_benchmark.py)
- [Reprodutor misto](../../work/mixed_concurrency_audit_20261011.py), [reprodutor legado](../../work/legacy_pair_race_audit_20261011.py)

Foram exportados somente os JSON públicos permitidos, sem bancos, certificados, chaves privadas ou estado de serviços. As novas ferramentas de benchmark passaram no Ruff; os reproduzidores em `work/` fazem bootstrap explícito de `sys.path` antes das importações, que requer exceção E402 na checagem de estilo. Não se repetiu a suíte anterior inteira porque o código de produção permaneceu igual; as evidências desta análise não transformam o defeito legado em um resultado aprovado.

Documentação externa consultada em 11/10/2026 UTC: [transações SQLite](https://www.sqlite.org/lang_transaction.html) e [locking/pager SQLite](https://www.sqlite.org/lockingv3.html). Os resultados de desempenho e concorrência acima provêm dos ensaios locais, não dessas fontes externas.
