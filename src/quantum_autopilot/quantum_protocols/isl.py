"""Inter-satellite geometry, terminal admission and received-event processing.

BBM92 is measured between two satellites: one source photon is detected locally
and its partner is transmitted to the peer. Those consumed pairs are not stored
quantum resources and cannot be concatenated into an unimplemented repeater.
"""
from __future__ import annotations

import math

from .contracts import ProtocolError, number
from .physics import C_KM_S, gaussian_aperture_budget, pointing_sigma
from .orbits import position, validate_orbit

ISL_SCHEMA = 'orion.inter-satellite-observation.v1'
ISL_CAPABILITIES = ('optical_space_terminal', 'tracking', 'time_tagger', 'single_photon_detector')


def terminal_parameters(parameters, node_id):
    return {**parameters, **parameters.get('device_parameters', {}).get(node_id, {})}


def terminal_compatibility(a, b, *, allowed_encodings=('polarization_BBM92',)):
    """Admit only encodings explicitly supported by the calling protocol.

    The measured BBM92 adapter keeps its default restriction. A separate memory
    relay may request its own encoding without making the BBM92 adapter accept
    stored pairs or mismatched terminals.
    """
    reasons=[]
    if abs(a.get('wavelength_nm',850.)-b.get('wavelength_nm',850.))>.1:
        reasons.append('incompatible_wavelength')
    encoding=a.get('quantum_encoding','polarization_BBM92')
    if encoding not in allowed_encodings or b.get('quantum_encoding','polarization_BBM92')!=encoding:
        reasons.append('incompatible_quantum_encoding')
    for terminal in (a,b):
        if terminal.get('optical_duplex','dedicated_quantum_and_classical')!='dedicated_quantum_and_classical':
            reasons.append('unpaired_physical_channels')
    return tuple(dict.fromkeys(reasons))


def inter_satellite_geometry(left, right, parameters=None):
    """Mutual LOS against expanded WGS84 ellipsoid, not a map connection.

    Vectors must share ECEF/time. Relative radial velocity is frame invariant;
    optical point-ahead uses inertial relative velocity (Earth rotation restored).
    The conservative limb margin avoids presenting a grazing atmosphere path as
    an unobstructed vacuum link. Sun exclusion/attitude ephemerides are absent.
    """
    p=parameters or {}
    if left.get('frame')!='ECEF' or right.get('frame')!='ECEF':
        raise ProtocolError('invalid_ephemeris','ISL requires two normalized ECEF vectors')
    if abs(left['at_s']-right['at_s'])>p.get('sync_tolerance_s',1e-6):
        raise ProtocolError('unsynchronized','ISL state vectors refer to different times')
    a,b=left['ecef_km'],right['ecef_km']
    delta=[y-x for x,y in zip(a,b)];length=math.sqrt(sum(x*x for x in delta))
    if length<=1e-9:raise ProtocolError('invalid_ephemeris','ISL endpoints coincide')
    direction=[x/length for x in delta]
    margin=p.get('earth_limb_margin_km',80.)
    radii=(6378.137+margin,6378.137+margin,6356.752314245+margin)
    scaled_a=[x/r for x,r in zip(a,radii)]
    scaled_delta=[x/r for x,r in zip(delta,radii)]
    closest=max(0.,min(1.,-sum(x*y for x,y in zip(scaled_a,scaled_delta))/sum(x*x for x in scaled_delta)))
    norm=math.sqrt(sum((x+closest*y)**2 for x,y in zip(scaled_a,scaled_delta)))
    clear=norm>1.
    velocity=[y-x for x,y in zip(left['velocity_ecef_km_s'],right['velocity_ecef_km_s'])]
    radial=sum(x*y for x,y in zip(direction,velocity))
    from .physics import OMEGA_EARTH
    inertial=[velocity[0]-OMEGA_EARTH*delta[1],velocity[1]+OMEGA_EARTH*delta[0],velocity[2]]
    transverse=math.sqrt(max(0.,sum(x*x for x in inertial)-radial**2))
    return {'range_km':length,'flight_time_s':length/C_KM_S,
        'radial_velocity_km_s':radial,'transverse_velocity_km_s':transverse,
        'angular_tracking_rate_rad_s':transverse/length,
        'point_ahead_urad':2*transverse/C_KM_S*1e6,
        'point_ahead_convention':'received beacon to outgoing aim; inertial 2*v_transverse/c',
        'earth_obstructed':not clear,'earth_limb_margin_km':margin,
        'earth_clearance_scaled':norm-1.,'closest_segment_fraction':closest,
        'at_s':left['at_s'],'frame':'ECEF',
        'geometry_scope':'WGS84 ellipsoid LOS + configured limb margin; Sun/attitude constraints not implemented'}


