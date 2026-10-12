# ADR-SEC-002 — Núcleo autenticado independente do desktop

Data: 2026-10-10. Estado: implementação de software validada em Windows e Linux/WSL; contêiner e implantação institucional pendentes.

## Contexto

O perfil local anterior ligava a autoridade implícita ao SID/UID do processo e ao
cofre Windows DPAPI. Isso serve ao workspace desktop, mas impede usar a mesma
autoridade em processos independentes ou migrar o núcleo para Linux. O sistema
não pode tratar a existência de uma conexão de rede ou o usuário do contêiner
como identidade autenticada. Mover a GUI para outro processo não elimina a
necessidade de autorização no serviço.

Este ADR complementa ADR-SEC-001: o perfil local do desktop continua válido;
identidade de serviço e proteção portável de segredos são escolhas explícitas
para o núcleo headless. Não se atribui qualificação de hardware a essa mudança.

## Decisão

`quantum_autopilot.service.cli` inicializa o núcleo sem importar Qt. O serviço
recebe identidade explícita provisionada, configuração de transporte e um
provedor de proteção de segredos. No modo remoto, o desktop é um cliente; não é autoridade de
roteamento, equipamentos ou KME. A fronteira `SecurityRuntime` continua aplicando
permissões por operação e recurso, inclusive chamadas internas.

O perfil headless não usa DPAPI nem autentica a sessão interativa do Windows. Um
segredo de serviço montado permite autenticar a identidade previamente
provisionada; o cofre persiste envelopes AES-GCM com chave de proteção fornecida
externamente. Serviço iniciado sem configuração válida, chave de proteção ou
identidade habilitada deve falhar antes de aceitar operações. O provisionamento
é administrativo e separado da inicialização normal. A aplicação não cria um
administrador a partir do UID do contêiner.

API de rede exige mTLS TLS 1.3 e autorização do certificado a cada solicitação.
Políticas, revogação, consumo conservador de chaves e auditoria autenticada
continuam pertencendo ao backend. A máquina desktop e a sessão gráfica não são
dependências dessas operações. O usuário Unix protege arquivos e o processo;
seu UID não é um privilégio de aplicação no perfil de serviço.

O artefato `packaging/core` gera um wheel sem `quantum_autopilot.console` ou
launcher desktop, conserva os módulos científicos e inclui Qiskit/Aer. Não
instala PySide6, PyTorch ou scikit-learn. Modelos ML continuam opcionais e devem
ser admitidos e qualificados em um perfil adicional, mantendo os contratos. O
wheel desktop original e sua lista de dependências permanecem preservados.

`RemoteProvider` liga as telas Qt ao `CoreClient` por HTTPS, sem construir
`AssuranceDashboardService`, `NetworkEngine`, `SecurityRuntime` ou journal local.
O snapshot contém registros do backend e identidade autenticada do peer;
seleção e formatação são estado de apresentação. O backend não importa contratos
Qt/console. Comandos usam uma lista explícita de serviços ou a interface de
comando de rede existente, com a mesma autorização por recurso. O modo local
legado continua sendo uma composição distinta para preservar os workspaces.
Ferramentas que exigem arquivos locais não são habilitadas no cliente remoto.

O serviço possui relógio de processamento próprio, com uma thread limitada,
autorização fresca por tick e métricas de sobrecarga. O cliente apenas lê a
publicação e envia comandos autorizados. Recalibração usa handoff exclusivo
do relógio; encerramento não fecha recursos enquanto processamento ainda os
utiliza. O tempo científico contado não é preenchido por resultados prontos
ou ticks omitidos para alcançar o relógio de parede.

## Alternativas consideradas

| Alternativa | Efeito | Decisão |
|---|---|---|
| Levar DPAPI e a sessão Windows para um servidor | Mantém vínculo com usuário/máquina e impede o perfil Linux | Não adotada |
| Aceitar permissões por UID ou rede privada | Confunde isolamento do processo com identidade e autorização | Não adotada |
| Serviços autenticados e cofre externo ao desktop | Permite processos independentes, mTLS e troca de proteção de segredos | Adotada |
| Portar todo o software para RTOS/FPGA | Requer contratos, tempo real e toolchains específicos não validados | Fora desta entrega |

## Consequências e limites

- A interface gráfica pode ser fechada sem ser a autoridade do serviço headless.
- Configuração, segredos e estado durável são montagens distintas; nenhuma chave
  ou certificado privado entra na imagem ou no contexto de build.
- Credenciais e chave mestra não são proteção contra um administrador host hostil.
  O mecanismo de entrega e recuperação deve ser qualificado no ambiente alvo;
  um arquivo montado não é um HSM ou um serviço de segredos institucional.
- Restauração conjunta de logs, cofres e checkpoints exige a mesma âncora externa
  ou procedimento conservador já descrito em ADR-SEC-001. AES-GCM não resolve rollback.
- Linux/container é o plano de integração terrestre. Isso não instala Python,
  protocolos de missão ou processos OrionQ em OBCs, RTOS ou FPGA bare-metal.
- Atuação física permanece bloqueada e material QKD emulado continua sem uso
  operacional. Transporte mTLS não certifica a segurança experimental QKD.
- `HEALTHCHECK` da imagem verifica apenas socket aberto. Readiness funcional
  precisa consultar a API com uma identidade e um certificado autorizados.

## Evidências e referências

Os testes do artefato produzem um wheel e verificam exclusão de GUI, preservação
das dependências científicas, montagens e liveness sem envio de credenciais.
Execução efetiva de imagem Linux deve ser registrada separadamente: neste
computador Docker não estava no PATH. WSL inicialmente não estava instalado;
após autorização do usuário, WSL 2/Ubuntu 24.04 passaram a executar a validação
como UID/GID 10001 em filesystem nativo. Os 154 testes Linux passaram, incluindo
duas KMEs independentes; um processo CLI separado continuou após desconexão do
cliente e encerrou com SIGTERM. Os resultados estão no
[relatório da versão](../results/orionq_portable_core_20261010.md); essa execução
Python Linux não comprova a imagem Docker ou outro computador físico.

O isolamento de etapas de build, redução de dependências, contexto específico e
usuário de serviço seguem as [práticas oficiais Docker](https://docs.docker.com/build/building/best-practices/)
e a [definição do contexto de build](https://docs.docker.com/build/concepts/context/),
consultadas em 2026-10-10. TLS 1.3 e verificação de certificados usam as interfaces
documentadas em [Python ssl](https://docs.python.org/3.12/library/ssl.html),
consultadas na mesma data. Essas fontes orientam a implementação; não constituem
certificação da implantação OrionQ.
