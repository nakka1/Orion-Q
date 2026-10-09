# OrionQ — identidade visual e instância única no Windows

Versão de referência: **5.57.0**, 08/10/2026. O nome público era ORION-Q até a 5.56; os identificadores persistentes permanecem compatíveis.

O aplicativo Windows usa a mesma logo da interface no executável, na janela e na barra de tarefas. A área de trabalho interativa admite uma instância por máquina: abrir novamente o ORION-Q solicita a restauração e a ativação da janela existente, inclusive quando a primeira abertura ainda está carregando.

## Logo e ícones

A fonte da logo é o desenho vetorial `paint_bloch_q`, em [widgets/brand_mark.py](../../src/quantum_autopilot/console/widgets/brand_mark.py). O Q combina a esfera de Bloch, seu vetor de estado e um satélite na cauda da letra. O mesmo desenho produz a marca nas telas e os ícones em todas as resoluções, com detalhes simplificados nas menores. O ICO usa a cor de destaque `#d97757` sobre fundo transparente. A identidade do Windows permanece estável quando o operador alterna o tema da interface.

[generate_console_icon.py](../../tools/generate_console_icon.py) renderiza a marca com Qt e gera imagens de 16, 24, 32, 48, 64, 128 e 256 pixels dentro do ICO, cada uma com 32 bits. Para regenerar, em um ambiente com o extra `console` instalado:

```powershell
python tools/generate_console_icon.py
```

O gerador escreve dois arquivos idênticos: [packaging/console.ico](../../packaging/console.ico), incorporado ao recurso do executável e usado pelo instalador, e [console/assets/orion-q.ico](../../src/quantum_autopilot/console/assets/orion-q.ico), distribuído com o pacote Python. O recurso do runtime é obrigatório no empacotamento. [branding.py](../../src/quantum_autopilot/console/branding.py) o encontra pela localização do próprio módulo, tanto na instalação Python quanto dentro de `_internal` na distribuição congelada.

A aplicação e `ConsoleWindow` recebem esse ícone antes de mostrar a interface. Assim, o ícone do arquivo no Explorer e o ícone da janela em execução têm a mesma origem visual.

## Identificação na barra de tarefas

