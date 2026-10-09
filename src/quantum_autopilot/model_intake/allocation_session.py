"""Bounded offline allocator snapshot with a same-input persistence comparison."""
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path

from .sync_session import MAX_INPUT_BYTES, _constant, _number, _unique

MODEL_IDS = ("qnet.allocator.persistence", "qnet.allocator.ridge")
SCHEMA = "orion.optional-allocation-input.v1"
LINK_NAMES = (
    "sao_paulo|campinas", "sao_paulo|sao_jose_dos_campos", "sao_paulo|santos",
    "campinas|sao_jose_dos_campos", "sao_jose_dos_campos|taubate", "sao_jose_dos_campos|santos",
    "taubate|resende", "resende|volta_redonda", "resende|juiz_de_fora",
    "volta_redonda|rio_de_janeiro", "rio_de_janeiro|juiz_de_fora",
)


def _vector(value, maximum=None):
    if not isinstance(value, list) or len(value) != 11:
        raise ValueError("eleven entries in reviewed link order required")
    numbers = [_number(item) for item in value]
    if any(n < 0 or (maximum is not None and n > maximum) for n in numbers):
        raise ValueError("vector outside permitted range")
    return numbers


def parse_allocation_input(model_id, text):
    if model_id not in MODEL_IDS:
        raise ValueError("unknown allocation checkpoint")
    if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_INPUT_BYTES:
        raise ValueError("input must be UTF-8 JSON of at most 64 KiB")
    request = json.loads(text, object_pairs_hook=_unique, parse_constant=_constant)
    fields = {"schema", "model_id", "evidence", "stream_id", "protocol", "link_names", "period_s",
              "latency_s", "horizon_s", "observation", "history_times_s", "drift_history"}
    if not isinstance(request, dict) or set(request) != fields or request["schema"] != SCHEMA:
        raise ValueError("invalid allocation input schema or fields")
    if request["model_id"] != model_id:
        raise ValueError("checkpoint differs from selected model; no substitution")
    if request["evidence"] != "simulated_offline" or request["protocol"] != "single_photon_bb84":
        raise ValueError("requires simulated_offline single_photon_bb84; no measured or decoy-state input")
    if request["link_names"] != list(LINK_NAMES):
        raise ValueError("link identity/order differs from reviewed eleven-link plant")
    stream = request["stream_id"]
    if not isinstance(stream, str) or not stream.strip() or len(stream) > 160 or any(ord(c) < 32 for c in stream):
        raise ValueError("bounded nonempty stream_id without control characters required")
    if [_number(request[key]) for key in ("period_s", "latency_s", "horizon_s")] != [5., 30., 150.]:
        raise ValueError("plant requires 5 s period, 30 s latency and 150 s forecast horizon")
    observation = request["observation"]
    obs_fields = {"time_s", "measured_qber", "pending_admission", "raman_qber_estimate",
                  "key_pool_bits", "backlog_bits", "urgency_s"}
    if not isinstance(observation, dict) or set(observation) != obs_fields:
        raise ValueError("invalid typed observation fields")
    stamp = _number(observation["time_s"])
    if stamp < 0 or any(not math.isfinite(stamp + d) or abs((stamp + d) - stamp - d) > 1e-9 for d in (5., 30., 150.)):
        raise ValueError("nonnegative relative time must represent plant timing precisely")
    for key in ("measured_qber", "raman_qber_estimate", "key_pool_bits", "backlog_bits"):
        _vector(observation[key], 1. if key in ("measured_qber", "raman_qber_estimate") else None)
    pending = observation["pending_admission"]
    if not isinstance(pending, list) or len(pending) != 6:
        raise ValueError("six committed admission rows required, oldest first")
    for row in pending:
        if any(n not in (0., .25, .5, .75, 1.) for n in _vector(row)):
            raise ValueError("pending admissions must use the five reviewed levels")
    urgency = observation["urgency_s"]
    if not isinstance(urgency, list) or len(urgency) != 11:
        raise ValueError("eleven urgency entries required")
    if any(value is not None and _number(value) <= 0 for value in urgency):
        raise ValueError("urgency must be positive seconds or null; no expired deadlines")
    times, history = request["history_times_s"], request["drift_history"]
    if not isinstance(times, list) or not isinstance(history, list) or not 0 <= len(times) == len(history) <= 5:
        raise ValueError("zero to five paired history timestamps/rows required")
    for i, (time, row) in enumerate(zip(times, history)):
        time = _number(time)
        if time < 0 or abs(time - (stamp - (len(times) - i) * 5.)) > 1e-9:
            raise ValueError("history must immediately precede the observation at 5 s cadence, oldest first")
        _vector(row, 1.)
    return request


def load_allocation_input(model_id, path):
    with Path(path).open("rb") as stream:
        raw = stream.read(MAX_INPUT_BYTES + 1)
    if len(raw) > MAX_INPUT_BYTES:
        raise ValueError("input exceeds 64 KiB")
    text = raw.decode("utf-8")
    parse_allocation_input(model_id, text)
    return text


