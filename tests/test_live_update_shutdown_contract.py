from types import SimpleNamespace
import pytest
from quantum_autopilot.console import live_update_validation as validation


def test_async_release_keeps_qt_turns_and_requires_worker_gone(monkeypatch):
    provider = SimpleNamespace(stopping=True, shutdown_error=None, _baseline_worker=object())
    turns=[]
    def turn(ms):
        turns.append(ms)
        if len(turns)==2:
            provider.stopping=False
            provider._baseline_worker=None
    monkeypatch.setattr(validation, "run_event_loop", turn)
    passed, elapsed=validation._wait_for_worker_release(provider)
    assert passed and turns==[5,5] and elapsed<=5000


@pytest.mark.parametrize("fault", ["pending", "worker", "error"])
def test_partial_or_failed_release_cannot_pass(monkeypatch, fault):
    provider=SimpleNamespace(stopping=fault=="pending", shutdown_error=RuntimeError("fixture") if fault=="error" else None,
                             _baseline_worker=object() if fault=="worker" else None)
    times=iter([0.,0.,6.,6.])
    monkeypatch.setattr(validation, "perf_counter", lambda: next(times))
    monkeypatch.setattr(validation, "run_event_loop", lambda ms: None)
    passed,_=validation._wait_for_worker_release(provider)
    assert passed is False


@pytest.mark.parametrize("timeout", [0,5001,True,float("nan")])
def test_deadline_not_relaxed(monkeypatch, timeout):
    with pytest.raises(ValueError):
        validation._wait_for_worker_release(None,timeout_ms=timeout)
