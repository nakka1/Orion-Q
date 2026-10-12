# OrionQ 5.66.0 — correções e qualificação Windows/Linux

Data: 11 de outubro de 2026. Compilação e aceitação automática de software
concluídas para Windows e Linux no escopo descrito abaixo. Concorrência,
validações científicas e cortes de layout foram corrigidos; a melhoria de
responsividade é parcial, com pausas residuais e regressão medida no mapa.
A versão anterior, as alterações existentes, os cadastros e o Git original
permanecem preservados. Fontes e configurações de produção estão vinculadas
ao [manifesto corrigido de 679 arquivos](../../outputs/release-5.66.0-20261011/windows/source-freeze-corrected-r2.json).

## Pendências corrigidas

| Pendência | Causa e correção | Evidência de regressão |
|---|---|---|
| Dois engines consumiam o mesmo par | O snapshot era substituído sem comparar o par persistido. Consumo, purificação, reposição e reconciliação agora verificam par, demanda e gerações de memória sob a transação SQLite. Um perdedor recebe `PairConflict`. | Dois processos leem o mesmo par antes da barreira: exatamente um consumo. Redefinir os extremos da demanda também rejeita o snapshot antigo. |
| Capacidade física duplicada entre bancos | `node_id` identifica o cenário, não o instrumento. Recursos explicitamente vinculados exigem autoridade local compartilhada por namespace/serial/endpoint. Rollback libera apenas os novos claims; queda exige nova sessão verificada. | Bancos separados, identidade comum, revogação, queda abrupta e 38 pedidos simultâneos: capacidade um produz um vencedor e 37 recusas, sem deadlock. |
| Reserva confirmada, auditoria falhava | O commit e a confirmação de auditoria não tinham recuperação explícita. Recibo e outbox agora compartilham a transação; falha posterior retorna HTTP 503 com `committed=true`, ID e hash. | Retry com o mesmo ID não duplica a reserva. Reinício invalida a reserva e reconcilia o hash original. Conflito de autoridade retorna HTTP 409. |
| Validações científicas desapareciam no EXE | `assert` era removido por `optimize=1`. As pré-condições agora lançam exceções explícitas, inclusive para NaN/Inf, booleanos, probabilidades, tempos, limites T1/T2 e parâmetros inválidos no ramo de perda óptica. | 226 testes aprovados; 113 verificações em cada perfil normal, `-O` e `-OO`. O diagnóstico também é obrigatório no EXE/ELF final. |
| Títulos e células cortados | A rolagem do host escondia a barra; colunas sem quebra e um scroll externo faziam parte do viewport da tabela ficar fora da janela. | Refitting coalescido, foco no botão de fechar, colunas proporcionais, delegate de texto completo e scroll próprio dos formulários. Campanha visual corrigida: 136 casos Windows, 114 Linux e oito fluxos nativos Windows. |
| Janelas deslocadas ao trocar o tema | A restauração limitava a posição salva contra um viewport oculto e provisório de 632 × 251 px. A posição válida (422, 35) virava (84, 0) antes de o layout real aparecer. | A posição fica pendente até o host visível receber o refit; é aplicada uma vez e removida ao fechar. O teste oficial original passou; três novos casos cobrem restauração, limites e fechar/reabrir. O limite inferior inclui o offset da barra, evitando corte de um pixel. |
| Trabalho excessivo ao apresentar detalhes | Campos eram criados sem proprietário/fonte final, provocando nova aplicação de estilos e layouts. | Construção no proprietário final e definição da fonte antes do primeiro layout. As linhas existentes continuam sendo reutilizadas durante os ticks. |

Os contratos e limites de concorrência estão em
[orionq_controller_concurrency.md](../product/orionq_controller_concurrency.md),
com [resultados Windows/Linux](../../outputs/release-5.66.0-20261011/concurrency/summary.json).
O corpus final focado tem 16 casos aprovados por SO. A união sem duplicar os
corpora anteriores contém 187 aprovados/um skip no Windows e 121 aprovados no
Linux; o resumo distingue os corpora anteriores à última tradução HTTP 409.
Na regressão Linux corrigida, incluindo os suplementos de auditoria e build,
o resultado resolvido pela última execução de cada identidade é 1.762 casos únicos
aprovados e um skip do golden de
dataset reservado ao Windows. Uma fixture inicial não abriu seu socket
Unix porque o caminho tinha 111 bytes; repetida com caminho de 82 bytes,
passou sem alteração no teste ou backend. A falha inicial continua no
JUnit histórico e não é contada como aprovação.

