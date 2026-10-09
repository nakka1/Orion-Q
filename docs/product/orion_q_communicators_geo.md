# Comunicadores e GEO — ORION-Q 5.43

Implementação de 06/10/2026. Os cadastros configuram o backend e a demonstração;
não estabelecem uma conexão com as instituições representadas nem acionam hardware.
As hipóteses científicas e localizações permanecem na
[documentação dos cinco módulos](orion_q_modular_quantum_protocols.md),
[matriz](../results/orion_q_modular_connection_matrix_20261006.md) e
[referências geográficas](../results/orion_q_modular_locations_20261006.md).

## Operação na interface

1. Abra **Comunicadores → Dispositivos → NOVO**. Informe identificadores,
   nome, tipo físico e função na rede. Campos com `*` são obrigatórios.
2. Selecione as tecnologias. Protocolos e capacidades mínimas são associados
   automaticamente conforme o tipo e o papel do dispositivo no protocolo.
   **ASSOCIAR PROTOCOLOS E CAPACIDADES** permite repetir essa associação; entradas
   adicionais do operador são preservadas. As abas exibem somente os parâmetros
   das tecnologias selecionadas. A declaração não comprova hardware disponível.
3. Informe coordenadas para instalações terrestres. Para satélites, use a aba
   **Órbita**, época UTC explícita e estações compatíveis por ID público ou interno.
   Os campos terrestres são ocultados no tipo Satélite: o backend propaga a posição.
   Os demais tipos não apresentam a aba Órbita. Estações ópticas podem oferecer
   **Enlace satélite–solo / Doppler** sem possuir elementos orbitais próprios.
   Latitude e longitude devem ser informadas juntas; ambas podem permanecer
   vazias no cadastro. Coordenadas ausentes nunca são substituídas por zero.
4. Escolha integração simulada ou arquivo de medições. O modo físico pode ser
   cadastrado, mas sua ativação é recusada enquanto não houver driver validado.
   Credenciais aceitam apenas referências `vault:`, `env:` ou `keyring:`;
   o cadastro não resolve esses segredos nem faz autenticação externa.
   A entrada OGS WebSocket fica disponível para a tecnologia de enlace satélite–solo;
   exige endpoint local e referência `env:`. Campos de integração acompanham o modo.
   Cada modo conserva seu próprio rascunho de endereço/interface/referência; trocar
   de fonte não mistura esses campos nem substitui implicitamente uma fonte física
   por simulada. Números aceitam ponto ou vírgula decimal sem separador de milhares.
5. **SALVAR CADASTRO** aguarda confirmação do backend. Cadastrar um dispositivo
   cria seu ponto, sem acrescentar enlaces. **VER NO GEO** seleciona o ponto.
   A confirmação exige a revisão e o conteúdo persistidos. Rascunhos e operações
   pendentes sobrevivem à reconstrução do tema; edições posteriores ao envio
   permanecem no formulário. Para editar um dispositivo já cadastrado, selecione
   seu registro na tabela. **NOVO** inicia um cadastro com a seleção anterior limpa.
6. Em **Conexões / ativação**, consulte uma sessão ou informe seu identificador,
   percurso ordenado, tecnologias, cenário e semente. IDs públicos e internos
   são aceitos. **PREPARAR SEGMENTOS** cria um par clássico/quântico por segmento.
   Selecionar **Nova conexão** limpa a sessão, o percurso e os canais anteriores.
7. Confira os dois IDs distintos, receptor, idade máxima e tamanho da mensagem.
   **VALIDAR E APLICAR CONEXÃO** valida todos os dispositivos antes de persistir.
   O status confirmado vem do backend; habilitado não significa que houve sucesso.
8. Inspecione estados e métricas em **Topology → PROTOCOLOS** e selecione enlaces
   ou nodes. **Consulta / evidências** captura um relatório estável do cadastro e
   sessões; novos ticks não substituem o texto nem os rascunhos dos formulários.

Troca de tema preserva inclusive campos ainda incompletos. Mudança da fonte ou
contexto da topologia limpa o rascunho para impedir envio a uma fonte diferente.
Edição de uma sessão substitui apenas os segmentos dessa sessão; outras rotas,
cadastros e conexões são preservados. Desativar um comunicador bloqueia seus
enlaces, inclusive os legados. Alterar capacidades ou origem revalida as sessões.

## Contrato e persistência

