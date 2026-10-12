# OrionQ — operação autenticada pelo terminal

O operador terminal controla uma instância independente do núcleo pela API
HTTPS/mTLS. Não inicia Qt, login Windows, DPAPI, um simulador local ou um
workspace alternativo. O processamento e os registros duráveis permanecem no
servidor. A atuação física continua bloqueada; ML depende da configuração
opcional do núcleo.

## Identidade e execução

Provisionar o serviço e seus clientes conforme
[orionq_linux_service.md](orionq_linux_service.md). Cada cliente precisa de CA
pública confiável, certificado próprio, chave privada e concessões de função e
recurso registradas no backend. O hostname da URL deve constar no SAN do
certificado do servidor. Estar em loopback não concede autoridade.

```bash
python -m quantum_autopilot.service.operator_cli --help

python -m quantum_autopilot.service.operator_cli \
  --url https://orion-core:8443 \
  --ca /run/operator/ca.pem \
  --cert /run/operator/operator.pem \
  --key /run/operator/operator.key \
  --timeout-s 30 health
```

O entry point de distribuição é `orion-operator`, quando instalado no perfil
que o inclui. As opções `--core-url`, `--core-ca`, `--core-cert`, `--core-key` e
`--core-timeout-s` são aliases. Opções de conexão e `--output` precedem o
subcomando.

Credenciais devem ser provisionadas externamente com permissões mínimas e
fora do repositório e da imagem de contêiner. A CLI não aceita senhas, tokens
ou material privado em argumentos e não acessa o cofre do servidor. A chave
privada do cliente deve estar pronta para carregamento pelo TLS. Uma chave
cifrada sem leitor protegido é rejeitada sem prompt. Integrações Python podem
fornecer `build_tls_context(..., key_password_reader=reader)` conectado ao seu
serviço institucional de segredos; a CLI não oferece exportação ou entrega de
senhas privadas do núcleo.

## Comandos e autorização

| Comando | Operação no núcleo | Uso |
|---|---|---|
| `health` | GET `/api/v1/health` | Saúde e fronteira operacional |
| `topology` | GET `/api/v1/control-plane/topology` | Inventário persistido |
| `state [--metadata-only]` | GET `/api/v1/network/state` | Estado e telemetria; sem avançar a simulação |
| `step [--metadata-only]` | POST `/api/v1/network/step` | Passo explícito, quando autorizado |
| `network-command VERB --input FILE` | POST `/api/v1/network/commands` | Comando admitido pelo backend |
| `console-command VERB --input FILE` | POST `/api/v1/console/commands` | Operação de serviço; não requer GUI |
| `evidence --scope SCOPE` | POST `/api/v1/console/evidence` | Evidência autorizada e hash, sem caminho de escrita no servidor |
| `mixed-status` | GET `/api/v1/mixed-controller/status` | Configuração, observações, demandas e impedimentos |
| `mixed-configure --input FILE` | POST `/api/v1/mixed-controller/configure` | Configurar capacidades e canais |
| `mixed-ingest --input FILE` | POST `/api/v1/mixed-controller/observations` | Admitir blocos de observações |
| `mixed-demand --input FILE` | POST `/api/v1/mixed-controller/demands` | Descobrir rota e reservar recursos de controle |
| `mixed-release --input FILE` | POST `/api/v1/mixed-controller/release` | Liberar reserva identificada |
| `watch` | GETs do snapshot consolidado; status misto na visão de reservas ou exclusivamente em `mixed` | Monitor interativo sem GUI ou relatório finito |

Permissões são verificadas pelo backend em cada chamada, incluindo revogação e
escopos. O cliente não deduz autoridade de nomes de usuário ou de papéis em
arquivos JSON. Um auditor pode consultar os recursos concedidos; uma tentativa
de `step` ou outra mutação sem autorização é rejeitada pelo serviço. As ações
do controlador misto usam `network.read`, `network.configure`,
`ingestion.write` e `network.operate`, conforme o endpoint.

## JSON, saída e limites

Payloads são objetos JSON em UTF-8, lidos de `--input arquivo.json` ou
`--input -` para stdin. O limite é 1 MiB. Chaves duplicadas, números não
finitos, objetos profundos, arquivos não regulares e symlinks são rejeitados
antes de enviar a operação. Schemas específicos, unidades e compatibilidade
continuam sob validação autoritativa do núcleo.