As [validações científicas](orionq_scientific_validation_5_66_0_20261011.md)
preservam equações, sequência aleatória e goldens nominais. A tolerância
de `1e-7` do diagnóstico no limite totalmente despolarizado cobre o erro
numérico do estimador matricial Qiskit; não altera o modelo físico.

As [correções visuais](orionq_visual_fixes_5_66_0_20261011.md) incluem
leitura topo/fundo/topo sem hover ou seleção de texto. A campanha corrigida
aprovou 136 casos Windows e 114 casos Linux, além de oito fluxos nativos
Windows. A inspeção final exige
que o viewport da tabela realmente caiba na janela, além de conferir os
dados do modelo. Windows nativo a 200% também passou quatro fluxos, com
monitor físico 3440 × 1440, geometria lógica 1720 × 720 e área útil
1720 × 696. O harness pede explicitamente uma janela de 1280 × 720 após
o ajuste inicial, excedendo a altura útil nesse DPI. Isso não qualifica a
barra inferior que fica fora do monitor; título, painel de 640 × 480 e
seleções foram verificados. O launcher normal ajusta a janela à área útil
e seu mínimo é 320 × 240. Os fluxos nativos adicionais usam um harness local
privado; os testes de geometria e texto de componentes são exportados,
mas esse harness adicional não constitui ferramenta portátil publicada.
No Linux, 114 casos únicos de widgets e layout passaram sem skips, com
Qt 6.12.0, UID 10001 e fontes verificadas. Essa campanha offscreen não
substitui a execução do ELF na sessão Wayland/WSLg. O ELF final passou
15 verificações de inicialização em Wayland e XWayland, seis contratos
funcionais da apresentação e comunicação GUI/core autenticada. As seis
capturas nativas do pipeline ficaram pretas e são explicitamente
`NOT_QUALIFIED`: não comprovam a pintura da tela. A evidência visual
offscreen tem escopo distinto. O terminal passou 185 verificações e
1.273 entradas de teclado; o modo E.V.A. passou outras 77 verificações.
Os resultados, hashes e dependências estão no
[relatório Linux corrigido](orionq_linux_release_5_66_0_corrected_r2_20261011.md).

A fixture de recalibração também aguardava apenas a conclusão do backend.
Um ensaio capturou `ready=false`, três itens de apresentação pendentes e o
aviso de tick anterior, enquanto o contador interno já refletia o resultado.
Depois da espera normal dos timers Qt, os campos completos corresponderam
ao recibo. O teste agora usa a espera de apresentação já existente antes de
ler o relatório, mantendo todas as verificações de valores e ligações;
nenhuma construção de campos é forçada. As falhas anteriores permanecem
no [resumo visual](../../outputs/release-5.66.0-20261011/visual/summary-corrected-r2.json).

## Desempenho: medidas e limites

Ambiente Windows: Ryzen 7 5700X, oito cores/16 threads, aproximadamente
32 GiB, RTX 3060, Windows 11 Pro 26200 e Qt 6.11.2. O cenário usa 76 nodes,
136 enlaces, 11 satélites, 38 sessões tecnológicas e 78 demandas globais,
telemetria ativa, seleção de pontos, navegação e troca Mapa/Globo. A fonte
simulada percorre o backend normal; nenhuma atuação física é realizada.

O [benchmark reproduzível de layout](../../tools/benchmark_console_kv_layout.py)
faz 40 medições por variante, após três aquecimentos, com 24 campos, mesmos
valores e fontes. No [ensaio final](../../outputs/release-5.66.0-20261011/performance/kv-layout-final.json),
criar no proprietário final reduziu a mediana de 26,18 para 19,37 ms e o p95
de 28,73 para 20,78 ms. Esse ganho local não certifica a latência global.

