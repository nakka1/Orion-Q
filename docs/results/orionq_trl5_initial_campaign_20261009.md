# OrionQ — início da qualificação SOFT-DT para TRL 5

Data: 09/10/2026. Escopo: software Windows e gêmeo digital com aquisição
simulada identificada, interfaces comuns e resultados condicionais. A versão
base é 5.60.1, com alterações no driver de diagnóstico; a distribuição existente
continua sendo uma evidência separada. **TRL 5 não foi demonstrado.**

O [protocolo](../product/orionq_trl5_qualification.md) e a
[configuração](../../configs/qualification/trl5_software_v1.json) definem sete
gates, referências admissíveis e limites prospectivos. SHA256 do protocolo:
`d0972d5cd3ae513c2868ac97960d5617fda5cebc7d1d3133b8cd993afebb0690`.
Os critérios não foram afrouxados depois das medições. Modelos continuam
opcionais e nenhuma atuação física foi executada.

## Implementação e evidências

| Componente | Entrega | Limite |
| --- | --- | --- |
| Protocolo | G01–G07, escopo, ambiente, sementes e critérios versionados | Não é certificação nem atribuição automática de TRL |
| Congelamento/avaliação | `tools/assess_trl5_software.py`; manifesto, fontes/dados/oráculos/testes, expectativas e tolerâncias congeladas; saídas novas sem sobrescrever evidências | Hashes verificam identidade dos bytes; não autenticam autoria |
| Referências | `tools/run_trl5_reference_campaign.py`; 35 comparações e sete referências primárias | Kernels reduzidos e hipóteses ideais; sem calibração experimental |
| Pipeline | `tools/run_trl5_pipeline_campaign.py`; quatro cenários, cinco famílias tecnológicas, sete falhas de fonte e replay sintético | Três sessões existentes; contrato de snapshot, sem qualificar toda a rede/API/UI |
| Instrumentação nativa | Sessão Windows autenticada, deadline de entrada e três avanços de ticks recebidos após entrada | Não usa senha, fixture de login nem bypass de permissões |

Os oráculos incluem conversão de potência em dB, propagação em fibra/vácuo,
purificação por matriz de densidade e CNOT, composição de erros Bell no swapping,
memória por gerador Markov e órbita circular por integração Newton/RK4.
Os seis valores agregados publicados de Gavignet são inventariados com origem;
não são tratados como amostras brutas, calibração ou conversão QBER→fidelidade.
Não foram admitidas todas as referências exigidas para segurança TF-QKD,
perdas atmosféricas, apontamento ou dispositivos específicos.

## Histórico preservado das tentativas

| Diretório | O que ocorreu | Interpretação |
| --- | --- | --- |
| `run1` | Congelamento preliminar; avaliação revisada antes de executar medições | Sem resultados de desempenho |
| `run2` | Driver antigo clicava em Sign in desabilitado; não criou plano de navegação | Tentativa interrompida somente no filho diagnóstico verificado; logs/launcher e nota preservados; não demonstra travamento da aplicação |
| `run3` | Sessão Windows abriu a topologia, mas o driver aguardava `steps_taken` ausente no `DeferredProvider`; ticks recebidos avançaram até 14 | Deadline encerrou explicitamente; fases de desempenho não realizadas. Runtime separado: 50/50 acima de 10 ms |
| `run4` | Entrada/ticks corrigidos; UI concluiu 122 ações e as campanhas analítica/pipeline passaram localmente | Consolidador recusou hashes de um teste analítico não incluído no manifesto. Lacuna de admissão preservada; inventário corrigido para a rodada seguinte |
| `run5` | Novo congelamento com inventário completo; referências/pipeline repetíveis; painel de CBPF desapareceu antes da verificação completa e UI excedeu latência; runtime excedeu o orçamento | Consolidado sem erro de identidade de fontes; G05 reprovado e demais gates com evidência incompleta |

Cada tentativa usa diretório próprio em `outputs/trl5_initiation_20261009`.
As correções foram no harness e na coleta de evidências. Não alteraram o
processamento científico, os providers, os modelos ou as regras de autenticação.
Os dados e modificações existentes do projeto foram preservados.

