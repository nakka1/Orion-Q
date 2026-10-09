# OrionQ 5.59.0: desligamento do modo E.V.A.

O botão **Unplug from E.V.A.** inicia uma animação vetorial de aproximadamente 3,6 segundos: os hexágonos vermelhos preenchem a janela, a faixa central apresenta **Disconnected from E.V.A.**, os hexágonos se apagam e a superfície fica totalmente preta. O retorno ocorre depois de esse preto ser pintado e mantido por pelo menos 180 ms. Não há texto `404` ou `NOT FOUND`.

O login normal retorna com o Q laranja e satélite, o ícone original da janela e sem o marcador **eva operator connected**. A senha é limpa e a confirmação visual de login é desativada. A origem selecionada e a identidade vinculada ao backend são preservadas. O efeito não emite comandos operacionais nem altera permissões.

## Implementação

- [DisconnectSignalOverlay](../../src/quantum_autopilot/console/widgets/disconnect_signal.py): polígonos e contornos de texto nativos Qt, geometria determinística em cache limitado, timer de 33 ms e barreira de pintura antes da conclusão. A referência raster não é um recurso da aplicação.
- [BootScreen](../../src/quantum_autopilot/console/screens/boot.py): botão fixo no rodapé enquanto o modo está ativo; o marcador permanece legível mesmo quando o formulário exige rolagem.
- [ConsoleWindow](../../src/quantum_autopilot/console/shell.py) e [chrome](../../src/quantum_autopilot/console/widgets/chrome.py): retorno pelo cabeçalho, barra lateral, carregamento ou F6 usa a mesma sequência. Diálogos próprios são ocultados preservando os dados dos formulários. Marca, ícone e controles normais são restaurados ao concluir.

Durante a animação, a entrada fica bloqueada na janela coberta, incluindo o canvas em `QGraphicsProxyWidget`; outras janelas continuam independentes. Escape não interrompe o desligamento. Fechar a aplicação descarta a superfície sem navegação tardia. Timer e filtros de aplicação e de redimensionamento são removidos ao encerrar. Ciclos sucessivos reutilizam a mesma instância; os eventos científicos continuam sendo processados e persistidos no backend, e o último snapshot recebido é conservado para a apresentação seguinte.

## Ambiente e evidências

Validação em Windows 11, build 26200, Python 3.12.14 e PySide6/Qt 6.11.2. Máquina: Ryzen 7 5700X, aproximadamente 32 GiB de RAM, RTX 3060 e monitor 3440×1440, DPR 1. As janelas de diagnóstico usam estado isolado e `MockProvider` explicitamente artificial; nenhum serviço ou equipamento é aberto pelas capturas.

| Verificação | Resultado | Evidência |
| --- | --- | --- |
| Login e integração da sequência, preservação da origem e identidade, bloqueio de entrada e descarte | 28 testes aprovados | [JUnit final](../../outputs/orionq_unplug_ui_20261008/tests-final.xml) |
| Vetores, preenchimento, apagamento, geometria, cache e quadro preto após pintura | 17 testes aprovados | [Testes do overlay](../../tests/test_console_disconnect_signal.py) |
| Emergência anterior, marca, entrada, progresso, verificador de identidade e troca de tema | 145 testes aprovados | [Consolidação](../../outputs/orionq_unplug_ui_20261008/validation-summary.json) |
| Rodada final completa | **190 aprovados; zero falhas, erros ou skips; 71,671 s** | [JUnit final](../../outputs/orionq_unplug_ui_20261008/tests-final.xml) |
| Janela nativa, temas claro/escuro, 1280×720 e 3440×1440 | **4 cenários aprovados, 28 capturas** | [Relatório nativo](../../outputs/orionq_unplug_ui_20261008/native-final/entry-visuals.json) |
| Marca original, patrocínio, versão e layouts | **16 cenários nativos aprovados** | [Relatório da marca](../../outputs/orionq_unplug_ui_20261008/native-brand/branding-report.json) |
| Ruff nos novos arquivos, diagnóstico e testes; compilação dos módulos alterados | Aprovados | Comandos abaixo |