O profiling localizado encontrou máximo de 96,49 → 6,17 ms em
`KVList.set_rows` e 199,87 → 15,02 ms em `FibreCard.show_link`. São ensaios
diagnósticos, com executável anterior e fontes atuais; número de chamadas,
runtime e duração diferem. Eles identificam a remoção do custo de layout,
sem substituir a comparação dos executáveis finais sob a mesma carga.
A coleta cíclica CPython de geração 2 ainda apresentou custo observável.
Ela ocorre a cada 60 chamadas do timer, aproximadamente um minuto, na
thread Qt para preservar o ciclo de vida seguro dos objetos PySide.
Essa pausa residual será distinguida das atualizações de dados.

A primeira comparação EXE anterior/fontes atuais foi mista: detalhes de
satélite ficaram mais rápidos, mas alguns picos no mapa aumentaram. Ela
permanece registrada em `performance/after-source-native`; não é usada para
afirmar ganho global. Um profiling completo inicial foi descartado como
aceitação por erro do próprio wrapper de sinal Qt, corrigido e coberto por
regressão. Tentativas falhas são preservadas, nunca contadas como PASS.

A comparação final de executáveis usa `before-native` (5.65.0) e
`after-native` (5.66.0), oito segundos por fase, sem profiling ou alteração
do intervalo de threads. O plano de ações, runtime Qt, monitor, DPR e carga
de componentes são idênticos; ambas as execuções completaram seis leituras
integrais dos detalhes, sem erro funcional. As ações são eventos Qt reais
no harness, não um ensaio independente de mouse físico.

| Medida p95, milissegundos | EXE anterior | EXE final |
|---|---:|---:|
| Heartbeat da UI, mapa | 59,47 | 94,64 |
| Heartbeat da UI, globo | 74,01 | 63,34 |
| Heartbeat da UI, abas | 53,42 | 46,54 |
| Heartbeat da UI, alternâncias | 59,67 | 44,42 |
| Pintura do palco, abas | 69,86 | 25,04 |
| Todos os campos de detalhes conferidos, mapa | 1.454,12 | 2.649,43 |
| Todos os campos de detalhes conferidos, globo | 1.971,59 | 1.278,65 |

Há ganho no globo, nas abas e nas alternâncias, mas regressão no mapa.
A latência de detalhes tem somente três observações por fase e seu p95
equivale ao máximo observado. As lacunas de heartbeat acima de 250 ms
passaram de uma para três; o máximo passou de 258,32 para 335,20 ms.
Uma execução curta de cada versão não demonstra significância estatística
nem atribui causalidade dos picos ao GC. A ausência de erros de conteúdo
não comprova eliminação das pausas ou aprovação do limite de um segundo
para detalhes. O ganho local de layout é demonstrado; a responsividade
global permanece parcialmente resolvida.

A [consolidação das medições](../../outputs/release-5.66.0-20261011/performance/comparison.json)
vincula hashes de todos os CSVs, JSONs, executáveis e parâmetros; confere
PID do processo real, plano concluído, ausência de timeout, igualdade das
condições disponíveis e fontes intactas. CPU/GPU do host podem incluir
outras aplicações. A GPU é contexto compartilhado, não prova de aceleração
atribuível ao OrionQ. Não houve mudança de linguagem nesta entrega.

O ensaio adicional `after-soak` completou **300,07 segundos** de continuidade,
150 ações planejadas nessa fase e, incluindo as fases anteriores, 81 leituras
completas e 80 mudanças efetivas Mapa/Globo, sem divergência de conteúdo ou
seleção. As 571 amostras de componentes da continuidade mostraram no máximo
uma instância e um renderizador ativo; isso é amostragem aproximadamente a
cada 0,5 s, não prova de cada frame. Três amostras registraram aposentadoria
temporária do renderizador, preservando o estado compartilhado.

| Medida na fase contínua | Resultado |
|---|---:|
| Heartbeat p95 / máximo | 51,97 / 808,91 ms |
| Pintura do palco p95 / máximo | 51,22 / 346,04 ms |
| Campos completos p95, 75 leituras | 2.427,82 ms |
| Pausas de heartbeat de pelo menos 250 ms | 36 |
| RSS da UI, mediana / máximo, 576 amostras | 603,82 / 616,50 MiB |
| RSS da árvore de processos, máximo | 720,57 MiB |
| CPU da UI p95 / árvore p95, 100% = um CPU lógico | 121,1% / 148,4% |
| Medianas RSS da UI, primeiros/últimos 20 s distintos | 588,25 / 606,31 MiB |
| Tendência RSS nos últimos 80% da fase | +0,79 MiB/min |

