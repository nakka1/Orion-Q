# OrionQ — desempenho de interface e avaliação GPU, 8 de outubro de 2026

As correções CPU melhoraram várias métricas da interface nativa, sobretudo no globo. Essa comparação usa duas execuções antes e uma depois; não corresponde a uma implantação de GPU. Os protótipos GPU são avaliados separadamente. Há pausas pontuais e crescimento de memória durante três minutos de execução, descritos abaixo.

## Cenário, hardware e comparabilidade

- Antes: `gpu-prototype/raster-1, gpu-prototype/raster-2`; depois: `cpu-optimized-1`. Fontes estáveis em cada execução; apenas três fontes CPU mudaram entre antes/depois.
- CPU AMD Ryzen 7 5700X, 8 núcleos/16 threads, RAM 31.93 GiB; NVIDIA GeForce RTX 3060 com 12 GiB, driver 616.64.
- Windows 11 build 26200, Python 3.12.14, Qt/PySide6 6.11.2, psutil 7.2.2, NumPy 2.5.2, SciPy 1.18.1. Metadados do projeto medido: 5.57.0.
- Monitor U34G4C, cliente nativo 3440×1440, DPR 1; fonte simulada seed 20261004; 71 nodes, 131 enlaces, nove satélites, 71 comunicadores, 36 sessões tecnológicas, quatro demandas locais e 76 globais.
- Mesmos parâmetros científicos e planos de ações nas quatro primeiras fases: Mapa, Globo, abas e alternâncias, cada uma com mínimo de 20 s e dez ações planejadas. Telemetria e simulação ativas. O depois inclui mais 180 s de execução; essa fase não é comparada ao antes sem soak.
- Heartbeat de 10 ms e amostragem CPU/RSS externa de 0,5 s; sem cProfile/override global da aplicação nos passes nativos. Eventos Qt exercitam a interface real; não são injeções de mouse do sistema operacional.
- Tempo de pintura mede o handler Qt do viewport, não apresentação final pela GPU, VSync ou FPS do monitor. CPU usa psutil: 100% equivale a um processador lógico; valores acima de 100% são possíveis.

As verificações de configuração, versões, parâmetros, ações, fontes, readbacks e auditoria passaram. Os hashes dos artefatos e a comparação legível por máquina estão em [cpu-performance-comparison.json](../../outputs/orionq_gpu_20261008/cpu-performance-comparison.json).

## Comparação nativa das fases equivalentes

O valor “antes” é a mediana dos dois valores por execução. Não se misturam distribuições nem se tratam todos os frames como repetições independentes. “Δ” compara o depois com essa mediana; negativo indica redução.

