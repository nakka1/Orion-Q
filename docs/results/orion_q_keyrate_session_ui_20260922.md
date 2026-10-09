# ORION-Q — sessão gráfica offline do agente QKD

Data: 22/09/2026. Segunda função com sessão offline nativa, após o sincronizador.
Implementada em código-fonte; não altera a distribuição `.exe` anterior e não
ativa políticas Q05. Ambos os modelos continuam opcionais.

Incremento posterior no mesmo dia: [sessão dos quatro compensadores](orion_q_compensation_session_ui_20260922.md).
Os resultados abaixo preservam o escopo e a contagem de testes desta etapa do ator.

## Uso e comparação correta

Em **EDGE CONTROL → OPTIONAL MODEL CATALOG**, selecionar `qkd.keyrate.actor`,
conferir o ZIP QKD com **VERIFY LOCAL ZIP** e abrir **OFFLINE SESSION**.
**LOAD JSON** ou **DEMO** preenche o editor; nada executa até **RUN** explícito.

O agente recomenda ajustes de configuração, não prevê a próxima taxa de chave.
Por isso, o baseline desta sessão é **manter os parâmetros atuais**, com comparação
campo a campo contra a recomendação limitada. Não se calcula ganho de taxa,
regret, erro preditivo ou probabilidade de sucesso sem resultados contrafactuais.
Probabilidades da política são pesos de amostragem, não chances de êxito físico.

A sessão mostra ação amostrada e ação gulosa separadamente, sem substituir uma
pela outra. O relatório exportado inclui as 16 features, logits, probabilidades,
configurações sugeridas para todas as nove ações, baseline, semente, algoritmo
PCG64, versão NumPy, hashes e o texto exato submetido ao RUN. Unidades:
intensidades em média de fótons/pulso, probabilidades em frações, clock em Hz.

## Contrato próprio e causalidade declarada

JSON UTF-8 de até 64 KiB com esquema `orion.optional-keyrate-input.v1`:

- `evidence`: somente `simulated_offline`.
- `stream_id`: identificador não vazio de até 160 caracteres, sem controles.
- `decision_timestamp_s`: instante não negativo da decisão.
- `previous_period_end_s`: instante do período anterior, não posterior à decisão.
  Pode coincidir com a fronteira de início do novo período; não se presume latência
  física de aquisição. Quando `previous` for null, este campo também deve ser null.
- `seed`: inteiro de 0 a 4294967295, sem coerção de booleanos ou strings.
- `protocol`: `decoy_state_bb84`; `link_span_km`: 25–80 km declarados.
- `settings`: campos exatos de `TransmitterSettings`.
- `calibration`: campos exatos de `LinkCalibration`, incluindo tempo morto de 50 ns.
- `previous`: campos exatos de `PeriodObservables`, ou null para bootstrap de
  calibração. O relatório distingue `previous_period` de `calibration_bootstrap`.

A validação reutiliza as restrições e a construção de observação do adaptador
original, antes de abrir os pesos. Campos adicionais, ausência de campos,
números não finitos, tipos indevidos, configurações fora dos limites, horizonte
causal incompatível ou protocolo errado são recusados. Não há campos de taxa
futura ou referência a serem incorporados à decisão.

Essas checagens validam o esquema e a declaração do operador, não autenticam
timestamps ou a origem física dos valores. Entradas de sincronização não são
convertidas para entradas do ator: cada sessão mantém seu contrato.

## Execução, estado e exportação

- Novo modelo e RNG PCG64 a cada RUN, com o pacote novamente conferido contra o
  pin. Mesma entrada/semente pode ser repetida sem histórico oculto. Registrar
  a versão NumPy auxilia a reprodutibilidade; não promete igualdade irrestrita
  entre plataformas/versões numéricas.
- O RNG da sessão não é o estado original não fornecido da política dos artigos.
  Não se afirma reprodução dos sorteios originais.
- Edição, troca de função/fonte/pacote ou fechamento invalidam o resultado;
  uma operação já iniciada pode terminar, mas seu resultado antigo é descartado.
- Trocar entre sincronizador e ator recria o tipo correto de sessão sem
  transportar entrada, resultado ou modelo da função anterior.
- EXPORT cria arquivo novo com abertura exclusiva; não sobrescreve arquivos.
  O hash de entrada corresponde ao texto UTF-8 submetido, incluído no relatório,
  não necessariamente aos bytes do arquivo antes da normalização pelo editor.
- Nenhuma recomendação é aplicada, enviada a driver ou persistida no journal
  de controle. Não há chave gerada, prova finite-key, previsão de Bell ou atuação.

## Verificação

Artefatos: `outputs/keyrate_session_ui_20260922/`.

- **172 testes aprovados** em `ui-tests.xml`: as duas sessões, catálogo, admissão
  de pacotes, capacidades, telas e fontes de dados.
- **276 testes de regressão aprovados** em `regression-tests.xml`: adaptadores,
  inferência, comandos e barreiras de evidência/atuação.
- Total da rodada: **448 aprovados**, sem falhas ou testes ignorados.
- Ruff aprovado nos seis arquivos Python alterados/adicionados nesta etapa.
- Capturas `session-wide.png` e `session-small.png` revistas em 1060 × 760 e
  640 × 480, com editor/resultado roláveis e controles visíveis.
- `demo-recommendation.json`: execução funcional com o pacote QKD fornecido,
  SHA-256 `94352f15b6dcb132a3c03559b9d94b28f42b6e9adb8b2cdaab18f46720a55a79`,
  e os valores artificiais do botão DEMO. Semente 1234, temperatura 0,25.

Nesse exemplo, a ação amostrada e a gulosa coincidiram em `mu +25%`, propondo
intensidade de sinal 0,5 → 0,625 e mantendo os outros parâmetros. Isso é uma
**recomendação em um caso artificial**, não evidência de ganho. Testes com pesos
sintéticos exercitam ação amostrada diferente da gulosa, limites de clipping,
bootstrap declarado, entrada inválida antes dos pesos e repetição com a mesma
semente, além de exportação sem sobrescrita e troca entre sessões.

## Pendente

Sessões gráficas de compensação, roteamento e alocação; avaliações em laço
fechado com resultados contrafactuais; integração de entradas do runtime;
ativação Q05 e distribuição Windows atualizada. A tese de informação preditiva
WDM sobre fidelidade quântica medida não ganha nova validação com esta etapa.
