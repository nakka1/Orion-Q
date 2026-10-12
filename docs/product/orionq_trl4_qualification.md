# OrionQ — protocolo de consolidação TRL 4 do software

Elemento: **SOFT-ALPHA-TRL4**, fontes do protótipo integrado executadas no Windows.
Este processo prepara uma avaliação interna rastreável e uma revisão humana;
não atribui TRL automaticamente. O [desenho](orionq_trl4_design.md) e o
[manual](orionq_trl4_operator_manual.md) delimitam o domínio de aplicação.

## Critério de maturidade e relação com TRL 5

A [NASA NPR 7123.1D, Apêndice E](https://nodis3.gsfc.nasa.gov/displayDir.cfm?Internal_ID=N_PR_7123_001D_&page_name=AppendixE)
descreve TRL 4 de software como integração/validação funcional dos componentes
críticos, com desempenho testado frente a previsões analíticas e ambiente
potencialmente relevante definido. O
[ECSS-E-HB-11A, A.4/Table A-1](https://ecss.nl/wp-content/uploads/2017/03/ECSS-E-HB-11A%281March2017%29.pdf)
relaciona TRL 4 a uma versão alpha com domínio identificado, manual/desenho,
casos de uso implementados e verificação em laboratório simulado representativo,
ainda parcialmente concluída. Fontes consultadas em 09/10/2026.

Essas referências orientam este protocolo; não constituem certificação NASA
ou conformidade integral ECSS. O contrato executável prospectivo é
[trl4_software_v1.json](../../configs/qualification/trl4_software_v1.json).
Cada função possui requisito, implementação, documentação, universo de testes,
evidências de campanha e limites. Nenhuma linha recebe aprovação apenas por
aparecer na interface ou por existir um teste com nome relacionado.

O [protocolo TRL 5](orionq_trl5_qualification.md) permanece intocado. Seus limites
de 10 ms/ciclo, 1 s/detalhe, cauda, soak e demais exigências não são relaxados
ou reclassificados como aprovados. A [rodada anterior](../results/orionq_trl5_continuation_20261009.md)
é antecedente explícito, incluindo falhas. Esta rodada avalia interoperabilidade
e comportamento funcional alpha, não prontidão operacional beta/produção.

## Admissão antes da execução

1. Mapear todas as funções críticas do domínio declarado a arquivos e testes
   reais; registrar separadamente capacidades não implementadas/validadas.
2. Coletar os nodeids de cada grupo pytest, incluindo parametrizações, sem
   executar os testes. Congelar esse universo; ausência/skip não equivale a pass.
3. Congelar protocolo, fontes, ferramentas, testes, fixtures, manuais, referências,
   sementes, comandos, versões e ambiente antes dos novos ensaios.
4. Executar em estados e diretórios novos; preservar tentativas malsucedidas.
   Não acessar cadastro, segredos ou journal do operador.
5. Reconferir integridade e vincular cada resultado à matriz. Hashes verificam
   bytes, não autenticam autoria nem exatidão científica por si só.

Uma correção posterior à execução exige nova rodada/congelamento; não mudar
critérios em resposta a uma falha. Coleção/importação e testes das ferramentas
de qualificação são preparação, não resultados da campanha congelada.

A revisão 2 mantém domínio, 20 funções, módulos, cenários, sementes, plano nativo
e critérios da revisão 1. Acrescenta regressões de autorização, edição modal e
troca assíncrona de fonte após as falhas preservadas de `run2`. As expectativas
da interface usam o mesmo frame recebido e exigem permissão **e** recursos
configurados: a permissão não habilita um dispositivo inexistente. A comparação
integral dos novos casos é congelada antes de `run3`; resultados antigos não
são reaproveitados como aprovação das fontes corrigidas.

## Evidências e classificação

Os grupos pytest devem ter todos os casos coletados presentes, sem falhas,
erros, skips ou exclusões silenciosas. O checker lê os casos individuais, não
confia somente nos contadores JUnit. Os ensaios analíticos usam expectativas
e tolerâncias congeladas. O pipeline verifica origem, causalidade, recursos,
recebimento clássico, persistência e replay; as duas repetições conservam as
saídas científicas normalizadas. A semente declarada é de reprodução conhecida,
não holdout novo nem validação física independente.

O ensaio nativo usa autenticação real de sessão Windows, fonte simulada
identificada, topologia completa e Qt Windows. Os testes Qt isolados/integrados
continuam distintos desse ensaio; mocks, fontes emuladas e seletores Qt devem
ser identificados. Deadlines de coleta são guardas contra travamento do teste,
não aprovação de latência/FPS. Registrar operações que não podem ser exercitadas,
sem preencher resultado pronto ou ignorar autorização.

`pass` exige as evidências funcionais especificadas; violação é `fail`; ausência
ou cobertura insuficiente é `not_demonstrated`. O pacote automatizado pode
ficar `complete_for_review`; a conclusão de maturidade exige revisão humana
identificada, vinculada ao domínio, limites e hashes. Revisão interna por agentes
não preenche essa etapa externa. Resultados não atribuem TRL à rede física,
à validade científica integral do gêmeo ou à segurança experimental QKD.

## Matriz prospectiva de funções críticas

O JSON do protocolo é a matriz executável completa: cada linha inclui arquivos
de implementação, módulos de teste, documentação, campanhas e impedimentos.
Os 72 módulos são particionados sem duplicação em três grupos; a coleção
congela os casos e todas as parametrizações antes de qualquer resultado.

| ID | Função | Evidência integrada adicional aos testes |
| --- | --- | --- |
| F01 | Ingresso, dois canais e proveniência | Pipeline, negativos e replay |
| F02 | Cinco protocolos e recursos | Oráculos reduzidos e pipeline causal |
| F03 | Comunicadores e topologia | Cadastro/edição/recusa na interface nativa |
| F04 | Demandas e rotas compatíveis | Pipeline e testes de recursos/rotas |
| F05 | Órbita e disponibilidade | Geometria independente e condições físicas |
| F06 | Gateway/decodificação satelital | Fixtures de missão/ingresso; sem RF local |
| F07 | Instrumentos, configuração e captura | Readback incerto, buffers, cancelamento e disponibilidade |
| F08 | Observações BB84 | Preparação/bases/relógio e pós-processamento por fixtures |
| F09 | Reserva, entrega e consumo de chaves | Estados próprios, entrega incerta e recuperação |
| F10 | Identidade e autorização | Entrada Windows nativa e chamadas indevidas recusadas |
| F11 | PKI, cofre, auditoria e autoridade | Revogação, adulteração, replay e DPAPI Windows |
| F12 | Persistência e reinício de processos | Crash antes/depois de commit e ciclo de vida WAL |
| F13 | Modos, política e reversão | Gate físico e recuperação conservadora |
| F14 | Modelos opcionais | Sem pacote obrigatório; cancelamento/ON/OFF por serviço |
| F15 | FPGA emulado | Configuração, reconexão e recibo pelo fluxo comum |
| F16 | Interface e publicação | Conteúdo recebido, scroll, seleção e callbacks; benchmark nativo |
| F17 | GEO e renderizadores | Satélites acessíveis, trocas e liberação de recursos |
| F18 | Evidência/exportação | Destino real novo e checker adversarial |
| F19 | Bootstrap das fontes Windows | Entrada autenticada, recursos e limpeza do diagnóstico |
| F20 | Oráculos e domínio científico | 35 comparações reduzidas, repetição e hipóteses explícitas |

Nenhum teste isolado é apresentado como comprovação da rede global física.
Módulos reutilizados por duas funções são executados uma única vez. Os testes
do avaliador usam fixtures adversariais descartáveis; não são resultados da
ciência do sistema.

## Execução reproduzível

Ambiente de referência: Python de `.audit-winvenv`, Qt/PySide6 instalado,
Windows e os recursos já disponíveis no repositório. A coleção registrada em
`configs/qualification/trl4_collection_v1.json` pode ser reconferida com
`python -m pytest --collect-only -q` e todos os arquivos de cada grupo do JSON.
Preservar nodeids completos, inclusive parâmetros; qualquer mudança exige uma
nova coleção antes de congelar. Não instalar SDK ou inferir acesso a hardware.

Exemplo de rodada nova, a partir da raiz do projeto:

```powershell
$env:PYTHONPATH = 'src'
$env:QT_QPA_PLATFORM = 'offscreen'
$py = '.audit-winvenv\Scripts\python.exe'
$run = 'outputs\trl4_consolidation_20261009\nova-rodada'
& $py tools\assess_trl4_software.py prepare --directory $run
$plan = Get-Content configs\qualification\trl4_software_v1.json -Raw | ConvertFrom-Json
New-Item -ItemType Directory -Path "$run\pytest-temp" | Out-Null
foreach ($group in @('core', 'integration', 'qt')) {
    $files = @($plan.test_groups.$group)
    & $py -m pytest @files -q --basetemp "$run\pytest-temp\$group" --junitxml "$run\group-$group.xml"
}
& $py tools\run_trl4_console_campaign.py --output-dir "$run\console" --size 1440x900 --timeout-s 600
& $py tools\benchmark_console_performance.py --output "$run\native-ui" --duration 15 --soak 3 --size 3440x1440
foreach ($repeat in @(1, 2)) {
    & $py tools\run_trl5_reference_campaign.py --output-dir "$run\reference-$repeat" --seed 2026100900
    & $py tools\run_trl5_pipeline_campaign.py --output-dir "$run\pipeline-$repeat" --seed 2026100900 --seconds 35 --timeout-seconds 180
}
& $py tools\assess_trl4_software.py assess --directory $run
```

Os dois lançadores nativos selecionam Qt Windows em seus processos próprios.
O plano fixo do benchmark é de 122 ações (8 em cada fase inicial e 90 no
soak), 57 seleções/readbacks e 8 navegações; não basta um relatório declarar
um plano menor como completo. Esses números correspondem ao perfil já definido
de 15 segundos por fase e 3 minutos de soak, antes da nova execução.
O conteúdo completo (alvos, ordem, modos e ações) foi registrado antes da rodada
em `configs/qualification/trl4_native_action_plan_v1.json`, a partir do plano
estático conhecido do mesmo perfil; a comparação exige igualdade integral,
não apenas contagens ou um hash declarado pelo próprio relatório. Não é holdout.
Executar UI/benchmark sem outros testes, builds ou campanhas concorrentes.
As ferramentas recusam diretórios de evidência existentes; não reutilizar estado
ou apagar uma tentativa para fazê-la desaparecer. Registrar stdout/stderr e
códigos de saída por operação. Os diagnósticos usam estado próprio e nunca
encerram uma instância do operador. O campo `commands` do freeze e o perfil do
protocolo documentam os parâmetros; timeout de coleta não é aceitação de
desempenho. Uma rodada incompleta continua revisável e não deve receber pass.

Pilotos de desenvolvimento das ferramentas, em diretórios de preparação, não
substituem estes ensaios. Suas falhas e correções ficam preservadas e são
descritas no relatório; a qualificação usa outra rodada integral após a
estabilidade das fontes, sem reaproveitar seus resultados como aprovações.

## Capacidades fora da qualificação

Não são capacidades qualificadas: relay orbital quântico coerente global,
FPGA físico, RF/óptica instalados e calibrados, replay Swabian nativo sem seus
recursos, segurança experimental/estimador TF-QKD plenamente admitido,
backup operacional completo do workspace, benefício geral de ML e previsão
temporal medida WDM→Bell. Testar a recusa segura dessas capacidades não prova
que elas foram implementadas. O executável anterior também não comprova a
execução das fontes atuais.
