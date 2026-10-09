"""Reproducible end-to-end satellite/OGS acceptance, without external hardware."""
from __future__ import annotations

import json
import os
import secrets
from dataclasses import replace
from pathlib import Path

from .contracts import DeviceCommand, ProtocolConfig
from .satellite_contracts import packets
from .satellite_processing import process_observation
from .sources import SimulatedAcquisitionSource


def validate_satellite_pipeline(output_dir,seed=42):
    output=Path(output_dir);output.mkdir(parents=True,exist_ok=False)
    checks={};report={'schema':'orion.satellite-validation.v2','passed':False,'seed':seed,
        'checks':checks,'physical_hardware_validated':False,'mission_ready':False,
        'security_certified':False,'physical_actuation':False}
    try:
        from ..network_engine.institutional import brazil_institutions
        from .topology import expand_global
        topology=expand_global(brazil_institutions())[0]
        raw_config=next(c for node in topology.nodes for c in node.telemetry.get('technology_sessions',[]) if c['session_id']=='RIO-SATELLITE')
        config=ProtocolConfig.from_dict(raw_config)
        environment={'ground_positions':[(-22.953888889,-43.173611111),(-22.8606982,-43.2310552)],'ground_altitudes_m':[0.,0.]}
        def sample(name,changes,duration=2.,scenario=None):
            cfg=replace(config,parameters={**config.parameters,**changes},scenario=scenario or config.scenario)
            cmd=DeviceCommand(name,cfg.session_id,'satellite_block',cfg.path,('q1','q2'),('c1','c2'),0.,duration+2.,1,{'duration_s':duration})
            raw=SimulatedAcquisitionSource(seed).acquire(cmd,cfg,environment).raw
            result=process_observation(raw,{'parameters':cfg.parameters,'endpoints':cfg.endpoints})
            (output/(name+'.json')).write_text(json.dumps(result,indent=2),encoding='utf-8')
            return result,raw,cmd
        nominal,raw,cmd=sample('nominal',{})
        checks['observed_pairs']=nominal['coincidences']>0
        checks['separate_streams']=len(raw['tdc'])==len(raw['tracking'])==2 and bool(raw['housekeeping'])
        checks['source_does_not_emit_results']=not set(raw)&{'coincidences','qber','final_key','skr_bps'}
        low,_,_=sample('extra-loss',{'extra_loss_db':100.})
        checks['extra_loss_effective']=low['coincidences']<nominal['coincidences']/10
        low,_,_=sample('jitter',{'timing_jitter_ps':1e6})
        checks['jitter_effective']=low['coincidences']<nominal['coincidences']/10
        degraded,degraded_raw,_=sample('degraded',{},scenario='degraded')
        geometry=process_observation(degraded_raw,{'parameters':config.parameters,'endpoints':config.endpoints,'environment':environment})['geometry']
        checks['degraded_budget_tracks_detector_model']=all(abs(g['link_budget_db']['insertion']-r['link_budget_db']['insertion'])<1e-9 for g,r in zip(geometry,degraded_raw['geometry']))
        noisy,noise_raw,_=sample('background',{'background_cps':5000.,'dark_count_cps':1000.})
        checks['background_effective']=len(noise_raw['tdc'][0]['time_ps'])>len(raw['tdc'][0]['time_ps'])
        checks['finite_small_block_aborts']=nominal['conditional_key_estimate_bits']==0
        checks['no_certified_keys']=nominal['certified_secret_bits']==noisy['certified_secret_bits']==0
        from ..control_plane.communicators import defaults
        orbit={**defaults('orbit'),'reference_latitude':environment['ground_positions'][0][0],
            'reference_longitude':environment['ground_positions'][0][1],'communication_windows':[[0.,.2]]}
        cut,cut_raw,_=sample('pass-ending',{'orbit_record':orbit})
        checks['window_closes_midblock']=all(b<=.2 for a,b in cut_raw['tracking'][0]['open_intervals_s']) and not cut['available_at_end']
        full,_,_=sample('finite-laboratory',{'pair_source_hz':100000.,'receiver_diameter_m':6.,'optical_qber':.001},duration=6.)
        checks['full_postprocessing_from_generated_clicks']=(full['reconciliation_verified'] and
            full['privacy_amplification_executed_bits']==full['conditional_key_estimate_bits']>0 and full['certified_secret_bits']==0)
        report['laboratory_scope']='Hypothetical 6 m OGS aperture, 100 kpair/s, optical error 0.001, 6 s; software exercise, no documented mission or guaranteed yield'

        # Exercise the actual engine command, socket, three streams, source
        # adapter, classical receipts, persistence and immutable publication.
        from ..control_plane.communicators import CommunicatorStore
        from ..control_plane.topology import SimulatedTopologyStore
        from ..network_engine.engine import NetworkEngine
        from ..network_engine.sources import SimulatedInputSource
        journal=output/'backend.sqlite';store=SimulatedTopologyStore(journal);store.initialize(topology)
        # Preserve every satellite's declared ephemerides before selecting the
        # loopback session. Filtering operations must not erase orbital data.
        seeded=CommunicatorStore(store,topology.topology_id).seed(topology,'simulated')
        topology=replace(seeded,nodes=tuple(replace(n,telemetry={**n.telemetry,'technology_sessions':
            [c for c in n.telemetry.get('technology_sessions',[]) if c['session_id']==config.session_id]}) for n in seeded.nodes),
            topology_version=seeded.topology_version+1)
        store._replace_existing(topology,expected_version=seeded.topology_version,
            event_kind='satellite_validation_session_selection',payload={'preserved_orbits':True})
        engine=NetworkEngine(store,journal,topology_id=topology.topology_id,source=SimulatedInputSource(seed=seed))
        reference='ORION_VALIDATION_OGS_EPHEMERAL';previous=os.environ.get(reference)
        os.environ[reference]=secrets.token_hex(32)
        try:
            engine.command('configure_technology_source',{'kind':'ogs_websocket','session_id':config.session_id,
                'port':0,'credential_ref':'env:'+reference,'origin':'simulated'})
            # Engine returns a command receipt; retrieve the actual bound port.
            port=engine._ogs_server.port
            engine.step();session=engine.technologies.sessions[config.session_id]
            from websockets.sync.client import connect
            with connect(f'ws://127.0.0.1:{port}/ogs/v2/status',additional_headers={'Authorization':'Bearer '+os.environ[reference]}) as connection:
                connection.send(json.dumps({'query':'status'}));status=json.loads(connection.recv())
            window=next(w for w in status['observation_windows'] if w['session_id']==config.session_id)
            checks['gateway_discovers_binding_without_internal_command_id']='command_id' not in window and status['physical_actuation'] is False
            request=DeviceCommand('external-fixture',config.session_id,'satellite_block',config.path,
                tuple(window['quantum_channel_ids']),tuple(window['classical_channel_ids']),window['start_s'],
                window['expires_s'],1,{'duration_s':window['end_s']-window['start_s']})
            fixture=SimulatedAcquisitionSource(seed).acquire(request,session.config,environment).raw
            # A gateway need not reproduce the simulator's optional optical
            # fields: the backend derives geometry/latency from received ECEF.
            fixture['geometry']=[{k:g[k] for k in ('range_km','elevation_deg','visible')} for g in fixture['geometry']]
            messages=packets(fixture,request,source_id='ogs_gateway',origin='simulated',sequence=1)
            (output/'synthetic-loopback.ogs-packets.jsonl').write_text('\n'.join(json.dumps(p) for p in messages)+'\n',encoding='utf-8')
            engine.step()
            acks=[]
            for packet in messages:
                with connect(f"ws://127.0.0.1:{port}/ogs/v2/{packet['kind']}",
                    additional_headers={'Authorization':'Bearer '+os.environ[reference]},max_size=8*1024*1024) as connection:
                    connection.send(json.dumps(packet));acks.append(json.loads(connection.recv()))
            checks['authenticated_socket_receives_three_streams']=all(a['status']=='accepted' for a in acks)
            engine.step()
            result=engine.live_state().technologies['sessions'][config.session_id]
            checks['external_observation_reaches_backend']=result['metrics'].get('observation_origin')=='simulated' and result['counters'].get('observations',0)>0
            checks['minimum_gateway_geometry_processed']=all('flight_time_s' in g for g in result['metrics'].get('geometry',[])) and result['counters'].get('observations',0)>0
            checks['backend_uses_received_ephemeris']=engine.live_state().satellites[config.path[1]].get('model')=='received ECEF observations'
            checks['backend_geographic_frame_wgs84']=engine.live_state().satellites[config.path[1]].get('geographic_reference','').startswith('WGS84')
            kinds=[event['event'] for event in engine.technologies.events]
            checks['classical_receipt_before_metrics']='classical_received' in kinds and kinds.index('classical_received')<kinds.index('metrics_computed')
            export=engine.technologies.export_events(output/'receipts.jsonl')
            checks['raw_tags_persisted_exportable']=export['satellite_acquisitions']>0 and Path(export['satellite_acquisitions_path']).is_file()
            checks['live_publication_excludes_raw_tags']=all('observation' not in e['detail'] for e in engine.technologies.snapshot()['events'] if e['event']=='acquired')
            report['loopback']={'acks':acks,'snapshot':result,'export':export,
                'evidence_kind':'simulated_interface_loopback','observation_origin':'simulated',
                'scope':'seeded synthetic photon fixture through local socket and backend; no satellite measurement'}
            engine.command('configure_technology_source',{'kind':'stop_ogs'})
            checks['listener_stops_without_actuation']=engine._ogs_server is None and not engine.technologies.external_sources
        finally:
            engine.close_ogs()
            if previous is None:os.environ.pop(reference,None)
            else:os.environ[reference]=previous
        report['passed']=all(checks.values())
    except Exception as exc:report['error']=f'{type(exc).__name__}: {exc}'
    report['check_count']=len(checks)
    (output/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    (output/'cases.md').write_text('| Caso | Resultado |\n|---|---|\n'+'\n'.join(f'| {k} | {"PASS" if v else "FAIL"} |' for k,v in checks.items())+'\n',encoding='utf-8')
    return report
