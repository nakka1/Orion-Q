# Rede global de demonstração — ORION-Q 5.46

O cenário institucional tem 71 pontos, 71 comunicadores, 131 enlaces e 36
sessões de protocolo. As 20 instituições originam e recebem demandas. As
instalações conservam capacidades combináveis: endpoint, encaminhamento
clássico, memória, BSM e purificação são funções distintas, admitidas somente
pelos contratos correspondentes. CBPF também conserva a identificação lógica
de Core. Localizações e infraestrutura documentada permanecem separadas dos
terminais, fibras e satélites propostos para esta simulação.

## Operação e inspeção

Abra a demonstração institucional e use **GEO > Globo**. Os satélites e suas
órbitas permanecem representados simultaneamente; os marcadores terrestres
diminuem com o zoom. Clique em um satélite ou use o seletor de foco para abrir
engenharia, estações, enlaces ISL, demandas e ciência recebida. Objetos ocultos
pela Terra continuam acessíveis pelo seletor. **Mapa** restaura a apresentação
terrestre. Botão e scroll preservam seleção, telemetria e painéis, com apenas
um renderizador ativo.

**PROTOCOLS** contém as tabelas **Demandas** e **Componentes**, além da captura
estável de evidências. O Inspector e as janelas dos pontos mostram atividade e
impedimentos recebidos do backend. Os estados são:

| Estado | Significado |
|---|---|
| `not_configured` | Faltam dispositivo, associação, localização ou enlaces. |
| `available_no_traffic` | Há aquisição atual admissível; a demanda aguarda sua próxima execução. |
| `waiting_resources` | Aguarda recursos, aquisição ou resultado de protocolo. |
| `establishing` | Iniciou descoberta/admissão e estabelecimento. |
| `transmitting` | Uma operação ou mensagem está em andamento. |
| `failed_attempt` | Tentativa falhou; há causa, prazo e política de nova tentativa. |
| `unavailable` | Condição física, desativação, configuração incompatível ou executor ausente impede a operação. |

O estado genérico de saúde do ponto resume seus enlaces; a coluna de operação
descreve a demanda específica. Disponibilidade, detecção de pares, geração de
uma chave e transporte de aplicação não são intercambiáveis.

## Rotas e capacidades

As sondas de controle são serializadas, enviadas pelo canal clássico associado
a cada salto, validadas e recebidas antes do próximo encaminhamento. Registram
origem, sequência, associação, hash do envelope, emissão, recebimento, latência,
perda e timeout. Não instalam chaves nem transportam dados de aplicações.

Os serviços quânticos usam o `ProtocolRuntime`: fonte → aquisição bruta →
validação/sincronização → protocolo → recursos e sinalização clássica → recibo
→ métricas → persistência → snapshot da interface. Fontes simuladas e de
arquivos utilizam os mesmos contratos; um dispositivo físico sem adaptador
validado permanece impedido. A troca de entrelaçamento terrestre consome pares,
usa memórias finitas, espera heralding, executa BSM e, quando aplicável,
purificação. Recibos de recursos não são reutilizados para satisfazer demandas
simultâneas.

O roteamento antigo BB84 continua excluindo os enlaces das tecnologias
opcionais: uma linha satelital não entra no grafo antigo como fibra ou repetidor.
O novo gerenciador descobre as rotas clássicas e vincula os serviços locais aos
recibos reais de seus drivers. As fibras diretas longas e os casos LEO
indisponíveis permanecem como comparações físicas.

## Matriz conexão × tecnologia

Todas as conexões desta tabela são propostas simuladas. A matriz exportada
contém os 202 componentes/enlaces, os identificadores públicos dos cadastros,
capacidades, canais, sessões, demandas, causas e referências de recibos.

