# ORION-Q — campanha temporal multisseed

Data: 13/09/2026, atualizado em 14/09/2026. Incremento parcial de Q14/Q15 sobre o caminho temporal de Q08/Q09. Implementado no backend, por linha de comando e no [painel nativo](orion_q_temporal_campaign_ui.md), com progresso, cancelamento e comparação verificada. O [pacote Windows novo](orion_q_frozen_temporal_worker.md) também executa o worker sem Python externo.

## O que esta entrega permite

Comparar de dois a quatro regimes de aquisição, coerência e retenção em um mesmo caminho de dois enlaces, com até 64 sementes distintas por regime. O motor reutilizado é `evaluate_temporal_path`: matrizes envelhecidas pelo canal T1/T2 existente, swapping sobre essas matrizes, memória isolada e nenhum comando físico.

A pergunta primária é: **qual a probabilidade de o primeiro swapping cumprir o alvo de fidelidade dentro do orçamento temporal?** Cada timeout entra como insucesso dessa pergunta binária, mas sua fidelidade continua ausente, não zero. Um swapping abaixo do alvo também é insucesso e encerra o ensaio, sem nova tentativa de serviço.

Não há inferência WDM nesta campanha, dados quânticos medidos, confirmação da tese WDM→fidelidade real, otimização automática ou seleção de um vencedor. Os regimes são análises exploratórias de sensibilidade, não controladores treinados nem equipamentos certificados.

## Executar

Na raiz do repositório, com as dependências instaladas:

```powershell
python -m quantum_autopilot.control_plane.temporal_campaign_cli `
  --manifest experiments/configs/temporal_path_campaign_demo.json `
  --store outputs/temporal-campaign-demo/state.sqlite `
  --output outputs/temporal-campaign-demo/receipt.json