A regressão com pintura suspensa conserva a animação ativa mesmo depois de expirar sua duração: só libera o retorno após pintar preto e cumprir o tempo mínimo. As integrações também cobrem três ciclos consecutivos, redimensionamento para 3440×1440, campos de formulário em diálogos e fechamento durante a apresentação. O ícone empacotado conserva seu hash; a variante de execução é apenas em memória. O quadro preto foi confirmado pixel a pixel nas quatro capturas nativas finais. A primeira rodada de capturas ainda obtinha o último frame do apagamento, pois a pintura Qt e a apresentação pelo compositor Windows são etapas distintas. Essa rodada permanece em `outputs/orionq_unplug_ui_20261008/native`; o diagnóstico foi corrigido para aguardar a apresentação e validar todos os pixels RGB. A evidência aprovada usada aqui está em `native-final`.

As capturas nativas comprimem somente os efeitos cosméticos: emergência em 1.200 ms e desligamento em 1.800 ms, mantendo o requisito de preto pintado. O som da emergência fica desativado no diagnóstico. Os testes de identidade usam bindings e APIs Windows simulados; esta rodada não submete senha ao Windows, não qualifica hardware, não repete campanhas científicas nem executa um binário congelado. **As alterações estão nas fontes 5.59.0; o executável não foi recompilado nesta rodada.** A evidência anterior 5.58.0 permanece em seu [relatório histórico](orionq_entry_ui_20261008.md).

## Reprodução