| Fase | Métrica | Antes p50 / p95 | Depois p50 / p95 | Δ p50 / p95 | Amostras antes (duas execuções) / depois |
|---|---|---:|---:|---:|---:|
| Mapa | Intervalo heartbeat, ms | 11.01 / 49.39 | 10.01 / 40.81 | -9.0% / -17.4% | 1112 + 1243 / 1372 |
| Mapa | Pintura viewport, ms | 2.80 / 45.01 | 2.05 / 45.50 | -26.8% / 1.1% | 280 + 277 / 289 |
| Mapa | Ação → primeira pintura, ms | 230.42 / 695.38 | 205.64 / 622.33 | -10.8% / -10.5% | 11 + 11 / 11 |
| Mapa | Ação → conteúdo completo, ms | 1715.13 / 2906.33 | 1640.99 / 2410.51 | -4.3% / -17.1% | 7 + 7 / 7 |
| Mapa | CPU processo UI, % | 73.55 / 120.50 | 75.10 / 114.80 | 2.1% / -4.7% | 39 + 37 / 39 |
| Mapa | RSS processo UI, MiB | 620.39 / 632.76 | 625.77 / 639.02 | 0.9% / 1.0% | 39 + 37 / 39 |
| Globo | Intervalo heartbeat, ms | 13.08 / 60.05 | 10.01 / 31.67 | -23.5% / -47.3% | 770 + 1147 / 1503 |
| Globo | Pintura viewport, ms | 13.99 / 46.93 | 9.03 / 49.66 | -35.5% / 5.8% | 426 + 424 / 234 |
| Globo | Ação → primeira pintura, ms | 97.90 / 497.24 | 71.51 / 181.70 | -26.9% / -63.5% | 11 + 11 / 11 |
| Globo | Ação → conteúdo completo, ms | 1090.77 / 1757.07 | 765.36 / 1093.28 | -29.8% / -37.8% | 8 + 8 / 8 |
| Globo | CPU processo UI, % | 92.60 / 137.80 | 52.90 / 117.70 | -42.9% / -14.6% | 37 + 39 / 37 |
| Globo | RSS processo UI, MiB | 625.99 / 635.68 | 637.16 / 642.83 | 1.8% / 1.1% | 37 + 39 / 37 |
| Abas | Intervalo heartbeat, ms | 12.98 / 28.28 | 10.00 / 20.32 | -22.9% / -28.2% | 1086 + 1708 / 1801 |
| Abas | Pintura viewport, ms | 12.97 / 26.07 | 10.05 / 24.36 | -22.5% / -6.6% | 136 + 140 / 105 |
| Abas | Ação → primeira pintura, ms | 47.59 / 500.02 | 45.56 / 423.88 | -4.3% / -15.2% | 10 + 10 / 10 |
| Abas | Ação → conteúdo completo, ms | — / — | — / — | —% / —% | 0 + 0 / 0 |
| Abas | CPU processo UI, % | 42.00 / 117.60 | 42.80 / 103.00 | 1.9% / -12.4% | 37 + 37 / 38 |
| Abas | RSS processo UI, MiB | 633.85 / 642.88 | 638.64 / 651.89 | 0.8% / 1.4% | 37 + 37 / 38 |
| Alternâncias | Intervalo heartbeat, ms | 13.01 / 36.71 | 10.01 / 21.02 | -23.0% / -42.7% | 1043 + 1447 / 1708 |
| Alternâncias | Pintura viewport, ms | 15.09 / 57.82 | 14.16 / 42.67 | -6.2% / -26.2% | 375 + 354 / 260 |
| Alternâncias | Ação → primeira pintura, ms | 102.55 / 214.61 | 78.69 / 192.77 | -23.3% / -10.2% | 12 + 12 / 12 |
| Alternâncias | Ação → conteúdo completo, ms | — / — | — / — | —% / —% | 0 + 0 / 0 |
| Alternâncias | CPU processo UI, % | 67.15 / 114.75 | 44.10 / 108.90 | -34.3% / -5.1% | 38 + 38 / 38 |
| Alternâncias | RSS processo UI, MiB | 651.09 / 659.96 | 660.89 / 664.88 | 1.5% / 0.7% | 38 + 38 / 38 |

As tendências não são uniformes: o p95 de pintura do Globo e parte das medidas do Mapa não melhoraram. As latências de conteúdo completo têm apenas sete/oito seleções por fase; não são estimativas populacionais. A confirmação de conteúdo é distinta da primeira resposta visual.

| Fase | Gaps heartbeat >100 ms antes / depois | Gaps >250 ms antes / depois |
|---|---:|---:|
| Mapa | 16, 17 → 16 | 4, 5 → 2 |
| Globo | 21, 22 → 11 | 4, 3 → 0 |
| Abas | 7, 6 → 3 | 1, 2 → 2 |
| Alternâncias | 8, 6 → 2 | 0, 0 → 0 |

Gaps medem atraso do heartbeat, não contagem de crashes. Ainda há pausas: na execução posterior o maior gap foi 439,64 ms no Mapa, 216,62 ms no Globo, 430,55 ms nas abas e 715,19 ms no soak. Não se declara ausência de travamentos perceptíveis.

## Correções CPU e evidências de microprofiling

- `orbit_globe.py`: reutiliza ponto/profundidade em cada amostra orbital; preserva clipping original, posições recebidas, paths, ícones, hitboxes e transmissões. A reconstrução offscreen caiu de p50 12,184/12,232 ms para 6,891/7,041 ms em ordenação ABBA, eliminando 1.737 projeções por rebuild. O perfil original atribui 0,171 s de 0,238 s cumulativos às projeções em dez chamadas.
- O timer continua com 33 ms e clamp de interpolação de um segundo; apenas pedidos de pintura de fase idêntica são evitados. Rastros determinísticos de 120 callbacks passam de 120 pedidos a um no caso parado, ou 31 com relógio ativo. São contagens de agendamento, não medição de GPU/CPU. Câmera, modelo, tema e seleção mantêm invalidação independente.
- `geo_view.py`: evita texto/layout repetidos, mantendo invalidação por modo, título, dimensões, controles, alertas e inventário. Microensaio p50 0,054→0,012 ms, ganho absoluto pequeno.
- `topology.py`: mede cada texto distinto uma vez por coluna, sem cache persistente ou alteração de linhas/valores/fontes/limites. Fixture de 512 enlaces: `_set_links` p50 6,685→1,299 ms; chamadas nativas de medida 2.048→20. Não se atribui esse resultado ao conjunto de 131 enlaces nem ao frame completo.
- Cache adicional de geometria WaveChart foi rejeitado: lookup p50 0,0037 ms contra cálculo atual 0,0020 ms. Não houve migração de linguagem nem alteração de protocolo, aquisição ou relógio científico.

