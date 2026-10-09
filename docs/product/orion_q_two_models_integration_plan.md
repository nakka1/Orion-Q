# Integração dos dois modelos fornecidos pelo autor

Atualização: 23/09/2026. Recebidos os dois artigos, `modelo_qkd_ml.zip` e a versão ampliada `qnet_network.zip`. Não são apenas duas redes: há seis componentes de inferência/controle, com checkpoints e regimes distintos. A admissão offline cobre sincronizador e agente de taxa QKD, dois roteadores, quatro compensadores e duas opções de alocação, incluindo persistência sem ML. A interface agora oferece sessões offline explícitas do sincronizador, do ator QKD, dos quatro compensadores, dos dois roteadores e das duas opções de alocação; nenhum foi ativado como política do runtime de controle. Ver [execução e limites](orion_q_author_model_intake.md) e [última sessão gráfica](../results/orion_q_allocation_session_ui_20260923.md).

**Requisito do produto final:** os modelos serão opcionais, com estado padrão OFF, escolha explícita por função, baselines preservados e aplicação utilizável sem os pesos. O [contrato de opcionalidade](orion_q_optional_models.md) distingue o catálogo/fábrica offline, consulta nativa, verificação local e sessões gráficas offline do sincronizador/ator QKD/compensadores/roteadores/alocadores já implementados do detector bloqueado e ativação Q05 ainda pendentes. O [build Windows de 23/09](../results/orion_q_optional_models_windows_20260923.md) inclui as sessões e foi testado localmente com/sem os pacotes externos; inspeção em máquina independente continua pendente.

## Efeito no roadmap

Manter Q04 como pré-requisito para alterações de política auditáveis. Em Q05/Q16, integrar os modelos existentes fornecidos pelo autor antes de considerar novo treinamento ou arquitetura. Preservar os modelos/baselines atuais como referências; nenhuma substituição silenciosa. Q17 passa a reutilizar os protocolos e resultados dos artigos somente após revisar a correspondência entre evidências, artefatos e dados.

| Frente | Integração pretendida | Fronteira obrigatória |
|---|---|---|
| Pacote QKD | Detector de anomalias/ataques, agente de taxa de chave, sincronizador | Detector: o pickle do pacote continua sem ser desserializado; desde 29/09 roda por exportação segura provada idêntica. Agente usa observáveis decoy-state; sincronizador prevê offset a +2 s. Nenhum prevê fidelidade de Bell. |
| Pacote Quantum Network | Compensação de polarização, roteamento/temporização, alocação híbrida | Compensador prevê rotação; roteador pontua ações; alocador recomenda admissão. As três saídas não são intercambiáveis. |

## Posição após recebimento

