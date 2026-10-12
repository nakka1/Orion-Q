# OrionQ 5.65.0 — compilação e varredura Windows/Linux

Data: 11 de outubro de 2026. Esta entrega reúne a versão atual, incluindo
as logos ASCII e o modo visual E.V.A. da TerminalUI. Os artefatos anteriores,
o histórico Git original e os cadastros do operador são preservados.

**Compilações qualificadas no escopo de software selecionado.** A aceitação não equivale a
qualificação de hardware nem permite afirmar funcionamento integral sem
ressalvas. A corrida no consumo de pares simulados por instâncias independentes
do engine continua reproduzível; seu escopo está registrado abaixo.

## Ambiente e vínculo com as fontes

- Windows 11 Pro x64, build 26200, Ryzen 7 5700X, oito cores/16 threads,
  aproximadamente 32 GiB de RAM e RTX 3060. Monitor: 3440 × 1440. As fixtures
  Windows são novas, privadas e ficam no SSD `C:`; o checkout fica em `E:`.
- Linux: Ubuntu 24.04.5 LTS/WSL2, x86_64, Python 3.12.3 e conta sem privilégios,
  UID 10001. Estado e certificados de teste ficam no filesystem Linux nativo.
- A versão é declarada em `pyproject.toml`; os rótulos da interface e os
  metadados dos executáveis são derivados dessa declaração.
- O [manifesto inicial](../../outputs/release-5.65.0-20261011/windows/source-freeze.json)
  identifica 677 arquivos de produção/configuração/packaging/FPGA por SHA-256.
  Metadados `egg-info`, caches e bancos operacionais não integram esse corpus.
  Os testes e ferramentas têm vínculos próprios nas campanhas.

[Ambiente Windows](../../outputs/release-5.65.0-20261011/windows/environment.json)
e [versões dos runtimes](../../outputs/release-5.65.0-20261011/windows/runtime-versions.json).

## Interface Windows: fluxos completos em execução nativa

O ensaio usa Qt Windows, sessão real do sistema operacional, backend normal
e fontes simuladas explicitamente identificadas. Executa fontes do checkout;
os gates do EXE compilado constituem uma validação separada. Os **16/16 fluxos
passaram**, com fontes inalteradas e nenhuma atuação física.

| Caso de uso | Resultado |
|---|---|
| Entrada pela sessão Windows e recebimento de ticks | Aprovado |
| Navegação pelas sete telas principais | Aprovado |
| GEO, Matrix e Graph | Aprovado |
| Detalhes de nodes, gráficos e percurso do conteúdo | Aprovado |
| Detalhes de satélites, órbita e telemetria recebida | Aprovado |
| Alternância Mapa/Globo e descarte do renderizador anterior | Aprovado |
| Campos dinâmicos de comunicadores | Aprovado |
| Cadastro terrestre e persistência | Aprovado |
| Cadastro de satélite e persistência | Aprovado |
| Recusa de configuração incompatível | Aprovado |
| FPGA emulada, configuração e monitoramento | Aprovado |
| Consulta de chaves e auditoria | Aprovado |
| Exportação de evidências pela interface | Aprovado |
| Cancelamento, confirmação e restauração do controle ML | Aprovado |
| Recusa de atuação física | Aprovado |
| Encerramento, workers e liberação de recursos GEO | Aprovado |

[Relatório dos fluxos](../../outputs/release-5.65.0-20261011/windows/native-ui-campaign/report.json)
e [recibo da cópia pública](../../outputs/release-5.65.0-20261011/windows/native-ui-campaign/evidence-copy.json).
As 14 imagens e os dois JSONs foram copiados com bytes conferidos; bancos,
certificados e logs privados ficaram fora da entrega. O driver pode deixar
o conteúdo rolado ao tornar um controle visível. A posição nas capturas de
detalhes não comprova, sozinha, um percurso completo de scroll ou um corte
indevido; o alcance dos checks deve ser lido no relatório.

Um [probe adicional de viewport](../../outputs/release-5.65.0-20261011/audit/native-viewport/summary.json)
percorreu topo/fundo/topo de CIMATEC, CBPF, GEO-AMERICAS-SIM e HOUSEKEEPING:
12 readbacks completos e oito mudanças com eventos de pintura, sem hover,
seleção de texto ou repaint forçado. Os quatro critérios de entrada,
detalhes terrestres, detalhes satelitais e encerramento passaram em 36,44 s.
Isso comprova atualização e conservação do conteúdo nesses percursos,
sem certificar legibilidade de cada pixel.

