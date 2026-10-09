# Compensação SO(3) — campanha artificial multissemente v1

Campanha concluída em 23/09/2026; consolidação e conferência dos artefatos em
24/09/2026. **240 execuções completas; 6/24 grupos atendem ao filtro descritivo.**
São os checkpoints Ridge dos dois regimes nos cenários suave, reversão térmica e
degrau. Nenhum MLP atende ao filtro. Os quatro modelos continuam opcionais, OFF
por padrão, somente offline/shadow; nenhuma política Q05 ou atuação física foi ativada.

## Protocolo e alcance

O [protocolo SO(3) v1](../product/orion_q_compensation_campaign_protocol.md)
fixou seis cenários × quatro checkpoints × dez sementes (100–109), sem seleção,
treinamento ou ajuste de parâmetros. Cada execução tem 32 lotes de 16 amostras,
cadência virtual de 2 ms, horizonte de 40 ms e intervalo de 0 a 1,022 s.
O [diagnóstico anterior](orion_q_compensation_temporal_20260923.md) usou 1337.
Sementes novas não tornam independente a família artificial já conhecida.

Comparadores causais: persistência (P) e velocidade angular constante (V),
calculada dos 15 incrementos SO(3) passados. A referência é a rotação observada
posteriormente, inclusive o ruído artificial, com alvo exato em lote posterior
disjunto; sem interpolação ou preenchimento retrospectivo. V é um preditor de
rotação, não um controlador alfa-beta calibrado. O cenário de resultados tardios
testa ordem de chegada, não tempo real de inferência ou deadline físico de 40 ms.

Erro: ângulo geodésico em SO(3). A agregação dá peso igual às dez sementes, sem
tratar amostras temporais correlacionadas como réplicas independentes. Filtro
completo: redução do erro médio ≥5% contra **ambas** as bases, vitória em ≥8/10
sementes contra cada uma e cobertura ≥90% em todas as sementes. Os filtros
individuais de erro no JSON não incluem cobertura; o filtro final do grupo inclui.
São critérios descritivos de engenharia, não testes de significância ou autorização.

## Grade completa

Valores em **mrad**, arredondados apenas para apresentação. Média [mín.; máx.]
refere-se aos dez erros médios por semente, não a intervalo de confiança.
V/E/D = vitórias/empates/derrotas do modelo contra a base indicada.
Os nomes dos checkpoints mantêm os regimes nominais do arquivo original; não
certificam pertencimento ao domínio de treinamento nem ausência de OOD.

