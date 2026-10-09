# OrionQ 5.60.0: som de desligamento, gatilho e variante GEO

**Unplug from E.V.A.** acompanha a animação vetorial com um som original de energia diminuindo. A variante local colore o mar da GEO de vermelho e desenha uma grande cruz vermelha semitransparente; ao desligar, retorna à apresentação normal. Esses efeitos conservam a origem selecionada, a identidade, as permissões e os resultados do backend. Modelos continuam opcionais e a atuação física permanece bloqueada.

O gatilho nesta versão exige o nome **S. Ikari** e a senha exata **thirdpilot**. O nome ignora maiúsculas/minúsculas e espaços nas bordas; a senha diferencia ambos. Uma combinação incorreta não aciona o efeito, e uma senha incorreta para o nome reservado não é encaminhada ao verificador de identidade. O nome visual não é autenticado nem recebe funções ou permissões adicionais. Os relatórios anteriores conservam seus próprios gatilhos históricos.

## Áudio e ciclo de vida

[generate_disconnect_sound.py](../../tools/generate_disconnect_sound.py) sintetiza osciladores de frequência descendente e pequenos pulsos eletrônicos com rampas suaves. Não lê gravações, samples, arquivos externos ou rede. O [WAV](../../src/quantum_autopilot/console/assets/disconnect-interface.wav) é mono PCM de 16 bits, 44.100 Hz e 3,2 segundos, com 141.120 amostras e 282.284 bytes. Sua geração é determinística; o SHA-256 é `85c542f2c7b6027c85e7a794dd5f4528de3b2c0903dd5c1c7ceb5f89ca417456`.

[DisconnectSignalOverlay](../../src/quantum_autopilot/console/widgets/disconnect_signal.py) recebe `sound_enabled`, cria `QSoundEffect` apenas quando necessário e solicita uma reprodução por ciclo. O carregamento do WAV é assíncrono: `statusChanged` inicia a reprodução em `Ready` somente enquanto a superfície está ativa e visível. O efeito visual não aguarda a decodificação nem o áudio terminar. Notificações tardias depois de cancelamento ou conclusão não iniciam reprodução. Parar, ocultar e finalizar interrompem o som; descartar desconecta o callback e libera a instância Qt. Falhas parciais durante a carga também liberam o recurso.

A sequência visual mantém aproximadamente 3,6 segundos e a barreira anterior: o preto precisa ser efetivamente pintado antes de contar **180 ms** de permanência. Ausência de WAV, QtMultimedia ou saída de áudio, erro de carregamento e falhas de reprodução não bloqueiam o retorno ao login. O gatilho compara a senha visual pública exata, sem autenticar uma conta Windows, decodificar telemetria ou executar uma operação científica.

## Evidências concluídas nas fontes

| Caso | Resultado | Evidência |
| --- | --- | --- |
| Geometria, pintura preta, bloqueio de entrada e ciclo de vida existentes; síntese, carga assíncrona, uma reprodução por início, notificações tardias, cancelamento e falhas de áudio | **31 testes aprovados**, 4,98 s | [JUnit final](../../outputs/orionq_unplug_sound_20261008/tests-disconnect-final.xml) |
| Nova combinação de nome/senha visual, entrada, confirmação de identidade, marca, tema e carregamento | **180 testes aprovados** | [JUnit da entrada](../../outputs/orionq_build_5_60_0_20261008/tests-entry-final.xml) |
| Mar e cruz em Mapa/Globo, restauração exata da paleta normal, seleção, órbitas, geometria de clique, alternância exclusiva e cadência | **41 testes aprovados**, 7,76 s; oito testes específicos da variante | [JUnit GEO](../../outputs/orionq_signal_geo_20261008/tests-final.xml) |
| Empacotamento e aceitação, incluindo o prazo diagnóstico existente | **57 testes aprovados** | [JUnit do pacote](../../outputs/orionq_build_5_60_0_20261008/tests-packaging-final.xml) |
| Consolidação dos quatro grupos acima | **309 testes únicos aprovados**, sem repetições, falhas, erros ou skips; nenhum teste histórico contado | [Resumo de fonte](../../outputs/orionq_build_5_60_0_20261008/source-test-summary.json) |
| Fluxo nativo com a nova combinação visual, marca, preenchimento, quadro preto e retorno ao login nos temas claro/escuro e resoluções 1280×720 e 3440×1440 | **4 cenários aprovados**, 28 capturas | [Relatório de entrada nativa](../../outputs/orionq_build_5_60_0_20261008/native-entry/entry-visuals.json) |
| Apresentação GEO com variante e globo normal restaurado nos dois temas e resoluções 1280×720 e 3440×1440 | **12 capturas de referência**, origem artificial explicitamente identificada | [Fixture e arquivos](../../outputs/orionq_signal_geo_20261008/source-visuals/fixture.json) |
| Dois ciclos naturais de 3,6 s, reutilização da instância, reinício e cancelamento, PCM não silencioso e liberação de recursos | **15 critérios aprovados**; `playback_verified: true` | [Diagnóstico nativo Windows](../../outputs/orionq_unplug_sound_20261008/native-audio/disconnect-audio.json) |
| Continuidade do event loop durante reprodução e desenho | 327 intervalos de heartbeat, período solicitado de 25 ms; **p95 37,514 ms**, máximo **189,765 ms** | [Mesmo diagnóstico](../../outputs/orionq_unplug_sound_20261008/native-audio/disconnect-audio.json) |
| Correção crítica Python nos arquivos de implementação e testes | Ruff aprovado | Verificação executada junto da rodada final de testes |

