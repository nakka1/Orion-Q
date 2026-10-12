# OrionQ — validação cruzada computacional, 10/10/2026

**Resultado: SeQUeNCe aprovado no escopo reduzido de 22 casos; NetSquid não
demonstrado por ausência do SDK.** A validação com ambos os motores permanece
incompleta (`complete_dual_backend_validation=false`). Não houve mudança nos
modelos científicos, atuação física, admissão de chaves ou atribuição de TRL.

## Escopo e execução independente

A campanha [CROSS-SIM-20261010-v1, revisão 2](../../configs/qualification/cross_simulators_v1.json)
compara a física reduzida e os adaptadores de observações OrionQ com componentes
reais do SeQUeNCe. Não é validação cruzada de aprendizado de máquina por folds.
A integração anterior de roteamento `qnet_network` e suas fórmulas Bell-diagonal
não foram apresentadas como referência independente do runtime modular.

Foram executados três processos SeQUeNCe, com sementes `2026101001`,
`2026101002` e `2026101003`, no commit
`3133c66d80a898c6c3cc9af9eec3fa94fdbe2233`, versão 1.2.0. Seus runners não
importam fórmulas OrionQ. Entradas contêm parâmetros e identificadores, sem
resultados esperados. A principal referência de swapping/BBPSSW é o circuito
QuantumManagerDensity com projetores e resultados de medidas nativos; BDS
upstream é uma conferência secundária.

O ambiente separado usa Python 3.12.14 Windows, NumPy 2.5.3, SciPy 1.18.1,
QuTiP 5.3.1, qutip-qip 0.4.2, gmpy2 2.3.2 e stim 1.16.0. O ambiente
`.audit-winvenv` OrionQ e o checkout `C:\SeQUeNCe` foram preservados. A ausência
de matplotlib gerou um aviso de visualização opcional, sem shim ou substituição
dos cálculos. Tempos dos três processos: 9,306 / 9,299 / 9,300 s; esses tempos
não são benchmark de desempenho do aplicativo.

O congelamento ocorreu em 10/10/2026 às 09:16:04,981 UTC. O inventário contém
592 arquivos: 583 fontes/recursos de produção e os arquivos da nova campanha.
As 583 entradas de produção são idênticas às do congelamento TRL 4 run3.
Os novos runners, testes, configuração e documentação ampliam o inventário de
validação; não alteram retrospectivamente os manifestos anteriores.

| Identidade | SHA-256 |
| --- | --- |
| Protocolo congelado | `055a8ac22b2729138892dfcf535dcf1ae5c8efc5e536b587f284658af3d3a1e6` |
| Inventário de fontes | `9ae5090ead234eb3f0314c315771b1255fdbaf3fcb7cd3e21f6ded84f27a24eb` |
| Relatório integral | `86dce9908401dcec53c86d8c7c435511d2aa534b992560eb1b57aa19f99c0f73` |

O resultado confirma `source_unchanged=true`. O SeQUeNCe registra hashes de
104 arquivos upstream e checkout sem mudanças rastreadas. Hashes verificam
integridade de bytes; não autenticam autoria humana nem calibração experimental.

## Critérios prospectivos

As três sementes executam os mesmos 22 cenários: seis de transmissão, cinco de
memória, quatro de swapping, quatro de purificação e três de atraso clássico.
São 66 execuções de componentes, **26 métricas determinísticas distintas/78
comparações entre repetições** e dez proporções amostrais com 6.144 ensaios cada
(61.440 ensaios de transmissão/aceitação no total). Esses números são distintos
dos testes do avaliador e não devem ser somados como um total científico único.

- Valores determinísticos: erro absoluto máximo `1e-9`.
- Tempo: erro absoluto máximo `2e-12 s`, incluindo quantização ps upstream.
- Proporções: intervalo Clopper–Pearson integralmente contido na margem de equivalência prospectiva.
- Margens: ±0,04; transmissão 100 km usa ±0,008.
- Bonferroni: alpha familiar 0,001, família planejada de 20 proporções, contando ambos os motores mesmo com NetSquid ausente.
- Casos ideais 0/1 exigem adicionalmente contagem exatamente correspondente.

Não se usou ausência de significância para declarar equivalência, nem igualdade
de sementes para exigir trajetórias iguais entre motores. As margens são
critérios computacionais, sem constituir incerteza metrológica de equipamentos.

## Matriz dos 22 casos SeQUeNCe

Todas as linhas foram aprovadas nas três sementes. Valores abaixo são métricas
determinísticas OrionQ; diferenças máximas correspondem à referência nativa.
F é fidelidade em relação ao estado Bell alvo corrigido, P é probabilidade
condicional/aceitação e eta é sobrevivência óptica escalar.

