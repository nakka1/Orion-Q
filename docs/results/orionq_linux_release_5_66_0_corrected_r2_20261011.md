# OrionQ 5.66.0 — entrega Linux corrigida r2

Data: 11/10/2026. O escopo aprovado é software x86_64 em Ubuntu 24.04.5/WSL2: interface gráfica, core autônomo, operador em terminal e gateway PL emulado. Hardware, Linux desktop independente e BSP PetaLinux não foram qualificados nesta campanha.

O resumo verificável dos artefatos é [`linux-delivery-summary.json`](../../outputs/release-5.66.0-20261011/linux/corrected-r2/linux-delivery-summary.json). A entrega r2 substitui os artefatos Linux anteriores desta versão: a varredura Windows encontrou perda da posição de janelas de nodes ao alternar o tema. A posição era limitada pelo viewport provisório ainda oculto; a correção restaura a posição após o layout final e considera o pixel inferior da barra de ações. Wheel, GUI e headless foram compilados novamente. Os arquivos e diagnósticos r1 permanecem preservados em seus diretórios originais. Os suplementos posteriores aprovam mais 177 casos, incluindo as regressões do leitor de evidências, e registram a união final de **1.762 aprovados e 1 skip**, sem reescrever os resumos ou os binários r2.

## Artefatos finais r2

| Perfil | Arquivo | Bytes | SHA-256 |
|---|---|---:|---|
| GUI, modelos CPU opcionais | `release/linux/5.66.0-native-gui-20261011-corrected-r2/OrionQ-5.66.0-linux-x86_64-gui.tar.gz` | 539.184.740 | `af4a1099727521974a328882972542fffe8bce1b1698b8799a20a22ee014c3b9` |
| Core, operador terminal, gateway PL sem Qt | `release/linux/5.66.0-native-headless-20261011-corrected-r2/OrionQ-5.66.0-linux-x86_64-headless.tar.gz` | 179.137.331 | `d092df80226f32b46c769052c45c82015cd1bbef18004b80aaf25c8d8e99ec09` |
| Pacote Python | `release/linux/5.66.0-corrected-r2/quantum_twin-5.66.0-py3-none-any.whl` | 11.502.538 | `f97e3dea6b400f18fa240e3b46c13bd6e230f45418437e180f941d5e5bc5b92f` |

ELF GUI: `709ec2ff7e1082686ccd3e9c1291936b4b93f9bc0c27c11a78c6fd5bebfe1144`. Os três entrypoints headless usam `16b8d21cdbd41a711330d8064879bdf756df983b5bc611cca8bd8df775a6a513`. Embora os bytes dos entrypoints headless sejam iguais aos da tentativa anterior, o pacote foi reconstruído e validado com a nova proveniência e metadados; o arquivo distribuído não foi reaproveitado.

Os dois TARs foram extraídos em diretórios privados novos no ext4. CRC gzip, todos os bytes dos arquivos, permissões e execução dos entrypoints passaram. São 3.700 arquivos no perfil GUI e 702 no headless. O manifesto de produção r2 inclui 679 entradas e tem SHA-256 `b236fa1bdab1ee2ff901f9469f2e5edb2be7b31c1aacf91c3a41203a186de29d`; todas ficaram intactas durante builds e ensaios. A inspeção do ELF GUI vincula 62 módulos e 25 recursos ao manifesto, incluindo módulos científicos do `quantum_twin`, em otimização de bytecode 1.

## Ambiente e verificações

Execução UID 10001, Python 3.12.3, Qt/PySide6 6.12.0, kernel WSL2 6.18.33.2 e glibc 2.39. O wheel corrigido foi instalado em ambiente privado novo. As dependências anteriores foram reutilizadas somente para leitura. GUI CPU: Torch 2.14.1+cpu, scikit-learn 1.9.1, CUDA indisponível. Tempo de build headless: 242,74 s; GUI: 854,60 s. Esses tempos incluem qualificação obrigatória e ocorreram sob outras cargas de validação.

Todas as evidências abaixo ficam em `outputs/release-5.66.0-20261011/linux/corrected-r2/`, exceto os ensaios internos do build, que ficam em `release/linux/<perfil-r2>/evidence/`.

