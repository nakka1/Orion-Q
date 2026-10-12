"""Versioned device inventory: physical type is independent of network function.

The topology identity is stable. Operator-facing identifiers may change without
breaking links, stored sessions, receipts or routes. Every write updates the
inventory and topology in one optimistic transaction; it never opens hardware.
"""
from __future__ import annotations

import json
import re
from contextlib import closing
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone

from .sqlite_transaction import connect
from .topology import NodeRole, SimulatedNodeState, _json_data

SCHEMA = 'orion.communicator.v1'
TYPES = ('installation', 'quantum_repeater', 'measurement_station', 'ground_station',
         'satellite', 'quantum_memory', 'purification_service')
ROLES = ('endpoint', 'core', 'repeater', 'measurement', 'ground_station', 'service', 'intermediate')
TECHNOLOGIES = ('entanglement_swapping', 'quantum_memory', 'purification', 'tf_qkd', 'satellite_doppler', 'satellite_isl', 'orbital_relay')
TYPE_TECHNOLOGIES = {
    'installation': (), 'measurement_station': (),
    'quantum_repeater': ('entanglement_swapping', 'quantum_memory'),
    'quantum_memory': ('quantum_memory',), 'purification_service': ('purification',),
    'satellite': ('satellite_doppler',), 'ground_station': ('satellite_doppler',),
}
PROTOCOLS = ('BB84', 'heralded_swap', 'memory_readout', 'BBPSSW', 'SNS-TF', 'BBM92', 'CSP1')
TECH_PROTOCOL = {'entanglement_swapping':'heralded_swap','quantum_memory':'memory_readout',
    'purification':'BBPSSW','tf_qkd':'SNS-TF','satellite_doppler':'BBM92',
    'satellite_isl':'BBM92','orbital_relay':'heralded_swap'}
TECH_CAPS = {
    'entanglement_swapping': ('bell_measurement',), 'quantum_memory': ('quantum_memory',),
    'purification': ('bilateral_cnot',), 'tf_qkd': ('coherent_sender', 'phase_reference',
        'interferometer', 'single_photon_detector'),
    'satellite_doppler': ('optical_ground_terminal', 'tracking', 'time_tagger'),
    'satellite_isl': ('optical_space_terminal', 'tracking', 'time_tagger', 'single_photon_detector'),
    'orbital_relay': ('quantum_memory', 'tracking', 'heralded_photon_capture'),
}
CAPABILITIES = tuple(dict.fromkeys(c for v in TECH_CAPS.values() for c in v)) + (
    'satellite_source', 'endpoint_send', 'endpoint_receive', 'classical_forward', 'classical_telemetry')

RF_ONLY_PAYLOAD = 'none_classical_telemetry_only'

