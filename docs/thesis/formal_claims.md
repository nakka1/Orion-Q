# Reivindicação científica formal do TCC

## Status desta formalização

Esta síntese foi escrita após a inspeção e a análise dos resultados. Ela não é
uma pré-especificação confirmatória. Sua função é fixar a linguagem final do
TCC, impedir extrapolações indevidas e ligar cada afirmação a um artefato
verificável.

O registro executável correspondente está em
[`tcc_claim_registry.json`](tcc_claim_registry.json). Cada evidência possui
seletor, valor esperado e hash SHA-256; qualquer alteração nos resultados
invalida automaticamente a reivindicação até nova auditoria.

As evidências citadas foram reduzidas a um
[`pacote portável e versionado`](evidence_bundle.md): 14 linhas agregadas em 8
tabelas, acompanhadas de hashes, proveniência e licenças. Assim, as 69
verificações podem ser repetidas em um clone novo sem baixar dados brutos nem
recriar a pasta local `outputs/`.

## Pergunta de pesquisa

> Em que medida condições ópticas clássicas WDM carregam informação sobre o
> desempenho de um sistema quântico e podem sustentar um gêmeo digital
> preditivo?

## Hipótese original e disposição

**Hipótese original forte:** a história temporal da telemetria WDM fornece
informação incremental para prever um alvo quântico físico futuro, além do
histórico do próprio alvo.

**Disposição:** aberta e não testada. Nenhuma fonte pública utilizável combina
telemetria WDM temporalmente ordenada com QBER, SKR ou fidelidade física futura
medida no mesmo relógio e enlace. O protocolo existe, mas preencher a ausência
de dados com fidelidade simulada tornaria o teste circular.

## Reivindicação principal defensável

> Nas varreduras experimentais públicas analisadas, a condição de coexistência
> WDM clássica está associada a degradação direcionalmente replicada de QBER e
> SKR. A estabilidade IrelandQCI é robusta condicionalmente às sessões
> publicadas, mas condição e sessão não são separáveis. Dentro dessa grade, uma
> regra monotônica baseada somente na potência ordena a dominância QBER–SKR
> melhor que o Pareto-TriHead avaliado, que não demonstra estrutura residual
> além da dose, transporte para potências não vistas ou previsão temporal de
> fidelidade quântica.

Essa é uma reivindicação de **associação física e informação de ordenação em
dados medidos**, não de previsão temporal ou fidelidade de Bell.

## Cadeia de evidência

| Elo | Pergunta | Resultado | Status formal |
|---|---|---|---|
| Mecanismo simulado | WDM pode afetar a fidelidade no gêmeo original? | Sim, dentro da equação e do simulador | Suportado somente em simulação |
| Efeito físico QKD | Coexistência clássica altera QBER/SKR medidos? | Direção positiva em IrelandQCI e Bristol | Suportado, exploratório |
| Robustez das leituras | O efeito IrelandQCI sobrevive à dependência dentro das células? | Positivo com blocos 1/5/10 e exclusões | Suportado condicionalmente às sessões |
| Separação de sessão | IrelandQCI separa condição WDM de deriva entre sessões? | Uma sessão por distância/condição | Não suportado |
| Informação conjunta | A condição alinhada ajuda a ordenar QBER/SKR? | Passa controle permutado e randomização conjunta | Suportado, exploratório |
| Informação além da dose | O TriHead supera uma regra monotônica de potência? | Regra fixa: 0,6784; Pareto-TriHead: 0,5625 | Refutado para os modelos avaliados |
| Fronteira completa | O modelo recupera todos os pontos Pareto? | Jaccard baixo e não superior ao nulo | Não suportado |
| Transporte | O modelo generaliza para potência inteiramente não vista? | Não supera baseline; falha em log-SKR | Refutado para os modelos avaliados |
| Previsão temporal | WDM passado prevê alvo quântico físico futuro? | Fonte pareada inexistente | Aberto, não testado |

O encadeamento correto é:

```text
plausibilidade simulada
        ↓
efeito QKD físico medido e replicado
        ↓
robustez condicional dentro das sessões; efeito de sessão não separável
        ↓
informação para ordenação dentro da grade
        ↓
estrutura aprendida além da dose: não demonstrada
        ↓
transporte para condição não vista: falhou
        ↓
previsão temporal de alvo físico: ainda não testada
```

Nenhum elo autoriza pular diretamente da simulação para uma alegação de
fidelidade física prevista.

## Fundamento quantitativo

### 1. Dependência física medida

Na IrelandQCI, a unidade é a média da célula fibra/VOA/condição, e a condição
injetada média é comparada à linha de base sem sinal em 16 estratos:

| Alvo | Degradação média | p corrigido | Resultado |
|---|---:|---:|---|
| QBER reportado | +0,013204 | 0,001495 | positivo |
| `log1p(SKR)` | +0,360128 | 0,011475 | positivo |

O valor de QBER permanece na escala ambígua publicada, “ratio (or %)”; ele não
é reinterpretado como pontos percentuais.

