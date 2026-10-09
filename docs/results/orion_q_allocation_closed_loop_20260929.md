# Alocador híbrido em laço fechado — campanha v1

Data: 29/09/2026. Alvo `allocation.admission.v1`, modo `simulation`.
**Na planta metropolitana do autor, com a contabilidade de chave finita que a
exportação declara, o alocador revisado carrega 2,4 a 2,6 vezes o tráfego
clássico da política que silencia o canal clássico onde há chave devida
(quântico-primeiro), servindo a mesma chave dentro de 0,5 ponto percentual, em 20
de 20 sementes novas e nas duas convenções de demanda (p de Holm 7,6×10⁻⁶ nas
oito hipóteses).** Antes de pontuar, o laço regenerou com erro 0,0 os quatro
episódios exportados e o ponto da fronteira com chave finita do autor. A previsão
Ridge não muda o resultado: a diferença para a persistência fica abaixo de 0,2
ponto. Nenhuma política foi ativada e nenhuma ação física foi executada.

## O laço

- **Planta:** `qnet_network.allocation.HybridAllocationEnv`, o corredor São
  Paulo–Rio do autor (9 nós, 11 vãos de 30 a 145 km), em que o tráfego clássico de
  um vão custa chave por espalhamento Raman; −5 dBm por enlace em admissão total;
  deriva residual do compensador; um terço dos enlaces degradados por episódio;
  chave por período com custo de bloco finito. É exatamente o que o
  `model/plant.json` da exportação declara, e o contrato confere configuração,
  parâmetros Raman e os 11 perfis de vão (a mesma planta com chave assintótica é
  recusada).
- **Laço** (`control_plane/allocation_loop.py`): a cada 5 s o ORION-Q entrega à
  réplica revisada (`AllocationReplayModel`) o que um controlador conhece — QBER
  medida, a própria estimativa Raman, as admissões já em fila, reservas de chave,
  dívida e prazos — e a planta aplica o vetor de admissão devolvido após os 30 s
  de reconfiguração, como no `rollout` do autor. Se a réplica recusar, vale a
  ação segura quântico-primeiro; após 3 recusas, o episódio segue quântico-primeiro.
- **Configurações:** as duas exportadas, persistência (a publicada) e Ridge, ambas
  com cobertura 1.

## Portões de reprodução

| Portão | Resultado |
|---|---|
| 4 episódios exportados inteiros (persistência e Ridge, sementes 500 e 501; demanda e planta da semente do episódio) | chave servida, tráfego clássico e admissão média idênticos (erro 0,0) |
| Ponto de cobertura 1 da fronteira com chave finita do autor (`runs/realism/finite_key_frontier.csv`, sementes 500–505, demanda da semente 0) | 0,46131117457715604 / 0,23337245571024942, idênticos |

## Protocolo congelado

`configs/optional_models/allocation_closed_loop_v1.json`, SHA-256
`4af5b9f25c68ff4740c65a39facaa858909441346a6c49f34694c46fd3c5ee62`. Sementes de
episódio 1000–1019 (o autor usou 0–11 no treino, 100–105 na validação e 500–511 no
teste), em duas convenções: A, demanda da semente 0 e planta da semente do episódio
(a das tabelas do autor); B, demanda e planta da semente do episódio (a dos
episódios exportados). Hipóteses primárias, por configuração e convenção:
(1) tráfego clássico maior que quântico-primeiro; (2) chave servida não inferior à
de quântico-primeiro com margem de 2 pontos percentuais. Sign-flip pareado exato,
unilateral, Holm sobre as oito. O limiar reativo foi ressintonizado pela regra do
autor nas sementes 100–102 (escolheu 0,02–0,04).

## Resultados (20 sementes novas)

| Convenção | Política | Chave servida | Tráfego clássico |
|---|---|---:|---:|
| A | **alocador (persistência)** | 0,458 | **0,233** |
| A | alocador (Ridge) | 0,458 | 0,234 |
| A | quântico-primeiro | 0,463 | 0,096 |
| A | limiar reativo (0,02–0,04) | 0,362 | 0,588 |
| A | admissão estática 0 / 0,25 / 0,5 | 0,468 / 0,345 / 0,262 | 0 / 0,512 / 0,708 |
| A | clássico-primeiro | 0,096 | 1,000 |
| B | **alocador (persistência)** | 0,496 | **0,249** |
| B | alocador (Ridge) | 0,496 | 0,247 |
| B | quântico-primeiro | 0,501 | 0,095 |
| B | limiar reativo (0,02–0,04) | 0,393 | 0,587 |

Frações da demanda servida (chave e clássico), médias sobre 20 episódios de 30 min.

| Hipótese primária | A (persist.) | A (Ridge) | B (persist.) | B (Ridge) |
|---|---:|---:|---:|---:|
| Clássico − quântico-primeiro | +0,137 | +0,137 | +0,154 | +0,152 |
| Chave − quântico-primeiro (+ margem 0,02) | +0,015 | +0,015 | +0,015 | +0,015 |
| Vitórias | 20/20 | 20/20 | 20/20 | 20/20 |
| p de Holm | 7,6×10⁻⁶ | 7,6×10⁻⁶ | 7,6×10⁻⁶ | 7,6×10⁻⁶ |

**Leitura.** O manuscrito resume o alocador como "a mesma chave que silenciar o
canal clássico, com 2,4× a vazão clássica" (com chave assintótica). Na planta com
chave finita e em sementes novas, a razão é 2,43 (A) e 2,62 (B), e a chave fica
0,5 ponto abaixo da de quântico-primeiro. Secundários, descritivos:

- **Admissão estática à mesma chave.** Para servir a chave do alocador, a
  admissão estática teria de ficar perto de 0 e carregaria 5,6× menos tráfego
  clássico (A, IC 95% 5,1–6,2; B: 8,0×, 6,9–9,5). Este não é o número de 2,7× do
  manuscrito, que é uma média sobre a sobreposição das fronteiras varrendo a
  cobertura; só a cobertura 1 existe como modelo revisado.
- **Limiar reativo.** A banda escolhida pela regra do autor (a de maior chave nas
  sementes de validação) serve 9,6 a 10,2 pontos a menos de chave que o alocador,
  com mais tráfego clássico.
  As duas políticas ocupam pontos diferentes da troca, e não há comparação
  pareada a serviço igual.
- **Ridge contra persistência.** Diferenças de +0,0001 a +0,0002 na chave e de
  −0,0018 a +0,0003 no clássico; algumas são estatisticamente distinguíveis, todas
  abaixo de 0,2 ponto. A previsão aprendida não acrescenta nada, como o autor
  registrou: o ganho vem da lei de controle.

O supervisor não registrou recusas nas 80 execuções da réplica.

## Como reproduzir

```
python tools/run_allocation_closed_loop.py --qnet-zip F:\qnet_network.zip --output outputs\allocation-closed-loop-v1
```

Resumo: [`orion_q_allocation_closed_loop_20260929_summary.json`](orion_q_allocation_closed_loop_20260929_summary.json).
Na nuvem levou ~70 min com a máquina compartilhada; usou um ZIP remontado a
partir de `F:\qnet_network` (os portões confirmam os arquivos exportados).

## Limites

- Planta simulada do autor, não uma rede implantada.
- A chave é a estimativa de bloco finito do autor por período de controle, não
  uma prova de segurança.
- Só a configuração exportada (cobertura 1) é um modelo revisado; a fronteira ao
  longo da cobertura é do autor e não foi refeita aqui.
