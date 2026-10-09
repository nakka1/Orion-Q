"""Raw receiver events, physically gated ISL and explicit stationary GEO."""
import copy
import json
from dataclasses import replace

import pytest

from quantum_autopilot.control_plane.channels import local_pair
from quantum_autopilot.control_plane.topology import NodeRole, SimulatedNodeState, SimulatedLink, SimulatedTopology
from quantum_autopilot.quantum_protocols.contracts import Acquisition, DeviceCommand, ProtocolConfig, ProtocolError
from quantum_autopilot.quantum_protocols.isl import ISL_CAPABILITIES, InterSatelliteAdapter, geometry_at, inter_satellite_geometry, isl_optics
from quantum_autopilot.quantum_protocols.isl_contracts import validate_isl_observation
from quantum_autopilot.quantum_protocols.orbits import GEO_ALTITUDE_KM, GEO_RADIUS_KM, geo_reference_orbit, position, satellite_state, validate_orbit
from quantum_autopilot.quantum_protocols.physics import C_KM_S, OMEGA_EARTH
from quantum_autopilot.quantum_protocols.registry import default_registry
from quantum_autopilot.quantum_protocols.runtime import ProtocolRuntime
from quantum_autopilot.quantum_protocols.sources import MeasurementFileSource, NoAcquisitionSource, SimulatedAcquisitionSource

pytestmark=pytest.mark.unit


def config(**parameters):
    return ProtocolConfig('GEO-ISL',('satellite_isl',),('GEO-A','GEO-B'),('GEO-A','GEO-B'),('ISL-AB',),
        parameters={'satellite_orbits':{'GEO-A':geo_reference_orbit(-60.),'GEO-B':geo_reference_orbit(0.)},
            'pair_source_hz':20000.,'block_s':.5,'timeout_s':4.,'max_attempts':2,
            'transmitter_diameter_m':2.,'receiver_diameter_m':4.,'divergence_urad':.4,
            'pointing_jitter_urad':.05,'tracking_latency_s':0.,'optical_efficiency':.8,
            'insertion_loss_db':0.,'background_cps':1.,'dark_count_cps':5.,
            'timing_jitter_ps':50.,'sync_tolerance_s':1e-6,**parameters})


def topology(cfg=None):
    cfg=cfg or config();nodes=[]
    for i,n in enumerate(cfg.endpoints):
        data={'technology_capabilities':list(ISL_CAPABILITIES)+(['satellite_source'] if i==0 else []),
            'inventory':{'kind':'satellite','origin':'test_simulated_GEO'},
            'technology_sessions':[cfg.to_dict()] if i==0 else []}
        nodes.append(SimulatedNodeState(n,NodeRole.ENDPOINT,0,'IDLE',telemetry=data))
    channels=local_pair('ISL-AB','GEO-B');channels['quantum']['protocol']='quantum-operation-record-v1'
    return SimulatedTopology('isl-test',1,tuple(nodes),
        (SimulatedLink('ISL-AB','GEO-A','GEO-B',42164.,.9,channels=channels,
                       technology={'session_id':cfg.session_id,'technologies':['satellite_isl']}),))


def raw_observation(cfg=None):
    cfg=cfg or config();source=SimulatedAcquisitionSource()
    command=DeviceCommand('ISL-CMD',cfg.session_id,'isl_block',cfg.path,('q1',),('c1',),0.,4.,1,{'duration_s':.5})
    observation=source.acquire(command,cfg,{'condition':cfg.scenario})
    return observation,command,cfg


def processed(observation,cfg,origin='simulated'):
    return InterSatelliteAdapter().process(observation.raw,{'parameters':cfg.parameters,'endpoints':cfg.endpoints,
        'duration_s':.5,'origin':origin,'environment':{}})


def test_explicit_geostationary_reference_is_fixed_for_days_and_ring_keeps_motion():
    orbit=geo_reference_orbit(-35.);validate_orbit(orbit)
    states=[position(orbit,t) for t in (0.,60.,3600.,86400.,604800.)]
    assert all(s['sat_longitude']==pytest.approx(-35.) and s['sat_latitude']==0. for s in states)
    assert all(s['velocity_ecef_km_s']==[0.,0.,0.] for s in states)
    assert states[0]['period_s']==pytest.approx(2*3.141592653589793/OMEGA_EARTH)
    assert states[0]['equatorial_altitude_km']==pytest.approx(GEO_RADIUS_KM-6378.137)
    ring=position(orbit,states[0]['period_s']/4.,earth_time_s=0.)
    assert ring['sat_longitude']==pytest.approx(55.)
    assert states[-1]['phase_turns']>7.


