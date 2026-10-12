# OrionQ — validação de rede com SeQUeNCe, 10/10/2026

## Resultado e escopo

O censo cobre a topologia completa de demonstração: **71 componentes físicos,
131 enlaces, 71 comunicadores, 36 sessões, 20 endpoints institucionais,
39 repetidores, três estações de medição, nove satélites e três FPGAs**.
São 76 demandas globais e quatro demandas legadas preservadas.

As **21 execuções nativas SeQUeNCe passaram nos critérios prospectivos**:
sete cenários em três sementes. Os 13 bancos terrestres produziram **236
entregas registradas na aplicação iniciadora**, distribuídas por cinco sessões;
oito sessões não entregaram pares no intervalo observado. Incluindo as
referências funcionais, foram 691 callbacks de entrega na ponta iniciadora.
Esses números não duplicam as notificações das duas pontas.

As três execuções globais de 64 ticks passaram nos 16 critérios declarados por
semente: **2.220 callbacks clássicos nativos, 1.890 admissões e 330 rejeições**
pelas falhas injetadas. Foram conservados 42.806 eventos globais e 423.728 eventos
quânticos locais. O status é **`partial_verified`**, com
`covered_scope_passed=true` e `full_hybrid_quantum_network_validated=false`.
A campanha durou 1.021,60 s de relógio real, aproximadamente 17 minutos.

**A rede quântica híbrida completa permanece sem validação externa integral.**
TF-QKD, payload óptico satelital e física quântica dos enlaces entre satélites
não receberam modelos nativos independentes qualificados nesta campanha.
Controle clássico global, reservas terrestres e protocolos de referência têm
escopos diferentes e não são combinados para afirmar comunicação quântica
global. Não há atribuição de TRL, hardware qualificado ou bits secretos
certificados.

## Evidências e reprodução

- [Protocolo prospectivo](../../configs/qualification/sequence_network_v1.json):
  `SEQ-NETWORK-20261010-v1`, revisão 1.
- [Congelamento](../../outputs/sequence_network_20261010/run1/freeze.json):
  595 arquivos, sendo 583 arquivos de produção e 12 arquivos de campanha,
  contratos, testes e documentação; 21 entradas nativas, censo e protocolo.
- [Censo](../../outputs/sequence_network_20261010/run1/census.json) e
  [190 combinações institucionais cadastradas](../../outputs/sequence_network_20261010/run1/endpoint-routes.json).
- [Resultado da campanha](../../outputs/sequence_network_20261010/run1/report.json),
  [condições do ambiente](../../outputs/sequence_network_20261010/run1/environment.json)
  e [resumo derivado](../../outputs/sequence_network_20261010/run1/delivery-summary.json).
- [Testes finais](../../outputs/sequence_network_preparation_20261010/combined-tests3.xml):
  **87 aprovados em 37,51 s**; Ruff sem achados nos cinco tools e quatro testes.
- [Testes de identificação da matriz](../../outputs/sequence_network_preparation_20261010/coverage-tests1.xml):
  **30 aprovados em 0,53 s**, com Ruff aprovado; 117 testes distintos no conjunto.
- [Contratos, parâmetros e comandos](../product/orionq_sequence_network.md).

SeQUeNCe real **1.2.0**, checkout upstream limpo no commit
`3133c66d80a898c6c3cc9af9eec3fa94fdbe2233`, sem modificar sua implementação,
carregado por Python isolado. Cada execução conserva o comando, exit code,
entrada, saída, stdout, stderr e trace JSONL comprimido com SHA-256.

Ambiente: Windows 11 Pro 10.0.26200; AMD Ryzen 7 5700X, oito cores e 16 threads;
34.280.902.656 bytes de RAM física; Python 3.12.14. SeQUeNCe usa NumPy 2.5.3,
SciPy 1.18.1, NetworkX 3.7, QuTiP 5.3.1, qutip-qip 0.4.2, gmpy2 2.3.2 e
stim 1.16.0. O ambiente OrionQ usa NumPy 2.5.2, PySide6 6.11.2, pytest 9.1.1
e Ruff 0.16.6. O ensaio é de backend, sem exercício da GUI ou do executável.

