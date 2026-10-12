"""Auditable reduced physical models; parameter provenance lives in the catalogue.

Werner/depolarizing states and circular Kepler orbits are explicit approximations,
not descriptions of institution hardware. No reach or fidelity is guaranteed.
"""
from __future__ import annotations

import math

C_KM_S = 299792.458
EARTH_KM = 6371.0
MU_EARTH = 398600.4418
OMEGA_EARTH = 7.2921159e-5


def entropy(error):
    if error <= 0. or error >= 1.:
        return 0.
    return -error * math.log2(error) - (1. - error) * math.log2(1. - error)


def transmission(length_km, attenuation_db_km, insertion_db=0.):
    return 10. ** (-(length_km * attenuation_db_km + insertion_db) / 10.)


def visibility_fidelity(visibility):
    return max(.0, min(1., (1. + 3. * visibility) / 4.))


def werner_visibility(fidelity):
    return (4. * fidelity - 1.) / 3.


def aged_fidelity(fidelity, elapsed_s, coherence_s):
    return visibility_fidelity(werner_visibility(fidelity) * math.exp(-max(0., elapsed_s) / coherence_s))


def swap_fidelity(first, second, operation_error):
    return visibility_fidelity(werner_visibility(first) * werner_visibility(second) * (1. - operation_error))


def bbpssw(first, second, operation_error=0.):
    a, b = 1. - first, 1. - second
    success = first * second + (first * b + second * a) / 3. + 5. * a * b / 9.
    conditional = (first * second + a * b / 9.) / success
    # Local gate/readout noise model after ideal conditional recurrence; not a
    # security proof or the state-specific DEJMPS recurrence.
    return success, visibility_fidelity(werner_visibility(conditional) * (1. - operation_error))


def tomography_fidelity(raw):
    counts = raw['correlations']
    correlations = []
    for axis in ('xx', 'yy', 'zz'):
        row = counts[axis]
        correlations.append((row['equal'] - row['opposite']) / (row['equal'] + row['opposite']))
    return max(0., min(1., (1. + correlations[0] - correlations[1] + correlations[2]) / 4.))


def circular_ephemeris(time_s, *, latitude, longitude, altitude_km=500., inclination_deg=53., phase_rad=0.):
    """Circular ECI orbit, rotating Earth, ECEF station, range/radial velocity.

    RAAN makes the track pass over the reference station at time zero when
    latitude is reachable. No TLE, SGP4, terrain or refraction is claimed.
    """
    lat, lon, inc = map(math.radians, (latitude, longitude, inclination_deg))
    reachable = math.asin(abs(math.sin(inc)))
    if not 0. <= inc <= math.pi or abs(lat) > reachable + 1e-12:
        raise ValueError('configured orbit cannot pass over its reference latitude')
    initial = 0. if abs(math.sin(inc)) < 1e-12 else math.asin(
        max(-1., min(1., math.sin(lat) / math.sin(inc))))
    local_lon = math.atan2(math.cos(inc) * math.sin(initial), math.cos(initial))
    raan = lon - local_lon
    radius = EARTH_KM + altitude_km
    mean_motion = math.sqrt(MU_EARTH / radius ** 3)
    angle = initial + mean_motion * time_s + phase_rad
    ca, sa, cr, sr, ci, si = math.cos(angle), math.sin(angle), math.cos(raan), math.sin(raan), math.cos(inc), math.sin(inc)
    eci = (radius * (cr * ca - sr * ci * sa), radius * (sr * ca + cr * ci * sa), radius * si * sa)
    velocity = (radius * mean_motion * (-cr * sa - sr * ci * ca), radius * mean_motion * (-sr * sa + cr * ci * ca), radius * mean_motion * si * ca)
    turn = OMEGA_EARTH * time_s
    ct, st = math.cos(turn), math.sin(turn)
    x, y, z = eci
    ecef = (ct * x + st * y, -st * x + ct * y, z)
    vx, vy, vz = velocity
    v_ecef = (ct * vx + st * vy + OMEGA_EARTH * ecef[1], -st * vx + ct * vy - OMEGA_EARTH * ecef[0], vz)
    station = (EARTH_KM * math.cos(lat) * math.cos(lon), EARTH_KM * math.cos(lat) * math.sin(lon), EARTH_KM * math.sin(lat))
    delta = tuple(a - b for a, b in zip(ecef, station))
    distance = math.sqrt(sum(x * x for x in delta))
    elevation = math.degrees(math.asin(max(-1., min(1., sum(a * b for a, b in zip(delta, station)) / (distance * EARTH_KM)))))
    radial = sum(a * b for a, b in zip(delta, v_ecef)) / distance
    return {'range_km': distance, 'elevation_deg': elevation, 'radial_velocity_km_s': radial,
            'sat_latitude': math.degrees(math.asin(z / radius)), 'sat_longitude': math.degrees(math.atan2(ecef[1], ecef[0])),
            'period_s': 2. * math.pi / mean_motion}


