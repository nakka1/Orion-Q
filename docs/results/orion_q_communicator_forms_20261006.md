# ORION-Q 5.43 — Comunicadores: correção e validação

Data da revisão: 2026-10-06. Resultado automático local: aprovado.

A mudança Satélite → Instalação deixava a tecnologia satélite/Doppler selecionada automaticamente. Selecionar tecnologias também não preenchia os protocolos obrigatórios. Nova conexão reutilizava a sessão anterior, e a confirmação de salvamento podia aceitar um snapshot antigo. Os problemas foram reproduzidos antes das alterações e corrigidos no formulário e na admissão de cadastros.

Órbita é exclusiva do tipo físico Satélite. Uma estação terrestre óptica ou instalação com capacidade satélite–solo continua precisando de apontamento, perdas e Doppler, sem dados orbitais próprios. Tecnologias escolhidas explicitamente pelo operador permanecem; dependências automáticas acompanham o tipo.

## Casos de uso

| Caso | Comportamento verificado | Evidência | Resultado |
|---|---|---|---|
| Satélite → seis tipos terrestres/serviços | Remove tecnologia automática anterior; Órbita oculta e orbit vazio. Ground Station conserva parâmetros de enlace satélite–solo. | Código + EXE | Aprovado |
| Cadastro dos sete tipos | Salvamento, edição, recarga e persistência no backend real isolado; cadastro não cria linhas nem resultados quânticos. | Código + EXE | Aprovado |
| Tecnologias opcionais | Checkbox associa protocolo/capacidades; conserva extras e função lógica explicitamente escolhidos pelo operador. | Código + EXE | Aprovado |
| TF-QKD: emissão e medição central | Capacidades mínimas específicas de cada papel; estação de medição sugere central_measurement. | Código + EXE | Aprovado |
| Modelos orbitais | Circular/Kepler/TLE/vetores apresentam somente campos aplicáveis; rascunho inválido inativo não bloqueia outro modelo. | Código + EXE | Aprovado |
| Coordenadas | Latitude e longitude precisam ser fornecidas juntas; ausência não é convertida em coordenada zero. Ativação mantém exigências geográficas. | Código + EXE | Aprovado |
| Valores e limites | Vírgula decimal aceita; campos opcionais em branco omitidos; erro de conversão identifica o campo; intervalos validados no backend. | Código + EXE | Aprovado |
| Fonte de dados | Arquivo, endereço e referência segura aparecem conforme o modo; OGS somente para tecnologia compatível; seleção incompatível não vira simulada silenciosamente. | Código + EXE | Aprovado |
| Rascunhos de integração | Interfaces/endereço/credenciais separados por modo; preservados na reconstrução do tema. | Código | Aprovado |
| Identificador duplicado em Novo | Recusa antes de emitir comando; não sobrescreve registro existente nem exibe confirmação falsa. | Código + EXE | Aprovado |
| Salvamento pendente | Snapshot antigo não confirma; botão permanece bloqueado; revisão e conteúdo persistido devem corresponder ao comando. | Código + EXE | Aprovado |
| Edição durante salvamento | Confirma registro durável e mantém alterações posteriores no rascunho; conflito de revisão pede recarga. | Código | Aprovado |
| Reconstrução do tema | Conserva rascunho, estado pendente, seleções e integração; nenhuma aba oculta reaparece. | Código + EXE | Aprovado |
| Nova conexão | Limpa sessão, percurso, tecnologias, parâmetros e tabela de ambos os canais anteriores. | Código + EXE | Aprovado |
| Confirmação de conexão | Mudança não relacionada na topologia não confirma a edição da sessão. | Código | Aprovado |
| Cadastro malformado | Location/configuration/orbit nulos ou listas recusados antes da persistência; editor não recebe DTO quebrado. | Código | Aprovado |
| GEO e ativação | Canais pareados, pipeline causal, estados orbitais, rótulos e 60 trocas Mapa/Globo mantidos. | EXE Windows + aceitação | Aprovado |
| Layout e execução | Formulários com scroll e renderização nativa; 20 rodadas de stress; seis inicializações em escalas DPI/modos diferentes. | EXE Windows | Aprovado |

