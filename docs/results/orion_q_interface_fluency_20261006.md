# ORION-Q — continuidade dos gráficos, janela e canais associados

Data: 06/10/2026. Repositório: `E:\Codex\2026-08-27\e\work\NISQ-Predictive-Twin`.
Evidências desta revisão: `outputs/interface_fluency_20261006_v1/`.

A revisão em fonte aprovou **982 casos distintos em 1.250 execuções**, incluindo
100 casos nativos em cada escala de 100%, 150% e 200%. Os XMLs selecionados e
seus hashes constam em `source-validation.json`. Esta é a regressão afetada pela
mudança, não uma execução de toda a suíte do repositório. O novo executável
passou nas 33 verificações do autoteste, inclusive com o plugin gráfico nativo
do Windows em 150%. A aceitação automática e a integridade do pacote passaram.
O aceite manual continua pendente. Não constitui uma nova campanha científica.

A comparação com a cópia recebida confirmou a retenção de **995 arquivos**:
962 idênticos byte a byte e 33 alterações delimitadas nesta revisão, além de
dez arquivos novos. Códigos de física dos autores, referências científicas,
campanhas anteriores, releases e journals do operador foram preservados.

## Causas e correções

O backend entrega uma janela móvel de 240 amostras, a 20 Hz. A apresentação
percorre essas amostras durante o segundo seguinte. A entrega nova removia do
buffer 20 amostras ainda visíveis: o início do traço saltava de 18,05 s para
19,05 s enquanto a tela ainda mostrava 30 s. A reprodução anterior registrou
um apagamento antecipado de 1 s, equivalente a 44,33 pixels na geometria testada.

O gráfico agora conserva um prefixo limitado de amostras realmente recebidas
até saírem da janela visível. A apresentação usa um timer preciso de 33 ms;
não interpola valores científicos nem extrapola na ausência de entrada. Grade
e traços são reutilizados entre quadros. O mapa também conserva sua própria
imagem enquanto geometria, tema, seleção e dados não mudam.

Na troca de tema, o stylesheet global voltava a processar árvores de widgets
que seriam descartadas. A mudança agora aplica estilos à raiz ativa e descarta
a tela anterior após o evento que iniciou a ação. A janela inicia restaurada,
dimensionada para o monitor; os gestos de mover e redimensionar no Windows
permanecem no laço de eventos Qt, sem solicitar o laço modal de dimensionamento
do Windows. Perda de captura, ocultação e desativação encerram o gesto.

A tela de campanhas também recebia atualizações redundantes a cada tick.
As verificações de fonte e autorização continuam executadas; uma apresentação
sem mudança não provoca outra reconstrução da tela.

## Casos de uso

“Fonte” significa execução do código no ambiente Windows com Qt, incluindo
modo offscreen. Eventos automatizados Qt não equivalem a operar o mouse real
do desktop. Os XMLs e relatórios JSON indicados preservam cada resultado.

