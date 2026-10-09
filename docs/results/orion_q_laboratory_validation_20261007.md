# Validação de laboratório do ORION-Q em 7 de outubro de 2026

Atualização 5.49: a integração IBM Quantum foi retirada após esta auditoria.
Referências a IBM neste documento descrevem exclusivamente o estado anterior;
os resultados laboratoriais e suas evidências foram preservados. Consulte a
[retirada da integração](orion_q_ibm_removal_20261007.md).

A jornada experimental passou pelo backend HTTP e pelo controle de aquisição do console nativo, com registros persistidos, origem explícita, repetição das métricas e recuperação das falhas injetadas. A bancada de fótons usa um modelo identificado; a sessão QKD entrega somente chaves emuladas de teste. Foram reproduzidos registros experimentais agregados de Bristol e de uma publicação QKD. **Nenhum equipamento físico foi validado nesta instalação, nenhum job IBM externo foi submetido e nenhum bit recebeu certificação de segurança QKD.**

## Evidência principal e critério de sucesso

O resultado integrado está em [integration_report.json](../../outputs/orion_lab_20261007/integration_complete/integration_report.json). O run HTTP é `5209de6c-5827-411a-b2bc-9d83944147ce`; a aquisição repetida pelo botão nativo é `c1c2a3f7-39bc-499a-8be0-63e2f9f9a645`. Os recibos correspondentes ficam no journal e nas pastas `integration_complete/laboratory/<run_id>/`. Cada recibo conserva configuração, resultados, vínculos aos arquivos e SHA256; o manifesto de cada aquisição conserva eventos e domínio temporal.

As cinco verificações integradas passaram: execução por HTTP, emissão pelo botão nativo, leitura do mesmo journal, igualdade de contagens/coincidências/histogramas na repetição e recuperação dos recibos após reiniciar o serviço. O [render da tela nativa](../../outputs/orion_lab_20261007/integration_complete/native_laboratory.png) foi inspecionado visualmente. Esse render e os testes de widgets não qualificam equipamentos físicos.

O CLI repetiu a bancada completa em `cli_reproduction`, com run `d9da9ff7-3407-44f5-8ad3-62e24b37f501` concluído. Após a apresentação do resumo de métricas no painel, 32 testes adicionais de serviço/telas passaram em 17,50 s. O `git diff --check` global encontrou whitespace em alterações prévias de documentos fora desta tarefa; elas foram preservadas.

O critério foi atendido para **jornada rastreável em emulação e replay agregado**, com melhorias demonstradas por regressões antes/depois. A validação de integração física exige identificação das unidades, SDKs/licenças, manuais e aquisições efetivas que não estavam acessíveis.

## Inventário da arquitetura e do ambiente

| Camada | Implementação encontrada | Fronteira de validação |
| --- | --- | --- |
| Aplicação nativa | PySide6, `console.app`, `ConsoleWindow`, `ServiceProvider`, `Snapshot`, telas | Fontes e capacidades explícitas; chamada em processo ao backend |
| Serviço e HTTP | `AssuranceDashboardService`, `qkd/dashboard_api.py`, loopback porta padrão 8765 | API de aplicação; não protocolo de instrumento |
| Controle | Runtime/policy/supervisor, SQLite, inventário, canais, recibos | Simulação/shadow; executor físico recusa atuação sem adaptador |
| Rede | `NetworkEngine`, plantas ópticas/QKD/sincronismo, registry de modelos | Observações e modelos identificados; `key_pool_bits` é orçamento numérico |
| Protocolos | Fontes, contratos, aquisição, processamento, persistência e fontes substituíveis | Satélite/ISL, TF-QKD, memórias, purificação e swapping com hipóteses declaradas |
| Dados medidos | Adaptadores Bristol, Santanna/WDМ, conjuntos QKD públicos e agregados | Granularidade e referência temporal específicas; não inventam cliques ou fidelidade |
| IBM | Catálogo, preparação, executor, ledger e CLI | Jobs falsos nos testes e Aer local; Runtime real não instalado no ambiente principal |
| Laboratório adicionado | `lab/photon.py`, `adapters.py`, `qkd.py`, `kms.py`, `service.py`, CLI, painel nativo | Aquisição/replay normalizados e interface ETSI 014 local com mTLS |

