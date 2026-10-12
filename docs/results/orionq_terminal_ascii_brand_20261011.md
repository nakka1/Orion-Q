# OrionQ — logos ASCII na TerminalUI

Data: 11 de outubro de 2026. Escopo: apresentação no terminal; contratos,
processamento científico, autenticação e releases anteriores preservados.

## Implementação

As artes fornecidas pelo autor são dados imutáveis em
`src/quantum_autopilot/service/terminal_brand_art.py`. As versões textuais
preservam caracteres e espaçamento interno. As variantes disponíveis apenas
como imagem foram recuperadas pela ocupação da grade regular de caracteres;
isso não equivale à recuperação dos bytes originais desses arquivos ASCII.
Fontes, hashes e hipóteses dessa conversão estão em
[provenance.json](../../outputs/terminal-ascii-brand-20261011/provenance.json).

| Variante | Dimensões da arte, sem margens vazias | Origem |
|---|---:|---|
| Q30 | 27 × 14 | Texto ASCII enviado |
| Q50 | 46 × 23 | Grade do PNG enviado |
| Q60 | 56 × 27 | Texto ASCII enviado |
| Q90 | 83 × 42 | Grade do PNG enviado |
| Q110 | 103 × 52 | Arquivos de texto anexados, idênticos |
| Q160 | 149 × 74 | Grade do PNG chamado `ascii-art110.png`, com 160 colunas |
| WORDMARK110 | 105 × 15 | Grade do PNG com nome completo |
| WORDMARK160 | 156 × 21 | Texto ASCII do nome completo enviado |

Espera por dados e ajuda escolhem a maior arte completa que cabe após as
mensagens e instruções. A escolha considera os dois eixos e não escala,
corta ou interpola caracteres. O nome completo usa letras claras e Q coral;
artes do Q usam coral. Codificações sem os glifos necessários usam `#` no
lugar de blocos `█`; `NO_COLOR` conserva a arte sem a paleta. Nesse modo,
Unicode recebido nos textos é representado por escapes como `\xb7` e
`\u6c34`, antes de aplicar limites de largura. Os objetos admitidos e a
apresentação UTF-8 permanecem inalterados.

O cabeçalho mantém identidade, estado e navegação em três linhas. A chegada
de um snapshot admitido remove a arte de espera e mostra os registros do
backend. A ajuda não substitui nem modifica esses registros. Fechá-la e
redimensionar preservam a seleção; pinturas diferenciais apagam a arte
anterior. Os layouts `dashboard` e `plain` compartilham os mesmos assets e
descrevem cores por atributos separados do texto, sem ANSI nos relatórios.

## Validação

Suíte consolidada final: **612 aprovados e um ignorado**, em 87,09 s. Inclui 145
casos novos de branding e regressões de gráficos, pintura, navegação,
backpressure e CLI autenticada. O caso ignorado exige PTY POSIX no executor
Windows; a campanha Linux separada exercita curses em PTY real.
[JUnit consolidado](../../outputs/terminal-ascii-brand-20261011/tests-consolidated-v3.xml).
Ruff aprovado nos quatro módulos de produção, testes alterados e ferramentas
novas de qualificação.

Os 145 casos vinculam as oito artes por hashes dos caracteres completos,
verificam encaixe exato e falta de uma célula em cada eixo, spans de cor,
codificação ASCII/CP437, preservação dos registros nas oito visões,
pausa/estado stale e limpeza de todas as células ao receber uma publicação
ou fechar ajuda. As referências antigas do microglifo foram substituídas
por verificações do cabeçalho intacto; os contratos científicos e de API
continuam cobertos pelas regressões existentes.

Foi reproduzida uma incompatibilidade de saída ASCII estrita: os separadores
U+00B7 do rótulo da fonte recebido pelo backend podiam gerar caracteres
inválidos em curses e `UnicodeEncodeError` no relatório finito. Seis
regressões falharam antes da correção de apresentação e passaram depois.
[Baseline de codificação](../../outputs/terminal-ascii-brand-20261011/ascii-encoding-baseline.xml),
[145 casos específicos aprovados](../../outputs/terminal-ascii-brand-20261011/ascii-brand-tests-v4.xml).
Não houve transliteração, alteração de registros ou modificação de parâmetros
científicos para satisfazer os testes.

