# OrionQ: entrada, confirmação de identidade e carregamento

As fontes **5.60.1** refinam a cruz vetorial da GEO: forma mais alongada, base mais estreita, brilho interior quente e discreto, e uma referência geográfica comum ao Mapa e ao Globo. Permanecem o som original de **Unplug from E.V.A.**, introduzido em 5.60.0, e o gatilho visual **S. Ikari** com a senha exata **thirdpilot**. O fluxo conserva a confirmação de identidade Windows e o carregamento por trabalho concluído. A marca inicial corresponde à última versão aceita do executável: “Orion” em Inter, na cor de texto do tema, seguido do Q laranja com esfera de Bloch e satélite.

## Entrada com a identidade existente

Selecione a origem de dados e confirme a conta vinculada à sessão. **Sign in** verifica a senha da conta Windows; nomes locais, `DOMÍNIO\usuário` e `usuário@domínio` são aceitos conforme a identificação do Windows. **Use current Windows session** confirma a identidade da sessão local já autenticada, sem solicitar a senha novamente. Nesse caminho, o nome informado deve corresponder ao nome da identidade vinculada.

[NativeLoginController](../../src/quantum_autopilot/console/login_session.py) executa a verificação em um worker. [authenticate_native_login](../../src/quantum_autopilot/security/native_login.py) revalida o principal recebido, compara sua identidade com o SID do processo e, ao verificar uma senha, consulta `LogonUserW` com `LOGON32_LOGON_NETWORK`. O SID do token retornado precisa ser o mesmo da identidade já vinculada. O token é fechado e o buffer nativo da senha é zerado; o campo da interface é limpo antes do envio. Falhas apresentam uma mensagem genérica, e a auditoria não recebe a senha nem o nome digitado.

O login conserva a identidade e as permissões provisionadas. A autorização de `network.read` é repetida depois da verificação para detectar revogação ou mudança de política durante o trabalho. Um principal de outra origem não pode ser substituído por uma conta Windows através desse formulário. A seleção de origem continua distinguindo serviço local, demonstração com entrada simulada e telemetria medida. A ativação física permanece bloqueada pelas mesmas fronteiras do backend. `--skip-auth` continua dispensando somente o fluxo visual de seleção e confirmação; a identidade OS e as permissões do backend permanecem exigidas.

## Sequência visual local

Digitar **S. Ikari** no campo de conta, preencher a senha exata **thirdpilot** e acionar **Sign in** inicia a sequência visual. O reconhecimento do nome ignora maiúsculas/minúsculas e espaços nas bordas; a senha não é normalizada e diferencia maiúsculas/minúsculas e espaços. Uma combinação incorreta não aciona o efeito; uma senha incorreta para o nome reservado não é enviada ao verificador de identidade. Esse caminho limpa o campo de senha, não autentica a identidade do efeito e não abre o workspace autenticado. O gatilho é público e cosmético, sem conceder função, autorização ou acesso adicional.

[EmergencySignalOverlay](../../src/quantum_autopilot/console/widgets/emergency_signal.py) desenha painéis, faixas, leitura digital e textos com geometria vetorial Qt. Os textos centrais são **EMERGENCY** e **YOU SHOULD NOT BE ABLE TO CONNECT TO EVA**. A sequência dura aproximadamente 4,7 segundos e retorna à entrada. A conclusão natural acrescenta o marcador exato **eva operator connected** na entrada e na barra de status; o marcador descreve apenas o efeito visual.

**Escape** interrompe a sequência. Ocultar a superfície também encerra a apresentação; fechar ou trocar a superfície cancela seu ciclo. A interrupção antes do fim não registra uma nova conclusão. Durante a apresentação, eventos de entrada da mesma janela são interceptados para impedir ações sobre os controles cobertos. A finalização para o timer, interrompe o som, remove o filtro de eventos e restaura o foco. Um cancelamento silencioso não dispara o fluxo de conclusão.

O áudio é [emergency-interface.wav](../../src/quantum_autopilot/console/assets/emergency-interface.wav), sintetizado originalmente para este projeto: PCM mono, 16 bits, 44,1 kHz e 2,1 segundos. Sua reprodução é opcional. Ausência de QtMultimedia ou de uma saída de áudio permite continuar a apresentação visual. O WAV está declarado no package-data e nos recursos obrigatórios do empacotamento Windows.

