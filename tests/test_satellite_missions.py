"""Mission-specific decode of captured public bytes, with independent wire expectations."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from quantum_autopilot.quantum_protocols.satellite_missions import (
    DisabledMissionError, FrameDecodeError, MissionDecodeError, Registry,
    CSP1_BEACON0_PROFILE, SATNOGS_BEACON0_PROFILE, Suomi100Decoder, UnknownMissionError, UnknownProfileError,
    crc32c, decode_frame, default_registry,
)


pytestmark = pytest.mark.unit
FIXTURES = Path(__file__).parent / 'fixtures' / 'satellite_public'
PROFILE = SATNOGS_BEACON0_PROFILE


def frame(suffix='135520'):
    return (FIXTURES / f'suomi100-frame-20261008-{suffix}.bin').read_bytes()


def replace_checksums(data):
    """Make altered bytes internally consistent; profile checks must still reject them."""
    result = bytearray(data)
    result[88:92] = crc32c(bytes(result[4:88])).to_bytes(4, 'big')
    result[92:96] = crc32c(bytes(result[:92])).to_bytes(4, 'big')
    return bytes(result)


@pytest.mark.parametrize('suffix,battery,com_temp,rssi,rferr,obc_temp,utc', (
    ('135420', 7295, [112, 111], -114, -337, [115, 119], '2033-10-06T19:21:00+00:00'),
    ('135520', 7285, [106, 104], -117, -1399, [108, 112], '2033-10-06T19:22:00+00:00'),
))
def test_real_public_frames_decode_engineering_without_qualifying_them(
        suffix, battery, com_temp, rssi, rferr, obc_temp, utc):
    captured = frame(suffix)
    result = decode_frame('suomi100', captured, profile=PROFILE)
    assert result['mission_id'] == 'suomi100' and result['norad_cat_id'] == 43804
    assert result['decoder_id'] == 'suomi100-stdlib' and result['decoder_version'] == '2'
    assert result['frame_bytes'] == 96 and result['frame_sha256'] == hashlib.sha256(captured).hexdigest()
    assert result['header'] == {'priority': 2, 'source': 1, 'destination': 10,
                                'destination_port': 30, 'source_port': 0, 'reserved': 0,
                                'fragmentation': False, 'hmac': False, 'xtea': False, 'rdp': False, 'crc': True}
    assert result['payload']['beacon_type'] == 0
    assert result['payload']['eps']['batt_v'] == battery
    assert result['payload']['eps']['pv_v'] == [291, 291, 311]
    assert result['payload']['eps']['batt_mode'] == 3
    assert result['payload']['com']['temp'] == com_temp
    assert result['payload']['com']['rssi'] == rssi and result['payload']['com']['rferr'] == rferr
    assert result['payload']['obc']['temp'] == obc_temp
    assert result['clock']['qualified'] is False and result['clock']['received_at_inferred'] is False
    for group in ('eps', 'com', 'obc'):
        timestamp = result['clock']['onboard_timestamps'][group]
        assert timestamp['interpreted_utc'] == utc
        assert timestamp['unix_s'] == result['payload'][group]['timestamp_unix_s']
    assert result['payload']['eps']['timestamp_unix_s'] == int.from_bytes(captured[5:9], 'big')
    assert result['qualification']['status'] == 'decoded_not_qualified'
    for key in ('housekeeping_validated', 'ogs_validated', 'qkd_validated', 'physical_hardware_validated'):
        assert result['qualification'][key] is False
    assert result['units'] == {'qualified': False, 'status': 'raw_unscaled', 'physical_units': None, 'scales': None}
    integrity = result['integrity']
    assert integrity['verified'] is True and integrity['authenticated'] is False
    assert integrity['canonical_csp_frame'] is False and integrity['profile_scope'] == 'observed_noncanonical'
    assert [item['byte_range'] for item in integrity['checksums']] == [[4, 88], [0, 92]]
    assert all(item['passed'] is True for item in integrity['checksums'])
    assert 'received_at' not in result and 'origin' not in result and 'qber' not in result['payload']
    assert json.loads(json.dumps(result, allow_nan=False)) == result
    assert frame(suffix) == captured


def test_fixture_provenance_and_crc_parameters_are_independent_verified_facts():
    provenance = json.loads((FIXTURES / 'provenance.json').read_text())
    assert provenance['license'] == 'CC-BY-SA-4.0' and provenance['norad_cat_id'] == 43804
    assert provenance['observation_id'] == 15146588
    for record in provenance['fixtures']:
        data = (FIXTURES / record['file']).read_bytes()
        assert len(data) == record['bytes'] == 96
        assert hashlib.sha256(data).hexdigest() == record['sha256']
        assert '/15146588/data_15146588_2026-10-08T13-' in record['source_url']
    assert crc32c(b'123456789') == 0xe3069283
    assert crc32c(b'') == 0
    # Literal checksums from the captured wire, independently of output formatting.
    assert crc32c(frame('135420')[4:88]) == 0xd8afea9a
    assert crc32c(frame('135420')[:92]) == 0xade9f766
    assert crc32c(frame()[4:88]) == 0xa4db95f3
    assert crc32c(frame()[:92]) == 0xbcc99bf4
    assert crc32c(frame()[4:92]) != int.from_bytes(frame()[92:], 'big')


@pytest.mark.parametrize('length', (0, 1, 3, 4, 5, 16, 53, 67, 87, 88, 91, 92, 95, 97))
def test_observed_profile_rejects_truncated_or_extended_frame(length):
    captured = frame()
    changed = captured[:length] if length < 96 else captured + b'\x00'
    with pytest.raises(FrameDecodeError) as error:
        decode_frame('suomi100', changed, profile=PROFILE)
    assert error.value.code == 'invalid_frame_length'


@pytest.mark.parametrize('offset', (0, 4, 15, 50, 71, 87, 88, 91, 92, 95))
def test_header_payload_or_checksum_corruption_is_rejected(offset):
    captured = frame()
    changed = bytearray(captured)
    changed[offset] ^= 1
    with pytest.raises(FrameDecodeError):
        decode_frame('suomi100', bytes(changed), profile=PROFILE)
    assert frame() == captured


@pytest.mark.parametrize('offset', (0, 1, 2, 3, 4))
def test_recomputed_checksums_do_not_admit_a_different_csp_header_or_beacon(offset):
    changed = bytearray(frame())
    changed[offset] ^= 1
    with pytest.raises(FrameDecodeError) as error:
        decode_frame('suomi100', replace_checksums(changed), profile=PROFILE)
    assert error.value.code == ('unsupported_beacon' if offset == 4 else 'csp_profile_mismatch')


def test_checksum_validity_alone_does_not_qualify_changed_engineering():
    changed = bytearray(frame())
    changed[15] ^= 1
    result = decode_frame('suomi100', replace_checksums(changed), profile=PROFILE)
    assert result['payload']['eps']['batt_v'] != 7285
    assert result['integrity']['verified'] is True and result['integrity']['authenticated'] is False
    assert result['qualification']['housekeeping_validated'] is False
    assert result['units']['qualified'] is False and result['clock']['qualified'] is False


@pytest.mark.parametrize('suffix', ('135420', '135520'))
def test_explicit_csp1_profile_accepts_crc_checked_92_byte_test_derivative(suffix):
    # This is a deliberately derived test input, not a claim of a 92-byte RF capture.
    captured = frame(suffix)
    csp1 = captured[:92]
    result = decode_frame('suomi100', csp1, profile=CSP1_BEACON0_PROFILE)
    observed = decode_frame('suomi100', captured, profile=PROFILE)
    assert result['frame_bytes'] == 92 and result['frame_sha256'] == hashlib.sha256(csp1).hexdigest()
    assert result['payload'] == observed['payload'] and result['header'] == observed['header']
    assert result['integrity']['profile_scope'] == 'csp1_payload_crc32c'
    assert result['integrity']['canonical_csp_frame'] is True
    assert result['integrity']['checksums'] == observed['integrity']['checksums'][:1]
    assert result['integrity']['authenticated'] is False
    assert result['clock']['qualified'] is False and result['units']['qualified'] is False
    assert result['qualification']['housekeeping_validated'] is False
    assert result['qualification']['ogs_validated'] is False and result['qualification']['qkd_validated'] is False
    assert frame(suffix) == captured


@pytest.mark.parametrize('length', (0, 4, 87, 88, 89, 91, 93, 96))
def test_csp1_profile_requires_exact_length_and_never_removes_outer_bytes(length):
    with pytest.raises(FrameDecodeError) as error:
        decode_frame('suomi100', frame()[:length], profile=CSP1_BEACON0_PROFILE)
    assert error.value.code == 'invalid_frame_length'


@pytest.mark.parametrize('offset', (0, 1, 2, 3, 4, 9, 15, 53, 68, 87, 88, 91))
def test_csp1_profile_rejects_header_beacon_payload_and_crc_corruption(offset):
    changed = bytearray(frame()[:92])
    changed[offset] ^= 1
    with pytest.raises(FrameDecodeError):
        decode_frame('suomi100', bytes(changed), profile=CSP1_BEACON0_PROFILE)


@pytest.mark.parametrize('offset', (0, 1, 2, 3, 4))
def test_csp1_header_and_beacon_still_checked_with_valid_payload_crc(offset):
    changed = bytearray(frame()[:92])
    changed[offset] ^= 1
    changed[88:92] = crc32c(bytes(changed[4:88])).to_bytes(4, 'big')
    with pytest.raises(FrameDecodeError) as error:
        decode_frame('suomi100', bytes(changed), profile=CSP1_BEACON0_PROFILE)
    assert error.value.code == ('unsupported_beacon' if offset == 4 else 'csp_profile_mismatch')


@pytest.mark.parametrize('profile', ('canonical-csp1', '', None))
def test_profile_is_explicit_and_never_guessed(profile):
    with pytest.raises(UnknownProfileError):
        decode_frame('suomi100', frame(), profile=profile)


def test_profile_argument_is_required():
    with pytest.raises(TypeError):
        decode_frame('suomi100', frame())


@pytest.mark.parametrize('value', (bytearray(b'abc'), 'abc', None, 96))
def test_mutable_or_nonbyte_input_is_rejected(value):
    with pytest.raises(FrameDecodeError) as error:
        decode_frame('suomi100', value, profile=PROFILE)
    assert error.value.code == 'invalid_frame_type'


class TestDecoder:
    __test__ = False
    version = 'test-1'
    profiles = ('test-profile',)

    def __init__(self, label):
        self.decoder_id = label
        self.calls = 0
        self.cached = {'header': {}, 'payload': {'label': label}, 'integrity': {},
                       'clock': {}, 'qualification': {}, 'units': {}}

    def decode(self, frame, *, profile):
        self.calls += 1
        self.cached['payload']['input'] = list(frame)
        return self.cached


def test_registry_switches_decoders_without_retaining_another_mission_result():
    registry = Registry()
    a, b = TestDecoder('decoder-a'), TestDecoder('decoder-b')
    registry.register('mission-a', a)
    registry.register('mission-b', b)
    first = registry.decode_frame('mission-a', b'A', profile='test-profile')
    first['payload']['label'] = 'caller mutation'
    second = registry.decode_frame('mission-b', b'B', profile='test-profile')
    third = registry.decode_frame('mission-a', b'C', profile='test-profile')
    assert registry.resolve('mission-a') is a and registry.resolve('mission-b') is b
    assert second['payload'] == {'label': 'decoder-b', 'input': [66]}
    assert third['payload'] == {'label': 'decoder-a', 'input': [67]}
    assert first['payload']['input'] == [65]
    assert a.calls == 2 and b.calls == 1
    assert second['mission_id'] == 'mission-b' and third['decoder_id'] == 'decoder-a'


def test_registry_unknown_disabled_replaced_and_reenabled_are_distinct():
    registry = Registry()
    with pytest.raises(UnknownMissionError):
        registry.resolve('missing')
    with pytest.raises(UnknownMissionError):
        registry.set_enabled('missing', True)
    old, new = TestDecoder('old'), TestDecoder('new')
    registry.register('test', old, enabled=False)
    assert registry.describe()['test']['enabled'] is False
    with pytest.raises(DisabledMissionError):
        registry.decode_frame('test', b'X', profile='test-profile')
    registry.set_enabled('test', True)
    assert registry.resolve('test') is old
    with pytest.raises(MissionDecodeError) as error:
        registry.register('test', new)
    assert error.value.code == 'duplicate_mission'
    registry.register('test', new, replace=True)
    assert registry.resolve('test') is new


def test_received_replay_and_simulation_call_the_same_decoder_contract():
    registry = default_registry()
    decoder = registry.resolve('suomi100')
    assert isinstance(decoder, Suomi100Decoder)
    results = [registry.decode_frame('suomi100', frame(), profile=PROFILE) for _mode in
               ('received', 'comparison_replay', 'simulated')]
    assert results[0] == results[1] == results[2]
    registry.set_enabled('suomi100', False)
    with pytest.raises(DisabledMissionError):
        registry.decode_frame('suomi100', frame(), profile=PROFILE)
    assert decode_frame('suomi100', frame(), profile=PROFILE) == results[0]


@pytest.mark.parametrize('failure', ('missing-section', 'non-dict-section', 'nonfinite', 'identity', 'exception'))
def test_registry_rejects_invalid_or_failing_extension_decoder(failure):
    decoder = TestDecoder('test')
    if failure == 'missing-section':
        del decoder.cached['clock']
    elif failure == 'non-dict-section':
        decoder.cached['clock'] = []
    elif failure == 'nonfinite':
        decoder.cached['payload']['invalid'] = float('nan')
    elif failure == 'identity':
        decoder.cached['mission_id'] = 'another'
    else:
        def fail(frame, *, profile):
            raise RuntimeError('extension failure')
        decoder.decode = fail
    before = deepcopy(decoder.cached)
    registry = Registry()
    registry.register('test', decoder)
    with pytest.raises(MissionDecodeError) as error:
        registry.decode_frame('test', b'X', profile='test-profile')
    assert error.value.code == ('decoder_failed' if failure == 'exception' else 'invalid_decoder_result')
    assert set(before).issubset(decoder.cached)


@pytest.mark.parametrize('mission_id', ('SUOMI100', '', '../x', None))
def test_registry_rejects_ambiguous_or_invalid_mission_identifiers(mission_id):
    with pytest.raises(MissionDecodeError) as error:
        Registry().register(mission_id, TestDecoder('test'))
    assert error.value.code == 'invalid_mission_id'