## Dashboard web complementar

Lint e build de produção passaram em uma cópia isolada, com **83 arquivos
públicos de entrada idênticos** e lockfile preservado. Node 22.17.0,
pnpm 11.25.0. O `node_modules` original aponta para a localização antiga do
checkout; não foi alterado. A restauração offline não encontrou metadados
de pacotes, e uma instalação nova com `--frozen-lockfile` permitiu executar
os checks. A comparação inicial de caches gerados foi preservada; CSS de
fontes sob `.vinext` contém caminhos do diretório de build e não é entrada
de código-fonte. Não houve ensaio de browser/servidor desse dashboard.

[Relatório do dashboard](../../outputs/release-5.65.0-20261011/windows/dashboard-validation.json).

## Falhas e limites encontrados

| Item | Evidência e impacto |
|---|---|
| Consumo concorrente no caminho legado de pares simulados | Duas instâncias independentes de `NetworkEngine`, sobre o mesmo banco/demanda, retornam `consumed` para um único par. O controle com uma instância rejeita o segundo consumo. A leitura e a escrita em `engine.py` não constituem uma única transação de consumo. É um defeito aberto desse caminho; não é uma demonstração de reutilização de chaves no KME. |
| Arbitragem entre bancos independentes | Reservas do controlador misto são exclusivas dentro de um banco. Bancos distintos com os mesmos IDs de equipamento podem reservar ambos; falta um mecanismo de autoridade/fencing global para hardware compartilhado. |
| Falha da auditoria após commit | O ensaio injetado retorna HTTP 500 após uma reserva já durável. Repetir o mesmo ID recupera o recibo, sem duplicar a reserva. Consumidores devem tratar resposta perdida/500 como resultado incerto e recuperar por ID. |
| Metadados visuais do mapa-múndi | O título e a atribuição de `world_v1.json`, nas cópias `console/geo` e `geography/geo`, contêm `U+00C2` extra antes de `·`/`©`. O screenshot Windows reproduz `Â·`; a leitura UTF-8 explícita confirma que o erro já está nos dados. A correção visual permanece aberta; coordenadas e rotas não foram alteradas nesta entrega. |
| Legibilidade em janelas menores | A revisão visual da captura de CIMATEC a 1280 × 720 identifica recorte parcial do topo do cabeçalho flutuante. A geometria do título está dentro do Stage, mas isso não prova ausência de oclusão pela barra superior. No HOUSEKEEPING a 640 × 480, épocas/vetores longos ficam elididos na coluna Valor. Os readbacks e eventos de pintura passaram; a leitura direta integral nessas dimensões permanece uma pendência visual. |
| Validação científica removida pela otimização | `PhysicsConfig` e `QuantumNoiseChannel` aceitam configurações inválidas em Python `-O`, enquanto a execução normal rejeita `T2 > 2*T1` e probabilidade de despolarização acima de 1. A compilação usa `optimize=1`, que remove os `assert` responsáveis. A reprodução compara construtores em Python normal/otimizado; não constitui teste direto desses construtores via API do EXE. A correção de validação explícita permanece aberta. |
| Alcance de um validador embarcado | Os 15 `assert` de `console/ui_pipeline_validation.py` são removidos no perfil compilado. Seu resultado nativo não comprova esses predicados. A campanha de 16 fluxos descrita acima usa gates explícitos, em modo fonte, e não sofre essa remoção. Nos 178 arquivos críticos de segurança, serviços, laboratório, engine, control plane e protocolos examinados não foram encontrados `assert` como controles. |
| Hardware e implantação | Fixtures e emulação não qualificam Swabian físico, FPGA/AXI/DMA real, óptica satelital ou produção institucional. Linux Qt em WSL/offscreen não substitui teste em desktop Linux físico ou outra máquina Windows. |
| Login local Linux | A confirmação da sessão atual do sistema operacional é portátil, e o acesso remoto utiliza mTLS. A tela Qt ainda apresenta textos de conta/sessão Windows; a verificação de senha continua específica do Windows. Não há adaptador PAM de senha Linux validado. Os diagnósticos de startup não qualificam esse caminho de senha. |
| Modelos originais | `F:/qnet_network.zip` está presente; os dois ZIPs esperados em Downloads, `modelo_qkd_ml.zip` e `Qnet-Network ML.zip`, estão ausentes. Os testes de contratos e o diagnóstico sem pacotes não qualificam a execução desses arquivos originais ausentes. |

