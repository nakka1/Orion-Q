# Ambiente local após a mudança para E:\Codex

Registro verificado em 24/09/2026. Raiz atual deste checkout:

```text
E:\Codex\2026-08-27\e\work\NISQ-Predictive-Twin
```

O ambiente virtual registra como caminho histórico de criação
`C:\Users\alexa\Documents\Codex\2026-08-27\e\work\NISQ-Predictive-Twin`.
Esse registro não é a localização atual. Usar caminhos relativos à raiz para
código, configurações e resultados; o caminho absoluto acima identifica somente
esta instalação, sem impor diretório a outras máquinas.

```powershell
Set-Location -LiteralPath 'E:\Codex\2026-08-27\e\work\NISQ-Predictive-Twin'
.\.audit-winvenv\Scripts\python.exe --version
```

O executável acima iniciou e executou a auditoria documental em 24/09, reportando
Python 3.12.14. `pyvenv.cfg` ainda guarda a versão/caminho históricos de criação.
Esse teste não valida todos os launchers do ambiente movido; preferir invocação
explícita de `python.exe` e `python.exe -m ...` em vez de wrappers antigos.
O ambiente não foi recriado nem seus metadados históricos reescritos.

O Git local apontou diferença de proprietário após a mudança. A inspeção deste
checkout utilizou uma exceção limitada a cada comando:

```powershell
git -c safe.directory=E:/Codex/2026-08-27/e/work/NISQ-Predictive-Twin status --short
```

Nenhuma configuração global de confiança foi alterada. A mudança de localização
não autoriza descartar alterações rastreadas ou arquivos novos. O estado existente
foi preservado durante a consolidação; código científico e artefatos originais
não foram modificados.

## Continuidade da campanha SO(3)

- Resumo: `outputs/compensation_campaign_20260923/author-v1-run1/summary.json`.
- Protocolo congelado e 240 relatórios: mesmo diretório do resumo.
- Regressão: `outputs/compensation_campaign_20260923/regression-final.xml`, no pai
  de `author-v1-run1`, não no pai do repositório.
- [Relatório consolidado](../results/orion_q_compensation_campaign_20260923.md):
  grade completa, cobertura, limites e hashes.
- [Roadmap](roadmap.md): campanha concluída; Q16 parcial e Q05 pendente.

`outputs/`, ambientes virtuais e releases são locais e ignorados pelo Git.
Preservar esses diretórios ao mover ou fazer backup do trabalho. Os ZIPs do autor
continuam externos, sem cópia para o produto. Os modelos permanecem opcionais,
OFF por padrão, somente offline/shadow e sem atuação física.