`control_plane.communicators.Communicator` usa `orion.communicator.v1`.
`device_id` é a identificação pública e `node_id` a identidade persistente da
topologia. As identidades ficam fixas após o primeiro cadastro. Migração inicial
é idempotente, preservando sessões, recibos, localizações e hipóteses recebidas.
Ela registra o evento de migração sem avançar as versões operacionais da
topologia/nodes: acrescentar metadados não invalida campanhas congeladas.
A transação compara também o estado anterior completo, recusando concorrência.
Edições do operador continuam avançando as versões e revisões normalmente.

| Grupo | Campos e regras principais |
|---|---|
| Geral | nome, IDs únicos, instituição, fabricante/modelo, categoria, descrição, revisão, estado enabled/maintenance/disabled |
| Tipo físico | installation, quantum_repeater, measurement_station, ground_station, satellite, quantum_memory, purification_service |
| Função lógica | endpoint, core, repeater, measurement, ground_station, service, intermediate; independente do tipo físico |
| Tecnologias | entanglement_swapping, quantum_memory, purification, tf_qkd, satellite_doppler |
| Protocolos/capacidades | listas explícitas; dependências mínimas verificadas, incluindo papéis SNS-TF e fonte óptica satelital |
| Localização | latitude [-90,90]°, longitude [-180,180]°, altitude [-500,10000] m; obrigatória antes de ativar um enlace terrestre |
| Integração | modo, adaptador, interface, endereço/arquivo, credential_ref, data_origin; chaves finais ou segredos não entram no cadastro |
| Óptica | comprimento de onda [100,100000] nm, fonte, emissão [0,10¹⁰] pares/s, detector, eficiência [0,1], inserção [0,1000] dB, jitter ps, tolerância de sincronização s, limite de emissão |
| BSM | segmentos, recursos [2,1024], modelo, sucesso [0,1], erro de operação, heralding clássico obrigatório; linear optics unassisted limitado a 0,5 |
| Memória | plataforma, modos [2,1024], coerência/cutoff [10⁻⁶,10⁶] s, escrita/leitura [0,1], compatibilidade de comprimento de onda |
| SNS-TF | sender/central_measurement, pulsos [1,10¹⁰] Hz, sinal/chamariz, ν<μ, estabilização/erro de fase, fEC [1,3], orçamento assintótico condicional |
| BBPSSW | operações locais, fidelidade mínima [0,5,1], matching_parity, dois pares consumidos/tentativa, erro operacional, rendimento máximo 0,5 |
| Satélite/solo | elevação mínima, divergência/jitter µrad, abertura m, atmosfera, eficiência, Doppler residual/filtro Hz, apontamento, nuvens |
| Órbita | modelo, classe, época UTC/rede, fonte, perigeu/altitude km, inclinação, excentricidade, RAAN, argumento do perigeu, anomalia média, referência/fase circular, estações, janelas, carga útil |

Todos os limites exatos e valores iniciais estão em `FIELDS` no modelo de dados
e aparecem como unidades, intervalos e tooltips no formulário. Esses limites são
limites de admissão do software; não são especificações universais de hardware.
Valores iniciais descrevem componentes hipotéticos e não comprovam capacidades.

Os comandos `save_communicator` e `configure_communicator_connection` exigem
`expected_version`. Edição de registro também exige sua revisão atual.
Uma transação SQLite atualiza cadastro, topologia e evento contendo o estado
anterior. Conflitos e configurações inválidas não aplicam alterações parciais.
No primeiro cadastro em um workspace novo, a mesma transação persiste o blueprint
e o cadastro. Uma submissão inválida não inicializa a topologia nem cria enlaces.
Limites: 256 componentes e 256 segmentos por topologia; payload até 32768 bytes.
A tabela `communicators` mantém o registro; a topologia publica o mesmo contrato
em `node.telemetry.communicator`. `LiveState`/`LiveView` publicam `communicators`
e `satellites`. GRAPH, GEO, MATRIX e inspeção usam esse snapshot.
No preset institucional, os metadados iniciais são migrados com auditoria sem
avançar versões operacionais. No inventário comum já editado, a leitura preserva
a topologia e os hashes dos recibos; o primeiro salvamento explícito de um
comunicador cadastra os componentes legados na mesma transação.

## Nomenclaturas e rótulos

