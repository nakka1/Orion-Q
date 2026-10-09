# ORION-Q — demonstração institucional e controle opcional de ML

Implementação: 05/10/2026; aceite automático concluído em 06/10/2026
(America/Bahia). Cenário: `brazil-institutions-v1`.
IME significa **IME-USP**, conforme a confirmação do usuário.

O console de demonstração usa sete locais institucionais, oito enlaces
hipotéticos e cinco demandas. Os dados brutos vêm das plantas do backend;
a interface apresenta os resultados do motor. O mapa offline nacional inclui
foco por campus, necessário para distinguir USP e IME-USP no Butantã.
O switch de ML inicia **OFF** em um workspace novo, exige confirmação nos dois
sentidos e só muda após a resposta do backend. Nenhuma operação atua em hardware.

## Referências geográficas

As posições abaixo identificam um campus ou edifício de referência, sem precisão
de levantamento de um terminal quântico. Os endereços foram conferidos nas
fontes institucionais indicadas. A precisão decimal é de armazenamento e não
uma declaração de exatidão física. Cada node conserva a fonte na proveniência.

| Node | Referência | Latitude | Longitude | Fonte do local | Fonte da posição |
| --- | --- | ---: | ---: | --- | --- |
| UNIJORGE | Paralela, Salvador/BA | -12.9367532 | -38.4106091 | [UNIJORGE](https://www.unijorge.edu.br/sedes/) | [OSM: campus](https://www.openstreetmap.org/way/1096965108) |
| CIMATEC | Piatã, Salvador/BA | -12.9391787 | -38.3866934 | [SENAI CIMATEC](https://senaicimatec.com.br/sobre-o-senai-cimatec/nossos-campi/cimatec-sede/) | [OSM: campus](https://www.openstreetmap.org/node/9864252064) |
| CBPF | Urca, Rio de Janeiro/RJ | -22.953888889 | -43.173611111 | [CBPF](https://www.gov.br/cbpf/pt-br/o-cbpf/como-chegar) | Coordenadas publicadas pelo CBPF |
| IME-USP | Rua do Matão, 1010, Butantã/SP | -23.5600895 | -46.7315809 | [IME-USP](https://www.ime.usp.br/localizacao/) | Referência do mapa institucional |
| USP | Cidade Universitária, Butantã/SP | -23.5587573 | -46.7257853 | [Prefeitura do campus](https://puspc.usp.br/insitucional/) | [OSM: referência no campus](https://www.openstreetmap.org/way/152638233) |
| UNICAMP | Barão Geraldo, Campinas/SP | -22.8243075 | -47.067048 | [UNICAMP](https://www.fecfau.unicamp.br/localizacao-e-mapas/a-unicamp) | [OSM: referência no campus](https://www.openstreetmap.org/way/562047779) |
| CIQUANTA | Estação das Artes Luciano Agra, João Pessoa/PB | -7.1512091 | -34.797882 | [Governo da Paraíba: implantação](https://qa.site-padrao.portais.rke.codatahml.pb.gov.br/noticias/governo-da-paraiba-recebe-visita-do-mcti-para-acompanhar-implantacao-do-ciquanta/) | [OSM: edifício](https://www.openstreetmap.org/way/191634953) |

A localização de implantação do CIQUANTA também consta no jornal oficial
[A União, 05/12/2025](https://auniao.pb.gov.br/servicos/copy_of_jornal-a-uniao/2025/dezembro/jornal-em-pdf-05-12-25-cdepc.pdf).
Isso não confirma equipamentos, disponibilidade operacional ou uma rede real.

A [API de malhas do IBGE](https://servicodados.ibge.gov.br/api/docs/malhas?versao=3)
forneceu os limites das 27 UFs, com 47 partes poligonais. A versão offline e sua
proveniência estão em `console/geo/brazil_v1.json` e
[SOURCES.md](../../src/quantum_autopilot/console/geo/SOURCES.md).

## Enlaces e limites do modelo

| Enlace hipotético | Comprimento mínimo WGS84 (km) |
| --- | ---: |
| UNIJORGE ↔ CIMATEC | 2.609 |
| CIMATEC ↔ CIQUANTA | 751.327 |
| CIMATEC ↔ CBPF | 1218.584 |
| CIMATEC ↔ USP | 1468.442 |
| CBPF ↔ USP | 369.596 |
| USP ↔ IME-USP | 0.610 |
| USP ↔ UNICAMP | 88.524 |
| IME-USP ↔ UNICAMP | 88.427 |

Esses números são limites inferiores geodésicos, calculados pelo backend em
WGS84; não são comprimentos de rotas de cabo levantadas. O modelo usa os
comprimentos declarados sem comprimi-los ao intervalo de treinamento dos modelos.
IP, papéis dos nodes, hardware de referência e conexões são metadados hipotéticos.
Não há descoberta de infraestrutura das instituições, calibração de campo ou
repetidores intermediários inventados. A projeção visual do mapa nacional é
aproximada; as distâncias do motor são geodésicas.

Na validação inicial, os quatro enlaces acima de 300 km ficaram DOWN, com taxa e
estoque de chave utilizável iguais a zero. Os dois enlaces abaixo de 3 km
produziram chaves na planta de referência. Isso demonstra a execução dos modelos
de entrada e dos controles; não comprova desempenho experimental nessas instituições.
A fidelidade de Bell exibida é uma estimativa da simulação, sem tomografia.
Os acoplamentos e limites das plantas continuam descritos no
[motor de rede](../product/orion_q_network_engine.md).
O monitor de polarização mantém o ponto de operação de referência `calibrado`,
sem caracterizar o cabo de cada instituição. As plantas de polarização, relógio
e QKD são parcialmente acopladas pelo modelo existente; não representam um
gêmeo digital calibrado em campo para esses sete locais.

A expansão revelou uma estimativa positiva residual de chave em um enlace de
751 km, dominado por contagens escuras, com QBER próximo a 50%. A admissão agora
zera chave utilizável quando o QBER atinge o limite existente `qber_down`.
A estimativa rejeitada fica na proveniência; a fórmula de referência do autor,
os pesos e os critérios das campanhas científicas não foram alterados.

## Casos de uso e evidências

| Caso | Verificação | Resultado observado |
| --- | --- | --- |
| Abrir uma demonstração nova | Sete locais, oito fibras e cinco demandas no backend | Aprovado |
| Interpretar IME | ID e referência separados de USP, no campus Butantã | Aprovado |
| Preservar o cenário anterior | Journals nacional e Salvador separados | Aprovado |
| Reabrir após editar nodes | Node acrescentado e modo ML persistem | Aprovado |
| Mostrar a rede nacional | Sete posições e oito conexões da topologia persistida | Aprovado |
| Usar janelas de tamanhos distintos | 660×460, 1000×650 e 1440×900; controles sem sobreposição | Aprovado |
| Selecionar cada instituição | Foco, seleção e hit test dos sete nodes | Aprovado |
| Distinguir USP e IME-USP | Foco por campus sem alterar coordenadas | Aprovado |
| Calcular comprimentos | Mesma geodesia WGS84 da topologia | Aprovado |
| Simular uma fibra longa | DOWN e zero chave utilizável no cenário validado | Aprovado |
| Simular fibras locais | QBER e chaves calculados pela planta e pelo processamento | Aprovado |
| Iniciar sem pesos | Cinco papéis de controle desabilitados; baselines atuantes | Aprovado |
| Cancelar a confirmação | Nenhum comando nem alteração do switch | Aprovado |
| Habilitar ML | Confirmação, comando real, revisão, recibo e readback | Aprovado |
| Habilitar sem modelo admitido | Permissão ON, recibo em fallback e baselines atuantes | Aprovado |
| Desabilitar ML | Baselines assumem após comando confirmado | Aprovado |
| Alterar ML antes do readback | Switch mantém o estado recebido até a confirmação do backend | Aprovado |
| Enviar payload inválido ou confirmação vencida | Recusa sem efeito sobre o modo vigente | Aprovado |
| Receber uma recusa na interface | Pending liberado, estado mantido e erro disponível | Aprovado |
| Trocar fonte durante confirmação | Diálogo cancelado pelo contexto, sem envio do comando | Aprovado |
| Usar uma fonte sem permissão | Switch oculto/desabilitado e verbo recusado | Aprovado |
| Trocar controle com alarmes existentes | Detector, baseline, histórico e estoque preservados | Aprovado |
| Carregar ZIPs originais admitidos | Compensação, sincronização e taxa de chaves no contrato de 50 km | Aprovado |
| Habilitar roteamento fora do contrato institucional | Baseline usado no plano e no episódio; ML não chamado | Aprovado |
| Auditar a mudança | Verificação do hash dos recibos e `physical_action_performed=false` | Aprovado |
| Inspecionar mapa e confirmações | Capturas com tema e fontes reais do aplicativo | Aprovado em fonte e no executável; capturas conferidas visualmente |

Os testes usam eventos dos widgets Qt e o backend simulado real. Eles não são
uma execução manual por mouse no desktop nem uma campanha de hardware.
Os testes novos estão em `tests/test_institutional_demo.py` e
`tests/test_console_institutional_demo.py`; a aceitação interna do executável
inclui `console/institutional_validation.py`.

A regressão completa do motor e da UI executou 552 casos: 550 passaram na
primeira execução e dois exigiram ajuste. O teste de login ainda esperava o
journal anterior e dez nodes; passou a verificar o journal nacional, os sete
IDs e ML OFF. O switch também revelou excesso de largura no cabeçalho: seu
espaço vazio foi reduzido, preservando a fonte e o texto, e as medidas dos
layouts aninhados passaram a ser atualizadas antes do encaixe. Os dois casos
passaram na repetição; 113 testes dos oito módulos de interface afetados também
passaram. Os XMLs originais com as falhas permanecem preservados, e a
consolidação final considera os resultados posteriores dos mesmos casos.

As baterias independentes de interface foram separadas em processos Qt no
script de build, mantendo os 200 casos anteriores. Isso evita a lentidão
observada quando o corpus de leitura seguia muitas janelas no mesmo processo.
O corpus de leitura passou com 75 casos em aproximadamente 34 segundos isolado.

O teste dos ZIPs originais usa um enlace de referência de 50 km, separado da
rede institucional. Os hashes fixados são `6b8563209f54a2d566f1a4229b140b75fda93555085ff2bf6bfcad8f07abd938`
(qnet) e `94352f15b6dcb132a3c03559b9d94b28f42b6e9adb8b2cdaab18f46720a55a79`
(qkd). O teste verificou 10 condições de carregamento, handover, detector e recibos
([evidência](../../outputs/demo_institutional_20261005_v1/original-model-handover/report.json)).
ON concede permissão, mas não garante atuação: peso ausente, falha ou contrato
incompatível mantém o controle de referência. O detector permanece diagnóstico.
O recibo de permissão não declara um modelo ativo quando só baselines estão
disponíveis; nessa condição registra `model_state=fallback`.

## Regressões em fonte

| Bateria | Casos aprovados após as repetições documentadas | Skips restantes |
| --- | ---: | ---: |
| Evidências e diagnóstico do roadmap | 58 | 1 |
| Integração do console e fronteira física | 68 | 0 |
| Monitor, DPI e adaptação | 48 | 0 |
| Recibos de comandos e auditoria de decisões | 41 | 0 |
| Leitura e rolagem | 75 | 0 |
| Renderização ao vivo, composição e ondas | 36 | 0 |
| Janela sem moldura | 35 | 0 |
| Demonstração institucional e switch | 35 | 0 |
| Campanhas e histórico | 491 | 0 |
| Motor de rede e UI conectada | 552 | 0 |
| Ciclo de comandos e políticas | 651 | 0 |
| Modelos opcionais, incluindo três exemplos com ZIP original | 370 | 0 |
| Sincronização em shadow | 87 | 0 |
| Compensação e outras sessões em shadow | 580 | 0 |
| **Total de casos nas baterias** | **3127** | **1** |

O total inclui casos repetidos entre baterias: são 3087 identidades únicas,
das quais 3086 foram aprovadas e uma foi pulada. O skip restante exige o
ambiente isolado de LabOne Q 26.7.0; não testa a nova demonstração. Os três
skips por ausência do ZIP QKD foram supridos por uma execução com o original,
com 3/3 aprovações em `pytest-external-example-rerun-results.xml`.
As repetições de interface e dos exemplos ficam em XMLs separados; os relatórios
anteriores não foram apagados. Essas verificações são locais na mesma máquina
Windows, sem aceite independente ou hardware físico.

## Pacote e preservação

O pacote Windows x64 foi gerado e passou pelo aceite automático local em
Windows 11. A [consolidação final](../../outputs/demo_institutional_20261005_v1/final-validation.json)
verifica os resultados posteriores dos mesmos testes, a integridade do
executável e do ZIP e os relatórios congelados. Nenhum XML anterior foi substituído.

| Verificação no executável | Resultado | Evidência |
| --- | --- | --- |
| Autoteste completo | `passed=true`, incluindo as verificações anteriores do console | [self-test.json](../../release/windows/5.38.0-native-20261005-225924/self-test.json) |
| Rede institucional, mapa e switch | 28/28 verificações aprovadas | [report.json](../../release/windows/5.38.0-native-20261005-225924/institutional-demonstration/report.json) |
| Estresse do controlador | 20 rodadas, 140 ciclos e 535 verificações aprovadas; sem atuação física | [report.json](../../release/windows/5.38.0-native-20261005-225924/stress-validation/synthetic-controller-db8d8eb3f01f4329b4195dd0393352aa/report.json) |
| Modelos opcionais | Aprovado sem pacotes e com os ZIPs originais Qnet e QKD, no mesmo executável | [report.json](../../outputs/demo_institutional_20261005_v1/frozen-original-models/report.json) |
| Diagnóstico do roadmap e campanha temporal congelada | Aprovados nos gates do build | [log do build](../../outputs/demo_institutional_20261005_v1/build-remaining.log) |
| Integridade do pacote | 3898 arquivos no ZIP, CRC e hashes conferidos | [portable-package.json](../../release/windows/5.38.0-native-20261005-225924/portable-package.json) |
| Aceitação automática Windows | `automatic_passed=true`: integridade, autoteste e nova execução de estresse | [acceptance-report.json](../../release/windows/5.38.0-native-20261005-225924/native-acceptance/acceptance-report.json) |

As capturas do executável foram inspecionadas: [mapa nacional](../../release/windows/5.38.0-native-20261005-225924/institutional-demonstration/topology.png),
[confirmação ao habilitar](../../release/windows/5.38.0-native-20261005-225924/institutional-demonstration/confirm-enable.png)
e [confirmação ao desabilitar](../../release/windows/5.38.0-native-20261005-225924/institutional-demonstration/confirm-disable.png).
Os textos e botões aparecem completos, com a disposição adaptada ao tamanho da janela.

- [Executável](../../release/windows/5.38.0-native-20261005-225924/ORION-Q/ORION-Q.exe)
  — deve permanecer com a pasta `_internal` ao lado.
- [Pacote portátil completo](../../release/windows/5.38.0-native-20261005-225924/ORION-Q-5.38.0-native-windows-x64.zip)
  — 293744955 bytes; extraia a pasta completa para usar em outro diretório.

SHA-256 do executável:
`095016afe3d37c0a315927a5522dfcf6ac87a57f56d068b270450ba2099ee8f5`.
SHA-256 do ZIP:
`4f9e31a632f3d4c76b12a8970cbc74cfa15d60a3cf6e72e06d6c1a7c22f8a1b7`.

A aceitação automática é local. O relatório mantém
`independent_machine_verified=false` e `manual_review=pending`; não se declara
aceite em outra máquina, execução manual no desktop ou validação de hardware.
O skip de LabOne Q descrito acima continua separado desse aceite.

O manifest anterior e as cópias dos 620 arquivos recebidos estão em
`outputs/demo_institutional_20261005_v1/before-manifest.json` e `before/`.
As modificações pré-existentes e os executáveis anteriores são preservados.
O [manifest de preservação](../../outputs/demo_institutional_20261005_v1/preservation-report.json)
confirma que os 620 arquivos recebidos continuam presentes: 592 são idênticos,
28 receberam o incremento e oito arquivos foram acrescentados nos diretórios
auditados. O diff da sessão e as cópias anteriores permitem conferir o incremento.
O [guia de demonstração](../product/orion_q_demo_guide.md) descreve o uso atual.
