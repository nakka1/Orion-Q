# OrionQ 5.60.1: ancoragem geográfica, base e brilho da cruz GEO

No modo visual E.V.A., a cruz passa a acompanhar a mesma referência geográfica no Mapa e no Globo: **longitude 0° e latitude 55° N**. A forma ficou mais alongada, a base mais estreita, e o brilho vermelho quente permanece dentro da silhueta. Esse desenho original vetorial segue a inspiração visual solicitada pelo usuário para o Terceiro Impacto, sem usar imagens, clipes ou sons da obra como recursos deste ajuste. O modo normal, os marcadores, a seleção, os enlaces, as órbitas e os dados do backend conservam suas funções. O gatilho **S. Ikari / thirdpilot**, o áudio original de desligamento e a identidade operacional não foram alterados por esta manutenção. Modelos continuam opcionais e a atuação física permanece bloqueada.

## Referência comum e visibilidade

[signal_geography.py](../../src/quantum_autopilot/console/widgets/signal_geography.py) define `SIGNAL_CROSS_LONLAT = (0.0, 55.0)`, em ordem longitude/latitude. Trata-se de uma referência decorativa; nenhum comunicador, satélite ou estrutura física é cadastrado nesse ponto.

No [Mapa](../../src/quantum_autopilot/console/widgets/geo_map.py), a transformação geográfica da referência determina a base. `map_cross_rect` usa a altura de referência `SIGNAL_CROSS_HEIGHT_M = 2_548_400.0` multiplicada por pixels por metro para acompanhar o zoom. O valor em metros define apenas a escala visual; não é a dimensão de um objeto real. Pan e zoom alteram a apresentação da mesma coordenada. O renderer descarta a pintura quando o retângulo da cruz não intersecta o enquadramento.

No [Globo](../../src/quantum_autopilot/console/widgets/orbit_globe.py), `signal_cross_vector` calcula uma referência ECEF esférica da mesma coordenada, com raio de 6.371 km e cache limitado. A projeção usada para a Terra e os demais componentes determina a base e a profundidade. Quando `depth < 0`, a referência está no hemisfério posterior e a cruz não é pintada. `globe_cross_rect` usa a altura de referência proporcional ao raio visual, com **piso de 54 pixels** ao afastar. O piso favorece legibilidade e não altera a localização da base. A pintura também verifica a interseção com o enquadramento.

A orientação vertical da decoração é uma apresentação em tela; sua base permanece associada à referência geográfica. Ela não representa atitude, direção de transmissão ou telemetria de um dispositivo. A alternância Mapa/Globo continua usando o estado compartilhado e um único renderizador ativo.

## Forma, brilho e recursos

A forma usa largura/altura de **0,72**, em lugar de 0,88, para uma proporção mais alongada. A base normalizada ocupa **10% da largura**, entre `x = 0.45` e `x = 0.55`, ante os 28% anteriores. O corpo permanece fino, com um leve alargamento até a base; as três pontas continuam afiladas. O `QPainterPath` normalizado mantém cache de uma entrada.

Um único preenchimento com `QLinearGradient` horizontal combina o vermelho-base **`#F02D40`** com o núcleo quente **`#FF8B78`**, mantendo **alpha 0,25** em todos os stops. A cor-base permanece nos extremos e até 43%/a partir de 57% da largura; o centro é mais claro. O brilho continua discreto e limitado à silhueta pelo próprio path: não há halo, blur, strobe, filtro gráfico, widget ou timer adicional. A camada base continua sendo reutilizada pelos renderizadores, sem cálculos ou eventos do backend.

## Evidência concluída