def test_geostationary_contract_does_not_silently_rewrite_other_orbits():
    orbit=geo_reference_orbit(0.);original=copy.deepcopy(orbit)
    for key,value in [('inclination_deg',10.),('eccentricity',.01),('orbital_class','LEO'),('altitude_km',500.)]:
        with pytest.raises(ValueError):validate_orbit({**orbit,key:value})
    assert orbit==original
    leo={**orbit,'model':'circular_reference','orbital_class':'LEO','altitude_km':500.,'inclination_deg':53.}
    assert position(leo,3600.)['sat_longitude']!=pytest.approx(position(leo,0.)['sat_longitude'])
    assert leo['altitude_km']==500. and leo['model']=='circular_reference'


def test_stationary_satellite_scene_has_full_orbit_track_without_geographic_drift():
    orbit=geo_reference_orbit(-35.)
    record={'device_id':'geo','node_id':'GEO-A','orbit':orbit,'configuration':{},'optical':{},
        'integration':{'data_origin':'simulated'},'operational_state':'enabled'}
    first=satellite_state(record,topology(),0.);later=satellite_state(record,topology(),1800.)
    assert later['ecef_km']==first['ecef_km']
    assert len(later['orbit_ecef_km'])==193 and later['orbit_ecef_km'][0]==pytest.approx(later['orbit_ecef_km'][-1])
    assert later['orbit_ecef_km'][0]!=later['orbit_ecef_km'][48]
    assert first['optical_payload_available'] is False and later['optical_payload_available'] is False
    assert first['transmitting_links'] == later['transmitting_links'] == []


@pytest.mark.parametrize('technologies', [None, [], ['quantum_memory']])
def test_orbit_only_metadata_never_infers_payload_or_validated_radio(technologies, monkeypatch):
    from quantum_autopilot.quantum_protocols import orbits

    orbit = geo_reference_orbit(-35., compatible_stations=['OGS'])
    record = {'device_id': 'legacy-orbit', 'node_id': 'GEO-A', 'orbit': orbit,
              'integration': {'data_origin': 'simulated'}}
    if technologies is not None:
        record['technologies'] = technologies
    original = copy.deepcopy(record)
    station = SimulatedNodeState('OGS', NodeRole.ENDPOINT, 0, 'IDLE',
        telemetry={'location': {'latitude': 0., 'longitude': -35., 'altitude_m': 15.}})
    scene = replace(topology(), nodes=topology().nodes + (station,))

    def unexpected_optics(*args, **kwargs):
        pytest.fail('Orbit-only records must not execute an optical payload model.')

    monkeypatch.setattr(orbits, 'satellite_optics', unexpected_optics)
    state = satellite_state(record, scene, 120.)
    received = state['stations']['OGS']
    assert len(state['orbit_ecef_km']) == 193
    assert state['sat_longitude'] == pytest.approx(-35.)
    assert state['optical_payload_available'] is False
    assert state['physical_tracking_validated'] is False
    assert state['transmitting_links'] == []
    assert received['range_km'] > 35000. and received['elevation_deg'] == pytest.approx(90.)
    assert received['optical_payload_available'] is False
    assert received['radio_link_validated'] is False
    assert received['in_communication_window'] is True
    assert not {'channel_transmission', 'link_budget_db', 'doppler_shift_hz', 'qber', 'fidelity'} & received.keys()
    assert record == original


def test_declared_optical_satellite_preserves_configured_optical_processing():
    from quantum_autopilot.control_plane.communicators import Communicator, defaults
    from quantum_autopilot.quantum_protocols.orbits import geometry
    from quantum_autopilot.quantum_protocols.physics import satellite_optics

    orbit = geo_reference_orbit(-35., compatible_stations=['OGS'])
    optical = {**defaults('optical'), 'wavelength_nm': 810., 'insertion_loss_db': 7.}
    configuration = {**defaults('satellite_doppler'), 'divergence_urad': 15.,
                     'pointing_jitter_urad': 1., 'min_elevation_deg': 10., 'zenith_optical_depth': .35}
    communicator = Communicator('optical-geo', 'GEO-A', 'Configured optical satellite',
        'satellite', 'intermediate', technologies=['satellite_doppler'], protocols=['BBM92'],
        capabilities=['satellite_source', 'tracking'], optical=optical,
        configuration={'satellite_doppler': configuration}, orbit=orbit)
    station = SimulatedNodeState('OGS', NodeRole.ENDPOINT, 0, 'IDLE',
        telemetry={'location': {'latitude': 0., 'longitude': -35., 'altitude_m': 15.}})
    scene = replace(topology(), nodes=topology().nodes + (station,))
    record = communicator.to_dict()
    original = copy.deepcopy(record)
    state = satellite_state(record, scene, 120.)
    expected = satellite_optics(geometry(orbit, 120., (0., -35.), 15.), {**optical, **configuration})
    assert state['optical_payload_available'] is True
    assert state['stations']['OGS'] == {**expected, 'in_communication_window': True, 'pointing_enabled': True}
    assert state['stations']['OGS']['channel_transmission'] > 0.
    assert state['transmitting_links'] == []  # Geometric admission alone is not photon reception.
    assert record == original


