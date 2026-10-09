# Aceitação nativa do pacote portátil — 14/09/2026

Resultado: diagnóstico incorporado ao executável e validado **no computador de desenvolvimento**, não em uma segunda máquina. Sem novo resultado científico ou atuação física.

## Evidências

- 30 testes de diagnóstico/launcher aprovados em 10,56 s: `outputs/software_validation_windows_acceptance/native-tests.xml`.
- 90 testes de campanhas, histórico e capacidades aprovados em 70,47 s: `outputs/software_validation_windows_acceptance/regression-tests.xml`.
- Ruff aprovado; sintaxe PowerShell do build e YAML do workflow verificadas localmente. O workflow remoto não foi executado.
- 12 verificações do worker congelado aprovadas: `outputs/software_validation_windows_acceptance/frozen-worker/report.json`.
- Self-test separado: 19 verificações aprovadas, incluindo matriz responsiva. Stress separado: 20 rodadas aprovadas. Relatórios em `release/windows/5.38.0-native-acceptance-20260914/`.
- Fluxo final executado via `VALIDATE-ORION-Q.cmd`, com entrada redirecionada para não aguardar a tecla final: código 0, integridade de 3831 arquivos, self-test e 20 rodadas de stress aprovados. O manifesto não conta a si mesmo; o ZIP contém 3832 arquivos.
- Relatório final copiado para `outputs/software_validation_windows_acceptance/native-acceptance-report.json`. Execução 14:55:23–14:56:09 UTC, Windows 11 AMD64, versão 10.0.26200. Os processos e relatórios gráficos são reais; a revisão manual da interface em outro computador não foi feita.

O relatório preserva `automatic_passed: true`, `acceptance_status: manual_review_required`, `independent_machine_verified: false` e `manual_review: pending`. Essas distinções são intencionais.

Uma primeira abordagem em PowerShell foi bloqueada pela política de execução local. Os arquivos provisórios dessa abordagem foram substituídos pelo módulo nativo; nenhuma política de segurança foi alterada. `tests.xml` registra essa tentativa inicial, não o estado de aprovação da implementação final; use os dois relatórios aprovados acima.

## Artefato

Pasta: `release/windows/5.38.0-native-acceptance-20260914/`.

ZIP: `ORION-Q-5.38.0-native-windows-x64.zip`, 283160607 bytes, CRC integral verificado.

SHA256 do ZIP: `da2cbbd681739fbea1731a80a51359612fc88519e29c04779d629cea40cf3ce3`.

SHA256 do executável: `d46fe06a52ef158a15e8f59873d76be814c2c7ec2b2fe6a11b8b2ed6e69021e6`.

As distribuições anteriores foram preservadas. O código científico e os parâmetros das campanhas não foram alterados neste incremento.

Próxima ação externa: extrair o ZIP em outro computador Windows e seguir o [roteiro de aceitação](../product/orion_q_windows_acceptance.md). O escopo é portátil; instalações Inno Setup, hardware e validação científica não estão cobertos por essa aprovação.
