"""Photon/detector event generator; produces no coincidences, QBER or keys.

Piecewise optical budgets are integrated across pass boundaries. A Poisson pair
source and independent arm losses select detected events without allocating all
emitted photons. Basis/outcome, jitter, noise, afterpulsing and detector dead time
produce the time tags processed by the same OGS observation adapter as files.
"""
from __future__ import annotations

import math
from datetime import datetime
from decimal import Decimal

import numpy as np

from .contracts import ProtocolError
from .orbits import position
from .physics import C_KM_S, satellite_optics
from .satellite import station_geometry
from .satellite_contracts import MAX_TAGS, assemble, packets


def _orbit(parameters, reference):
    if parameters.get('orbit_record'):
        return parameters['orbit_record']
    return dict(model='circular_reference', epoch_utc='2026-10-06T00:00:00+00:00',
                epoch_network_s=0., source='hypothetical circular reference',
                reference_latitude=reference[0], reference_longitude=reference[1],
                altitude_km=parameters.get('altitude_km',500.), inclination_deg=parameters.get('inclination_deg',53.),
                orbit_phase_rad=parameters.get('orbit_phase_rad',0.), communication_windows=[])


def generate_observation(rng, command, config, environment, parameters, *, source_id, origin, sequence):
    p=parameters;profiles=p.get('device_parameters',{})
    start=command.emitted_at_s;end=start+command.arguments['duration_s']
    reference=environment['ground_positions'][0];orbit=_orbit(p,reference)
    degraded=environment.get('condition',config.scenario)=='degraded'
    windows=orbit.get('communication_windows',[])
    # Break exactly at configured windows; maximum integration step 250 ms.
    boundaries=set(np.linspace(start,end,max(1,math.ceil((end-start)/.25))+1).tolist())
    for a,b in windows:
        if start<a<end:boundaries.add(a)
        if start<b<end:boundaries.add(b)
    boundaries=sorted(boundaries)
    # Resolve geometric horizon/threshold crossings instead of granting the
    # entire last integration bin to an OGS that has already lost visibility.
    crossings=[]
    for i,station in enumerate(environment['ground_positions']):
        optical={**p,**profiles.get(config.endpoints[i],{})};threshold=optical.get('min_elevation_deg',20.)
        def margin(t):
            return station_geometry(t,reference,station,{**p,'orbit_record':orbit,
                'station_altitude_m':environment.get('ground_altitudes_m',[0.,0.])[i]})['elevation_deg']-threshold
        for a,b in zip(boundaries,boundaries[1:]):
            fa,fb=margin(a),margin(b)
            if (fa>=0)==(fb>=0):continue
            lo,hi=a,b
            for _ in range(40):
                middle=(lo+hi)/2
                if (margin(middle)>=0)==(fa>=0):lo=middle
                else:hi=middle
            crossings.append((lo+hi)/2)
    boundaries=sorted(set(boundaries+crossings))
    all_times=[[],[]];all_detectors=[[],[]];all_delays=[[],[]]
    candidate_counts=[0,0]
    active=[[],[]];intervals=[];emitted=0
    def geometries(t):
        output=[]
        in_window=not windows or any(a<=t<b for a,b in windows)
        for i,station in enumerate(environment['ground_positions']):
            optical={**p,**profiles.get(config.endpoints[i],{})}
            optical['extra_loss_db']=optical.get('extra_loss_db',0.)+(12. if degraded else 0.)
            optical['timing_jitter_ps']=optical.get('timing_jitter_ps',50.)*(8 if degraded else 1)
            geo=station_geometry(t,reference,station,{**p,'orbit_record':orbit,'station_altitude_m':environment.get('ground_altitudes_m',[0.,0.])[i]})
            geo['polarization_rotation_rad']=optical.get('polarization_error_rad',.03)+optical.get('polarization_rotation_rate_rad_s',.001)*t
            geo=satellite_optics(geo,optical,cloud=not in_window or p.get('cloud',False) or optical.get('cloud',False)
                or not p.get('pointing_enabled',True) or not optical.get('pointing_enabled',True))
            output.append(geo)
        return output
    for left,right in zip(boundaries,boundaries[1:]):
        dt=right-left;middle=(left+right)/2;geo=geometries(middle)
        count=int(rng.poisson(dt*p.get('pair_source_hz',5e6)));emitted+=count
        optics=[{**p,**profiles.get(n,{})} for n in config.endpoints]
        eta=[min(1.,g['channel_transmission']*o.get('detector_efficiency',.8)) for g,o in zip(geo,optics)]
        for i,g in enumerate(geo):
            sigma=optics[i].get('scintillation_sigma',0.)
            if sigma and eta[i]:eta[i]=min(1.,eta[i]*float(rng.lognormal(-sigma*sigma/2,sigma)))
            if g['visible']:active[i].append([left,right])
        joint=eta[0]*eta[1]
        detected=rng.multinomial(count,[joint,eta[0]*(1-eta[1]),eta[1]*(1-eta[0]),(1-eta[0])*(1-eta[1])])
        both,a_only,b_only=map(int,detected[:3])
        if max(both+a_only,both+b_only)>MAX_TAGS:
            raise ProtocolError('tdc_overflow','expected event volume exceeds the bounded TDC block; reduce block_s/rate')
        # Emission times are not exported as pair identifiers. Matching must use
        # time tags and independently supplied propagation/clock calibration.
        shared=rng.uniform(left-start,right-start,both)
        shared_bits=rng.integers(0,2,both)
        shared_bases=[rng.integers(0,2,both),rng.integers(0,2,both)]
        error=min(.5,p.get('optical_qber',.02)+sum(g['e_mis_after'] for g in geo)+(0.12 if degraded else 0.))
        for i,extra in enumerate((a_only,b_only)):
            times=np.concatenate((shared,rng.uniform(left-start,right-start,extra)))
            bases=np.concatenate((shared_bases[i],rng.integers(0,2,extra)))
            bits=np.concatenate((shared_bits.copy(),rng.integers(0,2,extra)))
            if i==1 and both:
                bits[:both]^=(rng.random(both)<error).astype(int)
                unmatched=shared_bases[0]!=shared_bases[1]
                bits[:both][unmatched]=rng.integers(0,2,int(unmatched.sum()))
            jitter=optics[i].get('timing_jitter_ps',50.)*(8 if degraded else 1)
            def endpoint_geometry(t):
                return station_geometry(t,reference,environment['ground_positions'][i],
                    {**p,'orbit_record':orbit,'station_altitude_m':environment.get('ground_altitudes_m',[0.,0.])[i]})
            g0,g1=endpoint_geometry(left),endpoint_geometry(right)
            def delay_at(emission_times):
                u=(emission_times-(left-start))/dt
                distance=(2*u**3-3*u*u+1)*g0['range_km']+(u**3-2*u*u+u)*dt*g0['radial_velocity_km_s']+(-2*u**3+3*u*u)*g1['range_km']+(u**3-u*u)*dt*g1['radial_velocity_km_s']
                return np.rint(distance/C_KM_S*1e12).astype(np.int64)
            delays=delay_at(times)
            stamps=np.rint(times*1e12+rng.normal(0.,jitter,len(times))).astype(np.int64)+delays
            dets=2*bases+bits
            # Background_cps and dark_count_cps are PER DETECTOR. The legacy
            # dark_probability is interpreted per emitted trial only if supplied.
            background=optics[i].get('background_cps',optics[i].get('background_rate_hz',500.)) if geo[i]['visible'] else 0.
            dark=optics[i].get('dark_count_cps',20.)
            dark+=optics[i].get('dark_probability',0.)*p.get('pair_source_hz',5e6)
            noise=int(rng.poisson(4*(background+dark)*dt))
            if len(stamps)+noise>MAX_TAGS:
                raise ProtocolError('tdc_overflow','detector background exceeds TDC block capacity')
            noise_times=rng.uniform(left-start,right-start,noise);noise_delays=delay_at(noise_times)
            noise_stamps=np.rint(noise_times*1e12).astype(np.int64)+noise_delays
            stamps=np.concatenate((stamps,noise_stamps));delays=np.concatenate((delays,noise_delays));dets=np.concatenate((dets,rng.integers(0,4,noise)))
            after=np.flatnonzero(rng.random(len(stamps))<optics[i].get('afterpulse_probability',0.))
            if len(after):
                stamps=np.concatenate((stamps,stamps[after]+np.rint(rng.exponential(optics[i].get('afterpulse_delay_ns',1000.)*1000.,len(after))).astype(np.int64)))
                dets=np.concatenate((dets,dets[after]))
                delays=np.concatenate((delays,delays[after]))
            if not optics[i].get('propagation_compensation',True):delays=np.zeros(len(delays),dtype=np.int64)
            else:
                uncertainty=optics[i].get('range_estimation_error_m',0.)/299792458.*1e12
                if uncertainty:delays=delays+np.rint(rng.normal(0.,uncertainty,len(delays))).astype(np.int64)
            candidate_counts[i]+=len(stamps)
            if candidate_counts[i]>MAX_TAGS:
                raise ProtocolError('tdc_overflow','simulated candidate-event budget exceeded before dead-time filtering; reduce block_s/rate')
            all_times[i].append(stamps);all_detectors[i].append(dets);all_delays[i].append(delays)
        intervals.append({'start_s':left,'end_s':right,'emitted_pairs':count,
            'visible':[g['visible'] for g in geo],'arm_transmissions':eta})
    stamp=datetime.fromisoformat(orbit['epoch_utc'].replace('Z','+00:00'))
    offset_ps=int(((Decimal(str(start))-Decimal(str(orbit['epoch_network_s'])))*10**12).to_integral_value())
    carry,fraction=divmod(stamp.microsecond*10**6+offset_ps,10**12)
    epoch_s=int(stamp.replace(microsecond=0).timestamp())+carry
    tracks=[];tdc=[]
    final_geometry=geometries(end)
    for i,node_id in enumerate(config.endpoints):
        optical={**p,**profiles.get(node_id,{})}
        times=np.concatenate(all_times[i]);detectors=np.concatenate(all_detectors[i]);delays=np.concatenate(all_delays[i])
        order=np.argsort(times,kind='stable');times,detectors,delays=times[order],detectors[order],delays[order]
        dead=int(round(optical.get('detector_dead_time_ns',50.)*1000.));last=[-2**63]*4;keep=[]
        for k,(t,d) in enumerate(zip(times,detectors)):
            if int(t)-last[d]>=dead:last[d]=int(t);keep.append(k)
        if len(keep)>MAX_TAGS:raise ProtocolError('tdc_overflow','TDC block capacity exceeded; no truncation')
        clock_offset=int(round(p.get('clock_offset_s',0.)*1e12))
        tracks.append({'station_id':node_id,'at_s':end,'locked':bool(active[i]),
                       'additional_channel_loss_db':12. if degraded else 0.,
                       'loss_reference':'simulated configured degradation; excess over registered optical parameters',
                       'open_intervals_s':active[i],'azimuth_deg':final_geometry[i].get('azimuth_deg'),
                       'polarization_rotation_rad':final_geometry[i]['polarization_rotation_rad'],
                       'elevation_deg':final_geometry[i]['elevation_deg'], 'source':'simulated APT receiver'})
        tdc.append({'station_id':node_id,'epoch_utc_s':epoch_s,'epoch_fraction_ps':fraction,
            'clock_reference':'simulated disciplined PPS / OGS calibrated clock',
            'clock_offset_ps':clock_offset,'clock_uncertainty_ps':optical.get('timing_jitter_ps',50.),
            'propagation_delay_ps':0,'flight_time_ps':delays[keep].tolist(),
            'delay_reference':'cubic Hermite slant range/radial velocity; per-tag propagation calibration',
            'time_ps':(times[keep]+clock_offset).tolist(),'detector':detectors[keep].tolist(),
            'lost_tags':0,'dead_time_discarded':len(times)-len(keep)})
    state=position(orbit,end)
    raw={'start_s':start,'end_s':end,'emitted_pairs':emitted,'geometry':final_geometry,
         'housekeeping':{'frame':'ECEF','ecef_km':state['ecef_km'],
             'velocity_ecef_km_s':state['velocity_ecef_km_s'],'at_s':end,'valid_until_s':end+p.get('housekeeping_max_age_s',5.),
             'at_utc':state['at_utc'],'source':orbit['source'],'orbit_model':orbit['model'],
             'source_status':'emitting' if emitted else 'idle','pair_source_hz':p.get('pair_source_hz',5e6)},
         'tracking':tracks,'tdc':tdc,'intervals':intervals,
         'public_randomness':{'seed_hex':bytes(rng.integers(0,256,16,dtype=np.uint8)).hex(),
                              'origin':'simulated seeded source; not a qualified QRNG'},
         'parameter_provenance':{'origin':'simulated','model_id':'orion.satellite-poisson-events.v1',
             'scope':'hypothetical devices; constants configurable; no mission calibration',
             'time_model':'piecewise <=250 ms optics; resolved pass boundaries; continuous Hermite propagation delay',
             'protocol':'BBM92 entangled-pair downlink; not decoy BB84 or TF-QKD',
             'source_references':['Yin et al. Science 2017 doi:10.1126/science.aan3211','Tomamichel et al. Nature Communications 2012 doi:10.1038/ncomms1631']}}
    return assemble(packets(raw,command,source_id=source_id,origin=origin,sequence=sequence),parameters=p)