O [inventário executado](../../outputs/orion_lab_20261007/inventory.json) catalogou 97 arquivos de dados/configuração com hashes. O runtime principal reportou Python 3.12.14, NumPy 2.5.2, PySide6 6.11.2, SGP4 2.27 e cryptography 46.0.7. TimeTagger, pyserial, PyVISA, PyVISA-sim, qiskit-ibm-runtime e LabOne Q não foram encontrados nesse runtime. Existe ambiente LabOne separado em `work/laboneq-env`; sua existência não identifica instrumento conectado.

O filtro Windows de dispositivos presentes encontrou `USB Serial Device (COM5)` e `Communications Port (COM1)`. Não houve associação validada dessas portas a fonte/detector QKD, nem envio de comandos. A observação PnP é limitada: não demonstra ausência de equipamentos em outra máquina ou endpoint de rede. Nenhum endpoint de instrumento foi identificado nas configurações examinadas. Firmware das unidades e contratos proprietários completos permanecem dependências.

`acquisition/paired-qkd-001` é um scaffold de coleta, explicitamente sem feed de hardware, e não foi contado como aquisição válida. Os números de instrumentos em `MockProvider` são fixtures declaradas de UI, não identificação real.

## Modos por equipamento e experimento

| Dispositivo ou experimento | Modo executado | Evidência e limite |
| --- | --- | --- |
| Fonte SPDC810N como referência | Emulação | 810 nm, USB somente como transporte documentado da referência; nenhum comando proprietário |
| Fonte SPDC810 na substituição | Emulação | Referência RS232, largura espectral revalidada, canais 3/4 e recalibração |
| Detectores dos braços A/B | Emulação | Banda 780–850 nm, eficiência, jitter, dead time e pulsos são premissas do modelo |
| Time Tagger da bancada nova | Emulação | Canais 1/2, timestamps int64 ps, buffer e qualidade explícitos |
| JSONL ORION | Replay de simulação | Eventos, configuração e relógio originais preservados; origem continua simulated |
| Bristol HCF | Replay experimental agregado | CSVs/histogramas adquiridos com Photon Spot SNSPD e Swabian Ultra; hashes verificados |
| Swabian nativo local/remoto/virtual | Interface experimental e teste por stub | Nenhuma conexão física ou replay nativo licenciado; SDK indisponível |
| Clavis XGR e Toshiba MU | Referências documentais | Fronteiras públicas pesquisadas; nenhum appliance disponível para teste |
| Alice/Bob QKD e suas KMEs | Emulação de protocolo; HTTPS/mTLS real em loopback | Bytes de teste previsíveis; 0 bits certificados; pool em memória |
| Micius | Referência histórica | Parâmetros do experimento publicado, sem operação de missão/estação |
| Satélite Rio e OGS | Emulação de modelo/interface | Timestamps, geometria e packets origin simulated; não replicam a passagem Micius |
| ISL | Cenário hipotético e replay de fixture simulada | Referência Micius não comprova terminais satélite–satélite |
| IBM | Aer local e fake jobs | Fluxo assíncrono e coleta exercitados; nenhuma distribuição óptica entre processadores |

## Instrumentos e separação dos caminhos

Os diagramas e contratos completos estão no [guia de laboratório](../product/orion_q_laboratory.md), [referências ópticas](../research/orion_lab_instruments_20261007.md), [QKD/ETSI](../research/orion_lab_qkd_20261007.md) e [satélite/IBM](../research/orion_lab_satellite_ibm_20261007.md).

A fonte emulada produz pares correlacionados em dois braços ópticos; a detecção produz pulsos elétricos modelados, que entram nos canais de temporização. O controle clássico cadastra/configura/calibra adaptadores. A entrega ETSI 014 começa no pool KME e termina nas aplicações. Esses caminhos têm contratos independentes e não usam HTTP como transporte presumido de USB/serial/SDK.

