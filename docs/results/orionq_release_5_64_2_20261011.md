# OrionQ 5.64.2 — entrega Windows, Linux e Git

Data: 11 de outubro de 2026. Escopo: compilar a versão atual, executar as verificações de software dos pacotes e preparar uma cópia Git independente. O checkout, seu histórico, configurações de operador e entregas anteriores são preservados. Esta entrega não qualifica equipamento físico nem encerra os impedimentos de TRL 5.

## Artefatos

| Perfil | Artefato | Situação |
|---|---|---|
| Windows x64, interface Qt | [OrionQ-5.64.2-native-windows-x64.zip](../../release/windows/5.64.2-native-20261011-004709-release/OrionQ-5.64.2-native-windows-x64.zip) | Compilado; consolidação e aceitação automática da cópia entregue aprovadas |
| Linux x86_64, interface Qt | [OrionQ-5.64.2-linux-x86_64-gui.tar.gz](../../release/linux/5.64.2-native-gui-bootstrap-release/OrionQ-5.64.2-linux-x86_64-gui.tar.gz) | Recompilado após a correção do bootstrap e aprovado no WSL/Qt offscreen |
| Linux x86_64, core, terminal e gateway PS/PL | [OrionQ-5.64.2-linux-x86_64-headless.tar.gz](../../release/linux/5.64.2-native-headless-release/OrionQ-5.64.2-linux-x86_64-headless.tar.gz) | Compilado; campanha nativa e manifesto integral aprovados |
| Fontes Git independentes | [ZIP](../../release/source/5.64.2/OrionQ-5.64.2-github.zip) e [bundle](../../release/source/5.64.2/OrionQ-5.64.2-github.bundle) | Snapshot independente da versão; verificação de selagem/clone no recibo externo |

Os pacotes Linux contêm executáveis ELF e seu runtime, sem exigir a instalação de Python. O pacote Qt utiliza Torch CPU; não se declara qualificação de CUDA. O pacote headless exclui Qt, Torch e scikit-learn. As implementações e os modelos opcionais do projeto foram preservados.

## Execução

No Windows, extraia o ZIP inteiro e execute `OrionQ/OrionQ.exe`. Mantenha `_internal` junto do executável. Esta é uma entrega portátil; não foi produzido instalador Inno Setup.

No Linux x86_64/glibc, extraia os arquivos em um filesystem Linux e execute:

```bash
tar -xzf OrionQ-5.64.2-linux-x86_64-gui.tar.gz
./OrionQ/OrionQ

tar -xzf OrionQ-5.64.2-linux-x86_64-headless.tar.gz
./OrionQ-headless/orion-core --help
./OrionQ-headless/orion-operator --help
./OrionQ-headless/orion-pl-gateway --help
```

Os comandos acima confirmam a instalação; a ativação institucional exige provisionar identidade, política, certificados e referências de segredos conforme os [procedimentos do core](../product/orionq_linux_service.md). O pacote não inclui chaves privadas, credenciais ou bancos do operador. Consulte o [serviço independente de login](../product/orionq_background_service.md), a [interface de terminal](../product/orionq_terminal_operator.md) e o [contrato PS/PL](../architecture/orionq_pspl_contract.md). A unidade `orionq-core.service` incluída é um template para instalação por wheel/venv; revise seu `ExecStart` para usar o ELF nativo, sem ativação automática.

## Evidências Linux

Ubuntu 24.04, WSL2, x86_64, glibc 2.39, Python 3.12.3 e PyInstaller 6.22.3. Processos de validação executados como UID 10001 em fixtures novas no filesystem Linux. Os ambientes e as versões anteriores foram preservados.

