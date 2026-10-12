"""Device registration and paired-channel sessions, confirmed by backend readback."""
from __future__ import annotations

import json
from contextlib import contextmanager

from PySide6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QGroupBox, QHeaderView,
    QLabel, QLineEdit, QScrollArea, QSplitter, QTabWidget, QTableWidget, QTableWidgetItem, QWidget)
from PySide6.QtCore import Qt

from ...control_plane.communicators import (Communicator, FIELDS, TYPES, ROLES, TECHNOLOGIES,
    TYPE_TECHNOLOGIES, TECH_PROTOCOL, RF_ONLY_PAYLOAD, active_orbit_fields, defaults, required_capabilities)
from ...control_plane.channels import local_pair
from ...quantum_protocols.contracts import ProtocolConfig
from ..widgets.prims import Btn, hbox, vbox
from ..widgets.evidence_text import EvidenceText
from .base import Screen

TYPE_NAMES=dict(zip(TYPES,('Instalação terrestre','Repetidor quântico','Estação de medição','Estação terrestre óptica',
    'Satélite','Dispositivo de memória quântica','Serviço de purificação')))
ROLE_NAMES=dict(zip(ROLES,('Endpoint','Core','Repetidor','Medição central','Estação terrestre','Serviço','Intermediário')))
TECH_NAMES=dict(zip(TECHNOLOGIES,('Troca de entrelaçamento','Memória quântica','Purificação','TF-QKD','Enlace satélite–solo / Doppler','Enlace satélite–satélite / ISL','Relay orbital com memórias')))
DEFAULT_ROLES={'installation':'endpoint','quantum_repeater':'repeater','measurement_station':'measurement',
    'ground_station':'ground_station','satellite':'intermediate','quantum_memory':'service','purification_service':'service'}


