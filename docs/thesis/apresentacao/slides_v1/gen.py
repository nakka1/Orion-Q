import json, datetime
from pathlib import Path
ROOT = Path(__file__).parent / "project"
INK, PAPER, ALT, SOFT = "#14213D", "#FAF8F3", "#EEF2F6", "#3D4A5C"
ORANGE, BLUE, LINE = "#B04E22", "#2B6A94", "#D9DEE5"
ON_DARK, ON_DARK_SOFT, ORANGE_LIGHT = "#F3EFE6", "#B9C6D6", "#F0A673"
HEAD = "font-family:'Source Serif 4', Georgia, serif"
BODY = "font-family:'IBM Plex Sans', Arial, sans-serif"
TOTAL = 16

def section(sid, n, inner, notes, bg=PAPER, color=INK, extra="", footer=True, transition="fade"):
    style = (f"background:{bg};color:{color};{BODY};padding:128px 128px 160px;display:flex;"
             f"flex-direction:column;gap:32px{extra}")
    foot = ""
    if footer:
        fc = ON_DARK_SOFT if bg == INK else SOFT
        foot = (f'<p style="position:absolute;left:128px;bottom:64px;width:1200px;font-size:24px;color:{fc}">'
                f'ORION-Q · controladores aprendidos em redes quânticas · pré-banca</p>'
                f'<p style="position:absolute;right:128px;bottom:64px;width:200px;font-size:24px;color:{fc};text-align:right">{n} / {TOTAL}</p>')
    return (f'<section id="{sid}" data-transition="{transition}" style="{style}">\n{inner}\n{foot}\n'
            f'<aside>{notes}</aside>\n</section>\n')

def eyebrow(text, color=ORANGE):
    return f'<p style="font-size:28px;font-weight:600;letter-spacing:2px;text-transform:uppercase;color:{color}">{text}</p>'

def title(text, color=INK, size=64):
    return f'<h2 style="{HEAD};font-size:{size}px;font-weight:600;line-height:1.15;color:{color}">{text}</h2>'

def card(head, body, bg="#FFFFFF", accent=BLUE, width=None):
    w = f"width:{width}px;" if width else "flex:1;"
    return (f'<div style="{w}display:flex;flex-direction:column;gap:16px;background:{bg};padding:36px;'
            f'border:1px solid {LINE};border-top:6px solid {accent};border-radius:12px">'
            f'<h3 style="font-size:36px;font-weight:600;line-height:1.2;color:{INK}">{head}</h3>'
            f'<p style="font-size:28px;line-height:1.4;color:{SOFT}">{body}</p></div>')

def big(number, label, color=ORANGE):
    return (f'<div style="flex:1;display:flex;flex-direction:column;gap:12px">'
            f'<p style="{HEAD};font-size:120px;font-weight:600;line-height:1.0;color:{color}">{number}</p>'
            f'<p style="font-size:30px;line-height:1.35;color:{SOFT}">{label}</p></div>')

slides = {}

slides["capa"] = section("capa", 1, f'''<div style="flex:1"></div>
{eyebrow("Pré-banca · TCC · 22/10/2026", ORANGE_LIGHT)}
<h1 style="{HEAD};font-size:88px;font-weight:600;line-height:1.1;color:{ON_DARK}">Integração de Aprendizado de Máquina em Redes Quânticas</h1>
<p style="font-size:40px;line-height:1.3;color:{ON_DARK_SOFT}">Controladores aprendidos e uma plataforma de controle auditável (ORION-Q)</p>
<div style="flex:1"></div>
<p style="font-size:28px;line-height:1.5;color:{ON_DARK}">Alexandre O. S. Santos · João M. S. Souza · Otto Menegasso Pires · Leonardo S. Almeida · Gilson Amorim Carvalho</p>
<p style="font-size:24px;color:{ON_DARK_SOFT}">Unijorge · QuIIN e LAQCC — Senai CIMATEC</p>''',
 "Apresentar o tema em uma frase: controladores aprendidos para decisões de uma rede quântica, medidos contra alternativas simples, e uma plataforma que só deixa um modelo agir depois de verificado.",
 bg=INK, color=ON_DARK, footer=False)

