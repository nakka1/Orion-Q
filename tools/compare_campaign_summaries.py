"""Compare official campaign runs (Windows, pinned ZIPs) with the published cloud summaries.

Each campaign writes ``summary.json`` into its own output directory. This tool walks the
published summary in ``docs/results`` and the new one side by side and compares every
number (relative tolerance, 1e-9 by default), string and flag. What legitimately depends
on the machine is left out: the manifest's archive and source hashes (the cloud runs
used an archive rebuilt from the author's folder; line endings change source bytes),
per-run artifact hashes, and tool versions. The protocol itself is compared. Yosys
resource counts are reported but never fail the comparison, since they depend on the
Yosys version; neither do the fields that only say whether a platform reproduced a
reference to the last bit (another libm changes the last bit of simulated values, hence
the hash of a generated CSV and which column carries a 1e-16 error).

A second, looser reading answers whether any conclusion changed: every flag, count,
verdict and label must be equal, and every other number within 1e-3 (relative). A run
can differ in the strict reading and still have identical conclusions; both are reported.

    python tools/compare_campaign_summaries.py --official outputs\\official-20261001
"""
import argparse
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "docs" / "results"
CAMPAIGNS = {
    "polarisation-calibrado": "orion_q_polarisation_closed_loop_20260929_summary.json",
    "polarisation-desenvolvimento": "orion_q_polarisation_closed_loop_dev_20260929_summary.json",
    "polarisation-actuator": "orion_q_polarisation_actuator_20260929_summary.json",
    "polarisation-quantisation": "orion_q_polarisation_quantisation_20260930_summary.json",
    "polarisation-blocking": "orion_q_polarisation_blocking_20260930_summary.json",
    "qkd": "orion_q_qkd_closed_loop_20260929_summary.json",
    "allocation": "orion_q_allocation_closed_loop_20260929_summary.json",
    "routing": "orion_q_routing_closed_loop_20260929_summary.json",
    "routing-sequence": "orion_q_routing_sequence_20260930_summary.json",
    "detector": "orion_q_detector_evaluation_20260929_summary.json",
    "detector-causal": "orion_q_detector_causal_20260930_summary.json",
    "rtl": "orion_q_author_rtl_20260930_summary.json",
    "polarisation-demo": "orion_q_polarisation_demo_20260930_summary.json",
}
#: Machine-dependent fields, never compared.
IGNORED = {"artifacts", "tools", "rtl_sources_sha256", "qkd_archive_sha256", "archive_sha256",
           "expected_archive_sha256", "source_sha256", "protocol_file_sha256", "evidence_sha256"}
#: Compared and reported, but a difference does not fail the campaign.
INFORMATIONAL = {"synthesis"}
#: Last-bit platform markers, anywhere in a summary: reported, never failing. Any key ending in
#: ``_csv_sha256`` is the hash of a CSV the simulators generated (``dataset_csv_sha256``,
#: ``dataset_seed_0_csv_sha256``): the values it holds are compared elsewhere, number by number.
PLATFORM = {"worst_column", "input_identical", "calibration_identical"}


def _platform(key):
    return key in PLATFORM or key.endswith("_csv_sha256")
#: Relative tolerance of the conclusions reading for non-integer numbers.
CONCLUSION_TOLERANCE = 1e-3


def _close(ours, theirs, tolerance):
    if isinstance(ours, bool) or isinstance(theirs, bool):
        return ours is theirs
    if math.isnan(ours) or math.isnan(theirs):
        return math.isnan(ours) and math.isnan(theirs)
    if math.isinf(ours) or math.isinf(theirs):
        return ours == theirs
    return abs(ours - theirs) <= tolerance * max(1.0, abs(theirs))


