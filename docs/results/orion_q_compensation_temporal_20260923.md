# Avaliação temporal SO(3) — 23/09/2026

Entrega em código-fonte: pareamento causal de previsões dos quatro compensadores
com referências posteriores, métricas no dashboard e exportação de histórico.
Sem alterações nos pesos, seleção automática de modelo, atuação ou novo EXE.
[Contrato e reprodução](../product/orion_q_compensation_temporal.md).

## Diagnóstico com pesos originais

Semente 1337, 32 lotes por checkpoint, cadência virtual de 2 ms, horizonte de 40 ms.
O produtor e os parâmetros não foram ajustados após inspecionar os erros. Todas as
quatro variantes foram avaliadas e registradas, inclusive quando piores que a base.
O sinal é artificial, composto por senoide e deriva, sem contagem de fótons ou
ruído de estimação. Não é réplica validada da planta do artigo.

Cada checkpoint: **32 previsões registradas, 30 pareadas, duas pendentes, zero
alvos perdidos**. Cobertura dos alvos resolvidos: 100%; não inclui as duas pendentes
e não é medida de deadline físico. As previsões foram concluídas antes de fornecer
o lote seguinte ao avaliador. A rotina não simula atrasos de processamento da GUI.

Erro médio angular (menor é melhor), em **mrad**:

| Regime | Modelo | Erro do modelo | Persistência | Delta modelo − persistência |
|---|---|---:|---:|---:|
| Calibrado | Ridge | 4,417 | 5,269 | −0,852 |
| Calibrado | MLP | 27,194 | 5,269 | +21,924 |
| Desenvolvimento | Ridge | 4,498 | 5,570 | −1,073 |
| Desenvolvimento | MLP | 35,574 | 5,570 | +30,003 |

Neste diagnóstico, Ridge foi melhor que persistência nos dois regimes; MLP foi
pior. Isso **não é evidência de superioridade geral do Ridge nem de fracasso geral
do MLP**: é uma única semente, uma família curta de sinais, sem certificação de
domínio ou planta física. O avaliador não calcula significância nem escolhe um
modelo para o produto. Todos continuam opcionais e OFF por padrão.

Arquivos em `outputs/compensation_temporal_20260923/author-seed1337/`: `summary.json`
e quatro relatórios completos com previsões, referências, hashes, ordem de chegada,
erro médio/RMS e contagens. Arquivo QNET externo utilizado:
`6b8563209f54a2d566f1a4229b140b75fda93555085ff2bf6bfcad8f07abd938` (SHA-256).
Os pesos não foram copiados ao produto.

## Verificação de software

**466 testes passaram.** O relatório
`outputs/compensation_temporal_20260923/regression-final.xml` registra a regressão
selecionada; não corresponde à suíte integral do repositório.

Cobertura adicionada:

- Alvo de 40 ms dentro do segundo lote posterior, matriz SO(3), distâncias
  angulares conhecidas até π, preservação de previsões piores que persistência.
- Referência/erro futuro no registro, entrada/hash divergente, baseline alterada,
  alvo incorreto, rotação inválida, mudança de modelo ou artefato: recusados.
- Duplicatas, sobreposição, rollback, regime/campanha/relógio/referencial diferentes,
  evidência medida e referência inválida: sem mutação parcial do histórico.
- Registro tardio e worker que termina depois do lote futuro: sem pontuação
  retrospectiva. Lacunas não são interpoladas nem preenchidas por lotes antigos.
- Limite de previsões, exportação sem sobrescrita, relatório capturado antes do
  seletor de arquivo e invalidação em STOP/troca de fonte/timeout/fechamento.
- Métricas da GUI após chegada do alvo; captura em 640×480 inspecionada com área
  rolável e botões visíveis. Testes GUI usam checkpoints-fixture; o diagnóstico
  numérico separado usa os quatro checkpoints originais.

Ruff seletivo passou. Testes incluídos nos gates locais/CI; nenhum novo build Windows
ou execução remota de CI foi realizado nesta etapa.

## Atualização posterior — 24/09/2026

A [campanha SO(3) v1 concluída](orion_q_compensation_campaign_20260923.md)
implementa o primeiro passo de robustez abaixo, com 240 execuções e seis cenários.
O novo relatório publica todos os 24 grupos, cobertura e 500 testes da regressão
selecionada; não somar às 466 aprovações históricas deste diagnóstico. A família
continua artificial e conhecida, sem validação física ou seleção de política.

## Próximo passo e limites científicos (posição do diagnóstico)

Antes de ajustar modelos, congelar uma campanha própria de robustez, com múltiplas
sementes, sinais fora deste exemplo, perdas/atrasos e comparadores adequados ao
tipo de previsão. Se houver ajuste, separar calibração e teste por campanha.
Preservar a persistência; um ensaio de controle também exige comparadores de
controle, como alfa-beta adequadamente calibrado, e uma planta explicitamente validada.

Nada aqui demonstra predição de fidelidade de Bell a partir de telemetria WDM,
ganho em QBER/SKR, controle em laço fechado ou funcionamento com equipamento real.
O progresso é uma avaliação causal auditável da integração SO(3) no sandbox.
