# OrionQ — encerramento e troca de fontes, 2026-10-12

O encerramento do provider foi dividido em aposentadoria imediata da apresentação e drenagem dos trabalhos aceitos. `stop()` não espera executores ou ticks na thread Qt. A drenagem conserva a identidade capturada, as auditorias e a posse dos recursos até os trabalhos terminarem; somente então invalida a preparação e fecha baseline, FPGA, receptores OGS/satelitais e pagers do controlador.

## Defeito reproduzido

Um comando no executor espera uma ação já enfileirada no Qt. O código anterior chama `shutdown(wait=True)` no Qt, que espera o comando. O comando, por sua vez, espera o Qt. A regressão usa uma barreira com fusível de dois segundos: antes da correção, `stop()` retornou em **2.015,05 ms** e o callback não pôde executar. A falha foi reproduzida antes das alterações; o fusível impede que o teste pare indefinidamente.

Após a correção, vinte ensaios com o mesmo tipo de dependência e executores/Qt reais produziram retorno de `stop()` com **mediana 0,155 ms, p95 0,222 ms e máximo 0,258 ms**. Cada comando aceito terminou e cada drenagem encerrou sem erro. Esses números medem somente o encerramento nessa fixture; não representam throughput nem carregamento da demonstração completa.

## Integração dos callers

- `ServiceProvider.stopping` indica recursos ainda em drenagem. Reinício e novos comandos são recusados enquanto isso ocorrer. Uma falha de liberação mantém o reinício bloqueado e registra somente o tipo do erro na mensagem de interface.
- `SwitchableProvider` mantém todas as fontes aposentadas durante trocas consecutivas e inicia somente o último candidato depois da liberação. Cancelamento invalida o início pendente. Falha da fonte anterior bloqueia a fonte nova; um candidato ainda não iniciado também é encerrado se a aposentadoria anterior falhar.
- `DeferredProvider`, usado no executável, mantém o QObject delegado até o fim da drenagem. Uma preparação cancelada que já está executando é aguardada fora do Qt; seu serviço é liberado sem criar um novo provider Qt.
- O rollback de um início reentrante não encerra a fonte sucessora escolhida por um listener.
- O fluxo atual de **Sign out** volta à tela de login e conserva o núcleo em execução, conforme o requisito de independência do login. Fechamento e troca de fonte seguem o encerramento corrigido. Não foi alterada a autorização do backend.

## Validação

| Verificação | Resultado | Evidência |
| --- | --- | --- |
| Bootstrap, polling, ordem de publicação, lifecycle de serviço real, wrappers e segurança no Windows | 80 aprovados, 170,13 s | `outputs/defect-corrections-20261012/provider-stop-final-r2.xml` |
| Revisão final dos 24 casos de stop, wrapper, cancelamento, liberação e rollback no Windows | 24 aprovados, 3,61 s | `outputs/defect-corrections-20261012/provider-stop-wrappers-final-r4.xml` |
| Revisão final dos mesmos 24 casos no Linux, UID 10001 e Qt offscreen | 24 aprovados, 14,75 s | `outputs/defect-corrections-20261012/provider-stop-wrappers-linux-final-r4.xml` |
| Ruff nos seis arquivos de implementação/testes alterados | Aprovado | Execução de `python -m ruff check` |
| Latência do encerramento, vinte ensaios | Aprovado no escopo descrito | `outputs/defect-corrections-20261012/provider-stop-measurements.json` |

Os testes antigos de lifecycle agora mantêm o loop Qt ativo enquanto `stopping` é verdadeiro, antes de exigir ausência de threads e estabilidade do relógio. Os números e as assertivas científicas não foram relaxados. Um teste anterior que permitia reiniciar a preparação antes de sua predecessora terminar passou a exigir a liberação antes do reinício.

## Limites

Uma operação externa que nunca retorna mantém os recursos em drenagem e a aquisição bloqueada. Não há encerramento forçado de um worker que possa ainda estar usando um dispositivo. Isso evita trocar um bloqueio de interface por liberação prematura ou estado físico incerto. A recuperação de recursos que falham ao fechar exige resolver a causa e repetir a liberação; a fonte nova não é habilitada automaticamente. Os testes utilizam código-fonte atualizado, sem equipamentos físicos; os executáveis publicados anteriores não incorporam a alteração até uma nova compilação.
