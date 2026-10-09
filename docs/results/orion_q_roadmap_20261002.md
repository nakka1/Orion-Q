# ORION-Q — implementação dos avanços de maior valor

02/10/2026. R1/R2/R4/R5 concluídos nos escopos abaixo. Candidato local R3
aprovado; o usuário adiou o teste em outro computador. O
[guia de uso](../product/orion_q_roadmap_usage.md) contém os comandos e o
[roadmap](../product/roadmap_alto_valor_20261002.md) registra os critérios.

## Localização no projeto

A pasta original é a referência desta entrega:
`E:\Codex\2026-08-27\e\work\NISQ-Predictive-Twin`.
Código, contratos, testes, ferramentas, documentação, pacote Windows e
evidências operacionais estão dentro dela. Os caminhos abaixo são relativos
ao projeto; os links abrem os arquivos locais.

| Conteúdo | Localização |
|---|---|
| Código e contratos | `src/quantum_autopilot/` e `configs/integrations/` |
| Testes e ferramentas | `tests/`, `tools/`, `packaging/` e `.github/workflows/windows-console.yml` |
| Roadmap | [Plano e status](../product/roadmap_alto_valor_20261002.md) |
| Guia de uso | [Comandos e limites](../product/orion_q_roadmap_usage.md) |
| Aceite Windows | [Validação local e segundo computador](../product/orion_q_windows_acceptance.md) |
| Pacote Windows | [ZIP aprovado localmente](../../release/windows/5.38.0-roadmap-20261002-080228/ORION-Q-5.38.0-native-windows-x64.zip) e [SHA256](../../release/windows/5.38.0-roadmap-20261002-080228/ORION-Q-5.38.0-native-windows-x64.zip.sha256) |
| Evidências consolidadas | [ZIP de evidências](../../outputs/ORION-Q-evidencias-roadmap-20261002.zip) e [SHA256](../../outputs/ORION-Q-evidencias-roadmap-20261002.zip.sha256) |
| Evidências individuais | `outputs/software_validation_high_value_20261002/`, com [manifesto de integridade](../../outputs/software_validation_high_value_20261002/evidence-manifest.json) |

Os arquivos de código, testes e documentação fazem parte da árvore de trabalho.
`release/` e `outputs/` guardam artefatos locais e são ignorados pelo Git,
conforme o `.gitignore` existente. O teste no segundo Windows permanece adiado.

## Resultado entregue

| Marco | Implementação | Evidência |
|---|---|---|
| R1 | Catálogo de 36 perfis, consulta exata e tela de comparação contextual no painel FPGA/polarização | Resultados positivos, negativos, inconclusivos e fora do domínio testados |
| R2 | Recibo verificável, JSON, Markdown, catálogo e manifesto SHA256 | Números preservados dos resumos fechados e limites do registro de afirmações |
| R4 | Diagnóstico isolado por etapa, percentis e orçamento | 1.000 ciclos sem carga imposta e 1.000 com thread de cálculo; 0 falhas |
| R3 | Build, novos comandos nativos, gate de pacote e roteiro manual ampliado | Candidato local aprovado; aceite independente pendente |
| R5 | Contrato limitado, worker com timeout, compilação/execução LabOne Q emulada e exportação | Compilador real aprovado, timeout real registrado, 22 testes no ambiente opcional |

As mudanças se concentram nos módulos `controller_evidence`,
`runtime_diagnostic`, `evidence_export`, diálogo `ControllerComparison` e
adaptador `integrations/laboneq/emulation`. O launcher, build, workflow e
empacotador incluem os comandos e verificações necessários. O workflow
remoto não foi executado nesta sessão.

O candidato usa a árvore de trabalho atual, com as melhorias de interface
que já estavam presentes. A revisão Git de referência é
`6181050fd1f414a106b1f3f2c4e5cd19e08d3062`; ela não identifica sozinha as
alterações locais. Os relatórios e o manifesto do build registram hashes
de fontes, e o pacote tem hashes próprios.

## Recomendações e fidelidade das exportações

O catálogo deriva de três resumos fechados, do registro de afirmações e da
tabela de evidência. SHA256 do catálogo revisado:
`e3e9eb7f963091c2a605f9829a623643e5698bbfe3406eb8b6709182a861b6c2`.
O leitor rejeita um catálogo diferente; o verificador reconstitui o recibo
para recusar alterações mesmo quando seu hash local foi recalculado.

Exemplos de ridge no ponto calibrado, 20 sementes por comparação:

| Atuador | QBER modelo | QBER referência | Razão referência/modelo | p Holm | Resultado |
|---|---:|---:|---:|---:|---|
| 30 rad/s | 4,769089% | 4,345226% | 0,911123 | 0,0000686646 | Referência recomendada |
| 120 rad/s | 4,487319% | 4,353086% | 0,970086 | 0,0709782 | Inconclusivo |
| 250 rad/s | 3,607049% | 4,355118% | 1,207391 | 0,0000686646 | Modelo recomendado |

Os recibos preservam os valores de origem e os intervalos de confiança.
120 rad/s não demonstra equivalência. 200 rad/s, identidade de pesos diferente
ou cenário incompatível não recebem recomendação por interpolação. A consulta
e a exportação não ativam o modelo; o laço ativado conserva o contrato ideal
existente. O recibo sem ZIP local verificado identifica somente pesos históricos.

## Desempenho medido

Windows 11 build 26200, AMD Ryzen 7 5700X (8 núcleos/16 threads), Python
3.12.14, SQLite em pasta nova no workspace. Cada condição tem 20 ciclos de
aquecimento e 1.000 tentativas medidas. O estado fixo do repetidor tem dois
pares com fidelidade 0,9 e produz SWAP simulado. O alvo exploratório de
10 ms representa 100 ciclos/s de software, sem vínculo com prazo de hardware.