| Caso | Entrada | Métrica OrionQ | Resultado |
| --- | --- | --- | --- |
| `loss_identity` | 1 km, 0 dB/km | eta=1 | Aprovado |
| `loss_10km` | 10 km, 0,2 dB/km | eta=0,630957344 | Aprovado |
| `loss_25km` | 25 km, 0,2 dB/km | eta=0,316227766 | Aprovado |
| `loss_50km` | 50 km, 0,2 dB/km | eta=0,1 | Aprovado |
| `loss_100km` | 100 km, 0,2 dB/km | eta=0,01 | Aprovado |
| `loss_insertion` | 10 km, 0,2 dB/km, 3 dB inserção | eta=0,316227766 | Aprovado |
| `memory_identity` | F=1, t=0, T=1 s | F=1 | Aprovado |
| `memory_short` | F=0,93, t=0,05 s, T=1 s | F=0,896836009 | Aprovado |
| `memory_one_lifetime` | F=0,9, t=1 s, T=1 s | F=0,489121637 | Aprovado |
| `memory_degraded` | F=0,7, t=2 s, T=0,8 s | F=0,286938249 | Aprovado |
| `memory_mixed_limit` | F=0,75, t=10 s, T=1 s | F=0,250022700 | Aprovado |
| `swap_identity` | F1=1, F2=1 | F=1 | Aprovado |
| `swap_equal` | F1=0,9, F2=0,9 | F=0,813333333 | Aprovado |
| `swap_unequal` | F1=0,9, F2=0,75 | F=0,683333333 | Aprovado |
| `swap_degraded` | F1=0,6, F2=0,6 | F=0,413333333 | Aprovado |
| `purify_identity` | F1=1, F2=1 | F=1; P=1 | Aprovado |
| `purify_equal` | F1=0,8, F2=0,8 | F=0,838150289; P=0,768888889 | Aprovado |
| `purify_unequal` | F1=0,92, F2=0,7 | F=0,842013889; P=0,768 | Aprovado |
| `purify_degraded` | F1=0,6, F2=0,6 | F=0,620437956; P=0,608888889 | Aprovado |
| `delay_10km` | Fibra, c/1,468 | 48,967209 microsegundos | Aprovado |
| `delay_50km` | Fibra, c/1,468 | 244,836046 microsegundos | Aprovado |
| `delay_500km` | Fibra, c/1,468 | 2.448,360459 microsegundos | Aprovado |

O maior erro de fidelidade/probabilidade foi **4,4408921×10⁻¹⁶**. O maior erro
de tempo foi **2,4559391×10⁻¹³ s (0,246 ps)**. Isso demonstra consistência dos
modelos equivalentes configurados, sem validar coeficientes de um equipamento.

### Contagens e equivalência estatística

Intervalos são os simultâneos registrados; arredondamento da tabela não altera
o cálculo feito com os valores completos. Cada denominador é 6.144.

| Caso | Eventos observados | Proporção | Intervalo simultâneo | Dentro da margem |
| --- | ---: | ---: | --- | --- |
| `loss_identity` | 6.144 | 1 | [0,998277; 1] | Sim |
| `loss_10km` | 3.885 | 0,632324 | [0,607068; 0,657092] | Sim |
| `loss_25km` | 1.970 | 0,320638 | [0,296755; 0,345183] | Sim |
| `loss_50km` | 630 | 0,102539 | [0,087500; 0,119050] | Sim |
| `loss_100km` | 70 | 0,011393 | [0,006700; 0,017915] | Sim |
| `loss_insertion` | 1.970 | 0,320638 | [0,296755; 0,345183] | Sim |
| `purify_identity` | 6.144 | 1 | [0,998277; 1] | Sim |
| `purify_equal` | 4.718 | 0,767904 | [0,745497; 0,789321] | Sim |
| `purify_unequal` | 4.712 | 0,766927 | [0,744490; 0,788378] | Sim |
| `purify_degraded` | 3.772 | 0,613932 | [0,588468; 0,638976] | Sim |

O intervalo de 100 km fica próximo à borda superior de 0,018; essa margem não
foi ampliada. Os cenários de 25 km e de inserção têm transmissão total igual e
usam os mesmos streams por semente, portanto suas contagens são iguais; não
representam duas amostras físicas independentes. Bonferroni não exige
independência entre hipóteses, mas a campanha não alegou independência física.

## O que foi realmente exercitado

- Canais quânticos SeQUeNCe geram sobrevivência de fótons e eventos de chegada; não são apenas indicadores estáticos.
- Memórias em ambas as extremidades recebem ruído Pauli isotrópico; Tframework=8Tpar/3 é definido antes da medição.
- Circuitos de swapping medem quatro resultados Bell e aplicam correções; a fidelidade final é calculada do estado retido.
- BBPSSW faz CNOT bilateral, medidas, aceitação 00/11 e descarte, usando dois pares por tentativa e retendo no máximo um.
- Comunicação clássica SeQUeNCe tem barreira de não recebimento antes da chegada e timestamp de recepção observado.
- A duração correspondente OrionQ vem de recibos reais de `ProtocolRuntime`, após aquisição, validação, sincronização e processamento em estado isolado.

