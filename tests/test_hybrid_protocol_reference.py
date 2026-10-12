"""Independent BBM92 component oracles and observation pipeline regression."""
from __future__ import annotations

import copy
import math
import random

import pytest

from tools.hybrid_protocol_campaign import ingress_compare, process_checked, run_campaign
from tools.hybrid_protocol_reference import (
    aligned_tags,
    finite_reference,
    observation_reference,
    serfling_log_tail,
    toeplitz_gf2,
)

pytestmark = pytest.mark.unit


def config():
    from quantum_autopilot.quantum_protocols.contracts import ProtocolConfig

    return ProtocolConfig("independent-bbm92-fixture", ("satellite_doppler",), ("OGS-A", "OGS-B"),
                          ("OGS-A", "SAT-FIXTURE", "OGS-B"), ("link-a", "link-b"),
                          {"coincidence_window_s": 1e-9, "sync_tolerance_s": 1e-6})


def capture(left, right, detectors=None):
    raw = {"satellite_schema": "orion.satellite-observation.v2", "start_s": 0.0, "end_s": 1.0,
           "emitted_pairs": max(len(left), len(right)),
           "housekeeping": {"frame": "ECEF", "ecef_km": [6878.137, 0.0, 0.0],
                            "velocity_ecef_km_s": [0.0, 7.0, 0.0], "at_s": 0.0,
                            "valid_until_s": 1.0, "source": "synthetic fixture, not mission telemetry"},
           "tracking": [], "tdc": [],
           "geometry": [{"range_km": 500.0, "elevation_deg": 90.0, "visible": True}] * 2,
           "public_randomness": {"seed_hex": "00000000000000000000000000000007", "origin": "reproducible synthetic fixture"}}
    for i, stamps in enumerate((left, right)):
        station = ("OGS-A", "OGS-B")[i]
        raw["tracking"].append({"station_id": station, "locked": True, "at_s": 0.0,
                                "open_intervals_s": [[0.0, 1.0]]})
        raw["tdc"].append({"station_id": station, "time_ps": list(stamps),
                           "detector": list(detectors[i]) if detectors else [0] * len(stamps),
                           "epoch_utc_s": 1700000000, "epoch_fraction_ps": 0, "clock_offset_ps": 0,
                           "propagation_delay_ps": 0, "clock_uncertainty_ps": 0,
                           "clock_reference": "fixture clock", "lost_tags": 0})
    return raw


def test_exact_gf2_known_vector():
    assert toeplitz_gf2([1, 0, 1], [1, 0, 1, 1], 2) == [0, 1]
    assert toeplitz_gf2([0, 0, 0], [1, 0, 1, 1], 2) == [0, 0]


@pytest.mark.parametrize("n,m", [(1, 1), (3, 2), (31, 34), (63, 127), (1024, 1024)])
def test_gf2_bitpack_agrees_explicit_matrix(n, m):
    rng = random.Random(123)
    bits = [rng.randrange(2) for _ in range(n)]
    seed = [rng.randrange(2) for _ in range(n + m - 1)]
    expected = [sum(bits[j] * seed[n - 1 + i - j] for j in range(n)) % 2 for i in range(m)]
    assert toeplitz_gf2(bits, seed, m) == expected


@pytest.mark.parametrize("bits,seed,length", [([1], [], 1), ([2], [1], 1), ([], [], 1), ([1], [1], -1)])
def test_gf2_rejects_malformed_input(bits, seed, length):
    with pytest.raises(ValueError):
        toeplitz_gf2(bits, seed, length)


@pytest.mark.parametrize("left,right,expected", [
    ([1000], [1500], 1), ([1000], [1501], 0),
    ([1000, 1001, 1002, 5000], [1001, 5000], 1),
    ([1000], [1000, 1001], 0), ([], [1000], 0),
    ([1000, 1000], [1000], 0),
])
def test_graph_rejects_ambiguity_and_honours_closed_gate(left, right, expected):
    raw = capture(left, right)
    assert observation_reference(raw, config().parameters)["coincidences"] == expected
    actual, reference, checks, _, _ = process_checked(raw, config())
    assert actual["coincidences"] == reference["coincidences"] == expected
    assert all(checks.values())


def test_graph_sparse_query_matches_dense_adjacency_many_random_clusters():
    rng = random.Random(171)
    for _ in range(100):
        left = sorted(rng.sample(range(100, 5000), rng.randrange(1, 20)))
        right = sorted(rng.sample(range(100, 5000), rng.randrange(1, 20)))
        adjacency = [[abs(a - b) <= 500 for b in right] for a in left]
        expected = sum(sum(row) == 1 and sum(adjacency[k][row.index(True)] for k in range(len(left))) == 1
                       for row in adjacency)
        ref = observation_reference(capture(left, right), config().parameters)
        assert ref["coincidences"] == expected
        assert all(process_checked(capture(left, right), config())[2].values())


