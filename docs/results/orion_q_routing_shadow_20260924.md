# Validação da ponte shadow de roteamento — 24/09/2026

A ponte de roteamento Q16 foi implementada em código-fonte, com contrato de
recursos instantâneos, API somente leitura, método explícito do runtime, CLI e
exportação sem sobrescrita. Os dois checkpoints continuam opcionais e OFF por
padrão. Nenhuma recomendação aciona política, executor ou consumo de recursos.
[Contrato completo](../product/orion_q_routing_shadow_bridge.md).

## Evidências locais

- **451 testes aprovados**, zero falhas/erros/skips, em 18,51 s, incluindo **96
  testes novos** de roteamento shadow. A seleção cobre sessão offline e gráfica
  de roteamento, admissão do pacote, runtime, gate físico, outras três pontes
  shadow e catálogo nativo.
- **Quatro comparações com os pesos originais aprovadas**: dois checkpoints ×
  sementes 7 e 1234. Todas as saídas, exceto o instante de execução, coincidem
  com a sessão offline sobre a mesma entrada. Ordem de candidatos preservada.
- Snapshot e inventário permaneceram idênticos. Testes substituem ciclo,
  política, journal e os três executores por funções que falham se chamadas;
  nenhum foi chamado e os bytes do journal permaneceram iguais.
- As recusas cobrem identidade, relógio, versão, SHA dos recursos, recursos
  ausentes/reservados/expirados, memória, capacidades, endpoints, fidelidade
  mínima, candidatos inválidos e importação JSON estrita. Expiração no instante
  da decisão é recusada para uso; descarte de par expirado é admitido.
- Ruff aprovou os quatro arquivos Python envolvidos. Os gates Windows incluem
  a nova suíte. Não houve execução de CI remota nem reconstrução do EXE.

| Checkpoint | Semente | Recomendação | Paridade offline |
|---|---:|---|---|
| nominal | 7 | SWAP | Sim |
| nominal | 1234 | DELIVER | Sim |
| prazo_longo | 7 | DELIVER | Sim |
| prazo_longo | 1234 | DELIVER | Sim |

Essas decisões não foram executadas. A fixture tem três nós, três enlaces,
quatro pares e seis candidatos (um por tipo). Features foram escritas à mão para
exercitar a integração; não são observações normalizadas produzidas pelo
simulador original. Por isso, as decisões acima não demonstram mérito da política,
fidelidade final, sucesso ou ganho de roteamento. O SHA dos recursos identifica
a fixture, não uma planta certificada. O conjunto limitado de regras do ORION
não reproduz toda a legalidade do simulador do artigo.

## Arquivos e reprodução

Raiz: `E:\Codex\2026-08-27\e\work\NISQ-Predictive-Twin`.
Evidências locais em `outputs/routing_shadow_20260924/`:

- `regression-final.xml`: execução aceita dos 451 testes;
- `author-original/report.json`: quatro verificações, decisões e hashes;
- `author-original/*.json`: entradas e registros completos;
- `verification.json`: auditoria de preservação, hashes e contagens;
- `before.json` e `before/`: linha de base e cópias anteriores dos arquivos editados.

ZIP original: `F:\qnet_network.zip`, SHA-256
`6b8563209f54a2d566f1a4229b140b75fda93555085ff2bf6bfcad8f07abd938`.
Recursos da fixture, SHA-256
`cc843bc27089a14c86f0296b3e601dfaa54e5c715955dfd2633954f73c79e10c`.
O leitor revisado verifica o ZIP e lê arrays/JSON sem executar seus scripts.

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
$env:QT_QPA_PLATFORM = 'offscreen'
.\.audit-winvenv\Scripts\python.exe tools/validate_routing_shadow.py `
  --qnet-zip F:\qnet_network.zip --output outputs/routing-shadow-new
.\.audit-winvenv\Scripts\python.exe -m pytest `
  tests/test_optional_routing_shadow.py tests/test_optional_routing_session.py `
  tests/test_console_routing_session.py tests/test_author_model_intake.py `
  tests/test_control_plane_sandbox.py tests/test_physical_control_gate.py `
  tests/test_optional_sync_shadow.py tests/test_optional_compensation_shadow.py `
  tests/test_optional_allocation_shadow.py tests/test_console_model_catalog.py `
  -q --basetemp=outputs/routing-pytest-new --junitxml=outputs/routing-regression-new.xml
```

Use diretórios novos. As primeiras tentativas de teste foram preservadas:
`targeted-initial.xml` registra falha de preparação no temporário padrão;
`targeted-final.xml`, apesar do nome, **não é uma execução aceita**: o caso de
entrada grande produziu um identificador pytest que excedeu o limite de variável
de ambiente do Windows. IDs curtos corrigiram isso; a execução aceita é
`regression-final.xml`. Essas tentativas não são somadas ao total aprovado.

## Continuidade

A campanha SO(3) permanece com seus 240 registros e 500 testes históricos,
sem reexecução ou alteração dos artefatos. As verificações de roteamento não
ampliam as conclusões do [relatório científico SO(3)](orion_q_compensation_campaign_20260923.md).
A localização registrada permanece atual e as mudanças anteriores foram preservadas.

Próximo incremento Q16: produtor artificial de roteamento e sessão gráfica
shadow com START explícito, frescor, invalidação e exportação do frame capturado.
Q05, validação de domínio, medições e atuação física permanecem pendentes.
