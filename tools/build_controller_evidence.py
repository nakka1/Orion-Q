"""Derive the read-only controller catalog from reviewed campaign bytes."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PINS = {
    "orion_q_polarisation_actuator_20260929_summary.json":
        "9752ea98d6ec55d787ec7577faa45f624557c08fa17070b2eeb6cdd62ef889c5",
    "orion_q_polarisation_closed_loop_20260929_summary.json":
        "652bd502380a279394b400a34fdde403fdd0f21fdea7f06ce38a16e66778f76e",
    "orion_q_polarisation_closed_loop_dev_20260929_summary.json":
        "4649a11926fba7719a51d389bcbe5b1f47bd2824aebbb2879f3ed35ce2b01f01",
}
OUTPUT = ROOT / "src/quantum_autopilot/control_plane/data/controller_evidence_v1.json"
REGISTRY = "docs/thesis/orion_q_platform_claim_registry.json"
REGISTRY_SHA256 = "ff688ad94704ce5b2b16a756e85159a0b08da37c4d099de9d3b34da14ce93f65"
CLAIM_TABLE = "docs/results/orion_q_platform_evidence_20260930.csv"
CLAIM_TABLE_SHA256 = "e47c2687ae272c92da6d67b9137364b30f07ab257db72ef945af815d81f6bc19"


def build(root=ROOT):
    registry_bytes = (root / REGISTRY).read_bytes()
    if (hashlib.sha256(registry_bytes).hexdigest() != REGISTRY_SHA256
            or hashlib.sha256((root / CLAIM_TABLE).read_bytes()).hexdigest() != CLAIM_TABLE_SHA256):
        raise ValueError("reviewed platform claim evidence changed")
    registry = json.loads(registry_bytes)
    claims = {c["id"]: {"id": c["id"], "status": c["status"], "boundaries": c["boundaries"],
                        "automation_allowed": c["product_behavior"]["automation_allowed"]}
              for c in registry["claims"] if c["id"] in {"P-COMP-LF", "P-COMP-ATU"}}
    if len(claims) != 2 or any(c["status"] != "supported_simulation_only" or c["automation_allowed"] for c in claims.values()):
        raise ValueError("controller claims are not admitted for historical simulation")
    sources = {}
    for name, expected in PINS.items():
        raw = (root / "docs/results" / name).read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError(f"reviewed campaign changed: {name}")
        sources[name] = json.loads(raw)
    campaign, *closed = sources.values()
    if campaign["status"] != "complete" or not all(r["passed"] for r in campaign["reproduction"]):
        raise ValueError("actuator campaign is not admitted")
    profiles = []
    for row in campaign["comparisons"]:
        point, model, slew = row["operating_point"], row["model_id"], row["slew_rate_rad_per_s"]
        support = next(s for s in closed if s["manifest"]["protocol"]["operating_point"] == point)
        contract = next(c for c in support["contracts"] if c["model_id"] == model and c["status"] == "ADMITTED")
        if contract["archive_sha256"] != campaign["manifest"]["archive_sha256"]:
            raise ValueError("weight identity is not linked to the actuator campaign")
        protocol = support["manifest"]["protocol"]
        gains = next(g["selected"] for g in campaign["alpha_beta"]
                     if g["operating_point"] == point and g["slew_rate_rad_per_s"] == slew)
        scenario = {key: protocol[key] for key in
                    ("duration_s", "sample_interval_s", "control_period_s", "latency_s",
                     "monitoring_photons_per_basis", "detector_efficiency", "sifted_rate_hz")}
        scenario.update(operating_point=point, mode="simulation", slew_rate_rad_per_s=slew,
                        actuator=(campaign["manifest"]["protocol"]["actuator"] if slew is not None
                                  else {"model": "ideal"}),
                        plant_source_sha256=campaign["manifest"]["source_sha256"][
                            "quantum_autopilot/control_plane/polarisation_plant.py"],
                        supervisor=campaign["manifest"]["protocol"]["supervisor"])
        profiles.append({"id": f"{model}|{'ideal' if slew is None else f'{slew:g}'}",
                         "scenario": scenario, "model_id": model,
                         "checkpoint_sha256": contract["checkpoint_sha256"],
                         "comparison": row, "alpha_beta": {k: gains[k] for k in ("alpha", "beta")}})
    result = {"schema": "orion.controller-evidence-catalog.v1", "evidence": "simulated",
              "sources": {**PINS, REGISTRY: REGISTRY_SHA256, CLAIM_TABLE: CLAIM_TABLE_SHA256},
              "claim_registry": claims, "protocol": campaign["manifest"]["protocol"],
              "protocol_sha256": campaign["manifest"]["protocol_file_sha256"],
              "source_sha256": campaign["manifest"]["source_sha256"],
              "limitations": campaign["limitations"], "profiles": profiles}
    return (json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode("utf-8")


if __name__ == "__main__":
    raw = build()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_bytes(raw)
    print(f"{OUTPUT.relative_to(ROOT)} SHA256 {hashlib.sha256(raw).hexdigest()}")