def test_isl_geometry_evaluates_earth_obstruction_range_and_inertial_point_ahead():
    cfg=config();states,geometry=geometry_at(cfg.parameters,cfg.endpoints,10.)
    assert geometry['visible'] and geometry['earth_clearance_scaled']>0.
    assert geometry['flight_time_s']==pytest.approx(geometry['range_km']/C_KM_S)
    assert geometry['point_ahead_urad']>0.
    assert geometry['radial_velocity_km_s']==0.
    blocked=config(satellite_orbits={'GEO-A':geo_reference_orbit(-170.),'GEO-B':geo_reference_orbit(10.)})
    _,g=geometry_at(blocked.parameters,blocked.endpoints,0.)
    assert not g['visible'] and g['earth_obstructed'] and 'earth_obstruction' in g['block_reasons']


@pytest.mark.parametrize('change,reason',[
    ({'max_range_km':1000.},'terminal_range_limit'),
    ({'pointing_enabled':False},'pointing_disabled'),
    ({'pointing_jitter_urad':100.},'receiver_field_of_view'),
    ({'receiver_alignment_error_urad':30.},'receiver_field_of_view'),
    ({'point_ahead_compensation':False},'receiver_field_of_view'),
    ({'point_ahead_calibration_error_urad':30.},'receiver_field_of_view'),
])
def test_terminal_physical_constraints_block_emission(change,reason):
    cfg=config(**change);_,g=geometry_at(cfg.parameters,cfg.endpoints,0.)
    assert not g['visible'] and g['channel_transmission']==0. and reason in g['block_reasons']


def test_sub_diffraction_beam_is_not_treated_as_a_free_performance_gain():
    cfg=config(transmitter_diameter_m=.1,divergence_urad=.001)
    _,g=geometry_at(cfg.parameters,cfg.endpoints,0.)
    assert g['effective_divergence_urad']==pytest.approx(g['diffraction_floor_urad'])
    assert g['effective_divergence_urad']>cfg.parameters['divergence_urad']


def test_point_ahead_is_compensated_operationally_rather_than_only_displayed():
    cfg=config();_,good=geometry_at(cfg.parameters,cfg.endpoints,0.)
    assert good['point_ahead_correction_urad']==good['point_ahead_urad']>0.
    assert good['point_ahead_residual_urad']==0.
    _,bad=geometry_at(config(point_ahead_compensation=False).parameters,cfg.endpoints,0.)
    assert bad['point_ahead_correction_urad']==0.
    assert bad['point_ahead_residual_urad']==bad['point_ahead_urad']
    assert bad['channel_transmission']==0. and not bad['visible']


def test_nominal_isl_is_admitted_explicitly_and_has_measured_raw_events():
    cfg=config();default_registry().validate(cfg,topology(cfg))
    observation,_,cfg=raw_observation(cfg);raw=observation.raw
    assert 'isl_schema' in raw and len(raw['housekeeping'])==2 and len(raw['tdc'])==2
    assert not set(raw)&{'coincidences','qber','fidelity','final_fidelity','conditional_key_estimate_bits'}
    result=processed(observation,cfg)
    assert result['coincidences']>0 and result['sifted_bits']>0
    assert result['satellite_housekeeping']==raw['housekeeping']
    assert result['measurement_resources']['stored_pairs']==0
    assert result['certified_secret_bits']==0
    assert processed(observation,cfg,'measurement_file')==result


