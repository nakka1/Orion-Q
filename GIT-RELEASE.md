# OrionQ 5.66.2 — snapshot Git e evidências de qualificação

Este diretório preserva as fontes atuais, inclusive alterações e arquivos novos,
sem importar o histórico Git original. `release-source-manifest.json` contém
hashes de cada arquivo copiado, exclusões e diferenças desde a campanha híbrida.
Uma versão nova não altera retroativamente os congelamentos históricos.

Os relatórios TRL 4, SOFT-DT/TRL 5, SeQUeNCe e híbridos conservam seus estados,
falhas anteriores e limitações. Esta entrega não atribui TRL 5 à rede física.
Atuação física permanece bloqueada e os modelos ML são opcionais.
As medições de interface não declaram eliminação de todas as pausas nem
sustentação universal da simulação científica a 1× tempo real. Consulte o
comparativo e os limites de G05 e do processamento no relatório desta versão;
perfis com fontes atuais e executável anterior são diagnósticos separados da
comparação entre os executáveis finais. Leituras de CPU/RSS selecionadas são
registros de medição de esquema limitado, sem argumentos de processos ou
estado operacional bruto.

O reproduzidor `tools/benchmark_policy_panels.py` executa Python a partir de um
checkout escolhido. Seus ensaios Qt/offscreen dos três painéis de política
usam uma apresentação identificada, sem a engine científica global em execução,
e verificam ações com o serviço/autorização reais. Não são medições do EXE nem
qualificação global, visual nativa ou física. Use um `--output` novo e um
`--private-state-root` novo, separado, para manter credenciais, auditoria e SQLite
fora do relatório público. O resumo distingue o helper privado da medição
original e o reproduzidor público com verificações adicionais de origem/path.

Os arquivos pequenos de evidências mantêm os caminhos `outputs/` para preservar
links da documentação. Acrescente somente essa seleção ao novo Git com
`git add -f -- outputs`; novas execuções continuam ignoradas. Dados brutos,
SQLite, ambientes virtuais, SDKs proprietários, segredos e binários do aplicativo
não são distribuídos no Git. Consulte as exclusões do manifesto e os runners em
`tools/` para reproduzir aquisições e recibos omitidos; os resumos sozinhos não
substituem a conferência completa desses dados. O build Windows restaura recursos
fixados por `tools/prepare_console_resources.py`.

Os scripts `resume_windows_build.ps1`, `resume_windows_build_v2.ps1` e
`resume_windows_build_v3.ps1`, `resume_windows_build_v4.ps1` e
`resume_windows_build_v5.ps1`, com seus respectivos
`resume-official-suffix.diff`, `resume-official-suffix-v2.diff`,
`resume-official-suffix-v3.diff`, `resume-official-suffix-v4.diff` e
`resume-official-suffix-v5.diff`, são
evidências históricas das continuações da entrega 5.61.0; exigem a árvore e os gates originais. Eles
não substituem o build completo de um checkout novo por
`tools/build_windows_console.ps1`.

A continuação histórica da versão 5.64.2 usa o reproduzidor público
`work/resume_windows_native_release.py` e as provas nominais em
`outputs/native_release_5_64_2_20261011/windows/bootstrap-continuation/`.
O tail oficial preserva os comandos e substitui somente o basetemp do corpus 21.
Proveniência, teste original de lifecycle, reprodução corrigida e JUnit falho
conservam seus resultados distintos. Uma continuação aprovada não transforma a
tentativa original em execução integral aprovada. Logs e estados privados ficam
fora da seleção; referências a eles nos registros preservam apenas a proveniência.

