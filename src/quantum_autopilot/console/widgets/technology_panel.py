"""Operator inspection of optional protocols, using backend readback only."""
from __future__ import annotations

import json
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QHeaderView, QLabel,
                              QSpinBox, QTabWidget, QTableWidget, QTableWidgetItem, QWidget)
from .adaptive_dialog import AdaptiveDialog
from .evidence_text import EvidenceText, ScrollableTextEdit
from .prims import Btn, hbox, vbox


def _records(value):
    if isinstance(value,dict):
        return [(str(key),item) for key,item in value.items() if isinstance(item,dict)]
    return [(str(item.get('id',item.get('demand_id',item.get('component',item.get('node_id',index))))),item)
            for index,item in enumerate(value or []) if isinstance(item,dict)]


def _show(value):
    if value is None:return '—'
    if isinstance(value,(dict,list,tuple)):return json.dumps(value,ensure_ascii=False,default=str)
    return str(value)


def _resource_summary(component):
    allocation = component.get('simulation_resource_allocation', {})
    totals = allocation.get('totals', {})
    if not totals:
        return 'Não declarado'
    return (f"{totals.get('resource_bank_count', 0)} bancos dedicados · "
        f"{totals.get('memory_modes', 0)} modos de memória · "
        f"{totals.get('sources', 0)} fontes · {totals.get('optical_terminals', 0)} terminais · hipótese simulada")


OPERATION_NAMES={'classical_control_probe':'Sonda de controle clássico','entanglement_pair':'Pares entre extremos',
                 'qkd_estimate':'QKD · estimativa condicional','optical_pair_measurement':'Medições de um trecho óptico',
                 'global_entanglement':'Entrelaçamento global · recursos de toda a rota'}


class GlobalOperationsView(QWidget):
    """Readback of demands and audit: no renderer starts communication itself."""
    def __init__(self,parent=None):
        super().__init__(parent);self._state={};self._ids={}
        layout=vbox(self,(0,0,0,0),6)
        self.summary=QLabel();self.summary.setWordWrap(True);layout.addWidget(self.summary)
        self.tabs=QTabWidget();layout.addWidget(self.tabs)
        self.demands=self._table('Demandas globais',('Demanda','Extremos','Operação','Estado','Causa','Rota','Protocolo / sessão','Recibo / evento'))
        self.components=self._table('Auditoria dos componentes',('Componente','Papéis / capacidades','Configuração','Enlaces','Demandas / participação','Estado','Impedimentos','Recursos dedicados da simulação'))
        page=QWidget();body=vbox(page,(8,8,8,8),6)
        self.capture=Btn('CAPTURAR REDE GLOBAL');self.capture.setToolTip('Captura o snapshot da auditoria e dos recibos; a leitura permanece estável enquanto os estados operacionais continuam atualizando.')
        self.capture.clicked.connect(self._capture);body.addWidget(self.capture)
        self.evidence=EvidenceText();self.evidence.setMinimumHeight(180);body.addWidget(self.evidence,1)
        self.tabs.addTab(page,'Evidência capturada')

    def _table(self,label,columns):
        table=QTableWidget(0,len(columns));table.setHorizontalHeaderLabels(columns)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setWordWrap(False);table.setMinimumHeight(210)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        table.horizontalHeader().setStretchLastSection(True)
        for col in range(len(columns)):table.setColumnWidth(col,140 if col<4 else 220)
        self.tabs.addTab(table,label);return table

    def _update_table(self,table,records,values):
        ids=tuple(key for key,_ in records);selected=table.currentRow()
        old=self._ids.get(table,());selected_id=old[selected] if 0<=selected<len(old) else None
        table.blockSignals(True)
        if old!=ids:table.setRowCount(len(ids));self._ids[table]=ids
        for row,(ident,record) in enumerate(records):
            tooltip=json.dumps(record,ensure_ascii=False,default=str)
            for col,text in enumerate(values(ident,record)):
                text=_show(text);item=table.item(row,col)
                if item is None:item=QTableWidgetItem();table.setItem(row,col,item)
                if item.text()!=text:item.setText(text)
                if item.toolTip()!=tooltip:item.setToolTip(tooltip)
        if selected_id in ids and old!=ids:table.selectRow(ids.index(selected_id))
        table.blockSignals(False)

    def set_state(self,state):
        self._state=state or {}
        demands=_records(self._state.get('demands',[]));components=_records(self._state.get('components',[]))
        self.summary.setText(f"{len(demands)} demandas · {len(components)} componentes auditados · tempo {self._state.get('clock_s','—')} s\n"
            'Controle clássico confirma mensagens e recibos; cliques de um enlace quântico confirmam somente esse trecho. '
            f"Entrelaçamento entre extremos exige os recursos, operações e confirmações de toda a rota. Bits de chave certificados: {self._state.get('certified_secret_bits',0)}.")
        self._update_table(self.demands,demands,lambda ident,d:(ident,
            f"{d.get('a',d.get('source','—'))} ↔ {d.get('b',d.get('destination','—'))}",OPERATION_NAMES.get(d.get('operation'),d.get('operation')),d.get('state',d.get('status')),
            d.get('reason',d.get('cause')), ' → '.join(d.get('path',d.get('route',[]))), d.get('protocol',d.get('session_id')),
            d.get('last_receipt_id',d.get('last_event',d.get('last_receipt',d.get('receipt_ids',[])[-1] if d.get('receipt_ids') else None)))))
        self._update_table(self.components,components,lambda ident,c:(ident,
            c.get('capabilities',c.get('roles')),c.get('configuration',c.get('configured')),c.get('links',c.get('link_ids')),
            c.get('routes',c.get('demands',c.get('participation'))),c.get('operation_state',c.get('status')),c.get('impediments',c.get('reason')),
            _resource_summary(c)))

    def _capture(self):
        self.evidence.setPlainText(json.dumps(self._state,indent=2,ensure_ascii=False,default=str))


