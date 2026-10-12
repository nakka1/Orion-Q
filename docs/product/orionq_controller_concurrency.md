# OrionQ — exclusividade de pares e recursos de controle

Revisão de 11 de outubro de 2026. Estas garantias são de software; não
qualificam memórias quânticas físicas, interlocks ou um barramento FPGA.

## Estoque legado de pares simulados

`DemandStore.set_pair` aceita `expected_pair` e `expected_demand`. O engine
passa ambos para geração, consumo e purificação e os utiliza também na
reconciliação. Sob `BEGIN IMMEDIATE`, verifica a demanda persistida, o par
observado e as gerações das memórias antes de substituir a linha. Um snapshot
superado produz `PairConflict`, sem alterar o estoque. Pares novos têm
`pair_id` único; consumir, purificar ou invalidar um par antigo não pode
ressuscitar um par já consumido. A comparação da demanda também impede que
um episódio iniciado entre A e B seja gravado após redefinição para A e C.

A escrita incondicional continua disponível para importação explícita e
migração de dados pelo código confiável. Não é usada para consumir pares pelo
engine. Após uma recusa concorrente, consulte o estado durável; não repita
automaticamente uma operação científica com um snapshot antigo.

## Recursos identificados e autoridade compartilhada

`node_id` identifica um ponto dentro de um cenário; ele não comprova a
identidade de um dispositivo. Duas simulações independentes podem ter N01.
Para vincular um instrumento ou recurso compartilhado, inclua no nó:

```json
"resource_identity": {
  "namespace": "swabian",
  "serial": "SERIAL-QUALIFICADO",
  "endpoint": "tcp://instrumento-autorizado:porta"
}
```

Os três campos são obrigatórios nesse objeto, limitados a 512 caracteres e
sem controles ou credenciais no endpoint. Namespace e serial identificam o
recurso; trocar o endereço não cria uma segunda capacidade para a mesma
unidade. Identidades duplicadas em uma configuração são recusadas.

No serviço headless, configure `resource_authority_root` no JSON de
implantação, com caminho absoluto. Todos os controladores autorizados a usar
os mesmos recursos **devem apontar para a mesma autoridade**, provisionada
com ACLs pelo administrador e mantida separadamente de cópias das bases de
cada controlador. O caminho é composição confiável de implantação; clientes
HTTP não podem fornecê-lo. O uso direto por Python exige injetar
`EquipmentAuthority` como `MixedController(..., resource_authority=...)`.
Conflitos ou autoridade indisponível retornam HTTP **409**, com
`resource_authority_conflict_or_unavailable`; esse código não afirma que uma
configuração nova tenha sido aplicada. A configuração anterior é preservada.

Essa autoridade mantém um lock do sistema operacional durante a sessão e
uma geração de fencing durável. O namespace `controller-resource:` separa
exclusividade de capacidade entre controladores da autorização de aquisição
do driver. Configuração verificada, calibração, sincronismo e autoridade
autenticada do instrumento continuam exigidas pelo adaptador; o lock do
controlador não as substitui.

O mecanismo é deliberadamente conservador: um único controlador possui a
capacidade de uma identidade durante sua configuração, mesmo quando nenhum
pedido esteja usando a capacidade. Bancos diferentes não criam capacidade
duplicada dentro dessa autoridade. A configuração nova é integral: conflito
ou falha de gravação preserva configuração e reservas anteriores e libera
apenas os novos claims não publicados. Alterar o endpoint de uma identidade
já possuída requer encerramento e nova verificação da sessão.

Não há renovação pela sessão humana. Chamadas do serviço verificam/renovam a
permissão; expiração ou revogação bloqueia operações, e `status` mostra
`resource_authority_unavailable`, invalida as reservas afetadas e informa os
nós indisponíveis. Expirar a permissão não permite roubar o lock de um
processo ainda vivo. O fechamento invalida os pedidos antes de soltar os
claims. Após queda, a recuperação aumenta a geração, invalida pedidos
antigos e exige novas sessões e observações verificadas de capacidade e
canais; não reativa uma reserva pela simples presença da configuração.