- Interface Qt compilada: **39/39 checks do self-test** e **15/15 checks de startup**, em `QT_QPA_PLATFORM=offscreen`. A identidade atual da sessão Linux autentica a operação local; a validação de senha Windows não é equivalente a PAM institucional.
- Interface Qt remota compilada: comunicação efetiva com o core compilado independente por TLS 1.3/mTLS, **124 leituras auditadas `network.read` em 12 s** e ticks **1 → 13 → 14**. O cenário durou 17,748 s. O encerramento dessa fixture gráfica foi explícito por SIGTERM; este cenário não comprova clique no botão de fechar. A inicialização local publicou 22 snapshots, com primeiro frame em 4,65 s e heartbeat máximo de 325,4 ms.
- Core/terminal compilados: campanha curses/PTY com **155/155 verificações**, 1.197 teclas, reconexão, resize, scroll, saída e atributos de cor da logo. A campanha foi repetida com fixture nova no bundle final, incluindo gateway e hashes antes/depois: **670 arquivos idênticos**, iguais ao runtime público e ao manifesto intermediário. Esta prova não reutiliza aprovação baseada somente no hash ELF.
- Arquivo headless extraído novamente em diretório privado: **670/670 arquivos** com SHA-256 conferido, permissões de execução preservadas e três comandos `--help` aprovados. **332/332 fontes** do perfil headless correspondem ao wheel corrente.
- Interface Qt: comparação dos **52 módulos e 25 recursos** exigidos com o source atual, incluindo o módulo `console.service_provider` corrigido. Seu SHA-256 é `28c0f129c6209140359aca6fb0d99e8499973ad43302b9c83e946738aac4b96c`. Checks PE/nome `.exe` da ferramenta Windows são inaplicáveis no ELF; a comparação Linux os identifica separadamente.
- Arquivo Qt extraído novamente: **3.732/3.732 arquivos** conferidos, 40 symlinks com destinos internos e entrada executável `0755`; `--version` e `--help` aprovados. A instalação usada pelo builder foi vinculada ao wheel por **363/363 fontes idênticas**. A ferramenta recusa execução com `-O`/`PYTHONOPTIMIZE` antes de criar diretórios (**4/4 checks**); reutilizar evidências exige versão, perfil e manifesto integral do runtime.
- Snapshot GUI: **691/692 arquivos** coincidem com o checkout após a geração de metadados Windows. O único delta é a lista auxiliar `egg-info/SOURCES.txt`, que ganhou a linha do novo teste de bootstrap; código, recursos, `PKG-INFO` e dependências permanecem exatos. O [binding](../../release/linux/5.64.2-native-gui-bootstrap-release/evidence/gui-bootstrap-source-binding.json) registra ambos os hashes e o diff. O scanner dos 692 inputs não encontrou assinaturas de credenciais, symlinks ou estado operacional nesse escopo limitado; não representa auditoria completa de segredos.

Os relatórios estão em [headless/evidence](../../release/linux/5.64.2-native-headless-release/evidence/) e [gui/evidence](../../release/linux/5.64.2-native-gui-bootstrap-release/evidence/). A [qualificação gráfica final](../../release/linux/5.64.2-native-gui-bootstrap-release/evidence/gui-bootstrap-qualification.json) e o reproduzidor `work/validate_linux_gui_native_bootstrap.py` vinculam código, tar, extração e execução remota. Tentativas anteriores de harness e o pacote gráfico anterior à correção permanecem identificados; não são contabilizados como aprovações finais.

SHA-256 GUI: `45c24b2fa2d09bb9df54ab0fcc04ac04595b5e83421d0d9491b2337229670352` (414.188.510 bytes). SHA-256 headless: `8a5410e668e761ff7ea5b1908e0276bb242616fc9973c85a5bd656f566ba47b3` (141.916.110 bytes). O novo build gráfico durou 770,36 s; a qualificação adicional, 199,32 s. O wheel e os 670 arquivos headless foram preservados e conferidos novamente, pois o wheel exclui o módulo gráfico modificado.

## Evidências Windows e armazenamento

Host: Windows 11 Pro x64, Ryzen 7 5700X (8 cores/16 threads). O checkout fica no HDD SATA `E:`; a validação final utiliza armazenamento temporário no SSD NVMe `C:`. A opção nova `-ReleaseBaseDirectory` em `tools/build_windows_console.ps1` permite escolher esse volume sem mudar os testes, seus prazos, os critérios científicos ou a durabilidade do SQLite.

