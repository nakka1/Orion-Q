# ORION-Q — aquisição probabilística e memória no caminho configurado

Data: 13/09/2026. Primeiro incremento temporal de Q08/Q09, limitado a um caminho de dois enlaces e um repetidor. Código-fonte atualizado; o executável anterior não foi reconstruído.

## Uso na console

Abra **TOPOLOGY → RUN CONFIGURED PATH** e selecione **Acquisition + T1/T2 memory** no campo PROTOCOL. O modo **Ideal conditional SWAP** anterior continua disponível.

Escolha origem, repetidor, destino e alvo. Preencha os oito parâmetros temporais; a interface não introduz valores experimentais silenciosamente:

| Campo | Significado | Limite de software |
|---|---|---|
| LEFT / RIGHT HOP probability | Probabilidade efetiva de adquirir um par por tentativa em cada enlace | 0–1 |
| ATTEMPT INTERVAL | Duração de um tick virtual, em segundos | 1e-9–1 s |
| MAX TICKS | Orçamento até a aquisição simultânea ou timeout | 1–1000 |
| MEMORY T1 / T2 | Tempos de relaxação/coerência aplicados aos dois qubits de cada par armazenado | 1e-9–1000 s; T2 ≤ 2T1 |
| RETENTION LIMIT | Idade máxima em ticks; o par expira antes das aquisições desse tick | 1–1000 |
| SEED | Semente do gerador PCG64 | inteiro 0–4294967295 |

Esses intervalos são limites operacionais, não validação de plausibilidade para equipamentos específicos. As probabilidades são entradas assumidas: podem representar conjuntamente geração, transmissão e detecção, mas **não são calculadas a partir da distância nem estimadas de dados WDM**.

O formulário possui rolagem própria, mantendo fonte e controles legíveis em janela pequena. O resumo apresenta resultado, tempo virtual, ticks, idades dos pares, quantidade gerada e expirada. A trilha completa está no registro/exportação JSON; não foi criada uma visualização animada da fila de eventos.

## Ordem dos eventos e disponibilidade

O relógio começa em zero, sem pares. A primeira tentativa termina em `attempt_interval_s`; cada tick posterior representa outra oportunidade simultânea de aquisição nos dois enlaces.

1. Antes da aquisição, descarte um par quando `tick_atual - tick_de_chegada >= memory_ttl_steps`.
2. Sorteie dois números com `numpy.Generator(PCG64(seed))`, sempre na mesma ordem esquerda/direita, mesmo quando um slot estiver ocupado. Só slots vazios realizam tentativa e usam o respectivo sorteio.
3. Uma tentativa tem sucesso quando `sorteio < probabilidade`; o par entra com a fidelidade nominal do enlace e idade zero.
4. Quando os dois slots estão ocupados, envelheça seus estados até o instante atual e realize um único swapping ideal.
5. Encerre no primeiro swapping, mesmo se o alvo não for atingido. Se o orçamento terminar antes, registre timeout e descarte os pares remanescentes.

Há exatamente um slot por enlace. Pares ocupados não são sobrescritos; pares expirados podem ser substituídos no mesmo tick. O registro permite conferir, por enlace:

`gerados = expirados + consumidos_no_swap + descartados_ao_encerrar`.

O tempo registrado é virtual, não duração de CPU ou latência instrumental. A semente não depende do ID da solicitação nem da frequência de atualização da interface. Versões NumPy, Qiskit e Qiskit Aer são registradas; mudanças de dependências devem ser consideradas ao reproduzir resultados.

## Modelo quântico e preservação dos estados

Cada aquisição bem-sucedida começa com `werner_state(fidelidade_nominal)`. Durante a espera, é aplicado o modelo existente `QuantumChannel.apply_decoherence_to_state`, com T1/T2 explícitos e depolarização adicional zero. O ruído de armazenamento afeta **ambos os qubits de cada par** com os mesmos parâmetros, não apenas o qubit situado no repetidor. Os dois enlaces usam o mesmo T1/T2 nesta versão.

O tempo de exposição é a idade acumulada; não se reaplica ruído cumulativo em cada consulta sobre um estado já envelhecido. O estado inicial é evoluído uma vez até o instante do swapping. Pares recém-adquiridos, com idade zero, conservam seu estado inicial.

