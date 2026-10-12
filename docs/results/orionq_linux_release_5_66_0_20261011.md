# OrionQ 5.66.0 — entrega e qualificação Linux

Data: 11/10/2026. Escopo aprovado: software Linux x86_64 em Ubuntu 24.04.5/WSL2, com interface gráfica, controlador sem interface gráfica e operador em terminal. Não é qualificação de hardware nem de uma estação Linux independente.

O resumo verificável é [`linux-delivery-summary.json`](../../outputs/release-5.66.0-20261011/linux/linux-delivery-summary.json). Ele separa resultados funcionais, capturas visuais, tentativas anteriores e dependências externas. As tentativas que falharam foram preservadas; não foram transformadas em aprovações.

## Artefatos

| Perfil | Arquivo | Bytes | SHA-256 |
|---|---|---:|---|
| GUI e modelos CPU opcionais | `release/linux/5.66.0-native-gui-20261011/OrionQ-5.66.0-linux-x86_64-gui.tar.gz` | 539.189.752 | `4b5e58264f4ae524c29a7b4cf8a56f94d41606a11c95e4f3a2ab54b86d9fad31` |
| Core, operador terminal e gateway PL sem Qt | `release/linux/5.66.0-native-headless-20261011/OrionQ-5.66.0-linux-x86_64-headless.tar.gz` | 179.138.168 | `b811ec3c78736ea6fd6a94396829e7582dee5f093300c468dc38cdbc131d7b37` |
| Pacote Python | `release/linux/5.66.0/quantum_twin-5.66.0-py3-none-any.whl` | 11.502.199 | `c757def2f486bd2a06d7034a0c40b420dceb14989e10d7675d75613116d686d2` |

Os arquivos `.tar.gz` foram extraídos em diretórios privados novos. CRC gzip, conteúdo de todos os arquivos, permissões e execução dos entrypoints foram verificados. O ELF GUI tem SHA-256 `82238e6fd9a987ce22a29f57769d69de09a1427d58644240e3d97ef0e113cacc`; os três entrypoints headless usam `16b8d21cdbd41a711330d8064879bdf756df983b5bc611cca8bd8df775a6a513`.

Todos os 679 arquivos de produção do manifesto Windows/Linux permaneceram iguais antes e após a compilação, os testes e as qualificações. SHA-256 do manifesto: `ca6ab331be823c53f02fb69bf5d050c51cfb7dc2efb6306b711cc033de521e70`. A inspeção do binário vincula 62 módulos e 25 recursos à fonte congelada, com bytecode compilado em otimização 1 e sem substituir módulos da aplicação durante o ensaio.

## Ambiente e resultados

Execução não privilegiada, UID 10001; Python 3.12.3; PyInstaller 6.22.3; Qt/PySide6 6.12.0; kernel WSL2 6.18.33.2; glibc 2.39. O perfil GUI contém Torch 2.14.1+cpu e scikit-learn 1.9.1. CUDA estava indisponível. Dependências dos ambientes anteriores foram consultadas sem modificá-los; o wheel 5.66.0 foi instalado em um ambiente privado novo.

