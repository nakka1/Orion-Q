# Estratégia até a pré-banca — ORION-Q

29/09/2026. Cópia versionada do documento de estratégia; complementa o
[roadmap da pré-banca](roadmap_prebanca_20261022.md) e o [roadmap geral](roadmap.md).

**Atualização de escopo em 07/10/2026:** a integração IBM Quantum foi
descontinuada, com IBM-2C cancelado e retirado das pendências. O
[arquivo histórico](../../archives/retired_ibm_quantum_20261007/ibm_quantum_source.zip) conserva os registros anteriores.
Qiskit/Aer continua na simulação local; os resultados registrados abaixo
preservam suas datas e condições.

## Onde estamos

**Atualização de 01/10.** O .exe 5.38.0 foi gerado no Windows e passou no self-test e
no teste dos modelos com os ZIPs originais. As campanhas oficiais no Windows reproduziram
as conclusões das doze campanhas e cada número em onze (6.698 valores); a exceção é uma
célula do atuador limitado (1,6×10⁻⁵). Detector e taxa de chave tinham parado em portões
que exigiam igualdade de bytes de valores simulados (último bit da libm); os portões
passaram a usar a tolerância do protocolo, sem mudar nada no Linux. A semana 1 está
fechada, salvo o push.

**Atualização de 30/09.** O detector deixou de ser bloqueado (exportação sem *pickle*
provada idêntica; avaliado em sessões novas), o Verilog do autor foi simulado e
sintetizado, o compensador em ponto fixo foi validado nos dois pontos, e as afirmações da
plataforma — inclusive a de segurança da ativação (C-SEGURANCA) — estão num registro
verificável (20 afirmações, 795 checagens). O detector também foi avaliado com as
variáveis que um monitor em tempo real calcularia (a taxa de temperatura do autor usa o
período seguinte): perde só 0,2 ponto. E o SeQUeNCe entrou no laço do roteamento: o
gêmeo analítico prevê o SeQUeNCe, e lá o agente fica abaixo da heurística nos dois
prazos — a limitação "SeQUeNCe fora do laço" saiu do texto. Para reduzir o risco do .exe novo, o
console foi empacotado com o mesmo `packaging/console.spec` em Linux (PyInstaller 6.22):
o *self-test* do executável congelado passou em 19 de 19 checagens, com as plantas do
autor alimentando os painéis, e o teste dos modelos opcionais em 11 de 11. O .exe de
Windows continua sendo do seu lado. Os textos dos painéis *shadow* de roteamento, taxa de
chave e alocação ainda falavam em "fixture ilustrativa"; agora nomeiam a planta do autor.
Por fim, o controlador de blocagem do autor entrou no laço da polarização nos dois pontos:
10,9× e 7,9× o melhor temporizador no enlace da persistência e 99,8–100,0% dele com os
compensadores aprendidos, depois de regenerar as tabelas de blocagem do autor. O texto v5 (3.724 testes) e os
slides v1 estão prontos para revisão. Para a semana 1 no Windows há um comando único,
`tools/run_official_campaigns.ps1`, que roda as campanhas com os ZIPs fixados e compara
cada número com a nuvem (10⁻⁹). O que resta é do seu lado: push, campanhas oficiais,
.exe novo e envio da v5 ao orientador.

**Atualização de 29/09 (noite).** A pedido do autor, o produtor artificial do ORION-Q foi
substituído pelas plantas dos próprios modelos, e os cinco modelos passaram a ter laço
fechado sobre a planta de treino, cada um com portões que regeneram os resultados
publicados antes de pontuar em sementes novas. Isso tirou da lista "Fica para depois" o
atuador limitado, o ponto de desenvolvimento, o alocador e o roteamento em laço fechado,
e resolveu o endereço do qnet_autopilot (agora incorporado ao repositório).

| Frente | Estado em 29/09 | O que falta |
| --- | --- | --- |
| Controladores (qnet_autopilot) | As duas versões incorporadas (`src/qnet_network`, `src/qnet_qkd`), suítes do autor passando | — |
| Compensador em laço fechado | Dois pontos: 3,33× e 4,75× abaixo do alfa-beta, 20/20 sementes; atuador limitado do autor nos dois pontos | Execução oficial no Windows com o ZIP fixado |
| Taxa de chave e sincronismo em laço fechado | Tabelas publicadas regeneradas; ator > comissionamento de 25 a 65 km, não a 80 km; Ridge > alfa-beta em todas as execuções | — |
| Alocador e roteamento em laço fechado | Portões aprovados; resultados nos documentos de 29/09 | Execução oficial com o ZIP fixado |
| Ativação Q05 do compensador | Pronta por API, linha de comando e painel no console | — |
| Painéis shadow | Alimentados pelas plantas do autor; detector com sessão offline | .exe novo |
| Executável Windows | Último gerado em 23/09 | Gerar com o código atual |
| Texto do TCC | v5 pronta (43 páginas; detector, ponto fixo, Verilog) em `docs/thesis/monografia` | Revisão do orientador |
| Git | Branch `roadmap-prebanca` com commits locais | Push (você) |