```powershell
$env:PYTHONPATH = 'src;tools'
$py = '.audit-winvenv\Scripts\python.exe'
$seqpy = 'outputs\cross_simulator_preparation_20261010\sequence-venv\Scripts\python.exe'
$run = 'outputs\sequence_network_20261010\nova-rodada'
& $py tools\run_sequence_network_campaign.py prepare --output $run
& $py tools\run_sequence_network_campaign.py run --output $run `
    --sequence-python $seqpy --sequence-src C:\SeQUeNCe
```

Escolher um diretório novo: o runner recusa sobrescrita, mudança de fonte ou de
entrada congelada. Indisponibilidade, erro ou timeout do simulador permanecem
falhas explícitas; não há resultado local substituto para um caso externo.

## Modelo nativo e parâmetros

As 13 sessões terrestres abrangem 54 componentes físicos únicos. Seus bancos
dedicados são representados por **67 routers virtuais e 54 spans quânticos**,
com estações BSM virtuais do modelo Barrett–Kok. Não são 67 novas instalações.
`RouterNetTopo`, `QuantumRouter`, `RequestApp`, RSVP, ResourceManager, geração,
memórias e swapping executam no SeQUeNCe. O experimento de purificação usa seu
protocolo BBPSSW e registra dois recursos de entrada por tentativa.

A associação explícita `native_id → physical_id` consta em
`census.native_mapping.session_mappings.devices`. Campos redundantes
`physical_node_id` e `resource_bank_id` do `node_census` nativo ficaram nulos;
isso é uma limitação de exportação, sem perda da associação conservada no censo.

Todos os 36 bancos OrionQ são hipotéticos e dedicados por sessão: 132 alocações
locais, 536 modos de memória, 74 fontes de pares, seis fontes de pulsos e 192
terminais ópticos. A reserva nativa solicita duas memórias por extremo e quatro
nos intermediários. Não há qualificação de arbitragem entre equipamentos
físicos compartilhados.

No cenário completo, horizonte nativo de 1,1 s, reserva a partir de 0,05 s e
janela ativa de um segundo. O maior RTT clássico de configuração é
0,037261393973282673 s; não foram encurtadas distâncias para obter reservas.
Comprimentos efetivos, perdas escalares, frequências e associações dos bancos
originais são conservados segundo os mapeamentos declarados. Referências do
Rio usam parâmetros alterados e identificados, horizonte de 1,5 s, fontes de
10 kHz, fidelidade de entrada 0,95 no caso nominal, coerência de um segundo,
eficiência de emissão 0,8, detectores de eficiência 0,8 e operações locais
ideais. O caso de purificação usa fidelidade de entrada 0,8 e limiar 0,83.

O mapeamento Barrett–Kok é um experimento alternativo à geração agregada do
OrionQ. Expiração nativa de memória não qualifica seu modelo contínuo de
decoerência. Distribuir perda de inserção em atenuação de fibra conserva uma
perda escalar, sem validar a localização dessa perda. Detecção idealizada,
ruído local, tomografia e eficiência de escrita/leitura não recebem equivalência
implícita. Não são afirmadas igualdade de taxas, calibração física ou precisão
universal do simulador de produção.

## Entregas por sessão terrestre

As três colunas usam as sementes 2026101001, 2026101002 e 2026101003. Todas as
13 reservas foram aprovadas em cada execução. Aprovação da reserva não implica
entrega. Zero significa ausência de callback de entrega na janela deste
experimento; não é prova de impossibilidade universal.

| Sessão | Extremos físicos | Semente 1 | Semente 2 | Semente 3 |
|---|---|---:|---:|---:|
| BR-BUTANTA-ENT | USP ↔ IME-USP | 33 | 28 | 29 |
| BR-CAMPINAS-ENT | USP ↔ UNICAMP | 0 | 0 | 1 |
| BR-IME-CAMPINAS-ENT | IME-USP ↔ UNICAMP | 0 | 2 | 2 |
| BR-NORDESTE-ENT | CIMATEC ↔ CIQUANTA | 0 | 0 | 0 |
| BR-RIO-USP-ENT | CBPF ↔ USP | 0 | 0 | 0 |
| BR-SALVADOR-RIO-ENT | CIMATEC ↔ CBPF | 0 | 0 | 0 |
| BR-SALVADOR-USP-ENT | CIMATEC ↔ USP | 0 | 0 | 0 |
| CN-BEIJING-JINAN | Pequim ↔ Jinan | 0 | 0 | 0 |
| CN-HEFEI-SHANGHAI | Hefei ↔ Xangai | 0 | 0 | 0 |
| CN-JINAN-HEFEI | Jinan ↔ Hefei | 0 | 0 | 0 |
| RIO-ENTANGLEMENT | PUC-Rio ↔ UFRJ, via IME-Rio e CBPF | 17 | 12 | 15 |
| RIO-IME-ENDPOINT | IME-Rio ↔ CBPF | 27 | 37 | 33 |
| US-REPEATER-EXPERIMENT | IQMP ↔ IBM-Watson | 0 | 0 | 0 |
| **Total** | | **77** | **79** | **80** |

Os registros `EG_SUCCESS` do cenário completo foram 228/214/230, com notificações
nas duas pontas; `ES_SUCCESS`, 35/25/30. Não houve `EP_SUCCESS` no cenário
completo; purificação foi exercitada em cenário de referência separado. Os
67 eventos de aprovação por execução correspondem aos routers do caminho,
não a 67 pedidos independentes. Foram 13 pedidos.

## Casos funcionais e falhas

| Cenário nativo | O que foi verificado | Entregas 1 / 2 / 3 | Critérios |
|---|---|---:|---|
| Topologia terrestre completa | Bancos, reservas, canais, geração e entregas observadas | 77 / 79 / 80 | Passaram |
| Referência multissalto Rio | Geração, swapping e entrega na ponta iniciadora | 33 / 39 / 35 | Passaram |
| Purificação | BBPSSW, aceitação e consumo de dois recursos distintos | 32 / 35 / 43 | Passaram |
| Detector com eficiência zero | Reserva aprovada sem fabricar pares | 0 / 0 / 0 | Passaram |
| Canal clássico bloqueado | Ausência de rota/entrega | 0 / 0 / 0 | Passaram |
| Disputa e liberação | Reserva concorrente recusada; nova reserva entrega após liberação | 51 / 53 / 49 | Passaram |
| Falha e recuperação | Nenhuma entrega durante a queda; entregas novas após recuperação | 26 / 34 / 25 | Passaram |

Após liberação de recursos, as novas reservas entregaram 25/25/23 pares;
a reserva concorrente entregou zero em todas as sementes. Após recuperação do
router, as novas reservas entregaram 17/22/14; os pedidos durante a queda
ficaram `pending_no_response`, sem entrega. A purificação registrou
64/70/86 eventos `EP_SUCCESS` nas duas pontas e 98/96/90 registros de consumo
de dois recursos de entrada. Esses contadores descrevem o framework; não são
rendimento experimental de um equipamento.

Nos 21 casos: 3.398 registros `EG_SUCCESS`, 793 `ES_SUCCESS`, 220 `EP_SUCCESS`
e 288.371 recibos clássicos nativos. A diferença máxima observada entre o
horário de recepção e o atraso configurado foi zero ps, dentro da tolerância
prospectiva de dois ps. Isso comprova execução e conformidade temporal com as
entradas; não calibra um relógio ou uma infraestrutura clássica física.

## Fidelidade e limitação do formalismo

O piloto prolongado `density_matrix` falhou no upstream fixado:
`SingleAtomBSM` recebeu um estado com mais de duas chaves e levantou
`NotImplementedError("Unknown state")`. O reproducer independente preservado
em [diagnose-density-native.json](../../outputs/sequence_network_preparation_20261010/diagnose-density-native.json)
mostra que uma correção remota pode restaurar o estado compartilhado de uma
memória já resetada. Nenhum patch upstream, descarte de exceção ou reparo oculto
foi usado. Os pilotos e stderr permanecem no diretório de preparação.

A campanha congelada usa explicitamente **`ket_vector`**, compatível com os
exemplos oficiais. Seus estados retidos são **trajetórias puras condicionais**,
sem demonstrar a densidade média de um ensemble. `RequestApp` admite entregas
segundo a fidelidade escalar dos metadados. Essa fidelidade não é confundida com
uma tomografia independente.

Exemplo: no primeiro callback da referência nominal da primeira semente,
metadados de fidelidade 0,7737809375 coexistem com uma trajetória cuja
sobreposição com Φ+ é 1. A seleção inicial amostra estados Bell, mas a
recorrência escalar de swapping/purificação não constitui uma verificação
independente da média do ensemble. Todos os estados retidos desta campanha
têm máximo entre estados Bell igual a 1; **isso não demonstra fidelidade média
igual a 1 na rede**.

As correções Pauli podem estar em trânsito no instante do callback. As quatro
entregas BR-IME-CAMPINAS têm sobreposição Φ+ igual a zero nesse instante;
o runner não confirma independentemente o reconhecimento do frame nas duas
pontas. Portanto, callback aceito pelo framework não é apresentado como par
final corrigido e qualificado para uso operacional. Faltam ensaios prospectivos
do ensemble, confirmação do frame e calibração dos modelos de ruído.

## Integração global e matrizes

O ensaio usa `SequenceClassicalSource` no contrato existente de encaminhamento.
Callbacks reais `ClassicalChannel` retornam de outro processo e atravessam a
validação, o agendamento e a persistência do `GlobalOperationManager` existente.
A fonte óptica dos 36 serviços continua sendo a fonte simulada original OrionQ,
identificada nos recibos. Não há injeção direta de fidelidade ou chave final.

São seis fases por semente: nominal (10 ticks), perda de entrega (10), timeout
(12), recuperação da fonte (16), isolamento de GEO-AMERICAS-SIM (8) e
restauração (8), totalizando 64 ticks. Perda e timeout são injetados explicitamente
**após** a recepção pelo canal SeQUeNCe ideal sem perdas. O backend administra
o sucesso, a falha e as novas tentativas a partir desses recibos.

| Fase | Tempo de rede por semente | Sucessos clássicos acumulados por semente | Falhas clássicas acumuladas por semente | Serviços ópticos locais: sementes 1 / 2 / 3 |
|---|---:|---:|---:|---:|
| Nominal | 10 s | 40 | 30 | 37 / 44 / 41 |
| Perda de entrega | 20 s | 40 | 80 | 69 / 77 / 78 |
| Timeout | 32 s | 40 | 140 | 115 / 118 / 120 |
| Fonte recuperada | 48 s | 80 | 140 | 170 / 171 / 178 |
| Satélite isolado | 56 s | 108 | 164 | 195 / 194 / 202 |
| Satélite restaurado | 64 s | 128 | 176 | 226 / 224 / 232 |

Esses contadores são acumulados. Perda e timeout acrescentam zero sucesso
clássico e, por semente, 50 perdas e 60 timeouts sem admissão. Os 20 endpoints
originaram e receberam probes no nominal. Durante o isolamento, rotas afetadas
foram bloqueadas ou desviadas por caminhos disponíveis; nenhum novo salto
nativo usou o satélite isolado. A restauração exigiu callbacks e entregas novos.
Serviços ópticos locais não são resultados quânticos externos do SeQUeNCe.

| Semente | Callbacks clássicos nativos | Admitidos | Rejeitados | Eventos globais | Eventos quânticos locais | Critérios globais |
|---|---:|---:|---:|---:|---:|---|
| 2026101001 | 740 | 630 | 110 | 14.288 | 141.340 | 16/16 |
| 2026101002 | 740 | 630 | 110 | 14.238 | 141.134 | 16/16 |
| 2026101003 | 740 | 630 | 110 | 14.280 | 141.254 | 16/16 |
| **Total** | **2.220** | **1.890** | **330** | **42.806** | **423.728** | **48/48** |

Todos os 1.890 recibos de recepção admitidos foram vinculados a callback nativo,
payload, commit, sequência e envio anterior. As cadeias de hashes dos eventos
globais e quânticos foram recomputadas por leitura independente em stdlib, sem
importar o produto. Nenhum desvio foi encontrado. Os 471 critérios registrados
nas execuções nativas e 48 nos fluxos globais passaram; são verificações
repetidas dos casos declarados, não 519 experimentos científicos independentes.

Cada salto clássico integrado possui Timeline SeQUeNCe própria, com propagação,
serialização e jitter fornecidos pelo OrionQ. A ordem causal global e a escolha
das rotas continuam no backend OrionQ. Não é qualificação independente da
geometria, da autenticação ou da contenção de banda de uma rede física.

A matriz externa contém 317 registros: 71 componentes físicos, 131 enlaces,
36 sessões, 76 demandas globais e três FPGAs. As quatro demandas do planejador
legado são preservadas no censo, sem execução externa neste ensaio. Bancos e
capacidades estão no detalhe dos componentes e das sessões. Cada snapshot
operacional também exporta uma matriz de 71 dispositivos e os 131 enlaces no
`live.json`; não se deve confundir essa matriz de dispositivos com a matriz
externa de cobertura.

A conferência final encontrou um erro de identificação na matriz bruta:
o exportador procurava `device_id` no topo da FPGA, embora ele esteja em
`configuration.device_id`. As três linhas saíram como `unknown`. A
[matriz bruta](../../outputs/sequence_network_20261010/run1/coverage-matrix.json)
e o resultado congelado foram preservados. O pós-processador adicional gera
a [matriz identificada JSON](../../outputs/sequence_network_20261010/run1/coverage-matrix-identified.json),
o [CSV de 317 registros](../../outputs/sequence_network_20261010/run1/coverage-matrix-identified.csv)
e a [tabela legível](../../outputs/sequence_network_20261010/run1/coverage-matrix-identified.md),
associando FPGA-DEMO-1/2/3 às configurações e hashes originais.
Todas as linhas não-FPGA e todos os resultados científicos ficam intactos.
O [manifesto de derivação](../../outputs/sequence_network_20261010/run1/coverage-identification-manifest.json)
documenta hashes, erro original e pós-processamento; seus testes e a checagem
de entrega verificam unicidade e igualdade dos IDs com o censo.

Após `prepare` e `run`, finalizar as identidades do relatório:

```powershell
& $py tools\finalize_sequence_network_coverage.py --campaign $run
```

Os 190 pares institucionais são um censo estático, **não 190 comunicações
testadas**. Distâncias orbitais de referência iguais a zero fazem 189 desses
caminhos cadastrados atravessar satélites. Não se pode inferir que são ótimos,
disponíveis ou fisicamente viáveis. A atividade efetiva é demonstrada somente
pelas demandas e observações de tempo de execução, usando os comprimentos
dinâmicos do backend.

## Cobertura não demonstrada e trabalho restante

| Escopo | Estado nesta campanha | Evidência necessária |
|---|---|---|
| 13 bancos terrestres | Funcionamento nativo observado sob experimento alternativo | Ensemble, frame, ruído e calibração; janelas maiores para rendimento |
| 3 sessões TF-QKD | Sem modelo quântico nativo qualificado SeQUeNCe | Modelo independente de pulsos/interferência/decoy e pós-processamento |
| 16 sessões satélite–solo | Sem qualificação óptica externa | Modelo independente de geometria, atmosfera, apontamento, fonte e detecção |
| 4 sessões satélite–satélite | Sem qualificação quântica externa | Terminais, visibilidade, perdas e medições independentes |
| Relay quântico orbital global | Infraestrutura/protocolo ausente | Memórias/BSM orbitais, recursos e confirmação causal nas duas pontas |
| 3 FPGAs | Cadastro e simulador local preservados | Implementação independente e depois qualificação na bancada |
| 4 demandas legadas | Preservadas no censo | Campanha específica do planejador; sem sucesso externo inferido |
| Hardware, QKD segura, TRL | Não qualificados | Bancada, segurança experimental e avaliação humana identificada |

As instituições BSC, Google Quantum AI, IQC, Microsoft Quantum e Xanadu
continuam na rede e no controle clássico, mas não pertencem às 13 sessões do
experimento quântico terrestre. O ponto IBM-Watson é uma instalação de cenário;
não reintroduz a integração descontinuada com serviços IBM Quantum.

Fontes de produção e funcionalidades atuais foram preservadas. Os modelos ML
continuam opcionais e não foram ativados; atuação física permanece bloqueada.
Não houve recompilação do executável, mudança de versão, instalação do NetSquid
ou operação de Git nesta campanha. O pós-processamento não modifica as 595
fontes congeladas nem a execução; possui manifesto próprio.

## Integridade de entrega

| Artefato congelado | SHA-256 |
|---|---|
| report.json | `1fe501c9740a671120708c7e278a29e18cd6bb672c053b367024409626d1a780` |
| freeze.json | `4099c47085ebb3b674753ea174c910516b952e879328a9ccecb65b35ac531548` |
| protocol.json | `99d1fa3a96f91ebfddef48f82eccecae5aa4c04acb3c2a8eb147c1e85a99b8c3` |
| census.json | `e3457af5e1aee1351f96c2e4f546fbb8ae390c09d82ac878957004fb5d5a85f0` |
| coverage-matrix.json bruta | `b85ea7a98de8832bf5e7c0d4ece0db96857fb54d9c334b5d137db318370ba5e0` |
| coverage-matrix-identified.json | `55c0f2d95d8ef07df31fac427e8b6228d65b493fbb4097a671168a1a846cde94` |
| coverage-identification-manifest.json | `a22be7dd37479336b1c98ff9d1a40422ddaf5574e3ff9024c947f81b88ed8876` |

A [checagem de entrega](../../outputs/sequence_network_20261010/run1/handoff-check.json)
confere fontes, entradas, matrizes, links locais e limites das afirmações. Os
583 arquivos sob `src/` são idênticos aos congelados na campanha anterior
`cross_simulators_20261010/run2`; nenhum modelo de produção foi alterado.

## Fontes primárias

Consultadas em 10/10/2026, confrontadas com o código do commit executado:

- [Wu et al., SeQUeNCe](https://arxiv.org/abs/2009.12000): arquitetura de simulação.
- [Tutorial de topologia e aplicações](https://sequence-rtd-tutorial.readthedocs.io/latest/tutorial/sequence3min/three_minute.html): routers, canais e `RequestApp`.
- [RSVP](https://sequence-rtd-tutorial.readthedocs.io/stable/references/network_management/rsvp.html): reservas e admissão de recursos.
- [NetworkManager](https://sequence-rtd-tutorial.readthedocs.io/latest/tutorial/chapter5/network_manager.html): controle e roteamento.
- [Código upstream fixado](https://github.com/sequence-toolbox/SeQUeNCe/tree/3133c66d80a898c6c3cc9af9eec3fa94fdbe2233): autoridade para o comportamento efetivamente executado.