| Condição | p50 | p95 | p99 | Máximo | Falhas | Excederam 10 ms |
|---|---:|---:|---:|---:|---:|---:|
| Sem carga imposta | 174,596 ms | 332,937 ms | 417,789 ms | 658,425 ms | 0/1.000 | 1.000/1.000 |
| Thread de cálculo contínuo | 502,194 ms | 661,607 ms | 771,087 ms | 1.123,044 ms | 0/1.000 | 1.000/1.000 |

A persistência respondeu por aproximadamente 183,121 ms dos 192,118 ms
médios sem carga adicional. Sob carga, a média total foi 521,848 ms e a de
persistência foi 339,411 ms; custos fora das etapas também cresceram. A medição
identifica onde investigar, sem atribuir uma causa exclusiva ao disco ou ao GIL.
Percentis por etapa não devem ser somados como se fossem o percentil total.

O mínimo para declarar amostragem suficiente do p99 foi fixado em 1.000
ciclos. Falhas e prazos excedidos ficam no denominador de tentativas medidas.
A instrumentação participa do tempo e sua sobrecarga não foi isolada. `idle`
significa ausência de carga imposta pelo diagnóstico; atividade externa do
host não foi controlada. A thread de cálculo é uma condição declarada, não
um benchmark de todas as cargas possíveis.

Esses resultados medem a versão de fontes identificada nos relatórios, e
não o executável candidato. A versão final acrescentou metadados de ambiente
e suporte ao manifesto de fontes do build, sem alterar as etapas medidas.
O teste congelado usa 20 ciclos para funcionamento, com p99 explicitamente
marcado como amostragem insuficiente. Este escopo não mede inferência dos
modelos de polarização, rede completa ou temporização de dispositivo.

## Emulação LabOne Q

Ambiente separado: Python 3.12.14, LabOne Q 26.7.0 e xarray 2026.9.0;
demais versões em
`configs/integrations/requirements-laboneq-emulation-py312-win.txt`.
O experimento `orion-amplitude-demo-v1` usa SHFQC emulado, amplitudes
[0,1; 0,2; 0,3], drive de 160 ns e 8 médias cíclicas. Retornou forma `[3]`,
eixo correspondente e três artefatos serializados. Duração do worker completo
nesta execução: aproximadamente 2,906 s.

Recibo final:
`244e0ce959f5c5123efdcedaca09f9ce96f8493d013679739e63183e96a9589d`.
Cada artefato está vinculado ao recibo e ao manifesto. Um worker real com
orçamento de 1 s produziu `TIMED_OUT`; sem LabOne Q no ambiente base, a
API produziu `UNAVAILABLE`. Falha de compilação, resultados malformados e
artefatos alterados também foram exercitados por injeção controlada nos testes.

A categoria é `mock_emulated_acquisition`, com
`physical_action_performed=false` e `policy_activated=false`. Dados artificiais
não entram nas campanhas científicas. Não há conexão física configurável nem
mapeamento de PURIFY/SWAP ou correções SO(3) para protocolos instrumentais.
Referências oficiais e reprodução estão no guia de uso.

## Verificações e artefatos

A regressão do build passou com **2.294 testes e 4 omissões** distribuídos
nos grupos de roadmap, console, campanhas, comandos e modelos opcionais.
As verificações do executável real aprovaram três comparações, 20 ciclos
isolados, sessões opcionais sem pacotes, worker de campanhas, self-test
de telas e estresse de 20 rodadas. A aceitação nativa aprovou integridade
e funcionamento local, mantendo `manual_review_required` e
`independent_machine_verified=false`.

Candidato: `release/windows/5.38.0-roadmap-20261002-080228/`.
O ZIP contém 3.895 arquivos, tem 292.765.580 bytes e passou pela verificação
CRC. SHA256 do ZIP:
`a2cd0877d2dc62c73f37d990ca15733791264e50f620ef03a046d5eec55a63bd`.
SHA256 do executável:
`5510851cd38865e95daf1caedd00bac149a6c8199a24fbe46f4f79352783c41a`.

O primeiro build revelou ausência do caminho de fontes na coleta de recursos
e nos validadores após os testes. O script agora conserva esse caminho até
concluir o pacote, e o spec exige os dois contratos JSON. A recompilação
e as verificações nativas passaram. O diretório do primeiro build foi
preservado como histórico, sem ZIP aprovado para distribuição.

O grupo inicial do incremento passou com 57 testes no ambiente base e um
teste de compilador real omitido por ausência da dependência opcional. Após
as mudanças finais, os testes afetados de exportação/interface, metadados,
gate e emulação foram repetidos e aprovados. O teste adicional do gate também
passou. Os 22 testes do adaptador passaram no ambiente isolado com o compilador
real; o teste omitido no ambiente base foi executado ali. Ruff aprovou os
módulos e ferramentas adicionados/alterados pelo incremento.

Evidências operacionais consolidadas em
`outputs/software_validation_high_value_20261002/`:

- `runtime-idle.json` e `runtime-load.json`: todas as tentativas e metadados.
- `comparison-30/`, `comparison-120/` e `comparison-250/`: recibos, relatórios,
  catálogo revisado e manifestos.
- `laboneq-emulated/`, `laboneq-timeout/` e `laboneq-unavailable/`: estados
  distintos com recibos exportados.
- `evidence-manifest.json`: hashes dos arquivos consolidados.

As evidências científicas originais são as fontes dos resultados; esta pasta
tem a categoria de validação operacional. O teste no segundo Windows e a
avaliação humana R3 permanecem adiados, sem aceite independente declarado.