def demo_allocation_input(model_id):
    if model_id not in MODEL_IDS:
        raise ValueError("unknown allocation checkpoint")
    return json.dumps({
        "schema": SCHEMA, "model_id": model_id, "evidence": "simulated_offline",
        "stream_id": "demo/hand-authored/allocation", "protocol": "single_photon_bb84",
        "link_names": list(LINK_NAMES), "period_s": 5., "latency_s": 30., "horizon_s": 150.,
        "observation": {"time_s": 25., "measured_qber": [.025]*11, "raman_qber_estimate": [0.]*11,
                        "pending_admission": [[1.]*11 for _ in range(6)], "key_pool_bits": [0.]*11,
                        "backlog_bits": [100000.]*11, "urgency_s": [120.]*11},
        "history_times_s": [0., 5., 10., 15., 20.],
        "drift_history": [[.005 + .003*i]*11 for i in range(5)],
    }, indent=2)


@dataclass(frozen=True)
class AllocationEvaluation:
    model_id: str
    evaluated_at_utc: str
    input_json: str
    input_sha256: str
    stream_id: str
    archive_sha256: str
    plant_sha256: str
    history_samples: int
    recommendation: dict
    persistence: dict
    differing_admission_links: tuple[str, ...]
    schema: str = field(default="orion.optional-allocation-evaluation.v1", init=False)
    evidence: str = field(default="simulated_offline", init=False)
    physical_action_performed: bool = field(default=False, init=False)

    def serialized(self):
        return json.dumps(asdict(self), indent=2, ensure_ascii=False, allow_nan=False)

    def describe(self):
        result, baseline = self.recommendation, self.persistence
        rows = "\n".join(f"{name}: {level:.2f} / {base:.2f}"
                         for name, level, base in zip(LINK_NAMES, result["admission"], baseline["admission"]))
        return ("SIMULATED OFFLINE · HYBRID ALLOCATION · NO EXECUTOR\n\n"
                f"Checkpoint: {self.model_id}\nForecast status: {result['status']}\n"
                f"History: {self.history_samples}/5 previous samples + current observation\n"
                f"Decision: {result['time_s']:.9g} s; effective after latency: {result['effective_at_s']:.9g} s\n"
                f"Admission differences from persistence: {len(self.differing_admission_links)}/11\n\n"
                "LINK: SELECTED / PERSISTENCE (classical admission fraction)\n" + rows + "\n\n"
                "Persistence is NOT ML. Ridge needs five prior drift samples; otherwise warmup_persistence. "
                "Both decisions use the same observation and committed queue; differences are not performance gains.\n"
                "Six pending rows are externally supplied, oldest first; this session never advances or rewrites the queue. "
                "No feedback loop or topology remapping. Forecast horizon: 150 s, not actuation latency.\n"
                "Key budgets/coverage are asymptotic model estimates, NOT delivered keys or finite-key certificates. "
                "A null budget means not evaluated (no debt), not zero. No throughput or Bell-fidelity claim.\n\n"
                "REPRODUCIBLE RECORD (QBER fractions, seconds, bits; full per-link forecasts)\n" + self.serialized())


def evaluate_allocation_session(model_id, archive_path, input_text):
    request = parse_allocation_input(model_id, input_text)
    import numpy as np
    from .allocation import AllocationObservation
    from .catalog import open_optional_model

    observation = AllocationObservation(**request["observation"])
    kwargs = dict(drift_history=np.asarray(request["drift_history"], dtype=float).reshape(-1, 11),
                  history_times_s=request["history_times_s"], domain="simulated_offline",
                  protocol=request["protocol"], link_names=request["link_names"])
    model = open_optional_model(model_id, archive_path=archive_path)
    selected = model.recommend_snapshot(observation, **kwargs)
    baseline = (selected if model_id == MODEL_IDS[0] else
                open_optional_model(MODEL_IDS[0], archive_path=archive_path).recommend_snapshot(observation, **kwargs))
    if (selected.archive_sha256, selected.plant_sha256) != (baseline.archive_sha256, baseline.plant_sha256):
        raise ValueError("comparison requires identical reviewed archive and plant")
    report = AllocationEvaluation(
        model_id, datetime.now(timezone.utc).isoformat(), input_text, hashlib.sha256(input_text.encode()).hexdigest(),
        request["stream_id"], selected.archive_sha256, selected.plant_sha256, len(request["drift_history"]),
        asdict(selected), asdict(baseline),
        tuple(name for name, a, b in zip(LINK_NAMES, selected.admission, baseline.admission) if a != b),
    )
    report.serialized()
    return report


def export_allocation_evaluation(result, path):
    raw = result.serialized().encode("utf-8")
    with Path(path).open("xb") as stream:
        stream.write(raw)
