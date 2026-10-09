"""The portable checker must cover the real catalog and reject unsafe reports."""
import json

import pytest

from quantum_autopilot.model_intake.catalog import model_options
from tools import validate_frozen_optional_models as checker


@pytest.mark.unit
@pytest.mark.parametrize("defect", [None,"missing","duplicate","physical","active"])
def test_frozen_checker_uses_complete_catalog_and_safety_flags(tmp_path, monkeypatch, defect):
    exe=tmp_path/'fake-test.exe'
    exe.write_bytes(b'unit-test fixture; not a program')
    ids=[o.model_id for o in model_options() if o.status=='offline_only']
    def invoke(_exe,args,_output,_mode,_timeout):
        destination=args[args.index('--output-dir')+1]
        destination.mkdir()
        models=[{'model_id':model_id,'unloaded':True} for model_id in ids]
        if defect=='missing':
            models.pop()
        if defect=='duplicate':
            models[-1]=models[0]
        report={'passed':True,'frozen':True,'mode':'without_packages','executable_sha256':checker.digest(exe),
                'models':models,'physical_action_performed':defect=='physical','runtime_policy_activated':defect=='active'}
        (destination/'optional-models-report.json').write_text(json.dumps(report),encoding='utf-8')
    monkeypatch.setattr(checker,'invoke',invoke)
    result=checker.validate(exe,tmp_path/'result')
    assert result['passed'] is (defect is None)
    assert result['independent_machine_verified'] is False
