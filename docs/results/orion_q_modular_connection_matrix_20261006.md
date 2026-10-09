# ORION-Q — matriz das conexões modulares

6 de outubro de 2026. São 12 sessões e 36 novos segmentos. As 35 conexões anteriores sem vínculo com a UNIJORGE foram preservadas. Todos os equipamentos e enlaces quânticos acrescentados são propostas de simulação; o cadastro da instituição não comprova sua disponibilidade.

As coordenadas, instalações, precisão e fontes estão em [localizações verificadas](orion_q_modular_locations_20261006.md). A pesquisa, maturidade experimental, parâmetros e limitações estão nos [contratos e modelos](../product/orion_q_modular_quantum_protocols.md).

## Escolha por conexão entre extremos

| Sessão | Extremos | Tecnologias complementares | Escala geográfica (km) | Justificativa e condição |
|---|---|---|---:|---|
| RIO-ENTANGLEMENT | PUC-RIO ↔ UFRJ | troca + memória + purificação | 20.039 | Fibra metropolitana: PUC-Rio → IME militar → CBPF → UFRJ. Memórias, medições de Bell e LOCC explicitamente hipotéticas nos nós; capacidades institucionais não presumidas. |
| RIO-TF | PUC-RIO ↔ UFRJ | SNS-TF | 13.178 | Dois emissores e Charlie central explícito; interferência de fóton único, referências de fase e estimação chamariz. Não é MDI de dois fótons. |
| ONTARIO-TF | IQC ↔ XANADU | SNS-TF | 96.015 | Escala regional entre QNC e referência da sede da Xanadu; Charlie central hipotético. Edifício comercial não é prova de terminal óptico instalado. |
| IBM-IQC-TF | IBM-WATSON ↔ IQC | SNS-TF | 609.513 | Enlace regional longo, exigindo baixa perda e estabilização de fase. O modelo permite orçamento zero; não promete chave segura ou fibra disponível. |
| US-REPEATER-EXPERIMENT | IQMP ↔ IBM-WATSON | troca + memória + purificação | 1149.032 | Escala terrestre longa: seis estações intermediárias explícitas. Perdas, eficiência e decoerência podem impedir entrega. IQMP é infraestrutura em desenvolvimento. |
| CN-BEIJING-JINAN | CN-BEIJING ↔ CN-JINAN | troca + memória + purificação | 366.662 | Macrotrajeto documentado do backbone chinês, que usa relays QKD confiáveis; três repetidores quânticos são overlay experimental hipotético. |
| CN-JINAN-HEFEI | CN-JINAN ↔ CN-HEFEI | troca + memória + purificação | 534.061 | Macrotrajeto documentado com três intermediários hipotéticos; não representa as estações reais nem converte o backbone em repetidores de entrelaçamento. |
| CN-HEFEI-SHANGHAI | CN-HEFEI ↔ CN-SHANGHAI | troca + memória + purificação | 405.054 | Macrotrajeto documentado com overlay de troca, memória e purificação. Sem promessa de entrega na condição nominal. |
| RIO-SATELLITE | CBPF ↔ UFRJ | satélite / Doppler | 11.884 | Uma fonte no satélite LEO hipotético e dois terminais terrestres. Coincidências exigem visibilidade simultânea; perdas e Doppler são calculados. |
| EUROPE-SATELLITE | BSC ↔ MS-QUANTUM | satélite / Doppler | 1769.203 | Fonte comum e terminais hipotéticos em BSC e Lyngby; cobertura simultânea e contagens dependem da janela orbital. |
| US-SATELLITE | GOOGLE-QAI ↔ IQMP | satélite / Doppler | 2925.570 | Fonte comum entre Google/Goleta e IQMP. A distância pode impedir visibilidade simultânea do LEO de referência; indisponibilidade é exibida. |
| ATLANTIC-SATELLITE | UFRJ ↔ BSC | satélite / Doppler | 8513.184 | Caso explícito de limitação UFRJ–BSC: LEO de 500 km não é relay contínuo intercontinental. Permanece indisponível quando a geometria falha. |

