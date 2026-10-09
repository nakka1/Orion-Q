# Demonstração institucional nacional — atualização de 2026-10-05

A seleção **Demonstration** do console usa agora `brazil-institutions-v1`: UNIJORGE
(Paralela), SENAI CIMATEC (Piatã), CBPF (Urca), IME-USP e USP (Butantã), UNICAMP
(Barão Geraldo) e CIQUANTA (Estação das Artes Luciano Agra, João Pessoa).
O cenário anterior descrito abaixo continua disponível pela API `demo_service(...,
preset="salvador_metro")`; seu journal não é substituído. O novo cenário usa
`<state-root>/demo/brazil-institutions-v1/sandbox_journal.sqlite` e mantém as edições
do operador nas próximas aberturas.

O mapa offline nacional usa a malha simplificada do IBGE. **All nodes** enquadra a
rede; o seletor de campus amplia e seleciona um node, inclusive USP e IME-USP,
sem deslocar suas coordenadas. Zoom, arraste, matriz, grafo e detalhes continuam
usando os nodes e enlaces persistidos pelo backend. Nodes sem localização não
ganham linhas no mapa geográfico.

Os oito enlaces são hipotéticos, com comprimento mínimo geodésico WGS84. A planta
simula perda óptica, contagens, ruído, sincronização e controle usando o comprimento
real declarado, sem reduzi-lo ao intervalo de treinamento. Não há rota de cabo
levantada, parâmetros de equipamentos dessas instituições ou calibração de campo.
Assim, os enlaces interestaduais diretos podem ficar **DOWN** e entregar zero
chaves; isso é um resultado esperado do modelo, não uma falha visual. A fidelidade
de Bell apresentada pela simulação é uma estimativa do modelo, não uma tomografia.
Repetidores intermediários não são inventados para produzir resultados favoráveis.
O monitor de polarização usa o regime de referência `calibrado`; os acoplamentos
entre as três plantas continuam limitados ao modelo descrito no motor de rede.

O switch **ML: OFF / ML: ON**, na aba **Topology**, controla a permissão para
modelos de compensação, sincronização, taxa de chaves, roteamento e alocação.
Um workspace nacional novo inicia com ML **OFF**. A confirmação em português
explica os possíveis problemas nos dois sentidos. Cancelar mantém o estado;
confirmar envia o comando ao backend e o switch só muda após readback.

OFF usa os controladores de referência; ON permite somente modelos com pesos
verificados e contratos admitidos. ON não garante que exista um modelo atuando.
O detector e as regras de segurança continuam ativos nos dois modos. A troca
preserva topologia, estoque de chaves, alarmes, baseline do detector e histórico;
os controladores de controle são reinicializados, podendo apresentar transientes.
Desligar não desfaz decisões anteriores. Cada mudança possui um recibo com hash
em `network_decisions`; a permissão e sua revisão persistem no journal. Não há
atuação física.

Os parágrafos seguintes documentam o cenário metropolitano anterior.

---

# ORION-Q · Guia de demonstração (operador)

Estado: **implementado** (2026-10-05). Contrato: [`orion_q_live_network_design.md`](orion_q_live_network_design.md);
motor: [`orion_q_network_engine.md`](orion_q_network_engine.md); telas: [`native_console.md`](native_console.md).

## 1. O que a demonstração é (e o que não é)

No modo de demonstração **só os dados de entrada são simulados**, pelo motor do backend
(`SimulatedInputSource`): o monitor de polarização, o offset de relógio e a sessão decoy-BB84 de cada fibra.
Tudo o que aparece depois disso — QBER, fidelidade, taxa de chave, alarmes, rotas, relatórios de
recalibração, registros do journal — é calculado pelo **mesmo** serviço, o **mesmo** `NetworkEngine` e o
**mesmo** provedor da operação normal. A única outra diferença é o arquivo de estado: a demonstração usa o
seu próprio journal (`<state-root>/demo/sandbox_journal.sqlite`), que começa com a topologia de referência
"Salvador metro" (10 nós, 11 fibras, demandas P1–P4).

A interface só nomeia operações e lê resultados. Um clique mostra `PENDING`; a tela muda quando o snapshot
seguinte traz a resposta do backend (`CONFIRMED`, `UNKNOWN` com a mensagem do backend, novo estado das
fibras, rotas replanejadas). Nenhuma animação representa algo que não foi executado: a única coisa que se
move sozinha é a rolagem das amostras de onda **já recebidas**.

