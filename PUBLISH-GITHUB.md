# Publicar OrionQ 5.60.0 no GitHub

A pasta `OrionQ` contém o repositório Git independente. Abra um terminal dentro
dela. O histórico do projeto original, configurações remotas, credenciais e
estados de execução não foram transportados.

Crie no GitHub um repositório vazio com o nome desejado. Evite inicializá-lo com
README, licença ou `.gitignore`, pois esses arquivos já estão nesta entrega.
Escolha a visibilidade e observe os avisos dos materiais de terceiros em
`THIRD-PARTY-NOTICES.md`, especialmente os dados Bristol de uso não comercial.

Na entrega concluída, confira o commit e a tag da versão e substitua os dois
campos do endereço de exemplo pelo usuário e repositório criados:

```powershell
git status --short
git log -1 --oneline
git tag --list
git remote add origin https://github.com/SEU_USUARIO/SEU_REPOSITORIO.git
git push -u origin main
git push origin v5.60.0
```

Autentique-se pelo fluxo do Git/GitHub instalado na máquina; não grave tokens,
senhas, certificados privados ou arquivos `.env` no repositório. Se `origin` já
estiver definido, confira `git remote -v` e ajuste-o conscientemente.

Os executáveis Windows e o ZIP portátil são entregues separadamente. Publique-os
como anexos de uma GitHub Release, caso deseje; não os adicione ao histórico de
fontes. Não existem comandos de envio remoto executados nesta preparação.
