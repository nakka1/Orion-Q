"""The interactive worker must execute the unchanged baseline, exactly."""
import multiprocessing

import numpy as np
import pytest

from quantum_autopilot.control_plane import polarisation_loop as pol
from quantum_autopilot.network_engine.baseline_worker import BaselineWorker, batch_proposals, use_baseline_worker
from quantum_autopilot.network_engine.links import PolarisationChannel


@pytest.fixture(scope='module')
def worker():
    instance = BaselineWorker()
    yield instance
    pid = instance.process_id
    instance.close()
    assert pid not in {child.pid for child in multiprocessing.active_children()}


def readings(offset=0, count=500):
    times = offset + np.arange(count) * .002
    vectors = np.column_stack([.12*np.sin(times*3), .21*np.cos(times*4), .04*np.sin(times)])
    return times, np.asarray([pol.exp_map(vector) for vector in vectors]), np.zeros((count, 3))


def reference(controller, times, measured, covariates):
    values = np.full((len(times), 3, 3), np.nan)
    reasons = [None] * len(times)
    for index, at in enumerate(times):
        try:
            values[index] = controller.propose(float(at), measured[index], covariates[index])
        except (ValueError, ArithmeticError) as exc:
            reasons[index] = f'controller_error:{type(exc).__name__}'
    return values, reasons


def assert_identical(actual, expected):
    np.testing.assert_array_equal(actual, expected)
    assert actual.dtype == expected.dtype
    assert actual.shape == expected.shape
    assert actual.tobytes() == expected.tobytes(), 'IEEE representations must also match'


@pytest.mark.parametrize('gains', [(0, 0), (.6, .01), (.85, .15), (1, 1)])
def test_proposals_and_carried_state_are_bitwise_equal(worker, gains):
    original, offloaded = pol.AlphaBeta(*gains), pol.AlphaBeta(*gains)
    for offset in (0, 1, 2):
        times, measured, covariates = readings(offset)
        expected, reasons = reference(original, times, measured, covariates)
        with use_baseline_worker(worker):
            result = batch_proposals({'controller': offloaded}, times, measured, covariates)
        assert_identical(result['controller'][0], expected)
        assert result['controller'][1] == reasons
        assert_identical(offloaded._state, original._state)
        assert_identical(offloaded._velocity, original._velocity)
        assert offloaded._previous_time == original._previous_time


def test_supervisor_inputs_and_standby_match_original_interleaving(worker):
    direct = PolarisationChannel(pol.AlphaBeta(.6, .02), pol.AlphaBeta(.8, .01))
    separate = PolarisationChannel(pol.AlphaBeta(.6, .02), pol.AlphaBeta(.8, .01))
    for offset in (0, 1, 2):
        inputs = readings(offset)
        expected = direct._propose(*inputs)
        with use_baseline_worker(worker):
            actual = separate._propose(*inputs)
        for index in (0, 1):
            assert_identical(actual[index], expected[index])
        assert actual[2] == expected[2]
    direct.degraded = separate.degraded = True
    before = separate.controller._previous_time
    expected = direct._propose(*readings(3))
    with use_baseline_worker(worker):
        actual = separate._propose(*readings(3))
    assert_identical(actual[0], expected[0])
    assert_identical(actual[1], expected[1])
    assert separate.controller._previous_time == before


@pytest.mark.parametrize('case', ['custom', 'subclass', 'instance_override', 'alias'])
def test_custom_or_shared_controllers_keep_original_call_order(worker, case):
    controller = pol.AlphaBeta(.6, .02)
    if case == 'custom':
        class Custom:
            def propose(self, *_):
                return np.eye(3)
        controllers = {'controller': controller, 'fallback': Custom()}
    elif case == 'subclass':
        class CustomAlpha(pol.AlphaBeta):
            pass
        controllers = {'controller': CustomAlpha(.6, .02)}
    elif case == 'instance_override':
        controller.propose = lambda *_: np.eye(3)
        controllers = {'controller': controller}
    else:
        controllers = {'controller': controller, 'fallback': controller}
    with use_baseline_worker(worker):
        assert batch_proposals(controllers, *readings(count=8)) is None


def test_context_restores_after_exception_and_stays_thread_local(worker):
    from concurrent.futures import ThreadPoolExecutor
    inputs = readings(count=8)
    controllers = {'controller': pol.AlphaBeta(.6, .02)}
    with use_baseline_worker(worker):
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(batch_proposals, controllers, *inputs).result() is None
        with pytest.raises(ValueError):
            with use_baseline_worker(None):
                assert batch_proposals(controllers, *inputs) is None
                raise ValueError('restore')
        assert batch_proposals(controllers, *inputs) is not None
    assert batch_proposals(controllers, *inputs) is None


def test_numpy_error_policy_is_preserved(worker):
    original, separate = pol.AlphaBeta(.6, .02), pol.AlphaBeta(.6, .02)
    times, measured, covariates = readings(count=8)
    measured[2, :, :] = np.inf
    with np.errstate(all='raise'):
        expected, reasons = reference(original, times, measured, covariates)
        with use_baseline_worker(worker):
            actual = batch_proposals({'controller': separate}, times, measured, covariates)
    assert_identical(actual['controller'][0], expected)
    assert actual['controller'][1] == reasons


def test_modified_class_method_stays_in_the_calling_process(worker, monkeypatch):
    monkeypatch.setattr(pol.AlphaBeta, 'propose', lambda *_: np.eye(3))
    with use_baseline_worker(worker):
        assert batch_proposals({'controller': pol.AlphaBeta(.6, .02)}, *readings(count=8)) is None


@pytest.mark.parametrize('policy', ['call', 'log'])
def test_numpy_error_handlers_are_not_lost_at_the_process_boundary(worker, policy):
    import os
    messages = []
    class Log:
        def write(self, message):
            messages.append((os.getpid(), message))
    previous = np.geterrcall()
    handler = (lambda error, flag: messages.append((os.getpid(), error))) if policy == 'call' else Log()
    inputs = list(readings(count=8))
    inputs[1][2, :, :] = np.inf
    original = PolarisationChannel(pol.AlphaBeta(.6, .02), None)
    separate = PolarisationChannel(pol.AlphaBeta(.6, .02), None)
    try:
        np.seterrcall(handler)
        with np.errstate(all=policy):
            expected = original._propose(*inputs)
            messages.clear()
            with use_baseline_worker(worker):
                actual = separate._propose(*inputs)
            assert messages and all(pid == os.getpid() for pid, _message in messages)
        assert_identical(actual[0], expected[0])
        assert actual[2] == expected[2]
    finally:
        np.seterrcall(previous)


def test_worker_lifecycle_is_lazy_and_close_is_idempotent():
    instance = BaselineWorker()
    assert instance.process_id is None
    with use_baseline_worker(instance):
        assert batch_proposals({'controller': pol.Persistence()}, *readings(count=8)) is None
    assert instance.process_id is None
    instance.close()
    instance.close()
    with pytest.raises(RuntimeError, match='closed'):
        instance.propose({'controller': pol.AlphaBeta(.6, .02)}, *readings(count=8))


def test_worker_failure_is_reported_and_cleaned_up():
    instance = BaselineWorker()
    controller = {'controller': pol.AlphaBeta(.6, .02)}
    try:
        instance.propose(controller, *readings(count=8))
        instance._process.terminate()
        instance._process.join(timeout=5)
        with pytest.raises(RuntimeError, match='exited unexpectedly'):
            instance.propose(controller, *readings(count=8))
    finally:
        instance.close()