Nada atua sobre hardware: todo registro carrega `physical_action_performed: False` e nenhum socket é aberto.

## 2. Como iniciar

```powershell
# a partir do código-fonte (uma vez)
python -m pip install -e ".[console]"
python tools/prepare_console_resources.py     # restaura os dois recursos fixados por hash

orion-q --demo                                # abre direto na demonstração
orion-q                                       # abre no serviço local; escolha a fonte na tela de login
ORION-Q.exe --demo                            # executável portátil do Windows
```

Na tela de login, marque **Demonstration** (`simulated input · real pipeline`) e clique em
**Open workspace →**. Para voltar à tela de login: botão da fonte no canto superior direito, `Sign out` no
trilho ou F6. **Local service** abre o espaço de trabalho do operador, sem alimentação: a topologia e as
rotas aparecem, mas todo valor medido fica "—" (nada é fabricado).

Com os modelos do autor (opcional — ver ressalvas):

```powershell
orion-q --demo --qnet-archive C:\modelos\qnet_autopilot.zip --qkd-archive C:\modelos\qkd_autopilot.zip
# ou as variáveis ORION_QNET_ARCHIVE / ORION_QKD_ARCHIVE
```

O estado fica em `%LOCALAPPDATA%\ORION-Q` (use `--state-root PASTA` para uma sessão isolada). O journal da
demonstração é persistente: nós criados e fibras desligadas continuam lá na próxima abertura. Para recomeçar
do zero, apague a pasta `demo` dentro do state-root.

## 3. O que cada tela mostra

| Tela (tecla) | Conteúdo |
|---|---|
| **Topology** (1) | Abre em **GEO**: mapa vetorial de Salvador em escala (barra de escala, norte, atribuição) com os nós nas coordenadas declaradas pelo operador. **Matrix** mostra os mesmos nós e a fibra direta de cada par; **Graph** o desenho lógico. Embaixo, a fila **Awaiting location** com os nós sem localização, sem linhas; clique no nó para ler suas conexões. As vistas lógicas preservam essas conexões. À direita o inspetor: `Inspector` (nó ou fibra selecionada e suas operações), `Events`, `Links`, `Pairs` (demandas, rota, motivo do backend quando não há rota, par guardado). A faixa de baixo mostra fidelidade, QBER e taxa de chave da fibra selecionada — ou a **mediana / soma rotulada** das fibras com estimativa. |
| **Janela do nó** | Um clique em um nó (mapa, fila, matriz, grafo) abre uma janela flutuante: gráfico de onda (visibilidade `1 − 2·QBER`, 20 Hz, últimos 12 s) com uma série por conexão, todos os campos do registro do nó, a recalibração em curso ou a última, as demandas que passam por ele, e os botões **Recalibrate**, **Isolate/Restore**, **Set location…**. Várias janelas podem ficar abertas; Esc fecha a de cima. |
| **Fidelity & decoherence** (2) | Estimativas da fibra escolhida no último segundo de rede e o histórico de 1 Hz do backend (fidelidade Werner, QBER, taxa de chave), ponto a ponto. A linha tracejada é o limiar "fibra fora" **publicado pelo motor em execução**. Abaixo, o replay shadow do modelo WDM congelado. |
| **Key management** (3) | QKD ao vivo da fibra escolhida: relógio de pulsos, taxa peneirada, QBER, taxa de chave secreta, estoque e bits descartados, última ação do controlador. Abaixo, as células QBER/SKR publicadas (evidência experimental, não gerada aqui). |
| **Nodes** (4) | Inventário e formulário **Add node** (localização opcional: sem ela o nó vai para a fila da Topologia). Detalhe do nó com o registro do backend. |
| **Edge control** (5) | Estado de cada modelo (`active` / `fallback` / `unavailable`, em contrato ou não, inferências, falhas, latência, motivo), **feed de decisões** (mais novas primeiro, filtro por tipo), demandas e rotas e — só na demonstração — **Input events**: o controle que perturba a ENTRADA simulada de uma fibra. |
| **Access & audit** (6) | Todos os registros: ciclos de controle, eventos de segurança, eventos da topologia, decisões do motor (com hash), eventos do motor e comandos do console. Exportação JSON não assinada. |

