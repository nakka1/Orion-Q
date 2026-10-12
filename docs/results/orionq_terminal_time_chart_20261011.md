# OrionQ: gráficos temporais no terminal — 11/10/2026

Os gráficos densos passaram na interface curses real em Ubuntu 24.04/WSL, x86_64, Python 3.12, com usuário sem privilégios. A fixture simulada isolada continha 76 componentes, 136 enlaces e 11 satélites; a saída física permaneceu bloqueada. A implementação foi validada a partir da fonte atual instalada em uma wheel privada. Executáveis e recibos da release 5.64.2 anterior permaneceram intactos.

O raster Braille oferece 2×4 posições por célula. Os pontos usam os timestamps recebidos do backend, com janela de 60 segundos e eixos visíveis; não há interpolação nem criação de amostras. Fidelidade e QBER usam domínio 0–100%; a taxa de chave conserva bit/s. Séries de protocolo usam seu próprio relógio `at_s`. Lacunas e valores inválidos têm contadores e não viram medidas inventadas.

A engine avançou pelo relógio independente do serviço até publicar 64 amostras crescentes. O readback usado pelo benchmark já tinha 66 timestamps. A seleção por teclado chegou ao enlace canônico `REP-CIMATEC-USP-08`; a tela pausada capturada posteriormente mostrava tick 68 e intervalo 8..68 s, com 61 amostras inclusivas por métrica. Os valores continuaram evoluindo entre essas publicações: não foi feita comparação pixel a pixel com o snapshot anterior.

| Verificação | Resultado |
| --- | --- |
| Regressão consolidada da fonte congelada | **467 PASS, 1 SKIP**, 87,91 s; skip de PTY no Windows, suprido pela campanha Linux |
| Campanha curses focada | **79/79 checks PASS**, dois PTYs, 11,72 s de probe |
| Campanha completa, incluindo aquecimento e verificações auxiliares | 266,54 s |
| Histórico do readback selecionado | 66 timestamps; 61 amostras na janela de cada métrica |
| Viewport 120×40 | 147 células Braille, 183 pontos ocupados, cor `d75fd7` |
| Viewport 200×60 | 183 células Braille, 183 pontos ocupados, mesma cor |
| Teclado | 35 teclas; 33 interações cronometradas: p50 **20,123 ms**, p95 **42,612 ms**, máximo 48,409 ms |
| Seleção, Enter e resize | ID canônico preservado; 120×40 → 200×60 → 80×24 → 120×40; repaint posterior ao resize verificado |
| Pausa | Apresentação congelada enquanto o backend continuava avançando |
| `NO_COLOR` e saída | Gráficos preservados; foreground/background `default`; `q` com exit 0 e termios restaurado nos dois PTYs |

O benchmark puro chamou `render_dashboard` 40 vezes em cada tamanho sobre a mesma publicação autenticada, cujo hash permaneceu igual. Ele exclui rede, avanço da engine, pintura curses e I/O:

| Tamanho | Wall p50 / p95 | CPU do processo p50 / p95 |
| --- | --- | --- |
| 80×24 | 3,064 / 3,717 ms | 3,115 / 3,777 ms |
| 120×40 | 9,753 / 13,904 ms | 9,911 / 14,145 ms |
| 200×60 | 23,276 / 39,055 ms | 23,686 / 39,747 ms |

O campo auxiliar `presentation.render_latency` mede 57 renderizações do layout plain e detalhes, com p50 1,063 ms e p95 3,022 ms. Esse conjunto tem carga diferente do benchmark dashboard e não fundamenta alegação de melhoria de CPU.

As verificações leram a tela atual pelo parser pyte 0.8.2 qualificado para REP/SU/SD, não pelo texto ANSI acumulado. Conferiram tokens separados `60s`/`0s`, eixos, três métricas, contagens de amostras, quantidade de pontos e cor efetiva. O monitor usa GETs autenticados; o probe não submete `network_step`. A configuração da entrada simulada ocorreu na preparação da fixture, antes do aquecimento.

Durante o aquecimento, antes da importação do probe e de qualquer PTY, o harness recebeu três ajustes de oráculo: tokens temporais exatos, ANSI posterior ao resize e elegibilidade por 64 triplets finitos mantendo lacunas. O [vínculo complementar](../../outputs/terminal-time-chart-20261011/harness-amendment.json) preserva o SHA inicial `2b12b46f…` e o efetivamente executado `59d440d3…`, observado no tick 61. O relatório principal conserva o hash da composição inicial; não houve alteração de produção nem da wheel.

Evidências públicas:

- [Campanha e checks](../../outputs/terminal-time-chart-20261011/campaign-v1/terminal-campaign.json), [ambiente e wheel](../../outputs/terminal-time-chart-20261011/campaign-v1/report.json), [hashes da fonte](../../outputs/terminal-time-chart-20261011/campaign-v1/source-hashes.json) e [perfil de empacotamento](../../outputs/terminal-time-chart-20261011/campaign-v1/core-artifact.json).
- [JUnit consolidado final](../../outputs/terminal-time-chart-20261011/tests-frozen-final.xml). Suítes focadas sobrepostas não foram somadas ao total consolidado.
- Previews [120×40](../../outputs/terminal-time-chart-20261011/previews-braille/00-time-chart-120x40.png) e [200×60](../../outputs/terminal-time-chart-20261011/previews-braille/01-time-chart-200x60.png), com [manifesto e hashes](../../outputs/terminal-time-chart-20261011/previews-braille/preview-integrity.json).

Os PNGs reconstruíram as células e atributos capturados do PTY; não são screenshots nativos. A identidade foi substituída explicitamente por `<test identity redacted>`, conservando parâmetros e resultados simulados. DejaVu Sans Mono 16 px desenha o texto; DejaVu Sans 13 px fornece os glyphs Braille dentro das mesmas células. A primeira tentativa em `previews-final/` fica preservada: a fonte monospace não continha Braille e mostrava caixas. O fallback corrigiu somente o preview.

A wheel privada tem SHA-256 `297e0d1d02be99dc4c98c8df3374cf23807feb0cdf18cb52143ef2eaf1046a25`; 447 módulos foram comparados entre fonte, wheel e instalação. O módulo dashboard executado tem SHA-256 `d92f36b2e34a39d59152302045ed224952cc60ae7c65561855df7466f1cd3a56`.

Reprodução da campanha, com destino novo:

```powershell
wsl.exe -d Ubuntu-24.04 -u root -- runuser -u orionq-validation -- /var/tmp/orionq-terminal-brand-e1j7wevg/venv/bin/python -I /mnt/e/Codex/2026-08-27/e/work/NISQ-Predictive-Twin/tools/validate_terminal_dashboard_linux.py --repository /mnt/e/Codex/2026-08-27/e/work/NISQ-Predictive-Twin --output /mnt/e/Codex/2026-08-27/e/work/NISQ-Predictive-Twin/outputs/terminal-time-chart-20261011/campaign-reproduction --minimum-history-samples 64 --time-chart
```

O [harness](../../tools/terminal_time_chart_pty_probe.py) atual já contém os oráculos fortalecidos. A ferramenta cria wheel, venv, credenciais e journals privados; exporta somente projeções permitidas. A dependência Linux existente deve estar disponível no caminho do comando, ou ser substituída por um ambiente compatível.

Esta campanha curta comprova funcionamento da fonte no Linux/WSL e densidade gráfica sobre publicações simuladas. Não qualifica hardware, taxas físicas de chave, terminal/fontes arbitrários, ausência de crescimento prolongado de memória, ARM, PetaLinux ou FPGA. Não foi produzida nova release de executáveis.
