"""Bounded FileReader normalization, with mandatory original metadata and file hashes.

No .ttbin decoder or proprietary format is guessed. Tests inject an interface double;
native decoding still requires the official SDK and a complete FileWriter dataset.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import importlib
import json
import operator
from pathlib import Path
from typing import Callable

from .adapters import AdapterUnavailable
from .photon import Acquisition, AcquisitionError, PhotonConfig, TimeTag, digest


@dataclass(frozen=True)
class FileWriterContext:
    experiment_id: str
    device_id: str
    source_id: str
    origin: str
    configuration: PhotonConfig
    duration_ps: int
    capture_start_ps: int
    native_configuration_sha256: str
    file_sha256: dict[str, str]
    clock_locked: bool = True


def import_filewriter_record(files: list[Path | str], *, context: FileWriterContext,
                             event_type_map: dict[int, str], reader_factory: Callable | None = None,
                             block_size: int = 10000, max_events: int = 1_000_000) -> Acquisition:
    context.configuration.validate()
    if not files or len(set(str(Path(p).resolve()) for p in files)) != len(files):
        raise AcquisitionError("FileWriter record requires a unique complete file set")
    if not all((context.experiment_id, context.device_id, context.source_id)) or context.origin not in {"physical", "simulated"}:
        raise AcquisitionError("original FileWriter provenance is mandatory")
    if type(block_size) is not int or not 1 <= block_size <= max_events or type(max_events) is not int or max_events <= 0:
        raise AcquisitionError("invalid FileReader block/event budget")
    if type(context.duration_ps) is not int or context.duration_ps <= 0 or type(context.capture_start_ps) is not int or context.capture_start_ps < 0:
        raise AcquisitionError("original acquisition interval is mandatory")
    if not event_type_map or any(type(k) is not int or value not in {
            "TimeTag", "Error", "OverflowBegin", "OverflowEnd", "MissedEvents"} for k, value in event_type_map.items()):
        raise AcquisitionError("documented SDK event mapping required")
    paths = [Path(p).resolve() for p in files]
    if any(not p.is_file() or p.suffix.casefold() != ".ttbin" for p in paths):
        raise AcquisitionError("all FileWriter source files must exist")
    if set(context.file_sha256) != {p.name for p in paths} or len({p.name for p in paths}) != len(paths):
        raise AcquisitionError("manifest must identify every source file exactly once")
    # A split header contains no events. A context declaring only that header cannot
    # claim a complete acquisition. Individual numbered files remain valid inputs.
    for path in paths:
        if not path.stem.rsplit(".", 1)[-1].isdigit():
            siblings = sorted(path.parent.glob(path.stem + ".*.ttbin"))
            if not siblings or not set(siblings) <= set(paths):
                raise AcquisitionError("FileWriter sequence header needs all available numbered data files")
            indices = sorted(int(p.stem.rsplit(".", 1)[-1]) for p in siblings if p.stem.rsplit(".", 1)[-1].isdigit())
            if indices != list(range(1, len(indices) + 1)):
                raise AcquisitionError("FileWriter split sequence has a missing data file")
    evidence = []
    for path in paths:
        value = hashlib.sha256(path.read_bytes()).hexdigest()
        if value != context.file_sha256[path.name]:
            raise AcquisitionError("FileWriter source integrity failed")
        evidence.append({"path": str(path), "sha256": value})
    synthetic_interface = reader_factory is not None
    if reader_factory is None:
        try:
            reader_factory = importlib.import_module("TimeTagger").FileReader
        except (ImportError, AttributeError) as exc:
            raise AdapterUnavailable("native FileReader requires the official Swabian SDK; no simulated fallback") from exc
    # Read numbered files explicitly, avoiding reading the same split sequence twice
    # when both header and numbered files were supplied for integrity verification.
    inputs = [str(p) for p in paths if p.stem.rsplit(".", 1)[-1].isdigit()]
    reader = reader_factory(inputs)
    native = reader.getConfiguration()
    if isinstance(native, str):
        native = json.loads(native)
    if digest(native) != context.native_configuration_sha256:
        raise AcquisitionError("native FileWriter configuration does not match original context")
    if not set(context.configuration.channels) <= set(reader.getChannelList()):
        raise AcquisitionError("original signal channels missing from FileWriter record")
    events = []
    flags = set()
    while reader.hasData():
        buffer = reader.getData(block_size)
        arrays = (buffer.getTimestamps(), buffer.getChannels(), buffer.getEventTypes(), buffer.getMissedEvents())
        if len({len(a) for a in arrays}) != 1:
            raise AcquisitionError("FileReader returned inconsistent arrays")
        if not len(arrays[0]) and reader.hasData():
            raise AcquisitionError("FileReader stalled without consuming data")
        if len(events) + len(arrays[0]) > max_events:
            raise AcquisitionError("FileReader event budget exceeded; use persistent streaming import")
        for timestamp, channel, kind, missed in zip(*arrays):
            event = event_type_map.get(operator.index(kind))
            if event is None:
                raise AcquisitionError("unknown native event type")
            timestamp = operator.index(timestamp)
            if not context.capture_start_ps <= timestamp < context.capture_start_ps + context.duration_ps:
                raise AcquisitionError("FileReader event outside original capture interval")
            events.append(TimeTag(timestamp, operator.index(channel), context.experiment_id,
                                  context.device_id, context.configuration.configuration_id,
                                  context.configuration.clock_domain, context.origin, context.source_id,
                                  event, operator.index(missed)))
            if event in {"OverflowBegin", "MissedEvents"}:
                flags.add("overflow")
            elif event == "Error":
                flags.add("native_error")
        if buffer.hasOverflows:
            flags.add("overflow")
        current = reader.getConfiguration()
        if isinstance(current, str):
            current = json.loads(current)
        if digest(current) != context.native_configuration_sha256:
            raise AcquisitionError("FileWriter configuration changed; split into separate acquisition contexts")
    result = Acquisition(context.experiment_id, context.device_id, "orion.swabian-filereader.v1", "replay",
                         context.origin, asdict(context.configuration), events, context.duration_ps,
                         context.configuration.clock_domain, context.source_id, context.clock_locked,
                         {"capture_start_ps": context.capture_start_ps, "quality_flags": sorted(flags),
                          "native_configuration": native, "source_files": evidence,
                          "validation_level": "synthetic_sdk_interface" if synthetic_interface else "native_file_decode",
                          "hardware_validated": False, "virtual_replay_license_validated": False,
                          "file_set_completeness": "verified against supplied manifest; original author attestation required"},
                         source_record=str(paths[0]))
    result.validate()
    return result
