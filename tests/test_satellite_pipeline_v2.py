"""Independent contracts, physical sensitivities, ingress and finite-key gates."""
import copy
import json
from dataclasses import replace

import numpy as np
import pytest

from quantum_autopilot.network_engine.institutional import brazil_institutions
from quantum_autopilot.quantum_protocols.topology import expand_global
from quantum_autopilot.quantum_protocols.contracts import DeviceCommand, ProtocolConfig, ProtocolError
from quantum_autopilot.quantum_protocols.sources import SimulatedAcquisitionSource
from quantum_autopilot.quantum_protocols.satellite_contracts import packets, assemble
from quantum_autopilot.quantum_protocols.satellite_processing import process_observation, toeplitz, finite_budget
from quantum_autopilot.quantum_protocols.satellite_ingress import OGSStore, OGSWebSocketServer, SatelliteStreamSource
from quantum_autopilot.quantum_protocols.orbits import position, _tle

pytestmark=pytest.mark.unit


@pytest.fixture(scope='module')
def topology():return expand_global(brazil_institutions())[0]


@pytest.fixture
def config(topology):
    raw=next(c for n in topology.nodes for c in n.telemetry.get('technology_sessions',[]) if c['session_id']=='RIO-SATELLITE')
    return ProtocolConfig.from_dict(raw)


def command(config,start=0.,duration=1.):
    return DeviceCommand('local-command',config.session_id,'satellite_block',config.path,('q1','q2'),('c1','c2'),start,start+duration+2.,1,{'duration_s':duration})


def observation(config,**changes):
    cfg=replace(config,parameters={**config.parameters,**changes})
    cmd=command(cfg,duration=changes.get('duration',1.))
    env={'ground_positions':[(-22.953888889,-43.173611111),(-22.8606982,-43.2310552)],'ground_altitudes_m':[0.,0.]}
    raw=SimulatedAcquisitionSource().acquire(cmd,cfg,env).raw
    return raw,cmd,cfg


def process(raw,cfg):
    return process_observation(raw,{'parameters':cfg.parameters,'endpoints':cfg.endpoints,'origin':'simulated'})


def test_simulation_emits_typed_records_and_derives_outcomes(config):
    raw,cmd,cfg=observation(config)
    assert 'coincidences' not in raw and 'errors' not in raw and 'final_key' not in raw
    restored=assemble(packets(raw,cmd,source_id='simulator',origin='simulated',sequence=1))
    result=process(restored,cfg)
    assert result['coincidences']>0 and result['sifted_bits']>0
    assert result['certified_secret_bits']==0
    if not result['conditional_key_estimate_bits']:
        assert 'toeplitz_privacy_amplification' not in result['postprocessing']
        assert result['postprocessing_skipped'][0]['reason']
    assert len(restored['tdc'])==2 and len(restored['tracking'])==2


@pytest.mark.parametrize('change',[{'insertion_loss_db':100.},{'extra_loss_db':100.}])
def test_loss_reduces_observed_pairs(config,change):
    base,_,cfg=observation(config);bad,_,bad_cfg=observation(config,**change)
    assert process(bad,bad_cfg)['coincidences']<process(base,cfg)['coincidences']/10


def test_reported_excess_loss_keeps_processed_and_generated_budgets_coherent(config):
    cfg=replace(config,scenario='degraded');raw,_,cfg=observation(cfg)
    environment={'ground_positions':[(-22.953888889,-43.173611111),(-22.8606982,-43.2310552)],'ground_altitudes_m':[0.,0.]}
    context={'parameters':cfg.parameters,'endpoints':cfg.endpoints,'environment':environment}
    result=process_observation(raw,context)
    for actual,expected in zip(result['geometry'],raw['geometry']):
        assert actual['link_budget_db']['insertion']==pytest.approx(expected['link_budget_db']['insertion'])
        assert actual['link_budget_db']['total']==pytest.approx(expected['link_budget_db']['total'])
    assert result==process_observation(raw,{**context,'origin':'physical'})


def test_jitter_changes_real_coincidence_matching(config):
    base,_,cfg=observation(config);bad,_,bad_cfg=observation(config,timing_jitter_ps=1e6)
    assert process(bad,bad_cfg)['coincidences']<process(base,cfg)['coincidences']/10


