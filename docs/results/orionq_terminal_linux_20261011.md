# OrionQ — validação do terminal Linux em 11/10/2026

O wheel Linux instalado `quantum-twin 5.64.1` passou o gate de 514 testes, sem
falhas, erros ou skips. A campanha interativa final v4 passou 173 verificações em
curses/PTY real, incluindo as oito visões e detalhes dos 11 satélites do
perfil simulado, além de 11 nodes Mixed e duas reservas preenchidas pela API.

A data do título acompanha o gate em UTC; baselines e teste Windows registram
horário `-03:00` nos XMLs. O ambiente foi Ubuntu 24.04.5 LTS
em WSL2, kernel `6.18.33.2-microsoft-standard-WSL2`, arquitetura `x86_64`,
Python 3.12.3, 16 CPUs lógicas e usuário Linux sem privilégios (`uid/gid 10001`).
O host possui AMD Ryzen 7 5700X, oito núcleos e 16 threads, identificado por
`Get-CimInstance Win32_Processor`.
A instalação, o serviço e as evidências de execução usaram filesystem Linux
nativo. O EXE Windows existente foi preservado; o artefato validado nesta
rodada é o [wheel Linux 5.64.1](../../release/linux/5.64.1/quantum_twin-5.64.1-py3-none-any.whl).
Seu SHA-256 é `41ce3886d6ed21e9c73d0975c68a235dde5595ade04be2c66980ff4142f20892`.
O [manifest de integridade da entrega](../../outputs/terminal_linux_20261011/delivery-integrity.json)
registrou PASS: 517 fontes congeladas comparadas, 16 arquivos públicos
exportados pela allowlist, wheel com o SHA acima e nenhum estado privado
exportado. As fontes congeladas do repositório e do runtime de teste não
mudaram durante a entrega; diferenças de metadados do build foram registradas
separadamente no manifest.

## Regressões reproduzidas e correções

O baseline foi executado contra o wheel instalado `5.64.0`, antes de instalar
as correções. A primeira coleção teve 13 falhas e 23 passes em 36 casos;
a coleção expandida teve **19 falhas e 23 passes em 42 casos**, sem erros ou
skips. As coleções se sobrepõem e não devem ser somadas.
As falhas reproduziram problemas de apresentação e de admissão de dados:

- Ajuda oculta quando a seleção estava fora da área inicialmente visível.
- Rolagem em Raw/detalhes acumulando deslocamento além do fim; uma seta para
  cima não movia a tela imediatamente.
- Identidade recebida anteriormente do console atribuída à visão Mixed,
  cujo endpoint não publica identidade do operador.
- Dados internos malformados admitidos e depois capazes de interromper a
  renderização: séries, comandos, histórias, listas de enlaces dos nodes e
  configuração mista com endpoints inválidos.
- Reservas antigas apresentadas sem `STALE` após falha de leitura Mixed e
  mudança de visão.
- Séries legítimas ausentes nos detalhes porque o backend publica `node_ids`;
  o monitor procurava outro campo. Enlaces agora usam a associação explícita
  de `provenance.technology.session_id` com a sessão da série.

O gate final executou **514 testes com PASS, zero falhas/erros/skips**, contra
o wheel instalado `5.64.1`. Incluiu os 42 casos do monitor e verificações
de CLI, serviço, autenticação e contratos do núcleo. O perfil headless exclui
console/desktop; a verificação do artefato confirmou ausência de imports de
GUI, ML opcional e integração Windows no smoke test do núcleo instalado.
O teste de compatibilidade do monitor no Windows passou **41 casos**, com
um skip esperado para interação curses/PTY exclusiva de Linux. Esses 42
casos se sobrepõem aos casos do monitor no gate Linux; não são 42 casos
adicionais ao total de 514.

## Campanha interativa e observação ao vivo

A campanha lançou o serviço independente e o operador instalado, com
HTTPS/mTLS TLS 1.3 e entrada explicitamente simulada. O relógio do serviço
processou o perfil de 76 nodes, 136 enlaces e 11 satélites. O monitor fez
somente GETs durante a apresentação; configuração e provisionamento da
fixture foram etapas separadas.

O harness interpretou a tela atual com `pyte 0.8.2`, evitando usar texto
histórico ANSI como prova de um estado ainda visível. Os campos citados na
tabela constam no [JSON final v4](../../outputs/terminal_linux_20261011/pty-v4/terminal-campaign.json):

| Caso | Evidência | Resultado |
|---|---|---|
| Oito abas por 1–8 e Tab | `pty.digit_views`, `pty.tab_views` | Overview, Links, Nodes, Satellites, Reservations, Audit, Raw e Mixed acessíveis |
| Seleção, Enter e ajuda | Checks de wrap, detalhes e ajuda com node distante; `pty.satellite_ids_visited` | 11 satélites abertos, seleção preservada e ajuda visível |
| Resize | `pty.viewport_sizes`, `pty.selection_preserved_on_resize` | 80×24, 120×40, 200×60, 40×10 e 1×1, com restauração |
| Scroll | `pty.details_scroll`, `pty.raw_scroll` | Fim alcançado em ambas as visões; uma seta para cima moveu o quadro |
| Queda e reconexão | `pty.outage` | `STALE`, ajuda responsiva e leitura recuperada sem reiniciar o operador |
| Pausa e metadados | `pty.pause`, `pty.reload_static_completed` | Tick retido 7, backend 8, retomada 8; atualização por `r` concluída |
| Autenticação e independência | `tls`, `operator_independence`, `authenticated_audit` | mTLS TLS 1.3, certificado revogado 401, anônimo rejeitado, mesmo servidor e auditoria válida |
| Mixed e reservas preenchidas | `mixed_fixture`, `pty.mixed_fixture` | N01–N11, campos de sessão/relógio/correlação recebidos e dois recibos com `quantum_result: null` |
| Saída e restauração | `pty.exit_code`, `pty.keyboard_interrupt`, `pty.termios_restored` | `q` retorna 0, Ctrl+C real retorna 130 e ambas as sessões restauram `termios` |