No backlog geral (Q01–Q30), a Etapa A está quase fechada (faltam Q06 replay e Q07 alarmes), a B está parcial e a C tem Q16 parcial. Adaptadores físicos e a hipótese WDM medida seguem abertos e não pesam na pré-banca. IBM-2C foi cancelado em 07/10/2026.

## O que a banca vai avaliar e onde estão os riscos

A pré-banca julga se problema, método e resultados parciais sustentam o TCC e se ele termina a tempo. Nisso o trabalho está forte; o maior risco agora é operacional: um único disco e um .exe que ainda não foi gerado com o que será mostrado.

| Risco | Chance (estimativa) | Efeito | Mitigação | Prazo |
| --- | --- | --- | --- | --- |
| Perder o trabalho (branch sem push) | Baixa | Total | Push hoje | 29/09 |
| O .exe quebra com seis dias de incrementos | Média | Demonstração cai | Gerar o .exe já com o código atual, antes do painel, e depois a cada incremento | 04/10 |
| A banca pede o código dos controladores (Seções 6–8) | Alta | Credibilidade dos resultados principais | Versionar o qnet_autopilot e citar o commit no texto | 04/10 |
| Números mudam no Windows | Baixa | Refazer a Tabela 10 | Rodar a campanha com o ZIP fixado nesta semana | 04/10 |
| O painel atrasa | Média | Demonstração sem ativação visual | A linha de comando já funciona; vídeo gravado como plano B | 11/10 |
| Perguntas sobre "só simulação" e "atuador ideal" | Alta | Questionamento de validade | Já estão nas limitações; preparar um slide de resposta | 18/10 |
| Forma SBC (Resumo e Abstract acima de 10 linhas) | Média | Correção de forma | Encurtar se o orientador confirmar | Com o orientador |
| Revisão do orientador chega tarde | Média | Correções sem tempo de absorver | Enviar a v4 nesta semana | 01/10 |

## Estratégia

Parar de acrescentar ciência e transformar o que já existe em algo que a banca veja funcionar e possa reproduzir. Cinco regras:

1. **Uma história, um laço.** Texto e demonstração giram em torno do compensador, o único modelo em laço fechado e ativável. Os outros aparecem como catálogo e shadow, com os resultados do texto.
2. **Verificar antes de acrescentar.** Reproduzir no Windows o que já existe antes de escrever código novo.
3. **Empacotar cedo e sempre.** Gerar o .exe a cada incremento, não no fim.
4. **Plano B antes do plano A final.** Vídeo da demonstração gravado até 15/10.
5. **Congelar em 18/10.** Depois disso, só correções.

| Entra na pré-banca | Fica para depois |
| --- | --- |
| Painel da polarização no console (laço, ativação, falha, reversão) | ~~Alocador em laço fechado~~ (feito em 29/09) |
| .exe novo e aceite em uma segunda máquina | ~~Roteamento em laço fechado e SeQUeNCe no laço~~ (feitos em 29 e 30/09) |
| qnet_autopilot versionado e citado | ~~Atuador limitado e ponto de desenvolvimento no laço da polarização~~ (feitos em 29/09) |
| Reivindicações C-COMP-LF e C-SEGURANCA no registro verificável | Q06 replay, Q07 alarmes e adaptadores físicos (IBM-2C cancelado) |
| README com objetivo e resultados | Hipótese WDM com dados medidos (o detector foi antecipado: exportação segura concluída em 29/09) |
| Slides, vídeo e dois ensaios | Restaurar ativações após reinício do programa |

## Plano até 22/10

A demonstração fica pronta no .exe em 11/10, o código congela em 18/10 e a última semana é só ensaio. Cada semana tem um critério de pronto; se ele não for atingido, a semana seguinte absorve o atraso cortando da lista "Fica para depois", nunca do ensaio.

Marcos: consolidar no Windows (29/09 a 04/10) · painel no console + .exe (05/10 a 11/10) · texto com o orientador até a v5 (29/09 a 18/10) · aceite e vídeo (12/10 a 15/10) · slides (12/10 a 18/10) · congelamento (18/10) · dois ensaios (19/10 a 21/10) · pré-banca (22/10).

**Semana 1 — 29/09 a 04/10: consolidar.** Pronta quando a campanha no Windows bate com a da nuvem até 10⁻⁹ e o .exe do código atual passa no self-test.

