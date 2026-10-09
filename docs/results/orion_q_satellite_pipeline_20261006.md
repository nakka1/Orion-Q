# ORION-Q 5.41 — desenvolvimento das pendências satelitais

Data: 06/10/2026. Referência: [auditoria 5.40](orion_q_satellite_readiness_audit_20261006.md).
As pendências de software foram implementadas e exercitadas com dados artificiais
e recepção local de registros OGS. **Nenhum satélite, RF, telescópio ou TDC real
foi conectado. A qualificação de uma missão e a certificação QKD permanecem abertas.**

O incremento preserva os demais protocolos, a topologia institucional, os modelos
ML opcionais e a proibição de atuação física. Não altera as referências nem os
resultados históricos das campanhas. A auditoria anterior continua registrada
como avaliação de 5.40, sem reescrever suas conclusões.

## Alterações e limites

| Achado | Implementação em 5.41 | Limite que permanece |
|---|---|---|
| F01 — recepção externa ausente | Servidor WebSocket OGS local, autenticado e somente de leitura; ACK, sequência, idempotência, filas e quotas persistentes | RF/CCSDS e drivers de instrumentos dependem do ICD da missão; acesso remoto exige gateway/TLS qualificado |
| F02 — engenharia, APT e payload misturados | Três streams tipados: housekeeping, tracking e TDC; associação obrigatória da sessão e dos dois canais de cada segmento | TDC entrega informação clássica sobre detecções; a API não transporta estados quânticos |
| F03 — jitter sem efeito | Timestamps com jitter, alinhamento de relógio/propagação, coincidências 1:1 e descarte de gates ambíguos | Precisão e relógios são hipóteses configuráveis até calibração real |
| F04 — perdas cadastradas sem efeito | Inserção e perdas extras por receptor atenuam a probabilidade de detecção; orçamento discriminado em dB | Modelo gaussiano reduzido; não substitui orçamento medido |
| F05 — background/dark ignorados | Ruído Poisson por detector, tempo morto não paralisável e afterpulse configurável | Dependência espectral, eficiência temporal e memória do detector precisam de caracterização |
| F06 — passagem avaliada só no início | Integração óptica em passos ≤250 ms, limiares de elevação e bordas de janela resolvidos; perda de lock impede coincidências | Não representa operação contínua quando há bloqueio ou falta de visibilidade |
| F07 — órbita apenas configurada | SGP4/TLE validado e vetores ECEF cobertos; dados recebidos comandam posição, disponibilidade e geometria; azimute, velocidade, PAA e Doppler expostos | TEME→ECEF usa UT1=UTC e movimento polar zero; EOP, APT e efemérides de missão ainda necessários |
| F08 — pós-processamento agregado | Cliques → matching → bases → amostra finita → paridades/look-back → verificação universal → Toeplitz; vazamento observado e motivos de aborto | Backend central observa os bits; não é uma implementação QKD distribuída certificada; saída certificada zero |
| F09 — compensações estáticas | Doppler/filtro e limites de ajuste, erro óptico dinâmico e residual, atraso de tracking, Cn²/Fried/Strehl para SMF, AO e cintilação opcionais | Aproximações e parâmetros hipotéticos identificados; não há calibração atmosférica espacial completa |
| F10 — inspeção incompleta | Tabelas Housekeeping e Science, unidades, validade/origem, parâmetros, aborto finito, entrada OGS e evidência capturada estável | Dados ausentes não viram valores de saúde inventados |
| F11 — qualificação independente ausente | Vetor publicado de Vallado, geometria analítica WGS84/PAA, matriz Toeplitz independente e fontes científicas documentadas | Comparação com capturas reais, carga sustentada, segurança composável e máquina independente continuam abertas |

Contratos, formulários, parâmetros, unidades, fontes e execução estão no
[guia OGS v2](../product/orion_q_satellite_ogs_v2.md). A
[ADR-SAT-003](../architecture/adr_satellite_003_ogs_observations.md) registra as
fronteiras entre OGS, dados clássicos, payload e atuação.

## Casos de uso verificados

Os dados da coluna de resultado são testes de software. O fixture enviado pelo
socket com `origin=physical` verifica o contrato de recepção física; seu conteúdo
continua explicitamente sintético e não demonstra comunicação com hardware.

