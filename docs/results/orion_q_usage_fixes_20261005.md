# ORION-Q — correções de leitura, mapa e resultados, 5 de outubro de 2026

Estado: fonte, renderizador nativo do Windows, novo executável e integridade
do ZIP aprovados na validação automática local.

## Diagnóstico e mudanças

No COMMAND CENTER, reescrever o mesmo documento durante uma atualização
devolvia a barra de rolagem ao início. A reprodução registrou a passagem de
148 para 0. As duas áreas deste painel agora recebem texto somente quando
o conteúdo mudou, preservando também a seleção e o cursor. Os quatro leitores
de COMMAND CENTER e DECISION AUDIT têm fundo próprio e repintam o conteúdo
inteiro após a rolagem. A verificação nativa observou eventos de pintura e
mudança do primeiro bloco visível, sem executar uma nova decisão para ler.

No RUN PATH, uma execução válida gerava o resultado persistido, mas o rótulo
ficava abaixo da área visível da janela de 570 × 550. O painel agora separa
REQUEST e RESULTS / RECEIPT; uma execução aceita abre o estado pendente e,
depois da confirmação do hash da requisição, mostra o resultado lido do registro.
Os valores, a rota, a versão, o identificador e os hashes completos podem ser
lidos e selecionados dentro da interface. Erros e recusas têm mensagens visíveis.
Durante uma operação, a página continua disponível para leitura e navegação;
os comandos ficam bloqueados até a conclusão, sem desabilitar a página inteira.

O mapa e a faixa de nodes sem localização não desenham linhas para coordenadas
ausentes. A conexão permanece no inventário, nos detalhes abertos ao clicar no
node e nas vistas lógicas. A rota geográfica também se interrompe em um node sem
coordenadas, evitando uma ligação visual direta entre os vizinhos desse node.
Declarar a localização faz a conexão aparecer no mapa; removê-la oculta a linha.
O crédito cartográfico passou de 11 para 9 pixels lógicos, preservando a atribuição.

Os botões recebem descrições da ação; a fonte indisponível e uma operação em
andamento acrescentam o motivo do bloqueio. As descrições incluem navegação,
simulação, auditoria, modelos opcionais, exportações, controles do mapa, abas e
controles internos do Qt. Os campos de RUN PATH descrevem suas unidades e premissas.

## Casos de uso

| Caso | Execução e resultado observado | Evidência |
|---|---|---|
| DECISION AUDIT: explicação | A roda do mouse muda o texto visível; o viewport repinta; 12 atualizações e o watchdog preservam a posição. Aprovado. | `source/report.json`, `native/report.json`, `reading-final.xml` |
| DECISION AUDIT: recibo completo | JSON extenso pode ser lido no painel; nenhuma decisão é reexecutada para a leitura. Aprovado. | `native/decision-receipt.png`, `reading-final.xml` |
| COMMAND CENTER: recibo original | Selecionar novamente o mesmo comando preserva a posição e a revisão do documento. Aprovado. | `reading-final.xml`, `native/report.json` |
| COMMAND CENTER: evidência observada | A comparação permanece legível durante atualizações; o recibo original fica preservado. Aprovado. | `reading-final.xml`, `native/command-center.png` |
| Seleção de texto durante atualização | Seleção, cursor e posição sobrevivem a 12 renderizações e à chegada de outro comando. Aprovado. | `reading-final.xml` |
| Node conectado sem localização | N-13 aparece na faixa sem linha para N-05; ao clicar, os detalhes mostram N05-N13. Aprovado. | `native/report.json`, testes de topologia |
| Node sem conexão e sem localização | N-12 aparece na faixa, sem linha ou conexão inventada. Aprovado. | Testes de topologia |
| Rota passando por localização ausente | Nenhuma linha geográfica atravessa o node desconhecido; as duas conexões continuam no modelo. Aprovado. | `reading-final.xml` |
| Localização declarada ou removida | A posição passa a controlar a visibilidade geográfica, preservando a fibra no backend. Aprovado. | `ui-final.xml` |
| Crédito cartográfico | Texto menor; os créditos IBGE e OpenStreetMap permanecem no mapa. Aprovado. | Teste de atribuição em `ui-final.xml` e revisão visual |
| Hover na interface | 200 botões com descrição; popup exibido na fonte e no Windows nativo. Aprovado. | `source/report.json`, `native/report.json` |
| Temas claro e escuro | Inventário dos botões sem descrições vazias nos dois temas. Aprovado. | `reading-final.xml` |
| Ação dinâmica Isolate / Restore | A descrição acompanha a alteração da ação e preserva descrições específicas. Aprovado. | `reading-final.xml` |
| RUN PATH ideal: meta atendida | Resultado confirmado, fidelidade e recibo visíveis; valores coincidem com o registro persistido. Aprovado. | `native/ideal_target_met.png`, `native/report.json` |
| RUN PATH ideal: meta não atendida | Mostra NOT MET sem transformar o resultado em sucesso. Aprovado. | `native/ideal_target_not_met.png` |
| RUN PATH temporal: aquisição concluída | Mostra resultado temporal, idade da memória e contabilidade dos pares. Aprovado. | `native/temporal_success.png` |
| RUN PATH temporal: timeout | Mostra ACQUISITION_TIMEOUT e ausência de fidelidade quando não houve swap. Aprovado. | `native/temporal_timeout.png` |
| RUN PATH temporal: expiração | Mostra timeout e os pares expirados, sem inferir uma taxa a partir de um ensaio. Aprovado. | `native/temporal_expiry.png` |
| Dados inválidos | NaN, infinito, valor negativo, valor acima de 1 e campo vazio geram REFUSED, sem despacho. Aprovado. | `reading-final.xml` |
| Erro do backend | A aba de resultados mostra UNCONFIRMED e o motivo; a requisição não é reenviada automaticamente. Aprovado. | `reading-final.xml` |
| Nova requisição e resultado anterior | Recarregar gera novo ID e mantém o resultado anterior identificado como histórico. Aprovado. | `reading-final.xml` |
| Resultado em 570 × 550 e 960 × 650 | Área de resultados e seus limites ficam dentro da janela nos dois tamanhos. Aprovado. | `reading-final.xml` |
| Operação em andamento | Comandos bloqueados; mapa, leitura e seletores de vista continuam habilitados; a página não alterna seu estado de habilitação. Aprovado. | `native/report.json`, `reading-final.xml` |
| Troca de fonte, fechamento e resposta atrasada | Contexto anterior descartado; nenhuma resposta antiga repovoa o painel. Aprovado. | Testes de auditoria e reconciliação em `reading-final.xml` |
| Modelos opcionais e execução física | Pacotes continuam opcionais; execução física permanece indisponível. Aprovado na fonte e no executável, com e sem os ZIPs originais. | `native/report.json`, `optional-models-original-validation/report.json` |
| Interface do executável empacotado | Autoteste inclui as quatro áreas de leitura, hover e os cinco resultados de RUN PATH. Aprovado. | `self-test.json`, `usage-feedback/report.json` no diretório da distribuição |
| Integridade e aceitação automática Windows | SHA256 dos arquivos e CRC do ZIP conferidos; autoteste e stress aprovados. Aprovado no escopo de software local. | `portable-package.json`, `native-acceptance/acceptance-report.json` |

