# Sessão gráfica shadow do ator de taxa QKD — 26/09/2026

Concluído o incremento Q16 iniciado em 24/09: produtor artificial e sessão nativa
shadow para `qkd.keyrate.actor`, integrados ao snapshot e ao catálogo existente.
A janela exige START explícito, mantém os parâmetros da fixture e compara a
recomendação com manter os valores atuais. Nenhuma ação é aplicada, nenhum
comando é emitido e nenhum resultado de ação é simulado.
[Contrato e uso](../product/orion_q_keyrate_shadow_bridge.md).

## Validação concluída

- **530 testes selecionados aprovados**, zero falhas, erros ou skips, em
  **105,24 s**, incluindo **50 novos casos** da interface/produtor QKD.
  A regressão cobre também a ponte/ator/sessão offline QKD, as outras quatro
  famílias de interface shadow, catálogo, fontes, telas, runtime e gate físico.
- **Seis avaliações nativas com os pesos originais aprovadas**: primeiro frame
  por calibração e cinco períodos anteriores explícitos. Incluem um período de
  taxa anterior zero. Todas as saídas, exceto o instante de avaliação, coincidem
  com a sessão offline sobre a mesma entrada e semente.
- As nove prévias e o baseline de manter parâmetros foram preservados. Snapshot
  do produtor permaneceu igual antes/depois de cada avaliação; nenhuma
  recomendação alterou os parâmetros ou a evolução dos observáveis.
- START/STOP, fechamento, perda/troca de fonte, mudanças de enlace/configuração/
  calibração, expiração, workers atrasados e ausência de fila adicional estão
  cobertos. Alterar apenas a decisão sobre um período já recebido não renova
  frescor, nem na sessão nem ao reabrir pelo catálogo.
- Exportação captura o frame antes do seletor. Atualização válida não substitui
  o capturado; perda de contexto cancela a exportação e arquivo existente é
  preservado. O catálogo reconfere o payload em cache antes da abertura.
- Capturas em **640×480 e 1000×700** foram inspecionadas visualmente: texto legível,
  comparação acessível e todos os controles dentro da janela.
- Ruff aprovou os sete arquivos Python envolvidos. Gates Windows incluem a
  nova suíte; YAML e sintaxe PowerShell foram conferidos. Não houve CI remota
  ou reconstrução do executável.

A execução focal inicial de 50 testes está incluída na regressão de 530; não se
somam os totais. Esta seleção difere da regressão anterior da ponte backend,
que permanece registrada separadamente com 564 testes.

## Produtor e limites científicos

`SyntheticKeyRateStream` publica um enlace artificial decoy-state de 50 km.
Cada intervalo consecutivo representa 1 s virtual. Parâmetros e calibração
permanecem fixos no segmento; observáveis variam segundo funções determinísticas
e uma semente, sem consumir recomendações. Não são medições nem resultados de
um modelo físico validado. A taxa anterior zero a cada seis intervalos exercita
o contrato numérico, sem representar falha de uma ação executada.

Após descontinuidade de tick, fonte, campanha, adaptador, semente ou nó, o produtor
inicia outro segmento na publicação atual e o identifica como
`calibration_bootstrap`. Não preenche intervalos ausentes. Vencimento da validade
de um dia virtual da calibração da fixture também inicia um segmento e invalida
a sessão; não significa que um equipamento foi recalibrado.

O relógio de frescor da interface usa chegada de períodos novos, com limite de
3,5 s. O período em si continua sujeito à causalidade e ao limite de idade da
ponte. Versão de parâmetros é identidade de configuração, não contador de frames.

Probabilidades do ator não são probabilidades de sucesso. Diferenças nos
parâmetros não medem ganho; não há previsão da taxa seguinte, chaves geradas,
certificado de chave finita, fidelidade de Bell ou atuação física. Os resultados
comprovam integração e paridade em entradas artificiais.

## Evidências e reprodução

Raiz registrada: `E:\Codex\2026-08-27\e\work\NISQ-Predictive-Twin`.
Diretório local: `outputs/keyrate_shadow_ui_20260924/`. A data do diretório
identifica o início do incremento; a validação final foi concluída em 26/09.

- `regression-final.xml`: 530 testes da execução aceita;
- `targeted-initial.xml`: 50 casos focais, sobrepostos à regressão;
- `author-original/report.json`: seis avaliações, modos, sementes e hashes;
- `author-original/qkd.keyrate.actor-*.json`: registros e snapshot inicial;
- `author-original/qkd.keyrate.actor-640.png` e `-1000.png`: capturas conferidas;
- `verification.json`: contagens, hashes finais e preservação;
- `before.json`, `before/`, `implementation.diff`: linha de base e alterações.

ZIP externo: `C:\Users\alexa\Downloads\modelo_qkd_ml.zip`.
SHA-256: `94352f15b6dcb132a3c03559b9d94b28f42b6e9adb8b2cdaab18f46720a55a79`.
A verificação existente lê arrays/JSON sem executar scripts do pacote ou carregar
pickle. Pesos não foram embutidos ou redistribuídos.

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
$env:QT_QPA_PLATFORM = 'offscreen'
.\.audit-winvenv\Scripts\python.exe tools/validate_keyrate_shadow_ui.py `
  --qkd-zip C:\Users\alexa\Downloads\modelo_qkd_ml.zip `
  --output outputs/keyrate-ui-new
.\.audit-winvenv\Scripts\python.exe -m pytest `
  tests/test_console_keyrate_shadow.py tests/test_optional_keyrate_shadow.py `
  tests/test_optional_keyrate.py tests/test_optional_keyrate_session.py `
  tests/test_console_keyrate_session.py tests/test_console_routing_shadow.py `
  tests/test_console_allocation_shadow.py tests/test_console_sync_shadow.py `
  tests/test_console_compensation_shadow.py tests/test_console_model_catalog.py `
  tests/test_console_input_sources.py tests/test_console_screens.py `
  tests/test_control_plane_sandbox.py tests/test_physical_control_gate.py `
  -q --basetemp=outputs/keyrate-ui-pytest-new --junitxml=outputs/keyrate-ui-regression-new.xml
```

Use diretórios novos. A tentativa de diagnóstico anterior à retomada não chegou
a executar devido a indisponibilidade da revisão automática de aprovação;
nenhuma avaliação desse bloqueio é contabilizada. O diagnóstico acima foi
executado integralmente após a retomada.

## Posição no roadmap

As dez configurações admitidas agora dispõem de interface offline e shadow
artificial em fonte. Q16 permanece parcial: detector em formato seguro, validação
em domínio medido, termos de redistribuição dos pesos e integração Q05 continuam
pendentes. O próximo incremento retorna ao pré-requisito Q04: reconciliação
explícita de comandos de inventário não resolvidos, preservando recibos e sem
reexecução automática, antes da preparação/revisão de políticas em simulação.

Os modelos continuam opcionais e OFF por padrão. As alterações anteriores foram
preservadas. A campanha SO(3) mantém 240 registros, os 15 hashes de código,
resumo, protocolo e XML histórico de 500 testes, sem reexecução ou ampliação de
suas conclusões científicas.
