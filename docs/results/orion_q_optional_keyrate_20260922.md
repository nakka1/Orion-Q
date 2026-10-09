# Admissão opcional do agente de taxa de chave

22/09/2026. Export `modelo_qkd_ml.zip`, SHA-256
`94352f15b6dcb132a3c03559b9d94b28f42b6e9adb8b2cdaab18f46720a55a79`.
Código de inferência relevante inspecionado; nenhum script do ZIP executado ou
importado, detector pickle não carregado. Uso exclusivamente offline/simulado.

## Resultado contra o exemplo fornecido

Foram conferidos todos os **40 períodos** de um episódio simulado de 50 km,
não apenas os seis primeiros mostrados pela rotina de demonstração do pacote.

| Verificação | Resultado | Tolerância |
|---|---|---|
| 16 features por período | maior desvio 4,993e-10 | absoluta 1e-8 |
| 9 probabilidades por período | maior desvio 4,955e-7 | absoluta 1e-6 |
| Índice da ação gulosa | zero divergências | igualdade |
| 5 parâmetros após ação gravada | zero divergências; erro máximo zero | relativa/absoluta 1e-12 |

As tolerâncias de features e probabilidades consideram arredondamento do export
a nove e seis casas decimais, respectivamente. O modelo não foi ajustado aos
exemplos. Normalização, ordem de features, temperatura 0,25, caixa e operações
aritméticas foram preservadas.

**Sorteios originais não foram reproduzidos:** o arquivo informa semente do
episódio, mas não um estado/semente de RNG da política. Conferiu-se o efeito
da ação amostrada já gravada, separadamente da geração de recomendações. A
amostragem do adaptador com RNG explícito é coberta por testes autocontidos.

Não foram regenerados `bits_de_chave`, treinado outro agente ou comparado ganho
contra a política estática. A paridade não comprova os ganhos do artigo,
segurança de chaves, latência de dispositivo ou generalização experimental.

## Observação sobre taxa zero

O código original usa a taxa recém-observada nas features 7 e 13, mas aplica
pisos diferentes antes do logaritmo. Logo, a descrição abreviada de que a 13
sempre repete a 7 não vale quando os pisos atuam. Esse comportamento foi mantido
e testado explicitamente, sem alterar pesos ou inventar um período anterior.

## Artefatos

226 testes passaram em 20,18 s: 69 novos do agente e 157 dos incrementos anteriores
e barreiras de controle/evidência/comandos. Foram cobertas as nove ações, limites
acoplados, normalização, taxa zero, RNG explícito, falha sem consumir RNG e
seleção opcional. Ruff passou nos arquivos da implementação e testes. Não foi
executada toda a suíte histórica do repositório.

- `outputs/author_keyrate_20260922/replay.json`: replay integrado dos oito
  checkpoints admitidos, com hashes e limites explícitos.
- `outputs/author_keyrate_20260922/final-tests.xml`: testes desta etapa e regressões.
- [Guia do agente opcional](../product/orion_q_optional_keyrate.md).

O catálogo passa a oito checkpoints `offline_only`, dois alocadores pendentes
e detector bloqueado. Todos continuam OFF por padrão, sem habilitação de
runtime/UI ou inclusão em novo executável. Próximo componente: alocador híbrido.
