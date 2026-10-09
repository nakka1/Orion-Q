"""Explicit mission decoders; engineering bytes never establish OGS/QKD evidence.

The Suomi100 implementation is independently written using Python's standard
library. It imports or vendors no gr-satellites/Construct code. Wire-layout facts
were checked against the public gr-satellites Suomi100 definition at commit
cb5d0fca22ed439e9ce029516b755491a57c5764 and SatNOGS observation 15146588:
https://github.com/daniestevez/gr-satellites/blob/cb5d0fca22ed439e9ce029516b755491a57c5764/python/telemetry/suomi100.py

Two separate Beacon0 profiles require CSP header 82a78001 and an 88-byte
engineering prefix. The observed 96-byte profile requires two CRC32C scopes;
the explicit CSP1.x profile is exactly 92 bytes with a payload-only CRC32C.
The latter coverage follows libcsp src/csp_crc32.c at commit
583c9ebc23b9193c2c6800fdff6a182e13df03e7 (non-CSP_21 append path):
https://github.com/libcsp/libcsp/blob/583c9ebc23b9193c2c6800fdff6a182e13df03e7/src/csp_crc32.c
The profiles are never inferred or transformed automatically; an 88-byte frame
without CRC is rejected. The meaning of the observed extra checksum layer is
not inferred. Raw units and onboard clocks remain unqualified in both profiles.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import re
import struct
from typing import Protocol


SATNOGS_BEACON0_PROFILE = 'satnogs-beacon0-dual-crc32c-v1'
CSP1_BEACON0_PROFILE = 'csp1-beacon0-crc32c-v1'
SCHEMA = 'orion.satellite-mission-decode.v1'


class MissionDecodeError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class UnknownMissionError(MissionDecodeError):
    def __init__(self, mission_id):
        super().__init__('unknown_mission', f'No decoder registered for mission {mission_id!r}')


class DisabledMissionError(MissionDecodeError):
    def __init__(self, mission_id):
        super().__init__('disabled_mission', f'Decoder disabled for mission {mission_id!r}')


class UnknownProfileError(MissionDecodeError):
    def __init__(self, profile):
        super().__init__('unknown_profile', f'Unsupported explicit frame profile {profile!r}')


class FrameDecodeError(MissionDecodeError):
    """Malformed bytes, checksum mismatch or a frame outside the selected profile."""


class Decoder(Protocol):
    decoder_id: str
    version: str
    profiles: tuple[str, ...]

    def decode(self, frame: bytes, *, profile: str) -> dict: ...


def _mission_identifier(mission_id):
    if not isinstance(mission_id, str) or re.fullmatch(r'[a-z0-9][a-z0-9._-]{0,63}', mission_id) is None:
        raise MissionDecodeError('invalid_mission_id', 'An explicit lowercase mission identifier is required')
    return mission_id


class Registry:
    """Choose the same mission decoder for received, replayed or simulated bytes."""

    def __init__(self):
        self._decoders: dict[str, tuple[Decoder, bool]] = {}

    def register(self, mission_id: str, decoder: Decoder, *, enabled=True, replace=False):
        mission_id = _mission_identifier(mission_id)
        profiles = getattr(decoder, 'profiles', None)
        if (not isinstance(getattr(decoder, 'decoder_id', None), str) or not decoder.decoder_id
                or not isinstance(getattr(decoder, 'version', None), str) or not decoder.version
                or not isinstance(profiles, tuple) or not profiles
                or any(not isinstance(value, str) or not value for value in profiles)
                or len(set(profiles)) != len(profiles) or not callable(getattr(decoder, 'decode', None))):
            raise MissionDecodeError('invalid_decoder', 'Decoder identity, version, explicit profiles and decode required')
        if type(enabled) is not bool or type(replace) is not bool:
            raise MissionDecodeError('invalid_registry_option', 'enabled and replace must be booleans')
        if mission_id in self._decoders and not replace:
            raise MissionDecodeError('duplicate_mission', f'Mission {mission_id!r} is already registered')
        self._decoders[mission_id] = (decoder, enabled)

    def set_enabled(self, mission_id: str, enabled: bool):
        mission_id = _mission_identifier(mission_id)
        if type(enabled) is not bool:
            raise MissionDecodeError('invalid_registry_option', 'enabled must be a boolean')
        if mission_id not in self._decoders:
            raise UnknownMissionError(mission_id)
        decoder, _previous = self._decoders[mission_id]
        self._decoders[mission_id] = (decoder, enabled)

    def resolve(self, mission_id: str) -> Decoder:
        mission_id = _mission_identifier(mission_id)
        if mission_id not in self._decoders:
            raise UnknownMissionError(mission_id)
        decoder, enabled = self._decoders[mission_id]
        if not enabled:
            raise DisabledMissionError(mission_id)
        return decoder

    def describe(self) -> dict:
        return {mission_id: {'decoder_id': decoder.decoder_id, 'decoder_version': decoder.version,
                             'profiles': list(decoder.profiles), 'enabled': enabled}
                for mission_id, (decoder, enabled) in sorted(self._decoders.items())}

    def decode_frame(self, mission_id: str, frame: bytes, *, profile: str) -> dict:
        decoder = self.resolve(mission_id)
        if not isinstance(frame, bytes):
            raise FrameDecodeError('invalid_frame_type', 'frame must be immutable bytes')
        if not isinstance(profile, str) or profile not in decoder.profiles:
            raise UnknownProfileError(profile)
        try:
            result = decoder.decode(frame, profile=profile)
        except MissionDecodeError:
            raise
        except Exception as exc:
            raise FrameDecodeError('decoder_failed', 'Mission decoder could not decode the frame') from exc
        required = {'header', 'payload', 'integrity', 'clock', 'qualification', 'units'}
        if (not isinstance(result, dict) or not required.issubset(result)
                or any(not isinstance(result[key], dict) for key in required)):
            raise MissionDecodeError('invalid_decoder_result', 'Decoder result lacks required mission sections')
        identity = {'mission_id': mission_id, 'profile': profile, 'decoder_id': decoder.decoder_id,
                    'decoder_version': decoder.version, 'frame_sha256': hashlib.sha256(frame).hexdigest(),
                    'frame_bytes': len(frame), 'schema': SCHEMA}
        if any(key in result and result[key] != value for key, value in identity.items()):
            raise MissionDecodeError('invalid_decoder_result', 'Decoder result conflicts with registry identity')
        try:
            # Own the returned JSON tree even if a third-party decoder caches mutable dictionaries.
            return json.loads(json.dumps({**result, **identity}, allow_nan=False))
        except (TypeError, ValueError, OverflowError) as exc:
            raise MissionDecodeError('invalid_decoder_result', 'Decoder result must be finite JSON data') from exc


def crc32c(data: bytes) -> int:
    """Castagnoli CRC with reflected input/output and initial/final xor FFFFFFFF."""
    checksum = 0xffffffff
    for byte in data:
        checksum ^= byte
        for _ in range(8):
            checksum = (checksum >> 1) ^ (0x82f63b78 if checksum & 1 else 0)
    return checksum ^ 0xffffffff


class Suomi100Decoder:
    decoder_id = 'suomi100-stdlib'
    version = '2'
    profiles = (SATNOGS_BEACON0_PROFILE, CSP1_BEACON0_PROFILE)

    def decode(self, frame: bytes, *, profile: str) -> dict:
        if profile not in self.profiles:
            raise UnknownProfileError(profile)
        if not isinstance(frame, bytes):
            raise FrameDecodeError('invalid_frame_type', 'frame must be immutable bytes')
        observed = profile == SATNOGS_BEACON0_PROFILE
        expected_length = 96 if observed else 92
        if len(frame) != expected_length:
            raise FrameDecodeError('invalid_frame_length',
                                   f'Selected Suomi100 Beacon0 profile requires exactly {expected_length} bytes')
        if frame[4] != 0:
            raise FrameDecodeError('unsupported_beacon', 'Selected profile admits only Beacon0')
        identifier = int.from_bytes(frame[:4], 'big')
        header = {'priority': (identifier >> 30) & 3, 'source': (identifier >> 25) & 31,
                  'destination': (identifier >> 20) & 31, 'destination_port': (identifier >> 14) & 63,
                  'source_port': (identifier >> 8) & 63, 'reserved': (identifier >> 5) & 7,
                  'fragmentation': bool(identifier & 16), 'hmac': bool(identifier & 8),
                  'xtea': bool(identifier & 4), 'rdp': bool(identifier & 2), 'crc': bool(identifier & 1)}
        if identifier != 0x82a78001:
            raise FrameDecodeError('csp_profile_mismatch', 'CSP header is outside the selected explicit profile')
        checksum_records = []
        scopes = [('payload_crc32c', 4, 88, 88)]
        if observed:
            scopes.append(('frame_crc32c', 0, 92, 92))
        for name, start, end, stored_at in scopes:
            expected = int.from_bytes(frame[stored_at:stored_at + 4], 'big')
            computed = crc32c(frame[start:end])
            if expected != computed:
                raise FrameDecodeError('invalid_checksum', f'{name} mismatch in selected explicit profile')
            checksum_records.append({'name': name, 'algorithm': 'CRC32C', 'byte_range': [start, end],
                                     'stored_at': stored_at, 'byte_order': 'big', 'stored': f'{expected:08x}',
                                     'computed': f'{computed:08x}', 'passed': True})
        # Fixed, independently implemented big-endian wire layout. No unit scaling is applied.
        eps = struct.unpack_from('>I3HH7H3HHH6HB', frame, 5)
        com = struct.unpack_from('>I5h', frame, 54)
        obc = struct.unpack_from('>I6H2h', frame, 68)
        payload = {'beacon_type': 0,
                   'eps': {'timestamp_unix_s': eps[0], 'pv_v': list(eps[1:4]), 'batt_v': eps[4],
                           'output_cur': list(eps[5:12]), 'pv_cur': list(eps[12:15]),
                           'batt_in_cur': eps[15], 'batt_out_cur': eps[16], 'temp': list(eps[17:23]),
                           'batt_mode': eps[23]},
                   'com': {'timestamp_unix_s': com[0], 'temp': list(com[1:3]), 'rssi': com[3],
                           'rferr': com[4], 'rssi_bgnd': com[5]},
                   'obc': {'timestamp_unix_s': obc[0], 'cur': list(obc[1:7]), 'temp': list(obc[7:9])}}
        clocks = {section: {'unix_s': values['timestamp_unix_s'],
                            'interpreted_utc': datetime.fromtimestamp(values['timestamp_unix_s'], timezone.utc).isoformat()}
                  for section, values in payload.items() if isinstance(values, dict)}
        return {'norad_cat_id': 43804, 'header': header, 'payload': payload,
                'integrity': {'verified': True, 'authenticated': False,
                              'profile_scope': 'observed_noncanonical' if observed else 'csp1_payload_crc32c',
                              'canonical_csp_frame': not observed, 'engineering_prefix_bytes': 88,
                              'checksums': checksum_records,
                              'checksum_layer_meaning': ('not established by this observed profile' if observed
                                                         else 'CSP1.x payload-only CRC; header checked separately')},
                'clock': {'qualified': False, 'onboard_timestamps': clocks,
                          'received_at_inferred': False,
                          'reason': 'Onboard Unix interpretation is preserved; mission epoch and synchronization are not qualified'},
                'qualification': {'status': 'decoded_not_qualified', 'housekeeping_validated': False,
                                  'ogs_validated': False, 'qkd_validated': False, 'physical_hardware_validated': False,
                                  'reasons': [('observed_noncanonical_profile' if observed else
                                               'crc_is_not_authentication_or_engineering_qualification'),
                                              'mission_clock_not_qualified',
                                              'engineering_scales_not_qualified', 'no_ogs_or_qkd_observations']},
                'units': {'qualified': False, 'status': 'raw_unscaled', 'physical_units': None, 'scales': None}}


def default_registry() -> Registry:
    registry = Registry()
    registry.register('suomi100', Suomi100Decoder())
    return registry


def decode_frame(mission_id: str, frame: bytes, *, profile: str) -> dict:
    return default_registry().decode_frame(mission_id, frame, profile=profile)