POINTING_JITTER_CONVENTIONS = ('gaussian_per_axis', 'gaussian_radial_rms', 'legacy_offset_surrogate')


def pointing_sigma(parameters, default_urad):
    """Convert declared jitter to a per-axis Gaussian angular standard deviation.

    The existing pointing_jitter_urad field now defaults to a one-axis sigma.
    A radial RMS is sqrt(2)*sigma. Historic deterministic-offset estimates can
    be requested explicitly for replay, never silently labelled as convolution.
    """
    convention=parameters.get('pointing_jitter_convention','gaussian_per_axis')
    if convention not in POINTING_JITTER_CONVENTIONS:
        raise ValueError('unknown pointing_jitter_convention')
    value=parameters.get('pointing_jitter_urad',default_urad)
    if type(value) not in (int,float):
        raise ValueError('pointing jitter requires numeric microradians')
    sigma=value*1e-6
    if not math.isfinite(sigma) or sigma<0:
        raise ValueError('pointing jitter requires finite nonnegative microradians')
    if convention=='gaussian_radial_rms':
        sigma/=math.sqrt(2.)
    return sigma,convention


def gaussian_aperture_budget(range_m, wavelength_m, divergence_rad, receiver_radius_m,
                             jitter_sigma_rad=0., deterministic_offset_rad=0., *, legacy=False):
    """Gaussian diffraction + 2D pointing convolution over a finite aperture.

    Paraxial I(r) proportional to exp(-2*r²/w²). For independent per-axis
    pointing sigma, w_eff² = w0² + (theta*L)² + 4*(L*sigma)². A nonzero mean
    offset uses the noncentral chi-square aperture integral, not an on-axis
    intensity multiplier. The input offset combines uncorrelated scalar
    tracking/calibration axes in quadrature; vector mission APT is not modelled.
    Reference: Wallnofer et al. (2022), arXiv:2110.15806v2, Appendix A.1.
    """
    inputs=(range_m,wavelength_m,divergence_rad,receiver_radius_m,jitter_sigma_rad,deterministic_offset_rad)
    if any(type(value) not in (int,float) or not math.isfinite(value) for value in inputs):
        raise ValueError('finite SI Gaussian optical parameters required')
    if range_m<0 or min(wavelength_m,divergence_rad,receiver_radius_m)<=0 or min(jitter_sigma_rad,deterministic_offset_rad)<0:
        raise ValueError('positive wavelength/divergence/aperture and nonnegative range/pointing required')
    waist=wavelength_m/(math.pi*divergence_rad)
    beam_sq=waist*waist+(divergence_rad*range_m)**2
    geometric=-math.expm1(-2*receiver_radius_m**2/beam_sq)
    effective_sq=beam_sq+4*(range_m*jitter_sigma_rad)**2
    if legacy:
        # Explicit historical surrogate. Original far-field approximation is
        # retained in this branch for numerical replay of old configurations.
        spot=max(.01,range_m*divergence_rad)
        geometric=-math.expm1(-2*receiver_radius_m**2/spot**2)
        pointing=math.exp(-min(700.,2*(math.hypot(jitter_sigma_rad,deterministic_offset_rad)/divergence_rad)**2))
        capture=geometric*pointing
        model='legacy_offset_surrogate; not a 2D Gaussian pointing distribution'
    else:
        if deterministic_offset_rad==0.:
            capture=-math.expm1(-2*receiver_radius_m**2/effective_sq)
        else:
            from scipy.special import chndtr
            capture=float(chndtr(4*receiver_radius_m**2/effective_sq,2.,
                                4*(range_m*deterministic_offset_rad)**2/effective_sq))
            if not math.isfinite(capture):
                raise ValueError('Gaussian aperture integral did not converge')
            capture=max(0.,min(1.,capture))
        pointing=min(1.,capture/geometric) if geometric else 0.
        model='gaussian_2d_per_axis_convolution_finite_aperture_v2'
    return {'geometric_efficiency':geometric,'pointing_efficiency':pointing,
            'aperture_capture_efficiency':capture,'beam_waist_m':waist,
            'beam_radius_m':math.sqrt(beam_sq),'jitter_broadened_beam_radius_m':math.sqrt(effective_sq),
            'pointing_sigma_per_axis_urad':jitter_sigma_rad*1e6,
            'pointing_deterministic_offset_urad':deterministic_offset_rad*1e6,
            'pointing_radial_rms_urad':math.sqrt(2*jitter_sigma_rad**2+deterministic_offset_rad**2)*1e6,
            'pointing_model':model,'pointing_model_legacy':legacy}


