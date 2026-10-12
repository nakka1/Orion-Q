# OrionQ 5.66.1 — responsividade dos painéis e qualificação Windows/Linux



A revisão R3 foi construída e aprovada no Windows, incluindo execução do EXE congelado, pacote relocável e verificação das fontes compiladas. No Linux x86_64/WSL2, as fontes, o wheel, os builds GUI/headless, a recuperação dos arquivos, a ciência, o startup Wayland e a integração autenticada por mTLS têm recibos aprovados nos escopos abaixo. O terminal headless normal foi exercitado. A campanha EVA original falhou em STALE e foi preservada; um novo ensaio privado, iniciado com publicação fresca, passou sem provar recuperação daquela falha. A GUI Linux também concluiu a medição funcional e de desempenho; a captura externa continua parcialmente não qualificada. A medição Windows da nova versão concluiu o roteiro de interação e cinco minutos de continuidade, mas a referência anterior falhou nas três tentativas de prontidão. Portanto, **não há comparação nativa antes/depois qualificada nem evidência de eliminação de todas as pausas**.

A revisão preserva as correções de concorrência, validação científica e layout da [campanha 5.66.0](orionq_release_5_66_0_20261011.md), incluindo seus artefatos, tentativas e limites. Modelos continuam opcionais e atuação física permanece bloqueada. As contagens abaixo qualificam software e evidência local; não elevam o projeto a TRL 5.

## Fontes e artefatos desta revisão

O [freeze R3](../../outputs/release-5.66.1-20261011/windows/source-freeze-corrected-r3.json) contém **680 arquivos**, SHA-256 `526916820b485be82e6a55af16137198739a7f6966043dd264889f944acb2723`. O [delta final](../../outputs/release-5.66.1-20261011/windows/production-delta-corrected-r3.json), SHA-256 `bc46308ff1b087047ca0482283f59cbdc5bba30d742d9b430b12536f4002ffd2`, registra **11 modificados, um novo e 668 inalterados** frente à versão 5.66.0.

Os modelos científicos, as configurações físicas, o RNG e `security/runtime.py` permanecem inalterados. **`security/service_boundary.py` foi modificado** para distinguir uma operação duravelmente comprometida de sua auditoria pendente. As guardas dos bindings e o bookkeeping de recuperação também foram corrigidos; não é correto afirmar que toda a camada de segurança permaneceu inalterada. De R2 para R3, a única alteração de produção foi o bootstrap autenticado do validador diagnóstico de rolagem, sem relaxar as guardas operacionais.

| Entrega | Estado e identificação |
|---|---|
| Windows x64 | [Pacote portátil R3](../../release/windows/5.66.1-native-20261011-185047-release/OrionQ-5.66.1-native-windows-x64.zip), 313.688.754 bytes; SHA-256 `7af478773a9a0f4f76b3fcb5954307c607bd3bf72fcfdfa9dcf2662058cc3477`. EXE SHA-256 `15dbbb00dd4f042ff164c7f0bc6ec013728f7651acf134b4aa4eee7b4dac838b`. |
| Wheel Linux | [Wheel R3](../../release/linux/5.66.1-20261011-corrected-r3/quantum_twin-5.66.1-py3-none-any.whl), 11.506.617 bytes; SHA-256 `c9c6d547bdfe4bf14001e09428fe8d2adf6b27667fe387a8965905052463f0bb`. |
| Linux headless x86_64 | [TAR R3](../../release/linux/5.66.1-native-headless-20261011-r3-native-final/OrionQ-5.66.1-linux-x86_64-headless.tar.gz), 179.135.953 bytes; SHA-256 `7f8cdbca8715756c96881a1b0c6a538a0ee125ee5b4f13a7a2bb381fd6cc2d79`. Build e recuperação aprovados para as fontes R3; não herda a aprovação R2. |
| Linux GUI x86_64 | [TAR R3](../../release/linux/5.66.1-native-gui-20261011-r3-native-final/OrionQ-5.66.1-linux-x86_64-gui.tar.gz), 541.997.108 bytes; SHA-256 `42c38ed647b658e901da921273af99f1ac76ce6a107f8cd341b978299a617f43`. ELF GUI SHA-256 `c0c8564c47f8f5be6bbc5b8a297273faed2c6409eb56b5d8655ff2bc15170133`. |
| Terminal Linux normal | PASS no ELF headless R3, com PTY real, mTLS, atualização de snapshots e revogação sem reiniciar o core. As métricas abaixo são da campanha atual. EVA: a tentativa original continua FAIL; um ensaio novo passou 79 verificações/78 nomes distintos, com a limitação de estado inicial fresco. |
| Git independente | Snapshot independente preparado pelo processo de selo. O [recibo externo](../../release/source/5.66.1/git-delivery.json) registra o commit, ZIP/bundle, reprodução no clone e preservação do Git original após a execução. O recibo não integra o próprio commit; sua presença e estado devem ser conferidos na entrega. |

