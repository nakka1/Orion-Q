# Investigação dos timeouts do console de laboratório — 2026-10-07

A falha foi reproduzida na espera de `tools/render_lab_console.py`. Seu polling chamava `app.processEvents()` e `QTest.qWait(50)` enquanto o worker Python montava o primeiro snapshot. Três execuções completas ultrapassaram o limite de 40 segundos. Ao utilizar o event loop Qt com deadline e o mesmo sinal do provider, quatro execuções concluíram a renderização e exibiram o recibo do backend.

## Método

Cada processo usou `QT_QPA_PLATFORM=offscreen` e um workspace próprio. O recibo inicial é uma fixture pequena identificada como `renderer-startup-receipt-v1`, em modo `emulation`; ela não representa aquisição nem segurança QKD experimental. O console, o `ServiceProvider`, a validação dos artefatos científicos, o journal e a leitura dos recibos são os caminhos reais existentes.

O diagnóstico opcional registra início/fim/duração das chamadas, nomes de threads, PID, publicação do snapshot e screenshot. Registra somente a classe de erros e o tamanho de mensagens do provider; argumentos, valores retornados, credenciais e chaves não são serializados. Amostras de stack conservam arquivo, função e linha, sem variáveis locais.

As stacks dos timeouts situam o worker em `validate_claim_registry`, `_select_rows` e no processamento de DataFrames. A thread de UI aparece na espera `QTest.qWait`. O primeiro build chama `service.summary()` duas vezes: uma para o resumo, outra a partir de `assess_published_cell`, que revalida os artefatos para o exemplo QKD. As duas validações foram preservadas na comparação.

## Resultados

| Execução | Espera | Resultado | Build do provider (s) | Processo diagnosticado (s) |
| --- | --- | --- | ---: | ---: |
| `qwait-1` | Polling anterior | Timeout de snapshot | 40.109 | 47.640 |
| `qwait-3` | Polling anterior | Timeout de snapshot | 40.140 | 47.735 |
| `qwait-4` | Polling anterior | Timeout de snapshot | 40.141 | 47.703 |
| `event-loop-1` | Event loop Qt | Aprovado | 1.922 | 10.094 |
| `event-loop-2` | Event loop Qt | Aprovado | 3.984 | 11.203 |
| `event-loop-3` | Event loop Qt | Aprovado | 2.953 | 10.406 |
| `event-loop-default` | Novo padrão do comando | Aprovado | 2.438 | 10.328 |

No primeiro par, as duas chamadas de `summary` passaram de 16.907/15.906 s para 0.078/0.063 s. Depois de expirar a espera anterior, a finalização do worker ocorreu rapidamente durante `provider.stop()`, que libera a oportunidade de execução da thread Python. Isso explica por que o timeout produzia um falso resultado de inicialização incompleta embora o build acabasse logo após abandonar o polling.

Um ensaio menor, com um contador executado por uma thread Python, também mostrou menor progresso durante `qWait` nas três repetições. Os valores estão em [gil-wait-comparison.json](../../outputs/lab_hardening_20261007/startup/investigation/gil-wait-comparison.json). Esse ensaio mede contenção local; seus números não constituem taxa garantida da aplicação.

Uma execução diagnóstica adicional, `qwait-2`, terminou com exit code 1 sem traceback Python final. O dump nativo `faulthandler` ficou truncado durante uma importação. Sua causa não foi comprovada, seus artefatos foram preservados e ela foi excluída das estatísticas de timeout. O diagnóstico passou a amostrar frames Python sob o GIL, sem o dump assíncrono nativo. `qwait-4` reproduziu novamente o timeout utilizando esse diagnóstico.

## Correção e regressão

O renderizador agora espera em `QEventLoop`, com um `QTimer` de deadline e o sinal `snapshot_changed` já utilizado pelo console. O event loop recebe as atualizações e permite que o worker Python execute. `--wait-mode qwait` mantém a reprodução explícita do comportamento anterior. Não foi introduzido cache científico nem alterada a inicialização de `ServiceProvider`, `app` ou `shell`.

Três testes específicos foram aprovados: cálculo Python e entrega do snapshot na thread da UI, deadline sem snapshot com chegada tardia segura, e diagnóstico sem serialização de conteúdo privado. Evidência: [harness-regression.xml](../../outputs/lab_hardening_20261007/startup/investigation/harness-regression.xml).

O teste independente da inicialização regular também passou: [source-3440.json](../../outputs/lab_hardening_20261007/startup/source-3440.json), primeiro snapshot após 10.797 s, navegação e redimensionamento completos. Essa execução foi offscreen, com tamanho de cliente de diagnóstico 3440×1440; não deve ser apresentada como validação física de um monitor ou como qualificação de um novo executável.

Os screenshots mostram a aba de chaves com os controles do laboratório e o recibo lido do backend. A captura final foi inspecionada: [native_console.png](../../outputs/lab_hardening_20261007/startup/investigation/event-loop-default/state/native_console.png).

## Reprodução

