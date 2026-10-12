# OrionQ — testes abertos e reprodução independente de artefatos locais

Data: 12/10/2026. Escopo: rechecagem focal dos IDs documentados e correção das dependências de `outputs/`/`work/`, preservando critérios físicos e de segurança. Não é uma execução integral de todos os testes, nova campanha científica ou qualificação de hardware.

## Falhas identificadas

Os seis casos da rodada TRL4/run2 citados em `orionq_trl4_consolidation_20261009.md` já tinham sido corrigidos em run3. Sua reexecução atual aprovou **6/6 em 112,32 s**: exportação após revogação, troca de origem na mesma janela, atualização Edge após alteração real do enlace, identificação das operações dos botões, publicação com terminal oculto e consumo/reset de pares pela interface. Não se afirma que seis defeitos atuais foram corrigidos nesta rodada; a descrição do pedido não contém outros seis IDs.

A regressão ampliada de `service_provider.stop` revelou duas falhas atuais adicionais no teste `test_first_frame_source_reset_does_not_reenter_backend_from_cleared_jobs`, variantes `policy_context` e `policy_review`. A reprodução focal teve um caso aprovado e dois reprovados, em 0,70 s. A fixture criava `SwitchableProvider` sem chamar `start()`, mas esperava permissões apresentáveis: as duas janelas recusam corretamente uma fonte parada.

A fixture agora inicia e encerra a fonte pelos métodos reais. As asserções originais de capacidade, incremento das gerações, limpeza dos resultados e ausência de consultas síncronas ao backend foram mantidas. Dois controles negativos novos verificam que um frame declarado pronto não habilita ações nem cria binding/contexto quando a fonte continua parada. **Todo o módulo de segurança passou: 18 casos, 25,68 s.** A produção da autenticação/autorização não foi alterada.

## Cinco dependências removidas

| Caso ou coleta | Dependência anterior | Entrada atual |
|---|---|---|
| `test_real_model_metrics_keep_correct_titles_units_and_count` | Bundle gerado em `outputs/wdm_model_bundle_v527` | Exportação temporária determinística pela API real, com SHA fixado no serviço; modelo simulado diagnóstico sem aprovação física |
| `test_real_frozen_census_read_only_has_three_identified_fpga_configurations` | Censo, matriz e relatório históricos em `outputs/sequence_network_20261010/run1` | Três JSON públicos arquivados byte a byte em ZIP versionado; extração temporária, contagens, identidades e hashes preservados |
| `test_author_tree_tampering_rejected_before_import` | Download/árvore em `outputs/space_validation_preparation_20261010` | ZIP oficial MIT de 138.402 bytes versionado; todos os 67 arquivos precisam corresponder antes de qualquer importação |
| `test_extra_upstream_module_rejected` | Mesma árvore local | Mesmo arquivo oficial verificado; módulo extra é recusado |
| Coleta de `test_package_git_release.py` | Utilitário em `work/publish_windows_native_5_64_2.py` | Cópia byte a byte do utilitário em `tools/`; arquivo histórico intacto e comportamento preservado |

As fixtures ficam em `tests/fixtures/qualification/`, acompanhadas por `provenance.json`, README e licença MIT do autor. O ZIP óptico mantém o hash científico original `895b3b093b33587d08c9d878c94597825ad0d83dae564a34e6455b673146f482`; não houve substituição do pin por uma referência sintética. Censo/matriz/relatório são da topologia histórica, não uma validação da topologia atual ampliada.

O exportador Git admite somente os dois nomes exatos das fixtures, com hashes fixos independentes do manifesto editável. ZIP desconhecido, renomeado ou adulterado continua recusado; cinco regressões novas cobrem inclusão e recusa. Segredos, SDKs, distribuições e arquivos brutos de aquisição continuam sujeitos aos filtros existentes.

## Evidência de reprodução

- Regressão inicial das quatro famílias após substituir os insumos: **410/410**, 33,19 s.
- Exportador Git após a exceção restrita por SHA: **356/356**, 65,33 s. Estes casos se sobrepõem aos conjuntos seguintes e não devem ser somados.
- Checkout separado, outro diretório de trabalho, sem `outputs/` nem `work/`: **415/415**, 135,32 s. Apenas 22 arquivos de código/configuração/fixtures, 848.515 bytes, foram copiados com SHA conferido. Os dois diretórios permaneceram ausentes antes e depois. O runtime Python e o código de produção vieram da instalação atual: não é prova de instalação limpa das dependências.
- Ruff dos oito módulos Python alterados: aprovado.

A primeira preparação do checkout mínimo omitiu `experiments/run_wdm_controller_comparison_campaign.py`, importado pelo exportador de bundle. Seu resultado de **414 passagens e uma falha de preparação** foi preservado. A revisão 2 incluiu esse fonte já versionado e reexecutou os mesmos 415 casos. Não houve mudança de critério nem utilização de artefatos ignorados para obter a aprovação.

O [resumo verificável](../../outputs/open_defects_20261012/test-portability-summary.json) contém os casos e SHA dos JUnits, hashes finais dos arquivos, escopos e limitações. As tentativas negativas permanecem em `source-reset-before.xml` e `hermetic-clean-checkout.xml`; as aprovações finais em `source-reset-after.xml`, `six-historical-current.xml` e `hermetic-clean-checkout-r2.xml`.