| Conexão / grupo | Tecnologia e componentes | Justificativa e limite |
|---|---|---|
| CIMATEC–CIQUANTA, CIMATEC–CBPF, CIMATEC–USP, CBPF–USP | Fibras segmentadas existentes, memórias, BSM, BBPSSW | Reutiliza os pontos intermediários já cadastrados; nenhum segmento excede 150 km. O sucesso depende de perdas, memória, operações e sinalização. |
| USP–IME-USP, USP–UNICAMP, IME-USP–UNICAMP | Distribuição de pares, memória e purificação | Estabelece serviços de extremos pelo caminho terrestre configurado. |
| IME-RIO–CBPF | Serviço dedicado de entrelaçamento | Faz IME militar originar/receber como endpoint, preservando os vínculos metropolitanos. |
| PUC-Rio–UFRJ / rede metropolitana existente | SNS-TF e serviços terrestres anteriores | Preserva a função da estação central e os contratos de preparação/interferência e processamento clássico. |
| IBM–Google, IQMP–IQC, IQC–Xanadu | `GEO-AMERICAS-SIM`, duas OGS por sessão, BBM92 | Visibilidade simultânea avaliada; não pressupõe fibra transcontinental ou infraestrutura real dessas instituições. |
| UFRJ–BSC, CIMATEC–CIQUANTA, CBPF–USP, IME-USP–UNICAMP, PUC-Rio–IME-RIO | `GEO-ATLANTIC-SIM`, duas OGS por sessão, BBM92 | Cenários complementares à infraestrutura terrestre, com perdas altas e poucas coincidências por bloco. |
| BSC–Microsoft Lyngby | `GEO-EUROPE-SIM`, OGS e BBM92 | Verifica elevação, percurso atmosférico, alinhamento e sincronização. |
| Pequim–Hefei, Hefei–Xangai | `GEO-ASIA-SIM`, OGS e BBM92 | Mantém a representação distribuída da rede chinesa; não declara integração ao backbone real. |
| Américas–Atlântico, Atlântico–Europa, Europa–Ásia, Ásia–Américas | Quatro ISL explícitos, terminais ópticos espaciais, APT, TDC e BBM92 | LOS WGS84, margem terrestre, alcance, difração, FoV, ruído, point-ahead e clocks admitem cada bloco. Não existe conexão automática entre os demais satélites. |
| UFRJ–BSC, comparação anterior | Ponte MEO anterior e comparação LEO | Preservadas com seus próprios parâmetros e limitações; não convertidas em GEO. |

## Hipóteses físicas e fontes

Os quatro GEO novos usam longitudes −100°, −20°, +20° e +120°, órbita circular
equatorial, raio calculado por `(mu/omega²)^(1/3)` e período sideral. Sua posição
ECEF permanece fixa; o movimento do tracejado não move o satélite. A altitude
armazenada de aproximadamente 35.793 km usa raio terrestre médio; sobre o
elipsoide equatorial resulta em aproximadamente 35.786 km. Órbitas reais,
TLE/SGP4, vetores de estado e perfis LEO/MEO existentes são preservados.

Downlinks novos: 850 nm, 5 milhões de pares/s por sessão, blocos de 2 s,
divergência de 2 µrad, apontamento de 0,3 µrad, receptor de 4 m, eficiência
óptica 0,5 e detecção 0,8, inserção de 2 dB, profundidade óptica de zênite 0,2,
elevação mínima 20°, jitter 50 ps, background 1 cps e dark counts 5 cps.
O background é uma hipótese favorável de filtragem/operação noturna; não
representa disponibilidade diurna garantida em todos os países.

ISL novos: fonte dedicada de 20 mil pares/s, transmissor de 2 m, receptor de
4 m, divergência de 0,4 µrad, erro de apontamento de 0,05 µrad, FoV 20 µrad,
alcance máximo 90 mil km e margem sobre o elipsoide de 80 km. São hipóteses
ambiciosas de engenharia. A fonte local, terminais e bancos de cada sessão
constituem recursos simulados do serviço; o cadastro não qualifica a integração
de um payload ou de uma memória física compartilhada.

