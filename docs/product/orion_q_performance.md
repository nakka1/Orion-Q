# ORION-Q — operação e reprodução do desempenho

Versão 5.51, 07/10/2026. A apresentação e o processamento científico têm
cadências independentes. A otimização não remove etapas, observações ou recibos
dos protocolos; o console apresenta o último frame autorizado.
A agregação de frames visuais não descarta medições. As limitações de recuperação
e persistência reproduzidas estão descritas no relatório de resultados.

## O que mudou

A seleção de nodes e enlaces entra em uma fila de inspeção com até 16 operações.
Cada operação aceita conserva sua identidade, recebe nova autorização no worker
e deixa auditoria autenticada antes da publicação. Uma fila cheia rejeita a nova
solicitação e informa a causa. Frames de seleções superadas podem ser agregados;
as operações e seus registros de autorização permanecem.

Respostas de serviço, seleção e leitura em tempo real compartilham uma sequência
de publicação. Uma resposta atrasada não substitui a fotografia mais recente;
os metadados de um node vêm dos inputs da própria fotografia. A sequência visual
é independente do tick científico e permite reiniciar um cenário legitimamente.

O diálogo de capacidades e as verificações de apresentação utilizam o último
frame comprometido. Isso evita consultas e gravações na thread do Qt. A propriedade
pública de capacidades e as chamadas ao serviço continuam verificando permissões
atuais. Revogação e falhas de autorização bloqueiam novas publicações.

GEO reutiliza projeções, órbitas, geometrias e rasters que não mudaram. Telemetria
não desenhada não invalida o mapa. Só o renderizador de Mapa ou Globo ativo existe;
a troca desconecta listeners, encerra animações e destrói o anterior. A captura
da transição é limitada a 1200×800 pixels. As vistas GEO, Matrix e Graph conservam
dados atuais mesmo ocultas e adiam a apresentação até serem exibidas. Matrix
calcula células sob demanda e limita seu cache a 2048 células.

Painéis flutuantes recebem identidade, origem e os primeiros dados antes de serem
exibidos; campos e tabelas restantes indicam seu carregamento. No console,
a criação de campos terrestres, cards e tabelas satelitais é repartida em turnos
da thread do Qt. A janela
identifica o carregamento, conserva uma única fotografia pendente e só declara
a apresentação completa quando todos os cards, a legenda e o gráfico correspondem
à fotografia apresentada, incluindo todas as seções e os campos recebidos. Os
turnos são cooperativos: uma chamada Qt individual não pode ser preemptada.
Fechar a janela cancela esse trabalho de apresentação;
isso não cancela medições nem operações aceitas pelo backend. A inspeção
de satélites atualiza seu painel orbital e científico sem preencher gráficos ou
construir os cards de conexões terrestres ocultos. O Inspector apresenta apenas sua aba ativa, mantém
os dados atuais das demais e atualiza os detalhes ao abri-las. Campos e estações
reutilizam suas linhas quando a configuração é preservada.

As telas ocultas armazenam a seleção e apresentam o snapshot mais recente ao
entrar. Uma recalibração não expira por quantidade de frames visuais: o console
acompanha o progresso recebido e sinaliza ausência de evidência após 30 segundos
sem progresso, mantendo a confirmação dependente do recibo do backend.

Os registros brutos usam o mesmo formato zlib com nível 1, reversível byte a byte.
O tamanho comprimido pode ser maior que no nível 6. As quotas continuam vigentes;
nenhum evento é descartado silenciosamente para economizar armazenamento.
As APIs de estado e passo continuam retornando o contrato completo por padrão.
O provider pode solicitar apenas metadados quando não consome o estado completo.

No Windows, a proteção de segredos conserva um único tipo `DATA_BLOB` e os
protótipos das funções DPAPI, inicializados sob demanda. Os buffers e a operação
criptográfica continuam novos a cada chamada. Nenhum segredo ou resultado de
autorização é conservado nesse cache. Isso evita que o cache interno de tipos de
ponteiro do Python retenha uma nova classe a cada acesso ao cofre. O escopo
CurrentUser, a entropia, os dados cifrados existentes e a liberação de memória
do Windows são preservados.

O worker da campanha temporal inicializa suas bibliotecas nativas de cálculo
antes de iniciar a leitura de comandos em stdin. Isso corrige um bloqueio
reproduzido no Windows entre o carregamento de DLLs e o listener. A inicialização
fica restrita a esse worker; a abertura comum do console continua sob demanda.
Cancelamento, cálculos, sementes e recibos usam os mesmos contratos.

Os validadores aguardam a confirmação recebida pelo Qt e a apresentação completa
do snapshot correspondente antes de ler os controles. Uma mensagem `UNCONFIRMED`
é uma recusa, e não satisfaz o critério `CONFIRMED`. O fornecedor sintético de
desenvolvimento conserva a configuração validada de ambos os canais ao criar
ou atualizar enlaces e ao avançar seu estado. Essa configuração não atesta
comunicação física nem inventa observações que a fonte não fornece.

