# ORION-Q — sessão gráfica offline de compensação de polarização

Data: 22/09/2026. Terceira função com sessão offline nativa, após sincronizador
e ator QKD. Implementada em código-fonte; o executável Windows anterior não
contém este incremento. Modelos permanecem opcionais, OFF por padrão, sem
ativação Q05, controle físico ou alteração dos pesos fornecidos.

Incremento posterior no mesmo dia: [sessões dos dois roteadores](orion_q_routing_session_ui_20260922.md).
O restante deste documento preserva o escopo e a contagem da etapa de compensação.

## Uso e contrato

Em EDGE CONTROL → OPTIONAL MODEL CATALOG, selecionar um dos quatro checkpoints
`qnet.compensation.{calibrado,desenvolvimento}.{ridge,mlp}`, conferir o ZIP local
Quantum Network e abrir OFFLINE SESSION. LOAD JSON importa uma entrada; DEMO
apenas preenche o editor. RUN executa explicitamente em segundo plano. EXPORT
cria um JSON novo, sem sobrescrever arquivos existentes.

O contrato `orion.optional-compensation-input.v1` exige:

- Evidência `simulated_offline`, checkpoint igual ao selecionado e `stream_id`
  explícito. Não há conversão de domínio ou troca automática de modelo.
- De 1 a 16 amostras consecutivas, timestamps relativos não negativos em
  segundos, cadência de 2 ms e horizonte de 40 ms.
- Declaração de 400 fótons de monitoramento por base. Isso não cria contagens
  de detecção nem comprova uma aquisição experimental.
- Matriz de rotação 3 × 3 em SO(3) por amostra; nenhuma projeção silenciosa.
- Covariáveis nomeadas: `temperature_c` (°C), `temperature_rate_k_s` (K/s) e
  `vibration_rms_rad` (rad RMS). Temperatura abaixo do zero absoluto ou RMS
  negativo são recusados.
- `reference: null`, ou rotação futura no instante da última amostra +40 ms.
  Essa referência é usada somente para avaliação após a inferência.

JSON é limitado a 64 KiB. Campos extras, duplicados, valores não finitos,
booleanos no lugar de números, cadência incorreta, checkpoint divergente e
matrizes inválidas são recusados antes de abrir o pacote/construir o modelo.
O regime escolhido é declarado, não uma certificação automática de compatibilidade
com o domínio de treinamento. Esta sessão não é uma ponte de telemetria física.

## Comparação e isolamento

Cada RUN abre um modelo com estado novo e verifica novamente o hash do ZIP.
As primeiras 15 saídas são persistência (`warmup_persistence`), não inferência
aprendida. Com 16 amostras, há 15 saídas de aquecimento e uma aprendida. Uma
entrada menor permite inspecionar o aquecimento sem alegar uso efetivo do ML.

O relatório contém todas as saídas, mas os erros angulares avaliam somente a
última previsão contra a referência futura, quando fornecida. A mesma referência
avalia o baseline de persistência. Sem referência, ambos os erros são ausentes,
não zero. A interface mostra mrad; o registro exportado usa radianos.

Editar a entrada ou trocar modelo, fonte/campanha, pacote ou fechar a sessão
invalida o resultado. Trabalho antigo pode terminar em segundo plano, mas não
reaparece como resultado atual. Trocar de checkpoint recria a sessão apropriada,
sem transportar JSON, resultado ou histórico. O registro exporta identidade do
modelo/pacote, hash e texto de entrada, horário UTC, etapas e limites de evidência.

## Verificação executada

- 222 testes passaram no conjunto de sessões, catálogo, fontes e telas nativas.
- 276 testes passaram na regressão de admissão, modelos, comandos, evidência e
  barreiras de controle físico. Total dos dois conjuntos: **498**, não a suíte
  completa do repositório.
- Ruff passou nos cinco arquivos Python adicionados/alterados neste incremento.
- Os quatro checkpoints fornecidos executaram através do worker Qt com DEMO.
- Capturas em 1060 × 760 e 640 × 480 foram inspecionadas visualmente; controles
  permanecem visíveis e áreas de entrada/resultado são roláveis. Há também uma
  captura da opção MLP de desenvolvimento.

Artefatos locais em `outputs/compensation_session_ui_20260922/`: `ui-tests.xml`,
`regression-tests.xml`, quatro relatórios `qnet.compensation.*.json`,
`session-wide.png`, `session-small.png` e `development-mlp.png`.

Os novos testes cobrem SO(3), entrada causal, quatro checkpoints, aquecimento,
independência da referência, estado novo, ZIP alterado, exportação sem sobrescrita,
recusa de entradas incompatíveis, descarte de resultado antigo e dimensões da UI.

## Resultado da demonstração — não é uma campanha científica

Foi usado o pacote fornecido `qnet_network.zip`, SHA-256
`6b8563209f54a2d566f1a4229b140b75fda93555085ff2bf6bfcad8f07abd938`.
Nenhum script do autor foi executado. DEMO constrói manualmente 16 rotações em z,
com ângulo `i × 0,001 rad`, timestamps `i × 0,002 s`, temperatura de 20 °C,
derivada térmica zero e RMS declarado de 0,3 rad. A referência é 0,035 rad em
0,07 s. Isso não reproduz o gerador de cenários nem os regimes dos artigos.

| Checkpoint | Erro final (mrad) | Persistência (mrad) |
|---|---:|---:|
| Calibrado · Ridge | 26,7835 | 20,0000 |
| Calibrado · MLP | 37,2701 | 20,0000 |
| Desenvolvimento · Ridge | 34,5178 | 20,0000 |
| Desenvolvimento · MLP | 30,6597 | 20,0000 |

Todos tiveram erro maior que a persistência neste caso artificial. Os resultados
negativos foram preservados; uma trajetória escolhida para testar a interface
não permite concluir superioridade/inferioridade geral, significância estatística
ou reprodução/falha dos resultados dos artigos.

Não foram demonstrados ganho em laço fechado, melhoria de QBER/SKR, previsão de
fidelidade de Bell ou informação preditiva WDM→quântico medida. A entrega é uma
ferramenta de avaliação offline auditável, não validação dessas hipóteses.

## Próxima etapa

Sessões próprias para roteamento e alocação, preservando os respectivos contratos
e baselines. Ponte de features, ativação auditável Q05 e empacotamento Windows
com/sem modelos continuam separados e pendentes.
