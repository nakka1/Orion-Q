# ORION-Q 5.47 — correções e validação local da bancada

Data: 07/10/2026. Workspace: `E:\Codex\2026-08-27\e\work\NISQ-Predictive-Twin`. Evidências: `outputs/lab_hardening_20261007/`. Os resultados anteriores e suas falhas intermediárias foram preservados. Não foi conectado equipamento, instalada licença proprietária, habilitado modelo opcional ou executada atuação física.

O defeito Swabian foi reproduzido: a aplicação do primeiro trigger podia modificar o equipamento emulado antes de rejeitar o segundo, conservando `ready` e configuração antiga. A implementação agora valida todos os parâmetros antes de qualquer setter. Falha durante aplicação deixa `configuration_unverified`, sem configuração publicada e sem aquisição. Releitura sincronizada e nova calibração são obrigatórias. As outras prioridades receberam implementação e ensaios locais; isso não encerra a qualificação física.

## Matriz de prioridades e evidências

| Prioridade | Implementação/correção | Prova local | Pendência externa ou limite |
| --- | --- | --- | --- |
| Imediata — configuração Swabian | Preflight completo, exclusão das chamadas, limpeza da configuração diante de falha, readback antes de aquisição | Dois testes falhavam antes; sequência 0,35/0,35 → 0,50/0,35 → aquisição bloqueada → releitura/verificação → calibração | Interface SDK emulada; valores elétricos não medidos |
| Imediata — disponibilidade | Dependências por `photon`, `qkd`, `all`; API de configuração versionada e formulário com unidades/limites | Dependências removidas em processo isolado; aquisição emulada permanece disponível e QKD é bloqueado antes de criar execução | `find_spec` indica presença; execução ainda pode falhar se instalação estiver corrompida |
| Imediata — recuperação | Lock de processo local, SQLite com eventos/outbox, projeção atômica de recibos e estado `interrupted` | Concorrência real e seis encerramentos de intérpretes durante commit/publicação; três reinícios sem reexecução/duplicação | Não qualifica energia, discos avariados ou compartilhamento distribuído |
| Alta — entrega incerta | Reserva irreversível, consumo antes da resposta, invalidação e outbox entre KMEs | Cortes de HTTP, partição, morte/reinício, resposta perdida e conflito de token; 14 condições de falha aprovadas | Chaves de teste, PKI e wrapping efêmeros; não cofre de produção |
| Alta — pontas independentes | Duas KMEs e duas aplicações em quatro processos, SQLite separado e mTLS real em loopback | Correspondência de registros, comunicação KME/SAE e SAE/SAE por TLS/AES-GCM; atraso e reinício de uma ponta | Fonte BB84 centralizada; ensaio não prova aparelhos QKD independentes |
| Alta — aquisição → protocolo | Observações explícitas com preparação/análise, bases/bits, clock e hash; BB84 consome esses registros | Reprodução bit a bit, bases diferentes, clock inválido, deslocamento, jitter e abortos | Timestamps isolados não fornecem bases; fonte física exige logs e clock medido |
| Alta — captura contínua | Drenagem, cancelamento, persistência por blocos, perdas e recuperação | 200 eventos nominais; 300 conservados + 1700 perdidos sob saturação; 60 no cancelamento; morte real do escritor | Eventos em voo perdidos sem contagem ficam explicitamente desconhecidos |
| Alta — contratos/substituição | Capacidades instrumentais separadas da configuração; registro de adaptadores usado pelo serviço | Emulação → referência → replay ORION pela mesma API, sem alterar o núcleo; metadados e hashes originais preservados | Swabian nativo não é selecionável pela API local emulada |
| Replay Swabian | Importador da API FileReader oficial, lista/hashes de segmentos, configuração e normalização | Fixtures identificadas de SDK; cabeçalho/segmentos ausentes, eventos inválidos e incompatibilidades recusados | SDK, licença aplicável e FileWriter completos ausentes; replay nativo não validado |
| Drivers proprietários | Contratos de dispositivo, limites, estados e diálogos SDK exercitados por doubles | Erros por canal, reconexão, configuração incerta e incompatibilidades | Nenhum SCPI ou resposta de unidade desconhecida foi inventado; manuais específicos/unidades pendentes |
| Média — rigor QKD | Amostragem X/Z com Serfling, contagem de vazamento, verificação e PA Toeplitz | Nove regimes/sementes, limites independentes, paridades e matriz Toeplitz de referência | Modelo ideal de fóton único; sem prova de segurança de fonte física/decoy |
| Média — inicialização | Diagnóstico do timeout e espera pelo event loop do Qt; lançador resolve `src` | Três timeouts com qWait e quatro execuções eventloop aprovadas; regressão de deadline/sinais | Um dump nativo parcial adicional permanece não classificado; não se promete ausência de travamentos em todo ambiente |
| Satélite público | Fonte OMM assíncrona, cache/hash/cadência, propagação SGP4 e vetores cobertos | ISS real pública, 181 amostras e integração no cálculo geométrico; fixtures de erro/expiração/adulteração | Predição orbital não é housekeeping/payload; Micius 41731 retornou 404 nesta consulta |