```bash
printf '%s\n' '{"kind":"simulated","seed":17,"scenario":"quiet"}' | \
  orion-operator --url https://orion-core:8443 \
  --ca /run/operator/ca.pem --cert /run/operator/operator.pem \
  --key /run/operator/operator.key \
  network-command configure_network_input --input -
```

Respostas autorizadas são JSON em stdout, permitindo composição com outras
ferramentas. Diagnósticos ficam em stderr, sem conteúdo dos argumentos,
payloads, respostas de erro do servidor ou exceções internas. Não encaminhe
stdout de evidências a um log público. Para salvar um resultado, use
`--output /caminho/novo.json` antes do subcomando: o arquivo é criado de forma
exclusiva, não sobrescreve arquivos e usa modo 0600 no POSIX. Seu diretório pai
deve existir. Esse caminho pertence ao computador cliente.

## Configuração do controlador misto

Exemplo de `mixed-config.json` para dois pontos com canais distintos:

```json
{
  "schema": "orion.mixed-controller.configuration.v1",
  "nodes": [
    {"node_id":"A","capabilities":["classical_control","time_tagging","correlation","entanglement_memory"],"capacity":8,"session_id":"capture-A","clock_domain":"aligned-clock"},
    {"node_id":"B","capabilities":["classical_control","time_tagging","correlation","entanglement_memory"],"capacity":8,"session_id":"capture-B","clock_domain":"aligned-clock"}
  ],
  "links": [
    {"link_id":"A-B","a":"A","b":"B","classical_channel_id":"A-B-classical","quantum_channel_id":"A-B-quantum"}
  ],
  "freshness_s":60
}
```

São admitidos 2–256 nodes e 1–1024 enlaces. Cada node exige
`classical_control`; capacidades combináveis são `time_tagging`,
`correlation`, `entanglement_memory` e `bsm`. Capacidade: 1–65536. Validade
das observações: 1–3600 segundos. Identificadores dos canais são distintos e
não podem ser reutilizados no cadastro. Intermediários de uma rota de
entrelaçamento precisam também da capacidade BSM.

```bash
orion-operator --url https://orion-core:8443 \
  --ca /run/operator/ca.pem --cert /run/operator/operator.pem \
  --key /run/operator/operator.key mixed-configure --input mixed-config.json
```

O cadastro não afirma que houve comunicação ou aquisição. Uma demanda sem
observações válidas retorna os impedimentos, incluindo falta de sessão,
recursos, alinhamento temporal, disponibilidade dos canais ou observações
recentes.

## Aquisição e demandas

`mixed-ingest` recebe `{"records":[...]}`. Cada registro contém:

- `observation`: schema `orion.controller.observation.v1`, `event_id`,
  `node_id`, `session_id`, `sequence`, `timestamp_ps`, `clock_domain`, `kind`
  e `data`;
- `provenance`: `source_id`, `adapter_id`, `origin` (`physical`, `simulated`
  ou `replay`) e `record_sha256` do registro original.

Tipos admitidos: `session_started`, `link_state`, `resources`, `timetags`,
`correlation`, `heartbeat` e `fault`. Tempos são inteiros em picossegundos; cada bloco tem no
máximo 4096 eventos, em ordem temporal e com canal diferente de zero. Eventos
perdidos devem ser explicitados em `lost_events`. Sessão, configuração,
sequência e domínio de relógio devem corresponder à fonte cadastrada.

Produza os registros por adaptadores do projeto:
`lab.mixed_sources.normalize_acquisition` para aquisições e
`normalize_sequence_callbacks` para callbacks nativos identificados. Ambos
preservam origem e rastreabilidade. O caminho físico exige a autoridade do
instrumento, configuração verificada e relógio alinhado; o normalizador não
abre equipamento. Timestamps não provam bases, bits, fidelidade, geração de
chaves ou entrelaçamento. Uma etiqueta de origem, isoladamente, não qualifica
medição física.

Uma observação `correlation` contém `block_sha256`, `configuration_id`,
`first_channel`, `second_channel`, `window_ps`, `event_count` e
`coincidence_pairs`, conforme o contrato do backend. Os hashes vinculam bloco
e configuração; canais são distintos e diferentes de zero. Contagens de
coincidência são apresentadas como análise do bloco, sem inferir fidelidade,
QBER ou entrelaçamento.

