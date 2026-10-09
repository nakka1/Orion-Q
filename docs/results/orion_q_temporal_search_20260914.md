# Busca no histórico de campanhas — 14/09/2026

Entregue busca literal pelo início do ID, com aplicação explícita, paginação que preserva o filtro, limpeza na troca de fonte e abertura verificada sem reexecução. Sem alteração do motor físico, estatística ou dados de pesquisa.

## Verificação

- **120 testes aprovados**, 80,25 s: `outputs/software_validation_temporal_search/accepted-tests.xml`. Os relatórios anteriores se sobrepõem e não devem ser somados. A primeira tentativa identificou uso incorreto de `.click()` no teste de um botão customizado; a fixture foi corrigida para emitir seu sinal público. Foi acrescentado teste de apresentação literal de marcações.
- Ruff aprovado nos módulos alterados. Sintaxe do build PowerShell e YAML do workflow verificados; workflow remoto não foi disparado.
- Layout inspecionado em 640×500 e 1080×740: `search-640.png` e `search-1080.png` na pasta de evidências.
- Paridade de código entre fonte e PYZ verificada para `campaign_runner`, `campaign_job` e `temporal_campaign`, compilando com otimização 1 e normalizando somente `co_filename`. A última proteção PlainText está no binário entregue.
- **13 verificações do worker aprovadas**: `outputs/software_validation_temporal_search/frozen-worker/report.json`. A busca nesse roteiro usa o controlador Python sobre um recibo criado pelo worker real; não representa interação manual com a busca compilada.
- Self-test do executável aprovado (19 verificações, incluindo matriz responsiva), stress de 20 rodadas aprovado.
- Aceitação nativa final aprovada em 42,27 s: `release/windows/5.38.0-history-search-20260914/native-acceptance/acceptance-report.json`. Integridade de 3831 arquivos, self-test e stress aprovados. O manifesto não conta a si mesmo; o ZIP tem 3832 arquivos.

## Distribuição

Pasta: `release/windows/5.38.0-history-search-20260914/`.

ZIP: `ORION-Q-5.38.0-native-windows-x64.zip`, 283162301 bytes, CRC integral verificado.

SHA256 do ZIP: `7b52fe1345f2b57a107e1a8fedfddcaba7e6f54d5ec0c12da2412bf8952d99e7`.

SHA256 do executável: `b9d2c4f27ecdeb113ce2ae2a3aacdc82a864ed1d8bfbe2c04c924ba03b5ed2f6`.

As distribuições anteriores foram preservadas. O relatório final mantém `manual_review_required` e `independent_machine_verified: false`: a verificação manual em outro computador continua pendente.

## Limites e próximo incremento

Filtro por prefixo, não busca exata, por data ou por metadados físicos. O limite de resultados não limita as linhas que o SQLite examina; não há benchmark em diários de milhões de registros. A busca não agrega resultados científicos entre campanhas.

Próxima pendência funcional de Q14: diário persistente das tentativas de execução, incluindo falhas e cancelamentos sem recibo. Esses estados devem ficar separados de resultados quânticos e nunca virar timeouts ou fidelidade zero. Roteiro de uso atual: [histórico](../product/orion_q_temporal_history.md).
