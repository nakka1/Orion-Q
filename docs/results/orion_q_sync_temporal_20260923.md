# Avaliação temporal causal do sincronizador — 23/09/2026

Incremento somente em código-fonte, posterior à sessão shadow contínua. O pacote
Windows anterior não foi modificado; não houve build ou CI remota nesta etapa.

## Entrega

`TemporalSyncEvaluation` registra previsões concluídas antes da chegada de um
frame mais novo. Somente a última amostra de um frame recebido posteriormente,
com timestamp exatamente igual ao alvo, pode fornecer a referência. Contexto
inclui fonte, campanha, fluxo, nó e relógio; um run aceita um único hash de pesos.

Previsões tardias, revisões conflitantes, dados medidos, unidades/entradas inválidas
e troca de contexto são recusados. Um salto além do alvo marca `missed_target`,
mesmo se a janela posterior contiver aquela amostra em seu histórico. A referência
é usada apenas no avaliador; o registro original e a entrada do modelo não mudam.

A sessão nativa exibe pares, pendências, alvos perdidos, frames observados e
previsões registradas, além do MAE pareado. **EXPORT TEMPORAL REPORT** captura
MAE, RMSE, diferença de MAE, registros originais, referências e seus hashes.
Sem pares, métricas são nulas, nunca zero. O limite padrão é 256 previsões, sem
remoção silenciosa; atingido o limite, a inferência para e o relatório pode ser
exportado. Reiniciar inicia outro run; STOP/CLEAR e perda de fonte apagam o run
em memória. Não há gravação automática ou mudança de política.

## Testes

**220 testes passaram** no conjunto selecionado: avaliação temporal, interface
shadow, ponte, sessão offline, gates físicos, trilhas de evidência e observações.
Não é a suíte integral do repositório. Cobertura inclui registro atrasado, ausência
de preenchimento retrospectivo, contexto/artefato incompatíveis, previsão pior
que persistência, baseline sem erro, capacidade, integridade dos relatórios,
exportação exclusiva e invalidação na interface. A primeira execução apontou uma
comparação exata de floats na fixture de baseline zero; a fixture passou a usar
o mesmo valor binário do baseline. A lógica de produção não arredonda esse erro.

Ruff e `git diff --check` passaram. Inspeção visual offscreen em 640×480 mostrou
controles de avaliação/exportação visíveis e relatório rolável. Evidências locais:
`outputs/sync_temporal_20260923/regression-final.xml` e
`pytest-02/test_export_and_small_window0/shadow-640.png`.

## Replay diagnóstico com pesos do autor

Protocolo escolhido antes da execução: **semente 1337, 128 frames, perfil artificial
reference, sem treinamento, busca ou ajuste**. Usa o mesmo `SyntheticSyncStream`
da interface, executado sequencialmente sem atraso de worker. Não mede desempenho
da UI em tempo real. Os primeiros sete frames não geram previsão; há 121 previsões,
119 pares, duas pendências no final e zero alvos perdidos.

ZIP QKD SHA-256:
`94352f15b6dcb132a3c03559b9d94b28f42b6e9adb8b2cdaab18f46720a55a79`.

| Métrica pareada | Sincronizador | Persistência |
|---|---:|---:|
| MAE | 20,477 ps | 22,522 ps |
| RMSE | 24,585 ps | 27,412 ps |

Relatório: `outputs/sync_temporal_20260923/author-diagnostic.json`, incluindo
snapshots, referências, protocolo e hashes de código. Reprodução explícita:

```powershell
python tools/evaluate_sync_temporal_demo.py --qkd-zip "C:/Users/alexa/Downloads/modelo_qkd_ml.zip" --output sync-temporal-demo.json --seed 1337 --frames 128
```

**Interpretação limitada:** o modelo teve erro menor nesse único sinal artificial,
composto por senoide e ruído semeado. Não é reprodução do artigo, teste independente
de generalização, efeito estatisticamente estabelecido ou ganho de controle.
Janelas sobrepostas são dependentes; não se calculou p-valor ou intervalo como se
fossem amostras independentes. A ordem causal é controlada no processo, não por
carimbo de tempo assinado. Hashes fornecem rastreabilidade, não autenticidade física.
Os preditores usam histórico de offset e temperatura, não WDM: este resultado não
valida a hipótese WDM→fidelidade quântica.

Próximo passo: definir uma campanha com múltiplas sementes/cenários e critérios
congelados, incluindo cenários adversos e análise da cobertura de previsões.
Não selecionar modelos ou reajustar parâmetros a partir deste replay diagnóstico.
