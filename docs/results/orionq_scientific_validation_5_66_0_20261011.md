# OrionQ 5.66.0 — validação científica em execução otimizada

Na reprodução da versão 5.65.0, as três configurações inválidas da auditoria foram rejeitadas no Python normal, mas aceitas com `-O`: `PhysicsConfig(T1=1,T2=3)`, probabilidade de despolarização `1.1` e `QuantumNoiseChannel(T1=1,T2=3)`. Os arquivos de reprodução e os hashes anteriores foram preservados em `outputs/release-5.66.0-20261011/science`.

A correção substitui os 17 `assert` científicos/de estado e os 15 `assert` do diagnóstico da interface por verificações explícitas, que continuam executando com `-O`, `-OO` e bytecode congelado com `optimize=1`. Os campos de `PhysicsConfig` rejeitam NaN, infinito, booleanos, tipos não numéricos, contagens não inteiras, taxas/distâncias/perdas/tempos negativos e probabilidades fora de `[0,1]`. `T1` e `T2` precisam ser positivos e satisfazer `T2 <= 2*T1`. Potências em dBm e a direção do acoplamento de fase continuam admitindo valores finitos com sinal.

As operações do canal validam os parâmetros antes do sorteio da perda óptica, impedindo que a perda de um fóton esconda uma configuração inválida. Configurações alteradas em memória também são verificadas na inicialização do canal e antes da persistência. O repetidor verifica os recursos inteiros e a compatibilidade da probabilidade com seu modelo de portas: o parâmetro da porta de dois qubits é `2*p`, sujeito ao limite de positividade completa `16/15` do canal despolarizante de dois qubits. Essa restrição pertence a esse modelo de portas; não é um limite universal de ruído para todos os dispositivos.

Os erros de entrada física agora são `ValueError`; operações antes de inicialização/calibração emitem `RuntimeError`; ajuste de parâmetros após congelamento do modelo emite `ProtocolViolationError`. A falha de um critério do diagnóstico visual continua produzindo `AssertionError`, lançado explicitamente. Não foram alteradas equações, ordem dos sorteios, sementes, valores de referência ou critérios físicos dos protocolos.

| Verificação | Resultado |
|---|---:|
| Regressão científica, ambientes, repetidores e seleção de modelos | 226/226 aprovados |
| Diagnóstico no Python normal | 113/113 aprovados |
| Mesmo diagnóstico com `-O` | 113/113 aprovados |
| Mesmo diagnóstico com `-OO` | 113/113 aprovados |
| Entradas inválidas rejeitadas por perfil | 112 |
| `assert` remanescentes no código Python de `src` | 0 |

A regressão aprovada inclui os goldens existentes de canal, memória, purificação, swapping e dataset no Windows, além da concordância entre Aer e operadores de Kraus. O limite completamente despolarizado é confrontado com fidelidade teórica `0.25`; a tolerância numérica explícita é `1e-7`, devido ao cálculo de raiz de matriz PSD/eigensolver do Qiskit. A diferença observada no Windows foi `4.43e-9`. As tentativas anteriores com tolerância `1e-12` permanecem registradas como falhas do diagnóstico, não como alterações do modelo.

`science-summary.json` vincula os resultados aos hashes dos arquivos. `regression-approved.xml` é o JUnit aprovado. As tentativas iniciais com erros de TMP do sandbox, carregamento de DLL nativa do Aer, nome de teste histórico ausente e tolerância numérica insuficiente são preservadas e não entram na contagem aprovada. A compatibilidade adicional executou 34 casos aprovados e três falhas de preparação do pip no TMP; esse grupo não é considerado integralmente aprovado.

O diagnóstico está disponível no executável gráfico e no Python, com destino novo para preservar relatórios existentes:

```powershell
OrionQ.exe --scientific-input-test C:\temp\orionq-566\scientific-input.json
python -O -m quantum_autopilot.desktop --scientific-input-test outputs\scientific-input-optimized.json
```

```bash
./OrionQ/OrionQ --scientific-input-test /var/tmp/orionq-566-scientific-input.json
```

Os novos builds Windows e Linux devem executar esse diagnóstico dentro dos binários antes da aprovação. A evidência de fonte não é apresentada como execução de um binário nem como qualificação experimental. O ensaio só usa canais locais simulados; não conecta instrumentos, não atua fisicamente e não certifica material QKD emulado.
