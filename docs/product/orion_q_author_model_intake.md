# Admissão offline dos modelos do autor

Incremento de código-fonte em 22/09/2026; não é uma nova versão do executável.
Integração parcial de Q16, preparatória para Q05. Não altera topologia, journal,
AppData, capacidades, fontes da interface ou políticas ativas.

## O que foi recebido e o que está disponível

| Componente | Contrato identificado | Estado nesta entrega |
|---|---|---|
| Detector QKD | Boosting; 17 features; baseline 60 períodos; janela 30; limiar/histerese para 1 s | Inventariado; pickle bloqueado, sem inferência |
| Agente de taxa de chave | Ator 16-64-64-9; observáveis decoy-state; temperatura 0,25 | Adaptador opcional offline; features, probabilidades e caixa de ações conferidas em 40 períodos |
| Sincronizador QKD | Ridge; 8 offsets e temperaturas; passo 1 s; horizonte 2 s | Adaptador offline e 593 previsões conferidas |
| Compensador | Ridge/MLP; 48 features; SO(3); 2 ms / 40 ms; desenvolvimento e calibrado | Quatro checkpoints offline; 40 mil saídas sequenciais e 48 janelas conferidas |
| Roteamento/temporização | Ator 34-64-64-1, crítico 11-64-64-1; 11 features de estado + 23 por ação | Dois checkpoints conferidos; sem construtor físico de features |
| Alocador híbrido | Lei de controle física; previsão por persistência ou Ridge; 11 enlaces; passo 5 s; fila de atraso 30 s | Duas opções offline; 1.440 passos e 20 snapshots conferidos sem divergência |

O novo `qnet_network.zip` inclui roteamento e alocação, ausentes do ZIP anterior
que continha somente o compensador. O artigo de rede também aborda blocagem
de chave não contígua, mas esse controlador não foi identificado como componente
exportado no ZIP. Não é uma função implementada nem uma prova de segurança.

**Continuação em 22/09:** todos os modelos são opcionais no produto final, OFF
por padrão. Já existe [catálogo/fábrica sob demanda](orion_q_optional_models.md)
para avaliação por código, sem ativação na UI. O [relatório do compensador](../results/orion_q_optional_compensation_20260922.md)
registra as verificações desta continuação.

O [agente de taxa de chave](orion_q_optional_keyrate.md) e as duas versões do
[alocador híbrido](orion_q_optional_allocator.md) também foram admitidos: dez
configurações offline, incluindo a persistência não aprendida. Não houve ativação automática.

## Executar

No ambiente de desenvolvimento do repositório, com o pacote instalado e NumPy:

```powershell
python -m quantum_autopilot.model_intake.evaluation `
  --qkd-zip "C:/Users/alexa/Downloads/modelo_qkd_ml.zip" `
  --qnet-zip "F:/qnet_network.zip" `
  --output "outputs/author-model-replay/new-report.json"
