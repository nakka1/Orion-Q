# Comunicadores e GEO — validação de 06/10/2026

Versão 5.40.0. Escopo: cadastros, fontes simuladas/arquivo, ativação de dois canais,
topologia, órbita e interface nativa. A versão 5.39.0 e as alterações recebidas
foram preservadas. Sem atuação física ou validação de instrumentação externa.

## Casos de uso

| Caso | Exercício e evidência | Resultado observado |
|---|---|---|
| Nomenclaturas | 14 identificadores públicos, nomes/categorias e funções; IDs internos mantidos | Cadastros publicados pelo backend, sem quebra de sessões e enlaces |
| Migração/reabertura | Semear novamente e reabrir SQLite | Idempotência e persistência |
| Compatibilidade da migração | Campanhas congeladas e versões de nodes/topologia | Metadados iniciais preservam versões operacionais; edições avançam a revisão |
| Recibos e inventário editado | Leitura do índice SQLite, hash confirmado e início de demonstração | Datas de índice/registro consistentes; leitura preserva a topologia existente |
| Instalação nova | Primeiro cadastro via backend e interface, sem campanha carregada | Inicialização e cadastro na mesma transação; erro não persiste o blueprint |
| Instalação terrestre | Formulário, salvar, readback | Cadastro funcional; nenhuma conexão inventada |
| Repetidor | BSM + memória, cadastrar/ativar percurso | Tentativas BSM e consumo de recursos observáveis |
| Estação de medição | Papel central SNS-TF, dois emissores | Aquisição, interferência e processamento causal |
| Estação óptica | Parâmetros terrestres e capacidade satelital | Cadastro aceito, compatibilidade verificada |
| Satélite | Tipo físico separado da função, órbita/estações | Posição dinâmica e órbita publicadas pelo backend |
| Memória | Plataforma/modos/coerência/escrita/leitura | Parâmetros entram no gerenciamento de recursos e decoerência |
| Purificação | Serviço BBPSSW e percurso operacional | Tentativas e consumo de dois pares, sem cópias retidas |
| Dois canais | Identidades diferentes, receptor e limites | Configuração admitida; canal ausente/desabilitado recusado |
| Alias público | Percurso usando BR-PUCRIO/BR-UFRJ | Resolvido para a mesma identidade persistente |
| Reconfiguração | Substituir segmentos de uma sessão | Segmentos antigos da sessão retirados; outros preservados |
| Edição inválida | Eficiência >1, segredo literal, função incorreta, BSM >0,5, memória incompatível | Recusa sem alteração parcial |
| Concorrência | Revisão/topologia desatualizadas | Conflito explícito; cadastro anterior preservado |
| Hardware indisponível | Cadastro físico, tentativa de ativação | Recusada por ausência de driver validado |
| Mudança de detector | Eficiência zero em uma extremidade | Nenhum par admitido; resultado não pré-calculado |
| Memórias distintas | T=2 s e T=4 s nos extremos | Decoerência composta com T efetivo de 4/3 s |
| Arquivo de medições | Replay sintético de observações brutas e mesmos comandos | Mesmas métricas; recibos com origem measurement_file |
| Interrupção da fonte | Selecionar none com fonte cadastrada | Sem novas observações/resultados ou geração |
| Desativar componente | CBPF disabled | Enlaces legados/modulares bloqueados; sem transmissão orbital |
| Janela orbital | Fechar janelas no relógio da rede | Transmissões removidas e coincidências zero |
| Posição e Doppler | Avançar o relógio | ECEF e velocidade radial/Doppler mudam |
| Órbita inválida | LEO com apogeu fora da classe, fonte TLE e época fora do intervalo | Recusa antes da persistência |
| Composição indisponível | Memória com driver satelital sem fluxo de recursos | Recusa explícita; recursos não executados não são anunciados como resultados |
| Emissão zero | Taxa satelital configurada em zero | Zero pares emitidos e nenhuma linha de transmissão |
| Pulsos insuficientes | Bloco TF abaixo do mínimo de categorias do modelo | Ausência de observação, sem completar artificialmente a amostra |
| Kepler elíptico | e=0,2; perigeu/apogeu e mudança de época | Raios consistentes com os parâmetros, referencial temporal preservado |
| Rótulos GEO | Classificações no snapshot | Destaque apenas Endpoint/Core |
| Foco satelital | Selecionar, zoom out, globo, orbitar/retornar | Órbita e links seguem snapshot; timer encerrado ao voltar |
| Resoluções | Renderizar 640×480, 1280×800 e 3440×1440 | Formulários/globo renderizados sem exceção |
| Rascunhos | Tick, captura de evidências, reconstrução por tema | Rascunhos incompletos e textos preservados |
| Ativação pela UI | Formulário de conexão e envio ao serviço | Confirmação após readback e eventos causais |

## Artefatos

