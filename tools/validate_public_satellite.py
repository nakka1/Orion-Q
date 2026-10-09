"""Fetch public OMM once asynchronously and export backend-compatible predictions."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from quantum_autopilot.quantum_protocols.public_ephemeris import PublicEphemerisSource
from quantum_autopilot.quantum_protocols.orbits import position, state_geometry


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog-id", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--latitude", type=float, default=-22.954)
    parser.add_argument("--longitude", type=float, default=-43.167)
    parser.add_argument("--altitude-m", type=float, default=20)
    args = parser.parse_args(argv)
    if not -90 <= args.latitude <= 90 or not -180 <= args.longitude <= 180 or not -500 <= args.altitude_m <= 9000:
        parser.error("invalid ground station coordinates")
    source = PublicEphemerisSource(args.catalog_id, args.output / "cache")
    try:
        future = source.refresh()
        if future is not None:
            future.result(timeout=35)
        state = source.snapshot()
        report = {"schema": "orion.public-satellite-validation.v1", "source": state,
                  "orbital_prediction_validated": False, "physical_payload_validated": False,
                  "physical_action_performed": False, "certified_secret_bits": 0}
        if state["state"] == "ready":
            epoch = datetime.now(timezone.utc)
            orbit = source.sample_orbit(epoch, duration_s=5400, stations=["reference-station"])
            samples = [state_geometry(position(orbit, float(offset)), (args.latitude, args.longitude), args.altitude_m)
                       for offset in range(0, 5401, 30)]
            report.update(orbital_prediction_validated=True, sample_count=len(samples),
                          predicted_visible_samples=sum(sample["elevation_deg"] >= 10 for sample in samples),
                          station={"latitude": args.latitude, "longitude": args.longitude, "altitude_m": args.altitude_m},
                          visibility_scope="geometric prediction above 10 deg; no tracking lock, weather or payload evidence")
            args.output.mkdir(parents=True, exist_ok=True)
            (args.output / "orbit.json").write_text(json.dumps(orbit, indent=2), encoding="utf-8")
            (args.output / "predicted_pass_samples.json").write_text(json.dumps(samples, indent=2), encoding="utf-8")
        args.output.mkdir(parents=True, exist_ok=True)
        (args.output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps({"state": state["state"], "orbital_prediction_validated": report["orbital_prediction_validated"],
                          "physical_payload_validated": False, "report": str(args.output / "report.json")}))
        return 0 if report["orbital_prediction_validated"] else 2
    finally:
        source.close()


if __name__ == "__main__":
    raise SystemExit(main())
