# Compensação opcional: reprodução dos exports

Data: 22/09/2026. Continuação da admissão Q16; não é conclusão da ativação Q05.
Fonte: pacote `qnet_network.zip` com SHA-256
`6b8563209f54a2d566f1a4229b140b75fda93555085ff2bf6bfcad8f07abd938`.
Nenhum script fornecido no ZIP foi executado/importado; pesos lidos como arrays.

## Resultado numérico

As quatro combinações reproduziram os exemplos do autor. Por checkpoint foram
conferidas 12 janelas em quatro estágios (features, normalização, incremento e
rotação prevista) e 10.000 previsões sequenciais. Total: 48 verificações de
janelas e 40.000 saídas sequenciais. Há **duas trajetórias**, uma por regime,
reutilizadas pelas duas arquiteturas; não são 40.000 experimentos independentes.

| Regime / modelo | Desvio máximo na rotação do stream | Erro angular médio / persistência, mrad |
|---|---|---|
| Calibrado / Ridge | 1,499e-15 | 200,19 / 823,04 |
| Calibrado / MLP | 6,731e-16 | 202,49 / 823,04 |
| Desenvolvimento / Ridge | 1,221e-15 | 165,85 / 920,77 |
| Desenvolvimento / MLP | 8,882e-16 | 159,87 / 920,77 |

Tolerância absoluta para paridade: 1e-9, igual à rotina de exemplos do export.
O maior desvio entre estágios nas janelas foi 4,548e-13. Os primeiros 15 passos
de cada checkpoint reproduzem exatamente persistência, identificados por status
`warmup_persistence`. Os 20 passos finais têm alvo truncado no export e foram
conferidos numericamente, mas excluídos das estatísticas de predição. Restam
9.980 erros pontuados por checkpoint, incluindo warm-up, como no export.

Os erros angulares acima são descritivos das trajetórias simuladas recebidas.
Não são QBER, fidelidade Bell, vantagem contra alfa-beta ressintonizado, nova
estimativa de generalização ou resultado de controle com atuador. Não foram
refeitas campanhas multissemente, testes de transferência ou tabelas dos artigos.

## Proteções e opcionalidade

- Checkpoint exige arquitetura e regime explícitos, janela de 16 amostras,
  cadência de 2 ms, horizonte de 40 ms e 400 fótons de monitoramento por base.
- Rejeita matrizes fora de SO(3), reflexões, NaN, normalização inválida,
  vibração RMS negativa, mudança de regime, salto temporal e mudança de stream.
- Erro não adiciona amostra ao histórico. `reset()` explícito inicia novo stream.
- O resultado inclui hash, modelo, regime, tempos, estágio de warm-up e
  `physical_action_performed=false`; não expõe um executor de atuação.
- Regime é declaração contratual. Não há identificação automática de espectro
  ou verificação experimental de que o dispositivo corresponde ao treino.
- Catálogo/fábrica opcionais têm estado padrão OFF, carregamento sob demanda,
  sem substituição dos modelos atuais. Ver [contrato do produto opcional](../product/orion_q_optional_models.md).

## Verificação e limite da entrega

157 testes passaram em 16,55 s: 55 novos de compensação/opcionalidade, 48 da
admissão anterior e 54 regressões de evidência, controle físico e comandos.
Incluem falha numérica sem avanço de estado, rotação perto de zero/pi, ordem de
features, normalização, seleção OFF sem leitura de arquivos e catálogo sem
importar o backend NumPy. Ruff passou nos arquivos da implementação e testes.
Não foi executada toda a suíte histórica nem uma medição de latência em tempo real.

Relatório: `outputs/author_compensation_20260922/replay.json`, contendo paridade
dos sete checkpoints já admitidos, hashes do código/arquivos e pendências.
Testes: `outputs/author_compensation_20260922/final-tests.xml`.
Os 593 resultados do sincronizador e as 2.091 decisões do roteador foram
reverificados na mesma execução e continuam aprovados.

O seletor nativo, persistência por projeto, instalação opcional de pesos,
integração ao runtime e novo `.exe` continuam pendentes. O passo seguinte é
admitir o agente QKD preservando seu pré-processamento e sua caixa de ações,
sem transformar recomendação em comando físico.
