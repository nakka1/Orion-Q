# Campanhas oficiais no Windows contra a nuvem — 30/09 a 01/10/2026

Execução no Windows 11 (Python 3.12.14, NumPy 2.4.4, pandas 3.0.2, scikit-learn 1.8.0,
SeQUeNCe 3133c66d), com os ZIPs fixados originais (`F:\qnet_network.zip`
`6b856320…`, `modelo_qkd_ml.zip` `94352f15…`, `Qnet-Network ML.zip` `f7673852…`), pelo
`tools/run_official_campaigns.ps1`, comparada número a número com os resumos publicados
da nuvem (Linux, Python 3.11.15, NumPy 2.4.6) por `tools/compare_campaign_summaries.py`.

**As doze campanhas deram as mesmas conclusões que na nuvem, e onze delas os mesmos
números (tolerância relativa 10⁻⁹, 6.698 valores).** A exceção é uma célula do atuador
limitado. O Verilog não foi repetido (sem simulador HDL no Windows). A comparação final
está em [`orion_q_windows_validation_20261001_comparison.json`](orion_q_windows_validation_20261001_comparison.json)
e entra no registro verificável como P-WINDOWS.

## Resultado por campanha

| Campanha | Valores | Números (10⁻⁹) | Conclusões | Observação |
|---|---:|---|---|---|
| polarisation-calibrado | 275 | idênticos | idênticas | |
| polarisation-desenvolvimento | 279 | idênticos | idênticas | |
| polarisation-actuator | 1.327 | 6 diferem | idênticas | uma célula (calibrado, MLP, 60 rad/s): QBER 0,0479593 contra 0,0479585 (1,6×10⁻⁵ relativo); as 36 conclusões estatísticas iguais |
| polarisation-quantisation | 1.043 | idênticos | idênticas | o nome da coluna de maior erro (10⁻¹⁶) dos portões varia: marcador de plataforma |
| polarisation-blocking | 984 | idênticos | idênticas | |
| polarisation-demo | 36 | idênticos | idênticas | |
| allocation | 401 | idênticos | idênticas | |
| routing | 352 | idênticos | idênticas | |
| routing-sequence | 312 | idênticos | idênticas | o SeQUeNCe no Windows regenerou as 480 linhas do autor |
| detector-causal | 335 | idênticos | idênticas | os SHA-256 dos CSV gerados diferem: marcador de plataforma |
| detector | 603 | idênticos | idênticas | repetida duas vezes em 01/10 (revisões 74403e9 e 065e1eb); com o portão relativo, 7/7 exemplos (entradas a 5,6×10⁻¹⁶), tabelas do autor com erro 0 e avaliação nas sementes 1000 e 2000 idêntica; os SHA-256 dos CSV gerados diferem: marcador de plataforma |
| qkd | 751 | idênticos | idênticas | repetida em 01/10 (revisão 74403e9) com o portão portável: calibração a 3,5×10⁻¹⁶ relativo; ações, parâmetros e bits de chave dos 40 períodos idênticos; tabelas do autor 15/15 e 24/24 |
| rtl | — | — | — | pulada: sem Icarus Verilog e Yosys no Windows |

As campanhas da nuvem usaram um `qnet_network.zip` remontado a partir da pasta do autor,
com outro SHA-256; o Windows usou o ZIP fixado original. Os números idênticos mostram que
o conteúdo dos dois é o mesmo.

## Por que há diferenças

Diferenças no último bit vêm da biblioteca matemática da plataforma (`sin`, `exp`): o
gerador de sessões do autor escreve a temperatura simulada com 17 dígitos, e o último
muda. Três verificações dependiam de igualdade de bytes desses valores:

- **Detector** — as entradas dos exemplos exportados eram comparadas byte a byte. Agora
  são comparadas valor a valor, com a tolerância de 10⁻¹² que o protocolo congelado já
  declara, relativa ao valor acima de 1 (|a − b| ≤ 10⁻¹² · max(1, |b|)); gabaritos, saídas
  e resumos continuam exigindo igualdade de bytes. A primeira versão do portão portável
  usava a tolerância como absoluta e reprovou, no Windows, a taxa de chave de entrada, da
  ordem de 10⁶ Hz, cujo último bit vale 2×10⁻¹⁰ Hz; na escala relativa, o pior caso no
  Windows fica em cerca de 6×10⁻¹⁶.
- **Taxa de chave** — a calibração do enlace do episódio exportado era comparada por
  igualdade. Agora, com tolerância relativa de 10⁻¹²; ações, parâmetros e bits de chave
  continuam exigindo igualdade.
- **Comparador** — o SHA-256 de um CSV gerado e o nome da coluna de maior erro passaram
  a ser marcadores de plataforma: relatados, sem reprovar.

No Linux, onde o autor exportou, as três verificações continuam exatas (erro 0), e as
campanhas do detector e da taxa de chave foram repetidas na nuvem com os portões
portáveis: os resultados são os mesmos de 29/09. A serialização dos CSV do detector passou
a usar LF (`lineterminator="\n"`), correção encontrada na execução no Windows.

O atuador é diferente: o MLP com o atuador limitado a 60 rad/s é sensível ao caminho
(*resets* e saturação), e uma diferença de último bit na inferência muda o percurso em
2×10⁻³ rad de 3.277 rad. A comparação estrita continua reprovando essa célula; nenhuma
conclusão muda.

## Leitura de conclusões

O comparador agora tem duas leituras. A estrita exige todos os números a 10⁻⁹. A de
conclusões exige iguais todos os veredictos, sinalizadores, contagens (vitórias,
sementes, blocos) e rótulos, e os demais números a 10⁻³ relativo. A pré-banca pode
afirmar: **as conclusões das onze campanhas concluídas se reproduzem no Windows; dez de
onze reproduzem também cada número.**

## Histórico da execução

1. 30/09: dez campanhas completaram (nove idênticas); detector e taxa de chave pararam em
   portões de igualdade de bytes.
2. 01/10, revisão 74403e9: taxa de chave idêntica; o detector ainda reprovou 4 de 7
   exemplos pela tolerância absoluta aplicada à taxa de chave de entrada (10⁶ Hz).
3. 01/10, revisão 065e1eb: detector idêntico; o comparador passou a tratar como marcador
   de plataforma todo hash de CSV gerado (`*_csv_sha256`), o que incluiu o hash do conjunto
   de reprodução do detector.
