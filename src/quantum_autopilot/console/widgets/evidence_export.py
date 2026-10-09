"""Choose a destination; the source remains responsible for authorized writing."""
import json
from pathlib import Path
from quantum_autopilot.product import PRODUCT_NAME

from PySide6.QtWidgets import QFileDialog


def _source_context(snapshot):
    return (snapshot.campaign, snapshot.input_kind, snapshot.capabilities.adapter_id)


def choose_evidence_export(screen, scope):
    snapshot = screen._snap
    if snapshot is None or not snapshot.capabilities.allows('export_record') or getattr(screen, 'commands_busy', False):
        return
    context = _source_context(snapshot)
    path, _ = QFileDialog.getSaveFileName(
        screen, 'Export evidence to a new JSON file', f'{PRODUCT_NAME}-{scope}-evidence.json',
        'JSON (*.json)', options=QFileDialog.Option.DontConfirmOverwrite)
    current = screen._snap
    if not path:
        return
    if (current is None or context != _source_context(current)
            or not current.capabilities.allows('export_record') or getattr(screen, 'commands_busy', False)
            or not screen.isVisible()):
        screen.notify.emit('Export cancelled: the source or its permissions changed.')
        return
    destination = Path(path)
    if not destination.suffix:
        destination = destination.with_suffix('.json')
    if destination.suffix.lower() != '.json':
        screen.notify.emit('Choose a JSON filename for the evidence export.')
        return
    if destination.exists():
        screen.notify.emit('Choose a new filename. Existing evidence files are preserved.')
        return
    screen.command.emit('export_record', json.dumps(
        {'scope': scope, 'destination': str(destination)}, ensure_ascii=False))