def test_unconfigured_satellites_do_not_automatically_form_an_operational_isl():
    cfg=config();top=topology(cfg)
    unconfigured=replace(top,nodes=tuple(replace(n,telemetry={}) for n in top.nodes))
    with pytest.raises(ProtocolError) as exc:default_registry().validate(cfg,unconfigured)
    assert exc.value.code=='incompatible_node'
    unequal=config(device_parameters={'GEO-B':{'wavelength_nm':1550.}})
    with pytest.raises(ProtocolError) as exc:default_registry().validate(unequal,topology(unequal))
    assert exc.value.code=='incompatible_terminal'
    mixed=replace(cfg,technologies=('satellite_isl','quantum_memory'))
    with pytest.raises(ProtocolError) as exc:default_registry().validate(mixed,topology(mixed))
    assert exc.value.code=='unsupported_composition'


def test_earth_occlusion_and_loss_change_actual_received_pairs():
    base,_,cfg=raw_observation();good=processed(base,cfg)['coincidences']
    bad_cfg=config(extra_loss_db=100.);bad,_,_=raw_observation(bad_cfg)
    assert processed(bad,bad_cfg)['coincidences']<good/10
    hidden=config(satellite_orbits={'GEO-A':geo_reference_orbit(-170.),'GEO-B':geo_reference_orbit(10.)})
    hidden_observation,_,_=raw_observation(hidden);result=processed(hidden_observation,hidden)
    assert not result['available'] and result['coincidences']==0 and 'earth_obstruction' in result['reason']
    assert hidden_observation.raw['housekeeping'] and hidden_observation.raw['emitted_pairs']>0


def test_synchronization_and_delay_correction_affect_real_matching():
    base,_,cfg=raw_observation();good=processed(base,cfg)['coincidences']
    bad_cfg=config(propagation_compensation=False);bad,_,_=raw_observation(bad_cfg)
    assert processed(bad,bad_cfg)['coincidences']<good/10
    jitter=config(timing_jitter_ps=1e6,sync_tolerance_s=1e-5);noisy,_,_=raw_observation(jitter)
    assert processed(noisy,jitter)['coincidences']<good/10
    raw=copy.deepcopy(base.raw);raw['tdc'][1]['clock_uncertainty_ps']=1e9
    with pytest.raises(ProtocolError) as exc:validate_isl_observation(raw,endpoints=cfg.endpoints,parameters=cfg.parameters)
    assert exc.value.code=='unsynchronized'


@pytest.mark.parametrize('mutation,code',[
    (lambda r:r.update(qber=.01),'precomputed_result'),
    (lambda r:r['tdc'][0].update(lost_tags=1),'tdc_overflow'),
    (lambda r:r['tdc'][1].update(station_id='other'),'wrong_station'),
    (lambda r:r['housekeeping']['GEO-B'].update(valid_until_s=0.),'invalid_parameter'),
    (lambda r:r['housekeeping']['GEO-B'].update(frame='TEME'),'invalid_ephemeris'),
])
def test_invalid_or_precomputed_isl_observations_are_rejected(mutation,code):
    obs,_,cfg=raw_observation();mutation(obs.raw)
    with pytest.raises(ProtocolError) as exc:processed(obs,cfg)
    assert exc.value.code==code


def test_detector_block_is_bounded_and_never_silently_clipped():
    with pytest.raises(ProtocolError) as exc:raw_observation(config(pair_source_hz=5e6))
    assert exc.value.code=='tdc_overflow'


def test_runtime_isl_uses_common_pipeline_with_classical_causality_and_resource_costs(tmp_path):
    cfg=config();top=topology(cfg);runtime=ProtocolRuntime(tmp_path/'isl.sqlite',top.topology_id,source=SimulatedAcquisitionSource())
    for tick in (1.,2.,3.,4.):runtime.advance(top,tick)
    state=runtime.snapshot()['sessions'][cfg.session_id]
    assert state['status']=='operational' and state['metrics']['coincidences']>0
    assert state['metrics']['memory_occupancy']==0 and state['metrics']['measurement_resources']['stored_pairs']==0
    assert state['metrics']['observation_origin']=='simulated'
    path=tmp_path/'events.jsonl';export=runtime.export_events(path)
    assert export['satellite_acquisitions']>0 and not export['physical_action_performed']
    rows=[json.loads(x) for x in path.read_text(encoding='utf-8').splitlines()]
    for sent in (r for r in rows if r['event']=='classical_transmitted'):
        received=next((r for r in rows if r['event']=='classical_received' and r['command_id']==sent['command_id']),None)
        if received:
            assert received['at_s']>=sent['detail']['arrival_at_s']>sent['at_s']
            assert sent['detail']['arrival_at_s']-sent['at_s']>=state['metrics']['isl_geometry']['flight_time_s']
    assert {'acquired','validated','synchronized','quantum_transmission_observed','protocol_processed',
            'classical_transmitted','classical_received','metrics_computed'}<={r['event'] for r in rows}


