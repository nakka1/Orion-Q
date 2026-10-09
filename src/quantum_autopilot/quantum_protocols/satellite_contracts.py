"""OGS observation contracts. RF/housekeeping, APT and TDC are distinct streams.

All messages are classical records of observations; a TDC message is not a
quantum transport. Times are UTC seconds plus integer relative picoseconds.
No Unix timestamp in floating point picoseconds, keys or derived QKD results.
"""
from __future__ import annotations

import copy
import math

from .contracts import ProtocolError, canonical, number

SATELLITE_SCHEMA = 'orion.satellite-observation.v2'
PACKET_SCHEMA = 'orion.ogs-packet.v2'
MAX_TAGS = 100_000
KINDS = ('housekeeping', 'tracking', 'tdc')


def integer(value, name, minimum=0, maximum=2**63-1):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ProtocolError('invalid_tdc', f'{name}: integer [{minimum}, {maximum}] required')
    return value


def vector(value, name, maximum):
    if not isinstance(value, list) or len(value) != 3:
        raise ProtocolError('invalid_ephemeris', f'{name}: three coordinates required')
    return [number(x, name, -maximum, maximum) for x in value]


def validate_observation(raw, *, endpoints=None, parameters=None):
    p = parameters or {}
    if raw.get('satellite_schema') != SATELLITE_SCHEMA:
        raise ProtocolError('invalid_schema', 'satellite observation v2 required')
    start = number(raw.get('start_s'), 'start_s')
    end = number(raw.get('end_s'), 'end_s', start + 1e-12, start + 60.)
    integer(raw.get('emitted_pairs'), 'emitted_pairs', 0, 10**12)
    hk = raw.get('housekeeping', {})
    if not isinstance(hk,dict):raise ProtocolError('invalid_ephemeris','housekeeping object required')
    if hk.get('frame') != 'ECEF':
        raise ProtocolError('invalid_ephemeris', 'gateway must normalize state to ECEF, km and km/s')
    ecef=vector(hk.get('ecef_km'), 'ecef_km', 1e6)
    if math.sqrt(sum(x*x for x in ecef))<=6378.137:
        raise ProtocolError('invalid_ephemeris','orbital ECEF position must be outside the Earth; terrestrial vectors cannot describe this satellite')
    vector(hk.get('velocity_ecef_km_s'), 'velocity_ecef_km_s', 100.)
    for key in ('at_s', 'valid_until_s'):
        number(hk.get(key), key, 0., 1e12)
    if hk['at_s'] > end or hk['valid_until_s'] < end:
        raise ProtocolError('stale_ephemeris', 'housekeeping does not cover the acquisition block')
    ttl=p.get('housekeeping_max_age_s',5.) if parameters is not None else 600.
    if end-hk['at_s']>ttl or hk['valid_until_s']-hk['at_s']>ttl:
        raise ProtocolError('stale_ephemeris','housekeeping validity exceeds the configured freshness policy')
    if not isinstance(hk.get('source'), str) or not hk['source']:
        raise ProtocolError('missing_provenance', 'ephemeris source required')
    for key,low,high in [('bus_voltage_v',0.,1000.),('payload_temperature_c',-273.15,500.),('battery_soc',0.,1.)]:
        if key in hk:number(hk[key],key,low,high)
    tracks, batches = raw.get('tracking'), raw.get('tdc')
    if not isinstance(tracks, list) or not isinstance(batches, list) or len(tracks) != 2 or len(batches) != 2:
        raise ProtocolError('missing_stream', 'two APT records and two TDC batches required')
    if any(not isinstance(item,dict) for item in tracks+batches):
        raise ProtocolError('invalid_packet','APT/TDC entries must be objects')
    station_ids = tuple(b.get('station_id') for b in batches)
    if len(set(station_ids)) != 2 or endpoints and station_ids != tuple(endpoints):
        raise ProtocolError('wrong_station', 'TDC station association differs from the session')
    if tuple(t.get('station_id') for t in tracks) != station_ids:
        raise ProtocolError('wrong_station', 'APT/TDC station association differs')
    for track, batch in zip(tracks, batches):
        if type(track.get('locked')) is not bool:
            raise ProtocolError('invalid_tracking', 'explicit APT lock required')
        if 'additional_channel_loss_db' in track:
            number(track['additional_channel_loss_db'],'additional channel loss',0.,1000.)
            if not isinstance(track.get('loss_reference'),str) or not track['loss_reference'].strip():
                raise ProtocolError('missing_provenance','excess optical loss requires a calibration/model reference')
        number(track.get('at_s'), 'tracking time', 0., end)
        if end-track['at_s'] > (p.get('tracking_max_age_s',60.) if parameters is not None else 600.):
            raise ProtocolError('stale_tracking', 'APT observation expired')
        integer(batch.get('epoch_utc_s'), 'epoch_utc_s', 0, 10**11)
        integer(batch.get('epoch_fraction_ps'), 'epoch_fraction_ps', 0, 10**12-1)
        for key in ('clock_offset_ps', 'propagation_delay_ps'):
            integer(batch.get(key), key, -2**63, 2**63-1)
        number(batch.get('clock_uncertainty_ps'), 'clock uncertainty', 0., 1e12)
        if parameters is not None and batch['clock_uncertainty_ps'] > p.get('sync_tolerance_s', 1e-6)*1e12:
            raise ProtocolError('unsynchronized', 'TDC clock uncertainty exceeds the session tolerance')
        if not isinstance(batch.get('clock_reference'), str) or not batch['clock_reference']:
            raise ProtocolError('unsynchronized', 'clock/PPS reference required; TDC relative time is not UTC')
        integer(batch.get('lost_tags'), 'lost_tags')
        if batch['lost_tags']:
            raise ProtocolError('tdc_overflow', 'lost tags abort this block; silent clipping is forbidden')
        times, detectors = batch.get('time_ps'), batch.get('detector')
        if not isinstance(times, list) or not isinstance(detectors, list) or len(times) != len(detectors) or len(times) > MAX_TAGS:
            raise ProtocolError('message_limit', 'bounded, equal-length TDC columns required')
        previous = -2**63
        for stamp, detector in zip(times, detectors):
            integer(stamp, 'relative timestamp', -2**63, 2**63-1)
            integer(detector, 'detector', 0, 3)
            if stamp < previous:
                raise ProtocolError('invalid_tdc', 'time tags must be ordered')
            previous = stamp
        corrections=batch.get('flight_time_ps')
        if corrections is not None:
            if not isinstance(corrections,list) or len(corrections)!=len(times) or not batch.get('delay_reference'):
                raise ProtocolError('unsynchronized','per-tag propagation calibration requires a reference and equal length')
            for correction in corrections:integer(correction,'propagation correction',0,10**16)
        intervals=track.get('open_intervals_s')
        if not isinstance(intervals,list) or len(intervals)>4096:
            raise ProtocolError('invalid_tracking','APT open intervals are mandatory')
        for interval in intervals:
            if not isinstance(interval,list) or len(interval)!=2:
                raise ProtocolError('invalid_tracking','APT interval [start,end] required')
            number(interval[0],'APT start',start,end)
            number(interval[1],'APT end',interval[0]+1e-12,end)
    geometry=raw.get('geometry')
    if not isinstance(geometry,list) or len(geometry)!=2 or any(not isinstance(g,dict) for g in geometry):
        raise ProtocolError('invalid_ephemeris','two normalized OGS geometry records required')
    for g in geometry:
        number(g.get('range_km'),'slant range',0.,1e6)
        number(g.get('elevation_deg'),'elevation',-90.,90.)
        if type(g.get('visible')) is not bool:raise ProtocolError('invalid_tracking','explicit geometric visibility required')
    canonical(raw)
    return raw


