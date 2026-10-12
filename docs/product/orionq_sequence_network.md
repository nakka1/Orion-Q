# OrionQ — campanha de rede com SeQUeNCe

A campanha parte da inicialização completa da rede de demonstração usada pelo
console, em estado separado. Inventaria todos os nodes, comunicadores, enlaces,
sessões, bancos de recursos, FPGAs e demandas; enumera também todas as combinações
de instituições. Uma rota cadastrada não comprova disponibilidade instantânea.

São duas evidências distintas:

1. Os bancos terrestres compatíveis executam `RouterNetTopo`, `QuantumRouter`,
   `RequestApp`, RSVP, regras de recursos, geração Barrett–Kok, memórias e troca
   de entrelaçamento no SeQUeNCe real. Cada banco dedicado da sessão OrionQ ganha
   routers virtuais próprios, mantendo a associação à instalação física. Não
   se fundem memórias de diferentes bancos no mesmo pool geográfico.
2. Os saltos clássicos das demandas globais usam uma fonte de campanha que recebe
   callbacks reais de `ClassicalChannel` SeQUeNCe em outro processo. Esses recibos
   percorrem a validação, o agendamento, as rotas, os estados e a persistência do
   `GlobalOperationManager` existente. Os dados ópticos dessa execução continuam
   vindo da fonte local simulada identificada, sem substituição oculta.

O contrato `orionq.sequence-network-input.v1` leva apenas configurações, origem,
semente, bancos, enlaces e demandas. A saída inclui reservas, caminhos, pares
observados, eventos nativos, ocupação, relógio, limitações e hashes. O worker
clássico exige o checkout upstream limpo no commit
`3133c66d80a898c6c3cc9af9eec3fa94fdbe2233`, sem importar o produto. A fonte recusa
IDs, payloads, sequência e tempos inválidos, usando os contratos existentes.

## Critérios e cenários

[sequence_network_v1.json](../../configs/qualification/sequence_network_v1.json)
é congelado antes da execução, junto com fontes e entradas. Três sementes repetem
o cenário. Nenhum resultado é imposto por igualdade de sorteios entre motores.
Os critérios são invariantes funcionais e causais, sem afirmar equivalência de
taxas entre modelos fisicamente diferentes.

- Censo integral e preservação de todos os IDs e parâmetros originais.
- Reservas em todos os bancos terrestres compatíveis, com distâncias e perdas
  configuradas. Ausência de par no intervalo observado é registrada, sem garantir
  sucesso em cadeias longas nem inferir uma taxa estacionária.
- Experimento de referência Rio com parâmetros alterados e identificados:
  multissalto, detectores sem eficiência, canal clássico bloqueado, reservas
  concorrentes no mesmo banco, liberação e queda/recuperação de um router.
- Rede global OrionQ completa: nominal, perda de entrega, timeout, recuperação,
  isolamento de hub geoestacionário e restauração. Recepção nativa e cadeias de
  recibos são conservadas; êxito clássico não representa transporte quântico.

O início da reserva externa deve suceder o tempo clássico de ida e volta. A
janela ativa é curta e declarada, preservando frequências e comprimentos do
inventário; não é uma campanha de rendimento científico de longa duração.
Cada banco recebe uma reserva de duas memórias por extremo, exigindo quatro
nos intermediários, durante um segundo nativo. Referências funcionais do Rio
usam horizonte de 1,5 s. Os pilotos anteriores, com janelas de 15–60 ms, são
preservados: reservas aprovadas não garantiram entrega dentro desses intervalos.

## Execução

```powershell
$env:PYTHONPATH = 'src;tools'
$py = '.audit-winvenv\Scripts\python.exe'
$seqpy = 'outputs\cross_simulator_preparation_20261010\sequence-venv\Scripts\python.exe'
$run = 'outputs\sequence_network_20261010\nova-rodada'
& $py tools\run_sequence_network_campaign.py prepare --output $run
& $py tools\run_sequence_network_campaign.py run --output $run `
    --sequence-python $seqpy --sequence-src C:\SeQUeNCe
