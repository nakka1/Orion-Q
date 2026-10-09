# ORION-Q — pausas nas atualizações ao vivo — 05/10/2026

## Problema e correção

O relato foi reproduzido na interface Qt nativa: a entrega de um novo segundo de
Bell fidelity, QBER e secret key rate coincidia com pausas nos demais componentes
ao vivo. A pausa máxima inicial foi de 672,99 ms em Telemetry e de 486,06 ms na
topologia com uma janela de node. Esses números medem intervalos do laço de
eventos, não o tempo do controlador nem uma garantia de tempo real.

Três causas contribuíam. Telemetry destruía e recriava todas as linhas das
condições quando uma única leitura mudava. O controlador SO(3), embora já rodasse
em uma thread, também disputava o interpretador Python com os callbacks de pintura
da interface. A reprodução com os mesmos registros previamente calculados deixou
de apresentar as pausas longas, isolando a disputa durante o cálculo. Com várias
janelas de nodes, a rasterização das curvas também se repetia a cada frame da
animação, embora as amostras só mudassem na entrega seguinte.

As linhas das condições, os chips de estado e os contadores da topologia agora
reutilizam seus widgets quando a estrutura permanece igual. Valores, cores,
contornos e tooltips continuam atualizados; mudanças de estrutura ainda criam ou
removem os componentes necessários.

O provider da interface usa um processo local para avaliar o AlphaBeta SO(3).
Esse processo chama o método original `AlphaBeta.propose`, com as mesmas amostras,
ganhos, timestamps e estado acumulado. O supervisor, a admissão dos modelos, o
journal e os comandos permanecem no processo principal. A cadência do backend
continua em 1 Hz; a apresentação da onda mantém sua cadência anterior. Não foram
introduzidos pontos interpolados, amostras fabricadas ou uma redução da frequência
de atualização. Os controladores personalizados conservam a execução original.

Somente os traçados das ondas são rasterizados
uma vez por entrega; a animação desloca e recorta essas imagens nas amostras
visíveis. Dados, timestamp, intervalo vertical, destaque, dimensões, tema e
densidade de pixels invalidam o traçado. A densidade inclui o zoom e o DPI do
monitor. Campos, botões e recibos continuam sendo repintados normalmente.

## Casos de uso da correção

| Caso | Verificação | Evidência |
|---|---|---|
| Fidelity atualizando continuamente | Dados do gráfico iguais aos do snapshot entregue; ticks novos; laço de eventos responsivo | `native-live-final/report.json`, caso `fidelity` |
| QBER atualizando continuamente | Mesma verificação, com a aba QBER selecionada | Caso `qber` |
| Secret key rate atualizando continuamente | Mesma verificação, com a aba key rate selecionada | Caso `skr` |
| Topologia com uma janela de node | Todas as amostras da onda coincidem com o backend | Caso `topology-one-node` |
| Topologia com três janelas de nodes | Ondas simultâneas e entrega de ticks sem acúmulo | Caso `topology-three-nodes` |
| Atualizar leituras durante o scroll | 35 condições, 20 atualizações, posição do scroll e widgets preservados | `test_console_live_rendering.py` |
| Alterar a estrutura das condições | Lista vazia, inclusão, reordenação e chaves repetidas | Mesmo módulo; temas claro e escuro |
| Alterar saúde, isolamento e compactação | Texto, cor, contorno e tooltip corretos sem recriar os chips estáveis | Mesmo módulo |
| Preservar o cálculo SO(3) | Quatro pares de ganhos; três períodos de 500 propostas; igualdade bit a bit das matrizes e estado | `test_network_baseline_worker.py` |
| Trocar para o controlador de reserva | Propostas iguais à execução intercalada original; primário degradado não avança | Mesmo módulo |
| Usar controlador personalizado | Subclasse, implementação externa, sobrescrita por instância ou classe e objeto compartilhado mantêm a execução original | Mesmo módulo |
| Tratar erros numéricos | Política NumPy de exceções e motivos de recusa preservados; handlers `call`/`log` executados no processo original | Mesmo módulo |
| Encerrar a aplicação | Processo de cálculo liberado; encerramento repetido permitido | Mesmo módulo e relatório ao vivo |
| Falha do processo de cálculo | Erro explícito e limpeza do processo; nenhum resultado sintetizado | Mesmo módulo |
| Provider sem engine | Caminho original, sem iniciar processo numérico | Testes do provider |
| Interagir com janelas sobrepostas | Reabrir/elevar, recalibrate, isolate, localização, fechar e reabrir; cliques pelo viewport real | `test_console_node_compositing.py`, quatro combinações de tema e zoom |
| Abrir a paleta com nodes na tela | Paleta recebe os cliques acima dos nodes; avisos permanecem visíveis | Mesmo módulo |
| Rolar e mudar o zoom dos nodes | Última conexão continua acessível; conteúdo e fechamento corretos | Mesmo módulo |
| Receber dados novos com o mesmo timestamp | Pixels mudam e amostras permanecem exatas | `test_console_wave_rendering.py`, temas claro e escuro |
| Isolar um link e restaurar a entrada | Onda vazia elimina o traçado; entrada nova volta a aparecer | Mesmo módulo |
| Revelar gradualmente uma entrega | Timestamps reais preservados; nenhum traçado no intervalo ainda oculto | Mesmo módulo |
| Alterar densidade, tamanho e destaque | Traçados regenerados em 100%, 150% e 200%, com amostras preservadas | Mesmo módulo |

