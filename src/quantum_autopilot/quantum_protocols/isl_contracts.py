"""Bounded raw ISL observations using the common Acquisition envelope.

Housekeeping, acquisition/tracking and detector time tags stay separate. TDC
records are classical observations of a quantum payload, never payload routing.
"""
from __future__ import annotations

import math

from .contracts import ProtocolError, canonical, number
from .isl import ISL_SCHEMA
from .satellite_contracts import MAX_TAGS, integer, vector


def validate_isl_observation(raw, *, endpoints=None, parameters=None):
    p=parameters or {}
    if raw.get('isl_schema')!=ISL_SCHEMA:
        raise ProtocolError('invalid_schema','ISL observation v1 required')
    start=number(raw.get('start_s'),'start_s')
    end=number(raw.get('end_s'),'end_s',start+1e-12,start+60.)
    integer(raw.get('emitted_pairs'),'emitted_pairs',0,10**12)
    hk=raw.get('housekeeping');tracks=raw.get('tracking');tdc=raw.get('tdc')
    if not isinstance(hk,dict) or len(hk)!=2 or not isinstance(tracks,list) or not isinstance(tdc,list) or len(tracks)!=2 or len(tdc)!=2:
        raise ProtocolError('missing_stream','two orbital HK, two APT and two TDC records required')
    if any(not isinstance(x,dict) for x in tracks+tdc):
        raise ProtocolError('invalid_packet','APT/TDC objects required')
    ids=tuple(x.get('station_id') for x in tdc)
    if len(set(ids))!=2 or set(ids)!=set(hk) or endpoints and ids!=tuple(endpoints) or tuple(x.get('station_id') for x in tracks)!=ids:
        raise ProtocolError('wrong_station','orbital HK, APT and detector endpoint associations differ')
    ttl=p.get('housekeeping_max_age_s',5.)
    for ident in ids:
        state=hk[ident]
        if not isinstance(state,dict) or state.get('frame')!='ECEF':
            raise ProtocolError('invalid_ephemeris','two normalized ECEF housekeeping objects required')
        ecef=vector(state.get('ecef_km'),'ECEF',1e6)
        if math.sqrt(sum(x*x for x in ecef))<=6378.137:
            raise ProtocolError('invalid_ephemeris','an ISL endpoint cannot be terrestrial')
        vector(state.get('velocity_ecef_km_s'),'ECEF velocity',100.)
        at=number(state.get('at_s'),'HK time',0.,end)
        until=number(state.get('valid_until_s'),'HK validity',end,at+ttl)
        if end-at>ttl or until<end:raise ProtocolError('stale_ephemeris','orbital HK expired')
        if not isinstance(state.get('source'),str) or not state['source']:
            raise ProtocolError('missing_provenance','orbital ephemeris source required')
    for track,batch in zip(tracks,tdc):
        if type(track.get('locked')) is not bool:raise ProtocolError('invalid_tracking','explicit APT lock required')
        at=number(track.get('at_s'),'tracking time',start,end)
        if end-at>p.get('tracking_max_age_s',60.):raise ProtocolError('stale_tracking','ISL tracking expired')
        intervals=track.get('open_intervals_s')
        if not isinstance(intervals,list) or len(intervals)>4096:raise ProtocolError('invalid_tracking','bounded APT intervals required')
        for interval in intervals:
            if not isinstance(interval,list) or len(interval)!=2:raise ProtocolError('invalid_tracking','APT interval pair required')
            number(interval[0],'APT interval start',start,end)
            number(interval[1],'APT interval end',interval[0]+1e-12,end)
        integer(batch.get('epoch_utc_s'),'UTC seconds',0,10**11)
        integer(batch.get('epoch_fraction_ps'),'UTC fraction',0,10**12-1)
        for key in ('clock_offset_ps','propagation_delay_ps'):integer(batch.get(key),key,-2**63,2**63-1)
        uncertainty=number(batch.get('clock_uncertainty_ps'),'clock uncertainty',0.,1e12)
        if uncertainty>p.get('sync_tolerance_s',1e-6)*1e12:raise ProtocolError('unsynchronized','ISL detector clock uncertainty exceeds tolerance')
        if not isinstance(batch.get('clock_reference'),str) or not batch['clock_reference']:
            raise ProtocolError('unsynchronized','ISL PPS/clock reference required')
        integer(batch.get('lost_tags'),'lost tags')
        if batch['lost_tags']:raise ProtocolError('tdc_overflow','lost tags abort ISL block; no silent clipping')
        times,detectors=batch.get('time_ps'),batch.get('detector')
        if not isinstance(times,list) or not isinstance(detectors,list) or len(times)!=len(detectors) or len(times)>MAX_TAGS:
            raise ProtocolError('message_limit','bounded equal-length ISL detector arrays required')
        previous=-2**63
        for stamp,detector in zip(times,detectors):
            integer(stamp,'time tag',-2**63,2**63-1);integer(detector,'detector',0,3)
            if stamp<previous:raise ProtocolError('invalid_tdc','time tags must be ordered')
            previous=stamp
        corrections=batch.get('flight_time_ps')
        if not isinstance(corrections,list) or len(corrections)!=len(times) or not batch.get('delay_reference'):
            raise ProtocolError('unsynchronized','referenced per-tag propagation correction required')
        for delay in corrections:integer(delay,'flight correction',0,10**16)
    if not isinstance(raw.get('geometry'),list) or len(raw['geometry'])!=2:
        raise ProtocolError('invalid_ephemeris','local/remote detector geometries required')
    for g in raw['geometry']:
        if not isinstance(g,dict) or type(g.get('visible')) is not bool:
            raise ProtocolError('invalid_tracking','explicit detector geometry availability required')
        number(g.get('range_km'),'range',0.,1e6)
    def reject_derived(value):
        if isinstance(value,dict):
            if set(value)&{'final_key','secure_key','skr_bps','final_fidelity','conditional_key_estimate_bits','qber','coincidences'}:
                raise ProtocolError('precomputed_result','ISL accepts raw events, not final metrics')
            for child in value.values():reject_derived(child)
        elif isinstance(value,list):
            for child in value:reject_derived(child)
    reject_derived(raw)
    canonical(raw)
    return raw
