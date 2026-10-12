# OrionQ 5.64.0: implementação Linux, PetaLinux e fronteira PS/PL

O controlador recebeu um gateway Linux em processo separado, contratos binários para correlação temporal, lógica RTL de referência e um layer de implantação PetaLinux. A validação final do pacote instalado em Linux aprovou **491 testes, sem falhas, erros ou skips**. Duas campanhas de onze nós passaram com os workers externos CPU e PL emulada. A execução física em PS/ARM, AXI/DMA e FPGA continua **não demonstrada**.

Esta etapa preserva as alterações anteriores, modelos opcionais, autenticação, auditoria e bloqueio de atuação física. O serviço mantém identidade e relógio próprios; o login humano identifica e autoriza intervenções. Terminal e desktop remoto continuam clientes da API autenticada. O executável Windows 5.61.0 foi preservado e não foi recompilado nesta etapa. Os nomes dos artefatos usam a data UTC de 2026-10-11; a execução ocorreu na noite de 2026-10-10 em America/Bahia.

## Escopo implementado e comprovado

| Componente | Implementação | Verificação executada | Limite restante |
|---|---|---|---|
| Controlador PS/Linux | Serviço headless com roteamento, reservas, protocolos, persistência, TLS 1.3/mTLS, permissões e auditoria | Wheel instalado, imports, integração, CLI e monitor terminal no Linux x86_64 | Boot e carga no PS/ARM da placa |
| Aquisição comum | Eventos normalizados com sessão, configuração, relógio, canais, qualidade e origem preservada | N01 emulado e dez nós SeQUeNCe nativos no mesmo pipeline | Swabian físico, SDK e calibração não disponíveis |
| Gateway externo | `orion-pl-gateway`: configuração estrita, probe, AF_UNIX, SO_PEERCRED, deadlines e ledgers persistentes | Processos Linux reais, falhas, cancelamento, reinício e gerações | Perfil atual exige mesmo UID; não isola contas distintas |
| Workers substituíveis | `cpu-external` e `emulated-pl-external`, registrados pelo serviço confiável | Histograma e contagens comparados integralmente à CPU independente | Ambos executam em CPU; nenhum ganho FPGA demonstrado |
| Correlação PL | RTL original `orionq_correlation`, codec QCOR 1.0, timestamps inteiros | Icarus, casos de borda, backpressure e síntese genérica Yosys | Wrapper AXI, DMA, bitstream, place/route e medição física |
| Portas rápidas | RTL `orionq_fast_gate`, oito portas, heartbeat, watchdog, interlocks e falha latched | 31 verificações de comportamento sem atuação | Integração elétrica e qualificação experimental; `PHYSICAL_ENABLE=0` |
| PetaLinux | Layer Scarthgap, receitas core/gateway, systemd e preflight | Staging determinístico e testes de contratos no host Linux | Provedores, BSP/XSA ou SDT, build ARM e boot não executados |

Os eventos de uma fonte não selecionam implementação nem carregam módulos. A composição confiável do serviço escolhe o worker; a origem permanece disponível para rastreabilidade e admissão física. Timestamps e coincidências não são evidência suficiente de entrelaçamento, bases, bits ou chaves QKD.

## Ambiente e reprodução

| Item | Ambiente observado |
|---|---|
| Host | AMD Ryzen 7 5700X, oito cores/16 processadores lógicos, Windows com WSL 2 |
| Linux | Ubuntu 24.04.5 LTS, x86_64, kernel `6.18.33.2-microsoft-standard-WSL2` |
| Identidade | Conta não privilegiada `orionq-validation`, UID/GID 10001 |
| Python | Linux 3.12.3; Windows 3.12.14 no ambiente de auditoria |
| Estado e testes Linux | Filesystem nativo em `/var/tmp`, identificado como ext2/ext3; não executados diretamente no mount Windows |
| SeQUeNCe | Checkout fixado em `3133c66d80a898c6c3cc9af9eec3fa94fdbe2233`, ambiente separado |
| RTL | Icarus Verilog 12.0 e Yosys 0.33, extraídos de pacotes Ubuntu; [manifesto](../../outputs/pspl_rtl_20261011/toolchain-manifest.json) |
| Recursos não usados | Docker, placa ARM/FPGA, BSP, ferramentas PetaLinux/Vivado e equipamento Swabian |