def test_sifting_does_not_turn_mismatched_bases_into_bits():
    raw = capture([1000, 10000, 20000, 30000], [1000, 10000, 20000, 30000],
                  ([0, 2, 0, 3], [1, 2, 2, 2]))
    ref = observation_reference(raw, config().parameters)
    assert ref["coincidences"] == 4
    assert ref["sifted_bits"] == 3
    assert ref["key_basis_bits"] == 1 and ref["parameter_sample_bits"] == 2
    assert ref["qber"] == 2 / 3
    assert all(process_checked(raw, config())[2].values())


def test_integer_epoch_correction_preserves_picosecond_correlations():
    raw = capture([1000, 10000], [1000, 10000])
    initial = observation_reference(raw, config().parameters)
    raw["tdc"][1]["epoch_utc_s"] += 1
    raw["tdc"][1]["time_ps"] = [t - 10**12 for t in raw["tdc"][1]["time_ps"]]
    assert observation_reference(raw, config().parameters) == initial
    assert all(process_checked(raw, config())[2].values())


def test_integer_alignment_rejects_wrap():
    raw = capture([1000], [1000])
    batch = copy.deepcopy(raw["tdc"][1])
    batch["epoch_utc_s"] = 10**11
    with pytest.raises(ValueError, match="signed 64-bit"):
        aligned_tags(batch, (1700000000, 0))


def test_open_window_is_half_open_and_clips_acquisition_interval():
    raw = capture([0, 499999999999, 500000000000, 1000000000000],
                  [0, 499999999999, 500000000000, 1000000000000])
    for track in raw["tracking"]:
        track["open_intervals_s"] = [[0.0, 0.5]]
    assert observation_reference(raw, config().parameters)["coincidences"] == 2
    assert all(process_checked(raw, config())[2].values())


@pytest.mark.parametrize("n,k,errors,leak", [(100, 20, 1, 50), (10000, 5000, 25, 1500), (1000000, 1000000, 10000, 150000)])
def test_numerical_tail_inversion_and_entropy_budget_match_backend(n, k, errors, leak):
    from quantum_autopilot.quantum_protocols.satellite_processing import finite_budget

    expected = finite_reference(n, k, errors, leak, 34)
    actual = finite_budget(n, k, errors, leak, 34, {})
    assert expected["budget_bits"] == actual["budget_bits"]
    assert expected["sampling_deviation"] == pytest.approx(actual["sampling_deviation"], rel=1e-14)
    assert serfling_log_tail(n, k, expected["sampling_deviation"]) == pytest.approx(math.log(1e-10))
    assert expected["smoothing_parameter"] == 1e-5


def test_serfling_bound_covers_exact_small_hypergeometric_population():
    n, k = 5, 5
    denominator = math.comb(n + k, k)
    for total_errors in range(n + k + 1):
        for deviation in (0.1, 0.3, 0.5, 0.7):
            probability = 0.0
            for sample_errors in range(k + 1):
                remaining = total_errors - sample_errors
                if 0 <= remaining <= n and remaining / n >= sample_errors / k + deviation:
                    probability += math.comb(total_errors, sample_errors) * math.comb(n + k - total_errors, k - sample_errors) / denominator
            assert probability <= math.exp(serfling_log_tail(n, k, deviation)) + 1e-14


@pytest.mark.parametrize("n,k", [(0, 100), (100, 0), (100, 19)])
def test_finite_inadequate_sample_aborts(n, k):
    assert finite_reference(n, k, 0, 0, 34)["budget_bits"] == 0


def test_reconciliation_transcript_and_privacy_amplification_exactly_replayed():
    n = 8000
    times = [10000 * i for i in range(n)]
    detectors_a = [2 * (i % 2) + (i // 2) % 2 for i in range(n)]
    detectors_b = detectors_a.copy()
    for i in range(0, n, 200):
        detectors_b[i] ^= 1
    actual, _, checks, hashes, transcript = process_checked(capture(times, times, (detectors_a, detectors_b)), config())
    assert all(checks.values())
    assert actual["reconciliation_corrected_bits"] > 0
    assert actual["conditional_key_estimate_bits"] > 0
    assert len(hashes) == 4 and transcript
    assert actual["certified_secret_bits"] == 0


def test_origin_changes_only_adapter_not_protocol_processing(tmp_path):
    from tools.hybrid_protocol_campaign import make_command

    cfg = config()
    raw = capture([1000, 10000], [1000, 10000])
    checks, evidence = ingress_compare(tmp_path, raw, make_command(cfg, "synthetic-origin", 1.0), cfg)
    assert all(checks.values())
    assert evidence["physical_measurement"] is False
    assert evidence["physical_hardware_validated"] is False


def test_campaign_keeps_failures_and_refuses_existing_output(tmp_path):
    output = tmp_path / "invalid-seed"
    result = run_campaign(output, seeds=(-1,))
    assert not result["passed"] and "error" in result
    assert (output / "report.json").is_file()
    with pytest.raises(FileExistsError):
        run_campaign(output, seeds=(1001,))


@pytest.mark.parametrize("seeds", [(), (1, 1), (True,), (-1,)])
def test_campaign_cannot_pass_vacuously_or_count_duplicate_seeds(tmp_path, seeds):
    result = run_campaign(tmp_path / "invalid-campaign", seeds=seeds)
    assert not result["passed"] and result["case_count"] == 0
    assert "distinct nonnegative integer" in result["error"]