# Each field: label, type, default, min/options, max, required. This is shared
# form metadata, not a substitute for the cross-field and protocol validators.
FIELDS = {
 'mission': {
  'mission_id': ('Identificador da missão', 'text', '', None, None, True),
  'norad_catalog_id': ('Identificador NORAD · inteiro positivo', 'int', '', 1, 999999999, True),
  'decoder_profile': ('Perfil do decodificador registrado', 'text', '', None, None, True),
 },
 'optical': {
  'wavelength_nm': ('Comprimento de onda · nm', 'number', 850., 100., 1e5, True),
  'photon_source': ('Fonte de fótons', 'text', 'proposed pair source', None, None, True),
  'pair_source_hz': ('Emissão · pares/s', 'number', 5e6, 0., 1e10, True),
  'detector': ('Detector', 'text', 'proposed single photon detector', None, None, True),
  'detector_efficiency': ('Eficiência do detector · 0–1', 'number', .8, 0., 1., True),
  'insertion_loss_db': ('Perda de inserção · dB', 'number', 2., 0., 1000., True),
  'timing_jitter_ps': ('Jitter · ps', 'number', 50., 0., 1e9, True),
  'sync_tolerance_s': ('Tolerância de sincronização · s', 'number', 1e-6, 0., 1., True),
  'operational_limit_hz': ('Limite de emissão · Hz', 'number', 1e10, 1., 1e10, True),
  'background_cps': ('Fundo por detector · contagens/s', 'number', 500., 0., 1e8, False),
  'dark_count_cps': ('Dark counts por detector · contagens/s', 'number', 20., 0., 1e8, False),
  'detector_dead_time_ns': ('Tempo morto por detector · ns', 'number', 50., 0., 1e9, False),
  'afterpulse_probability': ('Afterpulse por clique · 0–1', 'number', 0., 0., .3, False),
 },
 'entanglement_swapping': {
  'segments': ('Segmentos atendidos (IDs separados por vírgulas)', 'list', [], None, None, False),
  'available_resources': ('Recursos instalados (não pares já gerados)', 'int', 8, 2, 1024, True),
  'bsm_kind': ('Modelo BSM', 'choice', 'linear_optics_unassisted', ('linear_optics_unassisted','abstract_heralded'), None, True),
  'bsm_success': ('Probabilidade BSM · 0–1', 'number', .5, 0., 1., True),
  'operation_error': ('Erro de operação · 0–1', 'number', .005, 0., 1., True),
  'classical_required': ('Heralding exige canal clássico', 'bool', True, None, None, True),
 },
 'quantum_memory': {
  'platform': ('Plataforma física', 'text', 'proposed generic memory', None, None, True),
  'memory_slots': ('Capacidade · modos', 'int', 8, 2, 1024, True),
  'coherence_s': ('Coerência · s', 'number', 1., 1e-6, 1e6, True),
  'memory_cutoff_s': ('Armazenamento máximo · s', 'number', 1., 1e-6, 1e6, True),
  'memory_write_efficiency': ('Eficiência de escrita · 0–1', 'number', .7, 0., 1., True),
  'memory_read_efficiency': ('Eficiência de leitura · 0–1', 'number', .7, 0., 1., True),
  'compatible_wavelength_nm': ('Comprimento de onda compatível · nm', 'number', 850., 100., 1e5, True),
 },
 'tf_qkd': {
  'variant': ('Variante implementada', 'choice', 'SNS-TF', ('SNS-TF',), None, True),
  'device_role': ('Papel TF', 'choice', 'sender', ('sender','central_measurement'), None, True),
  'pulse_rate_hz': ('Taxa de pulsos · Hz', 'number', 1e6, 1., 1e10, True),
  'signal_mu': ('Intensidade sinal · fótons/pulso', 'number', .4, .001, 1., True),
  'decoy_nu': ('Intensidade chamariz · fótons/pulso', 'number', .1, .001, 1., True),
  'phase_sigma_rad': ('Erro de fase · rad', 'number', .05, 0., 10., True),
  'phase_stabilization': ('Estabilização de fase', 'text', 'simulated reference pulses', None, None, True),
  'ec_efficiency': ('Orçamento de reconciliação · fEC', 'number', 1.16, 1., 3., True),
  'postprocessing': ('Pós-processamento', 'choice', 'conditional_asymptotic_budget', ('conditional_asymptotic_budget','finite_sns_v2'), None, True),
  'decoy_strong_mu': ('Chamariz forte · fótons/pulso', 'number', .4, .001, 2., False),
  'send_probability': ('Probabilidade de enviar em Z · 0–1', 'number', .08, .000001, .5, False),
  'tf_z_probability': ('Probabilidade de janela Z · 0–1', 'number', .5, .001, .999, False),
  'tf_vacuum_probability': ('Probabilidade de vácuo em X · 0–1', 'number', .3, .001, .999, False),
  'tf_weak_probability': ('Probabilidade de chamariz fraco em X · 0–1', 'number', .4, .001, .999, False),
  'tf_phase_slice_rad': ('Largura total da seleção de fase · rad', 'number', .392699081699, .001, 1.570796326795, False),
  'statistical_failure': ('Falha de cada intervalo estatístico', 'number', 1e-10, 1e-15, .01, False),
  'privacy_amplification_error': ('Falha condicional de amplificação', 'number', 1e-10, 1e-15, .01, False),
  'entropy_smoothing_error': ('Parâmetro condicional de suavização', 'number', 1e-10, 1e-15, .01, False),
  'tf_max_sifted_bits': ('Limite explícito de bits por bloco', 'int', 50000, 1, 200000, False),
 },
 'purification': {
  'protocol': ('Protocolo implementado', 'choice', 'BBPSSW', ('BBPSSW',), None, True),
  'operations': ('Operações locais', 'text', 'bilateral CNOT and measurement', None, None, True),
  'min_fidelity': ('Fidelidade mínima de entrada · 0–1', 'number', .7, .5, 1., True),
  'acceptance': ('Aceitação', 'choice', 'matching_parity', ('matching_parity',), None, True),
  'pairs_per_attempt': ('Consumo · pares/tentativa', 'int', 2, 2, 2, True),
  'purification_operation_error': ('Erro de operação · 0–1', 'number', .01, 0., 1., True),
  'yield_upper_bound': ('Limite de rendimento · 0–0,5', 'number', .5, 0., .5, True),
 },
 'satellite_doppler': {
  'pointing_jitter_convention': ('Convenção do jitter angular', 'choice', 'gaussian_per_axis', ('gaussian_per_axis','gaussian_radial_rms','legacy_offset_surrogate'), None, False),
  'min_elevation_deg': ('Elevação mínima · graus', 'number', 20., 0., 90., True),
  'divergence_urad': ('Divergência · µrad', 'number', 10., .001, 1e6, True),
  'pointing_jitter_urad': ('Erro de apontamento · µrad', 'number', 2., 0., 1e6, True),
  'receiver_diameter_m': ('Abertura receptora · m', 'number', 1., .001, 100., True),
  'zenith_optical_depth': ('Profundidade óptica atmosférica', 'number', .2, 0., 100., True),
  'optical_efficiency': ('Eficiência óptica · 0–1', 'number', .5, 0., 1., True),
  'doppler_compensation': ('Compensação Doppler', 'bool', True, None, None, True),
  'doppler_residual_hz': ('Doppler residual · Hz', 'number', 5e6, -1e15, 1e15, True),
  'filter_sigma_hz': ('Largura do filtro · Hz', 'number', 1e9, 1., 1e15, True),
  'pointing_enabled': ('Apontamento operacional', 'bool', True, None, None, True),
  'cloud': ('Bloqueio atmosférico', 'bool', False, None, None, True),
  'coincidence_window_s': ('Janela total de coincidência · s', 'number', 1e-9, 1e-12, 1e-3, False),
  'propagation_compensation': ('Correção calibrada do tempo de voo', 'bool', True, None, None, False),
  'range_estimation_error_m': ('Incerteza simulada da distância · m', 'number', 0., 0., 1000., False),
  'extra_loss_db': ('Perda adicional por braço · dB', 'number', 0., 0., 1000., False),
  'tracking_latency_s': ('Atraso de rastreamento · s', 'number', 0., 0., 10., False),
  'doppler_tuning_limit_hz': ('Limite de ajuste Doppler · Hz', 'number', 1e11, 0., 1e15, False),
  'polarization_compensation': ('Compensação de polarização', 'bool', True, None, None, False),
  'polarization_error_rad': ('Rotação óptica inicial · rad', 'number', .03, 0., 3.14159265359, False),
  'polarization_rotation_rate_rad_s': ('Rotação óptica simulada · rad/s', 'number', .001, -10., 10., False),
  'polarization_calibration_error_rad': ('Erro residual de calibração · rad', 'number', .01, 0., 3.14159265359, False),
  'cn2_m_neg_2_3': ('Cn² constante na camada · m^(-2/3)', 'number', 0., 0., 1e-10, False),
  'turbulence_layer_m': ('Espessura da camada turbulenta · m', 'number', 2000., 1., 1e5, False),
  'receiver_coupling': ('Acoplamento receptor', 'choice', 'bucket', ('bucket','single_mode_fiber'), None, False),
  'adaptive_optics': ('Óptica adaptativa no modelo de acoplamento', 'bool', False, None, None, False),
  'ao_residual_fraction': ('Fração residual da variância de fase AO · 0–1', 'number', .5, 0., 1., False),
  'scintillation_sigma': ('Desvio lognormal de cintilação', 'number', 0., 0., 3., False),
 },
 'satellite_isl': {
  'pointing_jitter_convention': ('Convenção do jitter angular', 'choice', 'gaussian_per_axis', ('gaussian_per_axis','gaussian_radial_rms','legacy_offset_surrogate'), None, False),
  'point_ahead_compensation': ('Compensação de point-ahead', 'bool', True, None, None, True),
  'point_ahead_calibration_error_urad': ('Erro residual point-ahead · µrad', 'number', 0., 0., 10000., True),
  'pair_source_hz': ('Fonte ISL dedicada · pares/s', 'number', 20000., 1., 1e7, True),
  'transmitter_diameter_m': ('Abertura transmissora · m', 'number', .5, .01, 10., True),
  'receiver_diameter_m': ('Abertura receptora · m', 'number', .5, .01, 10., True),
  'divergence_urad': ('Divergência · µrad (limitada pela difração)', 'number', 3., .001, 1000., True),
  'pointing_jitter_urad': ('Jitter de apontamento · µrad', 'number', .3, 0., 1000., True),
  'terminal_fov_urad': ('Campo de visão do terminal · µrad', 'number', 20., .001, 10000., True),
  'tracking_latency_s': ('Atraso de rastreamento · s', 'number', .001, 0., 1., True),
  'pointing_enabled': ('Apontamento habilitado', 'bool', True, None, None, True),
  'max_range_km': ('Distância oblíqua máxima configurada · km', 'number', 90000., 1., 1e6, True),
  'earth_limb_margin_km': ('Margem acima do limbo terrestre · km', 'number', 80., 0., 1000., True),
  'local_detection_efficiency': ('Eficiência de detecção local · 0–1', 'number', .8, 0., 1., True),
  'background_cps': ('Fundo por detector espacial · contagens/s', 'number', 1., 0., 1e6, True),
  'dark_count_cps': ('Dark counts por detector espacial · contagens/s', 'number', 5., 0., 1e6, True),
  'detector_dead_time_ns': ('Tempo morto do detector · ns', 'number', 50., 0., 1e9, True),
  'propagation_compensation': ('Compensação calibrada do tempo de voo', 'bool', True, None, None, True),
 },
 'orbital_relay': {
  'resource_model': ('Modelo de estados', 'choice', 'werner_memory_logical_frame', ('werner_memory_logical_frame',), None, True),
  'memory_heralding_kind': ('Heralding de captura não destrutiva', 'choice', 'ancillary_capture_herald', ('ancillary_capture_herald',), None, True),
  'quantum_encoding': ('Codificação armazenada', 'choice', 'polarization_memory_pair', ('polarization_memory_pair',), None, True),
  'optical_duplex': ('Separação dos canais', 'choice', 'dedicated_quantum_and_classical', ('dedicated_quantum_and_classical',), None, True),
  'attempt_rate_hz': ('Taxa de tentativas · Hz', 'number', 200000., 1., 1e10, False),
  'pair_emission_probability': ('Emissão por tentativa · 0–1', 'number', .1, 0., 1., False),
  'divergence_urad': ('Divergência 1/e² · µrad', 'number', 2., .001, 1000., True),
  'pointing_jitter_urad': ('Jitter por eixo · µrad', 'number', .1, 0., 1000., True),
  'pointing_jitter_convention': ('Convenção do jitter', 'choice', 'gaussian_per_axis', ('gaussian_per_axis','gaussian_radial_rms'), None, False),
  'receiver_diameter_m': ('Abertura receptora · m', 'number', 4., .001, 100., True),
  'transmitter_diameter_m': ('Abertura transmissora · m', 'number', 1., .001, 100., True),
  'min_elevation_deg': ('Elevação mínima · graus', 'number', 10., 0., 90., True),
  'optical_efficiency': ('Eficiência óptica · 0–1', 'number', .8, 0., 1., True),
  'zenith_optical_depth': ('Profundidade óptica zenital', 'number', .2, 0., 100., True),
  'max_range_km': ('Distância máxima · km', 'number', 90000., 1., 1e6, True),
  'earth_limb_margin_km': ('Margem do limbo · km', 'number', 80., 0., 1000., True),
  'point_ahead_compensation': ('Compensação point-ahead', 'bool', True, None, None, True),
  'pointing_enabled': ('Rastreamento habilitado', 'bool', True, None, None, True),
 },
 'orbit': {
  'model': ('Propagador disponível', 'choice', 'circular_reference', ('circular_reference','kepler_elements','sgp4_tle','state_vectors','geostationary_reference'), None, True),
  'orbital_class': ('Classe orbital', 'choice', 'LEO', ('LEO','MEO','GEO','HEO'), None, True),
  'epoch_utc': ('Época orbital · ISO-8601 UTC', 'text', '2026-10-06T00:00:00+00:00', None, None, True),
  'epoch_network_s': ('Época no relógio da rede · s', 'number', 0., 0., 1e12, True),
  'source': ('Origem dos elementos/efemérides', 'text', 'hypothetical circular reference', None, None, True),
  'altitude_km': ('Altitude de referência/perigeu · km', 'number', 500., 100., 100000., True),
  'inclination_deg': ('Inclinação · graus', 'number', 60., 0., 180., True),
  'reference_latitude': ('Latitude de referência · graus', 'number', 0., -90., 90., True),
  'reference_longitude': ('Longitude de referência · graus', 'number', 0., -180., 180., True),
  'orbit_phase_rad': ('Fase na época · rad', 'number', 0., -6.28318530718, 6.28318530718, True),
  'eccentricity': ('Excentricidade (elementos Kepler)', 'number', 0., 0., .95, True),
  'raan_deg': ('RAAN · graus', 'number', 0., 0., 360., True),
  'argument_perigee_deg': ('Argumento do perigeu · graus', 'number', 0., 0., 360., True),
  'mean_anomaly_deg': ('Anomalia média na época · graus', 'number', 0., 0., 360., True),
  'compatible_stations': ('Estações compatíveis (IDs separados por vírgulas)', 'list', [], None, None, True),
  'communication_windows': ('Janelas no relógio da rede · JSON [[início,fim]]; [] = só visibilidade', 'json', [], None, None, True),
  'optical_payload': ('Carga útil óptica', 'text', 'hypothetical entangled photon source', None, None, True),
  'tle_line1': ('TLE linha 1 · 69 caracteres', 'text', '', None, None, False),
  'tle_line2': ('TLE linha 2 · 69 caracteres', 'text', '', None, None, False),
  'ephemeris_max_age_s': ('Validade máxima do TLE · s', 'number', 259200., 1., 1e7, False),
  'state_vectors': ('Efemérides ECEF · JSON [{at_network_s,ecef_km,velocity_ecef_km_s}]', 'json', [], None, None, False),
 },
}