Os binários e wheels são assets externos ao repositório de fontes. No export Git, o [índice `release-evidence-map.json`](../../release-evidence-map.json) fica na raiz e resolve os relatórios Windows originais para `evidence/release/5.66.1-native-20261011-185047-release/`. O índice é gerado no selo; não representa uma nova execução. JSONs nominais `outputs/` e metadados Linux mantêm os caminhos selecionados. Os ZIP/TAR/wheel só ficam disponíveis nos links originais quando esses assets são fornecidos separadamente nos mesmos caminhos.

## Causa reproduzida nos painéis de políticas

Os watchdogs de Decision Audit, Context Inspection e Policy Review consultavam permissões pelo serviço a cada 250 ms na thread Qt. Essas consultas executavam autenticação, consultas de saúde e gravação durável de auditoria. A interface passa a apresentar as capacidades do último snapshot comprometido; mudança de fonte e parada retiram as ações, enquanto toda ação explícita continua passando pela autorização atual do backend. Capacidades visuais não concedem autoridade.

O [ensaio corrigido antes/depois dos três painéis](../../outputs/release-5.66.1-20261011/watchdog/corrected-r2/summary.json) utiliza fontes 5.66.0 e 5.66.1 em processos novos, os três painéis visíveis, serviço autenticado pela identidade local e seis segundos por processo, sem executar a engine científica. O heartbeat p95 passou de **414,32 para 17,52 ms**; as consultas síncronas na thread principal passaram de 39 para zero, e as lacunas acima de 250 ms passaram de 12 para zero.

As verificações do harness sobre criação de contexto, revogação, acesso anônimo e vinculação ao recibo passaram em ambas as variantes; elas não cobriam todas as ações com um binding já carregado. Este é um **ensaio Qt offscreen isolado de apresentação**, distinto das medições do EXE nativo e da rede completa. Não demonstra ganho global, uso prolongado ou eliminação das pausas do backend.

O [reprodutor público](../../tools/benchmark_policy_panels.py) utiliza as mesmas operações e separa o estado privado das evidências públicas; seus 27 contratos e sete verificações da execução curta passaram. A regressão dos painéis tem 318 casos aprovados na rodada corrigida, incluindo 33 casos de cache, geração da fonte, parada/retomada, expiração, revogação e recuperação. As tentativas anteriores reprovadas permanecem identificadas, sem contá-las como aprovação.

## Autoridade atual nas ações e resultados duráveis

A revisão independente identificou uma lacuna em ações de Policy Review e Context Inspection que reutilizavam um binding carregado. O timer antigo consultava `network.read`, mas o binding não exigia a permissão específica de cada operação. Uma reprodução confirmou três recusas no serviço normal — acesso anônimo, identidade revogada e função alterada — enquanto o binding aceitava `prepare`. A primeira campanha Windows e a primeira campanha Linux foram interrompidas conservadoramente antes de criar binários nativos; seus resultados parciais permanecem históricos.

A correção captura a identidade e o deadline no dispatch, sem I/O. O worker verifica novamente identidade, função, recurso, sessão e prazo. Há verificações após adquirir locks, ao concluir leituras e imediatamente antes do commit. Leituras e propostas são descartadas se a guarda final falhar. O cache visual nunca serve de autorização, e um worker anônimo não recebe silenciosamente a identidade do serviço.

Após uma recuperação comprometida, o backend invalida as preparações antigas por bookkeeping interno; o método público de invalidação continua protegido. Assim, revogação entre commit e auditoria não deixa sessões antigas utilizáveis. Se uma ativação ou recuperação foi duravelmente comprometida e a auditoria falha, o resultado inclui identificação e hash do recibo e orienta consultar o estado, sem recomendar ressubmissão. Recibos incompletos ou adulterados não autorizam essa classificação. O contrato de retry idempotente do controlador misto permanece distinto e preservado.

A [qualificação de autoridade](../../outputs/release-5.66.1-20261011/watchdog/corrected-r2/authority-summary.json) registra 318 casos de políticas/UI e 132 de autoridade/concorrência, com 116 em ambos os corpora: **334 casos distintos aprovados**. As 450 execuções não são somadas como cobertura única. As injeções confirmam revogação efetiva, troca de função/recurso, identidade do worker, prazos e resultado pós-commit. A revisão independente conferiu 34 condições estáticas e a evidência ligada aos hashes; isso não substitui os gates nativos.

As falhas de fixtures durante a revisão ficaram identificadas, sem promoção: espera Qt que retinha o GIL, alteração de capacidades sem publicar um snapshot e instrumentação do ponto antigo de lock/invalidação. As correções de testes preservaram prazos e asserções; não enfraqueceram a autorização de produção.

## Construção incremental das janelas