A primeira tentativa no HDD apresentou duas falhas nos testes de laboratório. A reprodução multiprocessos registrou `database is locked`/`BrokenPipeError`; o teste original aprovado no SSD levou 2,42 s. Na tentativa anterior à correção de bootstrap, o conjunto de laboratório no SSD passou com **352 aprovações e 19 skips** e a verificação suplementar com **548 aprovações e 35 skips**. Essas aprovações são históricas. O diagnóstico mantém explícita a falha de disponibilidade no HDD: não é uma correção do núcleo nem garantia para toda classe de disco.

A segunda tentativa revelou uma falha diferente na interface: a primeira leitura do controlador e o primeiro tick inicializavam schemas SQLite simultaneamente. Uma leitura da topologia levantava `OperationalError: database is locked`; o fallback genérico ocultava o erro e publicava um frame sem identidade/versão da topologia. O manifesto era correto e o guard de versão o recusava. O [probe instrumentado](../../outputs/native_release_5_64_2_20261011/windows/campaign-topology-diagnostic.json) preservou os resultados e prazos originais: 9 casos aprovados, 1 falha reproduzida.

A correção inicia os ticks somente após a primeira leitura completa, seu registro de auditoria e a admissão do frame pela interface. Uma falha na leitura da topologia passa pelo erro/retry existente e preserva o último frame válido. O guard de versão e a durabilidade permanecem intactos; o início também verifica parada/revogação reentrante após os listeners. Os candidatos anteriores e seus testes são históricos, sem reutilizar suas aprovações para a interface corrigida.

As cinco novas regressões de bootstrap e os dez casos originais passaram: **15/15 em 17,52 s**, no [JUnit da reprodução corrigida](../../outputs/native_release_5_64_2_20261011/windows/bootstrap-regressions.xml). Elas cobrem erro transiente sem publicação parcial, ausência de ticks antes da admissão, conclusão atrasada após parada, parada reentrante pelo listener e revogação durante a leitura. O script de build passa a incluir essas regressões no corpus obrigatório.

O corpus de rede ativa também revelou um teste que exigia `stepping` imediatamente após `start()`, incompatível com o bootstrap assíncrono. A [reprodução do teste original](../../outputs/native_release_5_64_2_20261011/windows/live-lifecycle-original.xml) falhou nessa única assertiva em 4,82 s. Na campanha, a falta de limpeza após essa falha deixou duas threads de testes ativas e contaminou verificações posteriores. O resumo de console indicou 566 `passed`, quatro falhas e três erros de teardown; o JUnit classifica **563 casos aprovados, quatro falhas e três erros**, pois os três casos com erro no teardown haviam passado no corpo do teste. A adaptação do teste aguarda a emissão do primeiro frame e o stepping com o prazo original de 30 s, preserva as verificações de thread única/daemon, ticks, parada e reinício, e garante `stop()` em `finally`. A produção não é alterada por essa adaptação. Os dois testes específicos passaram em 15,29 s e a repetição integral da rede ativa passou com **570/570 casos em 446,87 s**.

A continuação usa `work/resume_windows_native_release.py`: preserva os 20 JUnits aprovados com o mesmo código de produção, repete integralmente o corpus 21 em basetemp novo e executa os corpora 22–26 e os gates nativos pelo tail literal do script oficial. A única substituição nesse tail é a pasta temporária nova. O [registro de continuação](../../outputs/native_release_5_64_2_20261011/windows/bootstrap-continuation/provenance.json) vincula script, provider, 641 inputs de produção e relatórios, sem atribuir à tentativa anterior uma aprovação integral. O JUnit falho estava completo antes da intervenção autorizada na árvore de processos durante a finalização; a [resolução](../../outputs/native_release_5_64_2_20261011/windows/bootstrap-continuation/failed-attempt-resolution.json) conserva tanto os arquivos falhos como o recibo inicial conservador de interrupção.

Os **26 corpora obrigatórios foram aprovados: 4.423 execuções aprovadas, 23 skips, zero falhas e zero erros**. São execuções agregadas dos corpora, que incluem alguns testes repetidos; não representam 4.423 testes distintos. O corpus de comandos teve 652 aprovações, o opcional 367 aprovações/3 skips, o de sincronização 87 e o de compensação 580. Os skips não são contabilizados como aprovação.

