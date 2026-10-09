# ORION-Q — travamento na abertura em 3440 × 1440

Data: 06/10/2026. Escopo: manutenção da interface Windows e da validação de
distribuição. As alterações recebidas, os diários do operador, os modelos
opcionais e o bloqueio de atuação física foram preservados.

## Causa e correção

O executável anterior, `5.38.0-native-20261006-062158`, foi aberto no monitor
U34G4C de 3440 × 1440, com um diário isolado. O Windows passou a informar que
ele não respondia. Os registros dessa reprodução estão em
`outputs/startup_ultrawide_20261006_v1/old-exe-default-observations.json`.
O campo `second` desse arquivo identifica a observação sequencial; a consulta
de resposta do Windows também pode consumir tempo, portanto não representa
uma cronometragem exata do processo.

Na inicialização normal a janela usa 90% da área disponível: 3096 × 1253,
incluindo os 34 pixels da barra personalizada. O corpo de 3096 × 1219 entrava
em um ciclo: o cálculo do zoom usava o viewport, cuja dimensão mudava quando
as barras de rolagem apareciam. A divisão em ponto flutuante podia produzir
`900.0000000000001`; o arredondamento seguinte criava 901 pixels lógicos.
As barras apareciam, o viewport diminuía para 3088 × 1211, o zoom era
recalculado e as barras desapareciam. O ciclo reaparecia na próxima atualização.
O ensaio com a moldura nativa restaurada também sofreu o problema.

`ResponsiveStage` agora calcula o zoom a partir da área total do cliente,
resolve o espaço das barras antes de redimensionar e evita arredondar ruído
numérico para um pixel adicional. Alterações de tamanho, retângulo de cena e
transformação só são aplicadas quando seus valores mudam. A rolagem continua
disponível nas janelas compactas. A janela sem moldura e seus controles foram
mantidos.

A coleta cíclica no thread da interface passou a começar antes da construção
dos widgets e a ser encerrada também quando a inicialização falha. Um ensaio
instrumentado anterior com demonstração sofreu uma violação de acesso; sua
causa específica não foi comprovada. Esse registro foi mantido, e não é tratado
como aprovação ou como prova de que a coleta explicava aquela ocorrência.

## Evidência de estabilidade

No ensaio comparável com moldura, os eventos de redimensionamento caíram de
203.254 para 1.727; as chamadas periódicas da interface passaram de 6 para 353.
O maior intervalo observado caiu de 3.368 ms para 240 ms. A contagem total
inclui os widgets construídos na abertura e não equivale a quadros por segundo.
As reproduções antes da correção sem moldura também registraram o ciclo,
inclusive 214.160 eventos acumulados em um registro intermediário.

Uma cópia consistente, feita por leitura do SQLite, do diário existente do
operador abriu e chegou à seleção de fonte com 1.727 eventos de tamanho, sem
o ciclo. Os três diários originais permaneceram idênticos em bytes.

O novo `--startup-test` usa a própria entrada normal do desktop e `app.run`:
constrói a janela, inicia o provider, recebe os snapshots, abre o workspace pelos
controles da interface e verifica navegação e layout. Seu diário é novo; um
`--state-root` já preenchido é recusado. Ele não preenche estados ou medições
diretamente. O modo de demonstração usa o provider normal com fonte simulada.

Resultados do executável final, com Qt Windows, PATH restrito ao Windows e sem
Python ou plugins Qt externos:

| Fonte e escala | Chamadas periódicas | Maior intervalo | P95 do intervalo | Redimensionamentos do canvas | Resultado |
|---|---:|---:|---:|---:|---|
| Serviço, 100% | 729 | 422 ms | 32 ms | 5 | Aprovado |
| Serviço, 125% | 742 | 188 ms | 32 ms | 4 | Aprovado |
| Serviço, 150% | 738 | 235 ms | 32 ms | 4 | Aprovado |
| Serviço, 200% | 746 | 250 ms | 32 ms | 4 | Aprovado |
| Demonstração, 100% | 663 | 312 ms | 32 ms | 4 | Aprovado |
| Demonstração, 150% | 669 | 313 ms | 32 ms | 4 | Aprovado |

São intervalos entre callbacks de um timer de 20 ms, medidos durante 16 segundos
após mostrar a janela, incluindo aplicação de snapshots e operações de layout.
Não incluem toda a carga inicial de DLLs/importações do processo. O primeiro
snapshot chegou entre 5,23 e 5,61 segundos após mostrar a janela; a tela de
carregamento continuou atendendo aos eventos. A maior pausa observada não é
uma garantia de latência máxima para qualquer equipamento ou carga.

## Casos de uso

