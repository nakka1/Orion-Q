# ORION-Q — Roadmap de avanços com maior retorno

Data: 02/10/2026. Status: R1, R2, R4 e R5 implementados e validados localmente;
R3 com candidato local aprovado, com aceite em outro computador adiado pelo usuário.
Este documento organiza os cinco avanços priorizados na comparação com o
LabOne Q. O [guia de uso](orion_q_roadmap_usage.md) descreve os comandos e os
limites das funcionalidades entregues. As estimativas abaixo são as do plano
inicial, não o tempo de calendário observado na implementação.

O objetivo é converter as evidências existentes em recomendações utilizáveis,
facilitar a avaliação do programa por terceiros e preparar uma integração
experimental limitada. O primeiro resultado deve permitir selecionar um cenário
avaliado, comparar controladores e exportar a justificativa da recomendação.

Este plano complementa o [roadmap geral](roadmap.md) e o
[plano da pré-banca](roadmap_prebanca_20261022.md). As notas históricas desses
documentos descrevem seus respectivos incrementos; a base considerada aqui
inclui os resultados consolidados até 01/10 e o painel de polarização existente.

## Prioridades e esforço

As estimativas referem-se ao esforço ativo de uma pessoa familiarizada com o
repositório, para os escopos mínimos abaixo. Incluem implementação, verificações
pertinentes e documentação. Espera por outro computador, instalação de
dependências e investigação de incompatibilidades externas podem ampliar o
prazo de calendário. Reestimar cada entrega após confirmar seus pré-requisitos.

| ID | Prioridade | Entrega mínima | Esforço | Dependência para conclusão |
|---|---:|---|---:|---|
| R1 | 1 | Recomendação de controlador para perfis simulados já avaliados | 2–4 dias | Evidências e contratos existentes |
| R2 | 2 | Relatório comparativo com métricas, incerteza, origem e limites | 1–3 dias | Contrato de recomendação definido em R1 |
| R3 | 3 | Distribuição atual validada em um segundo computador Windows | 2–4 dias | R1, R2 e R4 integrados ao pacote candidato |
| R4 | 4 | Diagnóstico de latência e cumprimento de orçamento do laço | 1–3 dias | Pontos de medição definidos no laço escolhido |
| R5 | 5 | Ponte ORION-Q → LabOne Q → recibo, somente em emulação | 4–8 dias | R2; ambiente compatível e experimento escolhido |

A prioridade indica valor esperado. A ordem de execução é **R1 → R2 → R4 → R3 → R5**:
o diagnóstico precede a distribuição para que o pacote de aceitação já inclua
essa evidência. A soma sequencial é de **10–22 dias de esforço**; uma reserva
de planejamento de 25% resulta em aproximadamente **13–28 dias**. Essas faixas
não são compromissos de entrega nem evidências de desempenho.

## Base disponível e preparação

| Capacidade existente | Uso neste plano |
|---|---|
| Campanhas de polarização com atuador ideal e limitado | Fundamentar R1 e R2 |
| Registro verificável de afirmações e resumos das campanhas | Preservar números e proveniência em R2 |
| Ativação Q05 e painel POLARISATION LOOP | Oferecer acesso contextual à recomendação de R1 |
| Medição de latência mínima, máxima e média no teste de estresse | Base para ampliar o diagnóstico em R4 |
| Empacotamento, launcher e diagnóstico Windows | Concluir a aceitação independente em R3 |
| Emulação de sessões no LabOne Q | Verificar compilação e contrato de resultados em R5 |

Preparação incluída no esforço das entregas:

- [x] Identificar a revisão de código e os hashes dos artefatos usados no plano.
- [x] Confirmar quais mudanças recentes da interface entrarão no pacote candidato:
  a árvore de trabalho atual, incluindo melhorias existentes, sem descartá-las.
- [x] Selecionar a polarização calibrada como primeiro domínio de R1/R2;
  o catálogo também contempla os perfis de desenvolvimento já avaliados.
- [x] Conferir os campos disponíveis nos resumos antes de definir o leitor.
- [ ] Reservar acesso a um segundo computador para R3 — adiado pelo usuário.
- [x] Registrar responsável e evidências: implementação/verificação local pelo
  assistente; teste independente depende de computador e avaliador disponíveis.

## R1 — Recomendação de controlador conforme cenário e atuador

**Resultado:** ao selecionar um perfil já avaliado, o usuário recebe uma
recomendação explicada entre controlador aprendido e referência clássica.

