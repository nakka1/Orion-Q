"""Official Windows runs are compared with the published cloud summaries number by number."""
import importlib.util
import json
from pathlib import Path
import shutil

import pytest

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def tool():
    spec = importlib.util.spec_from_file_location("orion_compare_summaries", ROOT / "tools/compare_campaign_summaries.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _copy(tool, tmp_path, name):
    target = tmp_path / name
    target.mkdir()
    shutil.copy(ROOT / "docs/results" / tool.CAMPAIGNS[name], target / "summary.json")
    return target / "summary.json"


def test_every_published_summary_exists(tool):
    assert all((ROOT / "docs/results" / name).is_file() for name in tool.CAMPAIGNS.values())


def test_identical_run_passes_and_machine_fields_are_ignored(tool, tmp_path):
    path = _copy(tool, tmp_path, "detector")
    summary = json.loads(path.read_text(encoding="utf-8"))
    summary["manifest"]["archive_sha256"] = "0" * 64
    summary["manifest"]["source_sha256"] = {}
    summary["evaluation"][0]["primary"]["mean_difference"] *= 1 + 1e-12
    path.write_text(json.dumps(summary), encoding="utf-8")
    row = tool.compare_campaign("detector", tmp_path / "detector")
    assert row["status"] == "identical" and row["values_checked"] > 200


def test_numbers_flags_and_protocol_changes_are_caught(tool, tmp_path):
    path = _copy(tool, tmp_path, "qkd")
    summary = json.loads(path.read_text(encoding="utf-8"))
    summary["keyrate"]["comparisons"][0]["holm_p_value"] *= 1.001
    summary["keyrate"]["comparisons"][1]["significant"] = not summary["keyrate"]["comparisons"][1]["significant"]
    summary["manifest"]["protocol"]["keyrate"]["evaluation_plant_seeds"] = []
    path.write_text(json.dumps(summary), encoding="utf-8")
    row = tool.compare_campaign("qkd", tmp_path / "qkd")
    paths = [item[0] for item in row["first_differences"]]
    assert row["status"] == "different" and row["differences"] == 3
    assert any("holm_p_value" in path for path in paths) and any("significant" in path for path in paths)
    assert any("evaluation_plant_seeds" in path for path in paths)


def test_synthesis_counts_never_fail(tool, tmp_path):
    path = _copy(tool, tmp_path, "rtl")
    summary = json.loads(path.read_text(encoding="utf-8"))
    summary["synthesis"][0]["luts"] += 7
    path.write_text(json.dumps(summary), encoding="utf-8")
    row = tool.compare_campaign("rtl", tmp_path / "rtl")
    assert row["status"] == "identical" and row["informational_differences"]


def test_command_line_report(tool, tmp_path):
    _copy(tool, tmp_path, "routing")
    assert tool.main(["--official", str(tmp_path), "--only", "routing"]) == 0
    assert json.loads((tmp_path / "comparison.json").read_text(encoding="utf-8"))["passed"] is True
    assert tool.main(["--official", str(tmp_path), "--only", "routing", "allocation"]) == 1


def test_platform_markers_and_conclusions_reading(tool, tmp_path):
    path = _copy(tool, tmp_path, "polarisation-actuator")
    summary = json.loads(path.read_text(encoding="utf-8"))
    row = summary["comparisons"][0]
    row["model_mean_qber"] *= 1 + 2e-5
    path.write_text(json.dumps(summary), encoding="utf-8")
    result = tool.compare_campaign("polarisation-actuator", tmp_path / "polarisation-actuator")
    assert result["status"] == "different" and result["conclusions_identical"] is True
    row["model_wins"] += 1
    path.write_text(json.dumps(summary), encoding="utf-8")
    result = tool.compare_campaign("polarisation-actuator", tmp_path / "polarisation-actuator")
    assert result["conclusions_identical"] is False

    path = _copy(tool, tmp_path, "detector-causal")
    summary = json.loads(path.read_text(encoding="utf-8"))
    summary["evaluation"][0]["dataset_csv_sha256"] = "0" * 64
    path.write_text(json.dumps(summary), encoding="utf-8")
    result = tool.compare_campaign("detector-causal", tmp_path / "detector-causal")
    assert result["status"] == "identical" and result["platform_differences"]


def test_generated_csv_hashes_are_platform_markers(tool, tmp_path):
    path = _copy(tool, tmp_path, "detector")
    summary = json.loads(path.read_text(encoding="utf-8"))
    summary["reproduction"]["dataset_seed_0_csv_sha256"] = "0" * 64
    summary["evaluation"][0]["dataset_csv_sha256"] = "1" * 64
    path.write_text(json.dumps(summary), encoding="utf-8")
    result = tool.compare_campaign("detector", tmp_path / "detector")
    assert result["status"] == "identical" and result["conclusions_identical"] is True
    assert len(result["platform_differences"]) == 2
    summary["safe_export"]["npz_sha256"] = "2" * 64      # an exported artefact's hash is not a platform marker
    path.write_text(json.dumps(summary), encoding="utf-8")
    assert tool.compare_campaign("detector", tmp_path / "detector")["status"] == "different"
