"""Read-only product imports from a preserved PyInstaller PYZ for benchmarking.

Install before importing quantum_autopilot/quantum_twin, in a fresh process.
Only those namespaces come from the archive; dependencies use the benchmark
venv. Missing own modules fail closed, preventing an accidental before/after
source mixture. Bytecode must match this interpreter's Python magic number.

Windows-spawn benchmark launchers should install this at top level from
ORION_BENCH_ARCHIVE_PYZ and ORION_BENCH_ARCHIVE_ASSETS in every spawned child.
This is diagnostic tooling, not the frozen production importer or a sandbox.
Only use a locally trusted, preserved build archive: its modules execute code.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.abc
import importlib.resources
import importlib.util
from importlib.resources.abc import TraversableResources
import json
from pathlib import Path
import sys
from types import CodeType

PREFIXES = ('quantum_autopilot', 'quantum_twin')
_installed = None


def _allowed(fullname):
    return any(fullname == prefix or fullname.startswith(prefix + '.') for prefix in PREFIXES)


def _inside(path, root):
    resolved = Path(path).resolve()
    if not resolved.is_relative_to(root):
        raise OSError(f'Archive resource is outside the preserved assets root: {path}')
    return resolved


class ArchiveResourceReader(TraversableResources):
    """On-disk resources collected beside the preserved executable."""

    def __init__(self, directory, assets):
        self.directory = _inside(directory, assets)

    def files(self):
        # A real pathlib.Path preserves compatibility with callers reading
        # package resources directly via __file__, as the frozen app does.
        return self.directory


class ArchiveModuleLoader(importlib.abc.InspectLoader):
    def __init__(self, finder, fullname, typecode):
        self.finder, self.fullname, self.typecode = finder, fullname, typecode
        module_path = finder.assets.joinpath(*fullname.split('.'))
        self.path = str(module_path / '__init__.pyc' if self.is_package(fullname) else module_path.with_suffix('.pyc'))

    def _check(self, fullname):
        if fullname != self.fullname:
            raise ImportError(f'This archive loader owns only {self.fullname}')

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        code = self.get_code(module.__name__)
        if code is not None:
            exec(code, module.__dict__)
        self.finder.loaded[module.__name__] = self.path

    def get_code(self, fullname):
        self._check(fullname)
        if self.typecode == self.finder.namespace_type:
            return None
        code = self.finder.archive.extract(fullname)
        if not isinstance(code, CodeType):
            raise ImportError(f'{fullname} has no executable module code in the preserved archive')
        return code

    def get_source(self, fullname):
        self._check(fullname)
        # Never read changed source beside a bytecode-only baseline.
        return None

    def get_filename(self, fullname):
        self._check(fullname)
        return self.path

    def is_package(self, fullname):
        self._check(fullname)
        return self.typecode in (self.finder.package_type, self.finder.namespace_type)

    def get_data(self, path):
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = self.finder.assets / candidate
        return _inside(candidate, self.finder.assets).read_bytes()

    def get_resource_reader(self, fullname):
        self._check(fullname)
        if not self.is_package(fullname):
            return None
        return ArchiveResourceReader(Path(self.path).parent, self.finder.assets)


class ArchiveModuleFinder(importlib.abc.MetaPathFinder):
    def __init__(self, pyz, assets):
        from PyInstaller.loader.pyimod01_archive import (
            PYZ_ITEM_MODULE, PYZ_ITEM_PKG, PYZ_ITEM_NSPKG, ZlibArchiveReader,
        )
        self.pyz, self.assets = Path(pyz).resolve(), Path(assets).resolve()
        if not self.pyz.is_file() or not self.assets.is_dir():
            raise FileNotFoundError('Both the preserved PYZ file and assets directory must exist')
        self.archive = ZlibArchiveReader(str(self.pyz), check_pymagic=True)
        self.module_type, self.package_type, self.namespace_type = PYZ_ITEM_MODULE, PYZ_ITEM_PKG, PYZ_ITEM_NSPKG
        self.loaded = {}
        with self.pyz.open('rb') as stream:
            self.archive_sha256 = hashlib.file_digest(stream, 'sha256').hexdigest()
        self.available = {name for name in self.archive.toc if _allowed(name)}
        if not all(prefix in self.available for prefix in PREFIXES):
            raise ImportError('The preserved archive must contain both product namespaces')

    def find_spec(self, fullname, path=None, target=None):
        if not _allowed(fullname):
            return None
        if fullname not in self.available:
            raise ModuleNotFoundError(f'Baseline archive does not contain {fullname}; source fallback is forbidden', name=fullname)
        typecode = self.archive.toc[fullname][0]
        if typecode not in (self.module_type, self.package_type, self.namespace_type):
            raise ImportError(f'Unsupported archive entry type for {fullname}')
        loader = ArchiveModuleLoader(self, fullname, typecode)
        spec = importlib.util.spec_from_file_location(fullname, loader.path, loader=loader,
                                                    submodule_search_locations=[str(Path(loader.path).parent)] if loader.is_package(fullname) else None)
        spec.loader_state = {'archive': str(self.pyz), 'archive_sha256': self.archive_sha256}
        return spec

    def provenance(self):
        return {'schema': 'orion.benchmark-archive-imports.v1', 'archive': str(self.pyz),
                'archive_sha256': self.archive_sha256, 'assets_root': str(self.assets),
                'prefixes': list(PREFIXES), 'python_magic_checked': True,
                'available_product_modules': len(self.available), 'loaded_product_modules': dict(sorted(self.loaded.items())),
                'dependency_origin': 'benchmark virtual environment', 'source_fallback_allowed': False,
                'frozen_execution_equivalent': False}


def install_archive_importer(pyz, assets):
    """Install once in a fresh process; do not replace already imported code."""
    global _installed
    resolved = (Path(pyz).resolve(), Path(assets).resolve())
    if _installed is not None:
        if resolved != (_installed.pyz, _installed.assets):
            raise RuntimeError('A different baseline archive is already installed in this process')
        return _installed
    existing = sorted(name for name in sys.modules if _allowed(name))
    if existing:
        raise RuntimeError(f'Install before any product import; existing modules: {existing[:8]}')
    finder = ArchiveModuleFinder(*resolved)
    sys.meta_path.insert(0, finder)
    _installed = finder
    return finder


def provenance():
    if _installed is None:
        raise RuntimeError('No baseline archive importer is installed')
    return _installed.provenance()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pyz', required=True)
    parser.add_argument('--assets', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args(argv)
    finder = install_archive_importer(args.pyz, args.assets)
    import importlib
    for name in ('quantum_autopilot.console.contract', 'quantum_autopilot.console.widgets.geo_map'):
        module = importlib.import_module(name)
        assert isinstance(module.__loader__, ArchiveModuleLoader)
        assert Path(module.__file__).is_relative_to(finder.assets)
    world = importlib.resources.files('quantum_autopilot.console').joinpath('geo/world_v1.json')
    data = json.loads(world.read_text(encoding='utf-8'))
    try:
        importlib.import_module('quantum_autopilot.__baseline_missing_module_check__')
    except ModuleNotFoundError as error:
        assert 'source fallback is forbidden' in str(error)
    else:
        raise AssertionError('Missing product modules must never use current sources')
    result = provenance()
    result.update(world_resource=str(world), world_sha256=hashlib.sha256(world.read_bytes()).hexdigest(),
                  world_schema=data.get('schema'), missing_module_rejected=True,
                  qt_application_created=False)
    Path(args.output).write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(f'Archive import/resource check passed: {args.output}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
