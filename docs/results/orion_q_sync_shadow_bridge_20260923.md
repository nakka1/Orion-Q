# ORION-Q — ponte de sincronização shadow, 23/09/2026

## Entrega

Contrato tipado de oito amostras por fluxo, avaliação opcional sobre
`NetworkStateSnapshot`, método separado do ciclo de controle, CLI explícita,
fixture artificial e relatório reproduzível. [Contrato e uso](../product/orion_q_sync_shadow_bridge.md).

O sandbox comum não publica essas amostras automaticamente. Sem a extensão
tipada retorna indisponibilidade; não deriva offset de BER, OSNR ou fidelidade.
Não ativa Q05, não altera políticas/baselines nem envia comandos ao simulador.

## Verificações realizadas

- **182 testes passaram** no conjunto selecionado: ponte nova, sessão offline
  do sincronizador, sandbox, gates físicos, trilhas de evidência e admissão dos modelos.
  Não é execução da suíte integral do repositório.
- Testes incluem OFF sem leitura, fontes/campanhas/relógios/nós incompatíveis,
  cadência e causalidade, janela vencida, unidades erradas, dados medidos recusados,
  limites/JSON estrito, pacote alterado, exportação exclusiva e replay.
- Espiões de teste impedem chamadas a política, ciclo e executores; o banco de
  comandos permanece byte a byte inalterado durante a avaliação shadow.
- Previsão da ponte igual à sessão offline para a mesma janela; alteração de
  WDM/fidelidade não altera offset. Resultado não retém dicionários mutáveis da origem.
- CLI executada com OFF e com o ZIP QKD original, hash
  `94352f15b6dcb132a3c03559b9d94b28f42b6e9adb8b2cdaab18f46720a55a79`.
  Na fixture manual, previsão = `7.606573347743155e-10 s`, persistência = `7e-10 s`.
  Referência futura e erros são nulos: **não se conclui ganho** dessa diferença.
- Ruff passou nos três arquivos Python alterados/criados.

Evidências locais: `outputs/optional_sync_shadow_20260923/regression-final.xml`,
`off-report.json` e `author-report.json`. A primeira ampliação da suíte encontrou
um problema de nome de teste muito longo no Windows; IDs curtos foram adicionados
e a execução final passou. Nenhum teste foi removido por isso.

## Limites e próximo passo

Incremento somente no código-fonte. O ZIP/EXE anterior não foi reconstruído nem
modificado. Interface, produtor contínuo, histórico shadow, avaliação posterior e
pontes dos demais modelos continuam pendentes. Os novos testes foram acrescentados
aos gates do próximo build; CI remota e empacotamento não foram executados nesta etapa.
Continua sem validação experimental de WDM→desempenho quântico, sem ação física
e sem prova de melhoria operacional.