NodeWindow continua notificando cada linha materializada pelo sinal existente. Um sinal separado anuncia novos controles operacionais; Topology reaplica a habilitação visual a partir das capacidades do snapshot nesse sinal, na abertura/fechamento e na recuperação de disponibilidade. Assim, a criação de texto informativo não exige uma nova busca por todos os controles. A habilitação visual é atualizada antes dos observadores existentes e da próxima entrada do usuário; a ação continua sendo autorizada no worker. A frequência científica e o filtro global de eventos não foram alterados.

O [resumo dos sinais](../../outputs/release-5.66.1-20261011/visual/controls-signals-summary.json) registra 19 testes aprovados: 12 contratos incrementais anteriores e sete casos novos, incluindo ação permitida, recusa, comando pendente, operação ocupada, recuperação e criação síncrona. Na fixture de três enlaces, as 21 notificações anteriores continuam presentes e as varreduras de permissões passam de 21 para três. Esse é um resultado estrutural da fixture, não um ganho percentual global. As tentativas iniciais com fixtures incompletas e importação incorreta permanecem no mesmo resumo.

A proposta privada de limitar o filtro global de eventos a widgets selecionados produziu resultados mistos e não foi promovida. Não houve mudança de linguagem nesta revisão.

## Falhas preservadas e correção diagnóstica R3

Na tentativa Windows R2, os 30 grupos de preparação terminaram com **4.854 execuções: 4.831 PASS e 23 SKIP**, mas o self-test congelado aprovou 36 verificações e falhou ao capturar o contexto para rolagem. O resultado geral foi **FAIL** e nenhum EXE dessa tentativa foi aprovado para publicação. O [recibo da falha](../../outputs/release-5.66.1-20261011/windows/attempt2-frozen-selftest-failure.json) e a [prova dos inputs após a falha](../../outputs/release-5.66.1-20261011/windows/build-2/test-inputs-after-failed-selftest.json) foram preservados.

A causa reproduzida foi uma fixture que criou o provider sem publicar seu primeiro snapshot autenticado. LOAD CONTEXT permanecia desabilitado; o clique não iniciava leitura e a mensagem “Source changed. Load a new context and explicit input.” aparecia sem mudança real da geração da fonte. O validador agora executa `provider.active.refresh()` sob o principal real capturado e `identity_scope`, aguarda o fluxo normal e garante `provider.stop()` inclusive quando o refresh é recusado. Não fabrica capacidades nem inicia o clock/stepper da engine. O baseline de imutabilidade do journal é capturado depois dessa inicialização; o refresh continua sujeito à auditoria real.

Duas regressões novas verificaram bootstrap autenticado sem clock e cleanup após recusa. A reprodução focal aprovou sete verificações diagnósticas; o [módulo integral de rolagem](../../outputs/release-5.66.1-20261011/watchdog/corrected-r3/text-scrolling.xml) passou **77 casos**. A nova construção R3 executou o self-test completo, agora **39/39**, inclusive esse caminho corrigido. Os resultados R2 não foram herdados como aprovação R3.

No Linux, o [corpus de políticas R2](../../outputs/release-5.66.1-20261011/linux/corrected-r2/policy-tests-summary.json) ficou em **330 PASS e dez FAIL**. A fixture antiga de exportação mudava `wrapper.capabilities` sem publicar um frame comprometido e exigia estado assíncrono imediato. A correção publica o snapshot pelo fluxo da UI e mantém o prazo de oito segundos, as mesmas dez identidades de teste, as recusas antes do seletor e a ausência de arquivo após recusa. O writer continua exigindo `evidence.export` e uma guarda atual antes de abrir o destino.

A [primeira execução do candidato](../../outputs/release-5.66.1-20261011/linux/corrected-r3-candidate/policy-and-explicit-exports-tests-summary.json), com acesso inadequado ao caminho/symlink no ambiente, terminou com **38 PASS, dez FAIL e 300 ERROR**; continua reprovada. A [reexecução isolada](../../outputs/release-5.66.1-20261011/linux/corrected-r3-candidate-rerun/policy-and-explicit-exports-tests-summary.json) com as 680 fontes copiadas e verificadas aprovou **348 casos**, mantendo bytes da fixture e inputs intactos. Depois da promoção coordenada da fixture, o [corpus oficial de fonte R3](../../outputs/release-5.66.1-20261011/linux/corrected-r3-final/policy-tests.xml) executou novamente **348 PASS**, incluindo os oito testes de exportação contra a autorização real do backend. As reexecuções não são contadas como cobertura adicional ou aprovação nativa.

As tentativas intermediárias com caminho/symlink de ambiente e a falha de `chmod` em DrvFS permanecem separadas da qualificação. Elas não aprovaram binários nem justificaram remover autorização, alterar permissões globais ou enfraquecer os testes. Os builds nativos Linux R3 abaixo têm recibos próprios; as falhas intermediárias não foram herdadas como aprovação.

## Qualificação Windows concluída

