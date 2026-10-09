"""Optimized presentation/persistence retain scientific and security contracts."""
from collections import UserDict
from dataclasses import dataclass, fields, is_dataclass, replace
import hashlib
import json
import math
import sqlite3
import subprocess
import sys
from typing import Mapping
import zlib

import pytest

from quantum_autopilot.network_engine.state import LiveState, _plain
from quantum_autopilot.quantum_protocols.contracts import ProtocolConfig, ProtocolError, canonical
from quantum_autopilot.quantum_protocols.runtime import ProtocolRuntime, Session
from quantum_autopilot.security import AuthenticationError, AuthorizationError
from tests.test_network_engine_service import service_on
from tests.test_network_engine_routing import square

pytestmark = pytest.mark.unit


def legacy_plain(value):
    """Independent previously published conversion contract, including subclasses."""
    if is_dataclass(value) and not isinstance(value, type):
        return {item.name: legacy_plain(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, (tuple, list)):
        return [legacy_plain(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): legacy_plain(item) for key, item in value.items()}
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("non-finite number in a live record; a missing value must be None")
    return value


def test_scalar_fast_path_preserves_plain_contract_and_independent_copies():
    class FloatSubclass(float):
        pass

    class ListSubclass(list):
        pass

    @dataclass
    class Record:
        observations: object
        clocks: tuple

    raw = {"a": [None, True, 1, 0.125, "µ"], 7: UserDict({"b": FloatSubclass(.5)}),
           "record": Record(ListSubclass([{"x": 3.0}]), (1, 2)), "class": Record}
    assert _plain(raw) == legacy_plain(raw)
    converted = _plain(raw)
    converted["a"].append("changed")
    converted["record"]["observations"][0]["x"] = 10
    assert raw["a"] == [None, True, 1, 0.125, "µ"]
    assert raw["record"].observations[0]["x"] == 3.0
    assert converted["class"] is Record
    for value in (float("nan"), float("inf"), -float("inf"), FloatSubclass("nan")):
        for convert in (legacy_plain, _plain):
            with pytest.raises(ValueError, match="non-finite number"):
                convert({"nested": ([value],)})


def test_small_metadata_does_not_change_default_full_api_or_advance_reads(tmp_path):
    service = service_on(tmp_path, square())
    original = service.network_live_state()
    metadata = service.network_live_state(metadata_only=True)
    assert metadata["schema"] == "orion.network-metadata.v1"
    assert metadata["source"] == original["source"]
    assert metadata["tick"] == original["tick"] == 0
    assert metadata["nodes"] == len(original["nodes"])
    assert metadata["links"] == len(original["links"])
    assert metadata["satellites"] == len(original["satellites"])
    assert service.network_live_state() == original
    assert LiveState.from_dict(original).to_dict() == original
    step = service.network_step(metadata_only=True)
    full = service.network_live_state()
    assert step["tick"] == full["tick"] == 1
    assert step["network_time_s"] == full["network_time_s"]
    assert json.loads(json.dumps(full)) == full
    for invalid in (None, 0, 1, "true", [], {}):
        with pytest.raises(ValueError, match="metadata_only must be a boolean"):
            service.network_step(metadata_only=invalid)
    assert service.network_live_state()["tick"] == 1
    assert service.security.audit.verify()["valid"]


def test_metadata_and_step_keep_backend_authorization_before_work(tmp_path):
    service = service_on(tmp_path, square())
    auditor = service.security.grant_identity("service:metadata-auditor", ["auditor"], ["workspace:*"])
    operator = service.security.grant_identity("service:metadata-operator", ["operator"], ["workspace:*"])
    with service.security.identity_scope(None):
        with pytest.raises(AuthenticationError):
            service.network_live_state(metadata_only=True)
        with pytest.raises(AuthenticationError):
            service.network_step(metadata_only=True)
    with service.security.identity_scope(auditor):
        assert service.network_live_state(metadata_only=True)["tick"] == 0
        with pytest.raises(AuthorizationError):
            service.network_step(metadata_only=True)
    service.security.revoke_identity(operator.subject)
    with pytest.raises(AuthenticationError), service.security.identity_scope(operator):
        service.network_step(metadata_only=True)
    assert service.network_live_state()["tick"] == 0
    assert service.security.audit.verify()["valid"]


def raw_session(parameters=None):
    return Session(ProtocolConfig("test-raw", ("satellite_doppler",), ("A", "B"), ("A", "S", "B"),
                                  ("A-S", "S-B"), parameters=parameters or {}))


def test_compression_profiles_preserve_content_hash_and_atomic_receipts(tmp_path):
    serialized = canonical({"tdc": [{"time_ps": list(range(2000)), "basis": [0, 1] * 1000}],
                            "clock": {"epoch": "fixture", "resolution_ps": 1}})
    expected = serialized.encode("utf-8")
    sha = hashlib.sha256(expected).hexdigest()
    sizes = []
    for level in (None, 0, 1, 6, 9):
        runtime = ProtocolRuntime(tmp_path / f"raw-{level}.sqlite", "test")
        session = raw_session({} if level is None else {"raw_compression_level": level})
        runtime._queue_raw_acquisition(session, sha, serialized)
        runtime._event(session, 0., "acquired", {"raw_sha256": sha}, "fixture-command")
        runtime._flush()
        with sqlite3.connect(runtime.journal_path) as connection:
            blob = connection.execute("SELECT observation_zlib FROM satellite_acquisitions WHERE sha256=?", (sha,)).fetchone()[0]
            rows = connection.execute("SELECT payload_json FROM technology_events").fetchall()
        assert zlib.decompress(blob) == expected
        assert hashlib.sha256(zlib.decompress(blob)).hexdigest() == sha
        assert json.loads(rows[0][0])["detail"]["raw_sha256"] == sha
        # A repeat is recognized by its raw content, regardless of compression.
        runtime._queue_raw_acquisition(session, sha, serialized)
        runtime._flush()
        with sqlite3.connect(runtime.journal_path) as connection:
            assert connection.execute("SELECT count(*) FROM satellite_acquisitions").fetchone()[0] == 1
        sizes.append(len(blob))
    assert sizes[0] == sizes[2]  # default profile is explicitly level 1
    assert sizes[1] > max(sizes[0], sizes[2], sizes[3], sizes[4])


def test_lossless_profile_does_not_change_simulated_satellite_observations_or_protocol(tmp_path):
    from quantum_autopilot.network_engine.institutional import brazil_institutions
    from quantum_autopilot.quantum_protocols.sources import SimulatedAcquisitionSource
    from quantum_autopilot.quantum_protocols.topology import expand_global

    topology = expand_global(brazil_institutions())[0]
    outcomes = []
    for level in (1, 6):
        nodes = []
        for node in topology.nodes:
            sessions = [{**raw, "parameters": {**raw["parameters"], "raw_compression_level": level}}
                        for raw in node.telemetry.get("technology_sessions", ())
                        if raw["session_id"] == "RIO-SATELLITE"]
            nodes.append(replace(node, telemetry={**node.telemetry, "technology_sessions": sessions}))
        scenario = replace(topology, nodes=tuple(nodes))
        runtime = ProtocolRuntime(tmp_path / f"scientific-{level}.sqlite", scenario.topology_id,
                                  source=SimulatedAcquisitionSource(seed=42))
        for tick in range(1, 5):
            runtime.advance(scenario, tick)
        with sqlite3.connect(runtime.journal_path) as connection:
            raw_records = [(sha, zlib.decompress(blob)) for sha, blob in connection.execute(
                "SELECT sha256,observation_zlib FROM satellite_acquisitions ORDER BY sha256")]
            event_counts = connection.execute(
                "SELECT event_kind,count(*) FROM technology_events GROUP BY event_kind ORDER BY event_kind").fetchall()
        assert raw_records and all(hashlib.sha256(raw).hexdigest() == sha for sha, raw in raw_records)
        session = runtime.snapshot()["sessions"]["RIO-SATELLITE"]
        outcomes.append((raw_records, event_counts, session["status"], session["metrics"], session["counters"]))
    assert outcomes[0] == outcomes[1]


def test_raw_quota_failure_retains_both_outboxes_for_recoverable_commit(tmp_path):
    runtime = ProtocolRuntime(tmp_path / "quota.sqlite", "test")
    session = raw_session({"raw_compression_level": 0, "raw_store_limit_bytes": 1000})
    serialized = canonical({"fixture": list(range(100))})
    sha = hashlib.sha256(serialized.encode()).hexdigest()
    runtime._queue_raw_acquisition(session, sha, serialized)
    runtime._event(session, 0., "acquired", {"raw_sha256": sha})
    pending = runtime._raw_outbox[sha]
    # Another writer can consume quota after enqueue, before atomic commit.
    with sqlite3.connect(runtime.journal_path) as connection:
        connection.execute("INSERT INTO satellite_acquisitions VALUES (?,?)", ("external", bytes(950)))
    with pytest.raises(ProtocolError, match="quota reached"):
        runtime._flush()
    assert runtime._raw_outbox[sha] == pending and len(runtime._outbox) == 1
    with sqlite3.connect(runtime.journal_path) as connection:
        assert connection.execute("SELECT count(*) FROM technology_events").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM satellite_acquisitions WHERE sha256=?", (sha,)).fetchone()[0] == 0
        connection.execute("DELETE FROM satellite_acquisitions WHERE sha256='external'")
    runtime._flush()
    assert not runtime._outbox and not runtime._raw_outbox
    with sqlite3.connect(runtime.journal_path) as connection:
        assert connection.execute("SELECT count(*) FROM technology_events").fetchone()[0] == 1
        assert zlib.decompress(connection.execute("SELECT observation_zlib FROM satellite_acquisitions").fetchone()[0]).decode() == serialized


@pytest.mark.parametrize("level", [-1, 10, True, False, 1.0, "1", None])
def test_invalid_compression_configuration_rejected_before_application(tmp_path, level):
    runtime = ProtocolRuntime(tmp_path / "invalid.sqlite", "test")
    session = raw_session({"raw_compression_level": level})
    with pytest.raises(ProtocolError, match="raw_compression_level"):
        runtime._validate_parameters(session.config)
    assert not runtime._queue and not runtime._outbox and not runtime._raw_outbox


def test_optional_models_are_lazy_in_fresh_process_without_changing_exports():
    code = """
import sys
import quantum_autopilot.control_plane as cp
assert 'FrozenTaskPolicySpec' in dir(cp)
assert 'torch' not in sys.modules
assert 'qiskit_aer' not in sys.modules
from quantum_autopilot.qkd.dashboard_api import AssuranceDashboardService
assert 'torch' not in sys.modules
assert 'qiskit_aer' not in sys.modules
from quantum_twin.optical.interface import ColumnSpec, CSVTelemetrySource
assert 'torch' not in sys.modules
assert 'qiskit_aer' not in sys.modules
from quantum_autopilot.control_plane.topology import SimulatedTopology
assert cp.SimulatedTopology is SimulatedTopology
assert cp.SimulatedTopology is cp.SimulatedTopology
try:
    cp.this_export_does_not_exist
except AttributeError:
    pass
else:
    raise AssertionError('unknown export was accepted')
from quantum_twin.optical.interface import QuantumNetworkDatasetV3
from quantum_twin.optical.wdm import QuantumNetworkDatasetV3 as implementation
assert QuantumNetworkDatasetV3 is implementation
"""
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