Ao acionar a sequência, o Q assume vermelho `#E53935`. Sua cauda parte de uma base larga junto à esfera e afina para fora seguindo a diagonal do Q; a terminação em cruz tem braços com pontas afiladas e substitui o satélite. A esfera de Bloch e as letras Inter são preservadas. A variante acompanha as marcas da janela, as superfícies nativas filhas e seu ícone em memória enquanto o modo E.V.A. permanece ativo, inclusive após reconstrução por troca de tema. Desligar esse modo ou iniciar uma nova execução restaura a marca laranja e o satélite. A variante não grava uma preferência nem modifica os ícones empacotados. Consulte também o [guia de marca](orionq_branding.md).

Em 5.60.1, a apresentação GEO conserva o mar vermelho e projeta a base da cruz em **longitude 0° e latitude 55° N** nos dois modos. No Mapa, sua posição acompanha a transformação geográfica e sua altura acompanha o zoom. No Globo, a base deriva do vetor ECEF da mesma referência e fica oculta no hemisfério posterior. Uma escala visual de 2.548.400 m controla a altura; o piso de 54 pixels no globo mantém a decoração legível ao afastar. Nenhum desses valores representa um dispositivo ou estrutura física. A forma usa largura/altura de 0,72 e base de 10% da largura. Um gradiente de núcleo quente `#FF8B78` sobre `#F02D40` produz o brilho exclusivamente dentro da silhueta, mantendo 25% de opacidade, sem halo ou animação adicional. Os ícones dos satélites e pontos terrestres, enlaces, destaques e áreas de clique mantêm seus estilos e funções. Desligar recupera a aparência normal. Esse estado compartilhado não modifica os cadastros, as posições orbitais, a seleção ou os estados de transmissão publicados pelo backend.

## Desligamento do modo E.V.A. em 5.60.0

Com o modo visual ativo, **Unplug from E.V.A.** aparece no rodapé da entrada e no botão de origem do cabeçalho. O controle de saída da barra lateral recebe esse mesmo nome acessível e tooltip; o botão de retorno do carregamento também usa esse texto. **F6** inicia a mesma sequência de desligamento.

[DisconnectSignalOverlay](../../src/quantum_autopilot/console/widgets/disconnect_signal.py) cobre a janela com uma animação nativa de aproximadamente **3,6 segundos**: hexágonos vermelhos acendem, uma faixa apresenta o texto exato **Disconnected from E.V.A.**, os hexágonos apagam e a superfície fica integralmente preta. O quadro preto permanece por pelo menos **180 ms**, contados após a pintura efetiva, antes do retorno à entrada normal. A geometria dos hexágonos e os contornos do texto são reutilizados do cache; o texto usa Inter e as fontes existentes do projeto. O efeito não consulta APIs, hardware, modelos científicos ou verificadores de autenticação.

O som de energia diminuindo é [disconnect-interface.wav](../../src/quantum_autopilot/console/assets/disconnect-interface.wav): síntese original com a biblioteca padrão Python, PCM mono de 16 bits, 44,1 kHz e **3,2 segundos**, sem gravações ou samples externos. [generate_disconnect_sound.py](../../tools/generate_disconnect_sound.py) reproduz os mesmos bytes. `QSoundEffect` é criado apenas quando necessário e carrega o WAV de forma assíncrona; uma notificação `Ready` inicia uma única reprodução enquanto a superfície está ativa. A animação não aguarda a decodificação nem o término do áudio. Ausência de saída, arquivo ou QtMultimedia e falhas de reprodução permitem o retorno normal. Uma notificação tardia não toca depois de concluir ou cancelar.

Ao concluir, a janela desativa a variante vermelha e limpa **eva operator connected** na entrada e na barra de status. O logo volta ao Q laranja com satélite e o ícone da janela volta ao original. O campo de senha fica vazio; o campo de conta apresenta novamente a identidade OS vinculada, e a seleção da origem é preservada. A confirmação de login da interface fica desativada, exigindo nova confirmação para abrir o workspace. O principal, suas permissões, a aquisição do backend e operações duráveis em andamento continuam preservados.

Durante o desligamento, a superfície captura a entrada da mesma janela. **Escape** é consumido, e a apresentação segue até o quadro preto e o retorno normal. Fechar a janela cancela e descarta a superfície, sem navegação posterior. Ciclos repetidos reutilizam o mesmo timer e a instância de som; concluir, cancelar ou ocultar interrompe a reprodução, para o timer, remove os filtros e libera o foco. O descarte desconecta a notificação de áudio e libera o recurso Qt. Uma nova execução também começa com a marca e a entrada normais.

## Carregamento por trabalho concluído

[LoadingStatus](../../src/quantum_autopilot/console/widgets/loading_state.py) apresenta cinco segmentos vetoriais, uma mensagem, o número de etapas concluídas e o total. Os cinco segmentos representam a fração informada; a operação pode ter outro número de etapas. A interpolação dura até 180 ms e termina no progresso recebido. O pulso de espera altera somente o brilho de um contorno, sem aumentar a contagem ou estimar tempo restante.