Na replicação entre IrelandQCI e Bristol, a direção adversa aparece nas duas
plataformas: `p=0,001495` para QBER e `p=0,005737` para `log1p(SKR)` após Holm.
As heterogeneidades são `95,4%` e `79,9%`, respectivamente. Portanto, replica-se
a direção, não uma magnitude universal. Veja o
[`relatório de replicação`](../results/cross_platform_qkd_replication.md).

Uma auditoria adicional retorna às 3.893 leituras sem tratá-las como unidades
experimentais independentes. Ela reamostra estratos VOA dentro de cada fibra e
blocos circulares de 1, 5 e 10 leituras dentro de cada célula. Para bloco 10,
os intervalos Bonferroni condicionais às sessões são:

| Alvo | Degradação média | Intervalo hierárquico condicional |
|---|---:|---:|
| QBER reportado | +0,013204 | [0,000966; 0,036818] |
| `log1p(SKR)` | +0,360128 | [0,075857; 0,733341] |

As duas direções permanecem positivas para todos os tamanhos de bloco, após a
exclusão de qualquer estrato e em cada configuração de fibra isoladamente.
Contudo, cada distância/condição aparece em uma única sessão. Logo, condição
WDM e deriva da sessão não podem ser separadas, e não existe valor-p
confirmatório clusterizado por campanha. Veja a
[`auditoria hierárquica`](../results/qkd_hierarchical_robustness.md).

### 2. Informação de ordenação QBER–SKR

Para o TriHead Pareto-aware, nas predições leave-one-stratum-out:

| Comparação | Acurácia candidata | Controle | Diferença | Intervalo Bonferroni |
|---|---:|---:|---:|---:|
| Condição alinhada vs WDM permutado | 0,5625 | 0,3874 | +0,1750 | [0,0168; 0,3111] |

Uma segunda falsificação mantém as previsões congeladas e relabela QBER/SKR
conjuntamente 20.000 vezes dentro de cada estrato físico. A acurácia observada
é `0,5625`, contra média nula `0,4089`, com `p` Bonferroni `0,0118`.

Logo, a evidência não depende de uma única realização de permutação. Ela indica
que o alinhamento da condição contém informação sobre **qual condição domina
outra** no plano QBER–SKR. Veja a
[`auditoria de falsificação`](../results/qkd_pareto_falsification.md).

Esse controle, porém, não determina se a rede aprendeu mais que a ordem física
óbvia das potências. Uma nova auditoria compara as mesmas predições congeladas
contra quatro baselines monotônicos, todos sem acesso aos alvos do estrato
retido:

| Comparação de dominância | Baseline | Pareto-TriHead | Diferença | Intervalo Bonferroni |
|---|---:|---:|---:|---:|
| Regra fixa de potência vs modelo | 0,6784 | 0,5625 | -0,1159 | [-0,1966; -0,0329] |
| Isotônico somente por potência vs modelo | 0,6223 | 0,5625 | -0,0598 | [-0,1449; 0,0297] |
| Ridge de caminho + isotônico vs modelo | 0,6223 | 0,5625 | -0,0598 | [-0,1441; 0,0315] |
| Ridge de caminho + linear monotônico vs modelo | 0,6784 | 0,5625 | -0,1159 | [-0,1976; -0,0298] |

A diferença é sempre `modelo − baseline`. A regra fixa, que usa apenas a
direção “mais potência é adversa”, supera corrigidamente o Pareto-TriHead. O
resultado anterior continua mostrando alinhamento acima de controles nulos,
mas não pode mais ser interpretado como estrutura aprendida além da dose. Veja
a [`auditoria monotônica`](../results/qkd_dose_order_falsification.md).

### 3. Limites que também fazem parte do resultado

- Pareto Jaccard do modelo: `0,2298`, abaixo do limiar congelado `0,5` e não
  superior à randomização.
- Ganho de dominância sobre o TriHead convencional: `+0,1342`, mas o intervalo
  corrigido `[-0,0004; 0,2987]` cruza zero.
- Contra a regra fixa de potência, o ganho do Pareto-TriHead é `-0,1159`, com
  intervalo corrigido `[-0,1966; -0,0329]`: o baseline é superior.
- A robustez IrelandQCI é condicional às dez sessões observadas; não há
  repetição independente da mesma combinação distância/condição.
- No holdout de condição, MAE de log-SKR da média do estrato: `0,2813`; TriHead:
  `0,6628`; Pareto-aware: `0,6624`. Ambos são corrigidamente piores.
- Nenhuma análise medida possui fidelidade de Bell ou eixo temporal WDM/QKD
  pareado.

Esses resultados negativos impedem que “informação dentro da grade” seja
rebatizada como “modelo geral de resposta à potência”.

## Estimandos formais

### Efeito físico

```text
Δ_físico = média[degradação QKD sob condição clássica injetada
                 − degradação na linha de base], pareada por fibra/VOA
```

Uma degradação positiva significa QBER maior ou SKR menor, conforme a
orientação previamente definida para o alvo.

