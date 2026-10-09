# Alocação híbrida — sessão gráfica shadow artificial

Data: 24/09/2026. Incremento Q16 em código-fonte após a ponte tipada.
O provider artificial agora publica observações de alocação no Snapshot comum;
o catálogo verificado abre a sessão nativa para persistência ou Ridge, sempre
OFF até START explícito. [Uso e contrato](../product/orion_q_allocation_shadow_bridge.md#sessão-gráfica-shadow--atualização-em-24092026).
Nenhuma recomendação é aplicada ao controlador, à fila ou ao estoque de chaves.

## Integração entregue

- `Snapshot.optional_allocation_state`, vazio por padrão, transporta a entrada
  própria do alocador; fontes medidas não alimentam esta sessão.
- Produtor determinístico por semente/enlace, sem ler pacotes ou carregar modelos,
  avança 5 s virtuais por publicação. Mantém cinco estimativas passadas observadas;
  lacunas, rollback, perda ou mudança de contexto iniciam outro segmento vazio.
- Fila 6×11 fixa e demais observáveis exógenos, sem consumo de recomendações.
  O nó identifica o publicador; não remapeia os 11 enlaces da planta para a
  topologia da console. Os relógios de timing/SO(3)/alocação são separados.
- Catálogo exige seleção e verificação do ZIP; abrir a sessão não inicia inferência.
  START/STOP controlam somente a avaliação shadow. A tela mostra admissão por
  enlace versus persistência, aquecimento e instante virtual previsto de efeito.
- Uma tarefa de inferência por sessão, sem fila. Entrada nova invalida o resultado
  do worker anterior; STOP, fechamento, expiração e perda de contexto também o
  descartam. Uma falha encerra a execução automática.
- Watchdog de 3,5 s reais sem nova observação; duplicatas não renovam frescor.
  Reordenação ou alteração com timestamp repetido é recusada. O catálogo verifica
  novamente o payload ao reabrir, protegendo também contra mutação em memória.
- EXPORT FRAME captura o registro no clique e recusa sobrescrita. Novas amostras
  válidas não trocam o registro capturado; perda de contexto cancela a escrita.

Não foi implementado pareamento de recomendações com resultados contrafactuais.
Os estoques posteriores da fixture não são resultados das admissões recomendadas.
Uma diferença de admissão não mede vazão, chaves entregues ou ganho em laço fechado.
Ridge com zero a quatro amostras anteriores é explicitamente `warmup_persistence`;
persistência não é inferência aprendida. Orçamentos permanecem BB84 assintóticos,
sem certificado de chave finita, promessa de entrega ou conversão para fidelidade de Bell.

## Diagnóstico final com o pacote original

Aceite: `outputs/allocation_shadow_ui_20260924/author-final/`.
Doze registros completos, dois snapshots iniciais, quatro capturas e `report.json`
com hashes individuais. O driver usa o provider, o catálogo, a verificação em
worker e os botões nativos por eventos de mouse; não substitui o modelo por mock.
Os relatórios declaram zero comandos emitidos e nenhuma ação física ou mutação de fila.

ZIP QNET SHA-256:
`6b8563209f54a2d566f1a4229b140b75fda93555085ff2bf6bfcad8f07abd938`.
Planta SHA-256:
`881213fab3f992e5ba86c66aac82d6cf82df93405e0d450de123c39236137b50`.

| Opção | Observações | Histórico anterior | Status final | Avaliações aceitas |
|---|---|---|---|---:|
| Persistência | ticks 0–5 | 0–5 amostras | `persistence` em todas | 6/6 |
| Ridge | ticks 0–4 | 0–4 amostras | `warmup_persistence` | 5/5 |
| Ridge | tick 5 | 5 amostras | `ridge` | 1/1 |

Capturas em 640×480 e 1000×700 com tema/fontes reais; controles conferidos dentro
da janela e capturas representativas inspecionadas visualmente. A área de resultado
rola quando necessário. A validação gráfica foi offscreen no ambiente local,
não aceite manual em outro computador Windows.

Reprodução na raiz, com ambiente instalado e saída nova:

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
.\.audit-winvenv\Scripts\python.exe tools/validate_allocation_shadow_ui.py --qnet-zip F:/qnet_network.zip --output outputs/minha-allocation-ui
```

## Verificação de software

| Rodada | Resultado | Duração | Artefato |
|---|---:|---:|---|
| Regressão ampliada da console, providers, opcionais e gates | 581 aprovados | 249.581 s | `regression.xml` |
| Rechecagem final do catálogo e sessões gráficas | 145 aprovados | 30.621 s | `final-ui.xml` |

Artefatos em `outputs/allocation_shadow_ui_20260924/`. Zero falhas, erros ou casos
ignorados. As rodadas têm sobreposição e **não devem ser somadas**; a rechecagem
final inclui 45 casos da nova sessão e a proteção adicional de reabertura contra
payload alterado. Os 44 testes da primeira rodada focada também se sobrepõem.
Não é a suíte integral do repositório; os 407 testes da ponte anterior e os 500
da campanha SO(3) são registros de outras etapas, não aprovações adicionais aqui.

Os testes cobrem causalidade e aquecimento, reinício após lacunas, ausência de
carregamento automático, fila preservada, isolamento por fonte/campanha/relógio/
planta, entrada incompatível, duplicatas, expiração, workers tardios, recusa do
ZIP alterado, exportação durante novos frames/perda de contexto, layout pequeno
e ligação Edge Control/provider sem comandos. Os testes unitários usam pacotes
sintéticos; o diagnóstico acima valida separadamente os pesos originais.

Ruff passou nos sete arquivos Python alterados/criados. YAML do workflow e
sintaxe PowerShell do script de build foram conferidos. O novo módulo de testes
foi acrescentado aos gates Windows/local; nenhum build Windows ou CI remota foi
executado. Os pesos continuam externos e os executáveis anteriores preservados.

## Continuidade

[Roadmap atualizado](../product/roadmap.md): Q16 continua parcial; próxima ponte
shadow é roteamento, com estado/candidatos explícitos e contexto de ações legais
ou recursos, sem executar decisões. Q05 permanece pendente de revisão/ativação
com readback e rollback. A campanha SO(3), os contratos científicos existentes e
o trabalho anterior foram preservados. Modelos opcionais, OFF por padrão;
nenhuma autoridade física é criada pela presença da interface.
