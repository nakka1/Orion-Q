# OrionQ — correções híbridas e limites de validação

Atualização: **10/10/2026**. Foram implementadas correções computacionais para o avanço óptico e o jitter, um pipeline SNS-TF com amostra finita e um adaptador separado para relay orbital com memórias. A [campanha integrada run3](../results/orionq_hybrid_remediation_20261010.md) aprovou 75 critérios nas três sementes, com status **`conditional_models_verified`**, 19 entregas orbitais e zero bits secretos certificados. Esta documentação distingue os contratos implementados de qualificação física e segurança operacional.

O [relatório histórico de qualificação híbrida](../results/orionq_hybrid_qualification_20261010.md), suas referências congeladas e sua classificação anterior permanecem preservados. Uma alteração do backend exige uma nova campanha e novos hashes; não transforma retroativamente um resultado histórico em aprovação.

| Pendência | Correção implementada | Alcance da evidência |
|---|---|---|
| PAA terrestre e jitter de apontamento | Referencial inercial correto para avanço óptico; convolução gaussiana 2D e integração da abertura | Comparação numérica com oráculos e sequências sintéticas; sem calibração de missão |
| SNS-TF finito | Observações conjuntas, limites estatísticos, reconciliação, verificação e amplificação reais; admissão após recibos clássicos | Processamento condicional e replay no runtime; zero bits secretos certificados |
| Composição orbital coerente | `OrbitalRelayAdapter`, captura anunciada por mecanismo auxiliar, memórias, BSM e quadros lógicos | Recursos Werner simulados e fluxo causal; hardware orbital hipotético |

## 1. Avanço óptico e orçamento de apontamento

A [documentação da correção óptica espacial](../results/orionq_space_optical_remediation_20261010.md) contém equações, antes/depois, parâmetros e recibos. O backend restaura a velocidade inercial relativa a partir de posição e velocidade ECEF antes de calcular o PAA. Tracking mecânico e Doppler continuam usando suas grandezas próprias. Um GEO pode ter tracking terrestre nulo e PAA não nulo na convenção adotada.