| Caso | Entrada ou ação | Resultado verificado | Evidência |
|---|---|---|---|
| 01 — Janela móvel | Entregar 30 s e 31 s antes de revelar o novo segundo | Amostras ainda visíveis permanecem; nenhum salto na borda esquerda | `test_console_wave_continuity` |
| 02 — Pixels da borda | Capturar o gráfico antes/depois da entrega | Pixels do traço não são apagados antecipadamente | `test_console_wave_continuity` |
| 03 — Densidade e tema | Rasterizar em 100%, 150%, 200%, dark e light | Borda preservada e imagens reutilizadas | `test_console_wave_continuity` |
| 04 — Entrega antecipada | Receber o próximo quadro durante a animação | Relógio visual permanece contínuo | `test_console_wave_continuity` |
| 05 — Sem nova entrada | Esperar após a última entrega | Para na última amostra; não fabrica pontos | `test_console_wave_continuity` |
| 06 — Enlace desativado | Entregar série vazia | Traço e histórico visual removidos | `test_console_wave_continuity` |
| 07 — Enlace removido | Remover a série | Nenhum histórico de conexão inexistente | `test_console_wave_continuity` |
| 08 — Taxa alterada | Trocar 20 Hz por 10 Hz | Não mistura amostras com taxas incompatíveis | `test_console_wave_continuity` |
| 09 — Correção de amostras | Reentregar o mesmo timestamp | Substitui a sobreposição sem reiniciar o relógio | `test_console_wave_continuity` |
| 10 — Reinício/lacuna | Recuar o relógio ou criar lacuna longa | Remove o prefixo antigo; não conecta períodos distintos | `test_console_wave_continuity` |
| 11 — Sessão prolongada | 200 entregas sucessivas | Histórico recebido limitado a 301 pontos por série a 20 Hz | `test_console_wave_continuity` |
| 12 — Vários nodes | Abrir três janelas e manter o backend a 1 Hz | Gráficos recebem a série publicada; ticks continuam | `after-native-v5/report.json`, `test_console_topology_live` |
| 13 — Detalhes do node | Abrir e redimensionar o painel | Gráfico, legenda, identidade e estado permanecem acessíveis | `test_console_node_window_layout`, `test_console_node_compositing` |
| 14 — Troca de tema | Alternar quatro vezes em cada uma das seis páginas | Página, geometria e rascunho preservados; updates habilitados | `test_console_theme_transition` |
| 15 — Tema e janela nativos | Seis trocas, 18 tamanhos, todas as páginas | Aplicação volta a responder; pausas medidas abaixo | `theme-resize-native-v3/report.json` |
| 16 — Inicialização | Abrir com dimensões do monitor | Janela restaurada; fullscreen apenas por opção explícita | `test_console_display_adaptation` |
| 17 — Bordas e cantos | Arrastar as oito regiões de dimensionamento | Geometria muda na direção esperada e captura termina | `test_console_window_chrome`, `test_console_resize_regression` |
| 18 — Interrupção de gesto | Soltar, ocultar, perder captura ou desativar | Gesto encerrado; próxima interação disponível | `test_console_resize_regression` |
| 19 — Hover externo no teste | Evento de outro fluxo durante gesto automatizado | Não interrompe o gesto artificial; sem captura do mouse do operador | `test_console_resize_regression` |
| 20 — Controles da janela | Maximizar, restaurar, minimizar, fullscreen e fechar | Estados e geometria conferidos; closeEvent respeitado | `test_console_window_chrome` |
| 21 — Terminal | Redimensionar, fechar, reabrir e navegar | Área das páginas se reorganiza; histórico e rascunho mantidos | `test_console_terminal_workspace` |
| 22 — Leitores | Scroll em auditoria, comando, contexto e recibos | Texto continua acessível sem selecionar/copiar | `test_console_text_scrolling`, `test_console_record_tables` |
| 23 — Topologia funcional | Criar, localizar, isolar e recalibrar nodes | Elementos e conexões correspondem ao estado do backend | `test_functional_topology`, `test_console_topology_live` |
| 24 — Campanhas durante ticks | Repetir fonte sem alteração, depois interrompê-la | Não reemite apresentação repetida; mudança de permissão invalida a ação | `test_console_temporal_campaign` |
| 25 — Configurar dois canais | Editar IDs, parâmetros e associação | Par obrigatório, IDs distintos e leitura de volta exata | `test_console_channel_configuration`, `test_console_link_editor` |
| 26 — Par incompatível | Omitir um canal, repetir ID ou indicar driver físico | Configuração recusada | `test_network_channel_io` |
| 27 — Canais operacionais locais | Receber aquisição bruta do simulador | Seis etapas de validação/envio/recebimento precedem o processamento | `test_network_channel_io` |
| 28 — Comunicação recusada | Canal ausente/desativado, receptor/associação incorretos | Nenhuma observação entregue ao processamento | `test_network_channel_io` |
| 29 — Payload inválido | Mensagem grande/antiga, NaN, matriz fora de SO(3), span/pulsos incompatíveis | Recusa explícita, sem substituir por valores de conexão | `test_network_channel_io` |
| 30 — Aquisição repetida | Repetir ou recuar a sequência | Não trata comunicação antiga como entrada nova | `test_network_channel_io` |
| 31 — Sem comunicação | Enlace sem par ou entrada rejeitada | Nenhum QBER fabricado e nenhuma rota operacional pelo enlace | `test_network_channel_io`, `test_network_engine_routing` |
| 32 — Controle clássico | Enviar comando para a planta local | Comando serializado e recebido somente pelo port clássico | `test_network_channel_io` |
| 33 — Equivalência de fontes | 65 ticks, mesma aquisição por simulador e arquivo, duas declarações de origem | Status, QBER, fidelidade, SKR, wave, histórico, QKD e detector coincidem | `test_network_channel_io` |
| 34 — Fim de arquivo | Consumir todas as aquisições | Ausência de entrada; último estado não é repetido | `test_network_channel_io` |
| 35 — Recibos imutáveis | Publicar outro tick/comando | Snapshot anterior conserva seu recibo | `test_network_channel_io` |
| 36 — Inventário legado | Ler configuração sem canais | Hash/serialização antigos preservados; canais não inventados | `test_network_channel_io`, `test_network_inventory` |
| 37 — Modelos opcionais | Rodar sem arquivos externos | Baselines disponíveis; modelos ausentes não são obrigatórios | `test_network_engine_models`, diagnóstico congelado |
| 38 — Atuação física | Exercitar comandos e testes de segurança | Executor físico permanece indisponível | `test_physical_control_gate`, autoteste congelado |
| 39 — Gráfico no executável | Autoteste com rasters nos dois temas | Aprovadas as 16 verificações do gráfico, dentro das 33 do autoteste | `self-test.json → wave_continuity` |
| 40 — Jornada do pacote | Roadmap, worker, autoteste, stress, integridade ZIP e aceitação automática no Windows | Aprovados os gates locais; aceite manual e alvo de runtime continuam pendentes | Relatórios na nova pasta de release |