def test_measurement_file_uses_identical_isl_validation_and_processing(tmp_path):
    obs,cmd,cfg=raw_observation();record=replace(obs,origin='measurement_file',source_id='measurement_file')
    path=tmp_path/'capture.jsonl';path.write_text(json.dumps(record.to_dict())+'\n',encoding='utf-8')
    replay=MeasurementFileSource(path).acquire(cmd,cfg,{})
    assert processed(replay,cfg,'measurement_file')==processed(obs,cfg)


def test_invalid_sync_retries_with_fresh_commands_and_cannot_publish_metrics(tmp_path):
    cfg=config(clock_offset_s=.01);top=topology(cfg)
    runtime=ProtocolRuntime(tmp_path/'sync.sqlite',top.topology_id,source=SimulatedAcquisitionSource())
    for tick in (1.,2.,3.,4.):runtime.advance(top,tick)
    state=runtime.snapshot()['sessions'][cfg.session_id]
    assert 'unsynchronized' in state['reason'] and not state['metrics'].get('coincidences')
    assert state['counters'].get('retries',0)>0


def test_measurement_file_passes_same_full_runtime_pipeline_with_exact_command_binding(tmp_path):
    cfg=config();top=topology(cfg);source=SimulatedAcquisitionSource()
    simulated=ProtocolRuntime(tmp_path/'simulated.sqlite',top.topology_id,source=source)
    simulated.advance(top,0.)
    captured=Acquisition.from_dict(source.records[0])
    file_record=replace(captured,origin='measurement_file',source_id='measurement_file')
    path=tmp_path/'simulated_capture_for_file_replay.jsonl'
    path.write_text(json.dumps(file_record.to_dict())+'\n',encoding='utf-8')
    replay=ProtocolRuntime(tmp_path/'replay.sqlite',top.topology_id,source=MeasurementFileSource(path))
    simulated.advance(top,1.);replay.advance(top,1.)
    actual=replay.snapshot()['sessions'][cfg.session_id]
    expected=simulated.snapshot()['sessions'][cfg.session_id]
    assert actual['metrics']['coincidences']==expected['metrics']['coincidences']>0
    assert actual['metrics']['qber']==expected['metrics']['qber']
    assert actual['metrics']['observation_origin']=='measurement_file'
    assert actual['metrics']['parameter_provenance']['origin']=='simulated'
    # A recorded simulated block is still transparently identified as such.
    report=replay.export_events(tmp_path/'replay-events.jsonl')
    assert report['satellite_acquisitions']==1
    events=[json.loads(x) for x in (tmp_path/'replay-events.jsonl').read_text(encoding='utf-8').splitlines()]
    assert {'acquired','validated','synchronized','protocol_processed','classical_received','metrics_computed'}<={e['event'] for e in events}


def test_absent_isl_source_has_timeouts_retries_and_no_usable_result(tmp_path):
    cfg=config(timeout_s=1.);top=topology(cfg)
    runtime=ProtocolRuntime(tmp_path/'absent.sqlite',top.topology_id,source=NoAcquisitionSource())
    runtime.advance(top,3.)
    state=runtime.snapshot()['sessions'][cfg.session_id]
    assert state['status']=='unavailable' and 'timeout' in state['reason']
    assert state['counters'].get('retries',0)>0 and state['counters'].get('observations',0)==0
    assert not state['metrics'].get('coincidences') and not state['metrics']['memory_occupancy']


def test_supplemental_operation_channel_cannot_bypass_disabled_parent_fibre(tmp_path):
    cfg=config();top=topology(cfg)
    parent=replace(top.links[0],link_id='PARENT',channels=local_pair('PARENT','GEO-B'),technology=None,active=False)
    operational=replace(top.links[0],technology={**top.links[0].technology,'source_span_id':'PARENT'})
    top=replace(top,links=(parent,operational))
    runtime=ProtocolRuntime(tmp_path/'parent.sqlite',top.topology_id,source=SimulatedAcquisitionSource())
    runtime.advance(top,2.)
    state=runtime.snapshot()['sessions'][cfg.session_id]
    assert state['status']=='unavailable' and state['counters'].get('observations',0)==0
