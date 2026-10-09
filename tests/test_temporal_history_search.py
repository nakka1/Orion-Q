"""Literal campaign-prefix search with stable pagination and source isolation."""
import os
from dataclasses import replace
from pathlib import Path

import pytest

from quantum_autopilot.control_plane.temporal_campaign import list_temporal_campaigns, run_temporal_campaign
from quantum_autopilot.control_plane.topology import SimulatedTopologyStore
from tests import test_temporal_history as shared
from tests.test_temporal_campaign import request

app, setup, settle, save = shared.app, shared.setup, shared.settle, shared.save


@pytest.mark.parametrize('prefix', [None, True, 5, [], 'x' * 129, 'x\n', '\x00', '\x7f'])
def test_invalid_prefix_rejected(tmp_path, prefix):
    with pytest.raises(ValueError, match='prefix'):
        list_temporal_campaigns(SimulatedTopologyStore(tmp_path / 'state'), campaign_prefix=prefix)


def test_literal_case_sensitive_search_and_pagination(tmp_path):
    store = SimulatedTopologyStore(tmp_path / 'state')
    save(store, 23)
    run_temporal_campaign(store, replace(request(max_attempts=1), campaign_id='unrelated-newest'))
    first = list_temporal_campaigns(store, campaign_prefix='history-0')
    assert len(first.entries) == 20 and first.entries[0].campaign_id == 'history-022'
    second = list_temporal_campaigns(store, campaign_prefix='history-0', before_sequence=first.next_before)
    assert [entry.campaign_id for entry in second.entries] == ['history-002', 'history-001', 'history-000']
    assert list_temporal_campaigns(store, campaign_prefix='history-022').entries == (first.entries[0],)
    for literal in ('History-', '%', '_', "' OR 1=1 --", ' history-'):
        assert not list_temporal_campaigns(store, campaign_prefix=literal).entries
    assert list_temporal_campaigns(store).entries[0].campaign_id == 'unrelated-newest'


def test_search_applies_explicitly_and_resets_pages(setup, app):
    service, _, job, dialog, _ = setup
    save(service.topology_store, 23)
    job.browse_history()
    settle(app, lambda: not job.active)
    job.browse_history('older')
    settle(app, lambda: not job.active)
    assert job.history_back
    dialog.history_filter.setText('history-02')
    assert not job.active and job.history_prefix == ''
    assert not dialog.history_newer.isEnabled()
    dialog.history_refresh.clicked.emit()
    settle(app, lambda: not job.active)
    assert job.history_prefix == 'history-02' and not job.history_back
    assert len(job.history_entries) == 3 and job.history_next is None
    assert 'history-02' in job.history_note
    dialog.history_table.selectRow(0)
    assert dialog.history_open.isEnabled()
    dialog.history_filter.setText('not-yet-applied')
    assert not dialog.history_open.isEnabled()
    dialog.history_filter.setText('history-02')
    assert dialog.history_open.isEnabled()
    dialog.history_open.clicked.emit()
    settle(app, lambda: not job.active)
    assert job.receipt['record']['request']['campaign_id'] == 'history-022'
    assert job.request is None and job.process is None
    dialog.history_filter.setText('')
    dialog.history_filter.returnPressed.emit()
    settle(app, lambda: not job.active)
    assert len(job.history_entries) == 20 and job.history_prefix == ''


def test_backend_navigation_keeps_applied_filter_and_empty_result_has_no_old_cursor(setup, app):
    service, _, job, _, _ = setup
    save(service.topology_store, 23)
    job.browse_history(campaign_prefix='history-0')
    settle(app, lambda: not job.active)
    with pytest.raises(ValueError, match='refresh'):
        job.browse_history('older', campaign_prefix='different')
    job.browse_history('older')
    settle(app, lambda: not job.active)
    assert len(job.history_entries) == 3 and job.history_prefix == 'history-0'
    job.browse_history('newer')
    settle(app, lambda: not job.active)
    assert len(job.history_entries) == 20
    job.browse_history(campaign_prefix='nothing')
    settle(app, lambda: not job.active)
    assert not job.history_entries and not job.history_back and job.history_next is None
    assert 'nothing' in job.history_note


def test_source_switch_clears_applied_and_draft_filter(setup, app, tmp_path):
    from quantum_autopilot.console.input_sources import MeasuredFileProvider
    service, provider, job, dialog, _ = setup
    save(service.topology_store)
    job.browse_history(campaign_prefix='history-')
    settle(app, lambda: not job.active)
    dialog.history_filter.setText('unsent-draft')
    provider.switch(lambda: MeasuredFileProvider(tmp_path / 'absent.json', tmp_path / 'measured'))
    assert job.history_prefix == '' and dialog.history_filter.text() == ''
    assert not dialog.history_filter.isEnabled() and not job.history_entries


def test_prefix_is_displayed_as_plain_text_not_markup(setup, app):
    from PySide6.QtCore import Qt
    _, _, job, dialog, _ = setup
    prefix = '<b>not markup</b>'
    job.browse_history(campaign_prefix=prefix)
    settle(app, lambda: not job.active)
    assert prefix in dialog.history_note.text()
    assert dialog.history_note.textFormat() == Qt.TextFormat.PlainText


def test_search_failure_does_not_leave_navigation_to_previous_query(setup, app, monkeypatch):
    import quantum_autopilot.console.campaign_job as adapter
    service, _, job, dialog, _ = setup
    save(service.topology_store, 23)
    job.browse_history()
    settle(app, lambda: not job.active)
    assert job.history_next is not None
    def fail(*a, **k):
        raise OSError('injected read failure')
    monkeypatch.setattr(adapter, 'list_temporal_campaigns', fail)
    job.browse_history(campaign_prefix='new-query')
    settle(app, lambda: not job.active)
    assert 'UNAVAILABLE' in job.history_note
    assert not job.history_entries and job.history_next is None and not job.history_back
    assert not dialog.history_older.isEnabled()


def test_filtered_history_layout(setup, app):
    from quantum_autopilot.console.theme import install
    service, _, job, dialog, _ = setup
    install(app)
    save(service.topology_store, 23)
    job.browse_history(campaign_prefix='history-0')
    settle(app, lambda: not job.active)
    dialog.tabs.setCurrentIndex(2)
    dialog.show()
    for width, height in ((640, 500), (1080, 740)):
        dialog.resize(width, height)
        app.processEvents()
        assert dialog.history_table.viewport().height() > 60
        assert dialog.rect().contains(dialog.history_filter.mapTo(dialog, dialog.history_filter.rect().topLeft()))
        assert dialog.rect().contains(dialog.history_open.mapTo(dialog, dialog.history_open.rect().bottomRight()))
        if folder := os.environ.get('ORION_SEARCH_EVIDENCE'):
            Path(folder).mkdir(parents=True, exist_ok=True)
            assert dialog.grab().save(str(Path(folder) / f'search-{width}.png'))