| Caso de uso | Resultado esperado | Resultado observado / evidência |
|---|---|---|
| Fonte simulada adquire dados brutos | Sem QBER, coincidências ou chave pronta na fonte | PASS — três streams e dois conjuntos de tags; processamento comum deriva as métricas |
| Associar canais de duas OGS | IDs clássico/quântico distintos e vínculo de sessão/bloco coerente | PASS — troca de vínculo recusada; streams incompletos não produzem resultado |
| Enviar métricas finais à entrada OGS | Recusa de resultados pré-calculados | PASS — `precomputed_result` |
| Abrir recepção sem credencial | Nenhum listener autenticado disponível | PASS — credencial ausente recusada; conexão sem bearer recusada |
| Enviar JSON de tipo errado ou pela rota errada | Recusa diagnosticada mantendo o socket utilizável | PASS — `invalid_packet` / `wrong_stream`; pacote válido subsequente aceito |
| Reenviar pacote e reconectar | ACK idempotente sem duplicar processamento | PASS — `duplicate`; reabertura preserva sequência e hash |
| Descobrir janelas e enviar capturas consecutivas | Gateway obtém os vínculos pela API, sem IDs internos de comando | PASS — status autenticado, dois blocos recebidos/processados e remoção das janelas concluídas |
| Reutilizar sequência com conteúdo diferente | Não sobrescrever evidência aceita | PASS — `replay` |
| Enviar dados futuros ou vencidos | Recusar o bloco | PASS — política de frescor; observações expiram |
| Saturar fila ou quota persistente | Backpressure/recusa; histórico preservado | PASS — `backpressure` / `storage_full`; pacote já aceito continua disponível |
| Receber timestamps inválidos, PPS ausente ou tags perdidos | Não processar sucesso | PASS — rejeição de float em época ps, referência ausente, overflow e erro de sincronismo |
| Produzir alinhamento fora de int64 | Não permitir wrap que crie coincidências | PASS — `invalid_tdc` antes da aritmética NumPy |
| Perder tracking | Nenhuma coincidência válida no segmento desbloqueado | PASS — indisponibilidade e estimativa zero |
| Encerrar janela dentro do bloco e reabrir | Contar somente intervalos operacionais | PASS — cortes e recuperação respeitados |
| Cruzar elevação mínima dentro de um passo | Não arredondar disponibilidade para mais 250 ms | PASS — fronteira geométrica resolvida por bisseção |
| Aumentar inserção/perda extra | Redução de pares detectados | PASS — perdas de 100 dB reduzem coincidências em mais de dez vezes |
| Inspecionar orçamento do cenário degradado | Mesma perda adicional usada na geração e no orçamento recebido | PASS — 12 dB adicionais registrados no tracking; referência e contribuição preservadas pelo processador comum |
| Aumentar jitter | Redução do matching na janela de 1 ns | PASS — jitter de 10⁶ ps reduz coincidências em mais de dez vezes |
| Alterar background, dark e tempo morto | Mudar cliques e descartes de detector | PASS — ruído aumenta eventos; tempo morto reduz registros e contabiliza descartes |
| Desligar correção de propagação | Não preservar coincidências artificialmente | PASS — atraso contínuo por tag; desligar correção reduz coincidências em mais de dez vezes |
| Injetar erro de distância | Degradar a sincronização efetiva | PASS — incerteza de 1 m reduz coincidências para menos de metade neste fixture |
| Exceder orçamento da geração simulada | Recusar antes de acumular grandes arrays | PASS — buffer de candidatos limitado; nenhuma truncagem silenciosa |
| Limitar compensação Doppler | Residual penaliza transmissão no filtro | PASS — GHz de portadora separados da frequência de repetição |
| Ativar turbulência/AO para SMF | Acoplamento responde aos parâmetros | PASS — Cn² reduz transmissão; AO recupera parcialmente conforme hipótese |
| Receber vetor ECEF diferente da órbita cadastrada | Geometria deve vir da observação | PASS — distância, PAA e estado do GEO usam dados recebidos |
| Converter posição recebida para o mapa | Latitude geodética e altura elipsoidal coerentes | PASS — WGS84 incluindo polos; o GEO recebe a mesma geometria nas origens física e simulada |
| Configurar tolerâncias de frescor/sincronismo | Aplicar a política da sessão em todas as origens | PASS — limites configurados exercitados em arquivo/stream e fonte simulada |
| Usar TLE inválido ou antigo | Recusar sem fallback silencioso Kepler | PASS — checksum/idade validados |
| Propagar TLE de referência de Vallado | Comparação numérica independente | PASS — posição e velocidade TEME no vetor publicado |
| Interpolar vetores ECEF | Interpolar posição/velocidade somente na cobertura | PASS — Hermite; fora da cobertura gera indisponibilidade |
| Processar bloco pequeno | Abortar orçamento finito sem inventar chave | PASS — motivo explícito e amplificação omitida nos recibos |
| Corrigir bits e aplicar Toeplitz | Operações reais sobre bits, com vazamento contabilizado | PASS — paridades/look-back, verificação e comparação com matriz independente |
| Processar captura simulada maior | Exercitar PA após matching e reconciliação | PASS — cenário `finite-laboratory`, estimativa condicional de 398 bits com seed 42; saída certificada zero |
| Atualizar interface a partir do backend | Engenharia/Science coerentes, unidades e origem visíveis | PASS — tabelas abastecidas pelo mesmo resultado do processador |
| Capturar evidência durante atualizações | Leitura capturada permanece estável | PASS — atualização ao vivo não substitui o texto capturado |
| Redimensionar e rolar inspeção | Última linha acessível em janela pequena | PASS — inspeção Qt verificada em 640×480 e 1100×750 |
| Abrir/fechar OGS pela interface | Enviar referência à credencial; liberar listener ao parar | PASS — comandos do formulário e encerramento no engine; nenhuma atuação física |
| Publicar snapshots e exportar recibos | Tags completos persistidos fora da publicação da GUI | PASS — referência SHA-256 no snapshot e exportação `.satellite-acquisitions.jsonl` |
| Distribuir executável | Validar dependências, pipeline e telas no binário | Resultado consolidado nas evidências de pacote abaixo |

