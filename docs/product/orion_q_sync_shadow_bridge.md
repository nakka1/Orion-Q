# Sincronizador opcional — primeira ponte shadow do sandbox

Incremento de código-fonte de 23/09/2026, posterior ao pacote Windows
`5.38.0-native-20260923-011322`. Não está naquele executável. O incremento
seguinte acrescentou a sessão gráfica descrita abaixo. Q05 continua sem ativação
de política nova.

## O que faz

`ControllerRuntime.observe_optional_sync(snapshot, model_id=..., archive_path=...)`
avalia explicitamente uma janela tipada, sem chamar `cycle`, política, supervisor,
executor ou journal de comandos. A função independente `evaluate_optional_sync`
permite a mesma avaliação sem sequer criar o banco do runtime.

Sem modelo selecionado retorna `off`, sem ler entrada ou pesos. Sem janela retorna
`unavailable`; entrada incompatível retorna `refused`; sucesso retorna `evaluated`.
Falha de carga não substitui o modelo silenciosamente por persistência. A persistência
é apenas a previsão comparadora no relatório, não uma ação nem a política do sandbox.

O sincronizador prevê **offset temporal a +2 s**, não fidelidade, QBER ou SKR.
Apenas dados declarados simulados são admitidos nesta primeira ponte. O suporte
do ORION-Q a entradas medidas permanece separado; este incremento não habilita
inferência do sincronizador em instrumentos reais.

## Contrato de aquisição

O snapshot existente recebe `telemetry.optional_sync_window`, com esquema
`orion.sandbox-sync-window.v1`. Exemplo completo:
[`sync_shadow_demo.json`](../../configs/optional_models/sync_shadow_demo.json).
É uma fixture manual, não uma campanha física nem um benchmark de desempenho.

- `source_id` deve coincidir com `snapshot.provenance.source`.
- `node_id` deve coincidir com o nó do snapshot.
- `campaign_id` e `clock_id` devem coincidir com os valores em `snapshot.metadata`.
- `stream_id` identifica o fluxo inteiro; não há combinação automática de fluxos.
- `evidence` deve ser `simulated`, também na proveniência do snapshot.
- Unidades explícitas: `offset_unit: s`, `temperature_unit: degC`.
- Exatamente oito amostras: `timestamp_utc`, `offset_s`, `temperature_c`.
  Timestamps têm fuso, ordem crescente e cadência de 1 s, tolerância de 1 µs.
- A última amostra não pode estar no futuro nem ter mais de 1 s em relação ao
  instante **do snapshot**, não ao relógio da máquina que executa o replay.
- O snapshot precisa declarar integridade verificada e não estar marcado OOD.
- Campos extras, rótulos futuros, booleanos como números e valores não finitos
  são recusados. Não há preenchimento de ausências nem interpolação.

O limiar de idade de 1 s é uma regra conservadora deste adaptador, não resultado
de calibração do artigo. Identificadores e a flag de integridade são declarações
do produtor; sua concordância não autentica instrumentos nem prova relógios
sincronizados. Uma ponte física futura precisa verificar essas condições.

O modelo recebe segundos relativos à primeira amostra, offsets e temperaturas.
A âncora UTC é preservada no snapshot do relatório. O futuro não entra nesta
ponte: `reference` é sempre nulo. O avaliador temporal separado descrito abaixo
agora pareia previsões já registradas com amostras posteriores, sem alterar a entrada.

## Uso explícito no código-fonte

Com o ambiente do projeto instalado, a partir da raiz do repositório:

```powershell
# OFF: não lê nem o arquivo de entrada nem os pesos.
python -m quantum_autopilot.control_plane.optional_sync --input configs/optional_models/sync_shadow_demo.json --output shadow-off.json

# Seleção explícita e pacote externo escolhido pelo operador.
python -m quantum_autopilot.control_plane.optional_sync --input configs/optional_models/sync_shadow_demo.json --output shadow-sync.json --model qkd.sync.ridge.1s.h2s --qkd-zip "C:/Users/alexa/Downloads/modelo_qkd_ml.zip"
```

Use um arquivo de saída novo e diretório existente. A leitura JSON é limitada
a 128 KiB, com recusa de chaves duplicadas. A saída nunca sobrescreve um arquivo.
OFF/sucesso retornam código 0; indisponibilidade/recusa retornam código 2.
Erros de arquivo ou esquema externo podem ocorrer antes de haver relatório.

O relatório contém o snapshot canônico e seu SHA-256, entrada efetivamente enviada
ao modelo e seu hash, identidade do ZIP, previsão/persistência, alvo UTC e idade
da janela. Cada avaliação revalida o pacote e cria um modelo novo. Nenhum peso é
copiado para o produto. Exportação é separada do journal de controle.