## Observar sobrecarga

O rodapé acrescenta `COMPUTE duração/período` quando um passo leva mais tempo que
seu período desejado. Seu tooltip mostra duração, overruns, eventos pendentes,
seleções pendentes e rejeitadas, e frames visuais agregados. Esses valores são
estatísticas de apresentação e execução, separados das métricas científicas.
Um período desejado de um segundo não certifica um tick por segundo de relógio
real: a topologia completa pode exigir mais tempo. A aplicação não elimina passos
científicos nem cria resultados para compensar essa diferença.

## Reproduzir os testes

Execute sem outra compilação ou campanha de benchmark concorrente. Use diretórios
novos; os diagnósticos mantêm seus próprios estados e não utilizam o cadastro do
operador. No Windows, com o ambiente auditado deste repositório:

```powershell
$env:PYTHONPATH = 'src'
$env:QT_QPA_PLATFORM = 'windows'
.audit-winvenv\Scripts\python.exe tools\benchmark_console_performance.py `
  --output outputs\performance-new\source --duration 20 --soak 0 --size 3440x1440
.audit-winvenv\Scripts\python.exe tools\benchmark_console_performance.py `
  --output outputs\performance-new\soak --duration 20 --soak 5 --size 3440x1440
```

Para a versão compilada, use o mesmo wrapper e acrescente
`--executable <caminho-do-ORION-Q.exe>`. O executável também oferece:

```powershell
ORION-Q.exe --performance-test C:\temp\orion-performance-new `
  --performance-duration 20 --performance-soak 5 --performance-size 3440x1440
```

O wrapper registra CPU e memória de todo o processo e seus workers em um sampler
independente do GIL. O workload utiliza a topologia global, todas as órbitas,
simulação autenticada, cliques, scroll, abas e alternâncias repetidas. Os arquivos
contêm condições, hashes, heartbeat do Qt, pinturas, ações, componentes,
telemetria, integridade da auditoria e evolução de memória.

Para profiling, faça uma execução separada com `--profile`; não compare seus
tempos com o benchmark normal. O wrapper também oferece `--local-profile` para
medir somente os métodos selecionados, mantendo a aplicação Qt original.
O resultado distingue tempo da callback e tempo
até a primeira pintura iniciada depois que ela terminou. Uma pintura anterior
durante a callback não comprova a atualização da seleção. O teste registra
separadamente o primeiro retorno visual e a conclusão verificada dos campos,
conexões, séries, origem e instante de apresentação. Uma indicação de carregamento
não conta como resultado completo.

O diagnóstico não ativa equipamentos nem autoriza políticas de controle físico.
No cenário de carga, o detector demonstrativo já configurado aparece ativo;
isso é diferente do catálogo de modelos opcionais, cujos defaults e admissão
são verificados separadamente. O diagnóstico não certifica geração QKD.
Os [resultados medidos](../results/orion_q_performance_20261007.md)
documentam as condições, ganhos e limitações da validação.

## Qualificação de 08/10/2026

A versão `5.51.0-native-20261008-015715` passou os gates compilados e a carga
normal de cinco minutos, com 190 ações e 90 leituras completas. O diagnóstico
separado de vinte minutos completou 640 ações e 315 leituras; o critério de
plateau de RSS passou nessa janela, mas memória privada cresceu 23,19 MiB.
Esse resultado não certifica ausência de vazamentos por tempo ilimitado.
Startup e a primeira abertura de Keys têm regressões observadas; os números
e as candidatas rejeitadas estão preservados no relatório de resultados.

Para o diagnóstico com coleta limitada, usando outra saída nova:

```powershell
.audit-winvenv\Scripts\python.exe `
  outputs\performance_20261007\geo\benchmark_streamed_observer_diagnostic.py `
  --output outputs\performance-new\lifecycle `
  --duration 20 --soak 20 --size 3440x1440
.audit-winvenv\Scripts\python.exe `
  outputs\performance_20261007\geo\verify_streamed_observer_evidence.py `
  --input outputs\performance-new\lifecycle `
  --output outputs\performance-new\lifecycle\stream-integrity-verification.json
```

O wrapper acima é um observador externo de fonte, não uma alteração de captura
científica nem um benchmark normal do EXE. Seus três streams têm fila limitada,
falhas explícitas e conservação integral verificável. O censo Qt tem custo e
não deve ser usado para declarar ganho de latência. A memória privada exige
amostragem do PID Qt verdadeiro; o launcher e o auxiliar em
`outputs/performance_20261007` registram a cobertura real. Não subtraia memória
lógica da instrumentação da RSS e não reduza históricos ou proteção contra
reutilização/replay para produzir uma curva aparentemente estável.
