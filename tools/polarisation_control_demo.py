"""Pre-banca demonstration: activate a learned compensator through Q05, run it, inject a fault, roll back.

    python tools/polarisation_control_demo.py --qnet-zip F:\\qnet_network.zip ^
        --evidence outputs\\polarisation-closed-loop-v1\\summary.json ^
        --operator "Alexandre" --confirm --output outputs\\polarisation-demo

Every step writes its receipt to ``--output`` (never overwriting) and the loop
runs are journalled in ``--output/journal.sqlite``. Simulation only.
"""
import argparse
import hashlib
import json
from pathlib import Path

from quantum_autopilot.control_plane import polarisation_activation as activation
from quantum_autopilot.control_plane import polarisation_plant as plant

MODEL = "qnet.compensation.calibrado.ridge"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--qnet-zip", required=True)
    parser.add_argument("--evidence", required=True, help="summary.json of the closed-loop campaign v1")
    parser.add_argument("--operator", required=True, help="declared operator label (not authentication)")
    parser.add_argument("--confirm", action="store_true", help="declare approval and activation of the simulation")
    parser.add_argument("--output", required=True)
    parser.add_argument("--model", default=MODEL, choices=["qnet.compensation.calibrado.ridge",
                                                            "qnet.compensation.calibrado.mlp"])
    parser.add_argument("--seed", type=int, default=2026)
    args = parser.parse_args()
    if not args.confirm:
        parser.error("--confirm is required: the demo approves and activates a simulated controller")

    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=False)
    step = iter(range(1, 100))

    def save(name, receipt):
        path = output / f"{next(step):02d}-{name}.json"
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(receipt, ensure_ascii=False, indent=2, allow_nan=False) + "\n")

    facts = {"schema": "orion.polarisation-control-demo.v1", "model": args.model, "plant_seed": args.seed,
             "operating_point": "calibrado", "duration_s": 20.0, "cycles": {}, "readbacks": {},
             "physical_action_performed": False}

    def run(label, **kwargs):
        result, receipt = slot.run(trajectory, plant_seed=args.seed, monitoring_seed=args.seed + 6000, **kwargs)
        save("ciclo-" + label, receipt)
        ledger = result.supervisor
        facts["cycles"][label] = {"controller_id": result.controller_id, "mean_qber": result.summary["mean_qber"],
                                "mean_key_rate_hz": result.summary["mean_key_rate_hz"], "faults": ledger["faults"],
                                "degraded_at_s": ledger["degraded_at_s"]}
        print(f"  {label:<28} {result.controller_id:<36} QBER {result.summary['mean_qber']:.4f}  "
              f"chave {result.summary['mean_key_rate_hz'] / 1e3:5.1f} kbit/s  falhas {ledger['faults']}  "
              f"degradou {ledger['degraded_at_s']}")

    def cycle(proposal, label):
        save("proposta-" + label, proposal)
        review = slot.review(proposal["record"]["proposal_id"], proposal["record_sha256"])
        save("revisao-" + label, review)
        approval = slot.approve(activation.approval_payload(proposal, review, args.operator))
        save("aprovacao-" + label, approval)
        receipt = slot.activate(activation.activation_payload(approval))
        save("ativacao-" + label, receipt)
        status = slot.status()
        save("leitura-" + label, status)
        facts["readbacks"][label] = {"status": status["record"]["status"],
                                     "controller_id": status["record"]["live_policy"]["controller_id"],
                                     "operator_authenticated": approval["record"].get("operator_authenticated")}
        print(f"  {label}: {status['record']['status']} ({status['record']['live_policy']['controller_id']})")

    evidence_sha = hashlib.sha256(Path(args.evidence).read_bytes()).hexdigest()
    slot = activation.PolarisationControlSlot(output / "journal.sqlite", archive_path=args.qnet_zip)
    trajectory = plant.simulate(plant.OPERATING_POINTS["calibrado"], 20.0, args.seed)
    print(f"Planta calibrada, semente {args.seed}, 20 s. Evidência {evidence_sha[:16]}…")
    facts["readbacks"]["inicial"] = {"status": slot.status()["record"]["status"]}
    print("1. Estado inicial:", facts["readbacks"]["inicial"]["status"])
    run("baseline")
    refused = MODEL.replace("calibrado", "desenvolvimento")
    try:
        slot.prepare(refused, evidence_path=args.evidence, evidence_sha256=evidence_sha)
        facts["refusal"] = {"model": refused, "refused": False}
    except ValueError as exc:
        facts["refusal"] = {"model": refused, "refused": True, "reason": str(exc)}
    print(f"   {refused}: {'recusado' if facts['refusal']['refused'] else 'ACEITO'}"
          f" ({facts['refusal'].get('reason', '')})")
    print("2. Ativação do compensador aprendido pelo fluxo Q05")
    cycle(slot.prepare(args.model, evidence_path=args.evidence, evidence_sha256=evidence_sha), "modelo")
    run("modelo-ativo")
    print("3. Falha injetada (50 passos inválidos a partir de t = 5 s)")
    run("falha-injetada", fault_steps=range(2500, 2550))
    print("4. Reversão aprovada para o filtro clássico")
    cycle(slot.prepare_rollback(), "reversao")
    run("apos-reversao")
    facts["evidence_sha256"] = evidence_sha
    save("resumo", facts)
    (output / "summary.json").write_text(json.dumps(facts, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                                         encoding="utf-8")
    print(f"Recibos e diário em {output}. Nenhuma ação física foi executada.")


if __name__ == "__main__":
    main()
