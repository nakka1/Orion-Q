# ORION-Q — distribuição Windows com sessões opcionais

Data: 23/09/2026. Build portátil x64 `5.38.0-native-20260923-011322`.
Inclui o catálogo, verificação local de pacotes e dez configurações de sessões
offline. O detector permanece bloqueado; não houve ativação Q05 ou controle físico.

## Entrega

- ZIP: `release/windows/5.38.0-native-20260923-011322/ORION-Q-5.38.0-native-windows-x64.zip`.
- Tamanho: 283.356.528 bytes; 3.836 arquivos, incluindo o manifesto de hashes.
- Executável: subpasta `ORION-Q/ORION-Q.exe`. Extraia a pasta inteira e mantenha
  `_internal` ao lado do executável; não mova só o `.exe`.
- ZIP SHA-256: `025d231c558cfaf47337d5548395989456d6e29aba600b92174c4719d21a72ce`.
- EXE SHA-256: `776f4c942dc13b82fc9fa8dfd5854ce4a8f6ef776d27ed39d4b1ce6e008bdcfe`.

O pacote contém Python, Qt e as dependências do aplicativo. Não requer Python
ou Node externos. Foi gerado com Python 3.12.14 e PyInstaller 6.22.2 em Windows 11
x64. É um pacote portátil, não um novo instalador; não tem assinatura digital
de editor. Builds anteriores e dados do operador foram preservados.

Os novos pacotes `modelo_qkd_ml.zip` e `qnet_network.zip` **não foram embutidos**.
O modelo WDM que já fazia parte do produto permanece no pacote base. Não se
afirma que todo o aplicativo deixou de conter pesos: a opcionalidade aqui é a
dos novos componentes QKD/Quantum Network.

## Uso das novas funções

EDGE CONTROL → OPTIONAL MODEL CATALOG → selecionar configuração → VERIFY LOCAL
ZIP → OFFLINE SESSION. Informar o pacote QKD ou Quantum Network correspondente,
então usar LOAD JSON ou DEMO e executar RUN explicitamente. Sem os novos pacotes,
o aplicativo e o catálogo abrem; as avaliações permanecem desabilitadas.

O [guia Windows](../product/orion_q_optional_models_windows.md), também incluído
em `ORION-Q/docs/orion_q_optional_models_windows.md`, descreve os dois modos de
diagnóstico e a inspeção manual. Documentos de etapas anteriores preservam o
escopo histórico; este relatório registra a entrega Windows das sessões.

## Verificação realizada

O fluxo completo de build passou em quatro conjuntos: **68 + 483 + 86 + 289 =
926 testes selecionados**, cobrindo console, campanhas/histórico, comandos e
modelos opcionais. Não é a suíte inteira do repositório. Os 11 testes adicionais
da barreira de empacotamento também passaram; o relatório focado final reúne
esses 11 e seis testes do diagnóstico. Ruff aprovou os arquivos Python alterados.

No executável real:

- Diagnóstico sem pacotes: catálogo e dez sessões disponíveis, inicialmente
  sem pesos/resultados, opções OFF e runtime indisponível.
- Diagnóstico com os dois pacotes explicitamente informados: dez verificações
  de identidade e dez demonstrações pelo worker Qt, com exportação exclusiva e
  invalidação do resultado após edição.
- Ambos os modos repetidos em diretório de trabalho separado, com caminhos
  contendo espaços, sem PYTHONPATH/PYTHONHOME e PATH limitado ao Windows.
  O harness usa Python; os processos diagnosticados usam somente o `.exe`.
- Paridade das dez demonstrações fonte versus pacote: maior diferença numérica
  **0,0**. Apenas `evaluated_at_utc` foi excluído da comparação. Isso não reproduz
  uma campanha científica ou comprova vantagem dos modelos.
- Autoteste de recursos, telas, fontes, responsividade, exportação e auditoria.
- Stress sintético de 20 rodadas, sem hardware.
- Worker de campanhas, incluindo paridade fonte/pacote, cancelamento, confirmação,
  exportação, histórico e ausência de mutação indevida do workspace.
- CRC do ZIP, hashes dos arquivos e aceitação nativa geral aprovados.

O relatório geral mantém `acceptance_status: manual_review_required`,
`manual_review: pending` e `independent_machine_verified: false`. Todos os testes
foram neste computador; não houve VM limpa, segunda máquina ou certificação física.

## Diagnóstico e barreira de distribuição

`--optional-model-test --output-dir pasta-nova` testa a ausência de pacotes.
Acrescentar **ambos** `--qkd-zip` e `--qnet-zip` habilita os testes com pesos.
Nenhum caminho é inferido ou procurado automaticamente. Saída existente ou dentro
da pasta do programa é recusada. Uma falha não vira aprovação silenciosa.

O empacotador exige que o diagnóstico sem pacotes seja de um executável congelado,
com o mesmo hash, todas as dez opções verificadas sem carga, sem atuação/política
ativada e resultado aprovado. A declaração é um diagnóstico local, não assinatura
ou autenticação criptográfica de autoria do relatório.

O script de build e o workflow Windows foram atualizados. O workflow remoto não
foi disparado; não houve publicação de release ou envio externo dos pacotes.

## Evidências locais

Em `release/windows/5.38.0-native-20260923-011322/`:

- `optional-models-validation/optional-models-report.json`;
- `campaign-validation/report.json`, `self-test.json`, `stress-validation/`;
- `portable-package.json`, ZIP e arquivo `.sha256`;
- `native-acceptance/acceptance-report.json`.

Em `outputs/optional_models_windows_20260923/`:

- `optional-diagnostic-tests.xml`, `package-tests.xml`, `diagnostic-tests.xml`;
- `source-external/`: dez avaliações em fonte;
- `isolated frozen validation/`: diagnósticos com e sem pacotes, logs e dez avaliações;
- `source-frozen-parity.json`.

## Pendências

Inspeção manual em segunda máquina, distribuição/licenças dos pesos, export
seguro do detector, ponte tipada para runtime shadow, ensaios em laço fechado e
ativação auditável Q05. Nenhuma previsão foi promovida a comando autorizado ou
evidência medida de fidelidade de Bell por este empacotamento.