## Casos de uso verificados

| Caso | Entrada/ação | Resultado esperado e obtido | Evidência |
| --- | --- | --- | --- |
| SW-01 | Trigger inválido no segundo canal | Nenhum setter executado; configuração anterior preservada | `swabian/before.xml`, `swabian/validation_v4.xml` |
| SW-02 | Setter do segundo canal lança exceção após primeiro aplicado | Configuração publicada removida; aquisição bloqueada | `swabian/state-sequence.json` |
| SW-03 | Readback diverge do esperado | Estado incerto permanece; sem aquisição | Mesmo registro de sequência |
| SW-04 | Valores aplicados relidos, explicitamente aceitos, calibrados | Aquisição só retorna após verificação e calibração | `swabian/summary_v2.json` |
| SW-05 | Configuração externa muda antes da captura | Divergência detectada antes de leitura de eventos | Testes SDK emulados |
| CF-01 | NaN, boolean em campo numérico, chave desconhecida, faixa inválida | Rejeição sem gravação parcial ou início de execução | `tests/test_lab_configuration.py` |
| CF-02 | Revisão antiga ou JSON com chave duplicada | Recusa; configuração atual permanece | API/configuração |
| CF-03 | Remover individualmente numpy/scipy/cryptography do ambiente de processo | Disponibilidade por experimento e bloqueio correto | Teste de importação isolada |
| CF-04 | Executar com configuração alterada | Recibo vinculado ao hash/revisão e métricas decorrentes dos eventos | `acceptance_source_v2/report.json` |
| RP-01 | Selecionar aquisição por UUID no formulário/API | Replay conserva origem, eventos e métricas; execução tem UUID novo | Mesmo relatório, adaptador `orion-replay` |
| RP-02 | Alterar parâmetros originais ou adulterar manifesto/eventos | Replay rejeitado | Configuração e testes de aquisição |
| RC-01 | Duas instâncias no mesmo workspace | Uma operação admitida; segunda `laboratory_busy` | `recovery/verification-final.xml` |
| RC-02 | Matar processo antes/depois de commit, temporário ou replace | Estado explícito e recibos reconciliados com SQLite | `recovery/verification-summary.json` |
| RC-03 | Reiniciar três vezes após interrupção | Sem reexecução e sem duplicação de eventos confirmados | Mesmo resumo e [relatório](orion_lab_recovery_20261007.md) |
| KD-01 | Duas KMEs/aplicações trocam dados por rede local | Quatro PIDs, dois bancos, TLS mútuo e ciphertext/AAD verificado | `keys/validation-final-v3/independent-delivery/qkd_report.json` |
| KD-02 | Perder resposta antes/durante/depois do corpo | Registro reservado/consumido não retorna a disponível | 14 condições de falha no mesmo relatório |
| KD-03 | Partição entre KMEs seguida de reinício | Pendência durável reconciliada; novas entregas bloqueadas enquanto incertas | [37 cenários de chave](orion_q_lab_keys_20261007.md) |
| KD-04 | Wrapping incorreto ou erro local inesperado | Inicialização rejeitada ou handler fechado; sem exposição/reuso | `keys/tests-final-v5.xml` |
| OB-01 | Mesmos registros observacionais em duas execuções | Mesmos sifting, erros, leak, verificação e bytes de teste | `keys/validation-final-v3/manifest.json` |
| OB-02 | Timestamps sem bases/bits ou clock físico sem referência | Protocolo não inventa informação; rejeição | Contratos observacionais |
| OB-03 | Perda elevada, ruído, amostra insuficiente ou dessincronização | Taxa/orçamento diminuem ou aborto explícito, sem material para KME | Nove regimes/sementes |
| CP-01 | Produtor conhecido abaixo/acima da capacidade | Conservação nominal e perdas contabilizadas sob saturação | Captura contínua, testes de aquisição |
| CP-02 | Cancelar e matar escritor durante captura | Drenagem/finalização; blocos confirmados recuperados sem repetição | CaptureJournal e teste com processo real |
| EP-01 | Obter OMM ISS público | Identidade/época/hash validados e 181 posições pelo backend | `public_satellite/report.json` |
| EP-02 | Janela de 90 min sem elevação ≥10° no Rio | Zero visibilidade geométrica; nenhuma conexão fabricada | `predicted_pass_samples.json` |
| EP-03 | Erro HTTP, cache expirado, OMM inválido ou cobertura esgotada | Indisponibilidade/rejeição; sem órbita fictícia | `tests/test_public_satellite_ephemeris.py` |
| ST-01 | Harness original usando qWait | Três timeouts reproduzidos em 40 s | `startup/investigation/comparison.csv` |
| ST-02 | Mesmo harness usando event loop | Quatro execuções aprovadas; build 1,92–3,98 s | `startup/investigation/verification-summary.json` |