O executável corrigido foi compilado em 429,6 s. Passaram os gates nativos de branding (16 casos/7 checks), comportamento de desktop (8 casos), roadmap (4 checks), histórico temporal (25 checks), laboratório (12), pipeline satelital (21), missão clássica (8) e rede global (33). O self-test passou em **39/39 checks**, com 8 checks de atualização e 16 de continuidade das ondas; o estresse de 20 rodadas solicitadas também foi aprovado, sem atuação física. A inicialização passou nas seis configurações Qt: serviço em escalas 100/125/150/200 e demonstração em 100/150. O heartbeat p95 foi de 32 ms; o maior intervalo observado foi de 735 ms na demonstração, sem equivaler a garantia de latência sob qualquer carga.

O ZIP contém **4.042 arquivos**, tem **310.819.840 bytes**, SHA-256 `68741dfb7d078e10d4ac0dd4e46819e2920239607a919ad372f2e3adf63bf35e` e CRC verificado. O SHA-256 do executável é `101356f238fa56c50711f01dcc2cc6ba39e5a3e849a2ee9834b4887769483243`. A aceitação automática no SSD passou em 360,63 s, com integridade, self-test e estresse aprovados. Seu escopo é `local_software_checks_only`; revisão humana e verificação em máquina independente permanecem pendentes.

A suíte suplementar fresca passou com **551 execuções aprovadas e 35 skips em 278,12 s**, sem falhas ou erros, em [supplementary-bootstrap/tests.xml](../../outputs/native_release_5_64_2_20261011/windows/supplementary-bootstrap/tests.xml). Ela não reutiliza a aprovação histórica de 548 casos. A [comparação source/binário](../../outputs/native_release_5_64_2_20261011/windows/frozen-source.json) foi aprovada para **52 módulos e 25 recursos**, incluindo o provider corrigido.

A [consolidação final](../../outputs/native_release_5_64_2_20261011/windows/native-release-consolidation.json) foi aprovada. Nos 27 JUnits selecionados, são **5.032 execuções: 4.974 aprovadas e 58 skips**, sem falhas ou erros. O resolver identifica **4.825 casos únicos**, cujo resultado mais recente é **4.786 aprovados e 39 skips**; a diferença decorre das repetições entre corpora. A [contagem selecionada](../../outputs/native_release_5_64_2_20261011/windows/native-release-selected-test-summary.json) vincula essas distinções e o hash da consolidação.

A [publicação por cópia verificada](../../release/windows/5.64.2-native-20261011-004709-release/delivery-copy.json) passou: **4.135 arquivos públicos** copiados, sem bancos de operador ou segredos de fixture. O executável publicado em `E:` passou por nova aceitação com estado novo e privado em `C:`: **39 checks de self-test e 535 checks de estresse**, com 20 rodadas solicitadas, em **344,81 s** (345,36 s incluindo o wrapper). Integridade, self-test e estresse foram aprovados. Somente os três JSONs de aceitação/self-test/estresse foram copiados depois; seus hashes, tamanhos e caminhos originais são preservados na [prova de relocação](../../release/windows/5.64.2-native-20261011-004709-release/relocation-acceptance/delivery-relocation-proof.json). O executável entregue tem **59.074.367 bytes**, e os hashes do EXE e do ZIP permanecem os mesmos do build canônico.

O [resumo definitivo Windows](../../outputs/native_release_5_64_2_20261011/windows/native-delivery-summary.json) reúne pacote, gates e os limites da aceitação automática. As evidências e logs das tentativas permanecem em [outputs/native_release_5_64_2_20261011/windows](../../outputs/native_release_5_64_2_20261011/windows/).

## Repositório Git

A entrega Git é um snapshot independente do estado atual, na branch `codex/release-5.64.2`, sem remote configurado e sem incorporar o histórico do checkout original. Inclui fontes, documentação e evidências públicas selecionadas; os executáveis, runtimes, ZIPs/tars de produto, bancos operacionais e segredos ficam fora dessa cópia. Os pacotes nativos são entregues separadamente; seus links neste relatório apontam para o workspace de entrega, não para binários incorporados ao Git.