| Verificação | Resultado | Evidência |
|---|---|---|
| Ciência, concorrência, SeQUeNCe, RTL, terminal, backend e topologia ao vivo | **1.471 aprovados, 1 skip** em 46 módulos | `source-tests.xml`, `source-tests-summary.json` |
| Widgets e layouts compactos, conteúdo extenso, atualização flutuante e restauração após tema | **114 aprovados** | `visual-regression.xml`, `visual-regression-summary.json`; PNGs finais em `../visual-captures-final/` |
| Login, relatório após recalibração e restauração de janelas com fixture final | **3 aprovados**, casos já existentes no corpus | `final-ui-fixture-regression-retry-1.xml` |
| Leitura e exportação de recibos, decisões e atualização assíncrona com fixture final | **42 aprovados**, Qt offscreen, fontes e testes intactos | `display-audit-fixture-final.xml`, `display-audit-fixture-final-summary.json` |
| Manifesto determinístico e diagnóstico de runtime | **19 aprovados**, incluindo 11 casos do gerador e 8 do runtime | `runtime-manifest-tool-final.xml`, `runtime-manifest-tool-final-summary.json` |
| Leitor de evidências e comparação estática, suplemento posterior ao build | **116 aprovados**, fontes e ferramentas congeladas durante o ensaio | `reader-static-contracts-final.xml`, `reader-static-contracts-final-summary.json` |
| União final dos JUnits pelo `(classname, nome completo parametrizado)` | **1.762 aprovados, 1 skip, 0 falhas/erros finais**, 1.763 casos únicos | `reader-static-contracts-final-summary.json`; resumos anteriores de 1.585 e 1.646 preservados |
| Entradas científicas inválidas/válidas no ELF GUI real | **113 verificações aprovadas**, `frozen=true` | `evidence/scientific-input-validation.json` do build GUI |
| Autoteste do ELF GUI, protocolos, cadastros, recibos, scroll e gráficos | **39 verificações aprovadas**, Qt offscreen | `evidence/gui-self-test-checks.json` do build GUI |
| Oito abas, compactação, ultrawide, maximizar/restaurar, terminal e layout original | **15 aprovadas por execução** em offscreen, Wayland explícito, Wayland automático com runtime WSLg e XWayland | `evidence/gui-startup.json`, `gui-wayland-startup.json`, `gui-default-display-retry-1.json`, `gui-xwayland-startup.json` |
| Topologia completa do backend com fonte simulada, timeline Google/TF-QKD, onda CIMATEC, FPGA, recibos e exportação | **6 contratos funcionais aprovados**, XWayland | `gui-ui-pipeline-xwayland.json`; pixels não qualificados |
| Operador real PTY/curses → API do core nativo | **185 verificações aprovadas**, 1.273 teclas exercitadas, TLS 1.3/mTLS | `evidence/native-terminal-campaign.json` do headless |
| Terminal EVA, paleta, mensagem, desconexão e limites de autoridade | **77 verificações aprovadas** | `native-eva-campaign.json`, `native-eva-provenance.json` |
| GUI → core nativo por mTLS, core independente do operador | **Aprovado**, ticks 1 → 13 durante GUI → 14 após GUI | `gui-remote-smoke.json` |
| TARs, bytecode congelado, recursos e proveniência | **Aprovados** | `linux-release-qualification.json`, `archive-*-smoke.json`, `gui-frozen-source-linux.json` |

O total de pytest não inclui as verificações internas dos executáveis. A consolidação final registra 1.768 execuções e deduplica os casos repetidos; não soma os 114 widgets duas vezes. Os 42 casos de auditoria, os 19 de manifesto/runtime e os 116 do leitor/inspeção estática são identidades novas nesta seleção Linux, conforme comparação dos nomes completos dos JUnits. As consolidações anteriores de 1.591 e 1.652 execuções permanecem intactas. O único skip é o hash exato de dataset de referência produzido no Windows, dependente de libm/BLAS/Torch. Regressões numéricas de canal, Aer/Kraus, invariantes e entradas normal/`-O`/`-OO` passaram no Linux. O teste de contratos das ferramentas de build também passou: 11 casos Windows e dois skips reservados a POSIX, em `../build-tool-contracts-r2.xml`.