[Reprodução da corrida](../../outputs/release-5.65.0-20261011/audit/legacy-pair-race.json)
e [reprodução de atomicidade/recuperação](../../outputs/release-5.65.0-20261011/audit/mixed-atomicity.json).
[Auditoria de codificação do mapa](../../outputs/release-5.65.0-20261011/windows/geo-metadata-encoding-audit.json).
[Auditoria de validações otimizadas](../../outputs/release-5.65.0-20261011/audit/optimized-assert-audit.json).

## Consolidação dos artefatos e demais testes

Os [32 JUnits suplementares finais](../../outputs/release-5.65.0-20261011/audit/final-junit)
registram **1.942 execuções/casos únicos: 1.926 aprovações e 16 skips**, sem
falhas nessa seleção final. Os skips têm causas explícitas de plataforma,
PTY e permissões de symlink; não são contados como aprovações. Os 70 módulos
de teste selecionados e as 677 entradas de produção permanecem vinculados
aos hashes examinados. As reproduções de defeitos são registradas à parte
e não se transformam em testes funcionais aprovados.

Dois testes de encerramento construíam o serviço com `object.__new__` sem
inicializar os atributos do controlador misto usados pelo construtor real.
Os fixtures receberam esses atributos, mantendo todas as assertions e o
código de produção. O conjunto inteiro passou **40/40**; o XML com as duas
falhas iniciais foi preservado em `audit/failed-attempts`.

[Auditoria suplementar](orionq_release_5_65_0_audit_20261011.md)
e [manifesto final](../../outputs/release-5.65.0-20261011/audit/final-audit.json).

## Windows: binário, pacote e execução após cópia

Os 26 corpora oficiais registram **4.446 execuções: 4.423 PASS e 23 SKIP**;
por identidade `classname + name`, são **4.369 casos: 4.346 PASS e 23 SKIP**.
Com os 32 corpora suplementares, a união é **6.388 execuções**, ou
**6.311 casos únicos: 6.272 PASS e 39 SKIP**, sem falhas/erros. As 77
repetições são internas aos conjuntos oficiais. São ensaios de fontes;
os gates do executável constituem uma camada distinta.

A interrupção da sessão preservou 19 JUnits oficiais já concluídos e
aprovados. Os sete corpora restantes foram executados novamente em fixtures
privadas novas, seguidos de todos os gates nativos do script oficial. O
marcador parcial `F` do log interrompido não tinha XML/traceback concluído,
foi preservado e não é contabilizado como resultado aprovado nem como causa
de um defeito confirmado. Não se descreve este build como corrida contínua.

| Gate do EXE | Resultado / fronteira |
|---|---|
| Logo, ícone, versão e instância única | Aprovado; metadados 5.65.0 e comportamento Windows nativo. |
| Roadmap e diagnóstico de modelos opcionais | Aprovado no escopo computacional/diagnóstico sem os ZIPs ausentes. |
| Workers e campanha temporal | Aprovado; execução em processos, leitura de histórico e associação de recibos. |
| Self-test e laboratório emulado | Aprovado; não qualifica bancada física. |
| Satélite/OGS e recepção clássica de missão | Aprovado com dados/fixtures identificados; não qualifica aquisição RF ou óptica real. |
| Rede global simulada | Aprovado no escopo de tentativas, protocolos, perdas e impedimentos; zero bits secretos certificados. |
| Stress do controlador | 20 rounds aprovados no build e na aceitação nativa. |
| Inicialização Qt ordinária | Aprovada no host Windows atual; não substitui segunda máquina. |
| Integridade e ZIP | 4041 arquivos do manifesto; CRC e bytes do arquivo confrontados com o pacote. |
| Correspondência fonte ↔ EXE | 52 módulos e 25 recursos selecionados, versão PE e metadados conferidos. |
| EXE na pasta final | Aceitação automática repetida em estado privado novo, com self-test e stress de 20 rounds, hashes do executável preservados. |

