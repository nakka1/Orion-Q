"""The author's Verilog against the author's fixed-point references (tools/verify_author_rtl.py)."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil

import numpy as np
import pytest

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[1]
RTL = ROOT / "external/qnet_network/rtl"
SUMMARY = ROOT / "docs/results/orion_q_author_rtl_20260930_summary.json"


@pytest.fixture(scope="module")
def tool():
    spec = importlib.util.spec_from_file_location("orion_verify_author_rtl", ROOT / "tools/verify_author_rtl.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def needs_simulator():
    if shutil.which("iverilog") is None or shutil.which("vvp") is None:
        pytest.skip("Icarus Verilog not installed")


def test_readme_tables_are_read(tool):
    ridge, mlp = tool.readme_layers(RTL / "ridge/README.md"), tool.readme_layers(RTL / "mlp/README.md")
    assert [(x["inputs"], x["outputs"], x["shift"], x["relu"]) for x in ridge] == [(48, 3, 15, False)]
    assert [(x["inputs"], x["outputs"], x["accumulator"]) for x in mlp] == [(48, 192, 38), (192, 192, 40), (192, 3, 40)]
    assert [x["output_fraction"] for x in mlp] == [10, 8, 4] and mlp[0]["input_fraction"] == 12


def test_mlp_reference_reproduces_the_goldens_only_with_the_recovered_bias_formats(tool):
    stimulus = tool.words(RTL / "mlp/vectors_in.hex").reshape(-1, 48)
    golden = tool.words(RTL / "mlp/vectors_out.hex").reshape(-1, 3)
    assert golden.shape == (64, 3)          # the network's output, not the first layer's
    network, _ = tool.fixed_network(RTL / "mlp", (13, 14, 15))
    assert np.array_equal(network.forward(stimulus), golden)
    for changed in ((13, 13, 15), (13, 15, 15), (13, 14, 14), (12, 14, 15)):
        network, _ = tool.fixed_network(RTL / "mlp", changed)
        assert not np.array_equal(network.forward(stimulus), golden)


def test_ridge_bench_of_the_author_passes(tool, tmp_path):
    needs_simulator()
    shutil.copytree(RTL / "ridge", tmp_path / "ridge")
    assert "PASS: 64 vectors x 3 outputs bit-exact" in tool.simulate(["qnet_tb.v", "qnet_mac.v"], tmp_path / "ridge")


def test_cordic_matches_the_reference_on_the_log_map_domain(tool, tmp_path):
    needs_simulator()
    tally = tool.cordic(tmp_path, RTL / "ridge/qnet_cordic.v", count=150)
    assert tally["vector_domain"]["mismatches"] == 0 and tally["rotation"]["mismatches"] == 0
    assert tally["vector_edge"] == {"cases": 6, "mismatches": 0}
    assert tally["vector_outside_q4"]["mismatches"] == 0 and tally["vector_outside_q3"]["mismatches"] > 0


def test_results_summary_records_every_check_passing():
    summary = json.loads(SUMMARY.read_text(encoding="utf-8"))
    compensator = summary["compensator"]
    assert compensator["ridge_author_bench"]["passed"] and not compensator["mlp_author_bench"]["passed"]
    assert compensator["mlp_chain"]["passed"] and compensator["mlp_chain"]["bias_fractions"] == [13, 14, 15]
    assert summary["qkd_sync"]["passed"] and all(summary["qkd_sync"]["identity"][key] for key in
                                                 ("coefficient_codes_equal", "coefficients.hex_identical",
                                                  "intercept.hex_identical"))
    assert summary["qkd_trees"]["passed"] and all(summary["qkd_trees"]["memories_identical"].values())
    for path, digest in summary["rtl_sources_sha256"].items():
        assert hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == digest
