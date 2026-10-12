# OrionQ em Linux embarcado, Yocto e PetaLinux

**Perfil preparado:** núcleo e gateway em processos Linux sem GUI, sem runtime de ML e sem login humano para manter o processamento. **Alvo da receita:** Yocto Scarthgap/AMD PetaLinux 2025.2. **Pesquisa:** 2026-10-11. A preparação de fontes, metadados, wheel no host, configurações e unidades pode ser verificada localmente. Não existe nesta entrega evidência de imagem ARM/PetaLinux compilada, boot em placa ou aquisição física por AXI DMA.

## O que já existe e o que esta camada acrescenta

O [núcleo portátil](orionq_linux_service.md) e seu [serviço autônomo](orionq_background_service.md) mantêm autenticação, RBAC, mTLS, cofre externo, estado durável, renovação da identidade própria e encerramento conservador. O operador identifica e autoriza suas ações; seu login ou logout não representa a alimentação do núcleo. O novo perfil não altera essa fronteira de segurança.

O [contrato PS/PL](../architecture/orionq_pspl_contract.md) já possui timestamps inteiros em picosegundos, sessões, sequências, clocks, qualidade/perdas, limites de buffers e comparação de correlação. O gateway externo oferece os modos `cpu-external` e `emulated-pl-external`; ambos executam software em CPU. `linux-pl-driver` exige composição confiável de um driver específico e permanece rejeitado pela CLI padrão. Um resultado de software não passa a ser medição de FPGA por ser transportado num socket.

Esta camada acrescenta:

| Componente | Implementação | O que ainda precisa ser demonstrado |
|---|---|---|
| Staging | `packaging/petalinux/prepare_layer.py` reutiliza o preparador headless atual, adiciona licença e a lista pública de recursos; gera tar determinístico, hashes, versão e dependências | Construção cruzada com os provedores escolhidos e BSP fixado |
| Receita core | `meta-orionq/recipes-orionq/orionq-core/orionq-core.bb` usa `python_setuptools_build_meta`, o mesmo `pyproject.toml` preparado e a distribuição `quantum-twin` | Compatibilidade de toda a cadeia Python/C/C++/Rust/Fortran com a CPU, libc e sysroot alvo |
| Receita gateway | Pacote separado instala a unidade de supervisão; seus módulos vêm do pacote core | Perfil mais leve do gateway, se medição justificar separar seu envelope de bibliotecas |
| Pré-check | Versões observadas, arquitetura, systemd, orçamento de buffers; hashes de BSP/exportação/bitstream/kernel e existência de driver no perfil físico | Readback confiável do dispositivo, IRQ, DMA/coerência/cache, reset e medição na unidade |
| Contas e processos | Serviço `orionq` sem shell/home, core HTTPS e gateway AF_UNIX em processos separados | Isolamento por UIDs diferentes, quando o transporte admitir política de grupo/capabilities |
| Imagem e atualização | Camada e entradas versionadas para o integrador | Rootfs, SBOM, assinatura/atualização, reboot/rollback e qualificação em placa |

A fonte selecionada é headless: a GUI e o launcher desktop são excluídos, enquanto dados de geografia e contratos científicos permanecem disponíveis. Código opcional pode continuar no artefato, mas PySide6, Torch e scikit-learn não são dependências desse envelope. Os mecanismos de segurança permanecem no núcleo; não dependem de DPAPI ou da sessão Windows.

## Decisão de empacotamento