A campanha global usa 76 nodes, 76 comunicadores e 136 enlaces, com semente
20261007. As 20 instituições originaram e receberam probes clássicos pelo
backend; esse resultado é distinto do estabelecimento de entrelaçamento
ou de uma chave QKD segura. Os impedimentos e cenários físicos constam da
matriz e dos recibos da campanha global, sem fabricar conectividade.

O startup Windows cobriu seis perfis: serviço a 100/125/150/200% e demonstração
a 100/150%. O primeiro snapshot após mostrar a janela ocorreu em 5,141–7,531 s.
O heartbeat p95 foi 32 ms no serviço e 63–78 ms na demonstração, com pico de
**1.609 ms** na demonstração a 150%. O gate admite gaps de até 3.000 ms; seu
PASS não comprova ausência de pausas perceptíveis. Esse pico não identifica,
sozinho, a causa nem a frequência de um travamento. A qualificação G05 e a
fluidez sob carga prolongada continuam pendentes.

O ZIP é portátil e contém a pasta `OrionQ/_internal`; não há instalador novo.
O pacote e a evidência pública foram copiados com bytes conferidos, mantendo
fora bancos, cofres, certificados operacionais e logs privados. A CA pública
do `certifi` é uma dependência distribuída, não credencial de operador.
A aceitação automática conserva `manual_review=pending` e
`independent_machine_verified=false`; isso não impede esta entrega técnica,
mas não constitui aprovação humana ou de implantação operacional.

[Consolidação nativa](../../outputs/release-5.65.0-20261011/windows/native-release-consolidation.json),
[resumo e proveniência](../../outputs/release-5.65.0-20261011/windows/native-delivery-summary.json)
e [aceitação após cópia](../../release/windows/5.65.0-native-20261011-080148-release/relocation-acceptance/acceptance-report.json).

## Linux: GUI e controlador sem interface gráfica

Foram entregues dois runtimes ELF `onedir`, acompanhados das dependências
Python e bibliotecas empacotadas. O pacote GUI utiliza Qt e ML por CPU; o
headless oferece `orion-core`, `orion-operator` e `orion-pl-gateway`, sem Qt ou
os pacotes ML. Ambos são x86_64. O wheel Python da mesma versão também foi
conferido. Não são imagens de FPGA, BSPs PetaLinux ou executáveis ARM.

| Ensaio Linux | Resultado e alcance |
|---|---|
| Pytest em fontes e integração local | 1.247 execuções; 1.190 casos únicos finais aprovados, zero falhas/skips finais. Os dois skips iniciais foram resolvidos em rerun opcional com SeQUeNCe fixado e Icarus; a tentativa inicial permanece registrada. |
| Dashboard terminal ELF, PTY real | 185 verificações, 1.273 ações de teclado; resize, navegação, scroll, falha/retorno da API e restauração do terminal. |
| Terminal ELF E.V.A. | 77 verificações aprovadas; 15 controles de flags/charset exclusivos do interpretador Python são N/A no ELF, não aprovações. |
| Wheel 5.65.0 E.V.A. e ASCII | 92 verificações aprovadas; os 449 módulos Python conferidos coincidem com o wheel distribuído. O rebuild de teste tem hash de arquivo distinto, explicitamente declarado. |
| Self-test GUI congelada | 39/39 verificações. |
| Startup offscreen | 15/15 casos. |
| GUI Wayland/WSLg | 15/15 casos; monitor 3440 × 1440, DPR 1, fechamento programático e exit 0. |
| Startup padrão, sem override Qt | 15/15 casos; Wayland escolhido automaticamente, confirmação normal da sessão do SO e exit 0. |
| GUI ↔ core independente | TLS 1.3/mTLS, 124 leituras autenticadas, ticks 2→14→15. A GUI remota foi encerrada por SIGTERM, que não prova um fechamento interativo. |
| Recuperação dos tar.gz | 3.732 arquivos GUI e 673 headless recuperados com todos os bytes conferidos; CRC gzip e 4.394/839 modos de entradas verificados. |
| Correspondência com produção | 677 fontes preservadas; comparação estática dos 52 módulos e 25 recursos selecionados da GUI congelada. |