O caso 33 utiliza uma aquisição **artificial de teste** também rotulada como
“medida” para testar o contrato. Não é evidência de equipamento ou de origem
física. O formato é explicado em [canais associados](../product/orion_q_paired_channels.md).

## Medições e limites

Sonda Windows Qt a 150%, três janelas de nodes, backend real da demonstração,
60 ticks de aquecimento, 12 s em topologia e 12 s em telemetria. As versões
anteriores e posteriores foram executadas separadamente. Média/P95 abaixo
referem-se ao custo de pintar, não à latência fim a fim. P95 usa interpolação
linear. O período nominal da sonda de atividade é 8 ms; a resolução efetiva do
timer pode diferir entre execuções.

| Medição | Antes | Depois (`after-native-v5`) |
|---|---:|---:|
| Mapa: média / P95 de pintura | 4,427 / 5,909 ms | 0,809 / 0,603 ms |
| Gráfico N-01: média / P95 | 3,015 / 13,038 ms | 1,084 / 0,829 ms |
| Gráfico N-02: média / P95 | 3,162 / 14,670 ms | 1,239 / 0,833 ms |
| Gráfico N-03: média / P95 | 2,677 / 11,626 ms | 0,874 / 0,706 ms |
| Maior intervalo da sonda, topologia | 133,769 ms | 132,270 ms |
| Maior intervalo da sonda, telemetria | 60,907 ms | 72,963 ms |
| Aplicação de snapshot, máximo topologia | 22,387 ms | 32,018 ms |
| Aplicação de snapshot, máximo telemetria | 4,066 ms | 23,889 ms |
| Troca de tema, seis alternâncias | 4,53–4,69 s | 1,85–1,99 s |