NAMES = {
 'PUC-RIO': ('BR-PUCRIO','Pontifícia Univ. Católica do Rio de Janeiro','Experimental Terrestre (Nó de Borda)','endpoint'),
 'UFRJ': ('BR-UFRJ','Universidade Federal do Rio de Janeiro (Fundão)','Experimental Terrestre (Nó de Borda)','endpoint'),
 'IME-RIO': ('BR-IME','Instituto Militar de Engenharia (Urca)','Experimental Terrestre (Centro de Testes)','endpoint'),
 'CBPF': ('BR-CBPF','Centro Brasileiro de Pesquisas Físicas','Experimental Terrestre (Nó Hub Central)','core'),
 'IBM-WATSON': ('US-IBM-TW','Thomas J. Watson Research Center — IBM','Cenário Simulado Corporativo (Computação/Rede)','endpoint'),
 'IQMP': ('US-IQMP','Illinois Quantum and Microelectronics Park','Cenário Simulado de Hub Tecnológico','core'),
 'GOOGLE-QAI': ('US-GOOG-QA','Google Quantum AI Lab','Cenário Simulado Corporativo (Processador Sycamore)','endpoint'),
 'BSC': ('ES-BSC-CNS','Barcelona Supercomputing Center','Cenário Simulado de Centro HPC Híbrido','core'),
 'MS-QUANTUM': ('DK-MSFT-QL','Microsoft Quantum Lab Lyngby','Cenário Simulado Corporativo (Topológico)','endpoint'),
 'IQC': ('CA-UOW-IQC','Institute for Quantum Computing — Waterloo','Cenário Simulado Acadêmico Global','endpoint'),
 'XANADU': ('CA-XANADU','Xanadu Quantum Technologies (Toronto)','Cenário Simulado Corporativo (Fotônica)','endpoint'),
 'CN-BEIJING': ('CN-QCN-BJ','CN-QCN — Backbone Hub Pequim','Cenário Simulado de Rede Distribuída (Espinha Dorsal)','core'),
 'CN-SHANGHAI': ('CN-QCN-SH','CN-QCN — Backbone Hub Xangai','Cenário Simulado de Rede Distribuída (Espinha Dorsal)','core'),
 'CN-HEFEI': ('CN-QCN-HF','CN-QCN — Hub Hefei (Laboratório Key)','Cenário Simulado de Rede Distribuída','core'),
}


