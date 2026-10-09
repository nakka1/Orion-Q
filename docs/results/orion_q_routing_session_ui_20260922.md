# ORION-Q — sessão gráfica offline de roteamento

Data: 22/09/2026. Quarta função com sessão nativa, após sincronizador, ator QKD
e compensação. Disponível em código-fonte; o executável Windows anterior não
foi atualizado. Modelos continuam opcionais, OFF por padrão e sem ativação Q05.

Incremento posterior (23/09): [sessões de alocação híbrida](orion_q_allocation_session_ui_20260923.md).
Este documento preserva a contagem e o escopo originais da etapa de roteamento.

## Como usar

EDGE CONTROL → OPTIONAL MODEL CATALOG → selecionar `qnet.routing.nominal` ou
`qnet.routing.prazo_longo` → VERIFY LOCAL ZIP → OFFLINE SESSION.
LOAD JSON importa uma entrada; DEMO apenas preenche o editor com features
artificiais escritas manualmente. RUN executa uma decisão, explicitamente.
EXPORT salva um novo registro JSON e recusa sobrescrita.

Cada execução confere novamente a identidade do ZIP, abre o checkpoint escolhido
com estado novo e cria um gerador PCG64 com a semente declarada. Nominal usa
amostragem com temperatura 1; prazo longo usa seleção gulosa com temperatura 0,
sem sorteio. A semente é registrada em ambos, junto ao indicador `rng_used` e à
versão NumPy. Reiniciar a sessão não reproduz automaticamente o estado RNG de
um episódio contínuo do autor.

O resultado preserva a ordem dos candidatos, índices, IDs, descrições, features,
logits, probabilidades, candidato guloso, recomendação efetiva e retorno do
crítico. Empates seguem o comportamento original: primeiro máximo na ordem
de entrada. Nenhuma ação é despachada.

## Contrato de entrada

`orion.optional-routing-input.v1`, limitado a 64 KiB de JSON UTF-8:

- `model_id` deve ser o checkpoint selecionado; `evidence` deve ser
  `simulated_offline`. Nenhuma troca automática ou aceitação de dados medidos.
- `feature_contract`: `qnet.routing.state11.action23.v1`.
- `stream_id`, `decision_timestamp_s`, `features_timestamp_s` e semente inteira
  de 0 a 4294967295. Features não podem ter instante posterior à decisão.
- `state_features`: objeto com as 11 features nomeadas do export.
- `candidates`: 1 a 64 candidatos ordenados, cada um com `action_id`, `action`
  e `features`, contendo as 23 features nomeadas do export.
- `reference_action_id`: ID de candidato usado como comparação declarada, ou
  `null`. Não participa da inferência.

Os nomes são mapeados para a ordem do checkpoint, independentemente da ordem
das chaves JSON. São números reais finitos, sem booleanos. Campos extras,
ausentes, duplicados, flags one-hot inconsistentes e checkpoint divergente
são recusados antes de construir o modelo ou abrir seu ZIP.

A descrição da ação exige `kind`, `link`, `pair_ids` e `node`: WAIT não recebe
recursos; GENERATE recebe dois endpoints distintos; PURIFY/SWAP recebem dois
IDs distintos de pares; DELIVER/DISCARD recebem um. Somente SWAP recebe nó.
IDs de candidatos e descrições exatamente duplicadas são recusados para não
alterar artificialmente a massa de probabilidade pela repetição da mesma entrada.

Essa validação é **estrutural**, não valida disponibilidade de pares, memória,
enlaces, compatibilidade física ou legalidade no estado da rede. Normalização,
origem causal e correspondência ao simulador ainda dependem de quem produz as
features. Nomes e timestamps corretos não certificam domínio ou ausência de
vazamento no processo externo de extração. Não há conversão de WDM para essas
features nem associação automática à topologia aberta no dashboard.

## Comparação sem resultados inventados

A referência é uma decisão declarada externamente. Pode representar a escolha
de uma heurística avaliada fora desta sessão, mas o programa não afirma ter
executado essa heurística. Só informa concordância de decisões. Sem referência,
a comparação fica indisponível, não falsa ou zero.

No DEMO, WAIT (`candidate-0`) é a referência ilustrativa. Com os pesos fornecidos
e semente 1234, o nominal recomendou GENERATE em `candidate-3` e o prazo longo
GENERATE em `candidate-1`. Ambos diferiram de WAIT. Não foram calculados ganho,
sucesso, entrega de pares, latência realizada ou recompensa contrafactual.
O DEMO não reproduz os regimes ou uma campanha dos artigos.

Probabilidades são as da política, não probabilidades de sucesso. O crítico
não mede recompensa nem fidelidade de Bell. As features de fidelidade são
**entradas do simulador**; esta sessão não demonstra uma nova previsão de
fidelidade a partir de WDM. Comparação em laço fechado com heurísticas originais
e sementes pareadas permanece pendente.

## Isolamento e testes

Editar a entrada, mudar modelo/fonte/campanha, esquecer o pacote ou fechar a
sessão invalida resultados. Trabalho antigo pode terminar, mas é descartado.
Trocar checkpoint recria a janela correta, sem reutilizar entrada ou estado.
O registro exporta texto/hash da entrada, hash do pacote, identidade do modelo,
horário UTC e resultados; não autentica a origem científica dos dados.

- **51 testes novos**: contratos, mapeamento de features, variantes, semente,
  referência isolada, recusa de tipos e candidatos, JSON limitado, ZIP alterado,
  exportação exclusiva, contexto gráfico e dimensões da janela.
- **273 testes** passaram no conjunto de sessões, catálogo, fontes e telas.
- **276 testes** passaram na regressão dos modelos, admissão, comandos e
  barreiras de evidência/controle. Total: **549**, não a suíte completa do repo.
- Ruff passou nos cinco arquivos Python adicionados/alterados nesta etapa.
- Capturas de 1060 × 760 e 640 × 480 inspecionadas: controles visíveis, editores
  e resultados roláveis. Execução dos dois checkpoints pelo worker Qt confirmada.

Além dos testes sintéticos independentes dos arquivos externos, os 12 exemplos
de cada export foram convertidos para o contrato nomeado e executados pelo novo
backend. A ordem das 11/23 features foi conferida contra os metadados de ambos.

| Checkpoint | Exemplos | Maior diferença absoluta em logits/probabilidades/crítico |
|---|---:|---:|
| nominal | 12 | 4,44 × 10⁻¹⁶ |
| prazo_longo | 12 | 1,78 × 10⁻¹⁵ |

Todos ficaram abaixo de 10⁻¹², e os índices gulosos coincidiram. Esta conferência
é de paridade com exemplos do autor, não evidência experimental independente.
Nenhum script do ZIP foi executado, nenhum peso foi alterado e nenhum novo
treinamento foi realizado.

Artefatos locais: `outputs/routing_session_ui_20260922/`, incluindo
`ui-tests.xml`, `regression-tests.xml`, `{nominal,prazo_longo}-author-example.json`,
`{nominal,prazo_longo}-demo.json` e capturas `{nominal,prazo_longo}-{640,1060}.png`.
Pacote conferido: `F:/qnet_network.zip`, identidade fixada pela admissão existente.

## Próximo incremento

Sessão offline de alocação híbrida (persistência e Ridge), com planta e fila de
atraso próprias. Continuam pendentes a ponte de features, avaliação fechada,
ativação auditável Q05 e distribuição Windows opcional com/sem pesos.
