"""Self-contained admission tests: synthetic weights, no user Downloads/F: dependency."""
import hashlib
import io
import json
import pickle
import stat
import zipfile

import numpy as np
import pytest

from quantum_autopilot.model_intake import artifacts, evaluation
from quantum_autopilot.model_intake.artifacts import QKD_ROOT, ROUTING_ROOT, ReviewedArchive
from quantum_autopilot.model_intake.inference import RoutingReplayModel, SyncReplayModel

pytestmark = pytest.mark.unit


@pytest.fixture
def make_archive(tmp_path, monkeypatch):
    def make(files, kind="qkd"):
        path = tmp_path / f"{kind}-{len(list(tmp_path.iterdir()))}.zip"
        with zipfile.ZipFile(path, "w") as archive:
            for name, content in files.items():
                entry = zipfile.ZipInfo(name)
                # ZipInfo normalizes backslashes on Windows at construction.
                # Preserve the supplied bytes to actually test an unsafe ZIP.
                entry.filename = name
                archive.writestr(entry, content)
        monkeypatch.setitem(artifacts.PINS, kind, hashlib.sha256(path.read_bytes()).hexdigest())
        return path
    return make


@pytest.fixture
def sync_archive(make_archive):
    config = {"window": 8, "scale": 1e-9, "update_period_s": 1.0, "horizon_s": 2.0,
              "coefficients": [0.0] * 9, "intercept": 0.5}
    return ReviewedArchive(make_archive({QKD_ROOT + "modelos/sincronizador_ridge.json": json.dumps(config)}), "qkd")


@pytest.fixture
def routing_archive(make_archive):
    arrays = {}
    for role, sizes in (("actor", (34, 64, 64, 1)), ("critic", (11, 64, 64, 1))):
        for i, (a, b) in enumerate(zip(sizes[:-1], sizes[1:])):
            arrays[f"{role}_{2*i}"] = np.zeros((a, b))
            arrays[f"{role}_{2*i+1}"] = np.zeros(b)
    stream = io.BytesIO()
    np.savez(stream, **arrays)
    files = {}
    for variant, temperature in (("nominal", 1.0), ("prazo_longo", 0.0)):
        base = ROUTING_ROOT + variant + "/model/policy"
        files[base + ".npz"] = stream.getvalue()
        files[base + ".json"] = json.dumps({
            "layers": {"actor": 3, "critic": 3},
            "sizes": {"actor": [34, 64, 64, 1], "critic": [11, 64, 64, 1]},
            "features": {"state_count": 11, "action_count": 23}, "temperature": temperature,
        })
    return ReviewedArchive(make_archive(files, "qnet"), "qnet")


def observation():
    features = np.zeros((2, 23))
    features[0, 0] = features[1, 1] = 1.0
    return np.zeros(11), features, [{"kind": "WAIT"}, {"kind": "GENERATE"}]


def test_hash_rejects_unknown_archive_before_zip_parser(tmp_path, monkeypatch):
    path = tmp_path / "invalid.zip"
    path.write_bytes(b"not a zip")
    monkeypatch.setattr(zipfile, "ZipFile", lambda *a, **k: pytest.fail("parsed before hash"))
    with pytest.raises(ValueError, match="hash"):
        ReviewedArchive(path, "qkd")


def test_pinned_archive_is_a_snapshot(make_archive):
    path = make_archive({"a.json": "{\"a\":1}"})
    archive = ReviewedArchive(path, "qkd")
    path.write_bytes(b"replaced")
    assert archive.json("a.json") == {"a": 1}


@pytest.mark.parametrize("name", ["../a.json", "/a.json", "C:/a.json", "a\\b.json"])
def test_unsafe_members_rejected(make_archive, name):
    with pytest.raises(ValueError, match="unsafe"):
        ReviewedArchive(make_archive({name: "{}"}), "qkd")


@pytest.mark.parametrize("case", ["duplicate", "symlink", "expansion"])
def test_archive_metadata_guards(tmp_path, monkeypatch, case):
    path = tmp_path / "guard.zip"
    with zipfile.ZipFile(path, "w") as z:
        if case == "duplicate":
            z.writestr("a.json", "{}")
            with pytest.warns(UserWarning):
                z.writestr("a.json", "{}")
        elif case == "symlink":
            member = zipfile.ZipInfo("a.json")
            member.create_system = 3
            member.external_attr = (stat.S_IFLNK | 0o777) << 16
            z.writestr(member, "target")
        else:
            z.writestr("a.json", "{}")
            monkeypatch.setattr(artifacts, "MAX_EXPANDED_BYTES", 1)
    monkeypatch.setitem(artifacts.PINS, "qkd", hashlib.sha256(path.read_bytes()).hexdigest())
    with pytest.raises(ValueError):
        ReviewedArchive(path, "qkd")


