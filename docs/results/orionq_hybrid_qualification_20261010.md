# Validação híbrida do OrionQ — 10/10/2026

Resultado oficial: **`partial_verified`**. Passaram os critérios computacionais
declarados, mas a qualificação da rede híbrida completa permanece aberta.
A campanha identificou sete divergências ópticas, insuficiência do contrato SNS
finito e ausência de repetição quântica orbital coerente. Nenhum equipamento,
chave operacional, segurança composable ou nível TRL foi qualificado.

## Campanha e rastreabilidade

[Protocolo prospectivo](../product/orionq_hybrid_qualification.md),
[configuração](../../configs/qualification/hybrid_quantum_v1.json),
[relatório oficial](../../outputs/hybrid_quantum_20261010/run1/report.json) e
[congelamento](../../outputs/hybrid_quantum_20261010/run1/freeze.json).
Sementes 101, 202 e 303; quatro processos separados; **1.322 verificações de
família e dez verificações gerais de integridade**. Esses números contam
asserções, não experimentos físicos ou conclusões científicas independentes.

Foram congeladas 700 entradas, incluindo **583 arquivos de produção**, os
oráculos, critérios, testes, guias, relatórios anteriores e recursos dos autores.
Nenhuma entrada mudou durante a execução. As campanhas SeQUeNCe anteriores
continuam preservadas: seus manifestos de 592 e 595 fontes foram conferidos.
O relatório oficial levou 89,89 segundos neste computador, com regressões
executadas simultaneamente; essa duração não é um benchmark de desempenho.

Ambiente: Windows 11 Pro 10.0.26200, Ryzen 7 5700X (8 núcleos/16 threads),
32 GiB de RAM; Python 3.12.14, NumPy 2.5.2, SciPy 1.18.1, SGP4 2.27,
WebSockets 17.2, PySide6 6.11.2 e pytest 9.1.1.
O inventário do computador e os recursos científicos usados recebem cópias
verificadas em `run1/references/`.

| Família | Casos/registros | Verificações | Evidência demonstrada | Pendência |
|---|---:|---:|---|---|
| SNS-TF | 27: nove condições × três sementes | 289 | Decoy reduzido, LP Poisson, Chernoff e estimador finito de referência; aquisição/replay de contagens | Backend não executa o protocolo SNS finito |
| Óptica espacial | 101 registros; inclui 45 tentativas de captura e 24 entradas inválidas | 403 | Código óptico dos autores, geometria/LOS/Doppler e sensibilidade das capturas ao ambiente | Sete divergências de pointing/PAA e ausência de calibração física |
| BBM92 | 42: 14 condições × três sementes | 564 | Matching, sifting/QBER, amostragem finita, transcript de reconciliação e hashes GF(2) | Pressupostos de segurança e dispositivos não qualificados |
| Ingestão OGS | Três execuções completas | 66: 21 critérios + conclusão por semente | Socket autenticado, streams separados, recibos, backend, persistência e publicação | Apenas loopback sintético; missão e hardware ausentes |

A [matriz por caso em CSV](../../outputs/hybrid_quantum_20261010/run1/case-matrix.csv)
e seu [manifesto de derivação](../../outputs/hybrid_quantum_20261010/run1/case-matrix-manifest.json)
contêm 180 linhas: 173 registros das famílias e sete divergências científicas.
São 139 linhas com resultado explícito associado, 34 descritivas e sete `GAP`.
Linhas descritivas preservam métricas/erros sem inventar uma aprovação individual
quando a associação por ID não está disponível nessa exportação. Os relatórios
originais mantêm todas as verificações; a tabela não recomputa física.

As [regressões congeladas](../../outputs/hybrid_quantum_20261010/run1/regression.xml)
aprovaram **326 testes únicos**, sem falhas, erros ou skips, em 265,36 segundos.
Incluem oráculos, gates de integridade, contratos modulares, pipeline satelital,
ISL/GEO, ingestão de missão e persistência de aquisição. Ruff passou para as
novas ferramentas e testes. A conferência posterior às regressões manteve zero
alterações nas 700 entradas. A revisão adicional automatizada corrigiu falhas do
harness antes do congelamento; não substitui revisão humana externa.

Hashes SHA-256: relatório oficial
`029a0dbece13d9ac2507a2e3feed51fcd92e9d1552acb42b5f4ccd327c5fdf13`;
manifesto prospectivo
`0bddca1f87140823c61ae427bbdbd36ebe2911a726401049c4c78174b1254be7`.

