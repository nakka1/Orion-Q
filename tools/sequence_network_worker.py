"""Isolated SeQUeNCe JSON-lines worker; no OrionQ product imports or fallback."""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import json
from pathlib import Path
import sys


def emit(value):
    print(json.dumps(value, sort_keys=True, allow_nan=False), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--sequence-src', required=True, type=Path)
    args = parser.parse_args()
    with redirect_stdout(sys.stderr):
        from sequence_network_backend import load_native, run_classical_probe
        api, metadata = load_native(args.sequence_src)
    emit({'schema': 'orionq.sequence-worker-ready.v1', 'metadata': metadata})
    for line in sys.stdin:
        try:
            request = json.loads(line, parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite JSON')))
            if request == {'operation': 'close'}:
                return 0
            with redirect_stdout(sys.stderr):
                result = run_classical_probe(request, api=api)
            emit({'ok': True, 'result': result})
        except Exception as exc:
            emit({'ok': False, 'error': f'{type(exc).__name__}: {exc}'})
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