Atalhos: `Ctrl K` paleta de comandos; `1`–`6` telas; `F6` fonte de dados; `F11` tela cheia; o painel
**Journal · terminal** aceita os mesmos verbos (`isolate R-07`, `disable N01-R07`, `recalibrate N-11`,
`inject intercept_resend CORE-N05 1.0 20`, `help`).

## 4. Roteiro de 5 minutos (pré-banca)

Abra o programa **um minuto antes de começar a falar**: o detector de ataques tira uma linha de base de 60
segundos de cada sessão antes de opinar (o campo *Detector* da fibra mostra `session baseline (n of 60
periods)` e depois o veredito, por exemplo `normal · no alarm`; em *Fidelity & decoherence* o campo
*Detector state* passa a `monitoring`).

| Tempo | Ação | O que apontar na tela |
|---|---|---|
| 0:00 | Login → **Demonstration** → *Open workspace* | Faixa superior: `SIMULATED INPUT`, nós `up`, `11/11 fibres`. Mapa de Salvador com 8 nós; 2 nós na fila. Barra de status: `DEMONSTRATION · SIMULATED INPUT / REAL PIPELINE`, `Physical locked`. |
| 0:20 | Clique no nó **CORE** | Janela flutuante com a onda de cada conexão rolando. Dizer: "são amostras entregues pelo backend; a tela só as desenha". Fechar com Esc. |
| 0:45 | Aba **Pairs** → P1. No mapa, clique na fibra **N01-R07** → **Disable fibre** | `PENDING` → `CONFIRMED · … topology v+1`. A fibra fica pontilhada; P1 e P3 passam a `REROUTED` (`N-01 → N-02 → CORE → …`) com o gatilho `fibre N01-R07 disabled`. No Journal: a decisão `reroute` com o controlador que decidiu. |
| 1:15 | **Enable fibre** | A rota volta ao caminho mais curto no mesmo tick. |
| 1:30 | Clique em **R-07** → **Isolate** | Nó `isolated`, três fibras fora do grafo. P4 (`N-09 ↔ N-03`) fica **BLOCKED**: o desvio tem 4+ saltos e nenhum episódio entrega o par dentro do prazo — o motivo do backend aparece no mapa e em Pairs. É um limite real da planta do autor, não um erro. → **Restore**. |
| 2:10 | Na janela de **N-11** (um nó que não está nas rotas) → **Recalibrate** | Fases uma por segundo: `queued → polarisation → sync → commissioning → memory → report`. Ao fim, o relatório antes/depois por fibra e o hash; `Calibrations 1`. |
| 2:40 | **Edge control** → *Input events*: `intercept_resend`, fibra **CORE-N05**, strength `1.0`, duration `20` → **Inject input event** | Dizer: "isto muda só a entrada". ~3 s depois o feed mostra a decisão `detector` de `qkd.detector.boosting` (`sustained intercept_resend for 3 periods… key discarded`). |
| 3:00 | **Topology** | Fibra vermelha `COMPROMISED · OUT OF ROUTING GRAPH`, QBER ≈ 9 %, chave 0. As demandas que passavam por ela foram replanejadas no mesmo tick (aqui ficam `BLOCKED`: CORE-N05 é o único caminho curto até N-05). |
| 3:20 | **Key management** → fibra CORE-N05 | Degrau na taxa de chave e bits descartados. **Fidelity & decoherence**: o mesmo degrau no histórico e a linha do limiar. |
| 4:00 | **Nodes** → *Add node* sem localização → **Topology** | O nó aparece na fila; *Set location…* (ou *Pick on map*) o coloca no mapa quando o backend confirma. |
| 4:20 | Alternar **Light / Dark**; **Matrix** | Mesmo estado nas duas aparências e nas três visões. |
| ≈ 4:40 | (esperar) | O alarme é liberado sozinho ≈ 80 s depois da injeção: 20 s de ataque, ~28 períodos em que o detector ainda chama o ataque (janela de tendência) e 30 períodos limpos. A fibra volta a `up` e as rotas voltam. |
| 5:00 | *Sign out* → **Local service** | Mesma interface, espaço do operador (3 nós, sem localização): `NO INPUT FEED`, tudo "—". Mostra que nada é fabricado quando não há entrada. |

Dicas para o ataque dar certo:

- Use uma fibra cujo *Detector* já mostre um veredito (`normal · no alarm`) e que **não** tenha sido
  recalibrada no último minuto: recalibrar reinicia a sessão e a linha de base de 60 períodos das fibras do nó.
