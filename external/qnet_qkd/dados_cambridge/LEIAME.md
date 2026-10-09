# Dados do Cambridge Quantum Network

Arquivos baixados do repositório Apollo da Universidade de Cambridge:

> Dynes, J. F.; Wonfor, A.; Tam, W. W.-S.; Sharpe, A. W.; Takahashi, R. et al.
> *Data relating to Cambridge Quantum Network.* Apollo — University of Cambridge
> Repository, 2019. https://doi.org/10.17863/CAM.44524

Licença: Creative Commons Attribution 4.0 (CC BY 4.0). Artigo associado:
Dynes et al., "Cambridge quantum network", *npj Quantum Information* 5:101 (2019),
https://doi.org/10.1038/s41534-019-0221-4.

Usados por `scripts/validate_cambridge.py`:

- `2b_1.txt` — taxa de chave segura e QBER diários do enlace CAPE–TREL,
  nov/2016 a jul/2018. A coluna rotulada "(%)" contém frações (0,0268 = 2,68%).
- `4b.txt` — QBER contra a potência recebida de um canal clássico de 100 Gbit/s
  na mesma fibra, com barras de erro.

Não usados (nenhum modelo deste pacote faz previsão sobre eles): `1b`, `1e_*`
(curvas analíticas de escalabilidade), `3b_*` (tráfego de gestão de chaves da
rede), `4c` (BER pré-FEC do canal clássico ao longo de 24 h).
