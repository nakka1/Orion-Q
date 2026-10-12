# Texto do TCC — versão para a pré-banca (22/10/2026)

Versão 7, de 11/10/2026. Reestrutura o texto com o OrionQ como protagonista: título novo
("OrionQ: uma Plataforma para Simulação e Controle Auditável de Redes Quânticas"), seção nova
sobre as tecnologias que a plataforma simula e controla, e os controladores aprendidos
condensados como componentes integrados. Nenhum resultado científico mudou. Formato de artigo
SBC; 49 páginas. A versão 6 continua em `TCC_prebanca_v6.pdf`.

## Como compilar

- **Overleaf:** Novo projeto → Upload Project → enviar o `.zip`. Compilador pdfLaTeX; o BibTeX
  roda automaticamente.
- **Template oficial da SBC:** `sbc-template.sty` e `sbc.bst` são réplicas locais. Se o projeto
  usar o template oficial, apague os dois; `main.tex` usa apenas os comandos do oficial.

## Pendências

Nenhuma marcada em vermelho. O endereço do qnet_autopilot foi resolvido: as duas versões
estão no próprio repositório (Disponibilidade).

## Novidades da versão 7

- Título, Resumo e Abstract centrados no OrionQ: arquitetura, tecnologias suportadas,
  controladores como componentes opcionais e o controle demonstrado em simulação (laço fechado
  nas plantas de treino; SeQUeNCe como planta e executor da parte terrestre; admissão e
  disponibilidade nos enlaces de campo gêmeo e de satélite).
- Introdução, pergunta de pesquisa, contribuições e objetivos reescritos em torno da plataforma.
- Fundamentação: subseções novas sobre QKD de campo gêmeo e o limite sem repetidores (PLOB) e
  sobre enlaces ópticos de satélite, com os números do Micius conferidos nos artigos (Yin 2017,
  Liao 2017, Lu et al. 2022). O "−75 dB" é a estimativa de Lu et al. para duas descidas a
  1.000 km; o medido por Yin et al. foi 64–82 dB.
- Trabalhos relacionados: pilhas e plataformas de controle de redes quânticas (Dahlberg 2019,
  Pompili 2022, Delle Donne 2025, RFC 9340, ETSI GS QKD 014/015) e comunicação por satélite
  (Micius, Han 2018, Wang 2021, Cheng 2025, Li 2025, Miller 2025 como pré-print).
- Seção 6 (Arquitetura do OrionQ): requisitos R1–R7 e "Motor de rede e operação" com figura nova.
- Seção 7 (nova, Tecnologias Suportadas pelo OrionQ): enlaces de fibra com QKD de estados-isca;
  repetidores (gêmeo do autor, física de referência e SeQUeNCe); QKD de campo gêmeo; BBM92,
  bancada emulada e entrega de chaves; satélites e enlaces entre satélites (com tabela de
  comparação com o Micius); canais clássicos e telemetria de satélite. Cada uma com equações,
  parâmetros, o que o OrionQ decide e estado de validação.
- Seção 8 (Controladores Integrados ao OrionQ): as antigas Seções 6 a 8 condensadas numa tabela
  e um parágrafo por controlador, com a validação de Cambridge.
- Seção 10: resultados organizados por capacidade, com subseções novas "Controle de redes
  executadas pelo SeQUeNCe" e "Desempenho e maturidade".
- Discussão e Conclusão abrem pela plataforma.
- Suomi 100 (Seções 7.5 e 7.6): o texto diz exatamente o que foi feito. Dois quadros reais
  recebidos por uma estação pública SatNOGS em Taiwan foram baixados, conferidos e decodificados;
  o TLE público foi propagado por SGP4 (previsão sem rastreamento medido). O OrionQ não se
  comunicou com o satélite, e a recepção operacional de ponta a ponta não foi demonstrada.
- Verificação independente do texto novo contra o código, os relatórios e a literatura; as
  correções apontadas foram aplicadas.

## Novidades da versão 6

- Nome: OrionQ em todo o texto (era ORION-Q até a versão 5.56); nota de rodapé na Seção 9.1
  explica os prefixos `orion_q`/`orion-q` que continuam no repositório.