O cenário laboratorial não modifica a topologia nem os dispositivos do usuário.
Usa dois receptores **hipotéticos de 6 m**, fonte de 100 mil pares/s, erro óptico
0,001 e bloco de 6 s. Com seed 42, o processador encontrou 5.919 coincidências,
2.902 bits sifted, corrigiu três erros e calculou 398 bits após descontar
amostragem finita e vazamento. Isso comprova o caminho computacional com aquela
amostra; não prevê rendimento de uma OGS existente. O perfil padrão pode ter
amostra insuficiente e abortar corretamente.

## Evidências e preservação

- [Qualificação final em fonte](../../outputs/satellite_pipeline_20261006/loss-final.xml): 51 testes aprovados em 33,65 s, incluindo contratos/física/processamento/UI, fluxo consecutivo, posição geodética, orçamento degradado e CLI.
- [Regressão modular](../../outputs/satellite_pipeline_20261006/modular-final.xml): 87 testes aprovados em 828,69 s; cinco tecnologias, entrada/replay, GEO e cadastro preservados. Esta rodada antecede os últimos ajustes específicos satelitais, cobertos pela qualificação final.
- [Regressão geográfica](../../outputs/satellite_pipeline_20261006/geographic-final.xml): 87 testes aprovados em 461,08 s; contratos, UI, GEO e cadastro. Antecede apenas o último ajuste de orçamento de perdas.
- [Gates de empacotamento](../../outputs/satellite_pipeline_20261006/package-docs-final.xml): 12 testes aprovados após a inclusão do guia OGS e da ADR no pacote portátil.
- [Pipeline OGS final em fonte](../../outputs/satellite_pipeline_20261006/end-to-end-loss/report.json): 21/21 verificações aprovadas; recibos e aquisições exportados na mesma pasta.
- [Inspeção Qt](../../outputs/satellite_pipeline_20261006/ui-visual-source/report.json): 7/7 verificações e capturas das duas tabelas; esse registro antecede os ajustes finais de pós-processamento.
- [Estado consolidado e preservação](../../outputs/satellite_pipeline_20261006/implementation-status.json): hashes, JUnit de cada fase e relatórios do pacote.
- [Comparação do binário com o código final](../../outputs/satellite_pipeline_20261006/frozen-source-comparison.json): 20/20 módulos novos ou alterados coincidem em bytecode, constantes, assinaturas e tabelas de exceção.

A regressão abrangente do primeiro candidato 5.41 e as rodadas específicas
posteriores totalizam **3.370 casos distintos aprovados**, sem falhas, e quatro
casos sem execução. Três exigem o ZIP original indicado em `ORION_QKD_ARCHIVE`
(não fornecido ao ambiente deste build); um exige ambiente LabOne Q 26.7.0
isolado. Os JUnit por fase permanecem no primeiro candidato
`release/windows/5.41.0-native-20261006-183647`; o pacote entregue corresponde ao
código final comparado acima e é novamente validado pelos gates do executável.

O baseline contém 1.097 arquivos anteriores. Foram mantidos integralmente 1.077;
os 20 modificados pertencem ao escopo deste incremento e constam no manifesto.
Nenhum arquivo do baseline foi removido. Os dois workflows receberam dependências
e gates satelitais e não estavam incluídos nesse baseline. Código/contratos novos
e documentação estão listados separadamente. O executável 5.40 anterior preserva
seu SHA-256 original, assim como o ZIP 5.40. Alterações não relacionadas já
existentes foram mantidas.

## Pacote final e temporização

Build: `5.41.0-native-20261006-200741`, Windows 11 / AMD64, no mesmo computador
da auditoria. Os testes gráficos internos usam Qt offscreen; a rodada de abertura
usa explicitamente a plataforma Qt Windows.