Os gates nativos compactos ficam em `evidence/release/<build>/`. Os self-tests
principal, de aceitação original, de relocação e o segundo insucesso histórico ficam em projeções
`*-summary.json`: somente checks booleanos e metadados permitidos, sem estados
brutos, recibos completos, chaves ou payloads. Essas projeções NÃO preservam os
bytes dos relatórios originais; incluem seu SHA e tamanho, e os originais completos
continuam fora deste Git. Os demais JSONs selecionados preservam seus bytes e
caminhos originais como proveniência;
`release-evidence-map.json` relaciona cada referência local original ao arquivo
portátil e ao SHA-256. Para conferir dependências em outra máquina, resolva
somente entradas desse índice sem reescrever os JSONs. A conferência dos gates
congelados também requer o executável correspondente entregue separadamente.
Os registros da cópia e da nova validação após relocação identificam a origem
pública no working-copy; não são atribuídos a um caminho inexistente do build
canônico. A proveniência interna desses JSONs continua preservada.
O índice não copia estados privados, não reexecuta testes e não autentica autoria.

As evidências históricas de terminal e contenção conservam os resultados de cada
rodada. Consulte `docs/results/orionq_terminal_linux_20261011.md`,
`docs/results/orionq_terminal_brand_linux_20261011.md` e
`docs/results/orionq_controller_contention_20261011.md`. Na campanha original,
o consumo duplicado de par no caminho legado permanecia aberto. O estado atual,
as regressões de correção e os limites devem ser conferidos no relatório desta
versão e em `docs/product/orionq_controller_concurrency.md`; exportar esses
registros não reexecuta nem amplia a qualificação. O envelope v1 não atesta
série/endpoint: cabe ao adaptador/serviço autorizado verificar a associação
fonte → identidade configurada. Não há qualificação física ou de fencing
distribuído externo. Somente os reproduzidores públicos nomeados de
`work/` e os cinco fontes/contratos RTL nomeados de `fpga/orionq_pl/` entram na
seleção. Ambientes e demais arquivos dessas árvores ficam fora. Arquivos compactados da
tese e distribuições compiladas não são fontes deste snapshot.

Os binários Windows/Linux e os wheels são entregues separadamente e devem ser
anexados manualmente como assets ao GitHub Releases; não entram no Git. Este
snapshot inclui somente os manifests, hashes e evidências de qualificação
selecionados. Links relativos dos relatórios para tar/ZIP/wheel só funcionam
quando esses assets estão nos mesmos caminhos locais indicados. Nenhuma URL
GitHub é presumida e nenhum upload é realizado. Consulte `SOURCE_RELEASE.md`.
O perfil Qt histórico 5.64.2 usa `release/linux/5.64.2-native-gui-bootstrap-release`;
seus resultados não qualificam o snapshot 5.66.2. Os resultados de bootstrap
e do comparador incluem o provider entre os 52 módulos e 25 recursos exigidos.
A prova `gui-bootstrap-source-binding.json` identifica separadamente qualquer
diferença auxiliar de `SOURCES.txt`; isso não implica mudança dos módulos,
recursos ou metadados de versão executados. Leia as condições e limites no
relatório final antes de interpretar históricos como aprovação do candidato.
Para reconstruir o perfil
Linux sem GUI, use `packaging/core/prepare_source.py` em um destino novo e faça
o build desse destino com Python; `packaging/core/requirements.txt` define
seu envelope. `tools/build_windows_console.ps1` continua sendo o build Windows
completo. Os links históricos para `release/` não trazem binários ao novo Git.

Licenças de terceiros devem ser conferidas antes de publicação. A licença
principal é Apache 2.0. Os manifests de projeto dos componentes qnet declaram
MIT, mas suas árvores locais não contêm LICENSE/COPYING upstream. Os dados
Cambridge conservam o aviso CC BY 4.0 e a referência no LEIAME. As fontes
conservam seus avisos OFL. A fonte Quantum não integra os recursos atuais nem
os novos pacotes; distribuições históricas permanecem identificadas como tais.
Esses registros não atribuem Apache automaticamente aos componentes, fontes
ou modelos.

Depois de inicializar e conferir o repositório, crie um remoto próprio no GitHub
e envie a branch. Nenhuma publicação é realizada por este exportador.
