"""Local manifest-driven campaign runner; no network or equipment access."""
import argparse
import json
from pathlib import Path

from .temporal_campaign import TemporalCampaignRequest, run_temporal_campaign
from .topology import SimulatedTopologyStore


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def parse_manifest(raw):
    """Shared bounded UTF-8 contract for a file or an explicit editor draft."""
    if not isinstance(raw, bytes):
        raise ValueError("manifest must be UTF-8 bytes")
    if len(raw) > 65536:
        raise ValueError("manifest exceeds 64 KiB")
    def nonfinite(_):
        raise ValueError("non-finite JSON number")
    try:
        return TemporalCampaignRequest.from_dict(json.loads(raw.decode("utf-8-sig"),
            object_pairs_hook=_unique, parse_constant=nonfinite))
    except (UnicodeError, RecursionError, TypeError) as exc:
        raise ValueError("invalid UTF-8 campaign manifest: " + type(exc).__name__) from exc


def load_manifest(path):
    with Path(path).open("rb") as stream:
        raw = stream.read(65537)
    return parse_manifest(raw)


def main(argv=None):
    parser = argparse.ArgumentParser(description="ORION-Q simulated temporal campaign; all timeouts retained.")
    parser.add_argument("--manifest", type=Path, required=True, help="Complete, predeclared JSON request")
    parser.add_argument("--store", type=Path, required=True, help="Local sandbox SQLite; existing workspace is read, never reset")
    parser.add_argument("--output", type=Path, required=True, help="New JSON receipt file; existing files are never overwritten")
    args = parser.parse_args(argv)
    try:
        request = load_manifest(args.manifest)
        if args.output.exists():
            raise ValueError("output already exists; choose a new file")
        receipt = run_temporal_campaign(SimulatedTopologyStore(args.store), request)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation protects an output created concurrently as well.
        with args.output.open("x", encoding="utf-8") as stream:
            json.dump(receipt, stream, indent=2, ensure_ascii=False, allow_nan=False)
            stream.write("\n")
    except (OSError, ValueError) as exc:
        parser.exit(2, f"Campaign refused/failed: {exc}\nNo partial campaign is accepted. If export failed, retry the same manifest with a new output path.\n")
    print("SIMULATION ONLY | exploratory | pointwise 95% Wilson | timeouts included")
    for regime in request.regimes:
        name = regime.regime_id
        summary = receipt["record"]["result"]["summaries"][name]
        lo, hi = summary["target_probability_wilson_95"]
        print(f"{name}: target {summary['target_met']}/{summary['trials']} "
              f"({summary['target_probability']:.1%}; CI {lo:.1%}-{hi:.1%}), "
              f"swaps {summary['swaps']}, timeouts {summary['timeouts']}")
    print(f"Replay: {receipt['idempotent_replay']} | SHA256: {receipt['record_sha256']}")
    print(f"Receipt: {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
