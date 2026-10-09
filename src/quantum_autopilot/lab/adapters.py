"""Device adapters with explicit transport, mode and source provenance."""
from __future__ import annotations

import importlib
import importlib.util
import math
import operator
import uuid
import random
import threading
from contextlib import contextmanager
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, Protocol

from .photon import Acquisition, AcquisitionError, MODEL_ID, PhotonConfig, TimeTag, digest, load_acquisition


class AdapterUnavailable(RuntimeError):
    """A documented SDK, licensed replay, identity, or endpoint is unavailable."""


class PhotonAdapter(Protocol):
    adapter_id: str
    state: str

    def identify(self) -> dict[str, Any]: ...
    def connect(self) -> dict[str, Any]: ...
    def configure(self, configuration: PhotonConfig) -> dict[str, Any]: ...
    def calibrate(self) -> dict[str, Any]: ...
    def acquire(self, experiment_id: str, *, fault: str | None = None) -> Acquisition: ...
    def disconnect(self) -> None: ...


class EmulatedPhotonAdapter:
    """Poisson SPDC model, two independent lossy arms and nonparalyzable detector dead times."""

    def __init__(self, device_id: str = "tagger-emulator"):
        if not device_id:
            raise AcquisitionError("device_id is required")
        self.device_id = device_id
        self.adapter_id = "orion-q.emulated-photon.v1"
        self.state = "registered"
        self.configuration: PhotonConfig | None = None
        self._lock = threading.Lock()

    def identify(self) -> dict[str, Any]:
        return {"device_id": self.device_id, "adapter_id": self.adapter_id, "mode": "emulation",
                "origin": "simulated", "identity_verified": False, "hardware_connected": False,
                "model_id": MODEL_ID, "transport": "in-process Python model",
                "roles": ["SPDC source model", "optical channels model", "detector pulse model", "tagger model"],
                "capabilities": ["integer_ps", "configuration_validation", "coincidences", "fault_injection"],
                "references": ["Thorlabs SPDC810/SPDC810N", "Single Quantum SNSPD amplified pulses",
                               "Swabian Time Tagger Ultra input contract"],
                "state": self.state}

    def connect(self) -> dict[str, Any]:
        with self._lock:
            if self.state not in {"registered", "disconnected", "fault"}:
                raise AcquisitionError(f"cannot connect from {self.state}")
            self.configuration = None
            self.state = "connected"
        return self.identify()

    def configure(self, configuration: PhotonConfig) -> dict[str, Any]:
        configuration.validate()
        with self._lock:
            if self.state not in {"connected", "configured", "ready"}:
                raise AcquisitionError(f"cannot configure from {self.state}")
            self.configuration = configuration
            self.state = "configured"
        return {"configuration_id": configuration.configuration_id, "applied": asdict(configuration)}

    def calibrate(self) -> dict[str, Any]:
        with self._lock:
            if self.state != "configured" or self.configuration is None:
                raise AcquisitionError("configure before calibration")
            cfg = self.configuration
            residual = (cfg.physical_delays_ps[1] + cfg.input_delays_ps[1]
                        - cfg.physical_delays_ps[0] - cfg.input_delays_ps[0])
            self.state = "ready"
        return {"mode": "emulation", "origin": "simulated", "method": "known model offsets",
                "residual_delay_ps": residual, "configuration_id": cfg.configuration_id,
                "hardware_calibration_validated": False}

    def disconnect(self) -> None:
        with self._lock:
            if self.state == "acquiring":
                raise AcquisitionError("acquisition in progress; use acquisition cancellation at a streaming boundary")
            self.state = "disconnected"
            self.configuration = None

    def acquire(self, experiment_id: str, *, fault: str | None = None) -> Acquisition:
        if fault not in {None, "overflow", "desync", "clock_drift", "disconnect",
                         "detector_unavailable", "loss", "timestamp_regression"}:
            raise AcquisitionError("unknown fault scenario")
        if not experiment_id:
            raise AcquisitionError("experiment_id is required")
        with self._lock:
            if self.state != "ready" or self.configuration is None:
                raise AcquisitionError("connected, configured and calibrated adapter required before acquisition")
            cfg = self.configuration
            self.state = "acquiring"
        try:
            result = self._generate(experiment_id, cfg, fault)
        except Exception:
            with self._lock:
                self.state = "fault"
            raise
        with self._lock:
            self.state = "disconnected" if fault == "disconnect" else ("fault" if fault in {"desync", "clock_drift"} else "ready")
        return result

    def _generate(self, experiment_id: str, cfg: PhotonConfig, fault: str | None) -> Acquisition:
        rng = random.Random(cfg.seed)
        source_id = f"{MODEL_ID};seed={cfg.seed}"
        config_id = cfg.configuration_id
        candidates: list[tuple[int, int]] = []
        stop_ps = cfg.duration_ps // 2 if fault == "disconnect" else cfg.duration_ps
        probability = [eff * 10 ** (-loss / 10) for eff, loss in zip(cfg.efficiency, cfg.loss_db)]
        if fault == "loss":
            probability = [p / 1_000 for p in probability]
        if fault == "detector_unavailable":
            probability[1] = 0.0
        emission_ps = 0.0
        generated_pairs = 0
        while True:
            emission_ps += rng.expovariate(cfg.pair_rate_hz) * 1e12
            if emission_ps >= stop_ps:
                break
            generated_pairs += 1
            for arm, channel in enumerate(cfg.channels):
                if rng.random() < probability[arm]:
                    arrival_ps = round(emission_ps + cfg.physical_delays_ps[arm] + cfg.input_delays_ps[arm]
                                       + rng.gauss(0, cfg.jitter_sigma_ps[arm]))
                    if 0 <= arrival_ps < stop_ps:
                        candidates.append((arrival_ps, channel))
        for arm, channel in enumerate(cfg.channels):
            if fault == "detector_unavailable" and arm == 1:
                continue
            if cfg.background_hz[arm] == 0:
                continue
            background_ps = 0.0
            while True:
                background_ps += rng.expovariate(cfg.background_hz[arm]) * 1e12
                if background_ps >= stop_ps:
                    break
                candidates.append((round(background_ps), channel))
        candidates.sort()
        previous = {channel: -10**30 for channel in cfg.channels}
        deadtimes = dict(zip(cfg.channels, cfg.deadtime_ps))
        tags: list[TimeTag] = []
        deadtime_dropped = 0
        for timestamp, channel in candidates:
            if timestamp - previous[channel] < deadtimes[channel]:
                deadtime_dropped += 1
                continue
            previous[channel] = timestamp
            tags.append(TimeTag(timestamp, channel, experiment_id, self.device_id, config_id,
                                cfg.clock_domain, "simulated", source_id))
        flags: list[str] = []
        if fault == "clock_drift":
            tags = [replace(tag, timestamp_ps=min(stop_ps - 1, tag.timestamp_ps + round(tag.timestamp_ps * 50e-6)))
                    if tag.channel == cfg.channels[1] else tag for tag in tags]
            tags.sort(key=lambda tag: (tag.timestamp_ps, tag.channel))
            flags.append("clock_drift")
        if fault == "desync":
            flags.append("clock_unlocked")
        if fault == "disconnect":
            flags.append("disconnected")
        if fault == "detector_unavailable":
            flags.append("detector_unavailable")
        capacity = min(cfg.buffer_capacity, 20) if fault == "overflow" else cfg.buffer_capacity
        dropped = max(0, len(tags) - capacity)
        if dropped:
            retained = tags[:capacity]
            marker_time = retained[-1].timestamp_ps if retained else 0
            for kind, missed in (("OverflowBegin", 0), ("MissedEvents", dropped), ("OverflowEnd", 0)):
                retained.append(TimeTag(marker_time, 0, experiment_id, self.device_id, config_id,
                                        cfg.clock_domain, "simulated", source_id, kind, missed))
            tags = retained
            flags.append("overflow")
        if fault == "timestamp_regression" and len(tags) >= 2:
            tags[0], tags[1] = tags[1], tags[0]
        return Acquisition(experiment_id, self.device_id, self.adapter_id, "emulation", "simulated",
                           asdict(cfg), tags, cfg.duration_ps, cfg.clock_domain, source_id,
                           clock_locked=fault != "desync",
                           diagnostics={"model_id": MODEL_ID, "seed": cfg.seed, "generated_pairs": generated_pairs,
                                        "deadtime_dropped": deadtime_dropped, "buffer_dropped": dropped,
                                        "quality_flags": flags, "fault_injection": fault,
                                        "capture_start_ps": 0, "captured_duration_ps": stop_ps,
                                        "clock_drift_ppm": 50.0 if fault == "clock_drift" else 0.0,
                                        "assumptions": ["stationary Poisson pairs", "independent arm loss",
                                                        "Gaussian timing jitter", "Poisson background",
                                                        "nonparalyzable detector dead time"]})