def defaults(group):
    return {key: spec[2] for key, spec in FIELDS[group].items()}


def validate_mission(mission):
    """Inventory metadata; installed decoders are admitted by the receiver.

    Registration neither invents a quantum payload nor declares a decoder or
    real RF terminal available. The receiver checks its extensible registry.
    """
    validate_group('mission',mission)
    for key in ('mission_id','decoder_profile'):
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}',mission[key]):
            raise ValueError(f'mission.{key}: identificador limitado obrigatório')


def local_websocket_address(address):
    from urllib.parse import urlsplit
    try:
        parsed=urlsplit(address)
        return (parsed.scheme=='ws' and parsed.hostname in ('127.0.0.1','localhost','::1')
                and parsed.port is not None and 1<=parsed.port<=65535
                and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment)
    except ValueError:return False


def required_capabilities(technology, physical_type, configuration):
    if technology == 'tf_qkd':
        return (('interferometer', 'single_photon_detector')
                if configuration.get('device_role') == 'central_measurement'
                else ('coherent_sender', 'phase_reference'))
    if technology == 'satellite_doppler' and physical_type == 'satellite':
        return ('satellite_source', 'tracking')
    if technology == 'orbital_relay':
        return ('quantum_memory','tracking','heralded_photon_capture','optical_space_terminal' if physical_type=='satellite' else 'optical_ground_terminal')
    return TECH_CAPS[technology]


def active_orbit_fields(model):
    specific = {
        'tle_line1': ('sgp4_tle',), 'tle_line2': ('sgp4_tle',),
        'ephemeris_max_age_s': ('sgp4_tle',), 'state_vectors': ('state_vectors',),
        'altitude_km': ('circular_reference', 'kepler_elements'),
        'inclination_deg': ('circular_reference', 'kepler_elements'),
        'reference_latitude': ('circular_reference',), 'reference_longitude': ('circular_reference','geostationary_reference'),
        'orbit_phase_rad': ('circular_reference','geostationary_reference'),
        'eccentricity': ('circular_reference', 'kepler_elements'),
        'raan_deg': ('kepler_elements',), 'argument_perigee_deg': ('kepler_elements',),
        'mean_anomaly_deg': ('kepler_elements',),
    }
    return tuple(key for key in FIELDS['orbit'] if key not in specific or model in specific[key])


def validate_group(group, values):
    import math
    if not isinstance(values, dict):
        raise ValueError(f'{group}: objeto obrigatório')
    unknown = set(values) - set(FIELDS[group])
    if unknown:
        raise ValueError(f'{group}: campos desconhecidos {sorted(unknown)}')
    for key, (label, kind, default, low, high, required) in FIELDS[group].items():
        if key not in values:
            if required: raise ValueError(f'{label}: campo obrigatório')
            continue
        value = values[key]
        if kind in ('number','int'):
            if type(value) not in ((int,) if kind == 'int' else (int,float)) or not math.isfinite(value) or not low <= value <= high:
                raise ValueError(f'{label}: intervalo [{low}, {high}]')
        elif kind == 'bool' and type(value) is not bool:
            raise ValueError(f'{label}: booleano obrigatório')
        elif kind == 'choice' and value not in low:
            raise ValueError(f'{label}: escolha não implementada')
        elif kind == 'text' and (not isinstance(value,str) or (required and not value.strip()) or len(value)>2048):
            raise ValueError(f'{label}: texto obrigatório, até 2048 caracteres')
        elif kind == 'list' and (not isinstance(value,list) or len(value)>256 or any(not isinstance(x,str) or not x for x in value)):
            raise ValueError(f'{label}: lista de identificadores obrigatória')