[DeferredProvider](../../src/quantum_autopilot/console/prepared_source.py) prepara o serviço em um worker e cria o `ServiceProvider`, seus objetos Qt e seus timers na thread da interface. O mesmo principal explícito e o contexto de execução acompanham ambas as fases. Um contexto anônimo ou revogado é recusado; a preparação não adota as permissões administrativas do processo como substituição.

O carregamento de serviço e demonstração usa **sete marcos**: serviço preparado, provider instalado e cinco etapas da leitura inicial de saúde, topologia/modelos, evidências/estado de protocolos, identidade/journal e publicação do snapshot. A origem permanece indisponível até um snapshot pronto ser publicado; o sétimo marco corresponde a essa publicação. Frames vazios de troca de origem e sinais de progresso antecipados não completam o carregamento nem permitem abrir o workspace. A troca de origem retransmite essas etapas pelo mesmo provider estável da janela. Se a interface voltar ao carregamento enquanto a preparação continua, `loading_state` recupera a contagem medida daquela execução.

Uma falha preserva o último progresso recebido e apresenta **Retry loading** e **Back to sign in** no modo normal. A tentativa seguinte reinicia a preparação ou solicita nova leitura ao provider existente. **Back to sign in** limpa a navegação automática do carregamento; no modo E.V.A., esse controle mostra **Unplug from E.V.A.** e segue a sequência de desligamento antes de retornar à entrada. Ao confirmar novamente o login para a mesma origem que falhou, a interface reinicia a preparação e continua aguardando prontidão, sem abrir uma tela vazia. A retomada após voltar à entrada exige confirmação do login. Parar a preparação invalida sua geração, solicita cancelamento sem esperar o worker e descarta resultados tardios. Os indicadores param ao ocultar, concluir, abortar ou remover a superfície; as sequências visuais também são descartadas no fechamento da janela.

## Evidência atual: 5.60.1

A [validação da manutenção GEO](../results/orionq_geo_anchor_20261009.md) registra **311 regressões únicas aprovadas** nesta rodada, incluindo **43 testes GEO**, em 7,61 s, sobre a revisão final com brilho quente, proporção 0,72 e piso de 54 pixels no Globo. Esses testes verificam ancoragem comum, pan/zoom, ocultação, culling, transparência, brilho, restauração e ciclo de vida dos renderizadores. O diagnóstico de fonte com **Qt Windows** aprovou 28 casos GEO e produziu 38 PNGs por `QWidget.grab` de componentes lógicos 1280×720/3440×1440, DPR 1, em host 960×640; não captura o console completo nem executa o binário congelado. A [entrada nativa atual](../../outputs/orionq_build_5_60_1_20261009/native-entry/entry-visuals.json) aprovou quatro casos, com fixture de interface, sem autenticação ou estado do operador. Testes de versões ou revisões anteriores permanecem separados; não são somados como novos resultados. Capturas prévias com Qt offscreen permanecem históricas e não qualificam esta revisão Windows.

A [rodada 5.60.1-native-20261009-061920](../../release/windows/5.60.1-native-20261009-061920/) produziu o [executável](../../release/windows/5.60.1-native-20261009-061920/OrionQ/OrionQ.exe) e o [ZIP portátil](../../release/windows/5.60.1-native-20261009-061920/OrionQ-5.60.1-native-windows-x64.zip), aprovados em **20 de 20 gates automáticos locais**, sem erros, no [consolidado final](../../outputs/orionq_build_5_60_1_20261009/frozen-release-final.json). A [aceitação nativa](../../release/windows/5.60.1-native-20261009-061920/native-acceptance/acceptance-report.json) confirma `automatic_passed: true`; o [manifesto do pacote](../../release/windows/5.60.1-native-20261009-061920/portable-package.json) registra os hashes do EXE e ZIP e o CRC verificado. Os autotestes do binário aprovam 39 critérios com Qt **offscreen**; os quatro casos de entrada acima continuam sendo testes de fonte com fixture e Qt **Windows**.

A revisão manual, outra máquina, mouse físico, som audível e integração física permanecem pendentes. O runtime do roadmap excedeu 10 ms nas 20 amostras; a aprovação funcional não qualifica desempenho sem pausas. Os documentos deste checkout receberam a conclusão após a qualificação: o ZIP conserva seu snapshot documental anterior, sem reempacotamento. O [guia de distribuição e marca](orionq_branding.md) apresenta os hashes e os limites dos gates.

## Evidência anterior: 5.60.0