class ReplayPhotonAdapter:
    """Replay ORION JSONL evidence. This class does not emulate the native .ttbin interface."""

    def __init__(self, manifest_path: str | Path):
        self.manifest_path = Path(manifest_path)
        self.adapter_id = "orion-q.jsonl-replay.v1"
        self.state = "registered"
        self.record: Acquisition | None = None

    def identify(self) -> dict[str, Any]:
        return {"adapter_id": self.adapter_id, "mode": "replay", "transport": "ORION JSONL + manifest",
                "source_record": str(self.manifest_path), "state": self.state,
                "device_id": self.record.device_id if self.record else None,
                "origin": self.record.origin if self.record else "not loaded",
                "hardware_connected": False, "identity_verified": False,
                "capabilities": ["integrity_hash", "integer_ps", "preserve_original_configuration_and_clock"]}

    def connect(self) -> dict[str, Any]:
        if self.state not in {"registered", "disconnected", "fault"}:
            raise AcquisitionError(f"cannot connect from {self.state}")
        self.record = load_acquisition(self.manifest_path)
        self.state = "connected"
        return self.identify()

    def configure(self, configuration: PhotonConfig) -> dict[str, Any]:
        configuration.validate()
        if self.state not in {"connected", "configured", "ready"} or self.record is None:
            raise AcquisitionError("connect the replay record before configuration")
        if configuration.configuration_id != digest(self.record.configuration):
            raise AcquisitionError("replay cannot rewrite the original acquisition configuration")
        self.state = "configured"
        return {"configuration_id": configuration.configuration_id, "original_configuration_preserved": True}

    def calibrate(self) -> dict[str, Any]:
        if self.state != "configured":
            raise AcquisitionError("configure before validating recorded calibration")
        self.state = "ready"
        return {"mode": "replay", "method": "preserve applied input delays; no physical recalibration"}

    def acquire(self, experiment_id: str, *, fault: str | None = None) -> Acquisition:
        if fault is not None:
            raise AcquisitionError("fault injection would change recorded evidence")
        if not experiment_id or self.state != "ready" or self.record is None:
            raise AcquisitionError("configured and calibrated replay required")
        self.state = "acquiring"
        result = replace(self.record, adapter_id=self.adapter_id, mode="replay",
                         source_record=str(self.manifest_path.resolve()),
                         events=list(self.record.events),
                         diagnostics={**self.record.diagnostics, "replay_run_id": experiment_id,
                                      "recorded_mode": self.record.mode})
        result.validate()
        self.state = "ready"
        return result

    def disconnect(self) -> None:
        self.record = None
        self.state = "disconnected"