- **Q16, primeiro incremento:** arquivos fixados por SHA-256, inventário dos membros, leitura sem extração/importação dos scripts dos anexos, adaptadores NumPy offline, contratos de entrada e replay dos exemplos. Falhas de identidade, esquema, finitude, cadência ou domínio recusam a entrada.
- **Q05 continua pendente:** não há política nova ativa. Construção de features físicas, validação contextual de ações legais, preparação/revisão/ativação/readback e rollback ainda precisam ser ligados ao runtime.
- **Q17:** os artigos foram usados para revisar hipóteses e limites, não para promover simulação a medição. Testes de paridade não reproduzem as campanhas de treinamento/avaliação dos artigos.
- **Compensador admitido offline:** Ridge/MLP por regime, SO(3), normalização, warm-up, cadência de 2 ms e horizonte de 40 ms conferidos contra exports. Ainda não há atuação ou ensaio em laço fechado. Preservar persistência e alfa-beta ressintonizado nos ensaios de controle, sem misturar os regimes de compensação.
- **Agente QKD admitido offline:** features/probabilidades/caixa de ações conferidas em 40 períodos, com RNG explícito e recomendação sem comando. Não se afirma reprodução dos sorteios originais ou do ganho de taxa.
- **Alocador admitido offline:** persistência/Ridge com fila de atraso e planta próprias; 1.440 passos e 20 snapshots sem divergências. Contabilidade assintótica de previsão, não prova de chave finita.
- **Catálogo nativo implementado e empacotado:** EDGE CONTROL lista as 11 opções OFF/offline/bloqueadas. VERIFY LOCAL ZIP confere identidade e integridade em segundo plano, sem instalação, construção de modelo ou ativação Q05.
- **Sessão offline gráfica do sincronizador:** OFFLINE SESSION habilita execução explícita em uma janela simulada de oito amostras, referência futura opcional fora do preditor, persistência e exportação JSON sem sobrescrita. Contrato preservado nas sessões dos demais componentes; incluída no build Windows de 23/09.
- **Sessão offline gráfica do ator QKD:** entrada causal decoy-state própria, semente PCG64 explícita, ação amostrada versus gulosa, nove prévias limitadas e baseline de manter parâmetros. Sem prever taxa, inferir ganho operacional ou executar comandos. Mantida a separação de dados/contexto ao trocar de sessão.
- **Sessão offline gráfica dos compensadores:** quatro checkpoints independentes, entrada SO(3) estrita, warm-up explicitamente separado da inferência aprendida, referência futura isolada e erro angular final versus persistência. Não equivale a ensaio de controle ou certificação de domínio.
- **Detector:** solicitar/exportar um formato de árvores/arrays seguro e provar paridade antes de habilitar. Não carregar pickle para obter essa exportação no processo do produto. O modelo FPGA podado não é substituto equivalente ao classificador completo.
- **Sessões offline dos roteadores:** seleção estocástica nominal e gulosa de prazo longo, features nomeadas, candidatos explícitos e semente registrada. Referência compara decisões, sem avaliar heurística, resultado contrafactual ou legalidade física. 24 exemplos conferidos pelo novo contrato; não é nova validação científica.
- **Sessões offline dos alocadores:** persistência e Ridge em snapshots da planta fixa, com mesma fila pendente e histórico, warm-up explícito e comparação de admissão por enlace. 20 snapshots conferidos sem divergência; sem avançar filas, executar ações ou alegar chaves entregues. Empacotamento Windows entregue com testes locais; próxima integração: ponte tipada para shadow do sandbox, sem ativar controle.

## Ponte shadow de alocação — 24/09

A [terceira ponte tipada](orion_q_allocation_shadow_bridge.md) agora integra
persistência/Ridge ao sandbox por API somente leitura e CLI explícita. Valida a
planta de 11 enlaces, origem/relógio/fluxo e histórico passado; mantém a mesma fila
comprometida para os dois comparadores, sem alterações. Quatro verificações com o
pacote original e 407 testes selecionados passaram. O incremento gráfico seguinte
já acrescenta produtor artificial e sessão contínua opt-in, com histórico causal,
invalidação e exportação; 12 avaliações nativas com o pacote original passaram.
A fila continua exógena, sem realimentação ou contagem de chaves entregues.
A ponte de roteamento também foi concluída em fonte, conforme a seção seguinte.
Nada ativa Q05, reconfigura tráfego ou transforma previsões em chaves disponíveis.

## Ponte shadow de roteamento — 24/09

A [quarta ponte](orion_q_routing_shadow_bridge.md) admite snapshots simulados com
features nomeadas, candidatos ordenados e inventário explícito de nós, enlaces,
memória e pares. Verifica disponibilidade instantânea e identidade dos recursos
antes dos pesos; qualquer candidato inválido recusa a janela inteira. Preserva
semente e seleção offline dos dois checkpoints. A API e a CLI somente observam:
não consomem recursos nem acionam política, executor ou journal. Quatro comparações
com o pacote original passaram; ver [validação](../results/orion_q_routing_shadow_20260924.md).
Não certifica features, legalidade completa do simulador ou ganho de roteamento.
A [sessão gráfica shadow de roteamento](../results/orion_q_routing_shadow_ui_20260924.md)
também foi concluída em fonte: produtor artificial próprio, START explícito,
frescor independente, invalidação por contexto/versão e exportação do frame
capturado. Doze avaliações nativas com os pesos originais coincidiram com a sessão
offline. As features continuam ilustrativas; não reproduzem a normalização do
simulador do artigo. A ponte do ator de taxa QKD também está concluída, conforme
abaixo. Q05 e atuação física continuam pendentes.

## Ponte shadow do ator de taxa QKD — 24/09

A [quinta ponte](orion_q_keyrate_shadow_bridge.md) agora recebe observáveis
decoy-state próprios, unidades explícitas e identidade de enlace, parâmetros e
calibração. Valida causalidade antes de abrir os pesos; início por calibração é
explícito e limitado à origem do relógio. Preserva as nove prévias, a seleção
PCG64 e o baseline de manter parâmetros, sem alterar configurações. Oito casos
com o pacote original e 564 testes selecionados passaram; ver
[relatório](../results/orion_q_keyrate_shadow_20260924.md). Não há previsão da taxa
seguinte, geração de chaves ou atuação.

