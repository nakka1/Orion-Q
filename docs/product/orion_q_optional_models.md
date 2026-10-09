# Modelos opcionais no produto final

Requisito explícito do autor em 22/09/2026: os modelos QKD e Quantum Network
devem estar disponíveis como opções, sem se tornarem dependência obrigatória
para abrir ou usar o ORION-Q. Estes são critérios de aceite do produto,
não uma afirmação de que a ativação nativa já foi entregue.

## Contrato do produto

1. **Desligado por padrão.** Abrir o programa sem pesos instalados mantém as
   funcionalidades e baselines existentes. Não iniciar download, treinamento,
   carregamento de arquivos externos ou atuação ao iniciar o programa.
2. **Seleção por função.** Sincronismo, recomendação de taxa, detecção, compensação,
   roteamento e alocação são funções distintas. Selecionar uma não ativa as outras.
   Ridge/MLP e regimes calibrado/desenvolvimento aparecem como opções distintas.
3. **Instalar não é ativar.** Arquivo localizado, identidade verificada, adaptador
   disponível, compatibilidade de entrada e política ativa são estados diferentes.
   O catálogo não declara pesos instalados simplesmente porque conhece um modelo.
4. **Escolha explícita e reversível.** O usuário pode manter o baseline, selecionar
   um candidato ou desabilitar a opção. Ativação em runtime deverá passar pela
   revisão/registro/readback Q05; previsão nunca equivale a comando autorizado.
5. **Recusa explicável.** Pesos ausentes, incompatíveis, modelo pendente ou domínio
   inadequado não causam substituição silenciosa. Mostrar a razão e preservar a
   política anterior. Warm-up por persistência deve ser identificado como tal.
6. **Comparação disponível.** Preservar baselines e resultados negativos. Não
   recomendar um candidato como superior apenas porque tem ML ou artigo.
7. **Isolamento de estado.** Mudança de modelo, campanha, canal ou fonte inicia
   nova janela. Não combinar histórico entre equipamentos ou sessões.
8. **Distribuição opcional.** O `.exe` base deve funcionar sem os pacotes. Suportar
   importação local revisada e, após confirmar condições de redistribuição,
   pacotes opcionais de pesos. Não incorporar pickle no processo do produto.

## Implementado nesta etapa

`quantum_autopilot.model_intake.catalog` lista 11 opções sem carregar NumPy ou
pesos, todas com adaptador offline (incluindo persistência sem ML). Desde 29/09 o
detector também é admitido, pela exportação segura descrita abaixo; o *pickle* do
pacote continua sem ser desserializado. Todas declaram `default_enabled=false` e
`runtime_available=false`. São estados de desenvolvimento, não status de instalação.

O código-fonte da interface nativa agora oferece **EDGE CONTROL → OPTIONAL MODEL
CATALOG**. A janela é somente leitura: lista essas mesmas 11 opções, diferencia
`OFFLINE ONLY` de `BLOCKED` (hoje nenhuma opção está bloqueada), mostra padrão `OFF` e runtime `NOT INTEGRATED`.
Selecionar uma linha consulta os limites científicos do componente; não carrega
pesos nem aciona o seletor ML MODEL/DETERMINISTIC existente. Instalação aparece
como `NOT CHECKED`, nunca inferida pela existência do adaptador. A janela tem
tabela e detalhes roláveis e não abre automaticamente. Ver [verificação da
interface](../results/orion_q_model_catalog_ui_20260922.md).

**Verificação local explícita:** após selecionar uma linha, `VERIFY LOCAL ZIP`
confere o pacote da família correspondente contra o SHA-256 fixado na revisão,
os limites de tamanho/membros e o CRC dos conteúdos. O resultado informa
`IDENTITY VERIFIED · NOT INSTALLED`, caminho, hash, horário UTC e arquivos pickle
bloqueados. Não extrai o ZIP, importa scripts, desserializa pesos ou constrói
modelos. A leitura ocorre fora da thread gráfica; só uma verificação pode estar
em andamento. Hash divergente, pacote da família errada ou arquivo danificado
produzem recusa explícita, sem alterar os pins nem substituir modelos.

`FORGET CHECK` descarta apenas o resultado da sessão, sem apagar o arquivo.
Trocar a linha, fonte/campanha ou fechar a janela também invalida o resultado;
uma leitura antiga que terminar depois não restaura o estado. Os caminhos não
são persistidos, e reiniciar não dispara nova leitura. Conferir identidade não
verifica compatibilidade com a telemetria atual; qualquer uso futuro precisa
conferir novamente os bytes. Ver [resultado desta etapa](../results/orion_q_model_archive_ui_20260922.md).

