# Roadmap até a pré-banca — 22/10/2026

Escrito em 28/09/2026; atualizado em 29/09/2026. Complementa o [roadmap geral](roadmap.md)
sem substituí-lo.

**Atualização de escopo em 07/10/2026:** IBM Quantum foi descontinuado,
IBM-2C foi cancelado e não faz parte das pendências para a banca final.
A implementação anterior fica no [arquivo histórico](../../archives/retired_ibm_quantum_20261007/ibm_quantum_source.zip);
Qiskit/Aer continua na simulação local. Os resultados de 29/09 abaixo
conservam seu contexto histórico.

## Estado em 29/09

- Trabalho local versionado no branch `roadmap-prebanca` (7 commits, sem push).
- Compensador em laço fechado supervisionado concluído
  ([resultado](../results/orion_q_polarisation_closed_loop_20260929.md)): Ridge e MLP
  calibrados vencem sem compensação, persistência e alfa-beta ressintonizado em 20/20
  sementes; Ridge 3,33× abaixo do alfa-beta (IC 95% 3,24–3,42), p de Holm 5,7×10⁻⁶.
- Ativação pelo fluxo Q05 implementada para o alvo de polarização
  ([contrato e uso](orion_q_polarisation_activation_q05.md)), com demonstração por linha
  de comando (`tools/polarisation_control_demo.py`). Falta mostrar isso no console.

## Objetivo do TCC (revisado)

Entregar um programa capaz de controlar uma rede quântica **simulada** com modelos
de ML acoplados e disponíveis para uso. O valor científico está nos modelos
integrados e na evidência de que eles funcionam dentro do programa. A hipótese
original WDM→fidelidade futura sai do escopo principal e permanece registrada como
resultado aberto/negativo em [`docs/thesis/`](../thesis/formal_claims.md).

## Lacuna central

Hoje nenhum modelo controla a rede:

1. **Ativação:** o fluxo Q05 (preparar/revisar/aprovar/ativar/rollback) só aceita o
   limiar do `RepeaterSandboxPolicy` (HALT/WAIT/PURIFY/SWAP). Os modelos produzem
   outros tipos de ação (rotação, admissão por enlace, rota, parâmetros decoy).
2. **Laço fechado:** os produtores shadow evoluem sem receber as recomendações
   (`SyntheticCompensationStream`, fila exógena do alocador, inventário do roteamento).
   Sem realimentação só se mede previsão e concordância, não efeito de controle.
3. **Domínio:** os modelos vêm do SeQUeNCe; as features do sandbox são ilustrativas.

## Plano semanal

### Semana 1 — 29/09 a 04/10: fundação

- [x] Commit de todo o trabalho local, em partes lógicas (`.gitattributes`
      primeiro; depois `quantum_twin`, `docs/thesis`, `evidence`; depois `quantum_autopilot`).
      **Push pendente** (feito pelo autor no Windows).
- [x] CI verde: `pytest.importorskip("PySide6")` em `tests/test_campaign_manifest_editor.py`;
      os dois goldens numéricos passaram a rodar só no Windows, onde foram gravados.
      **Pendente:** confirmar no Windows que eles passam
      (`pytest tests/test_physics_regression.py tests/test_domain_shift_asymmetry_10seed.py`).
- [x] Contrato por função para o alvo `polarisation.compensation.v1`: o checkpoint só
      roda se os próprios metadados declararem a planta, o período, o horizonte e os
      fótons de monitoramento (`polarisation_loop.check_checkpoint`).
- [x] Planta SO(3) com realimentação, transcrita do qnet_autopilot e conferida bit a bit
      (`polarisation_plant.py`). Métrica primária = QBER média (a do manuscrito);
      secundárias = ângulo residual, disponibilidade e taxa de chave.
- [x] **Texto:** nova estrutura de capítulos; reescrever introdução, objetivo e
      contribuições; condensar os capítulos WDM em um capítulo de estudo preliminar
      (versão 3 do texto, `docs/thesis/monografia/`).

### Semana 2 — 05/10 a 11/10: compensador em laço fechado

- [x] Ridge ativável pelo fluxo Q05 (preparar, revisar, aprovar, ativar, readback, rollback),
      com a evidência da campanha vinculada aos pesos. **Pendente:** painel no console.
- [x] Campanha com protocolo congelado (`configs/optional_models/polarisation_closed_loop_v1.json`):
      Ridge e MLP × sem compensação × persistência × alfa-beta ressintonizado, no ponto
      calibrado, 20 sementes novas, sign-flip exato + Holm, portão de reprodução e falha
      injetada. Os checkpoints de desenvolvimento são recusados pelo contrato nessa planta.
      Os cenários artificiais de estresse ficam com a campanha shadow de 23/09.
