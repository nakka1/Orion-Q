"""Measure the existing multiprocess ledger test without its 30-second deadline."""
from __future__ import annotations

import json
import multiprocessing
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
from tests.test_lab_key_ledger import _reserve_process, ledger, provision


def main():
    state = Path(tempfile.mkdtemp(prefix="ledger-release-measure-", dir=ROOT / "work"))
    path = state / "keys.sqlite"
    provision(ledger(path), 24)
    context = multiprocessing.get_context("spawn")
    processes, readers, assigned, completions = [], [], [], []
    started = time.monotonic()
    report = {"schema": "orion.ledger-batch-timing.v1", "passed": False,
              "processes": 4, "requests_per_process": 10, "provisioned_test_keys": 24,
              "deadline_s": 180, "physical_action_performed": False,
              "public_key_material": False}
    try:
        for number in range(4):
            parent, child = context.Pipe()
            process = context.Process(target=_reserve_process, args=(path, number, child))
            process.start()
            child.close()
            processes.append(process)
            readers.append(parent)
        for number, reader in enumerate(readers):
            remaining = max(0, 180 - (time.monotonic() - started))
            if not reader.poll(remaining):
                raise TimeoutError("ledger batch did not finish within bounded measurement")
            result = reader.recv()
            assigned.extend(result)
            completions.append({"process": number, "received_at_s": time.monotonic() - started,
                                "assigned_count": len(result)})
        counts = ledger(path).summary()["counts"]
        report.update(counts=counts, assigned_count=len(assigned), unique_count=len(set(assigned)),
                      completions=completions)
        report["passed"] = len(assigned) == len(set(assigned)) == 24 and counts == {"reserved": 24}
    except Exception as error:
        report.update(error_type=type(error).__name__, error=str(error))
    finally:
        for process in processes:
            process.join(10)
            if process.is_alive():
                process.terminate()
                process.join(5)
        for reader in readers:
            reader.close()
        report.update(elapsed_s=time.monotonic() - started,
                      process_exit_codes=[process.exitcode for process in processes])
        output = ROOT / "outputs/native_release_5_64_2_20261011/windows/ledger-batch-timing.json"
        output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report))
    return 0 if report["passed"] and all(code == 0 for code in report["process_exit_codes"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
