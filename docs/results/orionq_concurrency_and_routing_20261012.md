# OrionQ — concorrência, reservas e roteamento (12/10/2026)

A reserva lógica do controlador central é atômica. Isso não equivale a uma transação quântica distribuída entre equipamentos ou hosts. A atuação física continua bloqueada.

`MixedController` reserva slots lógicos/de controle com base na capacidade observada. Não vincula a reserva a um endereço físico de memória orbital e não emite uma operação óptica. Os bancos orbitais simulados são outra camada, com posse dedicada por sessão. Essa distinção impede usar um teste de exclusão no banco como prova de atomicidade experimental de uma memória quântica.

## Dois pedidos para a última unidade de capacidade lógica

`MixedController._db` toma o lock local, abre `BEGIN IMMEDIATE` e verifica autoridade. Na mesma transação, `demand` expira reservas antigas, desconta **todas** as reservas ativas, calcula a rota, grava a reserva e a saída de auditoria. A primeira transação que obtém o lock e confirma a capacidade reserva a unidade; a seguinte vê capacidade zero e recebe `blocked`. A chegada no mesmo milissegundo não muda essa exclusão. Não há garantia FIFO nem deadline de atendimento de 1 ms. O timeout de aquisição do lock SQLite é 10 s.

A rota central é reservada de uma vez, sem manter uma reserva parcial esperando outra rota. Falhas de admissão/gravação revertem a transação. IDs de pedido são idempotentes: repetir um pedido expirado não o transforma em uma reserva nova.

As reservas têm TTL e são marcadas `expired` na próxima verificação (`demand`, `release`, `status` ou ingestão), antes de usar novamente a capacidade. Na simulação de protocolos, a validade/decoerência de pares é independente do TTL administrativo; pais consumidos são removidos e resultados expirados não podem ser entregues como válidos. A fila usa ordem causal e não envelhece recursos além de eventos ainda não processados. Isso descreve relógio simulado, não um prazo de atuação de hardware em tempo real.

## Garantias por camada

| Camada | Garantia demonstrada | Limite |
|---|---|---|
| Controlador central + um SQLite | Reserva integral, contagem de capacidade e recibo/outbox atômicos; 38 solicitações concorrentes para capacidade 1 produziram 1 reserva e 37 recusas | Reserva lógica por capacidade; não atribui/qualifica endereço físico de memória orbital |
| Estoque persistido de pares | CAS compara demanda e versão do par no momento do commit; dois processos produziram exatamente 1 consumo e 1 conflito | Não certifica execução óptica física |
| Autoridade compartilhada local | Lock exclusivo por serial/endpoint e fencing; segundo controlador vivo no mesmo banco é recusado; outro banco que tenta o mesmo equipamento também é recusado | Exige a mesma autoridade local; `multi_host_coordination=False` |
| Bancos orbitais simulados | Um banco declarado exclusivo pertence à sessão registrada, com capacidade validada antes de adquirir | Bancos compartilhados entre sessões/dispositivos físicos distribuídos não estão implementados; cadastro incompatível é bloqueado |

Não há prova de ausência de **deadlock distribuído**: faltam arbitragens de slots pelos repetidores reais, qualificação de relógios/sincronismo, partições de rede, fencing reconhecido no equipamento e recuperação de operações físicas com resultado incerto. TTL central e SQLite não substituem essas garantias. Um fóton ou um estado que decoeriu não pode ser recuperado por repetir a requisição.

## Falhas reproduzidas e correções

- O duplo consumo persistido já estava protegido pelo CAS existente. Foi corrigido um recibo de purificação que podia devolver a versão antiga `delivered` depois de outro motor consumir o par.
- Duas sessões podiam assumir o mesmo banco orbital exclusivo: a reprodução reservou 32 modos para capacidade 16. A admissão agora recusa a dupla posse, sem inventar um escalonador distribuído.
- O planejamento era interrompido pelo relógio de CPU: com a mesma semente, o teste executou 12 ou 3 episódios. Agora executa a quota declarada, incluindo-a no hash das entradas. São 24 episódios **por avaliação de política**; a política de referência de reserva pode acrescentar outros 24. `time_budget_s` permanece compatível como alvo indicativo e não é mais corte científico.
- O cache de calibração estática tinha apenas 16 entradas e repetia a busca em grade numa topologia com mais de 16 comprimentos. Passou a 512 configurações imutáveis. Equações, parâmetros, amostragem e ações continuam iguais.

Evidências: `outputs/defect-corrections-20261012/pair-atomicity-qualification.json` (91 casos distintos), `routing-work-fixed-20261012.xml` (22 casos), `sequence-control/linux-work-cache-tests.xml` (7 novos casos no Linux), `routing-cache-engine-regressions.xml` (91 aprovados, 1 teste opcional não executado pela ausência do ZIP QKD original). A tentativa negativa foi preservada; os tempos de suítes não são latências do controlador.

## Encerramento, testes portáveis e apresentação

O encerramento em Qt passou a invalidar a apresentação e drenar workers sem bloquear a thread gráfica. A troca de fonte aguarda também a drenagem do wrapper usado pelo executável. Sign out retorna ao login e mantém o núcleo executando, conforme o requisito do produto. Consulte `orionq_provider_shutdown_20261012.md`.

Os seis testes históricos já corrigidos passaram novamente. Cinco dependências de `outputs/` e `work/` foram removidas: 415 testes passaram num checkout separado, sem essas pastas. Duas falhas atuais de fixtures de segurança foram corrigidas mantendo as assertivas de autorização. Consulte `orionq_test_portability_20261012.md`.