Os suplementos estão vinculados por SHA-256 em [`display-audit-fixture-final-summary.json`](../../outputs/release-5.66.0-20261011/linux/corrected-r2/display-audit-fixture-final-summary.json) e [`runtime-manifest-tool-final-summary.json`](../../outputs/release-5.66.0-20261011/linux/corrected-r2/runtime-manifest-tool-final-summary.json). O primeiro executou 42 casos em 59,28 s no mesmo ambiente Linux privado; o segundo executou 19 casos em estado temporário curto, com fixture e ferramenta congeladas. Ambos confirmaram os 679 arquivos de produção antes e depois. Nenhuma dessas etapas reconstruiu ou substituiu o ELF.

A campanha terminal usa **76 nodes, 136 enlaces e 11 satélites**. O core continua avançando sem operador autorizado, rejeita acesso anônimo e devolve 401 ao certificado revogado. Uma identidade nova acompanha a mesma instância do serviço. A apresentação não executa passos da simulação. As filas continuam limitadas e as observações e históricos recebem atualização do backend. O custo de layout textual teve p95 de **2,88 ms** em 57 renderizações; a leitura da API teve p95 de **2.203,48 ms** durante esta execução sob carga. Esses custos são diferentes e não comprovam prazo rígido de atualização.

No monitor **3440×1440, DPR 1**, o ensaio explícito Wayland teve heartbeat p95 de **23,63 ms**, máximo **368,01 ms**. O ensaio automático posterior teve p95 **21,62 ms**, máximo **773,60 ms**, incluindo inicialização e reconstrução de layouts. Esses valores não estabelecem tempo real nem substituem os benchmarks Windows da entrega.

## Dependências e evidência visual

A primeira sondagem sem override `QT_QPA_PLATFORM` herdou `XDG_RUNTIME_DIR=/run/user/10001`. Esse diretório não contém o socket WSLg; o log privado registrou `Failed to create wl_display (No such file or directory)`, e o fallback XCB encontrou bibliotecas ausentes. `gui-default-display.json` preserva **UNAVAILABLE**. A repetição com o mesmo ELF e somente `XDG_RUNTIME_DIR=/mnt/wslg/runtime-dir`, apontando para o socket já existente, aprovou 15 casos em Wayland sem forçar QPA. Não houve alteração de host, sessão, fontes ou executável. Isso qualifica o ambiente explicitado, sem prometer autodetecção em um host mal configurado.

X11 depende de bibliotecas do sistema. Nove pacotes oficiais Ubuntu foram extraídos em fixture privada na campanha anterior e reutilizados para leitura nesta campanha. Não foram instalados no host nem empacotados com o OrionQ; scripts de mantenedores não foram executados. Hashes, URLs e proveniência estão em `x11-private-dependencies.json` e no relatório anterior referenciado por ele.

| Dependência externa | Versão |
|---|---|
| `libxkbcommon-x11-0` | `1.6.0-1build1` |
| `libxcb-cursor0` | `0.1.4-1build1` |
| `libxcb-icccm4` | `0.4.1-1.1build3` |
| `libxcb-util1` | `0.4.0-1build3` |
| `libxcb-image0` | `0.4.0-2build1` |
| `libxcb-keysyms1` | `0.4.0-1build4` |
| `libxcb-render-util0` | `0.3.9-1build4` |
| `libxcb-shape0` | `1.15-1ubuntu2` |
| `libxcb-xkb1` | `1.15-1ubuntu2` |

**Captura nativa WSLg não qualificada.** A campanha r2 XWayland aprovou seis contratos, mas suas seis capturas `QScreen` são pretas e idênticas, com 3096×1296 pixels; os hashes e a inspeção estão em `gui-ui-pipeline-xwayland-pixel-inspection.json`. A revisão da imagem confirmou isso. Elas não provam pintura nem ausência de cortes. Nenhum repaint por `widget.grab` ou substituição de aplicação foi usado para contornar a captura. Os 114 casos e capturas Qt offscreen são evidências de software em escopo separado.

O diagnóstico r1 Wayland retornou capturas nulas; permanece histórico, sem promoção como prova r2. A tentativa r1 Xvfb privado exigiu `/usr/bin/xkbcomp`, ausente no host mínimo, e terminou antes de executar o OrionQ. `-xkbdir` e `-pn` não resolveram o caminho compilado, e XKEYBOARD não pôde ser desativado. O host e o servidor foram preservados; não se repetiu essa fixture inviável. Pintura nativa WSLg e arraste físico de bordas permanecem pendentes de ambiente adequado de captura.