O diagnóstico nativo durou 8,419 s, usando Qt Windows e uma janela isolada de 960×540. Registrou `Loading`, `Ready`, `playing=true` e `playing=false`, dois callbacks naturais únicos, interrupção no reinício e ausência de callback de conclusão no cancelamento. Não abriu serviço, tela de login, estado do operador ou equipamento. **`audibility_verified` permanece `false`**: a evidência confirma reprodução solicitada e executada por Qt, sem medição por microfone ou confirmação humana do som percebido.

O fluxo nativo de entrada usa `MockProvider` explicitamente artificial, sem serviço, autenticação Windows ou estado do operador. Somente para capturar a sequência, reduz as durações cosméticas de conexão e desligamento a 1.200 e 1.800 ms, com o áudio desativado; o quadro preto continua sujeito à pintura e ao tempo mínimo. O diagnóstico separado de áudio usa a duração normal e a saída Qt real. A rodada inicial de empacotamento encontrou 56 erros de preparação por inacessibilidade do diretório TEMP compartilhado; a execução final usa `--basetemp` isolado no workspace. Essa tentativa não integra os 309 resultados aprovados e permanece identificada no resumo de fonte.

## Variante GEO e compilação

A variante da GEO é uma apresentação em memória, separada dos dados físicos e da função do dispositivo. O mar recebe a tonalidade vermelha, e a cruz vetorial usa opacidade de 25%, posicionada no topo central do Mapa ou partindo do norte da esfera no Globo. Os marcadores de satélite e solo, enlaces, destaques e áreas de clique preservam seus estilos e funções. `GeoMap.set_signal_mode` conserva esse estado durante a alternância dos renderizadores exclusivos; a alteração atua sobre a camada base, sem substituir modelos ou gerar eventos científicos. Não transforma a cruz em capacidade de comunicação nem modifica a propagação orbital ou a disponibilidade dos enlaces.

Os testes GEO verificam restauração exata do raster normal, conservação de modelo e câmera, órbitas, overlay, posição dos marcadores, áreas de clique e ownership do renderizador. As capturas abaixo usam a geometria sintética de [test_console_signal_geo.py](../../tests/test_console_signal_geo.py), sem executar o backend ou a física. Mostram o layout, não uma rede operacional.

![Mapa com mar vermelho e cruz vetorial](../../outputs/orionq_signal_geo_20261008/source-visuals/map-dark-1280x720.png)

![Globo com a base da cruz no norte da esfera](../../outputs/orionq_signal_geo_20261008/source-visuals/globe-dark-3440x1440.png)

Compare o [globo normal restaurado](../../outputs/orionq_signal_geo_20261008/source-visuals/globe-normal-dark-3440x1440.png). A [lista de 12 capturas](../../outputs/orionq_signal_geo_20261008/source-visuals/fixture.json) inclui também o tema claro e a resolução 1280×720.

A rodada está identificada em [release-plan.json](../../outputs/orionq_build_5_60_0_20261008/release-plan.json), com destinos `release/windows/5.60.0-native-20261008-233510/OrionQ/OrionQ.exe` e `OrionQ-5.60.0-native-windows-x64.zip`. A qualificação final local foi concluída em 9 de outubro de 2026, com os 20 gates obrigatórios aprovados, `automatic_passed: true` e correspondência entre fontes, EXE, manifesto e ZIP. Os detalhes estão na seção de qualificação abaixo. A preparação Git ocorre depois da geração e da aceitação dessa versão.

Os [resultados de 5.59.0](orionq_unplug_ui_20261008.md) e [5.58.0](orionq_entry_ui_20261008.md) permanecem históricos; seus testes não são contabilizados novamente nesta rodada. O [guia de entrada](../product/orionq_entry_ui.md) e o [guia da distribuição](../product/orionq_branding.md) descrevem o comportamento e a qualificação atual.

