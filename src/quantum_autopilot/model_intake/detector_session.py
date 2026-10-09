"""Offline attack-detector session on one simulated QKD session log; classification only, no action.

The model is the reviewed safe export of the author's detector (``detector.py``): the
archive's pickle is hashed, never loaded. The input is the public log a receiver
publishes, in the columns of the author's ``exemplos/detector_*_entrada.csv``; the
baseline is the QBER alarm a deployed link uses today, at the author's calibration.
"""
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from .sync_session import _constant, _number, _unique

MODEL_ID = "qkd.detector.boosting"
SCHEMA = "orion.optional-detector-input.v1"
MAX_INPUT_BYTES = 256 * 1024
MIN_PERIODS, MAX_PERIODS = 90, 900
#: ``exemplos/detector_*_entrada.csv`` of the reviewed export, in order.
COLUMNS = ("time_s", "period", "signal_gain", "decoy_gain", "vacuum_gain", "signal_error", "decoy_error",
           "vacuum_error", "signal_intensity", "decoy_intensity", "single_photon_yield_lower",
           "single_photon_error_upper", "key_rate_hz", "temperature_c", "expected_gain", "pulses", "length_km")
#: Training domain (scripts/task5.py): 20-70 km spans, 1 s periods of 1e9 pulses, intensities 0.5 / 0.1.
TRAINED = {"signal_intensity": 0.5, "decoy_intensity": 0.1, "pulses": 1e9}
SPAN_KM = (20.0, 70.0)
#: The author's QBER alarm calibration (runs/detector/settings.json, threshold_sigma), reproduced exactly by
#: the detector campaign.
AUTHOR_QBER_SIGMA = 97.15752200912328


def parse_detector_input(text):
    if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_INPUT_BYTES:
        raise ValueError("input must be UTF-8 JSON of at most 256 KiB")
    request = json.loads(text, object_pairs_hook=_unique, parse_constant=_constant)
    if not isinstance(request, dict) or set(request) != {"schema", "evidence", "stream_id", "columns", "rows"} \
            or request["schema"] != SCHEMA:
        raise ValueError("invalid detector input schema or fields")
    if request["evidence"] != "simulated_offline":
        raise ValueError("only simulated_offline input is admitted; measured claims are refused")
    stream = request["stream_id"]
    if not isinstance(stream, str) or not stream.strip() or len(stream) > 160 or any(ord(c) < 32 for c in stream):
        raise ValueError("stream_id must be nonempty text up to 160 characters without control characters")
    if request["columns"] != list(COLUMNS):
        raise ValueError("columns must be the 17 public session columns of the reviewed export, in order")
    rows = request["rows"]
    if not isinstance(rows, list) or not MIN_PERIODS <= len(rows) <= MAX_PERIODS:
        raise ValueError(f"between {MIN_PERIODS} and {MAX_PERIODS} settling periods required")
    if any(not isinstance(row, list) or len(row) != len(COLUMNS) for row in rows):
        raise ValueError("every row needs one number per column")
    values = [[_number(value) for value in row] for row in rows]
    column = {name: [row[position] for row in values] for position, name in enumerate(COLUMNS)}
    if column["period"] != [float(k) for k in range(len(rows))] or column["time_s"] != column["period"]:
        raise ValueError("periods must be 0, 1, 2, ... with time_s = period (1 s settling period)")
    for name, value in TRAINED.items():
        if any(abs(item - value) > 1e-12 * max(1.0, value) for item in column[name]):
            raise ValueError(f"{name} outside the trained transmitter configuration ({value:g})")
    spans = set(column["length_km"])
    if len(spans) != 1 or not SPAN_KM[0] <= next(iter(spans)) <= SPAN_KM[1]:
        raise ValueError("one span length within the trained 20-70 km domain required")
    for name in COLUMNS[2:8] + COLUMNS[10:13] + ("expected_gain",):
        if any(item < 0 for item in column[name]):
            raise ValueError(f"{name} must be nonnegative")
    return request, column


def load_detector_input(path):
    with Path(path).open("rb") as stream:
        raw = stream.read(MAX_INPUT_BYTES + 1)
    if len(raw) > MAX_INPUT_BYTES:
        raise ValueError("input exceeds 256 KiB")
    text = raw.decode("utf-8")
    parse_detector_input(text)
    return text


def demo_detector_input(seed=20261022, label="intercept_resend", duration_s=240.0, length_km=35.0):
    """One session of the author's plant (qnet_qkd), public columns only; not measured data."""
    from qnet_qkd.datagen.qkd import generate_qkd_session_dataset

    from ..control_plane.keyrate_loop import LINK, SETTINGS

    frame = generate_qkd_session_dataset(episodes_per_class=1, duration_s=duration_s, period_s=1.0,
                                         baseline_periods=60, lengths_km=(length_km,), labels=(label,),
                                         settings=SETTINGS, link=LINK, residual_fraction=0.2, adversarial=True,
                                         seed=seed).frame
    rows = [[float(value) for value in row] for row in frame[list(COLUMNS)].itertuples(index=False)]
    return json.dumps({"schema": SCHEMA, "evidence": "simulated_offline",
                       "stream_id": f"demo/author-plant/{label}/{length_km:g}km/seed-{seed}",
                       "columns": list(COLUMNS), "rows": rows}, separators=(",", ":"))