As regressões GEO passaram 46 casos; tabela de enlaces passou 28 casos novos/focados e duas regressões existentes. As séries e dados capturados permanecem no pipeline original. Evidências: [GEO](../../outputs/orionq_gpu_20261008/geo/RESULTS.md), [microperfil GEO](../../outputs/orionq_gpu_20261008/geo/measurements/report.json), [textos de enlaces](../../outputs/orionq_gpu_20261008/ui/LINK-TEXT-OPTIMIZATION.md).

## Execução posterior de três minutos

| Janela do soak | Amostras | RSS UI p50 / p95, MiB | RSS árvore p50 / p95, MiB |
|---|---:|---:|---:|
| 0–60 s | 112 | 672.98 / 678.31 | 786.77 / 792.08 |
| 60–120 s | 112 | 683.59 / 694.76 | 797.39 / 808.57 |
| 120–180 s | 112 | 700.98 / 704.54 | 814.83 / 818.38 |

Comparando medianas dos primeiros/últimos 30 s, RSS UI cresceu **34.92 MiB (5.23%)**; tendência linear 14.30 MiB/min. O máximo observado foi 707,32 MiB. O renderizador ativo/instâncias nunca excedeu um nas amostras; a redução de pedidos repetidos não prova ausência de vazamentos.
O diagnóstico acumulou 8.09 MiB de objetos Python alcançáveis em seus coletores ao final. Esse valor é o total de todas as fases, não o aumento no soak nem RSS, e não explica sozinho o crescimento. O backend continuou aumentando contadores de recibos e auditoria; não se deve subtrair todo crescimento como “overhead conhecido”. A amostra é de três minutos, não 30 minutos. A [análise de limites de memória](../../outputs/orionq_gpu_20261008/ui/MEMORY-LIMITATIONS.md) distingue contagens observadas de limites do código e descreve a instrumentação ainda necessária, preservando consumo durável e proteção contra replay.
No depois: **60 readbacks completos**, zero mismatches de seleção/conteúdo/modo/abas/hitboxes, zero falhas de dispatch, zero violações do prazo diagnóstico de 5.000 ms, auditoria autenticada válida com 4116 registros e nenhum registro sem checkpoint. O prazo diagnóstico não altera os critérios de produto e seu sucesso não significa atualização instantânea.
O trabalho científico continua sendo um gargalo separado: último passo de rede **5.690 ms** para período solicitado de 1 s, 48 overruns. Clock científico de 48 s e backlog de protocolo de 0 s ao final; 190 eventos pendentes futuros. Mantiveram-se os cálculos e a causalidade. A UI pode responder mais depressa enquanto o próximo dado científico ainda não foi produzido.

## Histórico dos protótipos GPU — não é implantação

Os protótipos usaram contexto OpenGL real da RTX 3060, mas pioraram as métricas principais. Os testes ABBA tinham duas execuções raster e duas OpenGL com mesmas ações; o ensaio de cache tinha uma de cada e requer cautela adicional. Esses arquivos permanecem preservados.

| Variante histórica | Fase | Pintura p50 raster, ms | OpenGL, ms | Δ OpenGL |
|---|---|---:|---:|---:|
| Viewport OpenGL (ABBA) | Mapa | 2.80 | 23.43 | +738.0% |
| Viewport OpenGL (ABBA) | Globo | 13.99 | 25.51 | +82.3% |
| Viewport OpenGL (ABBA) | Abas | 12.97 | 26.18 | +101.8% |
| Viewport OpenGL (ABBA) | Alternâncias | 15.09 | 26.21 | +73.6% |
| Viewport OpenGL + cache | Mapa | 2.56 | 8.04 | +213.7% |
| Viewport OpenGL + cache | Globo | 17.73 | 21.76 | +22.7% |
| Viewport OpenGL + cache | Abas | 17.77 | 21.53 | +21.2% |
| Viewport OpenGL + cache | Alternâncias | 17.89 | 22.39 | +25.2% |

Não se habilita globalmente o viewport OpenGL com base nesses resultados. Os contadores `nvidia-smi` são do dispositivo compartilhado; outro OrionQ e outras aplicações permaneceram abertos, somente observados. A carga GPU não pode ser atribuída ao OrionQ a partir deles.

Esta parte do relatório não qualifica uma implementação GPU em produção, outro driver, outra GPU ou um executável compilado. Eventual integração GPU especializada e fallback precisam de evidência própria; os ganhos CPU acima não podem ser reutilizados como seus ganhos.

