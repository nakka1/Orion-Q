# OrionQ: marca e versão da distribuição

A partir de 5.57.0, o nome público do aplicativo, a janela, os diálogos, o executável e a distribuição portátil são **OrionQ**. A tela inicial e o cabeçalho usam `OrionQWordmark`: letras vetoriais “Orion” seguidas de um Q que representa uma esfera de Bloch com satélite. O mesmo `paint_bloch_q` gera o ícone de Windows e da janela, incluindo tamanhos de 16 a 256 pixels. O desenho é nativo Qt, sem serviços de imagens, animações ou processamento por tick.

A tela de entrada apresenta **Sponsored by SENAI CIMATEC - LAQCC/QuIIN** imediatamente abaixo de **Open workspace**, junto da versão do programa. O texto utiliza quebra de linha, preservando a leitura em telas menores. As telas inicial e de carregamento apresentam a versão; a descrição de build enviada pela fonte de dados permanece separada.

## Marca inicial e variante local

As fontes 5.58.0 restauraram a marca da última versão aceita do executável: letras “Orion” em Inter, na cor de texto do tema, e Q laranja com esfera de Bloch e satélite. Na versão **5.60.0**, o acionamento visual exige **S. Ikari** e a senha exata **thirdpilot**. O nome ignora maiúsculas/minúsculas e espaços nas bordas; a senha diferencia ambos. A variante troca o Q por vermelho `#E53935`; sua cauda tem base larga junto à esfera, afina para fora seguindo a diagonal e termina em uma cruz de braços afilados no lugar do satélite. As marcas da interface, suas superfícies filhas e o ícone da janela acompanham essa variante em memória enquanto o modo E.V.A. está ativo. O gatilho não autentica o nome visual nem amplia permissões; conserva a origem de dados e a identidade do backend. [Fluxo de entrada e sequências visuais](orionq_entry_ui.md).

Desde **5.59.0**, **Unplug from E.V.A.** encerra esse modo com hexágonos vermelhos, a faixa **Disconnected from E.V.A.**, o apagamento da geometria e um quadro totalmente preto mantido por pelo menos 180 ms após pintura. A sequência nativa dura aproximadamente 3,6 segundos e usa Inter, fontes existentes e contornos vetoriais em cache. Ao retornar à entrada, restaura o Q laranja com satélite e o ícone original da janela, limpa o marcador visual e solicita nova confirmação de login. A escolha da origem, o principal do backend, as permissões, a aquisição e operações duráveis continuam preservados. Uma nova execução também começa com o desenho original.

Na versão **5.60.0**, o desligamento inclui um som original descendente de 3,2 segundos, gerado localmente com a biblioteca padrão Python e carregado de forma assíncrona por Qt. Uma reprodução é solicitada por ciclo; concluir, cancelar, ocultar ou descartar impede áudio tardio e libera os recursos. Sem áudio disponível, o login retorna normalmente. Na GEO, o mar fica vermelho e uma grande cruz vetorial com opacidade de 25% aparece no topo central do Mapa ou com sua base no norte da esfera do Globo. Os ícones, enlaces, áreas de seleção e destaques preservam sua semântica; órbitas, enlaces e cadastros permanecem controlados pelo backend. O desenho normal retorna ao desligar.

## Distribuição 5.60.0

A compilação usa a [pasta da rodada 5.60.0-native-20261008-233510](../../release/windows/5.60.0-native-20261008-233510/). Os destinos são `OrionQ/OrionQ.exe` e `OrionQ-5.60.0-native-windows-x64.zip` dentro dessa pasta. Quando o relatório dessa rodada estiver disponível, consulte `native-acceptance/acceptance-report.json`: `automatic_passed: true` indica a conclusão aprovada das verificações automáticas. A qualificação repete os gates congelados para esse executável; os guias não antecipam aprovação.

A qualificação exige concordância entre versão declarada, metadados distribuídos, interface e recurso PE; diagnósticos de marca, comportamento Windows, startup, campanhas, laboratório, rede e demais gates do build; e hashes vinculando os relatórios ao mesmo EXE. O empacotamento verifica o manifesto de arquivos e o CRC do ZIP. `portable-package.json` registra os caminhos, hashes, tamanho e contagem de arquivos, e `native-acceptance/acceptance-report.json` precisa registrar `automatic_passed: true`. O guia de aceitação Windows, incluído no pacote em `docs/orion_q_windows_acceptance.md`, descreve o prazo finito de 3.600 segundos por etapa. A disponibilidade do EXE ou ZIP, sozinha, não constitui aprovação.

A [evidência de 5.60.0](../results/orionq_signal_audio_geo_20261008.md) registra 31 testes de fonte do áudio/overlay e 15 critérios do diagnóstico nativo de reprodução, com dois ciclos e reinício/cancelamento. `audibility_verified: false` distingue esses sinais Qt de uma verificação humana ou medição acústica.

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
