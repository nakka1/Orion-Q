# Agente QKD de taxa de chave: opção offline

Incremento Q16 em 22/09/2026. ID: `qkd.keyrate.actor`. O modelo foi admitido como
opção no catálogo, OFF por padrão. Não existe ainda seleção nativa/ativação Q05,
nem uma nova versão do executável com essa função.

## O que faz

Recebe observáveis do período anterior de QKD decoy-state, parâmetros atuais do
transmissor e calibração de comissionamento. Calcula 16 features, aplica a
normalização congelada do treino e executa o ator 16-64-64-9. Retorna nove
probabilidades, ação gulosa, ação sorteada a temperatura 0,25 e parâmetros
propostos para essa ação. O crítico do checkpoint é validado, mas não participa
da recomendação.

**Não prevê uma taxa futura**, não estima fidelidade Bell, não extrai chaves e
não aciona Alice/Bob. Taxa de chave é uma entrada observada anterior, não uma
saída prometida pelo modelo. Os limites são os da caixa do export simulado,
não limites certificados para qualquer equipamento físico.

As ações são manter, mu +25%/-20%, nu +25%/-20%, clock +50%/-33% e probabilidade
de sinal +0,05/-0,05. O multiplicador de redução do clock é exatamente 0,67,
como no export; não foi trocado por 2/3.

| Parâmetro | Limite do export |
|---|---|
| Intensidade de sinal mu | 0,05 a 1,0 |
| Intensidade decoy nu | 0,01 a 0,4, e no máximo 0,6 mu |
| Clock | 100 MHz a 5 GHz |
| Probabilidade de sinal | 0,4 a 0,85, respeitando probabilidade decoy mínima |
| Probabilidade decoy remanescente | pelo menos 0,05 |
| Tempo morto do detector | 50 ns |

Ponto atual inválido é recusado; clipping é aplicado apenas à recomendação a
partir de um ponto válido. A probabilidade de vácuo não é alterada. Mu reduzido
pode também limitar nu: essas restrições acopladas foram preservadas.

## Entradas e controles

- Contrato aceita somente `simulated_offline`, protocolo declarado
  `decoy_state_bb84` e vão declarado de 25 a 80 km. Essas declarações não são
  prova de correspondência com o treino nem detecção automática de OOD.
- Ganhos, yields e erros são frações, não percentuais; taxa/clock em Hz,
  tempo morto em segundos. Strings, booleanos, NaN e infinito são recusados.
- Calibração exige ganho/taxa/escuro positivos. Não são inventados valores de
  comissionamento; `previous=None` só representa o início sem observação anterior.
- RNG explícito é obrigatório. Não disponibilizar RNG não converte a escolha
  em argmax silenciosamente. O resultado mostra separadamente ambos os índices.
- `preview_action` calcula o efeito limitado de uma ação conhecida, sem executá-la.
- Não há estado temporal interno, construção de telemetria real ou verificação
  de frescor. Timestamp, canal, associação da calibração e causalidade da amostra
  precisarão ser garantidos pela futura ponte de runtime.

### Particularidade importante do pré-processamento

A feature 13 usa a taxa do mesmo período recém-observado que alimenta a feature
7, apesar de ser descrita como taxa anterior no export. Normalmente ambas
coincidem, mas **não sempre**: os pisos do logaritmo estão em posições distintas.
Com taxa zero e calibração de 1 Mbit/s, a feature 7 é `log(1e-12)` e a 13 é
`log(1e-18)`. A implementação preserva as duas expressões originais; não duplica
um valor calculado nem corrige o treinamento silenciosamente. Esse caso tem teste.

## Uso por código

```python
import numpy as np
from quantum_autopilot.model_intake.catalog import open_optional_model
from quantum_autopilot.model_intake.keyrate import TransmitterSettings, LinkCalibration

model = open_optional_model(
    "qkd.keyrate.actor", archive_path="C:/Users/alexa/Downloads/modelo_qkd_ml.zip"
)
result = model.recommend(
    previous=None,
    settings=TransmitterSettings(.5, .1, .1, .7, 1e9),
    calibration=LinkCalibration(.01, 1e6, 1e-8, .02, 50e-9),
    domain="simulated_offline",
    link_span_km=50.,
    protocol="decoy_state_bb84",
    rng=np.random.default_rng(123),
)
# Valores acima são apenas uma demonstração simulada, não calibração física.
# result.proposed_settings é uma recomendação; nada foi aplicado.
```

O carregamento mantém identidade SHA-256 do ZIP revisado e usa NPZ sem pickle.
Omitir a seleção em `open_optional_model()` continua retornando `None`, sem
ler pesos nem substituir os modelos/baselines existentes.

## Verificação e próximos passos

[Paridade em 40 períodos](../results/orion_q_optional_keyrate_20260922.md) foi
conferida para features, probabilidades, ação gulosa e efeito das ações gravadas.
O export não informa o estado RNG da política: a semente do episódio não foi
assumida como semente do agente. Não se afirma paridade dos sorteios originais.

Atualização da interface: a [sessão nativa offline](../results/orion_q_keyrate_session_ui_20260922.md)
agora aceita entrada simulada explícita, compara parâmetros recomendados com
manter a configuração atual e exporta o registro da decisão sem sobrescrever.
Semente PCG64, versão NumPy, features, probabilidades e nove prévias limitadas
ficam registrados. Isso não prevê taxa seguinte nem mede ganho operacional.

Faltam ensaio fechado reproduzindo novos resultados, ponte com entradas do
runtime e ativação auditável Q05. O `.exe` e a operação sem esses pesos permanecem
inalterados. O alocador híbrido também já foi admitido por código offline;
as respectivas sessões gráficas e a distribuição Windows permanecem pendentes,
mantendo fonte de fóton único e decoy-state em trilhas distintas.
