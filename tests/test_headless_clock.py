"""The backend processes independently of clients, with bounded scheduling."""
from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
from threading import Event, Thread
import time
from types import SimpleNamespace

import pytest

from quantum_autopilot.qkd.dashboard_api import AssuranceDashboardService
from quantum_autopilot.security import AuthenticationError, SecurityRuntime
from quantum_autopilot.security.secret_providers import AESGCMSecretProvider
from quantum_autopilot.security.service_identity import ServiceIdentityBinding, ServiceIdentityProvision
from quantum_autopilot.service.clock import HeadlessClock


ROOT = Path(__file__).resolve().parents[1]


def wait_for(predicate, *, timeout_s=5):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.005)
    assert predicate(), "bounded clock condition was not reached"


class StepFixture:
    def __init__(self, operation=None):
        self.security = SimpleNamespace(identity_profile="service", profile="operational")
        self.operation = operation
        self.tick = 0
        self.calls = []

    def network_step(self, *, metadata_only=False):
        self.calls.append(metadata_only)
        if self.operation:
            self.operation()
        self.tick += 1
        return {"tick": self.tick, "network_time_s": float(self.tick),
                "publication_revision": self.tick, "source": {"kind": "none"},
                "protocol_clock_s": float(self.tick), "protocol_backlog_s": 0.0}


class FixtureKeys:
    def __init__(self):
        self.value = os.urandom(32)

    def read_key(self, key_id, version):
        assert (key_id, version) == ("clock-fixture", 1)
        return self.value


@pytest.fixture
def service_binding(tmp_path, monkeypatch):
    import quantum_autopilot.security.runtime as runtime_module
    import quantum_autopilot.security.vault as vault_module
    def forbidden(*_args, **_kwargs):
        raise AssertionError("headless clock consulted Windows session or DPAPI")
    monkeypatch.setattr(runtime_module, "operating_system_identity", forbidden)
    monkeypatch.setattr(vault_module, "_dpapi", forbidden)
    provider = AESGCMSecretProvider(FixtureKeys(), active_key_id="clock-fixture", active_version=1)
    admin_secret, service_secret = os.urandom(48), os.urandom(48)
    administrator = SecurityRuntime(tmp_path / "security", profile="operational", identity_profile="service",
        secret_provider=provider, bootstrap=True,
        service_bootstrap=ServiceIdentityProvision("clock-admin", ("administrator",), ("*",), admin_secret))
    admin = administrator.authenticate_service("clock-admin", admin_secret)
    with administrator.identity_scope(admin):
        administrator.grant_service_identity("clock-core", ("operator",), ("*",), service_secret)
        administrator.grant_identity("clock-reader", ("auditor",), ("*",), source="service")
    runtime = SecurityRuntime(tmp_path / "security", profile="operational", identity_profile="service",
        bootstrap=False, secret_provider=provider,
        service_identity=ServiceIdentityBinding("clock-core", lambda: service_secret), process_identity_renewal=True)
    service = AssuranceDashboardService(ROOT, control_journal_path=tmp_path / "workspace.sqlite", security=runtime)
    yield service, administrator, admin
    service._release_controller_io_resources()
    if service._network_engine is not None:
        service._network_engine.mission_receiver.close()


@contextmanager
def running(service, **kwargs):
    clock = HeadlessClock(service, **kwargs).start()
    try:
        yield clock
    finally:
        clock.stop()


@pytest.mark.parametrize("period", [False, 0, -1, math.inf, math.nan, .0001, 3601])
def test_clock_rejects_invalid_cadence_before_start(period):
    with pytest.raises(ValueError):
        HeadlessClock(StepFixture(), period_s=period)


def test_clock_requires_service_identity_and_keeps_default_one_second():
    clock = HeadlessClock(StepFixture())
    assert clock.status()["period_s"] == 1
    assert clock.status()["nominal_time_scale"] == 1
    assert clock.status()["catch_up_queue_size"] == 0
    desktop = StepFixture()
    desktop.security.identity_profile = "os"
    with pytest.raises(ValueError, match="service identity"):
        HeadlessClock(desktop)


def test_one_worker_per_service_and_metadata_only_processing():
    service = StepFixture()
    with running(service, period_s=.01) as clock:
        wait_for(lambda: clock.status()["processed_steps"] >= 3)
        with pytest.raises(RuntimeError, match="already has"):
            HeadlessClock(service, period_s=.01).start()
        with pytest.raises(RuntimeError, match="started once"):
            clock.start()
    status = clock.status()
    assert status["state"] == "stopped" and not status["active"]
    assert status["processed_steps"] == service.tick == len(service.calls)
    assert all(service.calls)


