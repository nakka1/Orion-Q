# OrionQ — modo visual E.V.A. no terminal Linux

Data: 11 de outubro de 2026. Escopo: apresentação local da TerminalUI,
preservando o núcleo independente e sua autenticação mTLS.

## Uso e comportamento

No `watch` interativo, pressione **L** para abrir `DISPLAY SIGN-IN`.
Informe `S. Ikari`, Enter, `thirdpilot`, Enter. A frase é mascarada;
maiúsculas/minúsculas e espaços externos são aceitos no nome, mas a frase
exige correspondência exata. O gatilho apresenta `EMERGENCY` e
`YOU SHOULD NOT BE ABLE TO CONNECT TO EVA`. Enter ou Esc fecha a mensagem.

O monitor passa a usar os acentos EVA-01: roxo, verde e laranja sobre fundo
escuro. Cores neutras e vermelho continuam disponíveis para distinguir
informação secundária e falhas. As mesmas logos ASCII permanecem em uso;
Orion fica verde e Q roxo. Os gráficos, rótulos e registros preservam sua
origem e seus valores. Não há animação, som ou processamento científico novo.

`Connected to E.V.A 0-1` aparece em uma linha própria, sem substituir `Peer`,
estado da conexão ou fonte de dados. **U — Unplug from E.V.A** remove o modo
visual e restaura a paleta normal. Seleção, detalhes, pausa e snapshots são
preservados. **Q** encerra o cliente. O núcleo continua processando sem
depender desse diálogo ou da presença do operador.

O formulário local não autentica, cria identidades, altera concessões ou
transmite esses campos à API. O serviço continua admitindo as consultas
somente conforme seu certificado e suas permissões. Não há flags de senha,
variável de ambiente de ativação ou persistência desse modo. Os buffers de
entrada são limitados e ocultos na representação do objeto. A frase é limpa
após cada submissão; ambos os campos são limpos no sucesso, no cancelamento,
ao desconectar o modo ou ao encerrar `watch`.

## Implementação

- `terminal_effect.py`: estado de aparência, formulário/aviso, textos de saída
  e paleta sem redefinir as cores globais do terminal.
- `terminal_live.py`: entrada crua no formulário antes dos atalhos `j`/`k`/`v`,
  continuidade do worker de consultas e repintura ao mudar de paleta.
- `terminal_dashboard.py` e `terminal_brand.py`: mesma aparência nos layouts
  dashboard/plain e adaptação a dimensões menores.

O formulário conserva a máscara da frase no texto e nos spans de pintura;
não expõe o valor digitado. `NO_COLOR`, `TERM=dumb` e terminais sem cores
preservam os textos e os atributos padrão. Paletas de oito cores têm
fallback; pares com a mesma cor são reutilizados. ASCII estrito mantém o
desenho completo com `#` quando necessário. Relatórios finitos continuam
sem sequências ANSI e sem gatilho interativo.

## Validação

Regressões finais existentes: **612 aprovadas e uma ignorada**, em 85,99 s.
Incluem branding ASCII, gráficos temporais, dashboard, pintura diferencial,
navegação, worker de leitura e CLI autenticada. O caso ignorado requer
PTY POSIX no executor Windows.
[JUnit final](../../outputs/terminal-eva-20261011/regression-final.xml).
Ruff aprovado nos módulos de produção modificados.

Testes específicos finais: **87 aprovados**, em 1,49 s, com Ruff aprovado.
[JUnit específico final](../../outputs/terminal-eva-mode-20261011/eva-tests-v3.xml).
As duas suítes finais somam **699 testes aprovados e um ignorado**.

| Fluxo | Evidência nos testes específicos |
|---|---|
| Conta e frase | Correspondência do nome conforme o desktop, frase exata, rejeição e nova tentativa |
| Formulário | Máscara, limites, backspace, Tab, cancelamento e limpeza da frase |
| Atalhos | Entrada crua de `j`, `k`, `v`, `p`, `r`, `h` e `q`, sem acionar o monitor |
| Aparência | Aviso, banner, paleta, fallback de oito cores, `NO_COLOR`, ASCII e restauração |
| Estado | Publicações continuam durante o formulário; seleção, pausa, dados e Peer preservados |
| Autorização | Falha TLS permanece fatal; nenhum argumento de senha nem bypass por persona local |
| Transporte do cliente | `CoreClient` com contexto TLS 1.3 e transporte local instrumentado: apenas GETs, sem payload ou campos da persona |
| Encerramento | Buffers e aparência limpos em sucesso, falha do backend, erro de escrita e interrupção |
| Esc | Espera padrão de 100 ms, configuração explícita preservada, API ausente/falha tolerada e chamada única na inicialização |

