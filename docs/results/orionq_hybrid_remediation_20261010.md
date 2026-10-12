# OrionQ — resolução e validação das pendências híbridas

Data: **10/10/2026**. A campanha [run3](../../outputs/hybrid_remediation_20261010/run3/report.json) terminou como **`conditional_models_verified`**: 75 critérios integrados aprovados, 25 por semente, com 592 arquivos congelados e inalterados durante a execução. A consolidação de regressões registra **462 casos distintos aprovados**, sem falhas/erros/skips na última execução de cada caso. Foram implementadas as três pendências computacionais identificadas no [relatório anterior](orionq_hybrid_qualification_20261010.md): PAA/jitter, processamento SNS finito no backend e relay orbital com memórias. A qualificação anterior continua histórica e `partial_verified`; seus resultados não foram reescritos.

Esse resultado valida os modelos e contratos declarados. Não certifica chaves, instrumentos, apontamento de missão, segurança QKD completa ou ambiente físico relevante. **Atuação física continua bloqueada, modelos ML são opcionais e bits secretos certificados permanecem zero.** Nenhum executável novo foi gerado nesta etapa.

## Correções e critérios

| Pendência | Implementação e efeito verificável | Evidência |
|---|---|---|
| Avanço óptico terrestre | Velocidade inercial relativa inclui Ω × Δr; convenção beacon recebido → mira futura, 2v⊥/c. Tracking mecânico e Doppler conservam seus referenciais próprios. | Três casos preservados: erro máximo 3,56 × 10⁻¹⁴ µrad; [comparação óptica](orionq_space_optical_remediation_20261010.md). |
| Jitter e abertura | Ground/ISL usam convolução gaussiana 2D por eixo, integração de abertura e offset; convenções e limite de difração explícitos. | Quatro discrepâncias anteriores corrigidas; quadratura e arquivo dos autores preservado, além de testes de perda, horizonte, FOV e recuperação temporal. |
| SNS-TF finito | `finite_sns_v2` adquire estratos conjuntos, preparação privada, cliques e fase; aplica limites finitos, reconciliação, verificação e Toeplitz. Resultados só são admitidos após todos os recibos públicos. | Dados privados ficam na aquisição persistida; o canal clássico contém os anúncios/paridades e a conferência inclui sequência, payload, hashes e tempos. Replay por arquivo/fila reproduz o processamento nos testes. |
| Relay orbital | Driver próprio de captura anunciada em memória, ensemble Werner, reservas, decoerência, duas BSM e quadros lógicos ACK. BBM92/TDC destrutivo não é reutilizado como memória. | Pares entre duas OGS entregues nas três sementes; consumo sem duplicação, modos retidos e pendentes dentro da capacidade, timeout/cutoff sem restaurar pais. |
| Cadastro e GEO | Migração aditiva uma vez; configurações revisadas e IDs ocupados preservados; cadastros terrestre/GEO/SNS e apresentação dos emissores reais do modelo. | 14 testes de integração: persistência, recuperação, reservas, 11 ícones selecionáveis, 11 órbitas e três trocas com apenas um renderizador. |

A revisão adicional reproduziu e corrigiu quatro defeitos: receptor óptico invertido no primeiro downlink; perda de disponibilidade deixando o bloco SNS pendente; reservas não liberadas em erro após intake; fidelidade congelada durante readout orbital. A leitura orbital agora inclui sua duração no armazenamento. BSM/readout/purificação orbitais rejeitam memória que ultrapasse o cutoff durante a operação. Resultados globais referenciam o bloco do recibo, evitando copiar métricas de um bloco posterior.

## Campanha integrada

O runner [run_hybrid_remediation_campaign.py](../../tools/run_hybrid_remediation_campaign.py) utiliza `NetworkEngine`, inicializador institucional, API validada de configuração, fontes, fila causal, processadores, memória, SQLite, recibos e `LiveState` existentes. Somente a origem é simulada. Há propostas privadas de processamento SNS; elas não são resultados admitidos antes do transporte clássico causal. Esse transporte é emulado localmente, sem alegar autenticação RF ou endpoint externo operacional.

