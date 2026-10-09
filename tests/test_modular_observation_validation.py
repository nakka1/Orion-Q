"""Invalid measurement metadata must be refused before it reaches Qt widgets."""
import pytest
from quantum_autopilot.quantum_protocols.contracts import ProtocolError
from quantum_autopilot.quantum_protocols.protocols import SwappingAdapter, TwinFieldAdapter
from quantum_autopilot.quantum_protocols.physics import circular_ephemeris


@pytest.mark.parametrize('metadata', [
    {'optical_transmission': 'broken'}, {'effective_length_km': -1},
    {'memory_writes': True}, {'memory_writes': 2},
])
def test_generation_rejects_invalid_telemetry(metadata):
    with pytest.raises(ProtocolError):
        SwappingAdapter().process({'attempts': 1, 'heralds': 0, **metadata}, {'operation': 'generate'})


@pytest.mark.parametrize('metadata', [
    {'phase_reference_residual_rad': 'broken'}, {'arm_transmissions': [-.1, .3]},
    {'arm_transmissions': [.5]}, {'effective_arm_lengths_km': [1, True]},
])
def test_tf_rejects_invalid_telemetry(metadata):
    valid = {'z_trials': 1000, 'z_clicks': 10, 'z_errors': 1,
             'x_trials': 1000, 'x_clicks': 8, 'x_errors': 0,
             'vacuum': {'trials': 1000, 'clicks': 0},
             'weak': {'trials': 1000, 'clicks': 5},
             'strong': {'trials': 1000, 'clicks': 15}}
    TwinFieldAdapter().process(valid, {'parameters': {}})
    with pytest.raises(ProtocolError):
        TwinFieldAdapter().process({**valid, **metadata}, {'parameters': {}})


@pytest.mark.parametrize('inclination', [0., 10., 170., 180.])
def test_orbit_does_not_change_unreachable_parameters(inclination):
    with pytest.raises(ValueError, match='reference latitude'):
        circular_ephemeris(0.,latitude=-23.,longitude=-43.,inclination_deg=inclination)


@pytest.mark.parametrize('inclination', [0., 60., 120., 180.])
def test_valid_equatorial_and_retrograde_orbits(inclination):
    geometry=circular_ephemeris(0.,latitude=0.,longitude=0.,inclination_deg=inclination)
    assert geometry['range_km']==pytest.approx(500.)
    assert geometry['elevation_deg']==pytest.approx(90.)


@pytest.mark.parametrize('inclination', [0., 10., 170., 180.])
def test_unreachable_orbit_is_rejected_before_configuration_persistence(tmp_path,inclination):
    from quantum_autopilot.network_engine.institutional import brazil_institutions
    from quantum_autopilot.quantum_protocols.topology import expand_global
    from quantum_autopilot.quantum_protocols.runtime import ProtocolRuntime
    topology,_=expand_global(brazil_institutions())
    runtime=ProtocolRuntime(tmp_path/'orbit.sqlite',topology.topology_id)
    runtime.sync(topology)
    before=runtime.sessions['RIO-SATELLITE'].config.to_dict()
    with pytest.raises(ProtocolError,match='inclination cannot reach'):
        runtime.configure('RIO-SATELLITE',parameters={'inclination_deg':inclination})
    runtime.sync(topology)
    assert runtime.sessions['RIO-SATELLITE'].config.to_dict()==before