O mapa `release-evidence-map.json` distingue arquivos canônicos gerados no SSD dos recibos e verificações produzidos na cópia de entrega. Os JSONs canônicos mantêm seus caminhos originais. Self-tests grandes recebem uma projeção dos checks, identificada com os hashes da entrada e da projeção. As evidências sob `/release/**` preservam os bytes; eventuais normalizações de texto das fontes pelo Git são contabilizadas separadamente.

Os resultados da selagem, recuperação por clone, conteúdo dos artefatos e preservação dos metadados selecionados do `.git` original são registrados em [git-delivery.json](../../release/source/5.64.2/git-delivery.json). Os hashes externos ficam em [SHA256SUMS.txt](../../release/source/5.64.2/SHA256SUMS.txt), evitando autorreferência no snapshot. A cópia inclui `SOURCE_RELEASE.md` e `GIT-RELEASE.md` com instruções e as pendências de licença/proveniência para publicação externa.

Para recuperar o repositório pelo bundle:

```bash
git -c core.autocrlf=false -c core.longpaths=true clone OrionQ-5.64.2-github.bundle OrionQ-5.64.2
```

As opções se aplicam a essa operação, sem alterar a configuração Git global. Nenhum upload ou push é realizado por esta entrega.

## Limitações mantidas

- A validação Linux gráfica usa Qt offscreen/WSL; falta a execução em desktop Linux físico e outra máquina Windows. Não há imagem ARM/PetaLinux compilada, boot em placa ou aquisição física AXI/DMA nesta entrega. Consulte o [perfil embarcado](../product/orionq_petalinux.md).
- Os campos/textos de senha do boot Qt ainda descrevem Windows. A sessão Linux atual foi validada, mas autenticação por senha Windows não é oferecida como login Linux/PAM; essa adaptação visual permanece pendente.
- Permanecem os riscos documentados no [ensaio de contenção do controlador](orionq_controller_contention_20261011.md): corrida entre instâncias legadas do engine ao consumir o mesmo par, ausência de arbitragem global de hardware entre bancos distintos e limites de admissão/transporte sob concorrência. Compilar não resolve esses problemas.
- Modelos e campanhas são validação computacional condicional. Zero atuação física e zero bits secretos certificados. Hardware Swabian, FPGA e enlaces físicos não foram qualificados.
- As evidências automáticas distinguem software aprovado de revisão humana/aceitação operacional. Integridade por hashes não autentica autoria. Fontes vendorizadas e fonte tipográfica conservam suas notas; a publicação externa requer resolver a proveniência/licença documental pendente.

## Reproduzir o build

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools\build_windows_console.ps1 `
  -Python .audit-winvenv\Scripts\python.exe -SkipInstaller -SkipDependencyInstall `
  -ReleaseBaseDirectory "$env:TEMP\OrionQ-native-release"
```

O script Windows executa seus 26 corpora obrigatórios, congela a aplicação, testa o próprio executável, empacota e executa a aceitação automática. Ele recusa a entrega diante de falha. A opção que dispensa instalação de dependências pressupõe o ambiente provisionado; as versões observadas estão nos relatórios.

```bash
python tools/build_linux_native.py --profile headless \
  --repository "$PWD" --wheel release/linux/5.64.2/quantum_twin-5.64.2-py3-none-any.whl \
  --output "$PWD/release/linux/5.64.2-native-headless-rebuild-new"
python tools/build_linux_native.py --profile gui \
  --repository "$PWD" --wheel release/linux/5.64.2/quantum_twin-5.64.2-py3-none-any.whl \
  --output "$PWD/release/linux/5.64.2-native-gui-rebuild-new"
```

Use um Python Linux isolado com o wheel corrente e as dependências core. O builder cria ambientes privados novos; dependências resolvidas, manifestos e hashes são registrados. Os exemplos de configuração são templates, não credenciais prontas.
