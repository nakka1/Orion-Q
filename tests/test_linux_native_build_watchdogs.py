"""Reject unsupported build watchdogs before creating any artifact/state."""
from pathlib import Path
import importlib.util
import pytest
ROOT=Path(__file__).resolve().parents[1]
@pytest.mark.parametrize('flag,value',[('--gui-self-test-timeout-s','59'),('--gui-self-test-timeout-s','3601'),('--dependency-probe-timeout-s','29'),('--dependency-probe-timeout-s','301'),('--gui-self-test-timeout-s','true'),('--dependency-probe-timeout-s','1.5')])
def test_invalid_build_watchdog_cannot_create_release(tmp_path,capsys,flag,value):
 spec=importlib.util.spec_from_file_location('bounded_native_builder',ROOT/'tools/build_linux_native.py');tool=importlib.util.module_from_spec(spec);spec.loader.exec_module(tool)
 destination=tmp_path/'release'
 with pytest.raises(SystemExit) as caught:
  tool.main(['--repository',str(tmp_path),'--output',str(destination),'--wheel',str(tmp_path/'missing.whl'),flag,value])
 assert caught.value.code==2
 assert not destination.exists()
 message=capsys.readouterr().err
 assert 'watchdogs outside allowed range' in message or 'invalid int value' in message
