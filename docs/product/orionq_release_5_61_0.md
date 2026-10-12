# OrionQ 5.61.0 — notas e reprodução da entrega

Esta versão reúne as alterações atuais do projeto e a consolidação computacional de TRL de 09–10/10/2026. A versão exibida na interface e nos recursos Windows deriva de `pyproject.toml`.

## Alterações incluídas

- Processamento SNS-TF de blocos finitos no backend, com preparação privada, comunicação clássica causal, reconciliação, verificação e amplificação de privacidade. A sessão `RIO-SNS-FINITE` usa dados simulados identificados; a estimativa é condicional e os bits certificados continuam zero.
- Relay orbital dedicado `orbital-memory-cbpf-bsc-v1`, com captura heraldada sem medição destrutiva, memórias, BSM, confirmação de frames e consumo dos pares. Os equipamentos e parâmetros são hipotéticos e não qualificam enlaces físicos ou rotas globais arbitrárias.
- Correções de avanço óptico terrestre e integração de abertura/jitter gaussiano por eixo. Os contratos, hipóteses e fontes estão em [resolução híbrida](orionq_hybrid_remediation.md).
- Documentação e evidências de TRL 4, preparação SOFT-DT para TRL 5, validação com SeQUeNCe e comparação óptica. Os relatórios históricos e seus freezes são preservados; a nova versão não muda retroativamente seus resultados.

## Executar e compilar

Extraia o ZIP portátil inteiro e execute `OrionQ/OrionQ.exe`, mantendo a pasta `_internal` e os demais arquivos ao lado do executável. Feche a instância anterior antes de executar outra versão. Os dados locais continuam em `%LOCALAPPDATA%/ORION-Q`; esta entrega não apaga configurações existentes.

Em um checkout limpo, com Python e os requisitos do projeto:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[console,desktop,dev,satellite,lab]"
powershell -ExecutionPolicy Bypass -File tools\build_windows_console.ps1 -SkipInstaller
```

O script restaura somente recursos com hashes fixados em `runtime_assets/native/v1`, testa as fontes, compila com PyInstaller e executa os diagnósticos do binário. `-SkipInstaller` produz a distribuição portátil. O teste de modelos sem pacotes não valida ZIPs proprietários externos.

Para repetir a campanha híbrida, use um diretório de saída novo:

```powershell
.venv\Scripts\python.exe tools\run_hybrid_remediation_campaign.py --output outputs\hybrid-reproduction --workers 3
```

Consulte os comandos e dependências externas em [SeQUeNCe](orionq_sequence_network.md) e [validação cruzada](orionq_cross_simulators.md). O SDK NetSquid não foi executado. Os resultados computacionais não substituem medição, sincronismo, calibração e qualificação física.

## Repositório e evidências

O pacote Git é um snapshot novo das fontes atuais, preservando alterações e exclusões sem modificar o histórico original. Inclui testes, configurações, documentação, relatórios, matrizes, freezes, JUnits e manifestos selecionados. `release-source-manifest.json` registra os hashes e as exclusões. Os grandes bancos SQLite, capturas brutas, estados locais, ambientes, SDKs e binários ficam fora do Git. Os dados brutos permanecem no workspace original; resumos e hashes permitem rastreá-los, mas não equivalem à inspeção completa dos dados omitidos.

A distribuição portátil acompanha os documentos de operação e os relatórios científicos. Os artefatos referenciados em `outputs/` nesses relatórios estão no pacote Git de evidências; não são copiados integralmente para a distribuição do executável.

## Limites de aceitação

TRL 5 não foi demonstrado. O critério G05 de desempenho permanece aberto, assim como reprodução em outro ambiente, revisão humana identificada e qualificação física. A atuação física permanece bloqueada, os modelos ML são opcionais e material emulado não é admitido como chave operacional. Aprovação automática local do pacote não constitui certificação científica ou de segurança do equipamento.
