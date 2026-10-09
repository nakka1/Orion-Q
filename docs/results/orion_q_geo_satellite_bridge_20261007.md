# ORION-Q 5.45 — GEO e ponte satelital

Revisão de 07/10/2026. Validação automática local: aprovada.

## Resultado e escopo

A transição por scroll exige maior afastamento do mapa e maior aproximação do globo, com histerese de 60/20 milhões de metros e limiar 2,85. A correção iterativa mantém o ponto geográfico sob o cursor em scrolls consecutivos. O raster temporário é liberado por um timer preciso além do término normal da animação. O cabeçalho mais compacto usa espaçamento de oito pixels, corrigindo o excedente de dois pixels observado na largura de 1042 pixels.

Janelas satelitais mostram housekeeping e métricas conjuntas do protocolo, recebidas do backend, com validade, origem, órbita, canais, visibilidade, Doppler, perdas, coincidências e QBER. Inspeção detalhada aberta recebe os mesmos snapshots. Recalibração de fibra e edição de posição terrestre ficam ocultas para satélites; isolamento continua operacional. O inspector de enlace usa distância oblíqua e distingue fidelidade conjunta de um braço óptico.

O cenário aditivo UFRJ → SAT-ATLANTIC-MEO → BSC processa cliques nas duas OGS e recibos clássicos pelo pipeline existente. Uma associação cadastrada permanece pontilhada quando não existe emissão recebida válida. Emissões recentes usam linha fina contínua; a órbita é tracejada. Hover/seleção destacam o enlace, com tooltip e acesso aos canais. A legenda inferior combina os símbolos dos componentes e os traçados/cores dos enlaces, quebrando em linhas em janelas menores.

## Casos de uso verificados

| Caso | Evidência / resultado observado | Estado |
|---|---|---|
| Zoom de saída do mapa | Limiar mais distante; contexto geográfico preservado. | Aprovado |
| Aproximação do globo | Cinco passos de scroll partindo de zoom 1 permanecem no globo; o sexto retorna ao mapa. | Aprovado |
| Scroll fora do centro | Correção não linear preserva a região focalizada após passos consecutivos. | Aprovado |
| Alternância consecutiva | 100 trocas completas e 201 pedidos rápidos no código; 60 trocas no EXE. Uma instância ativa, caches/timers antigos liberados. | Aprovado |
| Satélites simultâneos | Posições e órbitas compartilhadas; seleção mantém os demais satélites. | Aprovado |
| Legenda inferior | Endpoint, Core, Node, Repetidor, Satélite, fibra, estados terrestres, cadastro orbital, emissão e órbita. | Aprovado |
| Janela flutuante | Clique real no globo abre conteúdo satelital, ocultando curva e ações genéricas de fibra. | Aprovado |
| Telemetria em tempo de rede | Novo bloco recebido altera ECEF, idade e métricas; detalhe aberto acompanha o snapshot. | Aprovado |
| Leitura por scroll | Rolagem alcança o último cartão de estação e o botão de Science; clique abre o detalhe. | Aprovado |
| Seleção de enlace orbital | Aproximação pelo botão e clique em trecho sem sobreposição de ícones abrem protocolo, canais e métricas conjuntas. | Aprovado |
| Ponte entre redes | Coincidências derivadas de duas sequências TDC; etapas acquired, validated, protocol_processed, classical_received e metrics_computed persistidas. | Aprovado |
| Controle clássico desligado | Ponte bloqueada, nenhum anúncio de emissão; recuperação exige recibos novos. | Aprovado |
| Payload quântico desligado | Mesmo bloqueio e regra de recuperação; canais continuam distintos e associados. | Aprovado |
| Indisponibilidade óptica | Backend declara bloqueio e remove emissões; associações permanecem consultáveis. | Aprovado |
| Recuperação | Novas operações recebidas restabelecem observação conjunta. | Aprovado |
| LEO de longa distância | Caso anterior permanece sem disponibilidade simultânea; não há extensão de alcance inventada. | Aprovado |
| Preservação do operador | Migração aditiva, diário com estado anterior, repetição idempotente e remoção respeitada. | Aprovado |
| Segurança / controle | Certificação em zero, sem chave utilizável ou atuação física; ML opcional. | Aprovado |
| Interface geral | Topologia, node windows, formulários, telas, temas e resoluções existentes passaram. | Aprovado |
| Windows nativo | Inicialização/DPI, campanhas/worker, diagnósticos e stress a partir do EXE. | Aprovado |

## Observações do cenário, fontes e limites

No bloco nominal registrado pelo EXE: 46 coincidências, 23.0 pares/s, QBER 0.0. Após a recuperação: 58 coincidências no bloco registrado. Esses valores são observações de blocos específicos, sem garantia para toda a órbita, outra semente ou condições atmosféricas diferentes. Nenhuma chave certificada foi produzida.

