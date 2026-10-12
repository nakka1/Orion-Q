# Correção óptica espacial do OrionQ — 10/10/2026

Os sete desvios ópticos identificados na [qualificação híbrida anterior](orionq_hybrid_qualification_20261010.md) foram corrigidos no modelo reduzido do backend: três casos de avanço óptico terrestre e quatro regimes de jitter. A comparação nova mantém os mesmos vetores, locais e parâmetros de referência. O erro máximo de PAA foi 3,56 × 10⁻¹⁴ µrad; os quatro casos de jitter coincidem com o oráculo analítico na precisão numérica observada. Isso qualifica a implementação computacional dessas hipóteses, sem qualificar apontamento de uma missão ou equipamento físico.

A [evidência nova](../../outputs/hybrid_remediation_preparation_20261010/space-remediation-evidence.json) registra o antes/depois, hashes das fontes atuais e seis sequências temporais. Os 74 recursos históricos conferidos — ferramentas, testes de referência, arquivo dos autores e relatório anterior — continuam com seus hashes originais. Os arquivos de produção mudaram intencionalmente e exigem novo congelamento para uma campanha oficial. O relatório histórico e sua classificação `partial_verified` permanecem intactos.

## Referencial do avanço óptico

`orbits.state_geometry` recebe posição e velocidade ECEF do housekeeping validado. A OGS é fixa nesse referencial. Para o vetor relativo Δr, restaura-se a velocidade inercial relativa:

```text
v_rel,inercial = v_rel,ECEF + Ω × Δr
v_radial = v_rel,ECEF · unit(Δr)
v_transversal,inercial = |v_rel,inercial × unit(Δr)|
θ_PA = 2 v_transversal,inercial / c
```