No terminal padrão, a latência de interação medida foi p50 **54,798 ms**,
p95 **301,116 ms** e máximo **831,656 ms** (155 amostras). O render foi
p95 **2,561 ms** e máximo **4,594 ms** (57 amostras). No E.V.A. ELF, a
interação apresentou p95 **141,186 ms**, máximo **158,958 ms**; no wheel
atual, p95 **143,618 ms**, máximo **220,243 ms**. São medições desta carga
de teste, sem equivalência automática a todas as cargas científicas.

O probe de backpressure rejeitou 100 requisições excedentes, conservando uma
leitura em andamento, um slot de requisição/publicação, até 128 registros de
observação e dois snapshots. A revogação do operador retornou HTTP 401; outro
operador recebeu tick 21 após o tick 20, com o mesmo PID do serviço. A saída
do cliente e o gatilho visual E.V.A. não reiniciaram o core nem alteraram seus
papéis. A identificação simulada permaneceu explícita e a atuação bloqueada.

Os heartbeats p95 foram **23,150 ms** no Wayland e **25,401 ms** no startup
padrão; os máximos foram **283,761/526,378 ms**. Não houve teste de arrasto
manual do mouse, de desktop Linux independente ou de estabilidade por dias.
As nove bibliotecas X11/xcb ausentes impedem qualificar o perfil X11 neste
ambiente; o Wayland/WSLg passou. A síntese auxiliar Yosys excedeu 180 s e
não é contada como aprovada. Estes resultados não qualificam PL/FPGA física.

[Resumo Linux](../../outputs/release-5.65.0-20261011/linux/linux-delivery-summary.json),
[qualificação dos arquivos e runtimes](../../outputs/release-5.65.0-20261011/linux/linux-release-qualification.json),
[casos Pytest](../../outputs/release-5.65.0-20261011/linux/source-tests-summary.json)
e [E.V.A. fresco do wheel](../../outputs/release-5.65.0-20261011/linux/wheel-eva/report.json).


## Artefatos da versão 5.65.0

| Entrega | Arquivo |
|---|---|
| Windows x64, GUI portátil | [ZIP](../../release/windows/5.65.0-native-20261011-080148-release/OrionQ-5.65.0-native-windows-x64.zip) |
| Windows, executável com dependências adjacentes | [OrionQ.exe](../../release/windows/5.65.0-native-20261011-080148-release/OrionQ/OrionQ.exe) |
| Linux x86_64, GUI | [tar.gz](../../release/linux/5.65.0-native-gui-20261011/OrionQ-5.65.0-linux-x86_64-gui.tar.gz) |
| Linux x86_64, core/terminal/PL gateway | [tar.gz](../../release/linux/5.65.0-native-headless-20261011/OrionQ-5.65.0-linux-x86_64-headless.tar.gz) |
| Fontes com Git independente | [ZIP](../../release/source/5.65.0/OrionQ-5.65.0-github.zip) |
| Histórico independente para clone | [bundle](../../release/source/5.65.0/OrionQ-5.65.0-github.bundle) |

SHA-256 do ZIP Windows: `e6d11bad8d729d6221fefa216a2fdd25c32b470ac7fb2f7d1244571c4874b161`.
SHA-256 do EXE Windows: `8dbe8efeb3321a55f9afd3e5a76dd965f58a216095a4ea0219128aca03edaf09`.
SHA-256 do tar.gz GUI Linux: `6d7faf65d6860e2b1634fdbd8d5246acf64ff2addcad02daafdb7f7e54feadac`.
SHA-256 do tar.gz headless Linux: `f18d77ee534e3f1f5c0acd4fdb81502e736b5bb638bfd9ab2351b7345e3dfc04`.
Os hashes detectam mudanças/corrupção; não são assinatura de autoria.

A cópia Git utiliza a branch `codex/release-5.65.0`, um commit independente e
nenhum remote. O `.git` original e todas as alterações do checkout são
preservados. Binários, credenciais, bancos, caches e estado de operadores
ficam fora da seleção Git. Os relatórios históricos mantêm suas datas e
limites; não são reclassificados como execuções novas da versão atual.
As verificações de fontes/ZIP/bundle/clone e os hashes finais estão no
[recibo externo](../../release/source/5.65.0/git-delivery.json) e
[SHA256SUMS](../../release/source/5.65.0/SHA256SUMS.txt), produzidos após o
selo. Seus hashes/commit não são incorporados ao próprio relatório para
evitar uma referência circular. Não houve upload ou push ao GitHub.
A procedência/licença da fonte Quantum e de material Qnet vendorizado
continua exigindo revisão antes de publicação externa.

