# Texto do TCC — versão para a pré-banca (22/10/2026)

Versão 5, de 30/09/2026. Acrescenta à versão 4 os laços fechados de todos os modelos
admitidos, rodando sobre as plantas do autor incorporadas ao repositório, com portões que
regeneram os resultados publicados antes de pontuar; o detector admitido por exportação
segura e avaliado em sessões novas; o Verilog do autor simulado e sintetizado; e o
compensador em ponto fixo nos dois pontos de operação. Formato de artigo SBC; 43 páginas.

## Como compilar

- **Overleaf:** Novo projeto → Upload Project → enviar o `.zip`. Compilador pdfLaTeX; o BibTeX
  roda automaticamente.
- **Template oficial da SBC:** `sbc-template.sty` e `sbc.bst` são réplicas locais. Se o projeto
  usar o template oficial, apague os dois; `main.tex` usa apenas os comandos do oficial.

## Pendências

Nenhuma marcada em vermelho. O endereço do qnet_autopilot foi resolvido: as duas versões
estão no próprio repositório (Disponibilidade).

## Novidades da versão 5

- Resumo e Abstract: os cinco componentes em laço fechado na planta de treino, com os
  resultados em sementes novas (inclusive os dois que não se confirmaram por inteiro: o ator a
  80 km e o roteamento no prazo nominal).
- Seção 9.4: o modo shadow agora usa as plantas do autor, conduzidas pelo controlador vigente.
- Seção 10.3: protocolo comum dos laços (plantas incorporadas, contrato, só observáveis,
  supervisor com reserva, portões de reprodução, protocolo congelado) e o de cada componente.
- Seção 11: ponto de desenvolvimento e atuador limitado (Tabela 11), portões de reprodução
  (Tabela 12), taxa de chave e sincronismo (Tabela 13), alocador (Tabela 14) e roteamento
  (Tabela 15).
- Discussão, limitações, conclusão e trabalhos futuros atualizados; Disponibilidade aponta o
  qnet_autopilot no repositório.
- Detector: deixou de ser bloqueado. Exportação sem *pickle* provada idêntica ao pacote
  (Seção 9.3, Tabela 9); portões e avaliação em sessões de fluxos aleatórios novos
  (Seções 10.3 e 11.8, Tabela 16); limitação e conclusão atualizadas. Com as variáveis que
  um monitor em tempo real calcularia (a taxa de temperatura do autor usa o período
  seguinte), o detector perde só 0,15 a 0,21 ponto (Seções 10.3 e 11.8).
- Verilog do autor em simulador HDL e síntese (Seção 11.11, Tabela 19); o parágrafo do porte
  para ponto fixo (Seção 7.1) e a limitação de *hardware* foram atualizados.
- Compensador em ponto fixo nos dois pontos de operação, com a varredura do autor regenerada
  (Seções 10.3 e 11.9, Tabela 17); limitação "Pontos de operação" atualizada.
- Controlador de blocagem do autor no laço do ORION-Q, nos dois pontos (Seções 7.4, 10.3 e
  11.10, Tabela 18): 10,9× e 7,9× o melhor temporizador com persistência; 99,8–100,0% com os
  compensadores aprendidos; cerca de 2% abaixo com o alfa-beta. Limitação "Pontos de
  operação", conclusão e trabalhos futuros atualizados.
- Seção 9.6: 3.724 testes (3.721 passam em Linux; 3 são próprios do Windows), contagem de 01/10
  na nuvem, e o registro verificável das afirmações da plataforma (20 afirmações, 795 checagens).
- Seção 9.6: repetição das campanhas no Windows com os arquivos originais (conclusões
  idênticas nas doze campanhas; onze idênticas em cada número; detector e taxa de chave
  com portões na tolerância do protocolo).
- Roteamento com o SeQUeNCe no laço (Seções 10.3 e 11.7, coluna nova da Tabela 15): o gêmeo
  prevê o SeQUeNCe, e lá o agente fica abaixo da heurística nos dois prazos; Resumo, Abstract,
  limitação "Roteamento", conclusão e trabalhos futuros atualizados.

## Novidades da versão 4

- Compensador em laço fechado supervisionado e ativação pelo fluxo Q05 (Tabela 10).

## Como os manuscritos foram incorporados