**Conclusão gráfica em 26/09:** o [produtor artificial e a sessão shadow QKD](../results/orion_q_keyrate_shadow_ui_20260926.md)
agora estão em fonte, com START explícito, parâmetros fixos, períodos causais,
bootstrap após descontinuidades e invalidação por configuração/calibração. Seis
avaliações nativas com os pesos originais mantiveram paridade offline sem comandos.
As dez configurações admitidas têm interface offline e shadow artificial. Q16
permanece parcial; detector, domínio medido, distribuição dos pesos e Q05 seguem
pendentes. Q04 já tem reconciliação por evidência e interface nativa, sem reexecução.
Em 27/09, Q05 ganhou [preparação/revisão local](orion_q_policy_preparation_q05.md)
de limiar da política existente, com referência explícita e invalidação; isso não
admite modelos para ativação. A interface EDGE CONTROL → POLICY REVIEW também
está implementada em fonte, com carregamento explícito, preparação, revisão e
exportação capturada, sem mudar a referência. Em 28/09, o contrato local de
[aprovação explícita](orion_q_policy_approval_q05.md) passou a registrar declarações
vinculadas à revisão após nova validação, sem autenticação ou autoridade de ativação.
Sua confirmação gráfica e exportação capturada também estão implementadas em
POLICY REVIEW, com rótulo declarado, consentimento explícito e invalidação por
fonte/seleção/prazo. A [ativação local em simulação](orion_q_policy_activation_q05.md)
agora persiste aprovação/configurações e fornece readback, exclusivamente para o
limiar da política existente, sem modelos opcionais. [Rollback aprovado e recuperação
explícita](orion_q_policy_recovery_q05.md) agora têm API local. Ativação/readback já
têm interface nativa com confirmação separada e observação datada. Rollback e
recuperação também estão integrados ao painel, sem habilitar seleção de modelos.
[Validação do fluxo](../results/orion_q_policy_recovery_ui_20260928.md).
A [explicação estruturada de decisões](orion_q_policy_decisions_q05.md) consulta
somente ciclos simulados e vínculos históricos, sem habilitar os modelos recebidos. [Validação nativa](../results/orion_q_policy_activation_ui_20260928.md). [Validação](../results/orion_q_policy_approval_ui_20260928.md).
[Validação nativa](../results/orion_q_policy_review_ui_20260927.md).

Em 29/09, os [critérios declarados de compatibilidade Q05](orion_q_policy_compatibility_q05.md)
passaram a aparecer no catálogo, API local e exportação CLI. As onze opções mantêm
bloqueios explícitos de domínio/ações/adaptador; identidade de pacote conferida não
promove seleção. O relatório não consulta contexto corrente nem carrega pesos.
Próximo: vincular observação a identidade, dados e topologia concretos em simulação,
antes de permitir preparação de troca. [Validação](../results/orion_q_policy_compatibility_20260929.md).

A [observação contextual local](orion_q_policy_context_q05.md) agora compara entrada
simulada explícita com a configuração observada e a topologia persistida, sem carregar
pesos ou autorizar ações. Verificação do alvo existente não valida o domínio de um
componente opcional; todos mantêm bloqueadores. [Validação](../results/orion_q_policy_context_20260929.md).
A interface CONTEXT INSPECTION já consulta em sessão privada, invalida mudanças e
exporta a observação capturada sem autorização de seleção. [Validação nativa](../results/orion_q_policy_context_ui_20260929.md).
Próximo: tradução tipada/limitada de ações dos roteadores, vinculada a recursos e
precondições simulados, antes de permitir qualquer seleção de componente.

## Materiais necessários por modelo