## Execução e reprodução

No Windows, extrair o ZIP inteiro e manter `_internal` junto ao EXE:

```powershell
.\OrionQ\OrionQ.exe
.\OrionQ\OrionQ.exe --self-test C:\temp\orionq-565-check\report.json --state-root C:\temp\orionq-565-check\state
```

Usar diretórios novos nos diagnósticos, preservando o estado operacional.
No Linux, extrair o tar correspondente em filesystem Linux:

```bash
tar -xzf OrionQ-5.65.0-linux-x86_64-gui.tar.gz
./OrionQ/OrionQ --state-root "$HOME/.local/state/orionq"
tar -xzf OrionQ-5.65.0-linux-x86_64-headless.tar.gz
./OrionQ-headless/orion-core --help
./OrionQ-headless/orion-operator --help
./OrionQ-headless/orion-pl-gateway --help
```

Provisionar configurações, identidades, permissões, cofre e certificados
conforme [guia Linux](../product/orionq_linux_service.md) e
[operação pelo terminal](../product/orionq_terminal_operator.md). Os exemplos
antigos de versões 5.61/5.62 no guia Linux não identificam os artefatos atuais.
No ELF headless, `repository_root` aponta para o diretório público extraído
`OrionQ-headless/_internal`. Após provisionamento administrativo separado:

```bash
./OrionQ-headless/orion-core --config /etc/orionq/core.json check
./OrionQ-headless/orion-core --config /etc/orionq/core.json serve
./OrionQ-headless/orion-operator \
  --url https://orion-core:8765 --ca /run/operator/ca.pem \
  --cert /run/operator/operator.pem --key /run/operator/operator.key \
  watch --layout dashboard --interval-s 1 --view overview
```

O serviço inicia com fonte `none` até configuração autorizada; `watch` lê o
backend, e `q` encerra somente o cliente. A conta/senha do modo E.V.A. não
substitui autenticação do serviço nem concede hardware. O template systemd
entregue espera wheel/Python: adaptar administrativamente `ExecStartPre` e
`ExecStart` para o ELF e as referências de segredos (`/run/credentials` versus
`/run/secrets`) antes de instalar. Foi validado pelo parser, não em boot.
Ver [serviço em background](../product/orionq_background_service.md) e
[contrato PS/PL](../architecture/orionq_pspl_contract.md).

Para recuperar a cópia Git local:

```bash
git -c core.autocrlf=false -c core.longpaths=true clone OrionQ-5.65.0-github.bundle OrionQ-5.65.0
```

A compilação Windows é reproduzida por `tools/build_windows_console.ps1`,
com Python qualificado, `-SkipInstaller`, `-SkipDependencyInstall` e
`-ReleaseBaseDirectory` novo no SSD. Em Linux, usar Python não otimizado,
usuário sem privilégios e `tools/build_linux_native.py --repository "$PWD"
--wheel "$PWD/release/linux/5.65.0/quantum_twin-5.65.0-py3-none-any.whl"
--expected-version 5.65.0 --profile gui` (ou `headless`) e `--output` novo sob
`release/linux/5.65.0-rebuild-*`. Os relatórios registram dependências
resolvidas e hashes; as faixas não constituem lock nem garantem rebuild
binário idêntico. Não usar resultados antigos para aprovar outro binário.

## Resultado da varredura

As compilações e os fluxos selecionados passaram, com perdas, recusas e
falhas esperadas verificadas em emulação. **Não se afirma funcionamento
integral sem ressalvas**: concorrência do engine legado, fencing entre
bancos, validações científicas otimizadas, legibilidade, codificação GEO e
login Linux continuam documentados acima. O gate G05 de desempenho da rede
completa e a qualificação experimental permanecem abertos; esta entrega não
demonstra TRL 5, missão satelital operacional, interlocks físicos nem geração
QKD segura em hardware.