O workspace precisa conter um recibo completo de uma execução local do laboratório, conforme a finalidade desse renderizador. Os workspaces desta investigação conservam suas fixtures identificadas.

```powershell
$env:PYTHONPATH = 'src'
$env:QT_QPA_PLATFORM = 'offscreen'
.audit-winvenv\Scripts\python.exe tools\render_lab_console.py --state outputs\lab_hardening_20261007\startup\investigation\event-loop-default\state --diagnostics outputs\lab_hardening_20261007\startup\investigation\reproduction --timeout 40
.audit-winvenv\Scripts\python.exe -m pytest tests\test_lab_console_startup_harness.py --basetemp outputs\lab_hardening_20261007\startup\investigation\tmp_reproduction -q
```

Matriz completa e hashes dos traces: [comparison.csv](../../outputs/lab_hardening_20261007/startup/investigation/comparison.csv), [verification-summary.json](../../outputs/lab_hardening_20261007/startup/investigation/verification-summary.json).

Os tempos são observações deste ambiente. A investigação não certifica hardware, aquisição real, replay nativo Swabian, comunicações físicas nem um novo `.exe`. A terminação não classificada do dump nativo permanece documentada, sem atribuir ao programa uma causa que não foi reproduzida independentemente.

## Instalação reproduzível do laboratório 5.47.0

O wheel final foi construído a partir de uma cópia dos arquivos do projeto, utilizando `pip wheel --no-deps --no-build-isolation --no-index`; o build e os metadados intermediários ficaram somente em `outputs/lab_hardening_20261007/clean_install/final-r2`. A instalação em um novo venv utilizou `--no-deps`. Os comandos de importação e execução partiram de `C:\Users\alexa`, fora do checkout, com `PYTHONPATH` ausente; o módulo CLI foi chamado com `python -I`. A versão da distribuição instalada, obtida de `importlib.metadata`, é **5.47.0**, e os módulos foram importados de `final-r2/venv/Lib/site-packages`.

Esse venv utiliza `--system-site-packages`: NumPy **2.3.5** e cffi **2.1.1** são dependências emprestadas do runtime base. Foram instalados apenas no novo venv, a partir do PyPI, cryptography **46.0.7** e SciPy **1.18.1**. O wheel final declara o extra `lab = ["cryptography>=43,<51"]`; a versão local está dentro desse intervalo. Não se trata de um sistema operacional limpo nem de uma instalação completa das dependências do desktop. Os caminhos e versões efetivamente importados estão no [relatório final](../../outputs/lab_hardening_20261007/clean_install/final-r2/verification-summary.json).

### Ausência de dependência e bloqueio antes da execução

O primeiro teste QKD nesse ambiente reproduziu a ausência de SciPy: a importação de `scipy.signal` falhou e a operação foi encerrada como `failed`. SciPy já era uma dependência declarada do projeto, mas não estava presente no ambiente criado com instalação `--no-deps`. O recibo e o [stderr da reprodução](../../outputs/lab_hardening_20261007/clean_install/final-r2/installed-module-qkd.stderr.txt) foram preservados.

Após a correção da descoberta de dependências, o wheel foi reconstruído com os arquivos finais do projeto e testado **antes** da instalação de SciPy. A configuração do laboratório passou a indicar `qkd` e `all` indisponíveis, com `missing_dependencies = ["scipy"]`, mantendo `photon` disponível. `LaboratoryService.run` recusou a solicitação com `laboratory_dependencies_unavailable:scipy`, sem criar workspace, arquivo de exclusão, SQLite ou recibos. Evidência: [missing-scipy-gate-final.stdout.txt](../../outputs/lab_hardening_20261007/clean_install/final-r2/missing-scipy-gate-final.stdout.txt). Depois da instalação local da dependência, o fluxo QKD completo foi executado.

### Fluxos executados

| Caso | Contexto | Resultado | Tempo de processo (s) | Evidência |
| --- | --- | --- | ---: | --- |
| Importação da distribuição final | Fora do checkout, `-I`, sem `PYTHONPATH` | 5.47.0 importado do novo `site-packages` | 0.375 | [installed-import-final.stdout.txt](../../outputs/lab_hardening_20261007/clean_install/final-r2/installed-import-final.stdout.txt) |
| CLI de fótons como módulo | Wheel instalado, sem `PYTHONPATH` | Recibo `completed`; aquisição e captura contínua aprovadas | 7.344 | [installed-module-photon.stdout.txt](../../outputs/lab_hardening_20261007/clean_install/final-r2/installed-module-photon.stdout.txt) |
| Entrypoint `orion-q-lab.exe` | Entrypoint instalado pelo wheel | Recibo `completed`; aquisição e captura contínua aprovadas | 9.453 | [installed-entrypoint-photon.stdout.txt](../../outputs/lab_hardening_20261007/clean_install/final-r2/installed-entrypoint-photon.stdout.txt) |
| CLI QKD como módulo | Wheel final, SciPy/cryptography locais | 12 verificações e 14 verificações de falhas aprovadas; 4 processos independentes | 36.625 | [installed-module-qkd-after-dependencies.stdout.txt](../../outputs/lab_hardening_20261007/clean_install/final-r2/installed-module-qkd-after-dependencies.stdout.txt) |
| Bootstrap `tools/run_local_orion.ps1 -Mode lab` | Fonte do checkout, comando iniciado fora dele | Recibo `completed`; `PYTHONPATH` restaurado ao valor ausente | 8.156 | [source-wrapper-environment.json](../../outputs/lab_hardening_20261007/clean_install/final-r2/source-wrapper-environment.json) |

