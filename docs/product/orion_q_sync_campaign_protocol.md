# Protocolo local congelado — sincronizador artificial v1

Definido antes de executar a campanha com os pesos do autor. O replay exploratório
anterior (semente 1337) já foi visto: esta campanha não é um teste externo cego.
O manifesto e hashes de código serão copiados para um diretório novo antes de
qualquer inferência da campanha. Isso não equivale a pré-registro público assinado.

Configuração: 128 frames por execução; sementes 100–109; seis cenários abaixo.
Modelo e pesos revisados inalterados; sem treinamento, busca, escolha de checkpoint
ou reajuste a partir do teste. OFF e políticas de controle permanecem inalterados.

## Geradores e cenários fixados

Um frame representa 1 segundo virtual. Ruído uniforme em [-0,02; 0,02] ns,
independente por tick; fase uniforme inicial por semente. Temperatura comum:
20 + 0,3 sen(t/40 + fase) °C. Temperatura não determina o offset neste gerador.

- **smooth:** 0,2 sen(t/13 + fase) ns + ruído.
- **independent_noise:** ruído × 10, sem termo temporal previsível. Ganho contra
  persistência sozinho pode ser simples suavização, não informação sobre o futuro.
- **drift_reversal:** 0,005 × min(t, 128−t) ns + ruído, reversão em t=64.
- **step_shift:** offset 0 até t=63, depois 0,3 ns, mais ruído.
- **fast_oscillation:** 0,2 sen(t/2 + fase) ns + ruído.
- **packet_loss:** mesmo sinal smooth, omitindo frames 48–51 e 88; buffer reinicia
  após lacunas e exige oito amostras consecutivas antes de prever novamente.

Janela 8, horizonte +2 s. Previsão registrada antes de receber a referência;
pareamento pelo timestamp exato da última amostra recebida. Sem preenchimento
retrospectivo ou consulta a sinal futuro. Comparadores: último offset e média
aritmética dos oito offsets passados, ambos sobre as mesmas referências.

## Métricas e critérios de engenharia

Por execução: MAE/RMSE pareados dos três métodos, pendências, alvos perdidos,
quantidade de previsões e cobertura sobre 119 oportunidades de alvo pós-warm-up.
Agregar MAE por semente com peso igual, reportar mínimo/máximo, vitórias/empates/
derrotas e todas as sementes. Sem p-valores: janelas dependentes não são réplicas.

Um cenário atende ao filtro descritivo apenas se houver, contra **cada** baseline:
redução de MAE médio ≥5%, menor MAE em ≥8/10 sementes, e cobertura ≥90% em todas
as sementes. Denominador zero não produz ganho infinito; o filtro não passa.
Limiares são escolhas de engenharia, não critérios publicados ou significância.
Reportar cenários que falharem, sem excluir outliers ou trocar cenários após execução.
Não existe autorização automática Q05, mesmo se todos atenderem ao filtro.

## Limites

Sinais artificiais definidos manualmente, sem calibração física, não reproduzem
os dados/artigo do autor. Offset e temperatura não são telemetria WDM. Resultados
não validam WDM→fidelidade quântica nem estimam desempenho de controle em laço
fechado. Execução sequencial sem atraso de worker não mede latência da interface.
Um resultado desfavorável deve limitar a recomendação do modelo, não ser ocultado.

Execução concluída, sem alterar o protocolo após observar os resultados:
[campanha v1 e limites encontrados](../results/orion_q_sync_campaign_20260923.md).
