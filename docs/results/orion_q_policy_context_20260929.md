# Q05 — observação contextual local (29/09/2026)

Implementada API de observação em sessão própria, vinculando uma entrada simulada
explícita ao runtime existente, sua política/supervisor e topologia persistida.
[Contrato e uso](../product/orion_q_policy_context_q05.md).

## Resultado e limites

A referência tem hash, identidade da sessão/runtime, revisão de objetos observados,
configuração e versão/hash da topologia. Expira em 300 s monotônicos. Entrada
incompleta ou contexto alterado/expirado é recusado; fonte/runtime substituídos
invalidam a sessão. Propostas/aprovações existentes não são usadas nem modificadas
pela observação.

O snapshot é explícito e limitado a 64 KiB, com proveniência estruturada, origem
obrigatória e esquema suportado. O diagnóstico compara semântica simulada Bell,
nó repetidor, versão, memória, fidelidades/idades dos pares e telemetria persistida.
Idade decorrida é somada à declarada; relógio futuro/divergente, incerteza excessiva,
OOD, integridade não declarada e ativação não resolvida produzem bloqueadores.

As leituras usam bloqueio de ciclo e SQLite somente leitura; segunda captura recusa
mudança confirmada durante o primeiro snapshot. Não há reserva do estado depois
da observação ou prova de ausência de mudanças transitórias não observadas.

OBSERVED_SUPPORTED_INPUT significa verificações atendidas para o controlador
existente, no instante observado. A origem continua declarada pelo chamador e não
é autenticada. Não significa calibração científica, autorização de ação ou política
ativa comprovada por histórico. Não há avaliação por ação, inferência ou decisão.
Todas as onze opções adicionais continuam BLOCKED; passar nas verificações de
entrada do alvo não valida o domínio específico desses componentes.

O catálogo declarativo permanece independente de runtime. Esta entrega é API local
e diagnóstico CLI isolado; observação contextual na interface nativa ainda é o
próximo incremento. Seleção, preparação por este recibo e ativação permanecem
indisponíveis. Não há rota HTTP nova ou importação de recibo como autorização.

## Validação

- **341 testes aprovados** na regressão final, incluindo **51 novos**;
  zero falhas, erros ou skips. As rodadas intermediárias não somam ao total.
- Novos testes verificam ausência de decisão/execução/carregamento, preservação de
  proposta/banco, todas as opções, incompatibilidade de entrada/proveniência/tempo,
  solicitações inválidas, substituição de política equivalente, supervisor, runtime,
  fonte, caminho e topologia, expiração, banco ausente, escrita concorrente e falha
  de ativação. Metadados/origem/esquema inválidos são recusados.
- Regressão inclui contratos declarativos, preparação, aprovação, ativação,
  recuperação, serviço QKD e catálogo nativo. As primeiras rodadas corrigiram
  duas suposições da fixture (retorno tupla e estado STORING), antes da execução final.
- Ruff passou em quatro arquivos Python; YAML, sintaxe PowerShell e diff passaram.
  Checks Windows incluem a nova suíte. EXE não reconstruído; CI remota não executada.

O diagnóstico em `outputs/policy_context_20260929/diagnostic/` registra baseline
OBSERVED_SUPPORTED_INPUT e roteador BLOCKED, recusa após invalidação e banco
inalterado pelas consultas. Seu simulador é criado em diretório novo: **zero ciclos,
zero modelos carregados e nenhuma atuação física**. Recibos e relatório foram
verificados por hash. As fixtures de teste também usam bancos isolados.

## Preservação e roadmap

A comparação parte de **633 arquivos preexistentes**: oito arquivos editados
neste incremento e cinco novos de sua autoria. Trabalho de monografia e demais
alterações anteriores foram preservados. A auditoria `verification.json` confere
240 relatórios SO(3), quinze fontes congeladas e os hashes de summary, protocolo e
XML histórico dos 500 testes. Nenhuma campanha científica foi refeita.

`implementation.diff` contém somente este incremento. O [roadmap](../product/roadmap.md)
agora pede inspeção contextual nativa com contexto/entrada explícitos, trabalho em
segundo plano, invalidação e exportação capturada. Q05 segue parcial; os adaptadores
de seleção de modelos não foram implementados. Modelos opcionais/OFF e sem atuação
física; nenhum runtime operacional foi ativado.
