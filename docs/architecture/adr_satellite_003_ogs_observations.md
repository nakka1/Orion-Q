# ADR-SAT-003: recepção OGS separada do payload quântico

**Status:** implementado em 5.41, sujeito à qualificação de cada missão.
**Data:** 2026-10-06.

## Contexto

A auditoria de 5.40 identificou contagens pré-agregadas, ausência de transporte
externo, parâmetros ópticos sem efeito e observação orbital obtida somente da
configuração. Um registro de detecção é informação clássica produzida pela OGS;
ele não representa o transporte de um estado quântico pela API.

## Decisão

Adotar `orion.ogs-packet.v2` com três streams distintos: housekeeping, tracking e
TDC. O gateway da estação normaliza dados da missão e fornece identidade, origem,
época UTC, tempos inteiros em ps, referência de relógio, associação dos dois
canais e perdas de captura. A recepção WebSocket é optativa, autenticada e local,
sem endpoint de comando físico. Uma missão remota exige gateway/TLS apropriado.

O backend reúne apenas streams vinculados à mesma sessão, bloco e canais. Dados
incompletos, vencidos, conflitantes, sem sincronização ou com overflow não
produzem sucesso. A fila é limitada; a sequência e os dados aceitos persistem em
SQLite e a repetição idêntica recebe ACK idempotente. O simulador produz os mesmos
contratos, substituindo os receptores e fontes; nenhum contador final de QBER ou
chave é produzido pela fonte.

Cliques brutos permanecem em armazenamento comprimido, com referência SHA-256
nos recibos. O snapshot de interface recebe métricas e referências, evitando
transferir grandes arrays de TDC a cada atualização. O processamento executa
coincidências, bases, amostragem finita, paridades/look-back, verificação universal
e hashing Toeplitz. Sua saída é uma demonstração condicional, sem chave utilizável.

TLE exige SGP4; efemérides ECEF medidas usam interpolação Hermite somente dentro da
cobertura. As hipóteses Kepler/circular continuam disponíveis e identificadas.

## Alternativas

| Alternativa | Avaliação |
|---|---|
| Enviar QBER e chave pronta | Impede verificar cliques, sincronização, sifting e limitações; recusada. |
| Decodificador CCSDS universal no núcleo | O payload depende do ICD da missão; gateway específico permanece externo. |
| WebSocket local tipado | Transporte testável, backpressure e integração com a aplicação sem impor SDK de instrumento. |
| gRPC/MQTT | Podem usar o mesmo contrato; drivers adicionais não foram implementados. |

## Consequências e limites

O inventário continua separando tipo físico, função lógica e modo de integração.
`measurement_stream` habilita recepção de observações e não valida hardware nem
autoriza atuação. Bearer/TLS não constitui prova de autenticação QKD com segurança
composável. O ORION-Q é um backend confiável de observação; não substitui a
implementação distribuída segura nos endpoints. Nenhuma chave é disponibilizada
em pools, arquivos ou API; `certified_secret_bits` permanece zero.

SGP4→ECEF usa GMST, UT1=UTC e movimento polar zero. Não oferece precisão de
apontamento sem EOP/calibração/efemérides da missão. O modelo óptico é reduzido,
com integração em até 250 ms, e não resolve turbulência espacial completa.

## Ações de qualificação externas

- Obter ICD, identificação dos dispositivos e captura de RF/TDC/PPS da missão.
- Validar gateway, calibração temporal, APT, detectores e efemérides/EOP contra OGS.
- Validar segurança, fontes aleatórias, autenticação e pós-processamento nos endpoints.
- Medir carga sustentada, perdas de captura e orçamento temporal com dados reais.