slides["pergunta"] = section("pergunta", 2, f'''{eyebrow("O problema")}
{title("Desempenho offline não basta")}
<div style="display:flex;gap:48px;flex:1">
<div style="flex:3;display:flex;flex-direction:column;gap:24px;justify-content:center;border-left:8px solid {BLUE};padding:8px 0 8px 40px">
<p style="{HEAD};font-size:40px;font-style:italic;line-height:1.4;color:{INK}">Que ganho controladores aprendidos oferecem sobre alternativas simples nas decisões de controle de uma rede quântica, e como integrá-los a um plano de controle de forma que cada modelo seja opcional, verificável e comparado a baselines antes de influenciar decisões?</p>
</div>
<div style="flex:2;display:flex;flex-direction:column;gap:24px;justify-content:center">
{card("1. Controladores", "Compensação de polarização, alocação híbrida, roteamento, taxa de chave, sincronismo e detecção de ataques, cada um contra a melhor regra simples.", accent=ORANGE)}
{card("2. Plataforma", "ORION-Q: nenhum modelo age sem contrato, supervisor, evidência e ativação auditada.")}
</div></div>''',
 "Ler a pergunta. Os dois problemas saíram do estudo preliminar: a hipótese WDM não se sustentou e ensinou que o modelo precisa ser julgado no seu domínio e contra baselines.")

slides["licao"] = section("licao", 3, f'''<div style="flex:1"></div>
{eyebrow("A lição que organiza o trabalho", ORANGE_LIGHT)}
<h2 style="{HEAD};font-size:80px;font-weight:600;line-height:1.15;color:{ON_DARK}">Menor erro de previsão não garante melhores decisões.</h2>
<p style="font-size:36px;line-height:1.45;color:{ON_DARK_SOFT}">No estudo preliminar, o modelo de menor erro teve a pior utilidade. Nos painéis shadow, o MLP perdeu em todas as sementes com um produtor artificial e voltou a empatar com o Ridge na planta em que foi treinado.</p>
<div style="flex:1"></div>''',
 "Os dois exemplos: WDM (0 de 3 comparações neurais significativas contra persistência) e o MLP do compensador. Daí a regra: julgar o modelo no domínio de treino e contra baselines ressintonizados.",
 bg=INK, color=ON_DARK)

rules = [("Planta do autor", "Cada modelo roda no código em que foi treinado, incorporado ao repositório."),
         ("Só observáveis", "O controlador vê o que um equipamento real veria, nunca o estado oculto."),
         ("Supervisor", "Recusa saídas inválidas e passa ao controlador de reserva."),
         ("Portões", "Antes de pontuar, a réplica regenera os resultados publicados do autor."),
         ("Protocolo congelado", "Arquivo com hash, sementes novas, teste pareado exato e Holm.")]
slides["metodo"] = section("metodo", 4, f'''{eyebrow("Método")}
{title("Cinco regras para todo laço fechado")}
<div style="display:grid;grid-template-columns:repeat(5, 1fr);gap:24px;flex:1;align-items:stretch">
{''.join(card(h, b, accent=(ORANGE if i == 3 else BLUE), width=None) for i, (h, b) in enumerate(rules))}
</div>
<p style="font-size:30px;line-height:1.4;color:{SOFT}">Comparação sempre contra a melhor alternativa simples, ressintonizada nas mesmas condições: filtro alfa-beta, comissionamento, quântico-primeiro, heurística gulosa, alarme de QBER.</p>''',
 "Destacar os portões: nenhuma campanha pontua em sementes novas sem antes reproduzir o autor. Todas passaram, com erro zero ou na precisão de máquina.",
 bg=ALT)

slides["compensador"] = section("compensador", 5, f'''{eyebrow("Resultados · compensação de polarização")}
{title("O compensador vence, com condições")}
<div style="display:flex;gap:48px">
{big("3,3×", "abaixo do alfa-beta ressintonizado no ponto calibrado, 20 de 20 sementes novas")}
{big("4,8×", "no ponto de desenvolvimento, 20 de 20 sementes", BLUE)}
{big("16 bits", "em ponto fixo ficam a até 3,8% do ponto flutuante; 12 bits colapsam", INK)}
</div>
<div style="display:flex;gap:24px">
{card("Atuador real importa", "Com o atuador limitado do autor, só vence acima de 120 rad/s (desenvolvimento) e 250 rad/s (calibrado); abaixo, o alfa-beta ganha.", accent=ORANGE)}
{card("Não se transfere", "Os pesos de um ponto de operação são recusados no outro pelo contrato.")}
</div>''',
 "Ridge 0,0131 contra 0,0436 no calibrado (Holm < 1e-5). O ganho exige controlador de polarização piezoelétrico rápido ou de niobato de lítio. O ponto fixo de 16 bits com CORDIC de 8 estágios é a configuração do RTL do autor.")

