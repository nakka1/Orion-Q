# ADR-GEO-004: estado compartilhado e renderizadores exclusivos

Data: 2026-10-06. Implementação: ORION-Q 5.42.

## Contexto

A GEO anterior criava o mapa e o globo simultaneamente e apenas escondia um
deles. O globo desenhava a órbita e o marcador do satélite selecionado; trocar a
seleção recentrava a câmera. As duas instâncias e seus caches permaneciam vivos.

## Decisão

`GeoMap` passa a ser o controlador da GEO. `GeoSceneState` retém o mesmo
`NetModel` entregue pela Topology, com inventário, comunicadores, conexões,
observações satelitais, seleção, camada, rota e demanda. Também guarda câmeras,
componente focado, alertas, âncoras da fila e cadastro de localização em andamento.
Seletor, botões e alertas pertencem ao controlador; inspector e janelas de detalhes
continuam pertencendo à Topology. Renderizadores não consultam nem assinam fontes.

Existe uma única instância gráfica: `MapRenderer` ou `OrbitGlobe`. Na troca,
`shutdown()` interrompe timers, desconecta seus callbacks e libera pixmaps,
paths e referências ao snapshot. O widget é escondido, removido da hierarquia e
retirado com `deleteLater()`. Somente após `destroyed`, um timer de execução única
cria a próxima instância. Isso evita destruir o objeto enquanto ele trata um
evento de scroll e evita coexistência de renderizadores. Pedidos sucessivos
antes dessa ativação convergem para o último modo solicitado.

A transição utiliza uma imagem estática limitada a 1200×800 por 350 ms. Esse
bitmap temporário não mantém instância, animação orbital ou listeners do modo
anterior; termina, é substituído na próxima troca ou é liberado ao ocultar/fechar.
Dados vetoriais imutáveis de cartografia podem permanecer no catálogo geográfico
compartilhado. Caches gráficos de uma visualização descarregada são descartados.

O globo percorre todas as observações dos satélites do inventário atual. Traços
animados acompanham o relógio publicado; o marcador nunca extrapola a posição
física. Órbitas e transmissões vêm do backend. Trechos ocultos pela Terra são
atenuados; linhas de emissão só são desenhadas na face visível. Satélites sem
efemérides continuam acessíveis no seletor, sem posição inventada.

Ícones têm largura mínima de 32 pixels lógicos e hit box de 48×48. Identificadores
coincidentes ganham alças separadas e linhas de referência às posições verdadeiras.
Hover e seleção destacam a alça e expõem a identificação sem recentrar a câmera.
Somente Endpoint/Core possuem os rótulos terrestres destacados de inventário.

No scroll, o mapa entra em globo ao exceder 44 milhões de metros de largura
projetada; o globo volta ao mapa com zoom 2,05 e largura de 24 milhões de metros.
Essa histerese evita oscilações após um pequeno movimento inverso. As coordenadas
sob o cursor orientam a região da transição. O botão sempre indica o modo atual
e o destino; trocas sem movimentar a câmera preservam a vista anterior do mapa.
Alterar a câmera do globo leva a nova região focal ao mapa.

## Alternativas e consequências

| Alternativa | Avaliação |
|---|---|
| Esconder ambos os widgets permanentes | Conserva caches e callbacks e viola carregamento exclusivo. |
| Recriar toda a Topology | Perde painéis, filtros e janelas; provoca consultas e trabalho redundante. |
| Destruir o renderer diretamente no wheel handler | Pode destruir um QObject ainda processando eventos. |
| Controlador persistente + renderer descartável | Mantém observações e UI de detalhes independentes do ciclo gráfico. |

GEO mantém a API anterior de geodesia/cartografia para os consumidores do mapa.
O estado salvo da Topology adiciona `geo_scene`, preservando a chave legada `geo`.
Os atalhos numéricos seguem a ordem da navegação, incluindo Comunicadores em 5.

As verificações cobrem exclusividade de QObjects, invalidação das instâncias
retiradas, timers, caches, dados por identidade, transição por scroll, sobreposição,
resoluções e trocas rápidas. Não constituem qualificação de GPU, arraste físico
em todos os monitores ou integração com satélite real.

## Referências

Qt, documentação oficial consultada em 2026-10-06:
[QObject::deleteLater](https://doc.qt.io/qt-6/qobject.html#deleteLater) e
[QObject em PySide6](https://doc.qt.io/qtforpython-6/PySide6/QtCore/QObject.html).

O diagnóstico de hover usa o overload `QWindow` de QTest para entregar eventos
QPA ao alvo sem depender da posição do cursor do sistema. O overload `QWidget`
pode usar `QCursor::setPos`, que não garante um evento no ensaio offscreen com
janelas auxiliares sobrepostas. Isso não substitui a qualificação de mouse
físico. Referência: [implementação oficial de QTest, Qt 6.10](https://raw.githubusercontent.com/qt/qtbase/6.10/src/testlib/qtestmouse.h).
