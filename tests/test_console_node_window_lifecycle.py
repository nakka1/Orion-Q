"""Window identities, cancellation and received content survive delayed signals."""
import os

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
pytest.importorskip('PySide6.QtWidgets')

from PySide6.QtCore import QCoreApplication, QEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from quantum_autopilot.console.providers import MockProvider  # noqa: E402
from quantum_autopilot.console.widgets.inspector import CommandStatus  # noqa: E402
from quantum_autopilot.console.widgets.network_views import build_net_model  # noqa: E402
from quantum_autopilot.console.widgets.node_window import NodeWindows, _FibreCard  # noqa: E402


@pytest.fixture
def registry():
    app = QApplication.instance() or QApplication([])
    host = QWidget()
    host.resize(1000, 700)
    host.show()
    windows = NodeWindows(host, incremental=True)
    try:
        yield app, windows
    finally:
        windows.shutdown()
        host.close()
        host.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_delayed_close_from_retired_window_cannot_remove_reopened_same_node(registry):
    _app, windows = registry
    records = []
    windows.set_diagnostic_observer(records.append)
    old = windows.open('CBPF')
    windows.close('CBPF')
    current = windows.open('CBPF')
    # Deferred Qt deletion has not run yet. A queued signal can still name CBPF.
    old.closed.emit('CBPF')
    assert windows.windows.get('CBPF') is current
    assert current.isVisible() and not current._closed_presentation
    ignored = [row for row in records if row['event'] == 'stale_close_ignored']
    assert ignored[-1]['instance_id'] == id(current)
    assert ignored[-1]['signaling_instance_id'] == id(old)


def test_current_close_button_cancels_all_pending_work_and_preserves_reason(registry):
    _app, windows = registry
    records = []
    windows.set_diagnostic_observer(records.append)
    snap = MockProvider().snapshot()
    net = build_net_model(snap)
    current = windows.open('N-01', prepare=lambda item: item.refresh(snap, net, CommandStatus()))
    assert current._pending_widgets and current._presentation_timer.isActive()
    current.close_button.click()
    assert 'N-01' not in windows.windows
    assert current._closed_presentation and not current._pending_widgets
    assert not current._presentation_timer.isActive()
    current._materialize_step()
    assert not current._pending_widgets
    close = [row for row in records if row['event'] == 'registry_close'][-1]
    assert close['reason'] == 'close_button' and close['signaling_instance_matches'] is True


def test_current_sender_cannot_close_another_node_identity(registry):
    _app, windows = registry
    first, second = windows.open('CBPF'), windows.open('CIMATEC')
    first.closed.emit('CIMATEC')
    assert windows.windows.get('CIMATEC') is second
    assert windows.windows.get('CBPF') is first


def test_card_is_constructed_in_final_owner_without_changing_field_widgets(registry):
    _app, windows = registry
    current = windows.open('N-01')
    assert current._presentation_timer.interval() == 8
    card = _FibreCard(current, 'link')
    assert card.parentWidget() is current.cards_host
    assert card.identity.__class__.__name__ == 'KVList'
    assert card.left.__class__.__name__ == card.right.__class__.__name__ == 'KVList'


@pytest.mark.parametrize('interval', (8, 20))
def test_materialized_details_keep_received_snapshot_and_complete_fields(registry, interval):
    _app, windows = registry
    windows.set_presentation_interval_ms(interval)
    snap = MockProvider().snapshot()
    net = build_net_model(snap)
    current = windows.open('N-01', prepare=lambda item: item.refresh(snap, net, CommandStatus()))
    assert current._presentation_timer.interval() == interval
    for _ in range(200):
        if current.presentation_status()['ready']:
            break
        current._materialize_step()
    assert current.presentation_status()['ready']
    assert current.presentation_snapshot() == (snap, net)
    assert current.field_texts()['id'] == 'N-01'
    assert set(current.connection_texts()) == set(current.presentation_status()['expected_link_keys'])
    assert all(card.identity.texts()['Identificador do enlace'] == key for key, card in current._cards.items())
