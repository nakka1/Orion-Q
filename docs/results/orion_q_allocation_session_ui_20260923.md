# ORION-Q — sessão gráfica offline de alocação híbrida

Conclusão: 23/09/2026; execução iniciada em 22/09. Quinta função com sessão
nativa, após sincronizador, ator QKD, compensação e roteamento. As dez opções
com adaptador offline agora têm sessão gráfica; isso não conclui Q16 nem ativa
Q05. O detector permanece bloqueado por pickle.

Entrega em código-fonte. O `.exe` anterior não foi atualizado; todos os modelos
continuam opcionais e OFF por padrão, sem controle físico ou mudanças nos pesos.

Incremento posterior: [distribuição Windows com as dez sessões](orion_q_optional_models_windows_20260923.md).
Este documento preserva o escopo e a contagem da etapa de alocação em fonte.

## Uso

EDGE CONTROL → OPTIONAL MODEL CATALOG → selecionar `qnet.allocator.persistence`
ou `qnet.allocator.ridge` → VERIFY LOCAL ZIP → OFFLINE SESSION. LOAD JSON importa
um snapshot; DEMO apenas preenche o editor com dados artificiais. RUN avalia
explicitamente. EXPORT cria um novo JSON, sem sobrescrever arquivos existentes.

Persistência é uma opção **sem ML**. Ridge utiliza cinco estimativas anteriores
de drift mais a observação atual. Com menos histórico, a saída é identificada
como `warmup_persistence`; não se afirma inferência aprendida durante aquecimento.
Cada RUN começa com estado novo, sem acumular histórico entre cliques.

## Contrato de entrada

O schema `orion.optional-allocation-input.v1` aceita no máximo 64 KiB de JSON
UTF-8. Campos extras, duplicados, valores não finitos e booleanos como números
são recusados antes de abrir o ZIP ou construir os modelos.

- Checkpoint explicitamente igual ao selecionado, `stream_id` e evidência
  `simulated_offline`; dados declarados medidos não são admitidos nesta sessão.
- Protocolo `single_photon_bb84`, não decoy-state BB84.
- Identidade e ordem exatas dos 11 enlaces da planta revisada. Os nomes de
  cidades não transformam essa planta simulada em uma rede física operacional.
- Período de 5 s, latência de reconfiguração de 30 s e horizonte de previsão de
  150 s. Esses tempos são distintos e não configuráveis neste checkpoint.
- Observação atual com tempo relativo não negativo, QBER e estimativa Raman
  em frações [0, 1], reserva de chaves/backlog em bits não negativos e urgência
  positiva em segundos, ou `null` quando não há prazo pendente.
- `pending_admission`: seis linhas de 11 níveis, mais antiga primeiro. Níveis
  permitidos: 0, 0,25, 0,5, 0,75 e 1, como fração de admissão clássica.
- `history_times_s` e `drift_history`: zero a cinco amostras anteriores,
  consecutivas a 5 s, imediatamente anteriores à observação, na mesma ordem
  dos enlaces. Drift fornecido em [0, 1]. Histórico futuro é recusado.

O nome `measured_qber` é preservado do contrato interno do export, mas nesta
sessão o domínio é exclusivamente simulado. Não significa medição experimental.
O histórico de drift é declarado pelo produtor; não é recalculado a partir de
telemetria passada nem certificado como causal apenas por ter timestamps válidos.

## Comparação e semântica da fila

Ridge é comparado à implementação de persistência usando **exatamente o mesmo
snapshot, histórico, planta e fila pendente**. Identidades do pacote/planta são
conferidas. Selecionar persistência não constrói nem carrega o modelo Ridge.
O relatório contém ambas as recomendações, previsões completas 11 × 30,
orçamentos/requisitos de chave, cobertura estimada e enlaces com admissão distinta.

Os primeiros 30 s estão comprometidos pela fila fornecida. Uma recomendação
nova não modifica retroativamente essa fila nem resolve um prazo já dentro da
latência. A sessão nunca avança filas, realimenta observações ou despacha ações.
O instante `effective_at_s` é o instante previsto pela planta, não confirmação
de que um comando foi executado.

Orçamentos de chave são estimativas assintóticas internas. Não representam
chaves entregues, segurança de chave finita ou garantias de SLA. `null` indica
que o orçamento não foi avaliado por ausência de dívida, não zero bits. O campo
de cobertura é uma avaliação do modelo, não sucesso observado da rede.
Diferenças entre recomendações não estabelecem ganho de throughput ou segurança.

A topologia aberta no dashboard não é remapeada para a planta. Não há bridge
de dados físicos, atuação, fidelidade de Bell prevista ou validação WDM→quântico
medida neste incremento.

## Verificação

- **60 testes novos**, incluídos nos **333 testes** de sessões, catálogo, fontes
  e telas que passaram nesta execução.
- **276 testes** de regressão dos modelos, admissão, comandos e barreiras de
  evidência/controle passaram. Total: **609**, não a suíte completa do repositório.
- Ruff passou nos cinco arquivos Python adicionados/alterados.
- Os testes incluem warm-up, baseline com mesma entrada, opção sem Ridge,
  fila mais antiga primeiro, prazo dentro da latência, diferenças de admissão,
  orçamento nulo, entradas inválidas, ZIP alterado, exportação exclusiva,
  invalidação gráfica e controles em janelas pequenas.
- As duas opções executaram pelo worker Qt com o pacote fornecido.
  Capturas 1060 × 760 e 640 × 480 foram inspecionadas: controles visíveis,
  entrada/resultado roláveis.

Os **20 snapshots fornecidos pelo autor** foram convertidos para o novo schema:
10 de persistência e 10 de Ridge. Drift, previsão e admissão coincidiram com
as saídas esperadas, com maior diferença absoluta **0,0** nas duas variantes.
A comparação não regenerou a planta, os 1.440 passos da campanha anterior nem
resultados em laço fechado; é paridade de snapshots do export.

O DEMO usa uma trajetória de drift escrita manualmente, backlog de 100.000 bits
por enlace e prazo de 120 s. Com os pesos fornecidos, Ridge recomendou 0,75 no
enlace `volta_redonda|rio_de_janeiro`, contra 0,50 da persistência. Os outros
dez enlaces tiveram recomendações iguais. Nenhuma dessas ações foi executada,
e a diferença não foi convertida em alegação de ganho.

Artefatos locais em `outputs/allocation_session_ui_20260922/`: `ui-tests.xml`,
`regression-tests.xml`, `{persistence,ridge}-author-example.json`,
`{persistence,ridge}-demo.json` e capturas `{persistence,ridge}-{640,1060}.png`.
Pacote: `F:/qnet_network.zip`, com identidade fixada na admissão existente.
Nenhum script do ZIP foi executado, nenhum novo treinamento foi realizado.

## Próximo incremento

Validar e empacotar as sessões opcionais na distribuição Windows, verificando
uso sem pesos e importação local dos pacotes revisados. Não embutir os pesos
fornecidos sem esclarecer redistribuição. Export seguro do detector, ponte de
features, ensaios em laço fechado e ativação auditável Q05 continuam pendentes.
