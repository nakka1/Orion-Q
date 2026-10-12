"""Orbit/OGS geometry with explicit frames and provenance.

Kepler/reference models remain hypothetical. Optional SGP4 propagates TLE in
TEME; ECEF conversion uses GMST with zero polar motion (not precision ITRF/EOP).
Measured ECEF vectors are interpolated only inside their supplied coverage.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from functools import lru_cache

from .physics import EARTH_KM, MU_EARTH, OMEGA_EARTH, circular_ephemeris, satellite_optics

GEO_RADIUS_KM = (MU_EARTH / OMEGA_EARTH ** 2) ** (1. / 3.)
GEO_ALTITUDE_KM = GEO_RADIUS_KM - EARTH_KM


def geo_reference_orbit(longitude, *, epoch_utc='2026-10-07T00:00:00+00:00',
                        epoch_network_s=0., compatible_stations=()):
    """Explicit hypothetical GEO, never a replacement for measured/TLE orbits.

    The radius follows the same mu/Earth rotation constants as propagation.
    Height uses ORION's existing mean-radius model; the familiar 35786 km is
    height above the equatorial ellipsoid, not above a 6371 km mean sphere.
    """
    return dict(model='geostationary_reference', orbital_class='GEO',
        epoch_utc=epoch_utc, epoch_network_s=epoch_network_s,
        source='explicit simulated geostationary reference; no mission ephemeris',
        altitude_km=GEO_ALTITUDE_KM, inclination_deg=0., eccentricity=0.,
        reference_latitude=0., reference_longitude=float(longitude), orbit_phase_rad=0.,
        raan_deg=0., argument_perigee_deg=0., mean_anomaly_deg=0.,
        compatible_stations=list(compatible_stations), communication_windows=[],
        optical_payload='hypothetical optical payload; no validated physical mission')


def ecef_geodetic(ecef):
    """WGS84 geographic coordinates and ellipsoidal height, including poles.

    NGA defining ellipsoid: a=6378137 m, inverse flattening=298.257223563.
    Latitude iterates the ellipsoid normal; height uses its support function,
    avoiding division by cos(latitude) near the poles.
    """
    x,y,z=ecef;p=math.hypot(x,y);a=6378.137;f=1/298.257223563;e2=f*(2-f)
    if math.hypot(p,z)<1e-12:raise ValueError('ECEF origin has no geographic position')
    if p<1e-12:
        latitude=math.copysign(math.pi/2,z)
    else:
        latitude=math.atan2(z,p*(1-e2))
        for _ in range(16):
            normal=a/math.sqrt(1-e2*math.sin(latitude)**2)
            updated=math.atan2(z+e2*normal*math.sin(latitude),p)
            if abs(updated-latitude)<1e-14:
                latitude=updated;break
            latitude=updated
    height=p*math.cos(latitude)+z*math.sin(latitude)-a*math.sqrt(1-e2*math.sin(latitude)**2)
    return {'sat_latitude':math.degrees(latitude),'sat_longitude':math.degrees(math.atan2(y,x)),
            'altitude_km':height,'geographic_reference':'WGS84 geodetic; ellipsoidal altitude'}


def validate_orbit(orbit):
    stamp=datetime.fromisoformat(orbit['epoch_utc'].replace('Z','+00:00'))
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError('época orbital exige fuso UTC explícito')
    windows=orbit['communication_windows']
    if not isinstance(windows,list) or len(windows)>256:
        raise ValueError('janelas: lista limitada de intervalos')
    for window in windows:
        if not isinstance(window,list) or len(window)!=2 or any(type(x) not in (int,float) or not math.isfinite(x) or x<0 for x in window) or window[1]<=window[0]:
            raise ValueError('janela deve ser [início,fim], finita e crescente, em segundos da rede')
    if orbit['source'].lower().startswith(('tle','norad')) and orbit['model']!='sgp4_tle':
        raise ValueError('TLE exige SGP4; não pode ser propagado como elementos Kepler')
    if orbit['model']=='sgp4_tle':
        _tle(orbit.get('tle_line1',''),orbit.get('tle_line2',''))
        position(orbit, orbit['epoch_network_s'])
        return
    if orbit['model']=='state_vectors':
        samples=orbit.get('state_vectors',[])
        if not isinstance(samples,list) or not 2<=len(samples)<=4096:
            raise ValueError('efemérides: 2–4096 vetores ECEF com tempo, posição e velocidade')
        from .satellite_contracts import vector
        previous=-math.inf
        for sample in samples:
            stamp=sample.get('at_network_s')
            if type(stamp) not in (int,float) or not math.isfinite(stamp) or stamp<=previous:
                raise ValueError('efemérides exigem tempos finitos estritamente crescentes')
            previous=stamp
            vector(sample.get('ecef_km'),'posição ECEF',1e6)
            vector(sample.get('velocity_ecef_km_s'),'velocidade ECEF',100.)
        return
    if orbit['model'] not in ('circular_reference','kepler_elements','geostationary_reference'):
        raise ValueError('propagador orbital desconhecido')
    if orbit['model']=='geostationary_reference':
        if (orbit['orbital_class']!='GEO' or orbit['eccentricity']!=0 or
            orbit['inclination_deg']!=0 or orbit['reference_latitude']!=0 or
            abs(orbit['altitude_km']-GEO_ALTITUDE_KM)>.01):
            raise ValueError('referência GEO exige classe GEO, equador, circularidade e raio síncrono calculado')
    if orbit['model']=='circular_reference':
        if orbit['eccentricity']!=0: raise ValueError('órbita circular exige excentricidade zero')
        circular_ephemeris(0,latitude=orbit['reference_latitude'],longitude=orbit['reference_longitude'],
            altitude_km=orbit['altitude_km'],inclination_deg=orbit['inclination_deg'])
    altitude=orbit['altitude_km']
    apogee=(EARTH_KM+altitude)*(1+orbit['eccentricity'])/(1-orbit['eccentricity'])-EARTH_KM
    if orbit['orbital_class']=='LEO' and not 100<=altitude<=apogee<=2000:
        raise ValueError('classe LEO: perigeu/apogeu entre 100 e 2000 km')
    if orbit['orbital_class']=='MEO' and not 2000<altitude<=apogee<35000:
        raise ValueError('classe MEO: perigeu/apogeu entre 2000 e 35000 km')
    if orbit['orbital_class']=='GEO' and (abs(altitude-35786)>100 or orbit['eccentricity']>.001 or orbit['inclination_deg']>.1):
        raise ValueError('GEO exige órbita aproximadamente circular/equatorial a 35786 km')
    try:
        first=position(orbit,0.)
        position(orbit,first['period_s']);position(orbit,-.05)
    except (OverflowError,ValueError) as exc:
        raise ValueError('época orbital/época da rede fora do intervalo de datas suportado') from exc


def _rotate_z(vector,angle):
    x,y,z=vector;c,s=math.cos(angle),math.sin(angle)
    return c*x-s*y,s*x+c*y,z


@lru_cache(maxsize=128)
def _tle(line1,line2):
    try:
        from sgp4.api import Satrec
        from sgp4.io import verify_checksum
    except ImportError as exc:
        raise ValueError('SGP4 indisponível: instale ORION-Q[satellite]; sem fallback Kepler') from exc
    if len(line1)!=69 or len(line2)!=69 or not line1.startswith('1 ') or not line2.startswith('2 '):
        raise ValueError('duas linhas TLE de 69 caracteres obrigatórias')
    verify_checksum(line1,line2)
    sat=Satrec.twoline2rv(line1,line2)
    if sat.error: raise ValueError(f'TLE/SGP4 inválido: {sat.error}')
    return sat


def _gmst(utc):
    jd=utc.timestamp()/86400.+2440587.5
    centuries=(jd-2451545.)/36525.
    return math.radians((280.46061837+360.98564736629*(jd-2451545.)+.000387933*centuries**2-centuries**3/38710000.)%360.)


def _position(orbit,network_time_s,*,earth_time_s=None):
    """ECEF position in km at network time; epoch maps explicitly to UTC.

    earth_time_s fixes Earth's orientation for sampling the instantaneous orbit
    plane. Leaving it None gives the actual rotating-Earth sub-satellite point.
    """
    elapsed=network_time_s-orbit['epoch_network_s']
    earth_elapsed=elapsed if earth_time_s is None else earth_time_s-orbit['epoch_network_s']
    utc=datetime.fromisoformat(orbit['epoch_utc'].replace('Z','+00:00')).astimezone(timezone.utc)+timedelta(seconds=elapsed)
    velocity=None
    if orbit['model']=='sgp4_tle':
        from sgp4.api import jday
        sat=_tle(orbit['tle_line1'],orbit['tle_line2'])
        jd,fr=jday(utc.year,utc.month,utc.day,utc.hour,utc.minute,utc.second+utc.microsecond/1e6)
        age=(jd+fr-sat.jdsatepoch-sat.jdsatepochF)*86400.
        if abs(age)>orbit.get('ephemeris_max_age_s',259200.):
            raise ValueError('TLE vencido: idade excede a política de efemérides')
        error,r,v=sat.sgp4(jd,fr)
        if error: raise ValueError(f'propagação SGP4 recusada: código {error}')
        earth_utc=utc if earth_time_s is None else utc+timedelta(seconds=earth_time_s-network_time_s)
        ecef=_rotate_z(r,-_gmst(earth_utc));rotated=_rotate_z(v,-_gmst(earth_utc))
        velocity=(rotated[0]+OMEGA_EARTH*ecef[1],rotated[1]-OMEGA_EARTH*ecef[0],rotated[2])
        period=2*math.pi/(sat.no_kozai/60.)
    elif orbit['model']=='state_vectors':
        samples=orbit['state_vectors']
        if not samples[0]['at_network_s']<=network_time_s<=samples[-1]['at_network_s']:
            raise ValueError('efemérides sem cobertura; extrapolação de posição medida bloqueada')
        left,right=next((a,b) for a,b in zip(samples,samples[1:]) if a['at_network_s']<=network_time_s<=b['at_network_s'])
        span=right['at_network_s']-left['at_network_s'];u=(network_time_s-left['at_network_s'])/span
        r0,r1,v0,v1=(left['ecef_km'],right['ecef_km'],left['velocity_ecef_km_s'],right['velocity_ecef_km_s'])
        ecef=tuple((2*u**3-3*u**2+1)*a+(u**3-2*u**2+u)*span*c+(-2*u**3+3*u**2)*b+(u**3-u**2)*span*d for a,b,c,d in zip(r0,r1,v0,v1))
        velocity=tuple((6*u*u-6*u)*a/span+(3*u*u-4*u+1)*c+(-6*u*u+6*u)*b/span+(3*u*u-2*u)*d for a,b,c,d in zip(r0,r1,v0,v1))
        period=2*math.pi*math.sqrt(math.sqrt(sum(x*x for x in ecef))**3/MU_EARTH)
    elif orbit['model']=='geostationary_reference':
        # The satellite's ECI orbit advances with Earth. The ground longitude
        # remains fixed; frozen Earth orientation samples the full orbit ring.
        angle=math.radians(orbit['reference_longitude'])+orbit.get('orbit_phase_rad',0.)+OMEGA_EARTH*(elapsed-earth_elapsed)
        ecef=(GEO_RADIUS_KM*math.cos(angle),GEO_RADIUS_KM*math.sin(angle),0.)
        velocity=(0.,0.,0.) if earth_time_s is None else (-OMEGA_EARTH*ecef[1],OMEGA_EARTH*ecef[0],0.)
        period=2*math.pi/OMEGA_EARTH
    elif orbit['model']=='circular_reference':
        sample=circular_ephemeris(elapsed,latitude=orbit['reference_latitude'],longitude=orbit['reference_longitude'],
            altitude_km=orbit['altitude_km'],inclination_deg=orbit['inclination_deg'],phase_rad=orbit['orbit_phase_rad'])
        lat,lon=map(math.radians,(sample['sat_latitude'],sample['sat_longitude']))
        radius=EARTH_KM+orbit['altitude_km']
        ecef=_rotate_z((radius*math.cos(lat)*math.cos(lon),radius*math.cos(lat)*math.sin(lon),radius*math.sin(lat)),OMEGA_EARTH*(elapsed-earth_elapsed))
        period=sample['period_s']
    else:
        eccentricity=orbit['eccentricity']
        a=(EARTH_KM+orbit['altitude_km'])/(1-eccentricity)
        n=math.sqrt(MU_EARTH/a**3)
        mean=(math.radians(orbit['mean_anomaly_deg'])+n*elapsed)%(2*math.pi)
        anomaly=mean if eccentricity<.8 else math.pi
        for _ in range(24):
            delta=(anomaly-eccentricity*math.sin(anomaly)-mean)/(1-eccentricity*math.cos(anomaly))
            anomaly-=delta
            if abs(delta)<1e-12: break
        x=a*(math.cos(anomaly)-eccentricity)
        y=a*math.sqrt(1-eccentricity**2)*math.sin(anomaly)
        x,y,z=_rotate_z((x,y,0.),math.radians(orbit['argument_perigee_deg']))
        inc=math.radians(orbit['inclination_deg'])
        eci=_rotate_z((x,y*math.cos(inc),y*math.sin(inc)),math.radians(orbit['raan_deg']))
        epoch=datetime.fromisoformat(orbit['epoch_utc'].replace('Z','+00:00')).astimezone(timezone.utc)
        jd=epoch.timestamp()/86400.+2440587.5
        gmst=math.radians((280.46061837+360.98564736629*(jd-2451545.))%360.)
        ecef=_rotate_z(eci,-gmst-OMEGA_EARTH*earth_elapsed)
        period=2*math.pi/n
    radius=math.sqrt(sum(x*x for x in ecef))
    geographic=(ecef_geodetic(ecef) if orbit['model'] in ('sgp4_tle','state_vectors') else
        {'sat_latitude':math.degrees(math.asin(ecef[2]/radius)),
         'sat_longitude':math.degrees(math.atan2(ecef[1],ecef[0])),'altitude_km':radius-EARTH_KM,
         'geographic_reference':'hypothetical geocentric model; 6371 km mean-radius reference'})
    utc=utc.isoformat()
    return {'ecef_km':list(ecef),**geographic,
            'period_s':period,'phase_turns':elapsed/period,'at_network_s':network_time_s,'at_utc':utc,
            'model':orbit['model'],'ephemeris_source':orbit['source'],
            **({'geostationary_reference':True,'equatorial_altitude_km':radius-6378.137,
                'stationkeeping_scope':'ideal simulated equatorial circular GEO; perturbations/station-keeping not modelled'}
               if orbit['model']=='geostationary_reference' else {}),
            **({'velocity_ecef_km_s':list(velocity)} if velocity is not None else {}),
            'frame':'ECEF','frame_accuracy':'SGP4 TEME→GMST ECEF; UT1=UTC, zero polar motion' if orbit['model']=='sgp4_tle' else 'configured ECEF model'}


def position(orbit,network_time_s,*,earth_time_s=None):
    state=_position(orbit,network_time_s,earth_time_s=earth_time_s)
    if 'velocity_ecef_km_s' not in state:
        before=_position(orbit,network_time_s-.001,earth_time_s=earth_time_s)['ecef_km']
        after=_position(orbit,network_time_s+.001,earth_time_s=earth_time_s)['ecef_km']
        state['velocity_ecef_km_s']=[(b-a)/.002 for a,b in zip(before,after)]
    return state


def state_geometry(state,station,altitude_m=0.):
    lat,lon=map(math.radians,station)
    # Geodetic OGS coordinates use WGS84, not geocentric latitude.
    e2=6.69437999014e-3
    n=6378.137/math.sqrt(1-e2*math.sin(lat)**2)
    h=altitude_m/1000.
    ground=((n+h)*math.cos(lat)*math.cos(lon),(n+h)*math.cos(lat)*math.sin(lon),(n*(1-e2)+h)*math.sin(lat))
    delta=tuple(a-b for a,b in zip(state['ecef_km'],ground))
    length=math.sqrt(sum(x*x for x in delta))
    if length<=0:
        raise ValueError('satélite coincide com a OGS')
    unit=tuple(x/length for x in delta)
    up=(math.cos(lat)*math.cos(lon),math.cos(lat)*math.sin(lon),math.sin(lat))
    east=(-math.sin(lon),math.cos(lon),0.)
    north=(-math.sin(lat)*math.cos(lon),-math.sin(lat)*math.sin(lon),math.cos(lat))
    elevation=math.degrees(math.asin(max(-1.,min(1.,sum(a*b for a,b in zip(unit,up))))))
    azimuth=math.degrees(math.atan2(sum(a*b for a,b in zip(unit,east)),sum(a*b for a,b in zip(unit,north))))%360.
    velocity=state['velocity_ecef_km_s']
    radial=sum(a*b for a,b in zip(unit,velocity))
    ecef_transverse=math.sqrt(max(0.,sum(x*x for x in velocity)-radial**2))
    # The ground station is stationary in ECEF, but both endpoints rotate in
    # inertial space. For a common-epoch relative position delta, restore
    # v_rel,ECI = v_rel,ECEF + Omega x delta. The rotation term is transverse,
    # so range rate/Doppler stay unchanged. GEO therefore still needs optical
    # point-ahead; zero ECEF velocity does not imply zero retarded-beacon offset.
    inertial=(velocity[0]-OMEGA_EARTH*delta[1],velocity[1]+OMEGA_EARTH*delta[0],velocity[2])
    transverse=math.sqrt(max(0.,sum(x*x for x in inertial)-radial**2))
    from .physics import C_KM_S
    return {**state,'range_km':length,'elevation_deg':elevation,'azimuth_deg':azimuth,
            'radial_velocity_km_s':radial,'transverse_velocity_km_s':transverse,
            'ecef_transverse_velocity_km_s':ecef_transverse,
            'inertial_relative_velocity_km_s':list(inertial),
            'angular_tracking_rate_rad_s':ecef_transverse/length,
            'transverse_velocity_reference':'inertial relative velocity; ECEF + Omega cross relative position',
            'tracking_rate_reference':'geometric line of sight in rotating ECEF; station fixed to Earth',
            'point_ahead_urad':2*transverse/C_KM_S*1e6,
            'point_ahead_convention':'received beacon t-flight to outgoing aim t+flight; first-order inertial 2*v_transverse/c'}


def geometry(orbit,network_time_s,station,altitude_m=0.):
    return state_geometry(position(orbit,network_time_s),station,altitude_m)


def satellite_state(record,topology,network_time_s):
    orbit=record['orbit'];state=position(orbit,network_time_s)
    # Legacy orbit-only views can omit inventory technology metadata. Geometry
    # remains available, but absence never implies an optical payload or link.
    optical_payload = bool(set(record.get('technologies', ())) & {'satellite_doppler', 'satellite_isl'})
    parameters=({**record['optical'],**record['configuration'].get('satellite_doppler',{})}
                if optical_payload else {})
    windows=orbit['communication_windows']
    in_window=not windows or any(a<=network_time_s<b for a,b in windows)
    stations={}
    for ident in orbit['compatible_stations']:
        node=next((n for n in topology.nodes if n.node_id==ident),None)
        if node is None:continue
        loc=node.telemetry.get('location')
        if not loc:continue
        if not optical_payload:
            stations[ident] = {**geometry(orbit,network_time_s,(loc['latitude'],loc['longitude']),loc.get('altitude_m',0.)),
                'in_communication_window': in_window, 'optical_payload_available': False,
                'radio_link_validated': False}
            continue
        from ..control_plane.communicators import Communicator
        ground=node.telemetry.get('communicator',{})
        optical={**parameters,**(Communicator.from_dict(ground).parameters() if ground and ground.get('revision',0)>0 else {})}
        output=satellite_optics(geometry(orbit,network_time_s,(loc['latitude'],loc['longitude']),loc.get('altitude_m',0.)),optical,
            cloud=parameters.get('cloud',False) or optical.get('cloud',False) or not in_window or not parameters.get('pointing_enabled',True) or not optical.get('pointing_enabled',True) or record['operational_state']!='enabled' or ground.get('operational_state','enabled')!='enabled')
        stations[ident]={**output,'in_communication_window':in_window,'pointing_enabled':parameters.get('pointing_enabled',True)}
    track=([s['ecef_km'] for s in orbit['state_vectors']] if orbit['model']=='state_vectors' else
           [position(orbit,network_time_s+state['period_s']*i/192.,earth_time_s=network_time_s)['ecef_km'] for i in range(193)])
    return {**state,'device_id':record['device_id'],'node_id':record['node_id'],'origin':record['integration']['data_origin'],
            'orbital_class':orbit['orbital_class'],'stations':stations,'orbit_ecef_km':track,
            'physical_tracking_validated':False,'transmitting_links':[], 'optical_payload_available': optical_payload}
