"""Acquisition implementations. All are consumed through AcquisitionSource.

The simulator emits detector counts, calibration correlations and local outcomes;
it does not report final protocol results. Queue intake is read-only and provides
an integration seam, not a validated instrument or a physical executor.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict, deque
from pathlib import Path
from threading import Lock

import numpy as np

from .contracts import Acquisition, ProtocolError
from .physics import C_KM_S, bbpssw, transmission


def correlation_counts(rng, count, visibility):
    # Separate calibration pairs are measured destructively; processor accounts
    # for them before admitting any unmeasured pair to memory.
    per_axis = min(300, count // 12)
    if per_axis == 0:
        return {}
    result = {}
    for axis, sign in (('xx', 1.), ('yy', -1.), ('zz', 1.)):
        equal = int(rng.binomial(per_axis, np.clip((1. + sign * visibility) / 2., 0., 1.)))
        result[axis] = {'equal': equal, 'opposite': per_axis - equal}
    return result


class NoAcquisitionSource:
    source_id = 'unconfigured'
    origin = 'unavailable'
    physical_validated = False

    def acquire(self, command, config, environment):
        return None


class SimulatedAcquisitionSource:
    source_id = 'local_quantum_devices_v1'
    origin = 'simulated'
    physical_validated = False

    def __init__(self, seed=42):
        if type(seed) is not int or seed < 0:
            raise ProtocolError('invalid_parameter', 'source seed must be a non-negative integer')
        self.seed = seed
        self._sequences = defaultdict(int)
        self._rng = {}
        self.records = deque(maxlen=4096)

    def generator(self, config):
        key = (config.session_id, config.seed, self.seed)
        if key not in self._rng:
            seed = int.from_bytes(hashlib.sha256(repr(key).encode()).digest()[:8], 'big')
            self._rng[key] = np.random.default_rng(seed)
        return self._rng[key]

    def acquire(self, command, config, environment):
        rng = self.generator(config)
        args=command.arguments
        p=dict(config.parameters)
        profiles=p.get('device_parameters',{})
        op=command.operation
        if op=='bsm':p.update(profiles.get(args.get('bsm_node'),{}))
        elif op=='tf_block':
            p.update(profiles.get(config.path[1],{}))
            sender=profiles.get(config.endpoints[0],{})
            for key in ('pulse_rate_hz','signal_mu','decoy_mu'):
                if key in sender:p[key]=sender[key]
        elif op=='satellite_block':p.update(profiles.get(config.path[1],{}))
        endpoints=args.get('device_endpoints',config.endpoints)
        efficiencies=[profiles.get(n,{}).get('memory_read_efficiency',p.get('memory_read_efficiency',.7)) for n in endpoints]
        duration = args.get('duration_s', 1.)
        stage = environment.get('condition', config.scenario)
        if stage == 'unavailable':
            return None
        loss = p.get('extra_loss_db', 0.) + (12. if stage == 'degraded' else 0.)
        jitter = p.get('timing_jitter_ps', 50.) * (8. if stage == 'degraded' else 1.)
        sigma = p.get('phase_sigma_rad', .05) * (8. if stage == 'degraded' else 1.)
        det = p.get('detector_efficiency', .8)
        dark = p.get('dark_probability', 1e-7)
        op = command.operation
        if op == 'generate':
            length = args['length_km'] * p.get('route_factor', 1.2)
            eta = transmission(length, p.get('attenuation_db_km', .2), p.get('insertion_loss_db', 2.) + loss)
            attempts = max(1, int(duration * p.get('attempt_rate_hz', 100000.)))
            gate = math.erf(p.get('gate_half_width_ps', 200.) / (math.sqrt(2.) * max(jitter, 1e-6)))
            detector_product=math.prod(profiles.get(n,{}).get('detector_efficiency',det) for n in endpoints)
            signal = p.get('pair_emission_probability', .1) * eta * detector_product * gate
            noise = 2. * dark
            probability = max(0., min(1., signal + noise))
            heralds = int(rng.binomial(attempts, probability))
            phase = math.exp(-sigma ** 2 / 2.)
            polarization = math.cos(p.get('polarization_error_rad', .03)) ** 2
            visibility = (1. - p.get('source_depolarization', .08)) * phase * polarization * signal / max(1e-30, signal + noise)
            correlations = correlation_counts(rng, heralds, visibility)
            measured = sum(sum(row.values()) for row in correlations.values())
            raw = {'attempts': attempts, 'heralds': heralds, 'correlations': correlations,
                   'memory_writes': int(rng.binomial(max(0, heralds-measured), math.prod(profiles.get(n,{}).get('memory_write_efficiency',p.get('memory_write_efficiency',.7)) for n in endpoints))),
                   'detector_dark_clicks': int(rng.binomial(attempts, min(1., noise))),
                   'timing_jitter_ps': jitter, 'gate_efficiency': gate, 'optical_transmission': eta,
                   'effective_length_km': length}
            if not raw['correlations']:
                raw['heralds'] = 0  # No admitted pair without separate characterization.
                raw['memory_writes'] = 0
        elif op == 'bsm':
            probability = p.get('bsm_success', .5) * p.get('memory_read_efficiency', .7) ** 2 * det ** 2
            success = int(rng.random() < probability)
            outcome = int(rng.integers(0,4))
            # Unassisted linear optics heralds the two identifiable psi states.
            # Failed measurements do not invent a Bell outcome.
            if p.get('bsm_kind','linear_optics_unassisted') == 'linear_optics_unassisted':
                outcome = 2 + outcome % 2
            raw = {'bsm_success': success, 'bell_outcome': outcome if success else None,
                   'detector_efficiency': det, 'readout_efficiency': p.get('memory_read_efficiency', .7)}
        elif op == 'purify':
            gate_error=max(profiles.get(n,{}).get('purification_operation_error',p.get('purification_operation_error',.01)) for n in endpoints)
            probability, _ = bbpssw(args['first_fidelity'], args['second_fidelity'], gate_error)
            # The local outcomes arise from the equal/unequal Werner recurrence
            # branches; the final conditional fidelity is computed by adapter.
            a = int(rng.integers(0, 2)); b = a if rng.random() < probability else 1 - a
            b ^= args.get('first_frame',(0,0))[0] ^ args.get('second_frame',(0,0))[0]
            raw = {'alice_outcome': a, 'bob_outcome': b}
        elif op == 'readout':
            raw = {'readout': int(rng.random() < math.prod(efficiencies))}
        elif op == 'tf_block':
            lengths = [x * p.get('route_factor', 1.2) for x in args['arm_lengths_km']]
            gate = math.erf(p.get('gate_half_width_ps', 200.) / (math.sqrt(2.) * max(jitter, 1e-6)))
            eta_a, eta_b = [transmission(x, p.get('attenuation_db_km', .2), p.get('insertion_loss_db', 2.) + loss) * det * gate for x in lengths]
            n = max(0, int(duration * p.get('pulse_rate_hz', 1000000.)))
            if n<4:return None  # No fabricated minimum sample; insufficient pulses for this block model.
            mu, nu, send = p.get('signal_mu', .4), p.get('decoy_mu', .1), p.get('send_probability', .08)
            # Z blocks: local send/not-send choices, Poisson photon arrivals and
            # threshold detectors. Both-send/not-send contributions are errors.
            categories = rng.multinomial(n, [(1-send)**2, send*(1-send), send*(1-send), send**2])
            probabilities = [2*dark, 1-math.exp(-mu*eta_a)+2*dark, 1-math.exp(-mu*eta_b)+2*dark,
                             1-math.exp(-mu*(eta_a+eta_b))+2*dark]
            clicks = [int(rng.binomial(int(k), min(1., max(0., q)))) for k, q in zip(categories, probabilities)]
            mismatch = clicks[0] + clicks[3]
            phase_reference_noise = float(rng.normal(0., sigma))
            visibility = math.cos(phase_reference_noise) * math.cos(p.get('polarization_error_rad', .03)) ** 2
            # X basis phase-sliced interference at Charlie, including intensity
            # imbalance. A residual random phase follows reference correction.
            balance = 2 * math.sqrt(eta_a*eta_b) / max(1e-30, eta_a+eta_b)
            x_n = n // 4
            x_gain = min(1., 1-math.exp(-nu*(eta_a+eta_b)) + 2*dark)
            x_clicks = int(rng.binomial(x_n, x_gain))
            x_error = np.clip((1-balance*visibility)/2 + dark/max(1e-30,x_gain),0.,.5)
            x_errors = int(rng.binomial(x_clicks, x_error))
            # Independent phase-randomized one-arm decoy probes, not exposed
            # photon-number truth, feed the same decoy estimator as measured data.
            decoys = {}
            for name, intensity in [('vacuum', 0.), ('weak', nu), ('strong', mu)]:
                count = n // 3
                gain = (2-math.exp(-intensity*eta_a)-math.exp(-intensity*eta_b))/2 + 2*dark
                decoys[name] = {'trials': count, 'clicks': int(rng.binomial(count, min(1., gain)))}
            raw = {'z_trials': n, 'z_clicks': sum(clicks), 'z_errors': mismatch,
                   'x_trials': x_n, 'x_clicks': x_clicks, 'x_errors': x_errors, **decoys,
                   'phase_reference_residual_rad': phase_reference_noise, 'arm_transmissions': [eta_a, eta_b],
                   'effective_arm_lengths_km': lengths, 'timing_jitter_ps': jitter}
        elif op == 'satellite_block':
            from .satellite_simulation import generate_observation
            raw=generate_observation(rng,command,config,environment,p,source_id=self.source_id,
                                     origin=self.origin,sequence=self._sequences[config.session_id]+1)
            geometry=raw['geometry']
        elif op == 'isl_block':
            from .isl_simulation import generate_observation
            raw=generate_observation(rng,command,config,environment,p,source_id=self.source_id,
                                     origin=self.origin,sequence=self._sequences[config.session_id]+1)
            geometry=raw['geometry']
        else:
            raise ProtocolError('unsupported_operation', op)
        quantum_flight_s = 0.
        if op == 'generate':
            quantum_flight_s = raw['effective_length_km']/(C_KM_S/1.468)
        elif op == 'tf_block':
            quantum_flight_s = max(lengths)/(C_KM_S/1.468)
        elif op in ('satellite_block','isl_block'):
            quantum_flight_s = max(g['flight_time_s'] for g in geometry)
        raw['quantum_flight_s'] = quantum_flight_s
        self._sequences[config.session_id] += 1
        acquisition = Acquisition(command.command_id, config.session_id, op, self.source_id, self.origin,
                    self._sequences[config.session_id], command.emitted_at_s +
                    (duration if op in ('generate','tf_block','satellite_block','isl_block') else p.get('operation_duration_s',1e-5)) + quantum_flight_s,
                    p.get('clock_offset_s', 0.), command.quantum_channel_ids, command.classical_channel_ids, raw)
        if op in ('satellite_block','isl_block'):
            previous=[r for r in self.records if r['operation'] in ('satellite_block','isl_block')]
            if len(previous)>=16:self.records.remove(previous[0])
        self.records.append(acquisition.to_dict())
        return acquisition


class MeasurementFileSource:
    origin = 'measurement_file'
    physical_validated = False

    def __init__(self, path, source_id='measurement_file'):
        self.source_id = source_id
        self.path = Path(path)
        if self.path.stat().st_size > 64*1024*1024:
            raise ProtocolError('invalid_source','acquisition file exceeds 64 MiB')
        self._records = {}
        self.sha256 = hashlib.sha256(self.path.read_bytes()).hexdigest()
        for line in self.path.read_text(encoding='utf-8').splitlines():
            if not line.strip():
                continue
            record = Acquisition.from_dict(json.loads(line))
            if record.origin != self.origin or record.source_id != source_id:
                raise ProtocolError('wrong_source','file origin and adapter source id must match')
            if record.command_id in self._records:
                raise ProtocolError('replay','duplicate command observation in file')
            self._records[record.command_id] = record

    def acquire(self, command, config, environment):
        return self._records.pop(command.command_id, None)


class ReadOnlyQueueSource:
    origin = 'physical'
    physical_validated = False

    def __init__(self, source_id, maximum=1024):
        self.source_id = source_id
        self.maximum = maximum
        self._queue = {}
        self._lock = Lock()

    def submit(self, acquisition: Acquisition):
        if acquisition.source_id != self.source_id or acquisition.origin != self.origin:
            raise ProtocolError('wrong_source', 'queue source binding mismatch')
        with self._lock:
            if acquisition.command_id in self._queue:
                raise ProtocolError('replay','duplicate queued command')
            if len(self._queue) >= self.maximum:
                raise ProtocolError('backpressure','queue capacity reached')
            self._queue[acquisition.command_id] = acquisition

    def acquire(self, command, config, environment):
        with self._lock:
            return self._queue.pop(command.command_id, None)
