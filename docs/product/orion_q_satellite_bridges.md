# Ponte satelital, telemetria e GEO — ORION-Q 5.45

O satélite participa de uma sessão de distribuição de pares entre duas estações
terrestres. As estações pertencem a redes distintas; o serviço conjunto é
calculado a partir dos cliques recebidos nas duas OGS, com comunicação clássica
e pós-processamento BBM92. Uma linha cadastrada no mapa não comprova transmissão.

## Operação

1. Execute `ORION-Q.exe --demo` e abra Topology → GEO → Globo. Todos os satélites
   e suas órbitas permanecem visíveis. Escolha **SAT-ATLANTIC-MEO** no seletor
   ou clique no seu ícone.
2. A janela flutuante mostra órbita recebida, idade/validade, protocolo, caminho,
   coincidências entre UFRJ e BSC, fidelidade conjunta, QBER e pós-processamento.
   Os cartões das estações mostram canais clássico e quântico associados,
   elevação/azimute, distância oblíqua, Doppler, point-ahead e perdas.
3. **HOUSEKEEPING / SCIENCE · DETALHES** abre os valores e suas unidades. A janela
   continua acompanhando o snapshot do backend enquanto estiver aberta.
4. Em **PROTOCOLOS**, escolha **ATLANTIC-MEO-BRIDGE**. `unavailable` bloqueia o
   caminho óptico; `nominal` o restabelece após nova aquisição e processamento.
   Os cenários e parâmetros continuam configuráveis. Não há sucesso garantido.
5. A legenda inferior identifica os símbolos e traçados. Linha fina cinza
   pontilhada significa associação cadastrada; violeta contínua significa
   emissão no último bloco recebido. A órbita violeta tracejada é uma trajetória,
   sem significado de comunicação. As linhas também permitem inspecionar enlaces.

Na ausência de novos dados válidos, a ponte deixa de anunciar recepção e a
interface indica espera/indisponibilidade. Efemérides cadastradas são referências
para a simulação; observações vencidas não autorizam uma linha de emissão.

## Cenários e hipóteses

O cenário LEO **ATLANTIC-SATELLITE** foi preservado. Ele não consegue atender
simultaneamente às estações UFRJ e BSC com seus parâmetros. O novo cenário usa
**SAT-ATLANTIC-MEO**, uma fonte hipotética de pares em órbita MEO, para explorar
uma arquitetura de dois downlinks com visibilidade simultânea. Os nomes das
instituições referenciam os locais já cadastrados; não afirmam a existência
de OGS, equipamentos, conexões reais ou parceria com o ORION-Q.

