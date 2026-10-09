# Compensação de polarização — ponte shadow tipada

Incremento em código-fonte de 23/09/2026. Não altera o ZIP/EXE anterior nem ativa
Q05. As sessões offline já existentes continuam funcionando. Um produtor artificial
e uma sessão gráfica shadow agora usam o fluxo comum de snapshots do dashboard.

## Função e fronteira

`ControllerRuntime.observe_optional_compensation(snapshot, model_id=..., archive_path=...)`
encaminha uma janela tipada para o checkpoint explicitamente escolhido, sem chamar
`cycle`, política, supervisor, executor ou journal de comandos. A função livre
`evaluate_optional_compensation` permite a mesma leitura sem criar banco do runtime.

Modelos: `qnet.compensation.{calibrado,desenvolvimento}.{ridge,mlp}`. Todos opcionais
e OFF por padrão. `model_id=None` não inspeciona entrada nem pacote. Seleção não
troca automaticamente regime ou arquitetura. Sem janela: `unavailable`; contrato
incompatível: `refused`; avaliação concluída: `evaluated`.

A saída é uma matriz SO(3) prevista e a persistência comparadora. **Não é fidelidade
de Bell, QBER/SKR, ganho em laço fechado ou comando para atuador de polarização.**
Não há conversão de BER, OSNR, fidelidade ou offset de sincronização para rotações.

## Contrato de entrada

Campo no `NetworkStateSnapshot`: `telemetry.optional_compensation_window`.
Esquema: `orion.sandbox-compensation-window.v1`.

- Identidades obrigatórias: fonte, campanha, fluxo, nó, relógio e referencial de
  rotação. Fonte/nó coincidem com o snapshot; campanha/relógio/referencial coincidem
  com `metadata.campaign_id`, `clock_id` e `rotation_frame_id`.
- `rotation_convention: author_export_so3_v1` identifica internamente a convenção
  matemática preservada pelo adaptador. Deve coincidir com a metadata homônima;
  **não atesta a orientação dos eixos de um instrumento físico**.
- `operating_point` coincide com `metadata.compensation_operating_point` e com o
  regime do checkpoint selecionado.
- Declarações por regime: calibrado = frequência de vibração 60 Hz e deriva térmica
  0,02 rad/s; desenvolvimento = 40 Hz e 0,05 rad/s. Isso confere o contrato do export,
  não estima o espectro de vibração nem certifica automaticamente o domínio.
- `period_s: 0.002`, `horizon_s: 0.04`, `monitoring_photons_per_basis: 400`.
- Unidades explícitas: `temperature_unit: degC`, `temperature_rate_unit: K/s`,
  `vibration_unit: rad_rms`.
- De uma a 16 amostras consecutivas, cada uma com `timestamp_utc`, `rotation`,
  `temperature_c`, `temperature_rate_k_s` e `vibration_rms_rad`.
- Matriz 3×3 em SO(3): sem corrigir/projetar silenciosamente matrizes inválidas.
  Covariáveis finitas; temperatura não inferior a −273,15 °C; RMS não negativo.
- Timestamps com fuso, cadência de 2 ms. A idade da última amostra em relação ao
  snapshot deve ficar entre 0 e 2 ms, nunca no futuro. O limiar é conservador deste
  adaptador, não uma certificação física. Tempo de replay não é tempo de parede.
- Somente `simulated` na janela e na proveniência. Integridade declarada verificada
  e OOD falso; essas flags não comprovam autenticidade de instrumentos.

Campos extras e referências futuras são recusados. O contrato da sessão offline
existente é reutilizado após traduzir timestamps UTC para segundos relativos à
primeira amostra. Assim não se perdem 2 ms na conversão por timestamps de época
grandes. A entrada enviada ao modelo sempre tem `reference: null`.

## Aquecimento, resultados e exportação

Cada chamada abre um modelo novo e revalida o ZIP externo. Não há estado de modelo
compartilhado entre nós, campanhas, regimes ou janelas.