A [validação de áudio e variante GEO](../results/orionq_signal_audio_geo_20261008.md) registra **31 testes de fonte do desligamento aprovados** e um diagnóstico Windows com **15 critérios aprovados**, dois ciclos naturais e reinício/cancelamento. O diagnóstico observou `Ready`, início e fim de reprodução Qt e continuidade do heartbeat: p95 de **37,514 ms** e intervalo máximo de **189,765 ms**. Essa evidência não certifica o volume percebido pelo usuário; `audibility_verified` permanece `false`. A validação específica da GEO e da nova combinação de gatilho é registrada separadamente, sem inferi-la a partir do áudio.

Os resultados e artefatos dessa rodada permanecem no [registro de 5.60.0](../results/orionq_signal_audio_geo_20261008.md). A nova ancoragem geográfica e o refinamento da cruz pertencem à manutenção 5.60.1.

## Evidência anterior: 5.59.0

A [validação do desligamento nas fontes](../results/orionq_unplug_ui_20261008.md) reúne **190 testes aprovados**, quatro cenários nativos nos dois temas e nas resoluções 1280×720 e 3440×1440, além de 16 cenários da marca. Verifica o retorno normal, a preservação da origem e identidade, o bloqueio do canvas, o descarte, ciclos repetidos e o quadro preto após pintura. As capturas usam uma fixture de interface explicitamente artificial e antecedem a rodada congelada atual.

O [registro histórico de 5.59.0](../results/orionq_unplug_ui_20261008.md) conserva a posterior compilação aprovada na rodada `5.59.0-native-20261008-223932`. Essa versão não contém o novo áudio de desligamento nem a variante visual GEO acrescentada em 5.60.0.

## Evidência anterior: 5.58.0

Os resultados desta seção pertencem à atualização **5.58.0**. Seus testes verificaram vínculo de identidade, revogação, responsividade do event loop, geração de cancelamento, geometria, interação e limpeza dos timers. A consolidação reúne **276 casos exclusivos aprovados**, usando contas e APIs Windows simuladas, serviços de fixture e estado isolado. A validação nativa confirmou a sessão Windows já vinculada, 16 layouts da marca e quatro casos da sequência visual nos dois temas. O ensaio de inicialização com serviço de demonstração real concluiu 15 casos de entrada, navegação, redimensionamento e terminal, com primeiro snapshot pronto em 26,406 segundos dentro do limite de 30 segundos.

A [validação de 5.58.0](../results/orionq_entry_ui_20261008.md) conserva também a tentativa inicial que excedeu o limite de carregamento e as medidas de responsividade: a rodada final teve intervalo de heartbeat no percentil 95 de 32 ms e intervalo máximo de 1.500 ms. A preparação permanece variável; esse resultado não qualifica a inicialização como livre de pausas. A senha Windows foi coberta com APIs simuladas; a verificação nativa usou a sessão existente, sem enviar uma senha. O áudio audível, OIDC, hardware físico e um executável 5.58.0 não foram qualificados naquela rodada. Esses resultados não validam o desligamento acrescentado em 5.59.0.

## Verificação reproduzível

Os comandos abaixo reproduzem testes de fonte e capturas de interface. A qualificação do executável empacotado usa os gates e o roteiro de aceitação da distribuição.

```powershell
python -m pytest tests\test_native_login.py tests\test_prepared_source.py
python -m pytest tests\test_console_emergency_signal.py tests\test_console_login_flow.py tests\test_console_loading_progress.py tests\test_console_node_incremental.py tests\test_console_node_window_layout.py
python -m pytest tests\test_console_disconnect_signal.py tests\test_console_login_flow.py
python -m pytest tests\test_console_signal_geo.py tests\test_console_geo_modes.py tests\test_console_geo_update_cadence.py
python tools\generate_disconnect_sound.py
```

Para repetir as capturas nativas no Windows, use o ambiente Python do projeto e diretórios novos. O primeiro comando verifica a marca e os layouts; o segundo usa uma fixture de interface sem serviço, credenciais ou estado do operador. Neste segundo diagnóstico, a sequência visual tem duração reduzida para captura e o som fica desativado.

```powershell
$env:QT_QPA_PLATFORM = "windows"
python -m quantum_autopilot.console.branding_validation outputs\entry-branding-check
python tools\validate_console_entry_visuals.py outputs\entry-visuals-check
python tools\validate_console_disconnect_audio.py --output-dir outputs\disconnect-audio-check
```

O último comando usa a duração normal do desligamento, verifica dois ciclos e registra a reprodução real de Qt em uma janela isolada, sem serviço, login ou estado do operador. `--sound-file` permite verificar o WAV extraído do pacote. Use uma nova pasta de evidências; o diagnóstico distingue reprodução Qt de confirmação humana de audibilidade.