A ponte é uma hipótese MEO a 10.000 km com receptores de 4 m, divergência de 2 µrad e jitter de apontamento de 0,3 µrad, marcada no inventário e na interface como simulação proposta. Não descreve equipamentos da UFRJ/BSC ou uma missão existente. O perfil inicial testado com 2 m e jitter de 2 µrad não produziu coincidências; sua evidência foi conservada em probe.json. O perfil revisto está registrado em probe-v2.json e tem seus requisitos explícitos, sem preencher sucesso ou fidelidade diretamente.

[Yin et al., Science 2017](https://arxiv.org/abs/1707.01339) demonstraram dois downlinks em LEO, sem validar nosso perfil MEO. A [OGS da ESA, publicação de 2022](https://www.esa.int/Enabling_Support/Space_Engineering_Technology/ESA_s_site_for_laser_and_quantum_links_marks_25_years) fornece uma referência real de terminal de 1 m. [Zaunders e Ralph, 2026](https://arxiv.org/abs/2510.01633) analisam arquiteturas sob canais ópticos; isso é distinto de qualificação experimental. Fontes consultadas em 07/10/2026. Parâmetros e contratos: [guia da ponte](../product/orion_q_satellite_bridges.md), [OGS v2](../product/orion_q_satellite_ogs_v2.md) e [GEO](../product/orion_q_geo_modes.md).

O serviço é distribuição de pares/BBM92 de teste. Encaminhamento de dados de aplicação, relay confiável de chaves, memória satelital para contatos não simultâneos e enlaces entre satélites permanecem fora dessa implementação. ICD/gateway da missão, calibração de hardware e segurança composável exigem qualificação externa. A aceitação automática local mantém `manual_review_required`; não substitui avaliação em outra máquina ou campo.

## Validação e preservação

278 testes únicos passaram, sem somar repetições. XMLs e logs preliminares foram preservados em `outputs/geo_satellite_bridge_20261007_v1`. As iterações identificaram incompatibilidade de contrato, ajuste de atualização inicial da fixture, diferença de revisão após a migração, encaixe do cabeçalho e sobreposição de ícones na seleção. Um ensaio intermediário de pintura usou o argumento de destaque antes de atualizar o contrato do pen; o processo Qt falhou e a repetição após corrigir ambos os lados passou. Os testes de zoom também detectaram deriva do foco e retenção do raster; ambos foram corrigidos. Erros de temp fora do workspace foram contornados usando diretórios novos dentro de outputs. O resumo consolida o resultado mais recente por caso, preservando os registros anteriores.

No EXE/QPA Windows: 38 checks gerais, 21 de Comunicadores, 11 da GEO, 16 da ponte, 21 de OGS, 25 de campanha e quatro de roadmap. Seis inicializações em DPI/modos distintos e 20 rodadas/140 ciclos de stress passaram. A aceitação automática repetiu os diagnósticos a partir do pacote. O diagnóstico opcional foi executado sem ZIPs de modelos; não declara validação nova dos pacotes originais.

O primeiro candidato nativo foi retido em `release/windows/5.45.0-native-20261007-074052` e não foi aprovado para distribuição: o verificador da tabela de protocolos ainda exigia 12 sessões, embora a ponte elevasse corretamente o inventário a 13. Os outros 32 checks institucionais e os 16 da ponte passaram nesse candidato. A comparação agora usa a quantidade declarada pelo backend; o fluxo institucional corrigido passou 33 checks no código e foi compilado novamente antes da aceitação final.

Preservação: 1116 arquivos existentes de baseline; 1092 idênticos, 24 alterados no escopo registrado, nenhum removido. EXE e ZIP 5.44 permanecem com seus hashes originais. Alterações anteriores do projeto foram conservadas. Novos módulos da ponte, validação, teste e guia complementam o inventário existente.

Evidências: `outputs/geo_satellite_bridge_20261007_v1/delivery-summary.json`, `release/windows/5.45.0-native-20261007-080023/self-test.json`, `release/windows/5.45.0-native-20261007-080023/communicators-geo/satellite-bridge/report.json` e `release/windows/5.45.0-native-20261007-080023/native-acceptance/acceptance-report.json`. Capturas nativas: satellite-orbit-window.png, satellite-live-window.png e bridge-and-legend.png no diretório satellite-bridge. As três imagens de 1280 × 900 foram inspecionadas: janela orbital, cartões de estações após scroll, globo com enlaces e legenda inferior legíveis. Valores extensos possuem inspeção detalhada e tooltips.

## Distribuição

- EXE: `release/windows/5.45.0-native-20261007-080023/ORION-Q/ORION-Q.exe`.
- ZIP: `release/windows/5.45.0-native-20261007-080023/ORION-Q-5.45.0-native-windows-x64.zip`.
- SHA-256 EXE: `f7dbe6107e9c87a09fccd1734e30102ccd50d2faafc39a1aa5af777844228cf1`.
- SHA-256 ZIP: `9b3af795bbd2fefbd2980533b53b185bc44cd99e71078222dc5569df0f2be1d9`.
- ZIP: 294794537 bytes, 3929 entradas; CRC, manual 5.45 e guias verificados.

Extraia o ZIP completo e mantenha `_internal` junto ao EXE. Para observar a ponte, abra o modo demonstração e selecione SAT-ATLANTIC-MEO em Topology → GEO → Globo.