```

Os caminhos são argumentos, não dependências fixas. Não requer pandas,
scikit-learn, torch, internet, backend Qt, permissões de hardware ou treinamento.
Usar outro caminho de saída em cada execução: o relatório não é sobrescrito.
Código de saída 0 significa paridade aprovada, 1 divergência numérica/decisão,
2 rejeição de entrada ou falha de acesso. Exceções inesperadas não geram aprovação.

O JSON contém ambiente Python/NumPy, hashes do código dos adaptadores, SHA-256 dos
dois arquivos e de cada membro, tolerâncias, contagens, divergências, limitações
e componentes ainda indisponíveis. Não contém credenciais nem comandos.

### Identidades admitidas

- QKD: `94352f15b6dcb132a3c03559b9d94b28f42b6e9adb8b2cdaab18f46720a55a79`
- Quantum Network: `6b8563209f54a2d566f1a4229b140b75fda93555085ff2bf6bfcad8f07abd938`

Alterar/recompactar um ZIP exige nova revisão: não existe `--ignore-hash`.
O limite externo é 32 MiB, com até 200 membros e 64 MiB expandidos;
NPZ admite até 32 membros e 4 MiB expandidos. A leitura é feita do mesmo snapshot
em memória que foi verificado, sem extração, importação dos scripts fornecidos,
`exec`, subprocessos dos anexos ou desserialização de pickle. Pesos usam
`allow_pickle=False`; objetos, strings, complexos e números não finitos são recusados.
Máscara booleana `truth_valid` é admitida explicitamente apenas pelo leitor do
stream de avaliação; pesos continuam exigindo arrays numéricos reais. Apenas o
campo `urgency_s` do stream do alocador admite +infinito explicitamente; NaN e
-infinito são sempre recusados. Essa exceção não é aplicada aos pesos.
O hash comprova identidade dos bytes revisados, não autenticidade externa ou validade científica.

## Contratos de avaliação

`RoutingReplayModel.evaluate` exige domínio `simulated_offline`, RNG explícito,
estado com 11 números, matriz n x 23 e n descrições de ações na mesma ordem.
Rejeita conjunto vazio, tamanho incompatível e desacordo entre tipo da ação e
one-hot. Nominal preserva amostragem T=1; prazo longo usa argmax T=0, sem consumir
RNG. O resultado distingue logits, probabilidades, retorno do crítico, índice
guloso e índice escolhido. Nenhum desses números é fidelidade prevista.

A conferência do tipo da ação **não** valida existência de pares, recursos,
conectividade, deadline ou legalidade física. Tampouco verifica o significado
físico de cada feature. Por isso este adaptador não entra no runtime: lê somente
features pré-computadas exportadas do simulador original. Antes da integração
shadow em tempo de execução é necessário um construtor de features auditado e
uma verificação independente das ações legais.

`SyncReplayModel.predict` exige exatamente oito timestamps ordenados, offsets
em segundos e temperaturas em graus Celsius. Só admite cadência de 1 s e alvo
a +2 s. Não preenche os sete passos de warm-up, não redimensiona janelas e não
aceita horizonte diferente silenciosamente. Retorna também persistência e
timestamp-alvo. Os adaptadores recusam domínio medido/físico e sempre
marcam `physical_action_performed=false`.

O relatório testa os exemplos do autor: não estima generalização nem retorna
uma calibração de incerteza. Não há conversão de BER/QBER/SKR em Bell fidelity,
ajuste de `KAPPA_DEPOL_FROM_BER`, retreino ou substituição dos modelos WDM existentes.

`CompensationReplayModel` aceita janela completa sem estado ou stream de matrizes
de rotação SO(3), com covariáveis `[temperatura_C, dT/dt_K_s, vibração_RMS_rad]`.
Exige regime explícito, 400 fótons de monitoramento/base, horizonte 40 ms e
cadência 2 ms. Timestamps devem usar um relógio relativo em segundos com precisão
suficiente para essa cadência. Os primeiros 15 passos são persistência
identificada. Mudança de identidade de campanha/dispositivo/canal ou lacuna de
tempo exige `reset()`; amostra rejeitada não altera a janela. Regime declarado
não equivale a detecção automática de OOD. Não é feita aplicação de transposta
em atuador, nem conversão da rotação em fidelidade quântica.

## Restrições científicas extraídas dos materiais

1. Os modelos foram treinados em simulação. Estatísticas de campo usadas na
   física não transformam trajetórias simuladas em treinamento experimental.
2. O sincronizador precisa de persistência como baseline. O README fornecido
   registra vantagem desprezível contra ela; a tabela 8 do artigo compara outros
   rastreadores e não inclui essa referência. O replay mantém essa distinção.
3. O roteador não supera a melhor heurística no regime documentado. Sua utilidade
   inicial no ORION-Q é como candidato/comparador, não como upgrade comprovado.
4. O ganho de 2,7x do alocador é uma alegação do artigo sobre controle versus
   admissão estática, com serviço de chave igualado; a documentação do pacote
   informa persistência nessa comparação e ausência de vantagem relevante do
   Ridge. Não atribuir o ganho à aprendizagem.
5. Compensação é a prioridade para testar ganho de ML: a tabela 2 relata vantagem
   de Ridge/MLP sobre alfa-beta ressintonizado no regime calibrado, mas também
   degradação abaixo de não compensar quando se transplanta o modelo de outro
   regime. Não carregar os pesos de 40 Hz como substitutos dos de 60 Hz.
6. O alocador usa planta de fóton único e resíduos do ponto de desenvolvimento;
   o agente QKD usa decoy-state e saturação. Unificar entradas requer uma ponte
   explícita, não somente renomear campos.
7. No agente QKD, as features 7 e 13 usam a taxa do mesmo período por comportamento
   do ambiente de treino. Normalmente coincidem, mas os pisos dos logaritmos
   são distintos e produzem valores diferentes com taxa zero. Preservar as duas
   expressões, não copiar uma na outra nem corrigir o treinamento silenciosamente.
8. `1-P(normal)` do detector inclui degradações benignas; não é probabilidade
   demonstrada de espionagem. O detector FPGA podado tem pesos/desempenho distintos.
9. O artigo de rede classifica blocagem não contígua como resultado de
   contabilidade ainda sem prova de segurança. Nada aqui autoriza uma oferta de
   chave criptograficamente segura com base nessa contabilidade.

Essas são conclusões sobre os documentos recebidos, não uma verificação externa
de publicação, revisão por pares ou reprodução integral das tabelas.

## Sequência seguinte

1. Concluído neste incremento: port/paridade do compensador nos dois regimes,
   inclusive warm-up e rejeição de horizonte/cadência incorretos. Avaliação em
   laço fechado com baselines e novo ambiente continua pendente.
2. Agente QKD e alocador admitidos offline; fila, planta e contabilidade
   explicitamente separadas. Próximo incremento de produto: catálogo nativo
   com estados OFF/offline/bloqueado claros, sem ativação automática.
3. Obter export seguro do detector; confirmar condições de redistribuição dos
   novos pesos/códigos antes de empacotá-los no `.exe` ou em repositório público.
4. Catálogo somente leitura na interface com componentes aptos/pendentes,
   hashes, domínio, baseline e motivo da recusa; integração shadow sem atuação.
5. Só então preparar Q05: seleção de política ativa, revisão, confirmação por
   leitura e rollback, mantendo supervisor independente e limites físicos.
