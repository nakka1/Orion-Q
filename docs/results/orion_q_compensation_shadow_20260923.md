# Ponte shadow SO(3) — 23/09/2026

Registro desta etapa do backend. O incremento posterior de produtor artificial e
interface está documentado em [shadow gráfico de polarização](orion_q_compensation_shadow_ui_20260923.md).

Segunda ponte de modelos opcionais ao sandbox, após sincronização. Entrega em
código-fonte, sem reconstrução do executável ou mudança de política Q05.
[Contrato e operação](../product/orion_q_compensation_shadow_bridge.md).

## Entrega

Janela tipada por fonte/campanha/fluxo/nó/relógio/referencial, convenção de rotação,
regime e unidades. Cadência de 2 ms, horizonte de 40 ms e 400 fótons de monitoração
por base declarados. Seleção explícita dos quatro checkpoints Ridge/MLP; matrizes
SO(3) e covariáveis verificadas antes de abrir o ZIP. Warm-up permanece persistência.

O método do runtime não entra no ciclo de controle. Sem telemetria apropriada,
retorna indisponibilidade; dados WDM ou do sincronizador não são reaproveitados
como rotação. Evidência medida é recusada nesta ponte simulada. Não há atuador,
predição de fidelidade, comando, geração de chaves ou alegação de ganho.

## Testes e diagnóstico

**261 testes passaram** no conjunto selecionado: ponte nova, sessão e adaptadores
de compensação, sandbox, ponte de sincronização, gates físicos e trilhas de
evidência. Não é a suíte integral do repositório. Ruff e `git diff --check` passaram.

Casos cobertos: quatro checkpoints, equivalência numérica à sessão offline,
aquecimento, regime/frequência/deriva/cadência/unidades incompatíveis, matriz com
reflexão/não ortogonal, covariáveis inválidas, tempo futuro/vencido, origem/referencial
divergentes, limites, pacote alterado, exportação exclusiva e isolamento de entrada.
Espiões impedem chamadas a política/ciclo/executores; journal permanece byte a byte
inalterado durante avaliação. Campos de fidelidade do envelope não alteram a saída.

O diagnóstico com `F:/qnet_network.zip` concluiu **8/8 verificações**: uma amostra
e uma janela completa para cada um dos quatro checkpoints. Saídas coincidiram com
a sessão offline; uma amostra produziu `warmup_persistence` e 16 produziram `model`.
Referência e erros angulares permaneceram ausentes. Nenhum ganho foi calculado.

ZIP QNET SHA-256:
`6b8563209f54a2d566f1a4229b140b75fda93555085ff2bf6bfcad8f07abd938`.
Evidências: `outputs/compensation_shadow_20260923/regression-final.xml` e
`outputs/compensation_shadow_20260923/author-checks/`, com oito registros e
`report.json`. Pesos permanecem externos, sem cópia para o produto.

## Ainda pendente

Produtor contínuo, apresentação shadow de polarização no dashboard, pareamento
temporal com referências posteriores e campanha de robustez própria. Inspeção de
hardware, compatibilidade de referenciais físicos e avaliação em laço fechado
permanecem fora desta entrega. Paridade do mesmo adaptador não é validação física
independente. Não se transporta o ganho ou fracasso do sincronizador para SO(3).
