# OrionQ — validação cruzada com simuladores

Este ensaio compara componentes reduzidos do backend OrionQ com implementações
externas reais. Não altera os modelos científicos do produto nem admite chaves
operacionais. A aprovação de primitivas não qualifica a rede inteira, hardware,
óptica satelital, segurança TF-QKD ou um nível TRL.

## Contratos e isolamento

`tools/run_cross_simulator_campaign.py` executa um processo Python separado por
simulador e semente. A entrada `orionq.cross-simulator-input.v1` contém apenas
semente, IDs, tipos e parâmetros, sem resultados esperados. A saída
`orionq.cross-simulator-output.v1` identifica backend, versão, origem, métricas,
unidades, eventos e limitações. Valores não finitos, IDs ausentes/duplicados,
unidades incompatíveis, contagens inválidas e alterações do congelamento são
recusados. Falta de SDK produz `not_demonstrated`, com métricas vazias.

O runner SeQUeNCe exige checkout sem mudanças rastreadas no commit
`3133c66d80a898c6c3cc9af9eec3fa94fdbe2233`, importa o framework e dependências
reais e registra hashes das fontes upstream. Não usa shim nem funções OrionQ.
O runner NetSquid importa somente o SDK autorizado; não obtém credenciais,
não instala o pacote e não substitui sua ausência por cálculo local.
Sua compatibilidade nativa continua pendente até a execução com o SDK.

As dependências SeQUeNCe ficam num ambiente separado. O ambiente qualificado
`.audit-winvenv` e o checkout em `C:\SeQUeNCe` não são alterados. Os estados do
OrionQ usados para observar comunicação clássica são criados exclusivamente no
diretório da campanha; cadastros e dados do operador não são utilizados.

## Equivalência e critérios prospectivos

[cross_simulators_v1.json](../../configs/qualification/cross_simulators_v1.json)
define 22 casos, três sementes conhecidas, 2.048 ensaios por semente para cada
proporção e limites congelados antes da execução. Sementes iguais identificam
o cenário; não impõem trajetórias ou sorteios iguais entre motores diferentes.

| Componente | Comparação | Mapeamento e limite |
| --- | --- | --- |
| Atenuação | Probabilidade de sobrevivência e contagem de chegada | SeQUeNCe usa m/dB por m/ps; perda de inserção distribuída é equivalência escalar, sem validar sua posição |
| Memória | Fidelidade de par Werner após armazenamento | Orion usa tempo do par T; duas memórias isotrópicas SeQUeNCe usam 8T/3; não se igualam tempos físicos pelo nome do campo |
| Swapping | Fidelidade condicional corrigida nos ramos de Bell | Circuito nativo CNOT/H/medidas/correções X/Z; BSM local ideal completo, sem taxa óptica garantida |
| BBPSSW | Fidelidade retida, probabilidade e contagem de aceitação | CNOT bilateral, paridade e descarte; entradas iguais e desiguais; operações ideais |
| Atraso clássico | Duração envio→recepção e ordem causal | Runtime Orion real, índice 1,468, rota 1, sem jitter/processamento; relógio ps/ns upstream |

Valores determinísticos têm tolerância absoluta `1e-9`; tempo tem `2e-12 s`
para quantização/conversão. A revisão 2 permite erro de arredondamento de até
`1e-12` nos limites físicos 0/1, conservando os valores nativos sem truncamento.
Isso corrige a rejeição de `1,0000000000000002` preservada na primeira tentativa;
tolerâncias de comparação e margens estatísticas permanecem iguais.
Probabilidades exatas obtidas pelo SeQUeNCe são
comparadas separadamente das proporções observadas. NetSquid publica proporções
amostrais de transmissão/aceitação: elas não recebem tolerância determinística.

A avaliação estatística soma 6.144 ensaios por configuração, usa intervalos
exatos Clopper–Pearson e correção Bonferroni para **20 proporções planejadas**,
incluindo o NetSquid mesmo quando ausente. Alpha familiar é 0,001. Aprovação
exige todo o intervalo dentro de ±0,04 da previsão Orion, ou ±0,008 no caso
de transmissão de 100 km. Probabilidade ideal 0/1 exige adicionalmente contagem
exatamente correspondente. Falta de significância de um teste de diferença
não é usada como prova de equivalência. As margens são critérios de verificação
computacional declarados, não orçamento de incerteza de equipamento.