- [ ] Push do branch e PR para `main` (você)
- [x] Rodar no Windows os testes que só valem lá (30/09: 18 testes, sem falhas)
- [x] Campanhas oficiais no Windows (30/09–01/10): as doze com as mesmas conclusões da nuvem e onze com os mesmos números; a exceção é uma célula do atuador ([relatório](../results/orion_q_windows_validation_20261001.md); P-WINDOWS)
- [x] Gerar o .exe com o código atual e rodar o self-test (30/09: `release/windows/5.38.0-native-20260930-1943`; self-test e modelos opcionais com os ZIPs originais aprovados)
- [x] Versionar o qnet_autopilot: incorporado ao repositório (`src/qnet_network`, `src/qnet_qkd`) e citado no texto
- [ ] Enviar a v5 ao orientador (você)

**Semana 2 — 05/10 a 11/10: console.** Pronta quando a demonstração inteira roda a partir do .exe.

- [x] Painel da polarização no EDGE CONTROL: rodar o laço, preparar, revisar, aprovar, ativar, ler, injetar falha, reverter e exportar recibos (eu)
- [x] Testes do painel no padrão dos painéis Q05 existentes (eu)
- [x] Reivindicações C-COMP-LF e C-SEGURANCA no registro verificável (eu; P-COMP-LF e P-SEGURANCA)
- [x] README com objetivo e resultados antes do histórico de versões (eu)
- [ ] .exe com o painel e self-test (você)

**Semana 3 — 12/10 a 18/10: apresentação.** Pronta quando há vídeo, slides v1 e texto v5.

- [ ] Aceite em uma segunda máquina com o roteiro (você)
- [ ] Gravar o vídeo da demonstração até 15/10 (você)
- [x] Slides v1 a partir do texto (rascunho pronto em `docs/thesis/apresentacao`; você ajusta)
- [ ] Texto v5 com as correções do orientador (nós dois)
- [ ] 18/10: congelamento do código

**Semana 4 — 19/10 a 22/10: ensaio.**

- [ ] Tag `v6.0.0-prebanca` e pacote de evidências
- [ ] Dois ensaios cronometrados, com as perguntas prováveis
- [ ] Slides finais
- [ ] 22/10: pré-banca

## Roteiro da demonstração

Seis minutos, tudo a partir do .exe, mostrando um modelo entrando e saindo de controle sob governança. Os valores abaixo são os da demonstração feita na nuvem (semente 2026).

1. Abrir o console com todos os modelos OFF e verificar o hash do pacote de pesos no catálogo.
2. No painel da polarização, rodar o laço com o filtro alfa-beta: QBER 0,045.
3. Tentar ativar o checkpoint de desenvolvimento e mostrar a recusa do contrato.
4. Preparar a ativação do Ridge citando o resumo da campanha; revisar, aprovar e ativar; leitura ACTIVE.
5. Rodar o laço: QBER 0,012 e chave de 81 kbit/s, contra 49 kbit/s do filtro.
6. Injetar a falha: o supervisor recusa as saídas inválidas e passa ao filtro em 16 ms.
7. Reverter pelo mesmo fluxo e rodar de novo.
8. Abrir o diário com as ativações e execuções e fechar com a Tabela 10.

**Plano B, em ordem:** o vídeo gravado até 15/10; a demonstração por linha de comando (`tools/polarisation_control_demo.py`), que já funciona hoje; os recibos exportados de uma execução anterior.

## Depois da pré-banca

Até a banca final, a ordem que mais fortalece o TCC é aprofundar o laço que já existe antes de abrir outro:

1. ~~Atuador limitado no laço da polarização e ponto de desenvolvimento~~ (29/09).
2. ~~Alocador em laço fechado na planta do próprio pacote~~ (29/09).
3. ~~Roteamento em laço fechado, inclusive com o SeQUeNCe como planta~~ (29 e 30/09).
4. Compensador retreinado com o atuador limitado no laço; detector retreinado com a taxa de temperatura unilateral.
5. Restauração de ativações após reinício e integração com o fluxo Q05 do repetidor.
6. Se surgirem dados pareados medidos, a hipótese WDM (a exportação segura do detector já foi feita em 29/09).

## Decisões que dependem de você

Cinco decisões destravam a semana 1; ao lado de cada uma, a recomendação.

- [ ] Onde versionar o qnet_autopilot: repositório próprio no GitHub, citado no texto pelo commit (recomendado), ou uma pasta dentro do NISQ-Predictive-Twin.
- [ ] Cortar o alocador em laço fechado da pré-banca: recomendo cortar.
- [ ] Encurtar Resumo e Abstract para cerca de 10 linhas, se o orientador confirmar a regra do SBC.
- [ ] Confirmar com os coautores o novo enfoque do texto (pendente desde a v3).
- [ ] Fazer o merge do branch em `main` depois do push.