Não houve travamento definitivo ou fila de seleções pendente ao encerrar.
Houve crescimento de memória nessa janela e pausas perceptíveis; cinco
minutos não permitem certificar ausência de vazamento prolongado. O backend
registrou 63 passos acima do período solicitado de um segundo, último passo
de 5.307,40 ms, 180 eventos de protocolo pendentes sem backlog temporal
declarado e nenhum salto clássico pendente. O relógio científico chegou a
63 s; isso não representa simulação à velocidade 1× do relógio de parede.
A apresentação coalesceu polls/seleções sem substituir resultados científicos.
O gate G05 continua aberto, incluindo duração/repetições prospectivas,
limite de latência, memória prolongada e a investigação adicional dos picos
do mapa. Não atribuímos esses picos especificamente ao GC sem perfil
sincronizado. A entrada do workspace aparece perto de 20 s após mostrar a
janela porque o harness aguarda deliberadamente cerca de 14 s antes do
pedido; esse valor não é o tempo puro de inicialização do programa.

Reprodução a partir deste repositório, com cada saída em diretório novo:

```powershell
.audit-winvenv/Scripts/python.exe tools/benchmark_console_kv_layout.py --output outputs/performance-new-layout.json
.audit-winvenv/Scripts/python.exe tools/benchmark_console_performance.py --output outputs/performance-new-short --duration 8 --soak 0 --size 3440x1440 --executable release/windows/5.66.0-native-20261011-131559-release/OrionQ/OrionQ.exe
.audit-winvenv/Scripts/python.exe tools/benchmark_console_performance.py --output outputs/performance-new-soak --duration 8 --soak 5 --size 3440x1440 --executable release/windows/5.66.0-native-20261011-131559-release/OrionQ/OrionQ.exe
```

Use o Python do ambiente de validação preparado, ou um ambiente equivalente
com as dependências declaradas. O benchmark não reutiliza workspace de
operador. Execute as campanhas sequencialmente, sem outro ensaio pesado.

A [primeira campanha oficial Windows](../../outputs/release-5.66.0-20261011/windows/attempt1-failed-summary.json)
terminou com 20 grupos aprovados e um grupo reprovado pela restauração do tema.
A tentativa foi bloqueada antes da compilação. Seus 21 JUnits e recibo FAIL
permanecem preservados. A correção altera apenas `node_window.py` em relação
ao primeiro manifesto; uma nova campanha completa usa uma pasta distinta.

A [segunda tentativa](../../outputs/release-5.66.0-20261011/windows/attempt2-failed-summary.json)
foi bloqueada por três timeouts da fixture de leitura de comandos, com 14
grupos aprovados e um reprovado. O timer de publicação permanecia ativo,
mas `QTest.qWait(20)` atrasava o worker no Windows ao reter o GIL.
Mantendo os mesmos casos, prazo de oito segundos e verificações, a espera
`processEvents()` seguida de `time.sleep(0.02)` permitiu a execução natural
do worker: os três casos passaram, e a fronteira da consulta caiu de
7,75–8,06 segundos para cerca de 38–39 ms. A alteração afeta apenas a
fixture; o corpus original completo passou 42/42 casos em 43,43 s.
As fontes de produção continuam vinculadas ao mesmo manifesto de 679
arquivos.

