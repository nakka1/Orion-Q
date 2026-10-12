"""Present received per-link algorithm identities separately from data origin.

No registry loading, model activation, scientific calculation or backend IO.
Unknown identities remain unclassified; a configured technology is not activity.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from ..model_intake.catalog import model_options

PROJECT_MODELS = frozenset(option.model_id for option in model_options()) - {"qnet.allocator.persistence"}
REFERENCE_CONTROLLERS = frozenset({
    "baseline.alpha_beta", "baseline.static", "baseline.qber_threshold",
    "baseline.quantum_first", "baseline.greedy_fidelity", "baseline.none",
    "baseline.persistence", "qnet.allocator.persistence",
})
REFERENCE_ADAPTERS = {
    "TwinFieldAdapter": "SNS-TF-QKD reference protocol and finite-block estimate",
    "SatelliteAdapter": "satellite optical/BBM92 reference model",
    "InterSatelliteAdapter": "ISL optical/BBM92 reference model",
    "OrbitalRelayAdapter": "orbital memory/BSM reference model",
    "SwappingAdapter": "entanglement generation/BSM reference model",
    "MemoryAdapter": "quantum-memory decoherence reference model",
    "PurificationAdapter": "purification reference model",
}


def _mapping(value):
    return value if isinstance(value, Mapping) else {}


@dataclass(frozen=True)
class LinkProcessingLabels:
    badge: str = "UNREPORTED"
    data_source: str = "unreported"
    physics: str = "not reported by the backend"
    controls: str = "not reported by the backend"
    fallback: bool = False
    observed: bool = False
    detail: str = "No per-link processing record received."

    @property
    def table_text(self):
        return f"{self.badge} | {self.data_source.upper()}"


def link_processing_labels(link, technologies=None) -> LinkProcessingLabels:
    """Describe one committed record; never infer models from a toggle or node name."""
    if link is None:
        return LinkProcessingLabels()
    provenance = _mapping(getattr(link, "provenance", {}))
    technology = _mapping(provenance.get("technology"))
    metrics = _mapping(provenance.get("protocol_metrics"))
    channels = _mapping(provenance.get("channels"))
    data_source = provenance.get("source") if technology else getattr(link, "input", None)
    if not isinstance(data_source, str) or not data_source:
        data_source = "unreported"
    controllers = tuple(getattr(link, "controllers", ()) or ())
    rows, project, unknown, fallback = [], False, False, False
    warmup = False
    for controller in controllers:
        ident = str(getattr(controller, "controller_id", ""))
        role = str(getattr(controller, "role", ""))
        state = getattr(controller, "model_state", None)
        is_fallback = state == "fallback"
        fallback |= is_fallback
        if ident in REFERENCE_CONTROLLERS:
            origin = "reference control"
        elif ident in PROJECT_MODELS and state == "active":
            if role == "detector" and str(getattr(getattr(link, "detector", None), "state", "")).startswith("session baseline"):
                origin = "project model waiting for detector baseline"
                warmup = True
            else:
                origin = "project model"
                project = True
        else:
            origin = "effective algorithm origin unreported"
            unknown = True
        suffix = f"; state={state}" if state else ""
        rows.append(f"{role}: {ident or 'unreported'} — {origin}{suffix}")
    admission = getattr(link, "classical_admission", None)
    if admission is not None and getattr(admission, "controller_id", ""):
        ident = admission.controller_id
        is_fallback = getattr(admission, "fallback_used", None) is True
        fallback |= is_fallback
        if ident in REFERENCE_CONTROLLERS:
            origin = "reference control"
        elif ident in PROJECT_MODELS and not is_fallback and getattr(admission, "model_state", None) == "active":
            origin, project = "project model", True
        else:
            origin, unknown = "effective algorithm origin unreported", True
        rows.append(f"classical allocation: {ident} — {origin}" + ("; fallback" if is_fallback else ""))
    if technology:
        declared = technology.get("technologies", ())
        registry = _mapping(_mapping(technologies).get("registry"))
        adapters = _mapping(registry.get("adapters"))
        physics_rows = []
        for ident in declared if isinstance(declared, (list, tuple)) else ():
            implementation = _mapping(adapters.get(ident)).get("implementation")
            description = REFERENCE_ADAPTERS.get(implementation)
            if description is None:
                unknown = True
                physics_rows.append(f"{ident}: implementation origin unreported ({implementation or 'unreported'})")
            else:
                physics_rows.append(f"{ident}: {description} ({implementation})")
        physics = "; ".join(physics_rows) or "protocol implementation not reported"
        observed = "last_observed_at_s" in metrics and channels.get("status") == "received"
        if not physics_rows:
            unknown = True
    else:
        physics = "Decoy-BB84 reference equations; Werner fidelity estimate from QBER" if controllers else "not reported by the backend"
        observed = bool(controllers) and data_source not in {"none", "withheld", "unreported"}
        if channels and channels.get("status") != "received":
            observed = False
    controls = "; ".join(rows) or ("protocol reference processing; no learned controller reported" if technology and not unknown else "not reported by the backend")
    if not observed:
        badge = "NO SAMPLE"
    elif unknown:
        badge = "UNREPORTED"
    elif project:
        badge = "REF + MODEL"
    elif warmup:
        badge = "REF / WARMUP"
    else:
        badge = "REFERENCE"
    if fallback:
        badge += " / FALLBACK"
    activity = "received processing record" if observed else "no received processing record; configured IDs are not activity"
    detail = f"Data source: {data_source}. Processing: {badge} ({activity}). Physics: {physics}. Controls: {controls}."
    if fallback:
        detail += " Fallback is reported by this link's backend record."
    detail += " Algorithm labels are descriptive; they neither authorize physical control nor certify QKD keys."
    return LinkProcessingLabels(badge, data_source, physics, controls, fallback, observed, detail)
