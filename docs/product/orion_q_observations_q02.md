# ORION-Q — Q02: observações numéricas e identidade temporal

Data: 12/09/2026. Incremento de código sobre a base v5.38.0, após Q01. Não é um novo executável distribuído.

## Entrega

Foi introduzido `Observation`, um contrato imutável independente de Qt, identificado como `orion.observation.v1`. O `Snapshot` transporta uma tupla de observações, além dos componentes de apresentação existentes.

As entradas JSON medida e artificial agora constroem observações a partir dos números originais, antes de formatar cartões e enlaces. Não há recuperação de valores por parsing de rótulos ou tabelas arredondadas.

| Quantidade | Unidade canônica | Exemplo de apresentação |
|---|---|---|
| BER, QBER e fidelidade | `fraction` | QBER `0.021234567891` pode aparecer como `2.12%`; o valor exportado permanece original |
| SKR | `bit/s` | `1250` pode aparecer como `1.25 kb/s` |
| OSNR | `dB` | Sem conversão implícita para potência ou fidelidade |
| Potência óptica | `dBm` | Sem conversão implícita para outra grandeza |

Além da quantidade, valor e unidade, cada observação mantém fonte, campanha, entidade/enlace, canal, dispositivo, identificador da medição e proveniência. Valores ausentes não geram observações; não são substituídos por zero. Valores booleanos, strings formatadas, NaN, infinito e inteiros excessivos são recusados como dados numéricos.

## Tempo e qualidade

- `acquired_at`: timestamp fornecido pela fonte; no contrato de integração, representa a conclusão da aquisição, não necessariamente o centro da janela.
- `received_at_utc`: primeira leitura válida daquela amostra pelo adaptador local. Não é horário de chegada de um fóton ou medida calibrada de latência de rede.
- `clock_domain`: `utc` ou `simulation`. O relógio virtual do gerador não é comparado ao relógio de parede para alegar frescor de dados reais.
- `clock_id`: identificação opcional, declarada pela fonte. Ausência fica explícita; presença não comprova sincronização.
- `integration_start` e `integration_end`: opcionais e inseparáveis; devem estar ordenados e terminar até o instante de conclusão da aquisição.
- `standard_uncertainty`: opcional, não negativa, na mesma unidade canônica da observação. Não é automaticamente um intervalo de confiança.
- `fidelity_method` e `fidelity_reference`: metadados exclusivos de fidelidade. A referência ausente permanece desconhecida; fidelidade genérica não é rebatizada como fidelidade de Bell.

`time_quality` distingue relógio virtual, relógio não informado, relógio declarado não verificado e horário da fonte adiantado em relação à primeira leitura local. A tolerância de frescor já existente no plug continua valendo; nenhum limite de precisão instrumental é inferido.

Na tela Telemetry da entrada medida, as condições incluem aquisição, primeira leitura, relógio, qualidade temporal e janela de integração. Quando há fidelidade, método e referência também são apresentados. A composição visual foi mantida.

Valores longos nesse painel têm quebra de linha, tooltip completo e rolagem própria, sem redução da fonte. A revisão visual também identificou rótulos antigos sobrepostos na barra superior durante atualizações sucessivas; a remoção agora os oculta e desvincula imediatamente antes da liberação adiada do Qt.

## Proteção temporal por enlace

Antes, a ordenação principal verificava o timestamp do envelope. Agora, um envelope mais recente também precisa respeitar o histórico de aquisição de cada enlace:

1. Timestamp regressivo de um enlace é recusado.
2. Mesmo enlace e mesmo timestamp com conteúdo diferente são recusados.
3. Mesmo conteúdo/amostra preserva o primeiro recebimento, inclusive ao reler o arquivo, selecionar outro painel ou atualizar apenas o envelope.
4. Mudança de canal, dispositivo, extremidades ou relógio sob um ID de enlace existente requer reconexão explícita.
5. O histórico é atualizado apenas quando o frame completo passa; uma linha inválida não confirma parcialmente as demais.
6. A estrutura aceita até 4096 IDs por conexão, com recusa explícita ao exceder o limite. Não é um arquivo histórico de telemetria nem substitui Q06.

