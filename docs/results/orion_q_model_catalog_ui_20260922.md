# ORION-Q — catálogo nativo de modelos opcionais

Data: 22/09/2026. Incremento de interface em código-fonte, não nova versão do
executável Windows e não validação experimental dos modelos.

Nota histórica: este registro descreve a primeira consulta gráfica. O
[incremento seguinte](orion_q_model_archive_ui_20260922.md), no mesmo dia, acrescenta
verificação explícita de ZIPs sem instalação ou ativação.

## Entregue

Em **EDGE CONTROL → OPTIONAL MODEL CATALOG**, o usuário consulta as 11 opções do
registro leve já existente: dez adaptadores offline e um detector bloqueado por
pickle. Todas mostram padrão OFF e runtime NOT INTEGRATED. A consulta não afirma
qual política do sistema está ativa. Não depende de pesos, conexão ou capacidade
de atuação da fonte de telemetria.

A tabela usa o mesmo registro da fábrica opcional; não mantém uma segunda lista
de modelos. Uma linha selecionada abre detalhes dentro da janela, incluindo
função, pacote externo, limitações de domínio, warm-up quando pertinente e
fronteira entre recomendação e comando. Persistência é identificada como baseline
sem ML. Instalação aparece como NOT CHECKED: nada é procurado ou carregado.

A janela só abre por clique, conserva as fontes/cores nativas e permite rolagem
da tabela e dos detalhes. Não há botão de ativação, instalação ou execução
aparentemente funcional antes da implementação das respectivas barreiras.

## Verificação

Saídas locais: `outputs/model_catalog_ui_20260922/`.

- **45 testes aprovados**: novo catálogo (19), telas existentes (11) e
  capacidades da interface (15), em `ui-tests.xml`.
- **276 testes de regressão aprovados**: admissão de artefatos, alocador,
  compensador, agente QKD, barreira de controle físico, trilhas de evidência e
  comandos, em `regression-tests.xml`.
- Processo Python novo comprova consulta sem importar NumPy/Torch ou os leitores
  de artefatos; o teste recusa acesso a ZIP/NPZ/pickle e conexões de rede.
- Todas as 11 linhas conferidas; sem chamada da fábrica ou emissão de comando.
  Trocar a fonte não converte a consulta em ativação ou instalação.
- Redimensionamento em 640 × 480, 1060 × 760 e 1600 × 1000: acesso à última linha,
  rolagem, detalhes somente leitura e botão de fechamento dentro da janela.
- Capturas Qt offscreen revistas visualmente: `catalog-small.png`,
  `catalog-wide.png` e `edge-entry.png`. A captura da tela-base usa MockProvider,
  apenas para composição visual; seus números não são medições desta campanha.
- Ruff aprovado nos arquivos Python deste incremento.

Execução reproduzível, com PySide6 e dependências de teste instaladas:

```powershell
New-Item -ItemType Directory -Force outputs/model_catalog_ui_check
python -m pytest tests/test_console_model_catalog.py tests/test_console_screens.py tests/test_console_capabilities.py -q -p no:cacheprovider --basetemp outputs/model_catalog_ui_check/tmp --junitxml outputs/model_catalog_ui_check/ui-tests.xml
```

Use diretório temporário novo a cada execução para preservar os artefatos
anteriores. Nenhum teste exige os ZIPs externos do autor.

## Pendente e próximo passo

Admissão local explícita de arquivos pela interface, verificação de identidade
e apresentação dos motivos de recusa, sem ativação automática. Depois, pontes de
entrada tipadas e fluxo Q05 de revisão/registro/readback para execução limitada
e auditável. A consulta atual não resolve essas etapas.

Não houve alteração nos modelos numéricos, novo treinamento ou geração de
resultados científicos. O programa base continua independente dos pesos
opcionais. Empacotamento e teste do novo catálogo no `.exe` permanecem pendentes;
a distribuição 5.38.0-command-center-20260921 não foi sobrescrita.