| Cenário | Checkpoint | Média [mín.; máx.] (mrad) | P (mrad) | V (mrad) | V/E/D vs. P | V/E/D vs. V | Cobertura mín. | Filtro |
|---|---|---:|---:|---:|---:|---:|---:|---|
| `smooth` | calibrado.ridge | 4,342 [4,260; 4,419] | 5,237 | 8,875 | 10/0/0 | 10/0/0 | 100,000% | Sim |
| `smooth` | calibrado.mlp | 27,224 [27,165; 27,328] | 5,237 | 8,875 | 0/0/10 | 0/0/10 | 100,000% | Não |
| `smooth` | desenvolvimento.ridge | 4,499 [4,497; 4,501] | 5,653 | 8,911 | 10/0/0 | 10/0/0 | 100,000% | Sim |
| `smooth` | desenvolvimento.mlp | 35,540 [35,417; 35,725] | 5,653 | 8,911 | 0/0/10 | 0/0/10 | 100,000% | Não |
| `rotation_noise` | calibrado.ridge | 7,373 [6,755; 8,245] | 6,841 | 12,819 | 1/0/9 | 10/0/0 | 100,000% | Não |
| `rotation_noise` | calibrado.mlp | 27,675 [26,226; 28,588] | 6,841 | 12,819 | 0/0/10 | 0/0/10 | 100,000% | Não |
| `rotation_noise` | desenvolvimento.ridge | 7,261 [6,462; 8,493] | 7,312 | 13,398 | 6/0/4 | 10/0/0 | 100,000% | Não |
| `rotation_noise` | desenvolvimento.mlp | 35,770 [35,249; 36,349] | 7,312 | 13,398 | 0/0/10 | 0/0/10 | 100,000% | Não |
| `thermal_reversal` | calibrado.ridge | 4,325 [4,244; 4,402] | 5,235 | 8,886 | 10/0/0 | 10/0/0 | 100,000% | Sim |
| `thermal_reversal` | calibrado.mlp | 27,055 [27,017; 27,183] | 5,235 | 8,886 | 0/0/10 | 0/0/10 | 100,000% | Não |
| `thermal_reversal` | desenvolvimento.ridge | 3,320 [3,316; 3,327] | 5,644 | 8,949 | 10/0/0 | 10/0/0 | 100,000% | Sim |
| `thermal_reversal` | desenvolvimento.mlp | 32,154 [32,008; 32,381] | 5,644 | 8,949 | 0/0/10 | 0/0/10 | 100,000% | Não |
| `rotation_step` | calibrado.ridge | 5,551 [5,503; 5,597] | 6,281 | 9,832 | 10/0/0 | 10/0/0 | 100,000% | Sim |
| `rotation_step` | calibrado.mlp | 26,650 [26,573; 26,750] | 6,281 | 9,832 | 0/0/10 | 0/0/10 | 100,000% | Não |
| `rotation_step` | desenvolvimento.ridge | 5,607 [5,605; 5,607] | 6,665 | 9,794 | 10/0/0 | 10/0/0 | 100,000% | Sim |
| `rotation_step` | desenvolvimento.mlp | 36,135 [36,023; 36,302] | 6,665 | 9,794 | 0/0/10 | 0/0/10 | 100,000% | Não |
| `packet_loss` | calibrado.ridge | 4,305 [4,088; 4,566] | 5,314 | 8,986 | 10/0/0 | 10/0/0 | 73,333% | Não |
| `packet_loss` | calibrado.mlp | 27,608 [27,433; 27,805] | 5,314 | 8,986 | 0/0/10 | 0/0/10 | 73,333% | Não |
| `packet_loss` | desenvolvimento.ridge | 4,511 [4,506; 4,515] | 5,652 | 9,032 | 10/0/0 | 10/0/0 | 73,333% | Não |
| `packet_loss` | desenvolvimento.mlp | 35,618 [35,525; 35,737] | 5,652 | 9,032 | 0/0/10 | 0/0/10 | 73,333% | Não |
| `late_result` | calibrado.ridge | 4,342 [4,261; 4,413] | 5,210 | 8,816 | 10/0/0 | 10/0/0 | 73,333% | Não |
| `late_result` | calibrado.mlp | 27,156 [27,100; 27,280] | 5,210 | 8,816 | 0/0/10 | 0/0/10 | 73,333% | Não |
| `late_result` | desenvolvimento.ridge | 4,503 [4,499; 4,507] | 5,656 | 8,873 | 10/0/0 | 10/0/0 | 73,333% | Não |
| `late_result` | desenvolvimento.mlp | 35,447 [35,167; 35,880] | 5,656 | 8,873 | 0/0/10 | 0/0/10 | 73,333% | Não |

## Interpretação e cobertura

Ridge atende ao filtro em três dos seis cenários, em ambos os regimes. No sinal
suave, reduz o erro médio contra persistência em 17,091% (calibrado) e 20,415%
(desenvolvimento). Sob ruído, o Ridge calibrado fica 7,788% pior que persistência,
com apenas uma vitória; o de desenvolvimento reduz somente 0,703%, com seis
vitórias. Ambos falham no filtro desse cenário, apesar de vencerem V em 10/10.
MLP perde para ambas as bases em todas as sementes de todos os cenários: 0/120
vitórias por base entre os dois regimes. Isso descreve esta família artificial,
não demonstra fracasso geral da arquitetura nem superioridade universal do Ridge.

Com perdas ou resultados tardios, Ridge vence ambas as bases nos pares disponíveis,
mas **nenhum checkpoint passa**, pois a cobertura é 22/30 = **73,333%**. Erro menor
nos pares observados não recupera oportunidades sem previsão ou referência.

| Cenários | Execuções | Previsões registradas por execução | Pares | Pendentes | Alvos perdidos | Resultados tardios recusados | Cobertura de campanha | Cobertura condicional |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Suave, ruído, reversão, degrau | 160 (40 por cenário) | 32 | 30 | 2 | 0 | 0 | 100% | 100% |
| Perda de pacotes | 40 | 28 | 22 | 2 | 4 | 0 | 73,333% | 84,615% |
| Resultado tardio | 40 | 24 | 22 | 2 | 0 | 8 | 73,333% | 100% |

As contagens de pares/pendentes/perdidos/recusados na tabela são por execução.
Totais: **7.200 previsões registradas, 6.560 pares, 480 pendências finais, 160
alvos perdidos e 320 resultados tardios recusados**, sem atrasos ainda por resolver.
As duas pendências de cada série têm alvos além do intervalo emitido. A cobertura
de campanha usa 30 oportunidades por série; a condicional usa somente
`paired / (paired + missed_target)`. Logo, 100% condicional em atraso não significa
que todas as oportunidades da campanha foram atendidas. São 7.200 oportunidades
na grade e 6.560 pares; a coincidência com o total de previsões não iguala os
conceitos. Previsões rejeitadas por atraso não entram no total registrado.