A campanha com atuador limitado já mostra inversão de vantagem conforme o
dispositivo. O ponto calibrado não apresentou vantagem significativa do modelo
a 120 rad/s e apresentou vantagem a 250 rad/s. Esses resultados pertencem aos
perfis e ao protocolo estudados; não definem um limiar universal de hardware.
Consultar a [campanha e suas limitações](../results/orion_q_polarisation_actuator_20260929.md).

Etapas:

- [x] R1.1 — Criar um catálogo derivado dos perfis efetivamente avaliados,
  identificando planta, atuador, velocidade, período, latência nominal,
  checkpoint, controlador de referência, protocolo e hashes de evidência.
- [x] R1.2 — Implementar consulta por correspondência exata de perfil. Produzir
  recomendação de modelo ou referência clássica somente quando a comparação
  correspondente sustentar a escolha. Produzir resultado inconclusivo quando
  faltar vantagem demonstrada, e sem evidência para um perfil não estudado.
- [x] R1.3 — Expor a consulta no contexto do painel de polarização, com motivo,
  métrica primária e vínculo ao resultado original.
- [x] R1.4 — Exportar a seleção e a recomendação para consumo por R2.

Critérios de conclusão:

- [x] Um perfil lento avaliado apresenta a vantagem observada da referência
  clássica; um perfil rápido avaliado apresenta a vantagem observada do modelo.
- [x] Perfil intermediário não estudado, pesos diferentes ou contrato
  incompatível produzem indicação explícita de evidência insuficiente.
- [x] Ausência de significância não é apresentada como prova de equivalência.
- [x] A consulta preserva o controlador ativo; recomendação e ativação são
  estados distintos na interface e no recibo.
- [x] Cada conclusão referencia os hashes e a configuração que a sustentam.

Pontos existentes: `control_plane/polarisation_actuator_campaign.py`,
`control_plane/polarisation_activation.py` e `console/widgets/polarisation_control.py`,
sob `src/quantum_autopilot/`; configuração
`configs/optional_models/polarisation_actuator_v1.json`.

**Extensão posterior, fora dos 2–4 dias:** executar o laço ativado com atuador
limitado e vincular esse dispositivo ao contrato de ativação. O painel atual
usa atuador ideal; a recomendação de R1 não altera esse escopo de execução.

## R2 — Relatório comparativo e pacote de evidência

**Resultado:** exportação JSON e Markdown que explica o cenário, a comparação,
a recomendação e seus limites sem exigir consulta manual a vários documentos.
O primeiro relatório cobre o domínio de polarização selecionado em R1.

Etapas:

- [x] R2.1 — Definir um contrato comum para cenário, unidades, controlador,
  referência, métrica, tamanho de amostra, incerteza disponível, resultado,
  limitações e origem dos dados.
- [x] R2.2 — Ler os resumos originais e o registro de afirmações, reaproveitando
  seus resultados. Preservar limitações que não constem da tabela de escalares.
- [x] R2.3 — Gerar tabela e texto de interpretação a partir de uma única
  representação de dados, com hashes e versão do contrato.
- [x] R2.4 — Disponibilizar exportação da comparação selecionada na interface.

Critérios de conclusão:

- [x] Os valores exportados correspondem aos resumos de origem; campos ausentes
  permanecem identificados como ausentes.
- [x] Ganho, perda e resultado inconclusivo são representados sem selecionar
  apenas resultados favoráveis.
- [x] O relatório identifica simulação, protocolo, domínio e limites de validade.
- [x] O leitor detecta evidência incompatível ou alterada antes de emitir uma
  recomendação verificável.
- [x] Um avaliador consegue localizar a origem de cada conclusão no pacote.

Pontos existentes: `tools/build_platform_evidence.py`,
`docs/thesis/orion_q_platform_claim_registry.json`,
`src/quantum_twin/evaluation/thesis_claims.py` e `tests/test_platform_claims.py`.

**Extensão posterior:** adicionar QKD, alocação e roteamento como outros
domínios, preservando suas métricas e contratos próprios.

## R4 — Diagnóstico do desempenho do laço completo

**Resultado:** identificar onde o software consome tempo e quais orçamentos
de execução ele atende no computador avaliado.

Etapas:

- [x] R4.1 — Delimitar o laço medido e instrumentar ingestão, inferência,
  supervisão, persistência e execução com relógio monotônico.
- [x] R4.2 — Registrar latência total, p50/p95/p99, máximo, quantidade de
  amostras, aquecimento e quantidade/proporção de prazos excedidos.
- [x] R4.3 — Executar um protocolo repetível em repouso e sob carga declarada,
  registrando máquina, versões, configuração e revisão de código.
