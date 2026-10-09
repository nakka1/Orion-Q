"""Policy readers must not derive export authority from reading captured data."""
from dataclasses import replace

import pytest

from tests import test_console_policy_activation as activation
from tests import test_console_policy_approval as approval
from tests import test_console_policy_context as context
from tests import test_console_policy_decisions as decisions
from tests import test_console_policy_review as review
from quantum_autopilot.console.widgets import policy_review as ui


app, provider = review.app, review.provider
context_panel, decisions_panel, review_panel = context.panel, decisions.panel, review.panel


def capture(case, panel, app):
    view = panel[0]
    if case == 'context':
        context.observe(panel, app)
        return 'observation', 'export', view.export_button
    if case == 'decision':
        decisions.explain(view, app)
        return 'explanation', 'export', view.export_button
    if case == 'review':
        review.review(panel, app)
        return 'review', 'export_review', view.export_button
    if case == 'approval':
        approval.approve(panel, app)
        return 'approval', 'export_approval', view.export_approval_button
    activation.activate(panel, app)
    activation.read_active(view, app)
    return 'active_observation', 'export_state', view.export_state_button


@pytest.mark.parametrize('case,fixture', [
    ('context', 'context_panel'), ('decision', 'decisions_panel'),
    ('review', 'review_panel'), ('approval', 'review_panel'), ('state', 'review_panel'),
])
@pytest.mark.parametrize('when', ['before_chooser', 'inside_chooser'])
def test_policy_export_requires_separate_capability_and_rechecks_after_chooser(
    request, app, monkeypatch, tmp_path, case, fixture, when,
):
    panel = request.getfixturevalue(fixture)
    view, wrapper = panel[:2]
    field, method, button = capture(case, panel, app)
    captured = getattr(view, field)
    assert captured is not None and button.isEnabled()
    original = wrapper.capabilities
    denied = replace(original, operations=tuple(op for op in original.operations if op != 'export_record'))
    target = tmp_path / 'permission-refused.json'
    choices = []

    def revoke_export():
        monkeypatch.setattr(type(wrapper), 'capabilities', property(lambda _: denied))

    def choose(*_):
        choices.append(True)
        revoke_export()
        return str(target), 'JSON'

    monkeypatch.setattr(ui.QFileDialog, 'getSaveFileName', choose)
    if when == 'before_chooser':
        revoke_export()
    getattr(view, method)()
    assert bool(choices) is (when == 'inside_chooser')
    assert not button.isEnabled() and not view.export_job.active
    assert not target.exists()
    # Reading remains authorized; denying export alone does not discard the
    # captured observation, change the policy or silently prepare a new one.
    assert getattr(view, field) is captured