| Verificação | Resultado | Evidência |
| --- | --- | --- |
| Forma afinada e alongada, núcleo quente, alpha, base na coordenada comum, escala visual e piso de 54 pixels | Aprovada na revisão final | [Testes](../../tests/test_console_signal_geo.py) e JUnit abaixo |
| Pan/zoom, ocultação no hemisfério posterior, culling, restauração exata da apresentação normal, conservação de geometria e seleção | Aprovada na revisão final | [JUnit GEO final](../../outputs/orionq_geo_anchor_20261009/tests-inspired.xml) |
| Alternância exclusiva, estado compartilhado e cadência GEO | Aprovada junto das regressões existentes | [JUnit GEO final](../../outputs/orionq_geo_anchor_20261009/tests-inspired.xml) |
| Rodada GEO elegível desta manutenção | **43 testes aprovados**, sem falhas, erros ou skips; **7,61 s** | [tests-inspired.xml](../../outputs/orionq_geo_anchor_20261009/tests-inspired.xml) |
| Áudio e ciclo de vida do desligamento | **31 testes aprovados** na rodada atual | [JUnit do áudio](../../outputs/orionq_build_5_60_1_20261009/tests-disconnect-final.xml) |
| Gatilho visual, entrada, marca, identidade, tema e carregamento | **180 testes aprovados** na rodada atual | [JUnit da entrada](../../outputs/orionq_build_5_60_1_20261009/tests-entry-final.xml) |
| Empacotamento e aceitação, mantendo a política de prazo | **57 testes aprovados** na rodada atual | [JUnit do pacote](../../outputs/orionq_build_5_60_1_20261009/tests-packaging-final.xml) |
| Consolidação dos quatro grupos atuais | **311 testes únicos aprovados**, sem repetições, falhas, erros ou skips; nenhum histórico contado | [Resumo de fonte](../../outputs/orionq_build_5_60_1_20261009/source-test-summary.json) |
| GEO de fonte com o plugin Qt Windows: ancoragem, ocultação, brilho, escala, restauração e descarte | **28 casos aprovados**, 38 PNGs; versão 5.60.1 | [evidence.json da revisão final](../../outputs/orionq_geo_anchor_20261009/native-windows/evidence.json) |
| Inspeção visual da GEO final em Qt Windows | Dois previews inspecionados no diagnóstico; mais dois na revisão da raiz | [visual-qa.json](../../outputs/orionq_geo_anchor_20261009/native-windows/visual-qa.json) e descrição abaixo |
| Entrada nativa, gatilho, marca, quadro preto e retorno ao login nos dois temas e resoluções | **4 cenários aprovados**, 28 capturas; `frozen: false` | [Entrada nativa 5.60.1](../../outputs/orionq_build_5_60_1_20261009/native-entry/entry-visuals.json) |
| Ruff nos arquivos verificados | Aprovado | Verificação executada com a rodada final |

Somente `tests-inspired.xml` corresponde à revisão GEO final, incluindo o núcleo quente, a proporção 0,72 e o piso de 54 pixels. A primeira rodada, `tests-final.xml`, teve 42 aprovações e uma falha de fixture: o enquadramento global ainda incluía a âncora usada no caso de exclusão. `tests-approved.xml` registrou 43 aprovações antes do ajuste do piso; `tests-current.xml` registrou 43 antes do último refinamento visual. Os três permanecem históricos e **não são somados** aos resultados atuais. A consolidação conta apenas os quatro XMLs elegíveis desta rodada, 31 + 180 + 43 + 57 = 311. Os resultados de 5.60.0 permanecem separados no [relatório anterior](orionq_signal_audio_geo_20261008.md).

Os testes usam geometria sintética identificada e validam a apresentação e o ciclo de vida, sem qualificar uma infraestrutura física. Uma rodada anterior produziu 38 PNGs e 28 verificações com o plugin **Qt offscreen**, apesar de executada em uma máquina Windows. Esse [material anterior](../../outputs/orionq_geo_anchor_20261009/native-visuals/evidence.json) antecede o refinamento quente e alongado; não qualifica a revisão atual nem a apresentação pelo plugin Qt Windows.

A validação com **Qt Windows** da revisão final registra `approved: true`, versão 5.60.1, 28 casos e 38 PNGs. Usa `QWidget.grab` de componentes de fonte em tamanhos lógicos **1280×720 e 3440×1440**, DPR 1, montados em uma janela host **960×640**. São capturas dos componentes lógicos solicitados, e não screenshots do console completo, do monitor inteiro ou de um executável congelado. A fixture usa geometria artificial de `tests/test_console_signal_geo.py::model` e geografia local `load_world`; não inicia backend, autenticação, estado do operador, aquisição científica ou equipamento.

O diagnóstico conserva hashes de fonte durante a captura e verifica a projeção da âncora, ocultação posterior, piso de 54 pixels, restauração exata do raster normal e preservação do modelo, seleção, trajetórias recebidas e hits de entrada. Nas quatro combinações de tema/tamanho, o descarte deixa zero instâncias de renderer, interrompe os timers próprios e libera o raster de transição. A inspeção [visual-qa.json](../../outputs/orionq_geo_anchor_20261009/native-windows/visual-qa.json) examinou o globo escuro 1280×720 de frente e o mapa claro 3440×1440 após pan/zoom. A revisão da raiz examinou adicionalmente o [mapa escuro 1280×720 após pan/zoom](../../outputs/orionq_geo_anchor_20261009/native-windows/map-dark-1280x720-pan-zoom.png) e o [globo escuro 1280×720 girado](../../outputs/orionq_geo_anchor_20261009/native-windows/globe-dark-1280x720-rotated.png).

