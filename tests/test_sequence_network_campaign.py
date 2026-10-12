"""Negative evidence contracts, independent of native scientific results."""
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import run_sequence_network_campaign as campaign


@pytest.mark.parametrize('data', ['{"a":1,"a":2}', '{"a":NaN}', '{"a":Infinity}'])
def test_duplicate_or_nonfinite_inputs_rejected(tmp_path, data):
    path = tmp_path / 'input.json'
    path.write_text(data)
    with pytest.raises(ValueError):
        campaign.load(path)


def test_evidence_is_never_overwritten(tmp_path):
    path = tmp_path / 'evidence.json'
    campaign.write(path, {'a': 1})
    with pytest.raises(FileExistsError):
        campaign.write(path, {'a': 2})
    assert campaign.load(path) == {'a': 1}


def test_freeze_refuses_changed_source_or_input(tmp_path, monkeypatch):
    sources = {'source.py': 'abc'}
    monkeypatch.setattr(campaign, 'inventory', lambda: sources)
    campaign.write(tmp_path / 'protocol.json', {'criteria': 1})
    campaign.write(tmp_path / 'input.json', {'a': 1})
    campaign.write(tmp_path / 'freeze.json', {'sources': sources, 'protocol_sha256': campaign.sha(tmp_path / 'protocol.json'),
                                             'inputs': {'input.json': campaign.sha(tmp_path / 'input.json')}})
    assert campaign.check_freeze(tmp_path)['sources'] == sources
    (tmp_path / 'input.json').write_text('{}')
    with pytest.raises(ValueError, match='input changed'):
        campaign.check_freeze(tmp_path)
    sources['source.py'] = 'def'
    with pytest.raises(ValueError, match='sources or criteria'):
        campaign.check_freeze(tmp_path)


def test_prospective_protocol_never_claims_full_hybrid_validation():
    protocol = json.loads(campaign.DEFAULT_PROTOCOL.read_text())
    assert len(protocol['seeds']) == 3
    assert protocol['native_demand_start_s'] >= .05
    assert 'does not validate' in protocol['acceptance']['no_blanket_pass']
    assert 'TF-QKD' in protocol['parameter_mapping']['unsupported']


def test_all_reference_scenarios_have_valid_prospective_resource_and_time_contract():
    from sequence_network_backend import validate_payload
    node_ids = ['A::RIO-ENTANGLEMENT', 'R::RIO-ENTANGLEMENT', 'B::RIO-ENTANGLEMENT']
    base = {'schema': 'orionq.sequence-network-input.v1', 'scenario_id': 'fixture', 'topology_sha256': 'a'*64,
            'seed': 1, 'duration_s': .1, 'nodes': [{'id': n} for n in node_ids],
            'links': [{'id': f'link-{i}', 'a': a, 'b': b, 'length_km': 1., 'attenuation_db_km': .2,
                       'classical_delay_s': .001, 'protocol': 'entanglement_swapping'}
                      for i, (a, b) in enumerate(zip(node_ids, node_ids[1:]))],
            'demands': [{'id': 'RIO-ENTANGLEMENT', 'source': node_ids[0], 'target': node_ids[-1]}]}
    scenarios = list(campaign.reference_requests(base))
    assert len(scenarios) == 6
    for scenario in scenarios:
        validate_payload(scenario)
    contention = next(s for s in scenarios if s['scenario_id'] == 'reference_contention_release')
    assert contention['demands'][0]['memory_count'] * 2 == contention['nodes'][1]['memory_count']


def test_native_result_refuses_unbound_request_before_comparison(tmp_path):
    req = {'seed': 1, 'scenario_id': 'x', 'topology_sha256': 'a'*64}
    row = {'schema': 'orionq.sequence-network-output.v1', 'backend': 'SeQUeNCe', **req, 'request_sha256': 'b'*64}
    with pytest.raises(ValueError, match='request hash'):
        campaign.native_checks(row, req, tmp_path)
