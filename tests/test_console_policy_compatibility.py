"""Native Q05 declarations remain blocked even after a successful package check."""

import os
from pathlib import Path

import pytest
from quantum_autopilot.model_intake import catalog

from tests import test_console_model_catalog as catalog_tests

app = catalog_tests.app
dialog = catalog_tests.dialog
checked = catalog_tests.checked


@pytest.mark.parametrize("row", range(11))
def test_all_native_options_show_target_and_block_reasons(dialog, row, monkeypatch):
    def forbidden(*a, **kw):
        pytest.fail("inspection must not open a model")

    monkeypatch.setattr(catalog, "open_optional_model", forbidden)
    dialog.table.selectRow(row)
    text = dialog.detail.toPlainText()
    assert "Q05 COMPATIBILITY: BLOCKED" in text
    assert "Target: repeater.utility.v1 / simulation" in text
    assert "Declared contracts only; runtime and artifacts NOT OBSERVED" in text
    for criterion in ("input_domain: BLOCKED", "action_mapping: BLOCKED", "lifecycle: BLOCKED"):
        assert criterion in text
    assert "Selection / activation: UNAVAILABLE" in text


def test_verified_package_does_not_promote_contract_or_retain_wrong_variant(app, dialog):
    dialog.table.selectRow(1)
    dialog.checker.result = checked()
    dialog.checker.changed.emit()
    text = dialog.detail.toPlainText()
    assert "PACKAGE IDENTITY VERIFIED" in text
    assert "Q05 COMPATIBILITY: BLOCKED" in text
    assert "identity: NOT_OBSERVED" in text
    dialog.table.selectRow(3)
    assert dialog.checker.result is None
    text = dialog.detail.toPlainText()
    assert "candidate_action_scoring" in text and "shared action names" in text
    assert "PACKAGE IDENTITY VERIFIED" not in text
    assert "Q05 COMPATIBILITY: BLOCKED" in text
    dialog.show()
    app.processEvents()
    evidence = os.environ.get("ORION_POLICY_COMPATIBILITY_EVIDENCE")
    if evidence:
        out = Path(evidence)
        out.mkdir(parents=True, exist_ok=True)
        for w, h in ((640, 480), (1060, 760)):
            dialog.resize(w, h)
            app.processEvents()
            assert dialog.grab().save(str(out / f"catalog-{w}.png"))
        (out / "routing-details.txt").write_text(text, encoding="utf-8")