A convenção do modelo é `θ_PA = 2 v_transversal,inercial / c`: do beacon recebido em posição retardada para a mira de emissão futura. É uma aproximação cinemática de primeira ordem, não uma solução iterativa de trajetória retardada nem uma calibração APT. A importância operacional do avanço óptico é fundamentada em [Han et al., Optics Express 26, 17044–17055, 2018; depósito arXiv de 2019](https://arxiv.org/abs/1912.06317).

Ground e ISL compartilham `gaussian_aperture_budget`: feixe gaussiano paraxial, jitter estacionário isotrópico por eixo e offset determinístico. A abertura finita é integrada; sigma radial RMS é convertido explicitamente para sigma por eixo. `legacy_offset_surrogate` continua identificado como aproximação histórica para reprodução. O modelo óptico é comparado ao apêndice A.1 de [Wallnöfer et al., 2022, versão arXiv v2](https://arxiv.org/html/2110.15806v2), sem alegar reprodução de todo o simulador dos autores.

A evidência de preparação corrigiu os sete desvios anteriores: três casos PAA e quatro regimes de jitter. Os detalhes e as contagens de testes ficam no relatório vinculado; suas suítes se sobrepõem e não devem ser somadas. Permanecem hipóteses reduzidas para atmosfera, acoplamento, FOV e offsets escalares, sem atitude de missão, servo APT, exclusão solar ou espectro medido.

## 2. SNS-TF finito a partir de observações

`TwinFieldAdapter` admite `tf_estimation_mode=finite_sns_v2` com esquema `orion.tf-sns.v2`. A fonte fornece contagens conjuntas `S00`, `S01`, `S10`, `S02`, `S20`, fatias de fase, eventos Z com preparação/análise e relógio, escolhas de fonte e origem dos parâmetros. Ela não fornece uma chave pronta. O modo reduzido anterior permanece identificado como `reduced_asymptotic`; seu payload não atende ao contrato finito.

O estimador implementa as equações 15, 16 e 26, inversões e limites de Chernoff e a penalidade numérica da equação 5 de [Jiang et al., 2019, arXiv:1904.00192v3](https://arxiv.org/html/1904.00192v3). A implementação de produção usa resolução Brent; o oráculo independente congelado usa outro método numérico. Não se substitui a estimação de fase Z por um agregado X sem a prova correspondente.

O fluxo efetivo é:

1. **Aquisição e persistência privada:** origem, sessão, canais, sequência e observação são vinculados ao comando; o bloco comprimido recebe hash e referência persistente.
2. **Validação e sincronização:** inteiros e contagens limitados, IDs ordenados, timestamps, bases, preparações, coincidência dos erros declarados e tolerância de relógio são verificados antes das divulgações.
3. **Anúncio clássico e sifting:** Charlie anuncia IDs de pulsos e detectores. As escolhas Z de enviar/não enviar e suas fases permanecem privadas.
4. **Estimação:** estratos e fatias observadas geram limites de rendimento de fóton único, quantidade não marcada, erro de fase e orçamento finito.
5. **Reconciliação e verificação:** permutações e paridades corrigem as strings efetivas; o vazamento observado é contabilizado. Hash universal verifica a concordância; falha aborta.
6. **Amplificação:** sementes públicas alimentam Toeplitz nos dois lados. O comprimento executado decorre do orçamento e da concordância, não de um resultado inserido na interface.
7. **Admissão e apresentação:** o runtime transmite e recebe cada divulgação pelo canal clássico associado, com tempos causais e limites. A proposta calculada localmente permanece privada até o último ACK; só então métricas e contadores são admitidos.

Os recibos públicos registram identidade da mensagem, estágio, hash, transmissão, recepção e associação dos canais. Não carregam as preparações Z privadas nem material final de chave. A interface consulta referências e métricas resumidas; o replay por arquivo ou fila utiliza o mesmo contrato de aquisição e processamento. Reproduzir uma captura sintética marcada como tal não equivale a validar uma medição física.

Este estimador exige **preparações de fonte simétricas e confiáveis**; configurações assimétricas são rejeitadas até existir suporte científico correspondente. Perdas diferentes nos braços não equivalem, por si só, a preparações diferentes. Amostras pequenas, orçamento não positivo, falha de verificação, perda de canal ou deadline impedem a entrega. Blocos acima da capacidade explícita produzem `tf_backpressure`, sem truncamento silencioso.

As regressões de preparação [backend e runtime](../../outputs/hybrid_preparation_20261010/sns-combined-tests1.xml) aprovaram 44 testes, incluindo três sementes, comparação com o oráculo, replay por arquivo/fila, ausência de métricas antecipadas e falhas de canais. Os [números do transporte causal](../../outputs/hybrid_preparation_20261010/sns_runtime_evidence2.json) e o [handoff técnico](../../outputs/hybrid_preparation_20261010/sns_finite_backend_handoff.md) detalham esse escopo. São evidências de preparação, não resultados da campanha integrada futura.

`conditional_key_estimate_bits` e `privacy_amplification_executed_bits` são resultados condicionais de demonstração. **`certified_secret_bits=0`**, `key_material_exported=False` e `physical_action_performed=False`. O material calculado é descartado. A soma dos intervalos estatísticos implementados não certifica, sozinha, a composição completa do orçamento de segurança do artigo, a autenticação, as fontes ou a implementação física.

## 3. Relay orbital com memórias e quadros lógicos

O driver `OrbitalRelayAdapter`, registrado como `orbital_relay`, exige `quantum_memory` e `entanglement_swapping`, terminais ópticos compatíveis, tracking, fonte orbital e canais clássicos/quânticos separados. Seu esquema é `orion.orbital-memory-observation.v1`, sua codificação `polarization_memory_pair` e seu heralding `ancillary_capture_herald`.

Um detector BBM92 ou um TDC mede o fóton recebido de forma destrutiva. Seus registros não podem ser reinterpretados como pares ainda armazenados. O novo adaptador rejeita esses payloads e exige um mecanismo auxiliar de captura anunciada, que preserva o payload quântico não medido em memória. Housekeeping, tracking, anúncios de captura e resultados de Bell circulam pelo plano clássico; não transportam o estado quântico.

O modelo representa um **ensemble Werner**, estimado por amostras XX/YY/ZZ sacrificadas, e um quadro lógico de Bell/Pauli associado a cada recurso. Não é um simulador geral de matrizes densidade nem tomografia experimental. Probes consumidos não se tornam recursos; candidatos sem caracterização são descartados. Decoerência, eficiência de escrita/leitura, cutoff e ocupação alteram os recursos elegíveis.

O runtime reserva os modos livres com `capture_mode_budget`, contabilizando pares armazenados, memórias retidas em operações e reservas pendentes. Candidatos excedentes são rejeitados explicitamente. BSM consome dois pais reservados; perdas de leitura, falha de Bell ou ausência de recibo não liberam cópias reutilizáveis. O recurso de saída só fica elegível após o anúncio clássico e o registro/ACK do quadro lógico. Isso não aplica uma correção Pauli física.

Memórias e sinalização de resultados são componentes essenciais de repetidores, conforme [Briegel et al., 1998](https://doi.org/10.1103/PhysRevLett.81.5932). O cenário orbital usa essas ideias e o estudo de [Wallnöfer et al., 2022](https://arxiv.org/html/2110.15806v2) como fundamentos, sem copiar seus resultados de taxa ou seu modelo completo de ruído. O BSM configurado é `linear_optics_unassisted`, com probabilidade intrínseca 0,5; esse limite pertence à classe de óptica linear com modos auxiliares em vácuo estudada por [Calsamiglia e Lütkenhaus, 2001](https://arxiv.org/abs/quant-ph/0007058), não a toda tecnologia de BSM.

## 4. Matriz das novas conexões

A migração adiciona dois braços SNS finitos e três segmentos orbitais. Todos são **propostos para simulação**, com equipamentos e bancos de recursos dedicados. Distâncias abaixo são referências derivadas do código atual, não cabos levantados em campo ou alcance operacional certificado.

| Enlace | Pontas e tecnologia | Distância de referência | Justificativa e requisitos |
|---|---|---:|---|
| `TECH-RIO-SNS-FINITE-01` | PUC-Rio → `TF-RIO-FINITE-CENTER`; SNS-TF finito em fibra | 6,589168 km geodésicos | Primeiro braço até uma estação central hipotética não confiável; pulsos preparados, interferência, fase, detectores e canal clássico. Base: [Jiang 2019 v3](https://arxiv.org/html/1904.00192v3). |
| `TECH-RIO-SNS-FINITE-02` | UFRJ → `TF-RIO-FINITE-CENTER`; SNS-TF finito em fibra | 6,589119 km geodésicos | Segundo braço da mesma sessão; sincronismo e associação dos dois canais. Comprimento óptico inclui o fator de rota do modelo, não uma fibra existente. Base: [Jiang 2019 v3](https://arxiv.org/html/1904.00192v3). |
| `ORBITAL-MEMORY-01` | `ORBITAL-GEO-W` → `ORBITAL-OGS-CBPF`; captura em memória | 36.662,649 km oblíquos | Um meio do par é retido no GEO e outro enviado à OGS; abertura, atmosfera, elevação, apontamento e anúncio auxiliar. Base: [Wallnöfer 2022 v2](https://arxiv.org/html/2110.15806v2) e [Han 2018](https://arxiv.org/abs/1912.06317). |
| `ORBITAL-MEMORY-02` | `ORBITAL-GEO-W` ↔ `ORBITAL-GEO-E`; ISL com memória e troca | 42.164,169 km oblíquos | LOS, obstrução/limbo terrestre, compatibilidade óptica, captura, modos, BSM e ACK são necessários; dois satélites não criam conexão automaticamente. Base: [Wallnöfer 2022 v2](https://arxiv.org/html/2110.15806v2), [Briegel 1998](https://doi.org/10.1103/PhysRevLett.81.5932) e [Calsamiglia 2001](https://arxiv.org/abs/quant-ph/0007058). |
| `ORBITAL-MEMORY-03` | `ORBITAL-GEO-E` → `ORBITAL-OGS-BSC`; captura em memória | 37.610,460 km oblíquos | Segundo segmento solo-espaço; composição exige recursos nos dois GEO e resultado clássico recebido antes de disponibilizar o par entre OGS. Base: [Wallnöfer 2022 v2](https://arxiv.org/html/2110.15806v2) e [Briegel 1998](https://doi.org/10.1103/PhysRevLett.81.5932). |

Os terminais `ORBITAL-OGS-CBPF` e `ORBITAL-OGS-BSC` usam as referências geográficas cadastradas para CBPF/Urca e BSC/Barcelona. **“Serve a instituição” e co-localização não demonstram fibra ou transferência quântica até seu core.** A demanda implementada termina nas duas OGS hipotéticas. Instalação, acoplamento local, transporte até o núcleo e integrações institucionais exigem configuração e validação adicionais.

| Perfil | Parâmetros de demonstração | Natureza dos valores |
|---|---|---|
| `RIO-SNS-FINITE` | 20 MHz; bloco 1 s; `μZ=0,15`, fraco 0,03, forte 0,15; envio Z 0,005; probabilidade Z 0,5; vácuo/fraco em X 0,3/0,4; largura total de fase π/4; erro intrínseco 0,005; resíduos fase/polarização 0,01 rad; dark 10⁻¹⁰; máximo 50.000 bits observados/bloco; timeout 120 s | Hipótese de preparação simétrica com ponto de operação explícito. Não reproduz uma otimização publicada nem valida uma fonte real. |
| `orbital-memory-cbpf-bsc-v1` | Dois GEO ideais em −60° e 0° de longitude; 850 nm; transmissores espaciais 2 m, receptores espaciais 4 m e OGS 6 m; divergência 0,4 µrad; jitter por eixo 0,05 µrad; residual PAA 0,01 µrad | **Equipamentos hipotéticos de grande abertura**, sem missão ou terminal qualificado. |
| Memória e operação orbital | 16 modos dedicados/dispositivo; tempo de coerência Werner 30 s; cutoff 10 s; escrita/leitura 0,8; tentativas 200 kHz; emissão por tentativa 0,1; bloco 0,5 s; BSM intrínseco 0,5; erro de operação 0,005; jitter temporal 50 ps | A combinação de coerência, banda, eficiência e capacidade é um projeto simulado, **não uma memória medida**. A taxa de tentativas não garante taxa de pares entregues. |
| Admissão orbital | Elevação mínima 10°; profundidade óptica zenital 0,15; margem do limbo 80 km; campo de visão 20 µrad; tracking e compensações habilitados | Políticas e hipóteses do cenário; clima, perda, pointing, recursos e janelas podem bloquear a operação. |

O lote usa geometria e orçamento óptico em época comum. Não propaga uma trajetória retardada para cada emissão do lote. Um bloco que cruza uma borda de janela declarada é descartado conservadoramente. O perfil GEO ideal mantém posição aproximadamente fixa em ECEF; não qualifica stationkeeping real. A animação orbital não altera a disponibilidade calculada.

## 5. Migração e configuração pelo serviço/interface

`upgrade_hybrid_workspace` aplica a atualização aditiva uma única vez ao workspace de demonstração. O evento persistido `hybrid_finite_sns_orbital_memory_v1` impede reaplicação automática. Sessões paralelas anteriores, satélites já cadastrados, enlaces e equipamentos do operador são preservados.

O ramo finito adiciona a estação central e um banco exclusivo por sessão. Não sobrescreve configurações externas ou cadastros revisados pelo operador. O ramo orbital adiciona quatro dispositivos e três enlaces; identificadores ocupados e referências institucionais removidas são registrados como impedimentos, sem recriar ou substituir esses objetos. Cada banco expõe sua capacidade simulada; isso não constitui arbitragem de hardware compartilhado entre sessões físicas.

Na aba **Comunicadores**, tipo físico, função, tecnologias, capacidades e origem continuam separados. O cadastro dinâmico oferece pós-processamento `finite_sns_v2`, intensidades e probabilidades SNS, limites estatísticos e capacidade de bloco; relay orbital exige memória e troca, captura auxiliar, codificação armazenada e terminais separados. Dados orbitais são pertinentes a satélites; uma OGS terrestre usa localização terrestre. O cadastro físico não ativa instrumentos.

Em **Conexões / ativação**, configure os dois canais e seus vínculos e valide as capacidades antes de ativar. O painel de protocolos permite aplicar parâmetros JSON, cenário, semente, habilitação e fonte pela chamada `configure_technology`. As validações pertencem ao backend, não apenas ao formulário. O histórico, referências de aquisição e resultados permanecem disponíveis pelos painéis e pela exportação de recibos.

## 6. Campanha integrada e reprodução

A campanha run3 congelou 592 arquivos e registrou ambiente, configurações, dados brutos, recibos e resultados. Para repetir em saída nova:

```powershell
python tools/run_hybrid_remediation_campaign.py --output outputs/hybrid_remediation_reproduction --workers 3
```

Use um diretório novo e preserve os pilotos e históricos. A aceitação é rastreada pelo [relatório efetivamente produzido](../../outputs/hybrid_remediation_20261010/run3/report.json) e pela [matriz de critérios](../../outputs/hybrid_remediation_20261010/run3/case-matrix.csv). As duas tentativas anteriores não foram aceitas e permanecem registradas.

A validação comprovou os dois braços finitos e os três segmentos orbitais, configuração preservada, recursos limitados, sequência causal, consumo de pais, métricas após recibos e resultados retomados após indisponibilidade. Replay, janelas/cutoff e cadastros foram exercitados pelos testes vinculados. Os detalhes e limites estão no relatório; testes sobrepostos não devem ser somados. O desempenho do backend nesta carga não encerra o gate G05.

## Limites de aceitação e fontes

Não estão demonstrados nesta entrega: hardware óptico/orbital, memórias ou heralding físicos, autenticação operacional do transcript, independência física das pontas, prova completa de segurança composável, qualificação de bancada ou ambiente relevante. Hashes encadeados ajudam a verificar integridade, mas não autenticam autoria sozinhos. A atuação física permanece bloqueada e não há promoção para TRL 5. **Não foi gerado ou qualificado um novo executável por esta documentação.**

Fontes primárias consultadas em 10/10/2026: [Jiang et al., 2019, v3](https://arxiv.org/html/1904.00192v3); [Wallnöfer et al., 2022, v2](https://arxiv.org/html/2110.15806v2), também [Communications Physics 5, 169](https://www.nature.com/articles/s42005-022-00945-9); [Han et al., 2018](https://arxiv.org/abs/1912.06317); [Calsamiglia e Lütkenhaus, 2001](https://arxiv.org/abs/quant-ph/0007058); [Briegel et al., 1998](https://doi.org/10.1103/PhysRevLett.81.5932). Seus princípios fundamentam modelos e restrições; os parâmetros hipotéticos desta demonstração não são apresentados como resultados experimentais dessas publicações.