Evidências históricas: [ABBA](../../outputs/orionq_gpu_20261008/gpu-prototype/comparison-abba.json), [cache](../../outputs/orionq_gpu_20261008/gpu-prototype/comparison-cache-first.json).

## Protótipo especializado de órbitas — comparação pareada

Um segundo projeto de composição conserva o QWidget como fonte de controles, dados e hit tests, mas apresenta seus paths orbitais como itens nativos da cena. A proxy mantém cache de apresentação. A pintura completa continua disponível para `render`/`grab` e para o fade; não se interpolam novas posições científicas. Todos os arquivos dessa variante permanecem em `outputs`, sem habilitação no código de produção.

O [ensaio nativo v2](../../outputs/orionq_gpu_20261008/gpu-prototype/qual-orbit-bridge-dpr1-v2/qualification.json) aprovou oito casos com os nove satélites, 18 paths front/back, animação, hits e popup, redimensionamento, vinte ciclos Mapa/Globo, três câmeras até zoom 6, temas dark/light/dark, restauração CPU por callback simulado e descarte das janelas próprias. Foram liberados 23 bridges. O callback não reproduz falha real do driver; DPR 1,5/2 e todos os painéis flutuantes da aplicação completa não estão qualificados por esse fixture.

A [comparação](../../outputs/orionq_gpu_20261008/gpu-prototype/comparison-bridge-first.json) entre `raster-optimized-2` e `opengl-bridge-1` passou os critérios de comparabilidade: mesmas fontes já otimizadas, configuração, plano, períodos de 20 s, soak zero e verificações funcionais. Houve uma execução de cada variante, sem profiling nem captura de tela no passe de desempenho; os valores não constituem repetição estatística.

| Fase | Pintura p50 CPU → GPU, ms | Pintura p95 CPU → GPU, ms | Heartbeat p95 CPU → GPU, ms | Ação → pintura p95 CPU → GPU, ms | RSS p50 CPU → GPU, MiB |
| --- | ---: | ---: | ---: | ---: | ---: |
| Mapa | 1,98 → 6,42 | 35,20 → 46,56 | 41,96 → 44,72 | 630,53 → 624,16 | 624,80 → 742,24 |
| Globo | 8,47 → 5,89 | 37,83 → 29,45 | 31,23 → 34,74 | 264,68 → 273,33 | 636,81 → 750,89 |
| Abas | 10,25 → 5,63 | 36,36 → 28,34 | 18,61 → 18,67 | 577,95 → 529,86 | 636,67 → 755,08 |
| Alternâncias | 13,45 → 19,30 | 44,27 → 52,61 | 23,43 → 24,71 | 138,49 → 261,67 | 664,32 → 781,36 |

O Globo ganhou 30,4% em pintura p50 e 22,1% em p95, mas seu heartbeat p95 aumentou 11,2%. A latência p95 das alternâncias aumentou 88,9%, e o RSS aumentou cerca de 114–119 MiB. Portanto, esse resultado não aceita ativação global automática: custo de pintura menor em uma fase não prova melhora de responsividade no uso misto. A composição especializada é uma evidência distinta das regressões do viewport simples.

