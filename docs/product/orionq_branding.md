# OrionQ: marca e versão da distribuição

A partir de 5.57.0, o nome público do aplicativo, a janela, os diálogos, o executável e a distribuição portátil são **OrionQ**. A tela inicial e o cabeçalho usam `OrionQWordmark`: letras vetoriais “Orion” seguidas de um Q que representa uma esfera de Bloch com satélite. O mesmo `paint_bloch_q` gera o ícone de Windows e da janela, incluindo tamanhos de 16 a 256 pixels. O desenho é nativo Qt, sem serviços de imagens, animações ou processamento por tick.

A tela de entrada apresenta **Sponsored by SENAI CIMATEC - LAQCC/QuIIN** imediatamente abaixo de **Open workspace**, junto da versão do programa. O texto utiliza quebra de linha, preservando a leitura em telas menores. As telas inicial e de carregamento apresentam a versão; a descrição de build enviada pela fonte de dados permanece separada.

## Fontes distribuídas após a manutenção de 2026-10-12

A marca atual usa Inter e o Q vetorial. A fonte Quantum não era carregada pela
interface e foi removida dos recursos, manifesto e seleção de pacote atuais,
junto de seu aviso separado. As famílias remanescentes conservam os respectivos
avisos SIL OFL. O verificador de novos executáveis recusa a presença dos arquivos
excluídos; versões e arquivos históricos não foram modificados. A evidência de
empacotamento e seus limites estão na [verificação de assets](../results/orionq_asset_licensing_20261012.md).

## Marca inicial e variante local

As fontes 5.58.0 restauraram a marca da última versão aceita do executável: letras “Orion” em Inter, na cor de texto do tema, e Q laranja com esfera de Bloch e satélite. Na versão **5.60.0**, o acionamento visual exige **S. Ikari** e a senha exata **thirdpilot**. O nome ignora maiúsculas/minúsculas e espaços nas bordas; a senha diferencia ambos. A variante troca o Q por vermelho `#E53935`; sua cauda tem base larga junto à esfera, afina para fora seguindo a diagonal e termina em uma cruz de braços afilados no lugar do satélite. As marcas da interface, suas superfícies filhas e o ícone da janela acompanham essa variante em memória enquanto o modo E.V.A. está ativo. O gatilho não autentica o nome visual nem amplia permissões; conserva a origem de dados e a identidade do backend. [Fluxo de entrada e sequências visuais](orionq_entry_ui.md).

Desde **5.59.0**, **Unplug from E.V.A.** encerra esse modo com hexágonos vermelhos, a faixa **Disconnected from E.V.A.**, o apagamento da geometria e um quadro totalmente preto mantido por pelo menos 180 ms após pintura. A sequência nativa dura aproximadamente 3,6 segundos e usa Inter, fontes existentes e contornos vetoriais em cache. Ao retornar à entrada, restaura o Q laranja com satélite e o ícone original da janela, limpa o marcador visual e solicita nova confirmação de login. A escolha da origem, o principal do backend, as permissões, a aquisição e operações duráveis continuam preservados. Uma nova execução também começa com o desenho original.

Desde **5.60.0**, o desligamento inclui um som original descendente de 3,2 segundos, gerado localmente com a biblioteca padrão Python e carregado de forma assíncrona por Qt. Uma reprodução é solicitada por ciclo; concluir, cancelar, ocultar ou descartar impede áudio tardio e libera os recursos. Sem áudio disponível, o login retorna normalmente. Na GEO, o mar fica vermelho durante o modo visual. Os ícones, enlaces, áreas de seleção e destaques preservam sua semântica; órbitas, enlaces e cadastros permanecem controlados pelo backend. O desenho normal retorna ao desligar.