A apresentação identifica por enlace dados recebidos, física/protocolo de referência, controladores efetivamente usados e fallback. Quantum.otf e seu aviso foram removidos do fonte/pacote atual; a atribuição cartográfica ficou legível e acessível. Consulte `orionq_processing_labels_20261012.md` (75 casos Windows/27 Linux) e `orionq_asset_licensing_20261012.md` (86 casos e wheel sem a fonte). Os EXE/ELF antigos foram preservados e **não incorporam as correções do fonte até nova compilação**.

## Demonstração e desempenho

Hardware: Ryzen 7 5700X (8 núcleos/16 threads), 32 GB RAM, Windows 11 build 26200, estados no HDD SATA Hitachi do disco E:. Mesma semente 20261004, bancos novos separados, processamento/persistência comuns. Profiling e gravação são ensaios separados.

| Cenário | Amostras | Mediana do passo | Máximo | Inicialização |
|---|---:|---:|---:|---:|
| Completo antes (76 pontos, 136 enlaces, 11 satélites) | 8 | 12,24 s | 33,28 s | 15,53 s |
| Completo após quota fixa/cache (mesma topologia) | 8 | 7,19 s | 16,27 s | 52,01 s |
| Salvador menor (10 pontos, 11 enlaces) | 12 | 0,400 s | 1,742 s (primeiro passo frio) | 3,94 s |

Os onze passos seguintes da topologia menor ficaram entre 0,400 e 0,441 s. O cenário completo **não atende 1 s**; a inicialização piorou no ensaio e permanece variável. Oito amostras, HDD e alteração necessária da quantidade efetiva de episódios não permitem atribuir um ganho geral causal à comparação. O perfil de dois passos separou 6,18 s em `fsync`, 6,09 s em commits SQLite, 5,52 s no runtime de protocolos e 4,25 s em buscas de calibração (tempos inclusivos podem se sobrepor). Auditoria e durabilidade não foram desativadas.

O launcher fonte aceita `--demo-preset salvador_metro`; o preset global continua padrão. Vídeo nativo: `outputs/defect-corrections-20261012/metro-video-r3/orionq-metro-demo.mp4` (32,2 s/161 frames). O roteiro usa controles reais/QTest: login pela identidade do SO, foco e seleção no mapa, isolamento, restauração, rotas, chaves, auditoria e Sign out. A cadeia auditada foi verificada; nenhuma autenticação foi contornada. Frames repetidos preservam pausas reais; não aceleram o tempo científico. A primeira tentativa falhou na finalização do gravador e a segunda clicou antes de o resultado estar disponível; ambas foram preservadas. A terceira passou sem erros.

Reproduzir: `python tools/benchmark_network_step.py --output <pasta-nova> --steps 20 --preset brazil-institutions-v1`; para carga menor, `--preset salvador_metro`. A gravação usa `python tools/record_metro_console_demo.py --output <pasta-nova> --ffmpeg <executável>`. O codificador auxiliar está isolado em `E:/OrionQ/dependencies/media-tools`, fora das dependências de produção.

## SeQUeNCe: aquisição, reserva e recuperação

A execução independente do commit `3133c66d80a898c6c3cc9af9eec3fa94fdbe2233` produziu, no cenário nominal de dez nós, 1.280 envios, 1.222 chegadas quânticas, 973 cliques e 964 mensagens clássicas recebidas, sem shims ou imports do produto. A aplicação normalizou as observações, executou o mesmo processamento PS/PL emulado e ingressou pela API real TLS 1.3/mTLS.

O runner autenticado passou nove critérios: ingestão de onze nós, métricas calculadas no backend, dez reservas/liberações, recusa de ponte física ausente, recusa de entrelaçamento sem caracterização, auditor sem permissão de mutação e cadeia de auditoria válida. N01 continua emulado e sem ponte física.

A resiliência passou onze critérios adicionais: falha N05 identificada invalidou a reserva, o endpoint foi bloqueado, outra rota foi possível, a mesma sessão não ressuscitou o estado e novas sessões/novos callbacks nativos recuperaram a capacidade pelo pipeline normal. Evidências em `outputs/defect-corrections-20261012/sequence-control/resilience/`, incluindo `authenticated-api-trace.jsonl`.

Esse ensaio demonstra **controle de capacidade sobre observações do SeQUeNCe**. Os runners existentes adquirem primeiro e reservam depois; não devolvem por si só os comandos à timeline nativa. A extensão opcional `--interactive` passou depois em processo persistente: o mesmo PID/timeline/nós recebeu recibos do controlador e encaminhou mensagens por `ClassicalChannel`. Executou N02→N03→N04→N05→N06; após falha real do nó **simulado** N05, executou o desvio N02→N11→N10→N09→N08→N07→N06; após novas sessões, entregou N05→N06. Os callbacks retornaram pela normalização/API comum. São nove verificações do runner e 14 de resiliência/execução nativa, além de dez testes de contrato em cada SO. A repetição aprovada está em `sequence-control/closed-loop-native-r2/resilience.json`. A primeira tentativa foi rejeitada por vínculo incorreto de sessão no harness e foi preservada; corrigiu-se o harness, mantendo a recusa do backend. Essa malha fechada é **clássica** e continua reservando slots lógicos, sem endereçar memória física orbital. Cliques não comprovam pares entrelaçados nem chaves secretas. Nenhum resultado deste relatório substitui qualificação de bancada ou prova de TRL 5.

Consolidação SeQUeNCe final: `orionq_sequence_control_20261012.md` e `sequence-control/qualification.json` registram 82 chamadas concluídas, 3.916 observações/IDs únicos, três entregas clássicas (4+6+1 saltos), validação de perdas, hashes de 47 arquivos e todos os processos próprios encerrados. O contrato de execução passou dez casos em Windows e dez em Linux.