### Alinhamento de ordenação

```text
Δ_ordem = acurácia de dominância do modelo alinhado
          − acurácia do mesmo modelo com condição WDM permutada
```

O resultado principal é `Δ_ordem=+0,1750`, com intervalo corrigido inteiramente
positivo frente ao modelo treinado com WDM permutado.

### Informação residual além da dose

```text
Δ_residual = acurácia de dominância do Pareto-TriHead
             − acurácia da regra monotônica de potência
```

O resultado é `Δ_residual=-0,1159`, com intervalo corrigido inteiramente
negativo. Logo, a arquitetura avaliada não demonstra informação de ordenação
adicional à regra de dose.

### Hipótese temporal ainda necessária

```text
Δ_temporal = erro(histórico do alvo)
             − erro(histórico do alvo + WDM causalmente alinhado)
```

Para confirmação futura, `Δ_temporal` deverá ser positivo e superar também o
controle de WDM temporalmente permutado em campanha ou dispositivo retido.

## Linguagem autorizada para o TCC

Pode-se afirmar:

- condições de coexistência WDM estão associadas à degradação de QBER/SKR
  medida em hardware;
- a direção da degradação aparece em duas plataformas públicas;
- na IrelandQCI, a direção sobrevive a blocos dentro das células e exclusões,
  condicionalmente às sessões publicadas;
- dentro da grade IrelandQCI, a potência WDM fornece uma ordenação útil da
  dominância conjunta QBER–SKR;
- o Pareto-aware passa um controle treinado de WDM permutado e uma randomização
  conjunta pós-ajuste, mas não supera a regra monotônica de potência;
- transporte para condição não vista e previsão temporal permanecem sem
  validação.

Não se pode afirmar:

- que telemetria WDM passada prevê fidelidade física futura;
- que QBER é fidelidade de Bell ou pode ser convertido diretamente nela;
- que o efeito possui magnitude comum entre plataformas;
- que a associação observada prova causalidade;
- que IrelandQCI possui sessões repetidas por condição ou que o bootstrap
  remove deriva compartilhada pela sessão;
- que o modelo extrapola para potências fora da grade;
- que o TriHead aprendeu relações de ordenação além do efeito de dose;
- que a fronteira prevista autoriza controle autônomo.

## Contribuição científica do trabalho

A contribuição não é apenas um resultado positivo de modelo. Ela possui três
partes verificáveis:

1. uma arquitetura experimental que separa simulação, replay híbrido e alvo
   quântico medido;
2. evidência física replicada, auditoria hierárquica das leituras e diagnóstico
   executável do confundimento condição–sessão;
3. identificação empírica de dois limites: o TriHead não supera o prior de dose
   e o sinal não transporta para uma condição inteiramente não vista.

Essa terceira parte dá valor científico e de engenharia ao resultado: o gêmeo
deve conhecer sua grade de validade e recusar extrapolação silenciosa.

## Tradução para o produto acadêmico

O dashboard local deverá consumir o registro JSON e mostrar, para cada saída:

| Situação | Selo | Comportamento |
|---|---|---|
| Efeito e prior de dose dentro da grade | `SUPORTADO_EXPLORATORIO` | Mostrar medições, regra monotônica e incerteza; sem atuação |
| Separação entre condição e sessão | `NAO_SUPORTADO` | Marcar intervalo como condicional e proibir linguagem causal |
| Ranking neural além da dose | `NAO_SUPORTADO` | Exibir o baseline lado a lado e impedir alegação de superioridade |
| Fronteira completa | `NAO_SUPORTADO` | Exibir como análise exploratória |
| Potência ausente da calibração | `FORA_DA_GRADE` | Bloquear recomendação automática |
| Fidelidade | `SIMULADO` | Manter somente no sandbox identificado |
| Previsão temporal medida | `NAO_TESTADO` | Informar ausência de fonte pareada |

Assim, o produto não comercial demonstra uma função concreta: **auditar dados,
quantificar evidência e impedir que uma previsão seja usada além do domínio em
que foi validada**.

## Texto curto para resumo ou conclusão

> Os resultados mostram que a coexistência WDM clássica está associada a
> degradação direcionalmente replicada de QBER e SKR medidos. Na IrelandQCI,
> essa direção permanece positiva sob bootstrap hierárquico e exclusões, mas
> apenas uma sessão foi publicada por distância/condição, impedindo separar
> condição WDM de deriva da sessão. O Pareto-TriHead superou controles de
> permutação ao ordenar
> relações de dominância QBER–SKR, porém uma regra fixa baseada somente na
> potência obteve acurácia maior (`0,6784` contra `0,5625`). A recuperação da
> fronteira completa permaneceu baixa e os modelos também não transportaram
> desempenho para potências não vistas. Conclui-se que há evidência
> exploratória de degradação físico-quântica e de uma relação dose–resposta
> útil, mas não de estrutura neural residual, previsão temporal ou fidelidade
> quântica física.