Após a ingestão, a demanda usa este formato de `demand.json`:

```json
{"request_id":"A-B-control-001","source":"A","target":"B","operation":"classical_control","ttl_s":30}
```

Operações: `classical_control`, `correlation` e `entanglement`; TTL: 1–3600 s.
Use identificadores próprios e mantenha-os durante consulta e liberação:

```bash
orion-operator --url https://orion-core:8443 \
  --ca /run/operator/ca.pem --cert /run/operator/operator.pem \
  --key /run/operator/operator.key mixed-demand --input demand.json

printf '%s\n' '{"request_id":"A-B-control-001"}' | \
  orion-operator --url https://orion-core:8443 \
  --ca /run/operator/ca.pem --cert /run/operator/operator.pem \
  --key /run/operator/operator.key mixed-release --input -
```

Uma resposta `reserved` aloca slots de controle. `quantum_result: null` e
`physical_action_performed: false` permanecem explícitos: essa reserva não é
um par estabelecido, chave QKD, atuação em FPGA ou resultado óptico.

## Monitor ao vivo, sem interface gráfica

Em um terminal Linux interativo:

```bash
orion-operator --url https://orion-core:8443 \
  --ca /run/operator/ca.pem --cert /run/operator/operator.pem \
  --key /run/operator/operator.key \
  watch --interval-s 1 --view satellites --select SAT-ID
```

`watch` usa apenas stdlib, HTTPS e curses no terminal POSIX. As visões são
`overview`, `links`, `nodes`, `satellites`, `reservations`, `audit`, `raw` e
`mixed`. Use 1–8 ou Tab para alternar, setas para selecionar/rolar, Enter para detalhes,
`p`/espaço para pausar a apresentação, `r` para atualizar metadados, `h`/`?`
para ajuda e `q` para sair. Redimensionar o terminal reorganiza a apresentação
e preserva seleção. A pausa visual não pausa processamento ou persistência no
núcleo.

O modo interativo abre o layout `dashboard`: painéis de estado, lista de
componentes e gráficos do componente selecionado, com cores semânticas. O
cabeçalho mantém identidade, conexão, idade de recebimento e atalhos. Terminais
estreitos reduzem os painéis; redimensionar preserva a seleção. Enter continua
abrindo os detalhes recebidos do backend, e as setas rolam esse conteúdo. Para
usar a apresentação textual anterior, escolha `watch --layout plain`;
`watch --layout dashboard` seleciona o dashboard explicitamente.
Durante a execução interativa, `v` alterna os dois layouts. Em `overview`, as
setas percorrem os enlaces exibidos, compartilhando a seleção com `links`;
Enter abre os detalhes do enlace selecionado.

As logos ASCII fornecidas pelo autor aparecem nas telas de espera por dados
e de ajuda. O terminal escolhe a maior versão completa que cabe no espaço
livre, considerando colunas e linhas; redimensionar refaz essa escolha sem
cortar a arte ou deslocar instruções e informações operacionais. Se nenhuma
versão cabe, a apresentação mantém apenas o cabeçalho. Ao receber o primeiro
snapshot, a tela de espera dá lugar aos painéis do backend. A logo não avança
o processamento nem representa progresso de aquisição.

As versões textuais preservam os caracteres e os espaços internos enviados;
somente margens externas vazias foram removidas. As variantes enviadas apenas
como PNG foram convertidas pela ocupação da grade de caracteres, com essa
origem registrada em
[provenance.json](../../outputs/terminal-ascii-brand-20261011/provenance.json).
O arquivo `ascii-art110.png` possui uma grade de 160 colunas e foi cadastrado
como Q160. O nome completo usa letras claras e Q coral; versões só do Q usam
coral. Quando a codificação requer ASCII, blocos `█` tornam-se `#`, mantendo
o desenho e as dimensões. `NO_COLOR` conserva a mesma arte sem a paleta.
Nesse modo ASCII, caracteres Unicode nos textos recebidos são apresentados
por escapes, como `\xb7`, `\u6c34` ou `\u0301`, antes da limitação de largura.
Os registros originais permanecem no backend e no snapshot admitido. A
apresentação UTF-8 conserva os caracteres; a saída ASCII estrita não falha
quando o rótulo da fonte contém um separador Unicode.
O cabeçalho de três linhas continua disponível em todas as visões.