A campanha final está no [relatório Linux](../../outputs/pspl_linux_20261011/installed-linux-final/report.json) e no [JUnit integral](../../outputs/pspl_linux_20261011/installed-linux-final/tests.xml). O [freeze de dependências](../../outputs/pspl_linux_20261011/installed-linux-final/dependency-freeze.log) registra as versões instaladas. Os comandos operacionais e o provisionamento estão no [manual PS/Linux](../product/orionq_pspl_linux.md) e no [guia PetaLinux](../product/orionq_petalinux.md).

Exemplo de reprodução no Linux, com caminhos próprios e diretórios de evidência novos:

```bash
export SEQUENCE_PYTHON=/caminho/sequence-venv/bin/python
export SEQUENCE_SRC=/caminho/SeQUeNCe
python3 tools/run_core_linux_validation.py \
  --repository /caminho/OrionQ \
  --evidence /var/tmp/orionq-linux-qualificacao-nova
python3 tools/run_pspl_linux_campaign.py \
  --repository /caminho/OrionQ \
  --native /caminho/evidencia-sequence/native-final-v2 \
  --evidence /var/tmp/orionq-pspl-qualificacao-nova
python3 tools/run_pspl_rtl_validation.py --help
```

Disponibilize `iverilog`, `vvp` e `yosys` no PATH para executar também os casos RTL. A segunda campanha consome os registros nativos identificados da [campanha mista anterior](orionq_mixed_controller_20261011.md); não transforma fixtures em recepção física. O runner completo constrói e instala o wheel em ambiente novo antes dos testes.

## Validação e correções

| Verificação | Resultado final | Evidência |
|---|---|---|
| Pacote instalado Linux 5.64.0 | **491 PASS, 0 FAIL, 0 ERROR, 0 SKIP**; pytest 82,44 s | [JUnit](../../outputs/pspl_linux_20261011/installed-linux-final/tests.xml) |
| Compatibilidade Windows dos novos contratos | **68 PASS, 4 SKIP** exclusivos do Linux; 0,83 s | [JUnit](../../outputs/pspl_linux_20261011/windows-preflight-final.xml) |
| Imports e comportamento do core instalado | 112 módulos carregados; sem imports GUI, ML ou autoridade Windows; probe de três nós/dois enlaces | [Relatório](../../outputs/pspl_linux_20261011/installed-linux-final/installed-core-report.json) |
| Serviço independente do cliente | TLS 1.3/mTLS; tick 2 antes de desconectar e tick 3 depois; SIGTERM limpo em 0,276 s; detector de segredos negativo | [CLI smoke](../../outputs/pspl_linux_20261011/installed-linux-final/cli-smoke.json) |
| Rede mista, worker CPU externo | Onze nós, 1.970 observações e nove critérios aprovados | [Campanha](../../outputs/pspl_linux_20261011/mixed-linux/cpu-external/report.json) |
| Rede mista, worker PL emulado externo | Onze nós, 1.970 observações e nove critérios aprovados | [Campanha](../../outputs/pspl_linux_20261011/mixed-linux/emulated-pl-external/report.json) |
| RTL de correlação | 39 cenários, três sementes, 253 frames e 215.476 ciclos simulados | [Icarus/estrutura](../../outputs/pspl_rtl_20261011/linux-rtl-final/report.json) |
| RTL das portas | 31 verificações; zero ações físicas | [Mesmo relatório RTL](../../outputs/pspl_rtl_20261011/linux-rtl-final/report.json) |
| Síntese genérica | Dois módulos aprovados; zero problemas estruturais | [Yosys](../../outputs/pspl_rtl_20261011/generic-synthesis-report.json) |

Os conjuntos acima têm sobreposição e não devem ser somados como casos únicos. O resultado de 491 casos corresponde à campanha final completa, sem herdar aprovação das rodadas anteriores. O probe de três nós do wheel também não representa a topologia global inteira.

Foram reproduzidos e corrigidos três defeitos de ciclo de vida: worker inválido não fechado, alteração de sessão antes de confirmar o fechamento do worker antigo e erro de diagnóstico que ocultava o bloqueio após falha de snapshot. A [revisão interna independente final](../../outputs/pl_transport_20261011/review-root-lifecycle-v3.json) confirma fechamento do candidato rejeitado, preservação da sessão antiga em falha e recibo conservador sem métricas aceitas. Isso não substitui revisão externa humana.