@dataclass(frozen=True)
class Communicator:
    device_id: str
    node_id: str
    name: str
    physical_type: str
    network_role: str
    institution: str = ''
    manufacturer_model: str = 'proposed simulated device'
    category: str = 'Cenário simulado'
    description: str = ''
    operational_state: str = 'enabled'
    technologies: list = field(default_factory=list)
    protocols: list = field(default_factory=list)
    capabilities: list = field(default_factory=list)
    location: dict = field(default_factory=dict)
    integration: dict = field(default_factory=lambda: {'mode':'simulated','adapter':'local_simulator',
        'interface':'local_queue','service_address':'','credential_ref':'','data_origin':'simulated'})
    optical: dict = field(default_factory=lambda: defaults('optical'))
    configuration: dict = field(default_factory=dict)
    orbit: dict = field(default_factory=dict)
    mission: dict = field(default_factory=dict)
    revision: int = 0
    schema: str = SCHEMA

    def __post_init__(self):
        for key in ('device_id','node_id'):
            if not isinstance(getattr(self,key),str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,95}',getattr(self,key)):
                raise ValueError(f'{key}: identificador inválido')
        if not isinstance(self.name,str) or not self.name.strip() or len(self.name)>240: raise ValueError('nome obrigatório, até 240 caracteres')
        for name in ('institution','manufacturer_model','category','description'):
            if not isinstance(getattr(self,name),str) or len(getattr(self,name))>2048:raise ValueError(f'{name}: texto limitado obrigatório')
        if self.schema!=SCHEMA or self.physical_type not in TYPES or self.network_role not in ROLES:
            raise ValueError('tipo físico ou função inválida')
        if type(self.revision) is not int or self.revision<0: raise ValueError('revisão inválida')
        if self.operational_state not in ('enabled','maintenance','disabled'): raise ValueError('estado inválido')
        for group in ('location','configuration','orbit'):
            if not isinstance(getattr(self,group),dict):raise ValueError(f'{group}: objeto obrigatório')
        if any(not isinstance(v,list) or any(not isinstance(x,str) for x in v) for v in (self.technologies,self.protocols,self.capabilities)):
            raise ValueError('tecnologias, protocolos e capacidades devem ser listas de identificadores')
        if not set(self.technologies)<=set(TECHNOLOGIES) or not set(self.protocols)<=set(PROTOCOLS) or not set(self.capabilities)<=set(CAPABILITIES):
            raise ValueError('tecnologia, protocolo ou capacidade não implementada')
        if len(set(self.technologies))!=len(self.technologies): raise ValueError('tecnologia duplicada')
        required=set(TYPE_TECHNOLOGIES[self.physical_type]) if self.physical_type!='satellite' else set()
        if self.physical_type=='ground_station' and 'orbital_relay' in self.technologies:required.discard('satellite_doppler')
        if not required<=set(self.technologies):raise ValueError('tipo físico exige tecnologias: '+', '.join(sorted(required)))
        if not isinstance(self.mission,dict):raise ValueError('mission: objeto obrigatório')
        if self.mission:
            if self.physical_type!='satellite':raise ValueError('mission: cadastro de missão exclusivo de satélite')
            validate_mission(self.mission)
        rf_only=self.physical_type=='satellite' and not self.technologies
        if rf_only:
            if not self.mission or 'classical_telemetry' not in self.capabilities or 'CSP1' not in self.protocols:
                raise ValueError('satélite sem enlace quântico exige classical_telemetry, CSP1 e mission válido')
            if self.optical or self.configuration:
                raise ValueError('satélite apenas RF exige optical={} e configuration={}; nenhuma carga útil quântica declarada')
            if set(self.capabilities)&{'satellite_source','single_photon_detector','optical_space_terminal','optical_ground_terminal','time_tagger','bell_measurement','quantum_memory','coherent_sender','interferometer','bilateral_cnot'}:
                raise ValueError('satélite apenas RF não pode declarar capacidades de carga útil quântica')
        elif self.physical_type=='satellite' and not set(self.technologies)&{'satellite_doppler','satellite_isl','orbital_relay'}:
            raise ValueError('satélite exige um driver de enlace satélite–solo ou satélite–satélite')
        if not rf_only:
            validate_group('optical',self.optical)
            if self.optical['pair_source_hz']>self.optical['operational_limit_hz']: raise ValueError('emissão excede o limite operacional')
        if 'classical_telemetry' in self.capabilities and 'CSP1' not in self.protocols:
            raise ValueError('telemetria clássica exige protocolo CSP1')
        for tech in self.technologies:
            validate_group(tech,self.configuration.get(tech,{}))
            if TECH_PROTOCOL[tech] not in self.protocols: raise ValueError(f'{tech}: protocolo associado obrigatório')
            required_caps=required_capabilities(tech,self.physical_type,self.configuration[tech])
            if not set(required_caps)<=set(self.capabilities): raise ValueError(f'{tech}: capacidades ausentes')
        if 'satellite_isl' in self.technologies:
            if self.physical_type!='satellite':raise ValueError('enlace ISL exige terminal físico do tipo Satélite')
            if self.configuration['satellite_isl']['pair_source_hz']>self.optical['operational_limit_hz']:
                raise ValueError('emissão ISL excede o limite operacional')
        if 'quantum_memory' in self.technologies:
            memory=self.configuration['quantum_memory']
            if abs(memory['compatible_wavelength_nm']-self.optical['wavelength_nm'])>.01:
                raise ValueError('memória incompatível com o comprimento de onda óptico')
        if 'entanglement_swapping' in self.technologies:
            p=self.configuration['entanglement_swapping']
            if not p['classical_required']: raise ValueError('BSM exige comunicação clássica')
            if p['bsm_kind']=='linear_optics_unassisted' and p['bsm_success']>.5:
                raise ValueError('BSM óptica linear sem auxílio: probabilidade máxima 0,5')
        if 'tf_qkd' in self.technologies:
            p=self.configuration['tf_qkd']
            if p['decoy_nu']>=p['signal_mu']: raise ValueError('intensidade chamariz deve ser menor que sinal')
            if p['postprocessing']=='finite_sns_v2':
                if p.get('decoy_strong_mu',.4)<=p['decoy_nu']:raise ValueError('SNS finito exige chamariz forte maior que fraco')
                if p.get('tf_vacuum_probability',.3)+p.get('tf_weak_probability',.4)>=1.:raise ValueError('probabilidades X devem deixar espaço para chamariz forte')
        if 'orbital_relay' in self.technologies and not {'quantum_memory','entanglement_swapping'}<=set(self.technologies):
            raise ValueError('relay orbital exige memória e troca de entrelaçamento cadastradas')
        integration=self.integration
        keys={'mode','adapter','interface','service_address','credential_ref','data_origin'}
        if not isinstance(integration,dict) or set(integration)!=keys or any(not isinstance(v,str) or len(v)>2048 for v in integration.values()):
            raise ValueError('integração exige campos definidos; segredos/atributos extras não são aceitos')
        if integration.get('mode') not in ('simulated','measurement_file','measurement_stream','physical'):
            raise ValueError('modo de integração obrigatório')
        if integration.get('data_origin')!= {'simulated':'simulated','measurement_file':'measured','measurement_stream':'physical','physical':'physical'}[integration['mode']]:
            raise ValueError('origem incompatível com modo de integração')
        if integration.get('adapter') not in ('local_simulator','measurement_file','ogs_read_only_v2','mission_gateway','external_unvalidated'):
            raise ValueError('adaptador desconhecido')
        adapters={'simulated':('local_simulator',),'measurement_file':('measurement_file',),
                  'measurement_stream':('ogs_read_only_v2','mission_gateway'),'physical':('external_unvalidated',)}
        if integration['adapter'] not in adapters[integration['mode']]:
            raise ValueError('adaptador incompatível com modo')
        if integration['mode']=='measurement_file' and not integration['service_address'].strip():raise ValueError('arquivo de medições obrigatório')
        if integration['mode']=='measurement_stream':
            if integration['adapter']=='mission_gateway':
                if (self.physical_type!='satellite' or not self.mission or 'classical_telemetry' not in self.capabilities
                        or 'CSP1' not in self.protocols or integration['interface']!='websocket_mission_v1'):
                    raise ValueError('missão RF: satélite, mission, classical_telemetry, CSP1 e websocket_mission_v1 obrigatórios')
            elif 'satellite_doppler' not in self.technologies or integration['interface']!='websocket_ogs_v2':
                raise ValueError('OGS v2: tecnologia satelital e interface websocket_ogs_v2 obrigatórias')
            if not local_websocket_address(integration['service_address']) or not integration['credential_ref'].startswith('vault:'):
                raise ValueError('recepção: endereço WebSocket local e referência vault: à sessão autorizada obrigatórios')
        if not integration['interface'].strip():raise ValueError('interface de integração obrigatória')
        reference=integration.get('credential_ref','')
        if reference and (not isinstance(reference,str) or not re.fullmatch(r'(?:vault|env|keyring)[:/][A-Za-z0-9_./:-]{1,192}',reference)):
            raise ValueError('credencial: use referência vault:, env: ou keyring:; não informe segredo')
        if self.physical_type=='satellite':
            validate_group('orbit',self.orbit)
            from ..quantum_protocols.orbits import validate_orbit
            validate_orbit(self.orbit)
            if self.mission and self.orbit['model']=='sgp4_tle':
                from ..quantum_protocols.orbits import _tle
                if _tle(self.orbit.get('tle_line1',''),self.orbit.get('tle_line2','')).satnum!=self.mission['norad_catalog_id']:
                    raise ValueError('mission: catálogo NORAD incompatível com o TLE cadastrado')
            if rf_only and self.orbit['optical_payload']!=RF_ONLY_PAYLOAD:
                raise ValueError('satélite apenas RF: optical_payload deve declarar none_classical_telemetry_only')
            if 'satellite_doppler' in self.technologies and not self.orbit['compatible_stations']:
                raise ValueError('satélite: ao menos uma estação terrestre compatível obrigatória para downlinks')
            if self.location: raise ValueError('satélite usa órbita; não fixe coordenadas terrestres')
            if 'satellite_doppler' in self.technologies and 'satellite_source' not in self.capabilities:
                raise ValueError('satélite: capacidade de fonte óptica obrigatória')
        else:
            if self.orbit: raise ValueError('referências orbitais são exclusivas de dispositivos do tipo Satélite')
            if self.location:
                from ..quantum_protocols.contracts import number
                number(self.location.get('latitude'),'latitude',-90.,90.)
                number(self.location.get('longitude'),'longitude',-180.,180.)
                number(self.location.get('altitude_m',0.),'altitude_m',-500.,10000.)
        # Copy/validate finite JSON even when a caller constructed the object.
        _json_data(self.to_dict())

    def to_dict(self):
        from dataclasses import asdict
        return asdict(self)

    @classmethod
    def from_dict(cls,value):
        return cls(**_json_data(value))

    def parameters(self, technologies=None):
        result={key:value for key,value in self.optical.items() if FIELDS['optical'][key][1] in ('number','int')}
        selected=set(self.technologies if technologies is None else technologies)
        for tech in self.technologies:
            if tech not in selected:continue
            result.update({k:v for k,v in self.configuration[tech].items() if FIELDS[tech][k][1] in ('number','int','bool') or k in ('bsm_kind','receiver_coupling','pointing_jitter_convention','resource_model','memory_heralding_kind','quantum_encoding','optical_duplex')})
        if 'tf_qkd' in self.technologies and 'tf_qkd' in selected:result['decoy_mu']=self.configuration['tf_qkd']['decoy_nu']
        if 'tf_qkd' in self.technologies and 'tf_qkd' in selected:result['tf_estimation_mode']='finite_sns_v2' if self.configuration['tf_qkd']['postprocessing']=='finite_sns_v2' else 'reduced_asymptotic'
        if self.physical_type=='satellite': result['orbit_record']=dict(self.orbit)
        return result


