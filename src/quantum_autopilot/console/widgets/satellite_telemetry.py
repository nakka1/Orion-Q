"""Housekeeping and science inspection using published backend observations."""
from __future__ import annotations

import json

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QHeaderView, QLabel, QLineEdit, QSpinBox, QTabWidget, QTableWidgetItem, QWidget

from .adaptive_dialog import AdaptiveDialog
from .evidence_text import EvidenceText
from .prims import Btn, vbox
from .prims import Frame, Label, Prose
from .inspector import number, percent, rate
from .metric_table import MetricTable
from .scrollable_table import ScrollableTableWidget
from ..theme import current_theme


def value(item):
    if item is None:return '—'
    if isinstance(item,bool):return 'Sim' if item else 'Não'
    if isinstance(item,float):return f'{item:.6g}'
    if isinstance(item,(dict,list)):return json.dumps(item,ensure_ascii=False)
    return str(item)


def mission_observation(ingress, satellite_id=''):
    """Public mission readback only; no credential or acquisition substitution.

    A global descriptor can be supplied by the protocol panel. A satellite's
    floating panel must never present another satellite's latest RF frame.
    Receipt UTC and unqualified onboard clocks remain separate.
    """
    ingress=ingress if isinstance(ingress,dict) else {}
    public={key:ingress[key] for key in ('source_id','origin','state','transport_state','listener_active',
                                       'last_received_at_utc','last_received_utc','accepted','quarantined','reason_code','error_type') if key in ingress}
    # The durable store names counters/decoded differently from the public
    # presentation contract. Read its aliases without copying frame_hex,
    # credentials, raw envelopes or the per-satellite history into the GUI.
    if 'accepted' not in public and isinstance(ingress.get('counters'),dict):public['accepted']=ingress['counters'].get('accepted')
    if 'quarantined' not in public and isinstance(ingress.get('counts'),dict):public['quarantined']=ingress['counts'].get('quarantined')
    latest=ingress.get('latest')
    if not isinstance(latest,dict) or (satellite_id and latest.get('satellite_id')!=satellite_id):
        public['latest']=None
        return public
    observation={key:latest[key] for key in ('satellite_id','mission_id','station','received_at_utc','ingested_at_utc',
                                           'origin','frame_sha256','status','historical_replay','provenance','reason_code',
                                           'presentation_limited','presentation_omitted','details_available') if key in latest}
    if 'status' not in observation:observation['status']=latest.get('state')
    envelope=latest.get('envelope')
    if isinstance(envelope,dict):
        for key in ('station','provenance'):
            if key not in observation and key in envelope:observation[key]=envelope[key]
    if isinstance(observation.get('provenance'),dict):
        observation['provenance']={key:observation['provenance'][key] for key in (
            'provider','url','retrieved_at_utc','sha256','observation_id','norad_cat_id','observation_sha256') if key in observation['provenance']}
    decode=latest.get('decode',latest.get('decoded'))
    if isinstance(decode,dict):
        observation['decode']={key:decode[key] for key in ('header','payload','integrity','clock','qualification','units','decoder_id','decoder_version') if key in decode}
    public['latest']=observation
    return public


def mission_rows(ingress, satellite_id=''):
    """Display the decoder's RAW observations, never invented physical units."""
    public=mission_observation(ingress,satellite_id)
    latest=public.get('latest') or {};decode=latest.get('decode',{})
    origin=f"{latest.get('origin',public.get('origin','sem observação'))} · {public.get('source_id','—')}"
    rows=[('Fonte de missão',public.get('source_id'),'',origin),
          ('Transporte / listener',f"{value(public.get('transport_state'))} / {value(public.get('listener_active'))}",'',origin),
          ('Estado do processamento',public.get('state','não configurada'),'',origin),
          ('Quadros da fonte · aceitos / quarentenados',f"{value(public.get('accepted'))} / {value(public.get('quarantined'))}",'quadros',origin),
          ('Última recepção da fonte',public.get('last_received_at_utc',public.get('last_received_utc')),'UTC','Relógio de recepção; não relógio da missão'),
          ('Satélite / missão',f"{value(latest.get('satellite_id'))} / {value(latest.get('mission_id'))}",'',origin),
          ('Estação receptora',latest.get('station'),'',origin),
          ('Recebido em',latest.get('received_at_utc'),'UTC','Recepção pelo gateway; não época orbital'),
          ('Ingresso no OrionQ',latest.get('ingested_at_utc'),'UTC','Relógio de entrada local; distinto da recepção na estação'),
          ('Replay histórico',latest.get('historical_replay'),'','Replay identificado não comprova recepção ao vivo'),
          ('Proveniência da recepção',latest.get('provenance'),'','Fonte, observação e hashes declarados; não substituem qualificação de hardware'),
          ('Estado do quadro',latest.get('status','Nenhum quadro deste satélite recebido'),'',origin),
          ('Motivo de quarentena / indisponibilidade',latest.get('reason_code',public.get('reason_code',public.get('error_type'))),'','Código do backend; nenhuma causa física é inferida'),
          ('Projeção da interface limitada',latest.get('presentation_limited'),'','Se limitada, campos omitidos permanecem na evidência completa'),
          ('Seções omitidas da projeção',latest.get('presentation_omitted'),'','Omissão visual não equivale a ausência de observação'),
          ('Evidência completa disponível',latest.get('details_available'),'','Consulte frames.latest / frames.query no receptor autenticado; captura desta janela é a projeção exibida'),
          ('SHA-256 do quadro',latest.get('frame_sha256'),'','Identifica bytes; hash não autentica autoria'),
          ('Decodificador / versão',f"{value(decode.get('decoder_id'))} / {value(decode.get('decoder_version'))}",'',origin),
          ('Cabeçalho decodificado',decode.get('header'),'RAW',origin),
          ('Integridade do quadro',decode.get('integrity'),'','CRC/integridade e autenticação são qualificações distintas'),
          ('Relógio a bordo · qualificação',decode.get('clock'),'RAW','Relógio não qualificado permanece em quarentena; não sincroniza OGS/TDC'),
          ('Unidades / escalas',decode.get('units'),'RAW','Sem ICD/calibração: nenhuma conversão para V, °C ou unidades físicas'),
          ('Qualificação operacional',decode.get('qualification'),'','RF/housekeeping não comprova tracking, recepção de fótons ou QKD')]
    payload=decode.get('payload',{})
    if isinstance(payload,dict):
        for section in ('eps','com','obc'):
            raw=payload.get(section)
            if isinstance(raw,dict):
                rows.extend((f'{section.upper()} · {name}',item,'RAW',origin+' · valor sem escala qualificada') for name,item in raw.items())
            else:
                rows.append((f'{section.upper()} · dados RAW',raw,'RAW',origin))
    return rows