Foi acrescentado `WernerStateSwapping.swap_states(rho1, rho2)`, reutilizando a BSM, o traço parcial e as correções de Pauli existentes. A entrada exige duas matrizes de densidade válidas de dois qubits. O método escalar antigo delega a esse núcleo, mantendo sua interface.

Isso evita substituir silenciosamente estados relaxados por novos estados de Werner com a mesma fidelidade. A fidelidade escalar não caracteriza em geral todo o estado. As matrizes envelhecidas, suas fidelidades e idades são armazenadas no resultado. O supervisor independente verifica o estado sintético e a intenção SWAP antes da execução do núcleo sobre as matrizes.

O contrato legado do supervisor ainda exige `predicted_fidelity`: nesse caminho, o valor é a menor fidelidade dos estados armazenados, com metadado `stored_state_fidelity_not_model_prediction`. Não é saída de uma rede neural. Incerteza/idade de telemetria zero pertencem ao estado sintético interno, não a uma calibração instrumental.

## Resultados e escopo

- `target_met`: houve aquisição dos dois pares e o resultado numérico do swapping atingiu o alvo.
- `target_not_met`: houve swapping, mas o resultado não atingiu o alvo.
- `acquisition_timeout`: não houve dois pares disponíveis a tempo; fidelidade e comparação com o alvo ficam **ausentes (`null`)**, não zero. Nenhum swapping é executado.

BSM e correções permanecem ideais e instantâneas. Não há modelagem de tempo de heralding, propagação, operações locais, perdas dependentes da distância, purificação, várias rotas, vários repetidores ou fila de solicitações. Não se produz uma estimativa de taxa de serviço a partir de uma única tentativa; em particular, `1/tempo` não é apresentado como taxa de rede.

O resultado físico do swapping é uma média ponderada pelas saídas da BSM ideal, não amostragem experimental. A comparação com o alvo usa os números não arredondados e herda as limitações de precisão numérica descritas no [modo condicional](orion_q_workspace_path.md).

Memórias e pares desta execução são isolados: o workspace congelado, seus nós/enlaces e seus pares anteriores permanecem inalterados. Nenhum equipamento é conectado e nenhuma inferência WDM é disparada.

## Contrato, auditoria e segurança

`TemporalPathRequest` define o protocolo `memory_single_swap_v1`. A API local exige os campos completos, incluindo todos os parâmetros temporais; campos extras, números não finitos, tipos incorretos e T2 incompatível são recusados. Os defaults do construtor Python são apenas conveniência interna, não um atalho para omitir campos na entrada JSON ou no formulário.

O adaptador declara a capacidade adicional `run_temporal_path`. Entradas somente leitura não podem executá-la, nem por meio do verbo do modo ideal. A UI confirma o **hash da solicitação completa**, incluindo semente e parâmetros de memória, evitando confirmar parâmetros diferentes com um resultado anterior do mesmo ID.

São reutilizados o snapshot congelado, a verificação de versão, a transação SQLite e a idempotência do executor de caminhos. Uma repetição inalterada recupera o registro original. Alterar a semente mantendo um ID já utilizado é recusado. `NEW REQUEST / RELOAD FORM` cria outro ID para um novo experimento.

Os registros temporais usam `orion.workspace_path.temporal.v1`, com a trilha de ticks, sorteios, tentativas, chegadas, expirações, idades, contabilidade e execução quântica. Os registros ideais anteriores mantêm seu formato. A exportação JSON existente inclui o registro completo. Hashes detectam inconsistência, mas não substituem assinatura ou autenticação.

## Verificação e próximos passos

`tests/test_temporal_path.py` cobre contrato, tempo inicial, casos p=0/p=1, expiração, conservação dos pares, repetibilidade, timeout sem fidelidade fabricada, preservação de matrizes, integração com o canal existente, idempotência, exportação, capacidades e interface. A regressão também executa os testes existentes de swapping/memória e física. Evidências ficam em `outputs/software_validation_temporal_path/`.

Este é um motor discreto limitado, **não a conclusão de Q08/Q09**. Não demonstra nova correlação experimental entre WDM e desempenho quântico.

Incremento multisseed implementado no backend/CLI e no [painel nativo](orion_q_temporal_campaign_ui.md): [campanha temporal comparativa](orion_q_temporal_campaign.md). Inclui todos os timeouts no denominador, probabilidade de cumprir o alvo com Wilson, comparações pareadas com Holm e métricas descritivas de tempo/custo. Empacotamento do worker, intervalos para tempo/custo e novas políticas/atrasos continuam pendentes.
