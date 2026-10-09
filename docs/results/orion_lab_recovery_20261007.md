# Recuperação dos experimentos locais — 2026-10-07

Escopo: `LaboratoryService`, laboratório local emulado, Windows. Nenhum equipamento físico foi conectado ou comandado. Os executores usados nos testes de interrupção são fixtures pequenas identificadas; os subprocessos, a exclusão do sistema operacional, as transações SQLite e as substituições de arquivos são reais.

## Falhas reproduzidas antes da correção

Uma instância tinha apenas um `threading.Lock` próprio. Duas instâncias apontando para a mesma pasta iniciaram **dois experimentos simultâneos**. Em um segundo ensaio, o processo foi encerrado durante a execução; depois do reinício seu registro continuava em **`running`**. A gravação anterior fazia o commit SQLite antes de `receipt.json`, sem mecanismo de reconciliação.

Evidência preservada: [reproduction.json](../../outputs/lab_hardening_20261007/recovery/before/reproduction.json).

## Implementação

`exclusive_operation()` combina exclusão por instância e um lock de arquivo do sistema operacional, compartilhado entre instâncias e processos locais. A concessão pertence ao descritor aberto: a morte do processo libera o lock. Não depende de expiração de relógio, idade do experimento ou tentativa de inferir disponibilidade a partir de um PID. Um executor lento e ainda ativo conserva sua concessão.

O recibo registra PID, identificador de instância, política da concessão e revisão/hash/configuração do experimento. O registro inicial `running` também recebe checksum. Configuração e aquisição utilizam o mesmo lock; dependências e configuração são verificadas antes de criar os arquivos do experimento e novamente sob exclusão.

A autoridade é o SQLite. `runs`, `run_events` e `receipt_outbox` são gravados na mesma transação `BEGIN IMMEDIATE`, usando WAL e `synchronous=FULL`. As transições terminam em `completed`, `failed` ou `interrupted`. Não há `INSERT OR REPLACE` de experimentos nem retomada automática.

O arquivo `receipt.json` é uma projeção do registro confirmado. A publicação usa arquivo temporário, `flush`, `fsync` e substituição atômica. A confirmação de publicação fica no outbox. A interrupção antes da confirmação não inicia outra execução: o reinício compara a projeção e publica o mesmo recibo. Divergências anteriores são preservadas em `receipt.previous-<SHA256>.json`.

Ao abrir um journal existente ou consultar histórico, `recover()` só resolve execuções abandonadas depois de obter o lock. Um `running` cujo executor já liberou a concessão passa a `interrupted`, com causa explícita e `never_resume_or_repeat_automatically`. Um registro terminal já confirmado permanece terminal. Registros antigos `running` sem checksum são identificados como `legacy_running_receipt_unverified`; essa migração não declara verificação retroativa.

Se a projeção não puder ser gravada, o resultado confirmado permanece no SQLite e o histórico expõe `persistence.reconciliation.pending_receipts`. O erro persistido contém somente a classe da exceção. Após restaurar a escrita, a reconciliação publica o recibo sem executar o experimento novamente. Corrupção do checksum, inconsistência entre eventos/recibos/outbox, registros órfãos e remoção do journal são rejeitadas em vez de apresentadas como histórico verificado.

## Casos exercitados

| Caso de uso / ponto de falha | Estado após reinício | Evidência esperada |
| --- | --- | --- |
| Encerrar após commit inicial, antes do executor | `interrupted` | Nenhuma chamada do executor; um único registro |
| Encerrar durante execução | `interrupted` | Uma chamada; nenhuma retomada |
| Encerrar dentro da transação terminal, antes do commit | `interrupted` | Transação desfeita; resultados não confirmados não aparecem como concluídos |
| Encerrar após commit terminal, antes da publicação | `completed` | Mesmo resultado confirmado e recibo reconstruído |
| Encerrar após `fsync` da projeção, antes de substituí-la | `completed` | Projeção publicada a partir do SQLite |
| Encerrar após substituir a projeção, antes de confirmar o outbox | `completed` | Mesma projeção confirmada, sem nova execução |
| Segundo processo com executor original ativo | `running` | Novo experimento rejeitado como `laboratory_busy`; recuperação não interfere no proprietário |
| Duas instâncias no mesmo processo | Concessão exclusiva | Segunda instância bloqueada; concessão liberada ao sair |
| Consultar histórico/reiniciar três vezes | Mesmo terminal | Mesmos eventos e mesma contagem de execuções |
| Arquivo perdido ou alterado | Terminal preservado | Reconstrução; versão divergente preservada |
| Falha de publicação em disco | Terminal + publicação pendente | Pendência explícita; recuperação depois de restaurar escrita |
| Alterar eventos/outbox ou remover registros | Verificação recusada | Sem reparo silencioso da autoridade |
| `KeyboardInterrupt` no executor | `interrupted` | Cancelamento explícito, locks liberados, contexto privado ausente do recibo |
| API/histórico/painel nativo existentes | Contratos preservados | Regressões anteriores executadas junto aos novos casos |

Os testes de interrupção terminam o PID do interpretador que adquiriu a concessão. No Windows, o launcher do virtualenv pode ter um PID diferente; encerrar somente o launcher não seria comprovação suficiente.

Evidências executáveis: [verification-final.xml](../../outputs/lab_hardening_20261007/recovery/verification-final.xml), [verification-summary.json](../../outputs/lab_hardening_20261007/recovery/verification-summary.json).

```powershell
$env:PYTHONPATH = 'src'
$env:QT_QPA_PLATFORM = 'offscreen'
.audit-winvenv\Scripts\python.exe -m pytest tests\test_lab_service.py tests\test_lab_service_recovery.py --basetemp outputs\lab_hardening_20261007\recovery\tmp_reproduction --junitxml outputs\lab_hardening_20261007\recovery\verification-reproduction.xml -q
```

## Limites

Esse teste comprova falhas de processo no armazenamento local e coordenação entre serviços que adotam o contrato de lock. Não valida partição de filesystem de rede, coordenação distribuída, binários antigos que ignoram o lock, desligamento abrupto do computador, controladores de disco ou unidades físicas. Serviços antigos devem estar parados antes de reutilizar o mesmo journal com esta versão.

Checksums e encadeamento detectam divergências dos registros; não autenticam um autor contra um adversário capaz de reescrever toda a base. O SQLite precisa permanecer íntegro e acessível para reconciliar os arquivos. Recibos parciais não autorizam atuação física nem geração/uso de chaves reais. Uma nova execução solicitada explicitamente recebe outro `run_id`; não existe deduplicação entre pedidos novos indistinguíveis sem um identificador de idempotência do cliente.