A [terceira tentativa](../../outputs/release-5.66.0-20261011/windows/attempt3-aborted-summary.json)
foi interrompida por uma mudança exclusivamente na referência Git de um
manifesto gerado durante a preparação. Três grupos estavam aprovados, com
482 casos aprovados e 19 skips; o grupo interrompido é inconclusivo. Os oito
hashes de código referenciados eram idênticos. Novos commits no Git original
foram preservados, sem restauração de refs ou index. O gerador agora valida
o contrato completo e preserva bytes e timestamps quando as fontes são
iguais; uma mudança real de fonte ou manifesto inválido exige regeneração.
A referência Git informa a criação desse manifesto parcial; o manifesto de
679 arquivos qualifica o conjunto de produção. Os 19 testes da ferramenta
e do diagnóstico passaram; a regressão nova também integra o gate oficial.
A quarta tentativa executou novamente o script oficial inteiro, sem herdar
JUnits: os 30 corpora contêm 4.659 execuções e 4.582 casos únicos,
sendo 4.559 aprovados e 23 skips identificados, sem falhas ou erros.
Os skips exigem o ambiente isolado LabOne Q, o ZIP QKD fixado informado por
`ORION_QKD_ARCHIVE` ou credenciais/permissões próprias do kernel Linux.
Os hashes das 679 fontes de produção, dos 429 arquivos de testes/configuração
e de todas as ferramentas ficaram idênticos antes e depois do script.
O [recibo oficial](../../outputs/release-5.66.0-20261011/windows/corrected-r4/official-build-worker-receipt.json)
e a [comparação posterior](../../outputs/release-5.66.0-20261011/windows/corrected-r4/test-inputs-after.json)
registram esse escopo.

O build e todos os gates nativos passaram, mas sua primeira consolidação
foi recusada pelo leitor de evidências. Ele confundia `sys.flags.optimize`,
que vale zero no bootloader do PyInstaller, com a compilação do bytecode
em `optimize=1`. A [recusa original](../../outputs/release-5.66.0-20261011/windows/corrected-r4/consolidation-rejected-summary.json)
permanece preservada. A correção posterior altera somente o leitor e seus
testes: exige flag inteira 0/1/2, comprovação estática completa da compilação
em nível 1, 112 rejeições `ValueError` e uma condição de fronteira numericamente
válida, com 113 nomes únicos. Foram aprovados 116 testes desse contrato em
cada SO, incluindo a comparação real de 62 módulos e 25 recursos no EXE.
O executável, os parâmetros científicos e os relatórios originais permanecem
idênticos. A campanha de ferramentas do pacote Git passou 326 testes em
cada SO; esses suplementos possuem sobreposição e não são somados como
casos únicos aos corpora oficiais.

## Compilação e entrega

O Windows foi compilado com Python 3.12.14, PyInstaller 6.22.2 e Qt 6.11.2.
Branding, ícone/instância única, roadmap, modelos opcionais, laboratório,
satélite/OGS, missão clássica, worker e rede global passaram nos gates do
executável congelado. O autoteste passou 39 verificações e o stress completou
20 rodadas. As seis configurações de inicialização cobriram serviço a
100/125/150/200% e demonstração a 100/150%; também passaram as verificações
de resize e troca de tema. A evidência GEO inclui mais de cem alternâncias
funcionais, preservando a seleção e apenas um renderizador ativo. Isso
qualifica esses contratos; não equivale a medir desempenho prolongado em
todos os DPIs nem a interação independente de um usuário físico.

| Artefato | Bytes | SHA-256 |
|---|---:|---|
| Windows portátil, `OrionQ-5.66.0-native-windows-x64.zip` | 312.442.507 | `d77ec611aa09fa77321b9e704da90319a29dd7e0de61d4be89711f4d164e87f1` |
| Executável Windows entregue | — | `0639c86dd39959a12d58b3181627cec849d6f545cd94c9bb2192c6d228a24385` |
| Linux GUI, x86_64 | 539.184.740 | `af4a1099727521974a328882972542fffe8bce1b1698b8799a20a22ee014c3b9` |
| Linux headless, x86_64 | 179.137.331 | `d092df80226f32b46c769052c45c82015cd1bbef18004b80aaf25c8d8e99ec09` |
| Wheel portável | 11.502.538 | `f97e3dea6b400f18fa240e3b46c13bd6e230f45418437e180f941d5e5bc5b92f` |

