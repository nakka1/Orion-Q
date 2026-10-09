from quantum_autopilot.quantum_protocols.validation import validate_modular_runtime


def test_source_and_packaged_validation_exercises_five_protocols(tmp_path):
    report = validate_modular_runtime(tmp_path)
    assert report['passed'], report['checks']
    assert report['check_count'] == 12
    assert (tmp_path / 'receipts.jsonl').is_file()