O worker oficial R3 executou os 30 grupos obrigatórios sem herdar corpora ou aprovação nativa da tentativa anterior. A verificação estática e a consolidação passaram, conforme as fases do [recibo de entrega nominal](../../outputs/release-5.66.1-20261011/windows/finalization-corrected-r3/delivery-worker.json). Os arquivos detalhados locais são `windows/finalization-corrected-r3/frozen-source-verification.json` e `native-consolidation.json`; não são exportados pelo seletor atual e não devem virar links que prometem sua presença no Git. Os manifests antes/depois mantêm os 680 arquivos de produção, 433 arquivos de testes e 128 ferramentas byte a byte.

| Evidência Windows R3 | Resultado | Unidade e limite |
|---|---:|---|
| 30 JUnits oficiais, execuções | 4.833 PASS + 23 SKIP = 4.856 | Contagem das execuções; 77 são repetições entre grupos. |
| União dos JUnits por `(classname,name)` | 4.756 PASS + 23 SKIP = 4.779 | Casos distintos; zero falhas, erros ou conflitos de estado. |
| Self-test do EXE | 39/39 | Verificações nativas; não adicionar como 39 testes pytest únicos. |
| Entradas científicas | 113/113 | 112 entradas inválidas recusadas e uma condição de fronteira aprovada. |
| Fonte compilada | 71 módulos + 25 recursos | Verificação estática do bytecode/recursos contra o freeze; compilação com otimização 1. |
| Execução Python congelada | `sys.flags.optimize = 0` | Observação em runtime, distinta da otimização do bytecode compilado. |
| Pacote e relocação | PASS | Integridade, self-test e stress sintético de 20 rodadas no pacote copiado. |

O [recibo de entrega](../../outputs/release-5.66.1-20261011/windows/finalization-corrected-r3/delivery-worker.json) liga o EXE e o ZIP aos hashes publicados. A prova de relocação, resolvida pelo índice para `evidence/release/5.66.1-native-20261011-185047-release/relocation-acceptance/delivery-relocation-proof.json`, confirma o mesmo EXE antes/depois e os três grupos da aceitação local. Não houve cópia de estado, chaves ou logs privados. Essa aceitação local não representa instalação em uma máquina independente ou qualificação de hardware físico.

## Validação Linux de fonte e escopos nativos concluídos

Os seis JUnits R3 foram executados em um ambiente Linux novo, vinculado ao freeze final, e recontados sem somar repetições:

| Corpus Linux R3 | PASS | SKIP | Execuções |
|---|---:|---:|---:|
| [Fonte](../../outputs/release-5.66.1-20261011/linux/corrected-r3-final/source-tests.xml) | 1.471 | 1 | 1.472 |
| [Widgets e rolagem](../../outputs/release-5.66.1-20261011/linux/corrected-r3-final/visual-regression-final.xml) | 210 | 0 | 210 |
| [Políticas e exportações](../../outputs/release-5.66.1-20261011/linux/corrected-r3-final/policy-tests.xml) | 348 | 0 | 348 |
| [Auditoria](../../outputs/release-5.66.1-20261011/linux/corrected-r3-final/audit-tests.xml) | 25 | 0 | 25 |
| [Ferramentas](../../outputs/release-5.66.1-20261011/linux/corrected-r3-final/tools-tests.xml) | 162 | 0 | 162 |
| [Contratos de pacote](../../outputs/release-5.66.1-20261011/linux/corrected-r3-final/package-tests.xml) | 494 | 0 | 494 |
| Soma das execuções | 2.710 | 1 | 2.711 |

A união é **2.567 PASS + 1 SKIP = 2.568 casos distintos**, com 143 repetições e zero falhas/erros/conflitos. O skip é o golden específico de Windows. Os 210 casos de widgets/fonte não comprovam pintura de um ELF nativo ou captura do compositor WSLg. O wheel foi instalado em ambiente novo; não equivale sozinho à aceitação GUI/headless.

A [qualificação nativa Linux R3](../../outputs/release-5.66.1-20261011/linux/r3-native-final/linux-release-qualification.json) está vinculada às mesmas 680 fontes. Os [recibos de recuperação headless](../../outputs/release-5.66.1-20261011/linux/r3-native-final/archive-headless-smoke.json) e [GUI](../../outputs/release-5.66.1-20261011/linux/r3-native-final/archive-gui-smoke.json) verificam CRC, bytes, entradas e modos Unix do TAR em extração privada ext4. Modos observados depois em DrvFS não substituem essa prova Linux. As tentativas anteriores de DrvFS e as preparações malsucedidas permanecem históricas.