def seed_record(node,topology,mode):
    telemetry=node.telemetry
    inventory=telemetry.get('inventory',{})
    site=telemetry.get('demonstration_site',{})
    loc=telemetry.get('location',{})
    kind=inventory.get('kind','installation')
    physical='satellite' if kind=='satellite' else 'ground_station' if kind=='ground_station' else 'measurement_station' if 'measurement' in kind else 'quantum_repeater' if 'repeater' in kind or 'demonstration_repeater' in telemetry else 'installation'
    if node.node_id in NAMES or node.node_id in ('CIMATEC','USP','IME-USP','UNICAMP','CIQUANTA'):physical='installation'
    if physical=='installation' and not site and node.role==NodeRole.REPEATER and kind=='installation':physical='quantum_repeater'
    role='intermediate' if physical=='satellite' else 'measurement' if physical=='measurement_station' else 'repeater' if physical=='quantum_repeater' else 'endpoint'
    if inventory.get('kind')=='core':role='core'
    ident,name,category,role=NAMES.get(node.node_id,(node.node_id,site.get('name',loc.get('place',node.node_id)), 'Cenário simulado · equipamento proposto',role))
    configs=[raw for n in topology.nodes for raw in n.telemetry.get('technology_sessions',()) if node.node_id in raw['path']]
    techs=list(dict.fromkeys(t for raw in configs for t in raw['technologies']))
    if physical=='quantum_repeater':techs=list(dict.fromkeys(techs+['entanglement_swapping','quantum_memory']))
    if physical=='satellite' and not set(techs)&{'satellite_doppler','satellite_isl','orbital_relay'}:
        techs=list(dict.fromkeys(techs+['satellite_doppler']))
    params={k:v for raw in configs for k,v in raw['parameters'].items()}
    config={t:{**defaults(t),**{k:v for raw in configs if t in raw['technologies']
        for k,v in raw['parameters'].items() if k in FIELDS[t]}} for t in techs}
    if 'tf_qkd' in config:config['tf_qkd']['decoy_nu']=params.get('decoy_mu',config['tf_qkd']['decoy_nu'])
    if 'tf_qkd' in config and params.get('tf_estimation_mode')=='finite_sns_v2':config['tf_qkd']['postprocessing']='finite_sns_v2'
    if physical=='measurement_station' and 'tf_qkd' in config:config['tf_qkd']['device_role']='central_measurement'
    optical_configs=[raw for raw in configs if 'satellite_isl' not in raw['technologies']] or configs
    optical={**defaults('optical'),**{k:v for raw in optical_configs for k,v in raw['parameters'].items() if k in FIELDS['optical']}}
    if 'orbital_relay' in techs and 'pair_source_hz' not in params:optical['pair_source_hz']=params.get('attempt_rate_hz',200000.)*params.get('pair_emission_probability',.1)
    caps=list(dict.fromkeys(telemetry.get('technology_capabilities',[]) + [c for t in techs for c in TECH_CAPS[t]]))
    if 'orbital_relay' in techs:caps=list(dict.fromkeys(caps+['optical_space_terminal' if physical=='satellite' else 'optical_ground_terminal']))
    orbit={}
    if physical=='satellite':
        downlinks=[raw for raw in configs if 'satellite_doppler' in raw['technologies']]
        sat=downlinks[0] if downlinks else None
        endpoints=list(dict.fromkeys(n for raw in downlinks for n in raw['endpoints']))
        p=sat['parameters'] if sat else {}
        explicit=telemetry.get('orbit_record') or p.get('orbit_record') or next((
            raw['parameters'].get('satellite_orbits',{}).get(node.node_id) for raw in configs
            if raw['parameters'].get('satellite_orbits',{}).get(node.node_id)),{})
        if not endpoints and not explicit:
            raise ValueError('inventário satelital sem órbita explícita ou estações associadas')
        reference=topology.node(endpoints[0]).telemetry['location'] if endpoints else {'latitude':0.,'longitude':0.}
        orbit={**defaults('orbit'),**{k:v for k,v in p.items() if k in FIELDS['orbit']},
               'reference_latitude':p.get('reference_latitude',reference['latitude']),
               'reference_longitude':p.get('reference_longitude',reference['longitude']),
               'compatible_stations':list(endpoints),**explicit}
        if downlinks or any(raw['endpoints'][0]==node.node_id and 'satellite_isl' in raw['technologies'] for raw in configs):
            caps=list(dict.fromkeys(caps+['satellite_source']))
    if telemetry.get('operational_capabilities',{}).get('originate_receive'):
        caps=list(dict.fromkeys(caps+['endpoint_send','endpoint_receive']))
    if telemetry.get('operational_capabilities',{}).get('classical_forwarding'):
        caps=list(dict.fromkeys(caps+['classical_forward']))
    return Communicator(ident,node.node_id,name,physical,role,institution=site.get('name',''),category=category,
        description='Referência documentada; interfaces ópticas propostas, sem conexão física comprovada.',
        technologies=techs,protocols=list(dict.fromkeys((['BB84'] if any(l.technology is None and node.node_id in (l.left_node_id,l.right_node_id) for l in topology.links) else [])+[TECH_PROTOCOL[t] for t in techs]+(['CSP1'] if 'classical_telemetry' in caps else []))),capabilities=caps,
        location={} if physical=='satellite' else {**({'latitude':loc['latitude'],'longitude':loc['longitude'],'altitude_m':loc.get('altitude_m',0.)} if loc else {})},
        integration={'mode':mode,'adapter':'local_simulator' if mode=='simulated' else 'external_unvalidated',
          'interface':'local_queue','service_address':'','credential_ref':'','data_origin':'simulated' if mode=='simulated' else 'physical'},
        optical=optical,configuration=config,orbit=orbit)