Entrada inválida limpa observações e dados visuais. Os guardas de ordenação permanecem para impedir que a recuperação aceite amostras regressivas. Reiniciar/reconectar inicia uma nova sessão de guarda; persistência dessa sequência ainda não foi implementada.

## Extensão opcional do JSON de aquisição

O envelope `orion.telemetry.v1` e as unidades existentes permanecem compatíveis. Cada linha de enlace pode acrescentar:

```json
{
  "clock_id": "acquisition-clock-01",
  "integration_start_utc": "2026-09-12T10:00:00Z",
  "integration_end_utc": "2026-09-12T10:00:01Z",
  "standard_uncertainties": {"qber": 0.00012},
  "fidelity_reference": "bell_phi_plus"
}
```

Este trecho é ilustrativo e não é um frame completo nem uma medição real. A linha precisaria conter QBER para declarar sua incerteza e fidelidade com método válido para declarar sua referência; o timestamp de aquisição deve ser igual ou posterior ao fim da integração. A janela declarada aplica-se às métricas daquela linha. Omitir metadados desconhecidos em vez de enviar `null` ou preencher valores estimados.

Campos novos continuam sujeitos à validação. Uma incerteza não pode referenciar uma métrica ausente. Os arquivos de exemplo com timestamps históricos não são entradas ao vivo válidas: preservar sua data e aguardar o modo replay apropriado, sem falsificar frescor.

## Alinhamento e limites científicos

O helper `require_declared_alignment` estabelece uma precondição conservadora para futuras integrações: mesma fonte/campanha/enlace/canal/dispositivo/relógio declarado, proveniência medida, janelas compatíveis e tolerância temporal explícita. Timestamps são comparados com timezone.

Ele não realiza junção de datasets, não verifica fisicamente o relógio, não calibra BER→fidelidade e não demonstra informação preditiva. Casos com instrumentos distintos exigirão um contrato de sincronização adicional, não uma flexibilização silenciosa. A campanha científica pareada existente não foi alterada nesta entrega.

## Cobertura e continuidade

- Migrados: números das métricas principais e dos enlaces nas entradas medida e artificial; exportação JSON preserva a tupla de observações.
- O gerador mantém seed, sinais e relógio virtual reproduzíveis. Gráficos e outros elementos demonstrativos continuam fixtures identificadas como artificiais; não foram convertidos em aquisições instrumentais.
- Não migrados: todos os registros históricos/modelos/QKD do provider de serviço. Eles continuam nos contratos anteriores, com `observations=()` neste novo campo. Não foram inventados timestamps ou identificadores para preencher essa lacuna.
- Pendentes: histórico/replay Q06, associação entre instrumentos distintos, migração completa das trilhas do serviço e UI de importação CSV/Parquet Q18.
- Próximo incremento do produto: Q03, cadastro e topologia persistentes ligados ao backend, apoiados nesta base de identidade e nas capacidades Q01. Q04 e Q05 permanecem pendentes.

## Verificação

Testes cobrem preservação de precisão, conversões de apresentação, ausência de métricas, metadados inválidos, diferenças de canal/campanha/relógio, recebimento estável, regressões por enlace, confirmação atômica do frame, limpeza na troca de fonte e limites do histórico. Todas as entradas medidas usadas nos testes são fixtures de software explicitamente identificadas, não dados experimentais.

Evidências da execução desta entrega: `outputs/software_validation_observations_q02/`. Os testes são locais e a renderização é offscreen; não há atuação física, envio IBM ou novo resultado científico.

Resultados registrados:

- Rodada integrada de observações, capacidades, fontes, telas, serviço, integração e proteção física: **146 testes aprovados** (`accepted-tests.xml`).
- Após os ajustes visuais finais, rechecagem de observações, entrada e telas: **87 testes aprovados**, sem falhas/erros (`final-ui-tests.xml`). Esta rodada se sobrepõe à anterior; os números não devem ser somados como casos distintos.
- Autoteste final em código-fonte: **19 verificações principais**, incluindo uma matriz de **71 verificações de responsividade/entrada**, todas aprovadas (`self-test-final.json`).
- Captura final de telemetria medida revisada em `responsive/orion-measured-telemetry-SOFTWARE-FIXTURE.png`; a imagem usa uma fixture, não equipamento.

Não foi executada toda a suíte científica do repositório nem recompilado o `.exe` anteriormente entregue.