A documentação oficial do [QOpenGLWidget](https://doc.qt.io/qt-6.11/qopenglwidget.html#limitations-and-other-considerations) informa que adicionar o primeiro widget OpenGL após mostrar a janela pode recriar sua janela nativa e que a composição do topo passa a usar OpenGL. Assim, uma ativação restrita ao Globo exige medir as trocas reais e qualificar a preservação da identidade da janela; remover o viewport não deve ser presumido equivalente a reiniciar toda a composição em CPU.

## Ativação GPU restrita ao Globo — rejeitada para implantação

O candidato adaptativo usa raster/NoCache no Mapa e em outras abas, e OpenGL/cache com a bridge somente quando um Globo está visível e sem painéis sobrepostos não qualificados. Libera o viewport GL ao sair; não mantém um GL oculto. A marca de diagnóstico acompanha a mudança de HWND. Isso não implementa o handler de identidade operacional na produção.

O primeiro passe, [adaptive-1](../../outputs/orionq_gpu_20261008/gpu-prototype/adaptive-1/launcher.json), terminou sem timeout, mas falhou ao montar o relatório: o monitor conservava uma referência ao viewport que `setViewport` havia destruído. Não se aceita esse passe como benchmark concluído. O wrapper v2, separado das fontes, redireciona apenas essa consulta geométrica final para o viewport atual do mesmo stage. Os observers, fases e dados científicos permanecem iguais.

A [qualificação v1](../../outputs/orionq_gpu_20261008/gpu-prototype/qual-adaptive-dpr1-v1/qualification.json) também foi preservada com falhas: duas asserções presumiam dois itens de cena, embora o fixture sem popup tivesse uma proxy; a contagem permaneceu constante em vinte ciclos. A terceira falha era funcional: a política reativava GPU depois do callback de perda de contexto. O candidato v3 notifica a política antes de liberar a bridge e enfileirar o retorno à CPU; a falha bloqueia reativação automática naquela instância.

A [qualificação v2](../../outputs/orionq_gpu_20261008/gpu-prototype/qual-adaptive-dpr1-v2/qualification.json) aprovou oito casos: conteúdo com nove satélites/18 paths e cobertura de 98,15% contra a referência completa, nove hits, NodeWindow real com atualização passiva sem mouse, CommandPalette translúcida, vinte trocas com seleção preservada, callback de contexto retornando a CPU e bloqueando reativação, e descarte. O fixture usa uma topologia capturada; os painéis vazios identificados como fixtures não são evidência de telemetria. Não houve reprodução de crash real de driver. O HWND mudou na primeira entrada GL e ficou estável depois; a superfície do topo permaneceu OpenGLSurface também nos ciclos com viewport raster.

O [passe adaptive-3](../../outputs/orionq_gpu_20261008/gpu-prototype/adaptive-3/report.json) terminou com exit 0, sem timeout, erros ou mutações de fontes. A [comparação pareada](../../outputs/orionq_gpu_20261008/gpu-prototype/comparison-adaptive-third.json) com `raster-optimized-2` passou os mesmos critérios do ensaio anterior. Foram 16 transições, oito contextos GL reais e zero viewports GL ativos ao final, sem falha de contexto naquele passe. Só a consulta diagnóstica de geometria de um viewport já apagado foi redirecionada. Uma execução por variante continua sendo uma evidência piloto.

| Fase | Pintura p50 CPU → adaptativo, ms | Heartbeat p95 CPU → adaptativo, ms | Ação → pintura p95 CPU → adaptativo, ms | RSS p50 CPU → adaptativo, MiB |
| --- | ---: | ---: | ---: | ---: |
| Mapa | 1,98 → 2,08 | 41,96 → 46,66 | 630,53 → 972,50 | 624,80 → 623,05 |
| Globo | 8,47 → 14,60 | 31,23 → 86,36 | 264,68 → 1.017,72 | 636,81 → 759,68 |
| Abas | 10,25 → 11,73 | 18,61 → 20,96 | 577,95 → 224,27 | 636,67 → 755,61 |
| Alternâncias | 13,45 → 15,05 | 23,43 → 30,28 | 138,49 → 280,83 | 664,32 → 780,25 |

A política restrita elimina a regressão de pintura mediana do Mapa antes da primeira ativação, mas a criação/liberação de contextos durante o uso misto perde o ganho do Globo e aumenta a latência das alternâncias em 102,8%. Não se promove essa implementação nem se declara que o OrionQ já utiliza GPU automaticamente. Permanecem implementadas as três otimizações CPU e a decoração estática validada. Os protótipos, suas falhas e seus resultados foram mantidos para revisão.

Há benefício de composição especializada em um cenário contínuo de Globo, mas não há benefício líquido demonstrado para ativação automática com esse perfil da aplicação. As [condições de integração futura](../../outputs/orionq_gpu_20261008/gpu-prototype/GPU-INTEGRATION-CONDITIONS.md) exigem injeção por instância, anexação transacional, identidade acompanhando HWND, diagnóstico limitado, overlays/DPR/tema, release e ganho no workload completo. A GPU não corrige por si só o processamento científico de 5,69 s observado no backend.

Escopo entregue: código fonte CPU, arte e validações; nenhum novo `.exe` foi compilado nesta alteração. Os testes gráficos são nativos Windows a partir da fonte, não qualificação de um novo binário. Modelos opcionais, parâmetros científicos, autorização de serviços e bloqueio de atuação física foram preservados.

## Reprodução das medições concluídas

Execute cada cenário isoladamente, com o mesmo monitor/configuração e sem outro benchmark simultâneo. Os diretórios de evidências existentes são imutáveis; use um diretório novo.

```powershell
.audit-winvenv\Scripts\python.exe tools\benchmark_console_performance.py --output outputs\orionq-performance-nova-medicao --duration 20 --soak 3 --size 3440x1440
```

O antes está identificado pelos hashes registrados nos seus arquivos; executar a árvore atual não recria o antes. O relatório compara arquivos concluídos, não reinicia medições nem altera dados do operador.
