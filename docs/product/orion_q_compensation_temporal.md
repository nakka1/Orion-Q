# Avaliação temporal de polarização em shadow

Incremento em código-fonte; nenhum atuador, política Q05 ou novo EXE é ativado.
Complementa a [ponte SO(3)](orion_q_compensation_shadow_bridge.md), sem mudar o
preditor ou incluir referências futuras nas entradas.

## Ordem causal e contrato

`TemporalCompensationEvaluation.observe(snapshot)` recebe lotes simulados tipados,
de 1–16 amostras consecutivas a 2 ms. Exige lotes novos **sem sobreposição**. Isso
serve ao produtor artificial atual, não a fontes de janelas deslizantes arbitrárias.
Duplicatas idênticas são ignoradas; sobreposição, rollback e duplicatas conflitantes
são recusados. Referenciais físicos e relógios não são autenticados por esse contrato.

`register(record)` aceita apenas previsão aprendida concluída para o último lote
observado, com 16 amostras. A entrada canônica, hashes, persistência, horizonte,
identidade do checkpoint/arquivo, flags shadow e ausência de referências/erros
futuros são conferidos. Aquecimento não entra nas métricas. Uma previsão que termina
depois da chegada de outro lote não pode ser registrada retrospectivamente.

O alvo UTC é a última amostra de entrada +40 ms. Como cada publicação avança 32 ms,
o alvo normalmente cai dentro do segundo lote posterior. O avaliador procura esse
timestamp exato em todas as amostras do lote novo; não usa apenas a última amostra.
Se o tempo já passou sem o alvo, marca `missed_target`: não interpola e não permite
reparo com lotes antigos. Sem avanço suficiente, a previsão permanece `pending`.

Fonte, campanha, fluxo, nó, relógio, referencial, convenção e regime são constantes
durante uma execução. Checkpoint e hash do arquivo também não podem mudar.
Validação e cálculo precedem mutação do histórico: uma observação recusada não
pontua parcialmente a execução. O relatório devolve cópias, não referências mutáveis.

## Métricas e exportação

Para cada par, o erro é o ângulo geodésico SO(3):
`acos(clip((trace(R_prevista.T @ R_observada) - 1) / 2, -1, 1))`, em radianos.
Reutiliza a definição do avaliador offline. Média e RMS são calculadas sobre os
mesmos pares para o modelo e para a persistência. O delta é modelo menos persistência:
negativo significa erro menor **nessa amostra pareada**, não ganho de controle.

O relatório mantém previsões completas imutáveis, referências e seus hashes,
índices locais de chegada, pendências, alvos perdidos e contagens. Cobertura de
alvos resolvidos = pareados / (pareados + perdidos); exclui pendentes. Essa cobertura
é condicional a previsões registradas, portanto deve ser lida junto do número de
lotes observados. Não mede throughput nem certifica deadlines físicos de 40 ms.

O limite padrão é 256 previsões, configurável até 4096. Não há descarte seletivo
de previsões ruins nem treinamento. Ao atingir o limite, exporte e inicie outra
execução. O registro de ordem é local ao processo, não um carimbo criptográfico.

## Dashboard

Na janela **POLARISATION · LIVE ARTIFICIAL SHADOW**, START inicia uma execução
nova. As métricas mostram pares, pendentes, alvos perdidos e erro médio em mrad.
O relatório JSON também inclui RMS e cobertura. **EXPORT TEMPORAL REPORT** captura
o relatório no clique, mesmo que cheguem outros lotes durante o seletor de arquivo.
Troca de fonte/contexto, fechamento, STOP ou timeout invalidam a exportação pendente.
Exportações exigem um arquivo novo; não sobrescrevem resultados anteriores.

STOP/fechamento/perda de entrada limpam o histórico em memória. Uma violação de
ordem ou do contrato interrompe a avaliação; os registros válidos anteriores
permanecem disponíveis para exportação, sem incorporar a entrada recusada.
Os resultados atuais de inferência e as métricas das previsões antigas ficam separados.

## Diagnóstico reproduzível

Com o ambiente instalado, pacote externo revisado e diretório-pai existente:

```powershell
python tools/run_compensation_temporal_diagnostic.py --qnet-zip F:/qnet_network.zip --output outputs/NOVO-DIRETORIO
```

O programa fixa semente 1337 e 32 lotes antes de calcular erros. Avalia os quatro
checkpoints, sem seleção, ajuste ou parada antecipada. Por checkpoint são 32
previsões, 30 pares, dois alvos ainda pendentes e nenhum perdido. Grava todos os
registros e um resumo. É diagnóstico curto de software, não campanha de robustez.

O produtor atual é uma composição simples de senoide e deriva sem ruído de medida,
contagem de fótons ou calibração da planta do artigo. Amostras correlacionadas e
uma única semente não sustentam significância ou generalização. Não há evidência
nova de predição de fidelidade de Bell a partir de WDM.

[Resultados iniciais](../results/orion_q_compensation_temporal_20260923.md).
