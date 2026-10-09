"""Bounded histories of published, classically received protocol observables.

This records backend publications; it never turns key rates into polarisation
waves or Bell fidelities. Observation times remain in the protocol clock domain.
"""
from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from math import isfinite


# technology, metric, label, unit, physical bounds, publication cadence
OBSERVABLES = (
    ("tf_qkd", "qber", "QBER observado", "1", (0., 1.), "receipt"),
    ("tf_qkd", "phase_reference_residual_rad", "Resíduo da referência de fase", "rad", (None, None), "receipt"),
    ("tf_qkd", "conditional_key_estimate_bps", "Estimativa condicional de chave", "bit/s", (0., None), "receipt"),
    ("tf_qkd", "sifted_bits", "Bits sifted do bloco", "bit", (0., None), "receipt"),
    ("tf_qkd", "single_photon_yield_lower_estimate", "Yield monofotônico · limite estimado", "1", (0., 1.), "receipt"),
    ("tf_qkd", "x_phase_error_observed", "Erro X observado · hipótese de fase", "1", (0., 1.), "receipt"),
    ("tf_qkd", "certified_secret_bits", "Bits secretos certificados", "bit", (0., None), "receipt"),
    ("entanglement_swapping", "generation_pair_rate_hz", "Taxa de pares gerados · média da sessão", "pair/s", (0., None), "clock"),
    ("entanglement_swapping", "delivered_pair_rate_hz", "Taxa de pares entregues · média da sessão", "pair/s", (0., None), "clock"),
    ("entanglement_swapping", "bsm_success_fraction", "Fração de sucesso BSM", "1", (0., 1.), "receipt"),
    ("entanglement_swapping", "last_delivered_fidelity", "Fidelidade do último par entregue", "1", (0., 1.), "receipt"),
    ("quantum_memory", "memory_occupancy", "Pares armazenados", "pair", (0., None), "clock"),
    ("purification", "purification_yield", "Rendimento de purificação", "1", (0., 1.), "receipt"),
    ("purification", "last_purification_success_probability", "Probabilidade de sucesso publicada", "1", (0., 1.), "receipt"),
    ("satellite_doppler", "qber", "QBER conjunto recebido", "1", (0., 1.), "receipt"),
    ("satellite_doppler", "coincidences", "Coincidências do bloco", "count", (0., None), "receipt"),
    ("satellite_doppler", "conditional_key_estimate_bps", "Estimativa condicional de chave", "bit/s", (0., None), "receipt"),
    ("satellite_doppler", "available", "Disponibilidade publicada do enlace", "1", (0., 1.), "receipt"),
    ("satellite_isl", "qber", "QBER ISL recebido", "1", (0., 1.), "receipt"),
    ("satellite_isl", "coincidences", "Coincidências ISL do bloco", "count", (0., None), "receipt"),
    ("satellite_isl", "conditional_key_estimate_bps", "Estimativa condicional de chave", "bit/s", (0., None), "receipt"),
    ("satellite_isl", "available", "Disponibilidade ISL publicada", "1", (0., 1.), "receipt"),
)


def finite(value):
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value) else None


