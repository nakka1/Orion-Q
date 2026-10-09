"""Frozen entry point for the Windows build.

PyInstaller needs a module-level script; keeping it here means the spec never
imports application code at build time. Defaults to the native console; the
web dashboard is still reachable with `OrionQ.exe --ui web`.
"""

from __future__ import annotations

import multiprocessing
import sys
import tempfile
import traceback
from pathlib import Path
from uuid import uuid4


def launch(argv):
    """Persist unexpected errors: windowless diagnostics must not hang in a dialog."""
    try:
        if argv[:1] == ['--compare-controllers']:
            from quantum_autopilot.control_plane.controller_evidence import main as comparison_main
            return comparison_main(argv[1:])
        if argv[:1] == ['--diagnose-runtime']:
            from quantum_autopilot.control_plane.runtime_diagnostic import main as diagnostic_main
            return diagnostic_main(argv[1:])
        if argv[:1] == ['--optional-model-test']:
            from quantum_autopilot.optional_models_acceptance import main as optional_main
            return optional_main(argv[1:])
        if argv[:1] == ['--acceptance-test']:
            from quantum_autopilot.windows_acceptance import main as acceptance_main
            return acceptance_main(argv[1:], package_root=Path(sys.executable).parent)
        # Explicit early dispatch: never construct a desktop window in the child.
        from quantum_autopilot.worker_runtime import WORKER_FLAG, restore_worker_stdio
        sys._orion_temporal_worker_v1 = True
        if argv[:1] == [WORKER_FLAG]:
            if not restore_worker_stdio():
                return 2
            from quantum_autopilot.control_plane.temporal_campaign_worker import main as worker_main
            return worker_main(argv[1:])
        from quantum_autopilot.desktop import main
        return main(argv)
    except Exception:
        details = traceback.format_exc()
        directory = Path(tempfile.gettempdir()) / "ORION-Q-diagnostics"
        for flag in ("--state-root", "--stress-test", "--performance-test"):
            if flag in argv and argv.index(flag) + 1 < len(argv):
                directory = Path(argv[argv.index(flag) + 1])
                break
        if '--startup-test' in argv and argv.index('--startup-test') + 1 < len(argv):
            directory = Path(argv[argv.index('--startup-test') + 1]).resolve().parent
        if '--satellite-test' in argv and argv.index('--satellite-test')+1<len(argv):
            directory=Path(argv[argv.index('--satellite-test')+1]).resolve().parent
        try:
            directory.mkdir(parents=True, exist_ok=True)
            with (directory / f"orion-error-{uuid4().hex}.log").open("x", encoding="utf-8") as handle:
                handle.write(details)
        except OSError:
            if sys.stderr is not None:
                sys.stderr.write(details)
        return 2


if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(launch(sys.argv[1:]))