- [x] Relatório em `docs/results/`. [ ] Reivindicação no novo registro.
- [ ] Execução oficial no Windows com o ZIP fixado (`tools/run_polarisation_closed_loop.py`).
- [ ] **Texto:** capítulos de arquitetura do ORION-Q (plano de controle, supervisor,
      fluxo Q05, admissão de modelos) e dos modelos acoplados; metodologia de
      avaliação (shadow × laço fechado, baselines, estatística).

**Ponto de corte (11/10):** se o laço fechado do compensador não estiver pronto,
cancelar a semana 3 do alocador e apresentar um modelo em laço fechado + demais em shadow.

### Semana 3 — 12/10 a 18/10: texto + alocador (opcional)

- [ ] **Texto:** resultados (paridade, campanhas shadow, laço fechado do compensador),
      discussão, limitações e conclusão; versão completa para a orientação.
- [ ] (Opcional, só com o texto em dia) Alocador Ridge em laço fechado na planta do
      próprio pacote (a fila avança com as admissões) × persistência.
- [ ] Build Windows com a ativação; aceite em uma segunda máquina.
- [ ] Novo registro de reivindicações (reaproveitar `thesis_claims.py` e o validador).
- [ ] README: resumo do objetivo e resultados antes do changelog; corrigir contagens e
      módulos antigos citados.
- [ ] **18/10: congelamento de funcionalidades.** Depois disso, só correções.

### Semana 4 — 19/10 a 22/10: ensaio

- [ ] Tag `v6.0.0-prebanca`, pacote de evidências e validador verdes no CI.
- [ ] **Texto:** revisão final com as correções da orientação.
- [ ] Roteiro de demonstração, slides e dois ensaios cronometrados.
- [ ] Plano B: vídeo gravado da demonstração.

## Roteiro de demonstração proposto

1. Abrir o console com todos os modelos OFF e o catálogo verificado.
2. Rodar o baseline (persistência) no cenário de polarização.
3. Preparar, revisar, aprovar e ativar o compensador Ridge.
4. Rodar o mesmo cenário; injetar ruído e mostrar onde o modelo ajuda e onde não ajuda.
5. Abrir o DECISION AUDIT de uma decisão; fazer rollback.
6. Mostrar o relatório da campanha e o registro de reivindicações validado.

## Reivindicações propostas

| ID | Afirmação | Status esperado |
|---|---|---|
| C-PLATAFORMA | O programa admite e executa os modelos do pacote com paridade aos exports originais, sem carregar pickle | suportado (testes de paridade existentes) |
| C-SEGURANCA | Modelos são OFF por padrão e só entram em controle por ativação aprovada, auditada e reversível; sem atuação física | suportado para o compensador (29/09, API e demonstração por linha de comando; 30/09, P-SEGURANCA no registro verificável, sobre o resumo da demonstração e os marcadores das campanhas) |
| C-COMP-LF | Em laço fechado simulado, o compensador Ridge reduz a QBER frente a sem compensação, persistência e alfa-beta ressintonizado no ponto calibrado | suportado (29/09; 20/20 sementes, Holm 5,7×10⁻⁶) |
| C-COMP-DEV | O mesmo resultado no ponto de desenvolvimento, com os checkpoints daquele ponto | suportado (29/09; 4,75× e 5,00×, 20/20 sementes) |
| C-COMP-ATU | Com o atuador limitado do autor, a vantagem do compensador exige dispositivo rápido | suportado (29/09; desde 120 rad/s no desenvolvimento e 250 rad/s no calibrado; portão: varredura do autor 36/36) |
| C-TAXA-LF | Em laço fechado na planta do autor, o ator de taxa de chave supera o comissionamento | suportado de 25 a 65 km; não significativo a 80 km (29/09) |
| C-SINC-LF | Em laço fechado, o sincronizador Ridge supera o alfa-beta | suportado (160/160 execuções; margem ≤ 5,1%) |
| C-ALOC-LF | Em laço fechado na planta do autor, o alocador serve a chave de quântico-primeiro com mais tráfego clássico | suportado (2,4–2,6×, 20/20; Ridge ≈ persistência) |
| C-ROT-LF | Em laço fechado, o agente de roteamento empata com a melhor heurística | suportado só no prazo longo e só no gêmeo analítico; no nominal fica 3 pontos abaixo (Holm p 0,010) |
| C-ROT-SEQ | Com o SeQUeNCe no laço, o resultado do roteamento se mantém | 30/09: portões do autor no SeQUeNCe (480/480 linhas, erro 0); o gêmeo prevê o SeQUeNCe nas 8 taxas (≤ 1,7 ponto); o agente fica abaixo da gulosa nos dois prazos (Holm 1×10⁻⁴ e 0,004); P-ROT-SEQ, P-ROT-GEMEO |
| C-REPRO | Antes de pontuar, cada campanha regenera os resultados publicados/exportados pelo autor | suportado (todos os portões aprovados) |
| C-SHADOW | Os painéis shadow são alimentados pelas plantas do autor | suportado (29/09) |
| C-BLOC | O controlador de blocagem do autor, no laço do ORION-Q, tira chave de enlace mal compensado e custa pouco em enlace bom | 30/09: portões do autor 432/432 e 24/24 linhas; persistência 10,9× e 7,9× o melhor temporizador (20/20); aprendidos 99,8–100,0% (não inferior, margem 1%); alfa-beta −2%; P-BLOC, P-BLOC-LF |
| C-COMP-PF | Em ponto fixo de 16 bits (configuração do RTL), o compensador fica a até 5% do ponto flutuante nos dois pontos | suportado (30/09; varredura do autor 20/20; 0,1% a 3,8%; 12 bits colapsa) |
| C-RTL | O Verilog do autor é exato ao bit em relação às referências em ponto fixo do autor | suportado (30/09; Icarus Verilog e Yosys; dois defeitos fora do caminho de dados usado) |
| C-REG | As afirmações da plataforma são verificáveis automaticamente | suportado (30/09; `docs/thesis/orion_q_platform_claim_registry.json`, 20 afirmações, 795 checagens) |
| C-DET | O detector do autor roda sem *pickle* e, em sessões novas, vê os ataques que o alarme de QBER não vê | suportado (29/09; exportação provada idêntica; 84–89% contra 7–14% dos períodos de ataque, Holm 5×10⁻²⁴; *intercept-and-resend* é a classe fraca; 30/09: com variáveis de tempo real, sem a diferença central do autor, perde 0,15–0,21 ponto, P-DET-CAUSAL) |
| H-WDM | Hipótese WDM→alvo quântico futuro | aberta, fora do escopo principal |