class ProtocolHistory:
    """Record bounded actual publication times, with nulls for unavailable data."""

    def __init__(self, *, max_samples=240, window_s=120., max_series=2048):
        self.max_samples, self.window_s, self.max_series = int(max_samples), float(window_s), int(max_series)
        self._series = {}
        self._fingerprints = {}
        self._clock = None

    def update(self, state, *, source_kind=None, network_time_s=None, at_utc="") -> list:
        """Consume ProtocolRuntime.snapshot() after reception/metric processing.

        Call while holding the engine's existing publication lock. The result is
        JSON-compatible and can be assigned to technologies['protocol_series'].
        ``at_s`` is the actual receipt or derived-state publication time, never a
        rate-derived regular grid. ``published_at_utc`` is engine publication time.
        """
        clock = finite(state.get("clock_s"))
        if clock is None:
            return []
        if self._clock is not None and clock < self._clock:
            self._series.clear()
            self._fingerprints.clear()
        self._clock = clock
        sessions = state.get("sessions", {})
        active_keys = set()
        for ident, session in sessions.items():
            config, metrics = session.get("config", {}), session.get("metrics", {})
            technologies = config.get("technologies", ())
            nodes = tuple(config.get("path", config.get("endpoints", ())))
            origin = str(metrics.get("observation_origin") or session.get("source", {}).get("origin")
                         or state.get("origin") or "not_published")
            acquisition = config.get("parameters", {}).get("acquisition_source", {})
            source_id = str(metrics.get("observation_source_id") or acquisition.get("source_id") or acquisition.get("path")
                            or state.get("source_id") or "not_published")
            fingerprint = (nodes, origin, source_id, repr(config.get("parameters", {}).get("acquisition_source")))
            if self._fingerprints.get(ident, fingerprint) != fingerprint:
                self._series = {key: value for key, value in self._series.items() if value[0]["session_id"] != ident}
            self._fingerprints[ident] = fingerprint
            received_at = finite(metrics.get("last_observed_at_s"))
            status = session.get("status", "configured")
            unavailable = not config.get("enabled", True) or status in (
                "configured", "disabled", "error", "paused", "no_input", "blocked") or (
                    status == "unavailable" and metrics.get("available") is not False)
            missing = "aguardando aquisição validada e recebimento clássico" if received_at is None else ""
            if unavailable:
                missing = str(session.get("reason") or status)
            for technology, metric, label, unit, bounds, cadence in OBSERVABLES:
                if technology not in technologies:
                    continue
                value = None if missing else finite(metrics.get(metric))
                if not missing and metric == "available" and type(metrics.get(metric)) is bool:
                    value = float(metrics[metric])
                reason = missing or ("observável não publicado" if value is None else "")
                at_s = clock if missing or cadence == "clock" else received_at
                self._record(active_keys, ident, technology, metric, label, unit, nodes, origin, source_id,
                             "session", bounds, at_s, value, reason, at_utc, clock)
            if "entanglement_swapping" in technologies:
                for segment, raw in metrics.get("segments", {}).items():
                    at_s = finite(raw.get("at_s"))
                    if at_s is None:
                        continue
                    heralds = finite(raw.get("heralds"))
                    value = finite(raw.get("fidelity_estimate")) if heralds is not None and heralds > 0 else None
                    reason = "nenhum par heraldado no segmento" if value is None else ""
                    if missing:
                        at_s, value, reason = clock, None, missing
                    self._record(active_keys, ident, "entanglement_swapping", "segment_fidelity:" + segment,
                                 "Fidelidade estimada · " + segment.replace("|", " ↔ "), "1", tuple(segment.split("|")),
                                 str(raw.get("origin") or origin), source_id, "segment", (0., 1.),
                                 at_s, value, reason, at_utc, clock)
        self._series = {key: value for key, value in self._series.items() if key in active_keys}
        self._fingerprints = {key: value for key, value in self._fingerprints.items() if key in sessions}
        return [{**metadata, "samples": [dict(point) for point in samples]}
                for metadata, samples in self._series.values()]

    def _record(self, active_keys, session, technology, metric, label, unit, nodes, origin, source_id,
                scope, bounds, at_s, value, reason, at_utc, clock):
        if at_s is None or at_s > clock:
            return
        key = f"{session}:{technology}:{metric}"
        active_keys.add(key)
        if key not in self._series and len(self._series) >= self.max_series:
            return
        metadata = {"key": key, "session_id": session, "technology": technology, "metric": metric,
                    "label": label, "unit": unit, "node_ids": list(nodes), "origin": origin,
                    "source_id": source_id, "scope": scope, "bounds": list(bounds),
                    "time_domain": "protocol_clock_s", "clock_kind": "simulation" if origin == "simulated" else "runtime",
                    "published_at_utc": at_utc, "window_s": self.window_s}
        _old, samples = self._series.get(key, (None, deque(maxlen=self.max_samples)))
        point = {"at_s": at_s, "value": value, "reason": reason, "origin": origin, "source_id": source_id}
        if samples and samples[-1]["at_s"] == at_s:
            samples[-1] = point
        elif not samples or at_s > samples[-1]["at_s"]:
            samples.append(point)
        while samples and samples[0]["at_s"] < clock - self.window_s:
            samples.popleft()
        self._series[key] = metadata, samples


def published_series(technologies, *, node_id=None) -> tuple:
    """Read the version's scalar history publications without generating data."""
    raw = technologies.get("protocol_series", ()) if isinstance(technologies, Mapping) else ()
    if not isinstance(raw, (list, tuple)):
        return ()
    return tuple(item for item in raw if isinstance(item, Mapping)
                 and (node_id is None or node_id in item.get("node_ids", ())))
