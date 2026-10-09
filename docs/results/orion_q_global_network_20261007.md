# Revisão da rede global de demonstração — 07/10/2026

O ORION-Q 5.46 acrescenta demandas observáveis, admissibilidade de enlaces
entre satélites e uma constelação GEO explicitamente simulada. Instituições
originam e recebem operações; encaminhamento clássico, distribuição de chaves,
troca de entrelaçamento, memória e purificação continuam capacidades distintas.
As configurações anteriores do operador, os comparadores LEO/MEO, os enlaces
diretos e o pacote 5.45 foram preservados.

## Auditoria e correções

| Área | Correção | Evidência / limite |
|---|---|---|
| Inventário | 71 pontos/comunicadores, 131 enlaces, 36 sessões e 76 demandas; cadastro e topologia associados | Matriz completa com 202 linhas, canais, recursos, rotas, recibos e impedimentos |
| Instituições | 20 instituições com envio/recebimento; CBPF mantém Core; capacidades intermediárias combináveis | Todas originaram e receberam sondas clássicas; recursos quânticos exigem seus contratos |
| Operações | Descoberta/admissão de rotas, mensagens serializadas por salto, perdas, atraso, timeout, nova tentativa e recibos persistidos | 108 sondas concluídas e 552 saltos recebidos na campanha fonte |
| Recursos | Bancos hipotéticos dedicados por sessão; ocupação, consumo, decoerência e falhas no executor | 36 bancos, 132 alocações locais, 536 modos de memória, 74 fontes de pares, seis de pulsos e 192 terminais; ausência de arbitragem compartilhada declarada |
| GEO | Quatro perfis equatoriais geoestacionários simulados, em −100°, −20°, +20° e +120° | Posição ECEF fixa durante uma semana; dados reais e perfis LEO/MEO preservados |
| ISL | Quatro associações explícitas, avaliação de obstrução terrestre, alcance, APT, difração, point-ahead e sincronização | Cada enlace recebeu housekeeping e TDC, produzindo coincidências; obstrução, isolamento e recuperação validados |
| Fluxo quântico | Aquisição, validação, protocolo, gerenciamento de recursos, sinalização, persistência e UI | 120 recibos quânticos únicos; 111 conclusões de serviços locais; nenhum recibo reaproveitado por demandas simultâneas |
| Disponibilidade | Separação entre falta de configuração, disponível sem tráfego, espera, estabelecimento, transmissão, falha e indisponibilidade | Telemetria vencida, canais desativados e isolamento não mantêm falsa disponibilidade |
| Publicação de estados | Canal clássico/quântico desativado ou isolamento remove imediatamente status operacional e fidelidade; emissão depende da HK válida da própria sessão | Quatro regressões específicas passaram; outra sessão com HK recente não mantém transmissões vencidas |
| Detalhes dos pontos | Ocupação de bancos do protocolo separada do inventário legado; distância satelital recebida separada da referência geográfica | Caso MEO apresentou 12.040,516 km de distância oblíqua; inventário legado IDLE não oculta ocupação nos bancos |
| Globo | Satélites maiores, seleção prioritária, todos os satélites e órbitas; solo discreto; Terra com contraste maior | Nove satélites, nove órbitas, 15 verificações nativas, dez trocas Mapa/Globo; 640×480 e 3440×1440 |
| Persistência | Aquisições e seus eventos gravados na mesma transação; quota/deduplicação/rollback mantidos | Perfil comparável: passo de 10,10 s para 5,76 s; não implica operação de todas as tecnologias a 1 Hz |

## Matriz e recibos

A [matriz CSV completa](../../outputs/global_network_20261007_v1/validation-agent/v4/component-matrix.csv)
inclui componente, tipo físico, função, capacidades, configuração, recursos
dedicados, enlaces, sessões, demandas, estado, impedimentos e referências dos
recibos. Há versões
[JSON](../../outputs/global_network_20261007_v1/validation-agent/v4/component-matrix.json)
e [tabela Markdown](../../outputs/global_network_20261007_v1/validation-agent/v4/component-matrix.md).
A interface disponibiliza os mesmos campos em **PROTOCOLS > Demandas /
Componentes**, com captura estável das evidências para inspeção.

O [relatório completo da campanha fonte](../../outputs/global_network_20261007_v1/validation-agent/v4/report.json)
passou nas **33 verificações**, em 155,64 s. Foram exercitados operação nominal,
degradação, indisponibilidade, obstrução pela Terra e recuperação. Os journals
contêm 8.245 eventos globais e 74.207 eventos de protocolo, com cadeias de hashes
válidas. Os snapshots e a timeline permitem identificar quais eventos
precederam cada resultado; contagens de coincidências são específicas da
semente e do bloco observado, sem promessa de taxas constantes.

O [resumo de recursos](../../outputs/global_network_20261007_v1/validation-agent/v4/resource-summary.json)
deriva dos snapshots nominal e recuperado e inclui seus hashes. As fontes de
pares e de pulsos são grandezas separadas. Os parâmetros e a justificativa de
cada grupo de conexão estão no [guia operacional](../product/orion_q_global_network.md)
e no [contrato de integração ISL](../product/orion_q_inter_satellite_contract.md).

