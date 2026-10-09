# Polarização no dashboard — 23/09/2026

Registro da etapa de ligação gráfica. O incremento posterior de pareamento e
métricas está em [avaliação temporal SO(3)](orion_q_compensation_temporal_20260923.md).

Entrega em código-fonte: produtor artificial e janela nativa opcional para os
quatro checkpoints `qnet.compensation.{calibrado,desenvolvimento}.{ridge,mlp}`.
O ZIP/EXE 5.38.0 anterior não foi reconstruído. Q05 continua sem ativação desses modelos.

## O que funciona

O `SyntheticProvider` entrega os dois regimes pelo campo
`Snapshot.optional_compensation_states`, percorrendo o mesmo plug de dados e
refresh de Edge Control. Não carrega modelos por conta própria. Seleção do
checkpoint, verificação do ZIP e START são explícitos. Modelo e hash ficam visíveis.

Cada publicação produz 16 amostras novas, a 2 ms, em relógios virtuais separados:
32 ms por lote, distintos do relógio de sincronização e do tempo real da interface.
O sinal é uma senoide de rotação com deriva; não é uma planta física calibrada.
A declaração contratual de fótons não representa contagens ou ruído simulados.

A janela mostra SO(3) previsto, persistência e registro completo, com exportação
exclusiva para arquivo novo. Os primeiros 15 passos são aquecimento. Não há
referência futura nem erro angular calculado. A inferência ocorre fora da thread
gráfica, com um trabalho por janela, sem fila acumulada. Resultados atrasados são
descartados. Fechamento, STOP, perda de fonte/contexto e timeout invalidam a sessão;
duplicatas não renovam a disponibilidade. A validade do cache é separada por regime.

## Verificação

Evidências em `outputs/compensation_shadow_ui_20260923/`:

- `regression-final.xml`: **382 testes passaram** na regressão selecionada de produtor, UI, catálogo, entrada
  medida, telas, compensadores, sincronizador, sandbox e gates físicos. Não é a
  suíte integral do repositório.
- `author-checks/report.json`: **8 avaliações concluídas**, dois lotes por
  checkpoint, com ZIP original QNET verificado pelo SHA-256
  `6b8563209f54a2d566f1a4229b140b75fda93555085ff2bf6bfcad8f07abd938`.
  Oito registros exportados e quatro capturas de janela em 640×480.
- Capturas inspecionadas: resultado rolável e quatro botões visíveis em 640×480.
  Teste de integração conecta o sinal do produtor a Edge Control e confirma que
  não houve emissão de comandos, incluindo após troca para fonte medida.
- Ruff seletivo passou. Os novos testes foram incluídos nos gates do build e CI;
  isso não significa que um novo build ou execução remota de CI ocorreu.

Os testes de unidade usam checkpoints-fixture; o diagnóstico adicional usa pesos
originais externos. Nenhum peso novo foi copiado ao produto. Arquivos existentes
não são sobrescritos pela exportação. STOP/fonte/timeout durante o seletor de
arquivo cancelam a exportação; novos lotes não alteram o registro já selecionado.

## Limites e próximo passo

Isto verifica integração de software, não vantagem do ML, fidelidade de Bell,
validade do domínio ou controle quântico físico. Fontes medidas não recebem
polarização artificial automaticamente. Não existe conversão de BER/OSNR ou
offset de sincronização para SO(3).

Próximo incremento: registrar previsões antes da chegada de referências futuras,
parear por timestamp e identidade, medir erro angular contra persistência e
planejar uma campanha de robustez própria. Sem extrapolar resultados do
sincronizador e sem tratar o novo produtor como réplica validada da planta do artigo.

[Como usar e reproduzir](../product/orion_q_compensation_shadow_bridge.md#uso-no-dashboard).
