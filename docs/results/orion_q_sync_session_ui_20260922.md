# ORION-Q — primeira sessão offline gráfica: sincronizador QKD

Data: 22/09/2026. Implementação em código-fonte, separada da ativação Q05 e da
distribuição Windows anterior. Os modelos continuam opcionais no produto.

Nota histórica: o [incremento seguinte](orion_q_keyrate_session_ui_20260922.md),
no mesmo dia, acrescenta a sessão do ator QKD com contrato próprio. O escopo
abaixo registra a primeira sessão do sincronizador.

## Fluxo entregue

1. Em **EDGE CONTROL → OPTIONAL MODEL CATALOG**, selecionar
   `qkd.sync.ridge.1s.h2s` e conferir o pacote QKD com **VERIFY LOCAL ZIP**.
2. Abrir **OFFLINE SESSION**. A sessão inicia vazia e não executa nada.
3. Usar **LOAD JSON**, colar JSON ou escolher **DEMO**. O exemplo é escrito
   artificialmente para demonstrar a interface, não uma medição ou benchmark.
4. Revisar e acionar **RUN**. O pacote é novamente conferido contra o pin antes
   de construir um sincronizador novo. A inferência roda fora da thread gráfica.
5. Conferir previsão de offset a +2 s e persistência. Uma referência futura
   simulada é opcional e nunca é passada ao modelo. Sem referência, os erros
   permanecem indisponíveis; com ela, ambos os erros absolutos são apresentados.
6. **EXPORT** cria um JSON novo; arquivos existentes são recusados. O registro
   inclui entrada submetida, hash dessa entrada, hash do pacote, instante UTC,
   stream, horizonte, previsão, baseline, referência e erros quando disponíveis.

Somente esta função está disponível na sessão gráfica. Não há seletor genérico
que envie os mesmos dados ao ator QKD, roteador, compensador ou alocador. Esses
componentes têm contratos diferentes e seguem acessíveis por código offline.

## Contrato de entrada

UTF-8 JSON de até 64 KiB, sem campos duplicados/adicionais, com números finitos
(booleanos e strings numéricas são recusados), oito amostras em ordem e cadência
de 1 s. `stream_id` identifica a janela declarada pelo operador; cada RUN constrói
estado novo, sem histórico automático ou combinação entre streams.

```json
{
  "schema": "orion.optional-sync-input.v1",
  "evidence": "simulated_offline",
  "stream_id": "demo/hand-authored/sync",
  "timestamps_s": [0, 1, 2, 3, 4, 5, 6, 7],
  "offsets_s": [0, 1e-10, 2e-10, 3e-10, 4e-10, 5e-10, 6e-10, 7e-10],
  "temperatures_c": [20, 20, 20, 20, 20, 20, 20, 20],
  "reference": {"target_timestamp_s": 9, "offset_s": 9e-10}
}
```

`reference: null` permite prever sem calcular erro. Se presente, o timestamp
da referência deve coincidir com o último timestamp +2 s. O campo `evidence`
deve ser `simulated_offline`; declarações `measured` são recusadas. Isso verifica
uma declaração e o esquema, não autentica a origem física dos números.

O hash é do texto UTF-8 **submetido ao RUN**, incluído em `input_json` no relatório,
não necessariamente dos bytes originais do arquivo (o editor pode normalizar
quebras de linha). Reproduzir a previsão exige o mesmo pacote revisado e a entrada;
o horário da nova avaliação naturalmente será diferente.

## Isolamento e segurança

- Entrada inválida é recusada antes de abrir pesos.
- O JSON futuro não entra nas features: somente timestamps, offsets passados e
  temperaturas da janela são passados ao preditor.
- Editar a entrada limpa resultado/erro anterior; uma execução ainda em curso
  pode terminar, mas sua geração invalidada impede que repovoe a tela.
- Trocar a fonte/modelo, esquecer o pacote ou fechar o catálogo/sessão invalida
  a associação com o pacote. Reabrir não reexecuta nem instala nada.
- Seletores de arquivos conferem o contexto novamente antes de carregar/exportar;
  exportação antiga não sobrevive à edição dentro do diálogo de salvar.
- Uma execução por sessão; sem treinamento, comandos, escrita em journal de
  controle, chave gerada, atuação física ou conversão BER/QBER/SKR → fidelidade.

## Verificação

Artefatos locais em `outputs/sync_session_ui_20260922/`:

- **127 testes aprovados** (`ui-tests.xml`): sessão, catálogo, verificação de
  pacotes, telas, capacidades e troca de fontes.
- **276 testes de regressão aprovados** (`regression-tests.xml`): modelos
  opcionais, admissão, comandos e barreiras científicas/de atuação.
- Total desta rodada: **403 aprovados**, sem falhas ou testes ignorados.
- Ruff aprovado nos seis arquivos Python alterados/adicionados neste incremento.
- Capturas `session-wide.png` e `session-small.png`, revisadas em 1060 × 760 e
  640 × 480. Editores e resultados são roláveis; controles permanecem acessíveis.
- `demo-evaluation.json`: execução real do código do sincronizador com o pacote
  fornecido e a janela **artificial** do botão DEMO. Pacote SHA-256
  `94352f15b6dcb132a3c03559b9d94b28f42b6e9adb8b2cdaab18f46720a55a79`.

Nesse exemplo único: previsão ≈ 0,760657 ns, persistência 0,7 ns, referência
artificial 0,9 ns; erros absolutos ≈ 0,139343 ns e 0,2 ns, respectivamente.
Esses números são uma verificação funcional, **não evidência de ganho geral**.
Testes com pesos sintéticos incluem explicitamente um modelo pior que o baseline;
o produto não oculta ou inverte esse resultado. Referências futuras alteradas ou
ausentes produzem a mesma previsão, confirmando a separação das entradas.

## Pendente

Sessões próprias dos demais componentes, avaliações temporais/entre campanhas,
ativação Q05, conexão com controle físico e nova distribuição `.exe`. Esta etapa
não valida predição de fidelidade quântica por WDM real nem reproduz um artigo.