class SwabianSDKAdapter:
    """Optional bridge to documented native SDK calls. Never falls back to emulation.

    Native enum mapping must come from the installed SDK version; no undocumented numeric
    event codes are guessed. A Time Tagger alone does not validate the rest of the optical bench.
    """

    def __init__(self, *, mode: str = "hardware", serial: str = "", address: str | None = None,
                 replay_file: str | Path | None = None, event_type_map: dict[int, str] | None = None,
                 expected_model: str = "Time Tagger Ultra", test_only_transport: bool = False):
        if mode not in {"hardware", "replay"}:
            raise AcquisitionError("Swabian adapter supports hardware or native replay only")
        if mode == "replay" and (not replay_file or address):
            raise AcquisitionError("native replay requires an identified FileWriter .ttbin record")
        self.mode, self.serial, self.address = mode, serial, address
        self.replay_file = Path(replay_file) if replay_file else None
        self.event_type_map = event_type_map
        self.expected_model = expected_model
        if type(test_only_transport) is not bool:
            raise AcquisitionError("test-only transport must be an explicit trusted code boolean")
        self._test_only_transport = test_only_transport
        self._security_guard = None
        self._clock_domain: str | None = None
        self.adapter_id = "orion-q.swabian-native.experimental.v1"
        self.state = "registered"
        self.configuration: PhotonConfig | None = None
        self.sdk: Any = None
        self.tagger: Any = None
        self._identity: dict[str, Any] = {}
        self._native_configuration: Any = None
        self._operation_lock = threading.Lock()
        self._requested_configuration: PhotonConfig | None = None
        self._configuration_error: dict[str, Any] | None = None

    @contextmanager
    def _exclusive(self):
        if not self._operation_lock.acquire(blocking=False):
            raise AcquisitionError("native adapter operation in progress")
        try:
            yield
        finally:
            self._operation_lock.release()

    def availability(self) -> dict[str, Any]:
        try:
            present = importlib.util.find_spec("TimeTagger") is not None
        except (ImportError, ValueError):
            present = False
        return {"sdk": "TimeTagger", "installed": present, "hardware_validated": False,
                "native_replay_validated": False,
                "dependency": None if present else "Install official Swabian SDK and obtain the per-PC virtual replay license.",
                "documentation_version": "2.22.6.0",
                "source": "https://www.swabianinstruments.com/static/documentation/TimeTagger/api/TimeTaggerLibrary.html"}

    def identify(self) -> dict[str, Any]:
        return {"adapter_id": self.adapter_id, "mode": self.mode, "state": self.state,
                "transport": "Swabian native TCP SDK" if self.address else ("FileWriter .ttbin SDK" if self.mode == "replay" else "Swabian USB SDK"),
                "identity_verified": bool(self._identity), "acquisition_blocked": self.state != "ready",
                "configuration_error": self._configuration_error,
                "applied_configuration_id": self.configuration.configuration_id if self.configuration else None,
                "test_only_transport": self._test_only_transport,
                "origin": "simulated" if self._test_only_transport else (
                    "physical" if self.mode == "hardware" else "unverified_replay_record"),
                **self._identity}

    def _require_equipment_authority(self, *, acquisition=False):
        if self._test_only_transport:
            # Opt-in exists only on the trusted Python constructor, never in
            # registry/API options. A real installed SDK cannot be used here.
            if self.sdk is not None and getattr(self.sdk, "__file__", None):
                raise AcquisitionError("test-only transport cannot operate the installed native SDK")
            return
        if not callable(self._security_guard):
            raise AcquisitionError("authenticated equipment authority required for physical acquisition")
        self._security_guard(acquisition=acquisition)

    def _require_acquisition_authority(self):
        self._require_equipment_authority(acquisition=True)

    def connect(self) -> dict[str, Any]:
        with self._exclusive():
            if not self.availability()["installed"]:
                raise AdapterUnavailable("Swabian TimeTagger SDK is not installed; no hardware acquisition attempted")
            self._require_equipment_authority()
            return self._connect()

    def _connect(self) -> dict[str, Any]:
        if self.state not in {"registered", "disconnected", "fault"}:
            raise AcquisitionError(f"cannot connect from {self.state}")
        if not self.availability()["installed"]:
            raise AdapterUnavailable("Swabian TimeTagger SDK is not installed; no hardware acquisition attempted")
        if self.tagger is not None:
            self.sdk.freeTimeTagger(self.tagger)
            self.tagger = None
        self.configuration = None
        self._configuration_error = None
        self._requested_configuration = None
        self._identity = {}
        self.sdk = importlib.import_module("TimeTagger")
        if self._test_only_transport and getattr(self.sdk, "__file__", None):
            self.state = "fault"
            raise AcquisitionError("test-only transport cannot operate the installed native SDK")
        try:
            if self.mode == "replay":
                if not self.replay_file or not self.replay_file.is_file():
                    raise AdapterUnavailable("FileWriter replay record is missing")
                self.tagger = self.sdk.createTimeTaggerVirtual(str(self.replay_file))
                self._identity = {"source_record": str(self.replay_file.resolve()), "hardware_connected": False,
                                  "device_id": f"swabian-replay:{self.replay_file.name}"}
            elif self.address:
                self.tagger = self.sdk.createTimeTaggerNetwork([self.address])
                self._identity = {"serial": self.tagger.getSerial(), "hardware_connected": True}
            else:
                # Explicit serial is recommended; the native API allows selecting the first available device.
                self.tagger = self.sdk.createTimeTagger(self.serial)
                self._identity = {"serial": self.tagger.getSerial(), "hardware_connected": True}
            if self.mode == "hardware":
                if not self._identity.get("serial") or (self.serial and self._identity["serial"] != self.serial):
                    raise AdapterUnavailable("hardware serial identity did not match")
                self._identity["model"] = self.tagger.getModel()
                self._identity["pcb_version"] = self.tagger.getPcbVersion()
                self._identity["firmware_version"] = "not exposed by the reviewed public contract"
                # This bridge currently implements the Ultra electrical contract only.
                if self._identity["model"].casefold() != self.expected_model.casefold() or "ultra" not in self.expected_model.casefold():
                    raise AdapterUnavailable("actual model differs from the supported Ultra electrical reference")
                self._clock_domain = f"swabian:{self._identity['serial']}:relative:{uuid.uuid4()}"
                self._identity["device_id"] = f"swabian:{self._identity['serial']}"
                self._identity["hardware_license"] = self.tagger.getDeviceLicense()
            self._native_configuration = self.tagger.getConfiguration()
        except Exception as exc:
            if self.tagger is not None:
                self.sdk.freeTimeTagger(self.tagger)
                self.tagger = None
            self._identity = {}
            self.state = "fault"
            raise AdapterUnavailable(f"native Time Tagger connection/replay license failed: {exc}") from exc
        self.state = "connected"
        return self.identify()

    def capabilities(self) -> dict[str, Any]:
        """Read-only instrument facts, separate from the optical experiment."""
        if self.tagger is None:
            raise AcquisitionError("connect native SDK before reading capabilities")
        from .instrument import InstrumentCapabilities
        channels = tuple(operator.index(c) for c in self.tagger.getChannelList() if c > 0)
        ranges = {channel: tuple(self.tagger.getTriggerLevelRange(channel)) for channel in channels}
        capabilities = InstrumentCapabilities(self.adapter_id, channels, ranges,
                                             origin="native_sdk", hardware_validated=False)
        capabilities.validate()
        return capabilities.to_dict()

    def _preflight(self, configuration: PhotonConfig) -> PhotonConfig:
        from .instrument import InstrumentCapabilities
        configuration.validate()
        InstrumentCapabilities.from_dict(self.capabilities()).validate_configuration(configuration)
        for method in ("setTriggerLevel", "getTriggerLevel", "setInputDelay", "getInputDelay",
                       "sync", "getConfiguration", "getReferenceClockState", "disableReferenceClock"):
            if not callable(getattr(self.tagger, method, None)):
                raise AdapterUnavailable(f"native SDK lacks the documented {method} contract")
        if configuration.reference_channel is not None and not callable(getattr(self.tagger, "setReferenceClock", None)):
            raise AdapterUnavailable("native SDK lacks reference clock configuration")
        return replace(configuration, clock_domain=self._clock_domain or configuration.clock_domain)

    def _readback(self, expected: PhotonConfig) -> dict[str, Any]:
        levels, delays = [], []
        # Always read every channel; a mismatch must not prevent inspecting the remainder.
        for channel in expected.channels:
            level = self.tagger.getTriggerLevel(channel)
            if isinstance(level, bool) or not isinstance(level, (int, float)) or not math.isfinite(level):
                raise AcquisitionError("native trigger readback is invalid")
            levels.append(float(level))
            delays.append(operator.index(self.tagger.getInputDelay(channel)))
        clock = self.tagger.getReferenceClockState()
        native = self.tagger.getConfiguration()
        enabled = bool(clock.enabled)
        mismatches = []
        if any(not math.isclose(a, b, abs_tol=1e-9, rel_tol=0) for a, b in zip(levels, expected.trigger_levels_v)):
            mismatches.append("trigger_levels_v")
        if tuple(delays) != tuple(expected.input_delays_ps):
            mismatches.append("input_delays_ps")
        if expected.reference_channel is None:
            if enabled:
                mismatches.append("reference_clock_enabled")
        elif (not enabled or operator.index(clock.clock_channel) != expected.reference_channel
              or operator.index(clock.clock_period) != round(1e12 / expected.reference_frequency_hz)
              or not bool(clock.is_locked)):
            mismatches.append("reference_clock")
        result = {"trigger_levels_v": levels, "input_delays_ps": delays,
                  "reference_clock_enabled": enabled, "native_configuration": native,
                  "mismatches": mismatches}
        self._last_readback = result
        if mismatches:
            raise AcquisitionError("native configuration readback mismatch: " + ", ".join(mismatches))
        return result

    def configure(self, configuration: PhotonConfig) -> dict[str, Any]:
        with self._exclusive():
            self._require_equipment_authority()
            return self._configure(configuration)

    def _configure(self, configuration: PhotonConfig) -> dict[str, Any]:
        if self.state not in {"connected", "configured", "ready"} or self.tagger is None:
            raise AcquisitionError("connect native SDK before configuration")
        if self.mode == "replay":
            raise AdapterUnavailable("native replay metadata must be converted from FileReader.getConfiguration; no synthetic config substitution")
        # Everything queryable is validated before the first setter. Native application
        # cannot be atomic; an error after any setter invalidates the acquisition permit.
        configuration = self._preflight(configuration)
        self._requested_configuration = configuration
        self.configuration = None
        self.state = "configuring"
        try:
            for index, channel in enumerate(configuration.channels):
                self.tagger.setTriggerLevel(channel, configuration.trigger_levels_v[index])
                self.tagger.setInputDelay(channel, configuration.input_delays_ps[index])
            if configuration.reference_channel is None:
                self.tagger.disableReferenceClock()
            else:
                self.tagger.setReferenceClock(configuration.reference_channel,
                                              clock_frequency=configuration.reference_frequency_hz, wait_until_locked=True)
            if not self.tagger.sync(timeout=5000):
                raise AcquisitionError("native settings synchronization timeout")
            readback = self._readback(configuration)
        except Exception as exc:
            self.state = "configuration_unverified"
            self._configuration_error = {"error_type": type(exc).__name__, "acquisition_blocked": True,
                                         "requested_configuration_id": configuration.configuration_id}
            raise AcquisitionError("native configuration unverified after application failure; readback and calibration required") from exc
        self.configuration = configuration
        self._native_configuration = readback["native_configuration"]
        self._configuration_error = None
        self.state = "configured"
        return {"applied": asdict(self.configuration), "configuration_id": self.configuration.configuration_id,
                "native_configuration": self._native_configuration, "readback_verified": True}

    def reverify_configuration(self, configuration: PhotonConfig | None = None) -> dict[str, Any]:
        """Read-only reconciliation after uncertain application; never silently roll back."""
        with self._exclusive():
            self._require_equipment_authority()
            if self.state != "configuration_unverified" or self.tagger is None:
                raise AcquisitionError("no unverified native configuration to reconcile")
            expected = configuration or self._requested_configuration
            if expected is None:
                raise AcquisitionError("explicit expected configuration required")
            expected = self._preflight(expected)
            try:
                if not self.tagger.sync(timeout=5000):
                    raise AcquisitionError("native readback synchronization timeout")
                readback = self._readback(expected)
            except Exception:
                self.configuration = None
                self.state = "configuration_unverified"
                raise
            self.configuration = expected
            self._native_configuration = readback["native_configuration"]
            self._requested_configuration = expected
            self._configuration_error = None
            self.state = "configured"
            return {"applied": asdict(expected), "configuration_id": expected.configuration_id,
                    "readback_verified": True, "calibration_required": True, "native_configuration": self._native_configuration}

    def calibrate(self) -> dict[str, Any]:
        with self._exclusive():
            self._require_equipment_authority()
            return self._calibrate()

    def _calibrate(self) -> dict[str, Any]:
        if self.state != "configured" or self.tagger is None:
            raise AcquisitionError("configure native hardware before calibration")
        try:
            self._readback(self.configuration)
            values = self.tagger.autoCalibration()
            if any(not math.isfinite(float(v)) or float(v) < 0 for v in values):
                raise AcquisitionError("invalid calibration diagnostic")
            self._readback(self.configuration)
        except Exception:
            self.configuration = None
            self.state = "configuration_unverified"
            raise
        self.state = "ready"
        return {"mode": "hardware", "method": "native autoCalibration diagnostic",
                "channel_jitter_ps": [float(v) for v in values],
                "limit": "not an optical channel delay calibration; HighRes values require separate interpretation"}

    def acquire(self, experiment_id: str, *, fault: str | None = None) -> Acquisition:
        with self._exclusive():
            return self._acquire(experiment_id, fault=fault)

    def _acquire(self, experiment_id: str, *, fault: str | None = None) -> Acquisition:
        if fault is not None:
            raise AcquisitionError("hardware fault injection is not authorized by this adapter")
        if self.mode == "replay":
            raise AdapterUnavailable("native replay connection is documented; normalized acquisition needs original metadata import")
        if self.state != "ready" or self.configuration is None or not experiment_id:
            raise AcquisitionError("native configuration and calibration are required")
        self._require_acquisition_authority()
        if not self.event_type_map:
            raise AdapterUnavailable("supply the documented event enum mapping from the installed SDK before native normalization")
        cfg = self.configuration
        try:
            self._readback(cfg)
        except Exception as exc:
            self._requested_configuration = cfg
            self.configuration = None
            self.state = "configuration_unverified"
            raise AcquisitionError("native configuration unverified before acquisition") from exc
        self.state = "acquiring"
        stream = None
        try:
            self.tagger.clearOverflows()
            stream = self.sdk.TimeTagStream(self.tagger, cfg.buffer_capacity, list(cfg.channels))
            stream.startFor(cfg.duration_ps)
            if not stream.waitUntilFinished(timeout=math.ceil(cfg.duration_ps / 1e9) + 5000):
                raise AcquisitionError("native acquisition timeout")
            buffer = stream.getData()
            arrays = (buffer.getTimestamps(), buffer.getChannels(), buffer.getEventTypes(), buffer.getMissedEvents())
            if len({len(values) for values in arrays}) != 1:
                raise AcquisitionError("native SDK returned inconsistent event arrays")
            events: list[TimeTag] = []
            flags: list[str] = []
            origin = "simulated" if self._test_only_transport else "physical"
            mode = "emulation" if self._test_only_transport else "hardware"
            source_id = f"{'synthetic-sdk-fixture' if self._test_only_transport else 'native-sdk'}:{self._identity['serial']}"
            for timestamp, channel, kind, missed in zip(*arrays):
                event_type = self.event_type_map.get(int(kind))
                if event_type is None:
                    raise AcquisitionError("unknown native event type; raw type must not be silently discarded")
                if event_type == "Error":
                    flags.append("native_error")
                events.append(TimeTag(operator.index(timestamp), operator.index(channel), experiment_id, self._identity["device_id"],
                                      cfg.configuration_id, cfg.clock_domain, origin, source_id, event_type, operator.index(missed)))
            if buffer.hasOverflows or self.tagger.getOverflows() > 0:
                flags.append("overflow")
            if buffer.size >= cfg.buffer_capacity:
                flags.append("stream_buffer_saturated")
            result = Acquisition(experiment_id, self._identity["device_id"], self.adapter_id, mode, origin,
                                 asdict(cfg), events, int(stream.getCaptureDuration()), cfg.clock_domain, source_id,
                                 diagnostics={"quality_flags": sorted(set(flags)), "capture_start_ps": int(buffer.tStart),
                                              "native_configuration": self._native_configuration,
                                              "hardware_license": self._identity.get("hardware_license"),
                                              "optical_bench_validated": False})
            result.diagnostics["test_only_transport"] = self._test_only_transport
            result.validate()
            self.state = "ready"
            return result
        except Exception:
            self.state = "fault"
            raise
        finally:
            if stream is not None:
                stream.stop()

    def acquire_continuous(self, experiment_id: str, journal_path: Path | str, *, cancel=None,
                           drain_interval_s=.01, on_block=None):
        """Persist each drained native block before requesting the next one."""
        from .capture import CaptureJournal, NativeStreamingSource, run_continuous_capture
        with self._exclusive():
            if self.mode != "hardware" or self.state != "ready" or self.configuration is None or not experiment_id:
                raise AcquisitionError("native configuration and calibration required for continuous capture")
            self._require_acquisition_authority()
            if not self.event_type_map:
                raise AdapterUnavailable("documented SDK event enum mapping required")
            cfg = self.configuration
            try:
                self._readback(cfg)
            except Exception as exc:
                self._requested_configuration = cfg
                self.configuration = None
                self.state = "configuration_unverified"
                raise AcquisitionError("native configuration unverified before continuous acquisition") from exc
            source = NativeStreamingSource(self, experiment_id)
            self.state = "acquiring"
            try:
                result = run_continuous_capture(source, CaptureJournal(journal_path), experiment_id,
                                                cancel=cancel, drain_interval_s=drain_interval_s, on_block=on_block,
                                                timeout_s=cfg.duration_ps / 1e12 + 5.)
                self._readback(cfg)
                self.state = "ready"
                return result
            except Exception:
                self.state = "fault"
                raise

    def disconnect(self) -> None:
        with self._exclusive():
            self._disconnect()

    def _disconnect(self) -> None:
        if self.state == "acquiring":
            raise AcquisitionError("stop acquisition before freeing native hardware")
        if self.tagger is not None:
            self.sdk.freeTimeTagger(self.tagger)
        self.tagger = None
        self._identity = {}
        self.configuration = None
        self.state = "disconnected"