Use o Python do ambiente do projeto. Crie os diretórios pais de `--basetemp` antes da execução e escolha destinos novos para as capturas.

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
python -m pytest -q tests/test_console_login_flow.py tests/test_console_disconnect_signal.py tests/test_console_emergency_signal.py tests/test_console_product_entry.py tests/test_console_branding.py tests/test_console_signal_brand.py tests/test_console_loading_progress.py tests/test_native_login.py tests/test_console_theme_transition.py --junitxml=outputs/unplug-check/tests.xml
python -m ruff check tools/validate_console_entry_visuals.py tests/test_console_login_flow.py src/quantum_autopilot/console/widgets/disconnect_signal.py tests/test_console_disconnect_signal.py
python -m py_compile src/quantum_autopilot/console/shell.py src/quantum_autopilot/console/widgets/chrome.py src/quantum_autopilot/console/screens/boot.py src/quantum_autopilot/console/widgets/disconnect_signal.py tools/validate_console_entry_visuals.py
$env:QT_QPA_PLATFORM = 'windows'
python tools/validate_console_entry_visuals.py outputs/unplug-native-check
python -m quantum_autopilot.console.branding_validation outputs/unplug-brand-check
```

Na interface, digite **Shinji Ikari** para acionar a sequência local. Depois, clique em **Unplug from E.V.A.**. O login padrão é restaurado ao término, sem autenticar o nome do efeito.

![Faixa central e hexágonos na janela nativa](../../outputs/orionq_unplug_ui_20261008/native-final/dark-1280x720-disconnect.png)

[Login com o botão](../../outputs/orionq_unplug_ui_20261008/native-final/dark-1280x720-signal-login.png), [quadro preto](../../outputs/orionq_unplug_ui_20261008/native-final/dark-1280x720-black.png), [login normal restaurado](../../outputs/orionq_unplug_ui_20261008/native-final/dark-1280x720-standard-login.png) e [visualização 3440×1440](../../outputs/orionq_unplug_ui_20261008/native-final/dark-3440x1440-disconnect.png).

## Recompilação e qualificação congelada 5.59.0 — rodada 223932

Este registro é posterior à validação de fontes descrita acima. O [executável OrionQ.exe](../../release/windows/5.59.0-native-20261008-223932/OrionQ/OrionQ.exe) foi recompilado e os gates congelados foram repetidos na rodada `5.59.0-native-20261008-223932`. A [consolidação final](../../outputs/orionq_build_5_59_0_20261008/frozen-release-final.json) aprovou **18 de 18 gates**, sem erros. A [aceitação automática](../../release/windows/5.59.0-native-20261008-223932/native-acceptance/acceptance-report.json) registra `automatic_passed=true`, com `integrity`, `self_test` e `synthetic_stress_20_rounds` aprovados. A revisão manual permanece registrada como `manual_review_required` / `pending`.

| Verificação | Resultado | Evidência |
| --- | --- | --- |
| Fonte, com a política de prazo corrigida | 811 execuções aprovadas; **757 testes únicos** e 54 repetições | [Consolidação da fonte](../../outputs/orionq_build_5_59_0_20261008/source-test-summary.json) |
| Correspondência estática entre fonte e congelado | 10 módulos e 8 recursos iguais; essa comparação não executa os módulos | [Identificação do congelado](../../outputs/orionq_build_5_59_0_20261008/frozen-entry-code-final.json) |
| Aceitação final isolada | 39 checks de autoteste e stress sintético de 20 rodadas; **736,084 s** | [Aceitação](../../release/windows/5.59.0-native-20261008-223932/native-acceptance/acceptance-report.json) |
| Marca, comportamento desktop e inicialização nativa | 16, 8 e 6 cenários aprovados, respectivamente | [Gates e relatórios vinculados](../../outputs/orionq_build_5_59_0_20261008/frozen-release-final.json) |
| Distribuição portátil | **4.018 arquivos; 309.820.701 bytes**; CRC e hashes de todos os membros conferidos contra o produto | [ZIP](../../release/windows/5.59.0-native-20261008-223932/OrionQ-5.59.0-native-windows-x64.zip), [registro do pacote](../../release/windows/5.59.0-native-20261008-223932/portable-package.json) |

A aceitação ocorreu em 8 de outubro de 2026, de **23:07:30,406 a 23:19:46,491, UTC−03:00**. O manifesto cobre 4.017 arquivos; o ZIP inclui também o próprio `SHA256SUMS.txt`, totalizando 4.018 membros. Os hashes SHA-256 são:

```text
OrionQ.exe: c022f83633806c7626fbf3109f7804dd3b3278cc91ce398ea9bd6295eb3c69d4
ZIP:        22121072febe2dc72a867eeb6ed472072eef3e2af398a49abb5f4db14ec2470b
```

O hash do ZIP também consta no [arquivo lateral](../../release/windows/5.59.0-native-20261008-223932/OrionQ-5.59.0-native-windows-x64.zip.sha256). A consolidação confirmou o mesmo hash do executável antes e depois das verificações.

Para esta recompilação, o prazo diagnóstico máximo passou de 900 para **3.600 segundos**, após o autoteste piloto aprovado medir 1.071,016 s; o padrão Python continua em 180 s. O build e o lançador de validação usam o prazo ampliado, com espera externa limitada a 7.320 s no build, mantendo todos os checks. O empacotador passou a incluir o guia de entrada e os relatórios de entrada/desligamento. Também foram corrigidos o tratamento de tecnologias ausentes como payload indisponível, preservando as órbitas, e o teste da apresentação de entrada OGS. O [piloto histórico](../../outputs/orionq_build_5_59_0_20261008/pilot-self-test-evidence.json) não foi usado para aprovar o novo executável; os testes históricos com falha e com o limite antigo foram excluídos dos totais atuais.

A aprovação é local e de software. No roadmap, **as 20 amostras excederam o orçamento de 10 ms**: p95 de 241,873 ms e máximo de 408,352 ms. Vinte amostras não qualificam uma garantia de p99. A inicialização congelada apresentou o primeiro snapshot entre 10,813 e 28,109 s, com intervalos máximos de heartbeat chegando a 2,360 s. Esses resultados não demonstram ganhos de desempenho nem ausência de pausas. A recepção satelital usa quadros públicos preservados e uma estação TCP local emulada; não comprova recepção RF local, rastreamento ou QKD óptica física. Outra máquina, mouse físico, audibilidade, hardware, assinatura do editor e instalador permanecem fora desta qualificação.

Esta seção foi acrescentada ao relatório do repositório após a aceitação. O produto empacotado e o ZIP foram preservados.
