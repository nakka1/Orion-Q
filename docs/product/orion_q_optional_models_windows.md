# ORION-Q — modelos opcionais no Windows

As distribuições que incluem o catálogo de modelos oferecem dez configurações
offline: sincronizador e ator QKD, quatro compensadores, dois roteadores e duas
opções de alocação. O detector continua bloqueado por pickle nessas distribuições
(até o build de 23/09); o código de 29/09 o admite pela exportação segura
([detector](../results/orion_q_detector_evaluation_20260929.md)), e o executável precisa
ser reconstruído para incluí-lo. Todas começam OFF;
abrir o catálogo não carrega pesos nem ativa políticas de controle.

## Uso normal

Extraia a pasta inteira do ZIP, mantenha `_internal` ao lado de `ORION-Q.exe` e
abra o executável. Não é necessário instalar Python ou Node. As sessões usam
pesos externos: os dois pacotes fornecidos pelo autor não são incluídos no ZIP
do programa nem baixados automaticamente.

Em **EDGE CONTROL → OPTIONAL MODEL CATALOG**, escolha a configuração e clique
em **VERIFY LOCAL ZIP**. Selecione `modelo_qkd_ml.zip` para opções QKD ou
`qnet_network.zip` para opções Quantum Network. Apenas os bytes já revisados
são aceitos; arquivos modificados ou da família errada são recusados. O catálogo
nunca instala ou extrai scripts desses ZIPs. Conferir identidade não certifica
a validade científica, a licença ou a compatibilidade com dados físicos.

Após a verificação, use **OFFLINE SESSION → DEMO → RUN**. DEMO só preenche uma
entrada artificial. LOAD JSON permite usar uma entrada compatível com o contrato
da função. EXPORT grava um arquivo novo; não sobrescreve o existente. Trocar
modelo/fonte/campanha, esquecer o pacote ou fechar a janela invalida o contexto.
Sem pesos, o programa base continua utilizável e as avaliações ficam desabilitadas.

Essas sessões não são o seletor de política ativa da rede. Não executam comandos,
não conectam os modelos à telemetria física e não geram chaves. Paridade com
exports ou uma demonstração isolada não estabelece ganho operacional.

## Diagnóstico no próprio executável

Feche a aplicação e use uma pasta de saída **nova**, fora da pasta do programa:

```powershell
Start-Process -FilePath .\ORION-Q.exe -Wait -ArgumentList '--optional-model-test --output-dir C:\ORION-Q-tests\optional-base-01'
```

O modo sem pacotes confere catálogo, dez sessões inicialmente vazias/desabilitadas,
defaults OFF e runtime indisponível. Ele não procura pesos no computador.
Para testar também as dez demonstrações, informe explicitamente os dois ZIPs:

```powershell
Start-Process -FilePath .\ORION-Q.exe -Wait -ArgumentList '--optional-model-test --output-dir C:\ORION-Q-tests\optional-models-01 --qkd-zip C:\models\modelo_qkd_ml.zip --qnet-zip C:\models\qnet_network.zip'
```

Adapte os caminhos; caminhos com espaços precisam estar entre aspas dentro de
`ArgumentList`. O diagnóstico grava `optional-models-report.json` e, no modo com
pacotes, dez registros de avaliação. Arquivos existentes não são sobrescritos.
Ele usa Qt offscreen e o worker real da sessão, testa exportação exclusiva e
invalidação por edição, mas não substitui uma inspeção visual manual.

Leia `passed`, `mode`, `frozen`, hash do executável e erros do relatório. Código
0 indica sucesso desse diagnóstico; código 2 indica falha. Arquivo ausente ou
processo interrompido não é aprovação. Logs inesperados podem ficar em
`%TEMP%\ORION-Q-diagnostics`. Nenhuma informação é enviada a terceiros.

## Limites da aceitação

O teste local do executável não equivale a executar em uma segunda máquina
limpa. `independent_machine_verified` permanece falso. Faça a inspeção manual
de fonte, escala, navegação, troca de modelo e exportação no computador de destino.
O diagnóstico geral `--acceptance-test` continua separado; ele verifica hashes,
self-test e stress sintético. Não habilite controle físico para validar um pacote.
O binário acadêmico é portátil e não tem assinatura digital de editor.