- Resumo e Abstract: uma frase sobre a rede de demonstração, que não sustenta afirmações.
- Contribuições: a quinta passa a dizer seis componentes (onze configurações), como o resto do
  texto; a quarta inclui a rede de demonstração.
- Seção 9.2: a estimativa de fidelidade derivada da QBER, exibida pela rede de demonstração,
  fica explicitamente fora das trilhas de evidência.
- Seção 9.3: removida a frase obsoleta sobre features de roteamento "ilustrativas".
- Seções 9.4 e 9.5: o fluxo de ativação auditado governa o plano de controle; o motor da rede
  de demonstração usa uma permissão de controle aprendido com recibo, contrato e supervisor,
  e estender o fluxo a ele vira trabalho futuro.
- Seção 9.6 (nova): rede de demonstração — 35 de 131 enlaces com as plantas do autor e 96 com
  física de referência da plataforma (Werner, BBPSSW, SNS-TF, satélites hipotéticos LEO/GEO,
  BBM92), conferida contra o SeQUeNCe em 22 casos; bancada de laboratório emulada e KME
  (subconjunto ETSI 014); extensões de engenharia (segurança, núcleo Linux, PS/PL emulado,
  PetaLinux); qualificação de software: nível 4 completo para revisão e não atribuído, nível 5
  não demonstrado; desempenho (62,8 ms por ciclo; passos de rede acima de 1 s).
- Seção 9.7: cerca de 101 mil linhas na camada de plataforma; 8.740 testes, com o resultado
  da execução completa em Linux de 11/10; versão 5.65.0 para Windows e Linux (WSL2).
- Seção 10.3 e Disponibilidade: o patch de compatibilidade do gmpy2 em quatro arquivos do
  autor, documentado em `external/patches/`.
- Limitações: item novo "Rede de demonstração"; "Plataforma" menciona a camada de segurança
  sem certificação. Conclusão e trabalhos futuros atualizados.
- Referências novas: Lucamarini 2018, Wang 2018, Jiang 2019, Pirandola 2017, Yin 2017,
  Liao 2017, Lu 2022, Bennett 1992, Vallado 2006, ETSI GS QKD 014, NASA NPR 7123.1D,
  ECSS-E-HB-11A, Clopper e Pearson 1934.
- Controle de redes no SeQUeNCe (Seção 9.6; Resumo, Abstract, contribuição 4, limitação
  "Rede de demonstração" e conclusão): as 13 sessões terrestres da demonstração traduzidas em
  redes nativas do SeQUeNCe 1.2.0, 21 execuções com os critérios funcionais cumpridos
  (77/79/80 pares; recusa e liberação de reserva; queda e recuperação; detector de eficiência
  zero), camada global com 2.220 recibos nativos e 16 critérios por semente, e dez nós nativos
  reservados por API; declarada como verificação funcional e parcial.
- Disponibilidade: o OrionQ passa ao repositório privado `nakka1/Orion-Q`, com acesso de leitura
  para a banca e o orientador mediante solicitação e o texto vinculado à etiqueta
  `v6.0.0-prebanca`; o estudo preliminar segue público em `nakka1/NISQ-Predictive-Twin`.
- Seção 5 condensada e renomeada para "Estudo Preliminar e Mudança de Foco" (de cerca de 3,5
  páginas para pouco mais de uma): hipótese, resultado com protocolo congelado (nenhum modelo
  superou a persistência; menor MAE com pior utilidade), dados públicos e a mudança de foco. Saíram
  as Tabelas e Figuras das fases 1 e 2 e da reavaliação; os arquivos `figuras/fase1_*` e
  `figuras/fase2_*` ficam sem uso.
- Hipótese WDM: tratada como encerrada. A Seção 5 diz que ela foi abandonada; saíram a frase
  final das limitações, que a dava como pendente, e o item de trabalhos futuros que buscava
  dados WDM pareados com alvo quântico.
- Atualizar na congelação do código (18/10): números de testes e linhas da Seção 9.7, e criar a
  etiqueta `v6.0.0-prebanca` no repositório `Orion-Q`.

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
