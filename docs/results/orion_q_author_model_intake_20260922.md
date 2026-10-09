# Admissão dos modelos do autor - 22/09/2026

Escopo: primeira reprodução numérica dos exports fornecidos, sem execução de
código dos ZIPs, retreino, novos episódios simulados ou atuação física.

## Entradas documentais

- `artigo_qkd_ml_1.pdf`: 23 páginas, SHA-256
  `dbc7cb99d92236afd07a73d19ed9f5706630d135e50dbe1924e6c799214c28c7`.
- `qnet_autopilot network.pdf`: 25 páginas, SHA-256
  `1c2dc20467541471f81edca0a5a60f993a5b440ce4a2fe817edecf89c8015db3`.
- ZIPs e contratos: [guia de admissão](../product/orion_q_author_model_intake.md).

Foram inspecionados inventários e documentos dos componentes, código autônomo
do roteador e suas rotinas de exemplo, e trechos relevantes de inferência QKD
(sincronizador e agente de taxa). O detector não foi carregado; compensador e
alocador ainda não passaram por revisão completa do caminho numérico.
Páginas relevantes de tabelas dos dois PDFs foram também renderizadas e conferidas.

## Paridade com o export

| Adaptador | Casos | Maior desvio absoluto | Resultado |
|---|---|---|---|
| Sincronizador Ridge | 593 previsões; 7 amostras de warm-up não previstas | 1,475e-18 s (limite 1e-15 s) | Passou |
| Roteador nominal T=1 | 12 exemplos + 24 episódios / 549 decisões | 4,441e-16 (limite 1e-12) | Zero divergência de ações |
| Roteador prazo longo T=0 | 12 exemplos + 24 episódios / 1.542 decisões | 1,776e-15 (limite 1e-12) | Zero divergência de ações |

Total: 24 exemplos isolados e 2.091 decisões sequenciais de roteamento. RNG e
ordem dos episódios preservados, incluindo empates de argmax. O crítico também
foi conferido nos exemplos isolados. Isso não reproduz os resultados físicos
dos episódios: estados e ações legais foram fornecidos pelo export original.

Na única trajetória QKD fornecida, RMSE do sincronizador = **57,348 ps**;
persistência = **57,385 ps**. É uma diferença descritiva pequena, sem teste de
significância ou amostras independentes neste incremento. Não substituir esses
números pelos resultados de múltiplas trajetórias do README/artigo, nem concluir
vantagem generalizável a partir deste replay.

## Verificação de software

- 48 testes unitários/adversariais autocontidos, com pesos sintéticos e sem
  depender dos caminhos externos do autor. Validam identidade do ZIP, snapshot
  imutável de entrada, nomes inseguros, expansão, pickle bloqueado, tipos/NaN,
  cadência, horizonte, domínio, forma dos pesos, coerência ação/feature, RNG e
  recusa de sobrescrita; paridade divergente não vira aprovação.
- Ruff: sem erros nos arquivos novos.
- 54 testes de regressão passaram em `test_evidence_lanes.py`,
  `test_physical_control_gate.py` e `test_console_commands.py` (6,58 s).
  Total deste incremento: 48 novos + 54 de regressão = 102 testes aprovados;
  não representa execução de toda a suíte do repositório.
- Na primeira execução, o diretório temporário padrão do pytest estava sem
  permissão. Os testes foram repetidos com diretório novo dentro do workspace.
- Um teste adversarial revelou normalização de barras invertidas pelo ZipInfo
  no Windows. A validação foi corrigida para inspecionar `orig_filename`,
  preservando a verificação do nome original antes da normalização.

Artefatos locais em `outputs/author_model_intake_20260922/`: `final-replay.json`
(com hashes do código final), `final-unit-tests.xml` (48 testes),
`regression-tests.xml` (54 testes); relatórios de tentativas anteriores
preservados separadamente. Não foi executada toda
a suíte histórica; contagens de entregas anteriores não representam esta execução.

## Impacto e pendências

Primeiro incremento Q16, não conclusão de Q05. A identidade, compatibilidade
numérica e recusa de entradas incompatíveis estão verificadas neste escopo.
Integração ao runtime, construção de features de rede, avaliação em laço fechado,
catálogo visual e nova distribuição `.exe` continuam pendentes. Nenhum checkpoint
foi ativado automaticamente, e nenhum arquivo do usuário foi alterado.

Não se demonstrou nesta entrega ganho operacional dos novos modelos, segurança
de chaves, FPGA sintetizado, desempenho em rede física ou previsão de fidelidade
quântica medida a partir de WDM. O próximo ensaio prioritário é a admissão do
compensador por regime, antes de testar seu ganho de controle no sandbox.
