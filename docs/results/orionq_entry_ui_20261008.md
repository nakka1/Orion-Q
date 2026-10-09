# OrionQ 5.58.0 — entrada e carregamento, 08/10/2026

**Validação concluída nas fontes 5.58.0:** 276 casos exclusivos de unidade/integração aprovados, layouts e sequência visual capturados na plataforma Windows e inicialização do serviço de demonstração aprovada no limite normal de 30 segundos. Nenhum novo executável foi compilado; os relatórios nativos registram `frozen=false` e não qualificam um EXE 5.58.0.

## Comportamento implementado

A entrada confirma a identidade Windows já vinculada ao backend, por senha real da mesma conta ou pela sessão Windows existente. O worker de login preserva o principal e suas permissões, verifica o SID e repete a autorização depois da confirmação. Falhas são genéricas, tokens nativos são fechados e o buffer nativo de senha é zerado.

O acionamento **Shinji Ikari** é exclusivamente visual, aceita qualquer valor de senha e não chama o verificador de credenciais. A apresentação vetorial retorna à entrada, admite Escape e acrescenta **eva operator connected** somente depois de concluir naturalmente. O som é um WAV sintetizado para o projeto; sua reprodução depende da disponibilidade de QtMultimedia e de uma saída de áudio.

A marca inicial restaura o desenho aceito do executável: letras Inter na cor de texto do tema e Q laranja com esfera de Bloch e satélite. Após o acionamento visual, o Q vermelho `#E53935` parte de uma base larga junto à esfera, afina para fora na diagonal e termina em uma cruz de braços afilados, substituindo o satélite durante essa execução. A variante acompanha a janela e suas superfícies filhas; seu ícone é criado em memória. A nova execução volta à marca inicial.

O carregamento tem cinco segmentos gráficos e contagens de trabalho concluído. A preparação de serviço/demonstração ocorre fora da thread Qt, seguida da criação do provider na thread da interface. Os sete marcos incluem as duas fases de preparação e as cinco etapas do primeiro snapshot; o último só é declarado depois da publicação efetiva. O gate de prontidão impede abrir um workspace vazio ou indisponível. Voltar à entrada limpa a navegação automática; depois de confirmar novamente o login, a mesma origem que falhou pode ser tentada novamente. `loading_state` restaura o progresso de uma preparação ainda ativa. Cancelamento invalida gerações antigas e impede que resultados tardios alterem a origem atual.

## Relatórios concluídos

| Corpus | Resultado | Evidência |
| --- | --- | --- |
| Interface integrada | 207 casos aprovados | [gui-tests.xml](../../outputs/orionq_entry_ui_20261008/gui-tests.xml) |
| Login nativo e preparação | 49 casos aprovados: 37 de login e 12 de preparação | [security-tests.xml](../../outputs/orionq_entry_ui_20261008/security-tests.xml) |
| Entrada, recuperação e preparação, rodada final | 55 casos aprovados | [entry-tests.xml](../../outputs/orionq_entry_ui_20261008/entry-tests.xml) |
| Marca inicial e Q/cruz revisado | 46 casos aprovados | [signal-brand-tests.xml](../../outputs/orionq_entry_ui_20261008/signal-brand-tests.xml) |
| Inicialização e confirmação de identidade | 16 casos aprovados | [startup-login-tests.xml](../../outputs/orionq_entry_ui_20261008/startup-login-tests.xml) |
| Regressões finais de preparação, loading, fontes e overlay | 93 casos aprovados | [startup-regressions.xml](../../outputs/orionq_entry_ui_20261008/startup-regressions.xml) |

Os relatórios registram zero falhas, erros ou skips. A [consolidação](../../outputs/orionq_entry_ui_20261008/tests-consolidated.json) substitui cada módulo pela sua rodada mais recente e registra **276 casos exclusivos aprovados**; as contagens da tabela se sobrepõem e não são somadas. Os casos cobrem os braços afilados da cruz, a ligação da base à esfera, o isolamento da variante por janela, a recuperação depois de confirmar novamente o login e o bloqueio de frames ainda não comprometidos. A fixture de preparação usa `QApplication` offscreen para coexistir com testes de widgets no mesmo processo; a última rodada executou esses testes antes dos widgets, sem reproduzir a falha anterior de inicialização de fontes Qt.

## Evidência nativa

