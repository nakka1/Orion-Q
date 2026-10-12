"""TCP liveness only. Readiness requires an authorized mTLS health request.

This probe deliberately has no credential, cannot read a key or application
state, and does not weaken the application's authenticated HTTP boundary.
"""
from __future__ import annotations

import argparse
import socket


def check(host: str, port: int, timeout: float = 2.0) -> bool:
    if not 1 <= port <= 65535 or not 0 < timeout <= 10:
        raise ValueError("invalid liveness target")
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--timeout", type=float, default=2.0)
    args = parser.parse_args(argv)
    return 0 if check(args.host, args.port, args.timeout) else 1


if __name__ == "__main__":
    raise SystemExit(main())