```

Após instalar esta versão do pacote, o comando equivalente é `orion-q-temporal-campaign`. No ambiente local de desenvolvimento, pode-se substituir `python` por `.\.audit-winvenv\Scripts\python.exe`.

O exemplo usa um banco dedicado: não reseta nem altera o workspace da console. Um banco novo fornece o blueprint de três nós, versão zero, congelado uma única vez; o blueprint não é inicializado na tabela do workspace só para executar a campanha. Para avaliar um workspace existente, indique seu banco e use os IDs e a versão atuais no manifesto. A API Python é `run_temporal_campaign(store, request)`.

Escolha um **novo caminho de saída** em cada exportação: o runner recusa sobrescrita. Repetir o mesmo manifesto no mesmo banco recupera o recibo original sem recalcular. Para uma nova realização, use outro `campaign_id`; para um novo experimento, declare também seus parâmetros/sementes. Reutilizar um ID com parâmetros diferentes é recusado. Repetir as mesmas sementes não constitui evidência estatística adicional.

## Contrato e comparabilidade

O manifesto exige todos os campos de `TemporalCampaignRequest`, inclusive `topology_id`, `protocol`, `baseline_id` e todos os parâmetros de cada regime. Campos extras, ausentes, IDs duplicados, sementes repetidas, bool em campo numérico e números não finitos são recusados. O leitor limita o arquivo a 64 KiB e recusa chaves JSON duplicadas, inclusive nos regimes.

| Comum e fixo | Variável entre regimes |
|---|---|
| Snapshot completo, IDs, fidelidades nominais e distâncias | Probabilidades de aquisição de cada enlace |
| Origem, repetidor e destino | T1/T2, compartilhados pelos dois enlaces e aplicados aos dois qubits de cada par |
| Alvo de fidelidade | Retenção máxima em ticks |
| Intervalo por tentativa e máximo de ticks | ID descritivo do regime |
| Lista de sementes e protocolo de swapping | — |

O prazo é `max_attempts × attempt_interval_s`. O produto `n_sementes × n_regimes × max_attempts` não pode superar 64.000 ticks; os limites individuais do [motor temporal](orion_q_temporal_path.md) continuam em vigor. Até 256 ensaios são possíveis, sujeitos ao limite de ticks. Isso limita trabalho e volume de recibo; não é timeout de CPU garantido.

Cada par regime/semente cria um gerador PCG64 com essa semente. O motor sorteia **dois números por tick, sempre esquerda/direita, inclusive para slots ocupados**. Logo, diferenças nas ações não deslocam a sequência exógena dos ticks comuns. Os regimes podem terminar em ticks diferentes. Os ensaios usam memórias isoladas, sem transportar pares entre sementes ou reutilizar os pares persistidos no workspace.

Uma probabilidade maior ou memória melhor é uma mudança de cenário assumida, não uma política mais inteligente. Igualar o prazo não iguala automaticamente o custo de geração: o relatório contabiliza os pares gerados e expirados. Não atribuir diferenças a uma única causa quando vários parâmetros forem alterados juntos.

## Estatística e métricas

- Primária: `target_met / total_trials`, incluindo **todos** os timeouts e swaps abaixo do alvo. Intervalo de Wilson pontual de 95%.
- Disponibilidade: `swaps / total_trials`, também com Wilson pontual de 95%.
- Comparações: diferenças de probabilidade candidato menos baseline, com tabela pareada de concordâncias/discordâncias por semente. Teste exato bilateral de McNemar: entre os discordantes, cauda binomial com p=0,5 duplicada e limitada a 1; sem discordâncias, p=1.
- Multiplicidade: Holm sobre a família de **todos os regimes não baseline versus o baseline declarado**, apenas para a métrica primária, α=0,05. Não há teste de todos os pares nem seleção de melhor regime. Diferenças negativas são conservadas; rejeição não implica ganho.
- Descritivas condicionais: fidelidade média, proporção que atinge o alvo e tempo médio **somente entre ensaios que fizeram swapping**, com tamanho desse subconjunto explícito. Ficam `null` se nenhum swap ocorreu.
- Descritivas incondicionais: tempo médio até encerramento, pares gerados e expirados por ensaio, incluindo timeouts. Tempo até encerramento não é latência média de entrega bem-sucedida, e seu inverso não é taxa de serviço.

Os intervalos são pontuais, **não simultâneos**, e não são intervalos para diferenças pareadas. As médias condicionais não recebem teste/intervalo nesta versão: os subconjuntos disponíveis podem ser diferentes e comparar somente suas fidelidades introduziria viés de seleção. Tempo/custo são descritos, sem alegar diferença significativa.

As sementes devem ser definidas sem selecionar resultados favoráveis. Sob as hipóteses do PRNG, representam repetições Monte Carlo aproximadamente independentes dentro de cada regime e pareadas entre regimes. Intervalos e testes só descrevem essa aleatoriedade, não incerteza de modelo físico, calibração ou precisão numérica. Uma lista curta não demonstra equivalência quando p é alto. Congelar um manifesto no software não equivale a um pré-registro público anterior à análise.

## Persistência, concorrência e falhas

O banco recebe uma nova tabela aditiva, `temporal_path_campaigns`; os registros anteriores permanecem intactos. A sequência é:

1. Verificar recibo idempotente e ler uma única versão da topologia sob transação curta.
2. Validar versão/caminho e calcular todos os ensaios **sem manter conexão ou bloqueio do banco**.
3. Inserir um único recibo completo sob transação de escrita, conferindo novamente o ID.

Se outro operador editar a topologia durante o cálculo, a campanha continua sobre a cópia congelada e registra essa versão, não a configuração nova. Duas solicitações concorrentes idênticas podem duplicar trabalho, mas aceitam apenas um recibo; a segunda devolve o primeiro. Uma falha de cálculo aborta a campanha inteira e não remove silenciosamente o ensaio nem o transforma em timeout. Não existe retomada parcial nesta versão; interrupção antes do commit não produz campanha concluída.

O recibo `orion.temporal_campaign.v1` contém manifesto, hash da solicitação, snapshot/hash de origem, resultados agregados, todos os ensaios com suas solicitações, trilhas temporais, contabilidade, matrizes de densidade e versões NumPy/Qiskit/Aer. Tem também data de conclusão, `evidence_kind=simulated`, `physical_action_performed=false` e `workspace_mutated=false`.

Leituras verificam hashes do registro, da solicitação e da topologia, além da correspondência do ID. Hash não é assinatura/autenticação. Exportação JSON é posterior ao commit: se ela falhar, o recibo completo pode já estar salvo no SQLite; repita o manifesto com outra saída para recuperá-lo. Um arquivo interrompido de exportação não deve ser usado como recibo válido.

Para reprodução numérica independente, use `TemporalCampaignRequest.from_dict(receipt['record']['request'])`, `SimulatedTopology.from_dict(receipt['record']['source_topology'])` e `evaluate_temporal_campaign(topology, request)` no mesmo código/dependências. Compare resumos, sorteios e resultados numéricos com tolerância apropriada. Timestamps reais internos mudam: o hash do novo registro não precisa coincidir. **Reexportação idempotente não é reprodução independente do cálculo.**

## Demonstração executada

Manifesto: `experiments/configs/temporal_path_campaign_demo.json`, sementes 0–31, alvo 0,8, prazo virtual 40 µs, enlaces nominais 0,9. Não foram ajustados parâmetros após observar os resultados desta execução.

| Regime | Atingiu alvo / 32 | Probabilidade [IC95% pontual] | Swaps | Timeouts | Pares gerados/ensaio |
|---|---:|---|---:|---:|---:|
| Baseline | 3 | 9,4% [3,2%; 24,2%] | 30 | 2 | 2,09 |
| T1/T2 100× maiores | 30 | 93,8% [79,9%; 98,3%] | 30 | 2 | 2,09 |
| Retenção de 1 tick | 24 | 75,0% [57,9%; 86,7%] | 24 | 8 | 11,81 |
| Aquisição direita 0,1→0,5 | 11 | 34,4% [20,4%; 51,7%] | 32 | 0 | 2,00 |

Contra o baseline, os p ajustados por Holm foram aproximadamente 4,47e-8, 1,91e-6 e 0,0215, respectivamente. Esses resultados são **sensibilidade do modelo simulado a parâmetros assumidos**, não descoberta experimental ou evidência de novidade científica.

A retenção curta ilustra o custo de descartar pares antigos: quando ocorre swapping, ambos os pares são recém-adquiridos, mas aumentam os timeouts e a geração necessária. Melhorar aquisição eliminou timeouts nesta amostra, mas não eliminou swaps abaixo do alvo. Aumentar T1/T2 não alterou as chegadas, pois o modelo não acopla coerência a aquisição. Isso exercita distinções relevantes para o produto sem alegar uma nova correlação WDM.

Recibo local: `outputs/software_validation_temporal_campaign/demo-receipt.json`, SHA256 `7e7539d26615605405edd875b1d2f6c7a86c8ea15eaa4e9941303899c6119a60`. O recibo completo depende também das dependências instaladas e de seus metadados; novas execuções podem ter diferenças numéricas pequenas e timestamps diferentes.

## Verificação e pendências

`tests/test_temporal_campaign.py` cobre contrato, limites, Wilson/McNemar/Holm, ausência de seleção de timeouts, matrizes preservadas, sorteios pareados, invariância à ordem de regimes, fonte congelada, gravação concorrente, falha sem recibo parcial, integridade e CLI sem sobrescrita. A regressão inclui console, caminhos, inventário e física existente. Evidências ficam em `outputs/software_validation_temporal_campaign/`.

Verificação nesta entrega: **384 testes passaram** na regressão integrada (`accepted-tests.xml`, 305,77 s); após os últimos testes de limites/pareamento e ajuste da ordem de impressão no replay, **42 testes específicos passaram** (`final-tests.xml`, 6,57 s). Esses conjuntos se sobrepõem e não devem ser somados. Ruff passou nos dois módulos novos e no arquivo de testes. A primeira execução dos testes encontrou uma asserção que pressupunha swapping em uma semente com timeout; a asserção foi corrigida para exigir ausência de execução quântica nesse caso, sem alterar o simulador para forçar sucesso.

O [painel nativo](orion_q_temporal_campaign_ui.md) agora seleciona manifesto, confere fonte/parâmetros, lança processo separado, acompanha progresso/cancelamento e lê o recibo confirmado fora da thread gráfica. O worker foi [empacotado e testado](orion_q_frozen_temporal_worker.md). O [histórico de recibos concluídos](orion_q_temporal_history.md) permite busca por prefixo, navegação, abertura verificada e exportação sem reexecução. O [diário operacional da console](orion_q_campaign_attempts.md) registra tentativas e conclusões de falha/cancelamento separadamente, com exportação individual verificada por EXPORT ATTEMPT. Próximas pendências de produto: filtros adicionais, reconciliação explícita de tentativas OPEN, exportação em lote e teste em máquina independente. Para a pesquisa, reservar sementes/cenários novos para confirmação de hipóteses explícitas e validar um subconjunto em simulador independente. Controle reativo/preditivo, políticas de retenção adaptativas, oracle, atrasos, filas e generalização entre topologias continuam pendentes; esta campanha não conclui Q08–Q15.