A campanha final em Ubuntu 24.04/Python 3.12.3, via WSL e conta sem
privilégios, terminou com **61/61 verificações aprovadas**. Foram três
sessões curses em PTY real, com saídas zero e restauração do `termios`.
O cenário exercitou 76 nodes, 136 enlaces e 11 satélites. As dimensões
incluíram 120 × 40, 200 × 60, 200 × 90, 80 × 24, 65 × 18 e 200 × 40 células.
[Relatório de qualificação](../../outputs/terminal-ascii-brand-20261011/campaign-v5/report.json),
[evidências da campanha](../../outputs/terminal-ascii-brand-20261011/campaign-v5/terminal-campaign.json).

| Verificação em Linux | Resultado |
|---|---|
| Espera e ajuda em diferentes dimensões | Retângulos completos e spans de cor conferidos; instruções preservadas |
| Chegada de dados e fechamento da ajuda | Todas as células da arte anterior removidas |
| Seleção, detalhes, pausa e redimensionamento | Seleção e registros do backend preservados |
| `NO_COLOR` | Todas as células conservam cores padrão do terminal |
| Ambiente ASCII estrito | Blocos substituídos por `#`; rótulo Unicode recebido exibido por escapes exatos |
| Relatório finito sem interação | Saída zero; 6.814 bytes ASCII; stderr vazio; 3,752 s com prazo de leitura de 15 s |

Das 13 teclas exercitadas, dez interações tiveram latência observável de
tecla até a condição visual: p50 **35,469 ms**, p95/máximo **49,557 ms**.
Essas medições qualificam as operações desse ensaio, não são uma comparação
antes/depois nem um benchmark geral de CPU, GPU ou desempenho do controlador.
A apresentação não executou passos da engine.

A wheel privada de validação foi vinculada ao código congelado por hashes:
448 módulos e 459 arquivos empacotados/instalados conferidos. Seu SHA-256 é
`31c2933f50b989d91dbd10c7fe0226aae2a3ff0ee2bbf9e22fd53b0a34cf3a3d`.
Não houve exportação de credenciais ou logs privados. A reconstrução das
prévias também possui
[manifesto de integridade](../../outputs/terminal-ascii-brand-20261011/previews-final/preview-integrity.json).

Tentativas anteriores reprovadas foram preservadas. Em particular, a v4
retornou `STALE` na primeira consulta finita com prazo de leitura de 2 s,
sem registrar a causa subjacente. O mesmo perfil passou isoladamente com
2 s; a campanha final utilizou 15 s nesse caso e conservou 2 s nos PTYs
interativos. Portanto, a primeira publicação com prazo de 2 s ainda não
está qualificada de forma confiável nesse cenário. O tempo de 3,752 s
abrange todo o subprocesso, não apenas a consulta à API, e não comprova
que a falha anterior tenha sido um timeout.

## Reprodução e limites

O manual de operação está em
[orionq_terminal_operator.md](../product/orionq_terminal_operator.md).
Em uma sessão autenticada, `h` abre a ajuda e `v` alterna os layouts;
redimensionar o terminal seleciona automaticamente uma versão que cabe.
Terminais pequenos omitem a arte para preservar as informações.

Para repetir as regressões no ambiente de desenvolvimento:

```bash
python -m pytest tests/test_terminal_ascii_brand.py tests/test_terminal_time_chart.py \
  tests/test_terminal_dashboard.py tests/test_terminal_dashboard_paint.py \
  tests/test_terminal_live.py tests/test_core_operator_cli.py -q
```

Para repetir a qualificação Linux, use Python sem otimização e uma conta sem
privilégios. O destino de evidência deve ser novo. O runner cria uma fixture
privada com credenciais de teste, instala uma wheel de validação e exporta
somente projeções de evidência autorizadas:

```bash
python -I tools/validate_terminal_ascii_brand_linux.py \
  --repository /caminho/OrionQ --output /caminho/evidencias-logo-novas
```

A integração não altera a logo vetorial da GUI nem recompila os releases
selados. Não requer `rich`, `plotext`, Qt, os PNGs originais ou arquivos da
pasta Downloads em execução. A aparência dos caracteres depende da fonte
e da paleta do terminal. As prévias são reconstruções das células e cores
capturadas em PTY, com identidade de teste ocultada; não são capturas
nativas da janela do terminal nem qualificam instrumentação física.

- [Nome completo em cores, espera em 200 × 60](../../outputs/terminal-ascii-brand-20261011/previews-final/01-brand-loading-200x60.png).
- [Q160 completo, ajuda em 200 × 90](../../outputs/terminal-ascii-brand-20261011/previews-final/05-brand-help-200x90.png).
- [Arte sem cores](../../outputs/terminal-ascii-brand-20261011/previews-final/06-brand-no-color-200x60.png).
- [Compatibilidade ASCII](../../outputs/terminal-ascii-brand-20261011/previews-final/07-brand-ascii-200x40.png).
