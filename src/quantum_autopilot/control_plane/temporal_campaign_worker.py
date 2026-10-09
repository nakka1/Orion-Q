"""Private stdio worker for the native console. No network transport."""
import argparse
import json
import os
import sys
from queue import Queue
from threading import Event, Thread

from .temporal_campaign import CampaignCancelled, TemporalCampaignRequest, run_temporal_campaign
from .temporal_campaign_cli import _unique
from .topology import SimulatedTopologyStore


def _listen(inbox, cancelled):
    # Raw reads avoid a blocked buffered-stdin lock at interpreter shutdown.
    buffer = b""
    first = True
    try:
        while True:
            chunk = os.read(sys.stdin.fileno(), 4096)
            if not chunk:
                cancelled.set()  # Parent disappeared or closed the command pipe.
                if first:
                    inbox.put(None)
                return
            buffer += chunk
            if len(buffer) > 65536:
                cancelled.set()
                if first:
                    inbox.put(None)
                return
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                if first:
                    inbox.put(line)
                    first = False
                else:
                    cancelled.set()  # Only subsequent supported command is CANCEL.
    except OSError:
        cancelled.set()
        if first:
            inbox.put(None)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--store", required=True)
    args = parser.parse_args(argv)
    cancelled, inbox = Event(), Queue()
    def emit(kind, **payload):
        print(json.dumps({"kind": kind, **payload}, allow_nan=False), flush=True)
    try:
        # Native BLAS/Aer DLL initialization can deadlock against a blocked CRT
        # stdin read on Windows. Initialize this worker's required libraries
        # before the listener; ordinary application imports remain lazy.
        import numpy  # noqa: F401
        import scipy.linalg  # noqa: F401
        import qiskit_aer  # noqa: F401
        Thread(target=_listen, args=(inbox, cancelled), daemon=True).start()
        raw = inbox.get(timeout=30)
        if raw is None:
            raise ValueError("missing or oversized request")
        request = TemporalCampaignRequest.from_dict(json.loads(raw, object_pairs_hook=_unique))
        emit("started", campaign_id=request.campaign_id)
        receipt = run_temporal_campaign(SimulatedTopologyStore(args.store), request,
            cancelled=cancelled.is_set,
            progress=lambda done, total, regime, seed: emit("progress", completed=done, total=total, regime=regime, seed=seed))
        emit("committed", record_sha256=receipt["record_sha256"], replay=receipt["idempotent_replay"])
        return 0
    except CampaignCancelled:
        emit("cancelled")
        return 3
    except Exception as exc:
        emit("failed", message=str(exc)[:1000])
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