def compare(official, published, tolerance=1e-9, path="", platform=None, integers_exact=False):
    """Every difference between two summaries, as (path, official, published) triples, and the count of values seen.

    Differences under a ``PLATFORM`` key go to ``platform`` (a list) when one is given.
    ``integers_exact`` compares integers exactly whatever the tolerance (the conclusions reading).
    """
    differences, checked = [], 0
    if isinstance(published, dict):
        if not isinstance(official, dict):
            return [(path, type(official).__name__, "object")], 1
        for key in sorted(set(published) | set(official)):
            if key in IGNORED:
                continue
            if platform is not None and _platform(key):
                if key in official and key in published:
                    found, _ = compare(official[key], published[key], tolerance, f"{path}/{key}")
                    platform += found
                continue
            if key not in official or key not in published:
                differences.append((f"{path}/{key}", "present" if key in official else "missing",
                                    "present" if key in published else "missing"))
                continue
            found, seen = compare(official[key], published[key], tolerance, f"{path}/{key}", platform,
                                  integers_exact)
            differences += found
            checked += seen
        return differences, checked
    if isinstance(published, list):
        if not isinstance(official, list) or len(official) != len(published):
            return [(path, f"list of {len(official) if isinstance(official, list) else '?'}",
                     f"list of {len(published)}")], 1
        for position, (ours, theirs) in enumerate(zip(official, published)):
            found, seen = compare(ours, theirs, tolerance, f"{path}[{position}]", platform, integers_exact)
            differences += found
            checked += seen
        return differences, checked
    numeric = (int, float)
    if isinstance(published, numeric) and isinstance(official, numeric) and not (
            isinstance(published, bool) or isinstance(official, bool)):
        if integers_exact and isinstance(official, int) and isinstance(published, int):
            return ([] if official == published else [(path, official, published)]), 1
        return ([] if _close(float(official), float(published), tolerance) else [(path, official, published)]), 1
    return ([] if official == published else [(path, official, published)]), 1


def compare_campaign(name, directory, tolerance=1e-9):
    published = json.loads((RESULTS / CAMPAIGNS[name]).read_text(encoding="utf-8"))
    summary = Path(directory) / "summary.json"
    if not summary.is_file():
        return {"campaign": name, "status": "missing", "summary": str(summary)}
    official = json.loads(summary.read_text(encoding="utf-8"))
    informational = {key: official.pop(key, None) for key in INFORMATIONAL}
    reference = {key: published.pop(key, None) for key in INFORMATIONAL}
    platform = []
    differences, checked = compare(official, published, tolerance, platform=platform)
    loose, _ = compare(official, published, CONCLUSION_TOLERANCE, platform=[], integers_exact=True)
    row = {"campaign": name, "status": "identical" if not differences else "different", "values_checked": checked,
           "differences": len(differences), "first_differences": [list(map(str, item)) for item in differences[:20]],
           "conclusions_identical": not loose,
           "conclusion_differences": [list(map(str, item)) for item in loose[:20]]}
    if platform:
        row["platform_differences"] = [list(map(str, item)) for item in platform[:20]]
    if any(value is not None for value in informational.values()):
        extra, _ = compare(informational, reference, tolerance)
        row["informational_differences"] = [list(map(str, item)) for item in extra[:20]]
    return row


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--official", required=True, help="directory with one subdirectory per campaign")
    parser.add_argument("--tolerance", type=float, default=1e-9, help="relative tolerance for numbers")
    parser.add_argument("--only", nargs="+", choices=sorted(CAMPAIGNS), default=None)
    parser.add_argument("--report", default=None, help="JSON report path (default: <official>/comparison.json)")
    args = parser.parse_args(argv)
    official = Path(args.official)
    rows = [compare_campaign(name, official / name, args.tolerance) for name in (args.only or CAMPAIGNS)]
    report = {"schema": "orion.official-campaign-comparison.v1", "tolerance": args.tolerance,
              "conclusion_tolerance": CONCLUSION_TOLERANCE, "campaigns": rows,
              "passed": all(row["status"] == "identical" for row in rows),
              "conclusions_identical": all(row.get("conclusions_identical") for row in rows)}
    target = Path(args.report) if args.report else official / "comparison.json"
    target.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    for row in rows:
        detail = f"{row.get('values_checked', 0)} valores" if row["status"] != "missing" else row["summary"]
        conclusions = ("" if row["status"] == "missing" else
                       " · conclusões idênticas" if row["conclusions_identical"] else " · CONCLUSÕES DIFERENTES")
        print(f"{row['campaign']:<30} {row['status']:<10} {detail}{conclusions}")
        for path, ours, theirs in row.get("first_differences", [])[:5]:
            print(f"    {path}: {ours} (nuvem {theirs})")
    print("APROVADO" if report["passed"] else "REPROVADO", "->", target)
    print("Conclusões:", "idênticas em todas as campanhas" if report["conclusions_identical"] else "há diferenças")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