**Segunda ponte shadow em fonte (23/09):** os quatro compensadores agora recebem
janelas SO(3) tipadas, com covariáveis, cadência, unidades, referencial e regime
explícitos. Não há conversão WDM→rotação nem atuação. Warm-up é identificado como
persistência, não inferência aprendida. [Contrato e limites](orion_q_compensation_shadow_bridge.md).
Produtor artificial e sessão gráfica shadow agora ligados pelo mesmo Snapshot do
dashboard, com quatro checkpoints opcionais, START explícito e exportação por lote.
São 16 amostras a 2 ms em relógios virtuais próprios, não controle físico a 500 Hz.
Avaliação angular contra referências posteriores agora implementada, com previsão
registrada antes da referência e pareamento exato em lotes novos. Diagnóstico de
uma semente: Ridge melhor e MLP pior que persistência neste sinal artificial;
isso não certifica a planta nem o domínio dos modelos.
**Consolidação em 24/09:** a [campanha SO(3) v1](../results/orion_q_compensation_campaign_20260923.md)
terminou com 240 execuções e 6/24 grupos atendendo ao filtro descritivo: Ridge em
ambos os regimes, nos cenários suave/reversão/degrau. Ruído falhou; perdas/atrasos
tiveram 73,333% de cobertura. MLP perdeu para ambas as bases em todas as sementes.
Regressão histórica selecionada: 500 aprovados. Nenhum resultado ativa Q05,
seleciona modelo ou amplia a evidência física. As pontes subsequentes de alocação
e roteamento mantêm observáveis próprios e já possuem sessões gráficas shadow,
sem controle. O ator de taxa QKD também já tem ponte e sessão gráfica shadow artificial, concluída em 26/09. [Protocolo temporal](orion_q_compensation_temporal.md).

**Primeira ponte shadow em fonte (23/09):** o sincronizador agora aceita uma janela
tipada anexada ao snapshot simulado, valida origem/nó/campanha/relógio e causalidade,
e exporta previsão versus persistência sem ciclo ou journal de comandos. Sem essas
entradas permanece indisponível. [Contrato e uso](orion_q_sync_shadow_bridge.md).
O incremento seguinte ligou a emissão contínua do provider artificial à sessão
gráfica shadow, com START explícito, invalidação e exportação de frame. Não há
política ativa; as outras pontes continuam pendentes e o EXE anterior não foi alterado.

1. Artigo completo e versão; licença e condições de distribuição do código, pesos e dados.
2. Código de inferência, dependências/versionamento e formato dos pesos. Não executar código nem desserializar pickle de artefato novo sem inspeção; preferir formato seguro compatível com a arquitetura existente.
3. Esquema de entradas: nomes/ordem, unidades, timestamps/janelas, canais/dispositivos, tratamento de ausências, limites e pré-processador ajustado no treino.
4. Esquema de saídas: alvo/unidade/horizonte e, se houver, intervalo/calibração. Não inventar incerteza para modelo que não a forneça.
5. Exemplos de entrada com saída de referência, tolerância numérica e identificação da versão que produziu os números do artigo.
6. Manifestos das divisões de treino/calibração/teste, hashes dos dados e condições dos resultados. Identificar claramente medido, simulado, derivado e previsto, além de limitações e vazamento temporal potencial.

## Ordem de implementação após recebimento

1. Inspecionar artigos e artefatos sem executar anexos automaticamente. Mapear compatibilidade e lacunas por modelo.
2. Congelar identidade, hashes, contrato, pré-processamento e ambiente; acrescentar testes de paridade com exemplos do autor.
3. Integrar inferência somente leitura/shadow. Exibir modelo efetivamente carregado, versão, domínio, indisponibilidade e entradas incompatíveis. Falha de um modelo não preenche a outra trilha.
4. Registrar previsões e avaliações separadas dos comandos e dos resultados medidos. Comparar com baselines sob o protocolo apropriado, sem retreinar ou selecionar no teste.
5. Somente depois ligar uma política compatível ao runtime via Q05: preparar, revisar, ativar, confirmar por leitura e permitir retorno explícito à versão anterior. Sem autoridade física decorrente da presença do modelo.
6. Verificar impacto operacional com orçamento comparável; MAE menor ou um artigo pronto não demonstram automaticamente ganho de controle. Resultados anteriores continuam válidos para as versões/experimentos anteriores e não são generalizados aos novos modelos.

Não é necessário concluir essa admissão para continuar Q04. A licença de redistribuição dos novos pesos/códigos não foi identificada nos ZIPs: esta entrega consome arquivos externos do autor, sem incluí-los no instalador ou declará-los cobertos pela licença do ORION-Q. Manifestos de treino/calibração/teste, campanhas completas e a ponte de features ainda são necessários para uma reprodução científica ampla. Segundo simulador e adaptadores físicos continuam frentes distintas. A integração IBM Quantum foi descontinuada em 07/10/2026 e não é uma pendência deste plano; sua implementação anterior permanece no [arquivo histórico](../../archives/retired_ibm_quantum_20261007/ibm_quantum_source.zip). Qiskit/Aer continua para simulação local.