def test_size_limit(make_archive, monkeypatch):
    path = make_archive({"a.json": "{}"})
    monkeypatch.setattr(artifacts, "MAX_ARCHIVE_BYTES", 1)
    with pytest.raises(ValueError, match="size limit"):
        ReviewedArchive(path, "qkd")


@pytest.mark.parametrize("text", ['{"x":1,"x":2}', '{"x":NaN}', '{"x":Infinity}'])
def test_strict_json(make_archive, text):
    archive = ReviewedArchive(make_archive({"a.json": text}), "qkd")
    with pytest.raises(ValueError):
        archive.json("a.json")


def test_no_pickle_or_script_execution_even_in_inventory(make_archive, monkeypatch):
    monkeypatch.setattr(pickle, "load", lambda *a, **k: pytest.fail("pickle executed"))
    monkeypatch.setattr(pickle, "loads", lambda *a, **k: pytest.fail("pickle executed"))
    archive = ReviewedArchive(make_archive({"detector.pkl": b"untrusted", "inference.py": b"raise Exception()"}), "qkd")
    assert archive.inventory()[0]["deserialization_blocked"] is True
    for name in ("detector.pkl", "inference.py"):
        with pytest.raises(ValueError, match="only JSON"):
            archive.read(name)


@pytest.mark.parametrize("array", [np.array([object()]), np.array([float("nan")]),
                                  np.array([1+2j]), np.array(["not numeric"])])
def test_unsafe_arrays(make_archive, array):
    stream = io.BytesIO()
    np.savez(stream, weight=array)
    archive = ReviewedArchive(make_archive({"weights.npz": stream.getvalue()}), "qkd")
    with pytest.raises(ValueError):
        archive.arrays("weights.npz")


def test_sync_predictions_are_typed_offline_and_timestamped(sync_archive):
    result = SyncReplayModel(sync_archive).predict(np.arange(8), np.zeros(8), np.ones(8)*20,
                                                 domain="simulated_offline")
    assert result.predicted_offset_s == 0.5e-9
    assert result.persistence_offset_s == 0
    assert result.last_timestamp_s == 7 and result.target_timestamp_s == 9
    assert result.physical_action_performed is False


@pytest.mark.parametrize("change", ["warmup", "gap", "reversed", "nan", "strings", "domain", "horizon"])
def test_sync_rejects_incompatible_inputs(sync_archive, change):
    times, offsets, temps = np.arange(8, dtype=float), np.zeros(8), np.ones(8)*20
    options = {"domain": "simulated_offline"}
    if change == "warmup":
        offsets = offsets[:7]
    elif change == "gap":
        times[-1] += 1
    elif change == "reversed":
        times = times[::-1]
    elif change == "nan":
        temps[2] = np.nan
    elif change == "strings":
        offsets = ["0"] * 8
    elif change == "domain":
        options["domain"] = "measured"
    else:
        options["horizon_s"] = 5
    with pytest.raises(ValueError):
        SyncReplayModel(sync_archive).predict(times, offsets, temps, **options)


def test_routing_sampling_matches_explicit_seed(routing_archive):
    model = RoutingReplayModel(routing_archive, "nominal")
    rng, expected_rng = np.random.default_rng(42), np.random.default_rng(42)
    for _ in range(12):
        result = model.evaluate(*observation(), domain="simulated_offline", rng=rng)
        assert result.probabilities == (0.5, 0.5)
        assert result.sampled_index == expected_rng.choice(2, p=[0.5, 0.5])
        assert result.physical_action_performed is False
        assert result.critic_return == 0


def test_long_deadline_greedy_does_not_consume_rng(routing_archive):
    rng = np.random.default_rng(42)
    before = json.dumps(rng.bit_generator.state)
    result = RoutingReplayModel(routing_archive, "prazo_longo").evaluate(
        *observation(), domain="simulated_offline", rng=rng)
    assert result.probabilities == (1.0, 0.0) and result.sampled_index == 0
    assert json.dumps(rng.bit_generator.state) == before


