"""No-Qt entry gate checks: ordinary authentication, one request and a deadline."""
from types import SimpleNamespace

import pytest

from quantum_autopilot.console.performance_validation import _WorkspaceEntryGate

pytestmark = pytest.mark.unit


class Button:
    def __init__(self, *, visible=True, enabled=True, callback=None):
        self.visible, self.enabled = visible, enabled
        self.callback = callback
        self.clicks = 0

    def isVisible(self):
        return self.visible

    def isEnabled(self):
        return self.enabled

    def click(self):
        self.clicks += 1
        if self.callback:
            self.callback()


def window(*, os_button=True, visible=True, enabled=True, fixture=False):
    boot = SimpleNamespace(open_button=Button(enabled=False), _fixture_entry=fixture,
                           password="", _login_verified=False)
    if os_button:
        boot.os_session_button = Button(visible=visible, enabled=enabled)
    return SimpleNamespace(current_screen="boot", boot=boot, screens={"topology": object(), "keys": object()})


def poll(gate, target, now, *, snapshot=True):
    return gate.poll(target, now, snapshot_ready=snapshot)


def receive(gate, tick, now, *, visible=True, source="simulated"):
    return gate.observe_tick(tick, now, workspace_visible=visible, source_kind=source)


def test_current_windows_session_control_is_preferred_over_disabled_password_sign_in():
    target = window()
    gate = _WorkspaceEntryGate(10.)
    state, button = poll(gate, target, 11.)
    assert state == "request" and button is target.boot.os_session_button
    button.click()
    for now in (12., 20., 50., 99.):
        assert poll(gate, target, now) == ("waiting_authentication", None)
    assert button.clicks == 1 and target.boot.open_button.clicks == 0
    assert target.boot.password == "" and target.boot._login_verified is False
    assert gate.evidence()["request_count"] == 1
    assert gate.evidence()["method"] == "current_windows_session"


def test_modern_login_never_falls_back_to_password_or_cosmetic_trigger_control():
    target = window(enabled=False)
    target.boot.open_button.enabled = True
    gate = _WorkspaceEntryGate(0.)
    assert poll(gate, target, 1.) == ("waiting_authentication", None)
    target.boot.os_session_button.visible = False
    assert poll(gate, target, 2.) == ("waiting_authentication", None)
    assert gate.requested_at is None


def test_available_session_control_waits_for_snapshot_then_requests_once():
    target = window()
    gate = _WorkspaceEntryGate(0.)
    assert poll(gate, target, 1., snapshot=False) == ("waiting_snapshot", None)
    assert gate.requested_at is None
    assert poll(gate, target, 2.) == ("request", target.boot.os_session_button)
    assert poll(gate, target, 3.) == ("waiting_authentication", None)


@pytest.mark.parametrize("snapshot,screen,expected_state", [
    (False, "boot", "waiting_snapshot"),
    (True, "boot", "waiting_authentication"),
    (True, "splash", "waiting_workspace"),
    (True, "topology", "warming_workspace"),
])
def test_unopened_or_unready_workspace_has_a_terminal_deadline(snapshot, screen, expected_state):
    target = window(enabled=False)
    target.current_screen = screen
    gate = _WorkspaceEntryGate(10.)
    assert poll(gate, target, 11., snapshot=snapshot)[0] == expected_state
    assert poll(gate, target, 100., snapshot=snapshot) == ("failed", None)
    assert "deadline (90s) exceeded" in gate.error
    assert "steady-state workload did not start" in gate.error
    assert poll(gate, target, 101., snapshot=True) == ("failed", None)


def test_successful_ui_authentication_and_source_transition_warm_up_workspace_once():
    target = window()
    gate = _WorkspaceEntryGate(0.)
    # Model a control invoking its ordinary product handler, not a gate changing
    # identity, credentials or permission flags itself.
    target.boot.os_session_button.callback = lambda: setattr(target, "current_screen", "splash")
    state, button = poll(gate, target, 1.)
    assert state == "request"
    button.click()
    assert poll(gate, target, 2.) == ("waiting_workspace", None)
    target.current_screen = "topology"
    assert poll(gate, target, 3.) == ("warming_workspace", None)
    assert receive(gate, 0, 3.)
    assert receive(gate, 1, 4.)
    assert poll(gate, target, 4.) == ("warming_workspace", None)
    assert receive(gate, 2, 5.)
    assert poll(gate, target, 5.) == ("warming_workspace", None)
    assert receive(gate, 3, 6.)
    assert poll(gate, target, 6.) == ("ready", None)
    assert gate.opened_at == 3. and gate.requested_at == 1.
    assert gate.evidence()["authentication_bypassed"] is False
    assert target.boot._login_verified is False


def test_explicit_ui_fixture_cannot_substitute_for_backend_authentication():
    target = window(fixture=True)
    target.boot.open_button.enabled = True
    gate = _WorkspaceEntryGate(0.)
    assert poll(gate, target, 1.) == ("waiting_authentication", None)
    assert gate.requested_at is None


