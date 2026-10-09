# Validação da ponte shadow do ator de taxa QKD — 24/09/2026

Entregue em código-fonte a ponte Q16 para `qkd.keyrate.actor`, com janela decoy-state
explícita, contexto de enlace/configuração/calibração, causalidade, API somente
leitura, método do runtime e CLI de replay. Modelos permanecem opcionais e OFF
por padrão. Nenhuma configuração foi aplicada e nenhum executor foi acionado.
[Contrato e uso](../product/orion_q_keyrate_shadow_bridge.md).

## Evidências

- **564 testes aprovados**, zero falhas, erros ou skips, em **23,14 s**. A seleção
  inclui **98 novos casos** da ponte, o ator e sua sessão offline/gráfica,
  catálogo, outras quatro pontes shadow, runtime e gate físico.
- **Oito verificações com os pesos originais aprovadas**: quatro fixtures ×
  sementes 7 e 1234. Casos: início por calibração, período anterior, taxa anterior
  zero e clock no limite superior. Extensões declaradas de 25, 50 e 80 km.
- Todas as saídas, exceto o instante de avaliação, coincidiram com a sessão offline
  sobre a mesma entrada. Foram preservados features, logits, probabilidades,
  seleção, nove prévias limitadas e referência de manter os parâmetros atuais.
- O caso de taxa zero preserva a diferença histórica entre os pisos logarítmicos
  das features 7 e 13 (índices base zero). Não houve alteração da aritmética do
  export. O caso de clock superior preserva o limite de 5 GHz.
- Testes com pesos sintéticos uniformes comprovam que a ação amostrada não é
  silenciosamente substituída pela gulosa. Com os pesos originais, coincidência
  entre os dois índices em um caso não demonstra substituição nem ganho.
- Snapshot e parâmetros permaneceram idênticos. Ciclo, política, journal e os
  três executores foram substituídos por funções que falham se chamadas; nenhum
  foi chamado e os bytes do journal permaneceram iguais.
- Recusas antes dos pesos cobrem contexto, unidades, domínio, configuração,
  calibração vencida/futura, período vazio/futuro/antigo, hash dos parâmetros,
  dados inválidos e tentativa de bootstrap fora da origem do relógio.
- Exportação recusa sobrescrita; a CLI usa carregamento JSON estrito. Ruff aprovou
  os quatro arquivos Python envolvidos. Gates Windows incluem a suíte nova.
  Não houve CI remota ou reconstrução do EXE.

Os 98 testes da execução focal inicial também passaram; estão incluídos nos 564,
portanto os totais não são somados. A primeira verificação Ruff apontou imports
não usados e a forma de reexportar a fixture pytest; ambos foram corrigidos antes
da regressão final, sem mudança no comportamento da ponte.

## Limites científicos

As fixtures foram escritas à mão para verificar a integração, sem novos dados
medidos. A paridade não reproduz uma campanha de treinamento, prova segurança de
chave finita ou demonstra domínio físico. Calibração e prazo de validade são
fornecidos pelo produtor. O hash identifica conteúdo, sem autenticar sua origem.

O início por calibração é declarado e restrito à origem do relógio. No modo de
período anterior, a ponte exige os mesmos parâmetros da referência e fim do
período até 1 s antes da decisão. Essas restrições são do adaptador ORION, não
limites aprendidos ou prazos físicos do modelo. Não se reutilizam automaticamente
observáveis sob outra configuração.

Probabilidades do ator são pesos de seleção, não probabilidades de sucesso. Não
há previsão da taxa seguinte, resultados contrafactuais, chaves geradas, fidelidade
de Bell ou alegação de ganho. A [admissão offline anterior](orion_q_optional_keyrate_20260922.md)
continua sendo a referência de paridade com o episódio fornecido pelo autor.

## Reprodução e artefatos

Raiz: `E:\Codex\2026-08-27\e\work\NISQ-Predictive-Twin`.
Evidências em `outputs/keyrate_shadow_20260924/`:

- `regression-final.xml`: execução aceita de 564 testes;
- `targeted-final.xml`: 98 casos focais, sobrepostos à regressão;
- `author-original/report.json`: oito casos, sementes, índices e hashes;
- `author-original/*.json`: snapshots e avaliações completas;
- `verification.json`: contagens e auditoria de preservação;
- `before.json`, `before/`, `implementation.diff`: linha de base e alterações.

Pacote original: `C:\Users\alexa\Downloads\modelo_qkd_ml.zip`.
SHA-256: `94352f15b6dcb132a3c03559b9d94b28f42b6e9adb8b2cdaab18f46720a55a79`.
O leitor revisado não executa scripts do ZIP nem carrega o detector pickle.

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
$env:QT_QPA_PLATFORM = 'offscreen'
.\.audit-winvenv\Scripts\python.exe tools/validate_keyrate_shadow.py `
  --qkd-zip C:\Users\alexa\Downloads\modelo_qkd_ml.zip `
  --output outputs/keyrate-shadow-new
.\.audit-winvenv\Scripts\python.exe -m pytest `
  tests/test_optional_keyrate_shadow.py tests/test_optional_keyrate.py `
  tests/test_optional_keyrate_session.py tests/test_console_keyrate_session.py `
  tests/test_console_model_catalog.py tests/test_optional_sync_shadow.py `
  tests/test_optional_compensation_shadow.py tests/test_optional_allocation_shadow.py `
  tests/test_optional_routing_shadow.py tests/test_control_plane_sandbox.py `
  tests/test_physical_control_gate.py -q --basetemp=outputs/keyrate-pytest-new `
  --junitxml=outputs/keyrate-regression-new.xml
```

Use destinos novos. Os 240 relatórios SO(3), seus 15 hashes de código, resumo,
protocolo e XML de 500 testes históricos foram preservados, sem reexecução. O
presente incremento não altera as conclusões dessa campanha nem as outras pontes.

Próximo passo Q16: produtor artificial e sessão gráfica shadow do ator QKD, com
START explícito, observáveis independentes das recomendações, frescor e invalidação
de contexto. Q05 e atuação física continuam pendentes.
