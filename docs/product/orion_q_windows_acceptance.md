# OrionQ — aceitação em outro computador Windows

Desde a versão 5.57, o pacote usa a pasta `OrionQ` e o executável `OrionQ.exe`.
O verificador mantém leitura de pacotes anteriores com `ORION-Q.exe`; a
renomeação preserva o diário em `%LOCALAPPDATA%\ORION-Q`, os identificadores
de protocolo e a identidade Windows. Releases antigos não são modificados.

14/09/2026. Ferramenta de diagnóstico de distribuição, não validação científica nem certificação de segurança. A entrada nativa `--acceptance-test` não depende de Python externo, Node ou scripts PowerShell. Não instala componentes, baixa dados, abre rede ou controla equipamentos.

## Antes de executar

Use Windows x64 com interface gráfica e permissão de escrita em uma pasta pessoal. Extraia **todo** o ZIP em uma pasta nova; não misture arquivos de versões distintas nem execute dentro do ZIP. Preserve `_internal`, `SHA256SUMS.txt` e os demais arquivos. Não coloque seus relatórios, recibos ou arquivos adicionais dentro da pasta do programa: o verificador recusa arquivos não declarados. Não precisa de administrador.

O escopo desta entrega é a **distribuição portátil ZIP**. A pasta criada por um instalador pode conter arquivos adicionais de desinstalação e será recusada pelo verificador estrito; a aceitação de instalações Inno Setup não está incluída nesta etapa.

O executável acadêmico não é assinado digitalmente. Confira a origem do ZIP e compare seu SHA256 com o valor fornecido por canal confiável. O manifesto local só detecta divergência entre arquivos e hashes declarados; não autentica o publicador e pode ser recalculado por alguém que modifique o pacote. O diagnóstico executa código do próprio pacote, portanto não é um scanner de arquivos desconhecidos.

Se a política da instituição bloquear o executável ou o arquivo `.cmd`, não desative proteções: solicite análise/liberação ao responsável. O programa não altera ExecutionPolicy, Defender, firewall ou SmartScreen.

## Diagnóstico automático

1. Feche instâncias de teste desnecessárias. Não precisa apagar ou reinicializar o diário normal.
2. Abra **VALIDATE-OrionQ.cmd** por duplo clique. O nome anterior **VALIDATE-ORION-Q.cmd** também funciona no pacote atual. O iniciador solicita 3.600 segundos por subprocesso, mostra a pasta de relatório em `%TEMP%` e aguarda a conclusão. Nenhuma janela adicional da console é aberta.
3. Ao finalizar, abra `acceptance-report.json` na pasta indicada e confira `automatic_passed` e `acceptance_status`. Guarde a pasta completa se houver falhas.

Também é possível chamar o executável diretamente; em PowerShell interativo, este comando aguarda o processo e permite conferir o código de saída:

```powershell
$p = Start-Process -FilePath '.\OrionQ.exe' -ArgumentList '--acceptance-test --timeout-seconds 3600 --output-dir "C:\ORION-Q-tests\acceptance-01"' -WindowStyle Hidden -PassThru -Wait
$p.ExitCode
```

Escolha uma pasta gravável, **ainda inexistente e fora do pacote**. Sem `--output-dir`, o programa cria uma pasta exclusiva `%TEMP%\ORION-Q-acceptance-*`. O executável sem console não garante texto no terminal; use a pasta explícita ou o iniciador `.cmd`. `--timeout-seconds` aceita um inteiro entre **30 e 3.600 segundos** e altera o limite de cada subprocesso, não a verificação inicial de arquivos. O padrão da API e da CLI permanece **180 segundos**; o iniciador e o exemplo acima solicitam explicitamente 3.600 segundos para o diagnóstico completo de 5.59.0.

Na medição piloto de 5.59.0, o [self-test isolado](../../release/windows/5.59.0-native-20261008-213533/self-test.json) terminou em **1.071,016 segundos** e aprovou **39 checks**. Essa medição motivou o prazo finito maior, preservando todas as verificações e os critérios de aprovação. O build concede até 3.600 segundos para o self-test e outros 3.600 para o stress, com espera externa limitada a **7.320 segundos**, incluindo 120 segundos para finalização dos relatórios. O piloto não aprova a nova distribuição: os gates serão repetidos para o executável da rodada `5.59.0-native-20261008-223932`.