Essas tabelas mostram resultados de cenários e não somam execuções repetidas como novos testes. A bateria integrada de regressão registrou **264 testes aprovados** (`regression_v2.xml`). Após os últimos reforços de wrapping/handler, a bateria de chaves registrou **49/49** (`keys/tests-final-v5.xml`). Aquisição registrou **85/85** (`swabian/validation_v4.xml`), recuperação **29/29** (`recovery/verification-final.xml`) e harness Qt **3/3**; essas baterias se sobrepõem à regressão. O aceite de fonte `acceptance_source_v2/report.json` passou nas **12 condições** do pipeline. Evidências finais da distribuição e da instalação são adicionadas abaixo após a execução efetiva.

A última rodada das áreas alteradas registrou **79/79** (`root-final-v11.xml`), incluindo ausência individual de NumPy, SciPy e cryptography. O índice `source-validation-index.json` confronta os JUnits aprovados com a coleta do corpus atual: **316 casos únicos aprovados, nenhum sem evidência**, excluindo dois nomes de testes aposentados pela parametrização. Esse é o corpus selecionado para a mudança; não é a suíte inteira do repositório. `source-validation-cases.csv` liga cada caso ao seu JUnit.

## Formulário e instalação reproduzível

O formulário foi inspecionado em 570×620, 640×480 e 440×360. Rolagem e Save/Cancel permanecem acessíveis. A revisão aparece no formulário; canais usam os identificadores reais da configuração. Triggers têm limite estrito calculado com Decimal. Valores válidos que excedem a precisão/faixa do editor são apresentados integralmente como somente leitura; salvar conserva os originais, inclusive seed de 64 bits, duração de 100 ns, janela de 2 milhões de ps e probabilidades com mais casas decimais. Para editar esses valores avançados usa-se a API validada. Replay apresenta os mesmos originais sem coerção. Evidências: `ui_final/advanced_after/summary.json`, capturas e tabela `ui_final/cases.csv`. O erro intermediário de clamp foi reproduzido e preservado em `emulated_clamp_before.json`.

A instalação adicional criou venv novo com `--system-site-packages`, instalou o wheel final, cryptography 46.0.7 e SciPy 1.18.1 localmente e executou módulo/entrypoint fora do checkout, sem `PYTHONPATH`. NumPy 2.3.5 e cffi 2.1.1 foram herdados; não é um Windows limpo nem validação de todo o desktop nessa instalação. Antes de provisionar SciPy, o preflight instalado recusou QKD e não criou workspace/SQLite/recibo. Depois, o QKD passou em 12 condições de protocolo/entrega e 14 de falha com quatro processos. Os 538 arquivos instalados foram conferidos por hash com a fonte final. Wheel SHA-256: `04584b0d02ed14706d7f85cd356bf66deb542b679358b3131a930535d320217b`.