## Medições nativas

Cada jornada durou 16 segundos, após inicialização e transição de tela, com heartbeat de 10 ms. A janela solicitou 1920 × 1080 pixels lógicos e manteve o ajuste à área útil do monitor. Tamanhos efetivos, DPR e versão Qt estão nos arquivos `environment.json`. Os pixels são comparados no viewport exibido, para detectar conteúdo parado mesmo quando os objetos internos já contêm dados novos.

| Escala | Jornada | Ticks | p95 (ms) | Máximo (ms) | Dados exatos / pixels atualizados |
|---|---|---:|---:|---:|---|
| 100% | topology-one-node | 16 | 13.40 | 59.50 | Sim / Sim |
| 100% | topology-three-nodes | 15 | 19.50 | 147.83 | Sim / Sim |
| 100% | fidelity | 16 | 10.74 | 45.91 | Sim / Sim |
| 100% | qber | 16 | 11.16 | 181.77 | Sim / Sim |
| 100% | skr | 16 | 10.68 | 47.07 | Sim / Sim |
| 150% | topology-one-node | 16 | 13.06 | 58.23 | Sim / Sim |
| 150% | topology-three-nodes | 15 | 18.84 | 178.70 | Sim / Sim |
| 150% | fidelity | 16 | 10.61 | 39.97 | Sim / Sim |
| 150% | qber | 16 | 11.15 | 176.22 | Sim / Sim |
| 150% | skr | 16 | 10.89 | 39.81 | Sim / Sim |

Foram contados 158 ticks. As dez jornadas passaram o orçamento de smoke test de 250 ms, com dados exatos, pixels novos, nenhum erro do provider e sem acúmulo de ticks no encerramento das amostras. Os relatórios registram todos os picos residuais; esses resultados não garantem ausência de jitter em toda máquina ou com qualquer número de janelas. O coletor de ciclos permanece no thread Qt, conforme a proteção anterior contra exclusão de wrappers no thread errado.

| Comparação com o mesmo instrumento | Máximo anterior (ms) | Máximo final (ms) |
|---|---:|---:|
| topology | 486.06 | 100.82 |
| telemetry | 672.99 | 37.94 |

Essa comparação usa 60 ticks de aquecimento, duas amostras de 16 segundos e heartbeat de 8 ms, sem cProfile ou builds/testes pesados simultâneos. Intervalos do laço de eventos não representam o tempo de uma operação científica. Evidências: `before-native-final/` e `production-native-final/`.

## Registros e escopo

As evidências desta correção estão em
`outputs/live_tick_stalls_20261005_v1/`. As cópias e hashes anteriores permitem
conferir o delta relativo ao projeto recebido, incluindo alterações que já
existiam. Os artefatos de tentativas de diagnóstico permanecem nessa pasta e não
devem ser confundidos com os relatórios finais. A primeira medição em 150%
reprovou com 292,67 ms; a separação das janelas sem otimizar os traçados ainda
reprovou com 251,31 ms. Ambas foram preservadas como `*-initial/`.
O cache de pixels de janelas inteiras foi descartado porque o teste de pixels
detectou conteúdo antigo; a renderização final não usa esse cache. Um desenho
alternativo por regiões também foi descartado por piorar a cadência medida.
A composição em proxies separados foi retirada após um travamento no fluxo de
seleção pela paleta. A hierarquia de widgets anterior foi restaurada; os 32 casos
de shell, sobreposição, pixels, zoom e ondas passaram depois dessa correção.
O registro do diagnóstico interrompido é `shell-diagnostic-interrupted.json`.

