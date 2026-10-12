# OrionQ: protocolo de qualificação TRL 5 do software/DigitalTwin

Estado em **09/10/2026: preparação da qualificação; TRL 5 não demonstrado**. O
elemento avaliado é **SOFT-DT**: software local de aquisição somente para leitura,
simulação declarada, replay, processamento causal, persistência, API e console.
Uma estimativa interna de TRL 4 para esse protótipo integrado não é certificação.
Este protocolo não promove a maturidade da rede quântica física, do equipamento
ou da segurança de chaves. Modelos opcionais começam OFF e atuação física é
proibida durante todos os ensaios.

O contrato executável dos critérios é
[trl5_software_v1.json](../../configs/qualification/trl5_software_v1.json), schema
`orionq.trl5-software-protocol.v1`, revisão 1, identificador `SOFT-DT-TRL5-v1`.
Resultados devem ficar em diretórios novos e separados; não se grava um resultado
de aprovação no próprio protocolo. Um relatório local aprovado não atribui TRL.

## Referência e escopo

A [NASA NPR 7123.1D, Apêndice E](https://nodis3.gsfc.nasa.gov/displayDir.cfm?Internal_ID=N_PR_7123_001D_&page_name=AppendixE)
está vigente desde 05/07/2023, com Change 2, e foi consultada em 09/10/2026.
Para software, TRL 4 trata de integração e validação funcional dos componentes
críticos; TRL 5 requer implementação e testes ponta a ponta no ambiente relevante,
com desempenho previsto. Sistemas ou simulações podem representar o
ambiente-alvo. Portanto, equipamento próprio e laboratório físico não são
requisitos universais para esse escopo de software; a representatividade do
ambiente continua precisando de demonstração.

O [ECSS-E-HB-11A, 01/03/2017](https://ecss.nl/wp-content/uploads/2017/03/ECSS-E-HB-11A%281March2017%29.pdf),
consultado em 09/10/2026, orienta a avaliação por função crítica, ambiente,
efeitos de escala e evidência. A seção5.3.5 trata da verificação em ambiente
relevante; A.3 discute software e requisitos congelados; 5.2.3 recomenda
especialistas independentes da equipe de desenvolvimento. Usamos essas
orientações para definir a avaliação G07, sem declarar conformidade integral
ECSS ou certificação NASA. O handbook não substitui engenharia e garantia de
qualidade de software.

As funções incluídas são admissão e validação de observações, relógios,
sincronização, canais associados, protocolos e recursos, comunicação clássica,
resultados duráveis e sua apresentação autorizada. A topologia demonstrada,
os modelos terrestres/satelitais e o ciclo exclusivo Mapa/Globo integram o teste.
As cinco famílias declaradas — troca de entrelaçamento, purificação, memória,
TF-QKD reduzido e enlace satelital/Doppler — precisam de cobertura explícita;
mostrar seus nomes na interface não satisfaz esse requisito.

Ficam fora da alegação de maturidade: rede física implantada, transmissão ou
controle de equipamento, RF/terminal óptico qualificado, instalação institucional,
QKD certificado e previsão temporal medida WDM→fidelidade Bell. A rede global
desenhada não prova conexões institucionais. Taxas QKD condicionais, geometrias
orbitais previstas e PRNGs públicos não são chaves de produção, housekeeping
medido ou comunicação física. `certified_secret_bits` permanece zero.

## Ambiente relevante e congelamento

O perfil inicial é Windows 11 x64, Python 3.12, Qt com QPA `windows`, DPR 1,
resoluções 1920×1080 e 3440×1440. A referência de hardware é Ryzen 7 5700X,
16 processadores lógicos, 32 GiB RAM e RTX 3060 de 12 GiB quando disponível. Isso
identifica um perfil de ensaio, não o hardware mínimo universal do produto.
Outro computador precisa de perfil próprio e análise de representatividade;
ganhos ou aprovações não são automaticamente transferidos.

Antes de pontuar, o manifesto deve registrar hash do protocolo, identidade e
estado dirty da fonte, hashes de arquivos, produto, dependências, datasets,
oráculos, sementes, comandos, relógio e ambiente. Os hashes de entrada são
reconferidos ao fim. Uma mudança de escopo ou limiar depois de observar o
resultado exige nova revisão, mantém a rodada antiga e não a torna aprovada.
Hashes comprovam integridade dos bytes; não autenticam autoria sozinhos.

Não executar benchmark junto de build, profiler pesado ou outro benchmark.
Registrar carga externa, CPU, memória, GPU/driver ou sua indisponibilidade,
backend gráfico, tamanhos efetivos, DPR, contagens de componentes/satélites e
volume de eventos. Não acessar cadastro ou credenciais do operador. As fontes
e fixtures devem ser claramente identificadas.

As sementes congeladas são três por condição: nominal 2026100900–2026100902,
degradada 2026100910–2026100912, indisponível 2026100920–2026100922 e
recuperação 2026100930–2026100932. A admissão deve confirmar que não foram usadas
para treino, ajuste ou seleção. Novas sementes não tornam independente a
família de dados, o modelo físico ou o avaliador.

## Matriz de requisitos e decisão

`pass` exige todas as evidências previstas do gate. Uma violação demonstrada
produz `fail`; ausência de cobertura produz `not_demonstrated`. Uma falha
observada não desaparece porque faltam outras medições. Na consolidação,
qualquer `fail` prevalece; sem falhas, qualquer evidência obrigatória ausente
mantém `not_demonstrated`. Sete `pass` permitem a avaliação humana de maturidade,
sem atribuição automática de TRL 5.

| Gate | Evidência necessária para `pass` | `fail` | `not_demonstrated` e limites |
| --- | --- | --- | --- |
| **G01 — scope_frozen** | Escopo, funções críticas, ambiente, critérios e referências congelados antes da pontuação; manifesto completo e hashes consistentes | Modificação posterior sem nova revisão; identidade ou entrada divergente | Manifesto ou justificativa do ambiente incompletos; integridade não prova exatidão científica |
| **G02 — reference** | Todas as funções/famílias mapeadas a referências independentes admitidas, domínio/unidades/incerteza declarados e tolerâncias numéricas congeladas; resultados dentro das tolerâncias | Erro além da tolerância, vazamento de treino/ajuste ou agregação inválida | Só goldens próprios, replay da mesma planta ou cobertura parcial; referências de outro regime não qualificam a operação pretendida |
| **G03 — pipeline** | Cenários nominais/degradados/indisponíveis/recuperados percorrem o fluxo comum de observação até recibo e apresentação; replay equivalente conserva ordem, métricas e linhagem; canais obrigatórios e origem demonstrados | Atalho de demonstração, métrica final injetada, comunicação inventada, inconsistência backend/UI ou violação de fronteira | Uma fixture local aprovada sem todos os estágios, fontes e famílias não qualifica o gate inteiro |
| **G04 — fault_recovery** | Falhas antes/depois de admissão, reserva/consumo, commit, recibo e resposta; reinício/retry sem duplicação/reutilização; rejeição de replay, dados inválidos e acesso indevido; perdas contabilizadas | Evento confirmado duplicado, chave consumida/incerta reativada, perda silenciosa ou aquisição em estado incerto | Matriz de falhas incompleta; ensaio local não qualifica energia, disco avariado, equipamento ou interlocks |
| **G05 — performance** | Todos os requisitos abaixo em carga completa, fonte e versão compilada; mesmas operações e métricas científicas; cobertura de cauda, duração, swaps e retenção | Limiar excedido, travamento, conteúdo incorreto, operação aceita perdida, poda de evento científico ou acúmulo de recursos | Benchmark curto, falta de FPS/cauda/soak ou escopo de produção; heartbeat não é frame rate |
| **G06 — reproduce** | Segunda instalação isolada reconstrói fonte/dependências/dados e a campanha completa; saídas e falhas dentro dos critérios; base funciona sem ZIPs/SDKs opcionais | Patch manual não documentado, substituição de arquivo/credencial ou divergência além da tolerância | Apenas checkout do desenvolvedor ou dependências herdadas; um venv isolado não comprova outro computador |
| **G07 — independent_review** | Especialista identificado fora da implementação revisa funções, relevância, dados, limites e resultados; conclusão autenticada ligada aos hashes | Rejeição da suficiência/relevância, conflito omitido ou divergência crítica não resolvida | Avaliador ausente; checker automático, revisão interna ou agente de IA não substituem especialista independente |

No início desta campanha, **os sete gates estão `not_demonstrated`**. Os relatórios
históricos informam hipóteses, limites e falhas conhecidas; não são reaprovados
retroativamente como ensaios congelados deste protocolo. G02/G03/G04 permanecem
assim quando o novo ensaio cobre apenas um subconjunto, mesmo que seu
`local_trial_passed` seja verdadeiro. G07 exige participação humana externa à
implementação e não será preenchido artificialmente.

## Referências, datasets e precisão

Cada referência admitida deve registrar origem, licença, versão/commit, SHA256,
unidades, base temporal, domínio, hipóteses, incerteza, independência da
implementação, observáveis esperados, tolerâncias numéricas, holdout e funções
críticas cobertas. A matriz deve conservar condições não cobertas. A aceitação
de G02 fica indisponível enquanto esse pacote por referência estiver incompleto.
Tolerâncias determinísticas e margens de equivalência estocástica devem ser
justificadas e congeladas **antes** da respectiva pontuação. Ausência de diferença
estatisticamente significativa não demonstra equivalência.

| Candidato já existente | Uso possível | Limite que permanece |
| --- | --- | --- |
| [SeQUeNCe pinado](../results/orion_q_routing_sequence_20260930.md), commit `3133c66d80a898c6c3cc9af9eec3fa94fdbe2233` | Outro simulador para roteamento e operações na configuração admitida | Cadeia de 4 nós, um par origem/destino; gêmeo calibrado pelo autor. Não cobre sozinho toda a topologia nem as cinco famílias |
| [Validação analítica](../../tests/test_physics_validation.py) e [matriz de purificação](../../tests/test_purification.py) | Verificação de limites, Werner/BBPSSW e composição de operações com oráculo independente quando demonstrado | Estados e ruídos reduzidos; [goldens gerados pelo próprio código](../../tests/test_physics_regression.py) são regressão, não validação experimental externa |
| [IrelandQCI/Toshiba e Bristol](../results/cross_platform_qkd_replication.md) | Observações experimentais públicas de coexistência e QBER/SKR | Magnitudes dependem da plataforma; ordem não randomizada; unidades e termos de uso precisam de verificação. Não há validação temporal WDM/Bell |
| [Órbitas públicas e orçamento finito](../results/orion_q_lab_hardening_20261007.md) | Admissão orbital, propagação e verificação matemática condicional | Efeméride não é housekeeping/payload; amostras fora da validade ficam indisponíveis; orçamento finito depende de fonte, detectores e autenticação declarados |

Os resultados de [compensação](../results/orion_q_polarisation_closed_loop_20260929.md),
[alocação](../results/orion_q_allocation_closed_loop_20260929.md) e
[QKD](../results/orion_q_qkd_closed_loop_20260929.md) reproduzem plantas e tabelas
do autor, com protocolos e sementes próprios. São evidência de implementação e
avaliação no domínio simulado. As extensões posteriores com atuadores limitados
devem ser admitidas por seus próprios protocolos; não se trata toda a manutenção
como atuador ideal. A reprodução da mesma planta não estabelece calibração
experimental independente, generalização ou segurança operacional.

## Desempenho: diagnóstico inicial e qualificação completa

O orçamento existente de **10 ms do diagnóstico de runtime permanece inalterado**.
O [roadmap congelado 5.60.1](../../release/windows/5.60.1-native-20261009-061920/roadmap-validation/report.json)
registrou20/20 amostras acima desse orçamento: a aprovação funcional não comprova
cumprimento do prazo. O orçamento corresponde à operação definida pelo diagnóstico
de runtime, não a uma promessa de processar a simulação global inteira em 10 ms.
Nenhum passo científico pode ser apagado, encurtado ou preenchido para atender
aos tempos visuais.

A primeira medição planejada usa o benchmark já existente, **uma repetição** em
3440×1440, 15 s por fase e 3 min de uso contínuo. Em seguida, sem concorrência,
executa 50 amostras de runtime após 5 aquecimentos, com orçamento 10 ms e carga de
computação externa ociosa. Esses parâmetros estão congelados em
`initial_baseline_protocol`; o resultado é diagnóstico inicial, não qualificação
completa. Resultado antigo ou uma só resolução não preenche medições ausentes.

Para a qualificação completa de G05, os requisitos prospectivos são:

| Grandeza | Critério congelado | Como interpretar |
| --- | --- | --- |
| Runtime | ≥ 1000 amostras após 5 aquecimentos; zero excedências de 10 ms; todas as amostras/overruns registrados | Limite empírico no perfil e operação declarados; não garantia universal de tempo real |
| Carga e repetição | Topologia atual inteira, todos os satélites/órbitas, telemetria e simulação; fonte e compilado; 3 repetições em cada resolução | Registrar a quantidade efetiva de componentes/eventos; não reduzir a carga para aprovar |
| Fases | 300 s cada em Mapa, Globo, abas e alternância; ≥ 100 trocas consecutivas; soak ≥ 20 min | O ensaio inicial 15 s/3 min não satisfaz essa cobertura |
| Heartbeat Qt | Timer 10 ms; gaps p95 ≤ 100 ms, p99 ≤ 250 ms, máximo ≤ 1000 ms; ≥ 1000 amostras para usar p99 | Mede event loop; não mede FPS. Contagem é condição necessária, não prova isolada da cauda |
| Interação | p95 entre ação e primeira pintura correta ≤ 100 ms; detalhes completos ≤ 1000 ms | `ready` deve corresponder a todos os dados da mesma fotografia; metadata ou primeiro callback não bastam |
| Animação | Intervalo de frames efetivos da animação ativa p95 ≤ 50 ms | Envelope de apresentação ≥ 20 Hz na carga medida; instrumentação de pintura é necessária |
| Correção | Zero erro de conteúdo, timeout de ação, perda não classificada ou alteração dos resultados/ordem científica | Agregar apresentação não permite descartar aquisição, protocolo ou persistência |
| Renderizadores | Um ativo; zero aposentados restantes; timers/listeners encerrados sem crescimento | Medir depois de cada descarte e ao terminar, conservando seleção/dados |
| Retenção | Após 5 min de aquecimento, bins 30 s: na segunda metade, inclinação RSS ≤ 2 MiB/min e amplitude ≤ 5% da mediana; contagens de renderer/timers/listeners sem crescimento | Usar inclinação robusta e dados crus; não subtrair estimativa de memória do observador do RSS |
| Ambiente | CPU, memória, carga externa e GPU/driver ou razão de indisponibilidade registrados | GPU compartilhada não pode ser automaticamente atribuída ao OrionQ |

Os limites de interface são requisitos de engenharia prospectivos desta
campanha: 100 ms para interação imediata, 1 s para acesso completo e 50 ms para um
envelope de animação de 20 Hz. Não são limites impostos ou garantidos por NASA,
ECSS, Qt ou pelo refresh do monitor. Deve-se medir diretamente a grandeza
declarada. A inclinação de memória e o piso de amostras não autorizam afirmar
ausência de vazamentos ou desempenho ilimitado.

Se qualquer limite medido for excedido, o relatório preserva a falha. É permitido
corrigir a implementação e repetir com nova identidade de fonte; não é permitido
afrouxar o requisito e apresentar a rodada anterior como aprovada. Uma alteração
justificada do requisito abre outra revisão e explicita a mudança de escopo.

## Relatório e próximos passos

O schema de saída previsto é `orionq.trl5-software-report.v1`. Cada gate registra
estado, critérios verificados/ausentes, falhas, evidências e hashes, inclusive
limites do dataset. O relatório distingue `local_trial_passed` de suficiência da
qualificação, conserva pendências de referências, desempenho e revisão externa
e mantém `automatic_trl_assignment=false`. Não inventar revisor, identidade,
assinatura, certificado ou parecer.

A sequência é congelar o manifesto, executar e registrar o diagnóstico inicial,
admitir referências e tolerâncias por função, preencher a matriz completa do
pipeline/falhas, realizar os ensaios completos de desempenho e reprodução, e
submeter o pacote à avaliação independente. A indisponibilidade de equipamento
nesta etapa não bloqueia automaticamente o escopo de software; a falta de uma
referência ou ambiente relevante demonstrado bloqueia a alegação correspondente.

Este incremento entrega requisitos e configuração. Não executa equipamentos,
não qualifica uma rede física e não declara TRL 5 atingido. Resultados e correções
da campanha serão ligados separadamente, preservando a história e os critérios.

## Ferramentas da primeira campanha

`tools/assess_trl5_software.py prepare` cria uma pasta nova, registra dependências,
hashes das fontes e datasets e congela `protocol.json`. Congela também os valores
esperados e tolerâncias dos oráculos analíticos em `analytic-acceptance.json`;
um relatório não pode redefinir esses critérios. `assess` verifica a identidade
dos arquivos, os resultados analíticos, os checks do pipeline e os limites
medidos. Reporta falha ou evidência ausente por gate; nunca atribui TRL.
Esses hashes não autenticam um autor nem substituem a avaliação independente.

No PowerShell, com o ambiente de desenvolvimento já instalado:

```powershell
$env:PYTHONPATH = "src"
$campaign = "outputs/trl5_initiation_NOVA_DATA/run1"
python tools/assess_trl5_software.py prepare --directory $campaign
python tools/benchmark_console_performance.py --output "$campaign/performance-baseline" --duration 15 --soak 3 --size 3440x1440
python -m quantum_autopilot.control_plane.runtime_diagnostic --output "$campaign/runtime-baseline" --samples 50 --warmup 5 --budget-ms 10 --condition idle
python tools/run_trl5_reference_campaign.py --output-dir "$campaign/reference-1" --seed 2026100900
python tools/run_trl5_reference_campaign.py --output-dir "$campaign/reference-2" --seed 2026100900
python tools/run_trl5_pipeline_campaign.py --output-dir "$campaign/pipeline-1" --seed 2026100900
python tools/run_trl5_pipeline_campaign.py --output-dir "$campaign/pipeline-2" --seed 2026100900
python tools/assess_trl5_software.py assess --directory $campaign
```

Executar os comandos sequencialmente, sem builds ou outros benchmarks em
concorrência. Preservar e registrar qualquer falha antes de continuar a coleta;
um exit code funcional zero do runtime não significa cumprimento de 10 ms.
Cada diretório de campanha e saída deve ser novo. A repetição de uma semente
documenta determinismo local, sem constituir holdout experimental ou segunda
instalação independente.

Os oráculos verificam elementos reduzidos: transmissão em dB, propagação,
purificação Bell por matriz/CNOT, swapping por composição de erros, memória
isotrópica e órbita circular por integração independente. Não calibram
coeficientes de equipamentos, atmosfera, apontamento ou estimador seguro TF-QKD.
O pipeline agenda três sessões institucionais já existentes, cobrindo as cinco
famílias em nominal, degradação, indisponibilidade e recuperação. Registra sete
falhas de fonte e replay de observações sintéticas identificadas. A entrega final
aceita ou recusada fica separada da integridade do processamento; zero pares
entregues não demonstra comunicação entre os extremos.