def test_noise_and_dead_time_affect_detector_events(config):
    base,_,_=observation(config,background_cps=0.,dark_count_cps=0.)
    noisy,_,_=observation(config,background_cps=10000.,dark_count_cps=1000.)
    dead,_,_=observation(config,background_cps=10000.,dark_count_cps=1000.,detector_dead_time_ns=1e6)
    assert len(noisy['tdc'][0]['time_ps'])>len(base['tdc'][0]['time_ps'])
    assert len(dead['tdc'][0]['time_ps'])<len(noisy['tdc'][0]['time_ps'])
    assert dead['tdc'][0]['dead_time_discarded']>0


def test_continuous_flight_time_requires_calibrated_compensation(config):
    raw,_,cfg=observation(config)
    delays=raw['tdc'][0]['flight_time_ps']
    assert max(delays)-min(delays)>100000
    assert 'Hermite' in raw['tdc'][0]['delay_reference']
    corrected=process(raw,cfg)['coincidences']
    without,_,bad_cfg=observation(config,propagation_compensation=False)
    assert not any(without['tdc'][0]['flight_time_ps'])
    assert process(without,bad_cfg)['coincidences']<corrected/10
    uncertain,_,uncertain_cfg=observation(config,range_estimation_error_m=1.)
    assert process(uncertain,uncertain_cfg)['coincidences']<corrected/2


def test_tdc_alignment_rejects_integer_overflow(config):
    raw,_,cfg=observation(config)
    raw['tdc'][1]['epoch_utc_s']=10**11
    with pytest.raises(ProtocolError) as error:process(raw,cfg)
    assert error.value.code=='invalid_tdc'


def test_generated_large_capture_executes_finite_postprocessing(config):
    raw,_,cfg=observation(config,duration=6.,pair_source_hz=100000.,receiver_diameter_m=6.,optical_qber=.001)
    result=process(raw,cfg)
    assert result['reconciliation_verified']
    assert result['conditional_key_estimate_bits']>0
    assert result['privacy_amplification_executed_bits']==result['conditional_key_estimate_bits']
    assert 'toeplitz_privacy_amplification' in result['postprocessing']
    assert result['certified_secret_bits']==0


def test_generated_capture_resource_limit_aborts_before_large_allocation(config):
    with pytest.raises(ProtocolError) as error:observation(config,duration=60.,background_cps=10000.)
    assert error.value.code=='tdc_overflow'


def test_window_closes_mid_block_and_recovery_opens(config):
    from quantum_autopilot.control_plane.communicators import defaults
    orbit={**defaults('orbit'),'reference_latitude':-22.953888889,'reference_longitude':-43.173611111,'communication_windows':[[0.,.2],[.7,1.]]}
    raw,_,cfg=observation(config,orbit_record=orbit)
    assert all(a<.2 or a>=.7 for a,b in raw['tracking'][0]['open_intervals_s'])
    assert sum((r['end_s']-r['start_s']) for r in raw['intervals'] if all(r['visible']))==pytest.approx(.5)
    assert process(raw,cfg)['available']
    closed,_,closed_cfg=observation(config,orbit_record={**orbit,'communication_windows':[[0.,.00001]]})
    assert process(closed,closed_cfg)['coincidences']==0


@pytest.mark.parametrize('mutate,code',[
    (lambda r:r['tdc'][0].update(lost_tags=1),'tdc_overflow'),
    (lambda r:r['tdc'][0].update(clock_uncertainty_ps=1e9),'unsynchronized'),
    (lambda r:r['housekeeping'].update(valid_until_s=0.),'stale_ephemeris'),
    (lambda r:r['housekeeping'].update(frame='TEME'),'invalid_ephemeris'),
    (lambda r:r['housekeeping'].update(ecef_km=[0.,0.,0.]),'invalid_ephemeris'),
    (lambda r:r['tdc'][0].update(clock_reference=''),'unsynchronized'),
    (lambda r:r['tdc'][0].update(epoch_utc_s=1.7e9),'invalid_tdc'),
    (lambda r:r['tracking'][0].update(station_id='other'),'wrong_station'),
])
def test_invalid_inputs_abort(config,mutate,code):
    raw,_,cfg=observation(config);mutate(raw)
    with pytest.raises(ProtocolError) as error:process(raw,cfg)
    assert error.value.code==code