def ingress_running(ingress):
    if not isinstance(ingress,dict) or not ingress:return False
    if 'listener_active' in ingress:return ingress['listener_active'] is True
    state=ingress.get('transport_state',ingress.get('state',ingress.get('status')))
    return state not in ('stopped','closed','disabled','failed','error','not_configured','unconfigured','unavailable')


def vault_reference(text):
    reference=text.strip()
    return reference if (reference.startswith('vault:') and reference[6:].strip()
                         and len(reference[6:])<=256 and not any(char in reference for char in '\r\n\0')) else None


class SatelliteLivePanel(QWidget):
    """Floating satellite content, updated solely by the shared backend snapshot."""
    details_requested=Signal(str)

    def __init__(self,parent=None):
        super().__init__(parent)
        self._state={}; self._session=''
        layout=vbox(self,(0,0,0,0),8)
        self.summary=Prose('',12.5); layout.addWidget(self.summary)
        # Every NodeWindow owns a hidden satellite panel, including ground nodes.
        # Keep its hosts light until a satellite observation or explicit readback
        # needs model/view tables; ground selection pays no table construction.
        self._tables=None;self._table_hosts={};self._table_titles={};self._table_labels={}
        self._prepared={};self._station_titles={};self._station_keys=()
        self._revision=0;self._section_revisions={};self._section_stamps={};self._section_contents={}
        for key,title in (('orbital','Órbita / housekeeping'),('mission','Missão / RF · engenharia RAW'),('bridge','Ponte entre redes / science'),
                          ('isl','Satélite ↔ satélite · canais / viabilidade'),
                          ('operations','Participação nas demandas da rede')):
            label=Label(title,wrap=True);self._table_titles[key]=title;self._table_labels[key]=label
            if key=='mission':
                self._mission_label=label;self._table_labels.pop(key);label.hide()
            layout.addWidget(label)
            host=QWidget();self._table_hosts[key]=vbox(host,(0,0,0,0),0)
            layout.addWidget(host)
        self.station_host=QWidget();self.station_lay=vbox(self.station_host,(0,0,0,0),8)
        layout.addWidget(self.station_host);self.stations={};self._station_frames={}
        self.details=Btn('HOUSEKEEPING / SCIENCE · DETALHES')
        self.details.setToolTip('Abre as medições do último bloco, unidades, origem e validade; continua recebendo os snapshots do backend.')
        self.details.clicked.connect(lambda:self.details_requested.emit(self._session))
        layout.addWidget(self.details)

    def _ensure_fields(self):
        for key in self._table_labels:self._ensure_field(key)
        return self._tables

    def _ensure_field(self,key):
        if self._tables is None:self._tables={}
        if key not in self._tables:
            self._tables[key]=MetricTable(size=12.5)
            self._table_hosts[key].addWidget(self._tables[key])
        return self._tables[key]

    @property
    def orbital(self):return self._ensure_fields()['orbital']

    @property
    def mission(self):return self._ensure_field('mission')

    @property
    def bridge(self):return self._ensure_fields()['bridge']

    @property
    def isl(self):return self._ensure_fields()['isl']

    @property
    def operations(self):return self._ensure_fields()['operations']

    def set_state(self,state,record,*,presentation=True):
        """Prepare received fields; the caller may present sections cooperatively.

        Default callers receive complete synchronous tables. No timer, snapshot
        history or protocol work is introduced by the optional visual staging.
        """
        self._state=state
        self._revision+=1;self._prepared={};self._station_titles={}
        bridges=[bridge for bridge in state.get('bridges',[]) if 'satellite_isl' not in bridge.get('technologies',[])]
        first=bridges[0] if bridges else {}
        first=next((bridge for bridge in bridges if bridge.get('metrics')),first)
        first_isl=next((link for link in state.get('isl_links',[]) if link.get('session_id') and link.get('metrics')), {})
        self._session=(first.get('session_id','') if first.get('metrics') else first_isl.get('session_id','')) or first.get('session_id','')
        metrics=first.get('metrics',{})
        hk=state.get('housekeeping') or metrics.get('satellite_housekeeping',{}).get(state.get('node_id',state.get('device_id',''))) or metrics.get('housekeeping',{})
        clock=state.get('clock_s');age=clock-hk['at_s'] if clock is not None and 'at_s' in hk else None
        observed=bool(hk)
        stale=(clock is not None and hk.get('valid_until_s',float('inf'))<clock) or (observed and state.get('housekeeping_valid') is False)
        summary = (f"{record.get('name',state.get('device_id','Satélite'))}\n"
            f"Origem: {state.get('origin','sem dados')} · "+('OBSERVAÇÃO VENCIDA' if stale else 'vetor recebido' if observed else 'efeméride cadastrada; aguardando observações da interface')+
            f"\n{state.get('acceleration_label','')}\nRF/telemetria clássica e carga útil óptica quântica são canais distintos."
            +(f"\nErro orbital: {state['error']}" if state.get('error') else ''))
        mission=mission_observation(state.get('satellite_mission_ingress'),state.get('node_id',state.get('device_id','')))
        received=mission.get('latest')
        if received:
            summary+=(f"\nMissão / RF: quadro recebido · {received.get('status','—')} · {received.get('mission_id','—')}"
                      f" · {received.get('origin',mission.get('origin','sem origem'))} · {mission.get('source_id','—')}"
                      +(' · REPLAY HISTÓRICO' if received.get('historical_replay') else '')
                      +f" · UTC {value(received.get('received_at_utc'))}"
                      +f"\nCamada óptica: {'observações publicadas; hardware não qualificado' if first.get('metrics') or first_isl.get('metrics') else 'sem observação'}. A recepção RF não comprova fótons ou QKD.")
        if self.summary.text() != summary:
            self.summary.setText(summary)
        self._prepared['orbital']=[
            ('Classe / modelo',f"{state.get('orbital_class','—')} · {state.get('model','—')}"),
            ('Tempo orbital',number(state.get('at_network_s'),'.3f','s')),
            ('Época UTC',value(state.get('at_utc'))),('Idade / validade',f"{number(age,'.3f','s')} / {number(hk.get('valid_until_s'),'.3f','s da rede')}"),
            ('Altitude',number(state.get('altitude_km'),'.2f','km')),
            ('Posição ECEF · km',value(state.get('ecef_km'))),('Velocidade · km/s',value(state.get('velocity_ecef_km_s'))),
            ('Escopo da posição',value(state.get('position_scope','received observation' if observed else 'configured ephemeris prediction'))),
            ('Housekeeping válido',value(state.get('housekeeping_valid',not stale if observed else None))),
            ('Período',number(state.get('period_s'),'.1f','s')),('Fonte orbital',value(state.get('ephemeris_source',record.get('orbit',{}).get('source'))))]
        # Older snapshots have no mission descriptor. Preserve their section
        # staging order; a current descriptor (including latest=None) displays
        # explicit lack of RF observations instead of hiding missing data.
        mission_present='satellite_mission_ingress' in state
        self._mission_label.setVisible(mission_present)
        if mission_present:
            self._table_labels['mission']=self._mission_label
            self._prepared['mission']=[(row[0],f'{value(row[1])}'+(f' · {row[2]}' if row[2] else '')+f' · {row[3]}')
                for row in mission_rows(state.get('satellite_mission_ingress'),state.get('node_id',state.get('device_id','')))]
        else:
            self._table_labels.pop('mission',None)
            if self._tables and 'mission' in self._tables:self._tables['mission'].hide()
        statuses={'observed_pairs':'Pares entre redes observados no último bloco','waiting':'Aguardando observações válidas',
                  'blocked':'Ponte indisponível; consulte o motivo do backend','sampling':'Recepção em amostragem; sem coincidências',
                  'disabled':'Sessão desativada'}
        rows=[]
        for bridge_index,bridge in enumerate(bridges,1):
            data=bridge.get('metrics',{})
            fields=[(bridge['session_id'],' → '.join(bridge.get('path',[]))),
                ('Protocolo / tecnologia',f"{bridge.get('protocol','—')} · {' + '.join(bridge.get('technologies',[]))}"),
                ('Estado da ponte',statuses.get(bridge.get('status'),bridge.get('status','—'))),
                ('Motivo do backend',bridge.get('reason','—')),
                ('Origem do bloco',f"{bridge.get('origin','sem observação')} · {data.get('observation_source_id','—')}"),
                ('Coincidências / taxa',f"{value(data.get('coincidences'))} / {number(data.get('pair_rate_hz'),'.3g','pares/s')}"),
                ('Fidelidade conjunta',number(data.get('fidelity'),'.4f')),
                ('QBER / sifting',f"{percent(data.get('qber'))} / {value(data.get('sifted_bits'))} bits"),
                ('Orçamento condicional',rate(data.get('conditional_key_estimate_bps'))),
                ('Chave certificada','0 · teste de protocolo; sem chave utilizável'),
                ('Aborto pós-processamento',value(data.get('finite_key',{}).get('abort_reason')))]
            rows.extend((f"{bridge_index} · {'Sessão / rota' if key==bridge['session_id'] else key}" if len(bridges)>1 else key,
                         f"{bridge['session_id']} · {val}" if len(bridges)>1 and key==bridge['session_id'] else val) for key,val in fields)
        self._prepared['bridge']=rows or [('Conexão','Nenhuma sessão satelital cadastrada')]
        isl_rows=[]
        for isl_index,link in enumerate(state.get('isl_links',[]),1):
            data=link.get('metrics',link.get('geometry',{}));geo=data.get('isl_geometry',data)
            c=link.get('classical',{});q=link.get('quantum',{})
            budget=geo.get('link_budget_db',{})
            fields=[
                ('Enlace / destino',f"{link.get('link_id','—')} ↔ {link.get('peer_id','—')}"),
                ('Estado / causa',f"{link.get('status',link.get('state','—'))} · {link.get('reason','—')}"),
                ('Origem / tempo',f"{link.get('origin',geo.get('observation_origin','sem observação'))} · {number(link.get('at_s',geo.get('at_s')),'.3f','s')}"),
                ('Escopo das métricas',link.get('geometry_scope','Origem descrita pelo backend; hardware não qualificado')),
                ('Canal clássico',f"{c.get('channel_id','—')} · {c.get('protocol','—')} · {'ON' if c.get('enabled') else 'OFF'}"),
                ('Payload quântico',f"{q.get('channel_id','—')} · {q.get('protocol','—')} · {'ON' if q.get('enabled') else 'OFF'}"),
                ('Visibilidade / obstrução terrestre',f"{value(geo.get('visible'))} / {value(geo.get('earth_obstructed'))}"),
                ('Distância entre satélites',number(geo.get('range_km'),'.2f','km')),
                ('Apontamento / sincronização',f"{number(geo.get('pointing_error_urad'),'.3g','µrad')} / {number(geo.get('sync_jitter_ps'),'.3g','ps')}"),
                ('Point-ahead / propagação',f"{number(geo.get('point_ahead_urad'),'.3g','µrad')} / {number(geo.get('flight_time_s'),'.4g','s')}"),
                ('Doppler / residual',f"{number(geo.get('doppler_shift_hz'),'.4g','Hz')} / {number(geo.get('doppler_residual_hz'),'.4g','Hz')}"),
                ('Perda total / transmitância',f"{number(budget.get('total',geo.get('loss_db')),'.3g','dB')} / {number(geo.get('channel_transmission'),'.3g')}"),
                ('Emissões / detecções recebidas',f"{value(geo.get('emitted_photons',geo.get('emitted_pairs')))} / {value(geo.get('detected_photons',geo.get('detections')))}")]
            prefix=f'ISL {isl_index}'
            isl_rows.extend((f'{prefix} · {key}',val) for key,val in fields)
        self._prepared['isl']=isl_rows or [('ISL','Nenhum enlace satélite–satélite cadastrado; proximidade não implica comunicação')]
        demands=state.get('global_demands',[])
        self._prepared['operations']=[(f'Demanda {index}',
            f"{demand.get('id',demand.get('demand_id','—'))} · {demand.get('operation','—')} · {demand.get('state',demand.get('status','—'))} · {' → '.join(demand.get('path',demand.get('route',[])))} · {demand.get('reason','—')}")
            for index,demand in enumerate(demands,1)] or [('Demandas','Nenhuma demanda atribuída a este satélite no snapshot')]
        entries=[(bridge,item) for bridge in bridges for item in bridge.get('connections',[]) if item.get('endpoint_kind')!='satellite']
        keys=tuple(dict.fromkeys(item['link_id'] for _,item in entries))
        self._station_keys=keys
        for key in tuple(self.stations):
            if key not in keys:
                frame,_label=self._station_frames.pop(key)
                self.station_lay.removeWidget(frame)
                frame.hide();frame.deleteLater();self.stations.pop(key)
                self._section_revisions.pop('station:'+key,None);self._section_stamps.pop('station:'+key,None)
                self._section_contents.pop('station:'+key,None)
        transmitting={t['link_id'] for t in state.get('transmitting_links',[])}
        for bridge,item in entries:
            key=item['link_id'];self._station_titles[key]=item['station_id']+' · '+key
            endpoints=bridge.get('endpoints',[]);geometry=bridge.get('metrics',{}).get('geometry',[])
            index=endpoints.index(item['station_id']) if item['station_id'] in endpoints else -1
            geo=geometry[index] if 0<=index<len(geometry) else {};q=item.get('quantum',{});c=item.get('classical',{})
            self._prepared['station:'+key]=[
                ('Associação',value(item.get('association_id'))),
                ('Canal clássico',f"{c.get('channel_id','—')} · {c.get('protocol','—')} · {'ON' if c.get('enabled') else 'OFF'}"),
                ('Payload quântico',f"{q.get('channel_id','—')} · {q.get('protocol','—')} · {'ON' if q.get('enabled') else 'OFF'}"),
                ('Emissão recebida','Sim · último bloco recebido' if item['link_id'] in transmitting else 'Não comprovada / indisponível'),
                ('Visibilidade',value(geo.get('visible'))),
                ('Elevação / azimute',f"{number(geo.get('elevation_deg'),'.2f','°')} / {number(geo.get('azimuth_deg'),'.2f','°')}"),
                ('Distância oblíqua',number(geo.get('range_km'),'.2f','km')),
                ('Doppler / residual',f"{number(geo.get('carrier_doppler_ghz'),'.4g','GHz')} / {number(geo.get('doppler_residual_hz'),'.4g','Hz')}"),
                ('Point-ahead',number(geo.get('point_ahead_urad'),'.3g','µrad')),
                ('Perda óptica total',number(geo.get('link_budget_db',{}).get('total'),'.3g','dB'))]
        self.details.setEnabled(bool(first.get('metrics') or first_isl.get('metrics') or mission_present))
        if presentation:
            for key in self.presentation_keys():self.present_section(key)
        else:
            for key in self.presentation_keys():self._update_section_label(key)

    def presentation_keys(self):
        return tuple(self._prepared)

    def invalidate_presentation(self):
        """Hide a different source's old tables until current data replaces them."""
        self._section_revisions.clear();self._section_stamps.clear();self._section_contents.clear()
        for table in (self._tables or {}).values():table.hide()
        for frame,_label in self._station_frames.values():frame.hide()

    def _update_section_label(self,key):
        if key.startswith('station:'):
            station=key.removeprefix('station:')
            if station not in self._station_frames:return
            _frame,label=self._station_frames[station];title=self._station_titles[station]
        else:
            label=self._table_labels[key];title=self._table_titles[key]
        stamp=self._section_stamps.get(key)
        pending=self._section_revisions.get(key)!=self._revision
        suffix=(f" · publicação exibida {number(stamp[0],'.3f','s')} · {stamp[1]}" if stamp else '')
        text=title+suffix+(' · atualização pendente' if pending else '')
        if label.text()!=text:label.setText(text)

    def present_section(self,key):
        """Present one latest section without forcing the other table factories."""
        rows=self._prepared[key]
        if key.startswith('station:'):
            station=key.removeprefix('station:')
            if station not in self.stations:
                frame=Frame();box=vbox(frame,(10,8,10,8),5)
                label=Label(self._station_titles[station],wrap=True);box.addWidget(label)
                table=MetricTable(size=12.5);box.addWidget(table)
                self.stations[station]=table;self._station_frames[station]=(frame,label)
            frame,_label=self._station_frames[station]
            position=self._station_keys.index(station)
            if self.station_lay.indexOf(frame)!=position:
                self.station_lay.insertWidget(position,frame)
            self.stations={ident:self.stations[ident] for ident in self._station_keys if ident in self.stations}
            table=self.stations[station]
        else:
            frame=None;table=self._ensure_field(key)
        # An unchanged received section still advances its publication stamp,
        # without resetting table geometry or invalidating an identical raster.
        # Include the theme so tones remain current when the data stays equal.
        content=(current_theme(),tuple(tuple(row) for row in rows))
        try:
            if self._section_contents.get(key)!=content:
                table.set_rows(rows)
        except Exception:
            table.hide()
            if frame is not None:frame.hide()
            raise
        self._section_contents[key]=content
        if table.isHidden():table.show()
        if frame is not None and frame.isHidden():frame.show()
        self._section_revisions[key]=self._revision
        origin=(self._state.get('satellite_mission_ingress') or {}).get('origin','sem dados') if key=='mission' else self._state.get('origin','sem dados')
        self._section_stamps[key]=(self._state.get('clock_s',self._state.get('at_network_s')),origin)
        self._update_section_label(key)

    def content_texts(self):
        """Read actual tables only; never construct missing sections for a readback."""
        return ({key:table.texts() for key,table in (self._tables or {}).items() if key!='mission' or key in self._prepared},
                {key:table.texts() for key,table in self.stations.items()})