A instrumentação do transporte nos testes unitários não equivale a uma
conexão de rede real. Essa parte foi exercitada separadamente no ensaio
Linux abaixo. Os releases selados anteriores não foram recompilados.

### Baseline em terminal Linux real

A campanha v1 em Ubuntu 24.04/Python 3.12, via WSL e conta sem privilégios,
terminou com **92/92 verificações aprovadas** em quatro sessões curses/PTY:
cores, `NO_COLOR`, ASCII estrito e interrupção no formulário mascarado.
Foram 50 trocas de entrada, com 145 caracteres de campos exercitados,
sem exportar as entradas brutas. O ensaio PTY levou 17,818 s; esse tempo
não constitui benchmark do controlador.
[Relatório](../../outputs/terminal-eva-20261011/campaign-v1/report.json),
[evidências da campanha](../../outputs/terminal-eva-20261011/campaign-v1/terminal-campaign.json).

| Verificação por PTY/API | Resultado |
|---|---|
| Conta antiga e frase com caixa incorreta | Rejeitadas; frase removida do formulário |
| Gatilho correto e mensagem | Literais conferidos; campos limpos após aceitação |
| Paleta | Verde `#87ff00`, roxo `#af5fff`, laranja `#ff8700` e fundo `#1c1c1c` observados no aviso |
| Seleção e tamanho | Segundo enlace real preservado; 80 × 24, 120 × 40 e 200 × 60 células |
| Alternância de layout | Dashboard/plain conservam Peer, seleção e marcador qualificado como cosmético |
| Unplug | Texto visível, banner removido e cor original do Q/fundo padrão restaurados |
| `NO_COLOR` e ASCII | Cores padrão em todo o viewport; caracteres ASCII válidos |
| Identidade e atividade | Consultas API antes/depois preservaram subject, origem, roles e scopes; tick avançou de 3 para 6 |
| Encerramento | Saídas `[0, 0, 0, 130]`, sendo 130 a interrupção deliberada; `termios` restaurado em todas |

O relógio continuou avançando durante a interação; a origem publicada e a
fronteira `physical_read_only` permaneceram intactas. O leitor independente
do ensaio utilizou prazo de 15 s. O controle de codificação mediu stdout
ASCII/locale `ANSI_X3.4-1968`/UTF-8 mode zero em um processo com a mesma
configuração de lançamento; a aplicação monitorada não foi instrumentada
para relatar internamente sua codificação.

A wheel privada correspondeu a **449 módulos e 460 arquivos** de fonte e
recursos, com SHA-256
`8f524215a20d6d9aec24bcc8c444de83a1f3202d551ea43bd335932050fde97a`.
Nenhum release público foi substituído. O resultado qualifica esse fluxo
visual em Linux/WSL com fixture simulada e mTLS real local; não certifica
equipamentos, operação espacial nem ambientes Linux distintos.

### Interpretação de Esc

A campanha v1 revelou seis cancelamentos com Esc isolado entre 1.028 e
1.174 ms. Um controle no mesmo Python instalado mediu
`curses.get_escdelay() == 1000` e ausência da variável `ESCDELAY`.
[Baseline vinculado à campanha](../../outputs/terminal-eva-20261011/escape-delay-baseline.json).
Esses tempos medem entrada até a condição visual verificada; não isolam
pintura nem constituem latência de processamento do protocolo quântico.

