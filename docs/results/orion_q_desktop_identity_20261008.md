# ORION-Q 5.55.0 — logo Windows e instância única

Entrega qualificada em 08/10/2026. A logo PixelMark da interface foi incorporada ao EXE e aos ícones da aplicação e da janela. A área de trabalho interativa usa uma propriedade atômica do Windows por máquina. Uma segunda abertura solicita a ativação da janela existente e termina antes de construir outra área de trabalho.

[Distribuição portátil](E:/Codex/2026-08-27/e/work/NISQ-Predictive-Twin/release/windows/5.55.0-native-20261008-145441/ORION-Q-5.55.0-native-windows-x64.zip) · [Guia de uso](E:/Codex/2026-08-27/e/work/NISQ-Predictive-Twin/docs/product/orion_q_desktop_identity.md)

SHA-256 do EXE: `1b7485352c82bbf9e081207d8fb9b065cd571d020e68334c5e273b5ab5dc898c`.

SHA-256 do ZIP: `690525252592edcc44a2e64d98555031e7bfb382095205de83a68da307e01d21`. CRC verificado; 3963 arquivos.

## Correções

- O recurso ICO anterior era uma marca diferente. O novo ICO deriva da geometria real de PixelMark, com fundo transparente e imagens de 16, 24, 32, 48, 64, 128 e 256 pixels. O arquivo do runtime e o recurso de compilação são idênticos.
- A aplicação não fornecia um ícone Qt nem uma identidade estável da barra de tarefas. QApplication, ConsoleWindow e os atalhos do instalador agora usam a logo e o AppUserModelID `ORION-Q.Desktop`.
- A abertura não possuía exclusão atômica. Um mutex Global do Windows precede os serviços e as janelas, com nome independente de usuário, versão, pasta e estado. Um evento solicita restauração e ativação pela thread da interface. A propriedade é liberada no fechamento e recuperada após encerramento abrupto.
- Falhas na preparação do modo web fecham o servidor e cancelam a abertura pendente do navegador.
- A regressão ampliada revelou uma disputa de publicação já presente: um lote leve de telemetria podia descartar o retorno completo de um comando e conservar recibos ou flags antigos. O retorno completo agora possui controle próprio de ordenação; aquisição, autorização, auditoria, cálculos, eventos científicos e timers permanecem com o mesmo fluxo.

## Validação nativa no executável entregue

| Caso | Resultado |
|---|---|
| Recurso Win32 de 32 px corresponde à logo | APROVADO |
| Recurso Win32 de 256 px corresponde à logo | APROVADO |
| Ícones Qt/Win32, AppUserModelID e janela elegível à barra de tarefas | APROVADO |
| Pedido de ativação recebido durante o carregamento | APROVADO |
| Segunda abertura com diretórios e estados diferentes não cria outra janela | APROVADO |
| Pedidos repetidos processados pela thread da interface | APROVADO |
| Restauração após minimizar | APROVADO |
| Reabertura após encerramento abrupto do processo de teste | APROVADO |

[Evidências nativas](E:/Codex/2026-08-27/e/work/NISQ-Predictive-Twin/release/windows/5.55.0-native-20261008-145441/desktop-behavior-validation/report.json). Foram utilizados a plataforma Windows real, o EXE com hash verificado antes e depois, fixtures MockProvider isoladas e processos próprios. Os diagnósticos não leram nem modificaram o estado do operador.

## Regressões e distribuição

| Grupo no EXE | Resultado | Duração (s) |
|---|---|---:|
| desktop-behavior | APROVADO | 18.08 |
| satellite-mission | APROVADO | 21.75 |
| satellite-optical | APROVADO | 26.97 |
| optional-models | APROVADO | 0.70 |
| roadmap | APROVADO | 7.50 |
| campaign-worker | APROVADO | 28.34 |
| self-test | APROVADO | 553.11 |
| laboratory | APROVADO | 114.94 |
| global-network | APROVADO | 130.31 |
| stress | APROVADO | 125.95 |
| startup | APROVADO | 210.17 |

Resultados de testes de fonte relevantes, sem somar casos repetidos entre execuções:

| Execução | Aprovados | Falhas |
|---|---:|---:|
| [lifecycle-r2.xml](E:/Codex/2026-08-27/e/work/NISQ-Predictive-Twin/outputs/desktop_identity_20261008/lifecycle-r2.xml) | 47 | 0 |
| [gates-r2.xml](E:/Codex/2026-08-27/e/work/NISQ-Predictive-Twin/outputs/desktop_identity_20261008/gates-r2.xml) | 172 | 0 |
| [packaging-r1.xml](E:/Codex/2026-08-27/e/work/NISQ-Predictive-Twin/outputs/desktop_identity_20261008/packaging-r1.xml) | 34 | 0 |
| [console-r1.xml](E:/Codex/2026-08-27/e/work/NISQ-Predictive-Twin/outputs/desktop_identity_20261008/console-r1.xml) | 167 | 2 |
| [live-provider-isolated-r1.xml](E:/Codex/2026-08-27/e/work/NISQ-Predictive-Twin/outputs/desktop_identity_20261008/live-provider-isolated-r1.xml) | 0 | 2 |
| [live-provider-fixed-r1.xml](E:/Codex/2026-08-27/e/work/NISQ-Predictive-Twin/outputs/desktop_identity_20261008/live-provider-fixed-r1.xml) | 2 | 0 |
| [live-provider-fixed-r2.xml](E:/Codex/2026-08-27/e/work/NISQ-Predictive-Twin/outputs/desktop_identity_20261008/live-provider-fixed-r2.xml) | 2 | 0 |
| [polling-rendering-fixed-r3.xml](E:/Codex/2026-08-27/e/work/NISQ-Predictive-Twin/outputs/desktop_identity_20261008/polling-rendering-fixed-r3.xml) | 35 | 0 |
| [live-provider-full-r2.xml](E:/Codex/2026-08-27/e/work/NISQ-Predictive-Twin/outputs/desktop_identity_20261008/live-provider-full-r2.xml) | 30 | 0 |

As duas falhas iniciais de comandos foram reproduzidas e corrigidas; as mesmas assertions passaram após a mudança. Um fixture de seleção que esperava atualização síncrona foi alinhado ao callback autenticado já existente, mantendo a verificação de controles disponíveis e ausência de comando de seleção no backend. O fixture de aprovação da bancada foi alinhado ao contrato de segurança existente; nenhum requisito de aceitação foi reduzido.

[Diagnóstico da disputa de publicação](E:/Codex/2026-08-27/e/work/NISQ-Predictive-Twin/outputs/desktop_identity_20261008/publication-order-fix.txt). O primeiro candidato 5.55.0 foi retido com `DO-NOT-RELEASE.txt`; o ZIP entregue pertence à reconstrução posterior à correção.

O empacotamento exige o gate nativo de identidade e instância, preserva o relatório interno e rejeita evidência ausente, externa ao diretório, obtida em modo fonte ou vinculada a outro EXE. Os 943 arquivos de entrada congelados foram conferidos. Os hashes do EXE e ZIP anteriores da 5.54.0 foram preservados.

[Conferência final](E:/Codex/2026-08-27/e/work/NISQ-Predictive-Twin/outputs/desktop_identity_20261008/final-qualification.json).

## Condições e limites

Ambiente: Microsoft Windows 11 Pro 10.0.26200; AMD Ryzen 7 5700X 8-Core Processor; 8 núcleos/16 processadores lógicos; 31.93 GiB. Python 3.12.14, PySide6 6.11.2, PyInstaller 6.22.2. Não foi utilizado hardware quântico nem atuação física; os modelos continuam opcionais.

O bloqueio Global abrange as versões atualizadas. Binários antigos não recebem a correção retroativamente; a compatibilidade reconhece janelas oficiais antigas na mesma área de trabalho. Feche essas versões e substitua seus atalhos pela nova distribuição.

O Windows pode restringir a transferência de foco, especialmente entre sessões. Essa restrição não permite outra instância. Workers, gateways, administração, modo web sem navegador e diagnósticos isolados continuam independentes da área de trabalho.

A validação nativa verificou os ícones reais, a identidade do processo e as propriedades da janela usadas pela barra de tarefas. Não foi feita comparação dos pixels do botão do Explorer nem qualificação em outro computador. Um item fixado que aponta para um EXE antigo deve ser substituído.