- [x] R4.4 — Exportar o diagnóstico como evidência operacional separada dos
  resultados científicos de R2.

Critérios de conclusão:

- [x] O relatório separa tempo virtual da planta e duração medida de execução.
- [x] O orçamento comparado é configurado e justificado para a tarefa escolhida.
- [x] O cálculo de percentis tem amostragem declarada; o tamanho mínimo é
  definido antes da execução, e amostragem insuficiente é sinalizada.
- [x] Sobrecarga da instrumentação é medida ou descrita como limitação.
- [x] Falhas e prazos excedidos permanecem no denominador pertinente.
- [x] Os resultados são interpretados como desempenho de software na máquina
  avaliada; temporização de dispositivo exige validação própria.

Pontos existentes: `src/quantum_autopilot/console/stress.py`,
`src/quantum_autopilot/control_plane/runtime.py` e
`src/quantum_autopilot/control_plane/polarisation_activation.py`.

## R3 — Distribuição utilizável e aceitação independente

**Resultado:** um pacote candidato que outra pessoa consegue abrir, usar e
avaliar seguindo o guia, sem depender do ambiente do desenvolvedor.

Etapas:

- [x] R3.1 — Congelar um candidato com R1/R2/R4 concluídos; registrar versões,
  hashes e referências dos modelos externos necessários à demonstração.
- [x] R3.2 — Executar os diagnósticos existentes no pacote compilado e revisar
  o roteiro manual para incluir recomendação, comparação e desempenho.
- [ ] R3.3 — Extrair e executar em um segundo Windows com conta comum, registrando
  configuração da máquina, escala de tela e resultados dos diagnósticos.
- [ ] R3.4 — Pedir a um avaliador para selecionar o cenário, comparar
  controladores, executar o laço existente e exportar os recibos pelo guia.
- [ ] R3.5 — Corrigir falhas bloqueantes, gerar novo candidato quando necessário
  e repetir apenas as verificações afetadas, preservando o histórico.

Critérios de conclusão:

- [ ] O aplicativo abre sem pesos; capacidades opcionais indisponíveis são claras.
- [ ] Com os pacotes fornecidos localmente, o avaliador completa o roteiro.
- [ ] Diagnósticos automáticos e inspeções manuais necessários passam no
  segundo computador, vinculados ao hash do pacote final.
- [ ] Os relatórios exportados mantêm os mesmos contratos e números de origem.
- [ ] Estado local, erros e dependências necessárias estão descritos no guia.

Pontos existentes: `tools/build_windows_console.ps1`,
`tools/validate_frozen_optional_models.py`,
`src/quantum_autopilot/windows_acceptance.py` e o
[guia de aceitação Windows](orion_q_windows_acceptance.md).

**Dependência externa:** sem acesso ao segundo computador, é possível concluir
o candidato e o diagnóstico local, mas o marco permanece sem aceite independente.

## R5 — Integração mínima com LabOne Q em emulação

**Resultado:** uma solicitação do ORION-Q gera um experimento LabOne Q,
passa pelo compilador e retorna um recibo de execução emulada verificável.

O experimento inicial será uma sequência curta de pulso e aquisição com
varredura limitada de amplitude, usando um setup emulado suportado. Essa
integração demonstra o transporte de configuração e resultados. O vínculo
físico entre correções SO(3), repetidores e dispositivos requer outro projeto.

Etapas:

- [x] R5.1 — Escolher o setup emulado e fixar versões compatíveis de Python,
  LabOne Q e requisitos associados em ambiente separado. Registrar um exemplo
  oficial de referência antes de implementar a ponte.
- [x] R5.2 — Definir contrato de solicitação com ID, tipo de experimento,
  parâmetros e unidades, perfil do setup e limites de execução.
- [x] R5.3 — Implementar adaptador opcional para construir, compilar e executar
  exclusivamente com sessão emulada; começar por CLI/API local.
- [x] R5.4 — Registrar modo, versões, configuração, artefatos de compilação,
  duração, resultado ou falha e proveniência das aquisições emuladas.
- [x] R5.5 — Exportar o recibo pelo mecanismo de R2, em uma categoria de evidência
  de integração distinta das campanhas científicas.

Critérios de conclusão:

- [x] Uma solicitação válida compila e retorna dados com eixos e formato esperados.
- [x] Tipo de experimento, parâmetros ou modos não suportados são recusados
  antes da execução; a ponte não permite trocar para conexão física.