O monitor agora configura 100 ms para essa espera quando `ESCDELAY` não foi
definida pelo usuário. A espera continua permitindo interpretar as sequências
das setas; configurações explícitas para conexões lentas são preservadas.
Terminais sem a API conservam o comportamento disponível, sem impedir o uso.
A campanha final v2, com wheel e fixture novas, aprovou **92/92 verificações**
nas mesmas quatro sessões PTY. Os 92 nomes de verificações e as 46 ações
medidas correspondem aos da v1. O backend preservou identidade e permissões;
o tick avançou de 4 para 5 e `physical_read_only` permaneceu ativo. Saídas
`[0, 0, 0, 130]` e restauração de `termios` foram conferidas novamente.
[Relatório final Linux](../../outputs/terminal-eva-20261011/campaign-v2/report.json),
[evidências finais](../../outputs/terminal-eva-20261011/campaign-v2/terminal-campaign.json),
[comparação vinculada por hashes](../../outputs/terminal-eva-20261011/escape-delay-comparison.json).

| Ação com Esc isolado | Antes (ms) | Depois (ms) |
|---|---:|---:|
| Cancelar conta incorreta | 1.127,527 | 132,469 |
| Cancelar frase incorreta | 1.031,793 | 144,953 |
| Cancelar e limpar formulário mascarado | 1.028,195 | 125,971 |
| Cancelar mantendo tema ativo | 1.041,819 | 137,142 |
| Cancelar após Unplug | 1.031,881 | 132,272 |
| Fechar aviso com `NO_COLOR`, mantendo o modo | 1.174,389 | 134,909 |

Nas 46 observações por ação, a mediana foi 15,100 ms, o percentil 95 foi
134,909 ms e o máximo foi 144,953 ms. As trocas podem incluir o envio de um
campo inteiro; esses valores não representam tempo de pintura isolado nem
latência individual de cada caractere. São dois ensaios curtos comparáveis,
sem conclusão sobre CPU/GPU, uso prolongado ou desempenho do controlador.
O ganho demonstrado é a resposta ao Esc isolado. A campanha v2 completa
levou 61,627 s e sua etapa PTY 19,325 s.

A wheel final conferiu novamente **449 módulos e 460 arquivos**, com SHA-256
`0362fa219e7a628a5f5fd97a7c3568f1209cdf99b348b6709684b3c17514e31f`.
As evidências e a wheel da v1 foram preservadas. Os ensaios não acionaram o
ramo de timeout do runner; recuperação de descendentes após timeout não foi
qualificada por essas execuções.

### Prévia visual final

As imagens abaixo reconstroem caracteres e cores capturados nas células dos
PTYs finais; não são capturas de uma janela gráfica. A identidade de teste
foi ocultada. O aviso e o dashboard foram inspecionados visualmente.

- [Aviso E.V.A., 200 × 60 células](../../outputs/terminal-eva-20261011/previews-v2/03-eva-notice-200x60.png).
- [Dashboard E.V.A., 120 × 40 células](../../outputs/terminal-eva-20261011/previews-v2/04-eva-dashboard-120x40.png).
- [Manifesto das nove prévias e fontes](../../outputs/terminal-eva-20261011/previews-v2/preview-integrity.json).

## Reprodução

O comando `watch` continua exigindo identidade provisionada:

```bash
orion-operator --url https://orion-core:8443 \
  --ca /run/operator/ca.pem --cert /run/operator/operator.pem \
  --key /run/operator/operator.key watch --layout dashboard
```

Depois, use L para o formulário, Enter para avançar/submeter, Tab para trocar
o campo e Esc para cancelar. Não coloque a frase em argumentos de shell.
As instruções completas estão no
[manual do operador](../product/orionq_terminal_operator.md).

Regressões e testes específicos:

```bash
python -m pytest tests/test_terminal_ascii_brand.py tests/test_terminal_time_chart.py \
  tests/test_terminal_dashboard.py tests/test_terminal_dashboard_paint.py \
  tests/test_terminal_live.py tests/test_core_operator_cli.py \
  tests/test_terminal_eva_mode.py -q
```

Ensaio Linux, em Python sem otimização e conta sem privilégios, usando um
destino de evidências novo:

```bash
python -I tools/validate_terminal_eva_linux.py \
  --repository /caminho/OrionQ --output /caminho/evidencias-eva-novas
```

O runner instala uma wheel privada vinculada à fonte, provisiona uma fixture
de teste com mTLS e exporta somente projeções de evidências. Essa execução
não substitui a implantação operacional nem qualifica equipamentos.