## Pacote entregue e resultados consolidados

Distribuição: `release/windows/5.38.0-native-20261005-125712`.

| Verificação | Resultado |
|---|---|
| Regressão ampliada: telas, topologia real, DPI, modelos de fonte, memória e barreira física | 178 testes aprovados em `ui-final.xml` |
| Leitura, reconciliação, auditoria e feedback de uso | 55 testes aprovados em `reading-final.xml` |
| Testes distintos nas duas suítes finais | 219 aprovados; 14 casos aparecem nas duas suítes |
| Fonte e renderizador nativo Windows | 35 verificações aprovadas em cada execução |
| Autoteste do executável | 27 verificações principais aprovadas; inclui 35 verificações dos fluxos corrigidos |
| Responsividade do executável | 251 verificações aprovadas; diálogos e topologia funcional aprovados |
| Campanha temporal em worker do executável | Aprovada; recibos, cancelamento, isolamento e paridade numérica verificados |
| Modelos opcionais | Aprovados sem pacotes e com os arquivos originais `F:\qnet_network.zip` e `modelo_qkd_ml.zip` |
| Stress do controlador | 20 rodadas aprovadas, sem atuação física |
| Aceitação automática Windows | `automatic_passed: true`; revisão humana e outro computador permanecem pendentes |
| ZIP portátil | 397.922.875 bytes; 3.892 arquivos; CRC aprovado |

Executável: `ORION-Q/ORION-Q.exe`. Extraia o ZIP inteiro e mantenha a pasta
`_internal` junto do executável. Os pacotes anteriores não foram substituídos.

SHA256 do EXE:
`177cbc3732b66bf6cd27653dfba7bb56e563a964d687557b0a60382ba55f16ee`.

SHA256 de `ORION-Q-5.38.0-native-windows-x64.zip`:
`4cddc527e8be2f47659d3382cad1329cbb0355caff0feaee11fe84c024779c4e`.

## Evidência e limites

A pasta de auditoria é `outputs/ui_usage_fixes_20261005_v1`. Ela contém cópias e
hashes de 177 arquivos recebidos antes de editá-los, a reprodução, os XMLs e
capturas dos fluxos. `reading-final.xml` registra 55 testes aprovados. As sondas
de fonte e do Windows nativo registram 35 verificações aprovadas cada uma.

A sonda nativa usa o backend `windows` do Qt e janelas próprias transparentes,
sem interagir com outras aplicações. As execuções são simulações em diretórios
isolados. Isso verifica a renderização local e os fluxos automatizados; não
substitui uma avaliação humana em outro computador, nem certifica hardware.
As campanhas científicas anteriores e as alterações recebidas foram preservadas.
O diagnóstico do controlador verificou 20 ciclos funcionais, mas os 20 excederam
o orçamento de 10 ms: média de 159,14 ms e máximo de 174,79 ms, conforme
`roadmap-validation/report.json`. Esse resultado não certifica controle em tempo real;
20 amostras também não são suficientes para caracterizar p99 de forma robusta.

![Resultado visível do RUN PATH](../../outputs/ui_usage_fixes_20261005_v1/native/ideal_target_met.png)

![Leitura do recibo no DECISION AUDIT](../../outputs/ui_usage_fixes_20261005_v1/native/decision-receipt.png)