A primeira campanha instalada preservou [489 aprovações e duas falhas](../../outputs/pspl_linux_20261011/installed-linux-v1/tests.xml): as fixtures não criavam o diretório persistente exigido pelo novo contrato. A correção provisiona o diretório 0700; `check` apenas verifica existência, propriedade e permissões. A [segunda rodada](../../outputs/pspl_linux_20261011/installed-linux-v2/tests.xml) parou na coleta devido a indentação de duas linhas dessa fixture. Após a correção, todos os 491 casos foram executados novamente. As falhas não foram apagadas nem convertidas em skips.

## Evidência dos onze nós e custo do transporte

Em cada modo, N01 usa uma interface Swabian explicitamente emulada; N02–N11 recebem observações de callbacks nativos SeQUeNCe. Os nove critérios verificam pipeline comum, ingestão de todos os nós, preservação dos tags N01, publicação de métricas calculadas, dez reservas de controle nativas, bloqueio da ponte ausente N01, ausência de entrelaçamento inferido de cliques, recusa de alteração pelo auditor e auditoria autenticada válida. Há **44 recibos** públicos de comparação, em dois modos × onze nós × dois workers.

As dez reservas por modo são operações do plano de controle. Não há ponte física para N01, SDK Swabian, entrega quântica certificada ou geração operacional de chaves neste ensaio. A ausência da ponte continua impedindo a comunicação correspondente.

| Modo | Pipeline local, mediana | Externo com CPU de referência, mediana | Transporte, mediana | Início/fim do gateway |
|---|---:|---:|---:|---:|
| `cpu-external` | 3,409 ms | 7,179 ms | 3,524 ms | 0,121 / 0,036 s |
| `emulated-pl-external` | 3,474 ms | 6,674 ms | 3,346 ms | 0,101 / 0,108 s |

As medianas cobrem onze blocos por modo, em uma execução curta. A instalação do wheel ocorreu simultaneamente no host. Os custos incluem transporte e comparação CPU integral: demonstram interoperabilidade com **sobrecusto**, não aceleração FPGA, determinismo temporal ou desempenho sob aquisição contínua. Os valores por nó estão no [relatório combinado](../../outputs/pspl_linux_20261011/mixed-linux/report.json).

## Contratos PS/PL, recuperação e segurança

O [contrato PS/PL](../architecture/orionq_pspl_contract.md), o [transporte OQPT](../architecture/orionq_pl_transport.md) e a [ABI RTL](../../fpga/orionq_pl/abi.md) documentam fronteiras diferentes:

- OQPL preserva eventos inteiros em ps, canais assinados, sessão, configuração e relógio. Seu anel lógico continua emulado, sem descritores físicos DMA.
- OQPT transporta blocos por AF_UNIX com identidade de processo, envelope limitado, digest, sequência, request ID e prazo total. O digest não substitui autenticação: a admissão local depende de SO_PEERCRED e permissões privadas.
- QCOR define o perfil do acelerador: request de 64 bytes, eventos/resultados de 16 bytes, máximo de 256 eventos e 257 bins. IDs, status, contagens e limites são verificados no PS; resultados parciais de falha não alimentam métricas.
- Gerações e sessões são persistidas antes do trabalho em diretório separado de `/run`, com exclusão entre processos e fsync. Resposta perdida, timeout, cancelamento ou estado incerto bloqueiam publicação. Recuperação exige worker verificado, geração superior e nova sessão; não há retry automático do mesmo bloco.
- Configuração das portas converte ps para ciclos apenas quando a divisão é exata. Isso não promete resolução elétrica de 1 ps nem autoriza arming. O default RTL mantém atuação física bloqueada.

A CLI recusa `linux-pl-driver` enquanto não existir composição confiável e qualificada. O código oferece a fronteira para probe/readback do driver, sem `/dev/mem`, endereço de placa universal, ioctl presumido ou carregamento de código solicitado pela fonte. Os ledgers têm limite explícito de 4.096 tombstones; backup e recuperação não podem eliminar sessões usadas ou restaurar gerações antigas.

## RTL: resultado e custo de implantação

Icarus comparou histograma inteiro, pares, status, identidade e TLAST; o backpressure manteve cada resultado por sete ciclos sem perdas ou duplicação. As sementes foram 3, 17 e 101. O perfil calcula pares dentro de um bloco; não inclui coincidências entre blocos ou inferência de fidelidade/QKD.

