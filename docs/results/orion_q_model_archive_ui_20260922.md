# ORION-Q — verificação local de pacotes opcionais

Data: 22/09/2026. Continuação do catálogo gráfico, em código-fonte. Nenhuma
política ativada e nenhuma distribuição Windows anterior sobrescrita.

Nota histórica: o [incremento seguinte](orion_q_sync_session_ui_20260922.md),
no mesmo dia, acrescenta uma sessão offline explícita do sincronizador. A
verificação de identidade descrita aqui continua sem construir modelos.

## Operação

1. Abrir **EDGE CONTROL → OPTIONAL MODEL CATALOG** e selecionar uma das 11 linhas.
2. Acionar **VERIFY LOCAL ZIP** e escolher o pacote QKD ou QNET correspondente.
3. A leitura em segundo plano confere SHA-256 fixado na revisão, limites de
   32 MiB compactados / 64 MiB expandidos / 200 membros, nomes e CRC, reutilizando
   `ReviewedArchive`. Não atualiza pins para aceitar um arquivo diferente.
4. Ver o resultado `IDENTITY VERIFIED · NOT INSTALLED`, com caminho, hash,
   horário UTC, quantidade de arquivos e membros pickle bloqueados, ou a razão
   de recusa. O resultado é de uma cópia em memória dos bytes examinados,
   não garantia de que o arquivo no disco continuará igual.
5. **FORGET CHECK** limpa apenas essa consulta. Não apaga arquivos. Selecionar
   outra linha, trocar fonte/campanha ou fechar a janela também descarta o
   resultado, inclusive quando a leitura antiga termina depois.

O detector pode ter a identidade de seu pacote conferida, mas continua
`blocked_pickle`. Os demais modelos continuam com padrão OFF e runtime
NOT INTEGRATED. Não há extração, importação dos scripts do ZIP, desserialização
dos pesos, construção de modelo, instalação ou persistência de caminhos.
Qualquer execução futura deverá verificar novamente os bytes escolhidos.

Uma única leitura pode estar em andamento. Se uma leitura já iniciada não puder
ser cancelada, a janela a descarta por geração de contexto e aguarda seu término
antes de permitir outra; não acumula trabalhos. O polling do Future ocorre na
thread Qt, sem callbacks de worker para objetos gráficos já destruídos.

## Verificação com os anexos fornecidos

Em 22/09/2026, foram conferidos novamente, sem alterar os arquivos:

| Pacote | SHA-256 revisado | Arquivos inventariados | Bytes expandidos |
|---|---|---:|---:|
| QKD | `94352f15b6dcb132a3c03559b9d94b28f42b6e9adb8b2cdaab18f46720a55a79` | 47 | 7.647.367 |
| QNET | `6b8563209f54a2d566f1a4229b140b75fda93555085ff2bf6bfcad8f07abd938` | 41 | 13.858.545 |

As contagens acima excluem entradas de diretórios. QKD contém
`modelo_qkd_ml/modelos/detector_boosting.pkl`, apenas lido como bytes para
integridade, nunca desserializado. QNET não contém membros pickle identificados
pelo inventário. A sessão Qt também verificou o QNET pelo worker assíncrono.
Nada disso demonstra licença de redistribuição, autenticidade criptográfica,
compatibilidade de telemetria ou validade científica dos modelos.

## Testes e revisão visual

Resultados em `outputs/model_archive_ui_20260922/`:

- **91 testes aprovados**, `ui-tests.xml`: catálogo nativo, verificação local,
  telas, capacidades e troca de fontes.
- **276 testes de regressão aprovados**, `regression-tests.xml`: admissão dos
  anexos, inferência opcional, comandos e barreiras de evidência/controle físico.
- Total desta rodada: **367 aprovados**, sem falhas ou testes ignorados.
- Ruff aprovado nos sete arquivos Python alterados/adicionados desta etapa
  (incluindo testes; o catálogo e a tela EDGE também integram esta rodada).
- Capturas `verified-wide.png` e `verified-small.png` revistas em 1060 × 760 e
  640 × 480; tabela/detalhes roláveis e comandos de consulta acessíveis.

Casos cobertos: família errada, hash alterado, CRC corrompido, arquivo ausente,
diretório, limite de tamanho, caminhos UNC/device/URL recusados, cancelamento do
seletor de arquivos, erro em texto literal, resultado tardio após seleção/fechamento,
fonte nova com os mesmos metadados, contexto alterado dentro do seletor e ausência
de ativação. Testes numéricos usam fixtures, sem depender dos anexos externos.

## Limite de entrega

Isto não instala nem executa os modelos pelo dashboard. Próximo incremento:
sessão offline limitada, com entradas tipadas e comparação com baseline, mantendo
a ativação de política Q05 separada. O `.exe` anterior permanece sem o catálogo
e sem esta verificação até uma nova etapa de empacotamento/aceite.