Com 1–15 amostras, `final_output_status` é `warmup_persistence`: a saída não é
inferência aprendida. Com 16, o status final é `model`; os primeiros 15 passos
continuam identificados como aquecimento no relatório.

O registro contém snapshot e hash, checkpoint, alvo UTC, idade da janela e
`evaluation_json` imutável com entrada/hash, hash do arquivo, passos, previsão e
persistência. Erros angulares ficam nulos porque não há referência futura nesta
ponte. Use `export_compensation_shadow(result, path)` para criar um arquivo novo;
nunca sobrescreve arquivos existentes. Falhas não provocam fallback oculto.

## Diagnóstico explícito

Na raiz do projeto, ambiente instalado e diretório-pai existente:

```powershell
python tools/validate_compensation_shadow.py --qnet-zip "F:/qnet_network.zip" --output outputs/compensation-shadow-check
```

Cria oito relatórios: uma amostra e 16 amostras para cada checkpoint. O diretório
deve ser novo. Os dados são fixtures artificiais e os regimes apenas declarados;
o diagnóstico comprova ligação/paridade com a sessão offline, não desempenho.

[Resultados de validação](../results/orion_q_compensation_shadow_20260923.md).
A [avaliação temporal](orion_q_compensation_temporal.md) já compara previsões
registradas com amostras posteriores. Próximo passo: campanha própria de robustez,
preservando persistência e os demais comparadores adequados a controle.

## Uso no dashboard

1. Execute a interface pelo código-fonte e selecione a entrada artificial.
2. Em **Edge Control → OPTIONAL MODEL CATALOG**, selecione um dos quatro compensadores.
3. Use **VERIFY LOCAL ZIP** para verificar o pacote QNET externo revisado.
4. Abra **POLARISATION · LIVE ARTIFICIAL SHADOW** e clique **START SHADOW**.
5. Leia a matriz prevista, a persistência e o registro com hashes. **EXPORT BATCH**
   salva o lote selecionado em um arquivo novo. **STOP / CLEAR** apaga o resultado.

`Snapshot.optional_compensation_states` transporta dois regimes independentes;
fontes medidas continuam com esse campo vazio. O produtor não abre pesos. Cada
publicação fornece 16 amostras novas a 2 ms: avança 32 ms de tempo virtual, não
1 s como o sincronizador. Não há interpolação de WDM ou conversão entre relógios.
O sinal artificial usa Rz(deriva × tempo) Rx(senoide), amplitude RMS de 0,003 rad,
fase determinística por semente/regime e temperatura com taxa de 0,1 K/s. A
declaração de 400 fótons por base é contratual: não há simulação de contagem de
fótons, ruído de estimação ou calibração física nesse produtor.

Só há um trabalho de inferência por janela, sem fila: lotes que chegam durante
um trabalho o invalidam e não enfileiram outro. Um próximo lote pode iniciar uma
nova avaliação. Ausência, duplicidade de regime, STOP, troca de fonte/campanha/
referencial ou fechamento descartam resultados pendentes. Após 3,5 s de tempo
real sem lote novo, é preciso START novamente; duplicatas não renovam esse prazo.
O prazo real da interface é distinto da idade virtual de 2 ms validada no backend.
A exportação captura o resultado no clique e o preserva se chegarem novos lotes,
mas é cancelada se a sessão perder contexto enquanto o seletor de arquivo está aberto.

O registro individual não recebe referência futura. Métricas temporais separadas
mostram erros de previsões anteriores, somente após chegar o lote com o alvo exato.
**EXPORT TEMPORAL REPORT** salva a série pareada, alvos perdidos e pendências.
Não há alegação de superioridade. Os 15 passos iniciais de cada avaliação são
persistência; só o 16º usa o modelo. A rotina
`tools/validate_compensation_shadow_ui.py --qnet-zip F:/qnet_network.zip --output outputs/NOVO-DIRETORIO`
verifica duas avaliações por checkpoint e grava capturas, sem comandos físicos.
Veja [os resultados da integração gráfica](../results/orion_q_compensation_shadow_ui_20260923.md).