def packets(raw, command, *, source_id, origin, sequence):
    """Simulator and gateway use the same three independently typed messages."""
    common = {'schema': PACKET_SCHEMA, 'session_id': command.session_id,
              'source_id': source_id, 'origin': origin, 'sequence': sequence,
              'start_s': raw['start_s'], 'end_s': raw['end_s'],
              'quantum_channel_ids': list(command.quantum_channel_ids),
              'classical_channel_ids': list(command.classical_channel_ids)}
    return [{**copy.deepcopy(common), 'kind': 'housekeeping', 'data': {'housekeeping': raw['housekeeping'],
                'emitted_pairs': raw['emitted_pairs'], 'geometry': raw['geometry'],
                'intervals': raw.get('intervals', []), 'parameter_provenance': raw.get('parameter_provenance', {}),
                'public_randomness':raw.get('public_randomness')}},
            {**copy.deepcopy(common), 'kind': 'tracking', 'data': raw['tracking']},
            {**copy.deepcopy(common), 'kind': 'tdc', 'data': raw['tdc']}]


def validate_packet(packet):
    required = {'schema', 'session_id', 'source_id', 'origin', 'sequence', 'start_s', 'end_s',
                'quantum_channel_ids', 'classical_channel_ids', 'kind', 'data'}
    if not isinstance(packet, dict) or set(packet) != required or packet['schema'] != PACKET_SCHEMA or packet['kind'] not in KINDS:
        raise ProtocolError('invalid_packet', 'exact OGS v2 packet fields and stream kind required')
    for key in ('session_id', 'source_id'):
        if not isinstance(packet[key], str) or not 1 <= len(packet[key]) <= 96:
            raise ProtocolError('wrong_binding', 'bounded session/source identifiers required')
    if packet['origin'] not in ('simulated', 'physical', 'measurement_file'):
        raise ProtocolError('wrong_source', 'explicit observation origin required')
    integer(packet['sequence'], 'sequence', 1)
    number(packet['start_s'], 'start_s')
    number(packet['end_s'], 'end_s', packet['start_s']+1e-12, packet['start_s']+60.)
    q, c = packet['quantum_channel_ids'], packet['classical_channel_ids']
    if any(not isinstance(ids, list) or len(ids) != 2 or any(not isinstance(x, str) or not x or len(x)>96 for x in ids) for ids in (q, c)) or len(set(q+c)) != 4:
        raise ProtocolError('wrong_channel', 'two distinct quantum/classical channel associations required')
    data=packet['data']
    if packet['kind']=='housekeeping':
        if not isinstance(data,dict) or not {'housekeeping','emitted_pairs','geometry'}<=set(data) or not isinstance(data['housekeeping'],dict):
            raise ProtocolError('invalid_ephemeris','housekeeping stream requires engineering records and source emission observation')
        integer(data['emitted_pairs'],'emitted pairs',0,10**12)
        vector(data['housekeeping'].get('ecef_km'),'ECEF position',1e6)
        vector(data['housekeeping'].get('velocity_ecef_km_s'),'ECEF velocity',100.)
    elif not isinstance(data,list) or len(data)!=2 or any(not isinstance(row,dict) for row in data):
        raise ProtocolError('invalid_packet','tracking/TDC stream requires two OGS objects')
    def reject_results(value):
        if isinstance(value,dict):
            if set(value)&{'final_key','secure_key','skr_bps','final_fidelity','conditional_key_estimate_bits','qber','coincidences'}:
                raise ProtocolError('precomputed_result','OGS streams accept raw observations, not derived protocol results')
            for child in value.values():reject_results(child)
        elif isinstance(value,list):
            for child in value:reject_results(child)
    reject_results(packet['data'])
    canonical(packet)
    return packet


def assemble(messages,*,parameters=None):
    for packet in messages:
        validate_packet(packet)
    if len(messages) != 3 or set(x['kind'] for x in messages) != set(KINDS):
        raise ProtocolError('missing_stream', 'housekeeping, tracking and TDC are mandatory')
    binding = ('session_id', 'source_id', 'origin', 'start_s', 'end_s', 'quantum_channel_ids', 'classical_channel_ids')
    if any(any(m[k] != messages[0][k] for k in binding) for m in messages[1:]):
        raise ProtocolError('wrong_binding', 'OGS streams must describe the same block and channels')
    data = {x['kind']: x['data'] for x in messages}
    if not isinstance(data['housekeeping'], dict):
        raise ProtocolError('invalid_ephemeris', 'housekeeping object required')
    raw = {**data['housekeeping'], 'satellite_schema': SATELLITE_SCHEMA,
           'start_s': messages[0]['start_s'], 'end_s': messages[0]['end_s'],
           'tracking': data['tracking'], 'tdc': data['tdc']}
    return validate_observation(raw,parameters=parameters)
