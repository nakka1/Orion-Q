# Ambiente local OrionQ no disco E:

Atualização de localização em 12/10/2026. Versão ativa: **5.66.1**.

| Recurso | Localização |
| --- | --- |
| Código, Git completo, resultados e releases | `E:\OrionQ\project` |
| Estado persistido Windows e cofre protegido | `E:\OrionQ\state\windows` |
| Python exclusivo Windows | `E:\OrionQ\dependencies\python312` |
| Ambiente principal recriado | `E:\OrionQ\project\.venv` |
| Clone SeQUeNCe fixado | `E:\OrionQ\dependencies\SeQUeNCe` |
| Modelos originais preservados | `E:\OrionQ\inputs\models` |
| Dados de referência externos | Diretórios `E:\OrionQ\bristol-*`, `optical-failure-dataset`, `qkd-coexistence-zenodo-21132088` |
| Temporários e caches exclusivos Windows | `E:\OrionQ\temp` e `E:\OrionQ\cache` |
| Histórico externo e versões preservadas | `E:\OrionQ\archives\external` |
| Disco Linux WSL Ubuntu-24.04 | `E:\OrionQ\platforms\wsl\Ubuntu-24.04\ext4.vhdx` |
| Inventários e recibos privados da migração | `E:\OrionQ\migration\20261011` |

Abra `E:\OrionQ\OrionQ.cmd` para usar a distribuição Windows com os caminhos explícitos do projeto e do estado. Os iniciadores em `E:\OrionQ\bin\windows` também oferecem desenvolvimento, serviço independente e cliente autenticado. Configuração, certificados e permissões existentes continuam obrigatórios; nenhum iniciador concede autoridade ou habilita atuação física.

```powershell
Set-Location -LiteralPath 'E:\OrionQ\project'
& 'E:\OrionQ\project\.venv\Scripts\python.exe' -m pytest tests/test_cross_simulator_campaign.py tests/test_sequence_network_campaign.py
$env:SEQUENCE_SRC = 'E:\OrionQ\dependencies\SeQUeNCe'
```

No Linux, o projeto fica em `/mnt/e/OrionQ/project`. Os iniciadores em `/mnt/e/OrionQ/bin/linux` usam o ambiente de qualificação preservado em ext4 e a conta `orionq-validation`. Credenciais, estado e caches Linux permanecem em ext4, fisicamente no disco E:, conservando permissões POSIX. Isso não constitui nova instalação produtiva em PetaLinux.

O caminho antigo `E:\Codex\2026-08-27\e\work\NISQ-Predictive-Twin` conserva aliases de compatibilidade para a tarefa já aberta no Codex; a carga útil ativa foi transferida ao destino. Relatórios científicos históricos mantêm suas datas, caminhos e hashes originais. Não se substituem caminhos dentro de recibos autenticados.

As alterações rastreadas e não versionadas existentes, os ambientes antigos, resultados ignorados e branches foram preservados. O ambiente principal é recriado, porque copiar um ambiente virtual não torna portáveis seus caminhos absolutos. Ferramentas compartilhadas do Windows/Codex permanecem no C:.

A migração não muda resultados científicos, maturidade TRL, modelos opcionais ou interlocks. Consulte os recibos privados para o andamento e o relatório final de transferência, validação e limpeza.

## Campanha SO(3) preservada

- Resumo: `outputs/compensation_campaign_20260923/author-v1-run1/summary.json`.
- Protocolo congelado e 240 relatórios no mesmo diretório.
- Regressão: `outputs/compensation_campaign_20260923/regression-final.xml`.
- Consolidação: [relatório de compensação](../results/orion_q_compensation_campaign_20260923.md).

Modelos permanecem opcionais e OFF por padrão. Os arquivos originais QKD/Author não estavam nos caminhos Downloads indicados no inventário inicial; não foram recriados nem substituídos por pacotes com hash diferente.

## Fechamento da migração

[Relatório final](../results/orionq_migration_20261012.md): Windows, Linux/TUI/Wayland e perfis oficial/LabOne Q validados; 89,33 GB lógicos de origens exclusivas removidos do C:. O ZIP qnet original agora está em `E:/OrionQ/inputs/models/qnet_network.zip`. Os perfis opcionais são selecionados explicitamente; LabOne Q compartilha o runtime core E e mantém suas próprias versões SDK. Originais compartilhados ou de uso incerto e aliases mínimos de compatibilidade foram preservados. Gates frios Linux e limites WSL permanecem registrados.