A primeira bateria reuniu 46 módulos Qt em um processo e acumulou mais de 2 GB
de objetos das fixtures. Ela foi interrompida por essa interferência entre testes,
sem ser contabilizada como uma aprovação final. A bateria final executa os mesmos
módulos da interface em processos separados e agrega seus XMLs. O registro da
interrupção está em `monolithic-interface-interrupted.json`; nenhuma instância do
programa do operador foi encerrada.

Os modelos continuam opcionais e sujeitos aos contratos existentes. O processo
numérico não abre ZIPs de modelos, journal, sockets de rede ou executor físico.
Os testes utilizam estados próprios, separados do workspace do operador.

A medição cobre o laço de eventos e o pipeline real com entrada simulada nesta
máquina. O controle externo do desktop estava indisponível por falha do helper
do sandbox; não se declara uma sessão manual de uso do desktop nem certificação
em outro computador. A aceitação manual permanece separada das verificações
automáticas.

## Consolidação da entrega

Regressão de fonte: **3162 aprovados, 1 pulado, zero falhas e zero erros**. Interface: 1029; rede/controle: 488; serviços e física: 1645. O único teste pulado é `test_real_compiler_emulator_round_trip_and_manifest`, que exige o ambiente isolado do SDK LabOne Q 26.7.0. A contagem reúne as baterias sucessivas; os módulos Qt rodam em processos separados, e os componentes afetados foram revalidados após retirar a composição experimental das janelas.

| Verificação do executável | Resultado |
|---|---|
| Self-test completo | APROVADO; 29 verificações principais |
| Leitura e rolagem dos textos | APROVADO; 1120 verificações |
| Cinco jornadas ao vivo, com pixels e dados exatos | APROVADO |
| Comparações do roadmap e execução em runtime isolado | APROVADO nos critérios funcionais |
| Modelos sem pacotes e com os ZIPs originais | APROVADO em ambos os modos |
| Pacotes canônicos opcionais | APROVADO; 11 modelos |
| Worker da campanha temporal | APROVADO |
| Stress, 20 rodadas, seed 1337 | APROVADO |
| Aceitação automática | APROVADO |
| ZIP portátil | CRC verificado e SHA-256 registrado |

A comparação do runtime isolado tem somente 20 amostras e não satisfaz o orçamento experimental de 10 ms. Esse teste funcional não certifica latência de hardware, p99 nem atuação física. A revisão manual e a verificação em outro computador continuam pendentes.

Dos 322 arquivos recebidos e copiados antes da intervenção, **314 permanecem idênticos** e 8 têm o delta desta correção. Nenhum desses arquivos foi removido. O patch relativo às cópias recebidas, os hashes e a lista de arquivos novos constam em [change-manifest.json](E:/Codex/2026-08-27/e/work/NISQ-Predictive-Twin/outputs/live_tick_stalls_20261005_v1/change-manifest.json) e [changes.patch](E:/Codex/2026-08-27/e/work/NISQ-Predictive-Twin/outputs/live_tick_stalls_20261005_v1/changes.patch). As campanhas científicas e as instâncias anteriores do operador foram preservadas.

Cada invocação da regressão, incluindo seus parâmetros e resultado, consta na [tabela de casos automatizados](E:/Codex/2026-08-27/e/work/NISQ-Predictive-Twin/outputs/live_tick_stalls_20261005_v1/casos-automatizados.md).

Nova entrega: [ORION-Q.exe](E:/Codex/2026-08-27/e/work/NISQ-Predictive-Twin/release/windows/5.38.0-native-20261005-190043/ORION-Q/ORION-Q.exe) e [ZIP portátil completo](E:/Codex/2026-08-27/e/work/NISQ-Predictive-Twin/release/windows/5.38.0-native-20261005-190043/ORION-Q-5.38.0-native-windows-x64.zip). O executável depende da pasta `_internal` que acompanha o ZIP; extraia o pacote inteiro.

SHA-256 do executável: `0966cedff94ba397c15a16f5f67bc45a3235f5c07a7e705989dec7eb00072e24`.

SHA-256 do ZIP: `38b5ca3987c9f4bed9d70e95083821adb2ba72ee6f33520ea0ceed4a7c923470`.

Os relatórios finais, com os caminhos e os limites da verificação, estão em [verification-summary.json](E:/Codex/2026-08-27/e/work/NISQ-Predictive-Twin/outputs/live_tick_stalls_20261005_v1/verification-summary.json).