## SNS-TF: componente validado e protocolo ainda incompleto

O oráculo novo não importa física do OrionQ. Compara o componente decoy
assintótico com a expressão independente e com um problema linear sobre
misturas Poisson, incluindo cauda adversarial. Fixtures estratificadas permitem
avaliar separadamente o estimador SNS finito publicado; igualdade do decoy
comprimido não significa igualdade de protocolo ou taxa.

| Condição | Resultado do orçamento finito de referência, três sementes |
|---|---|
| Nominal, braços de 100 km | 4.026.072 / 4.055.524 / 4.034.878 bits condicionais |
| Braços de 200 km | 16.958 / 16.363 / 17.743 |
| Perda elevada, poucos pulsos, ruído alto, fase degradada, vácuo ou probabilidade de envio elevada | Zero em todas as sementes |
| Braços de 50/200 km | Aplicação da prova simétrica rejeitada |

Esses valores vêm de **contagens sintéticas agregadas**, não de chaves extraídas
nem de uma execução temporal de cada pulso. O nominal usa 10¹² pulsos,
μ fraca/forte/sinal = 0,1/0,4/0,4, detector 0,8, dark count 10⁻¹⁰/gate,
atenuação 0,2 dB/km, desalinhamento 0,15 e probabilidade de envio 0,01.
Parâmetros de fonte, intensidades e distância são hipóteses fixadas; não são
pontos otimizados ou curvas reproduzidas do artigo.

O [piloto inicial](../../outputs/hybrid_preparation_20261010/sns-pilot1/report.json)
foi preservado: p_envio = 0,08 produziu orçamento nominal zero e seis critérios
reprovados. A preparação fixou p_envio = 0,01 para o nominal e manteve 0,08 como
caso negativo. Sementes não foram selecionadas. Os
[métodos e pressupostos](../../outputs/hybrid_preparation_20261010/sns_validation_methods.md)
registram essa escolha antes da campanha oficial.