## Fora da pré-banca (para a banca final ou trabalhos futuros)

Compensador retreinado com o atuador
limitado, Q06/Q07, novos incrementos do Q14 e a hipótese WDM. IBM-2C foi
cancelado em 07/10/2026. O detector
(exportação segura) foi antecipado e concluído em 29/09, e o SeQUeNCe no laço do
roteamento, em 30/09.

## Qualificação de desempenho — 08/10/2026

- [x] Diagnosticar a carga global de 71 nodes, 131 enlaces e nove satélites,
  com telemetria, simulação e navegação nativas em 3440×1440.
- [x] Otimizar GEO, vistas ocultas, apresentação de detalhes, conversão de
  estado, imports opcionais e retenção de tipos DPAPI; preservar os contratos,
  a autorização, a auditoria e a ordem científica.
- [x] Conferir 27.882 eventos e 120 blocos brutos no corpus controlado,
  conservando ordem, timestamps, métricas e hashes.
- [x] Qualificar a fonte em 1.199 casos distintos mais recentes/44 lotes,
  sem confundir esse agregado com um full-suite único fresco.
- [x] Exercitar o EXE `5.51.0-native-20261008-015715`: self-test 39,
  temporal 25, laboratório 12, satélite 21, global FULL 33, stress 535,
  startup seis casos, segurança 14 e os 11 modelos opcionais em ambos
  os modos, incluindo ZIPs originais, sem atuação física.
- [x] Medir a carga normal do EXE (190 ações/90 leituras) e o diagnóstico
  separado de vinte minutos (640 ações/315 leituras), com integridade da
  coleta e auditoria conferidas.
- [ ] Qualificar horas/dias e outra máquina; explicar a memória privada
  ainda crescente, os históricos sem limite demonstrado e os widgets sem
  parent, sem podar registros necessários à segurança ou à ciência.
- [ ] Reduzir as pausas ocasionais e a primeira abertura de detalhes/Keys;
  startup do primeiro snapshot regrediu. Não foi cumprido o orçamento de
  10 ms, nem foi demonstrada redução material de CPU ou 60 FPS.
- [ ] Resolver a proposta de durabilidade global mediante a autorização
  específica pendente após a recusa automática por risco de perda de dados;
  manter as evidências e a proposta sem aplicação até essa decisão.

O plateau de RSS passou na janela final com observador limitado; isso não
certifica ausência de vazamentos ilimitada. A memória privada subiu 23,19 MiB.
As duas candidatas rejeitadas mantêm `DO-NOT-DISTRIBUTE`. Os números, métodos,
alternativas nativas aceitas/rejeitadas, limitações de hardware e caminhos
dos recibos estão no [relatório de desempenho](../results/orion_q_performance_20261007.md).