| Parâmetro do novo cenário | Valor | Evidência / hipótese |
|---|---|---|
| Órbita | Circular de dois corpos, MEO a 10.000 km, inclinação 60° | Hipótese de engenharia; não é TLE de uma missão. Referência inicial no ponto médio esférico UFRJ–BSC. |
| Estações | UFRJ e BSC, nas coordenadas previamente verificadas do inventário | Localizações geográficas reais; terminais ópticos hipotéticos. |
| Arquitetura / protocolo | Fonte embarcada, dois downlinks, BBM92 | Distribuição em dois downlinks possui evidência experimental em LEO [Yin et al., 2017](https://arxiv.org/abs/1707.01339). Esse resultado não valida nosso perfil MEO. |
| Fonte / comprimento de onda | 5 milhões de pares/s; 850 nm | Parâmetros configurados da demonstração OGS v2; não são medições desses locais. |
| Receptor / divergência / jitter de apontamento | 4 m; 2 µrad; 0,3 µrad | Hipóteses exigentes para explorar o orçamento de dois braços longos. O perfil anterior de teste com 2 m e 2 µrad de jitter não produziu coincidências. |
| Eficiência óptica / detector | 0,5 / 0,8 | Hipóteses condicionais do modelo. Eficiência de detector não é transmitância de canal. |
| Perdas de inserção / profundidade óptica de zênite | 2 dB / 0,2 | Hipóteses configuráveis; geometria e atmosfera alteram o orçamento. |
| Elevação mínima | 20° | Limiar deste cenário; não é requisito universal. |
| Jitter temporal / blocos | 50 ps; blocos de 2 s; timeout 4 s | Parâmetros operacionais simulados, sujeitos à validação e ao relógio da rede. |
| Doppler | Compensação habilitada | Derivada da velocidade radial, comprimento de onda e resposta do modelo óptico; o residual é exibido. |

As maiores aberturas e menor jitter são identificadas como hipóteses, sem
esconder os requisitos físicos. Como comparação de escala, a OGS documentada
pela [ESA em 2022](https://www.esa.int/Enabling_Support/Space_Engineering_Technology/ESA_s_site_for_laser_and_quantum_links_marks_25_years)
usa um telescópio de 1 m. Essa referência não comprova um terminal MEO de 4 m.
A análise de [Zaunders e Ralph, 2026](https://arxiv.org/abs/2510.01633) compara
arquiteturas de distribuição sob canais ópticos realistas; trata-se de análise
teórica, distinta de qualificação de missão. Fontes consultadas em 07/10/2026.

Não extrapole uma taxa observada em um bloco para toda a órbita. A posição muda
com o relógio e a disponibilidade depende das duas estações, atmosfera, APT,
perdas, configuração e origem dos dados. Distância geográfica de cadastro e
distância oblíqua recebida são grandezas diferentes.

## Pipeline e contrato publicado

A fonte simulada passa pelo fluxo OGS v2 existente: aquisição separada de
housekeeping, tracking e cliques TDC; persistência; validação; sincronização;
coincidências; mensagens clássicas e recebimento; cálculo de métricas;
snapshot compartilhado; interface. Fontes de arquivo/stream utilizam os mesmos
contratos aplicáveis. Nenhuma janela gera fidelidade, chave ou sucesso final.
Veja [formatos, implementação e limites do gateway](orion_q_satellite_ogs_v2.md).

`LiveState.satellites[id].bridges` publica, para cada sessão:

- `session_id`, `endpoints`, `path`, `technologies` e `protocol`;
- `status`: `waiting`, `disabled`, `blocked`, `sampling` ou `observed_pairs`;
- estado/motivo do protocolo, origem, instante observado e métricas recebidas;
- `connections`: link, estação, estado de cadastro, IDs e parâmetros completos
  dos canais clássico e quântico, e sua `association_id`;
- escopo de segurança: teste de entrelaçamento/QKD, sem chave certificada.

`transmitting_links` continua reservado às emissões recebidas e válidas; não é
preenchido pela mera visibilidade orbital. A fidelidade de coincidências é uma
estimativa **conjunta**, não a fidelidade de um único braço óptico. Bloquear um
canal obrigatório ou dispositivo interrompe o serviço e a emissão anunciada.

A atualização do inventário da demonstração é aditiva, registra o estado anterior
no diário e não altera os nodes/enlaces existentes. Um marcador persistente
impede recriar a ponte quando o operador a remove. IDs conflitantes recebem um
sufixo; a topologia comum do operador não é convertida para o cenário institucional.

## Limites e reprodução

A ponte implementada entrega observações de pares e processamento de teste BBM92.
Ela não encaminha dados de aplicações, não implementa relay confiável de chaves,
memória satelital para recepção não simultânea ou enlaces entre satélites.
Chaves certificadas permanecem em zero. Abertura de gateway é somente recepção;
hardware, ICD de missão, calibração e segurança composável exigem qualificação
externa. Modelos ML seguem opcionais e a atuação física permanece bloqueada.

```powershell
$env:PYTHONPATH = 'src'
$env:QT_QPA_PLATFORM = 'offscreen'
.audit-winvenv\Scripts\python.exe -m pytest tests/test_console_satellite_bridges.py tests/test_console_geo_modes.py -q --basetemp outputs\pytest-ponte-novo
ORION-Q.exe --self-test C:\temp\orion-ponte-novo\report.json
```

Use diretórios novos. No diagnóstico congelado, confira
`communicators-geo/satellite-bridge/report.json`: os testes percorrem nominal,
indisponibilidade e recuperação; abrem a janela por clique no globo, inspecionam
telemetria atualizada e verificam os eventos persistidos. As capturas documentam
o fluxo simulado, sem alegar recepção física.