def test_packet_association_and_strict_channels(config):
    raw,cmd,_=observation(config);parts=packets(raw,cmd,source_id='ogs',origin='physical',sequence=1)
    parts[2]['classical_channel_ids'][0]='another'
    with pytest.raises(ProtocolError):assemble(parts)


def test_toeplitz_matches_independent_dense_gf2_matrix():
    rng=np.random.default_rng(7)
    for n,m in ((3,2),(61,34),(511,300)):
        a=rng.integers(0,2,n,dtype=np.uint8);seed=rng.integers(0,2,n+m-1,dtype=np.uint8)
        expected=[sum(int(a[j])*int(seed[n-1-j+i]) for j in range(n))%2 for i in range(m)]
        assert toeplitz(a,seed,m).tolist()==expected


def test_finite_small_blocks_abort_and_bound_shrinks_with_statistics():
    small=finite_budget(100,20,1,50,34,{})
    large=finite_budget(1_000_000,1_000_000,10000,150000,34,{})
    assert small['budget_bits']==0 and large['budget_bits']>0
    assert small['sampling_deviation']>large['sampling_deviation']


def test_bit_reconciliation_verification_and_privacy_amplification_execute(config):
    raw,_,cfg=observation(config);n=12000
    times=[1000000*i for i in range(n)]
    detectors=[2*(i%2)+(i//2)%2 for i in range(n)]
    for batch in raw['tdc']:
        batch.update(time_ps=times.copy(),detector=detectors.copy(),flight_time_ps=[0]*n,
                     propagation_delay_ps=0,clock_offset_ps=0,dead_time_discarded=0)
    # 1% measured Z errors. Basis X supplies an independent clean test sample.
    for i in range(0,n,200):raw['tdc'][1]['detector'][i]^=1
    result=process(raw,cfg)
    assert result['reconciliation_corrected_bits']>0 and result['reconciliation_verified']
    assert result['conditional_key_estimate_bits']>0
    assert result['privacy_amplification_executed_bits']==result['conditional_key_estimate_bits']
    assert result['certified_secret_bits']==0


def test_tdc_epoch_integers_preserve_picosecond_matching(config):
    raw,_,cfg=observation(config)
    b=raw['tdc'][1]
    # Different integer UTC epochs, representing exactly the same arrivals.
    before=process(raw,cfg)['coincidences']
    b['epoch_utc_s']+=1;b['time_ps']=[x-10**12 for x in b['time_ps']]
    assert process(raw,cfg)['coincidences']==before


def test_unlocked_tracking_cannot_publish_success(config):
    raw,_,cfg=observation(config);raw['tracking'][1]['locked']=False
    result=process(raw,cfg)
    assert not result['available'] and result['coincidences']==0 and result['conditional_key_estimate_bits']==0


def test_visibility_crossing_is_resolved_inside_integration_bin(config):
    raw,_,cfg=observation(config,duration=4.,pair_source_hz=1e6,min_elevation_deg=88.)
    open_intervals=raw['tracking'][0]['open_intervals_s']
    assert open_intervals and open_intervals[-1][1]<raw['end_s']
    assert not raw['geometry'][0]['visible']
    # Ending time is found from the physical threshold, not rounded to 250 ms.
    ending=open_intervals[-1][1]
    assert abs(ending/.25-round(ending/.25))>1e-5


def test_runtime_rejects_invalid_satellite_noise_parameters(tmp_path,topology):
    from quantum_autopilot.quantum_protocols.runtime import ProtocolRuntime
    runtime=ProtocolRuntime(tmp_path/'invalid.sqlite',topology.topology_id);runtime.sync(topology)
    with pytest.raises(ProtocolError):runtime.configure('RIO-SATELLITE',parameters={'afterpulse_probability':.9})
    with pytest.raises(ProtocolError):runtime.configure('RIO-SATELLITE',parameters={'receiver_coupling':'unimplemented'})


def test_received_vector_not_configured_orbit_drives_physical_geometry(config):
    raw,_,cfg=observation(config)
    raw['housekeeping']['ecef_km']=[6878.137,0.,0.]
    raw['housekeeping']['velocity_ecef_km_s']=[0.,7.5,0.]
    output=process_observation(raw,{'parameters':cfg.parameters,'endpoints':cfg.endpoints,'origin':'physical',
        'environment':{'ground_positions':[(0.,0.),(0.,.01)],'ground_altitudes_m':[0.,0.]}})
    assert output['geometry'][0]['range_km']==pytest.approx(500.)
    # Inertial beacon-to-outgoing 2*v/c includes Omega x delta (500 km range).
    assert output['geometry'][0]['point_ahead_urad']==pytest.approx(50.27785308394916)


def test_precomputed_results_refused_inside_ogs_packets(config,tmp_path):
    raw,cmd,_=observation(config);message=packets(raw,cmd,source_id='ogs_gateway',origin='physical',sequence=1)[0]
    message['data']['qber']=.001
    with pytest.raises(ProtocolError) as error:OGSStore(tmp_path/'precomputed.sqlite').submit(message,1.)
    assert error.value.code=='precomputed_result'


def test_doppler_filter_tuning_limits_and_turbulence_are_effective():
    from quantum_autopilot.quantum_protocols.physics import satellite_optics
    geo={'range_km':500.,'elevation_deg':60.,'radial_velocity_km_s':7.5,'transverse_velocity_km_s':2.}
    corrected=satellite_optics(geo,{'doppler_compensation':True})
    limited=satellite_optics(geo,{'doppler_compensation':True,'doppler_tuning_limit_hz':1e6})
    turbulent=satellite_optics(geo,{'cn2_m_neg_2_3':1e-15,'receiver_coupling':'single_mode_fiber'})
    ao=satellite_optics(geo,{'cn2_m_neg_2_3':1e-15,'receiver_coupling':'single_mode_fiber','adaptive_optics':True})
    assert abs(corrected['carrier_doppler_ghz'])==pytest.approx(8.8235294118)
    assert limited['channel_transmission']<corrected['channel_transmission']/100
    assert turbulent['channel_transmission']<ao['channel_transmission']<corrected['channel_transmission']


def test_sgp4_rejects_stale_tle_and_bad_checksum():
    from quantum_autopilot.control_plane.communicators import defaults
    lines=['1 00005U 58002B   00179.78495062  .00000023  00000-0  28098-4 0  4753',
           '2 00005  34.2682 348.7242 1859667 331.7664  19.3264 10.82419157413667']
    with pytest.raises(ValueError):_tle(lines[0][:-1]+'0',lines[1])
    orbit={**defaults('orbit'),'model':'sgp4_tle','source':'Vallado TLE','tle_line1':lines[0],'tle_line2':lines[1]}
    with pytest.raises(ValueError):position(orbit,0.)


def test_sgp4_independent_vallado_epoch_vector():
    sat=_tle('1 00005U 58002B   00179.78495062  .00000023  00000-0  28098-4 0  4753',
             '2 00005  34.2682 348.7242 1859667 331.7664  19.3264 10.82419157413667')
    error,r,v=sat.sgp4(sat.jdsatepoch,sat.jdsatepochF)
    assert error==0
    assert r==pytest.approx((7022.46529266,-1400.08296755,.03995155),abs=1e-7)
    assert v==pytest.approx((1.893841015,6.405893759,4.534807250),abs=1e-8)


def test_wgs84_overhead_azimuth_and_point_ahead():
    from quantum_autopilot.quantum_protocols.orbits import state_geometry
    state={'ecef_km':[6878.137,0.,0.],'velocity_ecef_km_s':[0.,7.5,0.]}
    geo=state_geometry(state,(0.,0.))
    assert geo['range_km']==pytest.approx(500.) and geo['elevation_deg']==pytest.approx(90.)
    assert geo['radial_velocity_km_s']==pytest.approx(0.)
    # ECEF mechanical tracking and inertial optical point-ahead differ by Omega x delta.
    assert geo['point_ahead_urad']==pytest.approx(2*(7.5+7.2921159e-5*500.)/299792.458*1e6)


def test_received_ecef_geographic_conversion_uses_wgs84_height():
    from quantum_autopilot.quantum_protocols.orbits import ecef_geodetic
    import math
    a=6378.137;f=1/298.257223563;e2=f*(2-f);lat=math.pi/4;lon=math.radians(-73.);h=500.
    n=a/math.sqrt(1-e2*math.sin(lat)**2)
    xyz=[(n+h)*math.cos(lat)*math.cos(lon),(n+h)*math.cos(lat)*math.sin(lon),(n*(1-e2)+h)*math.sin(lat)]
    geo=ecef_geodetic(xyz)
    assert geo['sat_latitude']==pytest.approx(45.,abs=1e-10)
    assert geo['sat_longitude']==pytest.approx(-73.,abs=1e-10)
    assert geo['altitude_km']==pytest.approx(500.,abs=1e-8)
    assert ecef_geodetic([a+500.,0.,0.])['altitude_km']==pytest.approx(500.)
    assert ecef_geodetic([0.,0.,a*(1-f)+500.])['altitude_km']==pytest.approx(500.)


def test_demo_geo_consumes_received_housekeeping_instead_of_direct_state(tmp_path,topology):
    from quantum_autopilot.control_plane.communicators import CommunicatorStore
    from quantum_autopilot.control_plane.topology import SimulatedTopologyStore
    from quantum_autopilot.network_engine.engine import NetworkEngine
    from quantum_autopilot.network_engine.sources import SimulatedInputSource
    journal=tmp_path/'map.sqlite';store=SimulatedTopologyStore(journal);store.initialize(topology)
    # Register all existing orbital records before selecting one protocol for
    # this test; removing sessions must not erase other satellites' ephemerides.
    seeded=CommunicatorStore(store,topology.topology_id).seed(topology,'simulated')
    topo=replace(seeded,nodes=tuple(replace(n,telemetry={**n.telemetry,'technology_sessions':
        [c for c in n.telemetry.get('technology_sessions',[]) if c['session_id']=='RIO-SATELLITE']}) for n in seeded.nodes),
        topology_version=seeded.topology_version+1)
    store._replace_existing(topo,expected_version=seeded.topology_version,event_kind='fixture_protocol_selection',payload={})
    engine=NetworkEngine(store,journal,topology_id=topo.topology_id,source=SimulatedInputSource(seed=42))
    for _ in range(3):engine.step()
    session=engine.technologies.sessions['RIO-SATELLITE'];state=engine.live_state().satellites[session.config.path[1]]
    assert state['ecef_km']==session.metrics['housekeeping']['ecef_km']
    assert state['origin']=='simulated' and state['geographic_reference'].startswith('WGS84')
    assert state['at_network_s']==session.metrics['housekeeping']['at_s']


def test_received_state_vectors_interpolate_but_never_extrapolate():
    from quantum_autopilot.control_plane.communicators import defaults
    orbit={**defaults('orbit'),'model':'state_vectors','source':'OGS test ephemerides',
        'state_vectors':[{'at_network_s':0.,'ecef_km':[6878.,0.,0.],'velocity_ecef_km_s':[0.,7.,0.]},
                         {'at_network_s':10.,'ecef_km':[6878.,70.,0.],'velocity_ecef_km_s':[0.,7.,0.]}]}
    assert position(orbit,5.)['ecef_km']==pytest.approx([6878.,35.,0.])
    with pytest.raises(ValueError):position(orbit,11.)


def _registered_ogs_security(tmp_path):
    from quantum_autopilot.security import SecurityRuntime
    runtime = SecurityRuntime(tmp_path/'security')
    runtime.grant_identity('service:ogs', ['operator'], ['source:ogs_gateway/*'])
    token = runtime.issue_service_session('service:ogs')
    runtime.vault.put_bytes('ogs-session', token.encode('ascii'))
    return runtime, token


def test_authenticated_websocket_duplicate_reconnect_and_real_pipeline(tmp_path,config):
    from websockets.sync.client import connect
    raw,cmd,cfg=observation(config);messages=packets(raw,cmd,source_id='ogs_gateway',origin='physical',sequence=1)
    store=OGSStore(tmp_path/'stream.sqlite');security,token=_registered_ogs_security(tmp_path)
    server=OGSWebSocketServer(store,lambda:1.,credential_ref='vault:ogs-session',security=security)
    try:
        with pytest.raises(Exception):
            with connect(f'ws://127.0.0.1:{server.port}/ogs/v2/tdc'):pass
        with connect(f'ws://127.0.0.1:{server.port}/ogs/v2/tdc',additional_headers={'Authorization':'Bearer '+token}) as client:
            client.send('[]')
            assert json.loads(client.recv())['code']=='invalid_packet'
            client.send(json.dumps(messages[0]))
            assert json.loads(client.recv())['code']=='wrong_stream'
        for message in messages:
            url=f"ws://127.0.0.1:{server.port}/ogs/v2/{message['kind']}"
            with connect(url,additional_headers={'Authorization':'Bearer '+token},max_size=8*1024*1024) as client:
                client.send(json.dumps(message));assert json.loads(client.recv())['status']=='accepted'
                client.send(json.dumps(message));assert json.loads(client.recv())['status']=='duplicate'
        source=SatelliteStreamSource(store,lambda:1.)
        observed=source.acquire(cmd,cfg,{})
        assert observed.origin=='physical' and observed.source_id=='ogs_gateway'
        assert process(observed.raw,cfg)['coincidences']==process(raw,cfg)['coincidences']
        assert source.acquire(cmd,cfg,{}) is None
        reopened=OGSStore(tmp_path/'stream.sqlite')
        assert reopened.submit(messages[0],1.)['status']=='duplicate'
    finally:server.close()


def test_ingress_replay_future_stale_and_backpressure(tmp_path,config):
    raw,cmd,_=observation(config);messages=packets(raw,cmd,source_id='ogs_gateway',origin='physical',sequence=1)
    store=OGSStore(tmp_path/'store.sqlite',max_pending=1)
    with pytest.raises(ProtocolError):store.submit(messages[0],0.)
    with pytest.raises(ProtocolError):store.submit(messages[0],100.)
    store.submit(messages[0],1.)
    with pytest.raises(ProtocolError) as error:store.submit(messages[1],1.)
    assert error.value.code=='backpressure'
    changed=copy.deepcopy(messages[0]);changed['data']['emitted_pairs']+=1
    with pytest.raises(ProtocolError) as error:store.submit(changed,1.)
    assert error.value.code=='replay'


def test_gateway_discovers_and_receives_consecutive_observation_windows(tmp_path,config):
    from websockets.sync.client import connect
    store=OGSStore(tmp_path/'consecutive.sqlite');now=[0.]
    security,token=_registered_ogs_security(tmp_path)
    server=OGSWebSocketServer(store,lambda:now[0],credential_ref='vault:ogs-session',security=security)
    source=SatelliteStreamSource(store,lambda:now[0]);simulator=SimulatedAcquisitionSource(42)
    headers={'Authorization':'Bearer '+token}
    environment={'ground_positions':[(-22.953888889,-43.173611111),(-22.8606982,-43.2310552)],'ground_altitudes_m':[0.,0.]}
    try:
        with connect(f'ws://127.0.0.1:{server.port}/ogs/v2/status',additional_headers=headers) as status:
            for sequence,start in enumerate((0.,1.2),1):
                now[0]=start;cmd=replace(command(config,start=start),command_id=f'window-{sequence}')
                assert source.acquire(cmd,config,environment) is None
                status.send(json.dumps({'query':'status'}));advertised=json.loads(status.recv())
                assert advertised['physical_actuation'] is False
                assert advertised['observation_windows'][0]['start_s']==start
                assert 'command_id' not in advertised['observation_windows'][0]
                raw=simulator.acquire(cmd,config,environment).raw;now[0]=start+1.1
                for packet in packets(raw,cmd,source_id='ogs_gateway',origin='physical',sequence=sequence):
                    with connect(f"ws://127.0.0.1:{server.port}/ogs/v2/{packet['kind']}",additional_headers=headers,max_size=8*1024*1024) as client:
                        client.send(json.dumps(packet));assert json.loads(client.recv())['status']=='accepted'
                observed=source.acquire(cmd,config,environment)
                assert observed.sequence==sequence and process(observed.raw,config)['coincidences']>0
                status.send(json.dumps({'query':'status'}))
                assert not json.loads(status.recv())['observation_windows']
    finally:server.close()


def test_missing_credential_never_opens_gateway(tmp_path):
    store=OGSStore(tmp_path/'store.sqlite')
    with pytest.raises(ProtocolError):OGSWebSocketServer(store,lambda:0.,credential_ref='env:DOES_NOT_EXIST')
    with pytest.raises(ProtocolError):OGSWebSocketServer(store,lambda:0.,credential_ref='env:X',host='0.0.0.0')


def test_configured_freshness_and_clock_limits_apply_to_both_origins(tmp_path,config):
    raw,cmd,cfg=observation(config,housekeeping_max_age_s=12.,sync_tolerance_s=1e-5,timing_jitter_ps=3e6)
    assert raw['housekeeping']['valid_until_s']-raw['housekeeping']['at_s']==12.
    with pytest.raises(ProtocolError) as error:process(raw,config)
    assert error.value.code=='stale_ephemeris'
    store=OGSStore(tmp_path/'configured-limits.sqlite')
    for packet in packets(raw,cmd,source_id='ogs_gateway',origin='physical',sequence=1):store.submit(packet,1.1)
    measured=SatelliteStreamSource(store,lambda:1.1).acquire(cmd,cfg,{})
    assert process(measured.raw,cfg)['coincidences']==process(raw,cfg)['coincidences']


def test_ingress_persistence_quota_preserves_existing_records(tmp_path,config):
    raw,cmd,_=observation(config);messages=packets(raw,cmd,source_id='ogs_gateway',origin='physical',sequence=1)
    size=len(json.dumps(messages[0],sort_keys=True,ensure_ascii=False,separators=(',',':')).encode('utf-8'))+1
    store=OGSStore(tmp_path/'quota.sqlite',max_persisted_bytes=size)
    store.submit(messages[0],1.)
    with pytest.raises(ProtocolError) as error:store.submit(messages[2],1.)
    assert error.value.code=='storage_full'
    assert store.submit(messages[0],1.)['status']=='duplicate'
    assert store.status()['pending_packets']==1


def test_ambiguous_cluster_cannot_leave_a_false_unique_coincidence(config):
    raw,_,cfg=observation(config,coincidence_window_s=2e-11)
    for batch,times in zip(raw['tdc'],([100,110,120,1000],[110,120,130,1000])):
        batch.update(time_ps=times,detector=[0]*len(times),flight_time_ps=[0]*len(times),
                     propagation_delay_ps=0,clock_offset_ps=0)
    result=process(raw,cfg)
    # Every tag in the first cluster has multiple possible partners, even
    # after a greedy scan discards earlier hits. The isolated pair is unique.
    assert result['coincidences']==1
    assert result['ambiguous_gates_discarded']==3


def test_engine_ogs_preserves_explicit_simulated_origin(tmp_path,topology,monkeypatch):
    from quantum_autopilot.control_plane.topology import SimulatedTopologyStore
    from quantum_autopilot.network_engine.engine import NetworkEngine
    from quantum_autopilot.network_engine.sources import SimulatedInputSource
    journal=tmp_path/'simulated-loopback.sqlite'
    store=SimulatedTopologyStore(journal)
    store.initialize(topology)
    engine=NetworkEngine(store,journal,topology_id=topology.topology_id,source=SimulatedInputSource(seed=42))
    monkeypatch.setenv('ORION_LAB_SIMULATED_OGS','c'*40)
    try:
        engine.command('configure_technology_source',{'kind':'ogs_websocket','session_id':'RIO-SATELLITE',
            'port':0,'credential_ref':'env:ORION_LAB_SIMULATED_OGS','origin':'simulated'})
        assert engine._ogs_server.store.origin=='simulated'
    finally:
        engine.close_ogs()


def test_satellite_file_replay_preserves_simulation_model_and_clock(tmp_path,config):
    from quantum_autopilot.quantum_protocols.sources import MeasurementFileSource
    raw,cmd,cfg=observation(config)
    replay_record={
        **SimulatedAcquisitionSource(42).acquire(cmd,cfg,{
            'ground_positions':[(-22.953888889,-43.173611111),(-22.8606982,-43.2310552)],
            'ground_altitudes_m':[0.,0.],
        }).to_dict(),
        'source_id':'measurement_file','origin':'measurement_file',
    }
    capture=tmp_path/'synthetic-satellite-capture.jsonl'
    capture.write_text(json.dumps(replay_record)+'\n',encoding='utf-8')
    restored=MeasurementFileSource(capture).acquire(cmd,cfg,{})
    assert restored.origin=='measurement_file'
    assert restored.raw['parameter_provenance']['origin']=='simulated'
    assert restored.raw['parameter_provenance']['model_id']=='orion.satellite-poisson-events.v1'
    assert restored.raw['tdc']==raw['tdc']
    assert process(restored.raw,cfg)==process(raw,cfg)