| Caso | Cenário e critério | Resultado | Evidência |
|---|---|---|---|
| 01 | Abrir o executável anterior no monitor nativo | Travamento reproduzido | `old-exe-default-observations.json` |
| 02 | Abertura normal anterior com moldura restaurada | Ciclo reproduzido; 203.254 eventos | `before-framed-native/report.json` |
| 03 | Repetir a abertura com a correção | Ciclo eliminado | `after-v1-framed-native/report.json` |
| 04 | Abrir com cópia do diário existente | Aprovado; original preservado | `operator-journal-copy/report.json` |
| 05 | Abrir o `.exe` final em 100% | Aprovado | `startup-validation/service-100/report.json` |
| 06 | Abrir o `.exe` final em 125% | Aprovado | `startup-validation/service-125/report.json` |
| 07 | Abrir o `.exe` final em 150% | Aprovado | `startup-validation/service-150/report.json` |
| 08 | Abrir o `.exe` final em 200% | Aprovado | `startup-validation/service-200/report.json` |
| 09 | Abrir a demonstração pelo mesmo fluxo, em 100% | Aprovado | `startup-validation/demo-100/report.json` |
| 10 | Abrir a demonstração pelo mesmo fluxo, em 150% | Aprovado | `startup-validation/demo-150/report.json` |
| 11 | Selecionar a fonte e acionar Open workspace | Aprovado nas seis aberturas | Caso `open-workspace` dos relatórios |
| 12 | Abrir Topology | Aprovado nas seis aberturas | Caso `page-topology` |
| 13 | Abrir Fidelity / telemetry | Aprovado nas seis aberturas | Caso `page-telemetry` |
| 14 | Abrir Key management | Aprovado nas seis aberturas | Caso `page-keys` |
| 15 | Abrir Nodes | Aprovado nas seis aberturas | Caso `page-node` |
| 16 | Abrir Edge control | Aprovado nas seis aberturas | Caso `page-fpga` |
| 17 | Abrir Access / audit | Aprovado nas seis aberturas | Caso `page-audit` |
| 18 | Reduzir para 320 × 240 e manter rolagem | Aprovado | Caso `compact-layout`; regressão de rolagem |
| 19 | Recuperar o tamanho amplo; testar 3440 × 1440 em 100% | Aprovado | Caso `ultrawide-layout` |
| 20 | Maximizar pelo botão da janela | Aprovado nas seis aberturas | Caso `maximize` |
| 21 | Restaurar pelo botão da janela | Aprovado nas seis aberturas | Caso `restore` |
| 22 | Fechar e reabrir o terminal | Aprovado nas seis aberturas | Casos `terminal-close` e `terminal-open` |
| 23 | Restaurar o formato inicial | Aprovado nas seis aberturas | Caso `original-layout` |
| 24 | Manter o canvas estável em sete dimensões fracionárias | Aprovado | `test_console_startup_stability.py` |
| 25 | Falhar na inicialização e restaurar a coleta de memória | Aprovado nas oito variações | `test_console_startup_stability.py` |
| 26 | Arrastar as oito bordas/cantos, liberar e continuar usando a interface | Aprovado com eventos Qt em 150% e 200% | `native-150-v2.xml`, `native-200-v2.xml` |
| 27 | Perder foco/captura ou a liberação de um arraste | Cancelamento preservado | `test_console_resize_regression.py` |
| 28 | Rolagem de textos, janelas de nodes e atualização de gráficos | Aprovado no escopo da regressão | XML dos módulos correspondentes |
| 29 | Canais associados obrigatórios na configuração | Aprovado; fluxo anterior preservado | `pytest-ui-channel_configuration.xml` |
| 30 | Gerar ZIP somente com os seis cenários de abertura aprovados para o mesmo `.exe` | Aprovado | Gate de empacotamento e `startup-validation/report.json` |
| 31 | Controladores, worker temporal, modelos opcionais desligados e autoteste | Aprovado | Gates da distribuição |
| 32 | Integridade do ZIP e aceite automático local | Aprovado | `portable-package.json`, `native-acceptance/acceptance-report.json` |

Os arquivos sem prefixo de distribuição estão em
`outputs/startup_ultrawide_20261006_v1`. Os diretórios `startup-validation`,
`roadmap-validation`, `campaign-validation` e `native-acceptance` estão na
distribuição final. A tabela resume cenários; não é a contagem de testes unitários.
Foram aprovados **374 casos distintos**, em **574 invocações**, dos módulos
afetados. Isso não representa uma execução nova de toda a suíte científica.

Uma primeira verificação de gesto Qt em 150% falhou. O registro instrumentado
mostrou `WindowDeactivate` interrompendo um gesto artificial sem captura física.
O diagnóstico agora isola essa interferência somente durante seus gestos
sintéticos; não modifica o controle de janela usado pelo operador nem bloqueia
o cancelamento de uma captura real. Os critérios de geometria continuam exatos.
Em 200%, uma expectativa inicial de altura fixa no teste foi corrigida para
verificar a cobertura do viewport efetivamente permitido pelo Windows.
Os ensaios anteriores e os XML com falhas foram preservados.

## Distribuição e limites

Distribuição: `release/windows/5.38.0-native-20261006-075149`.

- Executável: `ORION-Q/ORION-Q.exe`.
- Portátil: `ORION-Q-5.38.0-native-windows-x64.zip`, 293.808.265 bytes, 3.899 arquivos.
- SHA-256 do `.exe`: `a123fbeed3ba98db5813aed5ac4ee6a1979de8c7b8e3ca1cde189f019c716c33`.
- SHA-256 do ZIP: `51adb0d8078357929d74601f22437186a8b66ac32284ed755698b23b7fd30f64`.

Extraia o ZIP inteiro; `_internal` deve acompanhar o executável. A distribuição
anterior foi preservada. Dos 1.005 arquivos recebidos capturados no início,
todos foram mantidos e 994 permaneceram idênticos em bytes; os 11 alterados têm
sua versão anterior guardada em `before/`. Os novos arquivos de diagnóstico e
testes complementam essa base.

Os testes usam eventos dos widgets Qt, não injeção de movimento do mouse no
Windows. Arraste físico com o mouse e aceite em outro computador continuam
pendentes. O ganho medido corrige o ciclo de abertura; pausas de reconstrução
de tema anteriormente documentadas continuam sendo um limite separado.
Modelos continuam opcionais. Canais locais transportam observações e registros
ópticos estatísticos; arquivos de aquisição são replay. Não há transmissão de
qubits ou atuação em equipamentos, nem promoção de evidência científica.
