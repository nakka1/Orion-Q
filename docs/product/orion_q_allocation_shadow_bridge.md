# Ponte shadow do alocador híbrido — 24/09/2026

Incremento Q16 em código-fonte: `qnet.allocator.persistence` e
`qnet.allocator.ridge` podem receber um snapshot tipado da planta simulada de
11 enlaces. O resultado é uma recomendação comparada com persistência sobre a
mesma observação, histórico e fila comprometida. Não altera topologia, filas,
estoque de chaves, política ou journal de comandos. Nenhum executor é chamado.

`evaluate_optional_allocation(snapshot, model_id=None, archive_path=None)` é OFF
por padrão: não lê nem a entrada nem o ZIP. O método explícito
`ControllerRuntime.observe_optional_allocation(...)` delega ao mesmo avaliador,
sem executar `cycle()`. Não há ativação automática ou binding Q05.

## Contrato

Campo: `NetworkStateSnapshot.telemetry.optional_allocation_window`.
Esquema: `orion.sandbox-allocation-window.v1`.

| Campo | Significado e validação |
|---|---|
| `source_id`, `node_id` | Coincidem com fonte da proveniência e nó do snapshot. |
| `campaign_id`, `clock_id` | Coincidem com `metadata.campaign_id` e `clock_id`. |
| `stream_id` | Coincide com `metadata.allocation_stream_id`; nenhum histórico global implícito. |
| `plant_sha256` | SHA-256 hexadecimal da planta `model/plant.json` do export admitido; coincide com `metadata.allocation_plant_sha256` e, após avaliação, com o hash da planta carregada. |
| `origin_timestamp_utc` | Origem explícita do relógio relativo, com fuso; mesmo instante de `metadata.allocation_origin_timestamp_utc`. |
| `observation` | `AllocationSample` atual: timestamp UTC, QBER, Raman, fila, estoque, backlog e urgência por enlace. |
| `history` | Zero a cinco `AllocationDriftSample`, cada um com timestamp UTC e 11 derivas observadas/derivadas no passado. |
| `link_names` | Os 11 nomes exatos do [contrato offline](orion_q_optional_allocator.md), na ordem revisada. Sem remapeamento para a topologia corrente. |
| `protocol` | Somente `single_photon_bb84`; recusa entradas decoy-state. |
| `period_s`, `latency_s`, `horizon_s` | Respectivamente 5, 30 e 150 segundos. |
| `qber_unit`, `volume_unit`, `time_unit` | Respectivamente `fraction`, `bit`, `s`. |
| `queue_order`, `drift_definition` | `oldest_first` e `max(qber-raman-0.005,0)`; a definição identifica a deriva passada já fornecida. |
| `evidence` | Somente `simulated`; proveniência do snapshot também simulada, integridade declarada verificada e flag OOD falsa. |

`observation` contém `timestamp_utc`, `measured_qber`, `raman_qber_estimate`,
`pending_admission`, `key_pool_bits`, `backlog_bits`, `urgency_s`.
Os dois QBERs e as derivas históricas são frações finitas em [0,1]; estoques e
backlogs são finitos e não negativos. Cada urgência é positiva em segundos ou
`null` para ausência de prazo. Booleanos não são números admitidos.

A fila é uma matriz 6×11, mais antiga primeiro, com níveis 0, 0,25, 0,5, 0,75 ou 1.
A recomendação respeita os níveis da planta revisada, mas não certifica legalidade
ou disponibilidade de ações numa rede real. A fila fornecida não é avançada,
substituída pela recomendação ou reconstruída de campos do controlador.

O histórico deve preceder imediatamente a observação, sem duplicatas, lacunas ou
reordenação, a cada 5 s, nunca antes da origem do relógio. Timestamps são traduzidos
para segundos relativos e validados pelo parser offline existente. A idade da
observação em relação ao snapshot deve estar entre 0 e 5 s, inclusive. Esse limite
é uma regra de admissão do replay, não uma garantia de deadline físico ou de
frescor em tempo de parede. O tempo previsto de efeito é observação +30 s.