## Verificações e rastreabilidade

188 testes únicos passaram, sem falhas ou skips no resultado mais recente. O ensaio inicial encontrou uma falha adicional: a aplicação geral das permissões reabilitava Salvar enquanto a operação permanecia pendente. O tratamento local foi corrigido e a regressão passou. O XML inicial é preservado; repetições não aumentam a contagem.

O diagnóstico de código aprovou 20 checks de Comunicadores e oito dos formulários. O executável aprovou 38 checks gerais, 20 de Comunicadores, oito de formulários, 11 da GEO e 21 do pipeline OGS. O self-test principal usou QPA Windows, com renderização nativa. A aceitação do pacote repetiu os diagnósticos offscreen e stress; o relatório automático aprovou. Gates de roadmap (quatro checks), campanha temporal (25), modelos opcionais sem pacotes, stress (20 rodadas/140 ciclos) e inicialização (seis casos) completaram.

Foram solicitadas larguras 640, 1280 e 3440 pixels no ensaio dos formulários. O mínimo do widget isolado preserva a área útil e usa scroll; as dimensões efetivamente renderizadas estão registradas em delivery-summary.json: {"memory-form-1280x900.png": [1280, 900], "memory-form-3440x1440.png": [3440, 1421], "memory-form-640x480.png": [900, 832], "terrestrial-form-no-orbit.png": [1280, 900]}. Capturas foram inspecionadas; a avaliação não é uma medição universal de desempenho de GPU.

Evidências: `outputs/communicators_form_fix_20261006_v1/reproduction.json`, `before-satellite-fields-on-installation.png`, XMLs e `delivery-summary.json`. Diagnóstico nativo: `release/windows/5.43.0-native-20261006-224615/communicators-geo/forms/report.json`; capturas na mesma pasta. Diagnóstico geral: `release/windows/5.43.0-native-20261006-224615/self-test.json`. Aceitação: `release/windows/5.43.0-native-20261006-224615/native-acceptance/acceptance-report.json`.

Preservação: 1112 arquivos registrados antes desta revisão, 1105 idênticos e sete existentes alterados no escopo do cadastro, diagnóstico, build e documentação. Nenhum removido. EXE e ZIP 5.42 anteriores continuam com hashes iguais. Física e cálculos de protocolo não foram alterados. Modelos seguem opcionais e a atuação física bloqueada.

## Distribuição e execução

- EXE: `release/windows/5.43.0-native-20261006-224615/ORION-Q/ORION-Q.exe`.
- ZIP: `release/windows/5.43.0-native-20261006-224615/ORION-Q-5.43.0-native-windows-x64.zip`.
- SHA-256 EXE: `37850d59949e831a62e12609582c2b1145446de404d8417c63ecd8048d47d457`.
- SHA-256 ZIP: `7a7f90b882cde50d88221acb87378f37661574dc894cfb2b90b1b79894da720e`.
- ZIP: 294759407 bytes, 3928 entradas, CRC verificado e manual 5.43 incluído.

Extraia todo o ZIP e mantenha `_internal` junto ao EXE. Em Comunicadores, use Novo, escolha o tipo, preencha os dados aplicáveis e clique Salvar cadastro; a confirmação depende da leitura do registro persistido. Nova conexão inicia vazia e exige seu percurso e os dois canais.

Para repetir o diagnóstico, use um destino novo:

```powershell
ORION-Q.exe --self-test C:\temp\orion-comunicadores-novo\report.json
```

## Limites da validação

Os ensaios usaram interações Qt programáticas e o backend isolado nesta máquina Windows. A aceitação mantém `manual_review_required`; máquina independente e operação com mouse físico não foram qualificadas. Não houve comunicação com hardware nem atuação física, e esta correção não remove as limitações de integração real de missão documentadas em OGS v2.

Documentação operacional: [Comunicadores e GEO](../product/orion_q_communicators_geo.md), [OGS v2](../product/orion_q_satellite_ogs_v2.md).