class CommunicatorStore:
    def __init__(self,store,topology_id):
        self.store,self.topology_id=store,topology_id
        with closing(store._connect()) as conn,conn:
            conn.execute('CREATE TABLE IF NOT EXISTS communicators(topology_id TEXT,device_id TEXT,node_id TEXT,revision INTEGER,record_json TEXT, PRIMARY KEY(topology_id,device_id),UNIQUE(topology_id,node_id))')

    def seed(self,topology,mode):
        missing=[n for n in topology.nodes if 'communicator' not in n.telemetry]
        if not missing: return topology
        records=[seed_record(n,topology,mode) for n in missing]
        return self._write(topology,records,'communicators_seeded')

    def save(self,payload):
        current=self.store.get(self.topology_id)
        initialize=current is None
        if initialize:
            from .topology import default_simulated_topology
            current=default_simulated_topology(self.topology_id)
        if type(payload.get('expected_version')) is not int or payload['expected_version']!=current.topology_version:
            raise ValueError('conflito de versão da topologia; recarregue o cadastro')
        record=Communicator.from_dict(payload['record'])
        old=next((n.telemetry['communicator'] for n in current.nodes if n.telemetry.get('communicator',{}).get('device_id')==record.device_id),None)
        by_node=next((n for n in current.nodes if n.node_id==record.node_id),None)
        if any(n.node_id!=record.node_id and (n.node_id==record.device_id or n.telemetry.get('communicator',{}).get('device_id')==record.node_id) for n in current.nodes):
            raise ValueError('identificador público/interno colide com outro componente')
        if old:
            if old['node_id']!=record.node_id or old['revision']!=record.revision:
                raise ValueError('identidade/revisão conflitante; recarregue')
            record=replace(record,revision=record.revision+1)
        elif by_node is not None and 'communicator' in by_node.telemetry:
            raise ValueError('nó já vinculado a outro comunicador; edite o cadastro existente')
        if len(current.nodes)>=256 and by_node is None: raise ValueError('limite de 256 componentes')
        # Referenced stations must be terrestrial and present before committing.
        if record.physical_type=='satellite':
            aliases={n.telemetry.get('communicator',{}).get('device_id',n.node_id):n.node_id for n in current.nodes}
            record=replace(record,orbit={**record.orbit,'compatible_stations':[aliases.get(s,s) for s in record.orbit['compatible_stations']]})
            for station in record.orbit['compatible_stations']:
                n=next((n for n in current.nodes if n.node_id==station),None)
                if n is None or n.telemetry.get('communicator',{}).get('physical_type')=='satellite':
                    raise ValueError(f'estação terrestre inválida: {station}')
        initial=[seed_record(n,current,'simulated' if record.integration['mode']=='simulated' else 'physical')
                 for n in current.nodes if n.node_id!=record.node_id and 'communicator' not in n.telemetry]
        return self._write(current,[*initial,record],'communicator_saved',initialize=initialize)

    def connection(self,payload,registry,validate_parameters):
        from .channels import ChannelPair
        from .geodesy import geodesic_km
        from .topology import SimulatedLink
        from ..quantum_protocols.contracts import ProtocolConfig
        current=self.store.get(self.topology_id)
        if current is None or type(payload.get('expected_version')) is not int or payload.get('expected_version')!=current.topology_version:
            raise ValueError('conflito de versão; recarregue a conexão')
        config=ProtocolConfig.from_dict(payload['config'])
        aliases={n.telemetry.get('communicator',{}).get('device_id',n.node_id):n.node_id for n in current.nodes}
        resolve=lambda name:aliases.get(name,name)
        config=replace(config,path=tuple(resolve(n) for n in config.path),endpoints=tuple(resolve(n) for n in config.endpoints))
        drivers=set(config.technologies)&{'entanglement_swapping','tf_qkd','satellite_doppler','satellite_isl','orbital_relay'}
        if 'orbital_relay' in drivers:drivers.discard('entanglement_swapping')
        if not drivers:
            raise ValueError('memória/purificação complementam um protocolo de distribuição; selecione um driver')
        if len(drivers)!=1:
            raise ValueError('uma sessão exige exatamente um driver de distribuição; use sessões separadas para drivers distintos')
        if len(config.link_ids)!=len(config.path)-1: raise ValueError('um par de canais obrigatório por segmento')
        for node_id in config.path:
            node=current.node(node_id)
            raw=node.telemetry.get('communicator')
            if not raw: raise ValueError(f'{node_id}: cadastro de comunicador obrigatório')
            record=Communicator.from_dict(raw)
            if not set(config.technologies)<=set(record.technologies):
                raise ValueError(f'{node_id}: tecnologias não cadastradas')
        records=[Communicator.from_dict(current.node(n).telemetry['communicator']) for n in config.path]
        if any(not r.location for r in records if r.physical_type!='satellite'):
            raise ValueError('coordenadas terrestres obrigatórias antes de ativar enlaces')
        modes={r.integration['mode'] for r in records}
        if len(modes)!=1 or 'physical' in modes: raise ValueError('origens incompatíveis ou adaptador físico ainda não validado')
        if any(r.operational_state!='enabled' for r in records): raise ValueError('componente desabilitado/em manutenção')
        if len({r.optical['wavelength_nm'] for r in records})!=1: raise ValueError('comprimentos de onda incompatíveis entre dispositivos')
        params=dict(config.parameters)
        params['communicator_configuration_version']=current.topology_version+1
        for record in records: params.update(record.parameters(config.technologies))
        if modes=={'measurement_file'}:
            paths={r.integration.get('service_address') for r in records}
            if len(paths)!=1 or not next(iter(paths)): raise ValueError('arquivo de medições compartilhado obrigatório')
            from ..quantum_protocols.sources import MeasurementFileSource
            path=next(iter(paths));MeasurementFileSource(path)
            params['acquisition_source']={'mode':'measurement_file','path':path}
        elif modes=={'measurement_stream'}:
            if len({r.integration['service_address'] for r in records})!=1:
                raise ValueError('todos os dispositivos exigem a mesma entrada OGS da sessão')
            params['acquisition_source']={'mode':'measurement_stream','source_id':'ogs_gateway'}
        else: params['acquisition_source']={'mode':'simulated'}
        if 'satellite_doppler' in config.technologies:
            satellites=[r for r in records if r.physical_type=='satellite']
            if len(satellites)!=1 or records[1]!=satellites[0] or len(records)!=3:
                raise ValueError('satélite exige percurso estação–satélite–estação')
            if not set(config.endpoints)<=set(satellites[0].orbit['compatible_stations']):
                raise ValueError('estações não cadastradas como compatíveis com o satélite')
            params.update(satellites[0].orbit);params['orbit_record']=satellites[0].orbit
        if 'satellite_isl' in config.technologies:
            if len(records)!=2 or any(r.physical_type!='satellite' for r in records):
                raise ValueError('ISL exige dois satélites cadastrados e um par de canais explícito')
            params['satellite_orbits']={r.node_id:dict(r.orbit) for r in records}
            params['device_parameters']={r.node_id:r.parameters(config.technologies) for r in records}
        if 'orbital_relay' in config.technologies:
            params['satellite_orbits']={r.node_id:dict(r.orbit) for r in records if r.physical_type=='satellite'}
            params['device_parameters']={r.node_id:r.parameters(config.technologies) for r in records}
        config=replace(config,parameters=params)
        supplied=payload.get('links',[])
        if not isinstance(supplied,list) or len(supplied)!=len(config.link_ids): raise ValueError('configuração dos dois canais por segmento obrigatória')
        if len({row['link_id'] for row in supplied})!=len(supplied): raise ValueError('segmento duplicado')
        known={link.link_id:link for link in current.links}
        links=[]
        for index,ident in enumerate(config.link_ids):
            row=next((row for row in supplied if row.get('link_id')==ident),None)
            if row is None:raise ValueError(f'canais ausentes: {ident}')
            a,b=config.path[index:index+2]
            if 'tf_qkd' in config.technologies and index==1: a,b=b,a
            if 'satellite_doppler' in config.technologies and index==0: a,b=b,a
            channels=_json_data(row['channels']);channels['quantum']['receiver_id']=resolve(channels['quantum']['receiver_id'])
            pair=ChannelPair.from_dict(channels);pair.validate_link(ident,b)
            if not pair.classical.enabled or not pair.quantum.enabled: raise ValueError('ambos os canais precisam estar habilitados para ativar')
            old=known.get(ident)
            if old and (old.technology or {}).get('session_id')!=config.session_id:
                raise ValueError('segmento pertence a outra configuração; não será sobrescrito')
            first,second=current.node(a).telemetry.get('location'),current.node(b).telemetry.get('location')
            distance=geodesic_km(first['latitude'],first['longitude'],second['latitude'],second['longitude']) if first and second else 0.
            links.append(SimulatedLink(ident,a,b,distance,.9,active=config.enabled,channels=pair.to_dict(),
                technology={'session_id':config.session_id,'technologies':list(config.technologies),'infrastructure':'operator_simulation_configuration','physical_connection_to_orion':False}))
        prospective=replace(current,links=tuple(x for x in current.links if x.link_id not in config.link_ids and
            (x.technology or {}).get('session_id')!=config.session_id)+tuple(links))
        if len(prospective.links)>256:raise ValueError('limite de 256 segmentos')
        registry.validate(config,prospective);validate_parameters(config)
        # Config belongs to the path origin. Keep other sessions and stable node IDs.
        nodes=[]
        for node in prospective.nodes:
            sessions=[s for s in node.telemetry.get('technology_sessions',[]) if s['session_id']!=config.session_id]
            if node.node_id==config.path[0]: sessions.append(config.to_dict())
            nodes.append(replace(node,telemetry={**node.telemetry,'technology_sessions':sessions}))
        return self._write(current,[],'communicator_connection_saved',links=prospective.links,nodes=nodes)

    def _write(self,current,records,kind,links=None,nodes=None,initialize=False):
        metadata_only=kind=='communicators_seeded'
        by_id={r.node_id:r for r in records}
        source_nodes=current.nodes if nodes is None else nodes
        nodes=[]
        now=datetime.now(timezone.utc).isoformat()
        for node in source_nodes:
            r=by_id.pop(node.node_id,None)
            if r is None: nodes.append(node);continue
            data={**node.telemetry,'communicator':r.to_dict(),'technology_capabilities':list(r.capabilities)}
            # Dynamic satellites have no stationary terrestrial location.
            if r.physical_type=='satellite': data.pop('location',None)
            elif r.location: data['location']={**data.get('location',{}),**r.location,'place':r.name}
            else: data.pop('location',None)
            nodes.append(replace(node,telemetry=data,state_version=node.state_version+(0 if metadata_only else 1),updated_at_utc=node.updated_at_utc if metadata_only else now))
        for r in by_id.values():
            data={'communicator':r.to_dict(),'technology_capabilities':list(r.capabilities),
                  'inventory':{'kind':r.physical_type,'origin':'communicators-v1','scope':'metadata_only_no_connection'}}
            if r.location:data['location']={**r.location,'place':r.name}
            nodes.append(SimulatedNodeState(r.node_id,NodeRole.ENDPOINT if r.network_role in ('endpoint','core') else NodeRole.REPEATER,0,'IDLE',telemetry=data))
        updated=replace(current,nodes=tuple(nodes),links=current.links if links is None else tuple(links),topology_version=current.topology_version+(0 if metadata_only else 1),updated_at_utc=current.updated_at_utc if metadata_only else now)
        event={'records':[r.to_dict() for r in records],'before':current.to_dict(),'physical_action_performed':False}
        with self.store._lock,closing(self.store._connect()) as conn,conn:
            conn.execute('BEGIN IMMEDIATE')
            if metadata_only:
                from .topology import SimulatedTopology
                row=conn.execute('SELECT state_json FROM simulated_topologies WHERE topology_id=?',(self.topology_id,)).fetchone()
                if row is None or SimulatedTopology.from_dict(json.loads(row[0]))!=current:
                    raise ValueError('conflito de versão; nenhuma alteração aplicada')
            if initialize:
                cursor=conn.execute('INSERT INTO simulated_topologies VALUES(?,?,?,?) ON CONFLICT(topology_id) DO NOTHING',
                    (self.topology_id,updated.topology_version,now,json.dumps(updated.to_dict())))
            else:
                cursor=conn.execute('UPDATE simulated_topologies SET topology_version=?,updated_at_utc=?,state_json=? WHERE topology_id=? AND topology_version=?',
                    (updated.topology_version,updated.updated_at_utc,json.dumps(updated.to_dict()),self.topology_id,current.topology_version))
            if cursor.rowcount!=1: raise ValueError('conflito de versão; nenhuma alteração aplicada')
            for r in records:
                conn.execute('INSERT INTO communicators VALUES(?,?,?,?,?) ON CONFLICT(topology_id,device_id) DO UPDATE SET revision=excluded.revision,record_json=excluded.record_json',
                    (self.topology_id,r.device_id,r.node_id,r.revision,json.dumps(r.to_dict())))
            conn.execute('INSERT INTO topology_events(event_id,topology_id,version_before,version_after,event_kind,event_sha256,created_at_utc,event_json) VALUES(?,?,?,?,?,?,?,?)',
                self.store._event(updated,version_before=current.topology_version,event_kind=kind,payload=event))
        return updated