def satellite_optics(geometry, parameters, *, cloud=False):
    elevation = geometry['elevation_deg']
    visible = elevation >= parameters.get('min_elevation_deg', 20.) and not cloud and parameters.get('pointing_enabled',True)
    wavelength = parameters.get('wavelength_nm', 850.) * 1e-9
    range_m = geometry['range_km'] * 1000.
    divergence = parameters.get('divergence_urad', 10.) * 1e-6
    diameter=parameters.get('transmitter_diameter_m')
    floor=wavelength/(math.pi*diameter/2.) if diameter is not None else 0.
    divergence=max(divergence,floor)
    radius = parameters.get('receiver_diameter_m', 1.) / 2.
    sigma,convention=pointing_sigma(parameters,2.)
    angular_tracking=geometry.get('angular_tracking_rate_rad_s',geometry.get('transverse_velocity_km_s',0.)/max(geometry['range_km'],1e-9))
    tracking=angular_tracking*parameters.get('tracking_latency_s',0.)
    advance=geometry.get('point_ahead_urad',0.)*1e-6
    advance_correction=advance if parameters.get('point_ahead_compensation',True) else 0.
    advance_residual=advance-advance_correction+parameters.get('point_ahead_calibration_error_urad',0.)*1e-6
    alignment=parameters.get('receiver_alignment_error_urad',0.)*1e-6
    deterministic=math.hypot(tracking,advance_residual,alignment)
    aperture=gaussian_aperture_budget(range_m,wavelength,divergence,radius,sigma,deterministic,
                                     legacy=convention=='legacy_offset_surrogate')
    geometric=aperture['geometric_efficiency']
    pointing=aperture['pointing_efficiency']
    pointing_error=math.hypot(sigma,deterministic)
    fov=parameters.get('terminal_fov_urad')
    fov_margin=parameters.get('pointing_fov_sigma_margin',1.)
    if fov is not None and deterministic+fov_margin*sigma>=fov*1e-6:
        visible=False
    airmass = 1. / max(.05, math.sin(math.radians(max(0., elevation))))
    atmospheric = math.exp(-parameters.get('zenith_optical_depth', .2) * airmass)
    efficiency = parameters.get('optical_efficiency', .5)
    insertion_db = parameters.get('insertion_loss_db', 2.) + parameters.get('extra_loss_db', 0.)
    insertion = 10. ** (-insertion_db/10.)
    cn2 = parameters.get('cn2_m_neg_2_3', 0.)
    turbulent_path = parameters.get('turbulence_layer_m', 2000.)*airmass
    fried = (.423*(2*math.pi/wavelength)**2*cn2*turbulent_path)**(-3/5) if cn2>0 else None
    variance = (parameters.get('receiver_diameter_m',1.)/fried)**(5/3) if fried else 0.
    if parameters.get('adaptive_optics',False):
        variance *= parameters.get('ao_residual_fraction',.5)
    # Phase-screen Strehl approximation applies to SMF coupling. A bucket
    # detector does not inherit this penalty/gain merely by toggling AO.
    turbulence = math.exp(-min(700.,variance)) if parameters.get('receiver_coupling','bucket')=='single_mode_fiber' else 1.
    eta = geometric * pointing * atmospheric * efficiency * insertion * turbulence if visible else 0.
    shift = -geometry['radial_velocity_km_s'] / C_KM_S * (299792458. / wavelength)
    bandwidth=parameters.get('doppler_tuning_limit_hz',1e11)
    estimated_shift=shift+geometry.get('doppler_prediction_error_hz',0.)
    compensation = max(-bandwidth,min(bandwidth,-estimated_shift)) if parameters.get('doppler_compensation', True) else 0.
    residual = shift + compensation + parameters.get('doppler_residual_hz', 5e6)
    filter_efficiency = math.exp(-.5 * (residual / parameters.get('filter_sigma_hz', 1e9)) ** 2)
    total = eta * filter_efficiency
    def loss(value):
        return -10*math.log10(max(1e-300,value))
    angle=geometry.get('polarization_rotation_rad',parameters.get('polarization_error_rad',.03))
    correction=-angle if parameters.get('polarization_compensation',True) else 0.
    residual_angle=angle+correction+parameters.get('polarization_calibration_error_rad',.01)
    return {**geometry, **aperture, 'visible': visible, 'channel_transmission': total,
            'geometric_efficiency': geometric, 'pointing_efficiency': pointing, 'atmospheric_transmission': atmospheric,
            'doppler_shift_hz': shift, 'doppler_correction_hz': compensation, 'doppler_residual_hz': residual,
            'filter_efficiency': filter_efficiency, 'flight_time_s': geometry['range_km'] / C_KM_S,
            'carrier_doppler_ghz':shift/1e9,'repetition_doppler_hz':-geometry['radial_velocity_km_s']/C_KM_S*parameters.get('pair_source_hz',5e6),
            'pointing_error_urad':pointing_error*1e6,
            'pointing_error_semantics':'hypot(per-axis sigma, deterministic mean offset); see radial RMS separately',
            'pointing_jitter_convention':convention,'tracking_offset_urad':tracking*1e6,
            'point_ahead_correction_urad':advance_correction*1e6,'point_ahead_residual_urad':advance_residual*1e6,
            'receiver_alignment_error_urad':alignment*1e6,
            'fov_admission_policy':'configured conservative |mean| + N*sigma < FOV; not a hard bound on Gaussian tails',
            'pointing_fov_sigma_margin':fov_margin,
            'configured_divergence_urad':parameters.get('divergence_urad',10.),
            'effective_divergence_urad':divergence*1e6,'diffraction_floor_urad':floor*1e6,
            'cn2_m_neg_2_3':cn2,'fried_parameter_m':fried,
            'adaptive_optics':parameters.get('adaptive_optics',False),'turbulence_efficiency':turbulence,
            'e_mis_before':math.sin(angle)**2,'e_mis_after':math.sin(residual_angle)**2,
            'polarization_rotation_rad':angle,'polarization_correction_rad':correction,
            'link_budget_db':{'geometric':loss(geometric),'pointing':loss(pointing),
                'atmospheric':loss(atmospheric),'optical':loss(efficiency),'insertion':insertion_db,
                'turbulence':loss(turbulence),'spectral_filter':loss(filter_efficiency),
                'total':loss(total) if visible else None},
            'model_scope':'Paraxial finite-waist Gaussian aperture and 2D per-axis pointing convolution; scalar deterministic APT offsets; plane-parallel atmosphere; optional constant-Cn2 SMF Strehl approximation'}
