# OrionQ 5.66.2 — publicação da entrega de fontes

Este repositório contém o snapshot público de fontes e a seleção de manifests,
hashes e evidências de qualificação descrita em `GIT-RELEASE.md` e
`release-source-manifest.json`. O histórico e o Git de origem não são importados.

Os pacotes binários Windows/Linux e os wheels são entregues separadamente.
Após conferir as licenças/proveniência indicadas em `GIT-RELEASE.md`, anexe esses
arquivos manualmente como assets ao GitHub Releases; não os acrescente ao Git.
Esta preparação não cria remoto, publicação ou URL GitHub.

O repositório preparado e o ZIP preservam os bytes selecionados. Para restaurar
o bundle com a mesma política, execute:

```sh
git -c core.autocrlf=false -c core.longpaths=true clone OrionQ-5.66.2-github.bundle OrionQ-5.66.2
git -C OrionQ-5.66.2 config core.autocrlf false
git -C OrionQ-5.66.2 config core.longpaths true
```

Essas opções valem somente para a cópia nova. Os padrões `-text` de
`.gitattributes` protegem evidências e referências congeladas; fontes fora desses
padrões podem mudar de fim de linha sob outras configurações de checkout.
A prova externa da entrega distingue o snapshot, o ZIP e o clone com a política
de bytes preservados de qualquer ensaio com normalização automática.

Os relatórios preservam seus links relativos e hashes originais. Links para
tar/ZIP/wheel ausentes deste arquivo de fontes somente funcionam quando os
assets são colocados nos mesmos caminhos locais, inclusive `release/windows/`
e `release/linux/`, conforme cada relatório. A ausência desses binários no Git
é intencional; os manifests não substituem a entrega compilada correspondente.

Consulte `docs/results/orionq_release_5_66_2_20261012.md` para os perfis, resultados e limites desta rodada.
Confira as garantias e limites de concorrência em
`docs/product/orionq_controller_concurrency.md` e suas regressões selecionadas.
O envelope v1 não atesta origem física; o adaptador/serviço autorizado deve
verificar a associação fonte → série/endpoint. A entrega não qualifica hardware,
fencing distribuído externo ou disponibilidade em qualquer armazenamento.
Os ganhos locais de layout não afirmam eliminação de todas as pausas nem
sustentação universal do processamento científico a 1× tempo real. O relatório
desta versão distingue medidas finais, diagnósticos mistos e limites de G05.
Cada tentativa deve ser lida com seu freeze e resultado próprios; um corpus
anterior não aprova automaticamente uma fonte corrigida. Contratos funcionais
Qt e inspeção de pixels são provas distintas. Capturas marcadas como
`NOT_QUALIFIED` não certificam pintura ou legibilidade na sessão nativa.
