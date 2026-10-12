# OrionQ 5.66.2 — Windows, Linux e entrega Git

Esta versão foi recompilada para Windows e Linux x86_64. Os gates automáticos de software passaram nos ambientes e perfis descritos abaixo. A atuação física permanece bloqueada, os modelos são opcionais e nenhuma qualificação de hardware ou TRL 5 é declarada.

## Alterações incorporadas

Encerramento e troca de fontes agora conservam os workers e objetos Qt até a drenagem assíncrona terminar. O consumo de pares usa a reserva durável existente e apresenta corretamente o estado publicado; a admissão de recursos orbitais rejeita propriedade duplicada e capacidade excedida. O planejamento usa quota fixa de 24 episódios por política, com semente reiniciada; a janela de parede de 0,12 s foi removida do planejamento. O cache de commissioning passou de 16 para 512 entradas sem alterar fórmulas ou RNG.

A interface identifica por enlace o processamento, a origem dos dados e a física utilizada. Os testes portáveis usam fixtures públicas identificadas, sem depender de outputs/ ou work/. A atribuição do OpenStreetMap foi corrigida e Quantum.otf foi retirada do código e do empacotamento. A coleta dessa fonte de uma instalação antiga é rejeitada.

Durante a entrega, corrigimos fixtures que não aguardavam o encerramento assíncrono, o comparador de conteúdo que omitia duas novas notas da interface e a verificação de encerramento que consultava o worker antes de sua drenagem. Não foram reduzidos os critérios de leitura, alterados resultados científicos ou ampliados prazos de protocolos para corrigir esses comparadores. Os detalhes estão em [concorrência e roteamento](orionq_concurrency_and_routing_20261012.md).

## Validações e perfis

| Verificação | Resultado | Escopo |
|---|---:|---|
| Windows — 31 grupos de fonte | 4.848 casos distintos aprovados; 24 pulados | Dependências/hardware opcionais; sem falhas nos grupos finais |
| Windows — autoteste do EXE | 39/39 | Interface, fontes, topologia, tecnologias, leitores, ticks e encerramento |
| Linux — regressão de fonte | 1.789 aprovados | 43 módulos, UID 10001, estados temporários em tmpfs de 4 GiB |
| Linux — ferramenta de build | 19 aprovados | Versões, isolamento de retomadas e watchdogs inválidos |
| Ferramenta de entrega Git | 356 aprovados | Caminhos, bytes, evidências e exclusão de estado privado |
| Linux — GUI ELF | 39/39 e startup aprovados | Qt offscreen em ext4/VHD no HDD; 113 validações de entrada científica |
| Linux — core/operador/gateway ELF | Aprovados | TLS 1.3 mútua, PTY, cores, NO_COLOR, VT100, revogação, continuidade do core e gateway emulado; fixtures em RAM |
| Arquivos Linux extraídos | Aprovados | Gzip/TAR, todos os bytes, permissões e entrypoints; GUI↔core por mTLS |
| Fontes congeladas | 80 módulos e 27 recursos | Comparação do código e dos dados; checks PE não se aplicam ao ELF |

O relatório [Linux final](../../outputs/release-5.66.2-20261012/linux/final-native/linux-release-qualification.json) registra também a disponibilidade e o resultado do Wayland/WSLg. Isso não qualifica uma estação Linux independente, arraste físico do mouse, ARM/PetaLinux ou FPGA real. O dashboard web opcional tem os mesmos 83 arquivos da entrega anterior; sua validação de lint/build é herdada por identidade de bytes.

A máquina usada tem Ryzen 7 5700X, 32 GiB de RAM, Windows 11 e Ubuntu 24.04/WSL2 x86_64 com glibc 2.39. O projeto e o VHD do Linux estão no HDD Hitachi HDS721050CLA362 do disco E:. Python Windows 3.12.14; Linux 3.12.3. A GUI Linux usa PySide6 6.12.0, Torch 2.14.1+cpu e scikit-learn 1.9.1; CUDA não está incluído nesse perfil.

## Armazenamento e desempenho: limitações preservadas