def isl_optics(geometry, transmitter, receiver, *, allowed_encodings=('polarization_BBM92',)):
    """Gaussian far-field budget, finite aperture, jitter/FOV and Doppler.

    No atmospheric gain or attenuation is applied to a clear vacuum ISL.
    Divergence cannot be smaller than lambda/(pi*waist) for the configured
    ideal Gaussian transmitter aperture. Truncation/misalignment are hypotheses.
    """
    g=dict(geometry)
    reasons=list(terminal_compatibility(transmitter,receiver,allowed_encodings=allowed_encodings))
    if g['earth_obstructed']:
        reasons.append('earth_obstruction')
    if g['range_km']>min(transmitter.get('max_range_km',90000.),receiver.get('max_range_km',90000.)):
        reasons.append('terminal_range_limit')
    if not transmitter.get('pointing_enabled',True) or not receiver.get('pointing_enabled',True):
        reasons.append('pointing_disabled')
    wavelength=transmitter.get('wavelength_nm',850.)*1e-9
    diameter=transmitter.get('transmitter_diameter_m',.5)
    floor=wavelength/(math.pi*diameter/2.)
    divergence=max(transmitter.get('divergence_urad',3.)*1e-6,floor)
    radius=receiver.get('receiver_diameter_m',.5)/2.
    tracking=g['angular_tracking_rate_rad_s']*transmitter.get('tracking_latency_s',.001)
    sigma,convention=pointing_sigma(transmitter,.3)
    advance=g['point_ahead_urad']*1e-6
    advance_correction=advance if transmitter.get('point_ahead_compensation',True) else 0.
    advance_residual=advance-advance_correction+transmitter.get('point_ahead_calibration_error_urad',0.)*1e-6
    residual=receiver.get('receiver_alignment_error_urad',0.)*1e-6
    deterministic=math.hypot(tracking,advance_residual,residual)
    pointing=math.hypot(sigma,deterministic)
    fov=receiver.get('terminal_fov_urad',20.)*1e-6
    fov_margin=receiver.get('pointing_fov_sigma_margin',1.)
    if deterministic+fov_margin*sigma>=fov:
        reasons.append('receiver_field_of_view')
    aperture=gaussian_aperture_budget(g['range_km']*1000.,wavelength,divergence,radius,sigma,deterministic,
                                     legacy=convention=='legacy_offset_surrogate')
    geometric=aperture['geometric_efficiency']
    pointing_efficiency=aperture['pointing_efficiency']
    if pointing_efficiency<1e-12:
        reasons.append('pointing_loss')
    shift=-g['radial_velocity_km_s']/C_KM_S*(299792458./wavelength)
    limit=receiver.get('doppler_tuning_limit_hz',1e11)
    correction=max(-limit,min(limit,-shift)) if receiver.get('doppler_compensation',True) else 0.
    residual_hz=shift+correction+receiver.get('doppler_residual_hz',0.)
    spectral=math.exp(-min(700.,.5*(residual_hz/receiver.get('filter_sigma_hz',1e9))**2))
    efficiency=transmitter.get('optical_efficiency',.5)*receiver.get('optical_efficiency',.5)
    insertion=transmitter.get('insertion_loss_db',2.)+receiver.get('insertion_loss_db',2.)+transmitter.get('extra_loss_db',0.)+receiver.get('extra_loss_db',0.)
    optical=geometric*pointing_efficiency*efficiency*10**(-insertion/10.)*spectral
    visible=not reasons
    def loss(value):
        return -10*math.log10(max(1e-300,value))
    return {**g,**aperture,'visible':visible,'block_reasons':list(dict.fromkeys(reasons)),
        'channel_transmission':optical if visible else 0.,
        'potential_channel_transmission':optical,'geometric_efficiency':geometric,
        'pointing_efficiency':pointing_efficiency,'pointing_error_urad':pointing*1e6,
        'pointing_error_semantics':'hypot(per-axis sigma, deterministic mean offset); see radial RMS separately',
        'pointing_jitter_convention':convention,'tracking_offset_urad':tracking*1e6,
        'receiver_alignment_error_urad':residual*1e6,
        'fov_admission_policy':'configured conservative |mean| + N*sigma < FOV; not a hard bound on Gaussian tails',
        'pointing_fov_sigma_margin':fov_margin,
        'point_ahead_correction_urad':advance_correction*1e6,
        'point_ahead_residual_urad':advance_residual*1e6,
        'configured_divergence_urad':transmitter.get('divergence_urad',3.),
        'effective_divergence_urad':divergence*1e6,'diffraction_floor_urad':floor*1e6,
        'doppler_shift_hz':shift,'doppler_correction_hz':correction,'doppler_residual_hz':residual_hz,
        'carrier_doppler_ghz':shift/1e9,'filter_efficiency':spectral,
        'link_budget_db':{'geometric':loss(geometric),'pointing':loss(pointing_efficiency),
            'optical':loss(efficiency),'insertion':insertion,'spectral_filter':loss(spectral),
            'total':loss(optical) if visible else None},
        'model_scope':'hypothetical finite-waist Gaussian vacuum ISL; 2D per-axis pointing convolution; scalar APT offsets; diffraction floor; no validated mission/attitude calibration'}


