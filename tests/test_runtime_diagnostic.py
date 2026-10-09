import json

import pytest

from quantum_autopilot.control_plane.runtime_diagnostic import percentiles, run_diagnostic


def test_declared_quantiles_and_empty_stage():
    result = percentiles([1.0, 2.0, 3.0, 4.0, 5.0])
    assert result["p50"] == 3.0 and result["p95"] == pytest.approx(4.8)
    assert result["p99"] == pytest.approx(4.96) and result["max"] == 5.0
    assert percentiles([]) is None


def test_real_isolated_cycles_and_insufficient_sampling(tmp_path):
    output = tmp_path / "diagnostic"
    report = run_diagnostic(output, samples=3, warmup=1, budget_ms=0.000001)
    assert report["failed_attempts"] == 0
    assert report["budget_exceeded"] == 3 and report["budget_exceeded_fraction"] == 1
    assert report["adequate_p99_sampling"] is False
    assert len(report["attempts"]) == 3
    assert all(report["stages_ms"][stage]["n"] == 3 for stage in report["stages_ms"])
    assert json.loads((output / "diagnostic.json").read_text()) == report
    with pytest.raises(FileExistsError):
        run_diagnostic(output, samples=1)


def test_failed_cycles_are_retained(tmp_path):
    from quantum_autopilot.control_plane.runtime import ControllerRuntime

    class Broken:
        name = "broken diagnostic policy"

        def decide(self, snapshot):
            raise ValueError("fault injected")

    report = run_diagnostic(tmp_path / "failed", samples=2, warmup=0,
                            runtime_factory=lambda path: ControllerRuntime(path, policy=Broken()))
    assert report["failed_attempts"] == 2
    assert len(report["attempts"]) == 2 and report["stages_ms"]["policy"]["n"] == 2
    assert report["stages_ms"]["execution"] is None


@pytest.mark.parametrize("settings", [{"samples": True}, {"samples": 5001}, {"warmup": -1},
                                      {"budget_ms": float("nan")}, {"condition": "physical"}])
def test_invalid_diagnostic_requests_leave_no_output(tmp_path, settings):
    output = tmp_path / "invalid"
    with pytest.raises(ValueError):
        run_diagnostic(output, **settings)
    assert not output.exists()