`outputs/communicators_geo_20261006/` conserva todas as tentativas e seus XMLs,
incluindo as falhas intermediárias corrigidas. `source-diagnostic-1/report.json`
passou nas 15 verificações por widgets Qt e backend; inclui capturas do cadastro,
conexão, labels e globo. O diagnóstico é incorporado ao `--self-test` para conferir
o mesmo fluxo no executável congelado.

Consolidação dos 19 blocos exigidos pelo build e das repetições afetadas:
**3.313 casos únicos aprovados, quatro pulados e nenhuma falha pendente**.
As identidades dos casos são deduplicadas por módulo/nome; repetições não
aumentam a contagem. `source-validation-summary.json` registra os XMLs usados.
Os quatro pulados exigem o ambiente isolado LabOne Q 26.7.0 (um caso) ou o
`modelo_qkd_ml.zip` com hash fixado via `ORION_QKD_ARCHIVE` (três casos).
Os 37 casos específicos de Comunicadores/GEO estão cobertos nos XMLs
`communicators-final.xml`, `orbit-constraints.xml` e `enrolment.xml`.

Resultados da compilação final `release/windows/5.40.0-native-20261006-171729`:

| Verificação no executável | Resultado |
|---|---|
| Roadmap congelado | 4 checks aprovados |
| Worker de campanhas temporais | 25 checks aprovados |
| Modelos opcionais | Aprovado sem pacotes, modelos opcionais descarregados |
| Autoteste geral | 36 checks principais aprovados |
| Comunicadores/GEO | 18/18, sete cadastros, dois canais, posição orbital e transmissões |
| Matriz da interface | 281 checks aprovados, dez resoluções |
| Estresse do controlador | 535 checks aprovados em 20 rodadas |
| Abertura nativa | 6/6, normal 100–200%, demonstração 100% e 150% |
| Aceite automático | Aprovado; teste independente em outro computador continua pendente |

Primeiro snapshot após mostrar a janela: 5.781–6.250 s em
operação normal e 10.094–10.250 s na demonstração. São medidas
nesta máquina. Os controles de layout responderam; não há garantia de FPS ou
latência para outros equipamentos.

O diagnóstico de 20 ciclos não atendeu ao orçamento de 10 ms em nenhum ciclo:
p50=216.562 ms, p95=384.780 ms, máximo=399.289 ms.
A amostra não sustenta uma afirmação de p99; este aceite funcional não aprova
controle físico com prazo rígido de 10 ms.

SHA-256 do executável final: `d9a05abb5acae0294e036c2a1df34b76fcef6aee77edff66fd1526b44219195a`.
O ZIP e seu SHA-256 constam em `portable-package.json` e no arquivo `.sha256` ao
lado do pacote. A documentação final é incluída depois do aceite; o executável
permanece idêntico ao que os gates aprovaram. Todas as tentativas anteriores
foram mantidas. Capturas finais ficam em `communicators-geo/` nessa release.


## Correções encontradas na validação

O teste legado do terminal e dois testes de mapa exigiam desenhar sempre os 71
enlaces. Foram atualizados para exigir exatamente os enlaces terrestres e os
enlaces satelitais com emissão recebida, conforme a nova regra. O teste de
exportação assíncrona passou a obter a revisão atual em vez de fixar um número.

A migração inicial avançava versões operacionais e causava recusa de campanhas
congeladas. O código foi corrigido para registrar metadados/auditoria mantendo
essas versões, com comparação transacional do estado anterior completo. Edições
do usuário continuam incrementando a versão. Os manifests e critérios científicos
foram preservados. O bloco amplo passou posteriormente com 492 testes.

A validação posterior de comandos encontrou uma divergência entre a data do
índice SQLite e a data preservada dentro do registro de topologia. A gravação
foi corrigida para manter as duas consistentes. O cadastro inicial tardio também
foi restringido: a leitura de um inventário comum editado preserva seus hashes;
os componentes legados são cadastrados na primeira ação explícita de salvar um
comunicador. Os 28 testes direcionados dessas correções passaram.

O primeiro autoteste congelado aprovou as 18 verificações específicas, mas
recusou a matriz geral por uma lista antiga que exigia seis abas. A expectativa
foi atualizada para incluir Comunicadores. A matriz completa em fontes passou
nas 281 verificações, incluindo dez resoluções. O executável dessa primeira
tentativa e seus relatórios foram preservados em outra pasta de release.

## Limites da evidência

Os testes controlam widgets Qt e os adaptadores reais do projeto sobre fontes
simuladas/arquivo. Não medem instalações físicas nem validam desempenho em todos
os monitores/GPUs. Renderização em 3440×1440 não substitui um teste prolongado na
máquina do usuário. Animação a 30 Hz é frequência nominal do timer, não garantia
de FPS. Efemérides são hipóteses de dois corpos, sem SGP4/TLE, perturbações ou
rastreamento de satélite real. O modelo QKD mantém orçamento condicional e zero
chaves certificadas. ML é opcional e inicia desabilitado. Consulte os
[contratos e limites](../product/orion_q_communicators_geo.md) para execução e fontes.
