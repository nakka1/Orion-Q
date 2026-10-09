"""Frozen/source Qt acceptance of the actual satellite inspection widget."""
from pathlib import Path
import json


def validate_satellite_ui(destination,metrics):
    from PySide6.QtWidgets import QApplication
    from .widgets.satellite_telemetry import SatelliteTelemetryDialog, vault_reference
    app=QApplication.instance();out=Path(destination);out.mkdir(parents=True,exist_ok=False)
    dialog=SatelliteTelemetryDialog();checks={}
    try:
        dialog.set_metrics(metrics,session_id='RIO-SATELLITE',clock_s=3.)
        dialog.show();app.processEvents()
        checks['housekeeping_has_backend_rows']=dialog.housekeeping.rowCount()>8
        checks['science_has_detector_and_key_rows']=dialog.science.rowCount()>20
        texts=' '.join(dialog.housekeeping.item(r,2).text() for r in range(dialog.housekeeping.rowCount()))
        checks['orbital_units_visible']=all(unit in texts for unit in ('km','km/s','µrad','GHz'))
        checks['credentials_are_references']=all(vault_reference(edit.text())
            for edit in (dialog.credential,dialog.mission_credential))
        dialog.resize(1100,750);app.processEvents();dialog.grab().save(str(out/'housekeeping.png'))
        dialog.tabs.setCurrentIndex(1);app.processEvents();dialog.grab().save(str(out/'science.png'))
        dialog.capture();captured=dialog.evidence.toPlainText()
        dialog.set_metrics({**metrics,'coincidences':metrics.get('coincidences',0)+1},clock_s=3.)
        checks['capture_stays_stable']=dialog.evidence.toPlainText()==captured
        dialog.resize(640,480);app.processEvents()
        bar=dialog.science.verticalScrollBar();bar.setValue(bar.maximum());app.processEvents()
        checks['small_window_scrolls_to_last_row']=bar.value()==bar.maximum()
        checks['inspection_cannot_actuate_hardware']=not dialog.start.isEnabled()
        report={'schema':'orion.satellite-ui-validation.v2','passed':all(checks.values()),
            'checks':checks,'check_count':len(checks),'qt_platform':app.platformName(),
            'scope':'native widget/backend fixture; no independent-machine or hardware qualification'}
        (out/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        return report
    finally:dialog.close();dialog.deleteLater();app.processEvents()