A síntese genérica `synth -top ... -noabc; check; stat` resultou em **235.397 células genéricas** para correlação e **1.172** para portas. A correlação consumiu 199,14 s de CPU e pico de 3.494,41 MB durante a síntese; portas, 0,46 s e 21,89 MB. Esse custo é uma restrição material para seleção e otimização da placa. Células genéricas não são LUTs de um dispositivo específico. Não houve mapeamento em primitivas FPGA, timing closure, bitstream ou medição de throughput.

## Artefatos entregues

| Artefato público | Identificação |
|---|---|
| [Wheel headless testado](../../outputs/pspl_linux_20261011/wheel/quantum_twin-5.64.0-py3-none-any.whl) | SHA-256 `f2ffc7ce47942a28507b6c17811d1105fff2ccc886b067934e6d8f2b35bbae55` |
| [Layer PetaLinux final](../../outputs/pspl_linux_20261011/petalinux-layer-v2/orionq-staging.json) | 477 arquivos fonte; Scarthgap; serviços desabilitados por padrão |
| [Arquivo fonte da receita](../../outputs/pspl_linux_20261011/petalinux-layer-v2/recipes-orionq/orionq-core/files/orionq-core-5.64.0.tar.gz) | SHA-256 `797d9ee1478551cdc613463758b1e612f054d170cc99650df8408d091f5d3935` |
| [Manifesto de entrega](../../outputs/pspl_linux_20261011/delivery-integrity.json) | 517 arquivos comparados; fontes de runtime intactas; 66 evidências públicas exportadas por lista explícita |

O wheel é Python puro, mas suas dependências nativas precisam de builds compatíveis com a arquitetura alvo. A verificação de integridade distingue quatro metadados gerados pelo setuptools das fontes de runtime; os metadados foram conferidos como versão 5.64.0, dependências headless e três entry points. Não houve alteração de fonte de runtime durante os gates finais. A campanha mista usou a instalação anterior com o mesmo código de produção; seus quatro hashes de módulos coincidem com a entrega final.

A exportação pública exclui estado privado, certificados, credenciais e material de chaves. O manifesto lista hashes de cada arquivo permitido. Os dados brutos e estados completos permanecem no ambiente nativo de validação, sem cópia indiscriminada de diretórios.

## Pendências para placa e operação física

| Pendência | Próxima evidência necessária |
|---|---|
| PS/ARM e PetaLinux | Placa/SoC, BSP/XSA ou SDT, versões qualificadas, receitas/backports e rootfs com todas as dependências; build e boot reais |
| Dependências do core | Qualificar cryptography, NumPy, pandas, SciPy, Qiskit/Aer, NetworkX, SGP4, WebSockets e PyYAML no sysroot; o layer não garante provedores stock suficientes |
| Caminho AXI/DMA | Wrapper e integração do IP, UAPI versionada, DMAengine, buffers/cache, IRQ, reset, CDC e captura ILA na placa |
| Desempenho PL | Adequação dos recursos, place/route, clock, timing closure, carga sustentada, perdas e backpressure medidos |
| Swabian físico | SDK/licença, unidade/serial, canais, níveis elétricos, impedância, eficiência, jitter, sincronismo e calibração |
| Portas rápidas | Interlocks físicos, readback, autorização de equipamento e verificação de estados seguros antes de liberar qualquer saída |
| Segurança operacional | Isolamento entre contas conforme implantação, quotas do alvo, proteção e recuperação anti-rollback dos ledgers, qualificação institucional |
| Qualificação científica | Medições e referências independentes aplicáveis ao experimento; contagens simuladas não certificam entrelaçamento ou segurança QKD |

As referências técnicas usadas são [DMAengine Linux](https://docs.kernel.org/driver-api/dmaengine/client.html), [DMA mapping Linux](https://docs.kernel.org/core-api/dma-api-howto.html), [AMD AXI DMA PG021](https://docs.amd.com/r/en-US/pg021_axi_dma/Core-Overview) e [AMD PetaLinux 2025.2 UG1144](https://docs.amd.com/r/2025.2-English/ug1144-petalinux-tools-reference-guide/Introduction). As receitas e seus limites de versão estão no guia PetaLinux. Esta entrega demonstra a arquitetura e seu comportamento computacional; não atribui TRL 5 nem qualifica um equipamento espacial, rede QKD física ou FPGA instalada.