### Modo visual E.V.A. no terminal

No `watch` interativo, **L** abre um formulário local de aparência. Digite
`S. Ikari`, pressione Enter, digite `thirdpilot` no campo mascarado e pressione
Enter novamente. O nome aceita maiúsculas/minúsculas e espaços externos; a
frase exige correspondência exata. O formulário não transmite esses campos ao
serviço nem os usa como identidade. A conexão continua exigindo o certificado
mTLS e as permissões do backend.

O gatilho apresenta `EMERGENCY` e
`YOU SHOULD NOT BE ABLE TO CONNECT TO EVA`. Enter ou Esc fecha a mensagem e
retorna ao monitor com a paleta EVA-01: roxo, verde e laranja, mantendo cores
neutras e vermelho para falhas. A forma das logos e os dados permanecem os
mesmos. O terminal mostra `Connected to E.V.A 0-1` em uma linha própria,
separada da identidade `Peer` e da condição real da conexão.

**U — Unplug from E.V.A** encerra o modo visual e restaura a apresentação
normal, preservando seleção, snapshots e consultas. **Q** encerra o cliente
terminal. Nenhuma dessas ações desliga o núcleo independente. Esc cancela o
formulário e limpa seus campos; entrada limitada impede buffers crescentes.
Durante o preenchimento, letras como `p`, `r`, `h`, `v`, `j` e `k` são texto,
sem acionar os atalhos do monitor. Não há animação nem áudio no terminal.

O modo é local à execução interativa: não existe opção de senha em argumentos,
variável de ambiente ou relatório finito. `NO_COLOR`, `TERM=dumb` e saída
ASCII conservam os textos e o funcionamento; quando cores são desativadas,
a paleta do usuário é respeitada. Encerra-se ao sair do cliente e não é
persistido como cadastro, configuração da rede ou identidade de auditoria.

O monitor reduz para 100 ms a espera do curses para distinguir Esc isolado
das sequências das setas. Uma variável `ESCDELAY` explicitamente configurada
é respeitada, permitindo aumentar a espera em conexões lentas ou terminais
seriais. Essa espera é de interpretação de teclado, não de cálculo científico.

### Gráficos e atualização

Os gráficos do inspetor usam pontos menores, em rosa, com duas posições
horizontais e quatro verticais por célula Unicode Braille. Essa resolução
visual preserva as amostras recebidas: não interpola, duplica medições ou
conecta uma lacuna. Medições que ocupam o mesmo ponto são contabilizadas em
`overlap`; `samples` informa a quantidade de registros, incluindo lacunas
(`gaps`) e valores fora do domínio (`invalid`), que não são pintados.

Com timestamps válidos e ordenados, o eixo cobre os últimos **60 segundos**,
de `60s` à esquerda a `0s` à direita. Enlaces usam o `network_time_s` do
backend; séries de protocolo usam o último `at_s` publicado em seu próprio
domínio de relógio. Intervalos irregulares e ausência de dados deixam espaços
vazios; polling não adiciona pontos. A janela não mede segundos de parede
quando o relógio científico está acelerado ou lento. O rodapé apresenta os
tempos efetivos das amostras mostradas. Sem um relógio completo e coerente,
o gráfico informa `sample order` e apresenta as últimas amostras na ordem
recebida, sem atribuir tempos artificiais.

Fidelidade e QBER usam eixos de **0% a 100%**; a fidelidade legada conserva sua
qualificação como estimativa do backend. Taxas usam zero e o máximo visível;
protocolos respeitam os limites e as unidades publicados, inclusive fases
com valores negativos. A escala não é um intervalo de confiança. O modo
ASCII mantém um ponto por célula; painéis compactos mantêm sparklines por
amostra. A resolução visual não aumenta a precisão científica. Valores ausentes
continuam `no input`, e zero recebido continua sendo uma medição. Nenhum
gráfico transforma taxas ou contadores em uma forma de onda, calcula métricas
quânticas ausentes ou atribui dados de outra sessão ao componente selecionado.

