# Sincronização artificial contínua no dashboard — 23/09/2026

Implementação em fonte posterior à primeira ponte shadow e ao último ZIP Windows.
O executável não foi reconstruído nesta etapa.

## Entregue

- `SyntheticSyncStream`: sinal temporal artificial com semente, buffer limitado a
  oito amostras efetivamente emitidas, reinício em lacunas/dropout/contexto.
- Campo opcional no mesmo contrato `Snapshot`; fontes existentes continuam
  funcionando sem fornecer timing. Nenhum modelo é carregado pela fonte.
- Sessão nativa ligada ao catálogo verificado e a Edge Control: START explícito,
  inferência em worker sem fila, STOP/CLEAR, exportação exclusiva de frame.
- Janela de frescor local de 3,5 s, sem confundir relógio local com tempo virtual;
  cancelamento lógico por geração impede resultados tardios de reaparecerem.
- Troca de fonte/campanha/modelo, fechamento, perda de dados e pacote alterado
  interrompem avaliação. Sem fallback oculto, journal de comando ou política ativa.

## Verificação

- **147 testes passaram** no conjunto inicial com telas, fontes, catálogo,
  sessões de sincronização e ponte shadow.
- Após ampliar os testes e ajustar exportação, **181 testes passaram** no conjunto
  final: nova sessão/fluxo, ponte, sessão offline, gates físicos, trilhas de
  evidência e observações. Os conjuntos se sobrepõem; não somar como testes únicos.
- Inclui integração de `SyntheticProvider` com `EdgeControlScreen`, igualdade
  reprodutível por semente, ausência de carga automática, workers atrasados,
  watchdog Qt, pacote alterado, exportação durante fluxo e recusa após invalidação.
- Inspeção visual offscreen em 640×480: controles presentes, texto/relatório
  rolável e tema nativo preservado. Não é inspeção em máquina independente.
- Testes usam ZIP mínimo revisado por hash de fixture, sem depender dos pesos
  externos. A verificação com ZIP original da ponte está no resultado anterior;
  esta etapa não constitui nova campanha científica com o artefato do autor.

Evidências: `outputs/sync_shadow_ui_20260923/regression.xml`, `safety-final.xml` e
`pytest-02/test_export_and_small_window0/shadow-640.png`. O primeiro teste de
alteração do pacote tratava um caminho string como Path; a fixture foi corrigida
e as execuções seguintes passaram.

[Operação e limites](../product/orion_q_sync_shadow_bridge.md).
Próximo passo: avaliação causal com referências futuras e relatório temporal,
separado dos comandos. Offset não é fidelidade e diferença de previsões não é ganho.