## Tentativas preservadas e limites

- Os dois casos de UI escolhidos inicialmente sem a preparação de login falharam antes das operações. O retry incluiu o login original e aprovou os mesmos casos, preservando suas assertions. `final-ui-fixture-regression.xml` continua disponível e classificado como seleção incompleta de fixture.
- O ajuste final de fixture Windows espera a fila de apresentação terminar antes de ler as linhas do relatório de recalibração. O backend já estava completo enquanto o rodapé informava apresentação pendente; os critérios do relatório não foram reduzidos. A regressão Linux com fixture final passou.
- O helper de espera da auditoria passou a processar eventos Qt e liberar o GIL com `time.sleep(0.020)` entre consultas, preservando os prazos e as assertions. A suíte Linux de 42 casos aprovou a fixture final. Essa é uma alteração do ensaio, sem modificar o backend ou os binários.
- Um commit externo somente de documentação mudou o HEAD Git durante a preparação Windows. O gerador antigo reescreveu o campo `revision` do manifesto apesar de seus oito hashes de fonte continuarem iguais. A tentativa Windows foi interrompida; os bytes do manifesto congelado foram restaurados e todos os 679 inputs confirmados. O gerador agora preserva bytes e timestamps do manifesto existente somente quando sua estrutura, schema, revisão e mapa exato de oito hashes são válidos. Mudança nas fontes ou cache inválido gera um novo manifesto. A revisão identifica a criação do manifesto, enquanto os hashes identificam as fontes; ela não pretende identificar sozinha todo o checkout atual. Os 19 testes Linux incluem commits Git reais, fontes alteradas, schema/revisão/mapa inválidos, JSON duplicado ou não finito e preservação de bytes CRLF. Essa correção de ferramenta não alterou os artefatos Linux r2.
- As falhas r1 de comprimento AF_UNIX, aquecimento EVA, precheck de plugins e captura permanecem no relatório anterior. Não são reclassificadas como resultados r2 nem atribuídas a causas sem medição. A campanha r2 completa de origem aprovou o gateway com diretório temporário curto; EVA r2 passou na primeira execução.
- Desktop Linux independente, ARM/PetaLinux BSP, GPU Linux, Swabian físico, estação OGS/recepção RF ou óptica, FPGA AXI/DMA físico, calibração elétrica e interlocks continuam sem qualificação. O gateway CPU/emulado não qualifica PL física.

Modelos permanecem opcionais e atuação física bloqueada. Não foram exportados estado privado, bancos, certificados privados, chaves ou buffers de material secreto.

## Execução

Em Linux x86_64 com bibliotecas e serviço de exibição compatíveis, extraia cada perfil em pasta própria:

```bash
tar -xzf OrionQ-5.66.0-linux-x86_64-gui.tar.gz
./OrionQ/OrionQ --version
./OrionQ/OrionQ
./OrionQ/OrionQ --scientific-input-test scientific-input-new.json

tar -xzf OrionQ-5.66.0-linux-x86_64-headless.tar.gz
./OrionQ-headless/orion-core --help
./OrionQ-headless/orion-operator --help
./OrionQ-headless/orion-pl-gateway --help
```

Na fixture WSLg desta campanha, a invocação gráfica usou o socket existente em `XDG_RUNTIME_DIR=/mnt/wslg/runtime-dir`. Não use esse caminho como parâmetro universal para desktops Linux. Serviço, identidade, mTLS e operação terminal estão documentados em [`orionq_linux_service.md`](../product/orionq_linux_service.md), [`orionq_terminal_operator.md`](../product/orionq_terminal_operator.md) e [`orionq_pspl_linux.md`](../product/orionq_pspl_linux.md).

As ferramentas `tools/build_linux_native.py`, `tools/qualify_linux_native_release.py`, `tools/run_linux_native_eva.py` e `tools/probe_linux_qt_display.py` registram versão, hashes, escopo e limites. A qualificação admite caminhos r2 explícitos e mantém a exigência de validação científica do ELF. Nenhum gate foi relaxado para aprovar esta entrega.