| Escopo nativo Linux R3 | Resultado e limite |
|---|---|
| Ciência no ELF GUI | 113/113 entradas científicas, vinculadas ao ELF pelo [recibo científico/estático](../../outputs/release-5.66.1-20261011/linux/r3-native-final/scientific-static-linked.json). |
| Fonte compilada GUI | [71 módulos e 25 recursos](../../outputs/release-5.66.1-20261011/linux/r3-native-final/gui-frozen-source-linux.json) correspondentes ao freeze; compilação com otimização 1, distinta de flags 0 no runtime. |
| Self-test GUI | 39/39 checks no binário atual. Não somar esses checks como novos casos pytest da união de fonte. |
| Startup Wayland | [15 casos PASS](../../outputs/release-5.66.1-20261011/linux/r3-native-final/gui-wayland-startup.json), encerramento normal e nenhum erro. Não prova desktop Linux independente ou arraste físico do mouse. |
| GUI com core remoto | [Smoke mTLS PASS](../../outputs/release-5.66.1-20261011/linux/r3-native-final/gui-remote-smoke.json), leitura autenticada e core observado durante a GUI. Não é benchmark de desempenho nem inspeção de todos os pixels. |
| Terminal headless normal | PTY/curses real e TLSv1.3-mTLS, com os resultados da campanha atual detalhados abaixo. |
| EVA no terminal | Tentativa original FAIL: STALE e saída 3. Novo ensaio privado: 79 verificações/78 nomes distintos PASS, saída normal 0 nos dois fluxos e 130 no cancelamento. Critérios originais preservados; não demonstra recuperação da falha anterior. |
| XWayland/X11 | Startup: 15 casos PASS; pipeline: seis contratos funcionais PASS. As seis capturas QScreen pretas permanecem não qualificadas. O observer X11 final continuou FAIL, apesar de quatro frames próprios não pretos persistidos. |
| Desempenho GUI | Quatro fases nominais de oito segundos concluídas no ELF atual, seis leituras completas e zero divergências. Os tempos e limites estão na seção própria abaixo. |

A [campanha atual do terminal normal](../../release/linux/5.66.1-native-headless-20261011-r3-native-final/evidence/native-terminal-campaign.json) durou **101,917 s**. Registrou **202 execuções de verificações e 161 nomes distintos**, incluindo PTY/controle VT100; não são 202 novos casos pytest nem testes únicos por tecla. Foram **1.273 teclas, 163 trocas de interação e 155 observações de latência de teclado**. O core continuou com o mesmo PID após o cliente sair e sua identidade ser revogada; acesso anônimo foi recusado, a identidade revogada recebeu HTTP 401 e outro operador autenticado recebeu um tick posterior. A apresentação não chama `engine.step()` para simular atualizações.

| Medição do terminal normal atual | Resultado |
|---|---:|
| Leitura da API durante watch | n = 6; p95 **1.601,3665 ms** |
| Latência de teclado | n = 155; p95 **301,028 ms** |
| Renderização de texto | n = 57; p95 **2,171091 ms** |
| Duração da campanha | **101,917 s** |
| CPU, RSS e startup total | **Não coletados** nesta campanha; não inferir consumo ou tempo de inicialização a partir da duração. |

O p95 da API com seis amostras é o máximo empírico. Renderizar texto em poucos milissegundos não equivale a receber telemetria nessa frequência nem garante ausência de pausas. O watch publicou mudanças de tick/históricos pelo serviço real; não houve comparação de desempenho contra a versão anterior nem garantia de tempo real determinístico. Os detalhes do terminal são preservados nos metadados da entrega headless; o texto bruto de captura continua externo à seleção pública.

O perfil desta campanha é **Linux x86_64 sob WSL2**. Build headless, build GUI, terminal, APIs autenticadas e pintura nativa têm escopos próprios. Nenhum desses resultados qualifica automaticamente ARM, OBC, PetaLinux, PL/FPGA, AXI/DMA, bancada Swabian física ou equipamentos ópticos.

## Medições Linux headless e GUI preservadas separadamente

O [resumo final Linux R3](../../outputs/release-5.66.1-20261011/linux/r3-native-final/linux-delivery-summary.json), SHA-256 `7d3413c255c8d9f288b9336435de47ea10fd435c178b241a3d5cc2e2e7016460`, liga as métricas aos binários, fontes, ambiente e digests dos raw. O perfil foi Ubuntu 24.04/WSL2 x86_64, UID 10001, Python 3.12.3, Qt 6.12.0 e Torch 2.14.1+cpu. Hardware compartilhado: Ryzen 7 5700X, 32 GiB, RTX 3060, monitor 3440×1440/DPR 1. O ensaio GUI utilizou cliente 1920×1080, stage 1920×1046 e XWayland/XCB. Ganho de GPU não foi qualificado.

Na tentativa original da GUI, o armazenamento privado em DrvFS recusou proteger `vault.json` com EPERM antes de gerar métricas. A falha e seus bytes foram preservados. A nova execução usou estado privado em ext4 com diretórios 0700/arquivos 0600, sem relaxar a proteção, os prazos ou a autorização. Foi utilizado o mesmo ELF atual e as mesmas quatro fases nominais de oito segundos, sem soak, profiling ou ajuste do intervalo do interpretador. As durações efetivas foram 10,21445 s no mapa, 8,08961 s no globo, 8,08702 s nas abas e 8,00677 s nas trocas.