O fixture clássico substitui comprimentos dos segmentos por distâncias
sintéticas do caso, rota 1, sem jitter/latência de processamento. Coordenadas
e nomes da topologia-base não comprovam essas distâncias como rotas instaladas.
Comparam-se durações de trânsito, preservando as diferentes épocas de envio.

## NetSquid e limites restantes

As três chamadas NetSquid retornaram os 22 casos como `not_demonstrated`, sem
métricas ou eventos científicos. O usuário confirmou não possuir instalação.
Foram criados contrato e runner opcional com componentes nativos, mas seus
resultados/API continuam **não qualificados até execução com SDK autorizado**.
Os testes locais não usam um substituto para aprovar o NetSquid. O
[site oficial](https://netsquid.org/) exige registro/termos e credenciais para
acesso ao pacote/documentação. Não foram obtidas credenciais ou aceitos termos
em nome do usuário.

Esta campanha também não demonstra:

- Troca completa de `Acquisition` produzida por simuladores externos pelo runtime OrionQ com todos os recursos, filas, retries e persistência.
- Rede global, roteamento completo, gerenciamento multiusuário ou capacidade sob carga.
- Modelos de ruído de portas física e experimentalmente equivalentes; a purificação Orion usa ruído após seleção, enquanto o modelo de portas upstream altera a aceitação.
- Estados gerais não Werner, quadros Pauli iniciais arbitrários, tempos assimétricos por dispositivo e uma cadeia de purificações sem twirling explícito.
- Física óptica/security proof TF-QKD, modelo satelital calibrado, RF/OGS/TDC ou hardware real.
- Desempenho da interface, novo executável, benefício ML ou atribuição automática de TRL 4/5.

## Preparação, interrupção e correções

Os testes locais finais tiveram **112 aprovações**, incluindo contrato,
fixtures adversariais do avaliador e subprocesso real SeQUeNCe. Ruff aprovou
os seis arquivos Python. Esses testes não são 112 experimentos científicos.

As tentativas de preparação foram preservadas: o primeiro teste combinado teve
105 aprovações e duas falhas de fixture, por usar probabilidade 0,8 em um caso
cuja margem estreita correspondia a 0,01; a fixture foi corrigida, mantendo
critérios. A ferramenta auxiliar de ramos também foi ajustada para evitar chamar
`swap_qubits` upstream no caso identidade, conforme o próprio QuantumManager;
o framework permaneceu inalterado. Foi corrigida a derivação das latências para
usar timestamps realmente recebidos, com testes de causalidade conservados.

[run1](../../outputs/cross_simulators_20261010/run1/incomplete.json) foi interrompida
após a primeira semente pelo parser de domínio, que rejeitou fidelidade
`1,0000000000000002` de circuito ideal. Seu resultado é incompleto. A revisão 2
aceita no domínio somente roundoff de até `1e-12`, preserva os valores raw e
repete tudo em novo diretório. Tolerâncias científicas, sementes, ensaios e
margens estatísticas não foram modificados. A nova campanha tem SeQUeNCe pass
e NetSquid não demonstrado; não transforma a ausência do SDK em aprovação.

## Evidências e reprodução

- [Relatório integral run2](../../outputs/cross_simulators_20261010/run2/report.json).
- [Congelamento](../../outputs/cross_simulators_20261010/run2/freeze.json) e [resumo derivado](../../outputs/cross_simulators_20261010/final-summary.json).
- [SeQUeNCe semente 1](../../outputs/cross_simulators_20261010/run2/sequence-2026101001.json), [2](../../outputs/cross_simulators_20261010/run2/sequence-2026101002.json), [3](../../outputs/cross_simulators_20261010/run2/sequence-2026101003.json).
- [Ausência NetSquid registrada](../../outputs/cross_simulators_20261010/run2/netsquid-2026101001.json).
- [Testes finais JUnit](../../outputs/cross_simulator_preparation_20261010/combined-tests3.xml).
- [Contrato, mapeamentos e comandos de nova execução](../product/orionq_cross_simulators.md).

Cada execução tem entradas, logs, comando, código de saída, tempo e hashes
preservados. Usar diretório novo para repetir; não reutilizar ou apagar tentativas.
Os códigos dos seis processos backend foram 0. A CLI de campanha prevê código
2 para validação dual incompleta; o shell externo retornou código não zero.
As causas e estados por motor estão no relatório, separados de falha científica.

As fontes primárias são [Wu et al., SeQUeNCe](https://arxiv.org/abs/2009.12000),
[Coopmans et al., NetSquid](https://arxiv.org/abs/2010.12535),
[Bennett et al., purificação](https://arxiv.org/abs/quant-ph/9511027),
[Zang et al., 2023](https://arxiv.org/abs/2305.14573) e a documentação/código
oficiais listados no protocolo, conferidos em 10/10/2026. A avaliação interna de
consistência por agentes não substitui revisão humana independente de maturidade.
