# ORION-Q — comparação, diagnóstico e emulação

Incremento de 02/10/2026. Estes recursos implementam os escopos mínimos R1,
R2, R4 e R5 do [roadmap](roadmap_alto_valor_20261002.md). O aceite R3 em
um segundo computador foi adiado pelo usuário. A recomendação usa evidência
histórica de simulação; a emulação do LabOne Q produz aquisições artificiais.

## Comparar controladores — R1/R2

Na tela **FPGA**, abra **POLARISATION LOOP** e selecione **COMPARE CONTROLLERS · REVIEWED
SCENARIOS**. Selecione um dos 36 perfis e pressione **COMPARE CONTROLLERS**.
O resultado contém cenário, QBER, razão referência/modelo, intervalo de confiança,
sementes, teste pareado com correção Holm e limites de validade.

| Estado | Interpretação |
|---|---|
| `BASELINE_RECOMMENDED` | A referência alfa-beta ressintonizada apresentou menor QBER com vantagem significativa |
| `MODEL_RECOMMENDED` | O modelo apresentou menor QBER com vantagem significativa |
| `INCONCLUSIVE` | A comparação não demonstrou vantagem significativa; não prova equivalência |
| `NO_EVIDENCE` | Não existe correspondência exata para cenário e identidade dos pesos |

No ponto calibrado, ridge a 30/60 rad/s recomenda a referência; a 120 rad/s
é inconclusivo; a 250 rad/s recomenda o modelo. As velocidades pertencem
ao atuador simulado e ao protocolo estudado. Não há interpolação para
200 rad/s nem identificação automática de um dispositivo de laboratório.

A consulta mantém o controlador ativo. O laço ativado existente conserva
seu contrato com atuador ideal. O botão **EXPORT COMPARISON** cria uma pasta
nova com JSON, Markdown, catálogo revisado e manifesto SHA256. O catálogo
incluído permite localizar o perfil e conferir os valores exportados.
Uma seleção nova invalida o
resultado anterior. Um pacote local, quando selecionado e verificado no
painel, fornece a identidade dos checkpoints à comparação. Sem esse pacote,
o recibo explicita `historical_identity_only`.

Para o executável portátil, usando uma pasta ainda inexistente fora do programa:

```powershell
$p = Start-Process -FilePath '.\ORION-Q.exe' -ArgumentList '--compare-controllers --profile "qnet.compensation.calibrado.ridge|30" --output "C:\ORION-Q-tests\comparison-30"' -WindowStyle Hidden -PassThru -Wait
$p.ExitCode
```

Na árvore de fontes, com dependências base já instaladas:

```powershell
$env:PYTHONPATH = (Resolve-Path 'src').Path
python -m quantum_autopilot.control_plane.controller_evidence --profile 'qnet.compensation.calibrado.ridge|30' --output 'work/comparison-30'
```

O catálogo é derivado dos resumos fechados e do registro de afirmações.
O leitor verifica um hash fixado no código; o verificador do recibo também
reconstitui os campos a partir desse catálogo. Recalcular somente o hash de
um relatório alterado não torna seus resultados admissíveis. Esses controles
detectam divergência em relação à versão revisada; não são assinatura digital.

## Medir o ciclo do software — R4

O diagnóstico cria uma instância privada de `ControllerRuntime` e um diário
SQLite novo. Mede ingestão, decisão da política, supervisão, execução simulada,
persistência e duração total com relógio monotônico. Não usa o diário do operador.
O estado fixo tem dois pares com fidelidade 0,9 e produz SWAP simulado.
O estágio de decisão inclui o cálculo usado pela política atual; este ensaio
não mede inferência dos modelos de polarização nem uma rede inteira.

```powershell
$p = Start-Process -FilePath '.\ORION-Q.exe' -ArgumentList '--diagnose-runtime --output "C:\ORION-Q-tests\runtime-idle" --samples 1000 --warmup 20 --budget-ms 10 --condition idle' -WindowStyle Hidden -PassThru -Wait
$p.ExitCode
```

Repita em outra pasta com `--condition background_math_thread`. Na árvore
de fontes, a mesma interface está em
`python -m quantum_autopilot.control_plane.runtime_diagnostic`.