A execução durou **87,646 s**, terminou normalmente e registrou `completed=true`, sem erros funcionais. A construção até attach foi **1,711 s**; primeira pintura após show **180,70 ms**; primeiro snapshot **194,60 ms**; workspace **18,880 s**; primeiro tick 3 observado **29,786 s**. O label `current_windows_session` no harness é uma constante histórica: a identidade efetivamente verificada foi Linux UID 10001, não uma sessão Windows.

A carga foi 76 componentes, 136 enlaces, 11 satélites, 38 sessões, 76 comunicadores, 78 demandas globais e quatro legadas. As seis seleções tiveram leitura completa, sem divergências. Houve seis transições de modo, cinco mudanças efetivas, quatro navegações e no máximo um renderizador GEO nas observações. O deadline de detalhes coletado foi **cinco segundos**; não substitui o critério anterior de um segundo de G05.

| Fase GUI Linux | Heartbeat p95 (n) | Interação → pintura p95 (n) | Detalhes completos p95 (n) | Lacunas ≥250 ms | CPU UI p95 | RSS UI p95 |
|---|---:|---:|---:|---:|---:|---:|
| Mapa | 119,92 ms (301) | 907,01 ms (5) | 3.116,60 ms (3) | 7 | 121,7% | 512,83 MiB |
| Globo | 84,73 ms (364) | 276,52 ms (5) | 2.274,33 ms (3) | 4 | 117,7% | 518,97 MiB |
| Abas | 24,92 ms (527) | 603,28 ms (4) | Sem observações | 3 | 115,6% | 526,00 MiB |
| Trocas | 63,94 ms (407) | 496,67 ms (6) | Sem observações | 4 | 125,6% | 542,00 MiB |

CPU 100% corresponde a um processador lógico. As 907 observações dos handlers de pintura são inclusivas dos filhos; não são frames GPU exclusivos. Com três amostras de detalhes por fase, o p95 é o máximo empírico, sem intervalo de confiança ou repetições independentes. Houve **18 lacunas ≥250 ms**. Na campanha inteira, o provider registrou **13 overruns**, último step **2.667,12 ms**, relógio científico 13 s e cadência alvo de um segundo. Não há aceitação de tempo real ou ausência de crescimento prolongado de memória.

Headless mede transporte API, resposta ao teclado em PTY e renderização textual; GUI mede interação/pintura, preenchimento, heartbeat, CPU e RSS da UI. Os três p95 headless estão na tabela anterior. Não há razão de velocidade GUI/headless nem comparação Linux antes/depois: operações, amostras e escopos diferem. CPU/RSS/startup completos do processo headless não foram coletados.

O novo ensaio EVA preservou todas as asserções do ensaio original e acrescentou duas verificações de frescor. Foram 79 verificações/78 nomes distintos, 43 teclas/trocas e três sessões PTY, com restauração de termios. A publicação já estava fresca antes da espera adicional de 505,63 ms; portanto, o PASS não explica a causa nem comprova recuperação do STALE anterior. Preparação, diff, hashes e limitações constam do resumo nominal; os helpers privados e capturas brutas não são ferramentas públicas da release.

O startup sem override QPA selecionou Wayland e passou 15 casos, com heartbeat p95 21,25 ms/máximo 831,01 ms. O perfil XCB exigiu nove bibliotecas Ubuntu no ambiente isolado; essa dependência continua necessária no SO de destino. As verificações funcionais e o startup não comprovam legibilidade de todas as telas.

As cinco imagens da primeira observação X11 ficaram sem recibo completo de proveniência por frame. O novo observer persistiu **quatro frames não pretos vinculados ao PID, start time, UID, ELF e geometria da própria janela** antes de falhar ao consultar `/proc/<pid>/exe` após o encerramento do ELF. O pipeline funcional terminou com saída 0; o observer terminou `FAIL_NOT_QUALIFIED`, sem erros X11 registrados. Uma imagem vinculada foi inspecionada: mapa, gráfico, painel e terminal eram legíveis, mas o painel estava parcialmente pendente. Não há prova de todos os seis casos, sincronização cenário/tick por frame ou rolagem completa. Capturas pretas e observers reprovados não foram reclassificados.

Os raw GUI Linux, os diffs dos probes e as imagens ficam locais fora da seleção Git; o resumo nominal preserva seus hashes e métricas. Esse limite é explícito para não prometer um reprodutor público ou evidências privadas dentro do snapshot.

## Medição nativa da nova versão e comparação não qualificada

A [revisão parcial dos raw](../../outputs/release-5.66.1-20261011/performance/comparison.json), SHA-256 `ca7a6c3ec2bca71e97e0a3489ce9a2b783efae4285018fda58144723f26b79a3`, usa o schema `orion.native-performance.partial-review.v1`. Ela qualifica a integridade de **dois ensaios da nova versão**, com o mesmo EXE R3 aprovado, em 3440×1440/DPR 1, 76 nós, 136 enlaces, 11 satélites, 38 sessões tecnológicas e 78 demandas globais. As **16 condições disponíveis**, incluindo a origem registrada da cadência, coincidem entre o roteiro novo e seu soak, com **oito campos de cenário registrados separadamente**. Não se trata do schema de comparação antes/depois; `matched_before_after = NOT_QUALIFIED` e não há aceitação de limiar global de latência.