Na manutenção **5.60.1**, a base da cruz acompanha a mesma referência geográfica, **longitude 0° e latitude 55° N**, no Mapa e no Globo. Sua projeção acompanha pan, câmera e zoom; o lado oculto da Terra não apresenta a cruz, e geometria inteiramente fora do enquadramento não é pintada. A forma é mais alongada, com largura/altura de **0,72**. A base foi reduzida de 28% para **10% da largura**, com leve alargamento em relação ao corpo e pontas afiladas. Um único preenchimento com gradiente horizontal produz o brilho interior quente `#FF8B78` sobre `#F02D40`, mantendo **25% de opacidade** e sem halo, widgets, strobe ou timers adicionais. O desenho original vetorial segue a referência visual do usuário, sem assets da obra. A referência de escala de 2.548.400 m e o piso de 54 pixels no globo descrevem exclusivamente a decoração, sem representar um objeto físico. Consulte a [validação e reprodução de 5.60.1](../results/orionq_geo_anchor_20261009.md).

## Distribuição 5.60.1

A distribuição da [rodada 5.60.1-native-20261009-061920](../../release/windows/5.60.1-native-20261009-061920/) está **aprovada nas verificações automáticas locais**. O [consolidado final](../../outputs/orionq_build_5_60_1_20261009/frozen-release-final.json) registra `passed: true`, **20 gates obrigatórios e 20 aprovados**, sem erros. A [aceitação nativa](../../release/windows/5.60.1-native-20261009-061920/native-acceptance/acceptance-report.json) registra `automatic_passed: true`, mantendo `manual_review: pending` e `acceptance_status: manual_review_required`.

| Artefato | SHA-256 |
| --- | --- |
| [OrionQ.exe](../../release/windows/5.60.1-native-20261009-061920/OrionQ/OrionQ.exe) | `fd1537c637aa7bbde4f63584854de4c211e6cd6dfa7f3cd68c0c1774e56866c1` |
| [OrionQ-5.60.1-native-windows-x64.zip](../../release/windows/5.60.1-native-20261009-061920/OrionQ-5.60.1-native-windows-x64.zip) | `3c0aab4a5fa2450d372e396d3bb70dec83c0e5e745394e07b3c6fea40fcee4d2` |

A qualificação conferiu versão declarada, metadados distribuídos, interface e recurso PE; diagnósticos de marca, comportamento Windows, startup, campanhas, laboratório, rede e demais gates do build; e a vinculação dos relatórios à distribuição. O [manifesto do pacote](../../release/windows/5.60.1-native-20261009-061920/portable-package.json) registra **310.101.637 bytes**, **4.021 arquivos no ZIP** e CRC verificado. O manifesto interno contabiliza 4.020 arquivos, além do próprio manifesto. O guia de aceitação Windows, incluído no pacote em `docs/orion_q_windows_acceptance.md`, descreve o prazo finito de 3.600 segundos para cada autoteste ou estresse da aceitação; os demais diagnósticos têm prazos próprios registrados no consolidado. Os autotestes congelados aprovam 39 critérios com **Qt offscreen**, separadamente das capturas de fonte com **Qt Windows**.

A revisão manual, a execução em outra máquina, a entrada física por mouse, a audibilidade no equipamento do usuário e a qualificação física/óptica permanecem pendentes. O runtime do roadmap excedeu o orçamento de 10 ms em **20 de 20 amostras**; aprovação funcional não demonstra ganho de desempenho nem garantia de p99. Os documentos deste checkout foram atualizados após a consolidação; os documentos incluídos no ZIP preservam o snapshot anterior à aceitação final. O ZIP e seu manifesto não foram alterados por esta atualização documental.

A [evidência de fonte 5.60.1](../results/orionq_geo_anchor_20261009.md) registra **311 regressões únicas aprovadas**, incluindo os **43 testes GEO** da revisão final com brilho quente e proporção 0,72. O diagnóstico com o plugin **Qt Windows** aprovou **28 casos GEO** e gerou 38 PNGs de componentes isolados; a entrada nativa aprovou quatro casos. As capturas GEO usam `QWidget.grab` de canvases lógicos 1280×720/3440×1440, DPR 1, em host 960×640 e fixture artificial. Não são screenshots do console completo nem uma execução congelada. Capturas anteriores com Qt offscreen permanecem separadas; a aprovação local do executável está documentada nos relatórios próprios da distribuição.

## Evidência anterior: 5.60.0

