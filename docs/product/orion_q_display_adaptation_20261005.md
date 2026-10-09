# ORION-Q — adaptação da interface ao monitor

Atualização de 5 de outubro de 2026, aplicada ao console nativo PySide6.

A aplicação abre maximizada no monitor em que o cursor está. A barra de título
permite restaurar a janela e escolher seu tamanho; F11 alterna a tela cheia.
O tamanho restaurado é calculado pela área útil desse monitor, respeitando a
barra de tarefas e a moldura da janela.

O Qt aplica a escala de exibição do Windows, incluindo valores fracionários.
O console acompanha as mudanças de monitor, DPI e área útil sem recriar os
controles durante o redimensionamento. Uma janela restaurada também é ajustada
ao ser movida para um monitor menor. Campos ainda não enviados e seleções
permanecem na mesma instância da interface.

Os painéis passam de três para duas ou uma coluna conforme o espaço disponível.
Grupos de títulos e ações se empilham quando deixam de caber na mesma linha.
A tela inicial passa de duas colunas para uma e permite rolagem; a tela de
carregamento também permite rolagem em áreas menores. A paleta Ctrl+K calcula
sua largura e altura para caber na janela.

Em monitores maiores, a apresentação aumenta de forma uniforme, preservando
as proporções. Ctrl++ e Ctrl+- ajustam o zoom do espaço de trabalho; Ctrl+0
retorna ao ajuste automático. O mapa gera sua imagem em cache considerando
tanto a densidade de pixels do monitor quanto o zoom da apresentação.

As janelas secundárias permanecem janelas nativas. Ao abrir ou trocar de
monitor, ajustam sua moldura à área útil. O dimensionamento manual permanece
disponível. Conteúdo extenso pode ser rolado e controles
focados pelo teclado são trazidos para a área visível.

## Cobertura e limites

A matriz de regressão do executável inclui dez áreas de janela de 960×540 a
5120×2160 pixels lógicos. Os temas claro e escuro são verificados nas áreas
960×540, 1280×720 e 3840×2160. Há testes de
escala Qt em processos separados para 100%, 125%, 150% e 200%, além de
transições de geometria simuladas entre monitores com origens positivas e
negativas. Pixels lógicos são a área disponível após a escala do Windows:
uma tela física 1920×1080 a 200% corresponde aproximadamente a 960×540 antes
de descontar a barra de tarefas e a moldura.

O espaço de trabalho usa um limite de legibilidade de 960×480 pixels lógicos.
Abaixo desse espaço, a área externa permite rolagem. A janela nativa tem mínimo
de 320×240; a transição para uma área útil de 640×360 também é verificada.
Os painéis de conteúdo
e diálogos também permitem rolagem quando seus dados excedem a área disponível.
Os testes não substituem uma revisão humana em vários monitores físicos.

O comportamento dos modelos opcionais e o bloqueio de atuação física seguem
os contratos existentes. As mudanças desta atualização tratam da apresentação.
