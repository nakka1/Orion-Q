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


def satellite_optics(geometry, parameters, *, cloud=False):
    elevation = geometry['elevation_deg']
    visible = elevation >= parameters.get('min_elevation_deg', 20.) and not cloud
    wavelength = parameters.get('wavelength_nm', 850.) * 1e-9
    range_m = geometry['range_km'] * 1000.
    divergence = parameters.get('divergence_urad', 10.) * 1e-6
    spot = max(.01, divergence * range_m)
    radius = parameters.get('receiver_diameter_m', 1.) / 2.
    geometric = 1. - math.exp(-2. * radius ** 2 / spot ** 2)
    pointing_error = parameters.get('pointing_jitter_urad', 2.)*1e-6
    pointing_error = math.hypot(pointing_error, geometry.get('transverse_velocity_km_s',0.) / max(geometry['range_km'],1e-9) * parameters.get('tracking_latency_s',0.))
    pointing = math.exp(-2. * (pointing_error / divergence) ** 2)
    airmass = 1. / max(.05, math.sin(math.radians(max(0., elevation))))
    atmospheric = math.exp(-parameters.get('zenith_optical_depth', .2) * airmass)
    efficiency = parameters.get('optical_efficiency', .5)
    insertion_db = parameters.get('insertion_loss_db', 2.) + parameters.get('extra_loss_db', 0.)
    insertion = 10. ** (-insertion_db/10.)
    cn2 = parameters.get('cn2_m_neg_2_3', 0.)
    turbulent_path = parameters.get('turbulence_layer_m', 2000.)*airmass
    fried = (.423*(2*math.pi/wavelength)**2*cn2*turbulent_path)**(-3/5) if cn2>0 else None
    variance = (parameters.get('receiver_diameter_m',1.)/fried)**(5/3) if fried else 0.
    if parameters.get('adaptive_optics',False): variance *= parameters.get('ao_residual_fraction',.5)
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
    loss=lambda value: -10*math.log10(max(1e-300,value))
    angle=geometry.get('polarization_rotation_rad',parameters.get('polarization_error_rad',.03))
    correction=-angle if parameters.get('polarization_compensation',True) else 0.
    residual_angle=angle+correction+parameters.get('polarization_calibration_error_rad',.01)
    return {**geometry, 'visible': visible, 'channel_transmission': total,
            'geometric_efficiency': geometric, 'pointing_efficiency': pointing, 'atmospheric_transmission': atmospheric,
            'doppler_shift_hz': shift, 'doppler_correction_hz': compensation, 'doppler_residual_hz': residual,
            'filter_efficiency': filter_efficiency, 'flight_time_s': geometry['range_km'] / C_KM_S,
            'carrier_doppler_ghz':shift/1e9,'repetition_doppler_hz':-geometry['radial_velocity_km_s']/C_KM_S*parameters.get('pair_source_hz',5e6),
            'pointing_error_urad':pointing_error*1e6,'cn2_m_neg_2_3':cn2,'fried_parameter_m':fried,
            'adaptive_optics':parameters.get('adaptive_optics',False),'turbulence_efficiency':turbulence,
            'e_mis_before':math.sin(angle)**2,'e_mis_after':math.sin(residual_angle)**2,
            'polarization_rotation_rad':angle,'polarization_correction_rad':correction,
            'link_budget_db':{'geometric':loss(geometric),'pointing':loss(pointing),
                'atmospheric':loss(atmospheric),'optical':loss(efficiency),'insertion':insertion_db,
                'turbulence':loss(turbulence),'spectral_filter':loss(filter_efficiency),
                'total':loss(total) if visible else None},
            'model_scope':'Gaussian far-field half-angle divergence; plane-parallel atmosphere; optional constant-Cn2 SMF Strehl approximation'}