## Ambiente e interpretação das medições

Windows 11 build 26200, Ryzen 7 5700X/16 threads, RAM 31,93 GiB, Python 3.12.14,
Qt/PySide 6.11.2, NumPy 2.5.2, SciPy 1.18.1. Monitor U34G4C e área cliente
3440×1440, DPR 1. GPU RTX 3060/12288 MiB, driver 616.64, conforme coletor;
utilização da GPU é contexto compartilhado e não atribuída ao OrionQ.
A carga externa foi registrada, sem encerrar a instância pertencente ao usuário.

O benchmark usa `app.run` real, Qt Windows e eventos nos widgets. Não utiliza
injeção de entrada do sistema operacional nem hooks globais de profiling.
Mapa, Globo, abas e alternância têm mínimo de 15 s; soak de 3 min. A topologia
recebida contém 71 nodes/comunicadores, 131 enlaces, nove satélites e 36 sessões
tecnológicas; o backend apresenta quatro demandas locais e 76 globais.

Na primeira medição completa (`run4`), os 122 comandos planejados terminaram
sem erro registrado. Heartbeat p95: Mapa 78,35 ms; Globo 58,93 ms; abas 32,92 ms;
trocas 40,09 ms; soak 50,11 ms. Heartbeat é responsividade do event loop, não FPS.
Detalhes completos atingiram 2,72 s no Mapa, 1,64 s no Globo e 3,39 s no soak:
excedências do limite de 1 s. O runtime separado teve média 201,15 ms e p95
221,76 ms, com 50/50 excedências de 10 ms; a etapa de persistência teve média
193,58 ms. É um alvo de profiling; ainda não separa SQLite, flush de arquivos,
recibos e auditoria em custos individuais.

O controle automático da rede não foi habilitado. O snapshot lista cinco modelos
opcionais desabilitados e o detector boosting ativo no caminho existente;
isso não deve ser descrito como ausência de toda inferência ML. As campanhas
limitadas de protocolo não invocam modelos opcionais. A base sem arquivos
opcionais ainda precisa de uma reprodução separada e controlada.

## Consolidado final da iniciação

Evidência principal: [assessment.json](../../outputs/trl5_initiation_20261009/run5/assessment.json),
[manifesto](../../outputs/trl5_initiation_20261009/run5/freeze.json) e
[expectativas analíticas](../../outputs/trl5_initiation_20261009/run5/analytic-acceptance.json).
As fontes permaneceram iguais ao manifesto; os hashes dos relatórios constam
no consolidado. O protocolo manteve a mesma revisão e os mesmos limites.

| Ensaio no run5 | Resultado observado | Classificação |
| --- | --- | --- |
| Referências, duas execuções | 35/35 comparações em cada uma; fingerprints iguais | Verificação analítica limitada aprovada; G02 completo não demonstrado |
| Pipeline, duas execuções | 49/49 checks em cada uma; fingerprints iguais; um par simulado entregue no nominal de cada execução | Witness de cinco tecnologias aprovado; G03 completo não demonstrado |
| Ausência e entradas inválidas | Sete falhas controladas, sem protocolo processado indevidamente; indisponível com zero sucesso | Testes de fonte/timeout aprovados; matriz completa G04 pendente |
| Runtime, 50 amostras após cinco aquecimentos | Zero falha funcional; média 195,65 ms, p95 208,97 ms, máximo 217,90 ms; 50/50 acima de 10 ms | Orçamento reprovado; p99 não qualificado |
| Persistência no runtime | Média 186,89 ms, aproximadamente 95,5% do total | Gargalo localizado na etapa; causas internas precisam de profiling |
| UI nativa | 122 comandos planejados despachados; um erro `Incomplete or inconsistent details for CBPF (not_open)` | Ensaio com falha; não apresentar 122 ações como 122 resultados corretos |
| Detalhes completos | Máximos: Mapa 3282,68 ms, Globo 1970,64 ms, soak 2741,29 ms | Excedem o limite de 1000 ms |
| Heartbeat p95 | Mapa 87,57 ms, Globo 49,11 ms, abas 22,04 ms, trocas 32,44 ms, soak 43,13 ms | Medição diagnóstica; não FPS nem qualificação da cauda/uso prolongado |