## Evidências e conferência após a mudança de pasta

Raiz atual: `E:\Codex\2026-08-27\e\work\NISQ-Predictive-Twin`.
Ver [registro do ambiente local](../product/local_workspace.md).

Artefatos originais preservados em
`outputs/compensation_campaign_20260923/author-v1-run1/`:

- `frozen-protocol.json`: protocolo, identidade esperada do ZIP e hashes de fonte;
  o runner grava este arquivo antes da inferência.
- `summary.json`: status `complete`, 240 execuções, 24 comparações e hashes dos
  relatórios individuais.
- 240 arquivos `<cenario>-<model_id>-<semente>.json`, com previsões, referências,
  baselines, ordem de chegada, métricas médias/RMS e contagens.
- Regressão no **pai de `author-v1-run1`**:
  `outputs/compensation_campaign_20260923/regression-final.xml`.

A conferência em 24/09 verificou os 240 hashes de relatórios, igualdade dos resumos
individuais com o resumo consolidado, grade completa sem duplicatas, identidade
do arquivo de protocolo e os 15 hashes de código. Recalculou os 24 agregados,
vitórias/empates/derrotas e filtros a partir das métricas registradas por execução.
Não recalculou inferência nem erros angulares a partir das matrizes. Não houve
reexecução da campanha. Todos os relatórios declaram `policy_activated=false`
e `physical_action_performed=false`.

SHA-256 dos artefatos conferidos:

| Artefato | SHA-256 |
|---|---|
| `summary.json` | `1367635e564e6a6513b54bdf458c69c90f73573e9b46617a0e63d165eabc33fa` |
| `frozen-protocol.json` | `3c438995fb0ca327a360fe09962eae129584c1fbe75f4ad68f17630b325cb844` |
| `regression-final.xml` | `9fe8dffb54c4f6f498fba6e637485e696b7f2eababe9bb5f521e462cb52f0a15` |
| Configuração `compensation_campaign_v1.json` | `79e3fed105093fa578ec3a31e2d87b67a809cdd41bb23c88f0f098261ed449fc` |
| ZIP QNET declarado no manifesto | `6b8563209f54a2d566f1a4229b140b75fda93555085ff2bf6bfcad8f07abd938` |

O ZIP externo não foi reaberto nesta consolidação. Hashes conferem integridade
relativa aos registros, não autenticidade independente ou carimbo de tempo assinado.
Recibo local da conferência: `outputs/compensation_consolidation_20260924/verification.json`.
Os diretórios `outputs/` são ignorados pelo Git; este relatório rastreável não
substitui a preservação dos artefatos completos.

## Verificação de software

O XML histórico de 23/09/2026 registra **500 testes aprovados em 119,924 s**, zero
falhas, erros ou testes ignorados; foram conferidos também os 500 elementos
`testcase`. São 15 módulos selecionados, cobrindo campanha/temporal SO(3), sessões
e pontes opcionais, console, sincronização, sandbox, trilhas de evidência e gate
físico. Não é a suíte integral do repositório nem uma nova execução em 24/09.
Não somar 500 às regressões anteriores de 466 ou 1.025 testes, que têm sobreposição.
A consolidação documental não reconstrói o EXE nem atesta CI remota ou aceite em
uma segunda máquina Windows.

## Consequência para produto e próximos incrementos

Q16 permanece parcial. Preservar persistência e os quatro checkpoints, sem seleção
automática pelo resultado desta grade. O próximo incremento de integração é definir
uma ponte tipada shadow de outro componente opcional (roteamento ou alocação), com
observáveis causais próprios, ações legais explícitas e indisponibilidade quando
faltarem entradas. Não converter campos WDM/timing em features SO(3) ou vice-versa.
Q05 continua pendente de preparação/revisão/ativação auditável, readback e rollback.

Para ampliar a evidência SO(3), criar outro protocolo antes de executar: família
independente ou planta validada, sequências mais longas, hipóteses de ruído/OOD
explícitas e divisões de calibração/teste por campanha se houver ajuste. Comparações
de controle exigem planta, custos e baselines de controle, incluindo alfa-beta
adequadamente calibrado; esta campanha mede previsão angular em malha aberta.
Não reutilizar a grade v1 para ajustar e depois alegar confirmação independente.

Não há demonstração de generalização física, ganho em QBER/SKR, fidelidade de Bell
prevista por WDM, segurança ou vantagem em laço fechado. O ensaio não simula fótons
nem ruído de tomografia. Os pesos continuam externos e os modelos OFF por padrão,
com aplicação utilizável sem eles. Licenças, export seguro do detector, aceite
Windows independente e validação física continuam pendentes.
