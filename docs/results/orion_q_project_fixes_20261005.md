# ORION-Q — correções da revisão de 05/10/2026

As correções preservam a árvore de trabalho recebida, os dados científicos, os ZIPs
originais e os pesos dos modelos. Todos os cenários usam journals isolados e execução
local em simulação. Não há atuação física nem ativação de política operacional.

| Caso de uso | Falha reproduzida | Comportamento corrigido | Verificação |
|---|---|---|---|
| Remover um ZIP durante uma sessão | A referência era anunciada como atuante enquanto o modelo aprendido continuava executando | O estado mantém a identidade e o hash dos pesos em uso e explica a retenção até o próximo comissionamento | Compensação, sincronização e taxa, remoção e rejeição de pacote |
| Comissionar novamente após remover o ZIP | A atribuição do controlador anterior permanecia no registro | O reset libera as atribuições anteriores; o novo período adota a referência | Contadores, estados e hashes antes/depois do reset |
| Isolar/restaurar um extremo com par armazenado | O par podia ser consumido depois do reset da memória | O contador persistente de memória invalida o estoque; fidelidade e demais resultados ficam preservados como histórico | Ambos os extremos, comandos atuais e ações legadas |
| Recalibrar um extremo | O par antigo permanecia consumível após a recalibração completa | O reset da memória invalida o par e impede consumo/purificação | Ambos os extremos e reinício do motor |
| Outra instância resetar e restaurar entre polls | O estado final restaurado escondia a perda da memória | A geração de memória denuncia o reset mesmo com o nó restaurado | Alteração externa, motor parado e novo motor sobre o mesmo journal |
| Reset entre a validação e a gravação do par | A operação poderia aceitar memória já descartada | O contador é conferido dentro da transação SQLite que grava o par | Solicitação, consumo e purificação |
| Reset de nó que não é extremo do par entregue | Um reset alheio não deve apagar estoque dos extremos | O par armazenado continua válido | Preservação de estoque e consumo |
| Abrir estoque legado sem vínculo com a memória | Não há evidência suficiente de que o par ainda existe | O registro fica no histórico como invalidado; uma nova solicitação cria estoque válido | Histórico preservado, consumo recusado |
| Exibir um par invalidado na interface | A indicação de recurso e fidelidade podia induzir uso do estoque perdido | Status invalidado, fidelidade histórica, motivo acessível e operações indisponíveis | Integração da interface com o estado do motor |
| Declarar coordenadas antipodais | Divisão por zero após persistir uma fibra de aproximadamente 20.015 km; reinício também falhava | A demanda fica bloqueada com motivo explícito, sem probabilidade inventada ou prazo infinito | Comando, alteração externa, serialização e reinício |
| Fibra inviável em ramo que não é necessário ao destino | Uma probabilidade zero não deve impedir outra rota viável | A planta exclui a fibra inviável e mantém as alternativas | Episódio entregue na rota viável |
| Verificar pacotes vendorizados | Quatro adaptações de compatibilidade não estavam registradas | Hashes dos autores preservados; bytes de base, patch e hashes das adaptações registrados separadamente | Integridade de todos os arquivos, reconstrução do diff e coexistência com SymPy/Qiskit |
| Repetir diagnósticos no mesmo diretório | Evidências anteriores devem ser preservadas | Cada execução cria seu próprio workspace | Dois diagnósticos consecutivos com journals distintos |
| Abrir/atualizar a janela flutuante de um nó | Na primeira renderização após inserir as linhas, a altura mínima desatualizada comprimia o conteúdo sobre o gráfico | A altura mínima acompanha o conteúdo; as linhas permanecem legíveis e a última conexão é acessível por rolagem | 12 cenários: temas claro/escuro, três tamanhos e 4/24 conexões; transição de uma para várias conexões |
| Executar a nova distribuição Windows | O executável anterior não continha o motor e o mapa atuais | O pacote inclui o motor, o mapa GEO, os recursos e os novos diagnósticos | Inspeção do pacote congelado, self-test, interface responsiva e aceite automático local |
| Executar o binário com e sem os ZIPs originais | O fallback e a retenção de modelos precisam funcionar no ambiente distribuído | Ambos os modos passam; modelos permanecem opcionais | Executável em diretório isolado e PATH restrito, com os hashes originais verificados |

O teste direcionado anterior às correções registrou **20 falhas e 1 aprovação**.
Após corrigir os casos iniciais e ampliar a cobertura para gravações concorrentes,
os **36 testes direcionados passaram**. O diagnóstico adicional de memória/distância
também passou. A validação com os dois ZIPs originais confirmou os modelos ativos,
a retenção correta de identidade/hash e a adoção da referência após comissionamento.

Os testes adicionados constam do script de build Windows. O `--self-test` do próprio
executável verifica os resets, recusas de consumo, persistência e probabilidade zero.
O `--optional-model-test` com os dois ZIPs explícitos verifica também a retenção dos
controladores numa fibra simulada de 50 km. Nenhum desses testes abre journals do operador.

A evidência desta execução está em `outputs/project_fixes_20261005_v1`, incluindo os
arquivos anteriores às mudanças, hashes, resultados JUnit e o log do build.

## Verificações adicionais no Windows

- A jornada pela interface solicita um par, isola/restaura o extremo, verifica o
  estado invalidado e a fidelidade histórica, solicita outro par e o consome. O caso
  passou com a janela real sobre o serviço de demonstração, usando cliques nos widgets.
