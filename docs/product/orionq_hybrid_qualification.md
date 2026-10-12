# Qualificação computacional híbrida — protocolo de 10/10/2026

Esta campanha complementa os ensaios com SeQUeNCe. Compara componentes
SNS-TF, óptica espacial, geometria e pós-processamento BBM92 com referências
independentes. Não pressupõe que o SeQUeNCe implemente essas tecnologias nem
substitui experimentos físicos por dados de mapas, TLE ou telemetria RF.

Os critérios, sementes e arquivos são congelados antes da execução oficial.
Ensaios de preparação ficam em outro diretório. Um erro, divergência científica
ou recurso ausente permanece registrado; não é convertido em sucesso por uma
fonte alternativa. Os modelos de produção, campanhas anteriores e seus guias
congelados são preservados.

## Contratos e referências

| Família | Evidência comparável | Limite da interpretação |
|---|---|---|
| SNS-TF | Estimador decoy reduzido contra cálculo independente e restrições Poisson; limites finitos em fixtures estratificadas | O backend atual não registra todos os estratos e slices de fase exigidos para executar a prova SNS finita |
| Satélite–solo | Abertura gaussiana contra código publicado; WGS84, elevação, distância, Doppler e eventos TDC no pipeline | Dispositivos e atmosfera hipotéticos; o modelo de pointing deve ser comparado sob as mesmas hipóteses |
| Satélite–satélite | Obstrução pela Terra, alcance, orçamento e capturas BBM92 | Medições destrutivas não estabelecem uma rota de entrelaçamento com memórias e BSM orbital |
| BBM92 | Matching independente, sifting, estimativa finita, transcript de reconciliação, verificação e Toeplitz GF(2) | Orçamento condicional; autenticação distribuída, dispositivos e aleatoriedade não estão qualificados |
| Ingestão | WebSocket autenticado, três streams, recibos clássicos, NetworkEngine e persistência, com fixtures sintéticas | Não há recepção RF/TDC física nem ICD de missão qualificado |

Referências primárias:

- [Jiang et al., SNS com pulsos finitos, 2019](https://arxiv.org/abs/1904.00192v3): as observações de decoy e fase precisam corresponder ao protocolo da prova.
- [Tomamichel e Leverrier, 2017](https://arxiv.org/abs/1506.08458v3): protocolo, pressupostos, amostragem e extração de chave de tamanho finito.
- [Wallnöfer et al., 2022](https://arxiv.org/abs/2110.15806v2) e [código dos autores](https://github.com/jwallnoefer/multisat_qrepeater_sim_archive), [arquivo Zenodo](https://doi.org/10.5281/zenodo.5603047): abertura óptica, atmosfera e arquiteturas multi-satélite. Executar um componente óptico não reproduz a simulação completa de repetidores orbitais do artigo.

Consulta em 10/10/2026. Cada família registra versões, equações, parâmetros,
hipóteses, tolerâncias e artefatos adicionais. Os oráculos ficam separados dos
wrappers que importam o backend. A ausência de importação do OrionQ é uma
verificação de independência de implementação, não uma revisão científica
independente realizada por outra instituição.

## Execução

No PowerShell, a partir do repositório:

```powershell
& .audit-winvenv\Scripts\python.exe tools\space_validation_prepare.py
& .audit-winvenv\Scripts\python.exe tools\run_hybrid_quantum_campaign.py --output outputs\hybrid_quantum_20261010\run1
```

O primeiro comando obtém o arquivo público MIT fixado em SHA-256
`895b3b093b33587d08c9d878c94597825ad0d83dae564a34e6455b673146f482`,
confere os caminhos e preserva os 67 arquivos dos autores. A comparação verifica
novamente todos os bytes extraídos antes de importar a referência. Internet é
necessária somente para essa preparação quando o ZIP estiver ausente.
O ambiente Python precisa conter as dependências do projeto, NumPy e SciPy;
as versões efetivamente usadas ficam em `freeze.json`. O inventário do computador
fica em `outputs/hybrid_preparation_20261010/environment.json`.

O diretório deve ser novo. O runner congela configuração, hashes das fontes,
versões e referências anteriores; executa quatro processos separados e confere
os hashes novamente ao final. As famílias usam as sementes 101, 202 e 303.
Relatórios, observações e stdout/stderr são preservados. A validação pode ser
repetida em um novo diretório; sobrescrita de evidências é rejeitada.
Os recursos públicos e manifestos anteriores usados como entradas recebem
cópias verificadas em `references/` dentro da nova campanha.

Os limiares numéricos ficam nos arquivos congelados: decoy reduzido `1e-12`
absoluto, LP Poisson `2e-7` absoluto, óptica dos autores sem jitter `1e-8`
relativo, quadratura com jitter `1e-6`, aproximação Orion no campo distante
sem jitter `1%`, geometria `1e-10` relativo para distância e `1e-8°` para ângulos.
Esses domínios não aprovam automaticamente o modelo Orion de jitter nem seu
point-ahead solo. As divergências são publicadas como `fidelity_gaps`.
Os ensaios de recuperação das capturas espaciais usam a mesma época geométrica
para comparação controlada; continuidade do contador não prova uma passagem
temporal completa. O ensaio de ingestão exercita o scheduler existente separadamente.

Fixtures SNS representam contagens agregadas de pulsos, sem lista temporal de
cada emissão. Os orçamentos são quantidades condicionais; não representam taxa
operacional, chaves extraídas ou igualdade com as curvas otimizadas dos autores.
No BBM92, a probabilidade de falha da cauda de amostragem e seu parâmetro de
smoothing são distintos. A campanha não certifica a alocação composable global.

`partial_verified` significa que os critérios computacionais declarados
passaram e as lacunas estão explícitas. `failed` identifica critérios ou
integridade reprovados. Nenhum resultado atribui TRL, certifica segurança QKD,
admite material emulado para consumo operacional ou habilita atuação física.