Uma observação identificada como `physical` é recusada se seu nó não estiver
vinculado a um recurso com autoridade. O normalizador de aquisição física
continua exigindo SDK/transporte físico verificado e a permissão do adaptador.
O envelope v1 não inclui uma atestação de série/endpoint: a associação entre
fonte, nó e identidade configurada deve ser verificada pelo adaptador/serviço
autorizado que publica a observação. O contrato e o controlador isoladamente
não comprovam sua origem física. Essa vinculação foi ensaiada com interfaces
emuladas; não constitui qualificação de hardware ou de fencing externo.
A política de rotas recebe apenas capacidades e estados já admitidos; ela
não escolhe rotas pelo nome do driver ou pela origem dos dados.

`resource_authority.scope` distingue `shared_local_authority` de
`isolated_simulation`; `bound_nodes`, `fences` e `unavailable_nodes` dão
visibilidade ao vínculo. **Não existe arbitragem distribuída entre hosts ou
entre raízes de autoridade configuradas de forma independente.** Para esse
caso é necessário um serviço de autoridade comum e fences aceitos pelos
dispositivos. Não use bases/raízes copiadas para representar exclusividade
operacional e não interprete uma simulação independente como controle de
hardware. Reverter toda a máquina/autoridade não comprova monotonicidade
externa; preservar essa recuperação exige infraestrutura externa qualificada.

## Commit e auditoria autenticada

Reserva e liberação registram o recibo e uma entrada `mixed_audit_outbox`
na mesma transação SQLite. O recibo não contém material de chave e não prova
entrega quântica. A reserva mantém o mesmo `request_id` para recuperação;
reutilizar esse ID com outros parâmetros é recusado.

A entrada só passa a `completed` depois de registrar o resultado na auditoria
autenticada. Se essa etapa falha depois do commit, a API retorna **503** com:

```json
{
  "error": "durable_result_audit_pending",
  "committed": true,
  "operation": "reserve_mixed_route",
  "request_id": "pedido-123",
  "receipt_sha256": "sha256-do-recibo",
  "recovery": "retry_same_request_id_or_inspect_status"
}
```

`CoreRequestError` preserva `status=503`, `committed=True`, `request_id` e
`receipt_sha256`; a mensagem indica recuperação pelo mesmo ID. Não libera o
recurso e não faz retry automático. Se a resposta HTTP for perdida antes de
receber esse envelope, o resultado continua incerto: consulte o estado ou
repita **o mesmo pedido**, nunca gere outro ID para contornar a incerteza.

`audit_pending` lista até 200 entradas e `audit_pending_count` informa o total.
No retry autorizado, a operação retorna seu recibo durável e conclui a
auditoria. Se a reserva foi expirada ou invalidada após reinício, o hash
original pendente é registrado explicitamente como `reconciled`, com a
identidade do operador de recuperação. Não se apaga silenciosamente o
resultado original nem se transforma uma reserva invalidada em ativa. A
outbox é uma fila de recuperação, não substitui assinatura/MAC da auditoria.

## Regressões reproduzíveis

`tests/test_controller_concurrency_qualification.py` força os dois processos
a ler o mesmo par antes do commit; verifica exatamente um consumo, fencing
entre bases distintas, queda abrupta, nova sessão, rollback de configuração,
expiração sem roubo de lock, 38 pedidos simultâneos, recusa por revogação e
falhas de auditoria via API mTLS. Os dados e dispositivos são sintéticos.

```bash
python -m pytest tests/test_controller_concurrency_qualification.py \
  tests/test_mixed_controller.py tests/test_mixed_controller_service.py \
  tests/test_network_engine_routing.py tests/test_network_engine_regressions.py
```

Use um `--basetemp` novo se o diretório temporário padrão não estiver
acessível. Nunca reutilize como `basetemp` uma pasta com dados operacionais;
o pytest gerencia os arquivos desse diretório.