```

Escolha um diretório novo. Fontes, critérios ou entradas alterados invalidam o
congelamento. As tentativas e os logs não são sobrescritos. `covered_scope_passed`
se refere somente às verificações declaradas; o estado `partial_verified` mantém
`full_hybrid_quantum_network_validated=false`. Falta, erro ou timeout do simulador
nunca produz um resultado substituto local. Não é necessário ativar modelos ML.

## Mapeamentos e limites

O modelo de geração Barrett–Kok nativo é um experimento diferente da geração
agregada OrionQ. Memória nativa com prazo de expiração não equivale ao decaimento
contínuo de visibilidade do produto. Eficiências de escrita/leitura, tomografia,
dark counts, fase/polarização e ruído BBPSSW não recebem equivalência implícita.
Perda de inserção distribuída é equivalência de perda escalar, sem validar sua
localização. As operações ideais dos cenários de referência são explicitadas.

O modo nativo escolhido para a campanha de rede é `ket_vector`, compatível com
os exemplos oficiais. O piloto prolongado de `density_matrix` falhou no commit
fixado: chaves de ancillas medidas permanecem em estados compartilhados e podem
ser reutilizadas; `SingleAtomBSM` então rejeita um estado com mais de duas chaves.
A falha, as entradas e os logs são preservados. Nenhum código upstream foi
alterado. Vetor de estado representa trajetórias condicionais; os estados
capturados não são apresentados como estimação da densidade média de um ensemble
nem como confirmação do modelo de ruído ou decoerência do OrionQ.

A admissão de pares pelo `RequestApp` usa a fidelidade dos metadados nativos do
framework. Essa estimativa não é confundida com o estado de densidade retido:
o runner captura também sua matriz e fidelidades Bell quando disponíveis. A
correção remota de Pauli pode ainda estar em trânsito no instante do callback.
Diferenças entre a recorrência escalar de fidelidade e a densidade observada são
preservadas, sem ajustar o modelo OrionQ nem qualificá-las como equivalentes.

O framework configura canais clássicos de controle entre os routers com atraso
derivado dos caminhos registrados. Isso não comprova infraestrutura clássica
dedicada, contenção de banda ou autenticação de mensagens. Na fonte integrada,
propagação, serialização e jitter são entradas fornecidas pelo OrionQ; cada salto
usa uma Timeline nativa própria, e o backend OrionQ conserva a ordem global.
Perda e timeout são injetados após a recepção pelo canal nativo ideal sem perdas.

TF-QKD, downlinks ópticos satelitais e terminais ISL ficam no inventário com
validação quântica externa **não demonstrada**. Não são convertidos em fibras.
Também não existe qualificação de relay quântico coerente global através dos
satélites; estimativas QKD e detecções BBM92 não são concatenadas em pares ou
chaves utilizáveis. Hardware, recursos compartilhados reais, segurança QKD e
TRL não são admitidos pela campanha. Atuação física permanece bloqueada e bits
secretos certificados permanecem em zero.

Fontes primárias verificadas em 10/10/2026:

- [SeQUeNCe, Wu et al.](https://arxiv.org/abs/2009.12000): arquitetura do simulador.
- [Tutorial de rede e aplicações](https://sequence-rtd-tutorial.readthedocs.io/latest/tutorial/sequence3min/three_minute.html): `RouterNetTopo` e `RequestApp`.
- [RSVP e reservas](https://sequence-rtd-tutorial.readthedocs.io/stable/references/network_management/rsvp.html): admissão de recursos.
- [NetworkManager](https://sequence-rtd-tutorial.readthedocs.io/latest/tutorial/chapter5/network_manager.html): roteamento e controle.
- [Código oficial](https://github.com/sequence-toolbox/SeQUeNCe): commit local fixado e hashes preservados.