A ponte não reconstitui as observações históricas a partir das derivas declaradas
nem autentica seus timestamps. `integrity_verified` e OOD falso são declarações,
não certificação de instrumento ou estimador de domínio. Campos extras e
referências futuras no contrato são recusados. Telemetria WDM, offset de
sincronização, SO(3), fidelidade de Bell e `telemetry_age_s` genérico não substituem
os observáveis próprios nem a idade do alocador.

## Resultado e isolamento

| Status | Interpretação |
|---|---|
| `off` | Modelo não selecionado; nenhum acesso à entrada/pacote. |
| `unavailable` | Janela ou arquivo externo ausente. |
| `refused` | Contrato/contexto incompatível, planta divergente ou falha na admissão/avaliação. |
| `evaluated` | Comparação shadow concluída; nenhuma ação aplicada. |

Cada chamada é independente e revalida o ZIP. Ela não acumula histórico entre
nós, campanhas ou chamadas. Repetir uma janela é replay explícito, não nova
amostra experimental. Os tipos congelam vetores/matrizes e o registro captura
snapshot e avaliação como strings JSON independentes de mutações posteriores.

`final_output_status` distingue `persistence`, `warmup_persistence` (Ridge com
menos de cinco amostras anteriores) e `ridge`. Persistência não é ML. Diferenças
entre admissões não medem ganho de vazão, entrega de chaves ou efeito contrafactual.

O registro inclui snapshot/hash, checkpoint, idade, instante UTC previsto de
efeito e `evaluation_json` com entrada/hash, identidade do ZIP/planta, previsões,
admissões e comparação com persistência. As flags `policy_activated`,
`physical_action_performed`, `simulator_action_performed` e `queue_mutated` são
sempre falsas. Orçamentos de chave continuam estimativas BB84 assintóticas, não
chaves entregues, estoque utilizável em KMS ou certificado de chave finita.
`null` em orçamento significa não calculado, não zero observado.

`export_allocation_shadow(result, path)` cria um arquivo novo e recusa
sobrescrita. Snapshot de entrada tem limite de 128 KiB; JSON duplicado,
não finito ou com overflow é recusado pelo loader. Falhas de API/serialização
levantam erro; incompatibilidades de telemetria produzem registro `refused`.

## Reprodução por código-fonte