def geometry_at(parameters, endpoints, network_time_s):
    orbits=parameters.get('satellite_orbits',{})
    states={ident:{**position(orbits[ident],network_time_s),'at_s':network_time_s} for ident in endpoints}
    a,b=(terminal_parameters(parameters,n) for n in endpoints)
    geometry=isl_optics(inter_satellite_geometry(*(states[n] for n in endpoints),parameters),a,b)
    windows=[orbits[n].get('communication_windows',[]) for n in endpoints]
    if any(w and not any(x<=network_time_s<y for x,y in w) for w in windows):
        geometry.update(visible=False,channel_transmission=0.)
        geometry['block_reasons'].append('communication_window')
    return states,geometry


def received_geometry(raw, parameters, endpoints):
    a,b=(terminal_parameters(parameters,n) for n in endpoints)
    a={**a,'extra_loss_db':a.get('extra_loss_db',0.)+raw['tracking'][0].get('additional_channel_loss_db',0.)}
    b={**b,'extra_loss_db':b.get('extra_loss_db',0.)+raw['tracking'][1].get('additional_channel_loss_db',0.)}
    result=isl_optics(inter_satellite_geometry(*(raw['housekeeping'][n] for n in endpoints),parameters),a,b)
    if not all(t['locked'] and any(x<=raw['end_s']-1e-12<y for x,y in t['open_intervals_s']) for t in raw['tracking']):
        result.update(visible=False,channel_transmission=0.)
        result['block_reasons'].append('tracking_not_locked')
    result['sync_jitter_ps']=max(b['clock_uncertainty_ps'] for b in raw['tdc'])
    result['emitted_photons']=raw['emitted_pairs']
    result['detected_photons']=len(raw['tdc'][1]['time_ps'])
    return result


class InterSatelliteAdapter:
    technology_id='satellite_isl'
    required_capabilities=ISL_CAPABILITIES

    def validate(self,config,topology):
        if len(config.path)!=2 or len(config.link_ids)!=1:
            raise ProtocolError('unsupported_composition','ISL implements one measured satellite pair, not a transparent relay chain')
        for ident in config.endpoints:
            node=topology.node(ident);record=node.telemetry.get('communicator',{})
            if record and record.get('physical_type')!='satellite':
                raise ProtocolError('incompatible_node',f'{ident}: ISL requires an orbital physical device')
            caps=set(node.telemetry.get('technology_capabilities',()))
            if not set(self.required_capabilities)<=caps:
                raise ProtocolError('incompatible_node',f'{ident}: explicit optical ISL terminal/tracking/TDC capabilities required')
        if 'satellite_source' not in topology.node(config.endpoints[0]).telemetry.get('technology_capabilities',()):
            raise ProtocolError('incompatible_node','ISL transmitter requires an entangled-pair source and local detection')
        p=config.parameters;orbits=p.get('satellite_orbits',{})
        if set(orbits)!=set(config.endpoints):
            raise ProtocolError('invalid_ephemeris','one explicit orbital record per ISL endpoint required')
        for orbit in orbits.values():validate_orbit(orbit)
        for ident in config.endpoints:
            q=terminal_parameters(p,ident)
            for key,default,low,high in (
                ('wavelength_nm',850.,100.,1e5),('transmitter_diameter_m',.5,.01,10.),
                ('receiver_diameter_m',.5,.01,10.),('divergence_urad',3.,.001,1000.),
                ('pointing_jitter_urad',.3,0.,1000.),('terminal_fov_urad',20.,.001,10000.),
                ('tracking_latency_s',.001,0.,1.),('earth_limb_margin_km',80.,0.,1000.),
                ('max_range_km',90000.,1.,1e6),('local_detection_efficiency',.8,0.,1.),
                ('detector_efficiency',.8,0.,1.),('filter_sigma_hz',1e9,1.,1e15),
                ('optical_efficiency',.5,0.,1.),('insertion_loss_db',2.,0.,1000.),
                ('extra_loss_db',0.,0.,1000.),('pair_source_hz',20000.,0.,1e10),
                ('timing_jitter_ps',50.,0.,1e12),('background_cps',1.,0.,1e6),
                ('dark_count_cps',5.,0.,1e6),('detector_dead_time_ns',50.,0.,1e9),
                ('doppler_tuning_limit_hz',1e11,0.,1e15),('doppler_residual_hz',0.,-1e15,1e15),
                ('receiver_alignment_error_urad',0.,0.,10000.),('range_estimation_error_m',0.,0.,1e6)):
                number(q.get(key,default),key,low,high)
            number(q.get('point_ahead_calibration_error_urad',0.),'point_ahead_calibration_error_urad',0.,10000.)
            for key in ('pointing_enabled','doppler_compensation','propagation_compensation','point_ahead_compensation'):
                if type(q.get(key,True)) is not bool:
                    raise ProtocolError('invalid_parameter',f'{key}: explicit boolean required')
        incompat=terminal_compatibility(*(terminal_parameters(p,n) for n in config.endpoints))
        if incompat:raise ProtocolError('incompatible_terminal',', '.join(incompat))

    def process(self,raw,context):
        from .satellite_processing import process_observation
        return process_observation(raw,context)