| Verificação no executável final | Resultado | Evidência |
|---|---|---|
| Autotestes da aplicação | PASS — 38/38 | [self-test.json](../../release/windows/5.41.0-native-20261006-200741/self-test.json) |
| Pipeline satelital, perdas e recepção autenticada | PASS — 21/21 | [report.json](../../release/windows/5.41.0-native-20261006-200741/satellite-validation/report.json) |
| Inspeção Housekeeping / Science / captura / scroll | PASS — 7/7 | [UI e imagens](../../release/windows/5.41.0-native-20261006-200741/satellite-ogs-ui/report.json) |
| Estresse sintético do controlador | PASS — 20 rodadas, 535 verificações | [report.json](../../release/windows/5.41.0-native-20261006-200741/stress-validation/synthetic-controller-eddbbca6e2254362accefd7ae374aa53/report.json) |
| Abertura nativa | PASS — 6/6, DPI 100/125/150/200%; serviço em 3440×1440 e demonstração em duas escalas | [startup-validation](../../release/windows/5.41.0-native-20261006-200741/startup-validation/report.json) |
| Dependências opcionais, roadmap e worker de campanha | PASS — modelos sem pacotes, comparações e execução/exportação no binário | Relatórios `optional-models-validation`, `roadmap-validation` e `campaign-validation` do build |
| ZIP e integridade | PASS — CRC; 3.926 arquivos, 294.702.633 bytes | [portable-package.json](../../release/windows/5.41.0-native-20261006-200741/portable-package.json) |
| Aceitação automática Windows | PASS — integridade, nova rodada de autotestes e estresse isolado | [acceptance-report.json](../../release/windows/5.41.0-native-20261006-200741/native-acceptance/acceptance-report.json) |

Distribuição: [pacote portátil completo](../../release/windows/5.41.0-native-20261006-200741/ORION-Q-5.41.0-native-windows-x64.zip).
Extraia a pasta `ORION-Q` inteira, conservando `_internal`, e abra
[ORION-Q.exe](../../release/windows/5.41.0-native-20261006-200741/ORION-Q/ORION-Q.exe).
O ZIP inclui o guia OGS v2 e a ADR-SAT-003. Nenhum instalador foi produzido.
O gate terminou em 2026-10-06 23:30:22 UTC com `automatic_passed=true`, escopo
`local_software_checks_only` e `acceptance_status=manual_review_required`.
A revisão manual e a máquina independente continuam pendentes; o resultado
automático não homologa instrumentos nem a segurança de uma missão.

- SHA-256 do EXE: `d8837815b9f057c06e3171f8fa6d39b6166d8b894495c57112f26953cf6d5f31`.
- SHA-256 do ZIP: `0a3ccc1bf46fb11492c140ab03ae545f7fd54632c8a108788c1488c6a99f77ef`.

A primeira publicação após abrir levou 6,23–6,44 s no serviço e 10,27–10,66 s
na demonstração. O intervalo p95 do heartbeat Qt foi 32 ms no serviço e
47–78 ms na demonstração; o maior intervalo observado foi **1.031 ms** no modo
demonstração. Os gates verificam resposta e recuperação; ainda podem ocorrer
pausas transitórias sob processamento. Não foram verificados arraste físico de
borda nem aceitação em um computador independente.

O ensaio isolado do roadmap mediu 20 ciclos, média 195,01 ms e máximo 475,38 ms;
**todos excederam a meta de 10 ms**. A amostra é insuficiente para qualificar p99.
O sucesso funcional dos diagnósticos não estabelece cumprimento de um prazo de
controle físico. Carga sustentada e temporização da missão permanecem abertas.

## Execução e qualificação externa

```powershell
python -m pip install -e ".[console,satellite,dev]"
python tools/validate_satellite_pipeline.py --output-dir outputs/satellite-novo --seed 42
ORION-Q.exe --satellite-test C:\temp\orion-satellite-novo --seed 42
```

Use destinos novos: os validadores não sobrescrevem evidência anterior. No
console, abra GEO/Topology → PROTOCOLOS → `RIO-SATELLITE` → **SATÉLITE ·
HOUSEKEEPING / SCIENCE / ENTRADA OGS**. A configuração por dispositivo fica em
Comunicadores; campos orbitais aparecem conforme o propagador escolhido.

Para integração operacional ainda são necessários ICD/gateway RF/TDC da missão,
capturas medidas com PPS/UTC, calibração de detectores e APT, EOP/órbita precisa,
benchmark sustentado, identidade/autenticação distribuída e revisão de segurança
composável. Os limites atuais incluem blocos com até 100 mil tags por OGS,
simulação óptica reduzida e análise finita por bloco, sem acumular material de
chave entre passagens. Nenhuma chave utilizável é entregue. A ausência desses
itens impede declarar prontidão de um satélite real.
