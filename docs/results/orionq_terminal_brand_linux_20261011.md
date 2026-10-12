# OrionQ 5.64.2 — logo colorida no terminal Linux

A interface curses apresenta o nome OrionQ e uma adaptação textual do Q com esfera de Bloch, meridiano, equador, vetor de estado, cauda diagonal e satélite. O Q e o símbolo usam o coral da identidade vigente: índice 173 na paleta xterm-256, próximo do `#d97757` usado na interface gráfica. Em terminais básicos, utiliza a cor vermelha disponível. `NO_COLOR`, `TERM=dumb`, ausência de cores ou falha na inicialização mantêm a marca legível sem cor. Não se redefine a paleta do terminal.

A marca utiliza somente o espaço livre dos três headers, preservando identidade, status, atalhos, linhas de dados, seleção e scroll. Se a versão completa não couber, aparece uma versão compacta na primeira linha. Quando não há espaço ou a altura é menor que oito linhas, a decoração é omitida. Caracteres largos e combinantes são contabilizados em células de terminal. Os textos exportados continuam sem ANSI; a cor é aplicada separadamente pelo curses.

O fallback ASCII aplica-se **à logo** quando a codificação de saída não comporta seus glifos Unicode. Os gráficos e dados científicos preexistentes continuam podendo conter Unicode; esta alteração não promete converter toda a interface para ASCII. Não foram acrescentadas dependências gráficas, acesso à GPU, animações, threads ou consultas de backend para desenhar a marca.

## Validação

Pacote headless instalado em um ambiente Linux novo, sobre filesystem nativo, executado como UID 10001. As versões das dependências foram reutilizadas do ambiente headless previamente validado por um arquivo `.pth`; o pacote OrionQ 5.64.2 instalado nesse novo ambiente teve precedência e sua origem foi verificada. A instalação anterior 5.64.1 foi preservada.

- **68 testes do monitor:** zero falhas, erros ou skips. Incluem 26 novos casos de marca, layout, células Unicode, spans, paletas e `NO_COLOR`.
- **155 verificações na campanha curses/PTY:** todas aprovadas, incluindo oito visões, seleção/detalhes dos satélites, resize, pausa, scroll, indisponibilidade/reconexão e saída. Esta rodada usa Mixed/Reservations sem a fixture preenchida da campanha anterior; não substitui aquela evidência de onze nodes mistos.
- As duas capturas de preview verificam a cor real de cada uma das três linhas da marca no buffer `pyte` do processo curses. Não se aplicou cor fictícia aos textos depois da execução.
- Serviço independente por HTTPS/mTLS, dados explicitamente simulados, zero atuação física, auditoria validada e nenhum segredo encontrado na saída pública.
- Ruff aprovado para os módulos, testes e ferramentas alterados.

As únicas diferenças entre os 517 arquivos congelados da validação anterior e os arquivos correspondentes atuais são `terminal_live.py`, `test_terminal_live.py` e a versão em `pyproject.toml`. O módulo `terminal_brand.py` é novo. O probe PTY foi ampliado para registrar atributos de cor dos previews. Nenhum mecanismo de roteamento, reserva, aquisição ou consumo de chaves foi alterado nesta entrega.

## Imagens e pacote

As imagens são renderizações dos **viewports textuais reais do PTY Linux**, com as cores capturadas pelo curses. Foi utilizada DejaVu Sans Mono da instalação local Linux; o fundo padrão escuro foi escolhido para o preview. Somente linhas vazias no final foram recortadas. A fonte e o fundo efetivos dependem da configuração do terminal do usuário.

- [Visão geral](../../outputs/terminal_brand_linux_20261011/previews/overview.png)
- [Satélites](../../outputs/terminal_brand_linux_20261011/previews/satellites.png)
- [Integridade dos previews](../../outputs/terminal_brand_linux_20261011/previews/preview-integrity.json)
- [Resultados do pacote e dos testes](../../outputs/terminal_brand_linux_20261011/report.json)
- [Campanha e atributos dos viewports](../../outputs/terminal_brand_linux_20261011/terminal-campaign.json)
- [Capturas textuais da campanha](../../outputs/terminal_brand_linux_20261011/terminal-campaign.txt)
- [Manifesto de entrega](../../outputs/terminal_brand_linux_20261011/delivery-integrity.json)
- [Wheel Linux headless 5.64.2](../../release/linux/5.64.2/quantum_twin-5.64.2-py3-none-any.whl)

SHA-256 do wheel: `312e88f41a209eb2769d9b2a8a793076bdba908c77233aa3701333acd83ffe03`.

No WSL, instale no ambiente Python usado pelo serviço/operador:

```bash
python -m pip install --upgrade \
  /mnt/e/Codex/2026-08-27/e/work/NISQ-Predictive-Twin/release/linux/5.64.2/quantum_twin-5.64.2-py3-none-any.whl
```

O comando e as credenciais já configurados do `orion-operator watch` permanecem aplicáveis. A marca colorida aparece na apresentação interativa; exports e leituras finitas são texto simples.
