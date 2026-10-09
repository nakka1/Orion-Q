# Alocador híbrido opcional: admissão offline

Incremento Q16 de 22/09/2026. Duas opções: `qnet.allocator.persistence` e
`qnet.allocator.ridge`, ambas OFF por padrão. Não substituem política ativa,
não alteram a topologia atual e não acionam instrumentos ou uma rede física.

## Função e fronteiras

O alocador recomenda quanto tráfego clássico admitir em cada enlace de uma
planta simulada compartilhada com QKD. Escolhe entre 0%, 25%, 50%, 75% e 100%,
considerando ruído Raman estimado, estoque de chaves, demanda pendente, urgência
e admissões já decididas que ainda não entraram em vigor.

É uma **lei de controle baseada em modelo**, não uma rede neural. Persistência
mantém a deriva atual; Ridge prevê a deriva com as seis últimas observações.
O ganho de vazão relatado nos materiais não pode ser atribuído ao Ridge só
porque ambas as versões compartilham o nome "preditivo".

O escopo é a planta metropolitana simulada do export: 11 enlaces em ordem
fixa, período 5 s, atraso de seis períodos (30 s), previsão de 30 períodos
(150 s) e cobertura-alvo 1. O adaptador não converte a topologia do ORION-Q
nessa planta. Ordem/nome de enlace diferente é recusada.

O protocolo é declarado como `single_photon_bb84`. Não aceitar o estado do
agente decoy-state como se fosse equivalente. A previsão interna de chave usa
BB84 assintótico, mesmo que os metadados do ambiente original declarem avaliação
com chave finita. Aqui não se reproduz essa avaliação nem se gera certificado
de segurança; valores previstos não entram em um KMS como chaves disponíveis.

## Contrato

| Entrada | Forma/unidade |
|---|---|
| `time_s` | relógio relativo, segundos; passos consecutivos de 5 s |
| `measured_qber`, `raman_qber_estimate` | 11 frações em [0,1], nunca percentuais |
| `pending_admission` | 6 x 11, linha mais antiga primeiro; níveis discretos |
| `key_pool_bits`, `backlog_bits` | 11 volumes finitos não negativos, bits |
| `urgency_s` | 11 prazos positivos; `None` ou +infinito significa sem prazo |
| `link_names` | nomes exatos da planta, na mesma ordem dos arrays |
| `stream_id` | identidade explícita da campanha/planta |

NaN, infinito fora de urgência, valores negativos, prazos expirados/zero,
dimensões e níveis incorretos são recusados. Estimativa QBER ligeiramente acima
de 0,5 não é apagada: há tal amostra no export; ela continua uma fração válida.
O cálculo de coexistência preserva o clipping original a 0,5.

Ridge usa persistência nos cinco primeiros passos, com status
`warmup_persistence`. Trocar stream ou pular/repetir timestamps exige `reset()`.
Amostra/cálculo inválido não avança histórico. `recommend_snapshot` permite
conferir uma observação exportada com até cinco derivas anteriores e timestamps
consecutivos, sem modificar o estado do stream.

## Decisão e explicação

1. Estimar deriva retirando Raman próprio e erro óptico da QBER, com piso zero.
2. Prever deriva por persistência ou Ridge; limitar a janela pelo prazo/150 s.
3. Calcular a dívida de chave descontando o estoque e ponderando urgência.
4. Integrar primeiro a chave da fila já comprometida. Novas recomendações não
   substituem essa fila para tentar corrigir um prazo dentro dos 30 s de atraso.
5. Selecionar o maior nível cuja previsão cobre o requisito. Se nem nível zero
   cobre, retornar zero **com `model_coverage_met=false`**, não promessa de sucesso.

O resultado inclui hashes de arquivo/planta, nomes de enlace, previsão de
deriva, nível recomendado, tempo previsto de entrada em vigor e orçamento
assintótico usado na decisão. Em enlace sem dívida, orçamento de chave não
calculado aparece como `None`, não como medição zero. `physical_action_performed`
é sempre falso. A implementação apenas lê a fila; não reserva chaves, não
reconfigura o transporte e não simula o efeito futuro da recomendação.

## Uso por código

```python
from quantum_autopilot.model_intake.catalog import open_optional_model
from quantum_autopilot.model_intake.allocation import AllocationObservation

model = open_optional_model("qnet.allocator.persistence", archive_path="F:/qnet_network.zip")
obs = AllocationObservation(
    time_s=0., measured_qber=(.01,)*11, raman_qber_estimate=(0.,)*11,
    pending_admission=tuple((1.,)*11 for _ in range(6)),
    key_pool_bits=(0.,)*11, backlog_bits=(0.,)*11, urgency_s=(None,)*11,
)
result = model.recommend(
    obs, domain="simulated_offline", protocol="single_photon_bb84",
    link_names=model.link_names, stream_id="demo/metro",
)
# Sem dívida: recomenda admissão 1. Nada é aplicado ao backend.
```

O caminho acima é apenas exemplo de arquivo local. `open_optional_model()` sem
seleção permanece OFF e não lê artefatos. O modelo não fica obrigatório para
iniciar o produto. [Resultados de paridade](../results/orion_q_optional_allocator_20260922.md).

## Integração atual — 24/09/2026

Catálogo e sessão gráfica offline já estão disponíveis. A nova
[ponte shadow de alocação](orion_q_allocation_shadow_bridge.md) recebe snapshot
simulado tipado, compara persistência/Ridge e verifica relógio, fluxo e planta,
sem avançar a fila ou aplicar recomendações. API de runtime somente leitura,
CLI e exportação explícitas entregues em fonte; 407 testes selecionados passaram.

O incremento gráfico seguinte já liga um produtor artificial e uma sessão
shadow opt-in, com histórico causal, START explícito, frescor/invalidação e
exportação do frame capturado. A fila é uma fixture exógena, nunca realimentada
pelas recomendações. Ver [uso e limites](orion_q_allocation_shadow_bridge.md#sessão-gráfica-shadow--atualização-em-24092026).
Q05 continua pendente. Detector ainda exige export sem pickle; controle fechado,
filas reais, contabilidade de chave finita e novo `.exe` não foram implementados.