| Evidência interativa | Campanha final v4 |
|---|---:|
| Verificações aprovadas | 173/173 |
| Sessões PTY | 2 |
| Teclas enviadas | 1.200 |
| Trocas de entrada | 158 |
| Capturas da tela atual | 54 |
| Satélites visitados em detalhes | 11/11 |
| Duração do probe PTY | 45,847 s |
| Duração total da campanha | 89,787 s |

As seis leituras finitas da v4 observaram tick 2→4, histórias de enlaces
70→140 amostras e séries por protocolo 714→1.149 amostras, preservando 261
séries publicadas. Os hashes dessas histórias mudaram entre quadros. Um
único worker manteve máximo de uma leitura em voo e rejeitou 100 pedidos
adicionais durante backpressure, sem fila crescente.

No PTY, a pausa manteve tick 7 visível enquanto o backend chegou a 8; retomar
mostrou 8. A pausa do relatório finito, exercitada separadamente por quatro
segundos, reteve 16 enquanto o backend chegou a 17. Na indisponibilidade
temporária, o mesmo operador mostrou `STALE` e recuperou tick 14→15.

A revogação do certificado do primeiro operador retornou HTTP 401;
um cliente sem certificado foi rejeitado. Outro operador autorizado recebeu
tick 17→19, com o mesmo servidor e sem reinício. A auditoria autenticada
foi válida, com 779 registros; o serviço terminou com código 0 em 0,467 s e
a verificação da saída pública não encontrou o material secreto da fixture.

A v3 também passou: 145 verificações, 1.197 teclas, duas sessões PTY e 45
capturas. Ela repetiu as oito visões e os 11 satélites, porém Mixed e
Reservations estavam vazias. A v4 amplia esse escopo com conteúdo recebido.
As rodadas são evidências complementares; suas estatísticas não constituem
uma comparação antes/depois para atribuir benefício de desempenho à UI.

## Latências e limites práticos

As métricas abaixo distinguem uma tecla individual dos lotes de 16 setas,
que incluem uma espera deliberada de aproximadamente 300 ms para drenar a
entrada. Os lotes não integram a estatística de resposta por tecla.

| Medição final v4 | Amostras | Mediana | P95 | Máximo |
|---|---:|---:|---:|---:|
| Tecla individual até a condição visível | 86 | 9,903 ms | 20,847 ms | 240,706 ms |
| GET de snapshot no watch finito | 6 | 509,504 ms | 2.939,121 ms | 2.939,121 ms |
| Renderização textual Python | 57 | 0,853 ms | 2,149 ms | 2,311 ms |

**A demora de leitura da API continua presente:** o máximo foi aproximadamente
2,73 s na v3 e **2,94 s na v4**. A separação entre worker e pintura manteve
navegação e saída responsivas; o teste não estabelece atualização científica em tempo real
estrito, limite garantido de latência ou eliminação da demora do backend.

## Extensão v4: Mixed e reservas

A extensão v4 admitiu **1.970 observações** identificadas como replay de
SeQUeNCe pela API autenticada de configuração, ingestão, demanda e status do
serviço. Foram cadastrados N01–N11. N01 é emulado: seus detalhes no PTY
preservaram `session_id: mixed-session-N01`,
`clock_domain: fixture-aligned-clock` e `coincidence_pairs: 47`.

As duas reservas recebidas foram selecionadas e abertas no PTY:

| Recibo recebido | Status publicado | Resultado quântico publicado |
|---|---|---|
| `terminal-native-control` | `reserved` | `null` |
| `terminal-unavailable-bridge` | `blocked` | `null` |

O teste conferiu os literais recebidos nas páginas JSON visíveis, inclusive
após rolagem, sem inferir um resultado quântico da reserva. Esta rodada
**não executou uma nova simulação SeQUeNCe**. Replay e N01 emulado permanecem
identificados como tal e não qualificam aquisição física.

## Evidências e alcance

- [Baseline expandido do wheel 5.64.0](../../outputs/terminal_linux_20261011/baseline-expanded-tests.xml).
- [Baseline original de 36 casos](../../outputs/terminal_linux_20261011/baseline-tests.xml)
  e [compatibilidade Windows: 41 passes, um skip](../../outputs/terminal_linux_20261011/windows-terminal.xml).
- [Relatório do gate Linux](../../outputs/terminal_linux_20261011/linux-gate/report.json)
  e [JUnit com 514 testes](../../outputs/terminal_linux_20261011/linux-gate/tests.xml).
- [Campanha PTY v3](../../outputs/terminal_linux_20261011/pty-v3/terminal-campaign.json)
  e [capturas textuais v3](../../outputs/terminal_linux_20261011/pty-v3/terminal-campaign.txt).
- [Campanha PTY final v4](../../outputs/terminal_linux_20261011/pty-v4/terminal-campaign.json)
  e [capturas textuais v4](../../outputs/terminal_linux_20261011/pty-v4/terminal-campaign.txt).
- [Manifest de integridade e hashes da entrega](../../outputs/terminal_linux_20261011/delivery-integrity.json).

Esta é uma campanha curta em WSL2/x86_64 com processos Linux reais e dados
simulados, replay e emulação identificados. Não qualifica ARM, execução
prolongada/soak, hardware óptico, FPGA, OBC, sincronismo medido ou atuação
física. Reserva de slots não é um resultado quântico certificado. Nenhum
resultado desta rodada promove automaticamente o produto a um nível TRL.