class TechnologyDialog(AdaptiveDialog):
    command = Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('OrionQ · Protocolos e dispositivos modulares')
        self.resize(1060, 800)
        self.setMinimumSize(460, 400)
        self._snapshot = None
        self._selected = self._ids = self._edit_key = None
        self._satellite_dialog=None
        layout = vbox(self, (16,16,16,16), 10)
        self.summary = QLabel(); self.summary.setWordWrap(True); layout.addWidget(self.summary)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(('Sessão','Tecnologias','Estado','Pares/s g/d','BSM','Rendimento','Cond. bit/s*'))
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setMinimumHeight(190)
        self.table.itemSelectionChanged.connect(self._select)
        layout.addWidget(self.table)
        self.global_view=GlobalOperationsView();layout.addWidget(self.global_view)
        self.global_view.hide()
        controls = QWidget(); row = hbox(controls, (0,0,0,0), 8)
        self.enabled = QCheckBox('Sessão habilitada')
        self.scenario = QComboBox(); self.scenario.addItems(('nominal','degraded','unavailable','recovery'))
        self.scenario.setToolTip('Condição aplicada à origem: nominal, perdas/ruído, indisponibilidade ou recuperação periódica.')
        self.seed = QSpinBox(); self.seed.setRange(0, 2147483647)
        self.seed.setToolTip('Semente reproduzível da sessão. Alterar descarta os recursos da configuração anterior.')
        self.apply = Btn('APLICAR'); self.apply.clicked.connect(self._apply)
        self.apply.setToolTip('Valida e persiste parâmetros; reinicia a aquisição. Nenhuma atuação física.')
        for widget in (self.enabled, self.scenario, self.seed, self.apply): row.addWidget(widget)
        layout.addWidget(controls)
        layout.addWidget(QLabel('Parâmetros operacionais (JSON; alterações só valem após confirmação do backend)'))
        self.parameters = ScrollableTextEdit(); self.parameters.setMinimumHeight(140)
        layout.addWidget(self.parameters)
        self.capture = Btn('CAPTURAR EVIDÊNCIA'); self.capture.clicked.connect(self._capture)
        self.capture.setToolTip('Captura configuração, capacidades, métricas e eventos atuais. A leitura permanece estável enquanto a rede avança.')
        layout.addWidget(self.capture)
        self.satellite=Btn('SATÉLITE · HOUSEKEEPING / SCIENCE / ENTRADA OGS')
        self.satellite.clicked.connect(self._satellite)
        self.satellite.setToolTip('Inspeciona órbita, APT, orçamento óptico, cliques e pós-processamento; configura a recepção OGS autenticada da sessão selecionada.')
        layout.addWidget(self.satellite)
        self.evidence = EvidenceText(); self.evidence.setMinimumHeight(220)
        layout.addWidget(self.evidence, 1)
        footer = QWidget(); row = hbox(footer, (0,0,0,0), 8)
        self.simulated = Btn('FONTE SIMULADA'); self.simulated.clicked.connect(lambda: self._emit('configure_technology_source', {'kind':'simulated'}))
        self.measurements = Btn('ARQUIVO JSONL'); self.measurements.clicked.connect(self._file)
        self.stop_source = Btn('SEM FONTE'); self.stop_source.clicked.connect(lambda: self._emit('configure_technology_source', {'kind':'none'}))
        self.export = Btn('EXPORTAR RECIBOS'); self.export.clicked.connect(self._export)
        close = Btn('FECHAR'); close.clicked.connect(self.close)
        for widget in (self.simulated,self.measurements,self.stop_source,self.export,close): row.addWidget(widget)
        self.measurements.setToolTip('Leitura de medições no contrato de aquisição v1. Identificador da fonte: measurement_file. Não ativa hardware.')
        self.simulated.setToolTip('Substitui somente a origem; mantém aquisição, validação, sincronização, protocolo e canais.')
        self.stop_source.setToolTip('Interrompe a aquisição e descarta recursos utilizáveis da fonte anterior.')
        self.export.setToolTip('Exporta recibos persistidos, com sequência e hashes, incluindo as aquisições que deram origem às métricas.')
        layout.addWidget(footer); self.fixed_footer = (footer,)

    def _data(self):
        live = self._snapshot.live if self._snapshot else None
        return live.technologies if live else {}

    def set_snapshot(self, snap):
        self._snapshot = snap
        data = self._data(); sessions = data.get('sessions', {})
        ids = tuple(sessions)
        selected = self._selected
        self.table.blockSignals(True)
        if self._ids != ids:
            self.table.setRowCount(len(ids)); self._ids = ids
        for row, ident in enumerate(ids):
            session = sessions[ident]; metrics = session['metrics']
            abbreviations={'entanglement_swapping':'Swap','quantum_memory':'Mem','purification':'Purif',
                           'tf_qkd':'TF-QKD','satellite_doppler':'Sat/Doppler','satellite_isl':'ISL','orbital_relay':'Relay orbital'}
            generated = metrics.get('pair_rate_hz',0) if {'satellite_doppler','satellite_isl'}.intersection(session['config']['technologies']) else metrics.get('generation_pair_rate_hz',0)
            values = (ident, ' · '.join(abbreviations.get(x,x) for x in session['config']['technologies']), session['status'],
                      f"{generated:.2g}/{metrics.get('delivered_pair_rate_hz',0):.2g}",
                      f"{metrics.get('bsm_success_fraction',0):.1%}",
                      f"{metrics.get('purification_yield',0):.1%}",
                      f"{metrics.get('conditional_key_estimate_bps',0):.3g}")
            for col, text in enumerate(values):
                item = self.table.item(row,col)
                if item is None: item = QTableWidgetItem(); self.table.setItem(row,col,item)
                if item.text() != text: item.setText(text)
                item.setToolTip(', '.join(session['config']['technologies'])+'\n'+session.get('reason','')+
                                f"\nMemória: {metrics.get('memory_occupancy',0)} pares; idades: {metrics.get('memory_ages_s',[])} s")
        self.table.blockSignals(False)
        if ids and selected not in ids: self.table.selectRow(0)
        self._select()
        session=sessions.get(self._selected,{})
        self.satellite.setEnabled(bool({'satellite_doppler','satellite_isl'}.intersection(session.get('config',{}).get('technologies',[]))))
        global_state=data.get('global_operations',{})
        self.global_view.setVisible(bool(global_state));self.global_view.set_state(global_state)
        if self._satellite_dialog is not None and self._satellite_dialog.isVisible():self._update_satellite()
        self.summary.setText(f"Fonte: {data.get('origin','unavailable')} · {data.get('source_id','')} | "
                             f"Tempo: {data.get('clock_s',0):.3f} s | {data.get('acceleration_label','')}\n"
                             'Equipamento proposto para simulação; presença institucional não representa conexão física. '
                             'Nenhum adaptador de instrumento validado. *Taxa condicional NÃO CERTIFICADA; chaves certificadas: 0. '
                             'g/d = pares gerados/entregues. Evidências capturadas não mudam a cada tick.')
        for button, verb in ((self.apply,'configure_technology'),(self.simulated,'configure_technology_source'),
                             (self.measurements,'configure_technology_source'),(self.stop_source,'configure_technology_source'),
                             (self.export,'export_technology_events')):
            button.setEnabled(snap.capabilities.allows(verb) and (verb!='configure_technology' or bool(self._selected)))

    def _select(self):
        row = self.table.currentRow()
        if self._ids is None or not 0 <= row < len(self._ids): return
        ident = self._ids[row]; session = self._data()['sessions'][ident]
        key = (ident, json.dumps(session['config'],sort_keys=True))
        self._selected = ident
        if key != self._edit_key:
            self._edit_key = key; config = session['config']
            self.enabled.setChecked(config['enabled']); self.scenario.setCurrentText(config['scenario'])
            self.seed.setValue(min(config['seed'],2147483647))
            self.parameters.setPlainText(json.dumps(config['parameters'],indent=2,ensure_ascii=False))
            self._capture()

    def _capture(self):
        data = self._data(); session = data.get('sessions',{}).get(self._selected)
        if not session: return
        topology = self._snapshot.topology
        node_ids = session['config']['path']
        nodes = [node.__dict__ for node in topology.nodes if node.id in node_ids]
        detail = {'captured_clock_s':data['clock_s'], 'origin':data['origin'], 'source_id':data['source_id'],
                  'session':session, 'nodes':nodes, 'registry':data['registry'],
                  'parameter_sources':data.get('parameter_sources',{}),
                  'events':[r for r in data['events'] if r['session_id']==self._selected]}
        self.evidence.setPlainText(json.dumps(detail,indent=2,ensure_ascii=False,default=str))

    def _emit(self, verb, payload):
        self.command.emit(verb, json.dumps(payload,ensure_ascii=False))

    def _satellite(self):
        if self._satellite_dialog is None:
            from .satellite_telemetry import SatelliteTelemetryDialog
            self._satellite_dialog=SatelliteTelemetryDialog(self)
            self._satellite_dialog.command.connect(self.command.emit)
        self._update_satellite()
        self._satellite_dialog.show();self._satellite_dialog.raise_()

    def _update_satellite(self):
        data=self._data();session=data.get('sessions',{}).get(self._selected,{})
        path=session.get('config',{}).get('path',())
        satellites=(self._snapshot.live.satellites or {}) if self._snapshot.live is not None else {}
        satellite_id=next((item for item in path if item in satellites),'')
        self._satellite_dialog.set_metrics(session.get('metrics',{}),session_id=self._selected or '',
            clock_s=data.get('clock_s'),ingress=data.get('ogs_ingress',{}),
            mission_ingress=data.get('satellite_mission_ingress'),satellite_id=satellite_id,
            allowed_provision=self._snapshot.capabilities.allows('provision_satellite_session'),
            allowed=self._snapshot.capabilities.allows('configure_technology_source') and
                    'satellite_doppler' in session.get('config',{}).get('technologies',[]))

    def _apply(self):
        try:
            params = json.loads(self.parameters.toPlainText())
            if not isinstance(params,dict): raise ValueError('Os parâmetros devem ser um objeto JSON.')
        except (ValueError,TypeError) as exc:
            self.evidence.setPlainText(str(exc)); return
        self._emit('configure_technology',{'session_id':self._selected, 'parameters':params,
                   'enabled':self.enabled.isChecked(),'scenario':self.scenario.currentText(),'seed':self.seed.value()})

    def _file(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Medições operacionais', '', 'JSONL (*.jsonl)')
        if path: self._emit('configure_technology_source', {'kind':'measurement_file','path':path})

    def _export(self):
        path, _ = QFileDialog.getSaveFileName(self, 'Recibos dos protocolos', 'technology-receipts.jsonl','JSONL (*.jsonl)')
        if path: self._emit('export_technology_events',{'path':path})


class TechnologyPanel(QWidget):
    command = Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        row = hbox(self,(0,0,0,0),0); self.button = Btn('PROTOCOLOS'); row.addWidget(self.button)
        self.button.setToolTip('Inspecionar tecnologias, canais, recursos, fontes, falhas e recibos; configurar cenários no backend.')
        self.button.clicked.connect(self.open)
        self.dialog = None; self._snapshot = None

    def set_snapshot(self, snap):
        self._snapshot = snap
        available = bool(snap.live and (snap.live.technologies.get('sessions') or snap.live.technologies.get('global_operations')))
        self.setVisible(available)
        self.button.setEnabled(available)
        if not available and self.dialog is not None:
            self.shutdown()
        if self.dialog is not None: self.dialog.set_snapshot(snap)

    def open(self):
        if self.dialog is None:
            self.dialog = TechnologyDialog(self); self.dialog.command.connect(self.command.emit)
        self.dialog.set_snapshot(self._snapshot)
        selection = getattr(self.parent(),'_selection',None)
        if selection and selection[0]=='node':
            sessions=self.dialog._data().get('sessions',{})
            ident=next((key for key,s in sessions.items() if selection[1] in s['config']['path']),None)
            if ident is not None:self.dialog.table.selectRow(self.dialog._ids.index(ident))
        self.dialog.show(); self.dialog.raise_(); self.dialog.activateWindow()

    def shutdown(self):
        if self.dialog is not None:
            if self.dialog._satellite_dialog is not None:self.dialog._satellite_dialog.close()
            self.dialog.close(); self.dialog.deleteLater(); self.dialog = None

    def command_failed(self, message):
        if self.dialog is not None: self.dialog.evidence.setPlainText(f'Comando recusado pelo backend: {message}')