- Os testes de assimetria com dez sementes, regressão física e exportação do detector
  com o ZIP original passaram: **15 casos**. Os CSVs do detector coincidiram byte a byte.
- O ambiente principal do build não contém o SDK opcional LabOne Q. O ambiente
  existente `work/laboneq-env`, Python 3.12.14 e LabOne Q 26.7.0, foi usado para
  verificar a ponte: **22 casos aprovados**, incluindo compilação e aquisição emulada.
  A emulação declarou `physical_action_performed: false`. Nenhuma dependência foi instalada.
- A geração dos catálogos do build manteve inalterados os dois arquivos JSON de
  evidência recebidos. Os hashes de origem de todos os arquivos vendorizados foram
  comparados com o manifesto recebido e preservados.

## Resultado consolidado

Os **2.852 casos distintos** terminaram aprovados, sem falhas ou casos de teste pendentes.
As execuções consolidadas registram 2.978 observações: 2.974 aprovações e quatro
skips iniciais, todos substituídos por aprovações nas verificações complementares.
As repetições de um mesmo caso não aumentam o total de casos distintos.

| Grupo obrigatório do build | Aprovados | Skips iniciais |
|---|---:|---:|
| Roadmap | 58 | 1 |
| Integração da console | 68 | 0 |
| Telas, campanhas, histórico e worker | 491 | 0 |
| Rede, topologia, regressões e integridade | 523 | 0 |
| Comandos e políticas | 647 | 0 |
| Modelos opcionais e detector | 367 | 3 |
| Sincronização shadow | 87 | 0 |
| Compensação e demais shadows | 580 | 0 |
| **Total do build** | **2.821** | **4** |

As verificações complementares acrescentam duas aprovações da jornada de memória e
do diagnóstico repetido, 15 de assimetria/física/detector, 22 da ponte LabOne Q e
duas de sessões do detector com o ZIP original. Após a inspeção das imagens, os
12 cenários da primeira renderização reproduziram a sobreposição e passaram após
o ajuste de layout. A verificação final da interface aprovou **112 casos**, incluindo
esses 12 novos casos, os controles, a memória, a topologia, o editor de fibras e o
polling. Os oito grupos da primeira compilação permanecem como evidência dos demais
componentes; o ajuste final foi restrito ao painel e à sua validação gráfica.
O conjunto completo e os resultados
do binário constam de
[final-validation.json](E:/Codex/2026-08-27/e/work/NISQ-Predictive-Twin/outputs/project_fixes_20261005_v1/final-validation.json).

O executável passou nos 26 checks principais do `--self-test`, incluindo nove
checks de memória/distância, topologia funcional, demonstração e matriz de interface
responsiva. Também passaram o aceite automático local, o stress de 20 rodadas,
os diagnósticos de roadmap e os 25 checks de campanha/recibos do worker.
O teste do binário com PATH restrito passou com e sem os pacotes originais; a retenção
dos três controladores e a troca para a referência após novo comissionamento foram
verificadas no próprio executável.

## Nova distribuição Windows

Build **5.38.0-native-20261005-091928**, gerado com o Python e as dependências já
existentes, sem instalação de dependências ou geração de instalador.

- [ORION-Q.exe](E:/Codex/2026-08-27/e/work/NISQ-Predictive-Twin/release/windows/5.38.0-native-20261005-091928/ORION-Q/ORION-Q.exe)
- [Pacote portátil completo](E:/Codex/2026-08-27/e/work/NISQ-Predictive-Twin/release/windows/5.38.0-native-20261005-091928/ORION-Q-5.38.0-native-windows-x64.zip)

O ZIP tem 397.894.869 bytes e 3.892 arquivos; a verificação CRC passou. Para mover
ou distribuir o programa, extraia o ZIP completo e execute `ORION-Q/ORION-Q.exe`,
preservando a pasta `_internal` e os demais arquivos do pacote. As distribuições
anteriores foram preservadas. Os ZIPs dos modelos continuam externos e opcionais.

| Artefato | SHA-256 |
|---|---|
| Executável | `fee90cd1beb72f2592ba37ac38d137171f433c15255e95b5d2d9d0d796e41d58` |
| Pacote portátil | `6dcd357705fedfacaeb19146274bd447aaa2dd89e87c370ef93acf45f2945c4e` |
| Qnet original | `6b8563209f54a2d566f1a4229b140b75fda93555085ff2bf6bfcad8f07abd938` |
| QKD original | `94352f15b6dcb132a3c03559b9d94b28f42b6e9adb8b2cdaab18f46720a55a79` |

## Limites da validação

O aceite automático confirma as verificações locais de software. O relatório de
aceite mantém `manual_review_required`, com revisão manual pendente e
`independent_machine_verified: false`. Não foi feita validação em outra máquina
nem demonstração física; as jornadas gráficas foram automatizadas e os dispositivos
permaneceram simulados/emulados.

**A meta de 10 ms do runtime permanece pendente.** As 20 amostras do diagnóstico
tiveram média de 159,15 ms e máximo de 167,35 ms; todas excederam o orçamento, embora
nenhuma tentativa tenha falhado. O p99 informado é apenas descritivo: 20 amostras
não são suficientes para qualificá-lo. O resultado aprovado do validador verifica
seus critérios funcionais e de evidência; não certifica o cumprimento dos 10 ms.
Nenhum orçamento ou critério científico foi relaxado para obter aprovação.
