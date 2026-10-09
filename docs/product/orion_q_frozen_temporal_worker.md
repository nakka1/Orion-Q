# ORION-Q — campanhas no executável Windows

Data: 14/09/2026. Pacote portátil x64, identificado como `5.38.0-temporal-worker-20260914`. Continua sendo um produto acadêmico de simulação, não um controlador de dispositivos físicos.

## Entrega e uso

Extraia **a pasta inteira** do ZIP e abra `ORION-Q.exe`. Não mova apenas o executável: `_internal` contém Python, Qt, Qiskit/Aer, modelos, fontes e recursos de evidência. Não é necessário instalar Python ou Node para usar esta distribuição. Não foi gerado um instalador nesta entrega e o binário não possui assinatura digital de editor.

Em **TOPOLOGY → TEMPORAL CAMPAIGNS**, carregue `examples/temporal_path_campaign_demo.json`, revise e execute. Esse exemplo espera o workspace de três nós na versão zero. Não altera automaticamente um workspace editado para acomodar o exemplo. Os detalhes de configuração, interpretação, cancelamento e exportação estão no [guia do painel](orion_q_temporal_campaign_ui.md).

O programa mantém seu estado fora do pacote, por padrão em `%LOCALAPPDATA%\ORION-Q`. Versões anteriores, bancos existentes e arquivos pessoais não foram removidos nem migrados durante o empacotamento.

## Fronteira de execução

No código-fonte, o painel usa o Python atual com `-m quantum_autopilot.control_plane.temporal_campaign_worker`. No pacote, usa **o próprio `ORION-Q.exe`**, com a entrada interna `--temporal-campaign-worker --store ...`. Essa entrada é despachada antes de importar/inicializar o desktop e não abre outra interface.

O launcher declara explicitamente suporte ao protocolo. Um executável congelado com outro launcher, sem essa declaração, não recebe a capacidade só por ter `sys.frozen=True`.

O bootloader windowless deixa `sys.stdin/stdout/stderr` ausentes. O módulo leve `worker_runtime` recupera os handles Win32 herdados do QProcess, duplica-os para controlar sua própria duração e os envolve em streams UTF-8. Não usa `AllocConsole`, rede, serviço Windows, executável auxiliar ou um Python externo. Sem handles válidos, a entrada interna falha fechada em vez de abrir o desktop ou aceitar uma campanha incompleta.

O contrato da campanha, o algoritmo físico, a regra de timeouts e a confirmação por hash permanecem os mesmos. O pacote inclui metadados de NumPy, Qiskit e Qiskit Aer porque as versões são registradas em cada ensaio. O manifesto não pode escolher endpoints remotos nem habilitar atuação física.

## Verificação realizada

O executável foi construído com PyInstaller 6.22.2 e Python 3.12.14 no Windows x64.

- **67 testes de código-fonte passaram**, cobrindo dispatch, fronteira do desktop, campanha, painel e cancelamento.
- **11 verificações com o `.exe` real passaram**, incluindo processo filho via QProcess, responsividade, exportação completa, replay idempotente, cancelamento antes e depois do primeiro progresso e ausência de mutação do workspace.
- A comparação numérica fonte versus pacote passou, incluindo um caso com idade de memória positiva, canal T1/T2 e swapping sobre matrizes envelhecidas.
- O worker foi executado a partir de um diretório alheio ao repositório, com caminho contendo espaços, sem `PYTHONPATH`/`PYTHONHOME` e com PATH restrito ao Windows. O harness de teste usa Python, mas os processos de campanha executam somente o binário empacotado.
- O autoteste do executável passou: recursos de evidência/modelo, telas, fontes/layout responsivo, cenário, seleção de nó e exportação/auditoria.
- O teste de injeção de falhas do controlador passou com 20 rodadas, ainda em modo exclusivamente simulado.

Isso não equivale a testar em todo Windows ou em uma VM limpa sem qualquer runtime instalado. O próximo nível de distribuição é uma instalação independente/VM limpa, além de assinatura e eventual instalador. Nenhum teste de empacotamento constitui nova validação científica WDM→fidelidade medida.

Evidências:

- `outputs/software_validation_frozen_temporal_worker/source-tests.xml`
- `outputs/software_validation_frozen_temporal_worker/acceptance-a/report.json`
- `release/windows/5.38.0-temporal-worker-20260914/self-test.json`
- `release/windows/5.38.0-temporal-worker-20260914/stress-validation/`

SHA256 do executável aceito: `d70d500a456a23790f68df95cad008c4c5caef3baf950e33a60e24e339185ca8`.

O pacote inclui `SHA256SUMS.txt` para conferir seus arquivos. O ZIP recebe um `.sha256` e verificação de CRC; hashes detectam inconsistências, mas não substituem assinatura digital/autenticidade do editor.

## Reprodução do build

```powershell
powershell -ExecutionPolicy Bypass -File tools/build_windows_console.ps1 `
  -Python .\.audit-winvenv\Scripts\python.exe -SkipDependencyInstall -SkipInstaller
```

Use `-SkipDependencyInstall` apenas quando as dependências já estiverem instaladas. O script roda os testes, constrói o binário, valida o worker congelado e o autoteste/stress antes de criar o ZIP. O empacotador recusa ZIP já existente e arquivos adicionais conflitantes; builds anteriores permanecem preservados.

Pendências de produto: histórico navegável de campanhas, distribuição em máquina independente, assinatura/instalador e atualização versionada. Recursos de rede física continuam dependentes de adaptadores, hardware e autorização específicos.
