# Séries de conexão, apresentação ao vivo e FPGAs

ORION-Q 5.56.0 · 8 de outubro de 2026.

## Gráficos por protocolo

US-GOOG-QA corresponde ao ponto GOOGLE-QAI e participa de US-SATELLITE no cenário global atual. Não participa de uma sessão TF-QKD nesse cenário. O gráfico legado de CIMATEC mostra a observação de polarização `1 − 2·QBER`; por isso não era utilizado em enlaces administrados por outros protocolos.

O painel **Connection observables** mostra séries publicadas pelo backend, selecionáveis por sessão, tecnologia e métrica. TF-QKD apresenta QBER observado, resíduo de fase, bits sifted, yield estimado e estimativa condicional de chave. Satélite apresenta disponibilidade, coincidências e QBER quando recebidos. Repetidores, memória e purificação apresentam suas métricas próprias. Não se convertem taxas em uma onda óptica ou em fidelidade de Bell. A estimativa condicional de chave não é material secreto certificado.

`technologies.protocol_series` contém `key`, `session_id`, `technology`, `metric`, `label`, `unit`, `node_ids`, `origin`, `source_id`, `scope`, `bounds`, `clock_domain`, `samples`. Cada ponto contém tempo do protocolo, valor ou `null`, motivo e referência de publicação. Pontos de recebimento são deduplicados; métricas de ocupação e médias da sessão seguem o relógio correspondente. O histórico é limitado a 240 pontos por série, 120 s do protocolo e 2048 séries. Troca de fonte/rota ou reinício do relógio invalida o histórico correspondente.

## Atualização e exportação

A thread científica mantém sua frequência e a ordem causal. A interface consulta o descriptor publicado a cada segundo e copia métricas completas apenas quando o tick muda. Cadastros, relatórios e recibos completos são relidos em cadência separada de quatro segundos; comandos e atualização explícita fazem readback completo. Uma leitura lenta de relatório não bloqueia o worker de apresentação ao vivo. Somente fotografias de apresentação são agrupadas; aquisição, processamento e persistência não descartam eventos para acelerar a interface.

A admissão de cliques transporta a identidade ao worker sem acessar o cofre na thread Qt. O worker revalida identidade, revogação e permissões antes da operação. Capacidades já publicadas controlam os botões; não concedem autorização de backend.

**EXPORT EVIDENCE**, os exports de Telemetry e Audit e a exportação de captura de Command history permitem escolher um arquivo `.json` novo. Cancelar não grava. Uma gravação concluída mostra o caminho. Arquivos existentes não são substituídos. O contrato aceita `{"scope":"qkd","destination":"C:/pasta/arquivo-novo.json"}`; a forma antiga de escopo simples continua gravando no diretório de estado. Exportação de evidência exige `evidence.export`; dados medidos e fixtures preservam sua identificação de origem.

As tabelas de Housekeeping/Science e registros usam viewport opaco e repaint completo da área visível após rolagem. O título Fidelity by link ocupa sua própria linha, separado dos controles, permitindo leitura em larguras menores.

## Edge decision e FPGAs

**Edge decision → FPGA devices** recebe inventário, configuração e feedback do backend. O cenário institucional cadastra três dispositivos software nos primeiros repetidores compatíveis de CIMATEC–CIQUANTA. Cadastros persistem no SQLite do workspace, com revisão e SHA-256. Os dispositivos possuem identificador, repetidor receptor, adaptador, clock do modelo em Hz, ciclos do pipeline, timeout em segundos, intervalo de novas tentativas em ticks e política de reconexão.

Fluxo: fonte de observações brutas → aquisição do adaptador FPGA identificado → envelope com sessão, sequência, associação dos dois canais e hashes → validação → ChannelIntake com transmissão e recepção clássicas → LinkBank e protocolos existentes → feedback e recibo persistido → publicação → interface. Nenhum estado de node, QBER, fidelidade ou chave final é preenchido pelo painel ou pelo adaptador.

Operações: `register_fpga_device`, `configure_fpga_device`, `set_fpga_connection`, `inject_fpga_fault`. Configuração e conexão exigem revisão observada e permissões de rede/adaptadores; aquisição exige `adapters.acquire`. Falhas injetadas são exclusivas de fontes e adaptadores simulados: timeout, desconexão, envelope inválido e overrun. Reconexão exige handshake e observação nova. Configuração ou desconexão invalida observações dos spans afetados até nova aquisição; contadores e hashes permitem acompanhar o resultado.

A duração modelada `pipeline_cycles / clock_hz` afeta admissão e timeout no tempo da rede. O transporte possui capacidade de um período de entrada; pipeline acima dessa capacidade produz overrun explícito, sem fabricar entrega ou bloquear a thread com espera física.

O adaptador **physical-contract** declara indisponibilidade e bloqueia aquisição. Esta versão não programa bitstreams, não escreve registradores de equipamentos e não realiza atuação física. O dispositivo software não é uma simulação RTL ou FPGA caracterizada em bancada. Temperatura, margem de timing, latência medida no equipamento e atestação de bitstream são indisponíveis, explicitamente apresentadas como tais.

## Reprodução

```powershell
ORION-Q.exe --ui-pipeline-test C:\temp\orion-ui-pipeline-novo
python tools\benchmark_console_performance.py --executable caminho\ORION-Q.exe --output outputs\benchmark-novo --duration 20 --soak 3 --size 3440x1440
python tools\benchmark_acquisition_copy.py --output outputs\copia-observacoes-nova.json
```

Os diagnósticos utilizam estado novo e identificado, sem atuar sobre o workspace do operador. O relatório do teste de interface verifica publicação de observáveis, controles FPGA, exportação e geometria usando Qt e os mesmos serviços da aplicação.

Referências físicas: [Lucamarini et al., Nature 2018](https://doi.org/10.1038/s41586-018-0066-6), [Yin et al., Science 2017](https://doi.org/10.1126/science.aan3211). O primeiro fundamenta TF-QKD e seu processamento específico; o segundo fundamenta distribuição satelital de entrelaçamento. Nenhuma taxa ou fidelidade desses experimentos é prometida como resultado do cenário simulado.
