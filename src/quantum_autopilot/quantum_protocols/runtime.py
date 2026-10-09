"""Causal operational pipeline for optional protocols, independent of the UI.

Acquisition -> validation -> synchronization -> quantum protocol/resources ->
classical send -> classical receive -> derived metrics -> durable receipt -> UI.
Classical messages have propagation delay and jitter; no usable resource/result
is published before the associated herald/acceptance message reaches endpoints.
"""
from __future__ import annotations

import heapq
import hashlib
import json
import math
import zlib
from collections import Counter, deque
from contextlib import closing
from dataclasses import dataclass, field, replace

from ..control_plane.geodesy import node_location
from ..control_plane.sqlite_transaction import connect
from .contracts import DeviceCommand, ProtocolConfig, ProtocolError, canonical, digest, validate_binding
from .physics import C_KM_S, aged_fidelity
from .protocols import count
from .registry import default_registry
from .sources import NoAcquisitionSource


@dataclass
class PairResource:
    pair_id: str
    a: str
    b: str
    fidelity: float
    created_at_s: float
    available_at_s: float
    parents: tuple = ()
    purified: bool = False
    pauli_frame: tuple = (0,0)


@dataclass
class Session:
    config: ProtocolConfig
    revision: int = 0
    started_at_s: float = 0.
    command_number: int = 0
    last_sequence: int = 0
    sequence_streams: dict = field(default_factory=dict)
    pairs: list = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    counts: Counter = field(default_factory=Counter)
    status: str = 'configured'
    reason: str = 'no acquisition yet'
    operating: set = field(default_factory=set)
    pending: dict = field(default_factory=dict)
    delivered_fidelities: deque = field(default_factory=lambda: deque(maxlen=100))