**Primeira sessão offline gráfica:** a linha `qkd.sync.ridge.1s.h2s`, após a
verificação do pacote QKD, habilita `OFFLINE SESSION`. O usuário carrega JSON ou
um exemplo de demonstração, revisa e aciona `RUN` explicitamente. São oito
amostras a 1 s, horizonte +2 s, comparadas à persistência. A referência futura
é opcional e nunca entra no preditor; sem ela, não há erro calculado. O pacote
é conferido novamente a cada execução. `EXPORT` salva um registro novo com
hashes, entrada submetida e saídas, sem sobrescrever arquivos.

Editar a entrada invalida o resultado, inclusive se a execução ainda estiver
terminando. Fechar a sessão, esquecer o pacote ou trocar modelo/fonte invalida
o contexto. A execução usa estado novo e não aciona políticas, journal de
controle ou hardware. Somente entradas declaradas `simulated_offline` são
admitidas. Ver [protocolo e testes](../results/orion_q_sync_session_ui_20260922.md).

**Segunda sessão offline gráfica:** `qkd.keyrate.actor` agora também habilita
`OFFLINE SESSION` após conferir o pacote QKD. Seu JSON é distinto: configuração
atual, calibração declarada, observáveis do período anterior (ou bootstrap de
calibração), instantes causais, protocolo decoy-state BB84, vão de 25–80 km e
semente explícita. Cada RUN usa um RNG PCG64 novo e registra a semente e a
versão NumPy. Não reproduz sorteios originais cujo estado RNG não foi fornecido.

O baseline é **manter a configuração atual**, comparado aos parâmetros sugeridos,
não uma taxa de chave contrafactual. A sessão mostra ação amostrada, ação gulosa
sem substituição, probabilidades e prévias limitadas das nove ações no relatório.
Probabilidades da política não são probabilidades de sucesso. Não há previsão
da taxa seguinte, ganho operacional, chave gerada ou comando de instrumento.
Trocar entre ator e sincronizador cria a sessão correta, sem reutilizar JSON,
resultado ou estado da outra função. Ver [verificação](../results/orion_q_keyrate_session_ui_20260922.md).

**Terceira função com sessão gráfica:** as quatro opções de compensação
`qnet.compensation.{calibrado,desenvolvimento}.{ridge,mlp}` habilitam
`OFFLINE SESSION` após conferir o pacote Quantum Network. Cada escolha é
independente. O JSON exige o mesmo checkpoint selecionado, de 1 a 16 amostras
consecutivas a 2 ms, horizonte de 40 ms, 400 fótons de monitoramento por base
declarados, matrizes SO(3) e covariáveis nomeadas com unidades explícitas.
Matrizes inválidas são recusadas, nunca projetadas silenciosamente.

As primeiras 15 saídas são identificadas como `warmup_persistence`, não previsões
aprendidas. Com 16 amostras há uma saída aprendida. Uma rotação futura opcional
permite comparar o erro angular da última saída com a persistência; não entra
na inferência. Sem referência, não há erro calculado. DEMO apenas preenche uma
trajetória artificial; RUN é explícito e começa com estado novo. O regime é
declarado, não certificado automaticamente como dentro do domínio de treinamento.
Não há atuação, previsão de fidelidade de Bell ou alegação de ganho em laço fechado.
Ver [contrato, testes e resultados da demonstração](../results/orion_q_compensation_session_ui_20260922.md).

**Quarta função com sessão gráfica:** `qnet.routing.nominal` e
`qnet.routing.prazo_longo` habilitam sessões distintas após verificar o pacote
Quantum Network. O contrato nomeado exige 11 features de estado e 23 por ação,
1–64 candidatos ordenados, timestamps causais declarados e semente PCG64.
Nominal usa amostragem; prazo longo é guloso, sem sorteio. A estrutura dos
candidatos é validada, mas sua legalidade física não é certificada.

Uma referência opcional identifica outro candidato para comparar decisões.
Não é execução independente de heurística nem comparação de desempenho. Não
há recompensa, sucesso ou fidelidade futuros inventados. As features devem
vir do simulador; não existe ponte automática com telemetria/topologia física.
Ver [contrato e verificação](../results/orion_q_routing_session_ui_20260922.md).

**Quinta função com sessão gráfica (23/09):** `qnet.allocator.persistence` e
`qnet.allocator.ridge` avaliam um snapshot da planta fixa de 11 enlaces,
single-photon BB84. Histórico de até cinco amostras anteriores mais a observação
atual, período de 5 s, fila pendente de 30 s e previsão de até 150 s. Ridge com
histórico insuficiente é identificado como `warmup_persistence`.