- Não são mais citados; o conteúdo virou parte do texto:
  - física de estados-isca e chave finita: Seções 3.2 e 3.3;
  - trabalhos relacionados: Seção 4;
  - substrato físico, modelo de fibra, ataques e regras de avaliação: Seção 6;
  - controladores de rede: Seção 7;
  - controladores de QKD e validação com os dados de Cambridge: Seção 8;
  - limitações dos dois trabalhos: Seção 12.
- Mantidas com os números originais: Tabelas 1 e 2 do qnet_autopilot, Tabelas 1, 3, 7 e 8 do
  artigo de QKD, e as Figuras 5 (qnet) e 1, 3 e 6 (QKD), recortadas dos PDFs.
- Resumidos ou omitidos: a arquitetura de software do pacote; o compensador convolucional (TCN),
  que empatou com a rede densa; parte dos detalhes do RTL e do porte para ponto fixo; e as
  Figuras 1 a 4, 6 e 7 do qnet_autopilot e 2, 4 e 5 do artigo de QKD.
- Conferi mecanicamente que todos os números das Seções 6 a 8 aparecem nos manuscritos.
- A referência Shi et al. (2026) usa os autores corretos ("Shi, Y., Su, J."), conferidos no arXiv.
  O artigo de QKD grafava "Shi, S., Su, Y.".

## Referências

- **Removidas por não terem sido encontradas:** Cao, Yehia e Tura (2023); Chen, Li e Vincent (2025);
  Dietsche, Dür e Dunjko (2022); Peters, Rosas-Fernandez e Spiller (2023); Wang, Zhang e Xu (2025);
  Zhuang e Zhang (2024).
- **Corrigida:** Krutyanskiy et al. (2023), que tinha a lista de autores de outro artigo do grupo.
- **Todas as entradas atuais** foram conferidas por título, periódico e ano. Antes da versão final,
  exporte o BibTeX pelo DOI (Google Scholar ou doi.org) das entradas com "et al." na lista de autores:
  Patel, Xavier, Thomas, Coopmans, Gavignet, Javadi-Abhari e Paszke.
- Os conjuntos de dados (Sant'Anna, IrelandQCI e Bristol) estão em notas de rodapé com URL/DOI.

## Novidades da versão 3

- Título: "Controladores Aprendidos e uma Plataforma de Controle Auditável".
- Pergunta de pesquisa, objetivos, contribuições, resumos, discussão e conclusão tratam os
  controladores como resultado do próprio TCC.
- 34 referências novas, herdadas dos manuscritos e conferidas (66 no total).

## Novidades da versão 2

- Nova Seção 6, "Controladores Aprendidos": método comum e tabela com os resultados dos
  manuscritos, inclusive os negativos.
- Título do objetivo, pergunta de pesquisa, contribuições, Abstract, Resumo e Conclusão
  incluem os modelos como contribuição científica.
- Corrigido: os modelos foram treinados sobre uma transcrição do SeQUeNCe conferida contra
  ele, e não no próprio SeQUeNCe.
- A Discussão explica por que o MLP falhou no ORION-Q e empata com o Ridge no manuscrito: o
  produtor artificial usa vibração de ~0,003 rad RMS, sem ruído de fótons, contra 0,3 rad RMS
  no treino.
- Referências novas: Dynes et al. (2019), Tomamichel et al. (2012) e Wang e Lo (2019).

## Correções em relação à versão 14

- Objetivo e título alinhados ao foco atual: plataforma de controle com modelos acoplados.
- O ruído de Ornstein-Uhlenbeck agora é descrito como markoviano; antes o texto o chamava de "não markoviano".
- A RUC deixou de aparecer como resultado: era definida, mas nunca estimada.
- Figuras 2, 3, 5, 6 e 7 da versão 14 foram removidas. As legendas não correspondiam aos gráficos,
  e a Figura 7 contradizia o texto.
- A figura multi-salto foi reinterpretada: a taxa de sucesso é condicionada à admissão, não mede vazão.
- O ranking por custo físico agora vem com a ressalva de que depende do peso α = 100.
- A Fase 2 deixou de ser descrita como trabalho futuro.
- Resultados do estudo preliminar alinhados ao repositório: protocolo congelado v5.26, 0 de 3
  comparações neurais significativas contra persistência.
- Nova seção de arquitetura, nova seção de modelos, campanhas shadow (figura nova com os dados
  dos relatórios de 23/09) e conclusão, que não existia.

## Conferir

- Se os coautores concordam com o novo enfoque.
- As Figuras 1 e 2 foram extraídas do PDF antigo, com texto em inglês e resolução limitada.
  Se possível, regenere-as pelos scripts do repositório.
