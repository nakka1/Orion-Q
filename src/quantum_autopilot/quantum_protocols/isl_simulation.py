"""Seeded ISL photon/receiver events, not protocol outcomes or relay resources."""
from __future__ import annotations

import math
from datetime import datetime
from decimal import Decimal

import numpy as np

from .contracts import ProtocolError
from .isl import ISL_SCHEMA, geometry_at, terminal_parameters
from .isl_contracts import validate_isl_observation
from .physics import C_KM_S
from .satellite_contracts import MAX_TAGS


def generate_observation(rng,command,config,environment,parameters,*,source_id,origin,sequence):
    p=parameters;endpoints=config.endpoints
    start=command.emitted_at_s;end=start+command.arguments['duration_s']
    degraded=environment.get('condition',config.scenario)=='degraded'
    optical=[terminal_parameters(p,n) for n in endpoints]
    if degraded:
        optical=[{**o,'timing_jitter_ps':o.get('timing_jitter_ps',50.)*8} for o in optical]
        p={**p,'device_parameters':{n:o for n,o in zip(endpoints,optical)}}
        p['device_parameters'][endpoints[0]]['extra_loss_db']=optical[0].get('extra_loss_db',0.)+12.
    boundaries=set(np.linspace(start,end,max(1,math.ceil((end-start)/.25))+1).tolist())
    for orbit in p['satellite_orbits'].values():
        for a,b in orbit.get('communication_windows',[]):
            if start<a<end:boundaries.add(a)
            if start<b<end:boundaries.add(b)
    boundaries=sorted(boundaries)
    # Resolve LOS/FOV/range transitions. No integration bin may bridge an
    # observed obstruction merely because its midpoint is still visible.
    crossings=[]
    for left,right in zip(boundaries,boundaries[1:]):
        before=geometry_at(p,endpoints,left)[1]['visible']
        if before==geometry_at(p,endpoints,right)[1]['visible']:continue
        a,b=left,right
        for _ in range(36):
            m=(a+b)/2
            if geometry_at(p,endpoints,m)[1]['visible']==before:a=m
            else:b=m
        crossings.append((a+b)/2)
    boundaries=sorted(set(boundaries+crossings))
    all_times=[[],[]];all_detectors=[[],[]];all_delays=[[],[]]
    candidates=[0,0];intervals=[];open_intervals=[];emitted=0
    for left,right in zip(boundaries,boundaries[1:]):
        dt=right-left
        _,geo=geometry_at(p,endpoints,(left+right)/2)
        count=int(rng.poisson(dt*p.get('pair_source_hz',20000.)));emitted+=count
        local=optical[0].get('local_detection_efficiency',optical[0].get('detector_efficiency',.8))
        remote=min(1.,geo['channel_transmission']*optical[1].get('detector_efficiency',.8))
        joint=local*remote
        selected=rng.multinomial(count,[joint,local*(1-remote),remote*(1-local),(1-local)*(1-remote)])
        both,a_only,b_only=map(int,selected[:3])
        if max(both+a_only,both+b_only)>MAX_TAGS:
            raise ProtocolError('tdc_overflow','ISL detector event volume exceeds bounded block; reduce source rate/block_s')
        shared=rng.uniform(left-start,right-start,both);bits=rng.integers(0,2,both)
        bases=[rng.integers(0,2,both),rng.integers(0,2,both)]
        error=min(.5,p.get('optical_qber',.02)+sum(math.sin(o.get('polarization_error_rad',.01))**2 for o in optical)+(0.12 if degraded else 0.))
        g0,g1=geometry_at(p,endpoints,left)[1],geometry_at(p,endpoints,right)[1]
        if geo['visible']:open_intervals.append([left,right])
        for i,extra in enumerate((a_only,b_only)):
            times=np.concatenate((shared,rng.uniform(left-start,right-start,extra)))
            b=np.concatenate((bits.copy(),rng.integers(0,2,extra)))
            bs=np.concatenate((bases[i],rng.integers(0,2,extra)))
            if i==1 and both:
                b[:both]^=(rng.random(both)<error).astype(int)
                unmatched=bases[0]!=bases[1]
                b[:both][unmatched]=rng.integers(0,2,int(unmatched.sum()))
            def flight(tags):
                if i==0:return np.zeros(len(tags),dtype=np.int64)
                u=(tags-(left-start))/dt
                distance=(2*u**3-3*u*u+1)*g0['range_km']+(u**3-2*u*u+u)*dt*g0['radial_velocity_km_s']+(-2*u**3+3*u*u)*g1['range_km']+(u**3-u*u)*dt*g1['radial_velocity_km_s']
                return np.rint(distance/C_KM_S*1e12).astype(np.int64)
            delay=flight(times)
            stamps=np.rint(times*1e12+rng.normal(0.,optical[i].get('timing_jitter_ps',50.),len(times))).astype(np.int64)+delay
            detectors=2*bs+b
            noise=int(rng.poisson(4*(optical[i].get('dark_count_cps',5.)+(optical[i].get('background_cps',1.) if geo['visible'] else 0.))*dt))
            if len(stamps)+noise>MAX_TAGS:raise ProtocolError('tdc_overflow','ISL background exceeds detector capacity')
            nt=rng.uniform(left-start,right-start,noise);nd=flight(nt)
            stamps=np.concatenate((stamps,np.rint(nt*1e12).astype(np.int64)+nd))
            delay=np.concatenate((delay,nd));detectors=np.concatenate((detectors,rng.integers(0,4,noise)))
            candidates[i]+=len(stamps)
            if candidates[i]>MAX_TAGS:raise ProtocolError('tdc_overflow','ISL candidate-event budget exceeded; no truncation')
            if not optical[i].get('propagation_compensation',True):delay=np.zeros(len(delay),dtype=np.int64)
            else:
                uncertainty=optical[i].get('range_estimation_error_m',0.)/299792458.*1e12
                if uncertainty:delay+=np.rint(rng.normal(0.,uncertainty,len(delay))).astype(np.int64)
            all_times[i].append(stamps);all_detectors[i].append(detectors);all_delays[i].append(delay)
        intervals.append({'start_s':left,'end_s':right,'emitted_pairs':count,
            'visible':geo['visible'],'channel_transmission':geo['channel_transmission'],
            'block_reasons':geo['block_reasons']})
    orbit=p['satellite_orbits'][endpoints[0]]
    utc=datetime.fromisoformat(orbit['epoch_utc'].replace('Z','+00:00'))
    offset=int(((Decimal(str(start))-Decimal(str(orbit['epoch_network_s'])))*10**12).to_integral_value())
    carry,fraction=divmod(utc.microsecond*10**6+offset,10**12)
    epoch=int(utc.replace(microsecond=0).timestamp())+carry
    state,geo=geometry_at(p,endpoints,end)
    tracks=[];batches=[]
    for i,n in enumerate(endpoints):
        times=np.concatenate(all_times[i]);detectors=np.concatenate(all_detectors[i]);delays=np.concatenate(all_delays[i])
        order=np.argsort(times,kind='stable');times,detectors,delays=times[order],detectors[order],delays[order]
        dead=int(round(optical[i].get('detector_dead_time_ns',50.)*1000.));last=[-2**63]*4;keep=[]
        for k,(t,d) in enumerate(zip(times,detectors)):
            if int(t)-last[d]>=dead:last[d]=int(t);keep.append(k)
        clock=int(round(optical[i].get('clock_offset_s',p.get('clock_offset_s',0.))*1e12))
        tracks.append({'station_id':n,'at_s':end,'locked':bool(open_intervals),'open_intervals_s':open_intervals,
            'source':'simulated dedicated ISL APT terminal','endpoint_kind':'satellite',
            'additional_channel_loss_db':12. if degraded and i==0 else 0.,
            'loss_reference':'configured degradation over terminal optical budget'})
        batches.append({'station_id':n,'epoch_utc_s':epoch,'epoch_fraction_ps':fraction,
            'clock_reference':'simulated disciplined satellite clock; hardware qualification absent',
            'clock_offset_ps':clock,'clock_uncertainty_ps':optical[i].get('timing_jitter_ps',50.),
            'propagation_delay_ps':0,'flight_time_ps':delays[keep].tolist(),
            'delay_reference':'local arm zero / Hermite ISL range/range-rate calibration',
            'time_ps':(times[keep]+clock).tolist(),'detector':detectors[keep].tolist(),
            'lost_tags':0,'dead_time_discarded':len(times)-len(keep)})
        state[n].update(source=orbit['source'] if i==0 else p['satellite_orbits'][n]['source'],
            valid_until_s=end+p.get('housekeeping_max_age_s',5.),at_s=end)
    raw={'isl_schema':ISL_SCHEMA,'start_s':start,'end_s':end,'emitted_pairs':emitted,
        'housekeeping':state,'tracking':tracks,'tdc':batches,'intervals':intervals,
        'geometry':[{'range_km':0.,'flight_time_s':0.,'visible':geo['visible'],'local_detection':True},geo],
        'public_randomness':{'seed_hex':bytes(rng.integers(0,256,16,dtype=np.uint8)).hex(),
            'origin':'simulated seeded source; not a qualified QRNG'},
        'parameter_provenance':{'origin':origin,'source_id':source_id,'sequence':sequence,
            'scope':'hypothetical pair source / local receiver / remote optical receiver; no mission calibration',
            'time_model':'250 ms optical integration, LOS crossings, Hermite propagation calibration',
            'protocol':'BBM92 between measured satellites; no memory/transparent quantum relay',
            'source_references':['ESA EDRS optical links: classical maturity only',
                'Dabiri et al. arXiv:2506.20798: theoretical inter-satellite QKD',
                'Tomamichel & Leverrier arXiv:1506.08458: conditional finite-sample bound']}}
    return validate_isl_observation(raw,endpoints=endpoints,parameters=p)
