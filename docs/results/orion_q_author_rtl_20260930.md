# Verilog do autor em simulador HDL e síntese

Data: 30/09/2026. Ferramentas: Icarus Verilog 12.0, Yosys 0.33, Python 3.11,
NumPy 2.4.4 e scikit-learn 1.8.0 (esta só para o detector em árvores).
**Todo o Verilog dos dois pacotes do autor foi simulado contra as referências em
ponto fixo do próprio autor e sintetizado. O compensador Ridge, o MLP (camada a
camada), o CORDIC no domínio do logaritmo de SO(3), o preditor do sincronizador e o
classificador em árvores do detector são exatos ao bit em todas as palavras
conferidas. Dois defeitos apareceram, nenhum no caminho de dados usado: o *testbench*
do MLP gerado pelo autor não pode passar (compara a saída da primeira camada com a
saída da rede) e o CORDIC implementa só uma das duas dobras de quadrante da
referência, o que só importa com seno negativo, que o logaritmo nunca produz.**

O `README` do RTL do compensador dizia: "Este *testbench* nunca foi executado" — o
contêiner do autor não tinha simulador HDL. O dos modelos QKD foi co-simulado na
exportação; aqui ele é refeito de forma independente.

## O que foi conferido

| Bloco | Referência do autor | Conferência | Resultado |
|---|---|---|---|
| MAC do compensador Ridge (48→3, 16 bits) | `vectors_out.hex` de `deploy/fixed_point.py` | *testbench* do autor, sem alteração | 64 vetores × 3 saídas, exatos |
| MAC do compensador MLP (48→192→192→3) | idem | *testbench* do autor | não pode passar: lê 12.288 palavras esperadas de um arquivo com 192 (a saída da rede); defeito do gerador `emit_verilog` |
| MLP, três camadas encadeadas | `FixedLayer` do autor, com as memórias e os formatos do `README` | *testbench* encadeado do ORION-Q, três motores `qnet_mac` | 12.288 + 12.288 + 192 palavras, todas exatas |
| CORDIC (32 bits, 20 de fração, 8 estágios) | `cordic_atan2`, `cordic_sincos` | 3.000 vetorizações com y ≥ 0, 6 casos de borda, 3.000 rotações em [−π, π] | todas exatas |
| CORDIC fora do domínio | idem | 1.000 vetorizações com y < 0 | 4º quadrante exato (513); 3º quadrante diverge (487 de 487) |
| Preditor do sincronizador (9 coeficientes, 12 bits) | `QuantisedRidge` com a ROM do pacote | ROM, intercepto e cabeçalho reconstruídos idênticos aos do pacote; 512 linhas do pipeline de sincronismo do autor | 512 de 512 exatas |
| Classificador em árvores do detector (5 rodadas, 14 bits) | `QuantisedBoosting.raw_scores` | *boosting* retreinado pelo pipeline do autor; memórias de nós, árvores e linha de base idênticas byte a byte às do pacote; 64 linhas (a configuração da exportação) e 1.024 linhas, nos dois modos de memória | 0 escores e 0 classes divergentes (6 escores por linha) |

**MLP.** Os formatos de viés das camadas 1 e 2 não estão escritos no pacote. Os
pesos, os vieses e os demais formatos são os do pacote; o formato de viés da camada 0
está no *testbench* do autor. Entre as 256 combinações possíveis para as outras duas,
uma única faz a referência do autor reproduzir as 192 saídas de ouro: Q2.13, Q1.14 e
Q0.15. O *testbench* encadeado usa esses formatos e confere cada palavra oculta.

**CORDIC.** A referência dobra o vetor duas vezes quando x < 0 e y < 0 e corrige o
ângulo acima de π; o RTL dobra uma vez e não corrige. No logaritmo de SO(3) o `y` é
a norma do eixo (sempre ≥ 0), então o caso não ocorre no compensador; como `atan2`
genérico, o módulo estaria errado no 3º quadrante.

**Sincronizador.** A ROM de 12 bits do pacote não é o modelo exportado
(`sincronizador_ridge.json`, o que o ORION-Q admitiu e avaliou) quantizado: os
coeficientes diferem em até 8 LSB. O treino do sincronizador do autor sorteia o ruído
de medição sem semente (`build_sync_dataset(rng=None)`), então cada execução da
exportação produz um modelo um pouco diferente, e a ROM veio de outra execução. O RTL
está correto para a ROM que carrega.

**Detector.** O modelo retreinado é o mesmo que a exportação segura provou idêntico
ao *pickle* do pacote, que continua sem ser desserializado. A versão em FPGA é outro
modelo — podado a 5 rodadas e quantizado — e não substitui o detector completo.

## Síntese (Yosys, `synth_xilinx`, família Xilinx 7)

Mapeamento lógico apenas: sem posicionamento, roteamento ou análise de tempo, e sem
E/S. Os números são estimativas de recurso, não de frequência.

| Projeto | LUT | FF | DSP48 | BRAM18 |
|---|---:|---:|---:|---:|
| MAC do compensador Ridge (48→3) | 123 | 73 | 1 | 0 |
| MAC do MLP, camada 0 (48→192) | 156 | 85 | 1 | 0 |
| MAC do MLP, camada 1 (192→192) | 172 | 91 | 1 | 0 |
| MAC do MLP, camada 2 (192→3) | 125 | 79 | 1 | 0 |
| CORDIC (32 bits, 8 estágios, iterativo) | 698 | 200 | 0 | 0 |
| Sincronizador (`sync_top`, ROM em lógica) | 161 | 56 | 1 | 0 |
| Detector (`detector_top`, 1.830 nós de 42 bits) | 2.178 | 395 | 0 | 6 |

Os MAC são seriais (um produto por ciclo), por isso cabem num DSP; os pesos do MLP
(92 KiB) ficam fora do bloco sintetizado.

## Como reproduzir

```
python tools/verify_author_rtl.py --qkd-zip modelo_qkd_ml.zip --output outputs\author-rtl-v1
```

Exige `iverilog`/`vvp` e `yosys` no PATH; a parte do detector exige o ambiente do autor
(scikit-learn 1.8.0) e é pulada, com registro, em outro. Resumo:
[`orion_q_author_rtl_20260930_summary.json`](orion_q_author_rtl_20260930_summary.json).
`tests/test_author_rtl.py` repete as conferências rápidas e é pulado sem simulador.

## Limites

- Simulação funcional; nenhum projeto foi posicionado, roteado ou medido em placa.
- Só o caminho linear do compensador (MAC) e o CORDIC existem em RTL; o logaritmo e a
  exponencial de SO(3) completos, a normalização e o controle não foram escritos em
  Verilog pelo autor.
