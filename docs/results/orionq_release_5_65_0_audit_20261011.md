# OrionQ 5.65.0 — auditoria suplementar Windows

Data: 11 de outubro de 2026. Esta auditoria seleciona regressões adicionais aos
gates da compilação Windows. Os resultados positivos não eliminam os defeitos
reproduzidos em caminhos não protegidos pelos testes existentes.

## Regressões selecionadas

Qt de componente usa `offscreen`; core, KME e serviço usam processos/HTTP/TLS
locais e fixtures sintéticas novas no SSD. A inspeção de scroll, quando concluída,
usa Qt Windows e a entrada normal pela sessão do sistema operacional.

| Corpus | Passaram | Pulados | Tempo (s) |
|---|---:|---:|---:|
| Segurança de identidade, PKI, protocolo, cofres e equipamentos | 228 | 5 | 56.810 |
| Permissões e exportação pela interface | 26 | 0 | 39.856 |
| Core/KME/CLI, serviço misto e PS/PL emulado | 245 | 10 | 226.513 |
| TerminalUI, logo ASCII, gráfico e modo E.V.A. | 664 | 1 | 39.948 |
| Jornal, crash recovery, esquema SQLite e aquisição persistente | 40 | 0 | 6.351 |
| Causalidade, física, dados pareados e validações de estimadores | 69 | 0 | 60.119 |
| Contratos de exportação Git e consolidação de release | 248 | 0 | 10.725 |
| 25 módulos adicionais Qt (offscreen) | 406 | 0 | 200.431 |

Executados: **1942**; casos únicos por `classname + name`:
**1942**; execuções duplicadas: **0**.
Falhas/erros na seleção final: **0/0**.
Os XMLs finais e seus hashes estão em
[final-audit.json](../../outputs/release-5.65.0-20261011/audit/final-audit.json).

O inventário contém 412 módulos `test_*.py`; esta seleção suplementar
inclui 70. A lista de 179 módulos que não participam
da seleção explícita do build nem desta seleção está no JSON. Não se afirma que
todas as campanhas de treinamento, todas as sementes ou integrações externas
foram reexecutadas. O conjunto do build oficial deve ser lido em seu próprio
relatório; estes totais não incluem os JUnits oficiais nem os builds Linux.

## União com os gates oficiais Windows

Os 26 XMLs oficiais mais os 32 suplementares totalizam **6.388 execuções**:
**6.349 aprovadas, 39 puladas, zero falhas/erros**. São **6.311 casos únicos**,
com **6.272 aprovados e 39 pulados**. As 77 repetições são internas aos corpora
oficiais; não há sobreposição de identidades entre oficial e suplemento.

O campo `windows_source_junit_union` de `final-audit.json` contém os 58 XMLs,
hashes, regra de deduplicação e motivos agrupados dos skips. Os 23 skips oficiais
correspondem a 19 casos de credenciais/permissões Linux, um ambiente isolado
LabOne Q 26.7.0 ausente e três casos sem o ZIP QKD original. Os 16 skips
suplementares estão discriminados abaixo. Esta união ainda se refere aos testes
de fontes; os gates executados pelo EXE compilado são uma etapa separada.

## Motivos dos testes pulados

- `tests.test_portable_core_service.test_linux_cli_provision_restart_and_check_with_no_windows_identity`: actual private secret mounts require a POSIX host
- `tests.test_portable_kme.test_two_independent_kmes_without_os_session_and_conservative_restart`: native secret mounts and KME reconstruction require a Linux/POSIX host
- `tests.test_core_service_unit.test_systemd_255_unit_syntax_without_starting_service`: systemd syntax parser needs Linux; no service is installed or started
- `tests.test_service_pl_gateway.test_real_cli_gateway_pipeline_persistence_cancel_and_conservative_restart[cpu-external]`: kernel peer credentials and non-root Linux required
- `tests.test_service_pl_gateway.test_real_cli_gateway_pipeline_persistence_cancel_and_conservative_restart[emulated-pl-external]`: kernel peer credentials and non-root Linux required
- `tests.test_service_pl_gateway.test_unavailable_driver_and_mismatched_image_never_start[linux-pl-driver-None]`: non-root Linux configuration qualification
- `tests.test_service_pl_gateway.test_unavailable_driver_and_mismatched_image_never_start[cpu-external-0000000000000000000000000000000000000000000000000000000000000000]`: non-root Linux configuration qualification
- `tests.test_petalinux_deployment.test_fifo_and_symlink_artifacts_cannot_block_or_bypass_regular_file_requirement`: POSIX FIFO behavior is checked on Linux
- `tests.test_petalinux_deployment.test_gateway_state_survives_runtime_directory_cleanup_and_process_restart`: real Linux process credentials and durable ledgers required
- `tests.test_petalinux_deployment.test_units_are_accepted_by_real_systemd_parser_without_installing_services`: Linux systemd parser required
- `tests.test_security_vault_portable.test_mounted_key_rejects_symlink_before_permission_validation`: host does not grant symlink creation
- `tests.test_security_vault_portable.test_posix_mounted_source_rejects_fifo_swap_without_blocking`: requires a real POSIX FIFO
- `tests.test_security_vault_portable.test_mounted_source_enforces_permissions_again_after_creation`: actual POSIX filesystem permission validation
- `tests.test_security_vault_portable.test_mounted_source_rejects_untrusted_owner_and_nonregular_files`: actual POSIX filesystem permission validation
- `tests.test_service_identity_runtime.test_service_credential_mount_rejects_nonregular_or_oversized_before_open[fifo]`: FIFO creation is available on the Linux deployment target
- `tests.test_terminal_live.test_linux_pty_keyboard_resize_and_exit_restore_terminal`: curses PTY interaction is qualified on the Linux target