O roteiro verifica:

- Hashes de todos os arquivos declarados; ausência de arquivos extras, caminhos inseguros, entradas duplicadas e links/junctions no pacote.
- Inicialização, recursos/modelo, telas e matriz responsiva pelo self-test do executável real, em modo gráfico offscreen.
- Vinte rodadas determinísticas de injeção de falhas no controlador **simulado**, semente 1337.
- Código de saída e aprovação explícita dos relatórios. Falha de inicialização, timeout ou resultado inválido impedem aprovação automática.

Os processos têm diretório de trabalho na pasta de diagnóstico, PATH reduzido às pastas do Windows e variáveis Python/Qt de desenvolvimento removidas. Isso ajuda a detectar dependências acidentais; não equivale a executar em uma máquina limpa. Cada subprocesso tem limite de tempo; no timeout, somente o processo de diagnóstico criado pelo roteiro é encerrado. O ambiente do processo pai não é modificado.

`--integrity-only` verifica arquivos sem abrir a aplicação. Retorna 0 se os hashes forem consistentes, mas mantém `automatic_passed: false` e `acceptance_status: integrity_only_not_application_acceptance`. Não use esse modo para declarar que a aplicação funciona.

## Interpretar o relatório

| Campo/resultado | Significado |
| --- | --- |
| `automatic_passed: true` | Integridade, self-test e stress passaram nesta execução |
| `acceptance_status: manual_review_required` | Ensaios automáticos passaram; faltam interação e avaliação humana |
| `acceptance_status: failed` | Leia `error`, os logs e o último relatório produzido; não aprove a instalação |
| `independent_machine_verified: false` | O software não sabe se está em outro computador; nunca atesta isso sozinho |
| `manual_review: pending` | Não houve aprovação manual registrada pelo roteiro |

Código de saída 0 significa que o modo solicitado terminou sem falha, não aceitação completa. Código 2 indica falha detectada. Erros de argumentos ou criação da pasta podem ocorrer antes de existir um relatório. Nesse caso, consulte `%TEMP%\ORION-Q-diagnostics\orion-error-*.log` e mensagens do Windows. Nada é automaticamente enviado a terceiros.

## Verificações manuais em máquina independente

Faça esta parte **em outro computador Windows**, sem Python/Node do projeto. Use um diário de demonstração novo, fora da pasta do programa:

```powershell
.\OrionQ.exe --state-root "C:\ORION-Q-tests\manual-01"
```

Registre em um arquivo separado: data, versão/hash do executável, versão do Windows, escala de tela, resolução e confirmação humana de que não é o computador de desenvolvimento. Evite identificar o operador por dados pessoais. Para cada item, anote PASSOU/FALHOU/NÃO TESTADO e a evidência correspondente:

1. Abra as seis telas; confira fonte, contraste, GRAPH/GEO/MATRIX e popups somente por clique. Maximize, restaure e use F11; listas devem ter rolagem, sem barras laterais por proporção fixa nem sobreposição de controles. Teste pelo menos duas escalas de tela disponíveis.
2. Em **Existing service**, abra **TEMPORAL CAMPAIGNS**, carregue `examples/temporal_path_campaign_demo.json` no workspace novo, revise e execute. Aguarde **CONFIRMED**, não apenas 100% de progresso. Navegue na interface durante a execução.
3. Exporte o JSON fora da pasta do programa. Tente exportar para o mesmo nome: deve recusar a sobrescrita.
4. Feche e reabra com o mesmo `--state-root`. Entre em **SAVED CAMPAIGNS → REFRESH → OPEN / VERIFY**. Deve mostrar **HISTORICAL**, o mesmo hash e nenhum novo ensaio. A consulta não restaura a topologia antiga.
5. Para testar cancelamento, use uma cópia do manifesto com novo `campaign_id` e orçamento maior, respeitando limites documentados. Cancele enquanto executa. Cancelamento sem recibo não aparece como resultado; commit concluído antes do pedido pode aparecer como **CONFIRMED**. Não interpretar esse caso como rollback. Se terminar rápido demais, marque o teste como não exercitado.
6. Troque para fonte sintética e observe a limpeza dos dados anteriores. O histórico do serviço não deve ser associado à injeção visual. Use apenas arquivos de demonstração para testar a entrada medida; métricas ausentes não devem ser inventadas.