O estado final é `assessment_status=fail`, `local_trial_passed=false`,
`trl5_demonstrated=false` e `trl5_claim_allowed=false`. G05 está `fail`; G01,
G02, G03, G04, G06 e G07 estão `not_demonstrated` pela cobertura ausente.
A falha de CBPF foi preservada para investigação de inspeção. O clique fez hit
em `('node', 'CBPF')`, a seleção correspondia a CBPF e a janela estava visível em
55,936 s. A contagem de janelas passou de uma pendente em 57,630 s a zero em
58,139 s, antes do comando seguinte do driver, em 60,663 s. O defeito registrado
é o desaparecimento posterior do painel, não um miss inicial do marcador.
CBPF voltou a passar no soak em 2355 ms. A última publicação antes do fechamento
foi em 57,601 s e a próxima em 62,361 s; o fechamento por atualização de contexto
não coincide com o intervalo observado. Os registros não contêm `close_reason`
nem eventos nativos que identifiquem o comando de fechamento. A causa final ainda
precisa de um diagnóstico curto CIMATEC→CBPF, registrando motivo e identidade da
janela em cada fechamento, sem reabrir automaticamente ou afrouxar os critérios.

Foram aprovados **97 casos únicos de regressão das ferramentas e instrumentação**:
34 de referência, 23 de pipeline, 17 de avaliação e 23 de entrada/ticks. São
resultados de suites separadas, não uma qualificação total do produto.
Artefatos: [referência + pipeline + avaliação inicial](../../outputs/orionq_trl5_combined_tests_20261009.xml)
e [avaliação final + entrada/ticks](../../outputs/orionq_trl5_final_integrity_tests_20261009.xml).
Ruff aprovou os oito arquivos Python envolvidos. Nenhum modelo foi tornado
obrigatório e nenhuma ação física foi executada.

## Pendências para a avaliação de TRL

| Gate | O que permanece |
| --- | --- |
| G01 — escopo | Revisão de relevância do ambiente e mapeamento completo das funções críticas |
| G02 — referências | Admissão e tolerâncias independentes para todas as famílias/domínios; hipóteses e incertezas documentadas |
| G03 — pipeline | Todas as fontes/interfaces/rotas e ligação completa API–UI–persistência; os witnesses limitados não cobrem tudo |
| G04 — recuperação | Matriz completa de crashes, concorrência, saturação, autorização, restauração e entrega incerta |
| G05 — desempenho | Resolver as excedências; depois 1000 amostras de runtime e UI completa: 3 repetições em cada resolução, fonte/compilado, 300 s/fase, 100 trocas e 20 min de soak |
| G06 — reprodução | Reconstrução em outro ambiente isolado; mesmas sementes no checkout atual não bastam |
| G07 — revisão | Especialista independente identificado e parecer autenticado ligado aos hashes |

Três minutos não demonstram ausência de vazamentos nem estabilidade ilimitada.
O total de 50 amostras de runtime não qualifica p99. Métricas simuladas e um par
simulado entregue não demonstram enlace físico, chaves certificadas ou conexão
institucional. A prioridade seguinte é perfilar a persistência e a montagem dos
detalhes, preservar suas garantias e repetir com nova identidade de fonte.

As definições de software em [NASA NPR 7123.1D, Apêndice E](https://nodis3.gsfc.nasa.gov/displayDir.cfm?Internal_ID=N_PR_7123_001D_&page_name=AppendixE)
admitem sistemas/simulações que representem o ambiente alvo, com desempenho
previsto demonstrado. O [ECSS-E-HB-11A](https://ecss.nl/wp-content/uploads/2017/03/ECSS-E-HB-11A(1March2017).pdf)
orienta o escopo e a avaliação de evidências. A revisão externa é um requisito
escolhido neste protocolo; os testes locais não a substituem.

Instruções completas: [execução da campanha](../product/orionq_trl5_qualification.md#ferramentas-da-primeira-campanha).