| Verificação | Resultado | Evidência |
|---|---|---|
| Regressão Linux de código-fonte, incluindo ciência, concorrência, SeQUeNCe, RTL, terminal e widgets | **1.560 casos únicos aprovados, 1 skip, 0 falhas/erros finais** | `source-tests-latest-summary.json` e três JUnits vinculados |
| Widgets, layouts compactos, textos extensos e atualizações flutuantes | **111 aprovados**, incluídos nos 1.560 | `visual-regression.xml`, `visual-regression-summary.json`, PNGs `visual-captures/` |
| Entradas científicas no ELF GUI final | **113 verificações aprovadas**, `frozen=true` | `release/linux/5.66.0-native-gui-20261011/evidence/scientific-input-validation.json` |
| Autoteste do ELF GUI, incluindo tecnologias, cadastros, recibos, scroll e gráficos | **39 verificações aprovadas**, Qt offscreen | `evidence/gui-self-test-checks.json` |
| Inicialização, oito abas, compactação, ultrawide, maximizar/restaurar e terminal | **15 aprovadas por execução** em offscreen, Wayland padrão, Wayland explícito e XWayland | `gui-startup.json`, `gui-default-display.json`, `gui-wayland-startup.json`, `gui-xwayland-startup.json` |
| TF-QKD, timeline Google, onda CIMATEC, FPGA e exportação de evidências no ELF | **6 contratos funcionais aprovados** em XWayland | `gui-ui-pipeline-xwayland.json`; restrição dos pixels descrita abaixo |
| Operador real em PTY/curses ligado ao core por TLS 1.3/mTLS | **185 verificações aprovadas**, 1.273 teclas exercitadas | `release/linux/5.66.0-native-headless-20261011/evidence/native-terminal-campaign.json` |
| Terminal EVA, paleta, mensagem, desconexão e permissões inalteradas | **77 verificações aprovadas** no retry com os mesmos binários | `native-eva-campaign-retry-1.json` e proveniência |
| GUI conectada ao core nativo por mTLS | **Aprovado**; tick 1 → 13 durante GUI → 14 após GUI | `gui-remote-smoke.json` |
| Recuperação dos arquivos distribuídos e vinculação da fonte | **Aprovado** nos dois perfis | `linux-release-qualification.json`, `archive-*-smoke.json`, `gui-frozen-source-linux.json` |

Os totais acima pertencem a escopos diferentes; verificações internas dos executáveis não foram somadas ao número de casos pytest. O único skip final é um hash exato de dataset gravado no ambiente Windows, dependente de libm/BLAS/Torch. As regressões numéricas de canal, Aer/Kraus e validação de entradas em Python normal, `-O` e `-OO` passaram no Linux.

A topologia fresca da campanha terminal contém **76 nodes, 136 enlaces e 11 satélites**. O backend continuou avançando sem autoridade de operador: a revogação do certificado do cliente produziu HTTP 401, uma identidade nova recebeu ticks 20 → 22, e o mesmo processo de core permaneceu vivo. Leitura anônima foi rejeitada. Não houve passos de simulação executados pela apresentação. Backpressure manteve uma leitura em voo, uma publicação pendente, 128 registros de observação e dois snapshots retidos.

O layout textual da campanha teve p95 de **2,00 ms** para 57 renderizações; a leitura da API teve p95 de **1.500 ms** durante essa execução sob carga. São custos distintos. No ensaio inicial de navegação WSLg/Wayland em monitor **3440×1440, DPR 1**, o heartbeat p95 ficou em **26,09 ms**, máximo **497,56 ms**, incluindo abertura e reconstrução de layouts. Esses números não estabelecem garantia de tempo real nem substituem os benchmarks de responsividade da entrega Windows.

## Dependências de exibição e limite das capturas

O executável abre e navega no WSLg usando Wayland padrão, sem forçar `QT_QPA_PLATFORM`. O precheck inicial de `ldd` sobre plugins fora do bootloader deixou de resolver algumas bibliotecas Qt já empacotadas; o teste posterior do ELF real e o precheck com o caminho equivalente ao bundle comprovaram o funcionamento. Esse diagnóstico anterior foi preservado.

O perfil X11 depende também de bibliotecas do sistema. Neste Ubuntu mínimo faltavam os nove pacotes abaixo. Para qualificação, DEBs oficiais foram extraídos em uma pasta privada e fornecidos via `LD_LIBRARY_PATH`; tamanho e SHA-256 foram confrontados com os metadados Ubuntu. Nada foi instalado no host, nenhum script de mantenedor foi executado e essas bibliotecas não foram acrescentadas à distribuição OrionQ.

| Dependência externa X11 | Versão verificada |
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

