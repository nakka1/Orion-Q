# Sessão gráfica shadow de roteamento — 24/09/2026

Q16 avançou da API para a interface nativa: os dois roteadores possuem uma sessão
shadow opt-in, ligada ao produtor artificial por `Snapshot.optional_routing_state`.
O inventário evolui independentemente das recomendações. Nenhuma decisão consome
pares, reserva memória, chama executores ou emite comandos. Modelos permanecem
opcionais e OFF por padrão. [Contrato e uso](../product/orion_q_routing_shadow_bridge.md).

## Validação

- **531 testes aprovados**, zero falhas, erros ou skips, em **158,59 s**.
  Incluem **48 novos testes** de interface/produtor de roteamento, ponte de
  roteamento, sessões offline, catálogo, fontes, telas, alocação shadow, demais
  pontes e bloqueio de atuação física.
- **12 avaliações nativas com os pesos originais aprovadas**: dois checkpoints ×
  seis frames. Todas as saídas, exceto o instante de execução, coincidiram com a
  sessão offline sobre a mesma entrada, ordem de candidatos e semente.
- As janelas começam OFF e só avaliam após START SHADOW. Foi verificado o descarte
  de workers atrasados, sem fila adicional, ao parar/fechar, trocar fonte/pacote,
  perder contexto, receber entrada inválida ou expirar o prazo de 3,5 s.
- Duplicatas não renovam frescor. Regressão de timestamp ou versão dos recursos
  impede novo START. Mudanças de tarefa, origem, fluxo ou topologia estrutural
  invalidam a sessão. O catálogo reconfere o payload antes de abrir a janela.
- EXPORT FRAME captura o resultado antes do diálogo de arquivo; atualização
  válida não substitui o frame selecionado. Perda de contexto cancela exportação;
  arquivo existente é preservado.
- **640×480 e 1000×700**, nos dois checkpoints: controles dentro da janela e
  área de resultado acessível. As capturas finais nominal/640 e prazo_longo/1000
  foram inspecionadas visualmente, com separadores legíveis e sem cortes.
- Ruff aprovou os sete arquivos Python envolvidos. Os gates de teste Windows
  incluem a nova suíte. Não houve CI remota nem reconstrução do EXE.

A fixture usa três nós, três enlaces e quatro pares. O ciclo tem duas fases
livres, uma com enlace indisponível, uma com par reservado, uma com memória cheia
e uma com par expirado. O produtor declara 6, 6, 5, 5, 5 e 4 alternativas,
respectivamente. As regras da ponte validam integralmente cada entrada antes dos
pesos. A seleção do modelo nunca modifica esse ciclo. Cada publicação avança
1 s virtual, independentemente da cadência de pintura da interface.

## Limites científicos

As features são ilustrativas e definidas no produtor, não saídas normalizadas do
simulador do artigo. Elas exercitam a integração e a seleção reproduzível sem
certificar o domínio do modelo. WAIT é apenas uma referência declarada.
Concordância não mede desempenho; probabilidades da política não são taxas de
sucesso; retorno do crítico não é recompensa observada. Não há simulação de
resultados das ações, experimento em laço fechado, ganho operacional ou atuação.

A campanha SO(3) continua separada: 240 registros e 500 testes históricos,
sem reexecução. Seus 15 hashes de código, os registros, o resumo, o protocolo e
o XML histórico foram preservados. As alterações anteriores no repositório
permaneceram intactas fora das edições específicas deste incremento.

## Reprodução e evidências

Raiz registrada: `E:\Codex\2026-08-27\e\work\NISQ-Predictive-Twin`.
Diretório local: `outputs/routing_shadow_ui_20260924/`.

- `regression-final.xml`: execução aceita dos 531 testes;
- `author-final/report.json`: 12 avaliações, sementes, contagens e hashes;
- `author-final/*.json`: registros completos e snapshots iniciais;
- `author-final/*.png`: quatro capturas dos dois modelos/tamanhos;
- `verification.json`: auditoria de preservação e hashes finais;
- `before.json`, `before/`, `implementation.diff`: linha de base e alterações.

ZIP original `F:\qnet_network.zip`, SHA-256
`6b8563209f54a2d566f1a4229b140b75fda93555085ff2bf6bfcad8f07abd938`.
Leitura pelo verificador existente, sem executar scripts ou distribuir pesos.

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
$env:QT_QPA_PLATFORM = 'offscreen'
.\.audit-winvenv\Scripts\python.exe tools/validate_routing_shadow_ui.py `
  --qnet-zip F:\qnet_network.zip --output outputs/routing-ui-new
.\.audit-winvenv\Scripts\python.exe -m pytest `
  tests/test_console_routing_shadow.py tests/test_optional_routing_shadow.py `
  tests/test_console_allocation_shadow.py tests/test_console_model_catalog.py `
  tests/test_console_input_sources.py tests/test_console_screens.py `
  tests/test_console_routing_session.py tests/test_optional_routing_session.py `
  tests/test_optional_allocation_shadow.py tests/test_optional_compensation_shadow.py `
  tests/test_optional_sync_shadow.py tests/test_control_plane_sandbox.py `
  tests/test_physical_control_gate.py -q --basetemp=outputs/routing-ui-pytest-new `
  --junitxml=outputs/routing-ui-regression-new.xml
```

Escolha destinos novos. Tentativas anteriores foram preservadas: `initial.xml`
contém os primeiros 45 testes aprovados; `targeted-final.xml`, apesar do nome,
registra 47 aprovados e uma falha da fixture que tentou modificar diretamente um
snapshot imutável. A fixture foi corrigida com substituição explícita do objeto.
`author-original/` contém o primeiro diagnóstico, com paridade aprovada e texto
com separadores mal decodificados nas capturas. A codificação foi corrigida e o
diagnóstico completo repetido em `author-final/`. Esses números não são somados
à regressão final nem apresentados como campanhas científicas independentes.

## Próximo incremento

A próxima ponte Q16 será a do ator de taxa QKD, com observáveis decoy-state,
unidades/domínio declarados, semente explícita, limites de parâmetros e referência
de manter parâmetros. Q05 continua pendente; este incremento não autoriza
política de controle ou atuação física.