A pintura recorrente ficou mais barata e o defeito da borda foi corrigido. A
introdução dos recibos e verificações dos canais aumentou o custo de atualizar
snapshots. Os maiores intervalos de atividade **não foram eliminados** e a
mudança de tema ainda causa uma pausa de aproximadamente dois segundos.
As medições são locais e curtas; não demonstram fluidez universal ou aprovação
de uma sessão prolongada em outro computador.

O diagnóstico congelado de runtime completou 20 ciclos, mas **os 20 excederam
o orçamento de 10 ms**: média 198,742 ms, máximo 300,855 ms. Vinte amostras não
qualificam P99. O gate verifica execução e proveniência; o alvo de latência não
foi atendido. Isso não aprova controle físico ou tempo real.

Um ensaio intermediário (`after-native-v1`) terminou com access violation e sem
relatório completo. Ele não foi contado como aprovado; a causa desse término
isolado não foi demonstrada. As execuções posteriores completas estão
preservadas. Falhas intermediárias nos testes de eventos sintéticos de resize
a 200% também permanecem nos XMLs. A bateria final `pytest-native-final-v6-*`
passou nas três escalas, com os critérios de geometria mantidos. Não foi
demonstrada a causa de todas as falhas intermediárias.

O primeiro autoteste congelado com Qt Windows em 150% falhou na roda de
rolagem do primeiro `PolicyContextInspection.receipt`; teclas, barra de
rolagem, os demais leitores, gráficos e controles passaram. Os testes isolados
e a jornada completa em fonte com instrumentação não reproduziram a falha.
Uma nova execução isolada do **mesmo binário**, com os critérios mantidos,
aprovou as 33 verificações (`qt-windows-150-validation-v2/report.json`). A causa
da primeira falha não foi demonstrada; não se apresenta essa repetição como uma
correção específica do leitor. As duas execuções permanecem registradas.

## Entrega e proveniência

Release: `release/windows/5.38.0-native-20261006-062158/`.
O ZIP contém 3.899 arquivos, incluindo o manifesto; CRC e hashes foram
verificados. Extraia o pacote inteiro e mantenha a pasta `_internal` junto ao
executável. A release anterior `5.38.0-native-20261006-020322` conserva os hashes
originais do EXE e do ZIP.

| Artefato ou gate | Resultado |
|---|---|
| `ORION-Q/ORION-Q.exe` | SHA256 `317deae7a82d56644d99e95dfcf6e2176d9e4cc4fba2f502e74c9adba7f2749b` |
| `ORION-Q-5.38.0-native-windows-x64.zip` | SHA256 `db0cbfa4de5dec00f225a03cfb347f07a5af281fa9f2fcd71c99d72775abb581`; 293.796.544 bytes |
| Comparação de controladores | 30, 120 e 250 amostras; execução isolada de runtime completada |
| Worker/campanha congelados | Aprovados, com preservação dos journals de validação |
| Autoteste e leitores | 33 verificações principais; 16 específicas da continuidade do gráfico |
| Qt Windows em 150% | 33 verificações principais e 54 controles/gestos da janela aprovados na repetição isolada |
| Stress | 20 rodadas artificiais aprovadas; sem atuação física |
| Modelos opcionais | Diagnóstico sem pacotes e com `F:\qnet_network.zip` e `Downloads\modelo_qkd_ml.zip` originais aprovado |
| Aceitação do pacote | `automatic_passed: true`, `manual_review: pending`, `independent_machine_verified: false` |
| Proveniência final | `release-verification.json` vincula hashes, gates e arquivos preservados; código em fonte coincide com a compilação |

O port quântico transporta **registros ópticos estatísticos em memória**. Não
transporta qubits nem demonstra nova comunicação óptica física. Replay de
arquivos utiliza aquisições gravadas com origem declarada, não autenticada.
Drivers, autenticação de fonte, transporte quântico físico e aceitação com
mouse real/monitores mistos continuam pendentes. Modelos permanecem opcionais;
nenhuma atuação física foi habilitada.
