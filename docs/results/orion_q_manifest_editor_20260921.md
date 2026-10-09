# Editor interno de manifesto de campanha — 21/09/2026

## Entrega

**MANIFEST REVIEW → EDIT MANIFEST · IN-MEMORY DRAFT** abre o JSON completo da solicitação carregada em um diálogo editável. **VALIDATE / APPLY TO REVIEW** valida o rascunho e atualiza a revisão em memória, sem executar ensaios, salvar arquivo ou escrever no diário. Aplicação válida remove resultados anteriores da visualização e invalida a confirmação de revisão. Cancelar/fechar descarta somente o rascunho; uma recusa preserva a solicitação anteriormente carregada.

O editor exige capacidade de executar campanhas, solicitação carregada e nenhuma operação em andamento. Fonte/contexto e identidade da solicitação original são conferidos novamente ao aplicar. Troca de fonte, outro manifesto carregado, perda de capacidade ou operação concorrente impede aplicação indevida. O código do adaptador faz as verificações independentemente do botão habilitado.

O carregador de arquivos e o editor compartilham `parse_manifest`: até 64 KiB em UTF-8, contrato de campos/limites do motor, sem chaves duplicadas ou números não finitos. Codificação inválida, tipos incompatíveis e aninhamento excessivo têm recusa explícita. O carregamento por arquivo conserva sua semântica anterior de limpar estado antigo em caso de arquivo inválido; o editor preserva o rascunho separado até validação completa.

Nenhum ID, semente, caminho ou versão é inventado/adaptado. O usuário escolhe um novo campaign_id para um experimento alterado; o backend conserva idempotência e recusa conflito. Validar o rascunho não congela a topologia nem substitui a checagem de caminho/versão persistida ao executar. Não há salvamento automático ou formulário por campos nesta etapa.

## Evidências

- Regressão ampliada: **482 testes aprovados em 369,11 s**, `outputs/software_validation_manifest_editor/accepted-tests.xml`.
- Rodada focada: **84 testes aprovados em 77,06 s**, `outputs/software_validation_manifest_editor/tests.xml`, incluindo 27 casos novos e regressões da campanha/console. Não somar casos repetidos às outras rodadas.
- Cobertura: parser compartilhado, limite em bytes, JSON/UTF-8 inválido, duplicatas, números não finitos, aninhamento, orçamento e sementes inválidos; aplicação atômica; cancelamento; preservação do arquivo de origem/banco; revisão desmarcada e ausência de worker.
- Troca de fonte/manifesto, perda de capacidade, operação em andamento, edição após uma campanha realmente concluída sem alterar seu recibo e integração do botão da janela principal com o diálogo.
- Capturas `editor-640.png` e `editor-1080.png` inspecionadas com fontes/tema reais: editor rolável e botões/avisos acessíveis. A referência institucional permanece nas cores/fontes e na estrutura existente, sem novo menu lateral.
- Ruff aprovado; sintaxe PowerShell e YAML verificadas; teste incluído nos gates de build. Workflow remoto não disparado; política de execução do Windows preservada.
- Novo build PyInstaller concluído em aproximadamente 366 s. Paridade de código compilado confirmada em **77 módulos** `quantum_autopilot`, sem divergências.
- Harness com worker do executável: **24 verificações aprovadas**, incluindo edição antes do despacho, paridade numérica, cancelamento, replay e leitura sem modificar o diário. O diálogo controlador do harness roda pelo Python-fonte; isso não equivale a teste manual da GUI congelada.
- Autoteste nativo: **19 verificações aprovadas**. Injeção sintética: **20 rodadas aprovadas**, sem ação física, em diretórios isolados desta distribuição. Nenhum diário operacional em AppData foi usado.

## Distribuição verificada

- Pacote: `release/windows/5.38.0-manifest-editor-20260921/ORION-Q-5.38.0-native-windows-x64.zip` — **283.218.786 bytes**, **3.833 arquivos**, CRC do ZIP verificado.
- SHA-256 do ZIP: `e05f0c7a38278304931a5dec008c02954d9c04b7869fdffc1ebab4f093d294c1`.
- SHA-256 do executável: `2f14f55bc3e6c83ef66355da78d9c30fcf028d8b468222a37ca1ae8534a18d08`.
- Aceite nativo pós-empacotamento aprovado em **47,89 s**: integridade de 3.832 arquivos de conteúdo, autoteste e 20 rodadas de estresse. Relatório `native-acceptance/acceptance-report.json` da distribuição: `automatic_passed=true`, `acceptance_status=manual_review_required`, `independent_machine_verified=false`.

Extraia a pasta inteira antes de abrir `ORION-Q.exe`; o executável isolado não contém suas dependências. A distribuição contém o guia atualizado do editor. Evidências de validação ficam fora da pasta executável. A documentação desta entrega não transforma aceite automático local em homologação em outro computador.

## Limites

O rascunho existe somente em memória. Aplicar não altera o arquivo carregado, registra tentativa nem salva um novo manifesto. Fechar o aplicativo perde o rascunho; copie o texto caso precise guardá-lo antes. Um recibo histórico sem solicitação executável não habilita o editor. O produto não pré-registra uma hipótese: ajustes após exploração devem ser tratados como exploratórios.

Motor quântico, modelos WDM e protocolo estatístico permanecem inalterados. Nenhuma medição física ou evidência experimental da hipótese WDM→fidelidade foi adicionada. Aceite manual em computador Windows independente permanece pendente. Distribuições anteriores preservadas; nenhum commit criado.

[Guia do painel e editor](../product/orion_q_temporal_campaign_ui.md).