O fator dois declara a convenção do beacon recebido no instante retardado para a mira de emissão que alcançará o destino no instante futuro. Não é o ângulo de deslocamento entre posição atual e chegada, que tem outra convenção. A necessidade de avanço e calibração em comunicação quântica móvel é documentada por [Han et al., Optics Express 26, 17044–17055 (2018), arXiv depositado em 2019](https://arxiv.org/abs/1912.06317). Esta correção é uma aproximação cinemática de primeira ordem; não usa calibração experimental daquele trabalho.

Como Ω × Δr é perpendicular a Δr, distância e velocidade radial/Doppler permanecem iguais. O apontamento mecânico terrestre usa separadamente a taxa angular ECEF. Um GEO equatorial tem tracking mecânico zero, mas precisa de avanço óptico na convenção declarada.

| Caso preservado | Backend anterior (µrad) | Referência inercial (µrad) | Backend corrigido (µrad) | Erro absoluto (µrad) |
|---|---:|---:|---:|---:|
| GEO equatorial | 0 | 17,409103488058 | 17,409103488058 | 0 |
| LEO Salvador | 35,885179162656 | 36,115820401756 | 36,115820401756 | 3,56 × 10⁻¹⁴ |
| OGS polar | 14,889579282926 | 14,780193413503 | 14,780193413503 | 1,78 × 10⁻¹⁵ |

Os dois asserts correntes de `test_satellite_pipeline_v2.py` que esperavam PAA calculado apenas de v_ECEF foram atualizados. No fixture a 500 km com v_ECEF = 7,5 km/s, o valor passa de 50,034614279723 para 50,277853083949 µrad. Os oráculos e resultados históricos não foram atualizados para esconder essa mudança.

## Captura óptica e jitter

Ground e ISL utilizam o mesmo kernel `physics.gaussian_aperture_budget`. Para intensidade gaussiana proporcional a exp(−2r²/w²), meia divergência θ, distância L, comprimento de onda λ e sigma angular independente por eixo:

```text
w0 = λ / (π θ)
w² = w0² + (θ L)²
w_eff² = w² + 4 (L σ)²
η_apertura,centrada = 1 − exp(−2 a² / w_eff²)
```

Com offset médio d = L·δ, a integral de abertura usa a CDF qui-quadrado não central com dois graus de liberdade, argumento 4a²/w_eff² e não centralidade 4d²/w_eff². Essa forma foi conferida por quadratura independente em coordenadas polares com Bessel escalada; consulte a [documentação oficial de `scipy.special.chndtr`](https://docs.scipy.org/doc/scipy/reference/generated/scipy.special.chndtr.html). O offset agrega tracking, residual de PAA e desalinhamento escalar em quadratura, hipótese explícita que não substitui um vetor APT calibrado.

O modelo de feixe, convolução e abertura segue o apêndice A.1 de [Wallnöfer et al., Communications Physics 5, 169 (2022)](https://arxiv.org/abs/2110.15806v2). O [arquivo MIT v1.1.0](https://doi.org/10.5281/zenodo.6384558), commit `e2e3044c9d85bd7e8d618af7d85c60e0580f4590`, permanece intacto. Sua quadratura com jitter a 780 nm e 1.000 km resulta em η = 0,03771179524003175; o kernel resulta em 0,0377117960667213, diferença relativa de aproximadamente 2,19 × 10⁻⁸. O argumento de abertura do transmissor do código dos autores não é usado naquele cálculo; não demonstra caracterização de truncamento óptico.

| Sigma por eixo (µrad) | η anterior | Referência 2D | η corrigida | Erro relativo corrigido |
|---|---:|---:|---:|---:|
| 0,3 | 0,052970456882 | 0,051980058907 | 0,051980058907 | 0 |
| 1 | 0,043272274520 | 0,037711796067 | 0,037711796067 | 0 |
| 3 | 0,007313590582 | 0,011047938458 | 0,011047938458 | 0 |
| 6 | 0,000018128579 | 0,003262494029 | 0,003262494029 | 0 |

Esses quatro casos preservam L = 1.000 km, λ = 780 nm, θ = 3 µrad, raio receptor 0,5 m e demais eficiências unitárias. Doze combinações adicionais de distância (400/1.000/35.000 km) e sigma (0,3/1/3/6 µrad) conferem ground/ISL com o oráculo independente a tolerância relativa de 10⁻¹². Offset médio, perdas extras, nuvens, horizonte, obstrução terrestre e apontamento desabilitado alteram ou bloqueiam a transmissão.

Quando o diâmetro transmissor D é informado, a hipótese de cintura gaussiana w0 ≤ D/2 impõe θ ≥ 2λ/(πD). O primeiro zero Airy, 1,22λ/D, representa outro perfil de abertura e não foi usado como constante universal. Sem diâmetro declarado no modelo ground, não se infere uma abertura transmissora.

## Parâmetros e compatibilidade

| Campo | Significado/unidade | Padrão ou condição |
|---|---|---|
| `pointing_jitter_urad` | Sigma angular conforme convenção, µrad | Finito e não negativo; valores booleanos/texto rejeitados |
| `pointing_jitter_convention` | `gaussian_per_axis`, `gaussian_radial_rms` ou `legacy_offset_surrogate` | Padrão `gaussian_per_axis`; RMS radial é convertido por √2 |
| `pointing_fov_sigma_margin` | Margem N para admissão | Padrão 1; política conservadora magnitude da média + Nσ < FOV, sem representar limite absoluto das caudas gaussianas |
| `point_ahead_compensation` | Aplicar correção de PAA no orçamento óptico | Padrão verdadeiro |
| `point_ahead_calibration_error_urad` | Residual de calibração, µrad | Padrão zero |
| `receiver_alignment_error_urad` | Offset receptor, µrad | Padrão zero |
| `transmitter_diameter_m` | Abertura gaussiana ideal do transmissor, m | Opcional ground; necessário ao piso de divergência |
| `terminal_fov_urad` | Campo de visão para admissão, µrad | Opcional ground; padrão ISL 20 |

`legacy_offset_surrogate` mantém explicitamente o fator histórico exp(−2(error/θ)²) e aproximação de campo distante para replay. Ele não recebe a identificação de convolução 2D. Configurações antigas sem convenção passam a usar a semântica per-axis documentada; resultados antigos devem usar a convenção legada explícita para reprodução. Validação pelo serviço e formulários permanece sob o módulo compartilhado de configuração.

Os resultados expõem `pointing_model`, `pointing_model_legacy`, cintura, raio do feixe, raio alargado pelo jitter, sigma por eixo, offset determinístico, RMS radial, correção/residual de PAA, offset de tracking e política de FOV. `pointing_error_urad` continua como alias de compatibilidade, com semântica declarada como hypot(sigma por eixo, offset médio), distinta do RMS radial 2D.

`isl_optics` e `terminal_compatibility` aceitam `allowed_encodings` apenas por argumento nomeado. O padrão continua `polarization_BBM92`. Um protocolo separado de memória pode admitir `polarization_memory_pair` explicitamente; terminais com codificações diferentes continuam rejeitados. Isso disponibiliza o orçamento óptico ao relay separado sem converter o adaptador BBM92 em armazenamento quântico.

## Recuperação com tempo e órbita evoluindo

Uma fonte única por tecnologia/semente executa aquisição em 0, 10 e 20 segundos. A órbita LEO de referência evolui; a janela declarada permite [0,1) e [20,21). O primeiro e terceiro blocos têm 0,5 s de captura. Entre eles, o protocolo recebe e processa a indisponibilidade. A fixture usa fonte de 30.000 pares/s, abertura receptora 3 m, transmissora 0,5 m, meia divergência 10 µrad, sigma 0,3 µrad, eficiências 0,9 e ruído de fundo/dark zero. São parâmetros de teste explícitos, não especificação de uma missão.

| Tecnologia | Semente | Coincidências em 0 s | Em 10 s | Em 20 s |
|---|---:|---:|---:|---:|
| Satélite–solo | 101 | 272 | 0 | 191 |
| Satélite–solo | 202 | 260 | 0 | 235 |
| Satélite–solo | 303 | 230 | 0 | 228 |
| ISL BBM92 | 101 | 9.303 | 0 | 9.348 |
| ISL BBM92 | 202 | 9.295 | 0 | 9.474 |
| ISL BBM92 | 303 | 9.329 | 0 | 9.158 |

Os timestamps de época avançam de 1.791.590.400 para 1.791.590.410 e 1.791.590.420 s; posições ECEF também mudam. Sequências da fonte são 1/2/3, disponibilidade é verdadeiro/falso/verdadeiro. Os pacotes ground percorrem construção, validação, montagem e processamento a partir de housekeeping e cliques. ISL percorre aquisição e processamento correspondente. Raw/result de cada bloco são preservados nos diretórios de teste referidos na evidência. Todos os blocos têm zero bits secretos certificados. No ensaio ISL BBM92, os pares são medidos destrutivamente e `stored_pairs` permanece zero; a recuperação não comprova relay coerente.

## Verificações e reprodução

Regressão relacionada: **149 testes aprovados em 52,90 s**, com testes satelitais, ISL/GEO, oráculos e campanha anterior. A suíte final de correção, expandida com casos polar/Salvador e entradas inválidas, aprovou **46 testes em 2,00 s**. As contagens se sobrepõem e não devem ser somadas. Os [recibos JUnit](../../outputs/hybrid_remediation_preparation_20261010/space-regression2.xml) e [JUnit final da correção](../../outputs/hybrid_remediation_preparation_20261010/space-tests3.xml) registram as execuções. Ruff passou integralmente para `physics.py` e a suíte nova; `orbits.py` e `isl.py` ainda contêm avisos de estilo compactado pré-existentes fora dos trechos corrigidos.

```powershell
python -m pytest tests/test_space_optical_remediation.py tests/test_satellite_pipeline_v2.py tests/test_satellite_isl_geo.py tests/test_space_validation_reference.py tests/test_space_validation_campaign.py -q
```

Limitações: feixe paraxial gaussiano e jitter estacionário isotrópico por eixo; offsets escalares combinados em quadratura; atmosfera plane-parallel e opção Strehl/SMF reduzida; política de FOV conservadora sem integração das caudas sobre a resposta angular real; ausência de atitude de missão, exclusão solar, espectro medido, truncamento de abertura, servo APT, calibração elétrica/óptica e dados de passagem física. Igualdade com o oráculo e ensaios sintéticos não são qualificação operacional, segurança composable, prova experimental de entrelaçamento ou promoção automática de TRL.