![Globo final com brilho quente e base ancorada](../../outputs/orionq_geo_anchor_20261009/native-windows/globe-dark-1280x720-front.png)

![Mapa final claro após pan e zoom](../../outputs/orionq_geo_anchor_20261009/native-windows/map-light-3440x1440-pan-zoom.png)

A [entrada nativa](../../outputs/orionq_build_5_60_1_20261009/native-entry/entry-visuals.json), schema `orion.entry-visuals.v2`, versão 5.60.1, registra `passed: true` e `frozen: false`. Os quatro cenários usam `MockProvider` artificial nos dois temas e tamanhos, gerando 28 capturas. Para captura, somente as durações cosméticas são reduzidas a 1.200 ms na conexão e 1.800 ms no desligamento, com áudio desativado; a barreira de pintura preta continua exigida. Não autentica contas nem acessa estado do operador.

A aprovação dos componentes de fonte, inclusive pelo plugin Qt Windows, não é inferida como aceitação completa do executável. Esta rodada não demonstra ganho de desempenho sob carga nem ausência de pausas na simulação. As verificações congeladas e a conclusão automática local são registradas separadamente abaixo.

## Compilação e qualificação automática local

O [OrionQ.exe 5.60.1](../../release/windows/5.60.1-native-20261009-061920/OrionQ/OrionQ.exe) foi compilado na rodada `5.60.1-native-20261009-061920`. O SHA-256 conferido é `fd1537c637aa7bbde4f63584854de4c211e6cd6dfa7f3cd68c0c1774e56866c1`, igual antes e depois das verificações. O [consolidado final](../../outputs/orionq_build_5_60_1_20261009/frozen-release-final.json), schema `orion.frozen-release-consolidation.v1`, registra **20 gates obrigatórios, 20 aprovados e zero erros**, `passed: true`. Essa aprovação tem escopo de software local; a revisão manual permanece pendente.

| Fase concluída | Resultado | Evidência |
| --- | --- | --- |
| Comparação estática de código e recursos selecionados com o executável | **15 módulos e 9 recursos correspondentes**, `passed: true`, hash do EXE igual antes/depois | [frozen-entry-code-final.json](../../outputs/orionq_build_5_60_1_20261009/frozen-entry-code-final.json) |
| Comportamento desktop congelado | **8 casos aprovados**; vinculados ao mesmo hash do EXE | [Relatório desktop](../../release/windows/5.60.1-native-20261009-061920/desktop-behavior-validation/report.json) |
| Roadmap congelado | Comparações 30/120/250 e execução isolada de 20 ciclos aprovadas funcionalmente | [Relatório roadmap](../../release/windows/5.60.1-native-20261009-061920/roadmap-validation/report.json) |
| Campanha congelada | `passed: true`; vinculada ao mesmo hash do EXE | [Relatório de campanha](../../release/windows/5.60.1-native-20261009-061920/campaign-validation/report.json) |
| Autoteste do executável congelado | **39 critérios aprovados**, `passed: true`, `qt_platform: offscreen` | [self-test.json](../../release/windows/5.60.1-native-20261009-061920/self-test.json) |
| ZIP e manifesto portátil | CRC e hashes dos membros verificados; correspondência com a pasta do pacote | [portable-package.json](../../release/windows/5.60.1-native-20261009-061920/portable-package.json) e consolidado final |
| Aceitação automática nativa | `automatic_passed: true`; revisão manual pendente | [acceptance-report.json](../../release/windows/5.60.1-native-20261009-061920/native-acceptance/acceptance-report.json) |
| Autoteste repetido na aceitação | **39 critérios aprovados**, Qt **offscreen**, sem somar como novos testes únicos de fonte | [Autoteste da aceitação](../../release/windows/5.60.1-native-20261009-061920/native-acceptance/self-test.json) |
| Áudio com o WAV empacotado | **15 critérios aprovados**, `playback_verified: true`, `audibility_verified: false` | [disconnect-audio.json](../../release/windows/5.60.1-native-20261009-061920/disconnect-audio-validation/disconnect-audio.json) |

A comparação estática lê os módulos e recursos empacotados, sem executar o código congelado, iniciar GUI ou acessar o estado do operador. Seu escopo é o conjunto selecionado de 15 módulos e nove recursos; ela não substitui a execução dos diagnósticos do programa.