Os dois ensaios de fótons do wheel foram executados antes da última reconstrução, cuja diferença foi a descoberta de SciPy em `configuration.py` e o limite do extra em `pyproject.toml`. Os arquivos da aquisição de fótons permaneceram idênticos e foram vinculados ao wheel final por SHA-256. Cada ensaio nominal produziu 1.445 e 1.409 detecções nos canais 1 e 2, com 517 coincidências; esses números são resultados do cenário emulado, não especificações de equipamento.

O ensaio QKD executou dois KMEs e duas aplicações em processos distintos, com armazenamentos próprios, HTTP/mTLS local, correspondência entre identificadores e material, AES-GCM, recusa de identidades e certificados inválidos, concorrência sem atribuição duplicada, exaustão e reinício. A campanha adicional verificou falhas nas reservas e commits das duas pontas, perda parcial de resposta, particionamento, atraso e morte de processo, preservando a invalidação e impedindo reutilização. O QBER observado foi 0.010244735344336939. Os dados quânticos de entrada são emulados e usam uma fonte pseudoaleatória pública: **`certified_secret_bits = 0`**. A aprovação desses fluxos não certifica geração física de chaves secretas.

Foram copiados 540 arquivos de entrada, incluindo README e pyproject; os 538 arquivos de `src` instalados foram comparados por SHA-256 com a cópia e a fonte atual, incluindo o formulário final, a descoberta de dependências e o cliente de efemérides. Evidência: [source-snapshot-final.json](../../outputs/lab_hardening_20261007/clean_install/final-r2/source-snapshot-final.json) e [post-install-source-binding.json](../../outputs/lab_hardening_20261007/clean_install/final-r2/post-install-source-binding.json). O wheel final tem SHA-256 `04584b0d02ed14706d7f85cd356bf66deb542b679358b3131a930535d320217b`; seu [METADATA](../../outputs/lab_hardening_20261007/clean_install/final-r2/wheel-final-metadata.txt) e todos os [comandos, durações e saídas](../../outputs/lab_hardening_20261007/clean_install/final-r2/commands.json) foram preservados.

A etapa anterior `final-v547`, que herdou cryptography 50.0.1, conserva sua prova inicial restrita ao CLI de fótons. `final-r2` é a prova final dos fluxos de fótons e QKD com cryptography 46.0.7 local. O programa `orion-q-lab.exe` nessa tabela é o entrypoint do ambiente Python instalado; ele não é o executável desktop empacotado `ORION-Q.exe`.

### Limitações e comandos de consulta

O dataset agregado Bristol fica fora do wheel. Por isso os dois ensaios instalados informam `experimental_aggregate_replay_validated = false`, enquanto o bootstrap da fonte, que encontra o dataset do checkout, informa `true`. A emulação de captura funciona em ambos. A disponibilidade desse dataset externo é uma dependência explícita; não foi substituída por dados inventados.

Esta validação não cobre instalação limpa do desktop, SDK/licença Swabian, replay FileWriter nativo, aquisição de uma unidade física nem o novo executável desktop. O download público de dependências foi o único acesso externo necessário; os diálogos do ensaio QKD utilizaram localhost. Nenhum comando de instalação foi dirigido ao ambiente `.audit-winvenv` ou ao runtime global.

```powershell
# Os comandos abaixo reutilizam o ambiente de validação já provisionado.
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
$labInstalledPython = (Resolve-Path 'outputs\lab_hardening_20261007\clean_install\final-r2\venv\Scripts\python.exe').Path
& $labInstalledPython -I -c "import importlib.metadata; print(importlib.metadata.version('quantum-twin'))"
& $labInstalledPython -I -m quantum_autopilot.lab.cli --experiment photon --output outputs\lab_hardening_20261007\clean_install\new-photon-reproduction
& $labInstalledPython -I -m quantum_autopilot.lab.cli --experiment qkd --output outputs\lab_hardening_20261007\clean_install\new-qkd-reproduction
```

As etapas de criação/instalação e a reprodução da dependência ausente estão em [validate_installation.py](../../outputs/lab_hardening_20261007/clean_install/final-r2/validate_installation.py) e [finalize_installation.py](../../outputs/lab_hardening_20261007/clean_install/final-r2/finalize_installation.py). Seus diretórios de stage são exclusivos; os scripts preservam as etapas anteriores e exigem diretórios novos ao reconstruir uma campanha.
