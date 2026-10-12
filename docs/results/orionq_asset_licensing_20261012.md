# OrionQ — fontes e atribuição cartográfica, 2026-10-12

**Fonte e wheel corrigidos; nova distribuição nativa ainda precisa ser reconstruída e validada.**
A revisão preservou os executáveis, ELF, ZIPs e arquivos históricos existentes.

| Caso | Antes | Correção e evidência |
| --- | --- | --- |
| Fonte não utilizada | `Quantum.otf`, 17.556 bytes, seguia no manifesto/pacotes; seu name-table contém “Copyright 2018 Adobe Systems Incorporated. All rights reserved.” | Removidos a fonte e o aviso da árvore ativa. A interface já usa Inter/JetBrains Mono e a marca vetorial, portanto o desenho permanece igual. |
| Empacotamento | OTF incluído por padrão; verificador o exigia | Seleção de recursos atualizada, exclusão explícita, verificador rejeita sua presença. Novo wheel offline não contém fonte nem aviso. |
| Crédito cartográfico | 9 pixels lógicos, cor reduzida e elisão capaz de cortar o crédito OSM | 12 pixels, fundo sólido e contraste próprio; `© OpenStreetMap contributors` permanece completo. Em espaço reduzido, a descrição secundária é compactada e a atribuição sobe acima da escala. |
| Acesso à licença | Crédito pintado sem acesso direto à licença | Clique abre a página oficial OSM; tooltip preserva a descrição completa. Regressão confirma ausência de pan ou seleção acidental. |
| Dados cartográficos | Suspeita de texto corrompido no mapa-múndi | Os JSONs atuais de fonte e dos dois bundles examinados já decodificavam corretamente. Seus bytes/geometria foram preservados; o defeito residual reproduzível era a apresentação pequena/cortada. |

As [diretrizes oficiais OSMF](https://osmfoundation.org/wiki/Licence/Attribution_Guidelines)
exigem crédito legível e acesso à informação de origem/licença. A
[página oficial OpenStreetMap](https://www.openstreetmap.org/copyright) explica
ODbL e a atribuição. Consultadas em 2026-10-12; diretrizes atualizadas em 2026-09-10.
Natural Earth fornece os polígonos mundiais; o crédito OSM identifica os detalhes de campus.

## Validação

- **86 casos distintos aprovados**, incluindo marca, GEO, alternância de renderizadores,
  duas paletas, larguras de 240/320/640/1200 pixels e interação com a atribuição.
- Wheel isolado `quantum_twin-5.66.1-py3-none-any.whl`: **11.500.889 bytes**,
  SHA-256 `9458737f7cc58b994ffa4e765c0cfbf4c4bac6c05d38601a120815b4933fe086`.
- JSONs mundiais das duas camadas permanecem byte a byte iguais:
  SHA-256 `c35c176d8fbd485d25da1b8637e029627933431760988c6ea0037a1478f65b7a`.
- [Consolidado JSON](../../outputs/defect-corrections-20261012/asset-licensing-qualification.json),
  [inventário anterior](../../outputs/defect-corrections-20261012/asset-inventory-before.json)
  e [validação do wheel](../../outputs/defect-corrections-20261012/asset-package-validation.json).
- [Preview escuro](../../outputs/defect-corrections-20261012/osm-attribution-dark-1200.png),
  [claro](../../outputs/defect-corrections-20261012/osm-attribution-light-1200.png)
  e [viewport estreito](../../outputs/defect-corrections-20261012/osm-attribution-dark-320.png).
  São renderizadores isolados, sem tráfego de rede; a captura comprova a apresentação do crédito.

```powershell
python -m pytest tests/test_console_asset_compliance.py tests/test_console_branding.py tests/test_branding_validation.py tests/test_console_geo_modes.py -q
```

## Próxima distribuição nativa

Reconstruir em **nova pasta** com `tools/build_windows_console.ps1` e
`tools/build_linux_native.py`, qualificar os novos pacotes e só então ativá-los.
O gate `excluded_resources_absent` de `tools/verify_frozen_source.py` impede a
fonte excluída em novas distribuições Windows. A verificação também inclui a
camada `geography`, efetivamente utilizada pela interface, e os loaders de fontes.

Os bundles ativos anteriores não foram alterados: conservam sua fonte não utilizada
e seu código anterior de apresentação até a substituição por uma nova release.
Esta verificação não equivale a uma auditoria completa de todas as licenças do projeto,
qualificação de hardware ou demonstração de tráfego satelital.