def test_absent_session_control_does_not_fall_back_to_an_enabled_workspace_control():
    target = window(os_button=False)
    target.boot.open_button.enabled = True
    gate = _WorkspaceEntryGate(0.)
    assert poll(gate, target, 1.) == ("waiting_authentication", None)
    assert gate.method is None
    assert poll(gate, target, 2.) == ("waiting_authentication", None)


def test_already_open_workspace_has_bounded_warmup_without_any_login_request():
    target = window()
    target.current_screen = "keys"
    gate = _WorkspaceEntryGate(0.)
    assert poll(gate, target, 1.) == ("warming_workspace", None)
    for tick, now in ((0, 1.), (1, 1.1), (2, 2.1), (3, 3.)):
        assert receive(gate, tick, now)
    assert poll(gate, target, 3.) == ("ready", None)
    assert gate.evidence()["request_count"] == 0
    assert target.boot.os_session_button.clicks == 0


def test_run3_observed_tick_sequence_qualifies_without_private_provider_counter():
    # Rounded excerpt of run3/performance-baseline/snapshots.json, expressed
    # relative to monitor construction. The wrapper has no steps_taken.
    target = window()
    target.current_screen = "topology"
    target.provider = SimpleNamespace(active=SimpleNamespace(delegate=SimpleNamespace(steps_taken=14)))
    assert getattr(target.provider.active, "steps_taken", None) is None
    gate = _WorkspaceEntryGate(2.314)
    assert poll(gate, target, 2.314 + 19.130) == ("warming_workspace", None)
    for elapsed_s, tick in ((21.89, 0), (25.93, 1), (30.65, 2), (30.98, 2), (33.89, 2)):
        receive(gate, tick, elapsed_s)
        assert poll(gate, target, elapsed_s) == ("warming_workspace", None)
    assert receive(gate, 3, 36.40)
    assert poll(gate, target, 36.40) == ("ready", None)
    assert gate.evidence()["tick_advances"] == 3
    assert [row["tick"] for row in gate.evidence()["tick_observations"]] == [0, 1, 2, 3]


def test_duplicate_snapshots_and_large_jump_do_not_fabricate_observed_steps():
    target = window()
    target.current_screen = "topology"
    gate = _WorkspaceEntryGate(0.)
    poll(gate, target, 1.)
    receive(gate, 0, 1.)
    for now in (2., 3., 4.):
        assert not receive(gate, 0, now)
    receive(gate, 100, 5.)
    assert gate.tick_advances == 1
    assert poll(gate, target, 5.) == ("warming_workspace", None)
    receive(gate, 101, 6.)
    receive(gate, 102, 7.)
    assert poll(gate, target, 7.) == ("ready", None)
    assert [row["tick"] for row in gate.tick_observations] == [0, 100, 101, 102]


def test_three_fast_received_ticks_still_require_two_seconds_of_warmup():
    target = window()
    target.current_screen = "topology"
    gate = _WorkspaceEntryGate(0.)
    poll(gate, target, 1.)
    for tick, now in ((0, 1.), (1, 1.1), (2, 1.2), (3, 1.3)):
        receive(gate, tick, now)
    assert poll(gate, target, 1.3) == ("warming_workspace", None)
    assert poll(gate, target, 3.) == ("ready", None)


@pytest.mark.parametrize("tick", [None, True, -1, 1.0, float("nan"), float("inf")])
def test_invalid_tick_cannot_satisfy_readiness(tick):
    target = window()
    target.current_screen = "topology"
    gate = _WorkspaceEntryGate(0.)
    poll(gate, target, 1.)
    assert not receive(gate, tick, 2.)
    assert gate.tick_baseline is None and gate.tick_advances == 0


def test_pre_workspace_hidden_and_reverse_time_receipts_are_not_counted():
    target = window()
    gate = _WorkspaceEntryGate(0.)
    assert not receive(gate, 1, 1.)
    target.current_screen = "topology"
    poll(gate, target, 2.)
    assert not receive(gate, 2, 1.)
    assert not receive(gate, 2, 2.1, visible=False)
    receive(gate, 2, 3.)
    assert not receive(gate, 3, 2.9)
    assert gate.tick_advances == 0


@pytest.mark.parametrize("reset_kind", ["tick_regression", "source_change"])
def test_tick_regression_or_changed_source_restarts_observation_requirement(reset_kind):
    target = window()
    target.current_screen = "topology"
    gate = _WorkspaceEntryGate(0.)
    poll(gate, target, 1.)
    for tick, now in ((10, 1.), (11, 2.), (12, 3.)):
        receive(gate, tick, now)
    assert gate.tick_advances == 2
    source = "measurement_file" if reset_kind == "source_change" else "simulated"
    baseline = 12 if reset_kind == "source_change" else 0
    receive(gate, baseline, 4., source=source)
    assert gate.tick_advances == 0 and gate.tick_resets == 1
    assert poll(gate, target, 4.) == ("warming_workspace", None)
    for delta, now in ((1, 5.), (2, 6.), (3, 7.)):
        receive(gate, baseline + delta, now, source=source)
    assert poll(gate, target, 7.) == ("ready", None)