@dataclass(frozen=True)
class DetectorEvaluation:
    evaluated_at_utc: str
    input_sha256: str
    stream_id: str
    archive_sha256: str
    archive_member_sha256: str
    export_npz_sha256: str
    span_km: float
    periods_in: int
    periods_scored: int
    first_alarm_s: float | None
    alarm_periods: dict
    baseline_first_alarm_s: float | None
    baseline_alarm_periods: int
    periods: tuple[dict, ...]
    model_id: str = field(default=MODEL_ID, init=False)
    schema: str = field(default="orion.optional-detector-evaluation.v1", init=False)
    baseline: str = field(default=f"QBER alarm at the author's calibration (sigma {AUTHOR_QBER_SIGMA:.6g})",
                          init=False)
    decision_rule: str = field(default="gated gradient boosting, alarm after 3 consecutive equal calls", init=False)
    evidence: str = field(default="simulated_offline", init=False)
    physical_action_performed: bool = field(default=False, init=False)

    def serialized(self):
        return json.dumps(asdict(self), indent=2, ensure_ascii=False, allow_nan=False)

    def describe(self):
        alarms = ", ".join(f"{label} {count}" for label, count in self.alarm_periods.items()) or "none"
        changes, previous = [], None
        for row in self.periods:
            if row["alarm"] != previous:
                changes.append(f"  t = {row['time_s']:5.0f} s  alarm -> {row['alarm']}  "
                               f"(P(attack) {row['attack_probability']:.3g})")
                previous = row["alarm"]
        first = "never" if self.first_alarm_s is None else f"{self.first_alarm_s:g} s"
        baseline = "never" if self.baseline_first_alarm_s is None else f"{self.baseline_first_alarm_s:g} s"
        return ("SIMULATED OFFLINE · CLASSIFICATION ONLY · NO ACTION\n\n"
                f"Model: {self.model_id} (safe export, pickle hashed not loaded)\n"
                f"Span: {self.span_km:g} km · {self.periods_scored} of {self.periods_in} periods scored "
                "(the first 60 are the session's own baseline)\n"
                f"First alarm: {first} · alarm periods by class: {alarms}\n"
                f"QBER alarm baseline: first {baseline}, {self.baseline_alarm_periods} periods flagged\n\n"
                "ALARM CHANGES\n" + "\n".join(changes[:40]) + ("\n  ..." if len(changes) > 40 else "") + "\n\n"
                "Classes are the author's simulated event types. An alarm is a label for an operator, not a "
                "security proof, a key decision or a device command. Beam splitting has no physical signature "
                "and is not detectable by construction.\n\nREPRODUCIBLE RECORD\n" + self.serialized())


def evaluate_detector_session(archive_path, input_text):
    request, column = parse_detector_input(input_text)
    import numpy as np
    import pandas as pd
    from qnet_qkd.qkd.detection import QberThreshold

    from .catalog import open_optional_model

    detector = open_optional_model(MODEL_ID, archive_path=archive_path)
    frame = pd.DataFrame({name: column[name] for name in COLUMNS})
    frame["period"] = frame["period"].astype(int)
    result = detector.classify_session(frame)
    features, _ = detector.session_features(frame)
    baseline = QberThreshold(sigma=AUTHOR_QBER_SIGMA).predict(features)
    alarms = result["alarm"].to_numpy()
    flagged = result.loc[result["alarm"] != "normal", "time_s"]
    baseline_times = result["time_s"].to_numpy()[baseline != "normal"]
    periods = tuple({"period": int(row.period), "time_s": float(row.time_s), "argmax": str(row.argmax),
                     "attack_probability": float(row.attack_probability), "per_period": str(row.per_period),
                     "alarm": str(row.alarm), "qber_alarm": bool(flag)}
                    for row, flag in zip(result.itertuples(index=False), baseline != "normal"))
    evaluation = DetectorEvaluation(
        datetime.now(timezone.utc).isoformat(), hashlib.sha256(input_text.encode("utf-8")).hexdigest(),
        request["stream_id"], detector.archive_sha256,
        detector.archive_member_sha256, detector.export.npz_sha256, float(column["length_km"][0]),
        len(request["rows"]), len(result), None if flagged.empty else float(flagged.iloc[0]),
        {label: int(np.sum(alarms == label)) for label in sorted(set(alarms)) if label != "normal"},
        None if len(baseline_times) == 0 else float(baseline_times[0]), int(len(baseline_times)), periods)
    evaluation.serialized()  # finite, JSON-safe before it is shown or exported
    return evaluation


def export_detector_evaluation(result, path):
    raw = result.serialized().encode("utf-8")
    with Path(path).open("xb") as stream:
        stream.write(raw)