| Identidade preservada | Identificador público | Nome | Função |
|---|---|---|---|
| PUC-RIO | BR-PUCRIO | Pontifícia Univ. Católica do Rio de Janeiro | Endpoint |
| UFRJ | BR-UFRJ | Universidade Federal do Rio de Janeiro (Fundão) | Endpoint |
| IME-RIO | BR-IME | Instituto Militar de Engenharia (Urca) | Endpoint |
| CBPF | BR-CBPF | Centro Brasileiro de Pesquisas Físicas | Core |
| IBM-WATSON | US-IBM-TW | Thomas J. Watson Research Center — IBM | Endpoint |
| IQMP | US-IQMP | Illinois Quantum and Microelectronics Park | Core |
| GOOGLE-QAI | US-GOOG-QA | Google Quantum AI Lab | Endpoint |
| BSC | ES-BSC-CNS | Barcelona Supercomputing Center | Core |
| MS-QUANTUM | DK-MSFT-QL | Microsoft Quantum Lab Lyngby | Endpoint |
| IQC | CA-UOW-IQC | Institute for Quantum Computing — Waterloo | Endpoint |
| XANADU | CA-XANADU | Xanadu Quantum Technologies (Toronto) | Endpoint |
| CN-BEIJING | CN-QCN-BJ | CN-QCN — Backbone Hub Pequim | Core |
| CN-SHANGHAI | CN-QCN-SH | CN-QCN — Backbone Hub Xangai | Core |
| CN-HEFEI | CN-QCN-HF | CN-QCN — Hub Hefei (Laboratório Key) | Core |

As categorias solicitadas estão no cadastro e tooltips. O IME-USP permanece
distinto de BR-IME. O mapa destaca identificadores curtos somente quando a
função registrada é `endpoint` ou `core`; nomes completos aparecem na consulta.
Repetidores, estações de medição, serviços, satélites e caminhos não ganham
rótulo destacado por sua aparência ou pelo texto do nome.

## Pipeline e parâmetros operacionais

Cadastro → admissão de capacidades/protocolos/canais → aquisição bruta → vínculo
com comando/sessão/canais → validação/sincronização → operações/recursos → envio
clássico → recebimento/heralding → métricas → persistência → interface.

O simulador e `MeasurementFileSource` usam `Acquisition` e os mesmos processadores.
Uma sessão de arquivo exige o mesmo arquivo associado a seus dispositivos;
observações JSONL devem corresponder a comandos, tempos, sequências e canais.
O campo declarativo do cadastro de arquivo é `data_origin=measured`; a origem
efetivamente publicada pelos recibos é **measurement_file**. Um arquivo pode
conter medições ou replay sintético; seu nome não certifica medição física.
Os testes usam replay sintético explicitamente identificado.

Os parâmetros editados são projetados por dispositivo: detectores nas extremidades,
capacidade/escrita/leitura das memórias, BSM no repetidor, erros locais na purificação,
preparação nos emissores TF, medição central e óptica de cada estação. Decoerência
independente das memórias de um par combina as taxas 1/T; cutoff usa o menor tempo.
O modelo SNS-TF disponível exige taxas e intensidades iguais entre os emissores;
configurações assimétricas de preparação são recusadas, não aproximadas silenciosamente.
As diferenças de comprimento/perda dos braços continuam modeladas.
O número de pulsos depende da taxa e duração do bloco. Menos de quatro pulsos
não sustenta as categorias desse modelo reduzido e não gera uma observação;
o fluxo registra ausência/timeout. Taxa satelital zero produz emissão zero.

As memórias são recursos locais às sessões. Protocolos de distribuição são drivers
separados (swapping, TF ou satélite); memória/purificação podem complementá-los.
O fluxo de recursos memória/purificação é implementado no driver de swapping.
Composições com TF/satélite são recusadas na ativação até terem um driver de
recursos correspondente; cadastrar capacidades nesses dispositivos não executa
uma composição ainda indisponível.
Nenhum cadastro fornece pares disponíveis, fidelidade final, sucesso garantido
ou chaves finais. As chaves certificadas continuam zero: o orçamento condicional
não substitui autenticação, reconciliação de bits e prova composável de segurança.
`configure_technology_source` com `none` interrompe todas as sessões, incluindo
as que têm fonte cadastrada; reativação explícita de uma conexão libera só ela.

## Propagação orbital e transmissão

O módulo `quantum_protocols.orbits` fornece posição e geometria tanto à fonte
simulada quanto ao backend de visualização. `circular_reference` preserva os
cenários anteriores; `kepler_elements` resolve uma órbita elíptica de dois corpos.
São elementos osculantes de cenário, com época e origem explícitas. A altitude é
a altitude circular ou de perigeu: a=(6371+h)/(1−e), com μ=398600,4418 km³/s².
O modelo usa anomalia média, equação de Kepler e rotações RAAN/inclinação/perigeu,
seguido de rotação terrestre aproximada referenciada à época UTC.