A primeira rodada Linux no HDD foi interrompida para diagnosticar duas falhas de prazo: invalidação em 0,747 s contra 0,5 s, e três ticks que não chegaram em 30 s. Os mesmos dois testes passaram em RAM, sem mudança do aplicativo ou desses prazos. A regressão funcional completa e a campanha headless original também passaram em RAM. Essa comprovação não estabelece desempenho do HDD nem durabilidade em falta de energia.

No ELF headless com 76 nós, 136 enlaces e 11 satélites, a configuração global excedeu o cliente de 15 s no HDD e retornou resultado incerto. Com cliente de 30 s, o serviço publicou dados reais do backend: leituras de snapshot entre 1,41 e 2,54 s e layout do terminal com p95 de 2,05 ms. O segundo login NO_COLOR, limitado a 2 s, falhou nessa rodada. Esses resultados são preservados no [perfil de armazenamento](../../outputs/release-5.66.2-20261012/linux/native-storage-profile.json). Uma operação incerta exige inspeção do estado/recibos; não é autorização para repetir cegamente uma entrega ou configuração.

A campanha inteira da GUI no HDD excedeu o watchdog inicial de 600 s, e uma inspeção fria de dependências excedeu 30 s. A ferramenta agora aceita limites explícitos para essas tarefas de build. A rodada aprovada usou --gui-self-test-timeout-s 1200 e --dependency-probe-timeout-s 120. Esses limites abrangem a campanha e a inspeção de bibliotecas; não alteram deadlines de API, de cada interação, dos protocolos ou dos recursos quânticos.

O startup offscreen Linux mediu heartbeat p95 de 20,57 ms e máximo de 841,97 ms. Há pausas breves observáveis; não declaramos ausência universal de pausas. A medição de fonte da topologia completa em 12/10 teve p50 de 7,19 s por passo; a metropolitana teve p50 de 0,400 s e primeiro passo de 1,742 s. G05 — processamento científico da rede global a 1 Hz — permanece aberto. Esses últimos números são da fonte, não benchmarks dos novos binários.

## Artefatos

| Pacote | Caminho | SHA-256 |
|---|---|---|
| Windows portátil | [ZIP](../../release/windows/5.66.2-native-20261012-r3-native-final/OrionQ-5.66.2-native-windows-x64.zip) | 821d6b2272ee0de17ae5f0514597b3a3d32f6e3a88ce23aef31b476ab3b18fe2 |
| Linux GUI | [TAR](../../release/linux/5.66.2-native-gui-20261012-r3-native-final/OrionQ-5.66.2-linux-x86_64-gui.tar.gz) | 79910c2a86f0cf68dfa964539da9caeabed90d7a0234add24c244e8ec0c08b7d |
| Linux headless | [TAR](../../release/linux/5.66.2-native-headless-20261012-r2-native-final/OrionQ-5.66.2-linux-x86_64-headless.tar.gz) | de6352b062b2a0209c4682e7e143e43491ff9024ee9f148dab53607f246b6427 |

Extraia o pacote Windows completo e execute OrionQ/OrionQ.exe, mantendo a pasta _internal. No Linux, extraia o TAR com tar -xzf e mantenha o diretório interno: ./OrionQ/OrionQ --version para a GUI; ./OrionQ-headless/orion-core --help, orion-operator --help e orion-pl-gateway --help para os serviços. O pacote inclui o exemplo de configuração e a unidade systemd; provisionamento, permissões e certificados devem ser configurados para cada instalação, sem reutilizar as credenciais dos ensaios.

Os diretórios anteriores foram preservados. O relatório [consolidação Windows](../../outputs/release-5.66.2-20261012/windows/consolidation-final.json) inclui os grupos suplementares finais. A entrega Git usa um snapshot independente, branch codex/release-5.66.2, sem remote ou push. O histórico e as alterações do repositório original permanecem preservados. ZIP, bundle e resultados de fsck, recuperação, comparação de bytes e instalação do wheel recuperado ficam em [git-delivery.json](../../outputs/release-5.66.2-20261012/git/git-delivery.json).

Swabian, SDR/OGS e FPGA físicos não estavam conectados. Os ZIPs originais QKD/Author indisponíveis não foram reconstruídos para simular validação de hash. Reserva central e os testes emulados não demonstram consenso distribuído, segurança experimental de geração QKD nem integração física concluída.