O layer é preparado a partir das **fontes do perfil core**, que a classe Yocto constrói como wheel no sysroot adequado. Não usa um wheel Windows/x86 com extensões nativas numa placa ARM. O teste de wheel no host confirma metadados e exclusões; não substitui a compilação cruzada. A classe adequada para `setuptools.build_meta` é `python_setuptools_build_meta`, que usa o fluxo PEP 517. [Yocto Scarthgap — classes Python](https://docs.yoctoproject.org/scarthgap/ref-manual/classes.html#python-setuptools-build-meta).

A receita mantém as dependências atuais em vez de reduzi-las silenciosamente. Os limites inferiores entram em `RDEPENDS`; todos os limites, inclusive superiores, são conferidos no preflight com os metadados instalados. A resolução de dependências, as ABIs nativas e os imports reais ainda precisam passar pelo build do rootfs e pelo `orion-core ... check` no alvo. Uma receita inexistente ou uma versão antiga é impedimento de implantação, não motivo para fingir que a biblioteca é opcional.

| Dependência atual | Provedor esperado | Implicação no ARM/footprint |
|---|---|---|
| Python ≥ 3.10 | `python3-modules`; alvo Scarthgap com Python 3.12 | A receita inicial inclui a stdlib completa; isso simplifica compatibilidade e não representa uma imagem mínima |
| cryptography ≥ 43, < 51 | `python3-cryptography` | Precisa de versão compatível; compilar a biblioteca requer C/Rust, OpenSSL/libffi e sysroot coerente |
| NumPy ≥ 1.26, < 3 | `python3-numpy` | Extensões nativas, Meson, BLAS e características da CPU; não copiar artefatos do host |
| SciPy ≥ 1.11, < 2 | `python3-scipy` | C/C++/Fortran, BLAS/LAPACK e configurações de cross-compilation; validar recursos e bibliotecas numéricas |
| pandas ≥ 2.1, < 4 | `python3-pandas` | Extensões nativas e dependência NumPy; memória e import time precisam ser medidos |
| NetworkX ≥ 3, < 4 | `python3-networkx` | Núcleo Python puro, porém a versão escolhida impõe seu próprio `Requires-Python`; não selecionar automaticamente a versão mais nova |
| Qiskit ≥ 1, < 3; Aer ≥ 0.14, < 1 | `python3-qiskit`, `python3-qiskit-aer` | Bibliotecas adicionais e backend nativo do Aer; receitas e CPU/libc precisam de validação própria |
| PyYAML ≥ 6, < 7 | `python3-pyyaml` | Receita/versão e eventual extensão devem corresponder ao alvo |
| SGP4 ≥ 2.25, < 3 | `python3-sgp4` | Propagador e sua implementação escolhida devem ser testados no alvo |
| websockets ≥ 15, < 18 | `python3-websockets` | Verificar versão e compatibilidade do Python no sysroot |

`core`, `openembedded-layer` e `meta-python` são dependências do layer. Outros layers/backports podem ser necessários; o mapeamento acima não fornece dez novas receitas nem garante que o BSP já as contenha. O índice OpenEmbedded mostrou cryptography 42.0.5 para Scarthgap, abaixo do requisito atual, e SciPy em `meta-python-ai`; fixe o commit e confira as receitas reais com `bitbake-layers show-recipes`. As páginas detalhadas do índice retornaram HTTP 403 durante a abertura, portanto esse levantamento por resultado indexado precisa ser confirmado na árvore de build. [Índice OpenEmbedded — cryptography](https://layers.openembedded.org/layerindex/recipe/422177/), [meta-python-ai/Scarthgap](https://layers.openembedded.org/layerindex/branch/scarthgap/layer/meta-python-ai/).

Rust é requisito de **construção** de cryptography, não uma mudança da linguagem do controlador nem prova de aceleração de sua malha. NumPy/SciPy possuem instruções específicas para cross-compilation e problemas de seleção de headers/runtime do alvo. Aer usa componentes nativos; seus modos GPU dependem de plataforma e build compatíveis, sem benefício automaticamente transferível à PL. [cryptography — instalação](https://cryptography.io/en/stable/installation/), [NumPy — cross-compilation](https://numpy.org/doc/stable/building/cross_compilation.html), [SciPy — cross-compilation](https://docs.scipy.org/doc/scipy/building/cross_compilation.html), [Aer — construção](https://qiskit.github.io/qiskit-aer/getting_started.html).

## Preparar e integrar a camada

Em uma cópia preservada do repositório, crie um destino novo:

```bash
python3 packaging/petalinux/prepare_layer.py \
  --repository "$PWD" --destination /tmp/meta-orionq-staged
```

O script recusa sobrescrever destinos. `orionq-staging.json` registra hashes de fontes e recursos, dependências, versão e `arm_image_built=false`. O tar fica em `recipes-orionq/orionq-core/files/`; `orionq-artifact.inc` fornece `PV`, checksum do tar, checksum da licença e a lista runtime. Nenhum administrador, chave privada, certificado, banco de estado ou credencial é provisionado pelo build.

Depois de revisar o manifest, transfira a camada ao projeto BSP. Pelo fluxo AMD, adicione-a em `petalinux-config → Yocto Settings → User Layers`; pelo fluxo Yocto, use `bitbake-layers add-layer /caminho/meta-orionq-staged`. Selecione systemd no distro e os pacotes `orionq-core` e, quando necessário, `orionq-pl-gateway` no rootfs. A classe `systemd` normalmente habilitaria serviços automaticamente; esta camada define `SYSTEMD_AUTO_ENABLE="disable"` nas duas receitas. [UG1144 2025.2 — Adding Layers](https://docs.amd.com/r/2025.2-English/ug1144-petalinux-tools-reference-guide/Adding-Layers), [Yocto — systemd](https://docs.yoctoproject.org/scarthgap/ref-manual/classes.html#systemd).

```bash
bitbake-layers show-layers
bitbake-layers show-recipes 'python3-*'
bitbake -e orionq-core > orionq-core-build-environment.txt
bitbake orionq-core orionq-pl-gateway
```

Execute estes comandos no ambiente de build de BSP/Yocto qualificado; não há evidência de que tenham sido executados para ARM nesta entrega. Registre `MACHINE`, tune/ABI, arquitetura, BSP, XSA ou SDT, kernel/device tree, toolchains, libc, BLAS, versões/commits de layers, hashes do source tar/bitstream e manifest final de pacotes. Ao usar PetaLinux, mantenha as ferramentas compatíveis com a plataforma de hardware. O UG1144 2025.2 documenta fluxos XSA e SDT; o release AMD correspondente identifica Scarthgap 5.0.12 e linux-xlnx 6.12.40. Isso é referência da edição, não promessa de compatibilidade com qualquer kernel. [UG1144 — Introduction](https://docs.amd.com/r/2025.2-English/ug1144-petalinux-tools-reference-guide/Introduction), [AMD — release Yocto](https://xilinx-wiki.atlassian.net/wiki/spaces/A/pages/2613018625/Yocto+Project+related+Release+Note).

## Configuração e permissões no alvo

O build instala código em site-packages, unidades e recursos públicos em `/usr/share/orionq`. Os exemplos em `packaging/petalinux/examples/` **não são instalados nem aceitos automaticamente como configuração operacional**.

| Caminho | Responsável/permissão | Função |
|---|---|---|
| `/etc/orionq/core.json`, `platform.json`, `pl-gateway.json` | root; sem escrita de grupo/outros | Referências de implantação e backend; nenhum segredo inline |
| `/etc/orionq/secrets/orion-service`, `orion-vault` | root, leitura mínima; fornecidos externamente | systemd `LoadCredential` cria cópias privadas de uso runtime |
| `/run/credentials/orionq-core.service/*` | namespace privado do serviço | Origem das credenciais indicada pelo JSON do core |
| `/var/lib/orionq` | conta `orionq`, 0700 | Cofre, journal e estado durável previamente provisionados |
| `/var/lib/orionq-pl` | conta `orionq`, 0700 | Ledgers duráveis de gerações e sessões consumidas do gateway |
| `/run/orionq-pl` | conta `orionq`, 0700 | Diretório temporário do gateway |
| `/run/orionq-pl/worker.sock` | conta `orionq`, 0600 | Transporte local autorizado pelo UID, com prazo e framing limitados |

Core e gateway usam **a mesma conta** na versão atual. Há separação de processos, interfaces e supervisão, mas não isolamento de segurança por UID entre eles. O socket autoriza o peer pelo UID Linux; não concede permissões de operador. As chamadas HTTPS ao core continuam exigindo identidade/RBAC/mTLS. Uma política entre contas distintas precisa de implementação e testes de grupo/credenciais no transporte antes de alterar a unidade.

No JSON do gateway, `state_directory` é obrigatório e referencia `/var/lib/orionq-pl`. A unidade usa `StateDirectory=orionq-pl` para esse estado e `RuntimeDirectory=orionq-pl` para `/run/orionq-pl`; são namespaces distintos apesar do mesmo basename. A limpeza do socket ao parar/reiniciar o processo não apaga os registros que bloqueiam gerações e sessões já consumidas. Não mova esses ledgers para `/run` nem os apague para contornar uma recusa. Após um reinício, forneça uma geração nova e uma sessão PS nova, preservando o estado persistente. A CLI rejeita estado no diretório do socket, em `/run` ou em `/dev/shm`.

Provisionar a política institucional, material TLS e segredos continua sendo operação administrativa externa ao build. Use os [procedimentos do core](orionq_linux_service.md), sem colocar a identidade administrativa no processo de serviço. A versão embarcada executa o mesmo `service.cli` e a mesma renovação limitada da identidade própria; não renova autoridade de usuário ou equipamento. Consulte a recuperação conservadora no [guia background](orionq_background_service.md).

Para gerar a configuração do gateway, execute `describe` **com o Python e os módulos instalados no alvo**:

```bash
python3 -I -m quantum_autopilot.service.pl_gateway_cli describe
```

Use o `image_sha256` e o UID apresentados para preencher o template. O hash identifica os quatro módulos relevantes; não é o hash de um bitstream FPGA. O exemplo contém uma string e UID inválidos de propósito, para que não seja usado como registro funcional. Ajuste `backend_id`, `generation`, `max_events` e `request_timeout_s` conforme o backend registrado. A CLI rejeita UID root, UID diferente, hash divergente e modo físico sem composição confiável.

Verificações anteriores à ativação:

```bash
python3 -I /usr/libexec/orionq/platform-preflight.py \
  --config /etc/orionq/platform.json --artifact /usr/share/orionq/core-artifact.json
python3 -I -m quantum_autopilot.service.cli --config /etc/orionq/core.json check
python3 -I -m quantum_autopilot.service.pl_gateway_cli --config /etc/orionq/pl-gateway.json check
```

Os checkers retornam erro e bloqueiam quando há configuração incompleta/incompatível. Não habilite serviços para contornar uma falha. Depois de provisionar/revisar, o integrador pode iniciar gateway e core, confirmar `probe` do gateway e só então habilitar boot. Este trabalho não instala nem habilita serviços na máquina do desenvolvedor.

## Pré-check da plataforma e perfil físico futuro

`platform.json` usa `orion.petalinux.platform.v1`: `backend_mode`, `architecture` e limites de slots/eventos/memória/ABI. O exemplo configura `aarch64`, ABI 1.0, oito slots, até 256 eventos/bloco e orçamento wire de 64 MiB. O orçamento é de buffers de transporte; não é RSS total, nem capacidade de BRAM/DDR comprovada. Os limites não substituem a negociação de recursos do backend. O perfil de referência RTL tem limites próprios; a instalação deve manter ambos compatíveis.

O modo `linux-pl-driver` exige também `hardware`:

| Campo | Verificação read-only |
|---|---|
| `bsp`, `hardware_export`, `bitstream`: `{path, sha256}` | Arquivo regular não vazio, caminho absoluto, SHA-256 e estabilidade durante leitura; SDT deve ser empacotado como artefato identificado |
| `kernel`: `release`, `configuration: {path, sha256}`, `required_options` | Release local exato, hash de `.config`, opções `CONFIG_*=y/m` solicitadas explicitamente |
| `driver`: `device`, `module`, `abi_major/minor`, `design_id` | Dispositivo character dedicado, módulo presente em `/sys/module`, ABI declarada compatível; `/dev/mem` é recusado |

O preflight não abre o device, não realiza ioctl, não carrega bitstream, não escreve registradores e não autentica um driver apenas por seu nome. O perfil físico permanece `blocked`, mesmo quando os fingerprints conferem, com a causa `trusted-physical-driver-composition-and-qualification`. Hashes permitem conferir identificação/integridade dos arquivos; autenticidade do release e readback da unidade exigem controles adicionais.

Uma unidade futura que adquira device físico precisa de driver específico/DMA API do kernel, device tree coerente, política mínima de dispositivo, interrupções, cache/fences, reset/cancelamento, sincronismo, calibração e teste de perdas. `PrivateDevices=yes` no perfil atual impede acesso físico direto; um drop-in só deve conceder o dispositivo qualificado e manter bloqueios de atuação. A origem física dos timestamps deve continuar usando os mesmos contratos e protocolos, sem seleção do algoritmo pela origem da fonte.

## Desempenho e limites de tempo

Python no PS é uma escolha de controle e integração. Não equivale a execução bare-metal, RTOS, lógica FPGA ou garantias de hard real-time. O GIL, alocação de objetos, garbage collection, scheduler Linux, bibliotecas nativas, comunicação entre processos e armazenamento têm latências próprias. `request_timeout_s` é política de serviço; não qualifica deadlines elétricos ou temporais do hardware.

A referência deve medir rootfs, espaço ocupado por bibliotecas, RSS/PSS de core e gateway, import/startup time, CPU, latência de IPC e throughput/perdas de blocos, sem confundir tamanho do tar com footprint instalado. Compare CPU interna, CPU externa e PL emulada com o mesmo input/protocolo e custo de transporte incluído. Um ganho numa FPGA precisa de benchmark na placa; presença de NumPy/Rust/C++ ou do gateway não prova ganho. Não use `-ffast-math`, mudança de precisão ou descarte de eventos científicos para reduzir custo sem revisão do contrato e validação numérica.

## Validação reproduzível e aceitação pendente

```bash
python3 -m pytest tests/test_petalinux_deployment.py
```

Os testes verificam staging preservando fontes, tar determinístico, recursos, mapas de dependências, wheel real do host, ausência de GUI/segredos no pacote, versões incompatíveis/removidas, recursos/ABI inválidos, checksum adulterado, JSON inválido e sanitização. No Linux há testes de FIFO/symlink, processos separados com recriação do diretório runtime preservando as gerações/sessões consumidas e parser real do systemd usando executáveis substitutos temporários; o parser valida gramática, sem iniciar uma unidade. O host Windows aprovou 30 testes em 21,24 s; esses três cenários Linux foram explicitamente pulados, conforme `outputs/petalinux_deployment_20261011/host-tests-state-final.xml`. Resultados de Linux, ARM e hardware devem ser registrados separadamente do gate geral e da campanha RTL/gateway; o resultado no host não os substitui.

Antes de aceitar implantação física, faltam: toolchain/BSP/placa, receitas compatíveis e bloqueadas por digest, cross-build/rootfs, import/protocolo no alvo, boot e atualização/recovery, driver/bitstream/readback, validação de eventos/IRQ/cache em ARM, aquisição contínua e limites medidos, segurança de dispositivos/interlocks e calibração física. O serviço operacional de software já pode ser exercitado com workers externos sem alegar que essas etapas foram realizadas.

## Fontes e edições consultadas

| Fonte primária | Edição/data observada | Aplicação/limite |
|---|---|---|
| [AMD UG1144 — Introduction](https://docs.amd.com/r/2025.2-English/ug1144-petalinux-tools-reference-guide/Introduction) | 2025.2; 2025-11-20 | Fluxos XSA/SDT e alvo SoC; não demonstra o build deste projeto |
| [AMD UG1144 — Installation Requirements](https://docs.amd.com/r/2025.2-English/ug1144-petalinux-tools-reference-guide/Installation-Requirements) | 2025.2; 2025-11-20 | Ambiente de ferramentas/BSP a qualificar |
| [AMD Yocto Release Notes](https://xilinx-wiki.atlassian.net/wiki/spaces/A/pages/2613018625/Yocto+Project+related+Release+Note) | Release 2025.2/v25.11; Scarthgap 5.0.12 | Baseline explícita de release e kernel; não confundir com tip de um layer |
| [Yocto classes](https://docs.yoctoproject.org/scarthgap/ref-manual/classes.html) | Scarthgap 5.0-tip, consulta 2026-10-11 | PEP 517, systemd/useradd; fixar branch/commit na imagem |
| [NumPy cross-compilation](https://numpy.org/doc/stable/building/cross_compilation.html) | Página stable identificada como 2.5 na consulta | Meson/sysroot/formatos do alvo, não promete wheel para qualquer SoC |
| [SciPy cross-compilation](https://docs.scipy.org/doc/scipy/building/cross_compilation.html) e [BLAS/LAPACK](https://docs.scipy.org/doc/scipy/building/blas_lapack.html) | Página 1.18.0 na consulta | Toolchains, include dirs e bibliotecas numéricas |
| [cryptography installation](https://cryptography.io/en/stable/installation/) | Página stable 50.0.2 na consulta | C/Rust/OpenSSL/libffi e arquiteturas testadas pela biblioteca |
| [NetworkX installation](https://networkx.org/documentation/stable/install.html) | Página 3.7 na consulta | Python puro; versão corrente exige Python 3.12+, portanto respeitar o pacote selecionado |
| [Qiskit Aer getting started](https://qiskit.github.io/qiskit-aer/getting_started.html) | Página 0.17.1 na consulta | Construção nativa/CMake e restrições específicas de GPU |

As páginas `stable` e índices podem mudar. As edições acima foram registradas durante a pesquisa; o release de implantação precisa de dependências e fontes congeladas, SBOM e atualização de segurança, preservando estado durável e bloqueio de equipamentos quando houver incerteza.
