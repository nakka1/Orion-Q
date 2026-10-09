"""Shared circular ephemeris evaluated against arbitrary stations."""
import math
from .physics import C_KM_S, EARTH_KM, circular_ephemeris


def station_geometry(time_s, reference, station, parameters):
    if parameters.get('orbit_record'):
        from .orbits import geometry
        return geometry(parameters['orbit_record'],time_s,station,parameters.get('station_altitude_m',0.))
    def position(t):
        return circular_ephemeris(t, latitude=reference[0], longitude=reference[1],
            altitude_km=parameters.get('altitude_km',500.), inclination_deg=parameters.get('inclination_deg',53.),
            phase_rad=parameters.get('orbit_phase_rad',0.))
    def distance(t):
        orbit = position(t)
        slat,slon,lat,lon = map(math.radians,(orbit['sat_latitude'],orbit['sat_longitude'],*station))
        radius = EARTH_KM+parameters.get('altitude_km',500.)
        dot = math.sin(slat)*math.sin(lat)+math.cos(slat)*math.cos(lat)*math.cos(slon-lon)
        d = math.sqrt(radius**2+EARTH_KM**2-2*radius*EARTH_KM*dot)
        elevation = math.degrees(math.asin(max(-1.,min(1.,(radius*dot-EARTH_KM)/d))))
        return orbit,d,elevation
    orbit,d,elevation = distance(time_s)
    radial = (distance(time_s+.05)[1]-distance(time_s-.05)[1])/.1
    return {**orbit,'range_km':d,'elevation_deg':elevation,'radial_velocity_km_s':radial}