A comparação usa a mesma entrada e a mesma fila para o candidato e a persistência.
Escolher persistência não carrega Ridge. Recomendações não são executadas nem
realimentadas; a fila fornecida não é reescrita. Orçamentos assintóticos e cobertura
estimada não são chaves entregues ou certificados de chave finita. Não há remapeamento
para a topologia aberta. Ver [contrato e testes](../results/orion_q_allocation_session_ui_20260923.md).

As onze configurações admitidas offline têm sessões gráficas em fonte.
Isso não conclui Q16 nem habilita o runtime.

**Detector de ataques QKD (29/09).** O pacote traz o detector só como
`detector_boosting.pkl`. Em vez de desserializá-lo, `tools/export_detector_safe.py`
o retreina com o pipeline do próprio autor (conjunto semente 0, `fit_boosted`,
calibração do limiar de decisão) e prova identidade comparando os bytes da
serialização com o membro do ZIP, que nunca é aberto como objeto; só então grava as
árvores como *arrays* numéricos (`model_intake/derived/qkd_detector_boosting_v1.npz`,
SHA-256 fixado em `model_intake/detector.py`, lido com `allow_pickle=False`). A
fábrica devolve `SafeDetector`, que se vincula ao pacote conferindo o SHA-256 do
membro *pickle*. A sessão gráfica (`DetectorSession`) recebe um registro de sessão
simulado com as 17 colunas públicas dos exemplos do autor (1 s por período, 20–70 km,
configuração de transmissor do treino; até 256 KiB) e devolve chamada por período,
alarme com histerese de 3 períodos e o alarme de QBER na calibração do autor como
referência. O DEMO gera uma sessão com a planta do autor. Alarmes são rótulos para um
operador, não provas de segurança nem comandos. Ver
[avaliação](../results/orion_q_detector_evaluation_20260929.md).

O [agente QKD de taxa de chave](orion_q_optional_keyrate.md) já integra essas
opções offline, com entradas tipadas, nove recomendações limitadas e RNG explícito.
As duas opções do [alocador híbrido](orion_q_optional_allocator.md) também estão
admitidas: persistência e Ridge, sem adaptação automática à topologia do produto.

```powershell
python -m quantum_autopilot.model_intake.catalog
```

A fábrica opcional já permite uso por código em avaliação simulada:

```python
from quantum_autopilot.model_intake.catalog import open_optional_model

model = open_optional_model()  # None: desligado, sem acessar arquivos.
model = open_optional_model(
    "qnet.compensation.calibrado.ridge",
    archive_path="F:/qnet_network.zip",  # Caminho escolhido pelo operador.
)
result = model.predict(
    time_s=0.0,
    measured=[[1., 0., 0.], [0., 1., 0.], [0., 0., 1.]],
    covariates=[20., 0., 0.3],  # graus Celsius, K/s, rad RMS
    domain="simulated_offline",
    operating_point="calibrado",
    stream_id="campanha/dispositivo/canal",
)
# Primeiro passo é warmup_persistence, sem acionar nenhum dispositivo.
```

Cada abertura cria estado novo; falha lança erro e não escolhe outro modelo.
`None` significa ausência de modelo opcional, **não** ausência do baseline já
configurado pelo aplicativo. A fábrica não modifica a configuração do ORION-Q.

## Ainda necessário para disponibilidade no produto final

- Pontes para o runtime: os onze componentes foram admitidos offline e avaliados
  nas plantas do autor (laços fechados e, no detector, sessões novas); a ligação
  com a telemetria do produto permanece pendente, separada da ativação Q05.
- Persistir a seleção por projeto com identidade dos artefatos, sem ativação
  implícita após reinício; revisão e confirmação Q05 ao entrar em execução.
- Ligar entradas tipadas do sandbox, com ações legais/supervisor independentes,
  e registrar previsão/recomendação separadamente de efeitos executados.
- Ensaiar desligamento, erro de carga e troca de fonte/modelo durante operação.
- Concluir inspeção em máquina independente. O [build Windows de 23/09](../results/orion_q_optional_models_windows_20260923.md)
  já passou nos testes locais com e sem os pacotes externos; pesos não foram embutidos.

O catálogo/fábrica, replay, consulta gráfica, verificação local e sessão offline
do sincronizador, do ator QKD, dos quatro compensadores, dos dois roteadores e das
duas opções de alocação são código-fonte funcional. Ativação de política, runtime
de controle dos novos modelos não foram entregues. A primeira
[ponte shadow do sincronizador](orion_q_sync_shadow_bridge.md) está agora em fonte,
com janela tipada explícita e sem atuação. A sessão contínua da fonte artificial
também está ligada ao provider/GUI, com START explícito, invalidação e exportação;
fontes físicas e pontes dos demais modelos seguem pendentes. O build Windows de 23/09
inclui o catálogo e as dez sessões; executáveis anteriores permanecem inalterados.