Na raiz atual, com o ambiente instalado e o ZIP revisado disponível:

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
.\.audit-winvenv\Scripts\python.exe tools/validate_allocation_shadow.py --qnet-zip F:/qnet_network.zip --output outputs/minha-validacao-allocation
```

O diretório de saída deve ser novo. O diagnóstico gera quatro entradas artificiais,
quatro relatórios e `report.json`: duas opções × histórico vazio/completo. Verifica
paridade com a sessão offline e ausência de mutação; não é campanha científica
ou planta física validada. Um dos snapshots pode ser reavaliado explicitamente:

```powershell
.\.audit-winvenv\Scripts\python.exe -m quantum_autopilot.control_plane.optional_allocation --input outputs/minha-validacao-allocation/qnet.allocator.ridge-5-input.json --model qnet.allocator.ridge --qnet-zip F:/qnet_network.zip --output outputs/meu-replay-allocation.json
```

Omitir `--model` produz OFF sem ler o arquivo de entrada. Retorno 0 significa OFF
ou avaliação concluída; retorno 2 significa recusa/indisponibilidade/erro. O arquivo
de saída não pode existir. Pesos permanecem externos e não são incluídos no produto.

[Verificação deste incremento](../results/orion_q_allocation_shadow_20260924.md).
A atualização gráfica abaixo liga um produtor artificial à sessão shadow opt-in.
A sessão gráfica **offline** já existente continua separada. O EXE anterior não foi
reconstruído e Q05 permanece pendente. Ponte física, filas reais e avaliação em
laço fechado continuam fora deste incremento.

## Sessão gráfica shadow — atualização em 24/09/2026

1. Execute a console pelo código-fonte e escolha a fonte artificial.
2. Em **Edge Control → OPTIONAL MODEL CATALOG**, selecione
   `qnet.allocator.persistence` ou `qnet.allocator.ridge`.
3. Use **VERIFY LOCAL ZIP** com o pacote QNET revisado.
4. Abra **ALLOCATION · LIVE ARTIFICIAL SHADOW**; a sessão permanece OFF.
5. Clique **START SHADOW** para avaliar os snapshots recebidos. Leia a admissão
   clássica por enlace, a persistência comparadora, o status e o instante virtual
   previsto de efeito. **EXPORT FRAME** salva o registro selecionado em arquivo
   novo. **STOP / CLEAR** elimina o resultado e para novas avaliações.

`Snapshot.optional_allocation_state` é opcional e vazio em fontes que não o
fornecem. O provider artificial publica uma observação a cada atualização da tela,
avançando **5 s virtuais**, independentemente dos relógios de sincronização e
SO(3). Isso não é controle físico a cada 5 s nem evolução validada da planta.
O produtor não abre ZIPs, não carrega modelos e não recebe suas recomendações.
A identidade da planta original é declarada e conferida pela ponte contra o
pacote carregado; não há descoberta automática de pacotes ou topologia.

O sinal usa deriva por enlace `0.008 + 0.003*sin(tick/11 + fase + 0.2*i)`, fase
determinística pela semente e Raman `0.001 + 0.0003*cos(tick/17 + i)`.
A QBER artificial soma deriva, Raman e erro óptico 0,005. Estoque, backlog e
urgência também são fixtures exógenas, não consequências das recomendações.
A fila 6×11 é fixa: nível `((linha + enlace) % 5) * 0.25`, mais antiga primeiro.
Ela é reapresentada como entrada; não é deslocada, executada ou realimentada.
Isso permite inspecionar a integração, mas não medir vazão, chaves entregues
ou vantagem de controle a partir das diferenças de admissão.

São guardadas somente as cinco estimativas de deriva observadas anteriormente.
Ridge usa `warmup_persistence` nas cinco primeiras observações; a sexta já tem
cinco amostras passadas e usa `ridge`. Persistência mantém seu status próprio.
Lacuna, rollback, mudança de campanha/semente/nó ou perda de fonte reiniciam o
histórico e identificam outro segmento de fluxo; nenhuma amostra ausente é
preenchida. O nó identifica o contexto que publica a fixture: os 11 enlaces da
planta não são convertidos nos enlaces desenhados na topologia da console.

A interface aceita uma observação nova somente com timestamp crescente dentro
do mesmo contexto. Duplicatas não renovam o prazo de frescor; dados diferentes
com timestamp repetido ou reordenado são recusados. O catálogo também preserva
esse controle ao reabrir uma sessão, independentemente de atualizações dos outros
modelos. Sem nova observação por **3,5 s de tempo real**, o resultado perde validade
e um novo START é exigido. Esse watchdog difere do limite de idade virtual de
5 s aplicado pelo backend. Em perfis com publicação mais lenta, a sessão pode
expirar e exigir novo START; não se afirma atendimento de deadline físico.

Há no máximo um worker por sessão, sem fila de inferência. Uma entrada nova
invalida o trabalho anterior ainda em execução; seu resultado tardio é descartado.
Não se enfileira a amostra mais recente: uma publicação posterior pode iniciar
outra avaliação quando o worker terminar. Falha de avaliação para a execução
automática. STOP, fechamento, troca de pacote, fonte ou contexto invalidam
resultados pendentes. Perda de dados impede START até uma amostra nova chegar;
mudança de identidade exige reabrir a sessão pelo catálogo verificado.

A exportação captura o resultado no clique, antes do seletor de arquivo. Se
chegar uma amostra válida mais nova, o registro capturado continua sendo o
exportado; STOP, expiração ou perda de contexto durante o seletor cancelam a
escrita. Arquivos existentes nunca são sobrescritos. Não há métricas de ganho
operacional ou avaliação contrafactual do alocador nesta tela.

Diagnóstico nativo explícito, na raiz com `PYTHONPATH=src` e saída nova:

```powershell
.\.audit-winvenv\Scripts\python.exe tools/validate_allocation_shadow_ui.py --qnet-zip F:/qnet_network.zip --output outputs/minha-validacao-allocation-ui
```

Gera 12 registros (duas opções × seis observações), capturas e relatório de
integração. [Validação gráfica](../results/orion_q_allocation_shadow_ui_20260924.md).
Próximo incremento Q16: ponte própria para roteamento, com candidatos e ações
legais explícitos. Comparar políticas de alocação exige outro protocolo e uma
planta apropriada; a sequência de fixtures desta tela não fornece essa evidência.