O raster segue as posições do [bloco Unicode Braille Patterns](https://www.unicode.org/charts/PDF/U2800.pdf).
A aparência dos pontos depende da fonte do terminal; o fallback ASCII mantém
os mesmos dados e limites, com resolução visual menor.

A decoração, bordas e pontos têm fallback ASCII quando a codificação exige.
`NO_COLOR` presente ou `TERM=dumb` desativa a paleta colorida da interface e
preserva as cores padrão do terminal; um terminal
sem cores mantém os rótulos de estado e os atalhos. A pintura curses aplica
atributos separados do texto: o render e os relatórios finitos não contêm
sequências ANSI. Dados com caracteres CJK são limitados pela largura em
células, preservando os identificadores publicados.

Rajadas de teclas são processadas em ordem e agrupadas antes da pintura, para
que a repetição das setas não crie uma fila de redesenhos. O scroll permanece
limitado ao conteúdo: ao chegar ao fim, a seta para cima responde imediatamente.
Publicações novas e entrada do operador atualizam a tela; o indicador de idade
se renova a cada segundo, sem impor essa cadência ao cálculo científico.

São apresentados os estados recebidos do backend, condições dos enlaces,
fidelidade/QBER/taxa de chave e séries por protocolo, além dos registros de
Housekeeping/Science e conexões dos satélites. Séries usam amostras publicadas,
mantêm lacunas e não interpolam nem transformam uma taxa em forma de onda.
`no input` distingue ausência de medição de um valor zero. Contadores,
estimativas condicionais e chaves certificadas mantêm a identificação do
backend. Reserva de slots não é resultado quântico.

A visão `mixed` consulta somente `mixed_status`, independentemente do engine
da rede de demonstração. Exibe disponibilidade, sequência, tags recebidas,
perdas, recursos e análise de correlação publicada. Detalhes preservam os
campos recebidos de sessão, domínio de relógio, configuração e proveniência.
Esse endpoint não publica a identidade do operador; a tela informa essa
ausência em vez de deduzir um nome de uma sessão local. A conexão continua
autenticada por mTLS e as permissões são verificadas no serviço.

Há um único worker para consultas, um slot de requisição e um slot de
publicação, sem fila crescente. A aquisição científica permanece no serviço;
a pintura do terminal não solicita `step` nem executa modelos. Metadados
estáticos são obtidos no início, após mudanças de sessão/configuração
publicadas ou por `r`; as demais consultas preservam o cache e trazem estado
dinâmico. A visão de reservas acrescenta uma consulta ao status misto, sujeita
às permissões e quotas do backend; `mixed` faz apenas essa consulta. Idade de
recebimento e estado de falha são independentes para as duas fontes, inclusive
durante alternância de abas. Intervalos aceitos: 0,1–60 s; escolha uma
cadência compatível com as quotas do serviço.

Na versão 5.64.1, detalhes de nodes/satélites usam `node_ids` das séries
publicadas; enlaces usam a sessão explícita em
`provenance.technology.session_id`. As séries mantêm a identificação da
sessão e não são atribuídas a um enlace apenas por proximidade ou por
compartilhar um endpoint. Housekeeping/Science permanecem no início dos
detalhes de satélites. A ajuda não herda o deslocamento de uma lista, e o
scroll no fim é limitado para que a primeira seta para cima volte a mover
a apresentação.

Estruturas internas inválidas são rejeitadas antes de substituir o último
quadro, que permanece explicitamente `STALE`. A visão de reservas considera
também a idade/falha do status misto. A visão `mixed` não atribui à sua
publicação uma identidade guardada de outra aba.

Falhas de TLS, revogação ou indisponibilidade deixam o último quadro
explicitamente `STALE`, com idade de recebimento. Uma consulta posterior pode
reconectar somente a leitura; não há reenvio de mutações ou fallback local.
Sair cancela publicações futuras. Um GET já iniciado pode terminar até seu
timeout, sem manter a saída do terminal bloqueada.

Sem TTY, incluindo uso por pipe e PowerShell, `--frames` é obrigatório e a
saída padrão é o layout `plain`, um relatório textual finito sem comandos ANSI:

```bash
orion-operator --url https://orion-core:8443 \
  --ca /run/operator/ca.pem --cert /run/operator/operator.pem \
  --key /run/operator/operator.key --output live-report.txt \
  watch --frames 5 --interval-s 1 --view links
```

Para salvar também os painéis e gráficos textuais do dashboard, use
`watch --frames 5 --layout dashboard --view links`. Essa escolha continua
finita e sem ANSI; não inicia curses nem uma GUI. `--layout plain` permite
selecionar explicitamente o formato anterior em qualquer modo.

`--output` permanece exclusivo. Uma visão `raw` é uma projeção limitada pela
tela; use `evidence` para salvar o JSON autorizado completo. O modo interativo
curses é Linux/POSIX; a GUI Windows permanece separada e inalterada por esse
monitor. Isso não é uma interface de firmware de FPGA ou OBC.

## Quedas, recibos e códigos de saída

Comandos de operação realizam uma única requisição, sem retry automático;
`watch` renova apenas consultas de leitura. A perda de
uma resposta pode ocorrer depois de o backend confirmar uma mutação. Consulte
`state`, `mixed-status` ou `evidence` e os identificadores duráveis antes de
decidir uma nova ação. Não repita comandos para recriar um arquivo de saída.

| Código | Significado |
|---|---|
| `0` | Resposta recebida e saída escrita |
| `2` | Entrada/argumentos ou identidade TLS inválidos; nenhuma chamada realizada |
| `3` | Requisição rejeitada ou indisponível |
| `4` | Resultado da mutação incerto; conferir estado/recibos |
| `5` | Resposta recebida, mas saída local não pôde ser escrita |
| `130` | Monitor interrompido pelo usuário |

Os testes em `tests/test_core_operator_cli.py` verificam TLS real local,
permissões do backend, ausência de GUI, limites de entrada, saída exclusiva e
entrega incerta sem reenvio. Fixtures e callbacks simulados não qualificam
Swabian, FPGA, sincronismo medido ou qualquer enlace físico; esses requisitos
continuam dependentes da bancada e dos adaptadores disponíveis.

`tests/test_terminal_live.py` cobre cache e ordem de publicações, backpressure,
saída sem espera por uma consulta lenta, ausência de publicação após fechar,
resize, seleção, pausa visual, gaps e dados faltantes. Há teste TLS real de
monitoramento sem avançar o relógio científico e teste de teclado/resize em
PTY Linux com dados explicitamente identificados como fixture de interação.

`tests/test_terminal_dashboard.py` verifica largura em células de 1–240
colunas, telas pequenas, spans de cor correspondentes ao texto, pausa e
seleção, scroll de detalhes, sanitização de controles, ausência de importação
de GUI/modelos e gráficos com lacunas, zero e amostras não finitas. Os casos de
Mixed e reservas mantêm a ausência de identidade publicada e
`quantum_result: null`; layout e cores não concedem autorização adicional.

A campanha contra o backend completo pode exercitar também a CLI instalada
em um PTY real, interpretando a tela atual com
[pyte](https://pyte.readthedocs.io/en/latest/):

```bash
# Dependência exclusiva do teste; a TUI de produção não requer pyte.
python -m pip install pyte==0.8.2
python tools/run_terminal_live_campaign.py \
  --repository /caminho/OrionQ \
  --state /var/tmp/orionq-terminal-teste-novo/private \
  --report /var/tmp/orionq-terminal-teste-novo/public/report.json \
  --frames 6 --interval-s 1 --client-timeout-s 15 --pty
```

Use o Python do wheel headless instalado, uma conta sem privilégios e caminhos
novos. O setup provisiona credenciais de teste em diretório privado; somente
relatórios/capturas inspecionados devem ser exportados. A opção `--pty`
verifica teclado, todas as abas, seleção de satélites, resize, pausa visual,
scroll, indisponibilidade temporária do serviço, recuperação e restauração
de termios em `q`/Ctrl+C. Consulte o
[relatório de validação 5.64.1](../results/orionq_terminal_linux_20261011.md).

Para preencher também `mixed` e `reservations`, acrescente
`--mixed-fixture /caminho/resultado/cpu-external`, apontando para uma campanha
mista identificada e aprovada. Esse diretório precisa conter
`configuration.json`, `normalized-observations.jsonl` e `report.json`.
O runner confere o hash da configuração, a quantidade de registros e a origem
simulada; depois configura e ingere pela API autenticada, solicitando uma
reserva clássica entre N02/N03 e a ponte indisponível entre N01/N11. A tela
é confrontada com o readback do serviço. Esse caso é replay da evidência
anterior, sem nova aquisição física ou execução do SeQUeNCe. Os registros
brutos e as credenciais de setup permanecem locais; o relatório público
identifica os hashes e os recibos sem exportar esse estado privado.