slides["qkd"] = section("qkd", 6, f'''{eyebrow("Resultados · enlace QKD")}
{title("Enlace QKD: taxa de chave e sincronismo")}
<table style="font-size:30px;color:{INK};width:1664px">
<tr style="background:{ALT}"><th style="width:26%">Controlador</th><th style="width:30%">Comparado com</th><th style="width:44%">Resultado em sementes novas</th></tr>
<tr><td>Ator de taxa de chave</td><td>comissionamento</td><td>+13,6% a +20,8% de 25 a 65 km</td></tr>
<tr><td>Ator de taxa de chave</td><td>comissionamento, 80 km</td><td><span style="color:{ORANGE}">não significativo (+8%, Holm 0,10)</span></td></tr>
<tr><td>Sincronizador Ridge</td><td>alfa-beta</td><td>melhor em 160 de 160 execuções, margem até 5,1%</td></tr>
</table>
<p style="font-size:30px;line-height:1.4;color:{SOFT}">Portões: episódio exportado bit a bit (ações, parâmetros, bits de chave) e as tabelas do manuscrito regeneradas (15 e 24 entradas).</p>''',
 "O ganho a 80 km do manuscrito não se confirma: é um resultado refinado, e está registrado como não suportado no registro verificável.")

slides["rede"] = section("rede", 7, f'''{eyebrow("Resultados · planta metropolitana e repetidores")}
{title("Alocação ganha; roteamento não supera a heurística")}
<div style="display:flex;gap:48px">
{big("2,4–2,6×", "o tráfego clássico de silenciar o canal onde há chave devida, com a mesma chave (±0,5 ponto), 20 de 20 sementes")}
<div style="flex:1;display:flex;flex-direction:column;gap:24px">
{card("Gêmeo analítico", "Prazo quádruplo: empate (45,1% contra 45,0%). Nominal: abaixo (20,4% contra 23,4%; Holm 0,010).")}
{card("SeQUeNCe no laço", "O gêmeo prevê o SeQUeNCe; lá o agente fica abaixo nos dois prazos (19,0% contra 22,9%; 45,4% contra 46,7%).", accent=ORANGE)}
</div></div>''',
 "Alocador com chave finita, como a exportação declara. O ganho vem da lei de controle: a previsão Ridge não muda o resultado. Roteamento: resultado negativo, delimita onde o aprendizado ajuda. SeQUeNCe: portões regeneram as 480 linhas do autor com erro zero; nenhuma das 8 taxas muda entre gêmeo e SeQUeNCe (≤ 1,7 ponto); no prazo longo, 892 de 1.000 trajetórias idênticas, e nas outras a heurística entrega 13 vezes, o agente nenhuma.")

slides["detector"] = section("detector", 8, f'''{eyebrow("Resultados · detecção de ataques")}
{title("O detector vê o que o alarme de QBER não vê")}
<div style="display:flex;gap:48px">
{big("84–89%", "dos períodos de ataque marcados pelo detector, em sessões de fluxos aleatórios novos")}
{big("7–14%", "pelo alarme de QBER na calibração do autor", BLUE)}
{big("3–4", "alarmes falsos por hora, F1 macro 0,86–0,89", INK)}
</div>
<div style="display:flex;gap:24px">
{card("Sem desserializar o pickle", "Retreinado com o pipeline do autor e provado idêntico byte a byte; roda de arrays numéricos.")}
{card("Classe fraca", "Intercept-and-resend: 45–68%, porque eleva a QBER, justamente o que o alarme vê.", accent=ORANGE)}
{card("Em tempo real", "A taxa de temperatura do autor usa o período seguinte; sem ela, perde 0,2 ponto.")}
</div>''',
 "110 de 118 e 111 de 119 episódios, Holm 5e-24. Portões: 7 sessões exportadas byte a byte e 240 métricas do autor com erro zero. As sementes 1 e 2 repetiriam episódios de treino; a avaliação usa fluxos 1000 e 2000. Tempo real: variáveis recalculadas só com o passado, 83,5% e 88,9% contra 83,7% e 89,1%; diferença detectável (Holm 0,024 e 0,001), F1 muda 0,001.")

