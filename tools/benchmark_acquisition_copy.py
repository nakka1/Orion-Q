"""Compare independent copies of the same validated raw photon observations."""
from dataclasses import asdict
import argparse
import hashlib
import json
from pathlib import Path
import statistics
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from quantum_autopilot.quantum_protocols.contracts import Acquisition, canonical


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    record = Acquisition(command_id="copy-benchmark", session_id="fixture", operation="satellite_block",
        source_id="synthetic-copy-fixture", origin="simulated", sequence=1, observed_at_s=1., clock_offset_s=0.,
        quantum_channel_ids=("optical",), classical_channel_ids=("classical",),
        raw={"tdc": [{"timestamp_ps": list(range(10**18, 10**18 + 100_000)),
                      "detector_id": index} for index in range(2)], "parameters": {"unit": "ps"}})
    reference = asdict(record)
    output = {"schema": "orion.acquisition-copy-benchmark.v1", "events": 200_000,
              "repetitions": 7, "python": sys.version, "raw_sha256": hashlib.sha256(canonical(reference).encode()).hexdigest(),
              "identical_values_and_container_types": record.to_dict() == reference,
              "hardware_acquisition": False, "measurements_ms": {}}
    for name, operation in (("previous_dataclass_asdict", lambda: asdict(record)), ("validated_raw_copy", record.to_dict)):
        operation()
        values = []
        for _ in range(7):
            began = time.perf_counter()
            copied = operation()
            values.append((time.perf_counter() - began) * 1000)
            assert copied == reference
        output["measurements_ms"][name] = {"samples": values, "median": statistics.median(values)}
    copied["raw"]["tdc"][0]["timestamp_ps"].append(0)
    output["independent_ownership"] = len(record.raw["tdc"][0]["timestamp_ps"]) == 100_000
    output["speedup"] = (output["measurements_ms"]["previous_dataclass_asdict"]["median"] /
                         output["measurements_ms"]["validated_raw_copy"]["median"])
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(output, stream, indent=2)
    print(json.dumps({"speedup": output["speedup"], "identical": output["identical_values_and_container_types"],
                      "independent": output["independent_ownership"]}))


if __name__ == "__main__":
    main()
