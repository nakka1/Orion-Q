# Q05 — ativação e leitura nativas da política (28/09/2026)

POLICY REVIEW agora permite ativar explicitamente o limiar aprovado da política
existente e consultar a política atual na aba ACTIVE POLICY. A confirmação de
ativação é separada da aprovação e começa desmarcada. Somente fixtures locais de
simulação foram ativadas durante a validação. [Contrato](../product/orion_q_policy_activation_q05.md).

## Comportamento entregue

A confirmação mostra limiares anterior/aprovado, ID/hash da aprovação, operador
declarado e topologia. O worker usa a sessão privada do painel e mantém as
validações do backend: contexto, prazo, instância, aprovação selecionada e diário.
Ativação não executa ciclos ou decisões e não carrega modelos opcionais.

Fonte/runtime/capacidade, edição, expiração e fechamento invalidam a confirmação.
O backend recebe um callback interno adicional de validação da fonte, chamado
após adquirir o bloqueio de ciclo e imediatamente antes do commit. Cancelamento
anterior a essa fronteira impede a troca persistida. A GUI não espera o worker
para invalidar a seleção. Após o início do commit, a operação aceita pode terminar
mesmo com o painel fechado: resultados atrasados são descartados pela interface,
mas o diário conserva o resultado e uma nova leitura verifica o estado real.
Não se promete desfazer um commit ao fechar ou trocar fonte.

Sucesso mostra COMMITTED como evidência histórica e consome a aprovação. READ ACTIVE
POLICY consulta explicitamente o estado sem exigir nova preparação, mostra horário
e identidade da instância, e identifica política/limiar/revisão/hash quando ACTIVE.
NO_LOCAL_ACTIVATION, UNVERIFIED e UNRESOLVED permanecem distintos. Não há atualização
automática da observação nem afirmação de atividade atual baseada só em commit.
UNRESOLVED indica ciclos bloqueados e recuperação disponível pela API local.

EXPORT CAPTURED STATE escreve o recibo selecionado em segundo plano, sem sobrescrita,
com captura antes do diálogo de arquivo e nova validação de seleção/fonte antes do
envio. Exportação preserva a observação passada, sem reler a política.

## Validação local

- **406 testes distintos aprovados**, incluindo **31 testes novos** da interface
  de ativação/readback, sem falhas, erros ou skips.
- A primeira suíte gráfica (`ui.xml`) passou com **92 casos**,
  incluindo preparação/revisão e aprovação existentes. A suíte de capacidades/fontes
  (`capabilities.xml`) passou com **78 casos**, incluindo
  os quatro casos acrescentados e repetição da interface nova. O total distinto
  elimina os casos repetidos entre essas rodadas.
- Integração (`integration.xml`): **263 casos** de ativação, recuperação/rollback,
  preparação, aprovação, sandbox, barreira física e serviço QKD.
- Casos novos incluem confirmação desmarcada/cancelada, mudança de fonte/capacidade/
  runtime, expiração, mudança de topologia/política, cancelamento durante leitura e
  espera pelo ciclo, commit incerto antes/depois de persistir, fechamento após início
  do commit, observação posterior, reinício e exportação capturada/exclusiva.
- Ruff passou nos sete arquivos Python envolvidos. YAML do workflow e sintaxe
  PowerShell passaram. Ambos incluem a nova suíte nativa.

Capturas inspecionadas visualmente: confirmação em **600×450** e painel em **640×500**
(o painel também foi capturado em **1000×700**). Estão em
`outputs/policy_activation_ui_20260928/gui/`, com recibos de ativação e leitura
produzidos pelo teste automatizado. Não representam aprovação humana real.
A política da fixture passa de 0.65 para 0.7, sem execução de ciclos pela interface.
Testes do backend podem executar somente seus próprios simuladores isolados.

## Preservação e roadmap

`outputs/policy_activation_ui_20260928/verification.json` compara **594 arquivos
preexistentes**, conserva os **240 relatórios SO(3)**, **15 fontes congeladas**,
summary/protocolo e XML histórico dos **500 testes**. `implementation.diff` registra
somente este incremento; alterações anteriores foram preservadas.

Não houve campanha científica, ativação no runtime de operação, CI remota ou
reconstrução do executável Windows. Modelos permanecem opcionais/OFF e sem atuação
física. A ativação continua vinculada à instância, sem restauração após reinício.

Q05 permanece parcial: o próximo passo no [roadmap](../product/roadmap.md) é integrar
rollback aprovado e recuperação explícita à interface, seguindo as APIs existentes,
e validar o fluxo nativo completo. Os demais aceites Q16 permanecem pendentes.
