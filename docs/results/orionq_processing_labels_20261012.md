# OrionQ — origem do processamento por enlace

Data: 12/10/2026. A lista de enlaces ganhou a coluna **PROCESSING / DATA**; o inspector apresenta **Data origin**, **Processing**, **Physics / protocol** e **Acting controllers**. Tooltips da matriz e dos enlaces de um node e os cartões flutuantes mostram as mesmas informações recebidas. As cinco colunas anteriores, IDs, seleção e valores científicos são preservados.

A classificação utiliza os IDs efetivamente publicados por `LinkProcessor._controllers()`/`LiveLink.controllers`, seus estados e a classe do adaptador registrada no snapshot de protocolos. O nome da instituição e o switch global de ML não determinam a classificação. A origem do dado — simulado, medido, arquivo de medição, ausente ou retido — é apresentada separadamente. Não há nova consulta ao serviço, abertura de pesos, ativação de modelo ou cálculo científico na pintura.

| Rótulo | Significado |
|---|---|
| `REFERENCE` | Controlador/implementação matemática de referência conhecido no registro recebido |
| `REF + MODEL` | Física de referência com modelo do projeto reportado ativo naquele enlace |
| `REF / WARMUP` | Controle de referência; detector do projeto ainda coleta sua baseline e não se declara inferência concluída |
| `NO SAMPLE` | Não existe registro de processamento recebido; configurar um componente não comprova atividade |
| `UNREPORTED` | Identidade/estado insuficiente, inconsistente ou adaptador personalizado sem classificação conhecida |
| `/ FALLBACK` | Estado de fallback explícito do enlace; ID efetivamente atuante permanece identificável |

O detector boosting distribuído pode operar junto de compensação, sincronização e taxa de chave de referência mesmo quando o controle ML está desligado. Ele aparece como modelo do projeto somente após o período de baseline; sua existência não transforma a origem dos dados nem certifica uma decisão. IDs de modelos conhecidos com estado desabilitado, indisponível, rejeitado ou não informado não são apresentados como execução ativa.

TF-QKD, satélite–solo, ISL, relay orbital, swapping, memória e purificação recebem descrição de referência somente quando o backend informa uma implementação conhecida registrada. Uma configuração sem observações permanece `NO SAMPLE`. Uma classe nova/desconhecida não herda esse rótulo pelo nome da tecnologia. `REFERENCE` identifica a implementação computacional atual: não certifica equipamento, calibração, chaves QKD ou maturidade experimental.

## Verificações

- **Windows: 75/75 casos aprovados**, 201,36 s, Qt offscreen. Inclui tabelas, atualização incremental, cartões flutuantes, séries de protocolo, login real pelo fluxo de console e seleção do node.
- **Linux: 27/27 casos aprovados**, 221,58 s, Ubuntu/WSL, UID 10001, Python 3.12.3 e Qt offscreen. O fonte atualizado foi selecionado explicitamente; não se atribui essa aprovação ao wheel/executável anterior.
- O teste real de `LinkProcessor` percorre 62 ticks com fonte simulada e controles de referência, verificando a transição `REF / WARMUP` → `REF + MODEL` após a primeira saída efetiva do detector.
- O teste TF-QKD percorre três passos reais da demonstração e confere o rótulo `REFERENCE | SIMULATED`, a identificação `TwinFieldAdapter` e o inspector a partir do mesmo snapshot.
- Testes adversariais cobrem nomes de instituições, origem medida/simulada, fallback explícito, modelos inativos, IDs desconhecidos, adaptação personalizada, configuração sem amostras, preservação dos registros e ausência de I/O no helper.
- Ruff aprovou o helper e o teste novos. Os quatro módulos de UI preservam 42 avisos E701/E702 anteriores, com zero diagnóstico novo; não foi aplicada formatação geral a alterações existentes.

A projeção textual foi medida separadamente em uma fixture fabricada de 136 enlaces, 1.000 iterações: p50 **0,554 ms**, p95 **0,592 ms**, máximo **1,014 ms**. Isso mede somente a criação dos rótulos, não a latência da engine, o frame Qt ou o desempenho da demonstração completa.

As tentativas negativas foram preservadas: importação de nome incorreto na nova fixture, erro de escopo introduzido e corrigido no cartão, ID histórico de teste inexistente e seleção isolada de um caso que depende do login anterior do roteiro compartilhado. O par login + node passou separadamente e na rodada final, sem contornar autenticação. Não houve alteração de física, sementes, protocolos, autorização ou atuação.

[Qualificação e hashes](../../outputs/defect-corrections-20261012/processing-labels/labels-qualification.json), [JUnit Windows](../../outputs/defect-corrections-20261012/processing-labels/labels-qualified.xml), [JUnit Linux](../../outputs/defect-corrections-20261012/processing-labels/labels-linux.xml) e [custo isolado de apresentação](../../outputs/defect-corrections-20261012/processing-labels/labels-cost.json). Os tempos das suítes são durações de testes em condições distintas, não uma comparação de desempenho Windows/Linux.