verdicts = [("Compensador", "3,3× e 4,8× abaixo do alfa-beta", "confirmado, com atuador ≥ 120–250 rad/s"),
            ("Ponto fixo", "16 bits bastam nos dois pontos", "confirmado e estendido"),
            ("Blocagem", "10,9× e 7,9× o melhor temporizador", "confirmado nos dois pontos"),
            ("Ator de taxa", "+13,6% a +20,8% até 65 km", "refinado: 80 km não significativo"),
            ("Sincronizador", "160 de 160 execuções", "confirmado, margem pequena"),
            ("Alocador", "2,4–2,6× o tráfego clássico", "confirmado"),
            ("Roteamento", "não supera a heurística gulosa", "refinado: empate só no prazo longo do gêmeo"),
            ("Detector", "84–89% contra 7–14%", "confirmado; classe fraca")]
rows = "".join(f'<tr><td>{a}</td><td>{b}</td><td><span style="color:{ORANGE if "refinado" in c else BLUE}">{c}</span></td></tr>' for a, b, c in verdicts)
slides["sintese"] = section("sintese", 9, f'''{eyebrow("Síntese")}
{title("O que se confirmou em sementes novas")}
<table style="font-size:28px;color:{INK};width:1664px">
<tr style="background:{ALT}"><th style="width:22%">Controlador</th><th style="width:40%">Resultado</th><th style="width:38%">Em relação ao autor</th></tr>
{rows}
</table>''',
 "Todos os resultados vêm das plantas do autor, depois de reproduzir o que ele publicou. Dois foram refinados, não confirmados; ficam registrados como tais.", bg=ALT)

arch = f'''<div style="display:flex;gap:16px;align-items:stretch">
{card("Catálogo", "OFF; SHA-256; sem pickle.", width=340)}
<x-shape kind="arrow-right" style="background:{BLUE};width:48px;height:28px;align-self:center"></x-shape>
{card("Contrato", "Só na planta declarada.", width=340)}
<x-shape kind="arrow-right" style="background:{BLUE};width:48px;height:28px;align-self:center"></x-shape>
{card("Supervisor", "Recusa inválidos; reserva quente.", width=340, accent=ORANGE)}
<x-shape kind="arrow-right" style="background:{BLUE};width:48px;height:28px;align-self:center"></x-shape>
{card("Planta", "A do autor; sem ação física.", width=340)}
</div>'''
slides["plataforma"] = section("plataforma", 10, f'''{eyebrow("Plataforma")}
{title("Um modelo só age depois de verificado")}
{arch}
<div style="display:flex;gap:24px">
{card("Três modos", "Sessão offline, shadow alimentado pelas plantas do autor e laço fechado.")}
{card("Ativação Q05", "Preparar, revisar, aprovar, ativar, ler de volta e reverter, com diário.")}
{card("Seis componentes", "Onze configurações admitidas; o detector, por exportação segura.")}
</div>''',
 "Recusa real na demonstração: o checkpoint de desenvolvimento é recusado na planta calibrada antes de a evidência ser lida. A aprovação declara um operador, não o autentica.")

gates = [("Compensador", "QBER retida declarada; varredura de atuador (36 casos)", "≤ 6×10⁻¹⁷"),
         ("Ponto fixo", "varredura de largura de palavra (20 linhas)", "3×10⁻¹⁶"),
         ("Blocagem", "432 linhas de blocagem; 24 de estresse", "6×10⁻¹⁶"),
         ("Ator e sincronizador", "episódio exportado; tabelas do manuscrito", "0 / precisão publicada"),
         ("Alocador", "4 episódios e ponto da fronteira com chave finita", "0"),
         ("Roteamento", "2.091 decisões; 2 tabelas; 480 linhas no SeQUeNCe", "0"),
         ("Detector", "7 sessões; 240 métricas por classe", "0")]
