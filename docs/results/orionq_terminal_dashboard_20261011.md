# OrionQ — dashboard de terminal Linux, 11 de outubro de 2026

O dashboard curses passou **218/218 verificações PTY** no Linux real. A consolidação da fonte final passou **404 testes, com um skip**, em 86,26 s. O skip é o teste PTY no host Windows; a interação foi executada no destino Linux. Suítes sobrepostas não são somadas.

A interface apresenta cards, tabelas navegáveis, inspector, gráficos de fidelidade/QBER/taxa de chave e journal recebido do backend. Em 80 colunas usa gráficos compactos; janelas largas separam tabela e inspector. IDs longos conservam prefixo e quatro caracteres finais, distinguindo repetidores numerados; Enter revela o ID canônico. `--layout auto|plain|dashboard` seleciona o layout, e `v` alterna durante a sessão.

Esta entrega qualifica a fonte atual e um **wheel privado de teste**. Executáveis, wheel, exports Git e recibos da release 5.64.2 anteriormente selada permaneceram preservados.

## Resultado principal

Ubuntu 24.04 x86_64 no WSL, Python 3.12.3, ncurses, pyte 0.8.2, `TERM=xterm-256color`, locale `C.UTF-8`, UID/GID 10001. Operador e serviço são processos independentes dos módulos instalados, lançados sem shell, com HTTPS/mTLS. A fixture é autocriada, isolada e simulada, com atuação física bloqueada: **76 nodes, 136 links e 11 satélites**. Mixed recebeu onze nodes, 1.970 observações normalizadas e dois pedidos; `reserved`, `blocked` e `quantum_result: null` foram comparados com readback autenticado.

| Medição | Resultado |
| --- | --- |
| Campanha principal / fase PTY | PASS / 218 de 218 checks |
| Entrada / sessões | 1.332 teclas, 180 trocas, quatro PTYs |
| Duração total / PTY | 113,194 s / 60,034 s |
| Latência agregada, 166 observações | p50 35,855 ms; p95 301,103 ms; máximo 650,743 ms |
| Teclas comuns, 92 observações | p50 19,891 ms; p95 45,281 ms; máximo 650,743 ms |
| Scroll original até fim + UP | 624 DOWN em detalhes; 560 DOWN em raw; ambos PASS |
| NO_COLOR | Foreground/background `default`; sem SGR 37/40 |
| Saída e restauração | q=0; Ctrl+C=130; termios restaurado nas quatro sessões |
| Janela real de 3,003 s do operador | 360 ms CPU; média 11,990% de um CPU; 1.143 bytes ANSI; zero clear total |

A latência agregada inclui os períodos fixos de **300 ms** para observar rajadas. A distribuição de teclas comuns exclui esses períodos e mantém esperas de publicação/TLS. A janela de CPU cobre somente três segundos do operador, incluindo TLS e apresentação, com tick visível 10→11; exclui o core. Não é qualificação prolongada de CPU/memória.

Foram verificados oito views por 1–8 e Tab; setas xterm, wrap e seleção compartilhada Overview/Links; Enter com registros completos; detalhes de todos os onze satélites; Mixed N01 com sessão/clock/coincidências recebidos; os dois recibos de Reservations; ajuda após seleção distante; resize 80×24, 120×40, 200×60, 40×10, 1×1 e retorno. Em 1×1 exige processo vivo e recuperação, sem exigir painéis impossíveis nessa geometria.

A pausa segurou o display enquanto o backend avançava; a retomada e `r` receberam dados autenticados. Suspensão temporária somente do servidor da fixture produziu STALE, com ajuda responsiva e reconexão sem reiniciar o operador. O fluxo headless também verificou revogação, rejeição anônima e novo observador com o core avançando. As rajadas permaneceram **16 DOWN a cada 300 ms**, sem sincronização por lote para facilitar o teste; UP no fim mudou o viewport dentro de 3 s.

## Renderização e limites

O benchmark executou **1.080 chamadas em 27 combinações**, 40 por combinação: oito views dashboard × três geometrias, mais Overview plain. A publicação autenticada permaneceu byte a byte inalterada. Continha 261 séries publicadas, uma contagem de dados gráficos que não equivale a sessões físicas nem mede alocação quântica.

| Overview | Dashboard wall p50 / p95 (ms) | Plain wall p50 / p95 (ms) | Modelo CPU plain 10 Hz / dashboard 1 Hz (ms/s) |
| --- | ---: | ---: | ---: |
| 80×24 | 2,885 / 3,587 | 0,301 / 0,434 | 3,015 / 2,878 |
| 120×40 | 7,826 / 9,954 | 0,396 / 0,622 | 3,950 / 7,811 |
| 200×60 | 15,959 / 20,902 | 0,439 / 0,600 | 4,396 / 15,936 |

O dashboard custa mais layout por chamada. A fonte pinta em publicação/entrada e atualiza idade até 1 Hz, atualizando linhas alteradas. O modelo multiplica somente CPU p50 do renderer por uma cadência assumida; exclui core, rede, curses e pinturas adicionais por publicação/entrada. Não mede utilização total nem sustenta uma afirmação geral de que dashboard é mais rápido que plain. Os números são desta máquina/fixture, sem claims de hardware, throughput científico, FPS completo ou prazo de atuação física. Campanhas curtas não qualificam ausência de crescimento prolongado de memória, nem execução em ARM, PetaLinux ou FPGA.

## Parser e tentativas preservadas

