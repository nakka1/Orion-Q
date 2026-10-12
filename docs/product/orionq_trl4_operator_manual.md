# OrionQ — manual do protótipo SOFT-ALPHA

Uso previsto: inspeção, simulação declarada, replay e ensaios locais de software
no Windows. Não autoriza operação de bancada física, controle de satélite ou
uso operacional de chaves emuladas. A avaliação vale para as fontes e o ambiente
registrados no manifesto, não para qualquer instalação ou executável anterior.

## Ambiente e abertura

O ambiente da campanha usa Python 3.12, Qt/PySide 6, dependências do projeto e
extras `console,lab,satellite,dev` efetivamente disponíveis. Registrar as versões
reais; intervalos declarados em `pyproject.toml` não qualificam todas as versões.
SDKs, licenças, instrumentos e pacotes ML externos não são presumidos presentes.

Para abrir as fontes com estado separado, a partir da raiz:

```powershell
$env:PYTHONPATH = 'src'
& .audit-winvenv\Scripts\python.exe -m quantum_autopilot.desktop --ui native --state-root outputs/trl4_manual_workspace_new
```

Escolher um diretório novo para o ensaio. A abertura normal usa exclusão de
instância: se já houver OrionQ aberto, ele será focalizado e o novo comando não
constitui uma segunda execução independente. As ferramentas de qualificação
usam processos diagnósticos próprios e estados novos; não encerram a instância
do operador. Não usar `--skip-auth` como prova de autenticação.

Na entrada, usar a opção de sessão Windows e aguardar confirmação do backend.
Selecionar demonstração identifica a fonte; não concede identidade nem acesso
físico. Usuários e serviços institucionais dependem de provisionamento real,
com permissões por função/recurso. A campanha não instala um provedor OIDC.

## Jornadas e resultados esperados

| Jornada | Procedimento | Resultado esperado e limite |
| --- | --- | --- |
| Inspecionar rede | Abrir Topology, selecionar instalação ou satélite e aguardar publicação recebida | Origem, estado, causas e métricas correspondem ao backend; `No sample` não é substituído por valor inventado |
| Mapa/Globo | Alternar pelo botão/zoom; selecionar satélite e voltar ao mapa | Seleção e informações preservadas; somente renderizador ativo; órbita distinta de transmissão |
| Cadastrar comunicador | Escolher tipo físico, papéis e capacidades; preencher campos obrigatórios e salvar | Campos orbitais exclusivos de satélite; validação antes de persistência; revisão/readback confirmados |
| Configurar enlace | Identificar canais clássico e quântico e sua associação; configurar parâmetros compatíveis | Incompatibilidade, falta de canal ou aquisição inválida impedem a operação |
| Consultar FPGA | Abrir Edge/FPGA, selecionar dispositivo emulado e observar observações/feedback | Eventos passam pelo backend; disponibilidade de hardware físico não é deduzida da emulação |
| ML automático | Solicitar alternância, revisar confirmação e cancelar ou confirmar | Cancelar não envia alteração; confirmação autorizada muda o backend; desligamento restaura modo manual |
| Chaves e bancada | Abrir Key management; consultar contratos, executar somente emulação autorizada | Origem do material identificada; métricas condicionais; `certified_secret_bits=0` |
| Exportar evidência | Usar ação de exportação, escolher destino novo e conferir retorno | Arquivo corresponde à evidência autorizada; não sobrescreve silenciosamente nem inclui segredos |
| Interromper/reiniciar | Encerrar apenas o diagnóstico próprio, reiniciar sobre o estado de teste | Resultados confirmados continuam presentes; operações interrompidas/incertas permanecem explícitas |

O detalhamento de conexão, parâmetros científicos e origem da fonte está nas
[especificações do elemento](orionq_trl4_design.md). Uma instalação institucional
no mapa não comprova conexão real com OrionQ; linhas representam configurações
ou eventos conforme a legenda, não uma transmissão física presumida.

## Erros e recuperação

Negação de permissão exige uma identidade/atribuição autorizada, não bypass.
Configuração obsoleta exige recarregar a revisão antes de salvar. Estado incerto
de instrumento exige readback e condições de calibração/sincronismo válidas
antes de nova aquisição. Falta de observação exige investigar fonte/canais;
não inserir métricas finais para tornar o node disponível.

Em entrega de chave incerta, consultar reserva/recibo antes de repetir. Material
consumido ou potencialmente entregue não deve retornar ao pool. Não restaurar
apenas o arquivo principal de SQLite com WAL ativo; os testes locais não
substituem um procedimento operacional autorizado de backup de todo o workspace.

## Desempenho e suporte

O protótipo ainda apresenta latência de detalhes e passos globais demorados.
O gate G05 de TRL 5 foi reprovado e os demais não foram demonstrados; a
qualificação TRL 5 não foi atendida. Este manual não promete 10 ms por
ciclo, conteúdo completo em 1 s, disponibilidade contínua ou estabilidade
prolongada já demonstrada. Registrar falhas com identidade de fonte/configuração,
passos para reprodução e evidência sem segredos. Não habilitar atuação física
para investigar lentidão ou ausência de amostras.
