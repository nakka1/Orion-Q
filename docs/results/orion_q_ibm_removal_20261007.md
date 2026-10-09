# Retirada da integração IBM Quantum — ORION-Q 5.49

Em 07/10/2026, a integração IBM Quantum foi removida por solicitação do
responsável pelo projeto. A retirada abrange código, dependência opcional,
comandos instaláveis, experimentos e documentação operacional. Qiskit/Aer
continua como motor de simulação local. A instituição IBM-WATSON permanece
como endpoint geográfico simulado; ela não oferece acesso a serviços IBM.

| Componente | Alteração |
| --- | --- |
| Adaptadores e execução IBM | Removido `quantum_autopilot.ibm`, com 12 módulos: catálogo, preparação, Bell, executor QPU, ledger, operador, recuperação e readiness |
| Comandos | Removidos `quantum-twin-ibm-bell`, `quantum-twin-ibm-bell-recover` e `quantum-twin-ibm-readiness` |
| Dependências | Removidos extra `ibm` e `qiskit-ibm-runtime`; mantidos Qiskit/Aer local |
| Experimentos e testes exclusivos | Retirados cinco scripts e sete arquivos de teste IBM |
| Documentação | Retirados cinco guias IBM; README, roadmap, backlog, pré-banca e limites atualizados; IBM-2C/3/4 e Q25 cancelados |
| Inventário e evidência | Retirado o diagnóstico do SDK IBM e corrigidos os caminhos de fontes removidas no gerador de evidências |
| Empacotamento | Mantida exclusão do SDK e acrescentada exclusão defensiva do namespace retirado nos dois specs |
| Console | Retirada referência a IBM QPU da lista corrente de itens não testados |

## Preservação

O [arquivo de fontes](../../archives/retired_ibm_quantum_20261007/ibm_quantum_source.zip)
preserva 42 arquivos com seus bytes originais, incluindo `qpu.py` e
`test_ibm_qpu_executor.py` que já possuíam alterações locais antes desta tarefa.
O [manifesto](../../archives/retired_ibm_quantum_20261007/manifest.json) registra
caminhos, tamanhos e SHA-256. CRC e hashes foram conferidos antes da remoção.
O cache antigo foi preservado separadamente em
[build_cache.zip](../../archives/retired_ibm_quantum_20261007/build_cache.zip).
Resultados científicos, recibos e releases anteriores foram preservados.
O arquivo histórico não é importado, instalado ou coletado como teste.

## Validação

| Verificação | Resultado e evidência |
| --- | --- |
| Fonte e instalação editable | 14 verificações aprovadas: namespace não importável, comandos e extra ausentes, dependência IBM ausente e circuito Aer operacional — [relatório](../../outputs/ibm_removal_20261007/source-installation.json) |
| Wheel instalado separadamente | 15 verificações aprovadas; 532 entradas sem módulos IBM, versão 5.49 e simulação Aer operacional — [relatório](../../outputs/ibm_removal_20261007/wheel-installation.json) |
| Coleta da suíte | 5.118 testes coletados sem erros de importação — [registro](../../outputs/ibm_removal_20261007/collection.log) |
| Regressões de física, sandbox, laboratório e console | 241 casos distintos aprovados: 240 na rodada principal e a regressão do login/demo em execução posterior — [XML principal](../../outputs/ibm_removal_20261007/regression.xml), [XML após ajuste](../../outputs/ibm_removal_20261007/regression-shell-updated.xml) |
| Namespace no executável | 7.082 módulos inspecionados; nenhum `quantum_autopilot.ibm` ou `qiskit_ibm_runtime` — [relatório](../../outputs/ibm_removal_20261007/frozen-boundary.json) |
| Validações nativas | Interface 39/39, laboratório 12/12, satélite 21/21, rede global 33/33, estresse de 20 rodadas e seis casos de inicialização aprovados; roadmap, campanha temporal e modelos opcionais aprovados |
| Integridade da entrega | 23 verificações aprovadas: CRC, SHA-256, 3.948 arquivos do ZIP, manifesto, correspondência do executável e das fontes — [evidência final](../../outputs/ibm_removal_20261007/delivery-evidence.json) |

A primeira construção do wheel reutilizou um `build/lib` anterior que ainda
continha 12 módulos IBM. Esse wheel foi rejeitado e identificado com
`DO-NOT-DISTRIBUTE.txt`. O cache foi arquivado e retirado; o wheel entregue foi
construído em diretório novo. A instalação separada reutiliza explicitamente
as dependências da `.audit-winvenv`; ela valida o pacote instalado, sem alegar
um teste em sistema operacional limpo. O circuito Bell local produziu 33
resultados `00` e 31 `11`, com 64 shots e seed 7; nenhum job cloud foi submetido.

A primeira rodada de testes teve 38 erros de preparação por falta de permissão
na pasta temporária geral do Windows. A rodada no diretório temporário do
repositório executou 241 casos e revelou uma expectativa anterior de 66 nós e
71 enlaces no teste do login/demo. O backend já possui 71 nós e 131 enlaces,
como verifica `test_global_geo_constellation.py`. Apenas a expectativa desse
teste foi atualizada; a topologia e seus componentes foram preservados.
O caso corrigido passou em execução separada. Os registros anteriores foram
mantidos para rastreabilidade.

O snapshot de fontes do executável corresponde ao checkout. Comparado com a
última entrega aprovada 5.48, apenas a docstring de `quantum_autopilot`, a
docstring/lista de escopo do estresse e os 12 módulos IBM retirados mudaram.
As demais fontes de runtime permanecem com os mesmos hashes. Os gates nativos
completos executaram uma vez; a chamada final `--acceptance-test
--integrity-only` conferiu somente o manifesto, sem repetir self-test e estresse.
Seu campo `automatic_passed=false` indica o escopo restrito dessa chamada;
`checks.integrity=true` e os relatórios completos comprovam as validações.

## Entrega Windows

Extraia integralmente o
[pacote portátil 5.49](../../release/windows/5.49.0-native-20261007-144400/ORION-Q-5.49.0-native-windows-x64.zip)
e execute `ORION-Q/ORION-Q.exe`, mantendo `_internal` e os demais arquivos da
pasta. Nenhuma versão anterior foi sobrescrita.

| Artefato | SHA-256 |
| --- | --- |
| ZIP Windows — 298.982.935 bytes | `8700a8b6094d272171795fbb409094a72e2971dfb90ab73e6f2763fdbbd89393` |
| Executável Windows | `6d9e4307c7c39e1993b6a066514f86addf479ea9232290873c087ece71e9c9ed` |
| Wheel 5.49 — build limpo | `052d68c89104d365b38eaa6f12b69a194d4e6934aa13bbec5d719567c8690072` |

## Execução

As jornadas locais continuam disponíveis por `orion-q`, `orion-q-lab` e
`python -m quantum_autopilot.console.app`. Para atualizar uma instalação do
checkout, use `python -m pip install -e ".[console,lab,satellite]"`.
O extra IBM e seus comandos não são oferecidos pela versão 5.49.
As contas e credenciais externas não foram acessadas nesta retirada.
Os modelos permanecem opcionais, com atuação física bloqueada.
