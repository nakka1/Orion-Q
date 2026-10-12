"""The source benchmark keeps private service state out of public evidence."""
import importlib.util
import math
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

SPEC = importlib.util.spec_from_file_location('policy_panel_benchmark_tool',
    Path(__file__).resolve().parents[1]/'tools/benchmark_policy_panels.py')
tool = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tool)


@pytest.fixture
def source(tmp_path):
    root = tmp_path/'source'
    root.mkdir()
    (root/'pyproject.toml').write_text('[project]\nversion="5.66.1"\n', encoding='utf-8')
    return root


@pytest.mark.parametrize('seconds', (math.nan, math.inf, -math.inf, 0, .099, 60.01))
def test_nonfinite_or_unbounded_duration_is_rejected_before_writes(source, tmp_path, seconds):
    output, state = tmp_path/'public', tmp_path/'private'
    with pytest.raises(ValueError, match='seconds'):
        tool.validate_paths(source, output, state, seconds)
    assert not output.exists() and not state.exists()


@pytest.mark.parametrize('nesting', ('private-inside-output', 'output-inside-private', 'same'))
def test_private_state_cannot_be_placed_in_public_evidence(source, tmp_path, nesting):
    output = tmp_path/'public'
    state = output/'state' if nesting == 'private-inside-output' else tmp_path/'private'
    if nesting == 'output-inside-private':
        output = state/'output'
    elif nesting == 'same':
        state = output
    with pytest.raises(ValueError, match='separate'):
        tool.validate_paths(source, output, state, 1)
    assert not output.exists() and not state.exists()


@pytest.mark.parametrize('existing', ('output', 'state'))
def test_existing_destinations_are_never_overwritten(source, tmp_path, existing):
    output, state = tmp_path/'public', tmp_path/'private'
    (output if existing == 'output' else state).mkdir()
    with pytest.raises(ValueError, match='fresh'):
        tool.validate_paths(source, output, state, 1)


@pytest.mark.parametrize('tree', ('src', 'tests', 'tools', 'configs', 'runtime_assets', '.git'))
@pytest.mark.parametrize('destination', ('output', 'state'))
def test_destinations_do_not_mutate_source_or_git_trees(source, tmp_path, tree, destination):
    output, state = tmp_path/'public', tmp_path/'private'
    if destination == 'output':
        output = source/tree/'new'
    else:
        state = source/tree/'new'
    with pytest.raises(ValueError, match='source or Git'):
        tool.validate_paths(source, output, state, 1)
    assert not output.exists() and not state.exists()


def test_valid_fresh_paths_resolve_without_creating_state(source, tmp_path):
    output, state = tmp_path/'public', tmp_path/'private'
    assert tool.validate_paths(source, output, state, 6) == (source.resolve(), output.resolve(), state.resolve())
    assert not output.exists() and not state.exists()


def test_an_editable_or_loaded_module_from_another_checkout_is_rejected(source, tmp_path, monkeypatch):
    foreign = tmp_path/'foreign.py'
    foreign.write_text('fixture', encoding='utf-8')
    monkeypatch.setitem(sys.modules, 'benchmark_foreign_fixture', SimpleNamespace(__file__=str(foreign)))
    with pytest.raises(ValueError, match='outside the selected'):
        tool.verify_module_origins(source, ('benchmark_foreign_fixture',))


def test_verified_module_origin_uses_selected_source_bytes(source, monkeypatch):
    module = source/'src/fixture.py'
    module.parent.mkdir()
    module.write_text('fixture', encoding='utf-8')
    monkeypatch.setitem(sys.modules, 'benchmark_local_fixture', SimpleNamespace(__file__=str(module)))
    rows = tool.verify_module_origins(source, ('benchmark_local_fixture',))
    assert rows['benchmark_local_fixture']['path'] == 'src/fixture.py'
    assert rows['benchmark_local_fixture']['sha256'] == tool.hashlib.sha256(b'fixture').hexdigest()


def test_summary_percentile_is_reproducible_and_empty_measurement_has_no_fake_zeros():
    assert tool.stats([]) == {'count': 0, 'median_ms': None, 'p95_ms': None, 'max_ms': None}
    assert tool.stats(list(range(100))) == {'count': 100, 'median_ms': 50, 'p95_ms': 94, 'max_ms': 99}