As três tentativas atuais da referência 5.66.0 falharam antes da carga steady-state:

| Referência preservada | Resultado observado |
|---|---|
| Tentativa 1 — preservada em `before-native-timeout-context-attempt1`, registrada no [JSON parcial](../../outputs/release-5.66.1-20261011/performance/comparison.json) | Watchdog externo; nenhum report da UI ou fase completa. |
| Tentativa 2 — preservada em `before-native-timeout-context-attempt2`, registrada no [JSON parcial](../../outputs/release-5.66.1-20261011/performance/comparison.json) | Prazo de 90 s excedido em splash/`waiting_workspace`, sem pedido de autenticação; também houve watchdog externo. |
| [Tentativa 3](../../outputs/release-5.66.1-20261011/performance/before-native/report.json) | Pedido de autenticação aos 80,81 s e workspace aos 87,55 s; nenhuma observação de tick antes do prazo de 90 s em `warming_workspace`. Encerramento sem watchdog externo. |

As revisões independentes de [pinturas, contexto e ciência](../../outputs/release-5.66.1-20261011/performance/paint-science-review.json) e de [recursos/RSS](../../outputs/release-5.66.1-20261011/performance/comparison-review.json) recontam os raw selecionados. Esses três JSONs conservam o escopo after-only: validam contabilidade e proveniência, sem criar uma referência aceita ou calcular ganho nativo antes/depois.

Os prazos e critérios não foram aumentados. Os três resultados continuam falhas e seus hashes estão no JSON parcial. Não há stack que estabeleça uma causa comum; variações de construção, GIL, I/O, identidade ou compositor permanecem hipóteses quando não instrumentadas. A versão do pacote no ambiente que produz `configuration.json` não substitui a versão/hash do EXE medido. A campanha histórica 5.66.0 não foi reutilizada como referência atual equivalente.

O [roteiro after-native](../../outputs/release-5.66.1-20261011/performance/after-native/report.json) concluiu as quatro fases de oito segundos com seis readbacks completos, zero divergências e no máximo um renderer GEO nas observações. Heartbeat p95: mapa **68,71 ms**, globo **68,50 ms**, abas **21,90 ms**, trocas **33,85 ms**. Foram observados seis intervalos ≥250 ms, com máximo de **971,97 ms**. O preenchimento completo dos detalhes teve p95 de **1.641,59 ms** no mapa e **1.299,90 ms** no globo, com somente três amostras em cada fase; nessa amostra, p95 é o máximo empírico.

O [after-soak](../../outputs/release-5.66.1-20261011/performance/after-soak/report.json) concluiu o mesmo roteiro e uma fase de continuidade de **300,0847 s**, com 150 ações planejadas e 75 readbacks completos nessa fase, sem divergências. O máximo de renderers GEO permaneceu um nas amostras e nos readbacks; isso não qualifica todos os frames.

| Métrica da fase soak | Resultado observado |
|---|---:|
| Heartbeat p95 / máximo | 59,61 ms / 1.000,54 ms |
| Intervalos ≥250 ms | **48** |
| Detalhes completos p95 / amostras | 2.272,74 ms / 75 |
| CPU UI p95 | 120,6%; 100% equivale a um processador lógico |
| RSS mediana, janela inicial → final | 572,94 → 598,28 MiB: **+25,34 MiB** |
| RSS máximo | 637,98 MiB |
| Inclinação RSS no último 80% da fase | **+4,23 MiB/min** por OLS |

As estatísticas finais da **campanha after-soak inteira**, incluindo startup e fases anteriores, registraram **41 overruns**, último step de **7.935,48 ms** e relógio de protocolo de **41 s**, para cadência nominal de um segundo. Esses 41 overruns não devem ser atribuídos somente aos 300 segundos de soak. No ensaio after-native, o último step registrado foi 14.586,21 ms. São observações dos novos ensaios, sem proporção de ganho/perda contra uma referência qualificada.

Intervalos de heartbeat não medem exclusivamente tempo de bloqueio da thread. RSS inclui dados retidos pelos coletores do benchmark; árvore de processos pode somar páginas compartilhadas. Cinco minutos não provam nem excluem vazamento prolongado. O redutor primário transporta estatísticas dos reports; a revisão independente de pintura/ciência recontou os dois CSVs de pinturas e heartbeats, sem divergências. Os tempos de pintura incluem filhos e não equivalem a GPU exclusiva. A revisão de recursos conferiu o PID da UI e 571 amostras de RSS; as duas medianas usam janelas disjuntas de 20 s com 38 amostras cada. A diferença de +25,34 MiB e a inclinação OLS do último 80% (+4,23 MiB/min) são métricas distintas, sem extrapolação. Os 75 readbacks do soak pertencem ao mesmo experimento, não a 75 repetições independentes. Contexto GPU/CPU do host não demonstra aceleração específica do OrionQ.

