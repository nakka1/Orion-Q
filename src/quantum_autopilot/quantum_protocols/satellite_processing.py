"""Common OGS time-tag processing and finite-block BBM92 demonstration.

The finite-sample budget is conditional on trusted basis-independent devices,
uniform independent bases and authenticated endpoint exchanges. It is NOT a
mission security proof. Reconciliation and Toeplitz operations are executed on
the observed bits; no usable/certified key is exported or installed in a pool.
"""
from __future__ import annotations

import math
import secrets
from collections import deque

import numpy as np
from scipy.signal import fftconvolve

from .contracts import ProtocolError
from .physics import entropy
from .satellite_contracts import validate_observation


def toeplitz(bits, seed, length):
    bits=np.asarray(bits,dtype=np.uint8);seed=np.asarray(seed,dtype=np.uint8)
    if length<0 or len(seed)!=len(bits)+length-1:
        raise ValueError('Toeplitz seed length must be n+m-1')
    if length==0:return np.empty(0,dtype=np.uint8)
    if not len(bits):raise ValueError('nonempty input required')
    products=fftconvolve(bits.astype(float),seed.astype(float))
    return (np.rint(products[len(bits)-1:len(bits)-1+length]).astype(np.int64)%2).astype(np.uint8)


def reconcile(alice,bob,rng,estimated_error,passes=8,*,classical_exchange=None):
    """Cascade-style shuffled parity/bisection/look-back and explicit leakage.

This bounded reconciliation is deliberately verified after all passes. Multiple
errors can remain; verification failure aborts instead of reporting success.
Each Alice parity and Bob response is conservatively charged as two bits.
"""
    alice=np.asarray(alice,dtype=np.uint8);bob=np.asarray(bob,dtype=np.uint8).copy()
    leakage=0;corrected=0
    base=max(4,min(len(alice),int(.73/max(estimated_error,.001))))
    def mismatch(indices):
        nonlocal leakage
        leakage+=2
        a_parity=int(alice[indices].sum())%2;b_parity=int(bob[indices].sum())%2
        if classical_exchange is not None:
            # These are the actual publicly disclosed parity values and index
            # selection used by the protocol, authenticated before use.
            classical_exchange('reconciliation',{'indices':[int(i) for i in indices],
                'alice_parity':a_parity,'bob_parity':b_parity})
        return a_parity != b_parity
    history=[]
    def locate(indices):
        nonlocal corrected
        while len(indices)>1:
            half=len(indices)//2
            indices=indices[:half] if mismatch(indices[:half]) else indices[half:]
        bit=int(indices[0]);bob[bit]^=1;corrected+=1
        return bit
    for turn in range(passes):
        permutation=rng.permutation(len(alice))
        if classical_exchange is not None:
            classical_exchange('reconciliation',{'pass':turn,
                'public_permutation':[int(i) for i in permutation]})
        # Keep at least four independently shuffled blocks. Full-array blocks
        # cannot reveal a remaining even number of errors in short captures.
        width=min(max(1,len(alice)//4),base*2**min(turn,3))
        if not width:break
        inverse=np.empty(len(alice),dtype=int);inverse[permutation]=np.arange(len(alice))
        history.append((permutation,inverse,width))
        for offset in range(0,len(alice),width):
            indices=permutation[offset:offset+width]
            if not mismatch(indices):continue
            bit=locate(indices);queue=deque();queued=set()
            def look_back(changed):
                for pass_id,(_,inv,size) in enumerate(history):
                    block=int(inv[changed])//size;key=(pass_id,block)
                    if key not in queued:queue.append(key);queued.add(key)
            look_back(bit)
            while queue:
                pass_id,block=queue.popleft();queued.discard((pass_id,block))
                order,_,size=history[pass_id];candidate=order[block*size:(block+1)*size]
                if mismatch(candidate):look_back(locate(candidate))
    return bob,leakage,corrected


def finite_budget(n,k,x_errors,leak,verification_bits,parameters):
    eps=parameters.get('parameter_estimation_error',1e-10)
    pa=parameters.get('privacy_amplification_error',1e-10)
    if n==0 or k<parameters.get('min_parameter_sample',20):
        return {'phase_error_upper':.5,'sampling_deviation':None,'budget_bits':0,'abort_reason':'insufficient_parameter_sample'}
    # Serfling without replacement, Eq.76 of Tomamichel & Leverrier 2017.
    deviation=math.sqrt((n+k)*(k+1)*math.log(1/eps)/(2*n*k*k))
    phase=min(.5,x_errors/k+deviation)
    budget=max(0,math.floor(n*(1-entropy(phase))-leak-verification_bits-2*math.log2(1/pa)))
    return {'phase_error_upper':phase,'sampling_deviation':deviation,'budget_bits':budget,
            'parameter_estimation_error':eps,'privacy_amplification_error':pa,
            'abort_reason':'' if budget else 'finite_key_budget_nonpositive',
            'bound_reference':'Tomamichel & Leverrier arXiv:1506.08458 Eq.76; conditional sampling + leftover-hash budget'}


def _aligned(batch,reference_epoch):
    absolute_delta=((batch['epoch_utc_s']-reference_epoch[0])*10**12+
                    batch['epoch_fraction_ps']-reference_epoch[1])
    delay=batch.get('flight_time_ps',batch['propagation_delay_ps'])
    adjustment=absolute_delta-batch['clock_offset_ps']
    if not -2**63<=adjustment<2**63:
        raise ProtocolError('invalid_tdc','clock/epoch alignment exceeds signed 64-bit relative time')
    times=np.asarray(batch['time_ps'],dtype=np.int64);delay=np.asarray(delay,dtype=np.int64)
    if len(times):
        # Check Python integers before NumPy arithmetic can wrap a malformed
        # clock/epoch/tag into an apparently valid coincidence.
        lower=int(times.min())-int(delay.max());upper=int(times.max())-int(delay.min())
        if any(not -2**63<=value<2**63 for value in (adjustment,lower,upper,lower+adjustment,upper+adjustment)):
            raise ProtocolError('invalid_tdc','clock/propagation alignment exceeds signed 64-bit relative time')
    times=times-delay+adjustment
    order=np.argsort(times,kind='stable')
    return times[order],np.asarray(batch['detector'],dtype=np.uint8)[order]


def process_observation(raw,context):
    p=context['parameters'];endpoints=context.get('endpoints')
    isl=bool(raw.get('isl_schema'))
    if isl:
        from .isl_contracts import validate_isl_observation
        validate_isl_observation(raw,endpoints=endpoints,parameters=p)
    else:
        validate_observation(raw,endpoints=endpoints,parameters=p)
    geometry=raw['geometry']
    environment=context.get('environment',{})
    isl_geometry=None
    if isl:
        from .isl import received_geometry
        isl_geometry=received_geometry(raw,p,endpoints)
        geometry=[{'range_km':0.,'flight_time_s':0.,'visible':isl_geometry['visible'],
                   'endpoint_kind':'satellite','peer_id':endpoints[1],'local_detection':True},
                  {**isl_geometry,'endpoint_kind':'satellite','peer_id':endpoints[0],'local_detection':False}]
    elif environment.get('ground_positions'):
        from .orbits import state_geometry
        from .physics import satellite_optics
        positions=environment.get('ground_positions',[])
        if len(positions)!=2:raise ProtocolError('missing_geometry','two registered OGS locations required')
        hk=raw['housekeeping'];profiles=p.get('device_parameters',{})
        geometry=[]
        for i,station in enumerate(positions):
            state={**hk,'polarization_rotation_rad':raw['tracking'][i].get('polarization_rotation_rad',p.get('polarization_error_rad',.03))}
            optical={**p,**profiles.get(endpoints[i],{})}
            optical['extra_loss_db']=optical.get('extra_loss_db',0.)+raw['tracking'][i].get('additional_channel_loss_db',0.)
            geo=satellite_optics(state_geometry(state,station,environment.get('ground_altitudes_m',[0.,0.])[i]),
                optical,cloud=not raw['tracking'][i]['locked'] or not any(a<=raw['end_s']-1e-12<b for a,b in raw['tracking'][i]['open_intervals_s']))
            geometry.append(geo)
    batches=raw['tdc'];reference=(batches[0]['epoch_utc_s'],batches[0]['epoch_fraction_ps'])
    arrays=[_aligned(b,reference) for b in batches]
    gate=int(round(p.get('coincidence_window_s',1e-9)*1e12/2))
    duration=raw['end_s']-raw['start_s'];maximum=int(round(duration*1e12))
    usable=[]
    for (times,detectors),track in zip(arrays,raw['tracking']):
        mask=np.zeros(len(times),dtype=bool)
        if track['locked']:
            for a,b in track['open_intervals_s']:
                mask|=(times>=round((a-raw['start_s'])*1e12))&(times<round((b-raw['start_s'])*1e12))
        mask&=(times>=0)&(times<maximum)
        usable.append((times[mask],detectors[mask]))
    (ta,da),(tb,db)=usable;i=j=0;matches=[];ambiguous=0;residual=[]
    while i<len(ta) and j<len(tb):
        delta=int(ta[i])-int(tb[j])
        if delta < -gate:i+=1;continue
        if delta > gate:j+=1;continue
        # Reject ambiguous many-to-one gates, not an optimistic nearest hit.
        # Earlier rejected hits remain physical alternatives. Ignoring them
        # can turn the last hit in a dense cluster into a false unique pair.
        if ((i and abs(int(ta[i-1])-int(tb[j]))<=gate)
            or (i+1<len(ta) and abs(int(ta[i+1])-int(tb[j]))<=gate)
            or (j and abs(int(ta[i])-int(tb[j-1]))<=gate)
            or (j+1<len(tb) and abs(int(ta[i])-int(tb[j+1]))<=gate)):
            ambiguous+=1;i+=1;j+=1;continue
        matches.append((int(da[i]),int(db[j])));residual.append(delta);i+=1;j+=1
    z=[(a%2,b%2) for a,b in matches if a//2==b//2==0]
    x=[(a%2,b%2) for a,b in matches if a//2==b//2==1]
    x_errors=sum(a!=b for a,b in x);z_errors=sum(a!=b for a,b in z)
    sifted=len(x)+len(z);qber=(x_errors+z_errors)/sifted if sifted else None
    randomness=raw.get('public_randomness')
    if randomness:
        try:
            seed_bytes=bytes.fromhex(randomness['seed_hex'])
            if len(seed_bytes)!=16 or not isinstance(randomness['origin'],str):raise ValueError()
        except (ValueError,TypeError,KeyError) as exc:raise ProtocolError('invalid_randomness','128-bit public seed and origin required') from exc
        seed=int.from_bytes(seed_bytes,'big');random_origin=randomness['origin']
    else:seed=secrets.randbits(128);random_origin='OS CSPRNG; endpoint RNG qualification absent'
    # The processor is source-independent. A captured public seed allows exact
    # replay; its entropy and independence must be qualified before secure use.
    rng=np.random.default_rng(seed)
    a=np.asarray([row[0] for row in z],dtype=np.uint8);b=np.asarray([row[1] for row in z],dtype=np.uint8)
    verified=False;leak=0;corrected=0;amplified=0
    verification=math.ceil(math.log2(1/p.get('verification_error',1e-10)))
    if len(z):
        b,leak,corrected=reconcile(a,b,rng,z_errors/len(z))
        verification_seed=rng.integers(0,2,len(a)+verification-1,dtype=np.uint8)
        verified=bool(np.array_equal(toeplitz(a,verification_seed,verification),toeplitz(b,verification_seed,verification)))
    bound=finite_budget(len(z),len(x),x_errors,leak,verification,p)
    budget=bound['budget_bits'] if verified else 0
    if budget:
        pa_seed=rng.integers(0,2,len(a)+budget-1,dtype=np.uint8)
        amplified=budget if np.array_equal(toeplitz(a,pa_seed,budget),toeplitz(b,pa_seed,budget)) else 0
        if not amplified:bound['abort_reason']='privacy_amplification_verification_failed'
    if not verified:bound['abort_reason']='reconciliation_verification_failed' if z else 'no_sifted_key'
    stages=['coincidence_matching','basis_sifting','finite_parameter_estimation']
    if z:stages.extend(('parity_reconciliation','universal_hash_verification'))
    if budget:stages.append('toeplitz_privacy_amplification')
    common_open=any(max(a,c)<min(b,d) for a,b in raw['tracking'][0]['open_intervals_s'] for c,d in raw['tracking'][1]['open_intervals_s'])
    if not all(t['locked'] for t in raw['tracking']):common_open=False
    return {'protocol':'BBM92 inter-satellite time-tag / finite-block conditional demonstration' if isl else 'BBM92 time-tag / finite-block conditional demonstration',
        'emitted_pairs':raw['emitted_pairs'],'coincidences':len(matches),'pair_rate_hz':len(matches)/duration,
        'qber':qber,'qber_z':z_errors/len(z) if z else None,'qber_x':x_errors/len(x) if x else None,
        'sifted_bits':sifted,'key_basis_bits':len(z),'parameter_sample_bits':len(x),
        'error_correction_leak_bits':leak,'reconciliation_corrected_bits':corrected,
        'reconciliation_verified':verified,'verification_tag_bits':verification,
        'conditional_key_estimate_bits':amplified,'privacy_amplification_executed_bits':amplified,
        'certified_secret_bits':0,'finite_key':bound,
        'security_status':'not_certified: conditional finite-block BBM92; mission authentication/device proof and independent qualification required',
        'randomness_origin':random_origin,
        'geometry':geometry,'housekeeping':raw['housekeeping'],'tracking':raw['tracking'],
        **({'isl_geometry':isl_geometry,'satellite_housekeeping':raw['housekeeping'],
            'reason':', '.join(isl_geometry['block_reasons']),
            'resource_scope':'consumed detector measurements; no stored quantum state or end-to-end relay',
            'measurement_resources':{'emitted_pairs':raw['emitted_pairs'],
                'detected_tags':sum(len(b['time_ps']) for b in batches),'stored_pairs':0}}
           if isl else {}),
        'tdc_summary':[{'station_id':b['station_id'],'clicks':len(b['time_ps']),'clock_reference':b['clock_reference'],
            'clock_uncertainty_ps':b['clock_uncertainty_ps'],'lost_tags':b['lost_tags'],
            'dead_time_discarded':b.get('dead_time_discarded',None)} for b in batches],
        'coincidence_residual_rms_ps':math.sqrt(sum(x*x for x in residual)/len(residual)) if residual else None,
        'ambiguous_gates_discarded':ambiguous,'available':common_open,
        'available_at_end':all(g['visible'] for g in geometry),
        'parameter_provenance':raw.get('parameter_provenance',{}),'fidelity':None,
        'postprocessing':stages,
        'postprocessing_skipped':[] if budget else [{'stage':'toeplitz_privacy_amplification','reason':bound['abort_reason']}]}