As dependências, URLs oficiais e hashes estão em `x11-private-dependencies.json`. A instalação de desktop deve satisfazer os [requisitos Linux/X11 do Qt](https://doc.qt.io/qt-6/linux-requirements.html); esta entrega não declara independência das bibliotecas do sistema operacional.

**Captura nativa WSLg não qualificada.** No modo Wayland, `QScreen.grabWindow` retornou captura nula em cinco casos. Em XWayland, retornou seis imagens não nulas, porém todas são pretas, idênticas e têm 3096×1296 pixels. A inspeção de pixels e a revisão visual registram isso em `gui-ui-pipeline-xwayland-pixel-inspection.json`. Os seis contratos funcionais passaram, mas esses PNGs não comprovam pintura ou ausência de cortes. A API de captura é a [API nativa do Qt](https://doc.qt.io/qt-6/qscreen.html#grabWindow), sem fallback que repinta o widget para fabricar uma evidência.

Um ensaio adicional com Xvfb oficial extraído de forma privada terminou antes de executar o OrionQ: o pacote exige `/usr/bin/xkbcomp`, ausente no host mínimo. `-xkbdir` e `-pn` não mudaram esse caminho compilado; XKEYBOARD não pôde ser desativado. O host e o binário do servidor foram preservados. O impedimento está em `gui-xvfb-diagnostic.json`. As capturas Qt offscreen dos widgets e do autoteste são evidências de software válidas e separadas; a pintura nativa WSLg e o arraste físico de bordas seguem pendentes de uma fixture de captura apropriada.

## Tentativas anteriores e limites

- Um teste de reinício do gateway tentou usar socket AF_UNIX com caminho de 111 bytes. O mesmo caso, sem modificar fontes, passou com diretório temporário curto e caminho de 82 bytes. O JUnit inicial permanece disponível e a consolidação deduplica o caso pelo nome completo.
- A primeira campanha nativa EVA falhou no aquecimento com `CoreRequestError`, sob execução concorrente de builds/testes. O status HTTP e a causa não foram registrados naquela tentativa; não se atribui causalidade a CPU, disco ou backend. A repetição em estado privado curto no ext4, com os mesmos binários, aprovou 77 verificações sem relaxar os critérios.
- O relatório headless capturou uma versão anterior do script de build que continha uma observação genérica sobre corridas legadas. O script/hash anterior foi preservado em `evidence/build-script.py`; a qualificação de concorrência desta versão vem das novas regressões entre processos, não dessa frase antiga.
- Não foram qualificados GPU Linux, desktop Linux independente, ARM/PetaLinux BSP, FPGA AXI/DMA físico, Swabian físico, recepção óptica/RF real de satélites, calibração elétrica ou interlocks. O gateway CPU/emulado não qualifica acelerador físico.

Não foram exportados bancos de dados privados, chaves, certificados privados ou buffers de material secreto. Modelos continuam opcionais; atuação física permanece bloqueada.

## Executar e reproduzir

Em Linux x86_64 compatível, extraia cada perfil em uma pasta própria:

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

O relatório científico usa nome novo para evitar sobrescrever evidências. Configuração de identidade de serviço, permissões, mTLS e operação em terminal está documentada em [`orionq_linux_service.md`](../product/orionq_linux_service.md), [`orionq_terminal_operator.md`](../product/orionq_terminal_operator.md) e [`orionq_pspl_linux.md`](../product/orionq_pspl_linux.md).

As ferramentas versionadas `tools/build_linux_native.py`, `tools/qualify_linux_native_release.py` e `tools/run_linux_native_eva.py` incluem a versão canônica, a validação científica obrigatória do ELF e validações dos contratos de empacotamento. Todos os resultados públicos desta campanha ficam em `outputs/release-5.66.0-20261011/linux/`, e as evidências diretamente produzidas pelos builds acompanham cada perfil em `release/linux/`.
