"""Complete native content must precede the existing operational assertions."""
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope='module')
def app():
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication
    from quantum_autopilot.console import theme
    application = QApplication.instance() or QApplication([])
    theme.install(application)
    return application


@pytest.mark.parametrize('flow', ['demo', 'usage'])
def test_operational_validator_reads_complete_native_node_content(app, tmp_path, flow):
    if flow == 'demo':
        from quantum_autopilot.console.validation import validate_demo_pipeline
        report = validate_demo_pipeline(SimpleNamespace(repository_root=ROOT), tmp_path)
        receipt = report['node_presentation']
        assert report['checks']['node_window_details_readable_and_scrollable']
        assert report['checks']['node_window_wave_is_backend_samples']
    else:
        from quantum_autopilot.console.usage_validation import validate_usage_feedback
        report = validate_usage_feedback(SimpleNamespace(repository_root=ROOT), tmp_path)
        receipt = report['unlocated_node_presentation']
        assert report['checks']['unlocated_node_details_keep_connection']
        assert report['checks']['unlocated_nodes_have_no_geographic_wires']
    assert report['passed'], report['checks']
    assert receipt['ready'] and receipt['complete']
    assert all(receipt['checks'].values())
    assert receipt['checks']['requested_context_presented']
    assert receipt['source_kind'] == 'simulated'
    assert receipt['wait_elapsed_ms'] < receipt['content_deadline_ms'] == 5000
    assert receipt['presentation']['expected_sections'] == receipt['presentation']['completed_sections']