def test_slow_processing_records_overload_without_skipping_network_ticks():
    service = StepFixture(lambda: time.sleep(.035))
    with running(service, period_s=.01) as clock:
        wait_for(lambda: clock.status()["processed_steps"] >= 3)
    status = clock.status()
    assert status["overload_count"] >= 3
    assert status["missed_scheduler_deadlines"] >= 6
    assert status["last_duration_s"] >= .03
    assert status["processed_steps"] == service.tick
    assert status["last_tick"] == service.tick
    assert status["catch_up_queue_size"] == 0
    assert status["nominal_time_scale"] == 100


def test_hold_hands_clock_to_existing_calibration_without_double_ticks():
    service = StepFixture()
    with running(service, period_s=.01) as clock:
        wait_for(lambda: clock.status()["processed_steps"] >= 2)
        with clock.hold("calibration"):
            before = service.tick
            time.sleep(.035)
            assert service.tick == before
            with clock.hold("calibration phase"):
                assert clock.status()["hold_depth"] == 2
                # An existing blocking procedure remains responsible for its
                # own step. The scheduler does not run a second cadence.
                service.network_step(metadata_only=True)
            time.sleep(.035)
            assert service.tick == before + 1
            assert clock.status()["state"] == "paused"
        resumed = clock.status()["processed_steps"]
        wait_for(lambda: clock.status()["processed_steps"] > resumed)
    status = clock.status()
    assert status["hold_count"] == 2 and status["hold_depth"] == 0
    assert status["hold_duration_s"] >= .06
    assert service.tick == status["processed_steps"] + 1


def test_holds_are_exclusive_between_procedures():
    with running(StepFixture(), period_s=.01) as clock:
        entered, finished = Event(), Event()
        def competitor():
            with pytest.raises(TimeoutError, match="another procedure"):
                with clock.hold("second procedure", timeout_s=.03):
                    entered.set()
            finished.set()
        with clock.hold("first procedure"):
            thread = Thread(target=competitor)
            thread.start()
            assert finished.wait(1)
            assert not entered.is_set()
        thread.join(1)
        assert not thread.is_alive()


def test_handoff_and_stop_timeouts_never_claim_processing_was_cancelled():
    entered, release = Event(), Event()
    def blocked():
        entered.set()
        assert release.wait(3)
    service = StepFixture(blocked)
    clock = HeadlessClock(service, period_s=.01).start()
    try:
        assert entered.wait(1)
        with pytest.raises(TimeoutError, match="in-flight"):
            with clock.hold("calibration", timeout_s=.02):
                pytest.fail("the caller's procedure must never run after timeout")
        assert clock.status()["hold_depth"] == 0
        with pytest.raises(TimeoutError, match="resources must remain open"):
            clock.stop(timeout_s=.02)
        assert clock.status()["active"] and clock.status()["state"] == "stopping"
        assert clock.status()["stop_timeout_count"] == 1
    finally:
        release.set()
        clock.stop(timeout_s=1)
    assert clock.status()["processed_steps"] == 1
    assert clock.status()["state"] == "stopped"


def test_failure_blocks_clock_without_logging_exception_content():
    def fail():
        raise ValueError("private test payload must never enter status")
    with running(StepFixture(fail), period_s=.01) as clock:
        wait_for(lambda: clock.status()["state"] == "blocked")
    status = clock.status()
    assert status["last_error_type"] == "ValueError"
    assert status["attempts"] == 1 and status["processed_steps"] == 0
    assert "private test payload" not in json.dumps(status)


def test_stop_waits_for_procedure_that_temporarily_owns_the_clock():
    clock = HeadlessClock(StepFixture(), period_s=.01).start()
    entered, release = Event(), Event()
    def procedure():
        with clock.hold("calibration"):
            entered.set()
            assert release.wait(3)
    thread = Thread(target=procedure)
    thread.start()
    try:
        assert entered.wait(1)
        with pytest.raises(TimeoutError, match="procedure still owns"):
            clock.stop(timeout_s=.02)
        assert clock.status()["state"] == "stopping"
        assert clock.status()["hold_depth"] == 1
    finally:
        release.set()
        thread.join(1)
        clock.stop(timeout_s=1)
    assert clock.status()["state"] == "stopped"


