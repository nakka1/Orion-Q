# Sincronizador — campanha artificial multissemente v1

Data: 23/09/2026. Incremento em fonte; EXE anterior não reconstruído. Q05 continua
sem ativação de política nova. Nenhum parâmetro/checkpoint foi ajustado após os resultados.

## Protocolo e execução

[Protocolo definido antes da execução](../product/orion_q_sync_campaign_protocol.md):
seis cenários × dez sementes (100–109) × 128 frames. Os dados/resultado do replay
exploratório anterior eram conhecidos; esta campanha não é um teste externo cego.
Pesos QKD originais, sem treinamento, busca de hiperparâmetros ou seleção no teste.

Comparadores: persistência e média dos oito offsets passados. Ambos usam as mesmas
referências que o modelo. O baseline de média é uma função fixa das entradas
registradas, não usa a referência futura nem parâmetros ajustados ao teste.
Agregação com peso igual por semente, sem tratar janelas sobrepostas como réplicas
independentes. Não foram realizados testes de significância.

Filtro descritivo predefinido: redução de MAE médio ≥5% contra **cada** baseline,
vitória em ≥8/10 sementes e cobertura ≥90% em todas as sementes. São critérios de
engenharia desta avaliação, não significância estatística ou autorização operacional.

## Resultado completo

MAE médio por semente em picosegundos; menor é melhor.

| Cenário | Modelo | Persistência | Média passada | Vitórias vs. persistência | Cobertura mínima | Filtro completo |
|---|---:|---:|---:|---:|---:|---|
| Suave | 19,850 | 22,724 | 53,393 | 10/10 | 100% | Atendido |
| Ruído independente | 144,456 | 138,084 | 104,340 | 0/10 | 100% | Não atendido |
| Reversão de deriva | 15,963 | 15,879 | 26,892 | 5/10 | 100% | Não atendido |
| Mudança abrupta | 20,438 | 18,433 | 23,451 | 0/10 | 100% | Não atendido |
| Oscilação rápida | 135,540 | 122,218 | 182,637 | 0/10 | 100% | Não atendido |
| Perda de pacotes | 20,339 | 22,824 | 52,976 | 10/10 | 80,672% | Não atendido |

No sinal suave, redução de MAE de **12,646% contra persistência**. Na reversão de
deriva o modelo ficou 0,525% pior em média, com cinco vitórias e cinco derrotas.
Em ruído independente, mudança abrupta e oscilação rápida ficou respectivamente
4,615%, 10,874% e 10,900% pior que persistência. Com perda de pacotes, o erro nos
pares disponíveis foi 10,889% menor, mas a cobertura não atingiu o limite fixado.
O erro baixo dos pares disponíveis não compensa alvos sem previsão/referência.

Totais: **60 execuções, 7.070 previsões registradas, 6.910 pares avaliados, 120
pendências finais e 40 alvos perdidos**. Nos cinco cenários sem perda, 119 pares e
duas pendências por execução. Com perda: 96 pares, duas pendências, quatro alvos
perdidos e 102 previsões por execução. Cobertura considera as 119 oportunidades
de alvo pós-warm-up, incluindo as oportunidades sem previsão devido à nova espera
de oito amostras após lacunas. Não houve preenchimento retrospectivo.

## Evidências e reprodutibilidade

Diretório: `outputs/sync_campaign_20260923/author-v1/`.

- `frozen-protocol.json`: criado antes da primeira inferência, com protocolo,
  hash dos pesos esperados e hashes de código.
- `summary.json`: status complete, todas as sementes/cenários, métricas,
  comparações e hashes dos 60 relatórios detalhados.
- Relatórios `<cenario>-<semente>.json`: previsões originais, entrada do modelo,
  referências, pareamento e baselines. Nenhuma sobrescrita automática.

Manifesto de configuração SHA-256:
`4db6a0ca81e8ee5a2832b1e3c7a8e73b6b1c386b7f51176a3da27bbc435965d1`.
ZIP QKD:
`94352f15b6dcb132a3c03559b9d94b28f42b6e9adb8b2cdaab18f46720a55a79`.
Conferência posterior: 60/60 hashes de relatórios corretos e nenhuma divergência
nos arquivos de código registrados no manifesto. Hashes não são certificação
independente nem carimbo de tempo assinado.

**192 testes passaram** em dois conjuntos sem sobreposição: 110 de campanha,
avaliador temporal e ponte; 82 de interface shadow, sessão offline, gates físicos
e trilhas de evidência. Evidências: `outputs/sync_campaign_20260923/tests.xml` e
`safety.xml`. Ruff passou. Não é execução da suíte inteira nem CI remota.

Reprodução, a partir da raiz com ambiente instalado e diretório-pai existente:

```powershell
python tools/run_sync_campaign.py --qkd-zip "C:/Users/alexa/Downloads/modelo_qkd_ml.zip" --protocol configs/optional_models/sync_campaign_v1.json --output outputs/minha-campanha-sync-v1
```

O diretório de saída deve ser novo. Mudanças no protocolo v1 são recusadas; outro
experimento exige uma versão revisada. Em falha ficam artefatos parciais, sem
`summary.json` completo; não interpretar esse diretório como campanha concluída.

## Consequência para o produto e para a tese

**Não promover este sincronizador a substituto universal de persistência.** O
resultado positivo anterior não se sustenta uniformemente sob os cenários novos.
O modelo continua opcional, OFF por padrão, e somente shadow/offline. O filtro
atendido em um cenário não detecta automaticamente esse regime em uma rede real.
Não foi criado seletor de modelo ou fallback adaptativo baseado nestes resultados.

Estes geradores não são calibrados por instrumentos e não reproduzem o artigo do
autor. Não há confirmação de WDM→fidelidade, generalização física, descoberta de
uma nova lei ou ganho em laço fechado. O ganho de engenharia é tornar comparações
reproduzíveis e expor quando um modelo falha, com limites e cobertura explícitos.

Próximo foco no roadmap: avançar as entradas tipadas de outro componente opcional
em shadow — por exemplo, compensação SO(3) — preservando os modelos/baselines e
sem retrainar o sincronizador sobre esta campanha. Q05 segue dependente de
compatibilidade, autorização auditável, leitura da política ativa e rollback.