| Ensaio | Resultado | Evidência |
| --- | --- | --- |
| Marca e layouts de entrada/carregamento | 16 casos aprovados, 24 capturas; temas dark/light em 960×480, 1280×720, 1440×900 e 3440×1440 | [branding-report.json](../../outputs/orionq_entry_ui_20261008/native-brand-final/branding-report.json) |
| Sequência visual na janela nativa | Quatro casos aprovados, 16 capturas; temas dark/light em 1280×720 e 3440×1440 | [entry-visuals.json](../../outputs/orionq_entry_ui_20261008/native-entry-final/entry-visuals.json) |
| Sessão Windows existente | Verificador real aprovado, sem envio de senha, nova concessão de papéis, estado do operador ou ação física | [native-session.json](../../outputs/orionq_entry_ui_20261008/native-session.json) |
| Inicialização com demonstração real | 15 casos aprovados de confirmação de sessão, navegação, redimensionamento e terminal | [native-demo-startup-final.json](../../outputs/orionq_entry_ui_20261008/native-demo-startup-final.json) |

As capturas da sequência usam a janela real com `MockProvider` como fixture de interface, sem iniciar um serviço nem exercer autenticação. Verificam a marca inicial, o acionamento reservado, a cobertura da janela, a limpeza da senha, o Q vermelho e seu ícone em memória, o retorno à entrada, o marcador, a seleção da origem preservada, a liberação da animação e o bloqueio físico. A duração foi reduzida para captura e o áudio ficou desativado; isso não constitui validação de audibilidade. As prévias [signal-wordmark.png](../../outputs/orionq_entry_ui_20261008/signal-wordmark.png) e [signal-q.png](../../outputs/orionq_entry_ui_20261008/signal-q.png) registram a base larga começando na esfera e o afilamento para fora.

## Carregamento e limite observado

A [primeira tentativa](../../outputs/orionq_entry_ui_20261008/native-demo-startup.json) terminou no limite de 30 segundos sem um snapshot pronto. Houve apenas o frame inicial indisponível, sem erro reportado pelo provider, e nenhuma sequência de navegação foi concluída. Esse resultado permanece registrado como falha de inicialização dentro daquele prazo.

Uma [nova execução por arquivo, com limite explícito de 90 segundos](../../outputs/orionq_entry_ui_20261008/native-demo-extended.json), publicou o primeiro snapshot pronto em 25,015 segundos e concluiu os 15 casos; portanto, não precisou do prazo adicional. A rodada final repetiu o limite normal de **30 segundos**, publicou o primeiro snapshot pronto em **26,406 segundos** e concluiu os mesmos 15 casos sem erros. Publicou nove snapshots e coletou 1.773 heartbeats, com intervalo no percentil 95 de **32 ms** e intervalo máximo de **1.500 ms**. O ensaio usa `app.run` normal, provider real e journal local novo; não realizou atuação física.

Os perfis isolados do worker mediram construção de demonstração em [11,185 segundos](../../outputs/orionq_entry_ui_20261008/demo-preparation-profile-v1/report.json) e, em outro estado novo, [11,253 segundos de construção e 1,696 segundo de leituras iniciais do serviço](../../outputs/orionq_entry_ui_20261008/demo-first-reads-profile-v1/report.json). A integridade das evidências foi válida e o principal OS explícito foi preservado. Essas medidas incluem o custo de `cProfile` e não incluem a apresentação Qt nem o avanço autônomo da rede; não são somadas ao tempo da inicialização nativa nem determinam a causa da diferença entre rodadas. Os detalhes permanecem em [prepare-profile.txt](../../outputs/orionq_entry_ui_20261008/demo-preparation-profile-v1/prepare-profile.txt) e [first-reads-profile.txt](../../outputs/orionq_entry_ui_20261008/demo-first-reads-profile-v1/first-reads-profile.txt).

A variação do carregamento a frio e a pausa máxima de 1,5 segundo continuam sendo limites observados. O resultado aprovado não demonstra uma inicialização livre de pausas. A tentativa de rastreamento iniciada por stdin foi excluída da avaliação: o mecanismo de spawn de multiprocessing no Windows exige um arquivo importável; ela não representa uma execução suportada do produto.

## Escopo da validação

Esta rodada valida código fonte e interação local. A senha Windows foi testada com helpers Win32 simulados; a execução nativa confirmou a sessão OS existente sem senha. Não foram qualificados autenticação OIDC, credenciais reais por senha, audibilidade do som, hardware físico, arraste físico por mouse ou um novo executável. Os diagnósticos visuais e de preparação usaram estado isolado; a inicialização do serviço usou seu próprio journal de demonstração.

[Documentação do fluxo](../product/orionq_entry_ui.md). [Marca e versão da distribuição](../product/orionq_branding.md).