O inventário declara **36 bancos dedicados por sessão**, com 132 alocações
locais: 536 modos de memória, 74 fontes de pares, seis fontes de pulsos e 192
terminais ópticos hipotéticos. Modos ocupados, tempos de armazenamento,
consumo e falhas são controlados pelo executor da sessão. Cada banco possui
identificador rastreável na matriz e no cadastro; esses totais não comprovam
equipamentos instalados. Arbitragem de um mesmo hardware entre várias sessões
ainda não está implementada. Conectar hardware compartilhado exige um
gerenciador de recursos próprio antes de ativar essas operações.

Esses valores não são parâmetros universais nem equipamentos confirmados nas
instituições. Contagens provêm de amostragem de emissões, propagação, detectores
e ruído; nenhuma fidelidade ou chave final é injetada na interface. Perdas,
point-ahead desativado/residual, indisponibilidade e restauração alteram os
resultados. Os limites e equações do ISL estão no
[contrato satelital](orion_q_inter_satellite_contract.md).

Referências primárias consultadas em 07/10/2026:

- [ESA: tipos de órbitas](https://www.esa.int/Enabling_Support/Space_Transportation/Types_of_orbits), 2020: condição geoestacionária e período; não valida um enlace quântico GEO.
- [NASA: comunicações de pequenos satélites](https://www.nasa.gov/smallsat-institute/sst-soa/soa-communications/): terminais ópticos, apontamento e rastreamento.
- [ESA: EDRS, 50 mil enlaces](https://www.esa.int/Applications/Connectivity_and_Secure_Communications/European_system_speeds_data_flow_with_50_000_links), 2021: maturidade de ISL clássico; não demonstra ISL QKD.
- [Dabiri et al., ISL QKD](https://arxiv.org/abs/2506.20798), 2025: análise teórica de perdas, jitter e FoV; não comprova a constelação simulada.
- [Gündoğan et al., memórias espaciais](https://www.nature.com/articles/s41534-021-00460-9), 2021: requisitos para arquiteturas propostas com armazenamento.
- [Briegel et al., repetidores](https://doi.org/10.1103/PhysRevLett.81.5932), 1998: operações imperfeitas, purificação e recursos intermediários.
- [Rede integrada chinesa](https://www.nature.com/articles/s41586-020-03093-8), 2021: arquitetura documentada; relays confiáveis não equivalem a repetição quântica sem confiança.

## Verificação reproduzível e limites

No checkout, execute:

```powershell
$env:PYTHONPATH='src'
python tools/validate_global_network.py --output outputs/global-network-check --steps 10
```

A semente é `20261007`. O diagnóstico executa cenário nominal, degradação,
obstrução terrestre, isolamento e recuperação usando comandos do backend.
Exporta `report.json`, `report.md`, `component-matrix.csv/.json/.md`, journals,
snapshots e timeline. Execuções consecutivas usam journals distintos e
preservam o estado do operador. No pacote Windows, `ORION-Q.exe --global-network-test
outputs-global` executa a campanha completa; `ORION-Q.exe --self-test
report.json` verifica a integração da rede global e do globo em modo curto.

**Limite operacional:** detecções BBM92 consomem fótons. Concatenar downlinks e
ISL detectados não produz entrelaçamento global. Demandas que exigem um relay
coerente orbital registram o impedimento de armazenamento/QND, BSM e executor
compartilhado; não recebem sucesso fabricado. Outras arquiteturas de relay
óptico passivo ou de nós confiáveis exigem adaptadores próprios e análise de
segurança, ausentes neste cenário. Chaves certificadas permanecem zero.

Não há qualificação de hardware, transmissão física de qubits ou atuação em
equipamentos. Encaminhamento clássico usa contratos locais simulados com
taxa/latência/jitter/perda; orçamento RF/óptico clássico de missão, exclusão solar,
atitude completa, clima observado e calibração do terminal permanecem externos.
Os modelos ML continuam opcionais e desligados por padrão.
