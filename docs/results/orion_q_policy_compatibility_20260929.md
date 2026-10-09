# Q05 — compatibilidade declarada (29/09/2026)

Concluído o incremento iniciado em 28/09: critérios declarados de compatibilidade
na API local, CLI com exportação exclusiva e catálogo nativo. A política existente
recebe CONTEXT_REQUIRED; as onze opções adicionais permanecem BLOCKED. Os motivos
separam identidade, domínio de entrada, tradução de ações, ciclo de ativação,
evidência científica e aprovação. [Contrato e uso](../product/orion_q_policy_compatibility_q05.md).

## Resultado

O relatório descreve contratos conhecidos para `repeater.utility.v1` em simulação.
Não observa runtime, instalação ou telemetria corrente. Nenhum nome de ação ou
verificação de ZIP transforma previsões/recomendações em política selecionável.
Os roteadores, por exemplo, incluem GENERATE, DELIVER e DISCARD, e os nomes
WAIT/PURIFY/SWAP compartilhados ainda exigem tradução de recursos e semântica.
O detector permanece bloqueado por ausência de export seguro.

O módulo usa somente biblioteca padrão e catálogo leve. Consultar a API não
inicializa runtime, banco, preparação ou pesos. A CLI grava somente o destino
explícito e recusa arquivo existente. IDs desconhecidos e declarações ausentes
são recusados. Recibos têm SHA-256 local e horário de geração, sem assinatura,
observação de estado ativo ou autoridade de seleção.

No catálogo, o estado Q05 e seu alvo aparecem no início dos detalhes. A verificação
de pacote permanece separada; sucesso dessa verificação não elimina bloqueadores.
A captura em 640×480 foi inspecionada após ajustar a ordem do conteúdo; estado,
alvo e escopo ficam visíveis, e o restante é acessível por rolagem. Uma captura
adicional em 1060×760 também foi produzida.

## Validação

- **379 testes distintos aprovados**, incluindo **45 novos**: 33 de contrato/API/CLI
  e 12 de apresentação nativa. Zero falhas, erros ou skips.
- Rodada direcionada inicial: 73 casos. Rodada final, após formatação e ajuste da
  ordem visual: os mesmos 73 casos aprovados (`final.xml`). Integração: 306 casos
  (`integration.xml`). As repetições foram descontadas do total distinto.
- Cobertura nova inclui declarações de todas as variantes, ações de roteamento sem
  tradução implícita, detector bloqueado, referência não ML, ausência de contexto,
  IDs/tipos/modos recusados, rejeição de indicadores de compatibilidade fornecidos
  pelo chamador, recibos independentes, entrada nova sem declaração, correspondência
  com contratos tipados, importação sem backend numérico e sem acesso a artefatos,
  banco ou rede, preservação de proposta corrente, exportação sem sobrescrita e
  pacote verificado sem promoção de seleção.
- Regressão cobre preparação, aprovação, ativação, recuperação, decisões históricas,
  serviço QKD, verificação de arquivos, capacidades, providers e fontes de entrada.
- Ruff passou nos seis arquivos Python tocados; YAML do workflow, sintaxe PowerShell
  e diff passaram. Os checks Windows incluem as duas novas suítes. O EXE não foi
  reconstruído e CI remota não foi executada.

Evidências em `outputs/policy_compatibility_20260928/`: XMLs, matriz
`compatibility-catalog.json`, capturas e texto nativo em `gui/`,
`implementation.diff` e `verification.json`. O nome do diretório conserva a data
de início; a consolidação foi concluída em 29/09.

## Preservação e próximo passo

A auditoria compara os **606 arquivos preexistentes** com a cópia inicial: **598
inalterados** e oito arquivos modificados neste incremento, além de seis novos de
autoria deste incremento. Arquivos adicionais de monografia e roadmap de pré-banca
surgidos durante o intervalo foram apenas inventariados, sem edição; não pertencem
ao diff Q05. Seus hashes observados constam da auditoria.
Os 240 relatórios SO(3), quinze fontes congeladas, summary, protocolo e XML histórico
dos 500 testes mantêm os hashes. Não houve nova campanha científica ou eleição de
modelo a partir desses resultados.

O [roadmap](../product/roadmap.md) agora pede observação de compatibilidade vinculada
a um contexto simulado concreto: identidade/configuração, domínio e origem da
entrada, versão/hash da topologia e semântica das ações. Ausência de mapeamento ou
mudança de contexto deve impedir preparação de troca. Q05 permanece parcial.

Não houve decisão, inferência, ciclo ou atuação pela consulta de compatibilidade.
A fixture de integração inicializa seu próprio simulador apenas para verificar a
preservação da proposta e do banco; nenhum runtime operacional foi ativado.
Modelos permanecem opcionais/OFF, e `set_decision_source` continua bloqueado.