Tempo UTC = epoch_utc + (tempo da rede − epoch_network_s). O relógio causal da
rede determina posição, janelas e observações; aceleração existente é indicada
nas métricas da sessão. Classes LEO/MEO/GEO/HEO são validadas contra a geometria.
LEO/MEO verificam perigeu e apogeu, e o mapeamento da época precisa caber no
intervalo de datas do propagador durante a primeira órbita.
Classe GEO exige geometria equatorial compatível, não apenas um nome cadastrado.

Distância inclinada, elevação, velocidade radial e Doppler são calculados contra
cada estação terrestre, incluindo altitude. O orçamento óptico aplica divergência,
abertura, jitter, atmosfera, filtro, compensação residual e apontamento. Nuvens,
manutenção, perda de visibilidade ou janela fechada interrompem o braço afetado.
Só coincidências e comunicação clássica válidas sustentam resultado entre extremos.

Selecionar um satélite abre um globo ortográfico com zoom out de 850 ms. Arraste
para girar; use a roda do mouse ou +/− para zoom. A órbita tracejada usa 193 posições calculadas no backend e representa
o plano orbital no instante publicado, não o ground track. A metade posterior
fica discreta como referência espacial. O deslocamento dos traços acompanha a
ordem orbital, e o marcador usa exclusivamente a posição publicada. A animação
visual interpola por até um tick e para quando o relógio deixa de avançar.

Linhas finas usam `transmitting_links`: exigem um bloco recente de emissão
efetivamente recebido, geometria disponível e canais habilitados. Visibilidade
isolada não cria linha. Uma estação pode receber emissão sem coincidência na
outra; isso não é sucesso de QKD. As linhas representam o último bloco recebido,
não uma medida óptica instantânea. Trocar de mapa ou ocultar GEO para o timer;
o raster terrestre é cacheado e somente a sobreposição é animada a 30 Hz.

## Fontes e limites

Fontes primárias consultadas em 06/10/2026:

- [ESA Network of Models — Trajectory](https://nom.esa.int/models/trajectory):
  distingue elementos osculantes, época, sistemas de coordenadas e fontes de trajetória.
- [CelesTrak — More Frequently Asked Questions, março de 1998](https://celestrak.org/columns/v04n05/):
  elementos TLE são dependentes do modelo SGP4/SDP4; não devem ser usados como
  elementos Kepler genéricos.
- [CelesTrak — SGP4 tutorial](https://celestrak.org/software/tutorials/sgp4.php):
  referência para uma futura integração de propagador específico.

O ORION-Q não integra o serviço ESA nem implementa SGP4, ingestão TLE/OEM,
efemérides observadas, J2, arrasto, manobras, relógios relativísticos ou rastreamento
validado. Fonte declarada TLE/NORAD é recusada pelo propagador disponível. Os
satélites de demonstração continuam hipotéticos e a Terra é uma esfera de 6371 km.
O contrato permite futuros adaptadores; não afirma que eles já existem.
O driver satelital disponível modela distribuição de pares por fonte óptica.
Capacidades complementares podem ser cadastradas, mas sessões que tentem usar
um satélite como trecho de fibra/TF terrestre são recusadas: exigiriam outro
driver de propagação e protocolo, ainda não implementado.
O gerenciamento de recursos de memória e purificação está implementado com
troca de entrelaçamento. Esses recursos podem constar no cadastro de outros
dispositivos, mas sua composição operacional com TF-QKD ou com o driver
satelital disponível é recusada até existir um fluxo de recursos compatível.
Drivers físicos, autenticação de origem, apontamento externo e envio de fótons
por equipamento real não estão disponíveis. Modelos ML permanecem opcionais.

## Execução e validação

```powershell
$env:PYTHONPATH = "src"
.audit-winvenv\Scripts\python.exe -m pytest tests\test_communicators_geo.py
powershell -ExecutionPolicy Bypass -File tools\build_windows_console.ps1 -Python .audit-winvenv\Scripts\python.exe -SkipDependencyInstall -SkipInstaller
ORION-Q.exe --demo
ORION-Q.exe --self-test C:\temp\orion-geo\report.json
```

O autoteste inclui `communicators_geo` com cadastro dos sete tipos pela interface,
ativação por dois canais, eventos causais, labels, seleção orbital e capturas.
Veja o [relatório de validação](../results/orion_q_communicators_geo_validation_20261006.md)
para resultados, artefatos e limitações da execução.