grow = "".join(f"<tr><td>{a}</td><td>{b}</td><td>{c}</td></tr>" for a, b, c in gates)
slides["portoes"] = section("portoes", 11, f'''{eyebrow("Reprodução")}
{title("Primeiro, reproduzir o autor")}
<table style="font-size:28px;color:{INK};width:1664px">
<tr style="background:{ALT}"><th style="width:24%">Componente</th><th style="width:52%">Resultado do autor regenerado</th><th style="width:24%">Divergência</th></tr>
{grow}
</table>
<p style="font-size:28px;line-height:1.4;color:{SOFT}">Nenhum portão falhou. Isso mostra que a integração preserva o modelo, não que a planta representa uma rede real.</p>''',
 "Detalhes que viraram achados: o exemplo do ator usa um gerador separado; o treino do sincronizador não fixa semente; a tolerância do ponto fixo teve de ser relativa (colunas em Hz).")

slides["rtl"] = section("rtl", 12, f'''{eyebrow("Hardware")}
{title("Verilog do autor em simulador")}
<div style="display:flex;gap:48px">
{big("0", "palavras divergentes em todo o RTL, no domínio de uso (Icarus Verilog contra a referência do autor)", BLUE)}
<div style="flex:2;display:flex;flex-direction:column;gap:20px">
{card("Conferido", "MAC Ridge e MLP (24.768 palavras), CORDIC (6.006 casos), sincronizador (512), árvores do detector (1.088 linhas).")}
{card("Dois defeitos fora do caminho usado", "O testbench do MLP gerado pelo autor não pode passar; o CORDIC erra no 3º quadrante, que o logaritmo nunca usa.", accent=ORANGE)}
</div></div>
<p style="font-size:28px;color:{SOFT}">Síntese Yosys (Xilinx 7, sem posicionamento): detector em 2.178 LUT e 6 BRAM; cada MAC do compensador em 1 DSP.</p>''',
 "O README do autor dizia que o testbench nunca tinha sido executado. A ROM de 12 bits do sincronizador veio de outro treino (sem semente) e difere do modelo exportado em até 8 LSB.")

slides["verificacao"] = section("verificacao", 13, f'''{eyebrow("Verificação")}
{title("Afirmações que a máquina confere")}
<div style="display:flex;gap:48px">
{big("3.724", "testes automatizados; 3.721 passam em Linux e 3 são próprios do Windows", INK)}
{big("795", "checagens de 20 afirmações da plataforma sobre os resumos das campanhas, inclusive as negativas")}
{big("12/12", "campanhas repetidas no Windows com as mesmas conclusões da nuvem; 11 com os mesmos números (10⁻⁹)", BLUE)}
</div>''',
 "Registro verificável: docs/thesis/orion_q_platform_claim_registry.json. O ator a 80 km está registrado como não suportado. O comando: tools/run_official_campaigns.ps1. Windows 11, ZIPs originais: a única diferença numérica é uma célula do atuador limitado (1,6e-5); detector e taxa de chave tiveram portões tornados portáveis (último bit da libm).", bg=ALT)

steps = [("Estado inicial", "tudo OFF; filtro alfa-beta: QBER 0,0448, chave 49,1 kbit/s"),
         ("Recusa", "checkpoint de desenvolvimento recusado na planta calibrada"),
         ("Ativação Q05", "preparar, revisar, aprovar, ativar; leitura ACTIVE"),
         ("Modelo ativo", "QBER 0,0123, chave 80,9 kbit/s"),
         ("Falha injetada", "8 saídas inválidas; o supervisor passa ao filtro em t = 5,014 s"),
         ("Reversão", "mesmo fluxo; volta a QBER 0,0448")]
srows = "".join(f'<tr><td>{i}. {a}</td><td>{b}</td></tr>' for i, (a, b) in enumerate(steps, 1))
slides["demo"] = section("demo", 14, f'''{eyebrow("Demonstração · 6 minutos, a partir do executável")}
{title("Um modelo entrando e saindo de controle sob governança")}
<table style="font-size:30px;color:{INK};width:1664px">
<tr style="background:{ALT}"><th style="width:30%">Passo</th><th style="width:70%">O que se vê</th></tr>
{srows}
</table>''',
 "Plano B em ordem: vídeo gravado até 15/10; demonstração por linha de comando (tools/polarisation_control_demo.py); recibos exportados. Valores da execução na nuvem, semente 2026.")

