"""Frozen QKD closed-loop protocol, published reference tables and campaign statistics."""
import json
from pathlib import Path

import numpy as np
import pytest

from quantum_autopilot.control_plane import qkd_campaign as campaign

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "configs/optional_models/qkd_closed_loop_v1.json"
THESIS = ROOT / "docs/thesis/monografia/main.tex"


def test_protocol_file_is_the_frozen_protocol(tmp_path):
    assert campaign.load_protocol(PROTOCOL) == (PROTOCOL.read_bytes(), campaign.PROTOCOL)
    changed = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    changed["keyrate"]["evaluation_plant_seeds"] = changed["keyrate"]["evaluation_plant_seeds"][:-1]
    path = tmp_path / "changed.json"
    path.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(ValueError, match="differs from frozen v1"):
        campaign.load_protocol(path)
    with pytest.raises(ValueError, match="differs from frozen v1"):
        campaign.run_campaign("never-read.zip", path, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_evaluation_seeds_are_new():
    keyrate, sync = campaign.PROTOCOL["keyrate"], campaign.PROTOCOL["sync"]
    author_keyrate = set(range(10_000)) | {700_000} | set(range(900_000, 900_048))
    assert len(keyrate["evaluation_plant_seeds"]) == 20 and not set(keyrate["evaluation_plant_seeds"]) & author_keyrate
    author_sync = set(range(8)) | set(range(500, 512))
    assert len(sync["evaluation_seeds"]) == 20 and not set(sync["evaluation_seeds"]) & author_sync


def _thesis_number(text):
    return float(text.replace(",", "."))


def test_published_tables_are_the_thesis_tables():
    """The reference values the gates compare against are the ones printed in the TCC."""
    import re
    if not THESIS.exists():
        pytest.skip("thesis source not present")
    tex = THESIS.read_text(encoding="utf-8")
    for span, values in campaign.PUBLISHED_KEYRATE.items():
        row = re.search(rf"^{span}~km & (.+?)\\\\", tex, re.M).group(1)
        numbers = [_thesis_number(value) for value in re.findall(r"\d+,\d+", row)]
        assert numbers[:6] == [values["baseline.static"][0], values["baseline.static"][1],
                               values["qkd.keyrate.actor"][0], values["qkd.keyrate.actor"][1],
                               values["reference.oracle"][0], values["reference.oracle"][1]]
    table = tex[tex.index("label{tab:sinc}"):tex.index("end{tabular}", tex.index("label{tab:sinc}"))]
    rows = [[_thesis_number(value) for value in re.findall(r"\d+,\d+", line)]
            for line in table.splitlines() if "~ps" in line]
    published = [values for dark in ("10", "1000000") for values in campaign.PUBLISHED_SYNC[dark].values()]
    assert rows == published


def test_comparison_statistics_and_gap():
    generator = np.random.default_rng(1)
    static = 1e6 + 1e5 * generator.random(10)
    actor, oracle = static * 1.15, static * 1.25
    row = campaign.compare(actor, static, np.random.default_rng(2), reference_values=oracle)
    assert row["wins"] == 10 and row["p_value"] == pytest.approx(1 / 1024)
    assert row["model_to_baseline_ratio"] == pytest.approx(1.15)
    assert row["ratio_ci95"][0] <= 1.15 <= row["ratio_ci95"][1]
    assert row["gap_fraction_closed"] == pytest.approx(0.6)
    rows = campaign.holm([dict(row), dict(row, p_value=0.2)])
    assert rows[0]["significant"] and not rows[1]["significant"] and rows[1]["holm_p_value"] == 0.2


def test_no_invented_significance_on_ties():
    values = [2e6, 2.1e6, 1.9e6, 2e6]
    row = campaign.compare(values, values, np.random.default_rng(0))
    assert row["p_value"] == 1.0 and row["wins"] == 0 and row["ties"] == 4