As assertions inspecionam o **viewport atual**, sem procurar texto no histórico ANSI nem substituir módulos da aplicação. O pyte original não implementa CSI `b` (REP) e `S`/`T` (scroll up/down), definidos no [protocolo primário xterm](https://invisible-island.net/xterm/ctlseqs/ctlseqs.html). O harness acrescentou essas operações, qualificadas por **17 casos golden** de texto conhecido, atributos, contagens omitida/0/1, resize, margens, cursor preservado e blanks.

A campanha final consumiu **11 SU, 3 SD e zero REP**. Dois parsers acompanharam simultaneamente os mesmos bytes: o stock deixou resíduos e não reconheceu os dois IDs em Reservations; o qualificado mostrou ambos, com divergência em nove linhas. Já havia um SU antes dessa transição. A campanha pública contém contagens, booleans e hashes das telas, sem corpo privado. REP não explicou esse incidente; o scroll ausente explicou a divergência observada.

O controle independente **TERM=vt100 com stock pyte** passou **50/50 checks**, 1.889 teclas, 152 trocas e 43,510 s. Repetiu oito views/Tab, Mixed N01, ambos os recibos/Enter de Reservations e rajadas originais até fim+UP, q e termios, sem CSI S/T/b. Esse controle usa fonte **v4**, anterior à correção NO_COLOR; a principal final-v6 usa fonte **v5**.

As tentativas permanecem reprovadas:

- Preparações v1/v2: dependências herdadas e instalação same-version bloqueadas pelo binding, corrigidas no preparador.
- PTY v1: deadline inicial de tick em 3 s insuficiente para a fixture completa. A medição continuou em 3 s, com avanço exigido separadamente em até 20 s.
- [PTY v2](../../outputs/terminal-dashboard-20261011/attempts/pty-v2/terminal-campaign.json): UP após rajada reprovou; fonte passou a drenar até 32 teclas por pintura e limitar scroll durante cada tecla. O cenário original foi mantido e passou no final.
- [PTY v3](../../outputs/terminal-dashboard-20261011/attempts/pty-v3/terminal-campaign.json) / [v4](../../outputs/terminal-dashboard-20261011/attempts/pty-v4/terminal-campaign.json): resíduos de Reservations no parser incompleto, resolvidos na qualificação SU/SD e confirmados pelo controle independente.
- [PTY v5](../../outputs/terminal-dashboard-20261011/attempts/pty-v5/terminal-campaign.json): NO_COLOR branco/preto explícitos. Três PTYs mínimos reproduziram `curses.wrapper` inicializando cores; `use_default_colors()` preservou os defaults. A correção mínima passou no produto final.
- Consolidações intermediárias: falhas de setup no Temp Windows e basetemp sem parent; JUnits preservados. A rodada final em basetemp novo passou 404/1 skip.

Relatórios/logs integrais permanecem privados; projeções das tentativas estão linkadas. A revisão automática bloqueou uma cópia integral; a entrega usa whitelist de métricas/checks/hashes e identidade redigida. Nenhum critério de produto foi enfraquecido. O contador histórico `protocol_sessions=0` em uma projeção antiga não existia no schema e não deve ser interpretado como medição.

## Artefato, imagens e reprodução

Os **447 módulos Python** são iguais entre fonte preparada, wheel e instalação; antes da campanha o binding também foi conferido com a fonte atual do repositório. As dependências headless qualificadas foram reutilizadas sem modificar o ambiente anterior. `terminal_dashboard.py` está incluído no wheel privado, SHA-256 `4d61b856937b6712218ca84bd90e8ae63345e2115d150517e7558997378f11c4`.

Os PNGs desenham células/atributos recebidos do processo Linux, com viewport completo, DejaVu Sans Mono 16 px e fundo escuro. Somente o Peer sintético foi trocado por `<test identity redacted>`; parâmetros/resultados simulados e gráficos recebidos permanecem iguais.

- [Overview 120×40](../../outputs/terminal-dashboard-20261011/previews-final/00-startup.png), [Nodes 120×40](../../outputs/terminal-dashboard-20261011/previews-final/01-digit-nodes.png), [Satélites 80×24](../../outputs/terminal-dashboard-20261011/previews-final/02-resize-80x24.png), [Satélites 200×60](../../outputs/terminal-dashboard-20261011/previews-final/03-resize-200x60.png), [integridade dos previews](../../outputs/terminal-dashboard-20261011/previews-final/preview-integrity.json).
- [Campanha final-v6](../../outputs/terminal-dashboard-20261011/campaign-v6/terminal-campaign.json), [binding do wheel](../../outputs/terminal-dashboard-20261011/campaign-v6/report.json), [hashes da fonte](../../outputs/terminal-dashboard-20261011/campaign-v6/source-hashes.json), [desempenho](../../outputs/terminal-dashboard-20261011/performance-summary.json).
- [404 testes/um skip](../../outputs/terminal-dashboard-20261011/tests-v5-consolidated.xml), [controle vt100 v4](../../outputs/terminal-dashboard-20261011/vt100-control-v1/terminal-campaign.json), [parser/NO_COLOR mínimos](../../outputs/terminal-dashboard-20261011/parser-qualification-final.json).

Como usuário Linux sem UID 0, use um Python com dependências headless e **pyte==0.8.2**, destino público novo e a fixture simulada indicada:

```bash
python -I tools/validate_terminal_dashboard_linux.py \
  --repository "$PWD" --output outputs/terminal-dashboard-new \
  --mixed-fixture outputs/mixed_controller_20261011/campaign-pipeline-v2
```

Acrescente `--vt100-control` para repetir o controle independente. O preparador cria wheel/venv e guarda estado/credenciais somente na fixture privada. [Probe](../../tools/terminal_dashboard_pty_probe.py), [controle](../../tools/terminal_dashboard_vt100_control.py), [golden](../../tools/qualify_terminal_parser_linux.py), [projeção pública](../../tools/export_terminal_dashboard_evidence.py) e [previews](../../tools/render_terminal_dashboard_previews.py) são ferramentas de teste, fora do runtime do operador.