limits = ["Tudo é simulado: validar na planta de treino mostra que a integração preserva o modelo, não que a planta representa uma rede real.",
          "O atuador limitado é o modelo do autor, não um dispositivo caracterizado; os pesos foram treinados com atuador ideal.",
          "O detector não tem dados de campo rotulados; um ataque por episódio.",
          "Roteamento em uma topologia; o agente foi treinado no gêmeo, não no SeQUeNCe.",
          "A aprovação declara um operador, não o autentica; o RTL não foi medido em placa."]
lis = "".join(f'<li style="font-size:30px;line-height:1.45;color:{SOFT}">{t}</li>' for t in limits)
slides["limites"] = section("limites", 15, f'''{eyebrow("Limitações")}
{title("O que estes resultados não mostram")}
<ul style="display:flex;flex-direction:column;gap:20px">{lis}</ul>''',
 "Todas estão na Seção 12 do texto. Hipótese WDM medida continua sem teste por falta de dados pareados.")

slides["conclusao"] = section("conclusao", 16, f'''{eyebrow("Conclusão e próximos passos", ORANGE_LIGHT)}
<h2 style="{HEAD};font-size:64px;font-weight:600;line-height:1.2;color:{ON_DARK}">Três controladores entregam vantagem clara; dois resultados negativos delimitam onde o aprendizado ajuda.</h2>
<div style="display:flex;gap:24px">
<div style="flex:1;display:flex;flex-direction:column;gap:12px;border-top:4px solid {ORANGE_LIGHT};padding-top:24px">
<h3 style="font-size:36px;font-weight:600;color:{ON_DARK}">Até a banca final</h3>
<p style="font-size:28px;line-height:1.45;color:{ON_DARK_SOFT}">Agente treinado no SeQUeNCe; compensador retreinado com o atuador limitado; ativação auditada nos demais alvos.</p></div>
<div style="flex:1;display:flex;flex-direction:column;gap:12px;border-top:4px solid {ORANGE_LIGHT};padding-top:24px">
<h3 style="font-size:36px;font-weight:600;color:{ON_DARK}">Depois</h3>
<p style="font-size:28px;line-height:1.45;color:{ON_DARK_SOFT}">RTL em placa; dados de campo para o detector; um conjunto que una telemetria WDM e alvo quântico medidos.</p></div>
</div>''',
 "Fechar com a lição: um modelo só pode ser julgado no domínio em que foi treinado e contra baselines simples; a plataforma torna isso obrigatório.",
 bg=INK, color=ON_DARK)

order = ["capa", "pergunta", "licao", "metodo", "compensador", "qkd", "rede", "detector", "sintese",
         "plataforma", "portoes", "rtl", "verificacao", "demo", "limites", "conclusao"]
assert set(order) == set(slides) and len(order) == TOTAL
for sid in order:
    (ROOT / "slides" / f"{sid}.html").write_text(slides[sid], encoding="utf-8")
deck = {"v": 4, "createdOnFiles": {"v": 1, "at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")},
        "lists": "css", "title": "ORION-Q — Pré-banca", "order": order,
        "sections": {"abertura": {"description": "Tema, pergunta e a lição que organiza o trabalho", "start": "capa"},
                     "metodo": {"description": "Como cada controlador é avaliado", "start": "metodo"},
                     "resultados": {"description": "Resultados dos controladores em sementes novas", "start": "compensador"},
                     "plataforma": {"description": "ORION-Q, reprodução, hardware e verificação", "start": "plataforma"},
                     "fechamento": {"description": "Demonstração, limitações e próximos passos", "start": "demo"}},
        "faces": {"source-serif-4": {"family": "Source Serif 4", "href": "https://fonts.googleapis.com/css2?family=Source+Serif+4:wght@400..700&display=swap"},
                  "ibm-plex-sans": {"family": "IBM Plex Sans", "href": "https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;600&display=swap"}},
        "designSystems": []}
(ROOT / "deck.json").write_text(json.dumps(deck, ensure_ascii=False, indent=1), encoding="utf-8")
print("ok", len(order))