## Compatibilidade das demais funções

Não existe conversão legítima dos campos genéricos WDM/memória para todas as
features dos anexos. As seguintes pontes continuam pendentes:

| Função | Entrada que falta construir/validar no produtor |
|---|---|
| Sincronização contínua | Emissão artificial, sessão shadow e avaliação causal implementadas em fonte; aquisição física e campanhas independentes ainda pendentes |
| Ator QKD | Calibração, parâmetros do transmissor e observáveis causais decoy-state |
| Compensação | SO(3), covariáveis, cadência de 2 ms e regime correto |
| Roteamento | Features do simulador de origem, recursos e candidatos contextualmente legais |
| Alocação | Planta fixa de 11 enlaces, histórico e fila comprometida próprios; sem remapeamento automático |
| Detector | Export seguro; pickle continua bloqueado |

## Sessão contínua no dashboard (fonte)

1. Selecione **DATA SOURCE → fonte artificial**. O produtor acumula oito amostras
   emitidas consecutivas; não recupera um passado não observado. Cada frame representa
   1 s virtual, independentemente da velocidade de reprodução na máquina.
2. Em **EDGE CONTROL → OPTIONAL MODEL CATALOG**, selecione
   `qkd.sync.ridge.1s.h2s` e use **VERIFY LOCAL ZIP** para o pacote QKD revisado.
3. Abra **SYNCHRONIZER · LIVE ARTIFICIAL SHADOW**. Isso ainda não carrega o modelo.
4. Com uma janela disponível, clique **START SHADOW**. Cada nova janela é avaliada
   em segundo plano; a interface mostra offset previsto, persistência, alvo UTC e
   relatório completo. Há no máximo um trabalho por sessão, sem fila acumulada.
5. **EXPORT FRAME** salva o relatório imutável selecionado no clique, mesmo se um
   frame seguinte chegar durante a escolha do arquivo. STOP, perda de entrada,
   mudança de contexto ou pacote invalidam uma exportação ainda não confirmada.

O estado opcional percorre `SyntheticProvider → Snapshot → SwitchableProvider →
Edge Control → sessão shadow`, não um segundo gerador dentro da janela. Fontes
medidas/serviço não recebem timing artificial. A geração é reprodutível por semente,
sem depender de BER, OSNR, fidelidade, parâmetros do modelo ou rótulo futuro.

Lacunas, recuos temporais e troca de campanha reiniciam o buffer. Ausência de frames
novos durante 3,5 s de relógio local interrompe a sessão; repetir o mesmo frame não
renova frescor. Retomada exige START explícito. Fechar a janela/catálogo ou trocar
fonte/modelo invalida o pacote da sessão e descarta o resultado do worker pendente.

O envelope do controlador exige campos de fidelidade, mas a fonte de timing marca
esses campos como placeholders não utilizados. Eles não são previsões nem alimentam
qualquer ciclo de controle. Não há comandos, atuação, treinamento ou histórico
persistido automaticamente. Pesos continuam externos e cada avaliação revalida o ZIP.

## Avaliação temporal no mesmo painel

Ao iniciar uma sessão, o avaliador registra as previsões concluídas antes do próximo
frame. A referência chega depois e deve ser a última amostra do timestamp alvo exato,
no mesmo fluxo/campanha/nó/relógio. Histórico posterior não preenche alvo perdido.
O painel mostra cobertura, pendências e MAE; **EXPORT TEMPORAL REPORT** exporta o
estado capturado no clique, incluindo RMSE e registros verificáveis. Nenhuma referência
é enviada ao modelo. Erros são calculados sobre os mesmos pares para ambos os métodos.

Capacidade: 256 previsões por run na interface. O limite interrompe a inferência sem
remover registros; exporte e inicie outro run. STOP/CLEAR, fechamento e perda de fonte
apagam o run em memória. Sem pares, métricas ficam indisponíveis. O relatório distingue
pending de missed_target e não apresenta significância estatística ou ganho operacional.

[Validação e replay diagnóstico com pesos do autor](../results/orion_q_sync_temporal_20260923.md).
O [protocolo multi-semente/multi-cenário](orion_q_sync_campaign_protocol.md) foi
implementado como runner explícito em código-fonte. A campanha completa encontrou
vantagem não uniforme: somente um dos seis cenários atendeu todos os filtros.
[Resultados e reprodução](../results/orion_q_sync_campaign_20260923.md).
Não foi acrescentado seletor automático nem ativação de controle. A demonstração
artificial não estabelece ganho de controle nem confirma WDM→fidelidade quântica.
