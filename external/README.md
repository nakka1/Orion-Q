# Pacotes do autor incorporados ao repositório

Os modelos admitidos no ORION-Q foram treinados com duas versões do pacote `qnet_autopilot`
(A. O. S. Santos), que divergem em alguns módulos. Cada uma entra inteira, com nome próprio,
para que os produtores e as plantas do ORION-Q usem exatamente o código em que os modelos
foram treinados:

| Pacote | Origem | Modelos |
|---|---|---|
| `src/qnet_network` | `qnet_autopilot/` de `Qnet-Network ML.zip` (15/09/2026) | compensador, alocador, roteamento |
| `src/qnet_qkd` | `qnet_autopilot/` da árvore que gerou `modelo_qkd_ml.zip` (15/09/2026) | detector, ator de taxa de chave, sincronizador |

`external/qnet_network/` e `external/qnet_qkd/` trazem os scripts, os testes, o README e, no
caso de QKD, o manuscrito (`artigo/`), a exportação de inferência (`exportacao/`) e os dados
de campo da rede quântica de Cambridge (`dados_cambridge/`, Dynes et al. 2019, CC-BY 4.0,
doi:10.17863/CAM.44524).

## Regra da cópia

`tools/vendor_qnet.py` refaz a cópia a partir dos arquivos originais. Nos pacotes, só as raras
importações absolutas `from qnet_autopilot…` são renomeadas; o resto é byte a byte. Nos scripts
e testes, todo `qnet_autopilot` vira o nome novo. `external/VENDORED.json` guarda o hash de cada
arquivo antes e depois; `tests/test_vendored_qnet.py` falha se alguém editar a cópia.

Os pesos continuam fora do repositório, nos pacotes fixados por hash e lidos como dados.

## Testes do autor

Precisam do SeQUeNCe (o autor usou o `master` de 13/09/2026, commit `3133c66d`):

```
git clone https://github.com/sequence-toolbox/SeQUeNCe.git external/sequence
git -C external/sequence checkout 3133c66d
set SEQUENCE_SRC=%CD%\external\sequence          (Windows; no Linux: export SEQUENCE_SRC=...)
cd external\qnet_network && python -m pytest -q -o addopts=""
cd ..\qnet_qkd && python -m pytest -q -o addopts=""
```

Em 29/09/2026: `qnet_network` 878 aprovados; `qnet_qkd` 926 aprovados. O CI roda os dois
no job `qnet-packages`.