Linux GUI e headless, recuperação dos pacotes, comparação estática das
fontes congeladas, TerminalUI, API mTLS e GUI/core Linux estão concluídos
no escopo indicado no relatório Linux. O Windows passou também na pasta
transferida: o [finalizador corrigido](../../outputs/release-5.66.0-20261011/windows/corrected-r4-final2/delivery-worker.json)
registrou aprovação às 17:44:03 UTC; a nova aceitação automática levou
353,06 segundos. A [prova de transferência](../../release/windows/5.66.0-native-20261011-131559-release/relocation-acceptance/delivery-relocation-proof.json)
confere hashes de três recibos e do executável antes/depois, sem copiar
journals, certificados privados, credenciais ou logs privados. O autoteste
congelado usa Qt offscreen; não substitui as interações Qt nativas medidas
separadamente. Os benchmarks finais do EXE foram concluídos e estão
consolidados acima, com limites e regressões explicitamente preservados.

Downloads locais: [Windows portátil](../../release/windows/5.66.0-native-20261011-131559-release/OrionQ-5.66.0-native-windows-x64.zip),
[Linux GUI](../../release/linux/5.66.0-native-gui-20261011-corrected-r2/OrionQ-5.66.0-linux-x86_64-gui.tar.gz),
[Linux headless](../../release/linux/5.66.0-native-headless-20261011-corrected-r2/OrionQ-5.66.0-linux-x86_64-headless.tar.gz)
e [wheel](../../release/linux/5.66.0-corrected-r2/quantum_twin-5.66.0-py3-none-any.whl).

## Snapshot Git para entrega

A entrega de código usa uma cópia Git independente na branch
`codex/release-5.66.0`, com um commit de snapshot e sem remote configurado.
O histórico original não é importado nem modificado. A versão atual das
fontes, documentação e evidências públicas selecionadas é conservada;
binários de runtime, estado de operador, credenciais e bancos privados
ficam fora do pacote de código. O dashboard web opcional passou lint e
build em cópia isolada de 83 fontes, com lockfile fixado. A primeira
instalação offline falhou por um tarball ausente no cache, antes do lint;
seu recibo continua separado do [resultado final](../../outputs/release-5.66.0-20261011/windows/corrected-r4/dashboard-validation-final.json).

O processo verifica fontes exportadas, bytes dos objetos Git, `git fsck`,
recuperação limpa do bundle, CRC/bytes do ZIP, novo wheel do clone e sua
validação científica instalada. O [recibo da entrega Git](../../release/source/5.66.0/git-delivery.json)
contém commit, hashes e resultados finais, evitando referência circular
do commit dentro deste relatório. Os artefatos são o
[ZIP de código](../../release/source/5.66.0/OrionQ-5.66.0-github.zip) e o
[bundle Git](../../release/source/5.66.0/OrionQ-5.66.0-github.bundle).
Para recuperar o histórico de snapshot do bundle:

```sh
git clone OrionQ-5.66.0-github.bundle OrionQ
```

O Git original recebeu commits externos durante a campanha; eles foram
preservados. A comparação de metadados do exportador começa em uma nova
baseline imediatamente antes da exportação, não no início histórico da
campanha. Não alegamos que o original ficou imutável durante todo o dia.
Não houve publicação no GitHub. Procedência/licenças de fontes tipográficas
e cópias de dependências de terceiros ainda requerem revisão para eventual
publicação externa, conforme o recibo do pacote.

## Limites operacionais

Esta é qualificação de software no Windows e Ubuntu/WSL2 x86_64, não de uma
placa PetaLinux, FPGA, Swabian, memória física, satélite ou geração de chave
QKD certificada. A identidade física configurada não prova por si só a origem
do evento: o adaptador/serviço autorizado precisa verificar a associação
fonte→instrumento. O envelope v1 não atesta serial/endpoint.

A exclusividade exige a mesma autoridade local provisionada; não há
arbitragem entre hosts/raízes independentes nem fencing validado no próprio
dispositivo. Os modelos permanecem opcionais. Linux embarcado e interfaces
AXI/DMA mantêm seus contratos existentes, sem declarar qualificação de BSP,
temporização determinística ou interlocks físicos. Nenhum controle de
segurança foi desativado para obter os resultados.

O gate de desempenho G05 e a atribuição de TRL 5 permanecem sem aprovação.
Os ensaios desta entrega não substituem toda a campanha prospectiva de
[qualificação TRL 5](../product/orionq_trl5_qualification.md), incluindo
repetições, resoluções e duração exigidas, nem a revisão independente.