class SatelliteTelemetryDialog(AdaptiveDialog):
    command=Signal(str,str)

    def __init__(self,parent=None):
        super().__init__(parent);self.setWindowTitle('OrionQ · Satélite / OGS · Housekeeping e Science')
        self.resize(1180,820);self.setMinimumSize(460,400);self._metrics={};self._session_id=''
        self._mission_ingress={};self._satellite_id='';self._auto_satellite_ids=''
        self._allowed=False;self._allowed_provision=False;self._ogs_running=False;self._mission_running=False;self._isl=False
        self._ogs_available=False;self._view_context=None
        layout=vbox(self,(14,14,14,14),10)
        self.summary=QLabel();self.summary.setWordWrap(True);layout.addWidget(self.summary)
        self.tabs=QTabWidget();layout.addWidget(self.tabs,1)
        self.housekeeping=self._table('Housekeeping · órbita / APT')
        self.science=self._table('Science · payload / QKD')
        self.mission=self._table('Missão / RF · engenharia RAW')
        page=QWidget();self.ogs_page=page;controls=vbox(page,(12,12,12,12),10);self._ogs_tab=self.tabs.addTab(page,'Entrada OGS · somente recepção')
        note=QLabel('Gateway de estação terrestre: três entradas clássicas separadas para housekeeping, tracking e cliques TDC. '
                    'RF/CCSDS e o hardware TDC são decodificados pelo gateway da missão. Nenhuma atuação física. '
                    'A sessão autenticada é resolvida pelo cofre autorizado; informe apenas uma referência vault:, nunca o segredo.')
        note.setWordWrap(True);controls.addWidget(note)
        self.integration_note=note
        controls.addWidget(QLabel('Referência da sessão autorizada · vault:<referência> · obrigatório'))
        self.credential=QLineEdit('vault:orion-ogs-session');controls.addWidget(self.credential)
        self.credential.setToolTip('Referência de uma sessão do serviço ogs_gateway no cofre. A identidade e as permissões são revalidadas pelo backend; env: e tokens digitados não são aceitos.')
        self.provision=Btn('PROVISIONAR SESSÃO OGS');self.provision.setToolTip('Administrador: provisiona uma sessão de recepção por uma hora no cofre; a interface recebe apenas a referência, sem exibir o token.')
        self.provision.clicked.connect(lambda:self._provision('ogs'));controls.addWidget(self.provision)
        controls.addWidget(QLabel('Porta local · 1024–65535'))
        self.port=QSpinBox();self.port.setRange(1024,65535);self.port.setValue(8765);controls.addWidget(self.port)
        self.start=Btn('ABRIR RECEPÇÃO OGS');self.start.setToolTip('Inicia WebSocket autenticado em 127.0.0.1 e associa os dados à sessão selecionada; não transmite comandos aos dispositivos.')
        self.start.clicked.connect(self._start);controls.addWidget(self.start)
        self.stop=Btn('ENCERRAR RECEPÇÃO OGS');self.stop.setToolTip('Fecha o servidor de observações. A sessão fica sem novos dados, mantendo as evidências persistidas.')
        self.stop.clicked.connect(lambda:self._stop('ogs'));controls.addWidget(self.stop)
        self.ogs_error=QLabel();self.ogs_error.setWordWrap(True);controls.addWidget(self.ogs_error)
        self.ingress=QLabel();self.ingress.setWordWrap(True);controls.addWidget(self.ingress);controls.addStretch(1)
        page=QWidget();controls=vbox(page,(12,12,12,12),10);self.tabs.addTab(page,'Entrada Missão / RF · somente recepção')
        note=QLabel('Recepção clássica de quadros de missão já demodulados pela estação terrestre. '
                    'O decodificador preserva EPS/COM/OBC em RAW, integridade, origem e relógio não qualificado. '
                    'Esta entrada não cria vetores ECEF, tracking, cliques TDC ou resultados QKD e não controla equipamentos.')
        note.setWordWrap(True);controls.addWidget(note)
        controls.addWidget(QLabel('Identificador da fonte · obrigatório'))
        self.mission_source=QLineEdit('mission_gateway');controls.addWidget(self.mission_source)
        self.mission_source.setToolTip('Identidade da fonte que deve coincidir com os envelopes recebidos e com a sessão provisionada.')
        controls.addWidget(QLabel('Origem declarada dos dados · obrigatório'))
        self.mission_origin=QComboBox();self.mission_origin.addItems(('physical','measurement_file','simulated'));controls.addWidget(self.mission_origin)
        self.mission_origin.setToolTip('physical: dados de uma recepção física; measurement_file: replay identificado; simulated: quadros artificiais. O transporte local não comprova recepção de RF real.')
        controls.addWidget(QLabel('IDs dos satélites autorizados · separados por vírgula · obrigatório'))
        self.mission_satellite_ids=QLineEdit();controls.addWidget(self.mission_satellite_ids)
        self.mission_satellite_ids.setToolTip('Use os identificadores dos comunicadores cadastrados. O backend verifica o cadastro e a associação; a instituição não substitui o ID do satélite.')
        controls.addWidget(QLabel('Referência da sessão autorizada · vault:<referência> · obrigatório'))
        self.mission_credential=QLineEdit('vault:orion-mission-session');controls.addWidget(self.mission_credential)
        self.mission_credential.setToolTip('Referência protegida da sessão de recepção da fonte de missão. Nenhum token ou segredo deve ser digitado aqui.')
        self.mission_provision=Btn('PROVISIONAR SESSÃO DE MISSÃO');self.mission_provision.clicked.connect(lambda:self._provision('mission'));controls.addWidget(self.mission_provision)
        self.mission_provision.setToolTip('Administrador: cria uma sessão para a identidade da fonte por uma hora; o token permanece no cofre autorizado.')
        controls.addWidget(QLabel('Porta local · 1024–65535'))
        self.mission_port=QSpinBox();self.mission_port.setRange(1024,65535);self.mission_port.setValue(8766);controls.addWidget(self.mission_port)
        self.mission_start=Btn('ABRIR RECEPÇÃO DE MISSÃO');self.mission_start.clicked.connect(self._start_mission);controls.addWidget(self.mission_start)
        self.mission_start.setToolTip('Inicia o gateway WebSocket autenticado de quadros RF em 127.0.0.1. Origem e associação aos satélites são validadas pelo backend.')
        self.mission_stop=Btn('ENCERRAR RECEPÇÃO DE MISSÃO');self.mission_stop.clicked.connect(lambda:self._stop('mission'));controls.addWidget(self.mission_stop)
        self.mission_stop.setToolTip('Encerra a recepção de novos quadros; preserva os registros e sua qualificação.')
        self.mission_error=QLabel();self.mission_error.setWordWrap(True);controls.addWidget(self.mission_error)
        self.mission_status=QLabel();self.mission_status.setWordWrap(True);controls.addWidget(self.mission_status);controls.addStretch(1)
        for edit in (self.credential,self.mission_source,self.mission_satellite_ids,self.mission_credential):
            edit.textChanged.connect(self._refresh_controls)
        evidence_page=QWidget();ev=vbox(evidence_page,(12,12,12,12),8);self.tabs.addTab(evidence_page,'Evidência capturada')
        capture=Btn('CAPTURAR OBSERVAÇÃO');capture.clicked.connect(self.capture);ev.addWidget(capture)
        self.evidence=EvidenceText();ev.addWidget(self.evidence,1)
        close=Btn('FECHAR');close.clicked.connect(self.close);layout.addWidget(close);self.fixed_footer=(close,)
        self._refresh_controls()

    def _table(self,label):
        table=ScrollableTableWidget(0,4);table.setHorizontalHeaderLabels(('Campo','Valor','Unidade','Origem / qualidade'))
        table.setEditTriggers(ScrollableTableWidget.EditTrigger.NoEditTriggers)
        table.horizontalHeader().setSectionResizeMode(0,QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(1,QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setSectionResizeMode(2,QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(3,QHeaderView.ResizeMode.Stretch)
        table.setWordWrap(False);table.setMinimumHeight(230);self.tabs.addTab(table,label);return table

    @staticmethod
    def _rows(table,rows,origin):
        if table.rowCount()!=len(rows):table.setRowCount(len(rows))
        for r,row in enumerate(rows):
            for c,text in enumerate((row[0],value(row[1]),row[2],row[3] if len(row)>3 else origin)):
                item=table.item(r,c)
                if item is None:item=QTableWidgetItem();table.setItem(r,c,item)
                if item.text()!=text:item.setText(text)
                if item.toolTip()!=text:item.setToolTip(text)

    def set_metrics(self,metrics,*,session_id='',clock_s=None,ingress=None,allowed=False,
                    mission_ingress=None,satellite_id='',allowed_provision=False):
        self._metrics=metrics;self._session_id=session_id;hk=metrics.get('housekeeping',{})
        self._mission_ingress=mission_observation(mission_ingress,satellite_id);self._satellite_id=satellite_id
        if not self.mission_satellite_ids.text().strip() or self.mission_satellite_ids.text()==self._auto_satellite_ids:
            if self.mission_satellite_ids.text()!=satellite_id:self.mission_satellite_ids.setText(satellite_id)
            self._auto_satellite_ids=satellite_id
        self._allowed=allowed;self._allowed_provision=allowed_provision
        self._ogs_running=ingress_running(ingress);self._mission_running=ingress_running(mission_ingress)
        origin=f"{metrics.get('observation_origin','sem observação')} · {metrics.get('observation_source_id','')}"
        age=clock_s-hk['at_s'] if clock_s is not None and 'at_s' in hk else None
        expired=clock_s is not None and hk.get('valid_until_s',float('inf'))<clock_s
        quality='VENCIDA' if expired else 'Recebida; hardware não qualificado' if hk else 'Sem observação'
        summary=f'Sessão: {session_id or "—"} · {origin} · {quality}\n'+(
            'Telemetria e cliques TDC circulam como registros clássicos; fótons usam o payload óptico. '
            'Estimativas condicionais; chaves certificadas: 0. Nenhuma chave utilizável é exportada.')
        isl=metrics.get('isl_geometry')
        self._isl=bool(isl)
        self._ogs_available=bool(session_id) and not self._isl
        if isl:
            summary=f'Sessão ISL: {session_id or "—"} · {origin}\n'+(
                'Enlace óptico satélite–satélite: detecções confirmam este trecho. Entrelaçamento entre extremos requer operações e recibos adicionais. '
                'Nenhuma chave utilizável ou atuação física.')
            note='Enlace ISL: esta sessão não disponibiliza entrada OGS. A recepção Missão / RF é independente da sessão óptica. '+(
                'Fontes disponíveis pelo painel de protocolos: simulação e arquivo de medições no contrato de aquisição. '
                'Decodificação RF/TDC e transporte físico entre terminais ainda exigem adaptador e qualificação da missão.')
        else:
            note=('Gateway de estação terrestre: três entradas clássicas separadas para housekeeping, tracking e cliques TDC. '
                'RF/CCSDS e o hardware TDC são decodificados pelo gateway da missão. Nenhuma atuação física. '
                'A sessão autenticada é resolvida pelo cofre autorizado; informe apenas uma referência vault:, nunca o segredo.')
        latest=self._mission_ingress.get('latest')
        if latest:
            received_origin=f"{latest.get('origin',self._mission_ingress.get('origin','sem origem'))} · {self._mission_ingress.get('source_id','—')}"
            replay=' · REPLAY HISTÓRICO' if latest.get('historical_replay') else ''
            summary=(f"Satélite: {satellite_id or latest.get('satellite_id','—')} · Missão: {latest.get('mission_id','—')}\n"
                     f"Missão / RF: quadro recebido · {latest.get('status','—')} · {received_origin}{replay} · UTC {value(latest.get('received_at_utc'))}\n"
                     f"Camada óptica: {quality} · sessão {session_id or 'não cadastrada'} · {origin}\n"
                     'Integridade, relógio e unidades possuem qualificação própria. A recepção RF não comprova fótons, tracking ou QKD.')
        elif mission_ingress is not None and not session_id and satellite_id:
            summary=(f"Satélite: {satellite_id} · Missão / RF: {self._mission_ingress.get('state','sem observação')}\n"
                     'Camada óptica: nenhuma sessão cadastrada. A entrada clássica de missão é independente de OGS/TDC/QKD.')
        if self.summary.text()!=summary:self.summary.setText(summary)
        if self.integration_note.text()!=note:self.integration_note.setText(note)
        engineering=[('Época observada UTC',hk.get('at_utc'),'UTC'),('Idade da observação',age,'s'),
            ('Validade até o tempo da rede',hk.get('valid_until_s'),'s'),('Efemérides',hk.get('source'),''),
            ('Posição ECEF (X,Y,Z)',hk.get('ecef_km'),'km'),('Velocidade ECEF (Vx,Vy,Vz)',hk.get('velocity_ecef_km_s'),'km/s'),
            ('Estado da fonte',hk.get('source_status'),''),('Modelo orbital',hk.get('orbit_model'),''),
            ('Housekeeping RF / laser clássico',hk.get('control_downlink'),''),
            ('Tensão do barramento',hk.get('bus_voltage_v'),'V'),('Temperatura da carga útil',hk.get('payload_temperature_c'),'°C'),
            ('Estado de carga da bateria',hk.get('battery_soc'),'fração')]
        science=[('Protocolo',metrics.get('protocol'),''),('Pares emitidos observados',metrics.get('emitted_pairs'),'pares'),
            ('Fonte de pares',hk.get('pair_source_hz'),'pares/s'),('Coincidências processadas',metrics.get('coincidences'),'cliques pareados'),
            ('Coincidências por segundo',metrics.get('pair_rate_hz'),'1/s'),('Sifting (Z + X)',metrics.get('sifted_bits'),'bits'),
            ('Amostra de parâmetros (X)',metrics.get('parameter_sample_bits'),'bits'),('QBER',metrics.get('qber'),'fração'),
            ('QBER Z',metrics.get('qber_z'),'fração'),('QBER X',metrics.get('qber_x'),'fração'),
            ('Jitter residual observado (RMS)',metrics.get('coincidence_residual_rms_ps'),'ps'),
            ('Reconciliação verificada',metrics.get('reconciliation_verified'),''),
            ('Vazamento público EC contabilizado',metrics.get('error_correction_leak_bits'),'bits'),
            ('Orçamento finito condicional',metrics.get('conditional_key_estimate_bits'),'bits'),
            ('Hashing PA executado (teste)',metrics.get('privacy_amplification_executed_bits'),'bits'),
            ('Chaves certificadas',0,'bits','Não qualificado'),
            ('Motivo de aborto',metrics.get('finite_key',{}).get('abort_reason'),''),
            ('Limite do erro de fase',metrics.get('finite_key',{}).get('phase_error_upper'),'fração'),
            ('Disponível em parte do bloco',metrics.get('available'),''),('Disponível ao fim do bloco',metrics.get('available_at_end'),'')]
        for ident,satellite in metrics.get('satellite_housekeeping',{}).items():
            for label,key,unit in [('Época','at_utc','UTC'),('Posição ECEF','ecef_km','km'),
                    ('Velocidade ECEF','velocity_ecef_km_s','km/s'),('Validade','valid_until_s','s')]:
                engineering.append((ident+' · '+label,satellite.get(key),unit,'Housekeeping recebido; '+origin))
        isl_entries=list(isl.items()) if isinstance(isl,dict) and 'range_km' not in isl else [('ISL',isl)] if isinstance(isl,dict) else [(f'ISL {i+1}',geo) for i,geo in enumerate(isl or [])]
        for ident,geo in isl_entries:
            if not isinstance(geo,dict):continue
            for label,key,unit in [('Visibilidade mútua','visible',''),('Obstrução pela Terra','earth_obstructed',''),
                    ('Distância','range_km','km'),('Point-ahead','point_ahead_urad','µrad'),
                    ('Tempo de propagação','flight_time_s','s'),
                    ('Desvio Doppler','doppler_shift_hz','Hz'),('Doppler residual','doppler_residual_hz','Hz'),('Apontamento residual','pointing_error_urad','µrad'),
                    ('Jitter de sincronização','sync_jitter_ps','ps')]:
                engineering.append((str(ident)+' · '+label,geo.get(key),unit,'Geometria / terminais recebidos; '+origin))
            for label,key,unit in [('Transmitância','channel_transmission','fração'),('Emissões','emitted_photons','fótons'),
                    ('Detecções','detected_photons','cliques')]:
                science.append((str(ident)+' · '+label,geo.get(key),unit,origin))
            for name,loss in geo.get('link_budget_db',{}).items():science.append((str(ident)+' · perda '+name,loss,'dB','Orçamento óptico ISL'))
        for index,geo in enumerate(metrics.get('geometry',[])):
            tracking=metrics.get('tracking',[]);station=tracking[index].get('station_id',f'OGS {index+1}') if index<len(tracking) else f'OGS {index+1}'
            for label,key,unit in [('Elevação','elevation_deg','graus'),('Azimute','azimuth_deg','graus'),
                ('Distância oblíqua','range_km','km'),('Velocidade radial','radial_velocity_km_s','km/s'),
                ('Point-ahead (beacon recebido → emissão)','point_ahead_urad','µrad'),
                ('Desvio Doppler óptico','carrier_doppler_ghz','GHz'),('Correção Doppler','doppler_correction_hz','Hz'),
                ('Doppler residual','doppler_residual_hz','Hz'),('Apontamento residual','pointing_error_urad','µrad')]:
                engineering.append((station+' · '+label,geo.get(key),unit,'Derivado da efeméride/óptica; '+origin))
            for label,key,unit in [('Erro de polarização antes','e_mis_before','fração'),('Erro de polarização após','e_mis_after','fração'),
                ('Cn²','cn2_m_neg_2_3','m^(-2/3)'),('Óptica adaptativa','adaptive_optics',''),
                ('Transmitância óptica','channel_transmission','fração')]:
                science.append((station+' · '+label,geo.get(key),unit,'Modelo configurado; '+origin))
            for name,loss in geo.get('link_budget_db',{}).items():science.append((station+' · perda '+name,loss,'dB','Orçamento óptico condicionado à geometria'))
        for batch in metrics.get('tdc_summary',[]):
            station=batch['station_id']
            engineering.append((station+' · referência do relógio',batch.get('clock_reference'),''))
            science.extend([(station+' · cliques',batch.get('clicks'),'cliques'),
                (station+' · incerteza do relógio',batch.get('clock_uncertainty_ps'),'ps'),
                (station+' · tags perdidas',batch.get('lost_tags'),'cliques'),
                (station+' · descartes por tempo morto',batch.get('dead_time_discarded'),'cliques')])
        science.append(('Fonte dos parâmetros',metrics.get('parameter_provenance',{}).get('scope'),''))
        science.append(('Modelo temporal',metrics.get('parameter_provenance',{}).get('time_model'),''))
        science.append(('Origem da aleatoriedade pública',metrics.get('randomness_origin'),''))
        self._rows(self.housekeeping,engineering,origin);self._rows(self.science,science,origin)
        self._rows(self.mission,mission_rows(self._mission_ingress,satellite_id),'Missão / RF; engenharia RAW')
        self._refresh_controls()
        context=(satellite_id,session_id,mission_ingress is not None)
        if self._view_context!=context and mission_ingress is not None and not self._ogs_available:
            self.tabs.setCurrentWidget(self.mission)
        self._view_context=context
        fields=('state','status','source_id','origin','pending_packets','accepted','quarantined','url')
        public={key:ingress[key] for key in fields if key in ingress} if isinstance(ingress,dict) else {}
        if isinstance(ingress,dict) and isinstance(ingress.get('ogs_gateway'),dict):
            public['ogs_gateway']={key:ingress['ogs_gateway'][key] for key in fields if key in ingress['ogs_gateway']}
        text='Entrada OGS: '+json.dumps(public or {'status':'desligada'},ensure_ascii=False)
        if self.ingress.text()!=text:self.ingress.setText(text)
        text='Entrada de missão: '+json.dumps({key:self._mission_ingress[key] for key in ('transport_state','listener_active','state','source_id','origin','accepted','quarantined') if key in self._mission_ingress},ensure_ascii=False)
        if self.mission_status.text()!=text:self.mission_status.setText(text)

    def _start(self):
        if not self._allowed or not self._ogs_available or self._ogs_running:return
        reference=vault_reference(self.credential.text())
        if not reference:
            self.ogs_error.setText('Informe apenas vault:<referência de sessão autorizada>. Variáveis de ambiente e tokens não são aceitos.');return
        self.ogs_error.clear()
        self.command.emit('configure_technology_source',json.dumps({'kind':'ogs_websocket','session_id':self._session_id,
            'credential_ref':reference,'port':self.port.value()}))

    def _satellite_ids(self):
        return list(dict.fromkeys(item.strip() for item in self.mission_satellite_ids.text().split(',') if item.strip()))

    def _refresh_controls(self):
        if self.tabs.isTabVisible(self._ogs_tab)!=self._ogs_available:self.tabs.setTabVisible(self._ogs_tab,self._ogs_available)
        if self.ogs_page.isEnabled()!=self._ogs_available:self.ogs_page.setEnabled(self._ogs_available)
        self.start.setEnabled(self._allowed and self._ogs_available and not self._ogs_running and bool(vault_reference(self.credential.text())))
        self.stop.setEnabled(self._allowed and self._ogs_available and self._ogs_running)
        self.provision.setEnabled(self._allowed_provision and self._ogs_available and not self._ogs_running and bool(vault_reference(self.credential.text())))
        self.mission_start.setEnabled(self._allowed and not self._mission_running and bool(self.mission_source.text().strip()) and bool(self._satellite_ids()) and bool(vault_reference(self.mission_credential.text())))
        self.mission_stop.setEnabled(self._allowed and self._mission_running)
        self.mission_provision.setEnabled(self._allowed_provision and not self._mission_running and bool(self.mission_source.text().strip()) and bool(vault_reference(self.mission_credential.text())))

    def _start_mission(self):
        if not self._allowed or self._mission_running:return
        reference=vault_reference(self.mission_credential.text());source=self.mission_source.text().strip();satellites=self._satellite_ids()
        if not reference or not source or not satellites:
            self.mission_error.setText('Fonte, IDs de satélites e vault:<referência de sessão autorizada> são obrigatórios. Tokens e env: não são aceitos.');return
        self.mission_error.clear()
        self.command.emit('configure_technology_source',json.dumps({'kind':'mission_websocket','source_id':source,
            'origin':self.mission_origin.currentText(),'satellite_ids':satellites,'credential_ref':reference,'port':self.mission_port.value()}))

    def _stop(self,kind):
        if not self._allowed or not (self._ogs_running if kind=='ogs' else self._mission_running):return
        if kind=='ogs' and not self._ogs_available:return
        self.command.emit('configure_technology_source',json.dumps({'kind':'stop_ogs' if kind=='ogs' else 'stop_mission'}))

    def _provision(self,kind):
        if not self._allowed_provision or (self._ogs_running if kind=='ogs' else self._mission_running):return
        if kind=='ogs' and not self._ogs_available:return
        reference=vault_reference(self.credential.text() if kind=='ogs' else self.mission_credential.text())
        source='ogs_gateway' if kind=='ogs' else self.mission_source.text().strip()
        if not reference or not source:return
        self.command.emit('provision_satellite_session',json.dumps({'source_id':source,'session_reference':reference[6:],'ttl_seconds':3600}))

    def capture(self):
        data={'optical_session':self._metrics,'mission_rf':self._mission_ingress} if self._mission_ingress.get('latest') else self._metrics
        self.evidence.setPlainText(json.dumps(data,indent=2,ensure_ascii=False))