As referências Bell-diagonal do próprio SeQUeNCe são uma conferência auxiliar.
A evidência principal para swapping/BBPSSW vem de seu QuantumManagerDensity,
circuitos, projetores, ramos e medidas. Reimplementar a mesma recorrência no
avaliador não substitui essa execução.

Ruído de portas BBPSSW upstream modifica a aceitação; o parâmetro Orion modifica
a visibilidade após aceitação. O protocolo compara operações ideais e não
ajusta parâmetros a posteriori para esconder essa diferença. Também não cobre
tempos assimétricos por dispositivo, estados gerais não Werner, limites de
memória/roteamento globais, detector/tomografia completos ou ingestão integral
das aquisições externas no runtime. O ensaio clássico percorre intake, validação,
protocolo e recibos do runtime; isso não transforma a campanha inteira em
comparação de redes completas.

## Execução

Preserve tentativas anteriores e escolha diretório novo:

```powershell
$env:PYTHONPATH = 'src;tools'
$py = '.audit-winvenv\Scripts\python.exe'
$seqpy = 'outputs\cross_simulator_preparation_20261010\sequence-venv\Scripts\python.exe'
$run = 'outputs\cross_simulators_20261010\nova-rodada'
& $py tools\run_cross_simulator_campaign.py prepare --output $run
& $py tools\run_cross_simulator_campaign.py run --output $run `
    --sequence-python $seqpy --sequence-src C:\SeQUeNCe
```

Para provisionar outro ambiente, use Python compatível com o commit upstream e
as dependências oficiais dos módulos utilizados: NumPy, SciPy, NetworkX, QuTiP,
qutip-qip, gmpy2, stim, tqdm e PyYAML. O manifesto do resultado registra as versões
efetivamente usadas. Jupyter e visualizações opcionais não são necessários aos
componentes deste runner. Não confundir ambiente mínimo com instalação de todos
os extras do simulador.

Com NetSquid instalado mediante seus termos, acrescente
`--netsquid-python CAMINHO_DO_PYTHON_AUTORIZADO`. Um Python Linux/WSL requer
execução em ambiente com caminhos compatíveis; o comando Windows não converte
caminhos automaticamente. Não colocar credenciais na linha de comando, nos
artefatos ou no repositório.

O código 2 da campanha significa que a validação com **ambos** os motores não
foi aprovada; consulte os estados separados `pass`, `fail`, `error` e
`not_demonstrated`. Falha de execução preserva logs e resultados parciais;
use outro diretório após qualquer correção. A ausência do NetSquid não é
reclassificada como aprovação. O resultado inclui hashes de fontes antes/depois,
entradas, comandos, tempos e relatórios por semente.

## Fontes e limites científicos

Fontes primárias verificadas em 10/10/2026:

- [SeQUeNCe, Wu et al., 2020/2021](https://arxiv.org/abs/2009.12000): arquitetura de eventos e componentes.
- [Código oficial SeQUeNCe](https://github.com/sequence-toolbox/SeQUeNCe) e [canais/unidades](https://sequence-rtd-tutorial.readthedocs.io/stable/references/components/optical_channel.html): APIs confrontadas com o commit local.
- [NetSquid, Coopmans et al., 2020/2021](https://arxiv.org/abs/2010.12535) e [site oficial](https://netsquid.org/): acesso ao pacote e documentação exige registro/termos; execução nativa pendente.
- [Bennett et al., 1995/1996](https://arxiv.org/abs/quant-ph/9511027): purificação ideal.
- [Zang et al., 2023](https://arxiv.org/abs/2305.14573): repeater, purificação, ruído e buffer.

Esses modelos independentes ampliam evidência computacional do escopo testado.
Não são dados de bancada independentes nem provam validade experimental de
todos os parâmetros. Comparações TF-QKD exigem modelo óptico e prova de segurança
apropriados; satélites exigem geometria, apontamento, atmosfera e detectores
independentemente qualificados. Atuação física permanece bloqueada, modelos
opcionais e bits secretos certificados iguais a zero.
