# OrionQ 5.57.0 — marca, patrocínio e versão

Entrega validada em Windows em 08/10/2026. O nome público do aplicativo é **OrionQ**. A tela inicial e o cabeçalho usam o Q vetorial com esfera de Bloch, vetor de estado e satélite. O mesmo desenho é usado no ícone do executável e da janela.

A tela inicial apresenta **Sponsored by SENAI CIMATEC - LAQCC/QuIIN** junto de **Open workspace**. A versão vem de `product_version()`, usando `pyproject.toml` em um checkout e os metadados distribuídos no executável. O build informado pela fonte de telemetria permanece separado.

## Distribuição

- [Pacote portátil Windows](../../release/windows/5.57.0-native-20261008-175906/OrionQ-5.57.0-native-windows-x64.zip) — extraia o ZIP inteiro e execute `OrionQ/OrionQ.exe`. Feche a versão anterior antes de abrir a nova.
- [Executável qualificado](../../release/windows/5.57.0-native-20261008-175906/OrionQ/OrionQ.exe). Não copie apenas esse arquivo; ele usa os recursos em `_internal`.
- [Manifesto final](../../outputs/orionq_branding_20261008/final-qualification.json).
- [Guia de marca e atualização de versão](../product/orionq_branding.md).

SHA-256 do executável: `7e52f0956ef4db1824ec7b87dfa30a99f636f79c80c78d767ca08fd40746741f`.

SHA-256 do ZIP: `11ee4a0e27b0f9c5c032aa366a4d970a5d5abf4d2f37f622bee2cee3a1b4c4be`. CRC e 3973 membros conferidos.

## Correções verificadas

O build regenera o `egg-info` do checkout: os metadados antigos indicavam 5.49.0 quando `PYTHONPATH=src` precedia a instalação 5.57.0. O gate exige concordância entre declaração, distribuição, tela e propriedades PE do executável. Os campos PE observados são `{"ProductName": "OrionQ", "OriginalFilename": "OrionQ.exe", "FileVersion": "5.57.0", "ProductVersion": "5.57.0"}`.

Os atalhos externos de versões anteriores são preservados pelo instalador; novos atalhos OrionQ são criados. O empacotador rejeita executáveis legados antes de gravar um ZIP para evitar atribuir a versão atual a um binário antigo.

A comparação nativa do ícone foi corrigida para RGBA com alpha premultiplicado, o formato de apresentação do Windows. A tolerância permanece em dois níveis por canal e o alpha é comparado em todos os pixels. Isso evita confundir quantização das bordas translúcidas com uma cor incorreta; as regressões verificam também cores, cobertura e transparência realmente diferentes.

## Validação

O diagnóstico do executável renderizou **16 layouts** de boot e splash nos dois temas, em 960×480, 1280×720, 1440×900 e 3440×1440. Registrou 24 capturas nativas. Confirmou marca, patrocínio exato, versão, enquadramento, metadados PE e vínculo entre o código carregado e as fontes qualificadas. [Relatório de branding](../../release/windows/5.57.0-native-20261008-175906/branding-validation/branding-report.json).

| Corpus de fonte | Resultado | Evidência |
| --- | --- | --- |
| product-compatibility-final.xml | 29 casos aprovados | [product-compatibility-final.xml](../../outputs/orionq_branding_20261008/product-compatibility-final.xml) |
| vector-branding-final.xml | 21 casos aprovados | [vector-branding-final.xml](../../outputs/orionq_branding_20261008/vector-branding-final.xml) |
| branding-final-r2.xml | 50 casos aprovados | [branding-final-r2.xml](../../outputs/orionq_branding_20261008/branding-final-r2.xml) |
| shell-layout.xml | 72 casos aprovados | [shell-layout.xml](../../outputs/orionq_branding_20261008/shell-layout.xml) |
| evidence-brand.xml | 8 casos aprovados | [evidence-brand.xml](../../outputs/orionq_branding_20261008/evidence-brand.xml) |
| icon-compositing-final.xml | 72 casos aprovados | [icon-compositing-final.xml](../../outputs/orionq_branding_20261008/icon-compositing-final.xml) |
| package-readonly-final.xml | 101 casos aprovados | [package-readonly-final.xml](../../outputs/orionq_branding_20261008/package-readonly-final.xml) |
| installer-scope-final.xml | 9 casos aprovados | [installer-scope-final.xml](../../outputs/orionq_branding_20261008/installer-scope-final.xml) |

Os corpora têm sobreposição; suas contagens não representam uma soma de testes exclusivos.

Todos os **13 grupos do mesmo executável** passaram; a identidade SHA-256 foi verificada antes e depois. [Progresso final](../../release/windows/5.57.0-native-20261008-175906/frozen-progress.json).

| Grupo no executável | Resultado | Duração |
| --- | --- | --- |
| branding | Aprovado | 8.45 s |
| ui-pipeline | Aprovado | 112.78 s |
| desktop-behavior | Aprovado | 15.48 s |
| satellite-mission | Aprovado | 15.38 s |
| satellite-optical | Aprovado | 17.97 s |
| optional-models | Aprovado | 0.72 s |
| roadmap | Aprovado | 7.02 s |
| campaign-worker | Aprovado | 30.62 s |
| self-test | Aprovado | 688.42 s |
| laboratory | Aprovado | 119.88 s |
| global-network | Aprovado | 198.05 s |
| stress | Aprovado | 127.36 s |
| startup | Aprovado | 224.48 s |

O diagnóstico nativo confirmou os oito casos de ícone, identidade de janela e instância única. O fluxo funcional anterior, incluindo observações de protocolos, FPGAs simuladas e exportação de evidências, passou novamente. 1303 entradas de fonte e recursos permaneceram iguais durante a qualificação. O EXE e o ZIP anteriores 5.56.0 conservam seus hashes.

## Compatibilidade e limites

Os dados em `%LOCALAPPDATA%/ORION-Q`, AppUserModelID, mutexes, autenticação, protocolos, modelos opcionais e registros permanecem compatíveis. Os diagnósticos usam estado isolado; não acessam os journals do operador nem acionam equipamentos. A segunda abertura solicita ativar a instância já aberta, inclusive entre nomes antigo e novo.

Esta entrega é portátil. O script do instalador foi validado, mas o instalador não foi compilado neste ambiente sem Inno Setup. A reprodução em uma segunda máquina e a captura dos pixels da barra de tarefas do shell continuam pendentes; o ícone PE, os handles nativos da janela e a identidade Windows foram verificados. Não se atribui qualificação física de hardware ou certificação QKD a esses ensaios de interface.
