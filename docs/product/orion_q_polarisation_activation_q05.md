# Q05 para o alvo de polarização — ativação do compensador aprendido

Implementado em 29/09/2026 (`control_plane/polarisation_activation.py`). Leva ao alvo
`polarisation.compensation.v1` a mesma disciplina do Q05 do repetidor: um modelo só
entra no laço simulado por ativação explícita, auditada e reversível.

## Estado padrão

O slot começa com o filtro alfa-beta (α = 0,01, β = 0, os ganhos ressintonizados na
campanha v1). Nenhum peso é lido. Reiniciar o processo volta a esse estado; registros
históricos não são restaurados.

## Etapas

| Etapa | O que confere | O que produz |
|---|---|---|
| Preparar | contrato do checkpoint na planta; hash dos arquivos do modelo (`model/*.json` e `model/*.npz`); resumo da campanha cujo hash o operador informa, com protocolo v1, portão de reprodução aprovado e vitória significativa (Holm) contra os três baselines **para esses pesos** | proposta com a configuração atual e a proposta; expira em 300 s |
| Revisar | recalcula tudo acima e o hash da configuração viva | revisão CURRENT (não é aprovação) |
| Aprovar | proposta e revisão atuais; declaração `APPROVE_SIMULATION_PROPOSAL_ONLY`; rótulo do operador | aprovação declarada, `operator_authenticated=false` |
| Ativar | `ACTIVATE_APPROVED_SIMULATION_POLICY`; revalida sob trava e `BEGIN IMMEDIATE`; constrói o controlador e confere que ele reproduz a configuração aprovada | registro COMMITTED; troca do controlador; falha na gravação restaura o anterior |
| Ler | registro no diário × configuração viva | ACTIVE, NO_LOCAL_ACTIVATION ou UNVERIFIED (o laço não roda em UNVERIFIED) |
| Reverter | registro da ativação vigente | proposta de volta ao alfa-beta, que exige nova revisão, aprovação e ativação |

Cada execução do laço grava em `polarisation_cycles` o resumo, o livro do supervisor e a
ativação sob a qual rodou. Uma aprovação é consumida pela tentativa de ativação.

A evidência se vincula aos **pesos**, não ao ZIP: o resumo registra o hash dos arquivos do
modelo, e a proposta recalcula esse hash a partir do pacote aberto. Um resumo produzido com
outros pesos, outro protocolo, reprodução reprovada ou vantagem não significativa contra
qualquer baseline é recusado. O checkpoint de desenvolvimento nunca passa na planta
calibrada, porque o contrato o recusa antes de olhar a evidência.

## Demonstração

```
python tools/run_polarisation_closed_loop.py --qnet-zip F:\qnet_network.zip --output outputs\polarisation-closed-loop-v1
python tools/polarisation_control_demo.py --qnet-zip F:\qnet_network.zip ^
    --evidence outputs\polarisation-closed-loop-v1\summary.json ^
    --operator "Alexandre" --confirm --output outputs\polarisation-demo
```

Saída na nuvem (29/09, ZIP remontado do export extraído, semente 2026):

```
1. Estado inicial: NO_LOCAL_ACTIVATION
  baseline          baseline.alpha_beta                QBER 0.0448  chave 49.1 kbit/s
2. Ativação do compensador aprendido pelo fluxo Q05
  modelo: ACTIVE (qnet.compensation.calibrado.ridge)
  modelo-ativo      qnet.compensation.calibrado.ridge  QBER 0.0123  chave 80.9 kbit/s
3. Falha injetada (50 passos inválidos a partir de t = 5 s)
  falha-injetada    qnet.compensation.calibrado.ridge  QBER 0.0359  falhas 8  degradou 5.014
4. Reversão aprovada para o filtro clássico
  reversao: ACTIVE (baseline.alpha_beta)
  apos-reversao     baseline.alpha_beta                QBER 0.0448  chave 49.1 kbit/s
```

Cada etapa grava seu recibo em `--output` sem sobrescrever; o diário SQLite fica ao lado.

## Console

EDGE CONTROL → **POLARISATION LOOP** abre o painel nativo (`console/widgets/polarisation_control.py`).
Ele verifica o ZIP de pesos (VERIFY QNET ZIP), seleciona o resumo da campanha (SELECT EVIDENCE),
escolhe o checkpoint e percorre PREPARE → REVIEW → APPROVE… → ACTIVATE… → READ ACTIVE, com as
mesmas confirmações dos painéis Q05 (rótulo do operador e caixas inicialmente desmarcadas). RUN LOOP
roda 20 s da planta calibrada com o controlador vivo, opcionalmente com 100 ms de propostas inválidas
a partir de t = 5 s, e desenha a QBER a cada 100 ms das três últimas execuções, com a linha de 11 %.
PREPARE ROLLBACK volta ao alfa-beta pelo mesmo fluxo; EXPORT RECEIPT grava o último recibo sem
sobrescrever. O diário fica em `%LOCALAPPDATA%\ORION-Q\polarisation\journal.sqlite`. Todo trabalho
roda em segundo plano; trocar checkpoint, pacote ou evidência, ou fechar o painel, descarta a seleção
capturada. Testes: `tests/test_console_polarisation_control.py`.

## Limites

- Processo único; não há restauração após reinício, e o fluxo é separado do POLICY REVIEW do
  repetidor.
- A aprovação declara um rótulo; não autentica o operador.
- Simulação apenas. Nenhum registro autoriza atuação física, e o atuador é ideal.
- Testes: `tests/test_polarisation_activation.py` (15 casos: fluxo completo e reversão,
  evidência inválida ou de outros pesos, contrato, expiração, seleção consumida, falha de
  gravação com restauração, registro adulterado bloqueando o laço, degradação sob modelo ativo).
