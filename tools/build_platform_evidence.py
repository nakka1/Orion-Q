"""Flatten the ORION-Q campaign summaries into one evidence table for the platform claim registry.

The claim validator (``quantum_twin.evaluation.thesis_claims``) reads CSV evidence and
selects one row per check. This tool writes every scalar of every campaign summary as
one row (``key``, ``value``), keyed by a readable path: list items are named by their
identifying fields (``comparisons[model_id=...,baseline=...]/wins``). It also adds, per
campaign, the number of reproduction gates and how many of them failed. Nothing is typed
by hand; ``tests/test_platform_claims.py`` rebuilds the table and compares it byte for
byte, and the registry pins its SHA-256.

    python tools/build_platform_evidence.py
"""
import csv
import hashlib
import io
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "docs" / "results"
OUTPUT = RESULTS / "orion_q_platform_evidence_20260930.csv"
SUMMARIES = {
    "polarisation_calibrado": "orion_q_polarisation_closed_loop_20260929_summary.json",
    "polarisation_desenvolvimento": "orion_q_polarisation_closed_loop_dev_20260929_summary.json",
    "polarisation_actuator": "orion_q_polarisation_actuator_20260929_summary.json",
    "qkd": "orion_q_qkd_closed_loop_20260929_summary.json",
    "allocation": "orion_q_allocation_closed_loop_20260929_summary.json",
    "routing": "orion_q_routing_closed_loop_20260929_summary.json",
    "routing_sequence": "orion_q_routing_sequence_20260930_summary.json",
    "detector": "orion_q_detector_evaluation_20260929_summary.json",
    "detector_causal": "orion_q_detector_causal_20260930_summary.json",
    "rtl": "orion_q_author_rtl_20260930_summary.json",
    "quantisation": "orion_q_polarisation_quantisation_20260930_summary.json",
    "blocking": "orion_q_polarisation_blocking_20260930_summary.json",
    "demo": "orion_q_polarisation_demo_20260930_summary.json",
    "windows": "orion_q_windows_validation_20261001_comparison.json",
}
SKIP = {"manifest", "artifacts", "runs", "limitations", "source_sha256", "rtl_sources_sha256", "by_type", "tools",
        "output"}
IDENTIFIERS = ("operating_point", "point", "model", "configuration", "variant", "policy", "convention", "length_km", "gate_width_ps", "dark_count_rate_hz",
               "slew_rate_rad_per_s", "model_id", "controller_id", "baseline", "hypothesis", "metric", "gate", "seed",
               "example", "detector", "design", "top", "campaign")


def _name(item, position):
    fields = [f"{key}={item[key]}" for key in IDENTIFIERS if isinstance(item, dict) and key in item
              and not isinstance(item[key], (dict, list))]
    return ",".join(fields) if fields else str(position)


def flatten(value, path="", rows=None):
    rows = [] if rows is None else rows
    if isinstance(value, dict):
        for key, item in value.items():
            if key not in SKIP:
                flatten(item, f"{path}/{key}", rows)
    elif isinstance(value, list):
        if len(value) <= 64:
            names = [_name(item, position) for position, item in enumerate(value)]
            for position, (name, item) in enumerate(zip(names, value)):
                label = name if names.count(name) == 1 else f"{name},#{position}"
                flatten(item, f"{path}[{label}]", rows)
    elif value is None or isinstance(value, (bool, int, float)) or (isinstance(value, str) and len(value) <= 120):
        rows.append((path, "" if value is None else json.dumps(value) if isinstance(value, bool) else value))
    return rows


def gate_counts(summary, campaign):
    gates = summary if campaign == "rtl" else summary.get("reproduction", {})
    flags = [value for key, value in flatten(gates) if key.endswith("/passed")]
    if campaign == "rtl":   # the author's own MLP bench is expected to fail (a generator defect), not a gate
        flags = [value for key, value in flatten(gates)
                 if key.endswith("/passed") and "mlp_author_bench" not in key]
    return len(flags), sum(flag != "true" for flag in flags)


def build():
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["key", "value", "summary", "summary_sha256"])
    for campaign, name in SUMMARIES.items():
        raw = (RESULTS / name).read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        summary = json.loads(raw)
        total, failed = gate_counts(summary, campaign)
        rows = [("/gates_total", total), ("/gates_failed", failed)] + flatten(summary)
        for key, value in rows:
            writer.writerow([f"{campaign}:{key}", value, name, digest])
    return buffer.getvalue().encode("utf-8")


def main():
    OUTPUT.write_bytes(build())
    print(OUTPUT.relative_to(ROOT), hashlib.sha256(OUTPUT.read_bytes()).hexdigest())


if __name__ == "__main__":
    main()