## Pendências e limites

**G05 e as pausas da rede completa continuam abertos.** A revisão removeu uma causa comprovada nos três painéis e reduziu varreduras repetidas de controles, mas o backend completo continua excedendo a cadência nominal. Perfis anteriores sem Qt já mostravam steps de 4,67–5,05 s. Validação científica, GIL, feedback FPGA emulado e commits SQLite/fsync permanecem caminhos materiais; seus tempos inclusivos/aninhados não podem ser somados como causas independentes. Nenhuma mudança de processo da engine, batching de durabilidade, redução de ciência ou remoção de guardas foi promovida nesta revisão.

Ainda faltam qualificação prolongada, medição nativa antes/depois comparável, fechamento do crescimento de RSS, reprodução e explicação do STALE original no EVA, inspeção de todas as telas/rolagem nativa Linux e reprodução em máquina independente. GUI Linux e terminal têm medições atuais preservadas; a captura externa continua parcial. A integridade e reprodução do snapshot Git são verificadas no recibo externo de entrega, sem transformar esses limites em aprovação. Os resultados não estabelecem tempo real determinístico, disponibilidade industrial, certificação ou operação contínua sem supervisão.

Qualificação física, ARM/PetaLinux, segurança óptica, integração real Swabian/FPGA/satélite e **TRL 5** exigem evidências adicionais. Simulações, quantidade de testes e uso autenticado de APIs não substituem um ambiente relevante com hardware. Nenhuma tentativa reprovada, captura preta ou diagnóstico de ambiente deve ser promovido a PASS.

## Reprodução e limites da seleção de evidências

Execute o Windows pelo `OrionQ/OrionQ.exe` do ZIP extraído. No Linux GUI, preserve os modos Unix ao extrair o TAR e execute `./OrionQ`; confira as dependências Wayland/XCB do SO. No headless, os executáveis `orion-core`, `orion-operator` e `orion-pl-gateway` têm contratos distintos; siga a [documentação do terminal](../product/orionq_terminal_operator.md) e do [serviço Linux](../product/orionq_linux_service.md), e provisione identidades/certificados antes de comandar o serviço. O login do operador não inicia nem concede autoridade implícita à engine.

Para repetir o benchmark GUI público, mantenha estado/evidências em filesystem Linux com permissões verificáveis e registre o ambiente, o hash do ELF e os mesmos parâmetros:

```bash
QT_QPA_PLATFORM=xcb python tools/benchmark_console_performance.py \
  --executable /opt/orionq/OrionQ --output /var/tmp/orionq-gui-performance \
  --duration 8 --soak 0 --size 1920x1080
```

O diretório de saída deve ser novo; a configuração XCB exige bibliotecas adequadas. Mudar QPA, resolução, carga ou cadência cria outro perfil de comparação. A apresentação não deve reduzir eventos científicos para atingir o limite de UI.

Os três JSONs nominais `performance/comparison.json`, `paint-science-review.json` e `comparison-review.json` preservam a integridade e o escopo after-only, sem promover a referência reprovada. Os raw Windows selecionados incluem reports, configurações, CSVs e recursos em schema limitado, sem argumentos/ambiente. Estado, DB, chaves, certificados privados, capturas arbitrárias e logs não entram no Git. As tentativas Windows 1/2 e detalhes locais da finalização são citados por nomes/hashes no resumo, sem prometer cópia no export. Os metadados Linux nominais mantêm os caminhos originais e o resumo final liga os probes adicionais por hashes.

## Índices e recibo final

O export `OrionQ-5.66.1-github` contém na raiz `release-source-manifest.json`, `release-evidence-map.json`, `GIT-RELEASE.md` e `SOURCE_RELEASE.md`. Gates Windows selecionados ficam em `evidence/release/5.66.1-native-20261011-185047-release/`; self-tests grandes têm projeções explícitas `self-test-summary.json` com hash do original. O índice identifica as projeções, sem tratá-las como cópia byte a byte do original ou fechamento genérico de arquivos privados.

O recibo `release/source/5.66.1/git-delivery.json` é externo ao commit, com cópia nominal em `outputs/release-5.66.1-20261011/git/git-delivery.json`. `SHA256SUMS.txt`, ZIP e bundle ficam ao lado do recibo. O selo verifica commit, fsck, recuperação do clone e contratos; não representa nova execução de todas as campanhas históricas. Os assets Windows/Linux são externos ao Git de fontes; seus links requerem entrega separada ou URLs reais de release.

Os 83 inputs do dashboard opcional são verificados por identidade de bytes; lint/build anteriores foram reutilizados explicitamente, sem nova campanha de browser/API. O snapshot Git também conserva as limitações de revisão de publicação para licenças de terceiros e a fonte Quantum. Não há upload, certificação de segurança ou qualificação de outra máquina implícitos no selo local.

