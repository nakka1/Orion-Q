# Diário operacional de campanhas — 14/09/2026

Registro antes do despacho e finalização após readback implementados na console nativa, com tabela SQLite aditiva e aba ATTEMPTS. Tentativas e recibos científicos permanecem separados. O motor de simulação e os métodos estatísticos não foram alterados.

## Evidências locais

- 170 testes de regressão aprovados em 130,56 s: `outputs/software_validation_campaign_attempts/accepted-tests.xml`.
- Layout rerenderizado após corrigir a instalação do tema na fixture: `layout-tests.xml`, 1 teste aprovado em 5,43 s. Capturas 640×500 e 1080×740 inspecionadas. Não somar esse teste repetido à regressão como caso único.
- Ruff aprovado nos módulos alterados; sintaxe do build e YAML do workflow verificados. O workflow remoto não foi disparado.
- 14 verificações do worker empacotado aprovadas: `outputs/software_validation_campaign_attempts/frozen-worker/report.json`. O controlador externo desse roteiro é Python; seus processos de campanha executam o EXE real. A leitura da aba operacional nesse roteiro não é uma sessão manual da GUI congelada.
- Self-test separado do executável: 19 verificações aprovadas, incluindo matriz responsiva. Stress separado de 20 rodadas aprovado.
- Paridade estrutural de objetos de código entre fonte e PYZ confirmada para `campaign_runner`, `campaign_job`, `campaign_attempts` e `topology`, com otimização 1 e normalização apenas de nomes de arquivos.

## Artefato

Pasta: `release/windows/5.38.0-campaign-attempts-20260914/`.

ZIP: `ORION-Q-5.38.0-native-windows-x64.zip`, 283175797 bytes, 3833 arquivos, CRC integral verificado.

SHA256 do ZIP: `61c561b9973defe6f4d6f457a3964fe3305b2c9bfd3ab0456ea4f5cc025dc7e4`.

SHA256 do executável: `4c8cca9e553fc96496e2f1dae259dbbe70a5fb7eca1ccec14a72914aceb33ec2`.

O relatório de diagnóstico final fica em `native-acceptance/acceptance-report.json`, dentro da raiz dessa distribuição e fora do programa. As distribuições anteriores foram preservadas.

Diagnóstico nativo final aprovado em 42,30 s: integridade de 3832 arquivos declarados (excluindo o próprio manifesto), self-test e stress aprovados; código 0, `automatic_passed: true`. Continua como `manual_review_required` e `independent_machine_verified: false`, pois foi executado neste computador de desenvolvimento.

## Limites e próximo passo

OPEN não atesta liveness. Não há reconciliação automática, preenchimento retroativo, captura dos runners CLI nem exportação própria da tentativa; o JSON operacional pode ser consultado/copiado na aba. A gravação inicial indisponível impede despacho. Falha de finalização operacional é avisada e não apaga recibos científicos válidos.

Próxima melhoria funcional recomendada: exportação explícita do registro operacional, com verificação e sem sobrescrita, separada do botão de exportação científica. Validação manual em outro computador e evidência de hardware real continuam pendentes. [Guia operacional](../product/orion_q_campaign_attempts.md).