- A linha do tempo da demonstração tem eventos próprios (laço de 345 s): deriva térmica em t = 65 s, evento de
  perda em 135 s, intercept-resend em 185 s, PNS em 245 s, blinding em 295 s, cada um em uma fibra sorteada
  pela semente. Eles aparecem em *Input timeline* como `timeline`; o que você injeta aparece como `operator`.

## 5. Ressalvas honestas (dizer na banca)

1. **Modelos com pesos reais só com os ZIPs do autor.** Os pesos aprendidos de compensação, sincronismo,
   taxa de chave, roteamento e alocação existem apenas nos dois arquivos fixados por SHA-256
   (`--qnet-archive` / `--qkd-archive` ou `ORION_QNET_ARCHIVE` / `ORION_QKD_ARCHIVE`). Sem eles a tela mostra
   cada papel como `unavailable` e nomeia a referência que está decidindo (`baseline.alpha_beta`,
   `baseline.static`, `baseline.greedy_fidelity`, `baseline.quantum_first`). O único modelo aprendido que roda
   sempre é o **detector de ataques** (`qkd.detector.boosting`, export seguro no repositório).
2. **O agente de roteamento está fora de contrato nesta topologia.** Ele só foi revisado na cadeia de 4 nós;
   em "Salvador metro" toda decisão de rota leva `OUT OF CONTRACT` e, pelos resultados publicados, o agente
   não supera a heurística nem na cadeia revisada.
3. **O detector também roda fora do domínio de treino** (vãos de 20–70 km, outro ponto do transmissor): as
   fibras metropolitanas marcam `in_contract = False`.
4. **Falsos alarmes existem e mudam rotas.** Em rede quieta medimos ≈ 0,5–2,9 alarmes falsos por fibra-hora
   (≈ 1–11 % do tempo-fibra em `compromised`), quase todos `intercept_resend`. Em três execuções de 300 s da
   demonstração houve 2–3 alarmes, 3–8 mudanças de rota e 8–13 % do tempo-demanda sem entrega. Não foi
   atenuado: é o comportamento do detector revisado com a regra "alarme ⇒ fibra fora do grafo".
5. **Blinding não é detectado até ~50 km** e PNS em vãos curtos costuma ser classificado como perda — os
   dois aparecem na linha do tempo da entrada sem alarme correspondente.
6. **Desvio com 4 ou mais saltos termina `blocked`.** Com fidelidade bruta 0,90 por par elementar e limiar
   0,70, nenhum episódio entrega dentro do prazo. A interface mostra o motivo do backend
   (`… 0 of 24 episodes delivered; episodes ended by: deadline × …`).
7. **O alocador aprendido não roda aqui** (planta fixa de 11 vãos, 5 s, BB84 de fóton único): quem decide a
   admissão clássica é `QuantumFirst`, declarado como reserva.
8. **Fidelidade por fibra é uma estimativa** (`F = 1 − 3·QBER/2`, estado de Werner) e não entra na planta de
   roteamento, que usa a fidelidade bruta única do autor (0,90). As três plantas de entrada (polarização,
   relógio, sessão QKD) são independentes.
9. **Uma recalibração no meio de um ataque esconde o ataque**: a nova linha de base passa a contê-lo.
10. **Nada aqui é medição.** Coordenadas são declaradas pelo operador, as fibras são retas geodésicas, e a
    demonstração não valida a hipótese central do TCC (WDM → fidelidade de Bell medida).

## 6. Verificação automática

- `orion-q --self-test relatorio.json --state-root pasta` — inclui `demonstration` (17 verificações: fonte
  simulada, observáveis produzidos pelo processamento, valores do snapshot iguais aos do motor, GEO/fila/matriz,
  onda da janela do nó, limiar lido do estado, serviço ocioso sem observáveis).
- Testes: `tests/test_network_engine_*.py`, `tests/test_console_live_provider.py`,
  `tests/test_console_topology_live.py`, `tests/test_console_screens_live.py`.
- Roteiro ponta a ponta conduzido pelos widgets (login → Demonstração → … → Serviço local → Demonstração),
  com 5 min 30 s de uso: cada passo conferido na tela e contra `service.network_live_state()`; 1 tick de rede
  por segundo, nenhuma exceção, nenhum thread sobrando ao fechar, memória estável durante o uso contínuo.