## Cada segmento cadastrado

Os números abaixo são distâncias geográficas de referência, não comprimentos de cabos levantados. Na fibra, o fator de rota nominal 1,2 é uma hipótese configurável. Nos enlaces satelitais, o comprimento operacional é a distância oblíqua calculada da órbita a cada aquisição; a referência estática do mapa não substitui esse cálculo. As posições de intermediários são propostas geográficas, sem levantamento de sítio ou garantia de rota terrestre.

| Enlace | Emissor / estação A | Receptor / estação B | Família | Referência geográfica (km) | Comprimento operacional nominal |
|---|---|---|---|---:|---|
| TECH-RIO-ENTANGLEMENT-01 | PUC-RIO | IME-RIO | troca + memória + purificação | 7.357 | 8.828 km (hipótese) |
| TECH-RIO-ENTANGLEMENT-02 | IME-RIO | CBPF | troca + memória + purificação | 0.798 | 0.957 km (hipótese) |
| TECH-RIO-ENTANGLEMENT-03 | CBPF | UFRJ | troca + memória + purificação | 11.884 | 14.261 km (hipótese) |
| TECH-RIO-TF-01 | PUC-RIO | TF-RIO-CENTER | SNS-TF | 6.589 | 7.907 km (hipótese) |
| TECH-RIO-TF-02 | UFRJ | TF-RIO-CENTER | SNS-TF | 6.589 | 7.907 km (hipótese) |
| TECH-ONTARIO-TF-01 | IQC | TF-ONTARIO-CENTER | SNS-TF | 48.043 | 57.652 km (hipótese) |
| TECH-ONTARIO-TF-02 | XANADU | TF-ONTARIO-CENTER | SNS-TF | 47.972 | 57.566 km (hipótese) |
| TECH-IBM-IQC-TF-01 | IBM-WATSON | TF-IBM-IQC-CENTER | SNS-TF | 307.019 | 368.422 km (hipótese) |
| TECH-IBM-IQC-TF-02 | IQC | TF-IBM-IQC-CENTER | SNS-TF | 302.494 | 362.993 km (hipótese) |
| TECH-US-REPEATER-EXPERIMENT-01 | IQMP | R-US-EXPERIMENT-01 | troca + memória + purificação | 163.580 | 196.296 km (hipótese) |
| TECH-US-REPEATER-EXPERIMENT-02 | R-US-EXPERIMENT-01 | R-US-EXPERIMENT-02 | troca + memória + purificação | 163.769 | 196.523 km (hipótese) |
| TECH-US-REPEATER-EXPERIMENT-03 | R-US-EXPERIMENT-02 | R-US-EXPERIMENT-03 | troca + memória + purificação | 163.959 | 196.751 km (hipótese) |
| TECH-US-REPEATER-EXPERIMENT-04 | R-US-EXPERIMENT-03 | R-US-EXPERIMENT-04 | troca + memória + purificação | 164.148 | 196.978 km (hipótese) |
| TECH-US-REPEATER-EXPERIMENT-05 | R-US-EXPERIMENT-04 | R-US-EXPERIMENT-05 | troca + memória + purificação | 164.337 | 197.204 km (hipótese) |
| TECH-US-REPEATER-EXPERIMENT-06 | R-US-EXPERIMENT-05 | R-US-EXPERIMENT-06 | troca + memória + purificação | 164.526 | 197.431 km (hipótese) |
| TECH-US-REPEATER-EXPERIMENT-07 | R-US-EXPERIMENT-06 | IBM-WATSON | troca + memória + purificação | 164.714 | 197.657 km (hipótese) |
| TECH-CN-BEIJING-JINAN-01 | CN-BEIJING | R-CN-BEIJING-JINAN-01 | troca + memória + purificação | 91.638 | 109.966 km (hipótese) |
| TECH-CN-BEIJING-JINAN-02 | R-CN-BEIJING-JINAN-01 | R-CN-BEIJING-JINAN-02 | troca + memória + purificação | 91.656 | 109.988 km (hipótese) |
| TECH-CN-BEIJING-JINAN-03 | R-CN-BEIJING-JINAN-02 | R-CN-BEIJING-JINAN-03 | troca + memória + purificação | 91.675 | 110.009 km (hipótese) |
| TECH-CN-BEIJING-JINAN-04 | R-CN-BEIJING-JINAN-03 | CN-JINAN | troca + memória + purificação | 91.692 | 110.031 km (hipótese) |
| TECH-CN-JINAN-HEFEI-01 | CN-JINAN | R-CN-JINAN-HEFEI-01 | troca + memória + purificação | 133.553 | 160.263 km (hipótese) |
| TECH-CN-JINAN-HEFEI-02 | R-CN-JINAN-HEFEI-01 | R-CN-JINAN-HEFEI-02 | troca + memória + purificação | 133.527 | 160.233 km (hipótese) |
| TECH-CN-JINAN-HEFEI-03 | R-CN-JINAN-HEFEI-02 | R-CN-JINAN-HEFEI-03 | troca + memória + purificação | 133.503 | 160.203 km (hipótese) |
| TECH-CN-JINAN-HEFEI-04 | R-CN-JINAN-HEFEI-03 | CN-HEFEI | troca + memória + purificação | 133.478 | 160.174 km (hipótese) |
| TECH-CN-HEFEI-SHANGHAI-01 | CN-HEFEI | R-CN-HEFEI-SHANGHAI-01 | troca + memória + purificação | 101.024 | 121.229 km (hipótese) |
| TECH-CN-HEFEI-SHANGHAI-02 | R-CN-HEFEI-SHANGHAI-01 | R-CN-HEFEI-SHANGHAI-02 | troca + memória + purificação | 101.184 | 121.421 km (hipótese) |
| TECH-CN-HEFEI-SHANGHAI-03 | R-CN-HEFEI-SHANGHAI-02 | R-CN-HEFEI-SHANGHAI-03 | troca + memória + purificação | 101.344 | 121.612 km (hipótese) |
| TECH-CN-HEFEI-SHANGHAI-04 | R-CN-HEFEI-SHANGHAI-03 | CN-SHANGHAI | troca + memória + purificação | 101.502 | 121.803 km (hipótese) |
| TECH-RIO-SATELLITE-01 | SAT-RIO-SATELLITE | CBPF | satélite / Doppler | 0.000 | calculado da órbita e estação |
| TECH-RIO-SATELLITE-02 | SAT-RIO-SATELLITE | UFRJ | satélite / Doppler | 11.884 | calculado da órbita e estação |
| TECH-EUROPE-SATELLITE-01 | SAT-EUROPE-SATELLITE | BSC | satélite / Doppler | 0.000 | calculado da órbita e estação |
| TECH-EUROPE-SATELLITE-02 | SAT-EUROPE-SATELLITE | MS-QUANTUM | satélite / Doppler | 1769.203 | calculado da órbita e estação |
| TECH-US-SATELLITE-01 | SAT-US-SATELLITE | GOOGLE-QAI | satélite / Doppler | 0.000 | calculado da órbita e estação |
| TECH-US-SATELLITE-02 | SAT-US-SATELLITE | IQMP | satélite / Doppler | 2925.570 | calculado da órbita e estação |
| TECH-ATLANTIC-SATELLITE-01 | SAT-ATLANTIC-SATELLITE | UFRJ | satélite / Doppler | 0.000 | calculado da órbita e estação |
| TECH-ATLANTIC-SATELLITE-02 | SAT-ATLANTIC-SATELLITE | BSC | satélite / Doppler | 8513.184 | calculado da órbita e estação |

Cada segmento possui IDs distintos de canal clássico e canal quântico, ligados por `association_id`. A fonte e ambos os canais aparecem no recibo de aquisição e no snapshot. Os segmentos de TF conduzem os dois emissores ao nó central; os de satélite conduzem a fonte comum aos dois terminais. As mensagens de controle/heralding percorrem o canal clássico modelado. Não há transmissão física de qubits neste software.
