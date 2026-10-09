# Alocação híbrida — ponte shadow em fonte

Data: 24/09/2026. Próximo incremento Q16 após a campanha SO(3).
Implementada a [ponte tipada de alocação](../product/orion_q_allocation_shadow_bridge.md)
para persistência e Ridge, com seleção explícita, comparação no mesmo snapshot,
exportação sem sobrescrita, API somente leitura no runtime e CLI de replay.
Modelos opcionais e OFF por padrão; nenhum executor, política ou fila é alterado.

## Diagnóstico com pacote original

ZIP QNET SHA-256:
`6b8563209f54a2d566f1a4229b140b75fda93555085ff2bf6bfcad8f07abd938`.
Planta SHA-256:
`881213fab3f992e5ba86c66aac82d6cf82df93405e0d450de123c39236137b50`.

| Opção | Histórico anterior | Status | Paridade com sessão offline | Snapshot preservado |
|---|---:|---|---|---|
| Persistência | 0 | `persistence` | Sim | Sim |
| Persistência | 5 | `persistence` | Sim | Sim |
| Ridge | 0 | `warmup_persistence` | Sim | Sim |
| Ridge | 5 | `ridge` | Sim | Sim |

Evidências: `outputs/allocation_shadow_20260924/author-original/`, com quatro
snapshots artificiais explícitos, quatro registros completos e `report.json`
contendo identidades e hashes individuais. Os ZIPs não foram copiados ao produto;
nenhum script do autor foi executado. A rotina usa o leitor revisado de dados.

A paridade confere recomendações e persistência, incluindo previsões e orçamento,
contra a sessão offline existente sobre as mesmas entradas. Não reproduz a campanha
do artigo, não usa uma série temporal independente e não mede melhoria de previsão
ou vazão. O histórico é uma fixture declarada, não uma aquisição autenticada.
Orçamentos continuam assintóticos; não há chave entregue ou certificado de chave finita.

## Regressão de software

**407 testes aprovados em 15.546 s**, zero falhas/erros/ignorados:
`outputs/allocation_shadow_20260924/regression-final.xml`. Inclui **74 testes da nova
ponte** e cobertura existente de adaptador/sessão/GUI de alocação, opcionalidade,
catálogo, verificação de arquivos, runtime sandbox, gates físicos, trilhas de
evidência e pontes de sincronização/compensação. O conjunto não é a suíte inteira.
A rodada inicial de 72 testes se sobrepõe; não somar. Os 500 testes da campanha
SO(3) continuam um registro histórico separado, não uma contagem adicional.

Cobertura nova:

- OFF sem leitura, indisponibilidade sem observáveis próprios e ausência de
  conversão de WDM/SO(3)/timing/fidelidade.
- Fonte/nó/campanha/fluxo/relógio/origem/planta divergentes, entrada medida, OOD,
  integridade ausente, unidades/protocolo/ordem de enlaces incompatíveis recusados.
- Histórico futuro, anterior à origem, com lacuna, duplicata ou
  reordenação; matrizes/vetores inválidos; filas fora dos níveis; booleanos,
  valores não finitos, volumes negativos e urgências inválidas recusados.
- Limite de idade de 5 s, timestamp com fuso equivalente, tipos imutáveis,
  snapshot/avaliação destacados, aquecimento e comparação offline idêntica.
- Planta do snapshot comparada com a do arquivo carregado; ZIP alterado recusado.
- Exportação exclusiva, CLI OFF/evaluated/refused, limites de tamanho e JSON estrito.
- Spies nos três executores, na política, no ciclo e no journal: nenhum chamado;
  bytes do journal e snapshot permanecem iguais na avaliação explícita.

Ruff passou nos arquivos Python alterados. O teste foi incluído no gate Windows
e no script local de build; o job unitário genérico já coleta o marcador `unit`.
Não houve execução remota de CI, rebuild do EXE ou aceite em segunda máquina.

## Continuidade

O [roadmap](../product/roadmap.md) registra a ponte entregue e mantém Q16 parcial.
Próximo incremento: produtor artificial e sessão gráfica contínua de alocação,
com START explícito, frescor/invalidação e exportação, preservando a planta e
fila recebidas. Roteamento ainda precisa de sua ponte e contexto de ações legais.
Q05, atuação física e avaliação em laço fechado permanecem fora desta entrega.
A campanha SO(3), os modelos existentes e as alterações anteriores foram preservados.