Swabian documenta `createTimeTagger()` por USB, `createTimeTaggerNetwork()` por SDK/TCP e `createTimeTaggerVirtual()` para registros FileWriter, com licença gratuita específica por PC. O caminho nativo desta instalação continua bloqueado pela falta de SDK/licença, metadados normalizados e blocos de dados. [API oficial](https://www.swabianinstruments.com/static/documentation/TimeTagger/api/TimeTaggerLibrary.html).

Thorlabs distingue SPDC810 RS232 e SPDC810N USB. A compatibilidade configurada em 810 nm não pode ser extrapolada ao Toshiba MU telecom; a referência MU publica canal quântico 1310 nm. Single Quantum separa controle LAN dos pulsos SMA e oferece conversor TTL adicional. Níveis elétricos e acoplamento precisam ser verificados para cada unidade. QNET REST é documentado na família IDQ XGR para gestão, mas rotas/esquemas não foram inventados. [Thorlabs](https://punchout.thorlabs.com/newgrouppage9.cfm?ad=SPDC810N&objectgroup_id=13675), [Single Quantum](https://www.singlequantum.com/wp-content/uploads/2024/12/Single-Quantum-Operation-Principle-print.pdf), [Toshiba MU](https://www.toshiba.eu/solutions/quantum/products/quantum-key-distribution/multiplexed-qkd-system-mu/).

PyVISA-sim foi considerado como backend de diálogos para instrumentos VISA documentados. Nenhum instrumento VISA compatível foi identificado na bancada; não foi atribuído SCPI aos equipamentos listados. Seu uso futuro deve complementar o modelo físico. [Documentação oficial](https://pyvisa.readthedocs.io/projects/pyvisa-sim/en/latest/).

## Resultados rastreáveis

| Grandeza | Resultado | Origem e interpretação |
| --- | ---: | --- |
| Duração da bancada de fótons | 0,1 s | Modelo `orion-q.spdc-poisson-loss-jitter-deadtime.v1`, seed 810 |
| Contagens canais 1/2 | 1445 / 1409 | Eventos armazenados em JSONL, relógio relativo em ps |
| Coincidências | 517 | Janela total 500 ps, intervalo [-250,+250) ps |
| Acidentais estimados / janela deslocada | 0,010180025 / 0 | Poisson de singles / deslocamento 20.000 ps; expectativa pequena nesta duração |
| Entrelaçamento | Não caracterizado | Coincidências não bastam; exigem preparação, analisadores e tomografia/testemunha definida |
| Bristol | 12 linhas e 3 histogramas | Replay agregado físico, sem reconstrução de timestamps individuais |
| Picos Bristol AB/AC/BC | 0 / 0 / −10 ps | Histogramas publicados; não calibração nova da bancada |
| QKD detectados / selecionados | 27.960 / 13.857 bits | BB84 ideal single photon PCG64, seed 20261007, 1 s |
| QBER da amostra | 1,0104% | 3464 bits de estimação descartados antes da reconciliação |
| Vazamento / orçamento condicional / PA | 2254 / 4293 / 4096 bits | Paridades/verificação e Toeplitz executados sob hipóteses emuladas |
| Taxas bruta / selecionada / condicional | 27.960 / 13.857 / 4096 bit/s | Duração do modelo; taxa certificada 0 bit/s |
| Entrega e consumo | Alice 10 chaves, Bob 1; buffer Alice 0 | HTTPS/mTLS TLS 1.3, consumo atômico, correspondência por UUID |
| Aplicação | Payload de 35 bytes recuperado | AES-GCM cryptography; adulteração recusada |
| Concorrência | 9 IDs únicos e 3 recusas / 12 solicitações | Falta de chaves não repõe material; nenhuma duplicação |
| Satélite nominal | 136 coincidências, 65 selecionados, QBER 1,5385% | Modelo identificado, seed 42, 2 s; estimativa condicional 0 |
| Satélite ampliado hipotético | 5919 coincidências, estimativa 398 bits | Hipótese de recursos/6 s; não são chaves certificadas nem entregues |
| IBM Aer | Fidelidade ideal 1,0; ruidosa 0,982422/0,992188 | 2 layouts, 256 shots/base; execução local, sem QPU |

A taxa de disponibilidade medida das duas requisições válidas da aplicação foi 2/2. A latência média de 11 entregas bem sucedidas na jornada integrada foi aproximadamente 24,14 ms; trata-se de observação local com carga concorrente, não SLA nem benchmark de appliance. Os relatórios conservam mínimo, máximo, duração e denominadores.

Os dados Bristol preservam limites do depósito: o README identifica `.ttbin` como **cabeçalho**, com 21,6 GB de cliques não depositados. O arquivo de 13.936 bytes confere com o SHA1 e o tamanho originais. A série rotulada 180 minutos contém 12 linhas com offsets publicados de 0 a 6600 s; o código preserva esses valores e registra a divergência entre legenda/documentação e conteúdo, sem inventar os seis chunks restantes.

## Defeitos comprovados e correções

| Falha | Evidência antes | Correção e repetição | Nível |
| --- | --- | --- | --- |
| Último par de cluster ambíguo aceito como coincidência única | Matcher devolvia 2 para fixture com somente 1 par isolado | Considerar também vizinhos anteriores; devolve 1 e rejeita 3 gates ambíguos | Protocolo de análise temporal |
| Métricas IBM atuais descartadas | `qpu_charge_time_seconds` e `circuits_execution_time_ns` omitidos | Preservar cobrança, execução e timestamps separados, com unidades | Interface de coleta por fake job |
| OGS simulado rotulado physical | `origin=simulated` solicitado e physical publicado | Store/validador respeitam origem; packets, snapshot e export simulated | Interface e rastreabilidade |
| Raw satelital perdia origem/modelo no replay | Fixture gerada sem proveniência científica estruturada | Modelo/origem machine readable preservados; epoch e tags idênticos no replay | Modelo e replay de simulação |
| JSON HTTP aceitava campos duplicados | Parser anterior usava o último valor; teste de contrato falhou | Rejeição por `object_pairs_hook` antes de mutação; mesma regressão passou | Protocolo de configuração HTTP |
| Latência de loopback na bancada nova | Cliente localhost tentava IPv6 contra escuta IPv4; média 2053,62 ms | IP da escuta + IP SAN mantendo CA/hostname/identidade; 16,13 ms na repetição isolada | Transporte real local; não defeito de appliance |

Os quatro defeitos satélite/IBM têm logs em `satellite_ibm/regressions_before.txt`, `provenance_before.txt`, `model_origin_before.txt` e respectivas execuções depois. A comparação HTTP está em `duplicate_before.txt`/`duplicate_after.txt`; para isolar a causa, foi restaurado temporariamente apenas o parser anterior, mantendo a nova rota de diagnóstico, e depois reaplicada a correção. As latências da bancada nova estão em `qkd_initial_report.json` e `qkd_corrected_report.json`.

Foram injetados overflow, saturação/perda de eventos, regressão temporal, perda de lock, deriva de 50 ppm, desconexão, detector indisponível e aumento de perda. Os sete casos da bancada de fótons foram detectados, reconectados, reconfigurados e recalibrados; **cada recuperação possui seu próprio manifesto e eventos**. Registros rejeitados por regressão permanecem separados do schema aceito, para reprodução forense.

ETSI 014 foi exercitada com certificados expirados/ausentes/CA desconhecida, identidade SAE incorreta, identidade KME inesperada, chaves desconhecidas/consumidas, requisições duplicadas, esgotamento, concorrência e reinício. Dezesseis verificações passaram. Reinício do transporte não reemite chaves consumidas; reinício completo começa vazio. PKI é temporária; chaves e material privado não integram logs ou evidências.

## Testes e limites restantes

O relatório JUnit [final_lab.xml](../../outputs/orion_lab_20261007/final_lab.xml) registra **152 testes aprovados**, incluindo os 41 da bancada de fótons, 10 QKD, serviço/API/UI, capacidades, dados públicos e telemetria pareada. A validação satélite/IBM passou em 177 testes; após a extensão final de proveniência, outros 73 testes satélite/replay passaram. Dez testes Qt/regressões também passaram. Esses grupos se sobrepõem e não devem ser somados como testes únicos. O pipeline satelital final aprovou 21/21 verificações.

O renderer da janela completa teve dois timeouts de inicialização de 40 s durante as primeiras tentativas. Uma repetição do mesmo renderer, sem alterar o núcleo, passou em 8,27 s; um probe separado recebeu o snapshot em 3,157 s sem erro do provider, slot Qt ou stepping. A causa dos timeouts não foi isolada, portanto não foi apresentada como defeito corrigido. O harness agora grava estado do future/provider em eventual novo timeout. A [captura final da janela completa](../../outputs/orion_lab_20261007/integration_complete/native_console.png) foi inspecionada; o trace fica em `outputs/orion_lab_20261007/probe_qt_startup.json`.

Ruff passou nos módulos novos, testes e ferramentas de laboratório. Para os módulos satelitais compactados, os checks de correção F/E4/E9 passaram; 156 ocorrências de estilo E701/E702 já existentes foram registradas separadamente, sem reformatação ampla. A suíte inteira de treinamento/modelos do projeto não foi reexecutada; foram escolhidas as áreas afetadas e suas integrações.

Os obstáculos locais de execução foram distinguidos de defeitos do ORION-Q: inicialização do sandbox falhou antes de ler arquivos; execução fora dele foi aprovada. O Git exigiu exceção `safe.directory` limitada a cada comando; o venv movido exigiu `PYTHONPATH=src`; o diretório pytest global tinha acesso negado, resolvido usando `--basetemp` novo dentro de outputs. Nenhuma configuração global foi alterada e as alterações prévias do checkout foram preservadas.

Para hardware efetivo faltam: unidades e endpoints identificados; contratos/firmwares de controle IDQ/Toshiba/Thorlabs/Single Quantum; SDK/licença Swabian e dados FileWriter completos; medições espectrais, níveis elétricos, conectores, relógio e calibração da bancada. Para uma passagem Micius faltam efeméride/TLE com época apropriada, estação acessível, APT e registros originais. Para IBM físico faltam Runtime e acesso autorizado; uma chamada de circuito remoto continuaria distinta de interconexão óptica.

ETSI 020 foi avaliada conforme a versão pública de junho de 2026: troca horizontal de material entre KMS no mesmo nó confiável. Não foi implementada nem usada para alegar provisionamento óptico ou conexão remota entre appliances. [Especificação oficial](https://www.etsi.org/deliver/etsi_gs/QKD/001_099/020/01.01.01_60/gs_qkd020v010101p.pdf).

## Entrega e repetição

[Guia e contratos](../product/orion_q_laboratory.md), [configuração reproduzível](../../configs/lab/bench_v1.json), [inventário](../../outputs/orion_lab_20261007/inventory.json), [métricas](../../outputs/orion_lab_20261007/metrics.json), [manifesto SHA256](../../outputs/orion_lab_20261007/evidence_manifest.json) e [pacote de evidências](../../outputs/orion_lab_20261007/orion_lab_evidence.zip).

O pacote contém configurações, relatórios, logs de regressão, dados simulados, replay agregado, snapshots e hashes das fontes alteradas; não contém material de chave ou PKI privada. Hashes demonstram integridade dos arquivos, sem autenticar autores ou certificar a física. O comando de reprodução da jornada é `python -m quantum_autopilot.lab.cli --experiment all --output <diretório-novo>`; a execução integrada por HTTP e console usa `tools/run_lab_validation.py`.
