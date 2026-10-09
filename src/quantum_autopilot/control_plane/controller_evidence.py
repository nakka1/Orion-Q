"""Historical exact-profile recommendations; no policy mutation or execution."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import sys
from importlib.resources import files
from pathlib import Path

from quantum_autopilot.evidence_export import canonical, export_bundle, receipt

CATALOG_SHA256 = "e3e9eb7f963091c2a605f9829a623643e5698bbfe3406eb8b6709182a861b6c2"
MAX_CATALOG_BYTES = 256 * 1024


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError(f"non-finite JSON constant: {value}")


def load_catalog(path=None):
    resource = files(__package__).joinpath("data/controller_evidence_v1.json") if path is None else Path(path)
    with resource.open("rb") as stream:
        raw = stream.read(MAX_CATALOG_BYTES + 1)
    if len(raw) > MAX_CATALOG_BYTES or hashlib.sha256(raw).hexdigest() != CATALOG_SHA256:
        raise ValueError("controller evidence does not match the reviewed catalog")
    data = json.loads(raw, object_pairs_hook=_unique, parse_constant=_invalid_constant)
    if data["schema"] != "orion.controller-evidence-catalog.v1" or data["evidence"] != "simulated":
        raise ValueError("unsupported controller evidence")
    return data


def profile_request(profile):
    return copy.deepcopy({key: profile[key] for key in ("scenario", "model_id", "checkpoint_sha256")})


def recommend(request, *, catalog_path=None, weights_verified=False):
    """Match every declared field, including JSON numeric types and weight identities.

    ``weights_verified`` records an explicit caller-side package check. By default
    the request identifies historical weights, not a model loaded in a live slot.
    """
    if not isinstance(request, dict) or set(request) != {"scenario", "model_id", "checkpoint_sha256"}:
        raise ValueError("request must declare scenario, model_id and checkpoint_sha256")
    encoded = canonical(request)
    catalog = load_catalog(catalog_path)
    profile = next((p for p in catalog["profiles"] if canonical(profile_request(p)) == encoded), None)
    record = {"schema": "orion.controller-comparison.v1", "request": copy.deepcopy(request),
              "status": "NO_EVIDENCE", "recommended_controller": None, "comparison": None,
              "reference_configuration": None, "profile_id": None, "claim_context": None,
              "reason": "No exact reviewed profile matches this scenario and checkpoint.",
              "weights_verification": "verified_package" if weights_verified else "historical_identity_only",
              "evidence": "simulated", "policy_activated": False, "physical_action_performed": False,
              "catalog_sha256": CATALOG_SHA256, "sources": catalog["sources"],
              "protocol_sha256": catalog["protocol_sha256"], "protocol": catalog["protocol"],
              "limitations": catalog["limitations"] + [
                  "Historical recommendation only; the active loop still uses its existing ideal-actuator contract.",
                  "Exact profiles only; no interpolation, device characterization or authorization to actuate."]}
    if profile is not None:
        claim = catalog["claim_registry"]["P-COMP-LF" if profile["scenario"]["slew_rate_rad_per_s"] is None else "P-COMP-ATU"]
        record["claim_context"] = copy.deepcopy(claim)
        record["limitations"] += claim["boundaries"]
        row = copy.deepcopy(profile["comparison"])
        ratio, probability = row["baseline_to_model_qber_ratio"], row["holm_p_value"]
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
                   for v in (ratio, probability)) or ratio <= 0 or not 0 <= probability <= 1:
            raise ValueError("invalid comparison statistics")
        significant = probability < catalog["protocol"]["alpha"]
        if significant and ratio > 1:
            status, selected = "MODEL_RECOMMENDED", profile["model_id"]
            reason = "Reviewed paired comparison supports lower mean QBER for the learned controller."
        elif significant and ratio < 1:
            status, selected = "BASELINE_RECOMMENDED", row["baseline"]
            reason = "Reviewed paired comparison supports lower mean QBER for the retuned alpha-beta."
        else:
            status, selected = "INCONCLUSIVE", None
            reason = "No significant advantage demonstrated; this is not evidence of equivalence."
        record.update(status=status, recommended_controller=selected, reason=reason, comparison=row,
                      profile_id=profile["id"], reference_configuration=profile["alpha_beta"])
    return receipt(record)


def verify_report(report):
    if not isinstance(report, dict) or set(report) != {"record", "record_sha256"}:
        raise ValueError("invalid comparison receipt")
    if receipt(report["record"])["record_sha256"] != report["record_sha256"]:
        raise ValueError("comparison receipt changed")
    record = report["record"]
    # Compare every derived value to reviewed evidence, not only the local hash.
    expected = recommend(record["request"], weights_verified=record["weights_verification"] == "verified_package")
    if canonical(expected) != canonical(report):
        raise ValueError("comparison does not match reviewed evidence")
    return record


def markdown_report(report):
    record = verify_report(report)
    scenario, row = record["request"]["scenario"], record["comparison"]
    lines = ["# ORION-Q — Comparação de controladores", "", "Evidência: simulação histórica.", "",
             f"Resultado: **{record['status']}**.", f"Controlador recomendado: {record['recommended_controller'] or 'nenhum'}.",
             "", record["reason"], "", "## Cenário", "", "```json", json.dumps(scenario, indent=2), "```", "",
             f"Pesos: {record['weights_verification']}.", ""]
    if row is not None:
        lines += ["## Comparação pareada", "", "| Métrica | Valor |", "|---|---:|",
                  f"| QBER média do modelo (proporção) | {row['model_mean_qber']:.17g} |",
                  f"| QBER média da referência (proporção) | {row['baseline_mean_qber']:.17g} |",
                  f"| Razão referência/modelo | {row['baseline_to_model_qber_ratio']:.17g} |",
                  f"| IC 95% da razão | {row['ratio_ci95']} |", f"| Sementes | {row['n_seeds']} |",
                  f"| Vitórias do modelo | {row['model_wins']} |", f"| p ajustado Holm | {row['holm_p_value']:.17g} |",
                  "", f"Referência ressintonizada: {canonical(record['reference_configuration'])}.",
                  f"Método: {row['method']}.", ""]
    lines += ["## Validade e proveniência", "", *[f"- {item}" for item in record["limitations"]], "",
              f"Afirmação de contexto: {record['claim_context']['id'] if record['claim_context'] else 'nenhuma'}.",
              f"Recibo SHA256: {report['record_sha256']}", f"Catálogo SHA256: {record['catalog_sha256']}",
              f"Protocolo SHA256: {record['protocol_sha256']}", "",
              *[f"- {name}: {digest}" for name, digest in record["sources"].items()], ""]
    return "\n".join(lines)


def export_report(report, destination):
    """Export the captured evidence and a readable report into a new directory."""
    verify_report(report)
    raw = files(__package__).joinpath("data/controller_evidence_v1.json").read_bytes()
    if hashlib.sha256(raw).hexdigest() != CATALOG_SHA256:
        raise ValueError("controller evidence changed before export")
    return export_bundle(report, markdown_report(report), destination, stem="comparison",
                         extra_files={"controller_evidence_v1.json": raw})


def main(argv=None):
    parser = argparse.ArgumentParser(description="Export a reviewed simulated controller comparison.")
    parser.add_argument("--profile", required=True, help="Exact profile ID from the comparison dialog")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        selected = next((p for p in load_catalog()["profiles"] if p["id"] == args.profile), None)
        if selected is None:
            raise ValueError("profile has no reviewed evidence")
        report = recommend(profile_request(selected))
        export_report(report, args.output)
    except (OSError, ValueError) as exc:
        parser.exit(2, f"Comparison refused: {exc}\n")
    if sys.stdout is not None:
        print(report["record"]["status"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
