# GEO: Mapa, Globo e satélites

Disponível em ORION-Q 5.42, atualizado em 5.45. Mapa e Globo usam as mesmas observações do backend.
A troca de visualização não altera protocolos, telemetria ou controle da rede.

Símbolos revisados em ORION-Q 5.44: a classificação publicada pelo backend
determina o ícone; identificadores e nomes não são usados para inferir o papel.

| Componente | Símbolo e destaque |
|---|---|
| Endpoint | Porta circular de 24 pixels lógicos com setas opostas de entrada/saída; rótulo destacado e prioridade visual sobre intermediários. |
| Node | Hexágono de 14 pixels com centro; Core usa hub de 20 pixels e rótulo destacado. |
| Repetidor | Marca de 7 pixels, discreta; alvo de clique mínimo de 24 pixels. Identificação no tooltip/inspector. No GRAPH, o nome aparece quando selecionado ou com badge do percurso inspecionado. |
| Fibra | Apenas linha; cor/traçado refletem o backend. Clique na linha, com tolerância de cinco pixels; seleção destaca a própria linha. Não há quadrado no meio ou área de clique exclusiva desse antigo quadrado. |
| Satélite | Conserva seu símbolo físico próprio, órbita e transmissões publicadas. |

Os símbolos são vetoriais e mantêm tamanho lógico em tela durante zoom; usam
os dois temas existentes. Status, isolamento e recalibração continuam visíveis.
As mesmas primitivas são usadas na GEO, GRAPH, MATRIX, fila de nodes sem
localização e cabeçalhos de inspeção. Rótulos permanentes no mapa/globo continuam
restritos a Endpoint/Core; coordenadas, métricas, capacidades e enlaces não mudam.

## Operação

1. Abra Topology → GEO. No alto da visualização, **Mapa → Globo** indica que o
   mapa está ativo e abre o globo. **Globo → Mapa** faz o caminho inverso.
2. Afaste o mapa-múndi pelo scroll para entrar no globo; aproxime o globo para
   retornar ao mapa. A entrada ocorre em uma largura projetada de 60 milhões de
   metros; o retorno exige zoom 5,8 no globo e restaura uma largura de 20 milhões
   de metros no mapa. O globo permite zoom até 6, preservando maior aproximação
   antes da troca. Essa faixa evita alternâncias involuntárias. Nove passos
   de scroll de aproximação, partindo do zoom 1, ainda permanecem no globo; o décimo
   retorna ao mapa. O botão troca imediatamente. Arraste a Terra para girar; a
   região sob o cursor acompanha o scroll e a transição.
3. Todos os satélites com observações orbitais aparecem juntos com suas órbitas.
   Passe o mouse para identificá-los; clique para abrir os detalhes existentes da
   Topology. A seleção destaca um satélite e mantém os outros no globo.
4. Ícones mantêm tamanho em tela durante zoom out. Marcadores ocultos pela Terra
   são atenuados e identificados como ocultos. Se as projeções coincidirem, as
   alças de seleção são separadas e apontam para as coordenadas verdadeiras.
5. Use o seletor de componentes para acessar também satélites sem efemérides e
   nodes fora do enquadramento ou sem localização. Focar uma instalação abre seu
   mapa/campus. **Reset view** retorna à visão geral terrestre.
6. **Comunicadores** fica imediatamente abaixo de **Nodes**, com ícone de
   transceptor, descrição ao passar o mouse e atalho numérico **5**. Edge control
   e Access & audit passam a **6** e **7**.

A legenda inferior reúne os símbolos de Endpoint, Core, Node, Repetidor e
Satélite, além das cores e traçados dos enlaces. Ela quebra em novas linhas
quando a largura diminui. Cada item tem uma descrição ao passar o mouse.

Órbita tracejada não é canal de transmissão. Uma linha fina cinza pontilhada
satélite–solo indica uma associação cadastrada, mesmo sem recepção. Uma linha
fina violeta contínua indica emissão do último bloco recebido e validado para
uma estação visível. Ambas permitem inspecionar o enlace. Fibra cinza, degradação
laranja e demais estados terrestres conservam sua legenda e semântica.
O relógio movimenta os traços; posições e estados nunca são fabricados pela GEO.
Ausência de efemérides não autoriza representar uma posição ou conexão real.

Ao clicar em um satélite, a janela mostra housekeeping orbital, idade e validade,
protocolo, estado da ponte, coincidências, QBER e métricas de cada estação, com os
identificadores dos canais clássico e quântico. **HOUSEKEEPING / SCIENCE · DETALHES**
abre as medições e unidades do mesmo snapshot, atualizado enquanto estiver aberto.
O painel usa observações do backend, sem curvas genéricas de fibra ou métricas
finais inventadas. Veja [ponte satelital e hipóteses](orion_q_satellite_bridges.md).

## Preservação e recursos

O controlador conserva inventário, comunicadores, links, seleção, camada, rota,
demanda, alertas, câmeras e localização em edição. Inspector e janelas de detalhes
persistem fora do renderer. Atualizações chegam pelo fluxo já usado pela
Topology; alternar Mapa/Globo não abre nova consulta ou assinatura.

O renderer anterior é encerrado e destruído antes da criação do seguinte. Timers,
paths e rasters específicos são liberados. Somente uma imagem temporária de
transição permanece por 350 ms. O catálogo geográfico compartilhado conserva
dados vetoriais imutáveis, sem executar o modo descarregado.

## Créditos cartográficos

O Mapa conserva o crédito `© OpenStreetMap contributors`, em texto de pelo
menos 12 pixels lógicos, com contraste próprio e fundo sólido. Em áreas estreitas,
somente a descrição secundária das fontes é reduzida: a identificação OSM
permanece completa, junto de `ODbL`. Quando necessário, o crédito fica acima da
escala para evitar sobreposição. O tooltip apresenta a origem completa e o clique
abre a [licença e atribuição oficial](https://www.openstreetmap.org/copyright),
sem selecionar um componente ou iniciar pan.

A visão mundial usa Natural Earth para os polígonos; os detalhes de campus
provenientes de OSM mantêm sua atribuição. A manutenção não altera coordenadas,
limites, polígonos ou rotas. Veja as [evidências de apresentação e pacote](../results/orionq_asset_licensing_20261012.md).

## Validação

```powershell
$env:PYTHONPATH = 'src'
$env:QT_QPA_PLATFORM = 'offscreen'
python -m pytest tests/test_console_geo_modes.py -q
ORION-Q.exe --self-test C:\temp\orion-geo-novo\report.json
```

O self-test inclui `communicators-geo/geo-modes/report.json`, com tabela de casos,
captura do globo, interação, resoluções e 60 trocas consecutivas. Testes de código
incluem 100 trocas completas e 201 pedidos rápidos, sobreposição de ícones,
atualizações durante a retirada do renderer e preservação na reconstrução do tema.
Inclui também `communicators-geo/satellite-bridge/report.json`, que verifica
recepção conjunta entre redes, janela flutuante, falha, recuperação e canais.

Adaptação visual não representa qualificação de hardware/OGS, missão espacial,
chave segura utilizável ou atuação física. As limitações documentadas em
`orion_q_satellite_ogs_v2.md` permanecem aplicáveis.