As medições de heartbeat pertencem ao pequeno diagnóstico cosmético isolado e não demonstram ganho de desempenho sob a topologia global, nem ausência de pausas na simulação. Outra máquina, audibilidade humana, autenticação Windows por senha real e equipamento físico não foram qualificados por estas fixtures. As evidências de fonte não substituem a aceitação congelada da nova versão.

## Reprodução

Use o ambiente Python do projeto, com Qt instalado, e uma pasta nova para cada diagnóstico. O primeiro comando gera exatamente o som original; o teste automatizado emula o áudio para não tocar sons em CI. O diagnóstico Windows usa a saída de áudio padrão disponível e conserva sua descrição no relatório.

```powershell
python tools\generate_disconnect_sound.py
$env:QT_QPA_PLATFORM = 'offscreen'
python -m pytest tests\test_console_disconnect_signal.py -q --junitxml outputs\disconnect-check\tests.xml
python -m pytest tests\test_console_signal_geo.py tests\test_console_geo_modes.py tests\test_console_geo_update_cadence.py -q --junitxml outputs\geo-signal-check\tests.xml
$env:QT_QPA_PLATFORM = 'windows'
python tools\validate_console_disconnect_audio.py --output-dir outputs\disconnect-native-check
```

Para verificar o WAV distribuído sem substituí-lo no checkout:

```powershell
python tools\validate_console_disconnect_audio.py --output-dir outputs\disconnect-package-check --sound-file release\windows\5.60.0-native-20261008-233510\OrionQ\_internal\quantum_autopilot\console\assets\disconnect-interface.wav
```

Esse comando continua executando o diagnóstico Python de fonte sobre o WAV extraído do pacote. A execução do código congelado exige os gates e as evidências do build; não é demonstrada apenas por fornecer `--sound-file`.

## Qualificação final local

O [relatório consolidado](../../outputs/orionq_build_5_60_0_20261008/frozen-release-final.json) confirma **20/20 gates obrigatórios aprovados** para o mesmo EXE. A [aceitação nativa](../../release/windows/5.60.0-native-20261008-233510/native-acceptance/acceptance-report.json) registra `automatic_passed: true`. O pacote e os relatórios das rodadas anteriores foram preservados.

| Verificação | Resultado |
| --- | --- |
| Correspondência de código congelado e recursos atuais | 15 módulos e nove recursos correspondentes às fontes |
| Regressões atuais das fontes | 309 testes únicos aprovados; nenhuma execução histórica contabilizada |
| Marca, versão, ícone da barra de tarefas e instância única | Gates congelados aprovados |
| Roadmap, modelos opcionais e campanha temporal | Gates aprovados; pacotes externos de modelos não exigidos |
| Autoteste completo | Rodada inicial e repetição na aceitação aprovadas |
| Laboratório, satélite/OGS, telemetria clássica e rede global | Campanhas automatizadas aprovadas, dentro de seus escopos simulados/emulados documentados |
| Estresse | Duas execuções de 20 ciclos aprovadas |
| Inicialização Windows | Seis cenários aprovados, incluindo escalas 100%, 125%, 150% e 200% |
| WAV empacotado | 15 critérios aprovados com Qt nativo; `playback_verified: true`, `audibility_verified: false` |
| ZIP portátil | 4.020 arquivos; 310,092,254 bytes; CRC, manifesto e SHA-256 conferidos |

SHA-256 do EXE: `093b23fedf2a3d42b52ab5901a9c13ff9bf3bed4d99efbe3c8c18e26d2f6387d`.

SHA-256 do ZIP: `b98a3fed90c0cfa3b0d29c4787cf17317acc993a5e260dde3567c83502cd72fd`.

A aceitação automática durou 722.004 segundos. Sua classificação permanece `local_software_checks_only`, com `manual_review: pending` e `independent_machine_verified: false`: não houve qualificação de outra máquina, confirmação acústica humana, credenciais reais de login ou equipamentos físicos. Atuação física permanece bloqueada. Os diagnósticos cosméticos nativos usam código de fonte; a correspondência dos módulos compilados foi verificada separadamente.

Os gates funcionais não estabelecem um limite de tempo real: na amostra de 20 ciclos do roadmap, todos excederam o orçamento solicitado de 10 ms. A média observada foi 193,305 ms, p95 de 209,495 ms e máximo de 224,692 ms, sem amostra suficiente para qualificar p99. Estes efeitos visuais não constituem uma nova otimização do cálculo científico ou da topologia global.

Na cópia Git independente, os 405 registros de recursos vendorizados e dois recursos de build restauráveis foram conferidos por SHA-256. Seus imports foram resolvidos na própria cópia, e 26 testes de verificação passaram. O material de entrega inclui `RELEASE-VERIFICATION.json` com um resumo dos gates e hashes; os outputs completos permanecem no workspace e não entram no histórico novo.