class ProtocolRuntime:
    def __init__(self, journal_path, topology_id, *, registry=None, source=None, clock_rate=1.):
        if not 0.01 <= clock_rate <= 60.:
            raise ProtocolError('invalid_parameter','clock_rate must be in [0.01,60]')
        self.journal_path, self.topology_id = journal_path, topology_id
        self.registry = registry or default_registry()
        from pathlib import Path
        self.parameter_sources = json.loads((Path(__file__).parent/'data/sources_v1.json').read_text(encoding='utf-8'))
        self.source = source or NoAcquisitionSource()
        self.registry.register_source(self.source, replace=True)
        self.clock_rate = float(clock_rate)
        self.clock_s = 0.
        self.sessions = {}
        self.topology = None
        self.events = deque(maxlen=256)
        self._outbox = []
        self._raw_outbox = {}
        self._raw_store_used = None
        self._queue = []
        self._queue_index = 0
        self._fingerprint = None
        self._event_index = 0
        self._previous_hash = ''
        self._closed = False
        self.external_sources = {}
        with closing(connect(journal_path)) as conn:
            conn.executescript('''CREATE TABLE IF NOT EXISTS technology_events(
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,topology_id TEXT NOT NULL,session_id TEXT NOT NULL,
                simulated_at_s REAL NOT NULL,event_kind TEXT NOT NULL,payload_json TEXT NOT NULL,
                receipt_sha256 TEXT NOT NULL,previous_sha256 TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS technology_configuration(
                topology_id TEXT NOT NULL,session_id TEXT NOT NULL,config_json TEXT NOT NULL,
                PRIMARY KEY(topology_id,session_id));
                CREATE TABLE IF NOT EXISTS satellite_acquisitions(
                sha256 TEXT PRIMARY KEY,observation_zlib BLOB NOT NULL);''')
            row = conn.execute('SELECT receipt_sha256 FROM technology_events WHERE topology_id=? ORDER BY sequence DESC LIMIT 1',
                               (topology_id,)).fetchone()
            if row: self._previous_hash = row[0]

    def _event(self, session, at_s, kind, detail=None, command_id=None):
        self._event_index += 1
        descriptor=session.config.parameters.get('acquisition_source',{})
        external=self.external_sources.get(descriptor.get('source_id')) if descriptor.get('mode')=='measurement_stream' else None
        origin=external.origin if external else 'measurement_file' if descriptor.get('mode')=='measurement_file' else 'simulated' if descriptor.get('mode')=='simulated' else self.source.origin
        source_id=external.source_id if external else 'measurement_file' if descriptor.get('mode')=='measurement_file' else 'local_quantum_devices_v1' if descriptor.get('mode')=='simulated' else self.source.source_id
        body = {'schema':'orion.technology-event.v1','topology_id':self.topology_id,
                'session_id':session.config.session_id,'at_s':float(at_s),'event':kind,
                'command_id':command_id,'source_id':source_id,'origin':origin,
                'physical_actuation':False,'detail':detail or {},'previous_sha256':self._previous_hash}
        record = {**body,'receipt_sha256':digest(body)}
        self._previous_hash = record['receipt_sha256']
        self.events.append(record); self._outbox.append(record)
        return record

    def _flush(self):
        if not self._outbox and not self._raw_outbox: return
        # Raw evidence and referring receipts become durable in one transaction.
        # Keep both outboxes intact if a commit or quota check fails.
        rows=self._outbox
        with closing(connect(self.journal_path)) as conn, conn:
            if self._raw_outbox:
                if not conn.in_transaction:conn.execute('BEGIN IMMEDIATE')
                used=conn.execute('SELECT coalesce(sum(length(observation_zlib)),0) FROM satellite_acquisitions').fetchone()[0]
                pending=list(self._raw_outbox.items())
                placeholders=','.join('?' for _ in pending)
                existing={row[0] for row in conn.execute(
                    'SELECT sha256 FROM satellite_acquisitions WHERE sha256 IN ('+placeholders+')',
                    [sha for sha,_ in pending])}
                for sha,(blob,limit) in pending:
                    if sha not in existing:
                        if used+len(blob)>limit:
                            raise ProtocolError('storage_full','raw acquisition evidence quota reached; preserved journal requires export/archive')
                        used+=len(blob)
                conn.executemany('INSERT OR IGNORE INTO satellite_acquisitions VALUES(?,?)',
                    [(sha,blob) for sha,(blob,_) in pending])
            conn.executemany('INSERT INTO technology_events(topology_id,session_id,simulated_at_s,event_kind,payload_json,receipt_sha256,previous_sha256) VALUES(?,?,?,?,?,?,?)',
                [(self.topology_id,r['session_id'],r['at_s'],r['event'],canonical(r),r['receipt_sha256'],r['previous_sha256']) for r in rows])
        self._outbox=[];self._raw_outbox={};self._raw_store_used=None

    def _queue_raw_acquisition(self,session,sha,serialized):
        if sha in self._raw_outbox:return
        with closing(connect(self.journal_path)) as conn:
            if conn.execute('SELECT 1 FROM satellite_acquisitions WHERE sha256=?',(sha,)).fetchone():return
            if self._raw_store_used is None:
                self._raw_store_used=conn.execute('SELECT coalesce(sum(length(observation_zlib)),0) FROM satellite_acquisitions').fetchone()[0]
        # Existing native zlib, faster lossless profile. The content address is
        # over uncompressed bytes; quotas still count the actual stored bytes.
        level=session.config.parameters.get('raw_compression_level',1)
        if type(level) is not int or not 0<=level<=9:
            raise ProtocolError('invalid_parameter','raw_compression_level: integer [0,9] required')
        compressed=zlib.compress(serialized.encode('utf-8'),level)
        limit=session.config.parameters.get('raw_store_limit_bytes',2*1024**3)
        pending_bytes=sum(len(blob) for blob,_ in self._raw_outbox.values())
        if self._raw_store_used+pending_bytes+len(compressed)>limit:
            raise ProtocolError('storage_full','raw acquisition evidence quota reached; preserved journal requires export/archive')
        self._raw_outbox[sha]=(compressed,limit)

    def set_source(self, source):
        self.registry.register_source(source, replace=True)
        previous = self.source.source_id
        self.source = source
        self._paused_device_sessions=set(self.sessions) if source.origin=='unavailable' else set()
        self._queue.clear()
        for session in self.sessions.values():
            previous_counters = dict(session.counts)
            session.pairs.clear(); session.operating.clear(); session.pending.clear()
            session.last_sequence = 0; session.sequence_streams.clear(); session.revision += 1
            session.metrics.clear(); session.status='configured'; session.reason='waiting for new source acquisition'
            session.counts.clear(); session.delivered_fidelities.clear()
            session.started_at_s = self.clock_s
            self._event(session,self.clock_s,'source_replaced',{'previous_source_id':previous,'retained_usable_resources':0,
                        'previous_source_counters':previous_counters,'new_source_counters':{}})
            self._schedule(self.clock_s,session,'sample',None)
        self._flush()
        if self.topology is not None:
            self._fingerprint=None;self.sync(self.topology)

    def reset_node(self, node_id):
        """Invalidate consumed/stored states; re-acquire evidence, never set a fidelity."""
        for session in self.sessions.values():
            if node_id not in session.config.path:
                continue
            discarded = [p.pair_id for p in session.pairs]
            session.pairs.clear(); session.pending.clear(); session.operating.clear()
            session.revision += 1
            session.metrics.clear(); session.status = 'configured'
            session.reason = 'node reset; fresh acquisition required'
            self._event(session, self.clock_s, 'node_resources_reset',
                        {'node_id': node_id, 'discarded_pair_ids': discarded,
                         'scope': 'resource reset and reacquisition; no device calibration claim'})
            self._schedule(self.clock_s, session, 'sample', None)
        self._flush()

    def _configs(self, topology):
        configs = {}
        for node in topology.nodes:
            for raw in node.telemetry.get('technology_sessions',()):
                config = ProtocolConfig.from_dict(raw)
                if config.session_id in configs:
                    raise ProtocolError('duplicate_session',config.session_id)
                configs[config.session_id] = config
        with closing(connect(self.journal_path)) as conn:
            rows = conn.execute('SELECT session_id,config_json FROM technology_configuration WHERE topology_id=?',(self.topology_id,)).fetchall()
        for ident,raw in rows:
            # Operator overrides only an existing session; a removed node/link
            # never resurrects from an old saved configuration.
            if ident in configs:
                override=ProtocolConfig.from_dict(json.loads(raw))
                if override.parameters.get('communicator_configuration_version')==configs[ident].parameters.get('communicator_configuration_version'):
                    configs[ident]=override
        from ..control_plane.communicators import Communicator
        for ident,config in tuple(configs.items()):
            parameters=json.loads(canonical(config.parameters))
            devices=[]
            for node_id in config.path:
                raw=topology.node(node_id).telemetry.get('communicator')
                if not raw: continue
                record=Communicator.from_dict(raw)
                devices.append(record)
                # Received session assumptions survive migration. Edits and newly
                # registered devices become operational parameters after admission.
                if record.revision>0 or topology.node(node_id).telemetry.get('inventory',{}).get('origin')=='communicators-v1':
                    parameters.update(record.parameters(config.technologies))
                    parameters.setdefault('device_parameters',{})[node_id]=record.parameters(config.technologies)
                if record.physical_type=='satellite':
                    if 'satellite_isl' in config.technologies:
                        parameters.setdefault('satellite_orbits',{})[node_id]=record.orbit
                    else:
                        parameters.update(record.orbit)
                        parameters['orbit_record']=record.orbit
            if devices and not parameters.get('source_override') and any(r.revision>0 or r.integration['mode'] in ('measurement_file','measurement_stream') for r in devices):
                modes={r.integration['mode'] for r in devices}
                if modes=={'measurement_file'}:
                    parameters['acquisition_source']={'mode':'measurement_file','path':devices[0].integration['service_address']}
                elif modes=={'simulated'}:parameters['acquisition_source']={'mode':'simulated'}
                elif modes=={'measurement_stream'}:parameters['acquisition_source']={'mode':'measurement_stream','source_id':'ogs_gateway'}
            configs[ident]=replace(config,parameters=parameters)
        return configs

    def sync(self, topology):
        self.topology = topology
        fingerprint = digest(topology.to_dict())
        if fingerprint == self._fingerprint: return
        configs=self._configs(topology)
        for ident in set(self.sessions)-set(configs):
            session=self.sessions.pop(ident); session.revision+=1
            getattr(self,'_device_sources',{}).pop(ident,None)
            self._event(session,self.clock_s,'session_removed',{'resources_discarded':len(session.pairs)})
        for ident,config in configs.items():
            session=self.sessions.get(ident)
            if session is None or session.config != config:
                revision=0 if session is None else session.revision+1
                session=Session(config,revision=revision,started_at_s=self.clock_s); self.sessions[ident]=session
                self._event(session,self.clock_s,'configured',{'config':config.to_dict()})
                self._schedule(self.clock_s,session,'sample',None)
            try:
                self.registry.validate(config,topology)
                self._validate_parameters(config)
            except (ProtocolError,ValueError,KeyError) as exc:
                session.status='incompatible';session.reason=str(exc)
                session.pairs.clear();session.operating.clear()
                self._event(session,self.clock_s,'compatibility_rejected',{'reason':str(exc)})
            else:
                if session.status=='incompatible':
                    session.status='configured';session.reason='configuration now compatible'
                    self._schedule(self.clock_s,session,'sample',None)
        self._fingerprint=fingerprint
        self._flush()

    def _validate_parameters(self, config):
        from .contracts import number
        p=config.parameters
        if type(p.get('raw_compression_level',1)) is not int or not 0<=p.get('raw_compression_level',1)<=9:
            raise ProtocolError('invalid_parameter','raw_compression_level: integer [0,9] required')
        for name,default,low,high in [
            ('block_s',.1,.001,60.),('timeout_s',2.,.001,600.),('sync_tolerance_s',1e-6,0.,1.),
            ('classical_jitter_s',1e-5,0.,1.),('detector_efficiency',.8,0.,1.),
            ('dark_probability',1e-7,0.,.1),('min_fidelity',.7,0.,1.),
            ('attempt_rate_hz',100000.,1.,1e10),('pulse_rate_hz',1000000.,1.,1e10),
            ('memory_write_efficiency',.7,0.,1.),('coherence_s',1.,1e-6,1e6),
            ('operation_duration_s',1e-5,0.,1.),('ec_efficiency',1.16,1.,3.),
            ('verification_error',1e-10,1e-15,.1), ('phase_sigma_rad',.05,0.,10.),
            ('timing_jitter_ps',50.,0.,1e9), ('gate_half_width_ps',200.,1e-6,1e9),
            ('attenuation_db_km',.2,0.,10.), ('route_factor',1.2,1.,10.),
            ('insertion_loss_db',2.,0.,1000.), ('extra_loss_db',0.,0.,1000.),
            ('pair_emission_probability',.1,0.,1.), ('source_depolarization',.08,0.,1.),
            ('polarization_error_rad',.03,0.,math.pi), ('memory_cutoff_s',p.get('coherence_s',1.),1e-6,1e6),
            ('divergence_urad',10.,.001,1e6), ('receiver_diameter_m',1.,.001,100.),
            ('wavelength_nm',850.,100.,1e5), ('filter_sigma_hz',1e9,1.,1e15),
            ('pair_source_hz',5e6,0.,1e10), ('background_cps',500.,0.,1e10),
            ('coincidence_window_s',1e-9,1e-15,1.), ('zenith_optical_depth',.2,0.,100.),
            ('optical_efficiency',.5,0.,1.), ('classical_latency_s',.001,0.,600.)]:
            number(p.get(name,default),name,low,high)
        if 'satellite_doppler' in config.technologies:
            for name,default,low,high in [
                ('dark_count_cps',20.,0.,1e8),('detector_dead_time_ns',50.,0.,1e9),
                ('afterpulse_probability',0.,0.,.3),('afterpulse_delay_ns',1000.,1.,1e9),
                ('cn2_m_neg_2_3',0.,0.,1e-10),('turbulence_layer_m',2000.,1.,1e5),
                ('ao_residual_fraction',.5,0.,1.),('tracking_latency_s',0.,0.,10.),
                ('scintillation_sigma',0.,0.,3.),('doppler_tuning_limit_hz',1e11,0.,1e15),
                ('polarization_rotation_rate_rad_s',.001,-10.,10.),
                ('polarization_calibration_error_rad',.01,0.,math.pi),
                ('range_estimation_error_m',0.,0.,1000.),
                ('parameter_estimation_error',1e-10,1e-15,.01),('privacy_amplification_error',1e-10,1e-15,.01),
                ('tracking_max_age_s',60.,0.,600.)]:
                number(p.get(name,default),name,low,high)
            number(p.get('housekeeping_max_age_s',5.),'housekeeping_max_age_s',0.001,600.)
            number(p.get('raw_store_limit_bytes',2*1024**3),'raw_store_limit_bytes',1024**2,50*1024**3)
            for name in ('adaptive_optics','polarization_compensation','propagation_compensation'):
                if type(p.get(name,False if name=='adaptive_optics' else True)) is not bool:
                    raise ProtocolError('invalid_parameter',f'{name}: boolean required')
            if p.get('receiver_coupling','bucket') not in ('bucket','single_mode_fiber'):
                raise ProtocolError('invalid_parameter','unsupported receiver coupling')
            if type(p.get('min_parameter_sample',20)) is not int or not 1<=p.get('min_parameter_sample',20)<=100000:
                raise ProtocolError('invalid_parameter','min_parameter_sample: integer [1,100000]')
        if type(p.get('doppler_compensation',True)) is not bool:
            raise ProtocolError('invalid_parameter','doppler_compensation: boolean required')
        if type(p.get('memory_slots',4)) is not int or not 2 <= p.get('memory_slots',4) <= 1024:
            raise ProtocolError('invalid_parameter','memory_slots: integer [2,1024] required')
        if type(p.get('max_attempts',3)) is not int or not 1<=p.get('max_attempts',3)<=20:
            raise ProtocolError('invalid_parameter','max_attempts: integer [1,20] required')
        if p.get('timeout_s',2.) < p.get('block_s',.1):
            raise ProtocolError('invalid_parameter','timeout shorter than acquisition block')

    def configure(self, session_id, *, parameters=None, enabled=None, scenario=None, seed=None):
        if session_id not in self.sessions: raise ProtocolError('missing_session',session_id)
        current=self.sessions[session_id].config
        changes={}
        if parameters is not None: changes['parameters']={**current.parameters,**parameters}
        if enabled is not None: changes['enabled']=enabled
        if scenario is not None: changes['scenario']=scenario
        if seed is not None: changes['seed']=seed
        candidate=replace(current,**changes)
        self.registry.validate(candidate,self.topology);self._validate_parameters(candidate)
        with closing(connect(self.journal_path)) as conn,conn:
            conn.execute('INSERT INTO technology_configuration VALUES(?,?,?) ON CONFLICT(topology_id,session_id) DO UPDATE SET config_json=excluded.config_json',
                         (self.topology_id,session_id,canonical(candidate.to_dict())))
        self._fingerprint=None;self.sync(self.topology)
        self._event(self.sessions[session_id],self.clock_s,'operator_configured',{'before':current.to_dict(),'after':candidate.to_dict()})
        self._flush()
        return candidate.to_dict()

    def _schedule(self, at_s, session, kind, payload):
        self._queue_index+=1
        heapq.heappush(self._queue,(float(at_s),self._queue_index,session.config.session_id,session.revision,kind,payload))

    def _available(self, session):
        config=session.config
        if not config.enabled or session.status=='incompatible': return False
        for ident in config.path:
            node=self.topology.node(ident)
            if node.telemetry.get('console_control',{}).get('isolated') is True: return False
            if node.telemetry.get('communicator',{}).get('operational_state','enabled')!='enabled': return False
        for ident in config.link_ids:
            link=next((x for x in self.topology.links if x.link_id==ident),None)
            if link is None or not link.active or link.channels is None: return False
            if not link.channels['quantum']['enabled'] or not link.channels['classical']['enabled']: return False
            parent_id=(link.technology or {}).get('source_span_id')
            if parent_id:
                parent=next((x for x in self.topology.links if x.link_id==parent_id),None)
                if parent is None or not parent.active or parent.channels is None: return False
                if not parent.channels['quantum']['enabled'] or not parent.channels['classical']['enabled']: return False
        return True

    def _environment(self,session,at_s):
        scenario=session.config.scenario
        if scenario=='recovery':
            phase=at_s%30.
            condition='nominal' if phase<10. or phase>=20. else 'unavailable'
        else: condition=scenario
        locations=[node_location(self.topology.node(x)) for x in session.config.endpoints]
        return {'condition':condition,'ground_positions':[(x['latitude'],x['longitude']) for x in locations if x],
                'ground_altitudes_m':[x.get('altitude_m',0.) for x in locations if x]}

    def _source_for(self,session):
        if session.config.session_id in getattr(self,'_paused_device_sessions',()):return self.source
        descriptor=session.config.parameters.get('acquisition_source')
        if not descriptor: return self.source
        if descriptor.get('mode')=='measurement_stream':
            source=self.external_sources.get(descriptor.get('source_id','ogs_gateway'))
            if source is None:raise ProtocolError('adapter_unavailable','OGS read-only ingress is not running')
            return source
        from .sources import MeasurementFileSource,SimulatedAcquisitionSource
        if not hasattr(self,'_device_sources'): self._device_sources={}
        key=canonical(descriptor)+':'+session.config.session_id+':'+str(session.revision)
        cached=self._device_sources.get(session.config.session_id)
        if cached is None or cached[0]!=key:
            if descriptor['mode']=='measurement_file': source=MeasurementFileSource(descriptor['path'])
            elif descriptor['mode']=='simulated': source=SimulatedAcquisitionSource(session.config.seed)
            else: raise ProtocolError('adapter_unvalidated','physical acquisition driver unavailable')
            self._device_sources[session.config.session_id]=(key,source)
        return self._device_sources[session.config.session_id][1]

    def _lengths(self,session):
        return [next(x.distance_km for x in self.topology.links if x.link_id==ident) for ident in session.config.link_ids]

    def _request(self,session,at_s,operation,arguments,context=None,attempt=1):
        config=session.config
        context=context or {}
        arguments={**arguments,'device_endpoints':list(context.get('endpoints',config.endpoints))}
        session.command_number+=1
        quantum,classical=[],[]
        for ident in config.link_ids:
            link=next(x for x in self.topology.links if x.link_id==ident)
            quantum.append(link.channels['quantum']['channel_id']);classical.append(link.channels['classical']['channel_id'])
        command=DeviceCommand(f'{config.session_id}:{session.revision}:{session.command_number}',config.session_id,
            operation,config.path,tuple(quantum),tuple(classical),at_s,at_s+config.parameters.get('timeout_s',2.),attempt,arguments)
        session.pending[command.command_id]=(command,context or {})
        self._event(session,at_s,'acquisition_requested',{'command':command.to_dict()},command.command_id)
        self._schedule(at_s,session,'poll',command.command_id)

    def _poll(self,session,at_s,command_id):
        record=session.pending.get(command_id)
        if record is None: return
        command,context=record
        try:
            observation=self._source_for(session).acquire(command,session.config,self._environment(session,at_s))
            if observation is not None:
                if observation.observed_at_s > command.deadline_s:
                    raise ProtocolError('timeout','observation after deadline')
                self._schedule(max(at_s,observation.observed_at_s),session,'intake',(command,observation,context))
                session.pending.pop(command_id,None)
                return
        except (ProtocolError,ValueError,OSError,KeyError,TypeError) as error:
            session.pending.pop(command_id,None)
            self._failure(session,at_s,command,context,error)
            return
        if at_s >= command.deadline_s:
            session.pending.pop(command_id,None)
            self._failure(session,at_s,command,context,ProtocolError('timeout','source observation absent'))
        else:
            self._schedule(min(command.deadline_s,at_s+.05),session,'poll',command_id)

    def _failure(self,session,at_s,command,context,error):
        session.counts['errors']+=1
        session.status='unavailable';session.reason=f'{getattr(error,"code","source_error")}: {error}'
        self._event(session,at_s,'operation_error',{'code':getattr(error,'code','source_error'),'reason':str(error),
                    'attempt':command.attempt,'input_resources':context.get('parent_ids',[])},command.command_id)
        # Consumed quantum states are never restored on a retry. Only fresh
        # acquisition/count blocks may be requested again automatically.
        if command.operation in ('generate','tf_block','satellite_block','isl_block') and command.attempt < session.config.parameters.get('max_attempts',3):
            self._request(session,at_s+.05,command.operation,command.arguments,context,command.attempt+1)
            session.counts['retries']+=1
        else:
            session.operating.discard(context.get('resource_key'))

    def _intake(self,session,at_s,command,observation,context):
        serialized=observation.canonical_json();sha=hashlib.sha256(serialized.encode('utf-8')).hexdigest()
        if observation.raw.get('satellite_schema') or observation.raw.get('isl_schema'):
            # Raw clicks stay durable and exportable. Live snapshots contain a
            # reference, keeping large TDC arrays off the GUI publication path.
            self._queue_raw_acquisition(session,sha,serialized)
            detail={'observation_sha256':sha,'observation_reference':'satellite_acquisitions:'+sha,
                    'streams':['housekeeping','tracking','tdc'],
                    'clicks':[len(b['time_ps']) for b in observation.raw['tdc']],
                    'source_id':observation.source_id,'origin':observation.origin}
        else:detail={'observation':observation.to_dict(),'observation_sha256':sha}
        self._event(session,at_s,'acquired',detail,command.command_id)
        stream = (command.operation, tuple(context.get('endpoints', session.config.endpoints)))
        source=self._source_for(session)
        validate_binding(observation,command,last_sequence=session.sequence_streams.get(stream,0),source_id=source.source_id,
            origin=source.origin,now_s=at_s,sync_tolerance_s=session.config.parameters.get('sync_tolerance_s',1e-6))
        session.sequence_streams[stream]=observation.sequence
        session.last_sequence=max(session.last_sequence,observation.sequence)
        self._event(session,at_s,'validated',{'clock_offset_s':observation.clock_offset_s},command.command_id)
        self._event(session,at_s,'synchronized',{},command.command_id)
        if command.operation in ('generate','tf_block','satellite_block','isl_block'):
            observed_links = [link for link in self.topology.links
                if link.link_id in session.config.link_ids and
                (command.operation != 'generate' or
                 {link.left_node_id,link.right_node_id} == set(context['endpoints']))]
            self._event(session,at_s,'quantum_transmission_observed',{
                'quantum_channel_ids':[link.channels['quantum']['channel_id'] for link in observed_links],
                'classical_association_ids':[link.channels['classical']['channel_id'] for link in observed_links],
                'observed_segment_endpoints':list(context.get('endpoints',session.config.endpoints)),
                'binding_scope':'command binds required session channels; this event identifies observed quantum segments',
                'source_origin':observation.origin, 'scope':'source-reported photon/count observations; no physical executor'},command.command_id)
        payload_bytes=len(serialized.encode('utf-8'));fragments=1
        for ident in session.config.link_ids:
            link=next(x for x in self.topology.links if x.link_id==ident)
            limit=link.channels['classical']['max_message_bytes']
            if observation.raw.get('satellite_schema') or observation.raw.get('isl_schema'):
                if payload_bytes>8*1024*1024:raise ProtocolError('message_limit','OGS acquisition exceeds bounded 8 MiB assembly')
                fragments=max(fragments,math.ceil(payload_bytes/limit))
            elif payload_bytes>limit:
                raise ProtocolError('message_limit','classical observation exceeds configured message limit')
        op=command.operation
        if op in ('generate','bsm'): technology='entanglement_swapping'
        elif op=='purify': technology='purification'
        elif op=='tf_block': technology='tf_qkd'
        elif op=='satellite_block': technology='satellite_doppler'
        elif op=='isl_block': technology='satellite_isl'
        else: technology='quantum_memory'
        processed=self.registry.adapters[technology].process(observation.raw,{**context,'operation':op,
                   'duration_s':command.arguments.get('duration_s',.1),'parameters':session.config.parameters,
                   'endpoints':session.config.endpoints,'origin':observation.origin,
                   'environment':self._environment(session,at_s)})
        self._event(session,at_s,'protocol_processed',{'technology':technology,'operation':op,
                   'parent_ids':context.get('parent_ids',[])},command.command_id)
        propagation=sum(self._lengths(session))*session.config.parameters.get('route_factor',1.2)/(C_KM_S/1.468)
        if set(session.config.technologies)&{'satellite_doppler','satellite_isl'}:
            propagation=max((g.get('flight_time_s',g['range_km']/C_KM_S) for g in processed.get('geometry',())),default=propagation)
        # Deterministic positive jitter is reproducible and never grants a
        # negative latency. IDs also bind message receipts to both ports.
        token=int(digest(command.to_dict())[:8],16)/0xffffffff
        delay=propagation+session.config.parameters.get('classical_latency_s',.001)+token*session.config.parameters.get('classical_jitter_s',1e-5)
        for ident in session.config.link_ids:
            link=next(x for x in self.topology.links if x.link_id==ident)
            if delay>link.channels['classical']['max_age_s']:
                raise ProtocolError('timeout','classical transit exceeds configured maximum age')
        self._event(session,at_s,'classical_transmitted',{'classical_channel_ids':list(command.classical_channel_ids),
                    'associated_quantum_ids':list(command.quantum_channel_ids),'arrival_at_s':at_s+delay,
                    'payload_bytes':payload_bytes,'fragments':fragments,
                    'scope':'classical observation records; fragments obey each channel message limit'},command.command_id)
        self._schedule(at_s+delay,session,'received',(command,observation,context,processed))

    def _expire(self,session,at_s):
        remaining=[]
        for pair in session.pairs:
            coherence=self._pair_parameter(session,pair,'coherence_s',1.)
            maximum=self._pair_parameter(session,pair,'memory_cutoff_s',coherence)
            fidelity=aged_fidelity(pair.fidelity,at_s-pair.created_at_s,coherence)
            if at_s-pair.created_at_s>maximum or fidelity<=.5:
                session.counts['memory_expired']+=1
                self._event(session,at_s,'memory_expired',{'pair_id':pair.pair_id,'storage_s':at_s-pair.created_at_s,'fidelity_estimate':fidelity})
            else: remaining.append(pair)
        session.pairs=remaining

    def _add(self,session,pair,at_s):
        occupancy=Counter(x for p in session.pairs for x in (p.a,p.b))
        if any(occupancy[n]>=self._device_parameter(session,n,'memory_slots',4) for n in (pair.a,pair.b)):
            session.counts['memory_overflow']+=1
            self._event(session,at_s,'memory_capacity_rejected',{'pair_id':pair.pair_id,'occupancy':dict(occupancy)})
            return
        session.pairs.append(pair);session.counts['memory_stored']+=1
        self._event(session,at_s,'resource_stored',{'pair_id':pair.pair_id,'endpoints':[pair.a,pair.b],
                'parent_ids':list(pair.parents),'pauli_frame':list(pair.pauli_frame),
                'created_at_s':pair.created_at_s,'available_at_s':pair.available_at_s})

    def _received(self,session,at_s,command,observation,context,result):
        if not self._available(session):
            self._event(session,at_s,'late_message_discarded',{'reason':'link/node disabled before herald arrived'},command.command_id)
            session.operating.discard(context.get('resource_key'));return
        if at_s>command.deadline_s:
            self._failure(session,at_s,command,context,ProtocolError('timeout','classical herald missed deadline'));return
        self._event(session,at_s,'classical_received',{'classical_channel_ids':list(command.classical_channel_ids)},command.command_id)
        self._expire(session,at_s)
        op=command.operation;config=session.config
        session.counts['observations']+=1
        session.metrics['last_observed_at_s']=at_s
        session.status='operational';session.reason='observed operations and classical receipts processed'
        if op=='generate':
            session.counts['generation_attempts']+=observation.raw['attempts']
            session.counts['generated_pairs']+=result['generated_pairs']
            session.counts['tomography_consumed']+=result['tomography_consumed']
            writes=count(observation.raw,'memory_writes',result['available_pairs'])
            session.counts['memory_write_failures']+=result['available_pairs']-writes
            capacity=min(self._device_parameter(session,n,'memory_slots',4) for n in context['endpoints'])
            for index in range(min(writes,capacity)):
                self._add(session,PairResource(f'{command.command_id}:pair:{index}',*context['endpoints'],result['fidelity'],
                          observation.observed_at_s,at_s),at_s)
            session.counts['unbuffered_pairs']+=max(0,writes-capacity)
            session.metrics['last_optical_transmission']=observation.raw['optical_transmission']
            session.metrics['last_effective_length_km']=observation.raw['effective_length_km']
            session.metrics.setdefault('segments',{})['|'.join(context['endpoints'])] = {
                'fidelity_estimate':result['fidelity'], 'heralds':result['generated_pairs'],
                'at_s':at_s,'origin':observation.origin,
                'optical_transmission':observation.raw['optical_transmission'],
                'effective_length_km':observation.raw['effective_length_km']}
        elif op in ('bsm','purify'):
            session.operating.discard(context['resource_key'])
            success=result.get('success',result.get('accepted',False))
            counter='bsm' if op=='bsm' else 'purification'
            session.counts[counter+'_attempts']+=1;session.counts[counter+'_successes']+=int(success)
            if op=='purify':
                session.counts['purification_pairs_consumed']+=2
                session.metrics['last_purification_success_probability']=result['success_probability']
            if success:
                if op == 'bsm':
                    self._event(session,at_s,'pauli_frame_recorded',{'bell_outcome':result['bell_outcome'],
                                'frame':result['pauli_frame'],
                                'fidelity_reference':'conditional logical Bell frame; no physical correction actuated'},command.command_id)
                self._add(session,PairResource(f'{command.command_id}:output',*context['endpoints'],result['fidelity'],
                     observation.observed_at_s,at_s,tuple(context['parent_ids']),op=='purify',tuple(result['pauli_frame_bits'])),at_s)
            else:
                self._event(session,at_s,counter+'_failed',{'consumed':context['parent_ids']},command.command_id)
        elif op=='readout':
            session.operating.discard(context['resource_key'])
            if result['retrieval_success'] and result['fidelity']>=config.parameters.get('min_fidelity',.7):
                session.counts['delivered_pairs']+=1;session.delivered_fidelities.append(result['fidelity'])
                session.metrics['last_delivered_fidelity']=result['fidelity']
                self._event(session,at_s,'end_to_end_delivered',{'pair_id':context['parent_ids'][0],
                    'endpoints':list(config.endpoints),'fidelity_estimate':result['fidelity'],
                    'pauli_frame':list(context['pauli_frame']), 'fidelity_reference':'logical Bell frame',
                    'storage_s':result['storage_duration_s']},command.command_id)
            else:
                session.counts['delivery_rejected']+=1
                self._event(session,at_s,'delivery_rejected',{'reason':'retrieval failed or below requested fidelity','fidelity_estimate':result['fidelity']},command.command_id)
        else:
            session.metrics.update(result)
            if op in ('satellite_block','isl_block'):
                session.metrics.update(observation_origin=observation.origin,observation_source_id=observation.source_id)
            if op=='tf_block':
                session.metrics['phase_reference_residual_rad']=observation.raw['phase_reference_residual_rad']
                session.metrics['arm_transmissions']=observation.raw['arm_transmissions']
                session.metrics['effective_arm_lengths_km']=observation.raw['effective_arm_lengths_km']
            if result.get('available') is False:
                session.status='unavailable';session.reason=result.get('reason') or 'no simultaneous pass visibility or cloud blockage'
            session.counts['conditional_key_estimate_bits']+=result.get('conditional_key_estimate_bits',0)
            session.counts['sifted_bits']+=result.get('sifted_bits',0)
            session.metrics['conditional_key_estimate_bps']=result.get('conditional_key_estimate_bits',0)/command.arguments['duration_s']
            for stage in result.get('postprocessing',()):
                self._event(session,at_s,'postprocessing_'+stage,{'certified_secret_bits':0},command.command_id)
            for skipped in result.get('postprocessing_skipped',()):
                self._event(session,at_s,'postprocessing_skipped',skipped,command.command_id)
        self._event(session,at_s,'metrics_computed',{'operation':op},command.command_id)
        self._drive(session,at_s)

    def _reserve(self,session,pairs,at_s):
        for pair in pairs: session.pairs.remove(pair)
        session.counts['pairs_consumed']+=len(pairs)
        self._event(session,at_s,'resources_reserved_and_consumed',{'parent_ids':[p.pair_id for p in pairs],
                    'retained_copies':0})

    def _fidelity(self,session,pair,at_s):
        return aged_fidelity(pair.fidelity,at_s-pair.created_at_s,self._pair_parameter(session,pair,'coherence_s',1.))

    def _device_parameter(self,session,node_id,name,default):
        return session.config.parameters.get('device_parameters',{}).get(node_id,{}).get(name,session.config.parameters.get(name,default))

    def _pair_parameter(self,session,pair,name,default):
        values=[self._device_parameter(session,n,name,default) for n in (pair.a,pair.b)]
        if name=='coherence_s' and session.config.parameters.get('device_parameters'):
            return 1./sum(1./value for value in values)
        return min(values)

    def _drive(self,session,at_s):
        if not self._available(session) or 'entanglement_swapping' not in session.config.technologies: return
        self._expire(session,at_s)
        config=session.config
        groups={}
        for pair in session.pairs:
            if pair.available_at_s<=at_s: groups.setdefault((pair.a,pair.b),[]).append(pair)
        start,end=config.endpoints
        ready=groups.get((start,end),[])
        if ready:
            pair=max(ready,key=lambda x:self._fidelity(session,x,at_s));key=('readout',(start,end))
            if key not in session.operating:
                self._reserve(session,[pair],at_s);session.operating.add(key)
                self._request(session,at_s,'readout',{'pauli_frame':list(pair.pauli_frame)}, {'endpoints':(start,end),'parent_ids':[pair.pair_id],
                    'pauli_frame':pair.pauli_frame,
                    'resource_key':key,'fidelity':pair.fidelity,'elapsed_s':at_s-pair.created_at_s,
                    'coherence_s':self._pair_parameter(session,pair,'coherence_s',1.)})
            return
        path=config.path
        for middle in range(1,len(path)-1):
            left=groups.get((start,path[middle]),[]);right=groups.get((path[middle],path[middle+1]),[])
            if 'purification' in config.technologies:
                left=[p for p in left if p.purified or p.parents]
                right=[p for p in right if p.purified]
            key=('bsm',(start,path[middle+1]))
            if left and right and key not in session.operating:
                first=max(left,key=lambda x:self._fidelity(session,x,at_s));second=max(right,key=lambda x:self._fidelity(session,x,at_s))
                self._reserve(session,[first,second],at_s);session.operating.add(key)
                context={'endpoints':(start,path[middle+1]),'parent_ids':[first.pair_id,second.pair_id],'resource_key':key,
                    'first_frame':first.pauli_frame,'second_frame':second.pauli_frame,
                    'first_fidelity':self._fidelity(session,first,at_s),'second_fidelity':self._fidelity(session,second,at_s),
                    'operation_error':self._device_parameter(session,path[middle],'operation_error',.005)}
                self._request(session,at_s,'bsm',{'bsm_node':path[middle],
                              'input_frames':[list(first.pauli_frame),list(second.pauli_frame)]},context)
                return
        for key,pairs in tuple(groups.items()):
            if 'purification' in config.technologies:
                candidates=[p for p in pairs if not p.purified and self._fidelity(session,p,at_s)>.5]
                if len(candidates)>=2 and ('purify',key) not in session.operating:
                    first,second=candidates[:2];resource_key=('purify',key)
                    self._reserve(session,[first,second],at_s);session.operating.add(resource_key)
                    context={'endpoints':key,'parent_ids':[first.pair_id,second.pair_id],'resource_key':resource_key,
                             'first_frame':first.pauli_frame,'second_frame':second.pauli_frame,
                             'first_fidelity':self._fidelity(session,first,at_s),'second_fidelity':self._fidelity(session,second,at_s),
                             'operation_error':max(self._device_parameter(session,n,'purification_operation_error',.01) for n in key)}
                    self._request(session,at_s,'purify',{k:context[k] for k in ('first_fidelity','second_fidelity','first_frame','second_frame')},context)
                    return

    def _sample(self,session,at_s):
        block=session.config.parameters.get('block_s',.1)
        if session.status == 'incompatible':
            self._schedule(at_s+block,session,'sample',None)
            return
        if not self._available(session):
            session.status='disabled' if not session.config.enabled else 'unavailable'
            session.reason='operator disabled, isolated node or unavailable associated channel'
            if session.pairs:
                self._event(session,at_s,'resources_discarded',{'pair_ids':[p.pair_id for p in session.pairs]})
                session.pairs.clear()
        else:
            pending_kinds={c.operation for c,ctx in session.pending.values()}
            technologies=session.config.technologies
            if 'tf_qkd' in technologies:
                if 'tf_block' not in pending_kinds:
                    self._request(session,at_s,'tf_block',{'duration_s':block,'arm_lengths_km':self._lengths(session)})
            elif 'satellite_isl' in technologies:
                if 'isl_block' not in pending_kinds:
                    self._request(session,at_s,'isl_block',{'duration_s':block})
            elif 'satellite_doppler' in technologies:
                if 'satellite_block' not in pending_kinds:
                    self._request(session,at_s,'satellite_block',{'duration_s':block})
            else:
                for index,(a,b) in enumerate(zip(session.config.path,session.config.path[1:])):
                    if any(ctx.get('endpoints')==(a,b) and cmd.operation=='generate' for cmd,ctx in session.pending.values()): continue
                    self._request(session,at_s,'generate',{'duration_s':block,'length_km':self._lengths(session)[index]}, {'endpoints':(a,b)})
                self._drive(session,at_s)
        self._schedule(at_s+block,session,'sample',None)

    def advance(self,topology,network_time_s):
        self.sync(topology)
        target=float(network_time_s)*self.clock_rate
        if target<self.clock_s: raise ProtocolError('causality','clock cannot move backwards')
        processed=0
        while self._queue and self._queue[0][0]<=target and processed<4096:
            at_s,_,ident,revision,kind,payload=heapq.heappop(self._queue)
            session=self.sessions.get(ident)
            if session is None or revision!=session.revision: continue
            processed+=1
            try:
                if kind=='sample':self._sample(session,at_s)
                elif kind=='poll':self._poll(session,at_s,payload)
                elif kind=='intake':self._intake(session,at_s,*payload)
                elif kind=='received':self._received(session,at_s,*payload)
            except (ProtocolError,ValueError,KeyError,TypeError) as error:
                self._event(session,at_s,'processing_error',{'code':getattr(error,'code','invalid_observation'),'reason':str(error)})
                if kind=='intake':
                    # A rejected raw acquisition cannot publish metrics. Retry
                    # only with a fresh command/observation; never replay input
                    # records or restore a consumed quantum resource.
                    command,_,context=payload
                    self._failure(session,at_s,command,context,error)
                else:
                    session.status='error';session.reason=str(error)
        # If the bounded worker has a backlog, do not age resources past an
        # event still awaiting processing. Repeated calls drain the same target.
        watermark = min(target, self._queue[0][0]) if self._queue else target
        self.clock_s=watermark
        for session in self.sessions.values():
            self._expire(session,watermark)
            session.metrics.update(memory_occupancy=len(session.pairs),
                memory_qubit_occupancy=dict(Counter(x for pair in session.pairs for x in (pair.a,pair.b))),
                memory_ages_s=[max(0.,watermark-p.created_at_s) for p in session.pairs],
                stored_fidelity_estimates=[self._fidelity(session,p,watermark) for p in session.pairs],
                measurement_window_s=max(0.,watermark-session.started_at_s),
                generation_pair_rate_hz=session.counts['generated_pairs']/max(.001,watermark-session.started_at_s),
                delivered_pair_rate_hz=session.counts['delivered_pairs']/max(.001,watermark-session.started_at_s),
                bsm_success_fraction=session.counts['bsm_successes']/max(1,session.counts['bsm_attempts']),
                purification_yield=session.counts['purification_successes']/max(1,session.counts['purification_pairs_consumed']),
                certified_secret_bits=0)
        self._flush()
        return self.snapshot()

    def snapshot(self):
        return {'schema':'orion.technologies-live.v1','clock_s':self.clock_s,'clock_rate':self.clock_rate,
                'acceleration_label':f'{self.clock_rate:g} simulated seconds per network second',
                'source_id':self.source.source_id,'origin':self.source.origin,'physical_adapter_validated':self.source.physical_validated,
                'physical_actuation':False,'registry':self.registry.describe(),
                'ogs_ingress':{key:source.store.status() for key,source in self.external_sources.items()},
                'parameter_sources':self.parameter_sources,
                'pending_events':len(self._queue),'backlog_before_clock':sum(x[0]<self.clock_s for x in self._queue),
                'sessions':{key:{'config':s.config.to_dict(),'status':s.status,'reason':s.reason,
                               'source':{'origin':'measurement_file' if s.config.parameters.get('acquisition_source',{}).get('mode')=='measurement_file' else 'simulated' if s.config.parameters.get('acquisition_source',{}).get('mode')=='simulated' else self.source.origin},
                               'metrics':dict(s.metrics),'counters':dict(s.counts),'last_sequence':s.last_sequence,
                               'pending_commands':len(s.pending)} for key,s in self.sessions.items()},
                'events':list(self.events)}

    def export_events(self,path):
        with closing(connect(self.journal_path)) as conn:
            rows=conn.execute('SELECT payload_json FROM technology_events WHERE topology_id=? ORDER BY sequence',(self.topology_id,)).fetchall()
            acquisitions=conn.execute('SELECT sha256,observation_zlib FROM satellite_acquisitions WHERE sha256 IN (SELECT json_extract(payload_json,\'$.detail.observation_sha256\') FROM technology_events WHERE topology_id=?)',(self.topology_id,)).fetchall()
        from pathlib import Path
        Path(path).write_text('\n'.join(row[0] for row in rows)+'\n',encoding='utf-8')
        raw_path=Path(path).with_suffix('.satellite-acquisitions.jsonl')
        if acquisitions:raw_path.write_text('\n'.join(zlib.decompress(blob).decode('utf-8') for _,blob in acquisitions)+'\n',encoding='utf-8')
        return {'path':str(path),'events':len(rows),'scope':'protocol receipts; no key material or physical actuation',
                'satellite_acquisitions_path':str(raw_path) if acquisitions else None,'satellite_acquisitions':len(acquisitions),
                'physical_action_performed':False}