## Inspeção nativa de scroll

O diagnóstico privado usa entrada normal pela sessão Windows, janela 1280 × 720,
dois nodes terrestres (CIMATEC/CBPF), um satélite (GEO-AMERICAS-SIM) e seu diálogo
HOUSEKEEPING em 640 × 480. Foram feitas 12 leituras de conteúdo, nas posições
topo/fundo/topo de quatro viewports. A barra alcançou cada extremo, os widgets
corresponderam ao snapshot publicado, e ambas as mudanças de scroll receberam
eventos Paint, sem hover, seleção de texto ou chamada forçada a `update/repaint`.
Os quatro casos de entrada/detalhes/encerramento passaram.

[Relatório e capturas](../../outputs/release-5.65.0-20261011/audit/native-viewport/summary.json).
O mapeamento do título usa o QGraphicsProxy e confirma os bounds dentro do
viewport do Stage. Esse check não detecta oclusão entre widgets. A inspeção
visual da captura de CIMATEC identifica o topo do título flutuante parcialmente
coberto/cortado pela barra superior. **Essa pendência visual permanece aberta**.

O diálogo HOUSEKEEPING em 640 × 480 apresenta coluna Valor estreita: épocas e
vetores aparecem como `202...`, `-7...` ou `[0.0...`, impedindo leitura direta
integral. Os 12 readbacks completos não comprovam legibilidade. A função `_rows`
atribui o valor integral também ao tooltip da célula, conforme inspeção estática;
o diagnóstico não testou hover nem a leitura desse tooltip, e não qualifica
ausência de recortes em todas as resoluções. O ensaio é de fontes Qt Windows,
`frozen=false`, não do EXE distribuído, e não mede fluidez sob carga prolongada.

Tentativas anteriores do launcher privado foram preservadas. A primeira sem
guard `__main__` reexecutava o launcher durante o spawn do backend Windows;
uma tentativa com indentação incorreta não abriu o produto. Em outra tentativa,
a leitura durante materialização coincidiu com um novo tick. O diagnóstico final
aguarda a prontidão declarada pelo próprio componente antes de conferir conteúdo.
Essas correções são do harness, não alterações do produto.

## Defeitos e limites reproduzidos

- Duas instâncias independentes do engine legado consomem o mesmo par simulado:
  [reprodução](../../outputs/release-5.65.0-20261011/audit/legacy-pair-race.json).
  O ensaio de reprodução confirma o defeito; não é aprovação da exclusividade.
- O controlador misto reserva atomicamente dentro de um banco. Instâncias com
  bancos distintos não têm fencing global para o mesmo hardware. Após falha de
  auditoria posterior ao commit, recuperar pelo mesmo ID evita nova reserva:
  [reprodução](../../outputs/release-5.65.0-20261011/audit/mixed-atomicity.json).
- O perfil otimizado elimina validações por `assert` em dois construtores de
  física e 15 predicados de um validador da interface. Os 178 arquivos críticos
  examinados não usam `assert` como barreira de segurança. A reprodução normal
  versus `-O` não é um ensaio desses construtores pela API do EXE:
  [auditoria](../../outputs/release-5.65.0-20261011/audit/optimized-assert-audit.json).
- O scan de assinaturas é limitado ao código atual elegível e texto de até
  2 MiB; não certifica o histórico Git, dependências ou qualquer formato de segredo.
- Hardware Swabian, FPGA física, PetaLinux/BSP de placa, óptica satelital e serviços
  externos não foram qualificados. As fixtures são identificadas como simuladas.

## Preservação e procedência

Os 677 arquivos do manifesto de produção foram conferidos por SHA-256. Testes,
ferramentas e relatórios são vinculados separadamente. Duas falhas iniciais do
corpus de journal provinham de fixtures construídas por `object.__new__` sem
inicializar `_mixed_controller_lock` e `_mixed_controller`; só essas fixtures foram corrigidas,
com a tentativa original preservada. O corpus final tem 40 casos aprovados.

Os JUnits em `audit/final-junit` são cópias verificadas dos XMLs finais, adequadas
à consolidação. Tentativas interrompidas ou falhas iniciais ficam fora dessa
seleção. Não foram exportados bancos, certificados, chaves, logs privados ou
estado operacional. O relatório da entrega registra separadamente os defeitos
visuais, o alcance das validações nativas e os artefatos Linux/Windows/Git.