A [evidência de 5.60.0](../results/orionq_signal_audio_geo_20261008.md) registra 309 testes únicos de fonte e, separadamente, 15 critérios do diagnóstico nativo de áudio com dois ciclos e reinício/cancelamento. `audibility_verified: false` distingue os sinais Qt de uma verificação humana ou medição acústica. Esses resultados anteriores não qualificam a nova ancoragem geográfica ou o novo executável.

## Evidência anterior: 5.59.0

A [validação de 5.59.0](../results/orionq_unplug_ui_20261008.md) registra 190 testes de fonte, quatro cenários nativos do fluxo e 16 cenários da marca, seguidos pela qualificação congelada aprovada da rodada anterior. Essa evidência histórica conserva a restauração da variante original após desligamento, sem o novo som ou marcador GEO.

## Evidência anterior: 5.58.0

A [validação de 5.58.0](../results/orionq_entry_ui_20261008.md) inclui a geometria revisada do Q/cruz, capturas nativas nos dois temas e 276 casos exclusivos aprovados. Esses resultados pertencem à versão anterior.

## Uma versão oficial

[pyproject.toml](../../pyproject.toml) declara a versão da distribuição `quantum-twin`. [product.py](../../src/quantum_autopilot/product.py) fornece `PRODUCT_NAME`, `EXECUTABLE_NAME`, `PACKAGE_DIRECTORY` e `product_version()`.

Em um checkout, a interface lê a versão declarada no próprio repositório. Na instalação Python e no executável congelado, lê os metadados da distribuição incluídos no pacote. Não consulta o diretório de trabalho nem o build declarado por telemetria. Um executável sem esses metadados falha explicitamente, evitando apresentar uma versão incorreta.

Ao preparar uma nova versão:

1. Atualize `[project].version` em `pyproject.toml`.
2. Atualize a instalação de build com `python -m pip install --no-deps --no-build-isolation -e .` ou execute o build normal, que instala o projeto. Para congelamento manual, regenere também os metadados do checkout com `python -c "from setuptools import setup; setup()" egg_info`; o build normal e o CI fazem essa etapa automaticamente.
3. Execute `powershell -ExecutionPolicy Bypass -File tools\build_windows_console.ps1`.

A interface, `OrionQ.exe --version`, os metadados de versão do Windows e o instalador recebem a nova versão automaticamente. A spec rejeita metadados descobertos que não correspondam à declaração do projeto. Isso inclui um `src/quantum_twin.egg-info` antigo que preceda a instalação quando `PYTHONPATH=src` está ativo. O build regenera os metadados e os ícones, executa os diagnósticos e cria uma nova pasta; versões publicadas anteriormente não são sobrescritas.

## Compatibilidade

O diretório de dados `%LOCALAPPDATA%\ORION-Q`, o nome de organização do Qt, o AppUserModelID `ORION-Q.Desktop`, os mutexes, as variáveis de ambiente, os contratos de dados e os identificadores dos protocolos conservam seus nomes. Isso preserva cadastros, journals, configurações, permissões e a exclusão entre versões antigas e novas. O comando instalado `orionq` é adicionado; `orion-q` continua disponível.

A nova distribuição contém `OrionQ\OrionQ.exe`, `VALIDATE-OrionQ.cmd` e o alias de validação anterior. A detecção de janelas reconhece nomes e títulos antigos e novos. Binários anteriores à implementação do mutex continuam sujeitos às limitações documentadas na [identidade e instância única](orion_q_desktop_identity.md).

## Verificação reproduzível

```powershell
python -m pytest tests\test_product_identity.py tests\test_console_branding.py tests\test_console_product_entry.py
OrionQ.exe --version
OrionQ.exe --branding-test caminho\novo\branding-validation
python tools\generate_disconnect_sound.py
python tools\validate_console_disconnect_audio.py --output-dir outputs\disconnect-audio-check
```

O diagnóstico verifica a marca, o patrocínio, a versão da distribuição, a separação da origem dos dados e o enquadramento das ações nos dois temas e em diferentes resoluções. As capturas e o relatório permanecem no diretório isolado solicitado; não utiliza journals, autenticação ou equipamentos do operador.