- [x] Falha de compilação e orçamento excedido geram estados explícitos.
- [x] Os resultados identificam aquisição artificial e `physical_action_performed=false`.
- [x] Dados emulados não entram no cálculo de QBER, fidelidade ou vantagem
  de controlador das campanhas existentes.
- [x] Sem LabOne Q instalado, o produto base permanece utilizável e a integração
  aparece como indisponível.

Local implementado: módulo opcional sob
`src/quantum_autopilot/integrations/laboneq/`, com um comando de demonstração
limitado. O contrato de recibo pode reutilizar a estrutura de exportação de R2.

A [documentação oficial de sessões](https://docs.zhinst.com/labone_q_user_manual/core/functionality_and_concepts/01_session/concepts/index.html)
descreve compilação e execução em emulação, com dados artificiais para depuração.
Os [requisitos de instalação](https://docs.zhinst.com/labone_q_user_manual/getting_started/installation.html)
devem ser conferidos ao fixar as versões. Conclusão em emulação comprova a
integração de software; a próxima etapa física exige bancada e validação própria.

## Sequenciamento e ponto de corte

| Fase | Entregas | Esforço | Marco de saída |
|---|---|---:|---|
| A — Decisão e evidência | R1 + R2 | 3–7 dias | Cenário selecionado → recomendação explicada → relatório exportado |
| B — Avaliação por terceiros | R4 + R3 | 3–7 dias | Diagnóstico medido e pacote aceito em outra máquina |
| C — Integração experimental | R5 | 4–8 dias | Experimento compilado/executado em emulação com recibo |

Se for mantido o congelamento previsto para 18/10 no plano da pré-banca,
priorizar A e B até esse ponto, iniciando A na semana de 05/10. O limite
superior de esforço pode ultrapassar a janela disponível; reestimar ao concluir
A. Depois do congelamento, reservar o trabalho para correções, reprodução e
ensaio da demonstração. R5 fica preferencialmente após a pré-banca de 22/10.

Com apenas uma semana de capacidade, concluir o menor percurso de A: perfis
de polarização calibrada, consulta exata e relatório. Se houver atraso, reduzir
a quantidade de perfis apresentados e adiar a expansão de domínios. Preservar
os critérios de evidência e validade ao reduzir escopo.

## Verificação e acompanhamento

Cada marco deve registrar revisão, artefatos, critérios atendidos e limitações
restantes. As verificações futuras devem cobrir comportamento relevante:
seleção de referência/modelo/inconclusivo, perfil não estudado, evidência alterada,
fidelidade dos números exportados, duração medida e falhas de integração.
Uma alteração apenas de documentação não exige reexecutar campanhas científicas.

| ID | Estado atual | Evidência de conclusão ou pendência |
|---|---|---|
| R1 | Concluído no escopo simulado | 36 perfis; consulta exata, perdas/inconclusivos e alteração de evidência testados |
| R2 | Concluído para polarização | JSON/Markdown/manifesto; hashes dos resumos e registro de afirmações |
| R3 | Candidato local aprovado; aceite independente pendente | ZIP verificado e diagnósticos nativos aprovados; segundo Windows e avaliação humana adiados |
| R4 | Concluído no laço escolhido | 1.000 ciclos por condição; 0 falhas; p99 417,789/771,087 ms; alvo de 10 ms excedido em todos |
| R5 | Concluído em emulação | LabOne Q 26.7.0 compilou/executou a varredura; recibos de falha/timeout e ausência testados |

R4 mede um `ControllerRuntime` isolado com política de repetidor e execução
simulada de SWAP. Não mede a inferência dos modelos de polarização. O orçamento
de 10 ms é exploratório e não foi atendido. O resultado identifica um gargalo,
sem garantir temporização de hardware. As campanhas científicas originais
permanecem como origem dos números de R1/R2.

R5 foi antecipado enquanto o aceite externo R3 ficou adiado. A biblioteca e
suas dependências foram instaladas em ambiente separado; a integração não
exige LabOne Q no aplicativo base. Aquisições artificiais ficam fora das
campanhas científicas.

Candidato local R3: `release/windows/5.38.0-roadmap-20261002-080228/`.
ZIP SHA256 `a2cd0877d2dc62c73f37d990ca15733791264e50f620ef03a046d5eec55a63bd`.
Os diagnósticos preservam `independent_machine_verified=false` e exigem
revisão manual. Consulte o [relatório do incremento](../results/orion_q_roadmap_20261002.md).

Reavaliar esforço e prioridade ao final de A e B. Controle FPGA completo,
replay geral de sessões, retreinamento com atuador limitado e novos modelos
permanecem em seus planos próprios, fora destas cinco entregas mínimas.
