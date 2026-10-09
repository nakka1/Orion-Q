# Protocolo SO(3) v1 — robustez descritiva

Protocolo definido antes da primeira execução desta campanha. Não é pré-registro
independente, inferência estatística ou autorização de uso físico. Não retreina,
não altera pesos e não seleciona automaticamente modelos. Código-fonte apenas.

## Grade fixada

- Quatro checkpoints: Ridge e MLP, regimes calibrado e desenvolvimento.
- Dez sementes novas: 100–109; o diagnóstico anterior usou 1337.
- Seis cenários, 32 lotes de 16 amostras por caso: **240 execuções**, sem seleção.
- Cadência virtual de 2 ms e horizonte de 40 ms. Cada série cobre amostras de
  0 a 1,022 s. Não é uma série longa de operação nem uma planta do artigo validada.
- Sinal de base: produtor artificial existente, senoide de 0,003 rad RMS em X,
  deriva em Z, fase por semente/regime e covariáveis já declaradas. Frequência/
  deriva nominal: 60 Hz/0,02 rad/s ou 40 Hz/0,05 rad/s.

| Cenário | Perturbação fixada |
|---|---|
| `smooth` | Sinal artificial de base, sem perturbação adicional. |
| `rotation_noise` | Incremento SO(3) à esquerda, vetor com ruído gaussiano independente por eixo, σ = 0,002 rad. |
| `thermal_reversal` | Deriva e temperatura triangulares, com reversão em 0,512 s; taxa térmica muda de +0,1 para −0,1 K/s. |
| `rotation_step` | Rotação adicional de 0,02 rad em Y a partir de 0,512 s. |
| `packet_loss` | Lotes 10, 11, 20 e 24 ausentes; sem interpolação. |
| `late_result` | Resultados dos lotes cujo índice módulo 4 é 1 só ficam disponíveis depois da observação do lote seguinte; devem ser recusados. |

O regime do contrato identifica **o checkpoint nominal**, não atesta que as
perturbações estejam no domínio de treinamento. Os snapshots registram cenário
e ausência de estimativa OOD. Reversão/ruído/degrau são estresses intencionais;
não representam medição ou validação de instrumento. A referência é a rotação
observada posteriormente — inclui o ruído artificial, não verdade latente sem ruído.
Não se simulam fótons nem ruído de tomografia. O cenário de atraso testa ordenação
de eventos; não mede duração real de inferência ou deadlines físicos de 40 ms.

## Comparadores e pareamento

1. Persistência: última rotação observada.
2. Velocidade angular constante: média de `log(R[i] @ R[i-1].T)` dos 15 incrementos
   passados, dividida por 0,002 s. Previsão = `exp(velocidade × 0,04) @ R[-1]`.
   Sem parâmetros ajustados ao teste. É comparador de previsão, não controlador alfa-beta.

Os comparadores são calculados antes de observar o lote futuro. Modelo e bases
são pontuados nos mesmos pares, usando o ângulo geodésico SO(3), média e RMS em
radianos. Cada previsão é registrada antes da chegada de qualquer lote posterior.
O alvo exato pode estar dentro de um lote novo. Nenhuma previsão atrasada pode
ser incluída após olhar a referência. Aquecimento não é pontuado como ML.

Há 30 oportunidades de alvo por série completa: os dois últimos alvos estão fora
do intervalo emitido. **Cobertura de campanha = pares / 30**, incluindo no denominador
o efeito de entradas perdidas e resultados atrasados. É diferente da cobertura
condicional do avaliador temporal, que considera só previsões já registradas.
Ambas são preservadas. Resultados piores que as bases nunca são removidos.

## Agregação e critérios descritivos

Sementes têm pesos iguais. Publicar todos os 24 grupos cenário/checkpoint, com
média, mínimo/máximo do erro médio por semente, vitórias/empates/derrotas contra
cada base e menor cobertura entre sementes.

Filtro de engenharia fixado: redução de erro médio de pelo menos 5% contra
**ambas** as bases, vitória em pelo menos 8/10 sementes contra cada uma e cobertura
de pelo menos 90% em todas as sementes. Passar não equivale a significância,
segurança, vantagem em laço fechado ou generalização. Não há p-valores ou testes
de hipóteses; logo não se apresentam alegações inferenciais sem correção de
multiplicidade. A grade inteira é publicada, sem escolher só vencedores.

## Congelamento, arquivos e execução

`configs/optional_models/compensation_campaign_v1.json` deve corresponder exatamente
ao protocolo embutido; alterações requerem versão separada e revisão. A execução
cria diretório novo e grava `frozen-protocol.json` **antes** da inferência, contendo
configuração, hash do arquivo de protocolo, hash esperado do ZIP externo e hashes
dos módulos científicos. Gera 240 relatórios individuais e resumo com hashes.
Mudança de código durante a execução, arquivo divergente ou caso ausente impede
um resumo marcado como concluído. Falhas deixam evidências parciais, sem sobrescrita.

```powershell
python tools/run_compensation_campaign.py --qnet-zip F:/qnet_network.zip --output outputs/NOVO-DIRETORIO
```

Esse protocolo reutiliza uma família artificial já vista no diagnóstico: sementes
novas não tornam a família independente. Qualquer ajuste posterior exige separar
dados de calibração e teste. Controle fechado, planta física, comparador alfa-beta
calibrado e fidelidade de Bell a partir de WDM permanecem fora deste ensaio.