@pytest.mark.parametrize("change", ["nan", "shape", "empty", "count", "reordered", "kind", "domain", "rng"])
def test_routing_rejects_incompatible_inputs(routing_archive, change):
    state, features, actions = observation()
    domain, rng = "simulated_offline", np.random.default_rng(0)
    if change == "nan":
        state[0] = np.nan
    elif change == "shape":
        features = features[:, :22]
    elif change == "empty":
        actions = []
    elif change == "count":
        actions = actions[:1]
    elif change == "reordered":
        actions = actions[::-1]
    elif change == "kind":
        actions[0]["kind"] = "EXECUTE"
    elif change == "domain":
        domain = "measured"
    else:
        rng = None
    with pytest.raises(ValueError):
        RoutingReplayModel(routing_archive, "nominal").evaluate(state, features, actions, domain=domain, rng=rng)


def test_archive_domains_cannot_be_swapped(sync_archive, routing_archive):
    with pytest.raises(ValueError):
        RoutingReplayModel(sync_archive, "nominal")
    with pytest.raises(ValueError):
        SyncReplayModel(routing_archive)


@pytest.mark.parametrize("change", ["layers", "shape", "temperature", "features", "weight_shape", "missing"])
def test_routing_rejects_incompatible_model_metadata(routing_archive, monkeypatch, change):
    original_json, original_arrays = routing_archive.json, routing_archive.arrays
    def metadata(name):
        meta = original_json(name)
        if change == "layers":
            meta["layers"]["actor"] = 4
        elif change == "shape":
            meta["sizes"]["actor"][0] = 35
        elif change == "temperature":
            meta["temperature"] = 0.25
        elif change == "features":
            meta["features"]["state_count"] = 12
        return meta
    def arrays(name):
        values = original_arrays(name)
        if change == "weight_shape":
            values["actor_0"] = np.zeros((35, 64))
        elif change == "missing":
            values.pop("critic_5")
        return values
    monkeypatch.setattr(routing_archive, "json", metadata)
    monkeypatch.setattr(routing_archive, "arrays", arrays)
    with pytest.raises(ValueError):
        RoutingReplayModel(routing_archive, "nominal")


def test_routing_parity_detects_changed_expected_action(routing_archive, monkeypatch):
    original = routing_archive.json
    state, features, actions = observation()
    inputs = {"state_features": state.tolist(), "action_features": features.tolist(), "actions": actions}
    example = {"input": inputs, "expected": {"logits": [0, 0], "probabilities": [1, 0],
                                               "value": 0, "greedy_index": 0}}
    step = {**inputs, "probabilities": [1, 0], "chosen_index": 1}
    def data(name):
        if name.endswith("examples/examples.json"):
            return {"examples": [example]}
        if name.endswith("examples/episodes.json"):
            return {"episodes": [{"steps": [step]}], "temperature": 0, "policy_rng_seed": 0}
        return original(name)
    monkeypatch.setattr(routing_archive, "json", data)
    result = evaluation.evaluate_routing(routing_archive, "prazo_longo")
    assert result["passed"] is False and result["sampled_mismatches"] == 1


def test_sync_reference_timestamp_must_match(sync_archive, monkeypatch):
    original = sync_archive.read
    def data(name):
        if name.endswith("sincronizador_entrada.csv"):
            return ("time_s,offset_medido_s,temperatura_c\n" +
                    "\n".join(f"{i},0,20" for i in range(8))).encode()
        if name.endswith("sincronizador_saida.csv"):
            return b"time_s,offset_previsto_em_t+2s_s,offset_verdadeiro_em_t+2s_s\n8,0,0\n"
        return original(name)
    monkeypatch.setattr(sync_archive, "read", data)
    with pytest.raises(ValueError, match="timestamp"):
        evaluation.evaluate_sync(sync_archive)


def test_cli_never_overwrites_existing_report(tmp_path):
    path = tmp_path / "existing.json"
    path.write_text("keep", encoding="utf-8")
    with pytest.raises(SystemExit) as error:
        evaluation.main(["--qkd-zip", "missing", "--qnet-zip", "missing", "--output", str(path)])
    assert error.value.code == 2
    assert path.read_text(encoding="utf-8") == "keep"


def test_cli_failed_parity_is_nonzero_and_recorded(tmp_path, monkeypatch):
    monkeypatch.setattr(evaluation, "build_report", lambda *a: {"passed": False, "checks": []})
    path = tmp_path / "new.json"
    assert evaluation.main(["--qkd-zip", "unused", "--qnet-zip", "unused", "--output", str(path)]) == 1
    assert json.loads(path.read_text())["passed"] is False


def test_cli_rejection_does_not_create_pass_report(tmp_path):
    path = tmp_path / "new.json"
    with pytest.raises(SystemExit) as error:
        evaluation.main(["--qkd-zip", "missing", "--qnet-zip", "missing", "--output", str(path)])
    assert error.value.code == 2
    assert not path.exists()