O lançador `tools/run_local_orion.ps1` executou a bancada fora do checkout e restaurou `PYTHONPATH` ao sair. O dataset Bristol externo não acompanha o wheel e sua ausência é identificada; a aquisição emulada não é rotulada como replay experimental. Versão de distribuição é 5.47.0 por `importlib.metadata`; o antigo `quantum_autopilot.__version__` 0.18.0 foi preservado. Consulte `clean_install/final-r2/verification-summary.json` e o [relatório detalhado](orion_lab_console_startup_20261007.md).

O extra `lab` agora admite cryptography até a série 50. A bateria principal e o executável usam **46.0.7**, com NumPy **2.5.2**. Uma segunda instalação do mesmo wheel, também fora do checkout, preservou cryptography **50.0.1** herdada do runtime base, com NumPy **2.3.5** e SciPy **1.18.1**; aprovou as 12 verificações QKD e as 14 de falhas em quatro processos, em 33,641 s. Os 538 arquivos instalados novamente correspondem à fonte final por hash. Evidência: `clean_install/final-crypto50/verification-summary.json`. O [changelog oficial PyCA](https://cryptography.io/en/stable/changelog/) foi consultado. A ampliação de `<47` para `<51` permite essa versão efetivamente testada, sem declarar validação de todas as versões da faixa. SciPy já era dependência do projeto, mas faltava no cálculo de disponibilidade; a reprodução instalada motivou a correção do gate.

## Escopo científico e material de teste

No ensaio BB84 padrão, 80 mil pulsos produziram 28.209 detecções, 14.058 bits sifted e amostra de 3.514 bits com 36 erros. QBER observado 1,0245%, limite de fase condicional 8,0221%, vazamento contado 1.648 bits e orçamento 2.467 bits produziram nove blocos de teste de 256 bits. Esses números são de uma semente identificada, não capacidade universal ou medição física. `certified_secret_bits=0`. O orçamento inclui as hipóteses declaradas do modelo e não certifica uma implementação física.

Reservas/tombstones impedem reatribuição dos registros nos ledgers exercitados. Sementes repetidas entre ensaios reproduzem os mesmos bytes públicos: não há promessa de unicidade criptográfica entre sessões nem autorização para usar esse material em produção. Observações brutas de reprodução contêm bits de entrada; não constituem uma política de armazenamento seguro de dados físicos.

## Fontes e contratos

- [Contratos de aquisição e instrumentos](../product/orion_lab_acquisition_contracts.md); [API Swabian oficial](https://www.swabianinstruments.com/static/documentation/TimeTagger/api/TimeTaggerLibrary.html).
- [Observações, QKD e entrega persistente](../product/orion_q_lab_key_delivery.md); [ETSI GS QKD 014](https://www.etsi.org/deliver/etsi_gs/QKD/001_099/014/01.01.01_60/gs_qkd014v010101p.pdf); [Tomamichel/Leverrier, Eq. 76](https://arxiv.org/pdf/1506.08458).
- [Recuperação e concorrência](orion_lab_recovery_20261007.md); [diagnóstico Qt](orion_lab_console_startup_20261007.md).
- [OMM, integração e limitações](../product/orion_q_public_ephemeris.md); [formatos CelesTrak](https://celestrak.org/NORAD/documentation/gp-data-formats.php), [política de consulta](https://celestrak.org/usage-policy.php), [SatQuMA público v1.1.0](https://zenodo.org/records/8101679).

Consultas e downloads foram somente leitura. A integração pública validada cobre dinâmica orbital, sem acesso autorizado a serviços de engenharia/payload. Espectro, eficiência, jitter, níveis elétricos, impedância, sincronismo, calibração e caracterização de entrelaçamento continuam exigindo medições físicas. Drivers de unidades proprietárias continuam exigindo manuais específicos e verificação da resposta da unidade.

## Distribuição Windows final e aceite

Entrega final: `release/windows/5.47.0-native-20261007-133743-docs/`. O build original `...-125553` terminou com código zero, usando a fonte final estável. A conferência final corrigiu a descrição das versões de cryptography na documentação. A distribuição foi repacotada em diretório novo: somente `docs/product/orion_q_laboratory.md` e `SHA256SUMS.txt` diferem, comprovado por hash de todos os arquivos. Os binários permanecem idênticos aos que passaram pelo aceite completo. `documentation-repack.json` conserva a origem e os relatórios anteriores; o próprio executável verificou o novo manifesto com `--acceptance-test --integrity-only`. Esse comando não repete nem declara novo aceite funcional. O manifesto separado `outputs/lab_hardening_20261007/delivery-evidence.json` vincula o aceite funcional do mesmo executável, a nova integridade, fontes, CRC do ZIP e preservação das distribuições anteriores.

| Verificação executada pelo pacote | Resultado |
| --- | --- |
| Console, telas e contratos existentes | 39 condições aprovadas em `self-test.json` |
| Bancada completa, captura, replay e recibos após reinício | 12/12 em `laboratory-validation/report.json`; `frozen=true`, campanha completa e hash do `.exe` |
| KMEs/aplicações no executável | Quatro processos, dois bancos e mTLS; 14 verificações de falha de entrega aprovadas |
| Pipeline satelital/OGS | 21 condições aprovadas; sem declaração de payload físico validado |
| Campanha global completa | Aprovada, 33 condições, sem cenários omitidos ou pulados |
| Modelos opcionais | Diagnóstico sem pacotes externos aprovado; modelos descarregados, sem ativação de política física |
| Campanhas temporais e roadmap | Diagnósticos do mesmo executável aprovados |
| Stress | 20 rodadas aprovadas; repetido pelo aceite da distribuição |
| Abertura normal e layouts | Seis cenários de serviço/demonstração em escalas 100%, 125%, 150% e 200% aprovados |
| Aceite funcional dos binários | `automatic_passed=true` no build original, self-test/stress aprovados; binários idênticos no repacotamento; inspeção manual em outro computador permanece pendente |
| Integridade da documentação corrigida | Novo manifesto aprovado pelo executável, `integrity_only=true`; relatório separado em `documentation-integrity/acceptance-report.json` |
| ZIP | CRC aprovado, 3.948 entradas, 298.981.422 bytes |
| Preservação | `.exe` e ZIP aprovados da versão 5.46 mantêm seus hashes originais |

Executável SHA-256: `ca74e74f359e6237fee6f513d486814997c3a59a3e621f35905b773cb78fb575`.

ZIP SHA-256: `f92d4570401174a6d7fc11d7f48c451cfce0b87bc31c6449a7f63709a6aceacc`.

Extraia a pasta completa, mantendo `_internal` ao lado de `ORION-Q.exe`. Para reproduzir a bancada, use `ORION-Q.exe --lab-test C:\temp\orion-lab-new`. Não use os diretórios de evidência existentes como destino novo.

Nos quatro cenários de serviço, a primeira leitura apareceu 6,52–6,89 s após mostrar a janela; heartbeat p95 ≈32 ms e máximos 250–312 ms. Nos dois cenários institucionais, primeira leitura ≈11,58–11,64 s, p95 ≈188–234 ms e picos ≈1,53–1,55 s. Os testes não encontraram travamento permanente durante os redimensionamentos programáticos, mas ainda há pausas breves na demonstração. Heartbeat mede a resposta do event loop, não FPS ou desempenho em outro monitor. Arraste físico de bordas e validação em outro computador continuam sem comprovação nesta rodada.

As versões intermediárias `5.47.0-native-20261007-123955` e `...-124837` foram interrompidas para corrigir, respectivamente, a preservação de valores avançados no formulário e o preflight SciPy. Permanecem preservadas com `DO-NOT-DISTRIBUTE.txt`. O pacote `...-125553` também foi preservado integralmente como origem do repacotamento, com a observação externa `SUPERSEDED-DOCUMENTATION.txt`. Use a entrega `...-133743-docs`, que inclui as correções e a documentação conferida.
