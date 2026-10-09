# ORION-Q — séries temporais por tecnologia

Os nós que participam de protocolos possuem um painel **Séries temporais · observáveis dos protocolos**. O seletor identifica tecnologia, observável, unidade e sessão. Cada seleção usa seu próprio eixo: taxas em `bit/s` ou `pair/s`, resíduos de fase em `rad`, contagens em `count` e proporções em `1`. A origem, o identificador da fonte, o escopo e o domínio temporal aparecem junto ao gráfico.

O gráfico de polarização existente continua restrito aos enlaces legados: ele recebe a visibilidade `1 − 2·QBER` amostrada pelo monitor do backend. Métricas de TF-QKD, memória, BSM, purificação e satélites usam timestamps explícitos. A interface não cria uma onda óptica, uma fidelidade Bell ou amostras a 20 Hz a partir de uma taxa de chave.

## Por que Google e CIMATEC tinham apresentações diferentes

No demo institucional, CIMATEC mantém enlaces legados com amostras de polarização. US-GOOG-QA, alias canônico de GOOGLE-QAI, participa de US-SATELLITE, um protocolo que anteriormente publicava somente a tabela de observações. Esses enlaces eram excluídos corretamente do monitor de polarização, mas ainda não possuíam um gráfico temporal próprio.

O novo painel permite inspecionar os observáveis do protocolo de cada nó. As sessões TF-QKD do demo são RIO-TF, ONTARIO-TF e IBM-IQC-TF; o nome ou a instituição do nó não determina sua tecnologia. Quando um bloco de satélite recebido publica indisponibilidade ou coincidências zero, esses valores podem ser apresentados como tal. QBER sem cliques úteis permanece ausente. Um bloco recebido não comprova uma rota quântica utilizável.

## Cadeia de dados e lacunas

[ProtocolHistory](../../src/quantum_autopilot/network_engine/protocol_history.py) registra publicações após o processamento e a recepção clássica do backend. A história é limitada por quantidade de pontos, janela temporal e número de séries. Blocos usam `last_observed_at_s`; taxas cumulativas e ocupação de memória usam a publicação do estado derivado no relógio do protocolo. Repetir uma publicação não cria novas amostras de um bloco antigo. Alterar a fonte ou reiniciar o relógio remove a história anterior.

O backend publica as séries em `live.technologies['protocol_series']`. Cada série identifica sessão, tecnologia, métrica, unidade, nós, origem, fonte, escopo e limites do eixo. Os pontos contêm `at_s`, valor e motivo quando ausente. O tempo é identificado como `protocol_clock_s`, com origem simulada ou runtime explícita; o UTC da publicação permanece separado. A interface apenas projeta esses registros, sem acumular valores entre snapshots, reamostrar ou preencher lacunas.

Sem aquisição validada e recepção clássica, os valores são `null`, mesmo que o backend possua contadores iniciais iguais a zero. Falhas e ausência de um observável também geram lacunas explicadas. O traço é interrompido nesses pontos; observações isoladas aparecem como pontos. Fidelidade estimada de um segmento exige pares heraldados nesse segmento e mantém seu escopo local.

## TF-QKD e interpretação científica

O painel TF-QKD pode mostrar QBER observado, resíduo da referência de fase, bits sifted, yield monofotônico estimado, erro X observado e taxa condicional de chave. Esses observáveis correspondem a contagens, interferência de campos e pós-processamento do protocolo. A proposta original descreve a interferência de campos preparados independentemente e a relação entre taxa e transmissão; veja [Lucamarini et al., Nature, 2018](https://www.nature.com/articles/s41586-018-0066-6).

No protocolo sending-or-not-sending, a análise utiliza estados decoy e estima contribuições monofotônicas e erros de fase. Veja [Wang, Yu e Hu, Physical Review A, 2018](https://arxiv.org/abs/1805.09222). O modelo reduzido do ORION-Q identifica o erro X como hipótese substituta de fase; não o apresenta como um limite comprovado de segurança composable.

A **estimativa condicional de chave** permanece distinta dos **bits secretos certificados**. O backend atual publica zero bits certificados: um valor positivo de taxa condicional não certifica chave, segurança de hardware ou atuação física. Todas as séries conservam a origem simulada ou da aquisição recebida.

## Verificação

[test_protocol_time_series.py](../../tests/test_protocol_time_series.py) verifica timestamps, deduplicação, limites de retenção, troca de fonte, lacunas e ausência de fidelidade/onda inventada para TF-QKD. [test_console_protocol_series.py](../../tests/test_console_protocol_series.py) verifica seleção, unidades, eixos, pontos e proveniência na interface. [ui_pipeline_validation.py](../../src/quantum_autopilot/console/ui_pipeline_validation.py) exercita uma janela nativa de diagnóstico com estado novo, o provider real, controles FPGA e exportação QKEYS para o caminho escolhido.