## Executável e verificações finais

Build nativo:
[ORION-Q.exe](../../release/windows/5.46.0-native-20261007-100616/ORION-Q/ORION-Q.exe)
e [ZIP portátil](../../release/windows/5.46.0-native-20261007-100616/ORION-Q-5.46.0-native-windows-x64.zip),
294.949.542 bytes. O ZIP contém 3.931 arquivos e passou na verificação CRC.

| Verificação | Resultado | Evidência |
|---|---|---|
| Código fonte | 565 casos únicos aprovados; última rodada por caso, sem somar repetições | [Resumo dos XMLs](../../outputs/global_network_20261007_v1/source-verification-summary.json) |
| `.exe`, teste principal em Windows nativo | 39/39 | [Self-test](../../release/windows/5.46.0-native-20261007-100616/self-test.json) |
| `.exe`, cadastro e configuração | 22/22; prova de ativação consulta recibos persistidos, pois a prévia da UI é limitada | [Comunicadores](../../release/windows/5.46.0-native-20261007-100616/communicators-geo/report.json) |
| `.exe`, globo / ponte e inspeção | 15/15 e 16/16; arrasto e clique via eventos Qt | [Globo](../../release/windows/5.46.0-native-20261007-100616/communicators-geo/global-globe/report.json), [Ponte](../../release/windows/5.46.0-native-20261007-100616/communicators-geo/satellite-bridge/report.json) |
| `.exe`, OGS / UI satelital | 21/21 e 7/7 | [OGS](../../release/windows/5.46.0-native-20261007-100616/satellite-validation/report.json), [UI](../../release/windows/5.46.0-native-20261007-100616/satellite-ogs-ui/report.json) |
| `.exe`, campanha global completa | 33/33, em 128,80 s; hash vinculado ao executável | [Campanha completa](../../release/windows/5.46.0-native-20261007-100616/global-validation/report.json) |
| `.exe`, estresse de controladores | 20 rodadas, 140 ciclos e 535 assertivas aprovados | Diretório `stress-validation` do build |
| Inicialização nativa | Seis casos aprovados, incluindo diferentes escalas de DPI | [Startup](../../release/windows/5.46.0-native-20261007-100616/startup-validation/report.json) |
| Aceite automático final | Integridade, repetição do self-test e estresse aprovados | [Aceite](../../release/windows/5.46.0-native-20261007-100616/native-acceptance/acceptance-report.json) |

A [matriz produzida pelo executável](../../release/windows/5.46.0-native-20261007-100616/global-validation/component-matrix.csv)
é a referência final da distribuição. Seus resultados reproduziram os 108
probes, 552 saltos e 120 recibos únicos da campanha fonte.

SHA-256 do `.exe`:
`1e4249c7b8f144d10d4b33e69c1f24d42934db0a50ed285b66b3f2e0e6a1d615`.
SHA-256 do ZIP:
`0821b354c41cb3e126fe527b46897a024eb19dc79da3f7907c6a4b9cb6c08f62`.

A repetição automática final de aceite foi aprovada. O relatório mantém
`manual_review_required`, revisão manual pendente e
`independent_machine_verified=false`: a aprovação em máquina independente e a
revisão manual permanecem fora deste ensaio local.

O [registro de preservação](../../outputs/global_network_20261007_v1/preservation.json)
confirma que nenhum dos 1.121 arquivos anteriores foi removido e que os hashes
do `.exe` e do ZIP 5.45 continuam iguais. O primeiro build 5.46 interrompido foi
retido com `DO-NOT-DISTRIBUTE.txt`; ele não é a distribuição aprovada.

## Limites operacionais

A campanha demonstra comunicação **clássica global** e protocolos quânticos
locais, incluindo dois receptores terrestres de um mesmo satélite e pares
detectados entre satélites. Detectores BBM92 consomem os fótons; encadear esses
recibos não estabelece entrelaçamento quântico global. Demandas que exigem
relay orbital coerente permanecem impedidas e informam a necessidade de
armazenamento/QND, BSM e executor de recursos apropriado. Relays ópticos
passivos e nós confiáveis exigem adaptadores e análise de segurança próprios.

**Chaves certificadas: zero.** Terminais de 2–4 m, divergências submicroradianas
e background favorável são hipóteses ambiciosas, explicitamente simuladas;
nenhuma instituição é apresentada como fisicamente conectada ao ORION-Q.
Orçamento clássico de missão, exclusão solar, atitude completa, clima observado,
calibração física e arbitragem de hardware compartilhado continuam dependências
externas. Os modelos ML permanecem opcionais e desligados por padrão; atuação
física continua bloqueada.

No ensaio de inicialização da demonstração, o primeiro snapshot chegou entre
11,6 e 12,0 s; o heartbeat da interface teve P95 de aproximadamente 203 ms e
pausa máxima de 1,516 s. Os watchdogs passaram, mas ainda há pausas breves sob
carga. Essas medidas não equivalem a qualificação em outros computadores nem
a uma promessa de animação contínua a 60 FPS.