Ambiente registrado: Windows 11, Python 3.12.14, NumPy 2.5.2, SciPy 1.18.1, PySide6 6.11.2, SGP4 2.27; CPU AMD64 Family 25 Model 33, oito núcleos/16 processadores lógicos e 34.280.902.656 bytes de RAM. Três processos independentes, sementes **101/202/303**, 134 passos por processo: nominal 60 s, degradado 8 s, indisponível 6 s, recuperação nominal 60 s no relógio contínuo. Mudança de cenário invalida operações/recursos antigos; não reinicia o tempo. A fonte degradada aplica perdas e jitter; a indisponível não fornece aquisição. Configurações iniciais e finais, versões e topologia estão nos artefatos.

Cada execução usou **76 componentes, 136 enlaces, 38 sessões e 11 satélites**, mantendo os nove satélites e a topologia anteriores. As novas duas sessões utilizam bancos hipotéticos dedicados, sem presumir arbitragem de hardware compartilhado. A migração também soma fontes, terminais e modos do inventário. A matriz dos [cinco novos enlaces, parâmetros e justificativas](../product/orionq_hybrid_remediation.md#4-matriz-das-novas-conexões) diferencia comprimentos de fibra modelados, distância geodésica e alcance oblíquo orbital.

| Semente | SNS nominal: bits condicionais processados | Pares orbitais nominal | SNS recuperação: bits condicionais processados | Pares orbitais recuperação | Eventos persistidos |
|---|---:|---:|---:|---:|---:|
| 101 | 19.427 | 4 | 20.334 | 4 | 330.367 |
| 202 | 19.219 | 2 | 18.906 | 4 | 332.316 |
| 303 | 19.020 | 3 | 18.639 | 2 | 332.943 |

Somam-se **115.545 bits condicionais processados, 19 entregas de pares e 995.626 eventos**. Os bits são contagens dos resultados de amplificação emulada; material é descartado e não ingressa num buffer de chaves operacionais. Os pares terminam em `ORBITAL-OGS-CBPF` e `ORBITAL-OGS-BSC`: a associação às instituições é de serviço/co-localização, **sem fabricar uma fibra quântica até o core institucional**.

Nos três casos, os cenários degradado e indisponível produziram **zero novos bits condicionais e zero entregas orbitais**. Na degradação houve observações e orçamento óptico inferior; indisponibilidade bloqueou admissão de observações. A recuperação retomou resultados sem reutilizar os pais consumidos. Isso demonstra efeito das condições deste cenário, sem garantir taxas ou monotonicidade para toda semente/configuração.

Os 25 critérios por semente abrangem hashes de dados brutos e recibos encadeados, correspondência/ordem dos ACKs, ausência das preparações privadas no transcript, consumo único, ocupação completa (armazenado + retido + reserva), indisponibilidade, positividade nominal/recuperação, degradação óptica, preservação de configuração e publicação dos três enlaces orbitais por seus emissores corretos. [Matriz PASS/FAIL](../../outputs/hybrid_remediation_20261010/run3/case-matrix.csv).

O tempo total foi **1.042,55 s**. O passo completo do backend, sob três processos e regressões concorrentes, teve média de **6.230–6.336 ms** e máximos de **11.192–11.542 ms**. São medições desta carga, não benchmark isolado de interface. **O gate de desempenho G05 não está encerrado por esta campanha**; não se afirma atendimento ao limite anterior de 10 ms, fluidez de GPU ou qualificação de uso prolongado do executável.

## Regressões e rastreabilidade

O [JUnit de integração](../../outputs/hybrid_remediation_preparation_20261010/integration-remediation-final.xml) registra 14 casos aprovados em 205,16 s; [resumo da revisão](../../outputs/hybrid_remediation_preparation_20261010/integration-review-summary.json). Testes gráficos usam Qt offscreen. Mudanças posteriores nesse harness foram somente seis nomes de variáveis locais, verificadas por Ruff/compilação, sem mudar os critérios.

As suítes de backend SNS, relay, óptica e compatibilidade existentes estão registradas em [regressão principal](../../outputs/hybrid_remediation_regression_20261010/run1/regression.xml), [operações globais](../../outputs/hybrid_remediation_regression_20261010/global-run1/regression.xml), [migração repetida](../../outputs/hybrid_remediation_regression_20261010/migration-run2/regression.xml) e [rodada final](../../outputs/hybrid_remediation_regression_20261010/final-run2/regression.xml). A principal teve 415 passagens e uma expectativa antiga de inventário; a global teve 25 passagens e duas expectativas antigas. Contagens 71/131/nove satélites e cinco revisões foram atualizadas para 76/136/11 satélites e seis revisões, preservando atomicidade, edição e idempotência. Os dois casos globais passaram novamente; a rodada final aprovou 73 casos, incluindo a migração restante e toda a família orbital na fonte final. Não se apagaram os JUnits de falha nem se enfraqueceram os critérios científicos.

O [manifesto consolidado](../../outputs/hybrid_remediation_20261010/run3/validation-manifest.json) identifica **462 casos únicos, todos aprovados na última execução**, hashes dos JUnits/harnesses e zero mudanças nos 592 arquivos após a campanha. Casos repetidos não foram somados. Essa evidência combina execuções pertinentes; não afirma que todos os testes do repositório foram executados numa única rodada.

Os cinco controles negativos do [verificador](../../tests/test_hybrid_remediation_campaign.py) rejeitam ACK perdido/divergente, preparação privada no canal, adulteração, consumo duplicado e métrica antecipada. Evidência: [runner-checks1](../../outputs/hybrid_remediation_preparation_20261010/runner-checks1/tests.xml).

Preparações e tentativas sem aceitação foram preservadas: o piloto curto antecede as correções finais; `run1` foi interrompida pela direção física incorreta; `run2` falhou no leitor do formato de transmissões do runner. Seus [registros](../../outputs/hybrid_remediation_20261010/run1/interruption.json) e [causa do runner](../../outputs/hybrid_remediation_20261010/run2/interruption.json) não são evidências aprovadas. A aceitação integrada utiliza exclusivamente `run3` e seu novo congelamento.

## Reprodução e limites restantes

```powershell
.\.audit-winvenv\Scripts\python.exe tools\run_hybrid_remediation_campaign.py --output outputs\hybrid_remediation_reproduction --workers 3
```

Use saída nova/vazia; o runner recusa sobrescrever evidência. Dependências e instruções de cadastro estão nos [contratos](../product/orionq_hybrid_remediation.md). Cada semente exporta topologia, configurações, aquisições, recibos, demandas globais, métricas por fase e amostras por passo; `source-freeze.json` identifica o código e `environment.json` o ambiente. Uma mesma semente deve ser repetida com a mesma configuração, ordem causal e versões.

Continuam externos ou fora desta qualificação: hardware/memória/heralding óptico; fontes e terminais físicos calibrados; sincronismo medido; autenticação operacional dos dois extremos; implementação QKD composável revisada; atitude/servo/exclusão solar de missão; validação independente em outro ambiente e revisão humana identificada. Geometria usa época comum por lote e cinemática de primeira ordem; janelas precisam conter o lote inteiro. GEO, grandes telescópios e coerência de 30 s são hipóteses de projeto. O modelo não cobre estados quânticos gerais nem comunica payload de aplicação. NetSquid permanece sem SDK autorizado; o SeQUeNCe anterior não qualificou nativamente todo o cenário híbrido. Não há atribuição automática de TRL.

Fontes primárias e hipóteses estão documentadas em [Jiang et al. (2019), v3](https://arxiv.org/abs/1904.00192v3), [Wallnöfer et al. (2022), v2](https://arxiv.org/abs/2110.15806v2), [Han et al. (2018)](https://arxiv.org/abs/1912.06317), [Calsamiglia e Lütkenhaus (2001)](https://arxiv.org/abs/quant-ph/0007058) e [Briegel et al. (1998)](https://doi.org/10.1103/PhysRevLett.81.5932). Esses trabalhos fundamentam modelos; resultados do OrionQ são condicionais às hipóteses declaradas.
