"""Traceable photon acquisitions and analysis; no hardware fidelity is implied by emulation."""
from __future__ import annotations

import bisect
import hashlib
import json
import math
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

SCHEMA = "orion-q.photon-acquisition.v1"
MODEL_ID = "orion-q.spdc-poisson-loss-jitter-deadtime.v1"


class AcquisitionError(ValueError):
    """A violated acquisition, configuration, or provenance contract."""


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _finite(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise AcquisitionError(f"{name} must be a finite number")
    return float(value)


@dataclass(frozen=True)
class PhotonConfig:
    """SI magnitudes are named explicitly. Timestamp arithmetic always uses integer ps."""

    seed: int = 810
    source_model: str = "Thorlabs SPDC810N (technical reference only)"
    source_transport: str = "USB"
    wavelength_nm: float = 810.0
    bandwidth_nm: float = 0.25
    detector_band_nm: tuple[float, float] = (780.0, 850.0)
    optical_connector: str = "FC/PC"
    detector_connector: str = "FC/PC"
    pair_rate_hz: float = 40_000.0
    duration_ps: int = 100_000_000_000
    channels: tuple[int, int] = (1, 2)
    trigger_levels_v: tuple[float, float] = (0.35, 0.35)
    input_delays_ps: tuple[int, int] = (0, -125)
    physical_delays_ps: tuple[int, int] = (0, 125)
    efficiency: tuple[float, float] = (0.70, 0.70)
    loss_db: tuple[float, float] = (3.0, 3.0)
    background_hz: tuple[float, float] = (200.0, 200.0)
    jitter_sigma_ps: tuple[float, float] = (60.0, 60.0)
    deadtime_ps: tuple[int, int] = (30_000, 30_000)
    coincidence_window_ps: int = 500
    histogram_bin_ps: int = 50
    histogram_radius_ps: int = 2_000
    buffer_capacity: int = 100_000
    pulse_level_v: float = 0.75
    pulse_width_ps: int = 2_000
    input_impedance_ohm: int = 50
    conditioning: str = "amplified analog pulse; TTL converter optional and requires level verification"
    clock_domain: str = "emulator-clock-810"
    timestamp_unit: str = "ps"
    clock_epoch_utc: str | None = None
    reference_channel: int | None = None
    reference_frequency_hz: float = 10_000_000.0
    clock_tolerance_ppm: float = 10.0

    @property
    def configuration_id(self) -> str:
        return digest(asdict(self))

    def validate(self) -> None:
        if type(self.seed) is not int:
            raise AcquisitionError("seed must be an integer")
        if self.timestamp_unit != "ps":
            raise AcquisitionError("timestamp_unit must be ps; unit conversion must be explicit")
        if self.source_transport not in {"USB", "RS232", "emulation"}:
            raise AcquisitionError("source transport is not documented for the selected SPDC reference")
        if "SPDC810N" in self.source_model and self.source_transport != "USB":
            raise AcquisitionError("SPDC810N control reference is USB")
        if "SPDC810" in self.source_model and "SPDC810N" not in self.source_model and self.source_transport != "RS232":
            raise AcquisitionError("SPDC810 control reference is RS232")
        if self.optical_connector != self.detector_connector:
            raise AcquisitionError("optical connector mismatch; an identified coupling adapter is required")
        if not self.clock_domain or self.clock_epoch_utc is not None:
            raise AcquisitionError("this model has a relative clock; UTC requires a measured alignment")
        for name in ("duration_ps", "coincidence_window_ps", "histogram_bin_ps", "histogram_radius_ps",
                     "buffer_capacity", "pulse_width_ps", "input_impedance_ohm"):
            if type(getattr(self, name)) is not int or getattr(self, name) <= 0:
                raise AcquisitionError(f"{name} must be a positive integer")
        if self.duration_ps > 60_000_000_000_000:
            raise AcquisitionError("this bounded demonstrator accepts acquisitions up to 60 seconds")
        if self.histogram_radius_ps % self.histogram_bin_ps:
            raise AcquisitionError("histogram radius must be a multiple of histogram bin")
        if len(self.channels) != 2 or any(type(c) is not int or c <= 0 for c in self.channels):
            raise AcquisitionError("exactly two distinct positive physical channels are required")
        if len(set(self.channels)) != 2 or self.reference_channel in self.channels:
            raise AcquisitionError("duplicate signal channels or a reused reference channel")
        for name in ("trigger_levels_v", "input_delays_ps", "physical_delays_ps", "efficiency",
                     "loss_db", "background_hz", "jitter_sigma_ps", "deadtime_ps"):
            if len(getattr(self, name)) != 2:
                raise AcquisitionError(f"{name} must contain one value per signal channel")
        for name in ("wavelength_nm", "bandwidth_nm", "pair_rate_hz", "pulse_level_v",
                     "reference_frequency_hz", "clock_tolerance_ppm"):
            if _finite(getattr(self, name), name) <= 0:
                raise AcquisitionError(f"{name} must be positive")
        lo, hi = self.detector_band_nm
        if not (0 < _finite(lo, "detector band") < _finite(hi, "detector band")):
            raise AcquisitionError("invalid detector spectral interval")
        if self.wavelength_nm - self.bandwidth_nm / 2 < lo or self.wavelength_nm + self.bandwidth_nm / 2 > hi:
            raise AcquisitionError("source spectrum lies outside the configured detector band")
        if not (0.1 <= self.pulse_level_v <= 3.0) or self.pulse_width_ps < 500 or self.input_impedance_ohm != 50:
            raise AcquisitionError("pulse fails the Time Tagger Ultra reference electrical contract")
        for trigger in self.trigger_levels_v:
            if not (0 < _finite(trigger, "trigger voltage") < self.pulse_level_v):
                raise AcquisitionError("trigger must be inside the positive pulse amplitude")
        for name in ("input_delays_ps", "physical_delays_ps", "deadtime_ps"):
            if any(type(n) is not int for n in getattr(self, name)):
                raise AcquisitionError(f"{name} must use integer picoseconds")
        if any(n < 0 for n in self.deadtime_ps):
            raise AcquisitionError("dead time must be non-negative")
        if any(not 0 <= _finite(x, "efficiency") <= 1 for x in self.efficiency):
            raise AcquisitionError("efficiency must be in [0,1]")
        for name in ("loss_db", "background_hz", "jitter_sigma_ps"):
            if any(_finite(n, name) < 0 for n in getattr(self, name)):
                raise AcquisitionError(f"{name} must be non-negative")
        if self.reference_channel is not None and (type(self.reference_channel) is not int or self.reference_channel <= 0):
            raise AcquisitionError("reference_channel must be a positive physical channel")
        if (self.pair_rate_hz + sum(self.background_hz)) * self.duration_ps / 1e12 > 1_000_000:
            raise AcquisitionError("demonstrator event budget exceeded; use streaming for large acquisitions")


@dataclass(frozen=True)
class TimeTag:
    timestamp_ps: int
    channel: int
    experiment_id: str
    device_id: str
    configuration_id: str
    clock_domain: str
    origin: str
    source_id: str
    event_type: str = "TimeTag"
    missed_events: int = 0

    def validate(self) -> None:
        if type(self.timestamp_ps) is not int or not 0 <= self.timestamp_ps < 2**63:
            raise AcquisitionError("timestamp_ps must be an integer in the signed 64-bit relative domain")
        if type(self.channel) is not int:
            raise AcquisitionError("channel must be an integer")
        if type(self.missed_events) is not int or self.missed_events < 0:
            raise AcquisitionError("missed_events must be a non-negative integer")
        if self.event_type not in {"TimeTag", "Error", "OverflowBegin", "OverflowEnd", "MissedEvents"}:
            raise AcquisitionError("unknown event_type")
        if self.origin not in {"simulated", "physical"}:
            raise AcquisitionError("origin describes original acquisition, never the replay transport")
        if not all((self.experiment_id, self.device_id, self.configuration_id, self.clock_domain, self.source_id)):
            raise AcquisitionError("event provenance is incomplete")


@dataclass
class Acquisition:
    experiment_id: str
    device_id: str
    adapter_id: str
    mode: str
    origin: str
    configuration: dict[str, Any]
    events: list[TimeTag]
    duration_ps: int
    clock_domain: str
    source_id: str
    clock_locked: bool = True
    diagnostics: dict[str, Any] = field(default_factory=dict)
    source_record: str | None = None

    def validate(self) -> None:
        if type(self.clock_locked) is not bool:
            raise AcquisitionError("clock_locked must be a boolean")
        if self.mode not in {"hardware", "replay", "emulation"}:
            raise AcquisitionError("unknown acquisition mode")
        if self.origin not in {"simulated", "physical"}:
            raise AcquisitionError("missing original data origin")
        if self.mode == "emulation" and self.origin != "simulated":
            raise AcquisitionError("emulation cannot claim physical origin")
        if self.mode == "hardware" and self.origin != "physical":
            raise AcquisitionError("hardware acquisitions must have physical origin")
        if self.mode == "replay" and not self.source_record:
            raise AcquisitionError("replay requires an identified source record")
        if type(self.duration_ps) is not int or self.duration_ps <= 0:
            raise AcquisitionError("duration must be integer ps")
        PhotonConfig(**self.configuration).validate()
        config_id = digest(self.configuration)
        channels = self.configuration.get("channels", [])
        capture_start = self.diagnostics.get("capture_start_ps", 0)
        if type(capture_start) is not int or capture_start < 0:
            raise AcquisitionError("capture start must be integer nonnegative ps")
        previous = -1
        for event in self.events:
            event.validate()
            if event.timestamp_ps < previous:
                raise AcquisitionError("timestamp regression; data is rejected rather than sorted")
            previous = event.timestamp_ps
            if event.channel not in channels and (event.channel != 0 or event.event_type == "TimeTag"):
                raise AcquisitionError("event channel is not in the applied configuration")
            if (event.experiment_id != self.experiment_id or event.device_id != self.device_id
                    or event.configuration_id != config_id or event.clock_domain != self.clock_domain
                    or event.origin != self.origin or event.source_id != self.source_id):
                raise AcquisitionError("event provenance does not match the acquisition")
            if self.origin == "simulated" and not capture_start <= event.timestamp_ps < capture_start + self.duration_ps:
                raise AcquisitionError("simulated event is outside the recorded acquisition interval")

    @property
    def evidence_level(self) -> str:
        return {"emulation": "physical_model", "hardware": "hardware",
                "replay": "experimental_replay" if self.origin == "physical" else "simulation_replay"}[self.mode]


def analyze(acquisition: Acquisition) -> dict[str, Any]:
    """Count all cross-channel pairs in a symmetric window, without ground-truth pair IDs."""
    acquisition.validate()
    cfg = acquisition.configuration
    ch_a, ch_b = cfg["channels"]
    timestamps = {c: [e.timestamp_ps for e in acquisition.events
                      if e.channel == c and e.event_type == "TimeTag"] for c in (ch_a, ch_b)}
    left, right = timestamps[ch_a], timestamps[ch_b]
    window = cfg["coincidence_window_ps"]
    # W is the TOTAL window width. For odd W use the half-open interval [-W//2, W-W//2).
    lower, upper = -(window // 2), window - window // 2
    coincidences = sum(bisect.bisect_left(right, t + upper) - bisect.bisect_left(right, t + lower) for t in left)
    radius, binwidth = cfg["histogram_radius_ps"], cfg["histogram_bin_ps"]
    histogram = [0] * (2 * radius // binwidth)
    for t in left:
        for other in right[bisect.bisect_left(right, t - radius):bisect.bisect_left(right, t + radius)]:
            histogram[(other - t + radius) // binwidth] += 1
    shift = max(radius * 10, window * 20)
    # The delayed window only uses starts whose whole shifted window is observed.
    capture_start = acquisition.diagnostics.get("capture_start_ps", 0)
    if type(capture_start) is not int or capture_start < 0:
        raise AcquisitionError("capture_start_ps must be a non-negative integer")
    capture_end = capture_start + acquisition.duration_ps
    valid_starts = [t for t in left if capture_start <= t + shift + lower and t + shift + upper <= capture_end]
    delayed = sum(bisect.bisect_left(right, t + shift + upper)
                  - bisect.bisect_left(right, t + shift + lower) for t in valid_starts)
    exposure_s = acquisition.duration_ps / 1e12
    rate_a, rate_b = len(left) / exposure_s, len(right) / exposure_s
    missed = sum(e.missed_events for e in acquisition.events if e.event_type == "MissedEvents")
    flags = sorted(set(acquisition.diagnostics.get("quality_flags", []))
                   | ({"overflow"} if any(e.event_type in {"OverflowBegin", "MissedEvents"} for e in acquisition.events) else set())
                   | ({"clock_unlocked"} if not acquisition.clock_locked else set()))
    complete = not any(flag in flags for flag in ("overflow", "stream_buffer_saturated", "disconnected",
                                                  "clock_unlocked", "clock_drift", "detector_unavailable", "native_error"))
    return {
        "schema": "orion-q.photon-analysis.v1", "experiment_id": acquisition.experiment_id,
        "adapter_id": acquisition.adapter_id, "mode": acquisition.mode, "origin": acquisition.origin,
        "evidence_level": acquisition.evidence_level, "configuration_id": digest(cfg),
        "duration_ps": acquisition.duration_ps, "clock_domain": acquisition.clock_domain,
        "counts": {str(ch_a): len(left), str(ch_b): len(right)},
        "count_rates_hz": {str(ch_a): rate_a, str(ch_b): rate_b},
        "coincidences": coincidences, "coincidence_rate_hz": coincidences / exposure_s,
        "coincidence_window_total_width_ps": window,
        "coincidence_interval_ps": [lower, upper],
        "histogram": {"bin_width_ps": binwidth, "edges_ps": list(range(-radius, radius + binwidth, binwidth)),
                      "counts": histogram, "difference": "channel_b - channel_a"},
        "accidentals": {"poisson_estimate": rate_a * rate_b * window / 1e12 * exposure_s,
                        "method": "independent singles Poisson estimate; same-window width",
                        "delayed_window_count": delayed, "delayed_window_shift_ps": shift,
                        "delayed_window_valid_starts": len(valid_starts)},
        "missed_events": missed, "quality_flags": flags, "complete": complete,
        "entanglement_claim": False,
        "characterization_requirement": "state preparation and analyzers plus tomography or a defined entanglement witness",
    }


def store_acquisition(acquisition: Acquisition, directory: Path | str, name: str = "acquisition") -> Path:
    acquisition.validate()
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    if Path(name).name != name:
        raise AcquisitionError("record name must be a plain filename")
    events_path = root / f"{name}.events.jsonl"
    payload = "".join(canonical_json(asdict(event)) + "\n" for event in acquisition.events).encode("utf-8")
    events_path.write_bytes(payload)
    manifest = {key: value for key, value in asdict(acquisition).items() if key != "events"}
    manifest.update({"schema": SCHEMA, "events_file": events_path.name,
                     "events_sha256": hashlib.sha256(payload).hexdigest(), "event_count": len(acquisition.events),
                     "configuration_id": digest(acquisition.configuration),
                     "evidence_level": acquisition.evidence_level})
    manifest["manifest_sha256"] = digest(manifest)
    manifest_path = root / f"{name}.manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    return manifest_path


def load_acquisition(manifest_path: Path | str) -> Acquisition:
    path = Path(manifest_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != SCHEMA:
        raise AcquisitionError("unrecognized replay schema; this is not a native Swabian .ttbin")
    expected = data.pop("manifest_sha256", None)
    if expected != digest(data):
        raise AcquisitionError("manifest integrity check failed")
    events_file = data.get("events_file")
    if not isinstance(events_file, str) or Path(events_file).name != events_file:
        raise AcquisitionError("events_file must be a local plain filename")
    events_path = path.parent / events_file
    # Avoid a symlink carrying replay data outside the record's folder.
    if events_path.resolve().parent != path.parent.resolve():
        raise AcquisitionError("replay events escaped the record directory")
    payload = events_path.read_bytes()
    if hashlib.sha256(payload).hexdigest() != data["events_sha256"]:
        raise AcquisitionError("replay event integrity check failed")
    events = [TimeTag(**json.loads(line)) for line in payload.decode("utf-8").splitlines() if line]
    if len(events) != data["event_count"] or digest(data["configuration"]) != data["configuration_id"]:
        raise AcquisitionError("replay count or configuration integrity check failed")
    fields = set(Acquisition.__dataclass_fields__) - {"events"}
    result = Acquisition(**{key: data[key] for key in fields}, events=events)
    result.validate()
    return result


def run_registered_acquisition(output_path: Path | str, *, config: PhotonConfig, adapter_id: str,
                               manifest_path: Path | str | None = None, security=None,
                               principal=None, authority=None) -> dict[str, Any]:
    """Common service journey for interchangeable local emulation and original replay.

    The public laboratory service resolves a receipt-bound replay UUID to a local
    manifest; this low-level function is deliberately not an HTTP file-path API.
    """
    from .adapter_registry import create_photon_adapter, validate_adapter_configuration
    if adapter_id not in {"emulated", "emulated-reference", "orion-replay"}:
        raise AcquisitionError("registered laboratory acquisition does not enable hardware control")
    config.validate()
    options = {"manifest_path": manifest_path} if adapter_id == "orion-replay" else {}
    adapter = create_photon_adapter(adapter_id, options, security=security, principal=principal, authority=authority)
    root = Path(output_path)
    root.mkdir(parents=True, exist_ok=True)
    journey = [{"action": "register", "device": adapter.identify()}]
    try:
        adapter.connect()
        journey.append({"action": "connect", "state": adapter.state})
        validate_adapter_configuration(adapter, config)
        adapter.configure(config)
        journey.append({"action": "configure", "configuration_id": config.configuration_id, "state": adapter.state})
        calibration = adapter.calibrate()
        journey.append({"action": "calibrate", "result": calibration, "state": adapter.state})
        record = adapter.acquire(f"registered-acquisition:{root.name}")
        record.validate()
        stored = store_acquisition(record, root, "nominal")
        metrics = analyze(record)
        journey.extend([{"action": "acquire", "state": adapter.state, "event_count": len(record.events)},
                        {"action": "store", "manifest": str(stored.resolve())},
                        {"action": "analyze", "complete": metrics["complete"]}])
        result = {"schema": "orion-q.registered-photon-acquisition.v1", "mode": record.mode,
                  "origin": record.origin, "adapter_selection": adapter_id, "devices": [adapter.identify()],
                  "journey": journey, "configuration": record.configuration, "calibration": calibration,
                  "nominal": metrics, "artifacts": {"nominal": str(stored.resolve())}, "passed": metrics["complete"],
                  "hardware_validated": False, "experimental_replay_validated": False,
                  "original_experiment_id": record.experiment_id, "replay_run_id": record.diagnostics.get("replay_run_id"),
                  "limit": "Recorded events and applied configuration are preserved; replay does not measure a connected device."}
        (root / "photon_summary.json").write_text(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")
        return result
    finally:
        adapter.disconnect()


def run_photon_demo(output_path: Path | str, *, config: PhotonConfig | None = None, adapter_id: str = "emulated",
                    security=None, principal=None, authority=None) -> dict[str, Any]:
    """Execute registration -> connection -> configuration -> calibration -> acquisition -> replay."""
    from .adapters import EmulatedPhotonAdapter, ReplayPhotonAdapter, SwabianSDKAdapter

    root = Path(output_path)
    root.mkdir(parents=True, exist_ok=True)
    from .adapter_registry import create_photon_adapter
    config = config or PhotonConfig()
    config.validate()
    if adapter_id not in {"emulated", "emulated-reference"}:
        raise AcquisitionError("public demonstration permits identified emulated sources only")
    adapter = create_photon_adapter(adapter_id, security=security, principal=principal, authority=authority)
    journey: list[dict[str, Any]] = [{"action": "register", "device": adapter.identify()}]
    adapter.connect()
    journey.append({"action": "connect", "state": adapter.state})
    adapter.configure(config)
    journey.append({"action": "configure", "configuration_id": config.configuration_id, "state": adapter.state})
    calibration = adapter.calibrate()
    journey.append({"action": "calibrate", "result": calibration, "state": adapter.state})
    nominal = adapter.acquire("photon-nominal")
    manifest = store_acquisition(nominal, root, "nominal")
    nominal_metrics = analyze(nominal)
    journey.extend([{"action": "acquire", "state": adapter.state, "event_count": len(nominal.events)},
                    {"action": "store", "manifest": str(manifest)},
                    {"action": "analyze", "complete": nominal_metrics["complete"]}])
    replay_adapter = create_photon_adapter("orion-replay", {"manifest_path": manifest},
                security=security, principal=principal, authority=authority)
    replay_adapter.connect()
    replay_adapter.configure(config)
    replay_adapter.calibrate()
    replay = replay_adapter.acquire("photon-replay")
    replay_metrics = analyze(replay)
    replay_manifest = store_acquisition(replay, root, "replay")
    # Replay preserves the source experiment ID, device ID, configuration, clock and timestamps.
    comparable = ("counts", "coincidences", "histogram", "accidentals", "duration_ps", "clock_domain")
    replay_equal = all(nominal_metrics[k] == replay_metrics[k] for k in comparable)
    alternate = create_photon_adapter("emulated-reference", {"device_id": "tagger-emulator-b"},
                security=security, principal=principal, authority=authority)
    alternate_config = replace(config, source_model="Thorlabs SPDC810 (technical reference only)",
                               source_transport="RS232", bandwidth_nm=10.0, channels=(3, 4),
                               clock_domain="emulator-clock-alternate")
    alternate.connect()
    alternate.configure(alternate_config)
    alternate.calibrate()
    replacement = alternate.acquire("photon-replacement")
    replacement_metrics = analyze(replacement)
    replacement_manifest = store_acquisition(replacement, root, "replacement")
    failures: list[dict[str, Any]] = []
    for fault in ("overflow", "desync", "clock_drift", "disconnect", "detector_unavailable", "loss", "timestamp_regression"):
        probe = create_photon_adapter("emulated", {"device_id": f"tagger-fault-{fault}"},
                security=security, principal=principal, authority=authority)
        probe.connect()
        probe.configure(config)
        probe.calibrate()
        damaged = probe.acquire(f"photon-fault-{fault}", fault=fault)
        try:
            metrics = analyze(damaged)
            fault_path = store_acquisition(damaged, root, f"fault-{fault}")
            detected = (metrics["coincidences"] < nominal_metrics["coincidences"] / 10 if fault == "loss"
                        else not metrics["complete"])
            failures.append({"fault": fault, "detected": detected, "metrics": metrics, "manifest": str(fault_path)})
        except AcquisitionError as exc:
            # Preserve rejected raw events for forensic reproduction, clearly marked outside accepted schema.
            raw_path = root / f"rejected-{fault}.json"
            raw_path.write_text(json.dumps({"schema": "orion-q.rejected-acquisition.v1",
                                           "reason": str(exc), "raw": asdict(damaged)}, indent=2), encoding="utf-8")
            failures.append({"fault": fault, "detected": True, "rejected": True,
                             "reason": str(exc), "evidence": str(raw_path)})
        probe.disconnect()
        probe.connect()
        probe.configure(config)
        probe.calibrate()
        recovered = probe.acquire(f"photon-recovered-{fault}")
        recovery_metrics = analyze(recovered)
        failures[-1]["recovery_complete"] = recovery_metrics["complete"]
        failures[-1]["recovery_manifest"] = str(store_acquisition(recovered, root, f"recovered-{fault}"))
        failures[-1]["recovery_metrics"] = recovery_metrics
    sdk = SwabianSDKAdapter(mode="hardware")
    sdk_available = sdk.availability()
    result = {
        "schema": "orion-q.photon-demo.v1", "mode": "emulation", "origin": "simulated",
        "model_id": MODEL_ID, "validation_level": "physical_model_and_simulation_replay",
        "hardware_validated": False, "experimental_replay_validated": False,
        "devices": [adapter.identify(), replay_adapter.identify(), alternate.identify()],
        "bench_devices": [
            {"device_id": "source-emulator-810n", "role": "pair_source", "mode": "emulation",
             "reference": config.source_model, "control_transport_reference": config.source_transport,
             "hardware_connected": False},
            {"device_id": "detector-emulator-a", "role": "detector", "mode": "emulation",
             "spectral_band_nm": config.detector_band_nm, "tagger_channel": config.channels[0],
             "control_transport": "model only", "acquisition_transport": "modeled electrical pulse",
             "hardware_connected": False},
            {"device_id": "detector-emulator-b", "role": "detector", "mode": "emulation",
             "spectral_band_nm": config.detector_band_nm, "tagger_channel": config.channels[1],
             "control_transport": "model only", "acquisition_transport": "modeled electrical pulse",
             "hardware_connected": False},
            adapter.identify(),
        ],
        "journey": journey, "configuration": asdict(config), "calibration": calibration,
        "nominal": nominal_metrics, "replay": replay_metrics,
        "substitution": {"emulation_to_replay_equal": replay_equal, "native_ttbin_used": False,
                         "alternate_adapter": replacement_metrics,
                         "requires": ["adapter selection", "documented source transport", "channel map",
                                      "spectral/electrical validation", "recalibration"]},
        "faults": failures, "swabian_sdk": sdk_available,
        "artifacts": {"nominal": str(manifest), "replay": str(replay_manifest),
                      "replacement": str(replacement_manifest)},
        "limits": ["No physical source, detector, or time tagger was acquired by this demo.",
                   "Synthetic Poisson pairs with loss, jitter, background and nonparalyzable dead time.",
                   "No entanglement characterization and no proprietary commands.",
                   "JSONL replay of synthetic data does not validate Swabian virtual replay or its license."],
        "passed": replay_equal and all(f["detected"] and f["recovery_complete"] for f in failures),
    }
    record_root = Path(__file__).resolve().parents[3] / "data/raw/bristol-hcf-coex/61193flfovbn2puknndsr3qm5"
    aggregate_replay = replay_experimental_aggregates(record_root, root)
    result["experimental_aggregate_replay"] = aggregate_replay
    result["experimental_aggregate_replay_validated"] = aggregate_replay["available"]
    from .capture import run_capture_demo
    result["continuous_capture"] = run_capture_demo(root / "continuous-capture", configuration=config)
    result["passed"] &= result["continuous_capture"]["passed"]
    (root / "photon_summary.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    return result



def replay_experimental_aggregates(record_root: Path | str, output_path: Path | str) -> dict[str, Any]:
    """Reproduce deposited physical aggregates without inventing missing raw time tags."""
    import csv

    source = Path(record_root)
    metadata_path, readme_path = source / ".info.json", source / "readme.txt"
    if not metadata_path.is_file() or not readme_path.is_file():
        return {"available": False, "mode": "replay", "level": "experimental_aggregate",
                "reason": "Bristol experimental dataset is not present"}
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    entries: dict[str, dict[str, Any]] = {}

    def collect(value: Any) -> None:
        if isinstance(value, dict):
            if "id" in value and "sha1" in value:
                entries[value["id"]] = value
            for item in value.values():
                collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)

    collect(metadata)
    files: list[dict[str, Any]] = []

    def verify(path: Path) -> bytes:
        payload = path.read_bytes()
        relative = f"{source.name}/{path.relative_to(source).as_posix()}"
        entry = entries.get(relative)
        if entry is None:
            raise AcquisitionError(f"original dataset metadata has no entry for {relative}")
        original_size = int(entry["extent"]["@value"])
        sha1 = hashlib.sha1(payload).hexdigest()
        if len(payload) != original_size or sha1 != entry["sha1"]:
            raise AcquisitionError(f"experimental source integrity failed for {relative}")
        files.append({"path": str(path.resolve()), "relative_path": relative, "bytes": len(payload),
                      "sha256": hashlib.sha256(payload).hexdigest(), "original_sha1": sha1,
                      "matches_deposit_metadata": True})
        return payload

    readme = verify(readme_path).decode("utf-8")
    if "Photon Spot" not in readme or "header file for the time tag data" not in readme:
        raise AcquisitionError("original acquisition provenance or native header scope is unconfirmed")
    summary_path = source / "3UserNetworkData/summary_20220627_1236_3U_CoEx_180mins.csv"
    summary_rows = []
    for row in csv.DictReader(verify(summary_path).decode("utf-8").splitlines()):
        parsed = {key: _finite(float(value), key) for key, value in row.items() if key}
        if not parsed["chunk"].is_integer():
            raise AcquisitionError("experimental chunk number must be integral")
        parsed["chunk"] = int(parsed["chunk"])
        for link in ("AB", "AC", "BC"):
            if not 0 <= parsed[f"QBER {link}"] <= 1:
                raise AcquisitionError("reported experimental QBER is not a probability")
        summary_rows.append(parsed)
    offsets = [row["Chunk Time"] for row in summary_rows]
    if any(b <= a for a, b in zip(offsets, offsets[1:])):
        raise AcquisitionError("experimental chunk reference times must increase")
    histograms = {}
    for names, link in (("Alice_Bob", "AB"), ("Alice_Chloe", "AC"), ("Bob_Chloe", "BC")):
        path = source / f"3UserNetworkData/Histograms_{names}_20220627_1236_3U_CoEx_180mins.csv"
        rows = list(csv.DictReader(verify(path).decode("utf-8").splitlines()))
        x = [int(row["x"]) for row in rows]
        main = [_finite(float(row["main"]), "main histogram rate") for row in rows]
        error = [_finite(float(row["err"]), "error histogram rate") for row in rows]
        if any(b - a != 10 for a, b in zip(x, x[1:])) or any(v < 0 for v in main + error):
            raise AcquisitionError("original experimental histogram units/bins are inconsistent")
        peak = max(range(len(main)), key=main.__getitem__)
        histograms[link] = {"x_ps": x, "main_rate_hz_per_10ps_bin": main, "err_rate_hz_per_10ps_bin": error,
                            "peak_delay_ps": x[peak], "main_rate_sum_hz": sum(main), "err_rate_sum_hz": sum(error),
                            "source_file": str(path.resolve()), "source_chunk": 0}
    header = source / "3UserNetworkData/20220627_1236_3U_CoEx_180mins/3U_CoEx_180mins_20220627_1236.ttbin"
    verify(header)
    result = {
        "schema": "orion-q.experimental-aggregate-replay.v1", "available": True, "mode": "replay",
        "origin": "physical", "level": "experimental_aggregate", "experiment_id": "bristol-hcf-20220627-1236",
        "source_doi": metadata.get("doi"), "source_title": metadata.get("title"),
        "source_files": files,
        "source_devices": [{"manufacturer": "Photon Spot", "role": "SNSPD detection", "model": "not specified in readme",
                            "mode": "replay", "hardware_connected_here": False},
                           {"manufacturer": "Swabian Instruments", "model": "Time Tagger Ultra",
                            "role": "original acquisition", "mode": "replay", "hardware_connected_here": False}],
        "time_reference": {"acquisition_label": "20220627_1236", "timezone": None,
                           "chunk_interval_s_from_readme": 600,
                           "chunk_time_column_original": offsets,
                           "warning": "README calls Chunk Time duration, but values form elapsed offsets; no UTC epoch inferred"},
        "units": {"counts": "reported photon rate / s", "coincidences": "reported sifted coincidence rate / s",
                  "qber": "reported probability", "skr": "published estimate bit / s",
                  "histogram_x": "integer picoseconds", "histogram_rates": "coincidences / s / 10 ps bin"},
        "reported_summary": summary_rows, "histograms": histograms,
        "raw_native_replay_available": False, "native_file_is_header_only": True,
        "native_stream_bytes_if_saved_from_readme": "21.6 GB", "secret_material_generated": False,
        "certified_secret_bits": 0, "hardware_validated_here": False,
        "limits": ["Original records are aggregates; individual clicks and missed events are not recoverable.",
                   "Published SKR/QBER are reproduced, not independently certified or regenerated keys.",
                   "Native .ttbin header matches the deposit; the raw time-tag files were not deposited.",
                   "The original detectors are Photon Spot SNSPDs; this is not Single Quantum hardware validation."],
    }
    output = Path(output_path)
    output.mkdir(parents=True, exist_ok=True)
    artifact = output / "experimental_aggregate_replay.json"
    result["artifact"] = str(artifact.resolve())
    artifact.write_text(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    return result

