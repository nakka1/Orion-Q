# Pacote portável de evidências

## Objetivo

Os resultados completos das campanhas permanecem em `outputs/` e não são
versionados. Para que a reivindicação formal do TCC seja verificável em um
clone novo, o projeto mantém um pacote mínimo em `evidence/tcc/v1.0.0/`.

O pacote contém 8 tabelas e 14 linhas agregadas: exatamente as linhas
selecionadas pelos 14 itens do registro formal. Ele não contém leituras brutas,
observações individuais ou pesos de modelos. Reduzir o pacote dessa forma
também evita transformar um repositório de software em uma redistribuição
integral das bases de terceiros.

## Cadeia de rastreabilidade

```text
resultado completo local em outputs/
        ↓ validação de hash, seletor e asserção
generation_registry.json
        ↓ extração das linhas agregadas citadas
tables/*.csv + provenance.json + bundle_manifest.json
        ↓ novo hash por tabela
docs/thesis/tcc_claim_registry.json
        ↓ 69 verificações executáveis
relatório de validade do TCC
```

- `generation_registry.json` preserva o registro que aponta para os resultados
  completos e possibilita reconstruir o pacote quando `outputs/` existe.
- `provenance.json` registra o hash do artefato completo, a tabela derivada, os
  itens de evidência, DOI e licença.
- `bundle_manifest.json` fixa hashes, tamanhos, contagens e declarações
  negativas sobre dados brutos e pesos.
- `portable_claim_registry.json` replica exatamente o registro publicado em
  `docs/thesis/tcc_claim_registry.json`.

## Validação sem os resultados locais

```bash
python experiments/validate_tcc_claims.py \
  --registry docs/thesis/tcc_claim_registry.json \
  --repository-root . \
  --output-dir outputs/tcc-claim-validation
```

Esse comando depende somente dos arquivos versionados. A integração contínua
também o executa, impedindo que uma alteração em tabela, seletor, asserção ou
hash passe silenciosamente.

## Reconstrução controlada

Com os oito resultados completos presentes nos caminhos registrados:

```bash
python experiments/build_tcc_evidence_bundle.py
```

O gerador primeiro exige que o registro de geração passe integralmente na
validação. Em seguida, seleciona apenas as linhas citadas, calcula novos hashes,
atualiza o registro portável e valida o resultado final. Um artefato de fonte
sem política explícita de licença é rejeitado.

## Licenças e limites

- IrelandQCI/Toshiba: DOI `10.5281/zenodo.21132088`, CC BY 4.0, com atribuição.
- Bristol HCF: DOI `10.5523/bris.61193flfovbn2puknndsr3qm5`, Non-Commercial
  Government Licence.

O resultado de replicação entre plataformas inclui uma estatística agregada
derivada também do Bristol HCF. Seu uso neste produto é acadêmico e não
comercial. A licença original deve ser reavaliada antes de redistribuição ou
uso mais amplo. Os dados brutos continuam fora do repositório.