Os 39 critérios do autoteste executaram o programa congelado com Qt **offscreen**. São evidências distintas dos 28 casos e 38 capturas de componentes GEO executados a partir da fonte com Qt **Windows**. Nenhuma dessas rodadas comprova, por si só, a interação manual com o console completo, o compositor do desktop ou uma integração física.

O runtime do roadmap registrou **20 amostras, sem tentativas malsucedidas**, mas **20 de 20 excederam o orçamento de 10 ms**. A média foi **184,152 ms**, o p95 **200,825 ms** e o máximo **207,961 ms**. `adequate_p99_sampling` permanece `false`: vinte amostras não qualificam uma garantia de p99. A aprovação funcional do gate não demonstra cumprimento do orçamento, ganho de desempenho ou ausência de pausas.

A [aceitação nativa](../../release/windows/5.60.1-native-20261009-061920/native-acceptance/acceptance-report.json) concluiu em **721,877 s**, com `automatic_passed: true`, `manual_review: pending` e `acceptance_status: manual_review_required`. O consolidado contabiliza cada gate exigido, incluindo regressões de fonte, diagnósticos congelados, empacotamento e aceitação, sem converter esses escopos diferentes em uma soma de testes únicos. O autoteste não embute hash do executável: sua vinculação é a pasta oficial da rodada e o pacote conferido pela aceitação. O diagnóstico de áudio usa o widget de fonte com Qt nativo e o WAV empacotado; os módulos congelados foram comparados separadamente. Não comprova execução audível no dispositivo do usuário.

Outra máquina, entrada física por mouse, audibilidade, hardware e segurança experimental QKD continuam **não qualificados**. A validação satelital usa frames públicos preservados e uma estação TCP local emulada; não comprova recepção RF local, rastreamento ou QKD óptico. Material de ciência e laboratório simulado não constitui chave operacional certificada. Os modelos permanecem opcionais e a atuação física bloqueada. A correspondência de hashes e CRC identifica alterações ou corrupção; não substitui assinatura do distribuidor ou atestação independente.

## Distribuição e reprodução

A distribuição concluída está em `release/windows/5.60.1-native-20261009-061920/`. O [ZIP portátil](../../release/windows/5.60.1-native-20261009-061920/OrionQ-5.60.1-native-windows-x64.zip) tem **310.101.637 bytes**, **4.021 arquivos**, SHA-256 **`3c0aab4a5fa2450d372e396d3bb70dec83c0e5e745394e07b3c6fea40fcee4d2`** e CRC de todos os membros verificado. O manifesto interno registra 4.020 arquivos, além do próprio manifesto. O [portable-package.json](../../release/windows/5.60.1-native-20261009-061920/portable-package.json) e o [consolidado final](../../outputs/orionq_build_5_60_1_20261009/frozen-release-final.json) vinculam esse arquivo ao EXE qualificado. O [guia da marca e distribuição](../product/orionq_branding.md) e o [guia de entrada](../product/orionq_entry_ui.md) descrevem a versão atual.

Este relatório e os guias no checkout receberam a conclusão **após** a qualificação automática. A documentação dentro do ZIP mantém o snapshot preparado **antes** da aceitação final; o ZIP, a pasta `OrionQ` e seus manifestos não foram alterados ou reempacotados por esta atualização. Use os relatórios finais externos ao ZIP para conferir a conclusão registrada nesta seção.

Para repetir o escopo de fonte, use o ambiente Python do projeto com Qt instalado e uma pasta nova de evidências. Crie o diretório pai antes de definir `--basetemp`:

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
New-Item -ItemType Directory -Force outputs\geo-anchor-check | Out-Null
python -m pytest tests\test_console_signal_geo.py tests\test_console_geo_modes.py tests\test_console_geo_update_cadence.py -q --basetemp outputs\geo-anchor-check\pytest --junitxml outputs\geo-anchor-check\tests.xml
python -m ruff check src\quantum_autopilot\console\widgets\signal_geography.py tests\test_console_signal_geo.py
```

Na interface, acione o modo local com **S. Ikari** e a senha exata **thirdpilot**. Após a sequência, substitua o nome visual pela identidade Windows e confirme normalmente essa identidade para abrir o workspace. Na GEO, enquadre a referência de longitude 0° e latitude 55° N, alterne Mapa/Globo, aproxime, afaste e gire a Terra: a base deve continuar ligada à mesma referência, e a cruz deve desaparecer quando essa referência passar ao hemisfério posterior. Enquadramentos locais que não abrangem a referência podem mostrar apenas o mar vermelho. Ao usar **Unplug from E.V.A.**, a sequência de desligamento retorna ao login e restaura a aparência normal, sem alterar permissões ou resultados científicos.