Aceitação completa exige os testes automáticos aprovados **e** os itens manuais necessários aprovados com evidências no segundo computador. O relatório automático original não deve ser editado para transformar pendências em aprovações. Esta etapa não valida hardware, autenticação, fidelidade medida ou hipóteses WDM.

### Modelos opcionais (incremento de 23/09/2026)

Para distribuições com as dez sessões offline, execute também o diagnóstico
`--optional-model-test` e siga o [guia específico](orion_q_optional_models_windows.md).
Ele é separado de `--acceptance-test`: o relatório geral de integridade/self-test/
stress não certifica a inferência dos pacotes externos. Sem pacotes, confira que
o catálogo e o aplicativo abrem sem iniciar carga, download ou instalação.
Com os ZIPs explicitamente selecionados, confira as sessões e a recusa de
sobrescrita; trocar opção deve limpar o resultado anterior. O detector continua
bloqueado e nenhuma dessas avaliações ativa a política do runtime.

O empacotador exige aprovação do diagnóstico sem pacotes vinculada ao hash do
executável. Testes com os pesos fornecidos são evidência adicional local; eles
não autorizam sua redistribuição nem substituem inspeção na segunda máquina.

## Evidências e limitações desta entrega

### Incremento do roadmap — 02/10/2026

Use também o [guia de comparação, diagnóstico e emulação](orion_q_roadmap_usage.md).
No segundo Windows, acrescente ao roteiro manual:

1. Abra **POLARISATION LOOP → COMPARE CONTROLLERS · REVIEWED SCENARIOS**.
   Compare ridge calibrado a 30, 120 e 250 rad/s; confira referência,
   inconclusivo e modelo, respectivamente. O controlador ativo deve conservar
   seu estado. Sem pacote externo, confira `historical_identity_only`.
2. Exporte cada comparação para fora da pasta do programa e preserve
   JSON, Markdown e manifesto. Trocar o perfil deve invalidar o resultado
   anterior; repetir o destino deve recusar sobrescrita.
3. Execute `--diagnose-runtime` em uma pasta nova com 1.000 ciclos, aquecimento
   20 e orçamento explícito, em `idle` e `background_math_thread`. Confira
   contagem de falhas, prazos excedidos, percentis e ambiente. Não compare
   tempos de computadores diferentes como se fossem uma garantia de hardware.

O teste automático do build verifica as comparações no executável e 20 ciclos
isolados; isso não substitui a interação humana nem a amostra de 1.000 ciclos.
O teste em outro computador foi adiado por solicitação do usuário. Preserve
`independent_machine_verified: false` nos relatórios automáticos.

Os testes de código cobrem corrupção, arquivos extras/ausentes, caminhos inseguros, limites, ausência de sobrescrita, relatórios inválidos, falhas/timeout, isolamento de estado e despacho anterior à interface gráfica. Evidências de desenvolvimento: `outputs/software_validation_windows_acceptance/`.

Nesta etapa passaram **30 testes de diagnóstico/launcher** (`native-tests.xml`, 10,56 s) e **90 testes de regressão de campanhas/histórico/capacidades** (`regression-tests.xml`, 70,47 s). Ruff aprovou os módulos alterados. Os limites de falha/timeout são exercitados com injeção controlada nos testes unitários; os ensaios normais do pacote usam processos reais. O build local e o workflow Windows foram atualizados para incluir os novos testes, pacote completo com hashes e aceitação nativa. O workflow remoto não foi disparado nesta sessão.

A execução local do pacote, quando aprovada, demonstra o funcionamento do roteiro neste computador; **teste em uma segunda máquina continua pendente**. A comparação temporal e a navegação do histórico permanecem itens manuais no computador de destino; não são cobertas integralmente pelo self-test offscreen. Os logs podem conter caminhos locais e detalhes de falhas: revise-os antes de compartilhar. Nenhum diário real do operador é incluído automaticamente.