def test_real_core_without_source_never_invents_fidelity(service_binding):
    service, _administrator, _admin = service_binding
    with running(service, period_s=.03) as clock:
        wait_for(lambda: clock.status()["processed_steps"] >= 2)
        state = service.network_live_state()
        assert state["source"]["kind"] == "none"
        assert all(link["qber"] is None and link["fidelity"] is None for link in state["links"])
        assert service.health()["physical_read_only"] is True
    assert clock.status()["last_source_kind"] == "none"


def test_real_core_continues_when_observer_disconnects_and_does_not_inherit_caller(service_binding):
    service, _administrator, _admin = service_binding
    service.configure_network_input("simulated", seed=17, scenario="quiet")
    reader = service.security.principal("clock-reader")
    with service.security.identity_scope(reader):
        # Inheriting this auditor context would prevent network.operate. The
        # worker instead uses the separately configured operator binding.
        clock = HeadlessClock(service, period_s=.03).start()
        try:
            wait_for(lambda: clock.status()["processed_steps"] >= 2)
            observer_snapshot = service.console_snapshot(include_static=False)
            observed_tick = observer_snapshot["live"]["tick"]
            del observer_snapshot  # The observer no longer owns any processing.
            wait_for(lambda: clock.status().get("last_tick", 0) > observed_tick)
        finally:
            clock.stop()
    final = service.network_live_state()
    assert final["tick"] > observed_tick
    assert any(link["qber"] is not None for link in final["links"])
    assert service.security.audit.verify()["valid"]


def test_revocation_blocks_real_core_before_next_mutation(service_binding):
    service, administrator, admin = service_binding
    service.configure_network_input("simulated", seed=17, scenario="quiet")
    with running(service, period_s=.03) as clock:
        wait_for(lambda: clock.status()["processed_steps"] >= 2)
        with clock.hold("identity revocation"):
            before = service.network_engine.live_state().tick
            with administrator.identity_scope(admin):
                administrator.revoke_identity("clock-core")
        wait_for(lambda: clock.status()["state"] == "blocked")
        assert service.network_engine.live_state().tick == before
        assert clock.status()["last_error_type"] == "AuthenticationError"
        time.sleep(.06)
        assert service.network_engine.live_state().tick == before
    assert administrator.audit.verify()["valid"]


def test_clock_renews_own_identity_before_long_cadence_and_during_handoff(service_binding):
    service, _, _ = service_binding
    runtime = service.security
    original_binding = runtime._process_binding
    original = runtime.bind_service_identity(ServiceIdentityBinding(
        original_binding.subject, original_binding.credential_reader, ttl_seconds=1))
    with running(service, period_s=3) as clock:
        with clock.hold("long calibration"):
            # The maintenance wake-up remains independent of protocol cadence
            # and an exclusive engine handoff; no scientific ticks are added.
            wait_for(lambda: time.time() > original.expires_at + .1, timeout_s=3)
            assert clock.status()["state"] == "paused"
            assert clock.status()["processed_steps"] == 0
            assert runtime._process_identity.session_id != original.session_id
            assert runtime._process_identity.expires_at > time.time()
        assert clock.status()["processed_steps"] == 0
    assert runtime.audit.verify()["valid"]


def test_clock_renewal_never_adopts_observer_or_device_authority(service_binding):
    service, _, _ = service_binding
    runtime = service.security
    service.configure_network_input("simulated", seed=17, scenario="quiet")
    service.network_step(metadata_only=True)
    binding = runtime._process_binding
    original = runtime.bind_service_identity(ServiceIdentityBinding(binding.subject, binding.credential_reader, ttl_seconds=2))
    device = runtime.device_session_identity()
    reader = runtime.principal("clock-reader")
    with runtime.identity_scope(reader):
        with running(service, period_s=.03) as clock:
            wait_for(lambda: time.time() > original.expires_at + .1 and clock.status()["processed_steps"] >= 2,
                     timeout_s=6)
            assert clock.status()["processed_steps"] >= 2, clock.status()
            assert clock.status()["state"] != "blocked"
            assert runtime._process_identity.subject == "clock-core"
            assert runtime._process_identity.session_id != original.session_id
            assert runtime._device_service_identity.session_id == device.session_id
            with pytest.raises(AuthenticationError):
                runtime.device_session_identity()
    assert runtime.audit.verify()["valid"]