`diagnostic.json` informa todas as tentativas, falhas, prazos excedidos,
p50/p95/p99/máximo, ambiente e identidade das fontes. O mínimo declarado para
considerar a amostragem do p99 suficiente é 1.000 ciclos. Os 20 ciclos do
teste de empacotamento verificam funcionamento e ficam explicitamente abaixo
desse mínimo. O aquecimento é declarado e excluído dos percentis.

O orçamento de 10 ms é um alvo exploratório de 100 ciclos/s para identificar
gargalos do software. Ele não deriva da temporização da planta nem é uma
garantia de dispositivo. Um prazo excedido permanece no denominador de
tentativas; o código de saída falha por erro de execução, e os campos do
relatório indicam separadamente cumprimento do orçamento.

`idle` significa ausência de carga imposta pelo diagnóstico. A condição sob
carga adiciona uma thread Python de cálculo contínuo; outros processos do
computador não são controlados. A sobrecarga da instrumentação está incluída
e não foi isolada. Espera de locks e outros custos fora das etapas aparecem
na duração total. No executável, hashes de fontes vêm do manifesto do build,
sem exigir arquivos `.py` ou Git na máquina de destino.

## LabOne Q em ambiente separado — R5

Foi validado Python 3.12.14 em Windows x64, LabOne Q 26.7.0 e xarray 2026.9.0.
O setup é um SHFQC emulado com um qubit, pulso de drive, leitura integrada,
varredura de amplitude e média cíclica. A referência é o
[exemplo oficial da DSL](https://docs.zhinst.com/labone_q_user_manual/core/functionality_and_concepts/10_advanced_topics/tutorials/06_context_based_dsl.html).
A [documentação de sessões](https://docs.zhinst.com/labone_q_user_manual/core/functionality_and_concepts/01_session/concepts/index.html)
explica a aquisição artificial do emulador.

Esta ponte usa CLI/API nas fontes. As dependências LabOne Q não são exigidas
pelo executável base. Use um ambiente separado para evitar conflitos com
NumPy e demais dependências do ORION-Q. O arquivo de requisitos registra o
ambiente Windows/Python 3.12 efetivamente usado, incluindo ferramentas de teste.

```powershell
python -m venv work/laboneq-env
& '.\work\laboneq-env\Scripts\python.exe' -m pip install -r configs/integrations/requirements-laboneq-emulation-py312-win.txt
$env:PYTHONPATH = (Resolve-Path 'src').Path
& '.\work\laboneq-env\Scripts\python.exe' -m quantum_autopilot.integrations.laboneq.emulation --request configs/integrations/laboneq_emulation_v1.json --output work/laboneq-demo
```

O contrato aceita somente o perfil `shfqc_demo_v1` e o experimento
`drive_readout_amplitude`, 1–16 amplitudes finitas em [-1, 1], duração de
drive de 32 ns a 5 µs em múltiplos de 8 ns, médias de 1 a 64 em potência
de dois e orçamento de 1 a 120 s. Campos extras, modos físicos e tipos
não suportados são recusados. Não há opção de conexão física; a sessão
usa `connect(do_emulation=True)` e não consulta opções/ZSync de instrumentos.

O worker roda em subprocesso limitado pelo orçamento. O resultado pode ser
`EMULATED`, `FAILED`, `TIMED_OUT`, `UNAVAILABLE` ou `INCOMPATIBLE`.
O recibo JSON/Markdown e o manifesto registram configuração, versão,
duração, erro quando houver e hashes. No sucesso, o pacote inclui setup,
experimento e experimento compilado; seus hashes constam também do manifesto.

As aquisições têm a categoria `mock_emulated_acquisition` e
`physical_action_performed=false`. Elas não entram nas campanhas de QBER,
fidelidade ou vantagem de controlador. Não há tradução física de PURIFY,
SWAP ou correções SO(3) nesta ponte.

## Aceitação portátil — R3

Siga o [guia Windows](orion_q_windows_acceptance.md) para diagnóstico automático
e roteiro manual. O build verifica três comparações no executável real e
20 ciclos do diagnóstico, além das verificações nativas existentes.
Esses resultados são locais; concluir R3 requer o segundo Windows e avaliação
humana vinculados ao hash do ZIP. O adiamento desse teste não altera os
recibos nem transforma o diagnóstico local em aceite independente.