O backend atual agrega vacuum/weak/strong e erros X. Faltam os estratos
S01/S10/S02/S20, trials da seleção de fase e detector errado necessários ao limite
de fase Z. Também falta executar reconciliação, verificação e amplificação de
privacidade SNS com transcript distribuído autenticado. Isso impede declarar
`finite_sns_backend_validated`. A referência utiliza as equações e requisitos
de [Jiang et al., 2019](https://arxiv.org/abs/1904.00192v3).

## Óptica espacial e limitações encontradas

Foi executado o código original de
[Wallnöfer et al., 2022](https://arxiv.org/abs/2110.15806v2),
[arquivo MIT v1.1.0](https://doi.org/10.5281/zenodo.6384558), commit
`e2e3044c9d85bd7e8d618af7d85c60e0580f4590`.
Os **67 arquivos extraídos são comparados ao ZIP fixado antes de qualquer
importação**, e novamente verificados contra alterações. O oráculo gaussiano
inclui cintura finita, abertura e convolução do jitter por eixo; o teste com
jitter concordou com a quadratura dos autores dentro de 10⁻⁶ relativo.
Foram comparados abertura/atmosfera sem jitter e o limite Orion de campo
distante sem jitter, além de geometria WGS84, LOS, distância e Doppler.
Não foi reproduzida a simulação completa de memórias/repetidores orbitais do artigo.

| Divergência | OrionQ | Referência | Consequência |
|---|---:|---:|---|
| PAA solo, GEO equatorial | 0 µrad | 17,409103 µrad | A versão atual usa velocidade ECEF sem restaurar rotação terrestre para a convenção beacon→emissão |
| PAA solo, LEO Salvador | 35,885179 µrad | 36,115820 µrad | Diferença de 0,230641 µrad |
| PAA solo, OGS polar | 14,889579 µrad | 14,780193 µrad | Diferença de 0,109386 µrad |
| Jitter 0,3 µrad | η = 0,05297046 | η = 0,05198006 | Orion 1,905% acima |
| Jitter 1 µrad | η = 0,04327227 | η = 0,03771180 | Orion 14,745% acima |
| Jitter 3 µrad | η = 0,00731359 | η = 0,01104794 | Orion 33,801% abaixo |
| Jitter 6 µrad | η = 0,00001813 | η = 0,00326249 | Orion 99,444% abaixo |

A diferença de jitter resulta de hipóteses distintas: o Orion usa um fator de
offset/RMS; a referência integra uma distribuição gaussiana 2D sobre a abertura.
É necessário definir e implementar o modelo pretendido, sem tratar parâmetros
com significados distintos como equivalentes. O PAA exige convenção de emissão,
referencial e calibração explicitados; consulte a
[demonstração experimental de point-ahead](https://arxiv.org/abs/1912.06317).
Nenhum desses cálculos foi admitido para apontamento físico.

Exemplo de atividade observada, semente 101:

| Condição | Satélite–solo: coincidências | ISL: coincidências |
|---|---:|---:|
| Nominal | 70 | 108 |
| Degradada | 0 | 4 |
| Perda adicional 100 dB | 0 | 0 |
| Jitter temporal elevado | 2 | 0 |
| Fonte indisponível | 0; nenhuma aquisição | 0; nenhuma aquisição |
| Nuvem / obstrução terrestre | 0 | 0 |
| Apontamento desativado | — | 0 |
| Recuperação controlada | 84 | 114 |

Visibilidade e rendimento são distintos: perda elevada pode conservar a janela
visível e eliminar coincidências. Nas 45 tentativas espaciais, seis não tiveram
aquisição por indisponibilidade e 39 produziram observações. Recuperações são
comparações na mesma época t=0 com RNG/contador contínuos; não demonstram uma
passagem orbital completa. Capturas percorrem aquisição, replay, assembly OGS
e processador. O ensaio OGS separado cobre o scheduler e o transporte local.

## BBM92 e ingestão

Um grafo bipartido independente verifica coincidências únicas, ambiguidades,
fronteiras de janela, mudança de época, bases e QBER. Toda operação Toeplitz
usada na verificação ou extração condicional é comparada a GF(2) exato, sem FFT.
O transcript verifica paridades públicas, permutações e contabilização do
vazamento. A cauda Serfling é invertida numericamente em implementação separada.
Capturas inválidas com perda de tags, relógio incerto, overflow ou efemérides
expiradas são recusadas antes das métricas.

O exercício de 6 m de abertura, 100 kpares/s e erro óptico 0,001 produziu
455/463/468 bits condicionais no teste de componentes e 532/507/407 no ensaio
de ingestão. Os fixtures têm identidades/configurações aleatórias distintas;
esses valores não são comparações de efeito ou resultados de missão real.
Todos os resultados mantêm **zero bits certificados**.

A referência de [Tomamichel e Leverrier, 2017](https://arxiv.org/abs/1506.08458v3)
exige pressupostos sobre dispositivos, escolhas de bases e autenticação. A
probabilidade de falha da cauda e o smoothing √ε são diferentes; não foi
certificado um orçamento global composable. Testes que rotulam um adaptador
como origem física usam fixture sintética identificada, sem alegar medição real.

O ensaio de ingresso envia housekeeping, tracking e TDC por streams separados,
com identidade e associação aos canais. Os registros TDC são observações
eletrônicas; transmitir esses registros ao backend não transmite estados
quânticos pela rede IP. A autenticação local, os recibos clássicos antes das
métricas, a persistência exportável, a publicação sem tags brutas e o desligamento
do listener passaram nas três sementes. Isso não qualifica TLS/ICD operacional,
RF, OGS, TDC ou payload de uma missão.

## Próximos critérios de aceitação

1. Corrigir e comparar PAA solo com referencial/convenção explícitos; substituir
   ou qualificar a aproximação de jitter segundo distribuição e abertura.
2. Expandir o contrato SNS para estratos e seleção de fase; executar os passos
   finitos com transcript, autenticação e alocação de segurança revisada.
3. Escolher arquitetura orbital: QKD com nós confiáveis ou entrelaçamento com
   memórias/BSM. O ISL atual consome pares em BBM92 e armazena zero pares; não
   funciona como relay quântico coerente entre redes.
4. Executar passagens temporais completas, recursos compartilhados e cenários
   externos de missão/calibração, além de reprodução em outro ambiente e revisão
   humana identificada. NetSquid permanece ausente; não foi usado um substituto
   se passando pelo SDK.

Foram adicionadas ferramentas, critérios, testes e documentação. Nenhum arquivo
de produção foi alterado nesta etapa, nenhum executável foi gerado e os modelos
ML continuam opcionais. A atuação física permanece bloqueada.