class CommunicatorsScreen(Screen):
    key='communicators';title='COMUNICADORES';heading='Comunicadores'
    status='Cadastro persistente · atuação física bloqueada'

    def __init__(self,parent=None):
        super().__init__(parent);self.add_header()
        self.records={};self._record=None;self.pending=None;self.fields={};self.group_fields={}
        self._loading=False;self._adjusting_type=False;self._auto_techs=set();self._manual_techs=set()
        self._managed_protocols=set();self._managed_caps=set();self._manual_role=False;self._manual_tf_role=False;self._pending_save=None
        self._integration_mode=None;self._integration_drafts={};self._rf_draft=None
        self.tabs=QTabWidget();self.root.addWidget(self.tabs,1)
        devices=QWidget();layout=vbox(devices,(12,12,12,12),8);self.tabs.addTab(devices,'Dispositivos')
        self.table=QTableWidget(0,7);self.table.setHorizontalHeaderLabels(('Identificador','Nome','Tipo físico','Função','Origem','Estado','Revisão'))
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers);self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents);self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setMaximumHeight(210);self.table.itemSelectionChanged.connect(self._selected);layout.addWidget(self.table)
        buttons=QWidget();row=hbox(buttons,(0,0,0,0),8)
        self.new=Btn('NOVO');self.new.clicked.connect(self.new_record)
        self.reload=Btn('RECARREGAR');self.reload.clicked.connect(self.reload_record)
        self.save=Btn('SALVAR CADASTRO');self.save.clicked.connect(self.submit)
        self.save.setProperty('operation','save_communicator')
        self.geo=Btn('VER NO GEO');self.geo.clicked.connect(self.focus_record)
        for button in (self.new,self.reload,self.save,self.geo):row.addWidget(button)
        layout.addWidget(buttons)
        self.message=QLabel('Campos * obrigatórios. Cadastro físico não ativa instrumentos. Identidade interna preserva os vínculos da topologia.');self.message.setWordWrap(True);layout.addWidget(self.message)
        self.editor_tabs=QTabWidget();layout.addWidget(self.editor_tabs,1)
        self.general=self._page('Dados gerais');form=QFormLayout(self.general)
        for key,label in (('device_id','Identificador público *'),('node_id','Identidade na topologia *'),('name','Nome *'),('institution','Instituição'),('manufacturer_model','Fabricante / modelo'),('category','Categoria / cenário'),('description','Descrição')):
            widget=QLineEdit();form.addRow(label,widget);self.fields[key]=widget
        for key,label,values in (('physical_type','Tipo físico *',TYPE_NAMES),('network_role','Função na rede *',ROLE_NAMES),
            ('operational_state','Estado operacional *',{'enabled':'Habilitado','maintenance':'Manutenção','disabled':'Desabilitado'})):
            widget=QComboBox()
            for value,text in values.items():widget.addItem(text,value)
            self.fields[key]=widget;form.addRow(label,widget)
        self.fields['physical_type'].currentIndexChanged.connect(self._type_change)
        self.fields['network_role'].currentIndexChanged.connect(self._role_changed)
        techbox=QWidget();techlay=vbox(techbox,(0,0,0,0),3);self.techs={}
        for tech in TECHNOLOGIES:
            checkbox=QCheckBox(TECH_NAMES[tech]);checkbox.toggled.connect(lambda checked,t=tech:self._technology_changed(t,checked));techlay.addWidget(checkbox);self.techs[tech]=checkbox
        form.addRow('Tecnologias disponíveis',techbox)
        self.rf_only=QCheckBox('Somente telemetria clássica RF · sem carga útil quântica')
        self.rf_only.setToolTip('Satélite com CSP1 e missão identificada. Não exige fonte de fótons, detectores ou uma conexão quântica; a órbita continua obrigatória.')
        self.rf_only.toggled.connect(self._rf_only_changed);form.addRow(self.rf_only)
        self.mission_enabled=QCheckBox('Cadastrar telemetria de missão / CSP1')
        self.mission_enabled.setToolTip('Identifica missão, catálogo NORAD e perfil de decoder. A disponibilidade e a qualificação do decoder são verificadas pelo receptor, não presumidas pelo cadastro.')
        self.mission_enabled.toggled.connect(self._mission_changed);form.addRow(self.mission_enabled)
        self.fields['protocols']=QLineEdit();form.addRow('Protocolos (associados às tecnologias)',self.fields['protocols'])
        self.fields['capabilities']=QLineEdit();form.addRow('Capacidades (associadas às tecnologias)',self.fields['capabilities'])
        self.auto=Btn('ASSOCIAR PROTOCOLOS E CAPACIDADES');self.auto.clicked.connect(self.associate);form.addRow(self.auto)
        self.where=self._page('Localização / integração');form=QFormLayout(self.where);self._where_form=form
        for key,label in (('latitude','Latitude · graus [-90,90]'),('longitude','Longitude · graus [-180,180]'),('altitude_m','Altitude terrestre · m [-500,10000]')):
            widget=QLineEdit();form.addRow(label,widget);self.fields[key]=widget
        mode=QComboBox()
        for label,value in (('Simulado','simulated'),('Arquivo de medições','measurement_file'),('OGS WebSocket · somente recepção','measurement_stream'),('Físico · driver não validado','physical')):mode.addItem(label,value)
        self.fields['mode']=mode;form.addRow('Modo de integração *',mode);mode.currentIndexChanged.connect(self._integration)
        for key,label in (('adapter','Adaptador *'),('interface','Interface *'),('service_address','Arquivo / endereço do serviço'),('credential_ref','Referência segura (vault:, env:, keyring:)'),('data_origin','Origem dos dados *')):
            widget=QLineEdit();form.addRow(label,widget);self.fields[key]=widget
        self.fields['adapter'].setReadOnly(True);self.fields['data_origin'].setReadOnly(True)
        self.mission_stream=QCheckBox('Recepção de quadros de missão RF · separada da entrada OGS')
        self.mission_stream.toggled.connect(self._integration);form.addRow(self.mission_stream)
        self.integration_note=QLabel();self.integration_note.setWordWrap(True);form.addRow(self.integration_note)
        self.group_pages={}
        for group in FIELDS:
            page=self._page('Missão / RF' if group=='mission' else 'Órbita' if group=='orbit' else 'Óptica' if group=='optical' else TECH_NAMES[group])
            groupform=QFormLayout(page);self.group_pages[group]=page;self.group_fields[group]={}
            for key,spec in FIELDS[group].items():
                label,kind,default,low,high,required=spec
                if kind=='bool':widget=QCheckBox()
                elif kind=='choice':
                    widget=QComboBox();widget.addItems(list(low))
                else:widget=QLineEdit()
                widget.setToolTip(f'{label}; obrigatório={required}'+(f'; intervalo [{low}, {high}]' if kind in ('number','int') else ''))
                widget.setAccessibleName(label);groupform.addRow(label+(' *' if required else ''),widget);self.group_fields[group][key]=widget
        self.group_fields['mission']['mission_id'].setPlaceholderText('ex.: suomi100; o registro de decoders é extensível')
        self.group_fields['mission']['norad_catalog_id'].setPlaceholderText('ex.: 43804; não substitui efemérides')
        self.group_fields['mission']['decoder_profile'].setPlaceholderText('perfil explicitamente suportado pelo receptor')
        self.group_fields['orbit']['model'].currentIndexChanged.connect(self._orbit_fields)
        self.group_fields['tf_qkd']['device_role'].currentIndexChanged.connect(self._tf_role_changed)
        self.connections=QWidget();self.tabs.addTab(self.connections,'Conexões / ativação');self._connection_page()
        evidencepage=QWidget();el=vbox(evidencepage,(12,12,12,12),8);self.tabs.addTab(evidencepage,'Consulta / evidências')
        capture=Btn('CAPTURAR CADASTRO E ESTADO');capture.clicked.connect(self.capture);el.addWidget(capture)
        self.evidence=EvidenceText();el.addWidget(self.evidence,1)
        self.new_record()

    def _page(self,label):
        scroll=QScrollArea();scroll.setWidgetResizable(True);page=QWidget();scroll.setWidget(page)
        self.editor_tabs.addTab(scroll,label);page._tab_index=self.editor_tabs.count()-1
        return page

    def _dynamic(self):
        if self._loading or not hasattr(self,'group_pages'):return
        satellite=self.fields['physical_type'].currentData()=='satellite'
        self.rf_only.setVisible(satellite);self.mission_enabled.setVisible(satellite)
        self.mission_enabled.setEnabled(not self.rf_only.isChecked())
        for checkbox in self.techs.values():checkbox.setEnabled(not (satellite and self.rf_only.isChecked()))
        for group,page in self.group_pages.items():
            visible=(not (satellite and self.rf_only.isChecked()) if group=='optical' else
                     satellite and self.mission_enabled.isChecked() if group=='mission' else
                     satellite if group=='orbit' else self.techs[group].isChecked())
            self.editor_tabs.setTabVisible(page._tab_index,visible)
        for key in ('latitude','longitude','altitude_m'):self._where_form.setRowVisible(self.fields[key],not satellite)
        if not self.editor_tabs.isTabVisible(self.editor_tabs.currentIndex()):self.editor_tabs.setCurrentIndex(0)
        self._integration_fields()
        self._orbit_fields()

    def _orbit_fields(self):
        if self._loading or not hasattr(self,'group_fields') or 'orbit' not in self.group_fields:return
        fields=self.group_fields['orbit'];model=fields['model'].currentText()
        fields['optical_payload'].setReadOnly(self.rf_only.isChecked())
        geo=model=='geostationary_reference'
        fields['orbital_class'].setEnabled(not geo)
        if geo:
            from ...quantum_protocols.orbits import GEO_ALTITUDE_KM
            fields['orbital_class'].setCurrentText('GEO')
            for key,value in (('altitude_km',GEO_ALTITUDE_KM),('inclination_deg',0.),
                              ('eccentricity',0.),('reference_latitude',0.)):
                fields[key].setText(str(value))
            if fields['source'].text()=='hypothetical circular reference':
                fields['source'].setText('explicit simulated geostationary reference; no mission ephemeris')
        active=active_orbit_fields(model)
        form=self.group_pages['orbit'].layout()
        for key,widget in fields.items():
            form.setRowVisible(widget,key in active)
            label,_,_,_,_,required=FIELDS['orbit'][key]
            required=required or (key in ('tle_line1','tle_line2') and model=='sgp4_tle') or (key=='state_vectors' and model=='state_vectors')
            form.labelForField(widget).setText(label+(' *' if required else ''))

    @contextmanager
    def _hydrate(self):
        previous=self._loading;self._loading=True
        try:yield
        finally:self._loading=previous

    def _rf_only_changed(self,checked):
        if self._loading:return
        if checked:
            self._rf_draft={'technologies':{key:widget.isChecked() for key,widget in self.techs.items()},
                'auto':set(self._auto_techs),'manual':set(self._manual_techs),
                'payload':self.group_fields['orbit']['optical_payload'].text(),
                'mission':self.mission_enabled.isChecked(),'stream':self.mission_stream.isChecked()}
            with self._hydrate():
                for widget in self.techs.values():widget.setChecked(False)
                self.mission_enabled.setChecked(True);self.mission_stream.setChecked(True)
                self.group_fields['orbit']['optical_payload'].setText(RF_ONLY_PAYLOAD)
            self._auto_techs=set();self._manual_techs=set()
        else:
            draft=self._rf_draft
            with self._hydrate():
                for key,widget in self.techs.items():widget.setChecked(draft['technologies'][key] if draft else key in TYPE_TECHNOLOGIES['satellite'])
                self.mission_enabled.setChecked(draft['mission'] if draft else True)
                self.mission_stream.setChecked(draft['stream'] if draft else False)
                if self.group_fields['orbit']['optical_payload'].text()==RF_ONLY_PAYLOAD:
                    self.group_fields['orbit']['optical_payload'].setText(draft['payload'] if draft else defaults('orbit')['optical_payload'])
            self._auto_techs=set(draft['auto']) if draft else set(TYPE_TECHNOLOGIES['satellite'])
            self._manual_techs=set(draft['manual']) if draft else set()
            self._rf_draft=None
        self.associate();self._integration();self._dynamic()

    def _mission_changed(self):
        if self._loading:return
        if not self.mission_enabled.isChecked():
            with self._hydrate():self.mission_stream.setChecked(False)
        self.associate();self._integration();self._dynamic()

    def _role_changed(self):
        if not self._loading and not self._adjusting_type:
            self._manual_role=True
            if hasattr(self,'group_fields'):self.associate()

    def _technology_changed(self,tech,checked):
        if self._loading or self._adjusting_type:return
        if checked:self._manual_techs.add(tech)
        else:self._manual_techs.discard(tech)
        self.associate();self._dynamic()

    def _tf_role_changed(self):
        if not self._loading and not self._adjusting_type:self._manual_tf_role=True;self.associate()

    def _type_change(self):
        if self._loading or not hasattr(self,'techs'):return
        kind=self.fields['physical_type'].currentData();required=set(TYPE_TECHNOLOGIES[kind])
        if kind!='satellite':
            if self.rf_only.isChecked():self.rf_only.setChecked(False)
            with self._hydrate():self.mission_enabled.setChecked(False);self.mission_stream.setChecked(False)
        if kind=='satellite' and self.rf_only.isChecked():required=set()
        self._adjusting_type=True
        try:
            for tech in self._auto_techs-required-self._manual_techs:self.techs[tech].setChecked(False)
            for tech in required:self.techs[tech].setChecked(True)
            if not self._manual_role:self.fields['network_role'].setCurrentIndex(self.fields['network_role'].findData(DEFAULT_ROLES[kind]))
            if not self._manual_tf_role and 'tf_qkd' in self.group_fields:
                self.group_fields['tf_qkd']['device_role'].setCurrentText('central_measurement' if kind=='measurement_station' else 'sender')
        finally:self._adjusting_type=False
        self._auto_techs=required
        if 'protocols' in self.fields:self.associate()
        self._integration();self._dynamic()

    def _integration(self):
        if self._loading:return
        mode=self.fields['mode'].currentData()
        mission=(self.fields['physical_type'].currentData()=='satellite' and self.mission_enabled.isChecked() and self.mission_stream.isChecked())
        context=mode+':mission' if mode=='measurement_stream' and mission else mode
        if context!=self._integration_mode:
            if self._integration_mode is not None:
                self._integration_drafts[self._integration_mode]={k:self.fields[k].text() for k in ('interface','service_address','credential_ref')}
            draft=self._integration_drafts.get(context,{'interface':{'simulated':'local_queue','measurement_file':'jsonl_file',
                'measurement_stream':'websocket_mission_v1' if mission else 'websocket_ogs_v2','physical':'service_contract_unvalidated'}[mode],
                'service_address':'ws://127.0.0.1:8766/satellite/v1/frames' if mode=='measurement_stream' and mission else '',
                'credential_ref':('vault:orion-mission-session' if mission else 'vault:orion-ogs-session') if mode=='measurement_stream' else ''})
            for key,value in draft.items():self.fields[key].setText(value)
            self._integration_mode=context
        self.fields['adapter'].setText({'simulated':'local_simulator','measurement_file':'measurement_file','measurement_stream':'mission_gateway' if mission else 'ogs_read_only_v2','physical':'external_unvalidated'}[mode])
        self.fields['data_origin'].setText({'simulated':'simulated','measurement_file':'measured','measurement_stream':'physical','physical':'physical'}[mode])
        self._integration_fields()

    def _integration_fields(self):
        if not hasattr(self,'integration_note') or not hasattr(self,'techs'):return
        mode=self.fields['mode'].currentData();satellite_link=self.techs['satellite_doppler'].isChecked()
        mission=self.fields['physical_type'].currentData()=='satellite' and self.mission_enabled.isChecked()
        self.fields['mode'].model().item(self.fields['mode'].findData('measurement_stream')).setEnabled(satellite_link or mission)
        self.mission_stream.setVisible(mode=='measurement_stream' and mission)
        self.mission_stream.setEnabled(not self.rf_only.isChecked())
        self._where_form.setRowVisible(self.fields['service_address'],mode!='simulated')
        self._where_form.setRowVisible(self.fields['credential_ref'],mode in ('measurement_stream','physical'))
        self._where_form.labelForField(self.fields['service_address']).setText({'simulated':'Arquivo / endereço do serviço',
            'measurement_file':'Arquivo JSONL de medições *','measurement_stream':'Endpoint Missão / RF WebSocket local *' if mission and self.mission_stream.isChecked() else 'Endpoint OGS WebSocket local *',
            'physical':'Endereço do serviço (driver não validado)'}[mode])
        self._where_form.labelForField(self.fields['credential_ref']).setText('Referência vault: à sessão autorizada *' if mode=='measurement_stream' else 'Referência segura (vault:, env:, keyring:)')
        note={'simulated':'Fonte simulada local; cadastro não gera conexões nem resultados prontos.',
            'measurement_file':'Informe o arquivo de medições. Os dados passam pelo pipeline operacional do backend.',
            'measurement_stream':'Recepção de missão: bytes RF clássicos e dados RAW; não gera observações OGS/TDC/QKD. Endpoint local e vault: obrigatórios.' if mission and self.mission_stream.isChecked() else 'Recepção OGS clássica e eventos TDC; endpoint local e referência vault: à sessão autorizada obrigatórios.',
            'physical':'Cadastro descritivo; ativação física continua bloqueada até validação do adaptador.'}[mode]
        if mode=='measurement_stream' and not satellite_link and not (mission and self.mission_stream.isChecked()):note='Entrada OGS exige a tecnologia de enlace satélite–solo. Escolha uma fonte compatível com este dispositivo.'
        self.integration_note.setText(note)

    def _associations(self):
        techs=[t for t,c in self.techs.items() if c.isChecked()]
        tf_role=self.group_fields.get('tf_qkd',{}).get('device_role')
        config={'device_role':tf_role.currentText() if tf_role is not None else 'sender'}
        caps=list(dict.fromkeys(c for t in techs for c in required_capabilities(t,self.fields['physical_type'].currentData(),config)))
        role=self.fields['network_role'].currentData()
        if role in ('endpoint','core'):caps.extend(('endpoint_send','endpoint_receive'))
        if role in ('core','repeater','intermediate'):caps.append('classical_forward')
        protocols=list(dict.fromkeys(TECH_PROTOCOL[t] for t in techs))
        if self.fields['physical_type'].currentData()=='satellite' and self.mission_enabled.isChecked():
            caps.append('classical_telemetry');protocols.append('CSP1')
        return protocols,list(dict.fromkeys(caps))

    def associate(self):
        if self._loading:return
        protocols,caps=self._associations()
        for key,values,previous in (('protocols',protocols,self._managed_protocols),('capabilities',caps,self._managed_caps)):
            extras=[v for v in self._list(self.fields[key].text()) if v not in previous]
            self.fields[key].setText(', '.join(dict.fromkeys(extras+values)))
        self._managed_protocols=set(protocols);self._managed_caps=set(caps)

    @staticmethod
    def _list(value):return [x.strip() for x in value.split(',') if x.strip()]

    def _set_group(self,group,values):
        for key,widget in self.group_fields[group].items():
            kind=FIELDS[group][key][1];value=values.get(key,FIELDS[group][key][2])
            if kind=='bool':widget.setChecked(value)
            elif kind=='choice':widget.setCurrentText(value)
            else:widget.setText(', '.join(value) if kind=='list' else json.dumps(value) if kind=='json' else str(value))

    def _get_group(self,group):
        values=defaults(group) if group=='orbit' else {}
        if group=='orbit' and self.group_fields['orbit']['model'].currentText()=='geostationary_reference':
            from ...quantum_protocols.orbits import GEO_ALTITUDE_KM
            values.update(orbital_class='GEO',altitude_km=GEO_ALTITUDE_KM,
                          inclination_deg=0.,eccentricity=0.,reference_latitude=0.)
        active=active_orbit_fields(self.group_fields['orbit']['model'].currentText()) if group=='orbit' else FIELDS[group]
        for key,widget in self.group_fields[group].items():
            if key not in active:continue
            label,kind,_,_,_,required=FIELDS[group][key]
            if group=='orbit':required=required or key in ('tle_line1','tle_line2','state_vectors')
            if isinstance(widget,QLineEdit) and not widget.text().strip() and not required:
                values.pop(key,None);continue
            if kind=='bool':value=widget.isChecked()
            elif kind=='choice':value=widget.currentText()
            elif kind=='list':value=self._list(widget.text())
            elif kind in ('json','int','number'):
                try:value=json.loads(widget.text()) if kind=='json' else int(widget.text()) if kind=='int' else float(widget.text().strip().replace(',','.'))
                except ValueError as exc:raise ValueError(f'{label}: informe '+('JSON válido' if kind=='json' else 'um inteiro' if kind=='int' else 'um número válido')) from exc
            else:value=widget.text().strip()
            values[key]=value
        return values

    def new_record(self):
        self._record=None;self.pending=None;self._pending_save=None
        self._integration_mode=None;self._integration_drafts={};self._rf_draft=None
        with self._hydrate():
            self.rf_only.setChecked(False);self.mission_enabled.setChecked(False);self.mission_stream.setChecked(False)
            for widget in self.fields.values():
                if isinstance(widget,QLineEdit):widget.clear()
                else:widget.setCurrentIndex(0)
            for checkbox in self.techs.values():checkbox.setChecked(False)
            for group in FIELDS:self._set_group(group,defaults(group))
            self.table.blockSignals(True);self.table.clearSelection();self.table.setCurrentCell(-1,-1);self.table.blockSignals(False)
        self._auto_techs=set();self._manual_techs=set();self._managed_protocols=set();self._managed_caps=set();self._manual_role=False;self._manual_tf_role=False
        self.fields['manufacturer_model'].setText('proposed simulated device')
        self.fields['altitude_m'].setText('0');self._integration();self._dynamic();self.editor_tabs.setCurrentIndex(0)
        self.fields['node_id'].setReadOnly(False);self.fields['device_id'].setReadOnly(False)
        self.reload.setEnabled(False);self.geo.setEnabled(False);self._actions()
        self.message.setText('Novo comunicador. Campos * obrigatórios; coordenadas podem aguardar levantamento, mas são exigidas para ativar enlaces terrestres.')

    def load_record(self,record):
        self._record=record;self.pending=None;self._pending_save=None
        with self._hydrate():
            self.rf_only.setChecked(record['physical_type']=='satellite' and not record['technologies'])
            self.mission_enabled.setChecked(bool(record.get('mission')))
            self.mission_stream.setChecked(record['integration']['adapter']=='mission_gateway')
            for key,widget in self.fields.items():
                if key in record:
                    if isinstance(widget,QComboBox):widget.setCurrentIndex(max(0,widget.findData(record[key])))
                    elif key in ('protocols','capabilities'):widget.setText(', '.join(record[key]))
                    else:widget.setText(str(record[key]))
            for key in ('latitude','longitude','altitude_m'):self.fields[key].setText(str(record['location'].get(key,'')))
            for key,value in record['integration'].items():
                if key in self.fields:
                    widget=self.fields[key]
                    if isinstance(widget,QComboBox):widget.setCurrentIndex(max(0,widget.findData(value)))
                    else:widget.setText(value)
            for tech,checkbox in self.techs.items():checkbox.setChecked(tech in record['technologies'])
            self._set_group('optical',record['optical']);self._set_group('orbit',record['orbit']);self._set_group('mission',record.get('mission',{}))
            for tech in TECHNOLOGIES:self._set_group(tech,record['configuration'].get(tech,defaults(tech)))
        self._auto_techs=set() if self.rf_only.isChecked() else set(TYPE_TECHNOLOGIES[record['physical_type']]);self._manual_techs=set(record['technologies'])-self._auto_techs
        self._rf_draft=None
        self._integration_mode=record['integration']['mode']+(':mission' if record['integration']['adapter']=='mission_gateway' else '');self._integration_drafts={}
        protocols,caps=self._associations();self._managed_protocols=set(protocols);self._managed_caps=set(caps);self._manual_role=True;self._manual_tf_role=True
        self.fields['node_id'].setReadOnly(True);self.fields['device_id'].setReadOnly(True);self._dynamic()
        self.reload.setEnabled(True);self.geo.setEnabled(True);self._actions()
        self.message.setText(f"Cadastro carregado · revisão {record['revision']}. Edite e salve; ticks não alteram seu rascunho.")

    def payload(self):
        record={key:widget.currentData() if isinstance(widget,QComboBox) else widget.text().strip() for key,widget in self.fields.items() if key not in ('latitude','longitude','altitude_m','mode','adapter','interface','service_address','credential_ref','data_origin')}
        record['technologies']=[t for t,c in self.techs.items() if c.isChecked()]
        record['protocols']=self._list(self.fields['protocols'].text());record['capabilities']=self._list(self.fields['capabilities'].text())
        record['optical']={} if record['physical_type']=='satellite' and self.rf_only.isChecked() else self._get_group('optical')
        record['configuration']={t:self._get_group(t) for t in record['technologies']}
        record['orbit']=self._get_group('orbit') if record['physical_type']=='satellite' else {}
        record['mission']=self._get_group('mission') if record['physical_type']=='satellite' and self.mission_enabled.isChecked() else {}
        if record['orbit']:
            aliases={r['device_id']:node_id for node_id,r in self.records.items()}
            record['orbit']['compatible_stations']=[aliases.get(n,n) for n in record['orbit']['compatible_stations']]
        latitude=self.fields['latitude'].text().strip();longitude=self.fields['longitude'].text().strip()
        record['location']={}
        if record['physical_type']!='satellite' and (latitude or longitude):
            if not latitude or not longitude:raise ValueError('Localização: informe latitude e longitude; nenhuma coordenada ausente será preenchida com zero')
            try:record['location']={key:float(self.fields[key].text().strip().replace(',','.') or '0') for key in ('latitude','longitude','altitude_m')}
            except ValueError as exc:raise ValueError('Localização: informe coordenadas numéricas válidas') from exc
        record['integration']={key:widget.currentData() if isinstance(widget,QComboBox) else widget.text().strip() for key,widget in self.fields.items() if key in ('mode','adapter','interface','service_address','credential_ref','data_origin')}
        record['revision']=self._record['revision'] if self._record else 0
        return Communicator.from_dict(record).to_dict()

    def submit(self):
        if self.pending is not None:return
        try:record=self.payload()
        except (ValueError,TypeError,KeyError) as exc:self.message.setText(f'Cadastro recusado: {exc}');return
        if self._snap is None:self.message.setText('Cadastro aguardando uma fonte disponível');return
        if self._record is None and (record['node_id'] in self.records or any(r['device_id']==record['device_id'] for r in self.records.values())):
            self.message.setText('Cadastro recusado: identificador já cadastrado. Selecione o dispositivo existente para editar.');return
        expected=0 if self._record is None else self._record['revision']+1
        self._pending_save={'version':self._snap.topology.version,'record':{**record,'revision':expected}}
        self.pending=record['node_id'];self.message.setText('PENDENTE · aguardando confirmação do backend')
        self.fields['node_id'].setReadOnly(True);self.fields['device_id'].setReadOnly(True);self._actions()
        self.command.emit('save_communicator',json.dumps({'expected_version':self._snap.topology.version,'record':record},ensure_ascii=False))

    def _selected(self):
        item=self.table.item(self.table.currentRow(),0)
        if item and item.data(Qt.ItemDataRole.UserRole) in self.records:self.load_record(self.records[item.data(Qt.ItemDataRole.UserRole)])

    def reload_record(self):
        if self._record and self._record['node_id'] in self.records:self.load_record(self.records[self._record['node_id']])

    def focus_record(self):
        if self._record:self.select.emit('node',self._record['node_id']);self.navigate.emit('topology')

    def capture(self):
        if self._snap is None:return
        self.evidence.setPlainText(json.dumps({'records':self.records,'satellites':self._snap.live.satellites if self._snap.live else {},
            'sessions':self._snap.live.technologies if self._snap.live else {},'physical_actuation':False},ensure_ascii=False,indent=2))

    def refresh(self,snap):
        context=(snap.capabilities.adapter_id,snap.capabilities.mode,snap.topology.id,snap.input_kind,snap.campaign)
        if getattr(self,'_context',context)!=context:self.new_record();self._clear_connection();self.evidence.clear();self.connection_note.setText('Fonte alterada; recarregue o cadastro. Nenhum comando foi reenviado.')
        self._context=context
        records=snap.live.communicators if snap.live else {};self.records=records
        selection=self._record['node_id'] if self._record else None
        self.table.blockSignals(True);self.table.setRowCount(len(records))
        for row,(node_id,record) in enumerate(records.items()):
            values=(record['device_id'],record['name'],TYPE_NAMES[record['physical_type']],ROLE_NAMES[record['network_role']],record['integration']['data_origin'],record['operational_state'],str(record['revision']))
            for column,value in enumerate(values):
                item=self.table.item(row,column)
                if item is None:item=QTableWidgetItem();self.table.setItem(row,column,item)
                if item.text()!=value:item.setText(value)
                if column==0:item.setData(Qt.ItemDataRole.UserRole,node_id)
            if node_id==selection:self.table.selectRow(row)
        self.table.blockSignals(False)
        waiting=self._pending_save
        if waiting and self.pending in records and snap.topology.version>waiting['version']:
            actual=records[self.pending]
            if actual==waiting['record']:
                try:unchanged={**self.payload(),'revision':actual['revision']}==actual
                except (ValueError,TypeError,KeyError):unchanged=False
                if unchanged:self.load_record(actual)
                else:
                    self._record=actual;self.pending=None;self._pending_save=None;self.reload.setEnabled(True)
                self.message.setText('CONFIRMADO · cadastro persistido no backend'+(' · alterações posteriores continuam no rascunho' if not unchanged else ''))
            elif actual['revision']>=waiting['record']['revision']:
                self.pending=None;self._pending_save=None;self.message.setText('Cadastro alterado por outra operação. Rascunho preservado; recarregue para conferir a revisão.')
                self.fields['node_id'].setReadOnly(self._record is not None);self.fields['device_id'].setReadOnly(self._record is not None)
        self._actions()
        self.geo.setEnabled(self._record is not None)
        ids=tuple((snap.live.technologies.get('sessions',{}) if snap.live else {}).keys())
        if tuple(self.session.itemData(i) for i in range(1,self.session.count()))!=ids:
            self.session.blockSignals(True);selected=self.session.currentData();self.session.clear();self.session.addItem('Nova conexão','')
            for ident in ids:self.session.addItem(ident,ident)
            self.session.setCurrentIndex(max(0,self.session.findData(selected)));self.session.blockSignals(False)
        pending=getattr(self,'_pending_connection',None)
        if pending and snap.live:
            session=snap.live.technologies.get('sessions',{}).get(pending['config']['session_id'])
            if session and snap.topology.version>pending['version']:
                actual=session['config'];expected=pending['config']
                keys=('session_id','technologies','endpoints','path','link_ids','seed','scenario','enabled')
                if all(actual.get(key)==expected.get(key) for key in keys):
                    self.connection_note.setText(f"CONFIRMADO · {expected['session_id']} · estado {session['status']}: {session['reason']}");self._pending_connection=None
        self._actions()

    def _actions(self):
        if not hasattr(self,'connection_save'):return
        for button,verb,pending in ((self.save,'save_communicator',self.pending),
                (self.connection_save,'configure_communicator_connection',getattr(self,'_pending_connection',None))):
            button.setEnabled(self._snap is not None and self._snap.capabilities.allows(verb) and pending is None and not getattr(self,'commands_busy',False))

    def apply_command_capabilities(self):
        super().apply_command_capabilities();self._actions()

    def command_failed(self,message):
        self.pending=None;self._pending_save=None;self._pending_connection=None
        self.fields['node_id'].setReadOnly(self._record is not None);self.fields['device_id'].setReadOnly(self._record is not None);self._actions()
        self.message.setText(f'Backend recusou a operação: {message}');self.connection_note.setText(str(message))

    def _connection_page(self):
        layout=vbox(self.connections,(12,12,12,12),8);form=QFormLayout();layout.addLayout(form)
        self.session=QComboBox();self.session.addItem('Nova conexão','');self.session.currentIndexChanged.connect(self._load_connection);form.addRow('Consultar/editar sessão',self.session)
        self.session_id=QLineEdit();form.addRow('Identificador da sessão *',self.session_id)
        self.path=QLineEdit();self.path.setPlaceholderText('IDs públicos ou internos: estação A, satélite/centro/repetidor, estação B');form.addRow('Percurso ordenado *',self.path)
        self.connection_techs={};host=QWidget();row=hbox(host,(0,0,0,0),6)
        for tech in TECHNOLOGIES:
            widget=QCheckBox(TECH_NAMES[tech]);row.addWidget(widget);self.connection_techs[tech]=widget
        form.addRow('Tecnologias *',host)
        self.connection_seed=QLineEdit('42');form.addRow('Semente inteira ≥ 0 *',self.connection_seed)
        self.connection_scenario=QComboBox();self.connection_scenario.addItems(('nominal','degraded','unavailable','recovery'));form.addRow('Cenário *',self.connection_scenario)
        self.connection_enabled=QCheckBox('Habilitar sessão após validação dos dispositivos e dois canais');self.connection_enabled.setChecked(True);form.addRow(self.connection_enabled)
        prepare=Btn('PREPARAR SEGMENTOS');prepare.clicked.connect(self.prepare_segments);layout.addWidget(prepare)
        self.channel_table=QTableWidget(0,6);self.channel_table.setHorizontalHeaderLabels(('Segmento *','Canal clássico *','Canal quântico *','Receptor *','Idade máxima · s (0,30]','Mensagem · bytes [1024,8388608]'))
        self.channel_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents);layout.addWidget(self.channel_table,1)
        self.connection_save=Btn('VALIDAR E APLICAR CONEXÃO');self.connection_save.clicked.connect(self.submit_connection);layout.addWidget(self.connection_save)
        self.connection_save.setProperty('operation','configure_communicator_connection')
        self.connection_note=QLabel('Protocolos físicos sem driver validado serão recusados. Memória e purificação são serviços complementares. As identidades clássica e quântica são obrigatórias e distintas.');self.connection_note.setWordWrap(True);layout.addWidget(self.connection_note)

    def _load_connection(self):
        if not self.session.currentData():self._clear_connection();return
        if not self._snap or not self._snap.live:return
        session=self._snap.live.technologies.get('sessions',{}).get(self.session.currentData())
        if not session:return
        config=session['config'];self.session_id.setText(config['session_id']);self.path.setText(', '.join(self.records.get(n,{}).get('device_id',n) for n in config['path']))
        self.connection_seed.setText(str(config['seed']));self.connection_scenario.setCurrentText(config['scenario']);self.connection_enabled.setChecked(config['enabled'])
        for tech,widget in self.connection_techs.items():widget.setChecked(tech in config['technologies'])
        self.prepare_segments(config)

    def _clear_connection(self):
        self.session_id.clear();self.path.clear();self.connection_seed.setText('42')
        self.connection_scenario.setCurrentIndex(0);self.connection_enabled.setChecked(True)
        for checkbox in self.connection_techs.values():checkbox.setChecked(False)
        self.channel_table.setRowCount(0);self._pending_connection=None;self._actions()
        self.connection_note.setText('Nova conexão. Selecione tecnologias, informe o percurso e prepare os dois canais de cada segmento.')

    def prepare_segments(self,config=None):
        if not isinstance(config,dict):config=None
        path=self._path_ids();techs=[t for t,w in self.connection_techs.items() if w.isChecked()]
        self.channel_table.setRowCount(max(0,len(path)-1))
        for index,(a,b) in enumerate(zip(path,path[1:])):
            if 'tf_qkd' in techs and index==1:a,b=b,a
            if 'satellite_doppler' in techs and index==0:a,b=b,a
            ident=config['link_ids'][index] if config else f"TECH-{self.session_id.text().strip()}-{index+1:02d}"
            saved=next((l for l in self._snap.topology.links if l.id==ident),None) if self._snap else None
            pair=saved.channels if saved and saved.channels else local_pair(ident,b)
            for column,value in enumerate((ident,pair['classical']['channel_id'],pair['quantum']['channel_id'],b,str(pair['classical']['max_age_s']),str(pair['classical']['max_message_bytes']))):self.channel_table.setItem(index,column,QTableWidgetItem(value))

    def submit_connection(self):
        if not self._snap:return
        if getattr(self,'_pending_connection',None) is not None:return
        try:
            path=self._path_ids();techs=tuple(t for t,w in self.connection_techs.items() if w.isChecked())
            links=[]
            for row in range(self.channel_table.rowCount()):
                values=[self.channel_table.item(row,col).text().strip() for col in range(6)]
                pair=local_pair(values[0],values[3]);pair['classical'].update(channel_id=values[1],max_age_s=float(values[4]),max_message_bytes=int(values[5]))
                pair['quantum'].update(channel_id=values[2],protocol='quantum-operation-record-v1')
                links.append({'link_id':values[0],'channels':pair})
            if len(path)<2:raise ValueError('percurso com dois extremos obrigatório')
            config=ProtocolConfig(self.session_id.text().strip(),techs,(path[0],path[-1]),tuple(path),tuple(l['link_id'] for l in links),
                seed=int(self.connection_seed.text()),scenario=self.connection_scenario.currentText(),enabled=self.connection_enabled.isChecked(),
                parameters={'block_s':.5,'timeout_s':3.},infrastructure='operator_simulation_configuration')
        except (ValueError,TypeError,KeyError,AttributeError) as exc:self.connection_note.setText(f'Conexão recusada: {exc}');return
        self.connection_note.setText('PENDENTE · aguardando validação e readback do backend')
        self._pending_connection={'config':config.to_dict(),'version':self._snap.topology.version};self._actions()
        self.command.emit('configure_communicator_connection',json.dumps({'expected_version':self._snap.topology.version,'config':config.to_dict(),'links':links},ensure_ascii=False))

    def _path_ids(self):
        aliases={r['device_id']:node_id for node_id,r in self.records.items()}
        return [aliases.get(n,n) for n in self._list(self.path.text())]

    @staticmethod
    def _raw_value(widget):
        return widget.isChecked() if isinstance(widget,QCheckBox) else widget.currentIndex() if isinstance(widget,QComboBox) else widget.text()

    @staticmethod
    def _restore_raw(widget,value):
        if isinstance(widget,QCheckBox):widget.setChecked(value)
        elif isinstance(widget,QComboBox):widget.setCurrentIndex(value)
        else:widget.setText(value)

    def save_state(self):
        return {'tab':self.tabs.currentIndex(),'record':self._record,
                'rf_only':self.rf_only.isChecked(),'mission_enabled':self.mission_enabled.isChecked(),
                'mission_stream':self.mission_stream.isChecked(),
                'rf_draft':None if self._rf_draft is None else {**self._rf_draft,
                    'auto':sorted(self._rf_draft['auto']),'manual':sorted(self._rf_draft['manual'])},
                'fields':{k:self._raw_value(w) for k,w in self.fields.items()},
                'techs':{k:w.isChecked() for k,w in self.techs.items()},
                'groups':{g:{k:self._raw_value(w) for k,w in fields.items()} for g,fields in self.group_fields.items()},
                'connection':{'id':self.session_id.text(),'path':self.path.text(),'seed':self.connection_seed.text(),
                    'scenario':self.connection_scenario.currentIndex(),'enabled':self.connection_enabled.isChecked(),
                    'techs':{k:w.isChecked() for k,w in self.connection_techs.items()},
                    'channels':[[self.channel_table.item(row,col).text() for col in range(6)] for row in range(self.channel_table.rowCount())]},
                'editor_tab':self.editor_tabs.currentIndex(),
                'auto_techs':sorted(self._auto_techs),'manual_techs':sorted(self._manual_techs),
                'managed_protocols':sorted(self._managed_protocols),'managed_caps':sorted(self._managed_caps),
                'manual_role':self._manual_role,'manual_tf_role':self._manual_tf_role,'pending':self.pending,'pending_save':self._pending_save,
                'pending_connection':getattr(self,'_pending_connection',None),'integration_drafts':dict(self._integration_drafts)}

    def restore_state(self,state):
        if state.get('record'):self.load_record(state['record'])
        with self._hydrate():
            self.rf_only.setChecked(state.get('rf_only',self.rf_only.isChecked()))
            self.mission_enabled.setChecked(state.get('mission_enabled',self.mission_enabled.isChecked()))
            self.mission_stream.setChecked(state.get('mission_stream',self.mission_stream.isChecked()))
            for key,value in state.get('fields',{}).items():self._restore_raw(self.fields[key],value)
            for key,value in state.get('techs',{}).items():self.techs[key].setChecked(value)
            for group,values in state.get('groups',{}).items():
                for key,value in values.items():self._restore_raw(self.group_fields[group][key],value)
        connection=state.get('connection')
        if connection:
            self.session_id.setText(connection['id']);self.path.setText(connection['path']);self.connection_seed.setText(connection['seed'])
            self.connection_scenario.setCurrentIndex(connection['scenario']);self.connection_enabled.setChecked(connection['enabled'])
            for key,value in connection['techs'].items():self.connection_techs[key].setChecked(value)
            self.channel_table.setRowCount(len(connection['channels']))
            for row,values in enumerate(connection['channels']):
                for col,value in enumerate(values):self.channel_table.setItem(row,col,QTableWidgetItem(value))
        self._rf_draft=state.get('rf_draft')
        self._auto_techs=set(state.get('auto_techs',() if self.rf_only.isChecked() else TYPE_TECHNOLOGIES[self.fields['physical_type'].currentData()]))
        self._manual_techs=set(state.get('manual_techs',(t for t,w in self.techs.items() if w.isChecked() and t not in self._auto_techs)))
        protocols,caps=self._associations()
        self._managed_protocols=set(state.get('managed_protocols',protocols));self._managed_caps=set(state.get('managed_caps',caps))
        self._manual_role=state.get('manual_role',self._record is not None)
        self._manual_tf_role=state.get('manual_tf_role',self._record is not None)
        self._integration_mode=self.fields['mode'].currentData()+(':mission' if self.fields['mode'].currentData()=='measurement_stream' and self.mission_stream.isChecked() else '')
        self._integration_drafts=dict(state.get('integration_drafts',{}))
        self.pending=state.get('pending');self._pending_save=state.get('pending_save');self._pending_connection=state.get('pending_connection')
        self.fields['node_id'].setReadOnly(self._record is not None or self.pending is not None);self.fields['device_id'].setReadOnly(self._record is not None or self.pending is not None)
        self._dynamic();tab=state.get('editor_tab',0);self.editor_tabs.setCurrentIndex(tab if self.editor_tabs.isTabVisible(tab) else 0)
        self.tabs.setCurrentIndex(state.get('tab',0))
        self._actions()
