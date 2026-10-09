# ORION-Q — correção do redimensionamento — 06/10/2026

O usuário relatou que o pacote anterior travava após redimensionar a janela e
continuava travado depois de soltar a borda. A correção preserva a janela sem
moldura, os sete locais institucionais, o switch de ML opcional e a ausência de
atuação física.

## Diagnóstico e correção

As 200 mudanças programáticas de tamanho terminaram na fonte, com QPA offscreen
e Windows. Esse ensaio não reproduziu sozinho o travamento permanente relatado.
Os testes novos reproduziram uma captura de arraste pendente quando o evento de
soltar não era recebido e surgiu um movimento sem botão; a borda não observava
esse movimento porque mouse tracking estava desativado. O caso falhou tanto em
offscreen quanto em Windows antes da correção. O rastreamento agora está ativo,
e perder captura, desativar a janela, ocultar a borda ou mover sem botão termina
o arraste. Uma nova operação continua disponível.

No Windows, `startSystemResize` deixa de ser usado para essas bordas. O
[backend Windows do Qt 6.11.2](https://github.com/qt/qtbase/blob/v6.11.2/src/plugins/platforms/windows/qwindowswindow.cpp#L3037)
publica `WM_SYSCOMMAND/SC_SIZE` ao receber essa chamada. A nova operação mantém
o redimensionamento no fluxo normal de eventos Qt: captura o mouse, calcula a
geometria pelo deslocamento do ponteiro, respeita os limites e mantém a borda
oposta ancorada. Não se declara que o laço nativo foi confirmado como a causa
única do travamento observado no desktop. Outros sistemas preservam a delegação
ao sistema quando disponível. Arrastar a faixa de título continua usando o
recurso de movimentação do sistema.

Também surgiu `RuntimeError: Internal C++ object (UnplacedQueue) already deleted`
em um callback de resize que era executado após a destruição da tela. O callback
agora usa o contexto QObject da própria tela e é cancelado com a sua destruição.
Existe uma regressão específica para destruir a tela com esse trabalho pendente.

O perfil do mapa mostrou que desenhar os polígonos nacionais consumia boa parte
do ensaio. Durante mudanças contínuas de tamanho, apenas a rasterização do fundo
usa contornos sem antialiasing. Texto, nodes, conexões, coordenadas, escala e
hit tests continuam usando o tamanho atual. Após 100 ms sem novo resize, o
fundo é redesenhado com antialiasing e fica novamente em cache. O teste compara
a imagem final com a imagem anterior, após retornar ao mesmo tamanho.

No ensaio com profiler, as 200 mudanças passaram de 12,876 s para 6,213 s;
o tempo acumulado de `_base_layer` caiu de 6,932 s para 1,393 s. São medidas
locais com profiler e entrada programática, não uma certificação de FPS ou de
latência do arraste físico. As evidências anteriores permanecem em
`outputs/window_resize_20261006_v1/reproduction-before-3/` e a medição posterior
em `reproduction-after/`.

## Casos de regressão

| Caso de uso | Verificação |
| --- | --- |
| Redimensionar pelos quatro lados | Movimentos contínuos, borda oposta ancorada |
| Redimensionar pelos quatro cantos | Largura e altura independentes, ida e volta no mesmo arraste |
| Soltar a borda | Captura liberada e geometria estável nos movimentos seguintes |
| Não receber o release | Movimento sem botão cancela o arraste |
| Perder captura, foco ou visibilidade | Arraste cancelado; nova operação continua disponível |
| Navegar após redimensionar | Topology e Telemetry respondem aos widgets de navegação |
| Continuar na demonstração | Sete nodes, oito conexões e switch de ML acessível |
| Definir formatos estreitos, largos e altos | Tamanho solicitado conservado ao maximizar/restaurar |
| Restaurar a qualidade do mapa | Imagem final idêntica no mesmo tamanho; cache reutilizado |
| Fechar uma tela com resize pendente | Nenhum acesso a um widget destruído |
| Escalas Windows 100%, 150% e 200% | Eventos Qt na plataforma nativa; geometria em pixels lógicos |

Os testes usam widgets reais do console com um snapshot do serviço institucional
simulado. O helper Computer Use falhou durante a inicialização com
`helper_unknown_error: setup refresh had errors`, inclusive após reset. Portanto,
não foi possível realizar o arraste por entrada de desktop. Eventos Qt enviados
às bordas não são apresentados como uma sessão com mouse físico. As escalas
solicitadas usam `QT_SCALE_FACTOR` na mesma máquina.

## Resultados e pacote

| Bateria desta correção | Aprovações |
| --- | ---: |
| Janela, bordas e ciclo de vida do resize (offscreen) | 51 |
| Mesmos 16 cenários novos no QPA Windows, em três escalas | 48 |
| Adaptação de display | 34 |
| Renderização ao vivo, composição e ondas | 36 |
| Topologia ao vivo | 22 |
| Shell e navegação | 10 |
| Ciclo de vida e memória | 1 |
| Telas conectadas ao serviço | 27 |
| Janelas de nodes | 12 |
| Mapa institucional e confirmação de ML | 16 |
| **Execuções aprovadas** | **257** |

São 209 casos únicos, sem falhas ou skips nessas execuções finais. As repetições
em três escalas não são contadas como casos únicos adicionais. A execução
agrupada das telas foi interrompida por lentidão após a bateria de display;
os mesmos dez módulos foram executados em processos independentes, sem retirar
casos. Os XMLs e logs de cada módulo estão em
`outputs/window_resize_20261006_v1/pytest-isolated-*`.
As duas execuções anteriores com a falha de release ausente permanecem nos
relatórios `pytest-resize.xml` e `pytest-native-resize.xml`; as repetições finais
passaram. O [manifesto dos testes](../../outputs/window_resize_20261006_v1/source-validation.json)
registra hashes, escopo e a versão de referência.

O build usa essas regressões das áreas afetadas e mantém os gates completos do
executável congelado do script público: roadmap, modelos opcionais, campanha
temporal, autoteste, estresse, integridade e aceite automático. Não se declara
uma nova execução das 3127 aprovações das baterias anteriores. O código do
backend, modelos e configurações foi conferido por hash contra o estado recebido.

O pacote substituto foi concluído e passou pelo aceite automático local no
Windows 11. A [auditoria final](../../outputs/window_resize_20261006_v1/final-validation.json)
confere os XMLs, relatórios e hashes do mesmo executável.

| Verificação do pacote | Resultado | Evidência |
| --- | --- | --- |
| Autoteste completo | Aprovado | [self-test.json](../../release/windows/5.38.0-native-20261006-005525/self-test.json) |
| Janela, oito bordas/cantos e release | 52 verificações aprovadas, incluindo movimentos intermediários | [report.json](../../release/windows/5.38.0-native-20261006-005525/window-chrome/report.json) |
| Rede institucional e switch de ML | 28 verificações aprovadas | [report.json](../../release/windows/5.38.0-native-20261006-005525/institutional-demonstration/report.json) |
| Estresse | 20 rodadas, 140 ciclos, 535 verificações aprovadas | [aceitação](../../release/windows/5.38.0-native-20261006-005525/native-acceptance/acceptance-report.json) |
| Modelos com e sem ZIPs originais | Aprovado, no mesmo executável e em ambiente com PATH restrito | [report.json](../../outputs/window_resize_20261006_v1/frozen-original-models/report.json) |
| Roadmap e campanha temporal congelada | Aprovados nos gates do build | [log](../../outputs/window_resize_20261006_v1/build.log) |
| Integridade e aceite automático | Aprovados; CRC, hashes, autoteste e nova execução de estresse | [portable-package.json](../../release/windows/5.38.0-native-20261006-005525/portable-package.json), [acceptance-report.json](../../release/windows/5.38.0-native-20261006-005525/native-acceptance/acceptance-report.json) |

As verificações de janela do autoteste usam QPA offscreen e eventos dos widgets;
o funcionamento na plataforma Qt Windows foi coberto separadamente pelos 48
testes nas três escalas. A [captura da janela do executável](../../release/windows/5.38.0-native-20261006-005525/window-chrome/frameless-window.png)
foi conferida visualmente. O aceite não certifica entrada física de mouse nem
outra máquina: o relatório mantém `manual_review=pending` e
`independent_machine_verified=false`.

Entrega:

- [ORION-Q.exe](../../release/windows/5.38.0-native-20261006-005525/ORION-Q/ORION-Q.exe).
- [ZIP portátil completo](../../release/windows/5.38.0-native-20261006-005525/ORION-Q-5.38.0-native-windows-x64.zip)
  — 293748592 bytes e 3898 arquivos. Extraia o pacote inteiro; a pasta `_internal`
  deve continuar ao lado do executável.

SHA-256 do executável:
`cd51fc7839942f57048c9860996a0207d660455b5a46d40c7f9c9addb4dfab76`.
SHA-256 do ZIP:
`805ebb444ec4e454371091ad37c195f6add2984e2324b11e37ec379f7b80e321`.

Todos os arquivos recebidos foram copiados antes da edição para
`outputs/window_resize_20261006_v1/before/`, com os hashes em
`before-manifest.json`. Os pacotes anteriores e os journals do usuário são
preservados. Não há alteração dos modelos, pesos ou parâmetros científicos.
Dos 626 arquivos recebidos nos diretórios auditados, todos continuam presentes:
620 permanecem idênticos e seis receberam o incremento, descrito no
[diff da sessão](../../outputs/window_resize_20261006_v1/session-changes.diff).
O módulo novo de testes e este relatório foram acrescentados. A fonte não foi
alterada depois de gerar o executável.