O processo define `AppUserModelID = ORION-Q.Desktop` antes de criar `QApplication`. Os atalhos do menu Iniciar e da área de trabalho gerados pelo instalador declaram o mesmo identificador. Ele permanece constante entre versões e pastas de instalação, associando o aplicativo à sua identidade na barra de tarefas. A Microsoft exige que a identificação do processo ocorra no início, antes da apresentação da interface, e que os atalhos utilizem o mesmo ID. Veja [SetCurrentProcessExplicitAppUserModelID](https://learn.microsoft.com/en-us/windows/win32/api/shobjidl_core/nf-shobjidl_core-setcurrentprocessexplicitappusermodelid) e [Application User Model IDs](https://learn.microsoft.com/en-us/windows/win32/shell/appids).

Um atalho antigo continua apontando para seu executável antigo. Ao atualizar uma distribuição portátil, abra o `OrionQ.exe` da nova pasta e substitua atalhos ou itens fixados que ainda apontem para a versão anterior. O instalador mantém o AppId, reutiliza a pasta de instalação anterior e cria atalhos OrionQ. Os atalhos legados são preservados e podem precisar de atualização manual.

## Uma área de trabalho interativa por máquina

[desktop_instance.py](../../src/quantum_autopilot/desktop_instance.py) adquire um mutex do Windows antes de criar serviços, autenticação ou janelas do operador. A aquisição é atômica e usa o nome fixo:

```text
Global\ORION-Q.Desktop.7BE09401-981D-49EC-B157-6BC04F6AC80A.Owner
```

O nome independe da versão, usuário, pasta de extração, diretório atual, fonte de dados e banco de estado. O prefixo `Global\` alcança as sessões Windows da mesma máquina; a Microsoft documenta esse uso para detectar instâncias entre sessões em [Kernel object namespaces](https://learn.microsoft.com/en-us/windows/win32/termserv/kernel-object-namespaces).

A primeira abertura mantém a propriedade do mutex durante toda a execução interativa. Uma abertura concorrente não cria outra área de trabalho: sinaliza o evento de ativação da proprietária e termina. Se o Windows impedir a aquisição ou a sinalização por restrições de acesso, o aplicativo informa o problema e preserva o bloqueio. Quando a proprietária está em outra sessão, o operador deve usar essa sessão para acessar a interface.

O controle abrange os pontos de entrada interativos da distribuição, a execução direta da console e o modo web com abertura de navegador. A implementação usa `CreateMutexW` e `WaitForSingleObject` para distinguir uma propriedade ocupada de uma propriedade disponível. O fato de o objeto já existir, sozinho, não substitui a aquisição; consulte [CreateMutexW](https://learn.microsoft.com/en-us/windows/win32/api/synchapi/nf-synchapi-createmutexw).

## Restauração e foco

O evento de ativação transmite somente o pedido de mostrar a janela existente. Ele não encaminha os argumentos da segunda abertura, não altera o provider, não executa comandos e não autentica outra sessão. O evento pode permanecer sinalizado durante o carregamento inicial e é consumido pela thread da interface.

Se a janela estiver minimizada, o ORION-Q remove o estado de minimização, preservando a configuração de janela maximizada ou tela cheia. Em seguida, solicita a ativação. O modo web tenta localizar e ativar a janela de navegador já aberta, sem emitir outra sessão local nem abrir outra janela por causa da tentativa repetida.

O Windows pode restringir a mudança de foco, mesmo quando a aplicação solicita a ativação corretamente. Nesse caso, selecione a janela pelo botão na barra de tarefas ou por `Alt+Tab`; a restrição de foco não permite abrir uma segunda instância. Essa limitação é descrita pela Microsoft em [SetForegroundWindow](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setforegroundwindow).

## Encerramento e recuperação

Ao fechar normalmente, o ORION-Q libera a propriedade e os handles do mutex e do evento. Após encerramento inesperado, o Windows fecha os handles do processo. Se ainda existir um handle mantido por outro processo, a aquisição reconhece `WAIT_ABANDONED` e recupera a propriedade. A próxima abertura pode iniciar sem apagar um arquivo de lock: esse bloqueio é mantido pelo sistema operacional. A recuperação da propriedade está documentada em [WaitForSingleObject](https://learn.microsoft.com/en-us/windows/win32/api/synchapi/nf-synchapi-waitforsingleobject); o fechamento automático de handles está descrito em [CreateMutexW](https://learn.microsoft.com/en-us/windows/win32/api/synchapi/nf-synchapi-createmutexw).

Recuperar a propriedade permite reabrir o aplicativo; não restaura alterações que ainda não tinham sido persistidas.

## Diagnósticos, workers e versões anteriores

Workers de campanhas, administração de segurança, gateways e outras ferramentas sem interface continuam independentes da propriedade da área de trabalho. O servidor web iniciado com `--ui web --no-browser` também segue a rota headless. Diagnósticos explicitamente isolados podem abrir suas próprias janelas de teste, com estado separado, sem adquirir o mutex do operador. Essas janelas são identificadas para não serem confundidas com a interface de produção. A independência dos workers congelados é detalhada em [campanhas no executável Windows](orion_q_frozen_temporal_worker.md).

Binários anteriores à 5.55.0 não recebem o bloqueio retroativamente. A nova versão consegue reconhecer uma janela antiga oficial visível na mesma área de trabalho e solicitar sua ativação, mas essa compatibilidade não impede que o próprio binário antigo seja aberto várias vezes ou esteja em outra sessão. Para obter o comportamento de instância única, feche os ORION-Q antigos e passe a usar a 5.55.0, inclusive nos atalhos.

[test_console_branding.py](../../tests/test_console_branding.py) verifica as resoluções, os ICOs idênticos e a correspondência de pixels com a logo real da interface. [test_desktop_instance.py](../../tests/test_desktop_instance.py) cobre a propriedade entre processos, a recuperação e a integração dos pontos de entrada.
